"""Geometry preparation, UV coverage, and mip-0 color intent."""

from array import array
from dataclasses import dataclass
import os
import struct

from core.geometry.buffers import BufferStore
from core.geometry.conventions import geometry_convention_for
from core.geometry.packing import pack_draw_geometry
from core.resource_paths import safe_resource_path
from core.textures.color_adjustment import prepare_color_adjustment
from core.textures.uv_coverage import UVCoverageError, rasterize_uv_coverage

from .errors import TextureSaveError
from .request import (
    physical_identity, resolve_save_request, texture_path,
)


@dataclass(frozen=True)
class PreparedGeometry:
    indices: tuple
    source_uvs: tuple


@dataclass(frozen=True)
class PreparedSaveTarget:
    semantic_key: str
    metadata_key: str


@dataclass(frozen=True)
class PreparedTextureSave:
    entries: tuple
    selected_path: str
    info: object
    layout: object
    targets: tuple
    mip0_claims: object
    intent_adjustments: tuple
    mip0_affected_blocks: tuple


def draw_geometry(draw, group, mod_dir, buffers, sparse_shape_cache, convention):
    paths = [
        safe_resource_path(mod_dir, group.get("position_file")),
        safe_resource_path(mod_dir, group.get("texcoord_file")),
        safe_resource_path(mod_dir, group.get("ib_file")),
    ]
    if not all(path and os.path.isfile(path) for path in paths):
        raise TextureSaveError(
            "geometry_not_available",
            "The rendered draw geometry could not be prepared.")
    default_streams = buffers.vertex_streams(
        paths[0], group.get("position_stride"), paths[1],
        group.get("texcoord_stride"))
    buffers.raw(paths[2])
    return pack_draw_geometry(
        draw, group,
        mod_dir=mod_dir,
        default_streams=default_streams,
        default_index_size=group.get("index_size", 4),
        buffers=buffers,
        geometry_convention=convention,
        sparse_shape_cache=sparse_shape_cache,
    )


def unpack_indices(raw):
    if raw is None or len(raw) % 4:
        raise TextureSaveError(
            "geometry_not_available", "Packed mesh indices could not be read.")
    return struct.unpack(f"<{len(raw) // 4}I", raw)


def unpack_source_uvs(raw):
    if raw is None or len(raw) % 8:
        raise TextureSaveError(
            "mesh_has_no_uv", "The mesh has no UV coordinates.")
    values = struct.unpack(f"<{len(raw) // 4}f", raw)
    # pack_draw_geometry stores viewer-space V = 1 - source V for Three.js.
    return tuple((u, 1.0 - v)
                 for u, v in zip(values[::2], values[1::2]))


def prepare_uv_geometry(draw, group, mod_dir, buffers, sparse_shape_cache,
                        convention):
    """Pack one draw once and retain source-orientation UV triangles."""
    try:
        packed = draw_geometry(
            draw, group, mod_dir, buffers, sparse_shape_cache, convention)
        if packed is None:
            raise TextureSaveError(
                "geometry_not_available",
                "The rendered draw geometry could not be prepared.")
        indices = unpack_indices(packed.indices)
        source_uvs = unpack_source_uvs(packed.texcoords)
        return PreparedGeometry(indices=indices, source_uvs=source_uvs)
    except TextureSaveError:
        raise
    except Exception as error:
        raise TextureSaveError(
            "geometry_not_available",
            "The rendered draw geometry could not be prepared.") from error


def rasterize_geometry(geometry, width, height, unit_width, unit_height):
    try:
        return rasterize_uv_coverage(
            geometry.indices, geometry.source_uvs, width, height,
            unit_width=unit_width, unit_height=unit_height)
    except UVCoverageError as error:
        raise TextureSaveError(error.code, error.message) from error


def draw_metadata_key(draw, group):
    """Resolve the durable metadata identity for one resolved draw."""
    from core.geometry.identity import mesh_identity_for_draw
    try:
        return mesh_identity_for_draw(draw, group).key
    except AttributeError:
        return None


def adjustment_signature(adjustment):
    return tuple(sorted(adjustment.items()))


def texture_save_conflict(target_key, other_key):
    message = (
        f"Mesh {target_key} overlaps another color adjustment on the selected "
        "texture; the texture cannot represent both colors.")
    return TextureSaveError(
        "incompatible_texture_color_usage", message, "unsupported", {
            "meshes": [target_key, other_key],
            "target_semantic_key": target_key,
            "conflicting_semantic_key": other_key,
            "conflict": "adjustment",
        })


def prepare_texture_save(context, overrides, active_mesh_keys,
                         selected_texture_key, targets, texture_usage):
    """Resolve changed targets and mip-0 Color intent for one DDS."""
    if not isinstance(targets, list) or not targets:
        raise TextureSaveError(
            "no_color_adjustment", "Adjust a mesh color before saving.")
    if any(not isinstance(target, dict) for target in targets):
        raise TextureSaveError(
            "stale_mesh_state",
            "The model changed before the texture save started.")
    entries, selected_path, info, parsed, draws = resolve_save_request(
        context, overrides, active_mesh_keys, selected_texture_key,
        texture_usage)
    from core.textures.dds import inspect_dds_layout
    layout = inspect_dds_layout(selected_path)
    if layout is None:
        raise TextureSaveError(
            "invalid_dds", "The selected texture is not a valid supported DDS.")

    entry_by_key = {entry["semantic_key"]: entry for entry in entries}
    target_keys = set()
    requested_targets = []
    for target in targets:
        semantic_key = target.get("semantic_key")
        metadata_key = target.get("metadata_key")
        if (not isinstance(semantic_key, str) or not semantic_key
                or semantic_key in target_keys
                or not isinstance(metadata_key, str) or not metadata_key):
            raise TextureSaveError(
                "stale_mesh_state",
                "The texture save target identity is invalid.")
        target_keys.add(semantic_key)
        entry = entry_by_key.get(semantic_key)
        draw_pair = draws.get(semantic_key)
        target_path = texture_path(
            context.mod_dir,
            entry["texture_keys"]["diffuse"] if entry else None)
        if (entry is None or draw_pair is None or target_path is None
                or physical_identity(target_path)
                != physical_identity(selected_path)):
            raise TextureSaveError(
                "stale_mesh_state",
                "A texture save target no longer belongs to the selected DDS.")
        actual_metadata_key = draw_metadata_key(draw_pair[0], draw_pair[1])
        if actual_metadata_key != metadata_key:
            raise TextureSaveError(
                "stale_mesh_state",
                "A texture save target identity changed before the save started.")
        from core.textures.color_adjustment import normalize_color_adjustment
        from core.textures.color_adjustment import is_neutral_color_adjustment
        adjustment = normalize_color_adjustment(
            target.get("adjustment"), reject_invalid=True)
        if adjustment is None:
            raise TextureSaveError(
                "invalid_color_adjustment", "The color adjustment is invalid.")
        if is_neutral_color_adjustment(adjustment):
            raise TextureSaveError(
                "no_color_adjustment", "Adjust a mesh color before saving.")
        requested_targets.append((semantic_key, metadata_key, draw_pair,
                                  adjustment))

    buffers = BufferStore()
    sparse_shape_cache = {}
    convention = geometry_convention_for(parsed.game.game)
    prepared_targets = []
    unresolved = []
    unresolved_details = []
    base_mip = layout.mips[0]
    intent_classes = {}
    intent_sources = [None]
    mip0_claims = bytearray(base_mip.width * base_mip.height)
    mip0_affected_mask = bytearray(base_mip.units_x * base_mip.units_y)
    claims_need_wide_values = False
    intent_adjustments = [None]
    for semantic_key, metadata_key, draw_pair, adjustment in requested_targets:
        try:
            geometry = prepare_uv_geometry(
                draw_pair[0], draw_pair[1], context.mod_dir, buffers,
                sparse_shape_cache, convention)
            pixel_coverage = rasterize_geometry(
                geometry, base_mip.width, base_mip.height, 1, 1)
        except TextureSaveError as error:
            unresolved.append(semantic_key)
            unresolved_details.append({
                "semantic_key": semantic_key,
                "code": error.code,
                "error": error.message,
            })
            continue
        prepared_targets.append(PreparedSaveTarget(semantic_key, metadata_key))
        signature = adjustment_signature(adjustment)
        intent_class = intent_classes.get(signature)
        if intent_class is None:
            intent_class = len(intent_adjustments)
            if intent_class > 0xFFFF:
                raise TextureSaveError(
                    "texture_validation_failed",
                    "Too many distinct Color adjustments were submitted.")
            intent_classes[signature] = intent_class
            intent_adjustments.append(adjustment)
            intent_sources.append(semantic_key)
            if intent_class > 0xFF and not claims_need_wide_values:
                mip0_claims = array("H", mip0_claims)
                claims_need_wide_values = True
        mask = pixel_coverage.mask
        if len(mask) != len(mip0_claims):
            raise TextureSaveError(
                "texture_validation_failed",
                "Target pixel coverage does not match the source texture size.")
        for index, selected in enumerate(mask):
            if not selected:
                continue
            previous = mip0_claims[index]
            if previous and previous != intent_class:
                raise texture_save_conflict(
                    intent_sources[previous], semantic_key)
            mip0_claims[index] = intent_class
            x = index % base_mip.width
            y = index // base_mip.width
            mip0_affected_mask[(y // 4) * base_mip.units_x + x // 4] = 1

    if unresolved:
        # Preparation is still useful for diagnostics, but Save cannot safely
        # proceed when an explicit changed target is unknown.
        raise TextureSaveError(
            "unknown_texture_coverage",
            "Texture coverage could not be determined safely.",
            details={"unresolved_consumers": list(unresolved),
                     "consumers": list(unresolved_details)})
    if not any(mip0_claims):
        raise TextureSaveError(
            "incompatible_texture_color_usage",
            "The changed meshes have no writable texture units.", "unsupported",
            {"meshes": [target.semantic_key for target in prepared_targets]})
    return PreparedTextureSave(
        entries=entries, selected_path=selected_path, info=info, layout=layout,
        targets=tuple(prepared_targets),
        mip0_claims=mip0_claims,
        intent_adjustments=tuple(
            [None] + [prepare_color_adjustment(adjustment)
                      for adjustment in intent_adjustments[1:]]),
        mip0_affected_blocks=tuple(
            index for index, selected in enumerate(mip0_affected_mask)
            if selected))

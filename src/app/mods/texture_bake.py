"""Authoritative analysis and safe, unit-preserving DDS color baking."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import io
import os
import struct
import tempfile

from core.geometry.buffers import BufferStore
from core.geometry.conventions import geometry_convention_for
from core.geometry.packing import pack_draw_geometry
from core.geometry.semantics import (
    authored_texture_keys_for_draw, deduplicate_draws,
)
from core.resource_paths import _canonical, safe_resource_path
from core.textures import TEXTURE_ROLES, split_texture_key
from core.textures.color_adjustment import (
    adjust_rgba_bytes, is_neutral_color_adjustment,
    normalize_color_adjustment,
)
from core.textures.dds import (
    dds_layout_for_info, inspect_dds, inspect_dds_layout,
)
from core.textures.pipeline import load_texture_image_full
from core.textures.texconv import (
    TexconvError, TexconvUnavailableError, encode_png_to_dds,
)
from core.textures.uv_coverage import (
    UVCoverage, UVCoverageError, collapse_pixel_mask_to_units,
    dilate_pixel_mask, rasterize_uv_coverage,
)

from app.assets.textures import is_asset_texture_key
from app.mods.analysis import resolved_draws


_SUPPORTED_COLOR_FORMATS = frozenset({
    "bc1_unorm", "bc1_srgb", "bc2_unorm", "bc2_srgb",
    "bc3_unorm", "bc3_srgb", "bc7_unorm", "bc7_srgb",
    "rgba8", "bgra8",
})
_UNSUPPORTED_COLOR_FORMATS = frozenset({
    "bc4_unorm", "bc4_snorm", "bc5_unorm", "bc5_snorm",
    "bc6h_ufloat", "bc6h_float",
})
_OPAQUE_ONLY_FORMATS = frozenset({
    "bc1_unorm", "bc1_srgb", "bc7_unorm", "bc7_srgb",
})
_TEXTURE_USAGE_ROLES = tuple(TEXTURE_ROLES)
_TEXTURE_ROLE_LABELS = {
    "normal_map": "Normal Map",
    "normal_data": "Normal Data",
    "light_map": "Light Map",
    "material_map": "Material Map",
    "emission_map": "Emission Map",
}


class TextureBakeAnalysisError(ValueError):
    """An expected, stable failure from a coverage or bake request."""

    def __init__(self, code, message, status="error"):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(frozen=True)
class _PreparedGeometry:
    indices: tuple
    source_uvs: tuple
    triangle_count: int
    degenerate_triangle_count: int


@dataclass(frozen=True)
class _PreparedConsumer:
    semantic_key: str
    geometry: _PreparedGeometry
    coverages: tuple


@dataclass(frozen=True)
class _PreparedTextureBake:
    entries: tuple
    selected_path: str
    selected_metadata_key: str | None
    info: object
    layout: object
    selected_pixels: object
    selected_consumer: _PreparedConsumer
    other_consumers: tuple
    safe_masks: tuple
    shared_masks: tuple
    shared_with: tuple
    unresolved: tuple
    unresolved_details: tuple

    @property
    def safety(self):
        if self.unresolved:
            return "unknown"
        return "shared" if any(sum(mask) for mask in self.shared_masks) else "safe"


def _error(code, message, status="error", **details):
    result = {"status": status, "code": code, "error": message}
    result.update(details)
    return result


def _canonical_mod_path(mod_dir, relative_path):
    """Resolve a file strictly inside the mod root, not its escape ceiling."""
    path = safe_resource_path(mod_dir, relative_path)
    if not path or not os.path.isfile(path):
        return None
    root = _canonical(mod_dir)
    canonical = _canonical(path)
    try:
        if os.path.commonpath((canonical, root)) != root:
            return None
    except ValueError:
        return None
    return path


def _physical_identity(path):
    return os.path.normcase(os.path.realpath(os.path.abspath(path))).casefold()


def _texture_path(mod_dir, key, *, selected=False):
    if not key:
        if selected:
            raise TextureBakeAnalysisError(
                "no_diffuse", "The selected mesh has no diffuse texture.")
        return None
    role, relative_path = split_texture_key(key)
    if selected and role != "diffuse":
        raise TextureBakeAnalysisError(
            "not_diffuse_texture", "Texture coverage requires a diffuse texture.")
    if role != "diffuse" or not relative_path:
        return None
    if is_asset_texture_key(key):
        if selected:
            raise TextureBakeAnalysisError(
                "asset_texture_read_only",
                "Asset textures cannot be modified.", "unsupported")
        return None
    if selected and not relative_path.lower().endswith(".dds"):
        raise TextureBakeAnalysisError(
            "unsupported_texture_type",
            "Texture baking currently requires a DDS source.", "unsupported")
    path = _canonical_mod_path(mod_dir, relative_path)
    if path is None:
        if selected:
            raise TextureBakeAnalysisError(
                "texture_not_found", "The selected diffuse texture was not found.")
        return None
    return path


def _usage_texture_path(mod_dir, key, expected_role):
    """Resolve one submitted role assignment with the shared mod sandbox."""
    if not key:
        return None
    role, relative_path = split_texture_key(key, expected_role)
    if role != expected_role or not relative_path:
        return None
    if is_asset_texture_key(key):
        return None
    return _canonical_mod_path(mod_dir, relative_path)


def _texture_details(path, info):
    return {
        "file": os.path.basename(path),
        "width": info.width,
        "height": info.height,
        "format": info.format,
        "compressed": info.compressed,
        "mip_count": info.mip_count,
    }


def _inspect_color_texture(path):
    if not path.lower().endswith(".dds"):
        raise TextureBakeAnalysisError(
            "unsupported_texture_type",
            "Texture baking currently requires a DDS source.", "unsupported")
    info = inspect_dds(path)
    if info is None:
        raise TextureBakeAnalysisError(
            "invalid_dds", "The selected texture is not a valid supported DDS.")
    if info.format in _UNSUPPORTED_COLOR_FORMATS:
        raise TextureBakeAnalysisError(
            "unsupported_color_bake_format",
            f"DDS format {info.format} is not a color-bake target.",
            "unsupported")
    if info.format not in _SUPPORTED_COLOR_FORMATS:
        raise TextureBakeAnalysisError(
            "unsupported_color_bake_format",
            f"DDS format {info.format} is not a color-bake target.",
            "unsupported")
    return info


def _draw_geometry(draw, group, mod_dir, buffers, sparse_shape_cache, convention):
    paths = [
        safe_resource_path(mod_dir, group.get("position_file")),
        safe_resource_path(mod_dir, group.get("texcoord_file")),
        safe_resource_path(mod_dir, group.get("ib_file")),
    ]
    if not all(path and os.path.isfile(path) for path in paths):
        raise TextureBakeAnalysisError(
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


def _unpack_indices(raw):
    if raw is None or len(raw) % 4:
        raise TextureBakeAnalysisError(
            "geometry_not_available", "Packed mesh indices could not be read.")
    return struct.unpack(f"<{len(raw) // 4}I", raw)


def _unpack_source_uvs(raw):
    if raw is None or len(raw) % 8:
        raise TextureBakeAnalysisError(
            "mesh_has_no_uv", "The mesh has no UV coordinates.")
    values = struct.unpack(f"<{len(raw) // 4}f", raw)
    # pack_draw_geometry stores viewer-space V = 1 - source V for Three.js.
    return tuple((u, 1.0 - v)
                 for u, v in zip(values[::2], values[1::2]))


def _prepare_uv_geometry(draw, group, mod_dir, buffers, sparse_shape_cache,
                         convention):
    """Pack one draw once and retain source-orientation UV triangles."""
    try:
        packed = _draw_geometry(
            draw, group, mod_dir, buffers, sparse_shape_cache, convention)
        if packed is None:
            raise TextureBakeAnalysisError(
                "geometry_not_available",
                "The rendered draw geometry could not be prepared.")
        indices = _unpack_indices(packed.indices)
        source_uvs = _unpack_source_uvs(packed.texcoords)
        return _PreparedGeometry(
            indices=indices, source_uvs=source_uvs,
            triangle_count=len(indices) // 3,
            degenerate_triangle_count=0)
    except TextureBakeAnalysisError:
        raise
    except Exception as error:
        raise TextureBakeAnalysisError(
            "geometry_not_available",
            "The rendered draw geometry could not be prepared.") from error


def _rasterize_geometry(geometry, width, height, unit_width, unit_height):
    try:
        return rasterize_uv_coverage(
            geometry.indices, geometry.source_uvs, width, height,
            unit_width=unit_width, unit_height=unit_height)
    except UVCoverageError as error:
        raise TextureBakeAnalysisError(error.code, error.message) from error


def _coverage(draw, group, mod_dir, info, buffers, sparse_shape_cache, convention):
    """Compatibility helper for the original mip-0 analysis contract."""
    geometry = _prepare_uv_geometry(
        draw, group, mod_dir, buffers, sparse_shape_cache, convention)
    unit = 4 if info.compressed else 1
    return _rasterize_geometry(
        geometry, info.width, info.height, unit, unit)


def _validate_usage(active_mesh_keys, selected_semantic_key, selected_texture_key,
                    texture_usage, *, require_complete_roles=False):
    if not isinstance(texture_usage, list):
        raise TextureBakeAnalysisError(
            "stale_mesh_state",
            "The model changed before texture coverage could be analyzed.")
    entries = []
    keys = []
    for item in texture_usage:
        if not isinstance(item, dict):
            raise TextureBakeAnalysisError(
                "stale_mesh_state",
                "The model changed before texture coverage could be analyzed.")
        key = item.get("semantic_key")
        if not isinstance(key, str) or not key or key in keys:
            raise TextureBakeAnalysisError(
                "stale_mesh_state",
                "The model changed before texture coverage could be analyzed.")
        has_role_snapshot = "texture_keys" in item
        if require_complete_roles and not has_role_snapshot:
            raise TextureBakeAnalysisError(
                "stale_mesh_state",
                "The model changed before texture coverage could be analyzed.")
        role_keys = item.get("texture_keys")
        legacy_texture_key = item.get("tex_key")
        if not has_role_snapshot:
            # Keep older direct callers safe while the browser rolls forward;
            # a complete role snapshot is authoritative when it is present.
            role_keys = {role: None for role in _TEXTURE_USAGE_ROLES}
            role_keys["diffuse"] = legacy_texture_key
        if (not isinstance(role_keys, dict)
                or set(role_keys) != set(_TEXTURE_USAGE_ROLES)):
            raise TextureBakeAnalysisError(
                "stale_mesh_state",
                "The model changed before texture coverage could be analyzed.")
        if (legacy_texture_key is not None
                and legacy_texture_key != role_keys["diffuse"]):
            raise TextureBakeAnalysisError(
                "stale_mesh_state",
                "The model changed before texture coverage could be analyzed.")
        for role in _TEXTURE_USAGE_ROLES:
            texture_key = role_keys[role]
            if texture_key is not None and not isinstance(texture_key, str):
                raise TextureBakeAnalysisError(
                    "stale_mesh_state",
                    "The model changed before texture coverage could be analyzed.")
            if texture_key is not None and has_role_snapshot:
                parsed_role, relative_path = split_texture_key(
                    texture_key, role)
                if parsed_role != role or not relative_path:
                    raise TextureBakeAnalysisError(
                        "stale_mesh_state",
                        "The model changed before texture coverage could be analyzed.")
        keys.append(key)
        entries.append({
            "semantic_key": key,
            # The alias is retained for the existing diffuse consumer result.
            "tex_key": role_keys["diffuse"],
            "texture_keys": dict(role_keys),
        })
    expected = set(active_mesh_keys or ())
    if set(keys) != expected or len(keys) != len(expected):
        raise TextureBakeAnalysisError(
            "stale_mesh_state",
            "The model changed before texture coverage could be analyzed.")
    if selected_texture_key is not None and not isinstance(
            selected_texture_key, str):
        raise TextureBakeAnalysisError(
            "stale_mesh_state",
            "The model changed before texture coverage could be analyzed.")
    selected = next((item for item in entries
                     if item["semantic_key"] == selected_semantic_key), None)
    if selected is None or selected["tex_key"] != selected_texture_key:
        raise TextureBakeAnalysisError(
            "stale_mesh_state",
            "The model changed before texture coverage could be analyzed.")
    return tuple(entries)


def _resolve_request(context, overrides, active_mesh_keys, selected_semantic_key,
                     selected_texture_key, texture_usage,
                     require_complete_roles=False):
    entries = _validate_usage(
        active_mesh_keys, selected_semantic_key, selected_texture_key,
        texture_usage, require_complete_roles=require_complete_roles)
    selected_path = _texture_path(
        context.mod_dir, selected_texture_key, selected=True)
    info = _inspect_color_texture(selected_path)
    parsed, draws = resolved_draws(context, overrides)
    selected_draw = draws.get(selected_semantic_key)
    if selected_draw is None:
        raise TextureBakeAnalysisError(
            "mesh_not_found", "The selected mesh is no longer available.")
    selected_identity = _physical_identity(selected_path)
    cross_role_usage = []
    cross_role_seen = set()
    for entry in entries:
        for role in _TEXTURE_USAGE_ROLES:
            if role == "diffuse":
                continue
            texture_key = entry["texture_keys"][role]
            path = _usage_texture_path(
                context.mod_dir, texture_key, role)
            if path and _physical_identity(path) == selected_identity:
                value = (entry["semantic_key"], role)
                if value not in cross_role_seen:
                    cross_role_seen.add(value)
                    cross_role_usage.append(value)

    # The live viewer snapshot only describes active bindings.  Parse the
    # authored defaults and every conditional variant as well, since an
    # inactive branch can still own the same physical DDS.
    authored_consumers = {}
    for group in getattr(parsed, "groups", ()):
        for draw in deduplicate_draws(group):
            owned = authored_texture_keys_for_draw(
                draw, context.mod_dir, parsed.game.game)
            for role in _TEXTURE_USAGE_ROLES:
                for texture_key in owned.get(role, ()):
                    path = _usage_texture_path(
                        context.mod_dir, texture_key, role)
                    if not path or _physical_identity(path) != selected_identity:
                        continue
                    if role == "diffuse":
                        if draw.label != selected_semantic_key:
                            authored_consumers.setdefault(
                                draw.label, draws.get(draw.label))
                    else:
                        value = (draw.label, role)
                        if value not in cross_role_seen:
                            cross_role_seen.add(value)
                            cross_role_usage.append(value)
    if cross_role_usage:
        uses = []
        for semantic_key, role in cross_role_usage:
            uses.append(
                f"as a {_TEXTURE_ROLE_LABELS[role]} by {semantic_key}")
        if len(uses) == 1:
            message = f"This DDS is also used {uses[0]}."
        else:
            message = "This DDS is also used " + ", ".join(uses[:-1])
            message += f", and {uses[-1]}."
        raise TextureBakeAnalysisError(
            "cross_role_texture_usage", message, "unsupported")
    consumer_map = {}
    for entry in entries:
        if entry["semantic_key"] == selected_semantic_key:
            continue
        path = _texture_path(context.mod_dir, entry["tex_key"])
        if path and _physical_identity(path) == selected_identity:
            consumer_map[entry["semantic_key"]] = draws.get(entry["semantic_key"])
        elif entry["tex_key"] == selected_texture_key:
            # A consumer claiming the selected key but lacking a resolvable
            # source cannot be safely excluded from the sharing analysis.
            consumer_map[entry["semantic_key"]] = None
    for semantic_key, consumer in authored_consumers.items():
        if semantic_key not in consumer_map:
            consumer_map[semantic_key] = consumer
        elif consumer_map[semantic_key] is None and consumer is not None:
            consumer_map[semantic_key] = consumer
    return entries, selected_path, info, parsed, selected_draw, tuple(
        consumer_map.items())


def _unit_size(info):
    """Return the source-pixel span represented by one edit unit."""
    return (4, 4) if info.compressed else (1, 1)


def _protected_consumer_coverages(geometry, layout, info):
    """Protect neighboring edit units for non-selected texture consumers."""
    unit_width, unit_height = _unit_size(info)
    coverages = []
    for mip in layout.mips:
        pixels = _rasterize_geometry(
            geometry, mip.width, mip.height, 1, 1)
        protected = dilate_pixel_mask(
            pixels.mask, mip.width, mip.height, radius=1)
        mask = collapse_pixel_mask_to_units(
            protected, mip.width, mip.height, unit_width, unit_height)
        grid_width = (mip.width + unit_width - 1) // unit_width
        grid_height = (mip.height + unit_height - 1) // unit_height
        used = [index for index, value in enumerate(mask) if value]
        bounds = None
        if used:
            bounds = (
                min(index % grid_width for index in used),
                min(index // grid_width for index in used),
                max(index % grid_width for index in used),
                max(index // grid_width for index in used),
            )
        coverages.append(UVCoverage(
            grid_width, grid_height, mask, sum(mask), bounds,
            pixels.triangle_count, pixels.degenerate_triangle_count))
    return tuple(coverages)


def _prepare_texture_bake(context, overrides, active_mesh_keys,
                          selected_semantic_key, selected_texture_key,
                          texture_usage, *, require_file_layout=True,
                          require_complete_roles=False, metadata_key=None):
    """Resolve geometry and all per-mip masks for one authoritative request."""
    (entries, selected_path, info, parsed, selected_draw,
     consumers) = _resolve_request(
         context, overrides, active_mesh_keys, selected_semantic_key,
         selected_texture_key, texture_usage, require_complete_roles)
    from core.geometry.identity import mesh_identity_for_draw
    try:
        selected_metadata_key = mesh_identity_for_draw(
            selected_draw[0], selected_draw[1]).key
    except AttributeError:
        # Keep low-level mocked analysis fixtures source-compatible; real
        # resolved DrawCall records always contain the identity fields.
        selected_metadata_key = None
    if metadata_key is not None and metadata_key != selected_metadata_key:
        raise TextureBakeAnalysisError(
            "stale_mesh_state",
            "The selected mesh identity changed before the bake started.")
    layout = inspect_dds_layout(selected_path)
    if layout is None:
        if require_file_layout:
            raise TextureBakeAnalysisError(
                "invalid_dds", "The selected texture is not a valid supported DDS.")
        layout = dds_layout_for_info(info)

    buffers = BufferStore()
    sparse_shape_cache = {}
    convention = geometry_convention_for(parsed.game.game)
    selected_geometry = _prepare_uv_geometry(
        selected_draw[0], selected_draw[1], context.mod_dir, buffers,
        sparse_shape_cache, convention)
    selected_pixels = _rasterize_geometry(
        selected_geometry, layout.mips[0].width, layout.mips[0].height, 1, 1)
    selected_coverages = tuple(
        _rasterize_geometry(
            selected_geometry, mip.width, mip.height, *_unit_size(info))
        for mip in layout.mips)
    selected_consumer = _PreparedConsumer(
        selected_semantic_key, selected_geometry, selected_coverages)

    other_consumers = []
    unresolved = []
    unresolved_details = []
    for semantic_key, consumer in consumers:
        try:
            if consumer is None:
                raise TextureBakeAnalysisError(
                    "geometry_not_available",
                    "The rendered draw geometry could not be prepared.")
            geometry = _prepare_uv_geometry(
                consumer[0], consumer[1], context.mod_dir, buffers,
                sparse_shape_cache, convention)
            coverages = _protected_consumer_coverages(geometry, layout, info)
        except TextureBakeAnalysisError as error:
            unresolved.append(semantic_key)
            unresolved_details.append({
                "semantic_key": semantic_key,
                "code": error.code,
                "error": error.message,
            })
            continue
        other_consumers.append(_PreparedConsumer(
            semantic_key, geometry, coverages))

    safe_masks = []
    shared_masks = []
    shared_with = []
    for level, selected in enumerate(selected_coverages):
        union = bytearray(len(selected.mask))
        for other in other_consumers:
            other_mask = other.coverages[level].mask
            overlap = sum(left and right
                          for left, right in zip(selected.mask, other_mask))
            if level == 0 and overlap:
                shared_with.append({
                    "semantic_key": other.semantic_key,
                    "shared_units": overlap,
                })
            for index, value in enumerate(other_mask):
                union[index] = union[index] or value
        shared = bytearray(
            left and right for left, right in zip(selected.mask, union))
        safe = bytearray(
            value and not shared[index]
            for index, value in enumerate(selected.mask))
        safe_masks.append(safe)
        shared_masks.append(shared)
    return _PreparedTextureBake(
        entries=entries, selected_path=selected_path,
        selected_metadata_key=selected_metadata_key, info=info, layout=layout,
        selected_pixels=selected_pixels, selected_consumer=selected_consumer,
        other_consumers=tuple(other_consumers), safe_masks=tuple(safe_masks),
        shared_masks=tuple(shared_masks), shared_with=tuple(shared_with),
        unresolved=tuple(unresolved), unresolved_details=tuple(unresolved_details))


def _analysis_result(prepared):
    selected = prepared.selected_consumer.coverages[0]
    shared = prepared.shared_masks[0]
    selected_units = selected.count
    shared_units = sum(shared)
    total_units = len(selected.mask)
    mip_shared_levels = [
        level for level, mask in enumerate(prepared.shared_masks) if sum(mask)]
    return {
        "status": "ok",
        "safety": prepared.safety,
        "semantic_key": prepared.selected_consumer.semantic_key,
        "tex_key": next(item["tex_key"] for item in prepared.entries
                         if item["semantic_key"] == prepared.selected_consumer.semantic_key),
        "texture": _texture_details(prepared.selected_path, prepared.info),
        "coverage": {
            "mip_level": 0,
            "unit": "block" if prepared.info.compressed else "pixel",
            "unit_width": 4 if prepared.info.compressed else 1,
            "unit_height": 4 if prepared.info.compressed else 1,
            "total_units": total_units,
            "selected_units": selected_units,
            "unique_units": (
                None if prepared.unresolved else selected_units - shared_units),
            "shared_units": shared_units,
            "selected_percent": 100 * selected_units / total_units,
            "shared_percent_of_selected": (
                100 * shared_units / selected_units if selected_units else 0),
        },
        "mip_summary": {
            "levels": len(prepared.layout.mips),
            "shared_levels": mip_shared_levels,
            "safe_units": sum(sum(mask) for mask in prepared.safe_masks),
            "shared_units": sum(sum(mask) for mask in prepared.shared_masks),
        },
        "shared_with": list(prepared.shared_with),
        "unresolved_consumers": list(prepared.unresolved),
        "diagnostics": {
            "triangles": prepared.selected_consumer.geometry.triangle_count,
            "degenerate_uv_triangles": (
                selected.degenerate_triangle_count),
        },
        "unresolved_consumer_details": list(prepared.unresolved_details),
    }


def _legacy_analysis(context, overrides, active_mesh_keys, selected_semantic_key,
                     selected_texture_key, texture_usage):
    """Keep direct mocked analysis fixtures compatible with the PR 72 API."""
    (entries, selected_path, info, parsed, selected_draw,
     consumers) = _resolve_request(
         context, overrides, active_mesh_keys, selected_semantic_key,
         selected_texture_key, texture_usage)
    buffers = BufferStore()
    sparse_shape_cache = {}
    convention = geometry_convention_for(parsed.game.game)
    selected_coverage = _coverage(
        selected_draw[0], selected_draw[1], context.mod_dir, info,
        buffers, sparse_shape_cache, convention)
    union = bytearray(len(selected_coverage.mask))
    shared_with = []
    unresolved = []
    unresolved_details = []
    for semantic_key, consumer in consumers:
        try:
            if consumer is None:
                raise TextureBakeAnalysisError(
                    "geometry_not_available",
                    "The rendered draw geometry could not be prepared.")
            other = _coverage(
                consumer[0], consumer[1], context.mod_dir, info,
                buffers, sparse_shape_cache, convention)
        except TextureBakeAnalysisError as error:
            unresolved.append(semantic_key)
            unresolved_details.append({
                "semantic_key": semantic_key,
                "code": error.code,
                "error": error.message,
            })
            continue
        overlap = sum(left and right
                      for left, right in zip(selected_coverage.mask, other.mask))
        if overlap:
            shared_with.append({"semantic_key": semantic_key, "shared_units": overlap})
        for index, value in enumerate(other.mask):
            union[index] = union[index] or value
    shared_mask = bytearray(
        left and right for left, right in zip(selected_coverage.mask, union))
    safe_mask = bytearray(
        value and not shared_mask[index]
        for index, value in enumerate(selected_coverage.mask))
    return {
        "status": "ok",
        "safety": "unknown" if unresolved else ("shared" if sum(shared_mask) else "safe"),
        "semantic_key": selected_semantic_key,
        "tex_key": selected_texture_key,
        "texture": _texture_details(selected_path, info),
        "coverage": {
            "mip_level": 0,
            "unit": "block" if info.compressed else "pixel",
            "unit_width": 4 if info.compressed else 1,
            "unit_height": 4 if info.compressed else 1,
            "total_units": len(selected_coverage.mask),
            "selected_units": selected_coverage.count,
            "unique_units": (None if unresolved
                              else selected_coverage.count - sum(shared_mask)),
            "shared_units": sum(shared_mask),
            "selected_percent": 100 * selected_coverage.count / len(selected_coverage.mask),
            "shared_percent_of_selected": (
                100 * sum(shared_mask) / selected_coverage.count
                if selected_coverage.count else 0),
        },
        "mip_summary": {
            "levels": 1, "shared_levels": [0] if sum(shared_mask) else [],
            "safe_units": sum(safe_mask), "shared_units": sum(shared_mask),
        },
        "shared_with": shared_with,
        "unresolved_consumers": unresolved,
        "diagnostics": {
            "triangles": selected_coverage.triangle_count,
            "degenerate_uv_triangles": selected_coverage.degenerate_triangle_count,
        },
        "unresolved_consumer_details": unresolved_details,
    }


def analyze_texture_bake(
        context, overrides, active_mesh_keys, selected_semantic_key,
        selected_texture_key, texture_usage):
    """Analyze selected and same-source draws without modifying the texture."""
    try:
        _entries, selected_path, _info, _parsed, _draw, _consumers = \
            _resolve_request(
                context, overrides, active_mesh_keys, selected_semantic_key,
                selected_texture_key, texture_usage)
        if inspect_dds_layout(selected_path) is None:
            return _legacy_analysis(
                context, overrides, active_mesh_keys, selected_semantic_key,
                selected_texture_key, texture_usage)
        prepared = _prepare_texture_bake(
            context, overrides, active_mesh_keys, selected_semantic_key,
            selected_texture_key, texture_usage, require_file_layout=False)
        return _analysis_result(prepared)
    except TextureBakeAnalysisError as error:
        return _error(error.code, error.message, error.status)
    except Exception:
        return _error(
            "coverage_incomplete",
            "Texture coverage could not be analyzed safely.")


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _read_source(path):
    try:
        with open(path, "rb") as stream:
            return stream.read()
    except OSError as error:
        raise TextureBakeAnalysisError(
            "texture_read_failed", "The source DDS could not be read.") from error


def _assert_source_unchanged(path, original_hash):
    try:
        current_hash = _sha256_bytes(_read_source(path))
    except TextureBakeAnalysisError as error:
        raise TextureBakeAnalysisError(
            "texture_changed_during_bake",
            "The DDS changed while it was being baked.") from error
    if current_hash != original_hash:
        raise TextureBakeAnalysisError(
            "texture_changed_during_bake",
            "The DDS changed while it was being baked.")


def _write_backup(path, original):
    directory = os.path.dirname(path)
    stem = os.path.splitext(os.path.basename(path))[0]
    moment = datetime.now()
    for _index in range(10000):
        backup = os.path.join(
            directory, f"{stem}-{moment.strftime('%Y%m%d%H%M%S')}.dds")
        try:
            with open(backup, "xb") as stream:
                stream.write(original)
                stream.flush()
                os.fsync(stream.fileno())
            return backup
        except FileExistsError:
            moment += timedelta(seconds=1)
            continue
        except OSError as error:
            raise TextureBakeAnalysisError(
                "backup_failed", "The DDS backup could not be created.") from error
    raise TextureBakeAnalysisError(
        "backup_failed", "The DDS backup could not be created.")


def _write_temp(path, data):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="wb", dir=os.path.dirname(path),
                prefix=f".{os.path.basename(path)}.", suffix=".tmp",
                delete=False) as stream:
            temporary = stream.name
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        return temporary
    except OSError as error:
        if temporary and os.path.exists(temporary):
            os.remove(temporary)
        raise TextureBakeAnalysisError(
            "texture_write_failed", "The DDS temporary file could not be written.") from error


def _patch_dds_units(original, candidate, layout, safe_masks, candidate_layout=None):
    """Copy safe color data while retaining source alpha when possible."""
    candidate_layout = candidate_layout or layout
    if (len(original) < layout.payload_end
            or len(candidate) < candidate_layout.payload_end
            or len(safe_masks) != len(layout.mips)):
        raise TextureBakeAnalysisError(
            "texture_validation_failed", "DDS payload layout is invalid.")
    result = bytearray(original)
    for mip, candidate_mip, mask in zip(
            layout.mips, candidate_layout.mips, safe_masks):
        if (mip.width != candidate_mip.width
                or mip.height != candidate_mip.height
                or mip.units_x != candidate_mip.units_x
                or mip.units_y != candidate_mip.units_y
                or mip.bytes_per_unit != candidate_mip.bytes_per_unit
                or len(mask) != mip.units_x * mip.units_y):
            raise TextureBakeAnalysisError(
                "texture_validation_failed", "DDS payload layout is invalid.")
        for index, selected in enumerate(mask):
            if not selected:
                continue
            source_start = candidate_mip.offset + index * candidate_mip.bytes_per_unit
            target_start = mip.offset + index * mip.bytes_per_unit
            candidate_unit = candidate[
                source_start:source_start + candidate_mip.bytes_per_unit]
            if layout.info.format in {"rgba8", "bgra8"}:
                # The first three bytes are the format's RGB channels; byte 3
                # is authored alpha and must remain byte-for-byte unchanged.
                unit = candidate_unit[:3] + original[
                    target_start + 3:target_start + 4]
            elif layout.info.format.startswith(("bc2", "bc3")):
                # BC2/BC3 store alpha in an independent eight-byte sub-block.
                unit = original[target_start:target_start + 8] + candidate_unit[8:]
            else:
                # BC1/BC7 are admitted only after the source was proven opaque.
                unit = candidate_unit
            result[target_start:target_start + mip.bytes_per_unit] = unit
    return bytes(result)


def _validate_candidate(source_layout, candidate_path, candidate_bytes):
    candidate_layout = inspect_dds_layout(candidate_path)
    if candidate_layout is None:
        raise TextureBakeAnalysisError(
            "texconv_output_invalid", "The DDS encoder produced an invalid candidate.")
    if (candidate_layout.info.width != source_layout.info.width
            or candidate_layout.info.height != source_layout.info.height
            or candidate_layout.info.format != source_layout.info.format
            or candidate_layout.info.compressed != source_layout.info.compressed
            or candidate_layout.info.mip_count != source_layout.info.mip_count
            or len(candidate_bytes) < candidate_layout.payload_end):
        raise TextureBakeAnalysisError(
            "texconv_output_invalid", "The DDS encoder changed the source texture layout.")
    return candidate_layout


def _alpha_preservation_error():
    return TextureBakeAnalysisError(
        "alpha_preservation_unsupported",
        "This texture contains transparency that cannot currently be "
        "preserved exactly while recoloring its compressed blocks.",
        "unsupported")


def _bc1_block_is_opaque(block):
    """Return whether a BC1 block decodes to opaque alpha in every texel."""
    if len(block) != 8:
        raise TextureBakeAnalysisError(
            "texture_validation_failed", "DDS payload layout is invalid.")
    color0, color1 = struct.unpack_from("<HH", block)
    if color0 > color1:
        return True
    selectors = struct.unpack_from("<I", block, 4)[0]
    return all(((selectors >> (2 * index)) & 0x3) != 3
               for index in range(16))


def _decode_dds_mip_rgba(source, layout, mip):
    """Decode one source mip using the shared Pillow DDS decoder."""
    if (layout.info.format not in {"bc7_unorm", "bc7_srgb"}
            or len(source) < layout.data_offset
            or len(source) < mip.offset + mip.length):
        raise TextureBakeAnalysisError(
            "texture_validation_failed", "DDS payload layout is invalid.")
    header = bytearray(source[:layout.data_offset])
    struct.pack_into("<II", header, 12, mip.height, mip.width)
    struct.pack_into("<I", header, 28, 1)
    if layout.info.format == "bc7_srgb":
        # Pillow's BC7 decoder accepts the equivalent UNORM payload more
        # consistently than the typed sRGB DXGI variant. The alpha plane is
        # identical, so this header-only conversion is lossless for validation.
        struct.pack_into("<I", header, 128, 98)
    payload = source[mip.offset:mip.offset + mip.length]
    try:
        from PIL import Image
        image = Image.open(io.BytesIO(bytes(header) + payload))
        image.load()
        return image.convert("RGBA")
    except Exception as error:
        raise _alpha_preservation_error() from error


def _validate_bc7_safe_blocks(source, layout, safe_masks):
    """Require decoded alpha 255 for every safe BC7 block at every mip."""
    for mip, mask in zip(layout.mips, safe_masks):
        if not any(mask):
            continue
        try:
            image = _decode_dds_mip_rgba(source, layout, mip)
            if image.size != (mip.width, mip.height):
                raise ValueError("decoded mip dimensions differ from DDS")
            alpha = image.getchannel("A").tobytes()
        except TextureBakeAnalysisError:
            raise
        except Exception as error:
            raise _alpha_preservation_error() from error
        for index, selected in enumerate(mask):
            if not selected:
                continue
            unit_x = (index % mip.units_x) * 4
            unit_y = (index // mip.units_x) * 4
            unit_width = min(4, mip.width - unit_x)
            unit_height = min(4, mip.height - unit_y)
            if any(alpha[row * mip.width + unit_x:
                       row * mip.width + unit_x + unit_width].count(255)
                   != unit_width
                   for row in range(unit_y, unit_y + unit_height)):
                raise _alpha_preservation_error()


def _validate_alpha_preservation(source, layout, safe_masks):
    """Prove alpha is unchanged for every compressed block being replaced."""
    if layout.info.format not in _OPAQUE_ONLY_FORMATS:
        return
    if (len(source) < layout.payload_end
            or len(safe_masks) != len(layout.mips)
            or any(len(mask) != mip.units_x * mip.units_y
                   for mip, mask in zip(layout.mips, safe_masks))):
        raise TextureBakeAnalysisError(
            "texture_validation_failed", "DDS payload layout is invalid.")
    if layout.info.format.startswith("bc1"):
        for mip, mask in zip(layout.mips, safe_masks):
            for index, selected in enumerate(mask):
                if not selected:
                    continue
                start = mip.offset + index * mip.bytes_per_unit
                if not _bc1_block_is_opaque(
                        source[start:start + mip.bytes_per_unit]):
                    raise _alpha_preservation_error()
        return
    _validate_bc7_safe_blocks(source, layout, safe_masks)


def _affected_texture_keys(context, prepared):
    """Resolve every active usage key for the physically changed source."""
    selected_identity = _physical_identity(prepared.selected_path)
    affected = []
    seen_keys = set()
    for entry in prepared.entries:
        role_keys = entry.get("texture_keys")
        if role_keys is None:
            role_keys = {"diffuse": entry.get("tex_key")}
        for role in _TEXTURE_USAGE_ROLES:
            key = role_keys.get(role)
            path = _usage_texture_path(
                context.mod_dir, key, role) if key else None
            if (not key or not path or key in seen_keys
                    or _physical_identity(path) != selected_identity):
                continue
            seen_keys.add(key)
            affected.append(key)
    return affected


def _replacement_completed(source_path, temporary_path, final):
    """Detect a replace call that completed before surfacing an OSError."""
    try:
        if os.path.exists(temporary_path):
            return False
        return _read_source(source_path) == final
    except Exception:
        return False


def bake_texture_color(
        context, overrides, active_mesh_keys, selected_semantic_key,
        selected_texture_key, texture_usage, adjustment, metadata_key=None):
    """Safely bake a non-neutral adjustment into unique DDS units only."""
    committed = False
    success_result = None
    try:
        # Preparation includes the independent mod-root/Asset check and must be
        # repeated for the destructive request, even after a prior analysis.
        prepared = _prepare_texture_bake(
            context, overrides, active_mesh_keys, selected_semantic_key,
            selected_texture_key, texture_usage, require_file_layout=True,
            require_complete_roles=True, metadata_key=metadata_key)
        normalized = normalize_color_adjustment(
            adjustment, reject_invalid=True)
        if normalized is None:
            raise TextureBakeAnalysisError(
                "invalid_color_adjustment", "The color adjustment is invalid.")
        if is_neutral_color_adjustment(normalized):
            raise TextureBakeAnalysisError(
                "no_color_adjustment", "Adjust the mesh color before baking.")
        if prepared.unresolved:
            raise TextureBakeAnalysisError(
                "unknown_texture_coverage",
                "Texture coverage could not be determined safely.")
        mip0_safe = sum(prepared.safe_masks[0])
        if not mip0_safe:
            raise TextureBakeAnalysisError(
                "no_unique_texture_coverage",
                "The selected mesh has no unique texture coverage to bake.")

        original = _read_source(prepared.selected_path)
        original_hash = _sha256_bytes(original)
        image = load_texture_image_full(prepared.selected_path, preserve_alpha=True)
        if image is None or image.size != (
                prepared.info.width, prepared.info.height):
            raise TextureBakeAnalysisError(
                "texture_decode_failed", "The source DDS could not be decoded at full resolution.")
        rgba = image.convert("RGBA")
        _validate_alpha_preservation(
            original, prepared.layout, prepared.safe_masks)
        masked_rgba = adjust_rgba_bytes(
            rgba.tobytes(), rgba.width, rgba.height, normalized,
            prepared.selected_pixels.mask)
        # Resolve all post-commit metadata before the destructive boundary.
        affected = _affected_texture_keys(context, prepared)

        with tempfile.TemporaryDirectory(prefix="modviewer-bake-") as workdir:
            from PIL import Image
            png_path = os.path.join(workdir, "bake.png")
            Image.frombytes("RGBA", rgba.size, masked_rgba).save(png_path, format="PNG")
            try:
                candidate_path = encode_png_to_dds(
                    png_path, workdir, prepared.info.format,
                    prepared.info.mip_count, srgb=True)
            except TexconvUnavailableError as error:
                raise TextureBakeAnalysisError(
                    "texconv_unavailable", str(error)) from error
            except TexconvError as error:
                raise TextureBakeAnalysisError(
                    "texconv_failed", str(error)) from error
            try:
                candidate = _read_source(candidate_path)
            except TextureBakeAnalysisError as error:
                raise TextureBakeAnalysisError(
                    "texconv_output_invalid",
                    "The DDS encoder produced an invalid candidate.") from error
            candidate_layout = _validate_candidate(
                prepared.layout, candidate_path, candidate)
            _validate_alpha_preservation(
                candidate, candidate_layout, prepared.safe_masks)
            final = _patch_dds_units(
                original, candidate, prepared.layout,
                prepared.safe_masks, candidate_layout)

            temporary = _write_temp(prepared.selected_path, final)
            try:
                final_layout = inspect_dds_layout(temporary)
                if final_layout is None:
                    raise TextureBakeAnalysisError(
                        "texture_validation_failed",
                        "The patched DDS failed validation.")
                _validate_candidate(prepared.layout, temporary, final)
                _validate_alpha_preservation(
                    final, final_layout, prepared.safe_masks)
                # The backup is deliberately created only after the complete
                # temporary candidate has passed validation. If a later source
                # race or replace failure occurs, that backup remains useful.
                _assert_source_unchanged(
                    prepared.selected_path, original_hash)
                backup_path = _write_backup(prepared.selected_path, original)
                _assert_source_unchanged(prepared.selected_path, original_hash)
                success_result = {
                    "status": "ok",
                    "tex_key": selected_texture_key,
                    "affected_tex_keys": affected,
                    "texture": _texture_details(
                        prepared.selected_path, prepared.info),
                    "patched": {
                        "mip0_units": mip0_safe,
                        "total_units": sum(
                            len(mask) for mask in prepared.safe_masks),
                        "shared_units_preserved": sum(
                            sum(mask) for mask in prepared.shared_masks),
                    },
                    "backup": {"file": os.path.basename(backup_path)},
                }
                try:
                    os.replace(temporary, prepared.selected_path)
                except OSError as error:
                    if not _replacement_completed(
                            prepared.selected_path, temporary, final):
                        raise TextureBakeAnalysisError(
                            "texture_write_failed",
                            "The DDS could not be replaced safely.") from error
                    committed = True
                    temporary = None
                else:
                    committed = True
                    temporary = None
            finally:
                if temporary and os.path.exists(temporary):
                    os.remove(temporary)
        return success_result
    except TextureBakeAnalysisError as error:
        if committed and success_result is not None:
            success_result["warning"] = "post_bake_cleanup_failed"
            return success_result
        return _error(error.code, error.message, error.status)
    except Exception:
        if committed and success_result is not None:
            success_result["warning"] = "post_bake_cleanup_failed"
            return success_result
        return _error(
            "texture_write_failed", "The DDS could not be baked safely.")


def bake_mesh_texture_color(*args, **kwargs):
    """Compatibility name for callers that use the public bake operation name."""
    return bake_texture_color(*args, **kwargs)


__all__ = [
    "TextureBakeAnalysisError", "analyze_texture_bake",
    "bake_mesh_texture_color", "bake_texture_color", "_patch_dds_units",
]

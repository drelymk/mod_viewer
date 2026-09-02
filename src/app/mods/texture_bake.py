"""Authoritative analysis and safe, unit-preserving DDS color baking."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import io
import logging
import math
import os
import struct
import tempfile
import time

from core.geometry.buffers import BufferStore
from core.geometry.conventions import geometry_convention_for
from core.geometry.packing import pack_draw_geometry
from core.geometry.semantics import (
    authored_texture_keys_for_draw, deduplicate_draws,
)
from core.resource_paths import _canonical, safe_resource_path
from core.textures import TEXTURE_ROLES, split_texture_key
from core.textures import bc7 as _bc7_codec
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
_ALPHA_COUPLED_FORMATS = frozenset({
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
_BC7_ALPHA_WEIGHT_CANDIDATES = (2.0, 4.0, 8.0, 16.0, 32.0)
_BC7_MODE4_WEIGHTS = _bc7_codec.WEIGHTS_3
_BC7_MODE2_WEIGHTS = _bc7_codec.WEIGHTS_2
_BC7_MODE6_WEIGHTS = _bc7_codec.WEIGHTS_4
_LOGGER = logging.getLogger(__name__)


class TextureBakeAnalysisError(ValueError):
    """An expected, stable failure from a coverage or bake request."""

    def __init__(self, code, message, status="error", details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = {} if details is None else details


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
    metadata_key: str | None = None
    adjustment: object = None
    changed: bool = False
    pixel_coverages: tuple = ()


@dataclass(frozen=True)
class _PreparedTextureSave:
    entries: tuple
    selected_path: str
    info: object
    layout: object
    targets: tuple
    preservation_consumers: tuple
    target_pixel_masks: tuple
    safe_masks: tuple
    preserved_masks: tuple
    unresolved: tuple
    unresolved_details: tuple


@dataclass(frozen=True)
class AlphaCompatibilityStats:
    """Internal measurements for one alpha-compatible unit comparison."""

    tested_units: int
    compatible_units: int
    protected_units: int
    changed_pixels: int
    max_delta: int
    rgb_squared_error: int = 0
    rgb_absolute_error: int = 0
    rgb_max_error: int = 0
    source_mode6_tested: int = 0
    source_mode6_compatible: int = 0
    protected_mode_counts: tuple = ()


@dataclass(frozen=True)
class BlockCandidateQuality:
    """RGB error for one authored block against its intended bake target."""

    alpha_exact: bool
    rgb_squared_error: int
    rgb_absolute_error: int
    rgb_max_error: int


@dataclass(frozen=True)
class _BC7CandidateStrategy:
    """One DirectXTex candidate configuration for unresolved BC7 blocks."""

    name: str
    compression_backend: str = "auto"
    bc_flags: str | None = None
    alpha_weight: float | None = None


@dataclass(frozen=True)
class _PreparedTextureBake:
    entries: tuple
    selected_path: str
    selected_metadata_key: str | None
    info: object
    layout: object
    selected_pixels: object
    selected_pixel_coverages: tuple
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


def authored_diffuse_run_consumers(
        group, mod_dir, selected_physical_identity, game_profile=None):
    """Project possible diffuse consumers through one ordered draw group.

    A conditional authored diffuse assignment can start a texture run whose
    following draws inherit the selected texture.  The conservative projection
    keeps every draw after the first possible selected assignment because a
    later boundary is not necessarily active in every reachable state.
    """
    consumers = []
    run_started = False
    for draw in deduplicate_draws(group):
        if not run_started:
            owned = authored_texture_keys_for_draw(
                draw, mod_dir, game_profile)
            run_started = any(
                path and _physical_identity(path) == selected_physical_identity
                for texture_key in owned.get("diffuse", ())
                for path in (_usage_texture_path(
                    mod_dir, texture_key, "diffuse"),))
        if run_started and draw.label not in consumers:
            consumers.append(draw.label)
    return tuple(consumers)


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
                if role == "diffuse":
                    continue
                for texture_key in owned.get(role, ()):
                    path = _usage_texture_path(
                        context.mod_dir, texture_key, role)
                    if not path or _physical_identity(path) != selected_identity:
                        continue
                    value = (draw.label, role)
                    if value not in cross_role_seen:
                        cross_role_seen.add(value)
                        cross_role_usage.append(value)
        for semantic_key in authored_diffuse_run_consumers(
                group, context.mod_dir, selected_identity, parsed.game.game):
            if semantic_key != selected_semantic_key:
                authored_consumers.setdefault(
                    semantic_key, draws.get(semantic_key))
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
    selected_pixel_coverages = tuple(
        _rasterize_geometry(
            selected_geometry, mip.width, mip.height, 1, 1)
        for mip in layout.mips)
    selected_pixels = selected_pixel_coverages[0]
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
        selected_pixels=selected_pixels,
        selected_pixel_coverages=selected_pixel_coverages,
        selected_consumer=selected_consumer,
        other_consumers=tuple(other_consumers), safe_masks=tuple(safe_masks),
        shared_masks=tuple(shared_masks), shared_with=tuple(shared_with),
        unresolved=tuple(unresolved), unresolved_details=tuple(unresolved_details))


def _draw_metadata_key(draw, group):
    """Resolve the durable metadata identity for one resolved draw."""
    from core.geometry.identity import mesh_identity_for_draw
    try:
        return mesh_identity_for_draw(draw, group).key
    except AttributeError:
        return None


def _save_target_value(target, key):
    """Read a save-target field while tolerating the browser's JS spelling."""
    return target.get(key) if key in target else target.get({
        "semantic_key": "semanticKey", "metadata_key": "metadataKey",
    }.get(key, key))


def _adjustment_signature(adjustment):
    return tuple(sorted(adjustment.items()))


def _texture_save_conflict(target_key, other_key, *, neutral=False):
    relation = "a neutral consumer" if neutral else "another color adjustment"
    message = (
        f"Mesh {target_key} overlaps {relation} on the selected texture; "
        "the texture cannot preserve both colors.")
    return TextureBakeAnalysisError(
        "incompatible_texture_color_usage", message, "unsupported", {
            "meshes": [target_key, other_key],
            "target_semantic_key": target_key,
            "conflicting_semantic_key": other_key,
            "conflict": "neutral" if neutral else "adjustment",
        })


def _prepare_texture_save(context, overrides, active_mesh_keys,
                          selected_texture_key, targets, texture_usage,
                          *, require_file_layout=True):
    """Resolve all changed consumers and preservation constraints for one DDS."""
    if not isinstance(targets, list) or not targets:
        raise TextureBakeAnalysisError(
            "no_color_adjustment", "Adjust a mesh color before saving.")
    if any(not isinstance(target, dict) for target in targets):
        raise TextureBakeAnalysisError(
            "stale_mesh_state", "The model changed before the texture save started.")
    first_semantic_key = _save_target_value(targets[0], "semantic_key")
    entries, selected_path, info, parsed, selected_draw, consumers = \
        _resolve_request(
            context, overrides, active_mesh_keys, first_semantic_key,
            selected_texture_key, texture_usage, require_complete_roles=True)
    layout = inspect_dds_layout(selected_path)
    if layout is None:
        if require_file_layout:
            raise TextureBakeAnalysisError(
                "invalid_dds", "The selected texture is not a valid supported DDS.")
        layout = dds_layout_for_info(info)

    entry_by_key = {entry["semantic_key"]: entry for entry in entries}
    draw_by_key = {first_semantic_key: selected_draw}
    draw_by_key.update({key: value for key, value in consumers if value is not None})
    target_keys = set()
    requested_targets = []
    for target in targets:
        semantic_key = _save_target_value(target, "semantic_key")
        metadata_key = _save_target_value(target, "metadata_key")
        if (not isinstance(semantic_key, str) or not semantic_key
                or semantic_key in target_keys
                or not isinstance(metadata_key, str) or not metadata_key):
            raise TextureBakeAnalysisError(
                "stale_mesh_state",
                "The texture save target identity is invalid.")
        target_keys.add(semantic_key)
        entry = entry_by_key.get(semantic_key)
        draw_pair = draw_by_key.get(semantic_key)
        target_path = _texture_path(
            context.mod_dir, entry["tex_key"] if entry else None)
        if (entry is None or draw_pair is None or target_path is None
                or _physical_identity(target_path)
                != _physical_identity(selected_path)):
            raise TextureBakeAnalysisError(
                "stale_mesh_state",
                "A texture save target no longer belongs to the selected DDS.")
        actual_metadata_key = _draw_metadata_key(draw_pair[0], draw_pair[1])
        if actual_metadata_key != metadata_key:
            raise TextureBakeAnalysisError(
                "stale_mesh_state",
                "A texture save target identity changed before the save started.")
        adjustment = normalize_color_adjustment(
            target.get("adjustment"), reject_invalid=True)
        if adjustment is None:
            raise TextureBakeAnalysisError(
                "invalid_color_adjustment", "The color adjustment is invalid.")
        if is_neutral_color_adjustment(adjustment):
            raise TextureBakeAnalysisError(
                "no_color_adjustment", "Adjust a mesh color before saving.")
        requested_targets.append((semantic_key, metadata_key, draw_pair,
                                  adjustment))

    buffers = BufferStore()
    sparse_shape_cache = {}
    convention = geometry_convention_for(parsed.game.game)
    prepared_targets = []
    unresolved = []
    unresolved_details = []
    for semantic_key, metadata_key, draw_pair, adjustment in requested_targets:
        try:
            geometry = _prepare_uv_geometry(
                draw_pair[0], draw_pair[1], context.mod_dir, buffers,
                sparse_shape_cache, convention)
            pixel_coverages = tuple(
                _rasterize_geometry(
                    geometry, mip.width, mip.height, 1, 1)
                for mip in layout.mips)
            coverages = tuple(
                _rasterize_geometry(
                    geometry, mip.width, mip.height, *_unit_size(info))
                for mip in layout.mips)
        except TextureBakeAnalysisError as error:
            unresolved.append(semantic_key)
            unresolved_details.append({
                "semantic_key": semantic_key,
                "code": error.code,
                "error": error.message,
            })
            continue
        prepared_targets.append(_PreparedConsumer(
            semantic_key, geometry, coverages, metadata_key, adjustment, True,
            pixel_coverages))

    # Every same-DDS consumer absent from the target list is a preservation
    # constraint. This includes hidden active draws and authored conditional
    # variants returned by _resolve_request.
    preservation_consumers = []
    for semantic_key, consumer in consumers:
        if semantic_key in target_keys:
            continue
        try:
            if consumer is None:
                raise TextureBakeAnalysisError(
                    "geometry_not_available",
                    "The rendered draw geometry could not be prepared.")
            geometry = _prepare_uv_geometry(
                consumer[0], consumer[1], context.mod_dir, buffers,
                sparse_shape_cache, convention)
            coverages = tuple(
                _rasterize_geometry(
                    geometry, mip.width, mip.height, *_unit_size(info))
                for mip in layout.mips)
            pixel_coverages = tuple(
                _rasterize_geometry(
                    geometry, mip.width, mip.height, 1, 1)
                for mip in layout.mips)
        except TextureBakeAnalysisError as error:
            unresolved.append(semantic_key)
            unresolved_details.append({
                "semantic_key": semantic_key,
                "code": error.code,
                "error": error.message,
            })
            continue
        preservation_consumers.append(_PreparedConsumer(
            semantic_key, geometry, coverages, pixel_coverages=pixel_coverages))

    target_pixel_masks = []
    safe_masks = []
    preserved_masks = []
    for level, mip in enumerate(layout.mips):
        pixel_count = mip.width * mip.height
        target_union = bytearray(pixel_count)
        target_claims = [None] * pixel_count
        for target in prepared_targets:
            mask = target.pixel_coverages[level].mask
            signature = _adjustment_signature(target.adjustment)
            for index, selected in enumerate(mask):
                if not selected:
                    continue
                previous = target_claims[index]
                if previous is not None and previous[0] != signature:
                    raise _texture_save_conflict(
                        previous[1], target.semantic_key)
                target_claims[index] = (signature, target.semantic_key)
                target_union[index] = 1

        neutral_union = bytearray(pixel_count)
        for consumer in preservation_consumers:
            for index, selected in enumerate(
                    consumer.pixel_coverages[level].mask):
                if selected:
                    neutral_union[index] = 1
        for target in prepared_targets:
            actual = target.pixel_coverages[level].mask
            overlap = next((index for index, selected in enumerate(actual)
                            if selected and neutral_union[index]), None)
            if overlap is not None:
                consumer = next(
                    item for item in preservation_consumers
                    if item.pixel_coverages[level].mask[overlap])
                raise _texture_save_conflict(
                    target.semantic_key, consumer.semantic_key, neutral=True)
        try:
            target_units = collapse_pixel_mask_to_units(
                target_union, mip.width, mip.height, *_unit_size(info))
        except UVCoverageError as error:
            raise TextureBakeAnalysisError(error.code, error.message) from error
        preserved = bytearray()
        for consumer in preservation_consumers:
            mask = consumer.coverages[level].mask
            if not preserved:
                preserved = bytearray(len(mask))
            for index, selected in enumerate(mask):
                preserved[index] = preserved[index] or selected
        if not target_units:
            target_units = bytearray(mip.units_x * mip.units_y)
        if not preserved:
            preserved = bytearray(mip.units_x * mip.units_y)
        # A block may contain both changed and neutral pixels. The composite
        # target preserves the neutral pixels inside that block, so block
        # overlap alone must not turn into a logical color conflict.
        safe = bytearray(target_units)
        target_pixel_masks.append(bytearray(target_union))
        safe_masks.append(safe)
        preserved_masks.append(preserved)

    if unresolved:
        # Preparation is still useful for diagnostics, but Save cannot safely
        # proceed when any same-DDS consumer is unknown.
        raise TextureBakeAnalysisError(
            "unknown_texture_coverage",
            "Texture coverage could not be determined safely.",
            details={"unresolved_consumers": list(unresolved),
                     "consumers": list(unresolved_details)})
    if not any(safe_masks[0]):
        raise TextureBakeAnalysisError(
            "incompatible_texture_color_usage",
            "The changed meshes have no writable texture units.", "unsupported",
            {"meshes": [target.semantic_key for target in prepared_targets]})
    return _PreparedTextureSave(
        entries=entries, selected_path=selected_path, info=info, layout=layout,
        targets=tuple(prepared_targets),
        preservation_consumers=tuple(preservation_consumers),
        target_pixel_masks=tuple(target_pixel_masks), safe_masks=tuple(safe_masks),
        preserved_masks=tuple(preserved_masks), unresolved=tuple(unresolved),
        unresolved_details=tuple(unresolved_details))


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
        return _error(error.code, error.message, error.status,
                      **({"details": error.details} if error.details else {}))
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
                # BC1/BC7 units are copied only after decoded alpha was proven
                # identical for the selected source pixels.
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


def _validate_mip_candidate(source_layout, source_mip, candidate_path,
                            candidate_bytes):
    """Validate one encoded mip against its authored source-mip layout."""
    candidate_layout = inspect_dds_layout(candidate_path)
    if candidate_layout is None:
        raise TextureBakeAnalysisError(
            "texconv_output_invalid",
            "The DDS encoder produced an invalid mip candidate.")
    candidate_mip = candidate_layout.mips[0] if candidate_layout.mips else None
    if (candidate_layout.info.format != source_layout.info.format
            or candidate_layout.info.compressed != source_layout.info.compressed
            or candidate_layout.info.mip_count != 1
            or candidate_layout.info.width != source_mip.width
            or candidate_layout.info.height != source_mip.height
            or candidate_mip is None
            or candidate_mip.width != source_mip.width
            or candidate_mip.height != source_mip.height
            or candidate_mip.units_x != source_mip.units_x
            or candidate_mip.units_y != source_mip.units_y
            or candidate_mip.bytes_per_unit != source_mip.bytes_per_unit
            or len(candidate_bytes) < candidate_layout.payload_end):
        raise TextureBakeAnalysisError(
            "texconv_output_invalid",
            "The DDS encoder changed the source mip layout.")
    return candidate_layout


def _validate_atlas_candidate(source_layout, atlas_width, atlas_height,
                              candidate_path, candidate_bytes):
    """Validate one DDS candidate whose pixels are packed safe source blocks."""
    candidate_layout = inspect_dds_layout(candidate_path)
    if candidate_layout is None:
        raise TextureBakeAnalysisError(
            "texconv_output_invalid",
            "The DDS encoder produced an invalid atlas candidate.")
    candidate_mip = candidate_layout.mips[0] if candidate_layout.mips else None
    expected_units_x = (atlas_width + 3) // 4
    expected_units_y = (atlas_height + 3) // 4
    if (candidate_layout.info.format != source_layout.info.format
            or candidate_layout.info.compressed != source_layout.info.compressed
            or candidate_layout.info.mip_count != 1
            or candidate_layout.info.width != atlas_width
            or candidate_layout.info.height != atlas_height
            or candidate_mip is None
            or candidate_mip.width != atlas_width
            or candidate_mip.height != atlas_height
            or candidate_mip.units_x != expected_units_x
            or candidate_mip.units_y != expected_units_y
            or candidate_mip.bytes_per_unit
            != source_layout.mips[0].bytes_per_unit
            or len(candidate_bytes) < candidate_layout.payload_end):
        raise TextureBakeAnalysisError(
            "texconv_output_invalid",
            "The DDS encoder changed the safe-block atlas layout.")
    return candidate_layout


def _alpha_preservation_error(*, mip0_protected=False, stats=None):
    details = {}
    if stats is not None:
        details = {
            "mip": 0,
            "unresolved_units": stats.protected_units,
            "bc7_modes": {
                str(mode): count
                for mode, count in stats.protected_mode_counts
            },
        }
    if mip0_protected:
        return TextureBakeAnalysisError(
            "alpha_preservation_unsupported",
            "The texture could not be recolored uniformly while preserving "
            "alpha; some mip-0 blocks remain unresolved.",
            "unsupported", details)
    return TextureBakeAnalysisError(
        "alpha_preservation_unsupported",
        "The texture's alpha channel could not be preserved exactly while "
        "recompressing any unique color blocks.",
        "unsupported", details)


def _bc1_block_alpha_mask(block):
    """Return the decoded binary alpha values for one BC1 block."""
    if len(block) != 8:
        raise TextureBakeAnalysisError(
            "texture_validation_failed", "DDS payload layout is invalid.")
    color0, color1 = struct.unpack_from("<HH", block)
    if color0 > color1:
        return (255,) * 16
    selectors = struct.unpack_from("<I", block, 4)[0]
    return tuple(
        0 if ((selectors >> (2 * index)) & 0x3) == 3 else 255
        for index in range(16))


def _bc7_block_mode(block):
    """Return the BC7 mode encoded by the unary prefix in byte zero."""
    if len(block) != 16:
        raise TextureBakeAnalysisError(
            "texture_validation_failed", "DDS payload layout is invalid.")
    for mode in range(8):
        if block[0] & (1 << mode):
            return mode
    raise TextureBakeAnalysisError(
        "texture_validation_failed", "DDS payload contains an invalid BC7 block.")


def _bc7_mode_mask(original, source_mip, pending_mask, mode):
    """Return pending source blocks whose BC7 mode matches *mode*."""
    if len(pending_mask) != source_mip.units_x * source_mip.units_y:
        raise TextureBakeAnalysisError(
            "texture_validation_failed", "DDS payload layout is invalid.")
    result = bytearray(len(pending_mask))
    for index, pending in enumerate(pending_mask):
        if not pending:
            continue
        start = source_mip.offset + index * source_mip.bytes_per_unit
        block = original[start:start + source_mip.bytes_per_unit]
        if _bc7_block_mode(block) == mode:
            result[index] = 1
    return result


def _bc7_modes_mask(original, source_mip, pending_mask, modes):
    """Return pending source blocks whose BC7 mode is in *modes*."""
    if len(pending_mask) != source_mip.units_x * source_mip.units_y:
        raise TextureBakeAnalysisError(
            "texture_validation_failed", "DDS payload layout is invalid.")
    modes = frozenset(modes)
    result = bytearray(len(pending_mask))
    for index, pending in enumerate(pending_mask):
        if not pending:
            continue
        start = source_mip.offset + index * source_mip.bytes_per_unit
        block = original[start:start + source_mip.bytes_per_unit]
        if _bc7_block_mode(block) in modes:
            result[index] = 1
    return result


def _bc7_codec_call(function, *args):
    """Translate codec validation failures into the bake API error type."""
    try:
        return function(*args)
    except _bc7_codec.BC7Error as error:
        raise TextureBakeAnalysisError(
            "texture_validation_failed", str(error)) from error


def _bc7_get_bits(bits, start, count):
    """Read a little-endian BC7 bit field for legacy test helpers."""
    return _bc7_codec.get_bits(bits, start, count)


def _bc7_set_bits(bits, start, count, value):
    """Replace a little-endian BC7 bit field for legacy test helpers."""
    return _bc7_codec.set_bits(bits, start, count, value)


def _bc7_mode6_parameters(block):
    return _bc7_codec_call(_bc7_codec.mode6_parameters, block)


def _bc7_mode6_decode_block(block):
    return _bc7_codec_call(_bc7_codec.mode6_decode_block, block)


def _recolor_mode6_block(block, target_pixels, valid_width, valid_height):
    return _bc7_codec_call(
        _bc7_codec.recolor_mode6, block, target_pixels,
        valid_width, valid_height)


def _bc7_separate_parameters(block):
    return _bc7_codec_call(_bc7_codec.separate_parameters, block)


def _bc7_separate_decode_block(block):
    return _bc7_codec_call(_bc7_codec.separate_decode_block, block)


def _recolor_bc7_separate_block(block, target_pixels, valid_width,
                                 valid_height):
    return _bc7_codec_call(
        _bc7_codec.recolor_separate, block, target_pixels,
        valid_width, valid_height)


def _bc7_mode7_parameters(block):
    return _bc7_codec_call(_bc7_codec.mode7_parameters, block)


def _bc7_mode7_subset_for_pixel(partition, pixel):
    return _bc7_codec_call(
        _bc7_codec.mode7_subset_for_pixel, partition, pixel)


def _bc7_mode7_decode_block(block):
    return _bc7_codec_call(_bc7_codec.mode7_decode_block, block)


def _recolor_bc7_mode7_block(block, target_pixels, valid_width,
                             valid_height):
    return _bc7_codec_call(
        _bc7_codec.recolor_mode7, block, target_pixels,
        valid_width, valid_height)


def _bc7_candidate_strategy_groups(format_name, compression_backend="auto"):
    """Return candidate passes, ordered from default to targeted fallbacks."""
    default = _BC7CandidateStrategy(
        "default", compression_backend=compression_backend)
    if not format_name.startswith("bc7"):
        return ((default,),)
    weighted = tuple(
        _BC7CandidateStrategy(
            f"alpha-weight-{weight:g}",
            compression_backend=compression_backend, alpha_weight=weight)
        for weight in _BC7_ALPHA_WEIGHT_CANDIDATES)
    mode6_weighted = tuple(
        _BC7CandidateStrategy(
            f"mode6-alpha-weight-{weight:g}", bc_flags="q",
            compression_backend=compression_backend, alpha_weight=weight)
        for weight in _BC7_ALPHA_WEIGHT_CANDIDATES)
    return ((default,), weighted, mode6_weighted)


def _decode_alpha_coupled_mip_rgba(source, layout, mip):
    """Decode one authored BC1/BC7 mip using the shared Pillow decoder."""
    if (layout.info.format not in _ALPHA_COUPLED_FORMATS
            or len(source) < layout.data_offset
            or len(source) < mip.offset + mip.length):
        raise TextureBakeAnalysisError(
            "texture_validation_failed", "DDS payload layout is invalid.")
    header = bytearray(source[:layout.data_offset])
    struct.pack_into("<II", header, 12, mip.height, mip.width)
    struct.pack_into("<I", header, 28, 1)
    if layout.info.format == "bc1_srgb":
        # Pillow's typed sRGB BC1 decoder is not consistently available, but
        # the payload and decoded alpha are identical under the UNORM tag.
        struct.pack_into("<I", header, 128, 71)
    elif layout.info.format == "bc7_srgb":
        # The same header-only workaround is used for BC7 sRGB validation.
        struct.pack_into("<I", header, 128, 98)
    payload = source[mip.offset:mip.offset + mip.length]
    try:
        from PIL import Image
        image = Image.open(io.BytesIO(bytes(header) + payload))
        image.load()
        image = image.convert("RGBA")
        if image.size != (mip.width, mip.height):
            raise ValueError("decoded mip dimensions differ from DDS")
        return image
    except TextureBakeAnalysisError:
        raise
    except Exception as error:
        raise _alpha_preservation_error() from error


def _decode_dds_mip_rgba(source, layout, mip):
    """Compatibility alias for the authored alpha-coupled mip decoder."""
    return _decode_alpha_coupled_mip_rgba(source, layout, mip)


def _unit_bounds(mip, index):
    unit_x = (index % mip.units_x) * 4
    unit_y = (index // mip.units_x) * 4
    return (unit_x, unit_y, min(4, mip.width - unit_x),
            min(4, mip.height - unit_y))


def _alpha_compatibility_for_mapping(
        original, original_layout, source_mip, candidate, candidate_layout,
        candidate_mip, safe_mask, candidate_indices=None, source_image=None,
        candidate_image=None):
    """Compare source blocks with same-sized or compact-atlas candidates."""
    if candidate_indices is None:
        candidate_indices = tuple(range(len(safe_mask)))
    if (original_layout.info.format not in _ALPHA_COUPLED_FORMATS
            or candidate_layout.info.format != original_layout.info.format
            or len(safe_mask) != source_mip.units_x * source_mip.units_y
            or len(candidate_indices) != len(safe_mask)
            or candidate_mip is None
            or source_mip.bytes_per_unit != candidate_mip.bytes_per_unit
            or len(original) < source_mip.offset + source_mip.length
            or len(candidate) < candidate_mip.offset + candidate_mip.length):
        raise TextureBakeAnalysisError(
            "texture_validation_failed", "DDS payload layout is invalid.")
    if any(index < 0 or index >= candidate_mip.units_x * candidate_mip.units_y
           for index in candidate_indices):
        raise TextureBakeAnalysisError(
            "texture_validation_failed", "DDS payload layout is invalid.")

    compatible = bytearray(len(safe_mask))
    tested_units = 0
    compatible_units = 0
    changed_pixels = 0
    max_delta = 0

    if original_layout.info.format.startswith("bc1"):
        for index, selected in enumerate(safe_mask):
            if not selected:
                continue
            tested_units += 1
            source_start = source_mip.offset + index * source_mip.bytes_per_unit
            candidate_index = candidate_indices[index]
            candidate_start = (candidate_mip.offset
                               + candidate_index * candidate_mip.bytes_per_unit)
            source_alpha = _bc1_block_alpha_mask(
                original[source_start:source_start + source_mip.bytes_per_unit])
            candidate_alpha = _bc1_block_alpha_mask(
                candidate[candidate_start:
                          candidate_start + candidate_mip.bytes_per_unit])
            _, _, unit_width, unit_height = _unit_bounds(source_mip, index)
            matches = True
            for row in range(unit_height):
                for column in range(unit_width):
                    pixel = row * 4 + column
                    delta = abs(source_alpha[pixel] - candidate_alpha[pixel])
                    if delta:
                        matches = False
                        changed_pixels += 1
                        max_delta = max(max_delta, delta)
            if matches:
                compatible[index] = 1
                compatible_units += 1
    else:
        if source_image is None:
            source_image = _decode_alpha_coupled_mip_rgba(
                original, original_layout, source_mip)
        if candidate_image is None:
            candidate_image = _decode_alpha_coupled_mip_rgba(
                candidate, candidate_layout, candidate_mip)
        if (source_image.size != (source_mip.width, source_mip.height)
                or candidate_image.size != (candidate_mip.width,
                                             candidate_mip.height)):
            raise TextureBakeAnalysisError(
                "texture_validation_failed",
                "Decoded DDS alpha dimensions are invalid.")
        source_alpha = source_image.getchannel("A").tobytes()
        candidate_alpha = candidate_image.getchannel("A").tobytes()
        for index, selected in enumerate(safe_mask):
            if not selected:
                continue
            tested_units += 1
            source_x, source_y, unit_width, unit_height = _unit_bounds(
                source_mip, index)
            candidate_index = candidate_indices[index]
            candidate_x, candidate_y, _, _ = _unit_bounds(
                candidate_mip, candidate_index)
            matches = True
            for row in range(unit_height):
                for column in range(unit_width):
                    source_pixel = ((source_y + row) * source_mip.width
                                    + source_x + column)
                    candidate_pixel = ((candidate_y + row) * candidate_mip.width
                                       + candidate_x + column)
                    delta = abs(source_alpha[source_pixel]
                                - candidate_alpha[candidate_pixel])
                    if delta:
                        matches = False
                        changed_pixels += 1
                        max_delta = max(max_delta, delta)
            if matches:
                compatible[index] = 1
                compatible_units += 1

    return compatible, AlphaCompatibilityStats(
        tested_units=tested_units,
        compatible_units=compatible_units,
        protected_units=tested_units - compatible_units,
        changed_pixels=changed_pixels,
        max_delta=max_delta)


def _alpha_compatibility_for_units(
        original, original_layout, source_mip, candidate, candidate_layout,
        candidate_mip, safe_mask):
    """Compare decoded alpha for safe units and return mask plus statistics."""
    if (candidate_mip is None
            or source_mip.width != candidate_mip.width
            or source_mip.height != candidate_mip.height
            or source_mip.units_x != candidate_mip.units_x
            or source_mip.units_y != candidate_mip.units_y):
        raise TextureBakeAnalysisError(
            "texture_validation_failed", "DDS payload layout is invalid.")
    return _alpha_compatibility_for_mapping(
        original, original_layout, source_mip, candidate, candidate_layout,
        candidate_mip, safe_mask)


def _alpha_compatible_units(
        original, original_layout, source_mip, candidate, candidate_layout,
        candidate_mip, safe_mask):
    """Return the safe units whose decoded alpha survives recompression."""
    compatible, _stats = _alpha_compatibility_for_units(
        original, original_layout, source_mip, candidate, candidate_layout,
        candidate_mip, safe_mask)
    return compatible


def _validate_patched_alpha(original, final, layout, writable_masks):
    """Exhaustively validate decoded alpha for tests and debug tooling."""
    if layout.info.format not in _ALPHA_COUPLED_FORMATS:
        return
    try:
        for source_mip, mask in zip(layout.mips, writable_masks):
            if not any(mask):
                continue
            source_alpha_layout = layout
            final_layout = layout
            compatible = _alpha_compatible_units(
                original, source_alpha_layout, source_mip,
                final, final_layout, source_mip, mask)
            if any(selected and not compatible[index]
                   for index, selected in enumerate(mask)):
                raise TextureBakeAnalysisError(
                    "texture_validation_failed",
                    "The final DDS changed alpha in a writable block.")
    except TextureBakeAnalysisError as error:
        if error.code == "texture_validation_failed":
            raise
        raise TextureBakeAnalysisError(
            "texture_validation_failed",
            "The final DDS alpha could not be validated.") from error
    except Exception as error:
        raise TextureBakeAnalysisError(
            "texture_validation_failed",
            "The final DDS alpha could not be validated.") from error


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


def _build_safe_block_atlas(source_image, source_mip, pixel_mask, safe_mask,
                            adjustment=None, target_image=None):
    """Pack safe source blocks into a compact 4x4-cell RGBA bake image."""
    source_width, source_height = source_mip.width, source_mip.height
    if source_image.size != (source_width, source_height):
        raise TextureBakeAnalysisError(
            "texture_validation_failed",
            "Decoded DDS source dimensions are invalid.")
    source_rgba = source_image.tobytes()
    target_rgba = target_image.tobytes() if target_image is not None else None
    if target_image is not None and target_image.size != source_image.size:
        raise TextureBakeAnalysisError(
            "texture_validation_failed",
            "Composited DDS target dimensions are invalid.")
    safe_indices = tuple(
        index for index, selected in enumerate(safe_mask) if selected)
    if not safe_indices:
        raise TextureBakeAnalysisError(
            "texture_validation_failed", "No safe blocks were provided.")
    if len(pixel_mask) != source_width * source_height:
        raise TextureBakeAnalysisError(
            "texture_validation_failed",
            "Selected pixel coverage does not match the DDS mip size.")

    atlas_columns = max(1, math.isqrt(len(safe_indices)))
    if atlas_columns * atlas_columns < len(safe_indices):
        atlas_columns += 1
    atlas_rows = (len(safe_indices) + atlas_columns - 1) // atlas_columns
    atlas_width, atlas_height = atlas_columns * 4, atlas_rows * 4
    atlas_rgba = bytearray(atlas_width * atlas_height * 4)
    atlas_mask = bytearray(atlas_width * atlas_height)

    for atlas_index, source_index in enumerate(safe_indices):
        source_x, source_y, unit_width, unit_height = _unit_bounds(
            source_mip, source_index)
        atlas_x = (atlas_index % atlas_columns) * 4
        atlas_y = (atlas_index // atlas_columns) * 4
        for row in range(4):
            source_row = min(source_y + row, source_height - 1)
            for column in range(4):
                source_column = min(source_x + column, source_width - 1)
                source_pixel = (source_row * source_width + source_column) * 4
                atlas_pixel = ((atlas_y + row) * atlas_width
                               + atlas_x + column) * 4
                if (target_rgba is not None and row < unit_height
                        and column < unit_width
                        and pixel_mask[source_row * source_width + source_column]):
                    atlas_rgba[atlas_pixel:atlas_pixel + 4] = target_rgba[
                        source_pixel:source_pixel + 4]
                else:
                    atlas_rgba[atlas_pixel:atlas_pixel + 4] = source_rgba[
                        source_pixel:source_pixel + 4]
                if row < unit_height and column < unit_width:
                    atlas_mask[(atlas_y + row) * atlas_width
                               + atlas_x + column] = pixel_mask[
                        (source_y + row) * source_width
                        + source_x + column]

    adjusted_atlas = (bytes(atlas_rgba) if target_rgba is not None else
                      adjust_rgba_bytes(
                          bytes(atlas_rgba), atlas_width, atlas_height,
                          adjustment, atlas_mask))
    if adjusted_atlas[3::4] != atlas_rgba[3::4]:
        raise TextureBakeAnalysisError(
            "texture_validation_failed",
            "Color adjustment changed the source alpha channel.")
    return adjusted_atlas, safe_indices, atlas_width, atlas_height


def _block_candidate_quality(target_rgba, candidate_rgba,
                             source_mip, candidate_mip, source_index,
                             candidate_index, alpha_exact):
    """Measure decoded RGB error against one block's intended target."""
    if (len(target_rgba) != candidate_mip.width * candidate_mip.height * 4
            or len(candidate_rgba)
            != candidate_mip.width * candidate_mip.height * 4):
        raise TextureBakeAnalysisError(
            "texture_validation_failed",
            "Decoded DDS candidate dimensions are invalid.")
    _source_x, source_y, unit_width, unit_height = _unit_bounds(
        source_mip, source_index)
    candidate_x, candidate_y, _, _ = _unit_bounds(
        candidate_mip, candidate_index)
    squared_error = 0
    absolute_error = 0
    max_error = 0
    for row in range(unit_height):
        for column in range(unit_width):
            candidate_pixel = ((candidate_y + row) * candidate_mip.width
                               + candidate_x + column) * 4
            for channel in range(3):
                delta = (candidate_rgba[candidate_pixel + channel]
                         - target_rgba[candidate_pixel + channel])
                squared_error += delta * delta
                absolute_error += abs(delta)
                max_error = max(max_error, abs(delta))
    return BlockCandidateQuality(
        alpha_exact=bool(alpha_exact),
        rgb_squared_error=squared_error,
        rgb_absolute_error=absolute_error,
        rgb_max_error=max_error)


def _candidate_quality_key(quality):
    """Order alpha-exact candidates by decoded RGB error."""
    return (quality.rgb_squared_error, quality.rgb_absolute_error,
            quality.rgb_max_error)


def _atlas_block_pixels(atlas_rgba, atlas_width, atlas_index):
    """Return one compact-atlas block as sixteen RGBA pixel tuples."""
    atlas_x = (atlas_index % (atlas_width // 4)) * 4
    atlas_y = (atlas_index // (atlas_width // 4)) * 4
    return tuple(
        tuple(atlas_rgba[((atlas_y + row) * atlas_width
                          + atlas_x + column) * 4:
                         ((atlas_y + row) * atlas_width
                          + atlas_x + column + 1) * 4])
        for row in range(4) for column in range(4))


def _bc7_candidate_quality(target_pixels, candidate_pixels,
                           valid_width, valid_height, alpha_exact):
    """Measure RGB error for a decoded BC7 fallback block."""
    squared_error = 0
    absolute_error = 0
    max_error = 0
    for row in range(valid_height):
        for column in range(valid_width):
            index = row * 4 + column
            for channel in range(3):
                delta = (candidate_pixels[index][channel]
                         - target_pixels[index][channel])
                squared_error += delta * delta
                absolute_error += abs(delta)
                max_error = max(max_error, abs(delta))
    return BlockCandidateQuality(
        alpha_exact=bool(alpha_exact),
        rgb_squared_error=squared_error,
        rgb_absolute_error=absolute_error,
        rgb_max_error=max_error)


def _log_alpha_quality(alpha_stats):
    """Log alpha compatibility and unresolved source-mode diagnostics."""
    if not alpha_stats:
        return
    _LOGGER.debug(
        "texture bake alpha_quality=%s",
        [{"tested_units": item.tested_units,
          "compatible_units": item.compatible_units,
          "protected_units": item.protected_units,
          "changed_pixels": item.changed_pixels,
          "max_delta": item.max_delta,
          "rgb_squared_error": item.rgb_squared_error,
          "rgb_absolute_error": item.rgb_absolute_error,
          "rgb_max_error": item.rgb_max_error,
          "source_mode6_tested": item.source_mode6_tested,
          "source_mode6_compatible": item.source_mode6_compatible,
          "protected_mode_counts": dict(item.protected_mode_counts)}
         for item in alpha_stats])


def _encode_bc7_source_fallback(
        original, prepared, source_mip, source_image, pixel_mask,
        pending_mask, adjustment, timings, modes, timing_key,
        target_image=None):
    """Recolor supported BC7 modes while preserving source alpha exactly."""
    fallback_mask = _bc7_modes_mask(
        original, source_mip, pending_mask, modes)
    if not any(fallback_mask):
        return {}, AlphaCompatibilityStats(0, 0, 0, 0, 0)

    from PIL import Image

    started = time.perf_counter()
    atlas_rgba, safe_indices, atlas_width, _atlas_height = (
        _build_safe_block_atlas(
            source_image, source_mip, pixel_mask, fallback_mask, adjustment,
            target_image=target_image))
    target_image = Image.frombytes(
        "RGBA", (atlas_width, _atlas_height), atlas_rgba)
    target_rgba = target_image.tobytes()
    source_rgba = source_image.tobytes()
    timings["color_adjust"] = timings.get("color_adjust", 0.0) + (
        time.perf_counter() - started)

    candidate_blocks = {}
    rgb_squared_error = 0
    rgb_absolute_error = 0
    rgb_max_error = 0
    for atlas_index, source_index in enumerate(safe_indices):
        source_start = (source_mip.offset
                        + source_index * source_mip.bytes_per_unit)
        source_block = original[
            source_start:source_start + source_mip.bytes_per_unit]
        source_x, source_y, unit_width, unit_height = _unit_bounds(
            source_mip, source_index)
        target_pixels = _atlas_block_pixels(
            target_rgba, atlas_width, atlas_index)
        mode = _bc7_block_mode(source_block)
        if mode == 6:
            candidate_block, candidate_pixels = _recolor_mode6_block(
                source_block, target_pixels, unit_width, unit_height)
        elif mode in {4, 5}:
            candidate_block, candidate_pixels = _recolor_bc7_separate_block(
                source_block, target_pixels, unit_width, unit_height)
        elif mode == 7:
            candidate_block, candidate_pixels = _recolor_bc7_mode7_block(
                source_block, target_pixels, unit_width, unit_height)
        else:
            raise TextureBakeAnalysisError(
                "texture_validation_failed",
                "The BC7 fallback received an unsupported source mode.")
        alpha_exact = all(
            candidate_pixels[row * 4 + column][3]
            == source_rgba[((source_y + row) * source_mip.width
                            + source_x + column) * 4 + 3]
            for row in range(unit_height)
            for column in range(unit_width))
        if not alpha_exact:
            raise TextureBakeAnalysisError(
                "texture_validation_failed",
                "The BC7 fallback changed source alpha.")
        source_pixels = tuple(
            tuple(source_image.getpixel((source_x + column, source_y + row)))
            for row in range(unit_height)
            for column in range(unit_width))
        source_quality = _bc7_candidate_quality(
            target_pixels, source_pixels, unit_width, unit_height, True)
        quality = _bc7_candidate_quality(
            target_pixels, candidate_pixels, unit_width, unit_height, True)
        if _candidate_quality_key(quality) > _candidate_quality_key(
                source_quality):
            raise TextureBakeAnalysisError(
                "texture_validation_failed",
                "The BC7 fallback produced a worse RGB result than the "
                "source block.")
        candidate_blocks[source_index] = (candidate_block, quality)
        rgb_squared_error += quality.rgb_squared_error
        rgb_absolute_error += quality.rgb_absolute_error
        rgb_max_error = max(rgb_max_error, quality.rgb_max_error)
    timings[timing_key] = timings.get(timing_key, 0.0) + (
        time.perf_counter() - started)
    return candidate_blocks, AlphaCompatibilityStats(
        tested_units=len(candidate_blocks),
        compatible_units=len(candidate_blocks),
        protected_units=0,
        changed_pixels=0,
        max_delta=0,
        rgb_squared_error=rgb_squared_error,
        rgb_absolute_error=rgb_absolute_error,
        rgb_max_error=rgb_max_error)


def _encode_mode6_fallback(
        original, prepared, source_mip, source_image, pixel_mask,
        pending_mask, adjustment, timings):
    """Compatibility wrapper for the source-preserving mode-6 fallback."""
    return _encode_bc7_source_fallback(
        original, prepared, source_mip, source_image, pixel_mask,
        pending_mask, adjustment, timings, (6,), "mode6_fallback")


def _validate_patched_units(original, final, source_layout, writable_masks,
                            candidate, candidate_layout):
    """Assert that every final unit came from its allowed source or candidate."""
    if (len(original) < source_layout.payload_end
            or len(final) < source_layout.payload_end
            or len(candidate) < candidate_layout.payload_end
            or len(writable_masks) != len(source_layout.mips)
            or len(candidate_layout.mips) != len(source_layout.mips)):
        raise TextureBakeAnalysisError(
            "texture_validation_failed", "DDS payload layout is invalid.")
    for source_mip, candidate_mip, mask in zip(
            source_layout.mips, candidate_layout.mips, writable_masks):
        if (source_mip.width != candidate_mip.width
                or source_mip.height != candidate_mip.height
                or source_mip.units_x != candidate_mip.units_x
                or source_mip.units_y != candidate_mip.units_y
                or source_mip.bytes_per_unit != candidate_mip.bytes_per_unit
                or len(mask) != source_mip.units_x * source_mip.units_y):
            raise TextureBakeAnalysisError(
                "texture_validation_failed", "DDS payload layout is invalid.")
        for index, selected in enumerate(mask):
            source_start = source_mip.offset + index * source_mip.bytes_per_unit
            candidate_start = (candidate_mip.offset
                               + index * candidate_mip.bytes_per_unit)
            if not selected:
                expected = original[
                    source_start:source_start + source_mip.bytes_per_unit]
            else:
                candidate_unit = candidate[
                    candidate_start:
                    candidate_start + candidate_mip.bytes_per_unit]
                if source_layout.info.format in {"rgba8", "bgra8"}:
                    expected = candidate_unit[:3] + original[
                        source_start + 3:source_start + 4]
                elif source_layout.info.format.startswith(("bc2", "bc3")):
                    expected = original[source_start:source_start + 8] + (
                        candidate_unit[8:])
                else:
                    expected = candidate_unit
            actual = final[source_start:source_start + source_mip.bytes_per_unit]
            if actual != expected:
                raise TextureBakeAnalysisError(
                    "texture_validation_failed",
                    "The patched DDS changed an unauthorized unit.")


def _encode_alpha_candidate(
        original, prepared, source_mip, source_image, pixel_mask, pending_mask,
        adjustment, workdir, level, strategy, timings, target_image=None):
    """Encode one compact atlas and return only its alpha-exact blocks."""
    from PIL import Image

    if len(pending_mask) != source_mip.units_x * source_mip.units_y:
        raise TextureBakeAnalysisError(
            "texture_validation_failed", "DDS payload layout is invalid.")
    started = time.perf_counter()
    atlas_rgba, safe_indices, atlas_width, atlas_height = (
        _build_safe_block_atlas(
            source_image, source_mip, pixel_mask, pending_mask, adjustment,
            target_image=target_image))
    target_image = Image.frombytes(
        "RGBA", (atlas_width, atlas_height), atlas_rgba)
    timings["color_adjust"] = timings.get("color_adjust", 0.0) + (
        time.perf_counter() - started)

    png_path = os.path.join(
        workdir, f"bake-mip-{level}-{strategy.name}.png")
    target_image.save(png_path, format="PNG")
    started = time.perf_counter()
    try:
        candidate_path = encode_png_to_dds(
            png_path, workdir, prepared.info.format, 1, srgb=True,
            compression_backend=strategy.compression_backend,
            bc_flags=strategy.bc_flags,
            alpha_weight=strategy.alpha_weight)
    except TexconvUnavailableError as error:
        raise TextureBakeAnalysisError(
            "texconv_unavailable", str(error)) from error
    except TexconvError as error:
        raise TextureBakeAnalysisError(
            "texconv_failed", str(error)) from error
    timings["encode"] = timings.get("encode", 0.0) + (
        time.perf_counter() - started)

    started = time.perf_counter()
    try:
        candidate = _read_source(candidate_path)
    except TextureBakeAnalysisError as error:
        raise TextureBakeAnalysisError(
            "texconv_output_invalid",
            "The DDS encoder produced an invalid mip candidate.") from error
    candidate_layout = _validate_atlas_candidate(
        prepared.layout, atlas_width, atlas_height, candidate_path, candidate)
    candidate_mip = candidate_layout.mips[0]
    candidate_image = _decode_alpha_coupled_mip_rgba(
        candidate, candidate_layout, candidate_mip)
    candidate_indices = [0] * len(pending_mask)
    for candidate_index, source_index in enumerate(safe_indices):
        candidate_indices[source_index] = candidate_index
    compatible, comparison = _alpha_compatibility_for_mapping(
        original, prepared.layout, source_mip, candidate, candidate_layout,
        candidate_mip, pending_mask, candidate_indices,
        source_image=source_image, candidate_image=candidate_image)

    target_rgba = target_image.tobytes()
    candidate_rgba = candidate_image.tobytes()
    candidate_blocks = {}
    rgb_squared_error = 0
    rgb_absolute_error = 0
    rgb_max_error = 0
    for source_index in safe_indices:
        quality = _block_candidate_quality(
            target_rgba, candidate_rgba, source_mip, candidate_mip,
            source_index, candidate_indices[source_index],
            compatible[source_index])
        rgb_squared_error += quality.rgb_squared_error
        rgb_absolute_error += quality.rgb_absolute_error
        rgb_max_error = max(rgb_max_error, quality.rgb_max_error)
        if quality.alpha_exact:
            start = (candidate_mip.offset
                     + candidate_indices[source_index]
                     * candidate_mip.bytes_per_unit)
            candidate_blocks[source_index] = (
                candidate[start:start + candidate_mip.bytes_per_unit], quality)
    timings["candidate_validation"] = (
        timings.get("candidate_validation", 0.0)
        + time.perf_counter() - started)
    comparison = AlphaCompatibilityStats(
        tested_units=comparison.tested_units,
        compatible_units=comparison.compatible_units,
        protected_units=comparison.protected_units,
        changed_pixels=comparison.changed_pixels,
        max_delta=comparison.max_delta,
        rgb_squared_error=rgb_squared_error,
        rgb_absolute_error=rgb_absolute_error,
        rgb_max_error=rgb_max_error)
    return candidate_blocks, comparison


def _encode_alpha_coupled_mips(original, prepared, adjustment, workdir,
                               timings=None, compression_backend="auto",
                               target_images=None, source_images=None,
                               allow_quality_retries=True):
    """Encode selected BC1/BC7 RGB in compact, independent block atlases."""
    timings = {} if timings is None else timings
    pixel_coverages = getattr(prepared, "target_pixel_masks", None)
    if pixel_coverages is None:
        pixel_coverages = getattr(
            prepared, "selected_pixel_coverages", (prepared.selected_pixels,))
    if len(pixel_coverages) != len(prepared.layout.mips):
        raise TextureBakeAnalysisError(
            "texture_validation_failed",
            "Selected pixel coverage does not match the DDS mip chain.")
    if target_images is not None and len(target_images) != len(prepared.layout.mips):
        raise TextureBakeAnalysisError(
            "texture_validation_failed",
            "Composited color targets do not match the DDS mip chain.")
    if source_images is not None and len(source_images) != len(prepared.layout.mips):
        raise TextureBakeAnalysisError(
            "texture_validation_failed",
            "Decoded DDS mips do not match the DDS mip chain.")

    candidate_payloads = []
    writable_masks = []
    alpha_protected_masks = []
    stats = []
    strategy_groups = (_bc7_candidate_strategy_groups(
        prepared.info.format, compression_backend)
        if allow_quality_retries else ((
            _BC7CandidateStrategy(
                "default", compression_backend=compression_backend),),))
    for level, source_mip in enumerate(prepared.layout.mips):
        safe_mask = prepared.safe_masks[level]
        if len(safe_mask) != source_mip.units_x * source_mip.units_y:
            raise TextureBakeAnalysisError(
                "texture_validation_failed", "DDS payload layout is invalid.")
        source_start = source_mip.offset
        source_payload = original[source_start:source_start + source_mip.length]
        if not any(safe_mask):
            candidate_payloads.append(source_payload)
            writable_masks.append(bytearray(len(safe_mask)))
            alpha_protected_masks.append(bytearray(len(safe_mask)))
            stats.append(AlphaCompatibilityStats(0, 0, 0, 0, 0))
            continue

        started = time.perf_counter()
        if source_images is None:
            source_image = _decode_alpha_coupled_mip_rgba(
                original, prepared.layout, source_mip)
            timings["decode"] = timings.get("decode", 0.0) + (
                time.perf_counter() - started)
        else:
            source_image = source_images[level]
        pixel_mask = (pixel_coverages[level].mask
                      if hasattr(pixel_coverages[level], "mask")
                      else pixel_coverages[level])
        pending = bytearray(safe_mask)
        winners = {}
        tested_units = 0
        changed_pixels = 0
        max_delta = 0
        rgb_squared_error = 0
        rgb_absolute_error = 0
        rgb_max_error = 0

        for group in strategy_groups:
            retry_mask = bytearray(pending)
            if group[0].bc_flags == "q":
                retry_mask = _bc7_mode_mask(
                    original, source_mip, retry_mask, 6)
            if not any(retry_mask):
                continue
            group_winners = {}
            for strategy in group:
                candidate_kwargs = {}
                if target_images is not None:
                    candidate_kwargs["target_image"] = target_images[level]
                candidate_blocks, comparison = _encode_alpha_candidate(
                    original, prepared, source_mip, source_image, pixel_mask,
                    retry_mask, adjustment, workdir, level, strategy, timings,
                    **candidate_kwargs)
                tested_units += comparison.tested_units
                changed_pixels += comparison.changed_pixels
                max_delta = max(max_delta, comparison.max_delta)
                rgb_squared_error += comparison.rgb_squared_error
                rgb_absolute_error += comparison.rgb_absolute_error
                rgb_max_error = max(rgb_max_error, comparison.rgb_max_error)
                for source_index, candidate in candidate_blocks.items():
                    previous = group_winners.get(source_index)
                    if (previous is None
                            or _candidate_quality_key(candidate[1])
                            < _candidate_quality_key(previous[1])):
                        group_winners[source_index] = candidate
            for source_index, candidate in group_winners.items():
                previous = winners.get(source_index)
                if (previous is None
                        or _candidate_quality_key(candidate[1])
                        < _candidate_quality_key(previous[1])):
                    winners[source_index] = candidate
                pending[source_index] = 0

        fallback_stats = AlphaCompatibilityStats(0, 0, 0, 0, 0)
        if prepared.info.format.startswith("bc7") and any(pending):
            fallback_kwargs = {}
            if target_images is not None:
                fallback_kwargs["target_image"] = target_images[level]
            fallback_blocks, fallback_stats = _encode_bc7_source_fallback(
                original, prepared, source_mip, source_image, pixel_mask,
                pending, adjustment, timings, (4, 5, 6, 7),
                "bc7_source_fallback", **fallback_kwargs)
            tested_units += fallback_stats.tested_units
            rgb_squared_error += fallback_stats.rgb_squared_error
            rgb_absolute_error += fallback_stats.rgb_absolute_error
            rgb_max_error = max(rgb_max_error, fallback_stats.rgb_max_error)
            for source_index, candidate in fallback_blocks.items():
                previous = winners.get(source_index)
                if (previous is None
                        or _candidate_quality_key(candidate[1])
                        < _candidate_quality_key(previous[1])):
                    winners[source_index] = candidate
                pending[source_index] = 0

        writable = bytearray(
            index in winners for index in range(len(safe_mask)))
        protected = bytearray(
            selected and not writable[index]
            for index, selected in enumerate(safe_mask))
        mapped_payload = bytearray(source_payload)
        for source_index, (candidate_block, _quality) in winners.items():
            target_start = source_index * source_mip.bytes_per_unit
            mapped_payload[target_start:target_start + source_mip.bytes_per_unit] = (
                candidate_block)
        candidate_payloads.append(bytes(mapped_payload))
        writable_masks.append(writable)
        alpha_protected_masks.append(protected)
        mode6_safe = 0
        mode6_writable = 0
        if prepared.info.format.startswith("bc7"):
            mode6_safe = sum(_bc7_mode_mask(
                original, source_mip, safe_mask, 6))
            mode6_writable = sum(
                writable[index] for index in range(len(writable))
                if _bc7_block_mode(
                    original[source_mip.offset
                            + index * source_mip.bytes_per_unit:
                            source_mip.offset
                            + (index + 1) * source_mip.bytes_per_unit]) == 6)
        protected_mode_counts = {}
        if prepared.info.format.startswith("bc7"):
            for index, selected in enumerate(protected):
                if not selected:
                    continue
                start = source_mip.offset + index * source_mip.bytes_per_unit
                mode = _bc7_block_mode(
                    original[start:start + source_mip.bytes_per_unit])
                protected_mode_counts[mode] = (
                    protected_mode_counts.get(mode, 0) + 1)
        stats.append(AlphaCompatibilityStats(
            tested_units=tested_units,
            compatible_units=sum(writable),
            protected_units=sum(protected),
            changed_pixels=changed_pixels,
            max_delta=max_delta,
            rgb_squared_error=rgb_squared_error,
            rgb_absolute_error=rgb_absolute_error,
            rgb_max_error=rgb_max_error,
            source_mode6_tested=mode6_safe,
            source_mode6_compatible=mode6_writable,
            protected_mode_counts=tuple(sorted(protected_mode_counts.items()))))

    candidate = (
        original[:prepared.layout.data_offset] + b"".join(candidate_payloads)
        + original[prepared.layout.payload_end:])
    if len(candidate) < prepared.layout.payload_end:
        raise TextureBakeAnalysisError(
            "texconv_output_invalid",
            "The DDS encoder produced an incomplete mip chain.")
    return (candidate, tuple(writable_masks), tuple(alpha_protected_masks),
            tuple(stats))


def bake_texture_color(
        context, overrides, active_mesh_keys, selected_semantic_key,
        selected_texture_key, texture_usage, adjustment, metadata_key=None):
    """Safely bake a non-neutral adjustment into unique DDS units only."""
    committed = False
    success_result = None
    timings = {}
    try:
        # Preparation includes the independent mod-root/Asset check and must be
        # repeated for the destructive request, even after a prior analysis.
        started = time.perf_counter()
        prepared = _prepare_texture_bake(
            context, overrides, active_mesh_keys, selected_semantic_key,
            selected_texture_key, texture_usage, require_file_layout=True,
            require_complete_roles=True, metadata_key=metadata_key)
        timings["prepare"] = time.perf_counter() - started
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
        # Resolve all post-commit metadata before the destructive boundary.
        affected = _affected_texture_keys(context, prepared)
        alpha_stats = ()

        with tempfile.TemporaryDirectory(prefix="modviewer-bake-") as workdir:
            if prepared.info.format in _ALPHA_COUPLED_FORMATS:
                (candidate, writable_masks, alpha_protected_masks,
                 alpha_stats) = _encode_alpha_coupled_mips(
                    original, prepared, normalized, workdir, timings)
                _log_alpha_quality(alpha_stats)
                if not any(writable_masks[0]):
                    raise _alpha_preservation_error(stats=alpha_stats[0])
                if any(alpha_protected_masks[0]):
                    raise _alpha_preservation_error(
                        mip0_protected=True, stats=alpha_stats[0])
                patch_candidate_layout = prepared.layout
            else:
                started = time.perf_counter()
                image = load_texture_image_full(
                    prepared.selected_path, preserve_alpha=True)
                if image is None or image.size != (
                        prepared.info.width, prepared.info.height):
                    raise TextureBakeAnalysisError(
                        "texture_decode_failed",
                        "The source DDS could not be decoded at full resolution.")
                rgba = image.convert("RGBA")
                timings["decode"] = timings.get("decode", 0.0) + (
                    time.perf_counter() - started)
                started = time.perf_counter()
                masked_rgba = adjust_rgba_bytes(
                    rgba.tobytes(), rgba.width, rgba.height, normalized,
                    prepared.selected_pixels.mask)
                from PIL import Image
                png_path = os.path.join(workdir, "bake.png")
                Image.frombytes(
                    "RGBA", rgba.size, masked_rgba).save(png_path, format="PNG")
                timings["color_adjust"] = timings.get("color_adjust", 0.0) + (
                    time.perf_counter() - started)
                started = time.perf_counter()
                try:
                    candidate_path = encode_png_to_dds(
                        png_path, workdir, prepared.info.format,
                        prepared.info.mip_count, srgb=True,
                        compression_backend="cpu")
                except TexconvUnavailableError as error:
                    raise TextureBakeAnalysisError(
                        "texconv_unavailable", str(error)) from error
                except TexconvError as error:
                    raise TextureBakeAnalysisError(
                        "texconv_failed", str(error)) from error
                timings["encode"] = timings.get("encode", 0.0) + (
                    time.perf_counter() - started)
                started = time.perf_counter()
                try:
                    candidate = _read_source(candidate_path)
                except TextureBakeAnalysisError as error:
                    raise TextureBakeAnalysisError(
                        "texconv_output_invalid",
                        "The DDS encoder produced an invalid candidate.") from error
                candidate_layout = _validate_candidate(
                    prepared.layout, candidate_path, candidate)
                writable_masks = prepared.safe_masks
                alpha_protected_masks = tuple(
                    bytearray(len(mask)) for mask in prepared.safe_masks)
                patch_candidate_layout = candidate_layout
                timings["candidate_validation"] = (
                    timings.get("candidate_validation", 0.0)
                    + time.perf_counter() - started)
            started = time.perf_counter()
            final = _patch_dds_units(
                original, candidate, prepared.layout,
                writable_masks, patch_candidate_layout)
            timings["patch"] = time.perf_counter() - started
            _validate_patched_units(
                original, final, prepared.layout, writable_masks, candidate,
                patch_candidate_layout)

            started = time.perf_counter()
            temporary = _write_temp(prepared.selected_path, final)
            try:
                final_layout = inspect_dds_layout(temporary)
                if final_layout is None:
                    raise TextureBakeAnalysisError(
                        "texture_validation_failed",
                        "The patched DDS failed validation.")
                _validate_candidate(prepared.layout, temporary, final)
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
                        "mip0_units": sum(writable_masks[0]),
                        "total_units": sum(
                            sum(mask) for mask in prepared.safe_masks),
                        "shared_units_preserved": sum(
                            sum(mask) for mask in prepared.shared_masks),
                        "alpha_protected_units": sum(
                            sum(mask) for mask in alpha_protected_masks),
                        "alpha_protected_mip0_units": sum(
                            alpha_protected_masks[0]),
                        "alpha_protected_levels": [
                            level for level, mask in enumerate(
                                alpha_protected_masks) if any(mask)],
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
            timings["write"] = time.perf_counter() - started
        _LOGGER.debug(
            "texture bake timing_ms=%s",
            {key: round(value * 1000.0, 2)
             for key, value in timings.items()})
        return success_result
    except TextureBakeAnalysisError as error:
        if committed and success_result is not None:
            success_result["warning"] = "post_bake_cleanup_failed"
            return success_result
        return _error(error.code, error.message, error.status,
                      **({"details": error.details} if error.details else {}))
    except Exception:
        if committed and success_result is not None:
            success_result["warning"] = "post_bake_cleanup_failed"
            return success_result
        return _error(
            "texture_write_failed", "The DDS could not be baked safely.")


def _compose_texture_save_image(source_image, prepared, level):
    """Compose all target adjustments over one decoded DDS mip."""
    source_image = source_image.convert("RGBA")
    width, height = source_image.size
    source_rgba = source_image.tobytes()
    composed = bytearray(source_rgba)
    pixel_mask = prepared.target_pixel_masks[level]
    for target in prepared.targets:
        mask = target.pixel_coverages[level].mask
        adjusted = adjust_rgba_bytes(
            source_rgba, width, height, target.adjustment, mask)
        for index, selected in enumerate(mask):
            if not selected:
                continue
            start = index * 4
            composed[start:start + 4] = adjusted[start:start + 4]
    if composed[3::4] != source_rgba[3::4]:
        raise TextureBakeAnalysisError(
            "texture_validation_failed",
            "Color adjustment changed the source alpha channel.")
    if len(pixel_mask) != width * height:
        raise TextureBakeAnalysisError(
            "texture_validation_failed",
            "Composited pixel coverage does not match the DDS mip size.")
    from PIL import Image
    return Image.frombytes("RGBA", (width, height), bytes(composed))


def save_texture_color(
        context, overrides, active_mesh_keys, selected_texture_key, targets,
        texture_usage):
    """Save all non-neutral Color changes that target one physical DDS."""
    committed = False
    success_result = None
    timings = {}
    try:
        started = time.perf_counter()
        prepared = _prepare_texture_save(
            context, overrides, active_mesh_keys, selected_texture_key, targets,
            texture_usage, require_file_layout=True)
        timings["prepare"] = time.perf_counter() - started
        original = _read_source(prepared.selected_path)
        original_hash = _sha256_bytes(original)
        affected = _affected_texture_keys(context, prepared)
        alpha_stats = ()

        with tempfile.TemporaryDirectory(prefix="modviewer-save-") as workdir:
            if prepared.info.format in _ALPHA_COUPLED_FORMATS:
                source_images = []
                target_images = []
                for level, source_mip in enumerate(prepared.layout.mips):
                    started = time.perf_counter()
                    source_image = _decode_alpha_coupled_mip_rgba(
                        original, prepared.layout, source_mip)
                    timings["decode"] = timings.get("decode", 0.0) + (
                        time.perf_counter() - started)
                    source_images.append(source_image)
                    target_images.append(
                        _compose_texture_save_image(source_image, prepared, level))
                (candidate, writable_masks, alpha_protected_masks,
                 alpha_stats) = _encode_alpha_coupled_mips(
                    original, prepared, None, workdir, timings,
                    target_images=tuple(target_images),
                    source_images=tuple(source_images),
                    allow_quality_retries=False)
                _log_alpha_quality(alpha_stats)
                if not any(writable_masks[0]):
                    raise _alpha_preservation_error(stats=alpha_stats[0])
                if any(alpha_protected_masks[0]):
                    raise _alpha_preservation_error(
                        mip0_protected=True, stats=alpha_stats[0])
                patch_candidate_layout = prepared.layout
            else:
                started = time.perf_counter()
                image = load_texture_image_full(
                    prepared.selected_path, preserve_alpha=True)
                if image is None or image.size != (
                        prepared.info.width, prepared.info.height):
                    raise TextureBakeAnalysisError(
                        "texture_decode_failed",
                        "The source DDS could not be decoded at full resolution.")
                target_image = _compose_texture_save_image(image, prepared, 0)
                timings["decode"] = timings.get("decode", 0.0) + (
                    time.perf_counter() - started)
                png_path = os.path.join(workdir, "save.png")
                target_image.save(png_path, format="PNG")
                started = time.perf_counter()
                try:
                    candidate_path = encode_png_to_dds(
                        png_path, workdir, prepared.info.format,
                        prepared.info.mip_count, srgb=True,
                        compression_backend="cpu")
                except TexconvUnavailableError as error:
                    raise TextureBakeAnalysisError(
                        "texconv_unavailable", str(error)) from error
                except TexconvError as error:
                    raise TextureBakeAnalysisError(
                        "texconv_failed", str(error)) from error
                timings["encode"] = timings.get("encode", 0.0) + (
                    time.perf_counter() - started)
                started = time.perf_counter()
                candidate = _read_source(candidate_path)
                candidate_layout = _validate_candidate(
                    prepared.layout, candidate_path, candidate)
                writable_masks = prepared.safe_masks
                alpha_protected_masks = tuple(
                    bytearray(len(mask)) for mask in prepared.safe_masks)
                patch_candidate_layout = candidate_layout
                timings["candidate_validation"] = (
                    timings.get("candidate_validation", 0.0)
                    + time.perf_counter() - started)

            final = _patch_dds_units(
                original, candidate, prepared.layout,
                writable_masks, patch_candidate_layout)
            _validate_patched_units(
                original, final, prepared.layout, writable_masks, candidate,
                patch_candidate_layout)
            temporary = _write_temp(prepared.selected_path, final)
            try:
                if inspect_dds_layout(temporary) is None:
                    raise TextureBakeAnalysisError(
                        "texture_validation_failed",
                        "The patched DDS failed validation.")
                _validate_candidate(prepared.layout, temporary, final)
                _assert_source_unchanged(prepared.selected_path, original_hash)
                backup_path = _write_backup(prepared.selected_path, original)
                _assert_source_unchanged(prepared.selected_path, original_hash)
                success_result = {
                    "status": "ok",
                    "tex_key": selected_texture_key,
                    "affected_tex_keys": affected,
                    "saved_meshes": [
                        {"semantic_key": target.semantic_key,
                         "metadata_key": target.metadata_key}
                        for target in prepared.targets],
                    "texture": _texture_details(
                        prepared.selected_path, prepared.info),
                    "patched": {
                        "mip0_units": sum(writable_masks[0]),
                        "total_units": sum(sum(mask) for mask in writable_masks),
                        "shared_units_preserved": sum(
                            sum(mask) for mask in prepared.preserved_masks),
                        "alpha_protected_units": sum(
                            sum(mask) for mask in alpha_protected_masks),
                        "alpha_protected_mip0_units": sum(
                            alpha_protected_masks[0]),
                        "alpha_protected_levels": [
                            level for level, mask in enumerate(
                                alpha_protected_masks) if any(mask)],
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
        _LOGGER.debug(
            "texture save timing_ms=%s",
            {key: round(value * 1000.0, 2)
             for key, value in timings.items()})
        return success_result
    except TextureBakeAnalysisError as error:
        if committed and success_result is not None:
            success_result["warning"] = "post_save_cleanup_failed"
            return success_result
        return _error(error.code, error.message, error.status,
                      **({"details": error.details} if error.details else {}))
    except Exception:
        if committed and success_result is not None:
            success_result["warning"] = "post_save_cleanup_failed"
            return success_result
        return _error(
            "texture_write_failed", "The DDS could not be saved safely.")


def bake_mesh_texture_color(*args, **kwargs):
    """Compatibility name for callers that use the public bake operation name."""
    return bake_texture_color(*args, **kwargs)


__all__ = [
    "TextureBakeAnalysisError", "analyze_texture_bake",
    "bake_mesh_texture_color", "bake_texture_color", "save_texture_color",
    "_patch_dds_units",
]

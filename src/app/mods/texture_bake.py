"""Read-only DDS coverage analysis for the future texture baker."""

import os
import struct

from core.geometry.buffers import BufferStore
from core.geometry.conventions import geometry_convention_for
from core.geometry.packing import pack_draw_geometry
from core.resource_paths import _canonical, safe_resource_path
from core.textures import split_texture_key
from core.textures.dds import inspect_dds
from core.textures.uv_coverage import UVCoverageError, rasterize_uv_coverage

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


class TextureBakeAnalysisError(ValueError):
    """An expected, stable failure from a coverage analysis request."""

    def __init__(self, code, message, status="error"):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


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
    return [(u, 1.0 - v) for u, v in zip(values[::2], values[1::2])]


def _coverage(draw, group, mod_dir, info, buffers, sparse_shape_cache, convention):
    try:
        packed = _draw_geometry(
            draw, group, mod_dir, buffers, sparse_shape_cache, convention)
        if packed is None:
            raise TextureBakeAnalysisError(
                "geometry_not_available",
                "The rendered draw geometry could not be prepared.")
        unit = 4 if info.compressed else 1
        return rasterize_uv_coverage(
            _unpack_indices(packed.indices),
            _unpack_source_uvs(packed.texcoords) if packed.texcoords is not None
            else None,
            info.width, info.height,
            unit_width=unit, unit_height=unit)
    except UVCoverageError as error:
        raise TextureBakeAnalysisError(error.code, error.message) from error
    except TextureBakeAnalysisError:
        raise
    except Exception as error:
        raise TextureBakeAnalysisError(
            "geometry_not_available",
            "The rendered draw geometry could not be prepared.") from error


def _validate_usage(active_mesh_keys, selected_semantic_key, selected_texture_key,
                    texture_usage):
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
        keys.append(key)
        entries.append({"semantic_key": key, "tex_key": item.get("tex_key")})
    expected = set(active_mesh_keys or ())
    if set(keys) != expected or len(keys) != len(expected):
        raise TextureBakeAnalysisError(
            "stale_mesh_state",
            "The model changed before texture coverage could be analyzed.")
    selected = next((item for item in entries
                     if item["semantic_key"] == selected_semantic_key), None)
    if selected is None or selected["tex_key"] != selected_texture_key:
        raise TextureBakeAnalysisError(
            "stale_mesh_state",
            "The model changed before texture coverage could be analyzed.")
    return entries


def analyze_texture_bake(
        context, overrides, active_mesh_keys, selected_semantic_key,
        selected_texture_key, texture_usage):
    """Analyze selected and same-source draws without opening texture data."""
    try:
        entries = _validate_usage(
            active_mesh_keys, selected_semantic_key, selected_texture_key,
            texture_usage)
        selected_path = _texture_path(
            context.mod_dir, selected_texture_key, selected=True)
        info = _inspect_color_texture(selected_path)
        parsed, draws = resolved_draws(context, overrides)
        selected_draw = draws.get(selected_semantic_key)
        if selected_draw is None:
            raise TextureBakeAnalysisError(
                "mesh_not_found", "The selected mesh is no longer available.")

        selected_identity = _physical_identity(selected_path)
        consumers = []
        for entry in entries:
            if entry["semantic_key"] == selected_semantic_key:
                continue
            path = _texture_path(context.mod_dir, entry["tex_key"])
            if path and _physical_identity(path) == selected_identity:
                consumers.append((entry["semantic_key"], draws.get(entry["semantic_key"])))

        buffers = BufferStore()
        sparse_shape_cache = {}
        convention = geometry_convention_for(parsed.game.game)
        try:
            selected_coverage = _coverage(
                selected_draw[0], selected_draw[1], context.mod_dir, info,
                buffers, sparse_shape_cache, convention)
        except TextureBakeAnalysisError as error:
            return _error(error.code, error.message, error.status)

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
            overlap = sum(
                left and right
                for left, right in zip(selected_coverage.mask, other.mask))
            if overlap:
                shared_with.append({
                    "semantic_key": semantic_key,
                    "shared_units": overlap,
                })
            for index, value in enumerate(other.mask):
                union[index] = union[index] or value

        shared_units = sum(
            left and right
            for left, right in zip(selected_coverage.mask, union))
        selected_units = selected_coverage.count
        total_units = len(selected_coverage.mask)
        unit = 4 if info.compressed else 1
        return {
            "status": "ok",
            "safety": "unknown" if unresolved else (
                "shared" if shared_units else "safe"),
            "semantic_key": selected_semantic_key,
            "tex_key": selected_texture_key,
            "texture": _texture_details(selected_path, info),
            "coverage": {
                "unit": "block" if info.compressed else "pixel",
                "unit_width": unit,
                "unit_height": unit,
                "total_units": total_units,
                "selected_units": selected_units,
                "unique_units": selected_units - shared_units,
                "shared_units": shared_units,
                "selected_percent": 100 * selected_units / total_units,
                "shared_percent_of_selected": (
                    100 * shared_units / selected_units
                    if selected_units else 0),
            },
            "shared_with": shared_with,
            "unresolved_consumers": unresolved,
            "diagnostics": {
                "triangles": selected_coverage.triangle_count,
                "degenerate_uv_triangles": (
                    selected_coverage.degenerate_triangle_count),
            },
            "unresolved_consumer_details": unresolved_details,
        }
    except TextureBakeAnalysisError as error:
        return _error(error.code, error.message, error.status)
    except Exception:
        return _error(
            "coverage_incomplete",
            "Texture coverage could not be analyzed safely.")


__all__ = ["TextureBakeAnalysisError", "analyze_texture_bake"]

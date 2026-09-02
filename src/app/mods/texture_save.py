"""Authoritative, atomic BC7 DDS color saving."""

from __future__ import annotations

from array import array
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import logging
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
    PreparedColorAdjustment,
    apply_prepared_color_adjustment, apply_prepared_color_u8,
    is_neutral_color_adjustment,
    normalize_color_adjustment, prepare_color_adjustment,
)
from core.textures.dds import (
    inspect_dds, inspect_dds_layout,
)
from core.textures.uv_coverage import UVCoverageError, rasterize_uv_coverage

from app.assets.textures import is_asset_texture_key
from app.mods.analysis import resolved_draws


_TEXTURE_USAGE_ROLES = tuple(TEXTURE_ROLES)
_TEXTURE_ROLE_LABELS = {
    "normal_map": "Normal Map",
    "normal_data": "Normal Data",
    "light_map": "Light Map",
    "material_map": "Material Map",
    "emission_map": "Emission Map",
}
_LOGGER = logging.getLogger(__name__)


class TextureSaveError(ValueError):
    """An expected, stable failure from a texture save request."""

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


@dataclass(frozen=True)
class _PreparedSaveTarget:
    semantic_key: str
    metadata_key: str
    adjustment: object
    pixel_coverage: object


@dataclass(frozen=True)
class _PreparedTextureSave:
    entries: tuple
    selected_path: str
    info: object
    layout: object
    targets: tuple
    mip0_claims: object
    intent_adjustments: tuple
    mip0_affected_blocks: tuple


@dataclass(frozen=True)
class BC7SaveStats:
    """Quality and workload measurements for a direct BC7 Save."""

    touched_blocks: int
    improved_blocks: int
    unchanged_blocks: int
    source_rgb_error: int
    final_rgb_error: int
    modes: dict


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
            raise TextureSaveError(
                "no_diffuse", "The selected mesh has no diffuse texture.")
        return None
    role, relative_path = split_texture_key(key)
    if selected and role != "diffuse":
        raise TextureSaveError(
            "not_diffuse_texture", "Save to Texture requires a diffuse texture.")
    if role != "diffuse" or not relative_path:
        return None
    if is_asset_texture_key(key):
        if selected:
            raise TextureSaveError(
                "asset_texture_read_only",
                "Asset textures cannot be modified.", "unsupported")
        return None
    if selected and not relative_path.lower().endswith(".dds"):
        raise TextureSaveError(
            "unsupported_texture_type",
            "Save to Texture currently requires a DDS source.", "unsupported")
    path = _canonical_mod_path(mod_dir, relative_path)
    if path is None:
        if selected:
            raise TextureSaveError(
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


def _inspect_save_texture(path):
    if not path.lower().endswith(".dds"):
        raise TextureSaveError(
            "unsupported_texture_type",
            "Save to Texture currently requires a DDS source.", "unsupported")
    info = inspect_dds(path)
    if info is None:
        raise TextureSaveError(
            "invalid_dds", "The selected texture is not a valid supported DDS.")
    if info.format not in {"bc7_unorm", "bc7_srgb"}:
        raise TextureSaveError(
            "unsupported_texture_format",
            f"DDS format {info.format} is not supported by Save to Texture.",
            "unsupported")
    return info


def _draw_geometry(draw, group, mod_dir, buffers, sparse_shape_cache, convention):
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


def _unpack_indices(raw):
    if raw is None or len(raw) % 4:
        raise TextureSaveError(
            "geometry_not_available", "Packed mesh indices could not be read.")
    return struct.unpack(f"<{len(raw) // 4}I", raw)


def _unpack_source_uvs(raw):
    if raw is None or len(raw) % 8:
        raise TextureSaveError(
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
            raise TextureSaveError(
                "geometry_not_available",
                "The rendered draw geometry could not be prepared.")
        indices = _unpack_indices(packed.indices)
        source_uvs = _unpack_source_uvs(packed.texcoords)
        return _PreparedGeometry(
            indices=indices, source_uvs=source_uvs)
    except TextureSaveError:
        raise
    except Exception as error:
        raise TextureSaveError(
            "geometry_not_available",
            "The rendered draw geometry could not be prepared.") from error


def _rasterize_geometry(geometry, width, height, unit_width, unit_height):
    try:
        return rasterize_uv_coverage(
            geometry.indices, geometry.source_uvs, width, height,
            unit_width=unit_width, unit_height=unit_height)
    except UVCoverageError as error:
        raise TextureSaveError(error.code, error.message) from error


def _validate_usage(active_mesh_keys, selected_semantic_key, selected_texture_key,
                    texture_usage):
    if not isinstance(texture_usage, list):
        raise TextureSaveError(
            "stale_mesh_state",
            "The model changed before the texture save started.")
    entries = []
    keys = []
    for item in texture_usage:
        if not isinstance(item, dict) or "tex_key" in item:
            raise TextureSaveError(
                "stale_mesh_state",
                "The model changed before the texture save started.")
        key = item.get("semantic_key")
        if not isinstance(key, str) or not key or key in keys:
            raise TextureSaveError(
                "stale_mesh_state",
                "The model changed before the texture save started.")
        role_keys = item.get("texture_keys")
        if (not isinstance(role_keys, dict)
                or set(role_keys) != set(_TEXTURE_USAGE_ROLES)):
            raise TextureSaveError(
                "stale_mesh_state",
                "The model changed before the texture save started.")
        for role in _TEXTURE_USAGE_ROLES:
            texture_key = role_keys[role]
            if texture_key is not None and not isinstance(texture_key, str):
                raise TextureSaveError(
                    "stale_mesh_state",
                    "The model changed before the texture save started.")
            if texture_key is not None:
                parsed_role, relative_path = split_texture_key(
                    texture_key, role)
                if parsed_role != role or not relative_path:
                    raise TextureSaveError(
                        "stale_mesh_state",
                        "The model changed before the texture save started.")
        keys.append(key)
        entries.append({
            "semantic_key": key,
            "texture_keys": dict(role_keys),
        })
    expected = set(active_mesh_keys or ())
    if set(keys) != expected or len(keys) != len(expected):
        raise TextureSaveError(
            "stale_mesh_state",
            "The model changed before the texture save started.")
    if not isinstance(selected_texture_key, str):
        raise TextureSaveError(
            "stale_mesh_state",
            "The model changed before the texture save started.")
    selected = next((item for item in entries
                     if item["semantic_key"] == selected_semantic_key), None)
    if selected is None or selected["texture_keys"]["diffuse"] != selected_texture_key:
        raise TextureSaveError(
            "stale_mesh_state",
            "The model changed before the texture save started.")
    return tuple(entries)


def _resolve_save_request(context, overrides, active_mesh_keys,
                          selected_semantic_key, selected_texture_key,
                          texture_usage):
    entries = _validate_usage(
        active_mesh_keys, selected_semantic_key, selected_texture_key,
        texture_usage)
    selected_path = _texture_path(
        context.mod_dir, selected_texture_key, selected=True)
    info = _inspect_save_texture(selected_path)
    parsed, draws = resolved_draws(context, overrides)
    if draws.get(selected_semantic_key) is None:
        raise TextureSaveError(
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

    # The live viewer snapshot only describes active bindings. Parse authored
    # defaults and conditional variants for cross-role ownership too, since an
    # inactive branch can still own the same physical DDS.
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
        raise TextureSaveError(
            "cross_role_texture_usage", message, "unsupported")
    return entries, selected_path, info, parsed, draws


def _draw_metadata_key(draw, group):
    """Resolve the durable metadata identity for one resolved draw."""
    from core.geometry.identity import mesh_identity_for_draw
    try:
        return mesh_identity_for_draw(draw, group).key
    except AttributeError:
        return None


def _adjustment_signature(adjustment):
    return tuple(sorted(adjustment.items()))


def _texture_save_conflict(target_key, other_key):
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


def _intent_region_bounds(index, source_size, target_size):
    """Return the source-pixel range represented by one lower-mip pixel."""
    start = (index * source_size) // target_size
    end = ((index + 1) * source_size) // target_size
    return start, max(start + 1, end)


def _downsample_intent_counts(claims, counts, source_width, source_height,
                              target_width, target_height, class_count,
                              source_max_count=1):
    """Aggregate mip-0 adjustment weights into one lower-mip level."""
    source_pixels = source_width * source_height
    target_pixels = target_width * target_height
    if counts is None:
        if len(claims) != source_pixels:
            raise TextureSaveError(
                "texture_validation_failed",
                "Mip-0 color intent does not match the source texture size.")
    elif (len(counts) != class_count
          or any(len(item) != source_pixels for item in counts)):
        raise TextureSaveError(
            "texture_validation_failed",
            "Lower-mip color intent does not match the source texture size.")
    if class_count <= 0 or target_width <= 0 or target_height <= 0:
        raise TextureSaveError(
            "texture_validation_failed", "DDS mip dimensions are invalid.")
    max_region_width = (source_width + target_width - 1) // target_width
    max_region_height = (source_height + target_height - 1) // target_height
    max_count = source_max_count * max_region_width * max_region_height
    typecode = "H" if max_count <= 0xFFFF else "I"
    result = tuple(
        array(typecode, [0]) * target_pixels for _ in range(class_count))
    for y in range(target_height):
        source_y0, source_y1 = _intent_region_bounds(
            y, source_height, target_height)
        for x in range(target_width):
            source_x0, source_x1 = _intent_region_bounds(
                x, source_width, target_width)
            target_index = y * target_width + x
            if counts is None:
                for source_y in range(source_y0, source_y1):
                    start = source_y * source_width + source_x0
                    end = source_y * source_width + source_x1
                    for intent_class in claims[start:end]:
                        if not isinstance(intent_class, int) or not (
                                0 <= intent_class < class_count):
                            raise TextureSaveError(
                                "texture_validation_failed",
                                "Mip-0 color intent contains an invalid class.")
                        result[intent_class][target_index] += 1
            else:
                for intent_class, source_counts in enumerate(counts):
                    total = 0
                    for source_y in range(source_y0, source_y1):
                        start = source_y * source_width + source_x0
                        end = source_y * source_width + source_x1
                        total += sum(source_counts[start:end])
                    result[intent_class][target_index] = total
    return result


def _downsample_single_intent_counts(
        changed_counts, total_counts, source_width, source_height,
        target_width, target_height):
    """Downsample changed and total mip-0 weights for one adjustment."""
    source_pixels = source_width * source_height
    if (len(changed_counts) != source_pixels
            or len(total_counts) != source_pixels):
        raise TextureSaveError(
            "texture_validation_failed",
            "Mip color intent does not match the source texture size.")
    if any(changed > total for changed, total in zip(
            changed_counts, total_counts)):
        raise TextureSaveError(
            "texture_validation_failed",
            "Changed color intent exceeds total mip weight.")
    max_region_width = (source_width + target_width - 1) // target_width
    max_region_height = (source_height + target_height - 1) // target_height
    max_count = (max(total_counts, default=0) * max_region_width
                 * max_region_height)
    typecode = "H" if max_count <= 0xFFFF else "I"
    changed_result = array(typecode, [0]) * (target_width * target_height)
    total_result = array(typecode, [0]) * (target_width * target_height)
    for y in range(target_height):
        source_y0, source_y1 = _intent_region_bounds(
            y, source_height, target_height)
        for x in range(target_width):
            source_x0, source_x1 = _intent_region_bounds(
                x, source_width, target_width)
            changed = 0
            total = 0
            for source_y in range(source_y0, source_y1):
                start = source_y * source_width + source_x0
                end = source_y * source_width + source_x1
                changed += sum(changed_counts[start:end])
                total += sum(total_counts[start:end])
            if changed > total:
                raise TextureSaveError(
                    "texture_validation_failed",
                    "Changed color intent exceeds total mip weight.")
            target_index = y * target_width + x
            changed_result[target_index] = changed
            total_result[target_index] = total
    return changed_result, total_result


def _bc7_intent_level(prepared):
    """Create the level-zero intent state used by direct BC7 Save."""
    base_mip = prepared.layout.mips[0]
    claims = getattr(prepared, "mip0_claims", None)
    adjustments = getattr(prepared, "intent_adjustments", None)
    if claims is None or not adjustments or len(claims) != (
            base_mip.width * base_mip.height):
        raise TextureSaveError(
            "texture_validation_failed",
            "Mip-0 color intent does not match the source texture size.")
    class_count = len(adjustments)
    changed_counts = bytearray(1 if value else 0 for value in claims)
    total_counts = bytearray(b"\x01") * len(claims)
    return {
        "level": 0,
        "width": base_mip.width,
        "height": base_mip.height,
        "claims": claims,
        "changed_counts": changed_counts,
        "total_counts": total_counts,
        "single": class_count == 2,
        "counts": None,
        "max_count": 1,
        "affected_blocks": getattr(prepared, "mip0_affected_blocks", None),
    }


def _bc7_next_intent_level(state, target_width, target_height, class_count):
    """Advance direct-save intent by exactly one authored mip."""
    source_width, source_height = state["width"], state["height"]
    if state["single"]:
        changed_counts, total_counts = _downsample_single_intent_counts(
            state["changed_counts"], state["total_counts"], source_width,
            source_height, target_width, target_height)
        counts = None
        max_count = None
    else:
        total_counts = None
        counts = _downsample_intent_counts(
            state["claims"] if state["counts"] is None else None,
            state["counts"], source_width, source_height,
            target_width, target_height, class_count, state["max_count"])
        changed_counts = bytearray(
            1 if any(count[index] for count in counts[1:]) else 0
            for index in range(target_width * target_height))
        max_count = state["max_count"] * (
            (source_width + target_width - 1) // target_width) * (
            (source_height + target_height - 1) // target_height)
    return {
        "level": state["level"] + 1,
        "width": target_width,
        "height": target_height,
        "claims": None,
        "changed_counts": changed_counts,
        "total_counts": total_counts if state["single"] else None,
        "single": state["single"],
        "counts": counts,
        "max_count": max_count,
        "affected_blocks": None,
    }


def _bc7_affected_blocks(changed, width, height, mip):
    """Return only BC7 blocks containing changed intent pixels."""
    if len(changed) != width * height:
        raise TextureSaveError(
            "texture_validation_failed",
            "Mip color intent does not match the source texture size.")
    affected = set()
    for pixel, selected in enumerate(changed):
        if not selected:
            continue
        x = pixel % width
        y = pixel // width
        affected.add((y // 4) * mip.units_x + x // 4)
    return sorted(affected)


def _bc7_intent_rgb(source_rgb, state, pixel, adjustments):
    """Resolve one target RGB value from the current mip's intent state."""
    base = tuple(channel / 255.0 for channel in source_rgb)
    if state["level"] == 0:
        intent_class = state["claims"][pixel]
        if not isinstance(intent_class, int) or not (
                0 <= intent_class < len(adjustments)):
            raise TextureSaveError(
                "texture_validation_failed",
                "Mip-0 color intent contains an invalid class.")
        if not intent_class:
            return source_rgb
        return apply_prepared_color_u8(
            source_rgb, adjustments[intent_class])

    if state["single"]:
        changed_count = state["changed_counts"][pixel]
        total_count = state["total_counts"][pixel]
        if changed_count > total_count:
            raise TextureSaveError(
                "texture_validation_failed",
                "Changed color intent exceeds total mip weight.")
        if not changed_count or not total_count:
            return source_rgb
        adjusted = apply_prepared_color_adjustment(base, adjustments[1])
        return tuple(min(255, max(0, round(value * 255.0)))
                      for value in (
                          (base[channel] * (total_count - changed_count)
                           + adjusted[channel] * changed_count) / total_count
                          for channel in range(3)))

    counts = [count[pixel] for count in state["counts"]]
    total_count = sum(counts)
    changed_count = sum(counts[1:])
    if not changed_count or not total_count:
        return source_rgb
    weighted = [base[channel] * counts[0] for channel in range(3)]
    for intent_class, count in enumerate(counts[1:], 1):
        if not count:
            continue
        adjusted = apply_prepared_color_adjustment(
            base, adjustments[intent_class])
        for channel in range(3):
            weighted[channel] += adjusted[channel] * count
    return tuple(min(255, max(0, round(
        value / total_count * 255.0))) for value in weighted)


def _bc7_target_block_pixels(source_block, mip, block_index, state,
                             adjustments):
    """Build one sixteen-pixel BC7 target without a reconstructed image."""
    try:
        source_pixels = _bc7_codec.decode_block(source_block)
    except _bc7_codec.BC7Error as error:
        raise TextureSaveError(
            "invalid_bc7", "The texture contains invalid BC7 data.") from error
    source_x, source_y, valid_width, valid_height = _unit_bounds(
        mip, block_index)
    target_pixels = list(source_pixels)
    for row in range(valid_height):
        for column in range(valid_width):
            local = row * 4 + column
            pixel = ((source_y + row) * mip.width + source_x + column)
            rgb = _bc7_intent_rgb(
                source_pixels[local][:3], state, pixel, adjustments)
            target_pixels[local] = rgb + (source_pixels[local][3],)
    return (source_pixels, tuple(target_pixels), valid_width, valid_height)


def _save_bc7_blocks(original, prepared, timings=None, stage_callback=None):
    """Edit authorized BC7 blocks in-place while preserving all other bytes."""
    timings = {} if timings is None else timings
    if not prepared.info.format.startswith("bc7"):
        raise TextureSaveError(
            "texture_validation_failed", "Direct BC7 Save received another format.")
    if len(original) < prepared.layout.payload_end:
        raise TextureSaveError(
            "texture_validation_failed", "DDS payload layout is invalid.")
    raw_adjustments = getattr(prepared, "intent_adjustments", None)
    if not raw_adjustments:
        raise TextureSaveError(
            "texture_validation_failed", "Color intent is missing.")
    adjustments = tuple(
        None if adjustment is None
        else adjustment if isinstance(adjustment, PreparedColorAdjustment)
        else prepare_color_adjustment(adjustment)
        for adjustment in raw_adjustments)
    final = bytearray(original)
    state = _bc7_intent_level(prepared)
    touched = improved = unchanged = 0
    source_error = final_error = 0
    modes = {}
    for level, mip in enumerate(prepared.layout.mips):
        if level:
            started = time.perf_counter()
            state = _bc7_next_intent_level(
                state, mip.width, mip.height, len(adjustments))
            timings["intent"] = timings.get("intent", 0.0) + (
                time.perf_counter() - started)
        affected = state["affected_blocks"]
        if affected is None:
            affected = _bc7_affected_blocks(
                state["changed_counts"], mip.width, mip.height, mip)
        started = time.perf_counter()
        if stage_callback is not None:
            stage_callback("bc7_decode_fit")
        for block_index in affected:
            start = mip.offset + block_index * mip.bytes_per_unit
            source_block = bytes(original[start:start + 16])
            source_pixels, target_pixels, valid_width, valid_height = (
                _bc7_target_block_pixels(
                    source_block, mip, block_index, state, adjustments))
            result = _bc7_codec_call(
                _bc7_codec.recolor_block, source_block, target_pixels,
                valid_width, valid_height, source_pixels)
            final[start:start + 16] = result.block
            touched += 1
            improved += result.candidate_error < result.source_error
            unchanged += result.candidate_error == result.source_error
            source_error += result.source_error
            final_error += result.candidate_error
            modes[result.mode] = modes.get(result.mode, 0) + 1
        timings["bc7_decode_fit"] = timings.get("bc7_decode_fit", 0.0) + (
            time.perf_counter() - started)
        _LOGGER.debug(
            "texture save bc7 mip=%s size=%sx%s affected_blocks=%s "
            "improved_blocks=%s",
            level, mip.width, mip.height, len(affected),
            len(affected))
    stats = BC7SaveStats(
        touched_blocks=touched, improved_blocks=improved,
        unchanged_blocks=unchanged, source_rgb_error=source_error,
        final_rgb_error=final_error, modes=dict(sorted(modes.items())))
    if not touched:
        raise TextureSaveError(
            "incompatible_texture_color_usage",
            "The changed meshes have no writable texture units.",
            "unsupported")
    if final_error >= source_error:
        raise TextureSaveError(
            "texture_color_not_representable",
            "The requested Color change could not be represented safely in "
            "the source BC7 blocks.", "unsupported", {
                "touched_blocks": touched,
                "improved_blocks": improved,
                "source_rgb_error": source_error,
                "final_rgb_error": final_error,
            })
    return bytes(final), stats


def _prepare_texture_save(context, overrides, active_mesh_keys,
                          selected_texture_key, targets, texture_usage):
    """Resolve changed targets and mip-0 Color intent for one DDS."""
    if not isinstance(targets, list) or not targets:
        raise TextureSaveError(
            "no_color_adjustment", "Adjust a mesh color before saving.")
    if any(not isinstance(target, dict) for target in targets):
        raise TextureSaveError(
            "stale_mesh_state", "The model changed before the texture save started.")
    first_semantic_key = targets[0].get("semantic_key")
    entries, selected_path, info, parsed, draws = \
        _resolve_save_request(
            context, overrides, active_mesh_keys, first_semantic_key,
            selected_texture_key, texture_usage)
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
        target_path = _texture_path(
            context.mod_dir,
            entry["texture_keys"]["diffuse"] if entry else None)
        if (entry is None or draw_pair is None or target_path is None
                or _physical_identity(target_path)
                != _physical_identity(selected_path)):
            raise TextureSaveError(
                "stale_mesh_state",
                "A texture save target no longer belongs to the selected DDS.")
        actual_metadata_key = _draw_metadata_key(draw_pair[0], draw_pair[1])
        if actual_metadata_key != metadata_key:
            raise TextureSaveError(
                "stale_mesh_state",
                "A texture save target identity changed before the save started.")
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
    for semantic_key, metadata_key, draw_pair, adjustment in requested_targets:
        try:
            geometry = _prepare_uv_geometry(
                draw_pair[0], draw_pair[1], context.mod_dir, buffers,
                sparse_shape_cache, convention)
            pixel_coverage = _rasterize_geometry(
                geometry, base_mip.width, base_mip.height, 1, 1)
        except TextureSaveError as error:
            unresolved.append(semantic_key)
            unresolved_details.append({
                "semantic_key": semantic_key,
                "code": error.code,
                "error": error.message,
            })
            continue
        prepared_targets.append(_PreparedSaveTarget(
            semantic_key, metadata_key, adjustment, pixel_coverage))

    # Save represents only explicit Color intentions. A neutral same-diffuse
    # consumer has no independent desired output and must not block a write.
    intent_adjustments = [None]
    prepared_intent_adjustments = [None]
    intent_classes = {}
    intent_sources = [None]
    mip0_claims = bytearray(base_mip.width * base_mip.height)
    mip0_affected_mask = bytearray(
        base_mip.units_x * base_mip.units_y)
    claims_need_wide_values = False
    for target in prepared_targets:
        signature = _adjustment_signature(target.adjustment)
        intent_class = intent_classes.get(signature)
        if intent_class is None:
            intent_class = len(intent_adjustments)
            if intent_class > 0xFFFF:
                raise TextureSaveError(
                    "texture_validation_failed",
                    "Too many distinct Color adjustments were submitted.")
            intent_classes[signature] = intent_class
            intent_adjustments.append(target.adjustment)
            prepared_intent_adjustments.append(
                prepare_color_adjustment(target.adjustment))
            intent_sources.append(target.semantic_key)
            if intent_class > 0xFF and not claims_need_wide_values:
                mip0_claims = array("H", mip0_claims)
                claims_need_wide_values = True
        mask = target.pixel_coverage.mask
        if len(mask) != len(mip0_claims):
            raise TextureSaveError(
                "texture_validation_failed",
                "Target pixel coverage does not match the source texture size.")
        for index, selected in enumerate(mask):
            if not selected:
                continue
            previous = mip0_claims[index]
            if previous and previous != intent_class:
                raise _texture_save_conflict(
                    intent_sources[previous], target.semantic_key)
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
    return _PreparedTextureSave(
        entries=entries, selected_path=selected_path, info=info, layout=layout,
        targets=tuple(prepared_targets),
        mip0_claims=mip0_claims,
        intent_adjustments=tuple(prepared_intent_adjustments),
        mip0_affected_blocks=tuple(
            index for index, selected in enumerate(mip0_affected_mask)
            if selected))


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _read_source(path):
    try:
        with open(path, "rb") as stream:
            return stream.read()
    except OSError as error:
        raise TextureSaveError(
            "texture_read_failed", "The source DDS could not be read.") from error


def _assert_source_unchanged(path, original_hash):
    try:
        current_hash = _sha256_bytes(_read_source(path))
    except TextureSaveError as error:
        raise TextureSaveError(
            "texture_changed_during_save",
            "The DDS changed while it was being saved.") from error
    if current_hash != original_hash:
        raise TextureSaveError(
            "texture_changed_during_save",
            "The DDS changed while it was being saved.")


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
            raise TextureSaveError(
                "backup_failed", "The DDS backup could not be created.") from error
    raise TextureSaveError(
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
        raise TextureSaveError(
            "texture_write_failed", "The DDS temporary file could not be written.") from error


def _bc7_codec_call(function, *args):
    """Translate codec validation failures into the Save API error type."""
    try:
        return function(*args)
    except _bc7_codec.BC7Error as error:
        raise TextureSaveError(
            "texture_validation_failed", str(error)) from error


def _unit_bounds(mip, index):
    unit_x = (index % mip.units_x) * 4
    unit_y = (index // mip.units_x) * 4
    return (unit_x, unit_y, min(4, mip.width - unit_x),
            min(4, mip.height - unit_y))


def _affected_texture_keys(context, prepared):
    """Resolve every active usage key for the physically changed source."""
    selected_identity = _physical_identity(prepared.selected_path)
    affected = []
    seen_keys = set()
    for entry in prepared.entries:
        role_keys = entry.get("texture_keys")
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


def save_texture_color(
        context, overrides, active_mesh_keys, selected_texture_key, targets,
        texture_usage):
    """Save captured Color changes by editing authorized BC7 blocks."""
    committed = False
    success_result = None
    timings = {}
    stage = "prepare"
    try:
        started = time.perf_counter()
        prepared = _prepare_texture_save(
            context, overrides, active_mesh_keys, selected_texture_key, targets,
            texture_usage)
        timings["prepare"] = time.perf_counter() - started

        stage = "read"
        original = _read_source(prepared.selected_path)
        original_hash = _sha256_bytes(original)
        affected = _affected_texture_keys(context, prepared)

        def set_stage(value):
            nonlocal stage
            stage = value

        candidate, bc7_stats = _save_bc7_blocks(
            original, prepared, timings, stage_callback=set_stage)
        stage = "write"
        temporary = _write_temp(prepared.selected_path, candidate)
        try:
            candidate_layout = inspect_dds_layout(temporary)
            if candidate_layout != prepared.layout:
                raise TextureSaveError(
                    "texture_validation_failed",
                    "The saved DDS changed the source texture layout.")
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
                "diagnostics": {
                    "bc7": {
                        "touched_blocks": bc7_stats.touched_blocks,
                        "improved_blocks": bc7_stats.improved_blocks,
                        "unchanged_blocks": bc7_stats.unchanged_blocks,
                        "source_rgb_error": bc7_stats.source_rgb_error,
                        "final_rgb_error": bc7_stats.final_rgb_error,
                        "modes": {
                            str(mode): count
                            for mode, count in bc7_stats.modes.items()
                        },
                    },
                },
                "backup": {"file": os.path.basename(backup_path)},
            }
            try:
                os.replace(temporary, prepared.selected_path)
            except OSError as error:
                if not _replacement_completed(
                        prepared.selected_path, temporary, candidate):
                    raise TextureSaveError(
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
    except TextureSaveError as error:
        if committed and success_result is not None:
            success_result["warning"] = "post_save_cleanup_failed"
            return success_result
        return _error(error.code, error.message, error.status,
                      **({"details": error.details} if error.details else {}))
    except Exception:
        if committed and success_result is not None:
            success_result["warning"] = "post_save_cleanup_failed"
            return success_result
        _LOGGER.exception("Unexpected error while saving texture (stage=%s)",
                          stage)
        return _error(
            "texture_write_failed", "The DDS could not be saved safely.",
            details={"stage": stage})


__all__ = [
    "TextureSaveError", "save_texture_color",
]

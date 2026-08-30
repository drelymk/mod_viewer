"""Small, explicit skin-weight decoding helpers for the preview experiment."""

import math
import os
import posixpath
import struct
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SkinningSource:
    """One resolved Blend stream using one of the experiment layouts."""

    file: str
    stride: int
    influence_count: int
    encoding: str
    bone_id_offset: int = 0


@dataclass(frozen=True, slots=True)
class DecodedSkinning:
    """Canonical compact skin data sent to the frontend experiment."""

    vertex_count: int
    influence_count: int
    indices: bytes
    weights: bytes
    bone_ids: tuple[int, ...]
    diagnostics: dict


class SkinningPreviewError(ValueError):
    """A known, user-actionable failure while building a preview."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def normalize_skinning_source_file(value):
    """Return a safe, normalized mod-relative Blend-buffer path."""
    if not isinstance(value, str):
        return None
    value = value.strip().replace("\\", "/")
    if not value or value.startswith("/") or ":" in value[:3]:
        return None
    normalized = posixpath.normpath(value)
    if normalized in ("", ".") or normalized == ".." \
            or normalized.startswith("../"):
        return None
    if "/" not in normalized:
        return None
    return normalized


def skinning_source_key(source_file, bone_id_offset=0):
    """Build the case-insensitive identity for one decoded skin namespace."""
    normalized = normalize_skinning_source_file(source_file)
    if normalized is None:
        return None
    if isinstance(bone_id_offset, bool):
        return None
    try:
        offset = int(bone_id_offset)
    except (TypeError, ValueError):
        return None
    if offset < 0:
        return None
    return f"{normalized.casefold()}|offset={offset}"


def skinning_source_descriptor(source):
    """Return the stable frontend descriptor for a resolved source."""
    if not isinstance(source, SkinningSource):
        return None
    file = normalize_skinning_source_file(source.file)
    if isinstance(source.bone_id_offset, bool):
        return None
    try:
        offset = int(source.bone_id_offset)
    except (TypeError, ValueError):
        return None
    key = skinning_source_key(file, offset)
    if key is None:
        return None
    return {"key": key, "file": file, "bone_id_offset": offset}


def _format_supports_packed_weights(value):
    text = str(value or "").upper()
    return (not text or "R8_UINT" in text
            or "R8G8B8A8_UINT" in text)


def resolve_skinning_source(effective_vertex_resources, resolve_vertex_info, *,
                            bone_id_offset=0):
    """Resolve one conservative Blend candidate from active ``vbN`` state.

    The caller supplies the resolver already used by draw-group assembly, so
    copy/ref resource resolution and the mod-relative filename remain shared.
    Returns ``(source, error_code)``; an error code is retained on the draw as
    non-render metadata for the explicit preview operation.
    """
    try:
        bone_id_offset = max(0, int(bone_id_offset))
    except (TypeError, ValueError):
        bone_id_offset = 0
    candidates = {}
    unsupported = []
    for _slot, resource_name in sorted(
            (effective_vertex_resources or {}).items()):
        if not resource_name:
            continue
        info = resolve_vertex_info(resource_name) or {}
        filename = info.get("filename")
        if not filename:
            continue
        evidence = f"{resource_name} {filename}".lower()
        if "blend" not in evidence:
            continue
        try:
            stride = int(info.get("stride"))
        except (TypeError, ValueError):
            unsupported.append(None)
            continue

        encoding = None
        influence_count = None
        if stride == 32:
            encoding, influence_count = "gimi_f32_u32_4", 4
        elif stride in (8, 16) and _format_supports_packed_weights(
                info.get("format")):
            influence_count = 4 if stride == 8 else 8
            encoding = "wwmi_u8_4" if stride == 8 else "wwmi_u8_8"
        elif stride == 4:
            encoding, influence_count = "rigid_u32_1", 1
        else:
            unsupported.append(stride)
        if encoding is None:
            continue
        source = SkinningSource(
            file=filename, stride=stride,
            influence_count=influence_count, encoding=encoding,
            bone_id_offset=bone_id_offset)
        candidates[(source.file, source.stride, source.encoding,
                    source.bone_id_offset)] = source

    if len(candidates) > 1:
        return None, "ambiguous_skinning_source"
    if candidates:
        return next(iter(candidates.values())), None
    if unsupported:
        return None, "unsupported_skinning_layout"
    return None, None


def _zero_record(indices, weights, offset, influence_count):
    for influence in range(influence_count):
        struct.pack_into("<I", indices, offset + influence * 4, 0)
        struct.pack_into("<f", weights, offset + influence * 4, 0.0)


def decode_skinning(source, raw_data, used_vertices):
    """Decode and compact one supported Blend stream.

    Malformed records are represented as safe zero-influence records and are
    counted in diagnostics.  No weight normalization or index guessing is
    performed here.
    """
    if not isinstance(source, SkinningSource):
        raise TypeError("source must be a SkinningSource")
    layouts = {
        "gimi_f32_u32_4": (32, 4),
        "wwmi_u8_4": (8, 4),
        "wwmi_u8_8": (16, 8),
        "rigid_u32_1": (4, 1),
    }
    layout = layouts.get(source.encoding)
    if layout is None or (source.stride, source.influence_count) != layout:
        raise ValueError(f"Unsupported skinning encoding: {source.encoding}")

    raw_data = bytes(raw_data)
    used_vertices = tuple(used_vertices)
    count = len(used_vertices)
    item_bytes = source.influence_count * 4
    index_bytes = bytearray(count * item_bytes)
    weight_bytes = bytearray(count * item_bytes)
    invalid = 0
    zero_weight = 0
    truncated = 0
    out_of_range = 0
    sums = []
    bone_ids = set()

    for compact_index, source_index in enumerate(used_vertices):
        output_offset = compact_index * item_bytes
        try:
            source_index = int(source_index)
        except (TypeError, ValueError):
            source_index = -1
        record_offset = source_index * source.stride
        if (source_index < 0 or record_offset < 0
                or record_offset + source.stride > len(raw_data)):
            truncated += 1
            out_of_range += 1
            _zero_record(index_bytes, weight_bytes, output_offset,
                         source.influence_count)
            continue

        if source.encoding == "gimi_f32_u32_4":
            values = struct.unpack_from("<4f", raw_data, record_offset)
            decoded_indices = struct.unpack_from(
                "<4I", raw_data, record_offset + 16)
        elif source.encoding == "wwmi_u8_4":
            decoded_indices = tuple(raw_data[record_offset:record_offset + 4])
            values = tuple(
                value / 255.0 for value in raw_data[record_offset + 4:
                                                      record_offset + 8])
        elif source.encoding == "wwmi_u8_8":
            decoded_indices = tuple(raw_data[record_offset:record_offset + 8])
            values = tuple(
                value / 255.0 for value in raw_data[record_offset + 8:
                                                       record_offset + 16])
        else:
            decoded_indices = (struct.unpack_from(
                "<I", raw_data, record_offset)[0],)
            values = (1.0,)

        if (not all(math.isfinite(value) and value >= 0 for value in values)
                or any(value < 0 for value in decoded_indices)):
            invalid += 1
            _zero_record(index_bytes, weight_bytes, output_offset,
                         source.influence_count)
            continue

        weight_sum = sum(values)
        sums.append(weight_sum)
        if weight_sum <= 0:
            zero_weight += 1
        for influence, (raw_bone, weight) in enumerate(
                zip(decoded_indices, values)):
            bone = int(raw_bone) + int(source.bone_id_offset)
            struct.pack_into("<I", index_bytes,
                             output_offset + influence * 4, bone)
            struct.pack_into("<f", weight_bytes,
                             output_offset + influence * 4, weight)
            if weight > 0:
                bone_ids.add(bone)

    diagnostics = {
        "vertex_count": count,
        "influence_count": source.influence_count,
        "bone_count": len(bone_ids),
        "min_weight_sum": min(sums, default=0.0),
        "max_weight_sum": max(sums, default=0.0),
        "zero_weight_vertices": zero_weight,
        "invalid_weight_vertices": invalid,
        "truncated_vertices": truncated,
        "out_of_range_vertices": out_of_range,
        "bone_id_namespace": "model",
        "bone_id_offset": int(source.bone_id_offset),
    }
    return DecodedSkinning(
        vertex_count=count, influence_count=source.influence_count,
        indices=bytes(index_bytes), weights=bytes(weight_bytes),
        bone_ids=tuple(sorted(bone_ids)), diagnostics=diagnostics)


def _error_for_draw(draw):
    if draw.skinning_error == "ambiguous_skinning_source":
        return SkinningPreviewError(
            "ambiguous_skinning_source",
            "More than one possible Blend stream is active for this draw.")
    if draw.skinning_error == "unsupported_skinning_layout":
        return SkinningPreviewError(
            "unsupported_skinning_layout",
            "This Blend format is not supported by the experiment.")
    if draw.skinning_source is None:
        return SkinningPreviewError(
            "skinning_not_available",
            "No skin-weight stream was found for this draw.")
    return None


def build_skinning_preview(draw, group, mod_dir, *, buffers,
                           default_streams, default_index_size,
                           geometry_convention):
    """Prepare the selected draw, decode its source, and return canonical data."""
    from .packing import _prepare_draw_vertices
    from ..resource_paths import safe_resource_path

    error = _error_for_draw(draw)
    if error:
        raise error
    source_path = safe_resource_path(mod_dir, draw.skinning_source.file)
    if not source_path or not os.path.exists(source_path):
        raise SkinningPreviewError(
            "skinning_not_available",
            "The skin-weight buffer could not be found.")
    prepared = _prepare_draw_vertices(
        draw, group, mod_dir=mod_dir, default_streams=default_streams,
        default_index_size=default_index_size, buffers=buffers,
        geometry_convention=geometry_convention)
    if prepared is None:
        raise SkinningPreviewError(
            "geometry_not_available",
            "The rendered draw geometry could not be prepared.")
    decoded = decode_skinning(
        draw.skinning_source, buffers.raw(source_path),
        prepared.used_vertices)
    if decoded.diagnostics["truncated_vertices"]:
        raise SkinningPreviewError(
            "skinning_buffer_truncated",
            "The skin-weight buffer is truncated.")
    return decoded


__all__ = [
    "SkinningSource", "DecodedSkinning", "SkinningPreviewError",
    "normalize_skinning_source_file", "skinning_source_key",
    "skinning_source_descriptor", "resolve_skinning_source",
    "decode_skinning", "build_skinning_preview",
]

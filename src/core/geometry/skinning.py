"""Small, explicit skin-weight decoding helpers for the preview experiment."""

import math
import ntpath
import os
import posixpath
import re
import struct
import time
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class WwmiBlendRemap:
    """Verified stock proxy maps; decoded IDs use the full shared palette."""

    reverse_file: str
    forward_file: str
    remap_id: int
    bone_count: int
    compact_count: int
    vertex_count: int
    reverse_width: int = 2
    forward_width: int = 2


@dataclass(frozen=True, slots=True)
class SkinningSource:
    """One resolved Blend stream using one of the experiment layouts."""

    file: str
    stride: int
    influence_count: int
    encoding: str
    bone_id_offset: int = 0
    # WWMI can bind a per-vertex full VertexVG table to the compute remapper.
    # When present, these IDs are authoritative and already use the model
    # namespace; the ordinary Blend byte offset must not be applied.
    vertex_vg_file: str | None = None
    vertex_vg_stride: int = 16
    bone_id_namespace: str = "model"
    bone_id_domain: str = "model"
    blend_remap: WwmiBlendRemap | None = None


@dataclass(frozen=True, slots=True)
class SkinningManifestEntry:
    """Private mapping from one rendered mesh to authored skin vertices."""

    mesh_key: str
    skinning_source: SkinningSource
    used_vertices: tuple[int, ...]
    vertex_count: int
    asset_binding: object | None = None
    merged_layout: tuple = ()

    @classmethod
    def from_vertices(cls, mesh_key, skinning_source, used_vertices, *,
                      asset_binding=None, merged_layout=()):
        compact = (used_vertices if isinstance(used_vertices, tuple)
                   else tuple(used_vertices))
        return cls(mesh_key, skinning_source, compact, len(compact), asset_binding, merged_layout)


@dataclass(frozen=True, slots=True)
class DecodedSkinning:
    """Canonical compact skin data sent to the frontend experiment."""

    vertex_count: int
    influence_count: int
    indices: bytes
    weights: bytes
    bone_ids: tuple[int, ...]
    diagnostics: dict
    bone_stats: dict[int, dict[str, float | int]] = field(default_factory=dict)


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
    if not value or value.startswith("/") or ntpath.splitdrive(value)[0]:
        return None
    normalized = posixpath.normpath(value)
    # Resource loading permits one parent level when the resolved path remains
    # within the shared sandbox ceiling.  The normalizer has no mod root to
    # resolve against, so it preserves that one-parent spelling while
    # rejecting paths that can escape more than one level.
    if (normalized in ("", ".", "..")
            or normalized.startswith("../../")
            or normalized.startswith("/")
            or ntpath.splitdrive(normalized)[0]):
        return None
    return normalized


def skinning_source_key(source_file, bone_id_offset=0, vertex_vg_file=None,
                        bone_id_namespace="model", bone_id_domain=None):
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
    key = f"{normalized.casefold()}|offset={offset}"
    remap = normalize_skinning_source_file(vertex_vg_file)
    namespace = str(bone_id_namespace or "model")
    if namespace == "wwmi_merged":
        key += "|namespace=wwmi_merged"
    elif remap and namespace != "model":
        key += f"|namespace={namespace}|vertex-vg={remap.casefold()}"
    if bone_id_domain is not None:
        if not isinstance(bone_id_domain, str) or not re.fullmatch(r"[0-9a-f]{64}", bone_id_domain):
            return None
        key += f"|domain={bone_id_domain}"
    return key


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
    key = skinning_source_key(
        file, offset, source.vertex_vg_file, source.bone_id_namespace,
        source.bone_id_domain if source.bone_id_domain != "model" else None)
    if key is None:
        return None
    descriptor = {
        "key": key,
        "file": file,
        "bone_id_offset": offset,
        "bone_ids_model_wide": skinning_bone_ids_model_wide(source),
    }
    if source.bone_id_namespace == "wwmi_merged" or source.blend_remap:
        descriptor.update(bone_id_namespace=source.bone_id_namespace,
                          bone_id_domain=source.bone_id_domain)
    if source.bone_id_namespace == "wwmi_merged":
        descriptor.update(legacy_source_key=skinning_source_key(file, offset),
                          legacy_bone_id_offset=offset)
    elif source.blend_remap:
        descriptor.update(legacy_source_key=skinning_source_key(file, offset, source.vertex_vg_file, source.bone_id_namespace),
                          legacy_bone_id_offset=0)
    if source.vertex_vg_file and source.bone_id_namespace != "model":
        descriptor.update({
            "bone_id_namespace": source.bone_id_namespace,
            "vertex_vg_source": normalize_skinning_source_file(
                source.vertex_vg_file),
        })
    return descriptor


def skinning_bone_ids_model_wide(source):
    """Return whether decoded IDs already identify model bones."""
    if not isinstance(source, SkinningSource):
        return False
    return bool(
        source.encoding == "wwmi_u16_8"
        or source.bone_id_namespace == "wwmi_merged"
        or (source.vertex_vg_file
            and source.bone_id_namespace == "wwmi_vertex_vg"))


def _format_supports_packed_weights(value):
    text = str(value or "").upper()
    return (not text or "R8_UINT" in text
            or "R8G8B8A8_UINT" in text)


def _resolve_vertex_vg_resource(resource_name, resolve_vertex_info,
                                influence_count):
    """Resolve and validate the WWMI full VertexVG resource binding."""
    if not resource_name:
        return None, None
    info = resolve_vertex_info(resource_name) or {}
    filename = normalize_skinning_source_file(info.get("filename"))
    expected_stride = influence_count * 2
    try:
        stride = int(info.get("stride", expected_stride))
    except (TypeError, ValueError):
        stride = 0
    format_name = str(info.get("format") or "").upper()
    valid_format = "R16_UINT" in format_name
    if not filename or stride != expected_stride or not valid_format:
        return None, "invalid_vertex_vg_remap"
    return {
        "filename": filename,
        "stride": stride,
        "format": info.get("format"),
    }, None


def resolve_skinning_source(effective_vertex_resources, resolve_vertex_info, *,
                            bone_id_offset=0, remap_resources=None):
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
        labeled_blend = "blend" in evidence
        if not labeled_blend:
            continue
        try:
            stride = int(info.get("stride"))
        except (TypeError, ValueError):
            unsupported.append(None)
            continue

        encoding = None
        influence_count = None
        format_name = str(info.get("format") or "").upper()
        if stride == 32 and "R16_UINT" in format_name:
            encoding, influence_count = "wwmi_u16_8", 8
        elif stride == 32:
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
        remap_info, remap_error = (None, None)
        if encoding.startswith("wwmi_"):
            remap_name = (remap_resources or {}).get(35)
            if remap_name:
                remap_info, remap_error = _resolve_vertex_vg_resource(
                    remap_name, resolve_vertex_info,
                    influence_count)
                if remap_error:
                    unsupported.append(remap_error)
                    continue
        source = SkinningSource(
            file=filename, stride=stride,
            influence_count=influence_count, encoding=encoding,
            bone_id_offset=bone_id_offset,
            vertex_vg_file=remap_info["filename"] if remap_info else None,
            vertex_vg_stride=remap_info["stride"] if remap_info else 16,
            bone_id_namespace=("wwmi_vertex_vg" if remap_info
                               else "model"))
        candidates[(source.file, source.stride, source.encoding,
                    source.bone_id_offset, source.vertex_vg_file,
                    source.vertex_vg_stride, source.bone_id_namespace)] = source

    if len(candidates) > 1:
        return None, "ambiguous_skinning_source"
    if candidates:
        return next(iter(candidates.values())), None
    if "missing_vertex_vg_remap" in unsupported:
        return None, "missing_vertex_vg_remap"
    if "invalid_vertex_vg_remap" in unsupported:
        return None, "invalid_vertex_vg_remap"
    if unsupported:
        return None, "unsupported_skinning_layout"
    return None, None


def _zero_record(indices, weights, offset, influence_count):
    for influence in range(influence_count):
        struct.pack_into("<I", indices, offset + influence * 4, 0)
        struct.pack_into("<f", weights, offset + influence * 4, 0.0)


def decode_skinning(source, raw_data, used_vertices, vertex_vg_data=None, *,
                    reverse_map_data=None, forward_map_data=None):
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
        "wwmi_u16_8": (32, 8),
        "rigid_u32_1": (4, 1),
    }
    layout = layouts.get(source.encoding)
    if layout is None or (source.stride, source.influence_count) != layout:
        raise ValueError(f"Unsupported skinning encoding: {source.encoding}")

    raw_data = bytes(raw_data)
    if source.vertex_vg_file:
        expected_stride = source.influence_count * 2
        if (source.bone_id_namespace != "wwmi_vertex_vg"
                or source.vertex_vg_stride != expected_stride):
            raise ValueError("Invalid VertexVG remap descriptor.")
        vertex_vg_data = bytes(vertex_vg_data or b"")
    remap = source.blend_remap
    checked_ids = set()
    if remap:
        # The remapper dispatches custom_vertex_count threads; reads past the
        # buffers return zero and writes are dropped, so an overstated count
        # leaves every real vertex remapped. A short count would not.
        vertices = len(raw_data) // source.stride if source.stride else 0
        if (not source.vertex_vg_file or remap.bone_count not in (512, 1024)
                or not 1 <= remap.compact_count <= 256
                or not 0 <= remap.remap_id < 1024
                or not 1 <= remap.vertex_count <= 10_000_000
                or remap.reverse_width not in (2, 4) or remap.forward_width not in (2, 4)
                or not 1 <= vertices <= remap.vertex_count
                or len(raw_data) != vertices * source.stride
                or len(vertex_vg_data) != vertices * source.vertex_vg_stride):
            raise SkinningPreviewError("invalid_blend_remap", "The WWMI blend-remap layout is invalid.")
        reverse_map_data = bytes(reverse_map_data or b"")
        forward_map_data = bytes(forward_map_data or b"")
        for data, width in ((reverse_map_data, remap.reverse_width), (forward_map_data, remap.forward_width)):
            if len(data) < (remap.remap_id + 1) * remap.bone_count * width or len(data) % width:
                raise SkinningPreviewError("invalid_blend_remap", "The WWMI blend-remap table is truncated.")
    count = len(used_vertices)
    item_bytes = source.influence_count * 4
    index_bytes = bytearray(count * item_bytes)
    weight_bytes = bytearray(count * item_bytes)
    invalid = 0
    zero_weight = 0
    truncated = 0
    out_of_range = 0
    vertex_vg_truncated = 0
    sums = []
    bone_ids = set()
    bone_stats_accum = {}

    # Consume the retained compact sequence directly.  Indexing avoids a
    # decoder-side tuple materialization for backend manifest mappings.
    for compact_index in range(count):
        source_index = used_vertices[compact_index]
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
        elif source.encoding == "wwmi_u16_8":
            decoded_indices = struct.unpack_from(
                "<8H", raw_data, record_offset)
            values = tuple(value / 65535.0 for value in struct.unpack_from(
                "<8H", raw_data, record_offset + 16))
        else:
            decoded_indices = (struct.unpack_from(
                "<I", raw_data, record_offset)[0],)
            values = (1.0,)

        remapped_indices = None
        if source.vertex_vg_file:
            remap_offset = source_index * source.vertex_vg_stride
            if (remap_offset < 0
                    or remap_offset + source.vertex_vg_stride
                    > len(vertex_vg_data)):
                vertex_vg_truncated += 1
                _zero_record(index_bytes, weight_bytes, output_offset,
                             source.influence_count)
                continue
            remapped_indices = struct.unpack_from(
                f"<{source.influence_count}H", vertex_vg_data,
                remap_offset)

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
            if remap and weight > 0:
                shared = int(remapped_indices[influence])
                if shared not in checked_ids:
                    if shared >= remap.bone_count:
                        raise SkinningPreviewError("invalid_blend_remap", "A WWMI vertex-group ID exceeds its remap table.")
                    base = remap.remap_id * remap.bone_count
                    compact = struct.unpack_from("<H" if remap.reverse_width == 2 else "<I", reverse_map_data,
                                                 (base + shared) * remap.reverse_width)[0]
                    if compact >= remap.compact_count:
                        raise SkinningPreviewError("invalid_blend_remap", "A WWMI compact ID exceeds its palette.")
                    restored = struct.unpack_from("<H" if remap.forward_width == 2 else "<I", forward_map_data,
                                                  (base + compact) * remap.forward_width)[0]
                    if restored != shared:
                        raise SkinningPreviewError("invalid_blend_remap", "The WWMI forward and reverse maps disagree.")
                    checked_ids.add(shared)
            bone = (int(remapped_indices[influence])
                    if remapped_indices is not None
                    else (int(raw_bone)
                          if source.encoding == "wwmi_u16_8" or source.bone_id_namespace == "wwmi_merged"
                          else int(raw_bone) + int(source.bone_id_offset)))
            struct.pack_into("<I", index_bytes,
                             output_offset + influence * 4, bone)
            struct.pack_into("<f", weight_bytes,
                             output_offset + influence * 4, weight)
            if weight > 0:
                bone_ids.add(bone)
                stats = bone_stats_accum.get(bone)
                if stats is None:
                    bone_stats_accum[bone] = [compact_index, 1, weight]
                elif stats[0] != compact_index:
                    stats[0] = compact_index
                    stats[1] += 1
                    stats[2] += weight
                else:
                    stats[2] += weight

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
        "vertex_vg_remap": bool(source.vertex_vg_file),
        "vertex_vg_source": (normalize_skinning_source_file(
            source.vertex_vg_file) if source.vertex_vg_file else None),
        "vertex_vg_truncated_vertices": vertex_vg_truncated,
        "bone_id_namespace": "model",
        "bone_id_offset": int(source.bone_id_offset),
    }
    if source.vertex_vg_file:
        diagnostics["bone_id_namespace"] = source.bone_id_namespace
    bone_stats = {
        bone: {
            "affected_vertex_count": stats[1],
            "total_weight": stats[2],
        }
        for bone, stats in bone_stats_accum.items()
    }
    return DecodedSkinning(
        vertex_count=count, influence_count=source.influence_count,
        indices=bytes(index_bytes), weights=bytes(weight_bytes),
        bone_ids=tuple(sorted(bone_ids)), diagnostics=diagnostics,
        bone_stats=bone_stats)


def _error_for_draw(draw):
    if draw.skinning_error == "ambiguous_skinning_source":
        return SkinningPreviewError(
            "ambiguous_skinning_source",
            "More than one possible Blend stream is active for this draw.")
    if draw.skinning_error == "unsupported_skinning_layout":
        return SkinningPreviewError(
            "unsupported_skinning_layout",
            "This Blend format is not supported by the experiment.")
    if draw.skinning_error == "invalid_vertex_vg_remap":
        return SkinningPreviewError(
            "invalid_vertex_vg_remap",
            "The WWMI VertexVG remap resource is invalid.")
    if draw.skinning_error == "missing_vertex_vg_remap":
        return SkinningPreviewError(
            "skinning_remap_unavailable",
            "The WWMI VertexVG remap binding is unavailable.")
    if draw.skinning_source is None:
        return SkinningPreviewError(
            "skinning_not_available",
            "No skin-weight stream was found for this draw.")
    return None


def read_blend_remap_tables(source, resolve, exists, buffers):
    """Read verified proxy tables only through the caller's resource sandbox."""
    if not source.blend_remap:
        return {}
    result = {}
    for key, file in (("reverse_map_data", source.blend_remap.reverse_file),
                      ("forward_map_data", source.blend_remap.forward_file)):
        path = resolve(file)
        if not path or not exists(path):
            raise SkinningPreviewError("invalid_blend_remap", "A WWMI blend-remap table is unavailable.")
        result[key] = buffers.raw(path)
        if len(result[key]) > 8 * 1024 * 1024:
            raise SkinningPreviewError("invalid_blend_remap", "A WWMI blend-remap table is too large.")
    return result


def build_skinning_preview(draw, group, mod_dir, *, buffers,
                           default_streams, default_index_size,
                           geometry_convention, timing=None, source=None):
    """Prepare the selected draw, decode its source, and return canonical data."""
    from .packing import _prepare_draw_vertices
    from ..resource_paths import safe_resource_path

    error = _error_for_draw(draw)
    if error:
        raise error
    resolve = source.resolve_resource if source is not None \
        else lambda value: safe_resource_path(mod_dir, value)
    exists = source.is_file if source is not None else os.path.exists
    source_path = resolve(draw.skinning_source.file)
    if not source_path or not exists(source_path):
        raise SkinningPreviewError(
            "skinning_not_available",
            "The skin-weight buffer could not be found.")
    remap_path = None
    if draw.skinning_source.vertex_vg_file:
        remap_path = resolve(draw.skinning_source.vertex_vg_file)
        if not remap_path or not exists(remap_path):
            raise SkinningPreviewError(
                "skinning_remap_unavailable",
                "The WWMI VertexVG remap buffer could not be found.")
    prepare_started = time.perf_counter() if timing is not None else None
    prepared = _prepare_draw_vertices(
        draw, group, mod_dir=mod_dir, default_streams=default_streams,
        default_index_size=default_index_size, buffers=buffers,
        geometry_convention=geometry_convention, source=source)
    if timing is not None:
        timing["prepare_draw_vertices_seconds"] += (
            time.perf_counter() - prepare_started)
        timing["prepare_draw_vertices_calls"] += 1
    if prepared is None:
        raise SkinningPreviewError(
            "geometry_not_available",
            "The rendered draw geometry could not be prepared.")
    decode_started = time.perf_counter() if timing is not None else None
    decoded = decode_skinning(
        draw.skinning_source, buffers.raw(source_path),
        prepared.used_vertices,
        (buffers.raw(remap_path) if draw.skinning_source.vertex_vg_file
         else None), **read_blend_remap_tables(draw.skinning_source, resolve, exists, buffers))
    if timing is not None:
        timing["decode_skinning_seconds"] += (
            time.perf_counter() - decode_started)
    if decoded.diagnostics["truncated_vertices"]:
        raise SkinningPreviewError(
            "skinning_buffer_truncated",
            "The skin-weight buffer is truncated.")
    if decoded.diagnostics["vertex_vg_truncated_vertices"]:
        raise SkinningPreviewError(
            "skinning_remap_truncated",
            "The WWMI VertexVG remap buffer is truncated.")
    return decoded


__all__ = [
    "SkinningSource", "SkinningManifestEntry", "DecodedSkinning",
    "SkinningPreviewError",
    "normalize_skinning_source_file", "skinning_source_key",
    "skinning_source_descriptor", "skinning_bone_ids_model_wide",
    "resolve_skinning_source", "WwmiBlendRemap", "read_blend_remap_tables",
    "decode_skinning", "build_skinning_preview",
]

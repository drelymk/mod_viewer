"""Typed vertex-attribute sources and conservative normal decoding."""

from dataclasses import dataclass
import math
import struct


@dataclass(frozen=True, slots=True)
class VertexAttributeSource:
    """One supported authored vertex attribute within a binary stream."""

    file: str
    stride: int
    offset: int
    encoding: str

    def __post_init__(self):
        if not isinstance(self.file, str) or not self.file:
            raise ValueError("vertex attribute source requires a file")
        if self.stride <= 0 or self.offset < 0:
            raise ValueError("vertex attribute source has invalid layout")
        if self.encoding not in {"f32x3", "snorm8x3"}:
            raise ValueError(f"unsupported vertex attribute encoding: {self.encoding}")


def decode_snorm8(value):
    """Decode one DirectX signed-normalized byte."""
    signed = value if value < 128 else value - 256
    return max(-1.0, signed / 127.0)


def _normalize(values):
    if not all(math.isfinite(value) for value in values):
        return None
    length = math.sqrt(sum(value * value for value in values))
    if not math.isfinite(length) or length <= 1e-12:
        return None
    return tuple(value / length for value in values)


def decode_normal(source, data, vertex_index):
    """Decode and normalize one authored normal, or return ``None``."""
    if vertex_index < 0:
        return None
    offset = vertex_index * source.stride + source.offset
    if source.encoding == "f32x3":
        if offset + 12 > len(data):
            return None
        values = struct.unpack_from("<fff", data, offset)
    elif source.encoding == "snorm8x3":
        if offset + 3 > len(data):
            return None
        values = tuple(decode_snorm8(value) for value in data[offset:offset + 3])
    else:
        return None
    return _normalize(values)


def decode_normals(source, data, vertex_indices):
    """Return canonical float32 XYZ normals for every requested vertex.

    The result is all-or-nothing: a truncated, non-finite, zero or implausible
    stream returns ``None`` so the caller can use geometric reconstruction.
    """
    indices = tuple(vertex_indices)
    if not indices:
        return bytearray()
    decoded = []
    raw_lengths = []
    for vertex_index in indices:
        offset = vertex_index * source.stride + source.offset
        if source.encoding == "f32x3":
            if vertex_index < 0 or offset + 12 > len(data):
                return None
            raw = struct.unpack_from("<fff", data, offset)
            raw_length = math.sqrt(sum(value * value for value in raw))
            raw_lengths.append(raw_length)
        normal = decode_normal(source, data, vertex_index)
        if normal is None:
            return None
        decoded.append(normal)

    if source.encoding == "f32x3":
        # A genuine normal stream is generally unit length. Allow modest
        # authoring/format error because values are normalized below, but
        # reject arbitrary finite data such as position or color payloads.
        if any(not math.isfinite(length) or length < 0.1 or length > 4.0
               for length in raw_lengths):
            return None
        if len(raw_lengths) >= 8:
            plausible = sum(0.5 <= length <= 1.5 for length in raw_lengths)
            if plausible / len(raw_lengths) < 0.75:
                return None

    output = bytearray(len(decoded) * 12)
    for output_index, normal in enumerate(decoded):
        struct.pack_into("<fff", output, output_index * 12, *normal)
    return output


def normal_orientation_evidence(position_data, position_stride,
                                normal_data, triangle_indices, remap,
                                max_samples=4096):
    """Compare authored normals with indexed triangle orientation.

    ``normal_data`` is the canonical float32 stream returned by
    :func:`decode_normals`, while ``remap`` maps original vertex indices to
    that compact stream. Degenerate triangles are omitted; the aggregate
    decision ignores ambiguous dot products so custom authored normals do not
    force an orientation guess. The returned ``(dot, area)`` pairs can be
    aggregated across draws that share one source descriptor.
    """
    triangle_count = len(triangle_indices) // 3
    if triangle_count <= 0 or max_samples <= 0:
        return ()
    step = max(1, (triangle_count + max_samples - 1) // max_samples)
    evidence = []
    for triangle_number in range(0, triangle_count, step):
        triangle_offset = triangle_number * 3
        i0, i1, i2 = triangle_indices[triangle_offset:triangle_offset + 3]
        try:
            n0 = struct.unpack_from("<fff", normal_data, remap[i0] * 12)
            n1 = struct.unpack_from("<fff", normal_data, remap[i1] * 12)
            n2 = struct.unpack_from("<fff", normal_data, remap[i2] * 12)
            p0 = struct.unpack_from("<fff", position_data,
                                    i0 * position_stride)
            p1 = struct.unpack_from("<fff", position_data,
                                    i1 * position_stride)
            p2 = struct.unpack_from("<fff", position_data,
                                    i2 * position_stride)
        except (KeyError, struct.error):
            continue
        edge1 = tuple(p1[index] - p0[index] for index in range(3))
        edge2 = tuple(p2[index] - p0[index] for index in range(3))
        face = (edge1[1] * edge2[2] - edge1[2] * edge2[1],
                edge1[2] * edge2[0] - edge1[0] * edge2[2],
                edge1[0] * edge2[1] - edge1[1] * edge2[0])
        area = math.sqrt(sum(value * value for value in face))
        if not math.isfinite(area) or area <= 1e-12:
            continue
        authored = _normalize(tuple(n0[index] + n1[index] + n2[index]
                                    for index in range(3)))
        if authored is None:
            continue
        dot = sum(face[index] * authored[index] for index in range(3)) / area
        if math.isfinite(dot) and abs(dot) >= 0.25:
            evidence.append((dot, area))
    return tuple(evidence)


def choose_normal_orientation(evidence):
    """Return ``1`` or ``-1`` from aggregated geometry orientation evidence."""
    usable = [(dot, area) for dot, area in evidence
              if math.isfinite(dot) and math.isfinite(area) and area > 0
              and abs(dot) >= 0.25]
    if not usable:
        return 1
    total_area = sum(area for _dot, area in usable)
    mean_dot = sum(dot * area for dot, area in usable) / total_area
    negative_area = sum(area for dot, area in usable if dot < 0)
    if mean_dot < -0.5 and negative_area / total_area > 0.8:
        return -1
    return 1


def apply_normal_orientation(normal_data, orientation):
    """Apply a validated source orientation to canonical float32 normals."""
    if orientation != -1:
        return normal_data
    output = bytearray(len(normal_data))
    for offset in range(0, len(normal_data), 12):
        x, y, z = struct.unpack_from("<fff", normal_data, offset)
        struct.pack_into("<fff", output, offset, -x, -y, -z)
    return output


__all__ = [
    "VertexAttributeSource", "decode_snorm8", "decode_normal",
    "decode_normals", "normal_orientation_evidence",
    "choose_normal_orientation", "apply_normal_orientation",
]

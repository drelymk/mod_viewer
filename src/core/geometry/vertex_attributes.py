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
    try:
        count = len(vertex_indices)
        indices = vertex_indices
    except TypeError:
        indices = tuple(vertex_indices)
        count = len(indices)
    if not count:
        return bytearray()

    if source.encoding == "f32x3":
        output = bytearray(count * 12)
        plausible = 0
        for output_index, vertex_index in enumerate(indices):
            offset = vertex_index * source.stride + source.offset
            if vertex_index < 0 or offset + 12 > len(data):
                return None
            x, y, z = struct.unpack_from("<fff", data, offset)
            if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
                return None
            length_sq = x * x + y * y + z * z
            raw_length = math.sqrt(length_sq)
            if not math.isfinite(raw_length) or raw_length <= 1e-12:
                return None
            # A genuine normal stream is generally unit length. Allow modest
            # authoring/format error because values are normalized below, but
            # reject arbitrary finite data such as position or color payloads.
            if raw_length < 0.1 or raw_length > 4.0:
                return None
            if 0.5 <= raw_length <= 1.5:
                plausible += 1
            reciprocal = 1.0 / raw_length
            struct.pack_into("<fff", output, output_index * 12,
                             x * reciprocal, y * reciprocal, z * reciprocal)

        if count >= 8 and plausible / count < 0.75:
            return None
        return output

    if source.encoding != "snorm8x3":
        return None
    output = bytearray(count * 12)
    for output_index, vertex_index in enumerate(indices):
        offset = vertex_index * source.stride + source.offset
        if vertex_index < 0 or offset + 3 > len(data):
            return None
        x, y, z = data[offset], data[offset + 1], data[offset + 2]
        x = max(-1.0, (x if x < 128 else x - 256) / 127.0)
        y = max(-1.0, (y if y < 128 else y - 256) / 127.0)
        z = max(-1.0, (z if z < 128 else z - 256) / 127.0)
        length_sq = x * x + y * y + z * z
        length = math.sqrt(length_sq)
        if not math.isfinite(length) or length <= 1e-12:
            return None
        reciprocal = 1.0 / length
        struct.pack_into("<fff", output, output_index * 12,
                         x * reciprocal, y * reciprocal, z * reciprocal)
    return output


__all__ = [
    "VertexAttributeSource", "decode_snorm8", "decode_normal",
    "decode_normals",
]

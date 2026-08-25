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


__all__ = [
    "VertexAttributeSource", "decode_snorm8", "decode_normal",
    "decode_normals",
]

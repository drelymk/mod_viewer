"""Typed vertex-attribute sources and conservative normal decoding."""

from dataclasses import dataclass
import math
import struct

from .array_views import UnsupportedLayout, strided_view


_MIN_BATCH_NORMALS = 4096
_NORMAL_BATCH_SIZE = 65536


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


def decode_normals(source, data, vertex_indices, *, zero_out_of_bounds=False):
    """Return canonical float32 XYZ normals for every requested vertex.

    The result is all-or-nothing: a truncated, non-finite, zero or implausible
    stream returns ``None`` so the caller can use geometric reconstruction.
    Baked animation can preserve Direct3D's zero reads for missing elements.
    Large supported inputs use bounded arrays with scalar-equivalent Float64
    arithmetic; small or unusual layouts retain the scalar decoder.
    """
    try:
        count = len(vertex_indices)
    except TypeError:
        vertex_indices = tuple(vertex_indices)
        count = len(vertex_indices)
    if count >= _MIN_BATCH_NORMALS:
        result = _decode_normals_batch(
            source, data, vertex_indices, zero_out_of_bounds=zero_out_of_bounds)
        if result is not NotImplemented:
            return result
    return _decode_normals_python(
        source, data, vertex_indices, zero_out_of_bounds=zero_out_of_bounds)


def _decode_normals_batch(source, data, indices, *, zero_out_of_bounds=False):
    """Return a packed buffer/None, or NotImplemented for a scalar-only input."""
    import numpy as np

    if source.encoding not in {"f32x3", "snorm8x3"}:
        return NotImplemented
    try:
        view = strided_view(
            np, data, stride=source.stride, width=3, offset=source.offset,
            dtype="<f4" if source.encoding == "f32x3" else "i1")
    except UnsupportedLayout:
        return NotImplemented
    count = len(indices)
    output = bytearray(count * 12)
    output_view = np.frombuffer(output, dtype="<f4").reshape(count, 3)
    present_count = plausible = 0
    for start in range(0, count, _NORMAL_BATCH_SIZE):
        size = min(_NORMAL_BATCH_SIZE, count - start)
        try:
            selected = np.asarray(indices[start:start + size])
        except (TypeError, ValueError, OverflowError, KeyError):
            return NotImplemented
        if (selected.ndim != 1 or selected.dtype.kind not in "iu"
                or len(selected) != size):
            return NotImplemented
        if np.any(selected < 0):
            return None
        present = selected < len(view)
        if not zero_out_of_bounds and not present.all():
            return None
        values = view[selected[present]].astype(np.float64)
        if source.encoding == "snorm8x3":
            values = np.maximum(-1.0, values / 127.0)
        elif not np.isfinite(values).all():
            return None
        # Match Python's left-to-right sum and reciprocal multiplication. A
        # Float32 reduction or direct division changes canonical packed bytes.
        lengths = np.sqrt(values[:, 0] * values[:, 0]
                          + values[:, 1] * values[:, 1]
                          + values[:, 2] * values[:, 2])
        if np.any(~np.isfinite(lengths) | (lengths <= 1e-12)):
            return None
        if source.encoding == "f32x3":
            if np.any((lengths < 0.1) | (lengths > 4.0)):
                return None
            present_count += len(lengths)
            plausible += int(np.count_nonzero((lengths >= 0.5) & (lengths <= 1.5)))
        chunk = output_view[start:start + size]
        chunk[present] = values * (1.0 / lengths)[:, None]
    if (source.encoding == "f32x3" and present_count >= 8
            and plausible / present_count < 0.75):
        return None
    return output


def _decode_normals_python(source, data, vertex_indices, *, zero_out_of_bounds=False):
    """Conservative reference decoder for small or unsupported inputs."""
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
        present = count
        for output_index, vertex_index in enumerate(indices):
            offset = vertex_index * source.stride + source.offset
            if vertex_index < 0:
                return None
            if offset + 12 > len(data):
                if not zero_out_of_bounds:
                    return None
                present -= 1
                continue
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

        if present >= 8 and plausible / present < 0.75:
            return None
        return output

    if source.encoding != "snorm8x3":
        return None
    output = bytearray(count * 12)
    for output_index, vertex_index in enumerate(indices):
        offset = vertex_index * source.stride + source.offset
        if vertex_index < 0:
            return None
        if offset + 3 > len(data):
            if not zero_out_of_bounds:
                return None
            continue
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

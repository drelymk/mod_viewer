"""Shared binary geometry transport primitives."""

import math
import struct


class GeometryBlob:
    """Binary geometry storage shared by one model load.

    Normal geometry still uses :meth:`add`, while large baked-animation
    payloads can reserve their final range and fill it frame by frame.  The
    latter avoids holding a second ``join`` copy of the animation in memory.
    """

    __slots__ = ("data",)

    def __init__(self):
        self.data = bytearray()

    def add(self, value):
        raw = bytes(value)
        offset = len(self.data)
        self.data.extend(raw)
        return {"offset": offset, "length": len(raw)}

    def reserve(self, length):
        length = int(length)
        if length < 0:
            raise ValueError("Geometry reservation length must be non-negative.")
        offset = len(self.data)
        self.data.extend(b"\0" * length)
        return {"offset": offset, "length": length}

    def write(self, offset, value):
        raw = bytes(value)
        offset = int(offset)
        end = offset + len(raw)
        if offset < 0 or end > len(self.data):
            raise ValueError("Geometry write falls outside its reservation.")
        self.data[offset:end] = raw

    def truncate(self, length):
        """Rollback data appended after a build checkpoint."""
        length = int(length)
        if length < 0 or length > len(self.data):
            raise ValueError("Geometry truncate point is outside the blob.")
        del self.data[length:]

    def __len__(self):
        return len(self.data)

    def to_bytes(self):
        return bytes(self.data)


def canonicalize_uvs(data):
    """Return packed Float32 UVs in the viewer's vertically flipped space."""
    if data is None:
        return None
    raw = bytes(data)
    if len(raw) % 8:
        raise ValueError("Packed UV data must contain complete Float32 pairs.")
    result = bytearray(len(raw))
    for offset in range(0, len(raw), 8):
        u, v = struct.unpack_from("<ff", raw, offset)
        if not math.isfinite(u) or not math.isfinite(v):
            raise ValueError("UV data contains a non-finite value.")
        struct.pack_into("<ff", result, offset, u, 1.0 - v)
    return bytes(result)


__all__ = ["GeometryBlob", "canonicalize_uvs"]

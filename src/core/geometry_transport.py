"""Shared binary geometry transport primitives."""

import math
import struct


class GeometryBlob:
    """Append-only binary geometry storage shared by one model load."""

    __slots__ = ("data",)

    def __init__(self):
        self.data = bytearray()

    def add(self, value):
        raw = bytes(value)
        offset = len(self.data)
        self.data.extend(raw)
        return {"offset": offset, "length": len(raw)}

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

"""Shared binary geometry transport primitives."""


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


__all__ = ["GeometryBlob"]

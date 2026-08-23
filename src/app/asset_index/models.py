"""Normalized records shared by the Asset Folder metadata parsers."""

from dataclasses import dataclass


@dataclass(frozen=True)
class DrawRange:
    first_index: int
    index_count: int | None

    def as_dict(self):
        return {
            "firstIndex": self.first_index,
            "indexCount": self.index_count,
        }


@dataclass(frozen=True)
class GeometryRecord:
    geometry_hash: str
    ranges: tuple[DrawRange, ...]
    metadata_path: str
    detail_metadata_path: str | None = None

    def as_dict(self):
        value = {
            "hash": self.geometry_hash,
            "ranges": [item.as_dict() for item in self.ranges],
            "metadata": self.metadata_path,
        }
        if self.detail_metadata_path is not None:
            value["detailMetadata"] = self.detail_metadata_path
        return value


@dataclass(frozen=True)
class AssetRecord:
    relative_path: str
    geometry: tuple[GeometryRecord, ...]

    def as_dict(self):
        return {
            "path": self.relative_path,
            "geometry": [item.as_dict() for item in self.geometry],
        }

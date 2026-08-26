"""Normalized records shared by the Asset Folder metadata parsers."""

from dataclasses import dataclass


@dataclass(frozen=True)
class DrawRange:
    first_index: int
    index_count: int | None
    classification: str | None = None
    component_ordinal: int | None = None

    def as_dict(self):
        value = {
            "firstIndex": self.first_index,
            "indexCount": self.index_count,
        }
        if self.classification is not None:
            value["classification"] = self.classification
        if self.component_ordinal is not None:
            value["componentOrdinal"] = self.component_ordinal
        return value


@dataclass(frozen=True)
class GeometryRecord:
    geometry_hash: str
    ranges: tuple[DrawRange, ...]
    metadata_path: str
    detail_metadata_path: str | None = None
    component_name: str | None = None
    component_fingerprint: str | None = None
    metadata_paths: tuple[str, ...] = ()

    def as_dict(self):
        value = {
            "hash": self.geometry_hash,
            "ranges": [item.as_dict() for item in self.ranges],
            "metadata": self.metadata_path,
        }
        if self.detail_metadata_path is not None:
            value["detailMetadata"] = self.detail_metadata_path
        if self.component_name is not None:
            value["componentName"] = self.component_name
        if self.component_fingerprint is not None:
            value["componentFingerprint"] = self.component_fingerprint
        metadata_paths = self.metadata_paths or (self.metadata_path,)
        if len(metadata_paths) > 1:
            value["metadataPaths"] = list(metadata_paths)
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

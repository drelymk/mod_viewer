"""Persistent, metadata-only indexes for registered Asset Folders."""

from .index import (
    AssetIndexError,
    IndividualAssetError,
    NoValidAssetsError,
    build_index,
    delete_index,
    index_path,
    index_status,
    load_index,
    lookup_geometry,
    normalize_geometry_hash,
    save_index,
    snapshot_index,
    restore_index,
)

__all__ = [
    "AssetIndexError",
    "IndividualAssetError",
    "NoValidAssetsError",
    "build_index",
    "delete_index",
    "index_path",
    "index_status",
    "load_index",
    "lookup_geometry",
    "normalize_geometry_hash",
    "save_index",
    "snapshot_index",
    "restore_index",
]

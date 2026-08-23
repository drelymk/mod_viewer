"""WWMI Metadata.json parsing."""

import json
import os

from .models import AssetRecord, DrawRange, GeometryRecord


class MetadataError(ValueError):
    """A recognized metadata file that cannot produce a usable record."""


def _integer(value, *, minimum=0):
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result >= minimum else None


def parse_metadata_file(asset_path, root, metadata_path, normalize_hash):
    try:
        with open(metadata_path, encoding="utf-8") as stream:
            raw = json.load(stream)
    except (OSError, json.JSONDecodeError) as error:
        raise MetadataError(f"Metadata.json could not be parsed: {error}") from error
    if not isinstance(raw, dict):
        raise MetadataError("Metadata.json must contain an object.")

    geometry_hash = normalize_hash(raw.get("vb0_hash"))
    if geometry_hash is None:
        raise MetadataError("Metadata.json has no valid vb0_hash.")

    raw_components = raw.get("components")
    if raw_components is None:
        raw_components = [{}]
    if not isinstance(raw_components, list):
        raise MetadataError("Metadata.json components must be a list.")

    ranges = []
    for component in raw_components:
        if not isinstance(component, dict):
            continue
        first_index = _integer(component.get("index_offset", 0))
        index_count = _integer(component.get("index_count"))
        if first_index is None:
            continue
        if component.get("index_count") is not None and index_count is None:
            continue
        ranges.append(DrawRange(first_index, index_count))
    if not ranges:
        fallback_count = _integer(raw.get("index_count"))
        ranges = [DrawRange(0, fallback_count)]

    detail_path = os.path.join(os.path.dirname(metadata_path), "TextureUsage.json")
    if not os.path.isfile(detail_path) or os.path.islink(detail_path):
        detail_path = None
    return AssetRecord(
        _relative(asset_path, root),
        (GeometryRecord(
            geometry_hash=geometry_hash,
            ranges=tuple(sorted(set(ranges), key=lambda item: (
                item.first_index, item.index_count is None,
                item.index_count or 0))),
            metadata_path=_relative(metadata_path, root),
            detail_metadata_path=(
                _relative(detail_path, root) if detail_path else None),
        ),),
    )


def parse_object_asset(asset_path, root, metadata_paths, normalize_hash):
    """Parse object-level Metadata.json files into one AssetRecord."""
    geometry = {}
    for metadata_path in metadata_paths:
        record = parse_metadata_file(
            asset_path, root, metadata_path, normalize_hash)
        item = record.geometry[0]
        existing = geometry.get(item.geometry_hash)
        if existing is None:
            geometry[item.geometry_hash] = item
        else:
            ranges = tuple(sorted(
                set(existing.ranges + item.ranges),
                key=lambda value: (value.first_index,
                                   value.index_count is None,
                                   value.index_count or 0),
            ))
            geometry[item.geometry_hash] = GeometryRecord(
                existing.geometry_hash,
                ranges,
                existing.metadata_path,
                existing.detail_metadata_path or item.detail_metadata_path,
            )
    if not geometry:
        raise MetadataError("No valid WWMI geometry metadata was found.")
    return AssetRecord(
        _relative(asset_path, root),
        tuple(geometry[key] for key in sorted(geometry)),
    )


def _relative(path, root):
    return os.path.relpath(path, root).replace(os.sep, "/")

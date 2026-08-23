"""GIMI-style hash.json metadata parsing."""

import json
import os

from .models import AssetRecord, DrawRange, GeometryRecord


class MetadataError(ValueError):
    """A recognized metadata file that cannot produce a usable record."""


def _entries(value):
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("objects", "components", "entries", "records"):
            candidate = value.get(key)
            if isinstance(candidate, list):
                return candidate
    return []


def _integer(value, *, minimum=0):
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result >= minimum else None


def _values(entry, keys):
    for key in keys:
        value = entry.get(key)
        if isinstance(value, list):
            return value
        if value is not None:
            return [value]
    return []


def _string(value):
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _merge_geometry(records, geometry_hash, ranges, metadata_path,
                    component_name=None):
    previous = records.get(geometry_hash)
    if previous is None:
        records[geometry_hash] = {
            "ranges": set(ranges),
            "metadata": metadata_path,
            "component_name": component_name,
        }
        return
    previous["ranges"].update(ranges)
    previous["component_name"] = (
        previous.get("component_name") or component_name)


def parse_hash_file(asset_path, root, metadata_path, normalize_hash):
    """Parse one hash.json without opening any asset payload files."""
    try:
        with open(metadata_path, encoding="utf-8") as stream:
            raw = json.load(stream)
    except (OSError, json.JSONDecodeError) as error:
        raise MetadataError(f"hash.json could not be parsed: {error}") from error

    records = {}
    for entry in _entries(raw):
        if not isinstance(entry, dict):
            continue
        geometry_hash = normalize_hash(
            entry.get("ib") or entry.get("ib_hash")
            or entry.get("geometry_hash") or entry.get("geometryHash"))
        if geometry_hash is None:
            continue

        first_values = _values(entry, (
            "object_indexes", "objectIndexes", "first_indices",
            "firstIndices", "first_index", "firstIndex"))
        count_values = _values(entry, (
            "object_index_counts", "objectIndexCounts", "index_counts",
            "indexCounts", "index_count", "indexCount"))
        classification_values = _values(entry, (
            "object_classifications", "objectClassifications",
            "classifications"))
        component_name = _string(
            entry.get("component_name") or entry.get("componentName"))
        if not first_values:
            first_values = [0]

        ranges = []
        for position, first_value in enumerate(first_values):
            first_index = _integer(first_value)
            if first_index is None:
                continue
            index_count = None
            if position < len(count_values):
                index_count = _integer(count_values[position])
                if index_count is None and count_values[position] is not None:
                    continue
            classification = None
            if position < len(classification_values):
                classification = _string(classification_values[position])
            ranges.append(DrawRange(first_index, index_count, classification))
        if not ranges:
            continue
        _merge_geometry(
            records, geometry_hash, ranges, metadata_path, component_name)

    if not records:
        raise MetadataError("hash.json contains no valid geometry records.")

    geometry = []
    for geometry_hash in sorted(records):
        item = records[geometry_hash]
        ranges = tuple(sorted(
            item["ranges"],
            key=lambda value: (value.first_index,
                               value.index_count is None,
                               value.index_count or 0,
                               value.classification or "",
                               value.component_ordinal
                               if value.component_ordinal is not None else -1),
        ))
        geometry.append(GeometryRecord(
            geometry_hash=geometry_hash,
            ranges=ranges,
            metadata_path=_relative(metadata_path, root),
            component_name=item.get("component_name"),
        ))
    return AssetRecord(_relative(asset_path, root), tuple(geometry))


def _relative(path, root):
    return os.path.relpath(path, root).replace(os.sep, "/")

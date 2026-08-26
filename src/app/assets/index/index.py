"""Build, persist, and query lightweight Asset Folder indexes."""

from datetime import datetime, timezone
import hashlib
import json
import os

from app.assets import folders as asset_folders
from app.settings import config, paths
from core.geometry.identity import normalize_geometry_hash
from . import gimi, wwmi, zzmi
from .models import AssetRecord


INDEX_VERSION = 4
_HASH_GROUPS = frozenset({
    "enemydata",
    "miscellaneousdata",
    "npcdata",
    "playercharacterdata",
    "skilldata",
    "weapondata",
})
_WEAPON_GROUPS = frozenset({
    "bows",
    "catalysts",
    "claymores",
    "enemies",
    "polearms",
    "swords",
})
_WWMI_GROUPS = frozenset({"playercharacterdata"})


class AssetIndexError(ValueError):
    """A validation, parsing, persistence, or index-schema failure."""


class IndividualAssetError(AssetIndexError):
    """The selected folder is an individual asset, not its collection root."""


class NoValidAssetsError(AssetIndexError):
    """The selected root contains no usable asset metadata."""


class InvalidIndexError(AssetIndexError):
    """A cache file exists but is not a supported index."""


def index_filename(asset_type, root):
    key = f"{asset_type}\0{asset_folders.normalize_path(root)}"
    return f"{hashlib.sha256(key.encode('utf-8')).hexdigest()}.json"


def index_path(asset_type, root):
    return os.path.join(paths.asset_index_dir(), index_filename(asset_type, root))


def _safe_child_dirs(path, root):
    children = []
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                    child = os.path.abspath(entry.path)
                    if (child and asset_folders.is_within(child, root)
                            and asset_folders.normalize_path(child) != root):
                        children.append(child)
                except OSError:
                    continue
    except OSError as error:
        raise AssetIndexError(f"Unable to enumerate Asset Folder: {error}") from error
    return sorted(set(children), key=lambda value: (value.casefold(), value))


def _safe_file(path, root, filename):
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                if entry.name.casefold() != filename.casefold():
                    continue
                if not entry.is_file(follow_symlinks=False):
                    return None
                candidate = os.path.abspath(entry.path)
                if candidate and asset_folders.is_within(candidate, root):
                    return candidate
    except OSError as error:
        raise AssetIndexError(f"Unable to enumerate Asset Folder: {error}") from error
    return None


def _relative(path, root):
    return os.path.relpath(path, root).replace(os.sep, "/")


def _warning(root, path, reason):
    return {"path": _relative(path, root), "reason": str(reason)}


def _name(path):
    return os.path.basename(path).casefold()


def _hash_group_candidates(group, root):
    candidates = []
    weapon_group = _name(group) == "weapondata"
    for child in _safe_child_dirs(group, root):
        marker = _safe_file(child, root, "hash.json")
        if marker and (not weapon_group or _name(child) in _WEAPON_GROUPS):
            candidates.append((child, marker))
            continue
        # GIMI's full export places weapon assets below a known weapon-type
        # directory. Do not generalize this into arbitrary nested discovery.
        if not weapon_group or _name(child) not in _WEAPON_GROUPS:
            continue
        for asset in _safe_child_dirs(child, root):
            asset_marker = _safe_file(asset, root, "hash.json")
            if asset_marker:
                candidates.append((asset, asset_marker))
    return candidates


def _hash_candidates(root):
    candidates = []
    skipped = []
    for child in _safe_child_dirs(root, root):
        marker = _safe_file(child, root, "hash.json")
        if marker:
            candidates.append((child, marker))
            continue
        nested = (_hash_group_candidates(child, root)
                  if _name(child) in _HASH_GROUPS else [])
        if nested:
            candidates.extend(nested)
        else:
            skipped.append(child)
    return candidates, skipped


def _wwmi_object_markers(path, root):
    markers = []
    for child in _safe_child_dirs(path, root):
        if normalize_geometry_hash(os.path.basename(child)) is None:
            continue
        marker = _safe_file(child, root, "Metadata.json")
        if marker:
            markers.append(marker)
    return markers


def _wwmi_candidates(root):
    candidates = []
    skipped = []
    for child in _safe_child_dirs(root, root):
        direct = _safe_file(child, root, "Metadata.json")
        objects = _wwmi_object_markers(child, root)
        if direct or objects:
            candidates.append((child, direct, objects))
            continue

        nested = []
        if _name(child) in _WWMI_GROUPS:
            for grandchild in _safe_child_dirs(child, root):
                nested_direct = _safe_file(grandchild, root, "Metadata.json")
                nested_objects = _wwmi_object_markers(grandchild, root)
                if nested_direct or nested_objects:
                    nested.append((grandchild, nested_direct, nested_objects))
        if nested:
            candidates.extend(nested)
        else:
            skipped.append(child)
    return candidates, skipped


def _parse_hash_asset(asset_path, root, metadata_path, parser):
    metadata_paths = [metadata_path]
    for child in _safe_child_dirs(asset_path, root):
        nested = _safe_file(child, root, "hash.json")
        if nested:
            metadata_paths.append(nested)

    records = [parser.parse_hash_file(
        asset_path, root, metadata_paths[0], normalize_geometry_hash)]
    for nested in metadata_paths[1:]:
        try:
            records.append(parser.parse_hash_file(
                asset_path, root, nested, normalize_geometry_hash))
        except (OSError, ValueError, UnicodeError):
            # A malformed optional component folder must not discard the
            # parent Asset's otherwise usable geometry.
            continue
    return _merge_asset_records(records)[0]


def _parse_wwmi_asset(asset_path, root, direct, objects):
    if direct:
        return wwmi.parse_metadata_file(
            asset_path, root, direct, normalize_geometry_hash)
    return wwmi.parse_object_asset(
        asset_path, root, objects, normalize_geometry_hash)


def _individual_hash_asset(root, parser):
    marker = _safe_file(root, root, "hash.json")
    if not marker:
        return False
    try:
        _parse_hash_asset(root, root, marker, parser)
    except (OSError, ValueError, UnicodeError):
        return False
    return True


def _individual_wwmi_asset(root):
    direct = _safe_file(root, root, "Metadata.json")
    if direct:
        try:
            _parse_wwmi_asset(root, root, direct, [])
        except (OSError, ValueError, UnicodeError):
            return False
        return True
    objects = _wwmi_object_markers(root, root)
    if not objects:
        return False
    try:
        _parse_wwmi_asset(root, root, None, objects)
    except (OSError, ValueError, UnicodeError):
        return False
    return True


def _merge_asset_records(records):
    """Merge duplicate geometry hashes while retaining deterministic order."""
    merged = {}
    for record in records:
        current = merged.get(record.relative_path)
        if current is None:
            merged[record.relative_path] = record
            continue
        geometry = {
            item.geometry_hash: item for item in current.geometry}
        for item in record.geometry:
            previous = geometry.get(item.geometry_hash)
            if previous is None:
                geometry[item.geometry_hash] = item
                continue
            ranges = tuple(sorted(
                set(previous.ranges + item.ranges),
                key=lambda value: (value.first_index,
                                   value.index_count is None,
                                   value.index_count or 0,
                                   value.classification or "",
                                   value.component_ordinal
                                   if value.component_ordinal is not None else -1),
            ))
            from .models import GeometryRecord
            metadata_paths = tuple(dict.fromkeys(
                (previous.metadata_paths or (previous.metadata_path,)) +
                (item.metadata_paths or (item.metadata_path,))))
            geometry[item.geometry_hash] = GeometryRecord(
                item.geometry_hash,
                ranges,
                previous.metadata_path,
                previous.detail_metadata_path or item.detail_metadata_path,
                previous.component_name or item.component_name,
                (previous.component_fingerprint
                 if previous.component_fingerprint ==
                 item.component_fingerprint else None),
                metadata_paths,
            )
        merged[record.relative_path] = AssetRecord(
            record.relative_path,
            tuple(geometry[key] for key in sorted(geometry)),
        )
    return tuple(merged[key] for key in sorted(merged, key=lambda value: (
        value.casefold(), value)))


def build_index(asset_type, root):
    """Validate a registered root and build a metadata-only index in memory."""
    if asset_type not in asset_folders.ASSET_TYPES:
        raise AssetIndexError("Asset Folder type must be GIMI, ZZMI or WWMI.")
    root = asset_folders.normalize_path(root)
    if not root or not os.path.exists(root):
        raise AssetIndexError("Asset Folder path must be an existing directory.")
    if not os.path.isdir(root):
        raise AssetIndexError("Asset Folder path must be a directory.")

    if asset_type in ("GIMI", "ZZMI"):
        parser = gimi if asset_type == "GIMI" else zzmi
        if _individual_hash_asset(root, parser):
            raise IndividualAssetError(
                "This appears to be an individual character asset. "
                "Select its parent folder containing the character asset folders.")
        children = _safe_child_dirs(root, root)
        candidates, skipped_paths = _hash_candidates(root)
    else:
        if _individual_wwmi_asset(root):
            raise IndividualAssetError(
                "This appears to be an individual character asset. "
                "Select its parent folder containing the character asset folders.")
        children = _safe_child_dirs(root, root)
        candidates, skipped_paths = _wwmi_candidates(root)
    if not children:
        raise NoValidAssetsError(
            "Asset Folder must contain immediate child directories.")

    records = []
    warnings = []
    seen = set()
    for candidate in candidates:
        asset_path = candidate[0]
        key = asset_folders.normalize_path(asset_path)
        if key in seen:
            continue
        seen.add(key)
        try:
            if asset_type in ("GIMI", "ZZMI"):
                record = _parse_hash_asset(
                    asset_path, root, candidate[1], parser)
            else:
                record = _parse_wwmi_asset(
                    asset_path, root, candidate[1], candidate[2])
            records.append(record)
        except (OSError, ValueError, UnicodeError) as error:
            warnings.append(_warning(root, asset_path, error))

    records = _merge_asset_records(records)
    if not records:
        raise NoValidAssetsError(
            "No valid assets with geometry hashes were found in this folder.")

    reverse = {}
    for asset_index, record in enumerate(records):
        for geometry_index, geometry in enumerate(record.geometry):
            reverse.setdefault(geometry.geometry_hash, []).append({
                "asset": asset_index,
                "geometry": geometry_index,
            })
    reverse = {
        key: sorted(value, key=lambda item: (item["asset"], item["geometry"]))
        for key, value in sorted(reverse.items())
    }
    skipped_count = len(skipped_paths) + len(warnings)
    index = {
        "version": INDEX_VERSION,
        "type": asset_type,
        "root": root,
        "builtAt": datetime.now(timezone.utc).replace(
            microsecond=0).isoformat().replace("+00:00", "Z"),
        "stats": {
            "assetCount": len(records),
            "geometryRecordCount": sum(len(item.geometry) for item in records),
            "geometryHashCount": len(reverse),
            "skippedCount": skipped_count,
        },
        "assets": [item.as_dict() for item in records],
        "byGeometryHash": reverse,
    }
    if warnings:
        index["warnings"] = sorted(
            warnings, key=lambda item: (item["path"].casefold(), item["path"]))
    return index


def _validate_index(value, asset_type, root):
    if not isinstance(value, dict):
        raise InvalidIndexError("Asset index must contain an object.")
    if value.get("version") != INDEX_VERSION:
        raise InvalidIndexError("Unsupported Asset index version.")
    if value.get("type") != asset_type:
        raise InvalidIndexError("Asset index type does not match the Asset Folder.")
    if asset_folders.normalize_path(value.get("root")) != asset_folders.normalize_path(root):
        raise InvalidIndexError("Asset index root does not match the Asset Folder.")
    if not isinstance(value.get("builtAt"), str):
        raise InvalidIndexError("Asset index build timestamp is missing.")
    if not isinstance(value.get("stats"), dict):
        raise InvalidIndexError("Asset index stats are missing.")
    if any(not isinstance(value["stats"].get(key), int)
           for key in ("assetCount", "geometryRecordCount",
                       "geometryHashCount", "skippedCount")):
        raise InvalidIndexError("Asset index stats are invalid.")
    if not isinstance(value.get("assets"), list):
        raise InvalidIndexError("Asset index assets are missing.")
    if not isinstance(value.get("byGeometryHash"), dict):
        raise InvalidIndexError("Asset index reverse lookup is missing.")
    return value


def load_index(asset_type, root):
    """Load a validated index, returning None when its cache file is absent."""
    filename = index_path(asset_type, root)
    if not os.path.isfile(filename):
        return None
    try:
        with open(filename, encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, json.JSONDecodeError, UnicodeError) as error:
        raise InvalidIndexError(f"Could not read Asset index: {error}") from error
    return _validate_index(value, asset_type, root)


def _atomic_bytes(filename, payload):
    directory = os.path.dirname(filename)
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as error:
        raise AssetIndexError(f"Could not write Asset index: {error}") from error
    try:
        config.write_bytes_atomic(filename, payload)
    except OSError as error:
        raise AssetIndexError(f"Could not write Asset index: {error}") from error


def save_index(value):
    """Atomically save a previously built index and return its cache path."""
    asset_type = value.get("type") if isinstance(value, dict) else None
    root = value.get("root") if isinstance(value, dict) else None
    _validate_index(value, asset_type, root)
    filename = index_path(asset_type, root)
    try:
        payload = (json.dumps(value, indent=2, ensure_ascii=False,
                              sort_keys=False) + "\n").encode("utf-8")
    except (TypeError, ValueError) as error:
        raise AssetIndexError(f"Could not serialize Asset index: {error}") from error
    _atomic_bytes(filename, payload)
    return filename


def snapshot_index(asset_type, root):
    filename = index_path(asset_type, root)
    if not os.path.isfile(filename):
        return None
    try:
        with open(filename, "rb") as stream:
            return stream.read()
    except OSError as error:
        raise AssetIndexError(f"Could not read Asset index: {error}") from error


def restore_index(asset_type, root, payload):
    filename = index_path(asset_type, root)
    if payload is None:
        try:
            os.remove(filename)
        except FileNotFoundError:
            return
        except OSError as error:
            raise AssetIndexError(f"Could not remove Asset index: {error}") from error
        return
    _atomic_bytes(filename, payload)


def delete_index(asset_type, root):
    try:
        os.remove(index_path(asset_type, root))
    except FileNotFoundError:
        return
    except OSError as error:
        raise AssetIndexError(f"Could not remove Asset index: {error}") from error


def index_status(asset_type, root):
    if not os.path.isfile(index_path(asset_type, root)):
        return {"status": "missing"}
    try:
        value = load_index(asset_type, root)
    except AssetIndexError:
        return {"status": "invalid"}
    stats = value.get("stats", {})
    return {
        "status": "ready",
        "assetCount": stats.get("assetCount", 0),
        "geometryRecordCount": stats.get("geometryRecordCount", 0),
        "geometryHashCount": stats.get("geometryHashCount", 0),
        "skippedCount": stats.get("skippedCount", 0),
        "builtAt": value.get("builtAt"),
    }


def lookup_geometry(index, geometry_hash):
    """Return all reverse-index candidates for a normalized geometry hash."""
    geometry_hash = normalize_geometry_hash(geometry_hash)
    if geometry_hash is None or not isinstance(index, dict):
        return []
    values = index.get("byGeometryHash", {}).get(geometry_hash, [])
    return [dict(value) for value in values if isinstance(value, dict)]


def find_asset_by_path(index, relative_path):
    """Return the exact indexed Asset record for a root-relative folder."""
    if not isinstance(index, dict) or not isinstance(relative_path, str):
        return None
    requested = relative_path.replace("\\", "/").strip("/")
    if not requested or requested in (".", ".."):
        return None
    requested = "/".join(part for part in requested.split("/") if part)
    for item in index.get("assets", ()):
        if not isinstance(item, dict):
            continue
        candidate = str(item.get("path", "")).replace("\\", "/").strip("/")
        if candidate.casefold() == requested.casefold():
            return dict(item)
    return None

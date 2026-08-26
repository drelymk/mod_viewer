"""Persistent registry and lazy browsing for extracted asset roots."""

import os

from app.settings import config


ASSET_TYPES = frozenset({"GIMI", "ZZMI", "WWMI"})


class AssetFolderError(ValueError):
    """A readable asset-folder validation or enumeration failure."""


def normalize_path(value):
    return config.normalize_path(value)


def is_within(path, root):
    return config.is_within(path, root)


def _read_config(config_file=None):
    try:
        return config.read_config(config_file)
    except ValueError as error:
        raise AssetFolderError(str(error)) from error


def _read_entries(config_file=None):
    raw_entries = _read_config(config_file).get("assetFolders", [])
    if not isinstance(raw_entries, list):
        raise AssetFolderError("config.json assetFolders must be a list.")
    entries = []
    seen = set()
    for raw in raw_entries:
        if not isinstance(raw, dict):
            raise AssetFolderError("Each Asset Folder entry must be an object.")
        asset_type = raw.get("type")
        folder = raw.get("path")
        if asset_type not in ASSET_TYPES:
            raise AssetFolderError("Asset Folder type must be GIMI, ZZMI or WWMI.")
        if not isinstance(folder, str) or not os.path.isabs(folder):
            raise AssetFolderError("Asset Folder path must be absolute.")
        enabled = raw.get("enabled", True)
        if not isinstance(enabled, bool):
            raise AssetFolderError("Asset Folder enabled must be a boolean.")
        normalized = normalize_path(folder)
        if normalized in seen:
            raise AssetFolderError("config.json contains duplicate Asset Folder paths.")
        seen.add(normalized)
        entries.append({"type": asset_type, "path": normalized, "enabled": enabled})
    return entries


def load_registry(config_file=None):
    return _read_entries(config_file)


def registered_paths(entries):
    return {entry["path"] for entry in entries}


def enabled_entries(entries):
    """Return roots eligible for asset matching without dropping registry state."""
    return [entry for entry in entries if entry.get("enabled", True)]


def enabled_entries_for_type(entries, asset_type):
    """Return enabled roots for one game asset type."""
    return [entry for entry in enabled_entries(entries)
            if entry["type"] == asset_type]


def _validated_entry(asset_type, folder, *, require_exists):
    if asset_type not in ASSET_TYPES:
        raise AssetFolderError("Asset Folder type must be GIMI, ZZMI or WWMI.")
    if not isinstance(folder, str) or not os.path.isabs(folder):
        raise AssetFolderError("Asset Folder path must be absolute.")
    normalized = normalize_path(folder)
    if require_exists and not os.path.isdir(normalized):
        raise AssetFolderError("Asset Folder path must be an existing directory.")
    return {"type": asset_type, "path": normalized, "enabled": True}


def _write_entries(entries, config_file=None):
    value = _read_config(config_file)
    value["assetFolders"] = entries
    try:
        config.write_config(value, config_file)
    except ValueError as error:
        raise AssetFolderError(str(error)) from error


def validate_folder_entry(asset_type, folder, *, require_exists=True):
    """Validate a candidate entry without changing the registry."""
    return _validated_entry(asset_type, folder, require_exists=require_exists)


def write_entries(entries, config_file=None):
    """Persist a prepared registry after its other transaction steps succeed."""
    _write_entries(entries, config_file)


def add_folder(asset_type, folder, config_file=None):
    entries = _read_entries(config_file)
    entry = _validated_entry(asset_type, folder, require_exists=True)
    if any(item["path"] == entry["path"] for item in entries):
        raise AssetFolderError("That Asset Folder path is already registered.")
    entries.append(entry)
    _write_entries(entries, config_file)
    return entries


def edit_folder(original_folder, asset_type, folder, config_file=None):
    entries = _read_entries(config_file)
    original = normalize_path(original_folder)
    index = next((i for i, item in enumerate(entries)
                  if item["path"] == original), None)
    if index is None:
        raise AssetFolderError("That Asset Folder is not registered.")
    target = normalize_path(folder)
    entry = _validated_entry(
        asset_type, folder, require_exists=(target != original))
    if any(i != index and item["path"] == entry["path"]
           for i, item in enumerate(entries)):
        raise AssetFolderError("That Asset Folder path is already registered.")
    entry["enabled"] = entries[index].get("enabled", True)
    entries[index] = entry
    _write_entries(entries, config_file)
    return entries


def delete_folder(folder, config_file=None):
    entries = _read_entries(config_file)
    target = normalize_path(folder)
    filtered = [item for item in entries if item["path"] != target]
    if len(filtered) == len(entries):
        raise AssetFolderError("That Asset Folder is not registered.")
    _write_entries(filtered, config_file)
    return filtered


def set_enabled(folder, enabled, config_file=None):
    """Change only a registered root's matching participation flag."""
    if not isinstance(enabled, bool):
        raise AssetFolderError("Asset Folder enabled must be a boolean.")
    entries = _read_entries(config_file)
    target = normalize_path(folder)
    index = next((i for i, item in enumerate(entries)
                  if item["path"] == target), None)
    if index is None:
        raise AssetFolderError("That Asset Folder is not registered.")
    entries[index]["enabled"] = enabled
    _write_entries(entries, config_file)
    return entries


def list_subfolders(folder, authorized_root, *, index=None, asset_type=None):
    """Return immediate directory children without following root escapes."""
    folder = normalize_path(folder)
    authorized_root = normalize_path(authorized_root)
    if not is_within(folder, authorized_root):
        raise AssetFolderError("That folder is outside the registered Asset Folder.")
    if not os.path.isdir(folder):
        raise AssetFolderError("Folder not found or is not a directory.")
    children = []
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                    child = normalize_path(entry.path)
                    if not is_within(child, authorized_root):
                        continue
                    item = {"name": entry.name, "path": child, "asset": False}
                    if index is not None:
                        relative = os.path.relpath(child, authorized_root).replace(
                            os.sep, "/")
                        from .index import find_asset_by_path
                        if find_asset_by_path(index, relative) is not None:
                            item["asset"] = True
                            if asset_type:
                                item["asset_type"] = asset_type
                    children.append(item)
                except OSError:
                    continue
    except OSError as error:
        raise AssetFolderError(f"Unable to read this folder: {error}") from error
    return sorted(children, key=lambda item: (item["name"].casefold(), item["name"]))

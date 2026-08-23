"""Transactional Asset Folder registry and index orchestration."""

from . import asset_folders, asset_index


class AssetCatalogError(ValueError):
    """A catalog operation failed without committing its registry change."""

    def __init__(self, message, *, index_preserved=False):
        super().__init__(message)
        self.index_preserved = index_preserved


def _error(error, *, index_preserved=False):
    if isinstance(error, AssetCatalogError):
        return error
    return AssetCatalogError(str(error), index_preserved=index_preserved)


def _commit_index_and_entries(asset_type, folder_path, index, entries):
    try:
        previous = asset_index.snapshot_index(asset_type, folder_path)
        asset_index.save_index(index)
    except (asset_folders.AssetFolderError, asset_index.AssetIndexError) as error:
        raise _error(error) from error

    try:
        asset_folders.write_entries(entries)
    except (asset_folders.AssetFolderError, asset_index.AssetIndexError) as error:
        try:
            asset_index.restore_index(asset_type, folder_path, previous)
        except asset_index.AssetIndexError as restore_error:
            raise AssetCatalogError(
                f"{error}; unable to restore Asset index: {restore_error}",
                index_preserved=previous is not None) from restore_error
        raise _error(error, index_preserved=previous is not None) from error


def add(asset_type, folder_path):
    try:
        entries = asset_folders.load_registry()
        entry = asset_folders.validate_folder_entry(
            asset_type, folder_path, require_exists=True)
        if any(item["path"] == entry["path"] for item in entries):
            raise asset_folders.AssetFolderError(
                "That Asset Folder path is already registered.")
        new_entries = entries + [entry]
        index = asset_index.build_index(asset_type, entry["path"])
        _commit_index_and_entries(
            asset_type, entry["path"], index, new_entries)
        return new_entries
    except (asset_folders.AssetFolderError, asset_index.AssetIndexError) as error:
        raise _error(error) from error


def edit(original_path, asset_type, folder_path):
    try:
        entries = asset_folders.load_registry()
        original_index = next(
            (i for i, item in enumerate(entries)
             if item["path"] == original_path), None)
        if original_index is None:
            raise asset_folders.AssetFolderError(
                "That Asset Folder is not registered.")
        original_type = entries[original_index]["type"]
        entry = asset_folders.validate_folder_entry(
            asset_type, folder_path,
            require_exists=(folder_path != original_path))
        if any(i != original_index and item["path"] == entry["path"]
               for i, item in enumerate(entries)):
            raise asset_folders.AssetFolderError(
                "That Asset Folder path is already registered.")
        entry["enabled"] = entries[original_index].get("enabled", True)
        new_entries = list(entries)
        new_entries[original_index] = entry
        index = asset_index.build_index(asset_type, entry["path"])
        _commit_index_and_entries(
            asset_type, entry["path"], index, new_entries)
    except (asset_folders.AssetFolderError, asset_index.AssetIndexError) as error:
        raise _error(error) from error

    if original_path != entry["path"] or original_type != asset_type:
        try:
            asset_index.delete_index(original_type, original_path)
        except asset_index.AssetIndexError:
            pass
    return new_entries


def delete(folder_path):
    try:
        entries = asset_folders.load_registry()
        original = next(
            (item for item in entries if item["path"] == folder_path), None)
        new_entries = asset_folders.delete_folder(folder_path)
    except asset_folders.AssetFolderError as error:
        raise _error(error) from error
    if original:
        try:
            asset_index.delete_index(original["type"], folder_path)
        except asset_index.AssetIndexError:
            pass
    return new_entries


def set_enabled(folder_path, enabled):
    try:
        return asset_folders.set_enabled(folder_path, enabled)
    except asset_folders.AssetFolderError as error:
        raise _error(error) from error


def rebuild(folder_path):
    try:
        entries = asset_folders.load_registry()
        entry = next(
            (item for item in entries if item["path"] == folder_path), None)
        if entry is None:
            raise asset_folders.AssetFolderError(
                "That Asset Folder is not registered.")
        previous = asset_index.snapshot_index(entry["type"], folder_path)
        index = asset_index.build_index(entry["type"], folder_path)
        asset_index.save_index(index)
    except (asset_folders.AssetFolderError, asset_index.AssetIndexError) as error:
        raise _error(error, index_preserved=(
            previous is not None if "previous" in locals() else False)) from error
    return entries

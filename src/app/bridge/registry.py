"""Bridge orchestration for Mod and Asset Folder registries."""

import os

from app.assets import catalog as asset_catalog
from app.assets import folders as asset_folders
from app.assets import index as asset_index
from app.settings import mod_folders


class ModFolderRegistry:
    def __init__(self, access):
        self._access = access

    @staticmethod
    def _folder_entries(entries):
        return [{**entry, "exists": os.path.isdir(entry["path"])}
                for entry in entries]

    def get_mod_folders(self):
        try:
            entries = mod_folders.load_registry()
        except mod_folders.ModFolderError as error:
            return {"folders": [], "error": str(error)}
        self._access.refresh_mod_roots(entries)
        return {"folders": self._folder_entries(entries)}

    def get_panel_opacity(self):
        try:
            return {"value": mod_folders.load_panel_opacity()}
        except mod_folders.ModFolderError as error:
            return {"error": str(error),
                    "value": mod_folders.DEFAULT_PANEL_OPACITY}

    def set_panel_opacity(self, value):
        try:
            return {"value": mod_folders.save_panel_opacity(value)}
        except mod_folders.ModFolderError as error:
            return {"error": str(error)}

    def add_mod_folder(self, name, folder_path):
        folder_path = mod_folders.normalize_path(folder_path)
        if not self._access.was_picker_selected(folder_path):
            return {
                "error": "Choose the Mod Folder through the native folder picker first."
            }
        try:
            entries = mod_folders.add_folder(name, folder_path)
        except mod_folders.ModFolderError as error:
            return {"error": str(error)}
        self._access.refresh_mod_roots(entries)
        return {"folders": self._folder_entries(entries)}

    def edit_mod_folder(self, original_path, name, folder_path):
        original_path = mod_folders.normalize_path(original_path)
        folder_path = mod_folders.normalize_path(folder_path)
        try:
            entries = mod_folders.load_registry()
            if not any(item["path"] == original_path for item in entries):
                raise mod_folders.ModFolderError(
                    "That Mod Folder is not registered.")
            if (folder_path != original_path and
                    not self._access.was_picker_selected(folder_path)):
                raise mod_folders.ModFolderError(
                    "Choose the new Mod Folder through the native folder picker first.")
            entries = mod_folders.edit_folder(original_path, name, folder_path)
        except mod_folders.ModFolderError as error:
            return {"error": str(error)}
        self._access.refresh_mod_roots(entries)
        return {"folders": self._folder_entries(entries)}

    def delete_mod_folder(self, folder_path):
        try:
            entries = mod_folders.delete_folder(folder_path)
        except mod_folders.ModFolderError as error:
            return {"error": str(error)}
        self._access.refresh_mod_roots(entries)
        return {"folders": self._folder_entries(entries)}

    def list_subfolders(self, folder_path):
        requested = mod_folders.normalize_path(folder_path)
        try:
            entries = mod_folders.load_registry()
            roots = [entry["path"] for entry in entries
                     if mod_folders.is_within(requested, entry["path"])]
            if not roots:
                raise PermissionError(
                    "That folder is not inside a registered Mod Folder.")
            # The narrowest matching root is the most precise boundary when
            # registered roots are nested.
            root = max(roots, key=len)
            return {"folders": mod_folders.list_subfolders(requested, root)}
        except PermissionError as error:
            return {"error": str(error)}
        except mod_folders.ModFolderError as error:
            return {"error": str(error)}


class AssetFolderRegistry:
    def __init__(self, access, on_changed=None):
        self._access = access
        self._on_changed = on_changed

    @staticmethod
    def _asset_folder_entries(entries):
        result = []
        for entry in entries:
            item = {**entry, "exists": os.path.isdir(entry["path"])}
            item["index"] = asset_index.index_status(
                entry["type"], entry["path"])
            result.append(item)
        return result

    def _changed(self, entries, *, grant=None):
        self._access.refresh_asset_roots(entries)
        if grant is not None:
            self._access.grant_asset_folder(grant)
        if self._on_changed is not None:
            self._on_changed()

    def get_asset_folders(self):
        try:
            entries = asset_folders.load_registry()
        except asset_folders.AssetFolderError as error:
            return {"folders": [], "error": str(error)}
        self._access.refresh_asset_roots(entries)
        return {"folders": self._asset_folder_entries(entries)}

    def add_asset_folder(self, asset_type, folder_path):
        folder_path = asset_folders.normalize_path(folder_path)
        if not self._access.was_picker_selected(folder_path):
            return {
                "error": "Choose the Asset Folder through the native folder picker first."
            }
        try:
            entries = asset_catalog.add(asset_type, folder_path)
        except asset_catalog.AssetCatalogError as error:
            return {"error": str(error)}
        self._changed(entries, grant=folder_path)
        return {"folders": self._asset_folder_entries(entries)}

    def edit_asset_folder(self, original_path, asset_type, folder_path):
        original_path = asset_folders.normalize_path(original_path)
        folder_path = asset_folders.normalize_path(folder_path)
        if (folder_path != original_path and
                not self._access.was_picker_selected(folder_path)):
            return {
                "error": "Choose the new Asset Folder through the native folder picker first."
            }
        try:
            entries = asset_catalog.edit(original_path, asset_type, folder_path)
        except asset_catalog.AssetCatalogError as error:
            return {"error": str(error)}
        self._changed(entries, grant=folder_path)
        return {"folders": self._asset_folder_entries(entries)}

    def delete_asset_folder(self, folder_path):
        folder_path = asset_folders.normalize_path(folder_path)
        try:
            entries = asset_catalog.delete(folder_path)
        except asset_catalog.AssetCatalogError as error:
            return {"error": str(error)}
        self._changed(entries)
        return {"folders": self._asset_folder_entries(entries)}

    def set_asset_folder_enabled(self, folder_path, enabled):
        folder_path = asset_folders.normalize_path(folder_path)
        try:
            entries = asset_catalog.set_enabled(folder_path, enabled)
        except asset_catalog.AssetCatalogError as error:
            return {"error": str(error)}
        self._changed(entries)
        return {"folders": self._asset_folder_entries(entries)}

    def rebuild_asset_index(self, folder_path):
        folder_path = asset_folders.normalize_path(folder_path)
        try:
            entries = asset_catalog.rebuild(folder_path)
        except asset_catalog.AssetCatalogError as error:
            return {
                "error": f"Could not rebuild Asset index: {error}",
                "indexPreserved": error.index_preserved,
            }
        self._changed(entries)
        return {"folders": self._asset_folder_entries(entries)}

    def list_asset_subfolders(self, folder_path):
        requested = asset_folders.normalize_path(folder_path)
        try:
            entries = asset_folders.load_registry()
            roots = [entry for entry in entries
                     if asset_folders.is_within(requested, entry["path"])]
            if not roots:
                raise PermissionError(
                    "That folder is not inside a registered Asset Folder.")
            entry = max(roots, key=lambda item: len(item["path"]))
            index = None
            try:
                index = asset_index.load_index(entry["type"], entry["path"])
            except asset_index.AssetIndexError:
                # The tree remains browseable while the registry panel reports
                # an invalid cache. The index is still the only asset authority.
                index = None
            return {"folders": asset_folders.list_subfolders(
                requested, entry["path"], index=index,
                asset_type=entry["type"])}
        except PermissionError as error:
            return {"error": str(error)}
        except asset_folders.AssetFolderError as error:
            return {"error": str(error)}

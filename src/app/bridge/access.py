"""Authorization state for Mod and Asset folders."""

import os

from app.assets import folders as asset_folders
from app.settings import mod_folders


class FolderAccess:
    """Keep native-picker, launch, and registered-root trust boundaries distinct."""

    def __init__(self):
        self._authorized_folders = set()
        self._picker_authorized_folders = set()
        self._authorized_roots = set()
        self._authorized_asset_folders = set()
        self._authorized_asset_roots = set()
        try:
            self.refresh_mod_roots(mod_folders.load_registry())
        except mod_folders.ModFolderError:
            # Optional malformed configuration is reported by the registry UI;
            # it must not prevent the bridge from being constructed.
            pass
        try:
            self.refresh_asset_roots(asset_folders.load_registry())
        except asset_folders.AssetFolderError:
            pass

    def mod_folder(self, folder_path):
        requested = mod_folders.normalize_path(folder_path)
        if requested in self._authorized_folders:
            return requested
        if any(mod_folders.is_within(requested, root)
               for root in self._authorized_roots):
            # Preserve access to an exact descendant if its registry root is
            # later edited or removed during this process lifetime.
            self._authorized_folders.add(requested)
            return requested
        if not requested:
            raise PermissionError(
                "This folder was not selected through the native folder picker.")
        raise PermissionError(
            "This folder was not selected through the native folder picker.")

    def asset_folder(self, folder_path):
        requested = asset_folders.normalize_path(folder_path)
        if requested in self._authorized_asset_folders:
            return requested
        if any(asset_folders.is_within(requested, root)
               for root in self._authorized_asset_roots):
            self._authorized_asset_folders.add(requested)
            return requested
        raise PermissionError(
            "This folder is not inside a registered Asset Folder.")

    def remember_mod_picker_selection(self, folder_path):
        folder = mod_folders.normalize_path(folder_path)
        self._authorized_folders.add(folder)
        self._picker_authorized_folders.add(folder)
        return folder

    def remember_mod_launch_selection(self, folder_path):
        """Authorize one existing absolute folder for this process only."""
        try:
            raw_path = os.fspath(folder_path)
        except TypeError as error:
            raise ValueError("Startup mod path must be absolute.") from error
        if not isinstance(raw_path, str) or not raw_path.strip() \
                or not os.path.isabs(raw_path):
            raise ValueError("Startup mod path must be absolute.")

        folder = mod_folders.normalize_path(raw_path)
        if not os.path.isdir(folder):
            raise ValueError(f"Startup mod folder does not exist: {folder}")
        self._authorized_folders.add(folder)
        return folder

    def remember_asset_picker_selection(self, folder_path):
        folder = asset_folders.normalize_path(folder_path)
        # Mod and Asset pickers deliberately share this prerequisite set, but
        # selecting an Asset Folder never grants Mod Folder access.
        self._picker_authorized_folders.add(folder)
        return folder

    def was_picker_selected(self, folder_path):
        return mod_folders.normalize_path(folder_path) in \
            self._picker_authorized_folders

    def grant_asset_folder(self, folder_path):
        self._authorized_asset_folders.add(
            asset_folders.normalize_path(folder_path))

    def refresh_mod_roots(self, entries):
        self._authorized_roots = mod_folders.registered_paths(entries)

    def refresh_asset_roots(self, entries):
        roots = asset_folders.registered_paths(entries)
        self._authorized_asset_roots = roots
        self._authorized_asset_folders = {
            path for path in self._authorized_asset_folders
            if any(asset_folders.is_within(path, root) for root in roots)
        }

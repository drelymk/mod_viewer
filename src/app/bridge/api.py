"""The explicit JavaScript facade for the Mod Viewer application.

Every public method here is callable from the UI. Domain state and
orchestration live in underscore-private collaborators so pywebview exposes
only this class's deliberate bridge contract.
"""

import traceback

import webview

from app.bridge.access import FolderAccess
from app.bridge.asset_preview import AssetPreview
from app.bridge import ini as ini_api
from app.bridge import present as present_api
from app.bridge.mod_preview import ModPreview
from app.bridge.registry import AssetFolderRegistry, ModFolderRegistry
from app.bridge import toggle as toggle_api


class ModViewerAPI:
    def __init__(self):
        # Underscore-private on purpose: pywebview reflects over the js_api
        # object's public attributes to expose them to JavaScript, and the
        # native window is a deeply self-referential COM object. Exposing it
        # sends that reflection into infinite recursion.
        self._window = None
        self._access = FolderAccess()
        self._mod_preview = ModPreview(self._access)
        self._asset_preview = AssetPreview(self._access)
        self._mod_registry = ModFolderRegistry(self._access)
        self._asset_registry = AssetFolderRegistry(
            self._access,
            on_changed=self._asset_preview.invalidate_plan_cache)

    # -- native folder pickers ---------------------------------------------

    def select_folder(self):
        """Open a native folder-picker dialog. Returns None if cancelled."""
        result = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        if not result:
            return None
        return self._access.remember_mod_picker_selection(result[0])

    def select_asset_folder(self):
        """Pick an Asset Folder without granting mod-folder access."""
        result = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        if not result:
            return None
        return self._access.remember_asset_picker_selection(result[0])

    # -- persistent Mod Folders registry -----------------------------------

    def get_mod_folders(self):
        return self._mod_registry.get_mod_folders()

    def get_panel_opacity(self):
        return self._mod_registry.get_panel_opacity()

    def set_panel_opacity(self, value):
        return self._mod_registry.set_panel_opacity(value)

    def add_mod_folder(self, name, folder_path):
        return self._mod_registry.add_mod_folder(name, folder_path)

    def edit_mod_folder(self, original_path, name, folder_path):
        return self._mod_registry.edit_mod_folder(
            original_path, name, folder_path)

    def delete_mod_folder(self, folder_path):
        return self._mod_registry.delete_mod_folder(folder_path)

    def list_subfolders(self, folder_path):
        return self._mod_registry.list_subfolders(folder_path)

    # -- persistent Asset Folders registry ---------------------------------

    def get_asset_folders(self):
        return self._asset_registry.get_asset_folders()

    def add_asset_folder(self, asset_type, folder_path):
        return self._asset_registry.add_asset_folder(asset_type, folder_path)

    def edit_asset_folder(self, original_path, asset_type, folder_path):
        return self._asset_registry.edit_asset_folder(
            original_path, asset_type, folder_path)

    def delete_asset_folder(self, folder_path):
        return self._asset_registry.delete_asset_folder(folder_path)

    def set_asset_folder_enabled(self, folder_path, enabled):
        return self._asset_registry.set_asset_folder_enabled(folder_path, enabled)

    def rebuild_asset_index(self, folder_path):
        return self._asset_registry.rebuild_asset_index(folder_path)

    def list_asset_subfolders(self, folder_path):
        return self._asset_registry.list_asset_subfolders(folder_path)

    # -- Asset preview and fill --------------------------------------------

    def load_asset(self, folder_path):
        return self._asset_preview.load_asset(folder_path)

    def pick_asset_texture_file(self, folder_path, texture_role=None):
        return self._asset_preview.pick_asset_texture_file(
            self._window, folder_path, texture_role)

    def load_missing_asset_parts(self, folder_path):
        """Append original Asset parts not covered by the current mod INIs."""
        try:
            folder_path, overrides, _pending, context = \
                self._mod_preview.authoritative_context(folder_path)
            return self._asset_preview.load_missing_asset_parts(
                folder_path, context, overrides)
        except PermissionError as error:
            return {"status": "error", "error": str(error)}
        except Exception:
            traceback.print_exc()
            return {
                "status": "error",
                "error": "Could not load missing Asset parts. See the application log for details.",
            }

    def remove_missing_asset_parts(self, folder_path, fill_id=None):
        return self._asset_preview.remove_missing_asset_parts(
            folder_path, fill_id)

    # -- Mod preview --------------------------------------------------------

    def load_mod(self, folder_path):
        self._asset_preview.clear_fill()
        return self._mod_preview.load_mod(folder_path)

    def get_present_state(self, folder_path):
        return self._mod_preview.get_present_state(folder_path)

    def get_control_state(self, folder_path):
        return self._mod_preview.get_control_state(folder_path)

    def get_mesh_semantics(self, folder_path):
        return self._mod_preview.get_mesh_semantics(folder_path)

    def get_skinning_preview(self, folder_path, mesh_key):
        return self._mod_preview.get_skinning_preview(folder_path, mesh_key)

    def get_diagnostics(self, folder_path):
        return self._mod_preview.get_diagnostics(folder_path)

    def save_mesh_names(self, folder_path, names):
        return self._mod_preview.save_mesh_names(folder_path, names)

    def save_mesh_textures(self, folder_path, textures):
        return self._mod_preview.save_mesh_textures(folder_path, textures)

    def save_component_material_kind(self, folder_path, source, component,
                                     material_kind):
        return self._mod_preview.save_component_material_kind(
            folder_path, source, component, material_kind)

    def pick_texture_file(self, folder_path, texture_role=None):
        return self._mod_preview.pick_texture_file(
            self._window, folder_path, texture_role)

    # -- in-memory INI viewer/editor ----------------------------------------

    def list_ini_files(self, folder_path):
        return ini_api.list_inis(self._access.mod_folder(folder_path))

    def get_ini_text(self, folder_path, ini_name):
        return ini_api.get_text(self._access.mod_folder(folder_path), ini_name)

    def update_ini_text(self, folder_path, ini_name, text):
        return ini_api.update_text(
            self._access.mod_folder(folder_path), ini_name, text)

    # -- toggle authoring ---------------------------------------------------
    #
    # Each call stages its change in memory only (app/session/edit.py) --
    # nothing reaches the real ini file until export_changes() is called.

    def list_toggle_source_inis(self, folder_path):
        return toggle_api.list_source_inis(
            self._access.mod_folder(folder_path))

    def get_toggle_details(self, folder_path, ini_rel, section_name):
        return toggle_api.get_toggle_details(
            self._access.mod_folder(folder_path), ini_rel, section_name)

    def add_toggle(self, folder_path, ini_rel, name, key_combo, var, values,
                   options=None):
        return toggle_api.add_toggle(
            self._access.mod_folder(folder_path), ini_rel, name, key_combo,
            var, values, options)

    def edit_toggle(self, folder_path, ini_rel, section_name, changes=None):
        return toggle_api.edit_toggle(
            self._access.mod_folder(folder_path), ini_rel, section_name,
            changes)

    def delete_toggle(self, folder_path, ini_rel, section_name):
        return toggle_api.delete_toggle(
            self._access.mod_folder(folder_path), ini_rel, section_name)

    # -- PRESENT preset cycle ----------------------------------------------

    def add_present(self, folder_path, key_combo, back_combo, snapshots):
        return present_api.add_present(
            self._access.mod_folder(folder_path), key_combo, back_combo,
            snapshots)

    def edit_present(self, folder_path, key_combo, back_combo):
        return present_api.edit_present(
            self._access.mod_folder(folder_path), key_combo, back_combo)

    def delete_present(self, folder_path):
        return present_api.delete_present(self._access.mod_folder(folder_path))

    def capture_present(self, folder_path, snapshots, name, position=None,
                        allow_duplicate=False):
        return present_api.capture_present(
            self._access.mod_folder(folder_path), snapshots, name, position,
            allow_duplicate)

    def delete_present_position(self, folder_path, position):
        return present_api.delete_present_position(
            self._access.mod_folder(folder_path), position)

    # -- export / discard ---------------------------------------------------

    def has_pending_changes(self, folder_path):
        return toggle_api.has_pending_changes(
            self._access.mod_folder(folder_path))

    def export_changes(self, folder_path):
        return toggle_api.export_changes(
            self._access.mod_folder(folder_path))

    def discard_changes(self, folder_path):
        return toggle_api.discard_changes(
            self._access.mod_folder(folder_path))

    # -- record mode --------------------------------------------------------

    def get_record_positions(self, folder_path, ini_rel, section_name):
        return toggle_api.get_record_positions(
            self._access.mod_folder(folder_path), ini_rel, section_name)

    def record_toggle(self, folder_path, ini_rel, section_name, position_lines):
        return toggle_api.record_toggle(
            self._access.mod_folder(folder_path), ini_rel, section_name,
            position_lines)

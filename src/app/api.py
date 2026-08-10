"""The object bridged into JavaScript as `window.pywebview.api`.

Every public method here is callable from the UI, so the surface is kept
deliberately small: anything that doesn't need a window or a native dialog
belongs in mod_loader instead.
"""

import webview

from . import edit_session, mesh_metadata, mod_loader, toggle_api


class ModViewerAPI:
    def __init__(self):
        # Underscore-private on purpose: pywebview reflects over the js_api
        # object's public attributes to expose them to JavaScript, and the
        # native window is a deeply self-referential COM object — exposing it
        # sends that reflection into infinite recursion.
        self._window = None

    def select_folder(self):
        """Open a native folder-picker dialog. Returns None if cancelled."""
        result = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        return result[0] if result else None

    def load_mod(self, folder_path):
        # Preview any pending, not-yet-exported edits over the real files.
        overrides = edit_session.overrides_for(folder_path)
        pending_new_sections = edit_session.new_sections_for(folder_path)
        result = mod_loader.load_mod(folder_path, overrides=overrides,
                                     pending_new_sections=pending_new_sections)
        if isinstance(result, dict) and not result.get("error"):
            result["__mesh_names__"] = mesh_metadata.load(folder_path)
        return result

    def save_mesh_names(self, folder_path, names):
        return mesh_metadata.save(folder_path, names)

    # -- toggle authoring -----------------------------------------------------
    #
    # Each call stages its change in memory only (app/edit_session.py) --
    # nothing reaches the real ini file until export_changes() is called.

    def list_toggle_source_inis(self, folder_path):
        return toggle_api.list_source_inis(folder_path)

    def get_toggle_details(self, folder_path, ini_rel, section_name):
        return toggle_api.get_toggle_details(folder_path, ini_rel, section_name)

    def add_toggle(self, folder_path, ini_rel, name, key_combo, var, values, options=None):
        return toggle_api.add_toggle(folder_path, ini_rel, name, key_combo, var, values, options)

    def edit_toggle(self, folder_path, ini_rel, section_name, changes=None):
        return toggle_api.edit_toggle(folder_path, ini_rel, section_name, changes)

    def delete_toggle(self, folder_path, ini_rel, section_name):
        return toggle_api.delete_toggle(folder_path, ini_rel, section_name)

    # -- export / discard ----------------------------------------------------

    def has_pending_changes(self, folder_path):
        return toggle_api.has_pending_changes(folder_path)

    def export_changes(self, folder_path):
        return toggle_api.export_changes(folder_path)

    def discard_changes(self, folder_path):
        return toggle_api.discard_changes(folder_path)

    # -- record mode ----------------------------------------------------------

    def get_record_positions(self, folder_path, ini_rel, section_name):
        return toggle_api.get_record_positions(folder_path, ini_rel, section_name)

    def record_toggle(self, folder_path, ini_rel, section_name, position_lines):
        return toggle_api.record_toggle(folder_path, ini_rel, section_name, position_lines)

"""The object bridged into JavaScript as `window.pywebview.api`.

Every public method here is callable from the UI, so the surface is kept
deliberately small: anything that doesn't need a window or a native dialog
belongs in mod_loader instead.
"""

import os

import webview

from core.mesh_builder import encode_texture_file
from core.ini_sections import find_inis

from . import (edit_session, ini_api, metadata, mod_loader, present_api,
               server, toggle_api)


class ModViewerAPI:
    def __init__(self):
        # Underscore-private on purpose: pywebview reflects over the js_api
        # object's public attributes to expose them to JavaScript, and the
        # native window is a deeply self-referential COM object — exposing it
        # sends that reflection into infinite recursion.
        self._window = None
        self._authorized_folders = set()

    def _folder(self, folder_path):
        requested = os.path.normcase(os.path.abspath(folder_path or ""))
        if requested not in self._authorized_folders:
            raise PermissionError("This folder was not selected through the native folder picker.")
        return requested

    def select_folder(self):
        """Open a native folder-picker dialog. Returns None if cancelled."""
        result = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        if not result:
            return None
        folder = os.path.normcase(os.path.abspath(result[0]))
        self._authorized_folders.add(folder)
        return folder

    def pick_texture_file(self, folder_path):
        """Open a native file-picker rooted at the mod folder for the
        per-mesh/per-component texture picker. Returns {"tex_key", "uri"} / {"error"} on
        a real pick, None if the dialog was cancelled -- same shape as
        select_folder's own cancel case.
        """
        folder_path = self._folder(folder_path)
        result = self._window.create_file_dialog(
            webview.FileDialog.OPEN, directory=folder_path,
            file_types=("Textures (*.dds;*.png;*.jpg;*.jpeg;*.tga)",))
        if not result:
            return None
        return encode_texture_file(folder_path, result[0])

    def load_texture_file(self, folder_path, tex_key):
        """Encode a known mod-relative picker texture on first use."""
        folder_path = self._folder(folder_path)
        return encode_texture_file(folder_path,
                                   os.path.join(folder_path, tex_key))

    def load_mod(self, folder_path):
        folder_path = self._folder(folder_path)
        # Every active INI is loaded once into the authoritative in-memory
        # session. Reloads, text edits and toggle edits all read these docs;
        # only Export copies dirty versions back to physical files.
        edit_session.load_documents(folder_path, find_inis(folder_path))
        overrides = edit_session.overrides_for(folder_path)
        pending_new_sections = edit_session.new_sections_for(folder_path)
        result = mod_loader.load_mod(folder_path, overrides=overrides,
                                     pending_new_sections=pending_new_sections)
        if isinstance(result, dict) and not result.get("error"):
            saved_metadata = metadata.load(folder_path)
            result["__mesh_names__"] = saved_metadata.get("mesh_names", {})
            metadata.hydrate_textures(folder_path, result)
            metadata.hydrate_present(folder_path, result.get("__present__"))
            server.publish_payload_geometry(result)
        return result

    def save_mesh_names(self, folder_path, names):
        return metadata.save_mesh_names(self._folder(folder_path), names)

    def save_mesh_textures(self, folder_path, textures):
        return metadata.save_textures(self._folder(folder_path), textures)

    # -- in-memory INI viewer/editor ----------------------------------------

    def list_ini_files(self, folder_path):
        return ini_api.list_inis(self._folder(folder_path))

    def get_ini_text(self, folder_path, ini_name):
        return ini_api.get_text(self._folder(folder_path), ini_name)

    def update_ini_text(self, folder_path, ini_name, text):
        return ini_api.update_text(self._folder(folder_path), ini_name, text)

    # -- toggle authoring -----------------------------------------------------
    #
    # Each call stages its change in memory only (app/edit_session.py) --
    # nothing reaches the real ini file until export_changes() is called.

    def list_toggle_source_inis(self, folder_path):
        return toggle_api.list_source_inis(self._folder(folder_path))

    def get_toggle_details(self, folder_path, ini_rel, section_name):
        return toggle_api.get_toggle_details(self._folder(folder_path), ini_rel, section_name)

    def add_toggle(self, folder_path, ini_rel, name, key_combo, var, values, options=None):
        return toggle_api.add_toggle(self._folder(folder_path), ini_rel, name, key_combo, var, values, options)

    def edit_toggle(self, folder_path, ini_rel, section_name, changes=None):
        return toggle_api.edit_toggle(self._folder(folder_path), ini_rel, section_name, changes)

    def delete_toggle(self, folder_path, ini_rel, section_name):
        return toggle_api.delete_toggle(self._folder(folder_path), ini_rel, section_name)

    # -- PRESENT preset cycle -----------------------------------------------

    def add_present(self, folder_path, key_combo, back_combo, snapshots):
        return present_api.add_present(
            self._folder(folder_path), key_combo, back_combo, snapshots)

    def edit_present(self, folder_path, key_combo, back_combo):
        return present_api.edit_present(
            self._folder(folder_path), key_combo, back_combo)

    def delete_present(self, folder_path):
        return present_api.delete_present(self._folder(folder_path))

    def capture_present(self, folder_path, snapshots, name, position=None,
                        allow_duplicate=False):
        return present_api.capture_present(
            self._folder(folder_path), snapshots, name, position,
            allow_duplicate)

    def delete_present_position(self, folder_path, position):
        return present_api.delete_present_position(
            self._folder(folder_path), position)

    # -- export / discard ----------------------------------------------------

    def has_pending_changes(self, folder_path):
        return toggle_api.has_pending_changes(self._folder(folder_path))

    def export_changes(self, folder_path):
        return toggle_api.export_changes(self._folder(folder_path))

    def discard_changes(self, folder_path):
        return toggle_api.discard_changes(self._folder(folder_path))

    # -- record mode ----------------------------------------------------------

    def get_record_positions(self, folder_path, ini_rel, section_name):
        return toggle_api.get_record_positions(self._folder(folder_path), ini_rel, section_name)

    def record_toggle(self, folder_path, ini_rel, section_name, position_lines):
        return toggle_api.record_toggle(self._folder(folder_path), ini_rel, section_name, position_lines)

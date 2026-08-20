"""The object bridged into JavaScript as `window.pywebview.api`.

Every public method here is callable from the UI, so the surface is kept
deliberately small: anything that doesn't need a window or a native dialog
belongs in mod_loader instead.
"""

import os

import webview

from core.mesh_builder import (GeometryBlob, encode_texture_file,
                               encode_texture_key)
from core.mod_discovery import discover_ini_paths
from core.ini_health import analyze_mod
from core.texture_profiles import texture_profile_for

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

    @staticmethod
    def _active_texture_source(folder_path, validate=False):
        publication = server.active_texture_publication(folder_path)
        if publication is None:
            return None
        if not validate:
            return publication.register

        def register(path, role=None, transform=None):
            return publication.register(
                path, role, validate=True, transform=transform)

        return register

    def select_folder(self):
        """Open a native folder-picker dialog. Returns None if cancelled."""
        result = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        if not result:
            return None
        folder = os.path.normcase(os.path.abspath(result[0]))
        self._authorized_folders.add(folder)
        return folder

    def pick_texture_file(self, folder_path, texture_role=None):
        """Open a native file-picker rooted at the mod folder for the
        per-mesh/per-component texture picker. Returns {"tex_key", "uri"} / {"error"} on
        a real pick, None if the dialog was cancelled -- same shape as
        select_folder's own cancel case.
        """
        folder_path = self._folder(folder_path)
        if texture_role not in (None, "normal_map", "light_map", "material_map"):
            return {"error": "Unknown texture role."}
        result = self._window.create_file_dialog(
            webview.FileDialog.OPEN, directory=folder_path,
            file_types=("Textures (*.dds;*.png;*.jpg;*.jpeg;*.tga)",))
        if not result:
            return None
        texture_source = self._active_texture_source(
            folder_path, validate=True)
        encoded = encode_texture_file(
            folder_path, result[0], texture_role,
            texture_source=texture_source)
        if (encoded.get("error") or texture_role != "normal_map"):
            return encoded

        # WuWa's display normal is derived from a packed source. Keep the
        # original RGBA source paired with a manually selected NormalMap so
        # the material adapter can consume the exact authored data rather than
        # whichever normal happened to be selected before it.
        publication = server.active_texture_publication(folder_path)
        profile = texture_profile_for(
            publication.game_profile if publication else None)
        if not profile.retain_normal_data:
            return encoded
        raw = encode_texture_file(
            folder_path, result[0], "normal_data",
            texture_source=texture_source, texture_profile=profile)
        if raw.get("error"):
            return raw
        encoded["normal_data_key"] = raw["tex_key"]
        encoded["normal_data_file"] = raw["file"]
        encoded["normal_data_uri"] = raw["uri"]
        return encoded

    def load_texture_file(self, folder_path, tex_key):
        """Resolve a known mod-relative picker texture on first use."""
        folder_path = self._folder(folder_path)
        return encode_texture_key(
            folder_path, tex_key,
            texture_source=self._active_texture_source(folder_path, validate=True))

    def load_mod(self, folder_path):
        folder_path = self._folder(folder_path)
        # Reopen the same mod from its already-owned path set.  This keeps a
        # staged edit that changes root geometry from triggering a new disk
        # discovery and leaves only the first open responsible for selection.
        ini_paths = (edit_session.document_paths(folder_path)
                     or discover_ini_paths(folder_path))
        # Every active INI is loaded once into the authoritative in-memory
        # session. Reloads, text edits and toggle edits all read these docs;
        # only Export copies dirty versions back to physical files.
        edit_session.load_documents(folder_path, ini_paths)
        overrides = edit_session.overrides_for(folder_path)
        pending_new_sections = edit_session.new_sections_for(folder_path)
        context = mod_loader.ModLoadContext(
            folder_path, ini_paths, edit_session.documents_for(folder_path),
            metadata.load(folder_path))
        geometry = GeometryBlob()
        publication = server.begin_texture_publication(folder_path)
        try:
            result = mod_loader.load_mod(
                context=context, overrides=overrides,
                pending_new_sections=pending_new_sections, geometry=geometry,
                texture_source=publication.register)
            if not isinstance(result, dict) or result.get("error"):
                publication.discard()
                return result

            saved_metadata = context.metadata
            result.setdefault("metadata", {})["mesh_names"] = \
                saved_metadata.get("mesh_names", {})
            game_metadata = result.get("metadata", {}).get("game", {})
            publication.set_game_profile(game_metadata.get("id"))
            metadata.hydrate_textures(
                folder_path, result, saved_metadata,
                texture_source=publication.register,
                texture_profile=game_metadata.get("id"))
            controls = result.setdefault("controls", {})
            metadata.hydrate_present(folder_path, controls.get("present"),
                                      saved_metadata)
            server.publish_payload_geometry(result, geometry)
            publication.commit()
            return result
        except Exception:
            publication.discard()
            raise

    def get_diagnostics(self, folder_path):
        """Return the read-only health scan for the current edit revision."""
        folder_path = self._folder(folder_path)
        cached = edit_session.cached_diagnostics(folder_path)
        if cached is not None:
            return cached
        ini_paths = edit_session.document_paths(folder_path)
        if not ini_paths:
            ini_paths = discover_ini_paths(folder_path)
            edit_session.load_documents(folder_path, ini_paths)
        try:
            report = analyze_mod(
                folder_path, ini_paths=ini_paths,
                overrides=edit_session.overrides_for(folder_path),
                documents=edit_session.documents_for(folder_path))
            return edit_session.cache_diagnostics(folder_path, report)
        except Exception:
            return edit_session.cache_diagnostics(folder_path, {
                "summary": {"errors": 0, "warnings": 1, "issues": 1,
                            "unused_files": 0, "unused_resources": 0},
                "files": {"unreferenced": 0, "inactive_only": 0,
                          "viewer_only": 0, "referenced": 0},
                "issues": [{
                    "code": "health_check_failed", "severity": "warning",
                    "category": "ini",
                    "message": "The INI diagnostics could not be completed.",
                }],
            })

    def save_mesh_names(self, folder_path, names):
        return metadata.save_mesh_names(self._folder(folder_path), names)

    def save_mesh_textures(self, folder_path, textures):
        folder_path = self._folder(folder_path)
        result = metadata.save_textures(folder_path, textures)
        edit_session.invalidate_diagnostics(folder_path)
        return result

    def save_component_material_kind(self, folder_path, source, component,
                                     material_kind):
        folder_path = self._folder(folder_path)
        result = metadata.save_component_material_kind(
            folder_path, source, component, material_kind)
        edit_session.invalidate_diagnostics(folder_path)
        return result

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

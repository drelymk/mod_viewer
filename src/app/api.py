"""The object bridged into JavaScript as `window.pywebview.api`.

Every public method here is callable from the UI, so the surface is kept
deliberately small: anything that doesn't need a window or a native dialog
belongs in mod_loader instead.
"""

import os
import traceback

import webview

from core.mesh_builder import GeometryBlob
from core.textures import encode_texture_file
from core.mod_discovery import discover_ini_paths
from core.ini_health import analyze_mod
from core.texture_profiles import texture_profile_for

from . import (asset_catalog, asset_folders, asset_index, edit_session, ini_api,
               asset_loader, asset_paths, metadata, mod_folders, mod_loader, present_api, server,
               toggle_api)
from .asset_composition import plan_missing_asset_parts
from .asset_loader.models import build_asset_fill_payload


class ModViewerAPI:
    def __init__(self):
        # Underscore-private on purpose: pywebview reflects over the js_api
        # object's public attributes to expose them to JavaScript, and the
        # native window is a deeply self-referential COM object — exposing it
        # sends that reflection into infinite recursion.
        self._window = None
        self._authorized_folders = set()
        self._picker_authorized_folders = set()
        self._authorized_roots = set()
        self._authorized_asset_folders = set()
        self._authorized_asset_roots = set()
        self._active_mesh_keys = {}
        self._dds_classification_caches = {}
        self._asset_fill_sessions = {}
        self._asset_fill_plan_cache = {}
        try:
            self._authorized_roots = mod_folders.registered_paths(
                mod_folders.load_registry())
        except mod_folders.ModFolderError:
            # The UI can report the readable config error. Do not let malformed
            # optional configuration prevent the viewer from starting.
            pass
        try:
            self._authorized_asset_roots = asset_folders.registered_paths(
                asset_folders.load_registry())
        except asset_folders.AssetFolderError:
            pass

    def _folder(self, folder_path):
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
            raise PermissionError("This folder was not selected through the native folder picker.")
        raise PermissionError("This folder was not selected through the native folder picker.")

    def _asset_folder(self, folder_path):
        requested = asset_folders.normalize_path(folder_path)
        if requested in self._authorized_asset_folders:
            return requested
        if any(asset_folders.is_within(requested, root)
               for root in self._authorized_asset_roots):
            self._authorized_asset_folders.add(requested)
            return requested
        raise PermissionError(
            "This folder is not inside a registered Asset Folder.")

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

    def _clear_asset_fill(self, folder_path=None):
        """Release session-only Asset-fill resources before a model change."""
        keys = list(self._asset_fill_sessions)
        if folder_path is not None:
            requested = os.path.normcase(os.path.abspath(folder_path))
            keys = [key for key in keys if key == requested]
        for key in keys:
            state = self._asset_fill_sessions.pop(key, None) or {}
            server.release_texture_publication(state.get("publication"))
            server.release_geometry(state.get("geometry_url"))

    def _asset_fill_plan(self, folder_path, context, overrides):
        revision = edit_session.current_revision(folder_path)
        entries = tuple(sorted(
            (item.get("type"), item.get("path"),
             bool(item.get("enabled", True)))
            for item in context.asset_folders
            if isinstance(item, dict)))
        index_versions = []
        for asset_type, root, _enabled in entries:
            try:
                stat = os.stat(asset_index.index_path(asset_type, root))
                stamp = (stat.st_mtime_ns, stat.st_size)
            except OSError:
                stamp = None
            index_versions.append((asset_type, root, stamp))
        key = (os.path.normcase(os.path.abspath(folder_path)), revision,
               entries, tuple(index_versions))
        cached = self._asset_fill_plan_cache.get(key)
        if cached is not None:
            return cached
        plan = plan_missing_asset_parts(context, overrides)
        self._asset_fill_plan_cache[key] = plan
        return plan

    def _invalidate_asset_fill_plan_cache(self):
        """Drop plans whose Asset index or enabled-root state may have changed."""
        self._asset_fill_plan_cache.clear()

    def select_folder(self):
        """Open a native folder-picker dialog. Returns None if cancelled."""
        result = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        if not result:
            return None
        folder = mod_folders.normalize_path(result[0])
        self._authorized_folders.add(folder)
        self._picker_authorized_folders.add(folder)
        return folder

    def select_asset_folder(self):
        """Pick an Asset Folder without granting mod-folder access."""
        result = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        if not result:
            return None
        folder = asset_folders.normalize_path(result[0])
        self._picker_authorized_folders.add(folder)
        return folder

    # -- persistent Mod Folders registry -----------------------------------

    @staticmethod
    def _folder_entries(entries):
        return [{**entry, "exists": os.path.isdir(entry["path"])}
                for entry in entries]

    def _refresh_authorized_roots(self, entries):
        self._authorized_roots = mod_folders.registered_paths(entries)

    def _refresh_authorized_asset_roots(self, entries):
        roots = asset_folders.registered_paths(entries)
        self._authorized_asset_roots = roots
        self._authorized_asset_folders = {
            path for path in self._authorized_asset_folders
            if any(asset_folders.is_within(path, root) for root in roots)
        }

    def get_mod_folders(self):
        try:
            entries = mod_folders.load_registry()
        except mod_folders.ModFolderError as error:
            return {"folders": [], "error": str(error)}
        self._refresh_authorized_roots(entries)
        return {"folders": self._folder_entries(entries)}

    def get_panel_opacity(self):
        try:
            return {"value": mod_folders.load_panel_opacity()}
        except mod_folders.ModFolderError as error:
            return {"error": str(error), "value": mod_folders.DEFAULT_PANEL_OPACITY}

    def set_panel_opacity(self, value):
        try:
            return {"value": mod_folders.save_panel_opacity(value)}
        except mod_folders.ModFolderError as error:
            return {"error": str(error)}

    def add_mod_folder(self, name, folder_path):
        folder_path = mod_folders.normalize_path(folder_path)
        if folder_path not in self._picker_authorized_folders:
            return {"error": "Choose the Mod Folder through the native folder picker first."}
        try:
            entries = mod_folders.add_folder(name, folder_path)
        except mod_folders.ModFolderError as error:
            return {"error": str(error)}
        self._refresh_authorized_roots(entries)
        return {"folders": self._folder_entries(entries)}

    def edit_mod_folder(self, original_path, name, folder_path):
        original_path = mod_folders.normalize_path(original_path)
        folder_path = mod_folders.normalize_path(folder_path)
        try:
            entries = mod_folders.load_registry()
            if not any(item["path"] == original_path for item in entries):
                raise mod_folders.ModFolderError("That Mod Folder is not registered.")
            if (folder_path != original_path and
                    folder_path not in self._picker_authorized_folders):
                raise mod_folders.ModFolderError(
                    "Choose the new Mod Folder through the native folder picker first.")
            entries = mod_folders.edit_folder(original_path, name, folder_path)
        except mod_folders.ModFolderError as error:
            return {"error": str(error)}
        self._refresh_authorized_roots(entries)
        return {"folders": self._folder_entries(entries)}

    def delete_mod_folder(self, folder_path):
        try:
            entries = mod_folders.delete_folder(folder_path)
        except mod_folders.ModFolderError as error:
            return {"error": str(error)}
        self._refresh_authorized_roots(entries)
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

    # -- persistent Asset Folders registry ----------------------------------

    def get_asset_folders(self):
        try:
            entries = asset_folders.load_registry()
        except asset_folders.AssetFolderError as error:
            return {"folders": [], "error": str(error)}
        self._refresh_authorized_asset_roots(entries)
        return {"folders": self._asset_folder_entries(entries)}

    @staticmethod
    def _asset_folder_entries(entries):
        result = []
        for entry in entries:
            item = {**entry, "exists": os.path.isdir(entry["path"])}
            item["index"] = asset_index.index_status(
                entry["type"], entry["path"])
            result.append(item)
        return result

    def add_asset_folder(self, asset_type, folder_path):
        folder_path = asset_folders.normalize_path(folder_path)
        if folder_path not in self._picker_authorized_folders:
            return {"error": "Choose the Asset Folder through the native folder picker first."}
        try:
            entries = asset_catalog.add(asset_type, folder_path)
        except asset_catalog.AssetCatalogError as error:
            return {"error": str(error)}
        self._refresh_authorized_asset_roots(entries)
        self._invalidate_asset_fill_plan_cache()
        self._authorized_asset_folders.add(folder_path)
        return {"folders": self._asset_folder_entries(entries)}

    def edit_asset_folder(self, original_path, asset_type, folder_path):
        original_path = asset_folders.normalize_path(original_path)
        folder_path = asset_folders.normalize_path(folder_path)
        if (folder_path != original_path and
                folder_path not in self._picker_authorized_folders):
            return {"error": "Choose the new Asset Folder through the native folder picker first."}
        try:
            entries = asset_catalog.edit(original_path, asset_type, folder_path)
        except asset_catalog.AssetCatalogError as error:
            return {"error": str(error)}
        self._refresh_authorized_asset_roots(entries)
        self._invalidate_asset_fill_plan_cache()
        self._authorized_asset_folders.add(folder_path)
        return {"folders": self._asset_folder_entries(entries)}

    def delete_asset_folder(self, folder_path):
        folder_path = asset_folders.normalize_path(folder_path)
        try:
            entries = asset_catalog.delete(folder_path)
        except asset_catalog.AssetCatalogError as error:
            return {"error": str(error)}
        self._refresh_authorized_asset_roots(entries)
        self._invalidate_asset_fill_plan_cache()
        return {"folders": self._asset_folder_entries(entries)}

    def set_asset_folder_enabled(self, folder_path, enabled):
        folder_path = asset_folders.normalize_path(folder_path)
        try:
            entries = asset_catalog.set_enabled(folder_path, enabled)
        except asset_catalog.AssetCatalogError as error:
            return {"error": str(error)}
        self._refresh_authorized_asset_roots(entries)
        self._invalidate_asset_fill_plan_cache()
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
        self._refresh_authorized_asset_roots(entries)
        self._invalidate_asset_fill_plan_cache()
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

    def _asset_selection(self, folder_path):
        requested = self._asset_folder(folder_path)
        entries = asset_folders.load_registry()
        roots = [entry for entry in entries
                 if asset_folders.is_within(requested, entry["path"])]
        if not roots:
            raise PermissionError("This folder is not inside a registered Asset Folder.")
        entry = max(roots, key=lambda item: len(item["path"]))
        relative = asset_paths.relative_asset_path(entry["path"], requested)
        if not relative or relative == ".":
            raise asset_loader.AssetLoadError(
                "Select an indexed Asset folder, not the Asset Folder root.")
        asset_dir = asset_paths.safe_asset_dir(entry["path"], relative)
        if not asset_dir:
            raise asset_loader.AssetLoadError(
                "The selected Asset folder is missing or escapes its registered root.")
        index = asset_index.load_index(entry["type"], entry["path"])
        if index is None:
            raise asset_loader.AssetLoadError(
                "Asset index is missing. Rebuild the registered Asset Folder first.")
        record = asset_index.find_asset_by_path(index, relative)
        if record is None:
            raise asset_loader.AssetLoadError(
                "The selected folder is a category or is not present in the Asset index.")
        return requested, entry, index, record

    def load_asset(self, folder_path):
        """Load one exact indexed Asset without creating or editing an INI."""
        self._clear_asset_fill()
        try:
            selected, entry, _index, record = self._asset_selection(folder_path)
            geometry = GeometryBlob()
            publication = server.begin_texture_publication(selected)
            publication.set_game_profile({
                "GIMI": "genshin", "ZZMI": "zzz", "WWMI": "wuwa",
            }[entry["type"]])
            try:
                loaded = asset_loader.load_asset(
                    entry["type"], entry["path"], record, geometry=geometry,
                    texture_source=publication.register)
                payload = loaded.payload
                server.publish_payload_geometry(payload, geometry)
                publication.commit()
                return payload
            except Exception:
                publication.discard()
                raise
        except (PermissionError, asset_folders.AssetFolderError,
                asset_index.AssetIndexError, asset_loader.AssetLoadError) as error:
            return {"error": str(error)}
        except Exception:
            traceback.print_exc()
            return {"error": "Could not load Asset. See the application log for details."}

    def pick_asset_texture_file(self, folder_path, texture_role=None):
        """Register a manually chosen Asset texture for the current preview only."""
        try:
            selected, entry, _index, _record = self._asset_selection(folder_path)
            if texture_role not in (None, "normal_map", "light_map", "material_map"):
                return {"error": "Unknown texture role."}
            result = self._window.create_file_dialog(
                webview.FileDialog.OPEN, directory=selected,
                file_types=("Textures (*.dds;*.png;*.jpg;*.jpeg;*.tga)",))
            if not result:
                return None
            chosen = asset_folders.normalize_path(result[0])
            relative = asset_paths.relative_asset_path(entry["path"], chosen)
            safe = asset_paths.safe_asset_path(entry["path"], relative)
            if not safe:
                return {"error": "Choose a texture inside the registered Asset root."}
            publication = server.active_texture_publication(selected)
            if publication is None:
                return {"error": "The Asset preview is no longer active."}
            role = texture_role or "diffuse"
            from .asset_loader.models import make_texture
            texture = make_texture(
                entry["path"], safe, role,
                texture_source=publication.register, source="session")
            if not texture:
                return {"error": "The selected texture could not be published."}
            return {"tex_key": texture.key, "uri": texture.uri,
                    "file": relative, "role": role}
        except (PermissionError, asset_folders.AssetFolderError,
                asset_index.AssetIndexError, asset_loader.AssetLoadError) as error:
            return {"error": str(error)}

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
        publication = server.active_texture_publication(folder_path)
        profile = texture_profile_for(
            publication.game_profile if publication else None)
        transport_role = texture_role
        if (texture_role == "normal_map"
                and profile.normal_transport_role == "normal_data"):
            transport_role = profile.normal_transport_role
        encoded = encode_texture_file(
            folder_path, result[0], transport_role,
            texture_source=texture_source, texture_profile=profile)
        return encoded

    def _authoritative_context(self, folder_path):
        """Load active INI documents while preserving the current session."""
        folder_path = self._folder(folder_path)
        ini_paths = (edit_session.document_paths(folder_path)
                     or discover_ini_paths(folder_path))
        edit_session.load_documents(folder_path, ini_paths)
        overrides = edit_session.overrides_for(folder_path)
        pending_new_sections = edit_session.new_sections_for(folder_path)
        context = mod_loader.ModLoadContext(
            folder_path, ini_paths, edit_session.documents_for(folder_path),
            metadata.load(folder_path))
        cache_key = os.path.normcase(os.path.abspath(folder_path))
        context.dds_classification_cache = \
            self._dds_classification_caches.setdefault(cache_key, {})
        try:
            context.asset_folders = asset_folders.load_registry()
        except asset_folders.AssetFolderError:
            # Optional asset configuration must not make an otherwise valid
            # mod unloadable; the asset UI reports the config error directly.
            context.asset_folders = []
        return folder_path, overrides, pending_new_sections, context

    @staticmethod
    def _semantic_read_error():
        traceback.print_exc()
        return {"error": "Unexpected backend error. See the application log for details."}

    def load_mod(self, folder_path):
        self._clear_asset_fill()
        folder_path, overrides, pending_new_sections, context = \
            self._authoritative_context(folder_path)
        ini_paths = context.ini_paths
        geometry = GeometryBlob()
        publication = server.begin_texture_publication(folder_path)
        try:
            result = mod_loader.load_mod(
                context=context, overrides=overrides,
                pending_new_sections=pending_new_sections, geometry=geometry,
                texture_source=publication.register)
            if not isinstance(result, dict) or result.get("error"):
                publication.discard()
                self._active_mesh_keys.pop(folder_path, None)
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
            self._active_mesh_keys[folder_path] = set(result.get("meshes", {}))
            return result
        except Exception:
            publication.discard()
            self._active_mesh_keys.pop(folder_path, None)
            raise

    def load_missing_asset_parts(self, folder_path):
        """Append original Asset parts not covered by the current mod INIs."""
        try:
            folder_path, overrides, _pending, context = \
                self._authoritative_context(folder_path)
            key = os.path.normcase(os.path.abspath(folder_path))
            existing = self._asset_fill_sessions.get(key)
            if existing is not None:
                return {**existing["summary"], "status": "loaded",
                        "already_loaded": True}
            plan = self._asset_fill_plan(folder_path, context, overrides)
            summary = plan.to_dict()
            if plan.status != "ready":
                return summary

            geometry = GeometryBlob()
            publication = server.begin_texture_publication(folder_path)
            publication.set_game_profile({
                "GIMI": "genshin", "ZZMI": "zzz", "WWMI": "wuwa",
            }[plan.asset_type])
            try:
                loaded = asset_loader.load_asset_parts(
                    plan.asset_type, plan.root, plan.asset,
                    texture_source=publication.register,
                    part_filter=set(plan.missing_parts))
                payload = build_asset_fill_payload(
                    plan.asset_type, plan.root, plan.asset, loaded.parts,
                    geometry=geometry, warnings=loaded.warnings)
                server.publish_payload_geometry(
                    payload, geometry, replace=False)
                publication.commit(replace=False)
            except Exception:
                publication.discard()
                raise
            summary["payload"] = payload
            self._asset_fill_sessions[key] = {
                "publication": publication,
                "geometry_url": payload.get("geometry", {}).get("url")
                if payload.get("geometry") else None,
                "summary": summary,
            }
            return {**summary, "status": "loaded"}
        except (PermissionError, asset_folders.AssetFolderError,
                asset_index.AssetIndexError, asset_loader.AssetLoadError) as error:
            return {"status": "error", "error": str(error)}
        except Exception:
            traceback.print_exc()
            return {"status": "error",
                    "error": "Could not load missing Asset parts. See the application log for details."}

    def remove_missing_asset_parts(self, folder_path):
        """Remove the current session-only original Asset fill."""
        try:
            folder_path = self._folder(folder_path)
            key = os.path.normcase(os.path.abspath(folder_path))
            state = self._asset_fill_sessions.pop(key, None)
            if state is None:
                return {"status": "removed", "removed": False}
            server.release_texture_publication(state.get("publication"))
            server.release_geometry(state.get("geometry_url"))
            return {"status": "removed", "removed": True}
        except (PermissionError, mod_folders.ModFolderError) as error:
            return {"status": "error", "error": str(error)}

    def get_present_state(self, folder_path):
        """Return staged PRESENT state without loading geometry or textures."""
        try:
            folder_path, overrides, _pending, context = \
                self._authoritative_context(folder_path)
            present = mod_loader.load_present_state(context, overrides)
            metadata.hydrate_present(folder_path, present, context.metadata)
            return {"present": present}
        except Exception:
            return self._semantic_read_error()

    def get_control_state(self, folder_path):
        """Return staged control semantics without rebuilding the model."""
        try:
            folder_path, overrides, pending, context = \
                self._authoritative_context(folder_path)
            result = mod_loader.load_control_state(
                context, overrides, pending,
                active_mesh_keys=self._active_mesh_keys.get(folder_path))
            metadata.hydrate_present(
                folder_path, result["controls"]["present"], context.metadata)
            return result
        except Exception:
            return self._semantic_read_error()

    def get_mesh_semantics(self, folder_path):
        """Return staged draw visibility semantics without rebuilding meshes."""
        try:
            _folder_path, overrides, _pending, context = \
                self._authoritative_context(folder_path)
            return mod_loader.load_mesh_semantics(
                context, overrides, self._active_mesh_keys.get(_folder_path))
        except Exception:
            return self._semantic_read_error()

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

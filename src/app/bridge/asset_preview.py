"""Indexed Asset preview and session-only fill orchestration."""

import os
import traceback
import uuid

import webview

from core.geometry.mesh_builder import GeometryBlob

from app.assets import folders as asset_folders
from app.assets import index as asset_index
from app.assets import loader as asset_loader
from app.assets import paths as asset_paths
from app.assets.composition import plan_missing_asset_parts
from app.assets.loader.models import build_asset_fill_payload
from app.runtime import server
from app.session import edit as edit_session


class AssetPreview:
    def __init__(self, access):
        self._access = access
        self._asset_fill_sessions = {}
        self._asset_fill_plan_cache = {}

    def clear_fill(self, folder_path=None):
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

    def invalidate_plan_cache(self):
        """Drop plans whose Asset index or enabled-root state may have changed."""
        self._asset_fill_plan_cache.clear()

    def _asset_selection(self, folder_path):
        requested = self._access.asset_folder(folder_path)
        entries = asset_folders.load_registry()
        roots = [entry for entry in entries
                 if asset_folders.is_within(requested, entry["path"])]
        if not roots:
            raise PermissionError(
                "This folder is not inside a registered Asset Folder.")
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
        self.clear_fill()
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

    def pick_asset_texture_file(self, window, folder_path, texture_role=None):
        """Register a chosen Asset texture using the facade-owned window."""
        try:
            selected, entry, _index, _record = self._asset_selection(folder_path)
            if texture_role not in (
                    None, "normal_map", "light_map", "material_map", "emission_map"):
                return {"error": "Unknown texture role."}
            result = window.create_file_dialog(
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
            from app.assets.loader.models import make_texture
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

    def load_missing_asset_parts(self, folder_path, context, overrides):
        """Append original Asset parts not covered by the current mod INIs."""
        try:
            key = os.path.normcase(os.path.abspath(folder_path))
            existing = self._asset_fill_sessions.get(key)
            if existing is not None:
                return {**existing["summary"], "status": "loaded",
                        "already_loaded": True,
                        "fill_id": existing.get("fill_id")}
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
            fill_id = uuid.uuid4().hex
            self._asset_fill_sessions[key] = {
                "fill_id": fill_id,
                "publication": publication,
                "geometry_url": payload.get("geometry", {}).get("url")
                if payload.get("geometry") else None,
                "summary": summary,
            }
            return {**summary, "status": "loaded", "fill_id": fill_id}
        except (PermissionError, asset_folders.AssetFolderError,
                asset_index.AssetIndexError, asset_loader.AssetLoadError) as error:
            return {"status": "error", "error": str(error)}
        except Exception:
            traceback.print_exc()
            return {"status": "error",
                    "error": "Could not load missing Asset parts. See the application log for details."}

    def remove_missing_asset_parts(self, folder_path, fill_id=None):
        """Remove the current session-only original Asset fill."""
        try:
            folder_path = self._access.mod_folder(folder_path)
            key = os.path.normcase(os.path.abspath(folder_path))
            state = self._asset_fill_sessions.get(key)
            if state is None:
                return {"status": "removed", "removed": False}
            if fill_id is not None and state.get("fill_id") != fill_id:
                return {"status": "removed", "removed": False, "stale": True}
            self._asset_fill_sessions.pop(key, None)
            server.release_texture_publication(state.get("publication"))
            server.release_geometry(state.get("geometry_url"))
            return {"status": "removed", "removed": True}
        except (PermissionError, asset_folders.AssetFolderError) as error:
            return {"status": "error", "error": str(error)}

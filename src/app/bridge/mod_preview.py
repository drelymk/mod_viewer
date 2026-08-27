"""Mod preview orchestration behind the JavaScript bridge facade."""

import os
import traceback

import webview

from core.geometry.buffers import BufferStore
from core.geometry.conventions import geometry_convention_for
from core.geometry.mesh_builder import GeometryBlob
from core.geometry.semantics import deduplicate_draws
from core.geometry.skinning import (
    SkinningPreviewError, build_skinning_preview,
)
from core.resource_paths import safe_resource_path
from core.textures import encode_texture_file
from core.mod_discovery import discover_ini_paths
from core.ini.health import analyze_mod
from app.mods.analysis import analyze_mod_inis
from core.textures.profiles import texture_profile_for

from app.assets import folders as asset_folders
from app.mods import loader as mod_loader
from app.mods import metadata
from app.runtime import server
from app.session import edit as edit_session


class ModPreview:
    def __init__(self, access):
        self._access = access
        self._active_mesh_keys = {}
        self._dds_classification_caches = {}

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

    def authoritative_context(self, folder_path):
        """Load active INI documents while preserving the current session."""
        folder_path = self._access.mod_folder(folder_path)
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
        folder_path, overrides, pending_new_sections, context = \
            self.authoritative_context(folder_path)
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

    def get_present_state(self, folder_path):
        """Return staged PRESENT state without loading geometry or textures."""
        try:
            folder_path, overrides, _pending, context = \
                self.authoritative_context(folder_path)
            present = mod_loader.load_present_state(context, overrides)
            metadata.hydrate_present(folder_path, present, context.metadata)
            return {"present": present}
        except Exception:
            return self._semantic_read_error()

    def get_control_state(self, folder_path):
        """Return staged control semantics without rebuilding the model."""
        try:
            folder_path, overrides, pending, context = \
                self.authoritative_context(folder_path)
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
            folder_path, overrides, _pending, context = \
                self.authoritative_context(folder_path)
            return mod_loader.load_mesh_semantics(
                context, overrides, self._active_mesh_keys.get(folder_path))
        except Exception:
            return self._semantic_read_error()

    def get_skinning_preview(self, folder_path, mesh_key):
        """Decode one selected mod draw's weights on explicit user request."""
        try:
            folder_path, overrides, _pending, context = \
                self.authoritative_context(folder_path)
            if not isinstance(mesh_key, str) or not mesh_key:
                return {"status": "error", "code": "mesh_not_found",
                        "error": "The selected mesh could not be found."}
            active_mesh_keys = self._active_mesh_keys.get(folder_path)
            if (active_mesh_keys is not None
                    and mesh_key not in active_mesh_keys):
                return {"status": "error", "code": "mesh_not_found",
                        "error": "The selected mesh could not be found."}

            parsed = analyze_mod_inis(
                context.ini_paths, context.mod_dir, overrides, context.docs)
            selected = None
            for group in parsed.groups:
                for draw in deduplicate_draws(group):
                    if draw.label == mesh_key:
                        selected = (draw, group)
                        break
                if selected:
                    break
            if selected is None:
                return {"status": "error", "code": "mesh_not_found",
                        "error": "The selected mesh could not be found."}

            draw, group = selected
            paths = [
                safe_resource_path(context.mod_dir, group["position_file"]),
                safe_resource_path(context.mod_dir, group["texcoord_file"]),
                safe_resource_path(context.mod_dir, group["ib_file"]),
            ]
            if not all(path and os.path.exists(path) for path in paths):
                return {"status": "error", "code": "geometry_not_available",
                        "error": "The rendered draw geometry could not be prepared."}
            buffers = BufferStore()
            default_streams = buffers.vertex_streams(
                paths[0], group.get("position_stride"), paths[1],
                group.get("texcoord_stride"))
            buffers.raw(paths[2])
            decoded = build_skinning_preview(
                draw, group, context.mod_dir, buffers=buffers,
                default_streams=default_streams,
                default_index_size=group.get("index_size", 4),
                geometry_convention=geometry_convention_for(parsed.game.game))
            blob = decoded.indices + decoded.weights
            url = server.publish_geometry(blob, replace=False)
            index_length = len(decoded.indices)
            return {
                "status": "ok",
                "format_version": 1,
                "vertex_count": decoded.vertex_count,
                "influence_count": decoded.influence_count,
                "bone_ids": list(decoded.bone_ids),
                "encoding": draw.skinning_source.encoding,
                "data": {
                    "url": url,
                    "length": len(blob),
                    "indices": {"offset": 0, "length": index_length,
                                "type": "u32"},
                    "weights": {"offset": index_length,
                                "length": len(decoded.weights),
                                "type": "f32"},
                },
                "diagnostics": dict(decoded.diagnostics),
            }
        except SkinningPreviewError as error:
            return {"status": "error", "code": error.code,
                    "error": error.message}
        except Exception:
            return self._semantic_read_error()

    def get_diagnostics(self, folder_path):
        """Return the read-only health scan for the current edit revision."""
        folder_path = self._access.mod_folder(folder_path)
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
        return metadata.save_mesh_names(self._access.mod_folder(folder_path), names)

    def save_mesh_textures(self, folder_path, textures):
        folder_path = self._access.mod_folder(folder_path)
        result = metadata.save_textures(folder_path, textures)
        edit_session.invalidate_diagnostics(folder_path)
        return result

    def save_component_material_kind(self, folder_path, source, component,
                                     material_kind):
        folder_path = self._access.mod_folder(folder_path)
        result = metadata.save_component_material_kind(
            folder_path, source, component, material_kind)
        edit_session.invalidate_diagnostics(folder_path)
        return result

    def pick_texture_file(self, window, folder_path, texture_role=None):
        """Pick a mod texture using the facade-owned native window."""
        folder_path = self._access.mod_folder(folder_path)
        if texture_role not in (None, "normal_map", "light_map", "material_map"):
            return {"error": "Unknown texture role."}
        result = window.create_file_dialog(
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

"""Mod preview orchestration behind the JavaScript bridge facade."""

import os
import time
import traceback

import webview

from core.geometry.buffers import BufferStore
from core.geometry.conventions import geometry_convention_for
from core.geometry.mesh_builder import GeometryBlob
from core.geometry.skinning import (
    SkinningPreviewError, build_skinning_preview, decode_skinning,
    skinning_source_descriptor,
)
from core.resource_paths import safe_resource_path
from core.textures import encode_texture_file
from core.mod_discovery import discover_ini_paths
from core.ini.health import analyze_mod
from app.mods.analysis import resolved_draws
from app.mods.texture_save.service import save_texture_color
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
        self._skinning_manifests = {}
        self._current_model_folder = None
        self._last_skinning_diagnostics = {}
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

    def authoritative_context(self, folder_path, disabled_ini=False):
        """Load selected INI documents while preserving the current session."""
        folder_path = self._access.mod_folder(folder_path)
        ini_paths = (edit_session.document_paths(folder_path)
                     or discover_ini_paths(folder_path, disabled=disabled_ini))
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

    def clear_loaded_model(self):
        """Release private state for the model currently shown in the scene."""
        self._current_model_folder = None
        self._active_mesh_keys.clear()
        self._skinning_manifests.clear()
        self._last_skinning_diagnostics.clear()

    def load_mod(self, folder_path, disabled_ini=False):
        self.clear_loaded_model()
        folder_path, overrides, pending_new_sections, context = \
            self.authoritative_context(folder_path, disabled_ini=disabled_ini)
        geometry = GeometryBlob()
        publication = server.begin_texture_publication(folder_path)
        try:
            result = mod_loader.load_mod(
                context=context, overrides=overrides,
                pending_new_sections=pending_new_sections, geometry=geometry,
                texture_source=publication.register)
            if (disabled_ini and isinstance(result, dict)
                    and not context.ini_paths
                    and result.get("error") ==
                    "No active .ini files found in this folder."):
                result["error"] = "No disabled .ini files found in this folder."
            if not isinstance(result, dict) or result.get("error"):
                publication.discard()
                self._active_mesh_keys.pop(folder_path, None)
                self._skinning_manifests.pop(folder_path, None)
                self._last_skinning_diagnostics.pop(folder_path, None)
                return result

            saved_metadata = context.metadata
            result.setdefault("metadata", {})["mesh_names"] = \
                metadata.hydrate_mesh_names(result, saved_metadata)
            result["metadata"]["mesh_color_adjustments"] = \
                metadata.hydrate_mesh_color_adjustments(result, saved_metadata)
            rig_metadata = metadata.rig_pose_presets(data=saved_metadata)
            humanoid_rig = metadata.humanoid_control_rig(data=saved_metadata)
            if humanoid_rig is not None:
                rig_metadata["humanoid_control_rig"] = humanoid_rig
            result["metadata"]["rig"] = rig_metadata
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
            self._skinning_manifests[folder_path] = dict(
                getattr(context, "skinning_manifest", {}) or {})
            self._current_model_folder = folder_path
            return result
        except Exception:
            publication.discard()
            self._active_mesh_keys.pop(folder_path, None)
            self._skinning_manifests.pop(folder_path, None)
            self._last_skinning_diagnostics.pop(folder_path, None)
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

    def save_texture_color(
            self, folder_path, tex_key, targets, texture_usage,
            progress_callback=None):
        """Save all captured Color changes that target one physical DDS."""
        try:
            folder_path, overrides, _pending, context = \
                self.authoritative_context(folder_path)
            save_kwargs = {} if progress_callback is None else {
                "progress_callback": progress_callback,
            }
            result = save_texture_color(
                context, overrides, self._active_mesh_keys.get(folder_path),
                tex_key, targets, texture_usage, **save_kwargs)
            return result
        except Exception:
            return self._semantic_read_error()

    @staticmethod
    def _skinning_draws(context, overrides):
        """Resolve every rendered draw once for the model preview."""
        return resolved_draws(context, overrides)

    @staticmethod
    def _decode_skinning_draw(draw, group, mod_dir, buffers,
                              geometry_convention, timing=None):
        paths = [
            safe_resource_path(mod_dir, group["position_file"]),
            safe_resource_path(mod_dir, group["texcoord_file"]),
            safe_resource_path(mod_dir, group["ib_file"]),
        ]
        if not all(path and os.path.exists(path) for path in paths):
            raise SkinningPreviewError(
                "geometry_not_available",
                "The rendered draw geometry could not be prepared.")
        default_streams = buffers.vertex_streams(
            paths[0], group.get("position_stride"), paths[1],
            group.get("texcoord_stride"))
        buffers.raw(paths[2])
        return build_skinning_preview(
            draw, group, mod_dir, buffers=buffers,
            default_streams=default_streams,
            default_index_size=group.get("index_size", 4),
            geometry_convention=geometry_convention, timing=timing)

    @staticmethod
    def _skinning_source_descriptor(draw_or_source):
        source = getattr(draw_or_source, "skinning_source", draw_or_source)
        return skinning_source_descriptor(source)

    @staticmethod
    def _skin_entry(decoded, draw_or_source, offset):
        source = getattr(draw_or_source, "skinning_source", draw_or_source)
        indices_length = len(decoded.indices)
        blob = decoded.indices + decoded.weights
        return ({
            "status": "ok",
            "vertex_count": decoded.vertex_count,
            "influence_count": decoded.influence_count,
            "bone_ids": list(decoded.bone_ids),
            "encoding": source.encoding,
            "source": ModPreview._skinning_source_descriptor(source),
            "data": {
                "indices": {
                    "offset": offset,
                    "length": indices_length,
                    "type": "u32",
                },
                "weights": {
                    "offset": offset + indices_length,
                    "length": len(decoded.weights),
                    "type": "f32",
                },
            },
            "diagnostics": dict(decoded.diagnostics),
            "weight_stats": {
                str(bone_id): dict(stats)
                for bone_id, stats in getattr(decoded, "bone_stats", {}).items()
            },
        }, blob)

    def get_model_skinning_preview(self, folder_path):
        """Decode all active skin streams for the currently loaded model."""
        request_started = time.perf_counter()
        timing = {
            "resolve_draws_seconds": 0.0,
            "prepare_draw_vertices_seconds": 0.0,
            "decode_skinning_seconds": 0.0,
            "build_blob_seconds": 0.0,
            "resolve_draw_count": 0,
            "prepare_draw_vertices_calls": 0,
            "decoded_mesh_count": 0,
            "compact_vertex_count": 0,
            "weight_blob_bytes": 0,
        }
        try:
            folder_path, overrides, _pending, context = \
                self.authoritative_context(folder_path)
            saved_bones = metadata.weight_selected_bones(
                data=context.metadata)
            active_mesh_keys = self._active_mesh_keys.get(folder_path)
            manifest = self._skinning_manifests.get(folder_path)
            mapping_source = "loaded_model_manifest" if manifest is not None \
                else "legacy_rebuild"
            if manifest is not None:
                requested = (set(active_mesh_keys)
                             if active_mesh_keys is not None
                             else set(manifest))
                requested &= set(manifest)
                selected_items = {
                    key: manifest[key] for key in requested
                }
                parsed = None
            else:
                resolve_started = time.perf_counter()
                parsed, draws = self._skinning_draws(context, overrides)
                timing["resolve_draws_seconds"] = (
                    time.perf_counter() - resolve_started)
                timing["resolve_draw_count"] = len(draws)
                eligible_draws = {
                    key: selected for key, selected in draws.items()
                    if selected[0].skinning_source is not None
                }
                requested = (set(active_mesh_keys)
                             if active_mesh_keys is not None
                             else set(eligible_draws))
                requested &= set(eligible_draws)
                selected_items = {
                    key: eligible_draws[key] for key in requested
                }
            meshes = {}
            pieces = []
            offset = 0
            buffers = BufferStore()
            convention = (geometry_convention_for(parsed.game.game)
                          if parsed is not None else None)
            for mesh_key in sorted(selected_items):
                selected = selected_items[mesh_key]
                draw = selected[0] if manifest is None else None
                try:
                    if manifest is not None:
                        decoded = self._decode_skinning_manifest_entry(
                            selected, context.mod_dir, buffers, timing)
                        entry, blob = self._skin_entry(
                            decoded, selected.skinning_source, offset)
                    else:
                        draw, group = selected
                        decoded = self._decode_skinning_draw(
                            draw, group, context.mod_dir, buffers, convention,
                            timing=timing)
                        entry, blob = self._skin_entry(decoded, draw, offset)
                except SkinningPreviewError as error:
                    meshes[mesh_key] = {
                        "status": "error",
                        "code": error.code,
                        "error": error.message,
                    }
                    continue
                except Exception:
                    traceback.print_exc()
                    meshes[mesh_key] = {
                        "status": "error",
                        "code": "skinning_preview_failed",
                        "error": "Could not decode skin weights for this mesh.",
                    }
                    continue
                meshes[mesh_key] = entry
                pieces.append(blob)
                offset += len(blob)
                timing["decoded_mesh_count"] += 1
                timing["compact_vertex_count"] += decoded.vertex_count

            if not pieces:
                return {
                    "status": "error",
                    "format_version": 1,
                    "saved_bones": saved_bones,
                    "meshes": meshes,
                    "error": "No active mesh has usable skin weights.",
                }
            blob_started = time.perf_counter()
            blob = b"".join(pieces)
            url = server.publish_geometry(blob, replace=False)
            timing["build_blob_seconds"] = time.perf_counter() - blob_started
            timing["weight_blob_bytes"] = len(blob)
            return {
                "status": "ok" if all(
                    entry.get("status") == "ok" for entry in meshes.values())
                    else "partial",
                "format_version": 1,
                "saved_bones": saved_bones,
                "data": {"url": url, "length": len(blob)},
                "meshes": meshes,
            }
        except Exception:
            return self._semantic_read_error()
        finally:
            timing["total_seconds"] = time.perf_counter() - request_started
            timing["mapping_source"] = mapping_source if "mapping_source" in locals() \
                else "unavailable"
            self._last_skinning_diagnostics[folder_path] = timing

    @staticmethod
    def _decode_skinning_manifest_entry(entry, mod_dir, buffers, timing):
        source = entry.skinning_source
        source_path = safe_resource_path(mod_dir, source.file)
        if not source_path or not os.path.exists(source_path):
            raise SkinningPreviewError(
                "skinning_not_available",
                "The skin-weight buffer could not be found.")
        remap_path = None
        if source.vertex_vg_file:
            remap_path = safe_resource_path(mod_dir, source.vertex_vg_file)
            if not remap_path or not os.path.exists(remap_path):
                raise SkinningPreviewError(
                    "skinning_remap_unavailable",
                    "The WWMI VertexVG remap buffer could not be found.")
        decode_started = time.perf_counter()
        decoded = decode_skinning(
            source, buffers.raw(source_path), entry.used_vertices,
            buffers.raw(remap_path) if remap_path else None)
        timing["decode_skinning_seconds"] += (
            time.perf_counter() - decode_started)
        if decoded.vertex_count != entry.vertex_count:
            raise SkinningPreviewError(
                "skinning_preview_failed",
                "The retained skinning mapping is inconsistent.")
        if decoded.diagnostics["truncated_vertices"]:
            raise SkinningPreviewError(
                "skinning_buffer_truncated",
                "The skin-weight buffer is truncated.")
        if decoded.diagnostics["vertex_vg_truncated_vertices"]:
            raise SkinningPreviewError(
                "skinning_remap_truncated",
                "The WWMI VertexVG remap buffer is truncated.")
        return decoded

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

    def save_mesh_color_adjustment(self, folder_path, mesh_key, adjustment):
        folder_path = self._access.mod_folder(folder_path)
        return metadata.save_mesh_color_adjustment(
            folder_path, mesh_key, adjustment)

    def save_weight_selection(self, folder_path, bones):
        folder_path = self._access.mod_folder(folder_path)
        return metadata.save_weight_selected_bones(folder_path, bones)

    def save_rig_pose_preset(self, folder_path, preset):
        folder_path = self._access.mod_folder(folder_path)
        return metadata.save_rig_pose_preset(folder_path, preset)

    def rename_rig_pose_preset(self, folder_path, preset_id, name):
        folder_path = self._access.mod_folder(folder_path)
        return metadata.rename_rig_pose_preset(folder_path, preset_id, name)

    def delete_rig_pose_preset(self, folder_path, preset_id):
        folder_path = self._access.mod_folder(folder_path)
        return metadata.delete_rig_pose_preset(folder_path, preset_id)

    def save_humanoid_control_rig(self, folder_path, control_rig):
        folder_path = self._access.mod_folder(folder_path)
        return metadata.save_humanoid_control_rig(folder_path, control_rig)

    def clear_humanoid_control_rig(self, folder_path):
        folder_path = self._access.mod_folder(folder_path)
        return metadata.clear_humanoid_control_rig(folder_path)

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

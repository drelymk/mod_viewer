"""Public facade for semantic projection and mesh payload construction."""

import os
import time
from dataclasses import dataclass

from .buffers import (
    DEFAULT_UV_OFFSET, INDEX_SIZE, POSITION_OFFSET, POSITION_STRIDE,
    BufferStore, _res_get, read_indices, read_positions, read_texcoords,
)
from .conventions import geometry_convention_for
from .packing import _prepare_draw_vertices, pack_draw_geometry
from .semantics import (
    _deduplicate_draws, _rel_source, build_mesh_semantics,
    deduplicate_draws, validate_draw_count,
)
from .texture_bindings import (
    TextureRegistry, apply_draw_texture_bindings, build_texture_options,
    finalize_texture_candidates,
)
from .transport import GeometryBlob
from .identity import mesh_identity_for_draw
from .skinning import SkinningManifestEntry
from ..resource_paths import safe_resource_path
from .animation_frames import (
    _animation_clock_dict,
    animation_draws,
    animation_vertex_union,
    _prepare_animation_family,
)
from .compute_geometry import (
    _prepare_gimi_shared,
    _prepare_gimi_geometry,
)


@dataclass
class MeshBuildResult:
    """Named intermediate produced by the mesh-building pipeline.

    ``meshes`` contains only draw entries and ``textures`` is the shared
    texture registry. ``geometry`` records the shared binary blob
    writer used to produce offset/length references. ``skinning_manifest`` is
    private backend state and is never included in the public payload.
    """

    meshes: dict
    textures: dict
    geometry: GeometryBlob
    skinning_manifest: dict[str, SkinningManifestEntry] | None = None
    animations: dict | None = None
    diagnostics: dict | None = None


def build_mesh_result(groups, mod_dir, max_draws=0, geometry=None,
                      texture_source=None, game_profile=None, source=None,
                      animations=None, buffer_overrides=None):
    """Build mesh draw entries and a shared texture registry.

    Geometry packing and texture publication are delegated to focused stages;
    this facade owns one binary blob and final payload metadata.
    """
    from ..textures.profiles import texture_profile_for

    if geometry is None:
        geometry = GeometryBlob()
    texture_profile = texture_profile_for(game_profile)
    geometry_convention = geometry_convention_for(game_profile)
    validate_draw_count(groups)
    registry = TextureRegistry(
        mod_dir, texture_profile, texture_source, source=source)
    buffers = BufferStore(source=source, overrides=buffer_overrides)
    sparse_shape_cache = {}
    result = {}
    skinning_manifest = {}
    animation_clocks = {}
    for clock in animations or ():
        animation_id, value = _animation_clock_dict(clock)
        animation_clocks[animation_id] = value
    used_clock_ids = set()
    gimi_shared = {}
    animation_diagnostics = {
        "animation_family_count": 0,
        "animation_frame_count": 0,
        "animation_prepare_calls": 0,
        "animation_prepare_seconds": 0.0,
        "animation_pack_seconds": 0.0,
        "animation_geometry_bytes": 0,
    }

    for group in groups:
        resolve = source.resolve_resource if source is not None \
            else lambda value: safe_resource_path(mod_dir, value)
        exists = source.is_file if source is not None else os.path.exists
        pos_path = resolve(group["position_file"])
        tc_path = resolve(group["texcoord_file"])
        ib_path = resolve(group["ib_file"])
        tc_stride = group["texcoord_stride"]
        pos_stride = group.get("position_stride", POSITION_STRIDE)
        index_size = group.get("index_size", INDEX_SIZE)
        source_name = group.get("source")
        component = group.get("display_name") or group.get("name")

        if not all(path and exists(path)
                   for path in (pos_path, tc_path, ib_path)):
            continue

        default_streams = buffers.vertex_streams(
            pos_path, pos_stride, tc_path, tc_stride)
        # Preserve the original eager group-IB load and its build-scoped cache.
        buffers.raw(ib_path)
        unique = deduplicate_draws(group, max_draws=max_draws)
        group_entries = []

        for draw, family in animation_draws(unique, animation_clocks.values(), group):
            prepared = None
            if family is not None:
                prepare_started = time.perf_counter()
                vertex_union = animation_vertex_union(
                    family, mod_dir=mod_dir, buffers=buffers,
                    default_index_size=index_size,
                    geometry_convention=geometry_convention, source=source)
                prepared = _prepare_draw_vertices(
                    draw, group, mod_dir=mod_dir,
                    default_streams=default_streams,
                    default_index_size=index_size, buffers=buffers,
                    geometry_convention=geometry_convention, source=source,
                    zero_out_of_bounds=bool(family.get("position_switching")
                                            or family.get("binding_frames")),
                    vertex_union=vertex_union)
                animation_diagnostics["animation_prepare_calls"] += 1
                animation_diagnostics["animation_prepare_seconds"] += (
                    time.perf_counter() - prepare_started)
                if prepared is None:
                    continue
            packed = pack_draw_geometry(
                draw, group,
                mod_dir=mod_dir,
                default_streams=default_streams,
                default_index_size=index_size,
                buffers=buffers,
                geometry_convention=geometry_convention,
                sparse_shape_cache=sparse_shape_cache,
                source=source,
                prepared=prepared,
            )
            if packed is None:
                continue

            animation_payload = None
            if family is not None:
                animation_payload = _prepare_animation_family(
                    family, canonical_prepared=prepared,
                    canonical_packed=packed, mod_dir=mod_dir, group=group,
                    default_streams=default_streams,
                    default_index_size=index_size, buffers=buffers,
                    geometry_convention=geometry_convention, source=source,
                    geometry=geometry, diagnostics=animation_diagnostics)

            gimi_payload = None
            gimi = group.get("_compute_animation")
            if gimi is not None and animation_payload is None:
                shared = gimi_shared.get(gimi["track_id"])
                if shared is None:
                    shared = _prepare_gimi_shared(
                        gimi, mod_dir=mod_dir, buffers=buffers,
                        source=source, geometry=geometry)
                    if shared is not None:
                        gimi_shared[gimi["track_id"]] = shared
                if shared is not None:
                    gimi_payload = _prepare_gimi_geometry(
                        gimi, packed.used_vertices, mod_dir=mod_dir,
                        buffers=buffers, source=source, geometry=geometry,
                        shared=shared, sparse_shape_cache=sparse_shape_cache)

            if draw.skinning_source is not None:
                skinning_manifest[draw.label] = \
                    SkinningManifestEntry.from_vertices(
                        draw.label, draw.skinning_source,
                        packed.used_vertices)

            entry: dict = {
                "pos": geometry.add(packed.positions),
                "idx": geometry.add(packed.indices),
                "skinning_available": draw.skinning_source is not None,
                "tex_key": None,
                "normal_map_y_sign": texture_profile.normal_y_sign,
                "normal_map_enabled": texture_profile.bind_normal_map,
            }
            apply_draw_texture_bindings(entry, draw, registry=registry)
            if packed.texcoords is not None:
                entry["uv"] = geometry.add(packed.texcoords)
            if packed.normals is not None:
                entry["normal"] = geometry.add(packed.normals)
            if packed.shape_targets:
                entry["shape_targets"] = []
                for target in packed.shape_targets:
                    shape_entry = {
                        "var": target.var,
                        "pos": geometry.add(target.positions),
                    }
                    if target.mode:
                        shape_entry["mode"] = target.mode
                    if target.low_positions is not None:
                        shape_entry["low_pos"] = geometry.add(target.low_positions)
                    entry["shape_targets"].append(shape_entry)
            if animation_payload is not None:
                animation_id = animation_payload["track_id"]
                entry["animation_id"] = animation_id
                animation_geometry = {
                    "positions": animation_payload["positions_ref"],
                    "position_frame_bytes": animation_payload[
                        "position_frame_bytes"],
                    "frames": animation_payload["frame_count"],
                    "frame_start": animation_payload["frame_start"],
                    "clock_ids": animation_payload["clock_ids"],
                    "bounds": animation_payload["bounds"],
                }
                if animation_payload["normals_ref"] is not None:
                    animation_geometry["normals"] = animation_payload[
                        "normals_ref"]
                    animation_geometry["normal_frame_bytes"] = \
                        animation_payload["normal_frame_bytes"]
                for attribute, prefix in (("indices", "index"), ("texcoords", "texcoord")):
                    if animation_payload.get(f"{attribute}_ref") is not None:
                        animation_geometry[attribute] = animation_payload[f"{attribute}_ref"]
                        animation_geometry[f"{prefix}_frame_bytes"] = animation_payload[f"{prefix}_frame_bytes"]
                        animation_geometry[f"{prefix}_frames"] = animation_payload[f"{prefix}_frames"]
                entry["animation_geometry"] = animation_geometry
                used_clock_ids.update(animation_payload["clock_ids"])
                animation_diagnostics["animation_family_count"] += 1
                for reference in (animation_payload["positions_ref"],
                                  animation_payload["normals_ref"],
                                  animation_payload.get("indices_ref"),
                                  animation_payload.get("texcoords_ref")):
                    if reference is None:
                        continue
                    animation_diagnostics["animation_geometry_bytes"] += reference["length"]
            if gimi_payload is not None:
                entry["animation_id"] = gimi_payload["track_id"]
                entry["animation_geometry"] = gimi_payload
                animation_diagnostics["animation_family_count"] += 1
            if draw.conditions:
                entry["conditions"] = draw.conditions
            if draw.sources:
                entry["sources"] = [_rel_source(item, mod_dir, source=source)
                                    for item in draw.sources]
            # The literal drawindexed = count, start, base values let the UI
            # show a meaningful per-draw label instead of a bare index.
            if draw.count is not None:
                entry["drawindexed"] = [draw.count, draw.start, draw.base]
            if source_name:
                entry["source"] = source_name
            if component:
                entry["component"] = component
            entry["identity"] = mesh_identity_for_draw(draw, group).to_dict()
            binding = draw.asset_binding
            if binding is not None and hasattr(binding, "to_dict"):
                entry["asset_binding"] = binding.to_dict()
            if draw.texture_provenance:
                entry["texture_resolution"] = dict(draw.texture_provenance)
            if draw.asset_slot_evidence:
                entry["asset_slot_evidence"] = list(draw.asset_slot_evidence)
            result[draw.label] = entry
            group_entries.append(entry)

        if group_entries:
            finalize_texture_candidates(group, draws=unique)
            texture_options = build_texture_options(group, registry)
            if texture_options:
                for entry in group_entries:
                    entry["texture_options"] = texture_options

    return MeshBuildResult(
        meshes=result,
        textures=registry.sources,
        geometry=geometry,
        skinning_manifest=skinning_manifest,
        animations={key: animation_clocks[key]
                    for key in sorted(used_clock_ids)},
        diagnostics=animation_diagnostics,
    )


__all__ = [
    "MeshBuildResult", "build_mesh_result", "build_mesh_semantics",
    "GeometryBlob", "POSITION_STRIDE",
    "POSITION_OFFSET", "DEFAULT_UV_OFFSET", "INDEX_SIZE", "_res_get",
    "_deduplicate_draws", "read_positions", "read_texcoords", "read_indices",
]

"""Public facade for semantic projection and mesh payload construction."""

import base64
import os
from dataclasses import dataclass

from .buffers import (
    DEFAULT_UV_OFFSET, INDEX_SIZE, POSITION_OFFSET, POSITION_STRIDE,
    BufferStore, _res_get, read_indices, read_positions, read_texcoords,
)
from .conventions import geometry_convention_for
from .packing import pack_draw_geometry
from .semantics import (
    _deduplicate_draws, _rel_source, build_mesh_semantics,
    deduplicate_draws, validate_draw_count,
)
from .texture_bindings import (
    TextureRegistry, apply_draw_texture_bindings, build_texture_options,
)
from .transport import GeometryBlob
from ..resource_paths import safe_resource_path


@dataclass
class MeshBuildResult:
    """Named intermediate produced by the mesh-building pipeline.

    ``meshes`` contains only draw entries and ``textures`` is the shared
    texture registry. ``geometry`` records the optional caller-owned blob
    writer used to produce offset/length references.
    """

    meshes: dict
    textures: dict
    geometry: GeometryBlob | None = None


def _geometry_ref(raw, geometry):
    """Serialize bytes into the caller-owned geometry store or base64."""
    if geometry is not None:
        return geometry.add(raw)
    return base64.b64encode(raw).decode()


def build_mesh_result(groups, mod_dir, max_draws=0, geometry=None,
                      texture_source=None, game_profile=None):
    """Build mesh draw entries and a shared texture registry.

    Geometry packing and texture publication are delegated to focused stages;
    this facade keeps transport compatibility and final payload metadata.
    """
    from ..textures.profiles import texture_profile_for

    texture_profile = texture_profile_for(game_profile)
    geometry_convention = geometry_convention_for(game_profile)
    validate_draw_count(groups)
    registry = TextureRegistry(mod_dir, texture_profile, texture_source)
    buffers = BufferStore()
    sparse_shape_cache = {}
    result = {}

    for group in groups:
        pos_path = safe_resource_path(mod_dir, group["position_file"])
        tc_path = safe_resource_path(mod_dir, group["texcoord_file"])
        ib_path = safe_resource_path(mod_dir, group["ib_file"])
        tc_stride = group["texcoord_stride"]
        pos_stride = group.get("position_stride", POSITION_STRIDE)
        index_size = group.get("index_size", INDEX_SIZE)
        component = group.get("display_name") or group.get("name")
        source = group.get("source")

        if not all(path and os.path.exists(path)
                   for path in (pos_path, tc_path, ib_path)):
            continue

        default_streams = buffers.vertex_streams(
            pos_path, pos_stride, tc_path, tc_stride)
        # Preserve the original eager group-IB load and its build-scoped cache.
        buffers.raw(ib_path)
        unique = deduplicate_draws(group, max_draws=max_draws)
        texture_options = build_texture_options(group, registry)

        for draw in unique:
            packed = pack_draw_geometry(
                draw, group,
                mod_dir=mod_dir,
                default_streams=default_streams,
                default_index_size=index_size,
                buffers=buffers,
                geometry_convention=geometry_convention,
                sparse_shape_cache=sparse_shape_cache,
            )
            if packed is None:
                continue

            entry: dict = {
                "pos": _geometry_ref(packed.positions, geometry),
                "idx": _geometry_ref(packed.indices, geometry),
                "tex_key": None,
                "normal_map_y_sign": texture_profile.normal_y_sign,
                "normal_map_enabled": texture_profile.bind_normal_map,
            }
            apply_draw_texture_bindings(
                entry, draw, texture_options, registry=registry)
            if packed.texcoords is not None:
                entry["uv"] = _geometry_ref(packed.texcoords, geometry)
            if packed.normals is not None:
                entry["normal"] = _geometry_ref(packed.normals, geometry)
            if packed.shape_targets:
                entry["shape_targets"] = []
                for target in packed.shape_targets:
                    shape_entry = {
                        "var": target.var,
                        "pos": _geometry_ref(target.positions, geometry),
                    }
                    if target.mode:
                        shape_entry["mode"] = target.mode
                    if target.low_positions is not None:
                        shape_entry["low_pos"] = _geometry_ref(
                            target.low_positions, geometry)
                    entry["shape_targets"].append(shape_entry)
            if draw.conditions:
                entry["conditions"] = draw.conditions
            if draw.sources:
                entry["sources"] = [_rel_source(item, mod_dir)
                                    for item in draw.sources]
            if texture_options:
                entry["texture_options"] = texture_options
            # The literal drawindexed = count, start, base values let the UI
            # show a meaningful per-draw label instead of a bare index.
            if draw.count is not None:
                entry["drawindexed"] = [draw.count, draw.start, draw.base]
            if source:
                entry["source"] = source
            if component:
                entry["component"] = component
            binding = draw.asset_binding
            if binding is not None and hasattr(binding, "to_dict"):
                entry["asset_binding"] = binding.to_dict()
            if draw.texture_provenance:
                entry["texture_resolution"] = dict(draw.texture_provenance)
            if draw.asset_slot_evidence:
                entry["asset_slot_evidence"] = list(draw.asset_slot_evidence)
            result[draw.label] = entry

    return MeshBuildResult(
        meshes=result,
        textures=registry.sources,
        geometry=geometry,
    )


def build_mesh_payload(groups, mod_dir, max_draws=0, geometry=None,
                       texture_source=None, game_profile=None):
    """Legacy flat payload wrapper retaining the ``__textures__`` field."""
    built = build_mesh_result(
        groups, mod_dir, max_draws=max_draws, geometry=geometry,
        texture_source=texture_source, game_profile=game_profile)
    payload = dict(built.meshes)
    payload["__textures__"] = built.textures
    return payload


__all__ = [
    "MeshBuildResult", "build_mesh_result", "build_mesh_semantics",
    "build_mesh_payload", "GeometryBlob", "POSITION_STRIDE",
    "POSITION_OFFSET", "DEFAULT_UV_OFFSET", "INDEX_SIZE", "_res_get",
    "_deduplicate_draws", "read_positions", "read_texcoords", "read_indices",
]

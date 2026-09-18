"""Public facade for semantic projection and mesh payload construction."""

import base64
import os
from copy import copy
from dataclasses import dataclass, replace

from .buffers import (
    DEFAULT_UV_OFFSET, INDEX_SIZE, POSITION_OFFSET, POSITION_STRIDE,
    BufferStore, _res_get, read_indices, read_positions, read_texcoords,
)
from .conventions import geometry_convention_for
from .packing import (
    PackedAnimationFrame, _prepare_draw_vertices,
    pack_animation_frame_attributes, pack_draw_geometry,
)
from .draw_call import _freeze
from ..ini.animations import frame_condition
from .semantics import (
    _deduplicate_draws, _rel_source, build_mesh_semantics,
    deduplicate_draws, validate_draw_count,
)
from .texture_bindings import (
    TextureRegistry, apply_draw_texture_bindings, build_texture_options,
)
from .transport import GeometryBlob
from .identity import mesh_identity_for_draw
from .skinning import SkinningManifestEntry
from ..resource_paths import safe_resource_path


@dataclass
class MeshBuildResult:
    """Named intermediate produced by the mesh-building pipeline.

    ``meshes`` contains only draw entries and ``textures`` is the shared
    texture registry. ``geometry`` records the optional caller-owned blob
    writer used to produce offset/length references. ``skinning_manifest`` is
    private backend state and is never included in the public payload.
    """

    meshes: dict
    textures: dict
    geometry: GeometryBlob | None = None
    skinning_manifest: dict[str, SkinningManifestEntry] | None = None
    animations: dict | None = None


_ANIMATION_STATIC_FIELDS = (
    "conditions", "ib_file", "index_size", "texcoord_file",
    "texcoord_stride", "texture_default_file", "texture_variants",
    "texture_assignments", "normal_map_default_file", "normal_map_variants",
    "light_map_default_file", "light_map_variants",
    "material_map_default_file", "material_map_variants",
    "emission_map_default_file", "emission_map_variants",
    "asset_texture_defaults",
)


def _animation_clock_dict(clock):
    if hasattr(clock, "to_dict"):
        value = clock.to_dict()
        animation_id = clock.animation_id
    else:
        value = dict(clock)
        animation_id = value.get("id") or (
            f"{value.get('source_section', '')}::"
            f"{value.get('frame_var', '')}::"
            f"{value.get('frame_start', 0)}-{value.get('frame_end', 0)}::"
            f"{value.get('speed_var') or value.get('speed', 1)}")
    value["id"] = animation_id
    return animation_id, value


def _condition_groups_overlap(left, right):
    """Return whether two conservative DNF condition sets can co-exist."""
    left = left or [[]]
    right = right or [[]]
    for left_group in left:
        for right_group in right:
            positive = {}
            negative = {}
            valid = True
            for clause in [*left_group, *right_group]:
                var = str(clause.get("var", "")).casefold()
                value = str(clause.get("value", ""))
                if clause.get("negate"):
                    if positive.get(var) == value:
                        valid = False
                        break
                    negative.setdefault(var, set()).add(value)
                else:
                    if (var in positive and positive[var] != value
                            or value in negative.get(var, ())):
                        valid = False
                        break
                    positive[var] = value
            if valid:
                return True
    return False


def _clock_candidates(var, conditions, by_var):
    return [
        item for item in by_var.get(str(var).casefold(), ())
        if _condition_groups_overlap(conditions, item[1].get("conditions"))
    ]


def _frame_source_draw(draw, binding):
    """Clone a static draw with one conditional position stream selected."""
    position_file = binding.get("file")
    if not position_file:
        return None
    result = copy(draw)
    result.position_file = position_file
    result.position_stride = (binding.get("stride")
                              or draw.position_stride)
    normal = draw.normal_source
    if normal is not None:
        same_file = (str(normal.file).replace("/", "\\").casefold()
                     == str(draw.position_file).replace("/", "\\").casefold())
        if same_file:
            result.normal_source = replace(
                normal, file=position_file, stride=result.position_stride)
    return result


def _animation_families(draws, clocks, group=None):
    """Group direct frame draws and conditional vertex-buffer families."""
    by_var = {}
    for clock in clocks:
        animation_id, value = _animation_clock_dict(clock)
        by_var.setdefault(str(value["frame_var"]).casefold(), []).append(
            (animation_id, value))
    families = {}
    for draw in draws:
        candidate = frame_condition(
            draw.animation_conditions,
            by_var.keys())
        if candidate is None:
            continue
        var, frame = candidate
        clock_candidates = _clock_candidates(var, draw.conditions, by_var)
        if len(clock_candidates) != 1:
            continue
        animation_id, clock = clock_candidates[0]
        if not (int(clock["frame_start"]) <= frame
                <= int(clock["frame_end"])):
            continue
        signature = _freeze(tuple(
            getattr(draw, field) for field in _ANIMATION_STATIC_FIELDS))
        family = families.setdefault((animation_id, signature), {
            "clock": clock, "draws": {}, "base_draw": draw,
        })
        # Ambiguous duplicate branches are not a frame family.
        if frame in family["draws"]:
            family["ambiguous"] = True
        else:
            family["draws"][frame] = draw

    # Some 3DMigoto mods keep the ordinary drawindexed rows static and put
    # the animation in a command list that conditionally binds vb0 for each
    # frame.  Project those authored bindings onto the existing draw rows so
    # the normal topology/material path remains authoritative.
    for draw in draws:
        for binding in (group or {}).get("animation_vertex_bindings") or ():
            candidate = frame_condition(
                binding.get("animation_conditions"), by_var.keys())
            if candidate is None:
                continue
            var, frame = candidate
            if not _condition_groups_overlap(
                    draw.conditions, binding.get("conditions")):
                continue
            clock_candidates = _clock_candidates(
                var, draw.conditions, by_var)
            if len(clock_candidates) != 1:
                continue
            animation_id, clock = clock_candidates[0]
            if not (int(clock["frame_start"]) <= frame
                    <= int(clock["frame_end"])):
                continue
            frame_draw = _frame_source_draw(draw, binding)
            if frame_draw is None:
                continue
            signature = _freeze(tuple(
                getattr(draw, field) for field in _ANIMATION_STATIC_FIELDS))
            family = families.setdefault((animation_id, signature), {
                "clock": clock, "draws": {}, "base_draw": draw,
            })
            if frame in family["draws"]:
                # A direct drawindexed family already carries the same frame
                # information in its resolved draw snapshot.  Keep that
                # authoritative row when a command-list binding is also
                # visible, rather than turning the mixed representation into
                # a false ambiguity.
                if family["draws"][frame] is not draw:
                    continue
            else:
                family["draws"][frame] = frame_draw
    # Keep incomplete families too.  The validator will reject a missing
    # frame and the caller will then emit only the canonical static draw,
    # instead of accidentally publishing one mesh per surviving branch.
    return [family for family in families.values()
            if family["draws"] and not family.get("ambiguous")]


def _prepared_topology(prepared):
    indices = tuple(prepared.remap[value] for value in prepared.raw_indices)
    uvs = []
    for vertex in prepared.used_vertices:
        _x, _y, _z, u, v = prepared.decoded_vertices[vertex]
        uvs.append((u, None if v is None else 1.0 - v))
    return indices, tuple(uvs)


def _compatible_prepared(canonical, other):
    if len(canonical.used_vertices) != len(other.used_vertices):
        return False
    left_indices, left_uvs = _prepared_topology(canonical)
    right_indices, right_uvs = _prepared_topology(other)
    if left_indices != right_indices or len(left_uvs) != len(right_uvs):
        return False
    for left, right in zip(left_uvs, right_uvs):
        if left[0] is None or right[0] is None:
            if left != right:
                return False
            continue
        if (abs(left[0] - right[0]) > 1e-6
                or abs(left[1] - right[1]) > 1e-6):
            return False
    return True


def _prepare_animation_family(family, *, canonical_prepared, canonical_packed,
                              mod_dir, group, default_streams,
                              default_index_size, buffers,
                              geometry_convention, source):
    """Validate and pack changing attributes for one draw family."""
    clock = family["clock"]
    start = int(clock["frame_start"])
    end = int(clock["frame_end"])
    frames = family["draws"]
    if set(frames) != set(range(start, end + 1)):
        return None

    prepared_by_frame = {start: canonical_prepared}
    for frame in range(start + 1, end + 1):
        prepared = _prepare_draw_vertices(
            frames[frame], group, mod_dir=mod_dir,
            default_streams=default_streams,
            default_index_size=default_index_size, buffers=buffers,
            geometry_convention=geometry_convention, source=source)
        if prepared is None or not _compatible_prepared(
                canonical_prepared, prepared):
            return None
        prepared_by_frame[frame] = prepared

    packed_frames = []
    normals = []
    for frame in range(start, end + 1):
        if frame == start:
            packed_frame = PackedAnimationFrame(
                canonical_packed.positions, canonical_packed.normals)
        else:
            packed_frame = pack_animation_frame_attributes(
                frames[frame], prepared_by_frame[frame], mod_dir=mod_dir,
                buffers=buffers, source=source)
        packed_frames.append(packed_frame.positions)
        normals.append(packed_frame.normals)
    has_normals = all(value is not None for value in normals)
    return {
        "clock": clock,
        "draws": frames,
        "positions": b"".join(packed_frames),
        "normals": b"".join(normals) if has_normals else None,
        "position_frame_bytes": len(packed_frames[0]),
        "normal_frame_bytes": (len(normals[0]) if has_normals else 0),
        "frame_count": len(packed_frames),
    }


def _geometry_ref(raw, geometry):
    """Serialize bytes into the caller-owned geometry store or base64."""
    if geometry is not None:
        return geometry.add(raw)
    return base64.b64encode(raw).decode()


def build_mesh_result(groups, mod_dir, max_draws=0, geometry=None,
                      texture_source=None, game_profile=None, source=None,
                      animations=None):
    """Build mesh draw entries and a shared texture registry.

    Geometry packing and texture publication are delegated to focused stages;
    this facade keeps transport compatibility and final payload metadata.
    """
    from ..textures.profiles import texture_profile_for

    texture_profile = texture_profile_for(game_profile)
    geometry_convention = geometry_convention_for(game_profile)
    validate_draw_count(groups)
    registry = TextureRegistry(
        mod_dir, texture_profile, texture_source, source=source)
    buffers = BufferStore(source=source)
    sparse_shape_cache = {}
    result = {}
    skinning_manifest = {}
    animation_clocks = {}
    for clock in animations or ():
        animation_id, value = _animation_clock_dict(clock)
        animation_clocks[animation_id] = value
    used_animation_ids = set()

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
        families = _animation_families(
            unique, animation_clocks.values(), group=group)
        family_by_draw = {}
        for family in families:
            family_by_draw[id(family["base_draw"])] = family
            # Direct drawindexed families use the authored DrawCall objects
            # themselves; command-list families use cloned frame views.  The
            # latter are not in ``unique``, but mapping both forms keeps the
            # original direct-family behavior intact.
            for family_draw in family["draws"].values():
                family_by_draw.setdefault(id(family_draw), family)
        texture_options = build_texture_options(group, registry)
        processed_families = set()

        for draw in unique:
            family = family_by_draw.get(id(draw))
            if family is not None:
                family_key = id(family)
                if family_key in processed_families:
                    continue
                processed_families.add(family_key)
                canonical_draw = family["draws"].get(
                    int(family["clock"]["frame_start"]))
                if canonical_draw is None:
                    # An incomplete family still gets one safe static draw.
                    canonical_draw = family["draws"][
                        min(family["draws"])]
                if canonical_draw is None:
                    continue
                draw = canonical_draw

            prepared = None
            if family is not None:
                prepared = _prepare_draw_vertices(
                    draw, group, mod_dir=mod_dir,
                    default_streams=default_streams,
                    default_index_size=index_size, buffers=buffers,
                    geometry_convention=geometry_convention, source=source)
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
                    geometry_convention=geometry_convention, source=source)
                if animation_payload is None:
                    # A rejected family falls back to its representative
                    # frame, never a partially mapped mesh.
                    animation_payload = None

            if draw.skinning_source is not None:
                skinning_manifest[draw.label] = \
                    SkinningManifestEntry.from_vertices(
                        draw.label, draw.skinning_source,
                        packed.used_vertices)

            entry: dict = {
                "pos": _geometry_ref(packed.positions, geometry),
                "idx": _geometry_ref(packed.indices, geometry),
                "skinning_available": draw.skinning_source is not None,
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
            if animation_payload is not None:
                clock = animation_payload["clock"]
                animation_id = clock["id"]
                entry["animation_id"] = animation_id
                entry["animation_geometry"] = {
                    "positions": _geometry_ref(
                        animation_payload["positions"], geometry),
                    "position_frame_bytes": animation_payload[
                        "position_frame_bytes"],
                    "frames": animation_payload["frame_count"],
                }
                if animation_payload["normals"] is not None:
                    entry["animation_geometry"]["normals"] = _geometry_ref(
                        animation_payload["normals"], geometry)
                    entry["animation_geometry"]["normal_frame_bytes"] = \
                        animation_payload["normal_frame_bytes"]
                used_animation_ids.add(animation_id)
            if draw.conditions:
                entry["conditions"] = draw.conditions
            if draw.sources:
                entry["sources"] = [_rel_source(item, mod_dir, source=source)
                                    for item in draw.sources]
            if texture_options:
                entry["texture_options"] = texture_options
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

    return MeshBuildResult(
        meshes=result,
        textures=registry.sources,
        geometry=geometry,
        skinning_manifest=skinning_manifest,
        animations={key: animation_clocks[key]
                    for key in sorted(used_animation_ids)},
    )


def build_mesh_payload(groups, mod_dir, max_draws=0, geometry=None,
                       texture_source=None, game_profile=None, source=None,
                       animations=None):
    """Legacy flat payload wrapper retaining the ``__textures__`` field."""
    built = build_mesh_result(
        groups, mod_dir, max_draws=max_draws, geometry=geometry,
        texture_source=texture_source, game_profile=game_profile,
        source=source, animations=animations)
    payload = dict(built.meshes)
    payload["__textures__"] = built.textures
    return payload


__all__ = [
    "MeshBuildResult", "build_mesh_result", "build_mesh_semantics",
    "build_mesh_payload", "GeometryBlob", "POSITION_STRIDE",
    "POSITION_OFFSET", "DEFAULT_UV_OFFSET", "INDEX_SIZE", "_res_get",
    "_deduplicate_draws", "read_positions", "read_texcoords", "read_indices",
]

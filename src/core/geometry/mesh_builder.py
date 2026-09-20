"""Public facade for semantic projection and mesh payload construction."""

import base64
import hashlib
import math
import os
import struct
import time
from copy import copy
from dataclasses import dataclass, replace

from .buffers import (
    DEFAULT_UV_OFFSET, INDEX_SIZE, POSITION_OFFSET, POSITION_STRIDE,
    BufferStore, _res_get, read_indices, read_positions, read_texcoords,
)
from .conventions import geometry_convention_for
from .packing import (
    PackedAnimationFrame, _prepare_draw_vertices,
    pack_animation_frame_attributes, pack_animation_position_frame,
    pack_draw_geometry,
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
    diagnostics: dict | None = None


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


def _animation_track_id(frame_var, signature, label):
    identity = repr((str(frame_var).casefold(), signature, label))
    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:12]
    return f"track::{frame_var}::{digest}"


def _animation_family(families, *, frame_var, draw, signature):
    key = (str(frame_var).casefold(), signature)
    return families.setdefault(key, {
        "track_id": _animation_track_id(frame_var, signature, draw.label),
        "frame_var": frame_var,
        "draws": {},
        "base_draw": draw,
        "clock_ids": {},
        "position_switching": False,
    })


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
    """Group geometry tracks independently from their playback clocks."""
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
        ranged_candidates = [
            item for item in clock_candidates
            if int(item[1]["frame_start"]) <= frame
            <= int(item[1]["frame_end"])
        ]
        if not ranged_candidates:
            continue
        signature = _freeze(tuple(
            getattr(draw, field) for field in _ANIMATION_STATIC_FIELDS))
        family = _animation_family(
            families, frame_var=var, draw=draw, signature=signature)
        for animation_id, clock in ranged_candidates:
            family["clock_ids"].setdefault(animation_id, clock)
        family["frame_start"] = min(
            int(item[1]["frame_start"])
            for item in family["clock_ids"].items())
        family["frame_end"] = max(
            int(item[1]["frame_end"])
            for item in family["clock_ids"].items())
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
            clock_candidates = [
                item for item in by_var.get(str(var).casefold(), ())
                if _condition_groups_overlap(
                    draw.conditions, item[1].get("conditions"))
                and _condition_groups_overlap(
                    binding.get("conditions"), item[1].get("conditions"))
            ]
            ranged_candidates = [
                item for item in clock_candidates
                if int(item[1]["frame_start"]) <= frame
                <= int(item[1]["frame_end"])
            ]
            if not ranged_candidates:
                continue
            frame_draw = _frame_source_draw(draw, binding)
            if frame_draw is None:
                continue
            signature = _freeze(tuple(
                getattr(draw, field) for field in _ANIMATION_STATIC_FIELDS))
            family = _animation_family(
                families, frame_var=var, draw=draw, signature=signature)
            for animation_id, clock in ranged_candidates:
                family["clock_ids"].setdefault(animation_id, clock)
            family["frame_start"] = min(
                int(item[1]["frame_start"])
                for item in family["clock_ids"].items())
            family["frame_end"] = max(
                int(item[1]["frame_end"])
                for item in family["clock_ids"].items())
            if frame in family["draws"]:
                # A direct drawindexed family already carries the same frame
                # information in its resolved draw snapshot.  Keep that
                # authoritative row when a command-list binding is also
                # visible, rather than turning the mixed representation into
                # a false ambiguity.
                if not family.get("position_switching"):
                    continue
            else:
                family["draws"][frame] = frame_draw
            family["position_switching"] = True
    # Keep incomplete families too.  The validator will reject a missing
    # frame and the caller will then emit only the canonical static draw,
    # instead of accidentally publishing one mesh per surviving branch.
    for family in families.values():
        if family.get("position_switching"):
            continue
        family_draws = list(family["draws"].values())
        if len(family_draws) < 2:
            continue
        first = family_draws[0]
        topology_fields = ("count", "start", "base", "ib_file",
                           "index_size", "texcoord_file", "texcoord_stride")
        if all(tuple(getattr(draw, field) for field in topology_fields)
               == tuple(getattr(first, field) for field in topology_fields)
               for draw in family_draws[1:]):
            family["position_switching"] = True
    return [family for family in families.values()
            if family["draws"] and not family.get("ambiguous")]


def _prepared_topology(prepared):
    indices = tuple(prepared.remap[value] for value in prepared.raw_indices)
    uvs = []
    for vertex in prepared.used_vertices:
        _x, _y, _z, u, v = prepared.decoded_vertices[vertex]
        uvs.append((u, None if v is None else 1.0 - v))
    return indices, tuple(uvs)


def _compatible_prepared(canonical, other, canonical_topology=None):
    if len(canonical.used_vertices) != len(other.used_vertices):
        return False
    left_indices, left_uvs = (canonical_topology
                              or _prepared_topology(canonical))
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
                              geometry_convention, source, geometry=None,
                              diagnostics=None):
    """Validate and pack changing attributes for one draw family."""
    start = int(family["frame_start"])
    end = int(family["frame_end"])
    frames = family["draws"]
    if set(frames) != set(range(start, end + 1)):
        return None

    canonical_topology = None
    if not family.get("position_switching"):
        canonical_topology = _prepared_topology(canonical_prepared)

    frame_count = end - start + 1
    frame_bytes = len(canonical_packed.positions)
    checkpoint = len(geometry) if geometry is not None else None

    def reject():
        if geometry is not None:
            geometry.truncate(checkpoint)
        return None

    position_ref = (geometry.reserve(frame_count * frame_bytes)
                    if geometry is not None else None)
    packed_frames = []
    normals = []
    normal_ref = None
    normal_possible = canonical_packed.normals is not None
    normal_frame_bytes = (len(canonical_packed.normals)
                          if normal_possible else 0)
    bounds_min = [float("inf")] * 3
    bounds_max = [float("-inf")] * 3
    canonical_bounds = (canonical_packed.bounds_min,
                        canonical_packed.bounds_max)
    pack_started = time.perf_counter()
    for frame_index, frame in enumerate(range(start, end + 1)):
        if frame == start:
            packed_frame = PackedAnimationFrame(
                canonical_packed.positions, canonical_packed.normals)
        elif family.get("position_switching"):
            packed_frame = pack_animation_position_frame(
                frames[frame], canonical_prepared.used_vertices,
                mod_dir=mod_dir, buffers=buffers, source=source)
        else:
            prepare_started = time.perf_counter()
            prepared = _prepare_draw_vertices(
                frames[frame], group, mod_dir=mod_dir,
                default_streams=default_streams,
                default_index_size=default_index_size, buffers=buffers,
                geometry_convention=geometry_convention, source=source)
            if diagnostics is not None:
                diagnostics["animation_prepare_calls"] += 1
                diagnostics["animation_prepare_seconds"] += (
                    time.perf_counter() - prepare_started)
            if prepared is None or not _compatible_prepared(
                    canonical_prepared, prepared, canonical_topology):
                return reject()
            packed_frame = pack_animation_frame_attributes(
                frames[frame], prepared, mod_dir=mod_dir,
                buffers=buffers, source=source)
        if packed_frame is None or len(packed_frame.positions) != frame_bytes:
            return reject()
        frame_bounds = (canonical_bounds if frame == start else
                        (packed_frame.bounds_min, packed_frame.bounds_max))
        if frame_bounds[0] is None or frame_bounds[1] is None:
            return reject()
        for index in range(3):
            bounds_min[index] = min(bounds_min[index], frame_bounds[0][index])
            bounds_max[index] = max(bounds_max[index], frame_bounds[1][index])
        if position_ref is not None:
            geometry.write(
                position_ref["offset"] + frame_index * frame_bytes,
                packed_frame.positions)
        else:
            packed_frames.append(packed_frame.positions)
        if normal_possible:
            if (packed_frame.normals is None
                    or len(packed_frame.normals) != normal_frame_bytes):
                normal_possible = False
            elif geometry is None:
                normals.append(packed_frame.normals)
            else:
                if normal_ref is None:
                    normal_ref = geometry.reserve(frame_count * normal_frame_bytes)
                geometry.write(
                    normal_ref["offset"] + frame_index * normal_frame_bytes,
                    packed_frame.normals)
    if diagnostics is not None:
        diagnostics["animation_pack_seconds"] += (
            time.perf_counter() - pack_started)
        diagnostics["animation_frame_count"] += frame_count
    has_normals = normal_possible and (
        len(normals) == frame_count if geometry is None else normal_ref is not None)
    if not has_normals:
        if geometry is not None and normal_ref is not None:
            geometry.truncate(normal_ref["offset"])
        normal_ref = None
    return {
        "track_id": family["track_id"],
        "clock_ids": list(family["clock_ids"]),
        "draws": frames,
        "positions": (b"".join(packed_frames)
                      if position_ref is None else None),
        "positions_ref": position_ref,
        "normals": (b"".join(normals) if has_normals and geometry is None
                    else None),
        "normals_ref": normal_ref,
        "position_frame_bytes": frame_bytes,
        "normal_frame_bytes": (normal_frame_bytes if has_normals else 0),
        "frame_count": frame_count,
        "frame_start": start,
        "bounds": {"min": bounds_min, "max": bounds_max},
    }


def _geometry_ref(raw, geometry):
    """Serialize bytes into the caller-owned geometry store or base64."""
    if geometry is not None:
        return geometry.add(raw)
    return base64.b64encode(raw).decode()


def _gimi_path(mod_dir, value, source):
    return (source.resolve_resource(value) if source is not None
            else safe_resource_path(mod_dir, value))


def _prepare_gimi_shared(animation, *, mod_dir, buffers, source, geometry):
    """Load the one pose stream shared by all compact draws in a track."""
    pose = animation.get("pose")
    if not pose:
        return {}
    pose_path = _gimi_path(mod_dir, pose["file"], source)
    if not pose_path:
        return None
    pose_data = buffers.raw(pose_path)
    expected = (int(pose["frame_count"])
                * int(pose["bone_count"]) * 56)
    if len(pose_data) != expected:
        return None
    return {
        "pose_frames": _geometry_ref(pose_data, geometry),
        "pose_frame_bytes": 56,
    }


def _prepare_gimi_geometry(animation, used_vertices, *, mod_dir, buffers,
                           source, geometry, shared):
    """Pack fixed-layout compute inputs in the draw's compact vertex order."""
    base_path = _gimi_path(mod_dir, animation["base_file"], source)
    pose = animation.get("pose")
    blend_path = (_gimi_path(mod_dir, pose["blend_file"], source)
                  if pose else None)
    if not base_path or (pose and not blend_path):
        return None
    base_data = buffers.raw(base_path)
    blend_data = buffers.raw(blend_path) if pose else None
    vertex_count = int(animation["vertex_count"])
    if (len(base_data) != vertex_count * 40
            or (pose and len(blend_data) != vertex_count * 32)
            or any(index < 0 or index >= vertex_count
                   for index in used_vertices)):
        return None

    shape_entries = []
    base_normals = bytearray(len(used_vertices) * 12)
    for output, raw_index in enumerate(used_vertices):
        values = struct.unpack_from("<3f", base_data, raw_index * 40 + 12)
        if not all(math.isfinite(value) for value in values):
            return None
        struct.pack_into("<3f", base_normals, output * 12, *values)
    for item in animation.get("shape_passes", ()):
        target_path = _gimi_path(mod_dir, item["target_file"], source)
        if not target_path:
            return None
        target_data = buffers.raw(target_path)
        if len(target_data) != vertex_count * 40:
            return None
        deltas = bytearray(len(used_vertices) * 24)
        limit = min(int(item["dispatch_vertices"]), vertex_count)
        for output, raw_index in enumerate(used_vertices):
            if raw_index >= limit:
                continue
            base_offset = raw_index * 40
            target_offset = raw_index * 40
            base_values = struct.unpack_from("<6f", base_data, base_offset)
            target_values = struct.unpack_from("<6f", target_data, target_offset)
            struct.pack_into(
                "<6f", deltas, output * 24,
                *(target_values[index] - base_values[index]
                  for index in range(6)))
        shape_entries.append({
            "deltas": _geometry_ref(deltas, geometry),
            "vertex_count": len(used_vertices),
            "delta_floats": 6,
            "phase_offset": float(item.get("phase_offset", 0.0)),
            "amplitude": float(item["amplitude"]),
            "angular_scale": float(item["angular_scale"]),
            "bias": float(item["bias"]),
        })

    result = {
        "kind": "gimi_compute",
        "track_id": animation["track_id"],
        "base_normals": _geometry_ref(base_normals, geometry),
        "shape_passes": shape_entries,
        "shape_clock": animation.get("shape_clock"),
        "pose_clock": animation.get("pose_clock"),
        "vertex_count": len(used_vertices),
    }
    if pose:
        weights = bytearray(len(used_vertices) * 16)
        indices = bytearray(len(used_vertices) * 16)
        pose_active = bytearray(len(used_vertices) * 4)
        bone_count = int(pose["bone_count"])
        pose_limit = min(int(pose["dispatch_vertices"]), vertex_count)
        for output, raw_index in enumerate(used_vertices):
            offset = raw_index * 32
            values = struct.unpack_from("<4f4i", blend_data, offset)
            if (not all(math.isfinite(value) for value in values[:4])
                    or any(index < 0 or index >= bone_count
                           for index in values[4:])):
                return None
            struct.pack_into("<4f", weights, output * 16, *values[:4])
            struct.pack_into("<4i", indices, output * 16, *values[4:])
            struct.pack_into("<f", pose_active, output * 4,
                             1.0 if raw_index < pose_limit else 0.0)
        result["pose"] = {
            "frames": shared["pose_frames"],
            "frame_bytes": shared["pose_frame_bytes"],
            "frame_floats": 14,
            "bone_count": bone_count,
            "frame_count": int(pose["frame_count"]),
            "dispatch_vertices": int(pose["dispatch_vertices"]),
            "basis": pose.get("basis"),
            "blend": {
                "weights": _geometry_ref(weights, geometry),
                "indices": _geometry_ref(indices, geometry),
                "active": _geometry_ref(pose_active, geometry),
                "vertex_count": len(used_vertices),
            },
        }
    else:
        result["pose"] = None
    return result


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
                    int(family["frame_start"]))
                if canonical_draw is None:
                    # An incomplete family still gets one safe static draw.
                    canonical_draw = family["draws"][
                        min(family["draws"])]
                if canonical_draw is None:
                    continue
                draw = canonical_draw

            prepared = None
            if family is not None:
                prepare_started = time.perf_counter()
                prepared = _prepare_draw_vertices(
                    draw, group, mod_dir=mod_dir,
                    default_streams=default_streams,
                    default_index_size=index_size, buffers=buffers,
                    geometry_convention=geometry_convention, source=source)
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
                if animation_payload is None:
                    # A rejected family falls back to its representative
                    # frame, never a partially mapped mesh.
                    animation_payload = None

            gimi_payload = None
            gimi = group.get("_compute_animation")
            same_position = (source.same_reference(
                draw.position_file, group.get("position_file"))
                if source is not None else
                os.path.normcase(os.path.normpath(draw.position_file or ""))
                == os.path.normcase(os.path.normpath(
                    group.get("position_file") or "")))
            if gimi is not None and same_position and animation_payload is None:
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
                        shared=shared)

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
                animation_id = animation_payload["track_id"]
                entry["animation_id"] = animation_id
                animation_geometry = {
                    "positions": (animation_payload["positions_ref"]
                                   if animation_payload["positions_ref"]
                                   is not None else _geometry_ref(
                                       animation_payload["positions"],
                                       geometry)),
                    "position_frame_bytes": animation_payload[
                        "position_frame_bytes"],
                    "frames": animation_payload["frame_count"],
                    "frame_start": animation_payload["frame_start"],
                    "clock_ids": animation_payload["clock_ids"],
                    "bounds": animation_payload["bounds"],
                }
                if animation_payload["normals"] is not None:
                    animation_geometry["normals"] = _geometry_ref(
                        animation_payload["normals"], geometry)
                    animation_geometry["normal_frame_bytes"] = \
                        animation_payload["normal_frame_bytes"]
                elif animation_payload["normals_ref"] is not None:
                    animation_geometry["normals"] = animation_payload[
                        "normals_ref"]
                    animation_geometry["normal_frame_bytes"] = \
                        animation_payload["normal_frame_bytes"]
                entry["animation_geometry"] = animation_geometry
                used_clock_ids.update(animation_payload["clock_ids"])
                animation_diagnostics["animation_family_count"] += 1
                animation_diagnostics["animation_geometry_bytes"] += (
                    animation_payload["position_frame_bytes"]
                    * animation_payload["frame_count"]
                    + (animation_payload["normal_frame_bytes"]
                       * animation_payload["frame_count"]
                       if animation_payload["normals"] is not None
                       or animation_payload["normals_ref"] is not None else 0))
            if gimi_payload is not None:
                entry["animation_id"] = gimi_payload["track_id"]
                entry["animation_geometry"] = gimi_payload
                animation_diagnostics["animation_family_count"] += 1
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
                    for key in sorted(used_clock_ids)},
        diagnostics=animation_diagnostics,
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

"""Baked animation-family discovery and frame geometry preparation."""

import hashlib
import os
import struct
import time
from copy import copy
from dataclasses import replace

from .buffers import POSITION_STRIDE
from .packing import (
    PackedAnimationFrame, _prepare_draw_vertices,
    pack_animation_frame_attributes, pack_animation_position_frame,
    pack_animation_topology_frame,
)
from .draw_call import _freeze
from ..ini.animations import frame_condition
from ..resource_paths import safe_resource_path


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


def _animation_signature(draw):
    fields = _ANIMATION_STATIC_FIELDS
    if draw.animation_binding_join:
        fields = tuple(field for field in fields if field not in (
            "ib_file", "index_size", "texcoord_file", "texcoord_stride"))
    signature = _freeze(tuple(getattr(draw, field) for field in fields))
    if draw.animation_binding_join:
        signature = (signature, _freeze(draw.occurrence), draw.count, draw.start, draw.base)
    return signature


def _merge_animation_families(families):
    """Merge only complete, identical frame sequences on the same clocks."""
    merged = {}
    result = []
    for family in families:
        family.setdefault("alternatives", []).extend(
            [family["base_draw"], *family["draws"].values()])
        frames = range(family["frame_start"], family["frame_end"] + 1)
        if set(family["draws"]) != set(frames):
            result.append(family)
            continue
        key = (tuple(sorted(family["clock_ids"])), tuple(
            (frame, family["draws"][frame].render_identity()) for frame in frames))
        previous = merged.get(key)
        if previous is None:
            merged[key] = family
            result.append(family)
            continue
        previous["alternatives"].extend(family["alternatives"])
        # Every source already carries its own ordinary guard. Keep those
        # guards while OR-ing the visibility of the equivalent displayed draw.
        def merge_views(draw, other):
            return replace(
                draw, conditions=([] if not draw.conditions or not other.conditions else
                                  draw.conditions + [group for group in other.conditions
                                                     if group not in draw.conditions]),
                sources=draw.sources + [source for source in other.sources
                                        if source not in draw.sources])
        previous["base_draw"] = merge_views(previous["base_draw"], family["base_draw"])
        previous["draws"] = {frame: merge_views(previous["draws"][frame], family["draws"][frame])
                             for frame in frames}
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
        signature = _animation_signature(draw)
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
        if any(draw.animation_conditions):
            continue
        for binding in (group or {}).get("animation_vertex_bindings") or ():
            if (binding.get("position_file") != draw.position_file
                    or binding.get("ib_file") != draw.ib_file):
                continue
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
    result = [family for family in families.values()
              if family["draws"] and not family.get("ambiguous")]
    alternatives = {}
    for draw in draws:
        if draw.animation_binding_join:
            alternatives.setdefault(_animation_signature(draw), []).append(draw)
    for family in result:
        if family["base_draw"].animation_binding_join:
            signature = _animation_signature(family["base_draw"])
            # An untouched/out-of-range fallback is part of the same authored
            # draw, not an additional always-visible mesh beside its animation.
            family["alternatives"] = alternatives[signature]
    return _merge_animation_families(result)


def _prepared_topology(prepared):
    indices = tuple(prepared.remap[value] for value in prepared.raw_indices)
    uvs = []
    for vertex in prepared.used_vertices:
        _x, _y, _z, u, v = prepared.decoded_vertices[vertex]
        uvs.append((u, None if v is None else 1.0 - v))
    return indices, tuple(uvs)


def animation_draws(draws, clocks, group=None):
    """Select the same canonical draws for geometry and semantic refreshes."""
    family_by_draw = {}
    for family in _animation_families(draws, clocks, group):
        for draw in family["alternatives"]:
            family_by_draw.setdefault(id(draw), family)
    processed = set()
    for draw in draws:
        family = family_by_draw.get(id(draw))
        if family is not None:
            if id(family) in processed:
                continue
            processed.add(id(family))
            draw = family["draws"].get(family["frame_start"]) or family["draws"][min(family["draws"])]
        yield draw, family


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


def _static_position_records(family, canonical, *, mod_dir, buffers, source):
    """Prove identical drawn attributes without decoding unused record fields."""
    used = canonical.used_vertices
    if not used or used[-1] - used[0] + 1 != len(used):
        return False
    stride = canonical.streams.position_stride
    columns = set(range(12))
    reference = canonical.streams.position_data
    if stride < 12 or used[0] < 0:
        return False
    resolve = source.resolve_resource if source is not None else \
        lambda value: safe_resource_path(mod_dir, value)
    exists = source.is_file if source is not None else os.path.exists
    same = source.same_reference if source is not None else \
        lambda left, right: left == right
    canonical_draw = family["draws"][family["frame_start"]]
    canonical_normal = canonical_draw.normal_source
    if canonical_normal is not None:
        normal_size = {"f32x3": 12, "snorm8x3": 3}.get(
            canonical_normal.encoding)
        if (normal_size is None or canonical_normal.stride != stride
                or canonical_normal.offset + normal_size > stride
                or not same(resolve(canonical_normal.file),
                            canonical.position_path)):
            return False
        columns.update(range(canonical_normal.offset,
                             canonical_normal.offset + normal_size))
    columns = sorted(columns)
    begin = used[0] * stride
    end = used[-1] * stride + columns[-1] + 1
    if end > len(reference):
        return False
    # Column slices run in C and omit tangents/padding without a Python loop
    # over every vertex. Exact byte equality is a proof, never a rejection:
    # differing encodings, signed zero and normal scales use decoded fallback.
    reference_columns = [reference[begin + column:end:stride]
                         for column in columns]
    for frame, draw in family["draws"].items():
        if frame == family["frame_start"]:
            continue
        if (draw.position_stride or POSITION_STRIDE) != stride:
            return False
        normal = draw.normal_source
        if (normal is None) != (canonical_normal is None):
            return False
        if normal is not None and (
                normal.stride != canonical_normal.stride
                or normal.offset != canonical_normal.offset
                or normal.encoding != canonical_normal.encoding):
            return False
        if normal is not None and not same(
                resolve(normal.file), resolve(draw.position_file)):
            return False
        path = resolve(draw.position_file)
        if not path or not exists(path):
            return False
        data = buffers.transient(path)
        if end > len(data) or any(
                data[begin + column:end:stride] != expected
                for column, expected in zip(columns, reference_columns)):
            return False
    return True


def _translated_frame_vertices(draw, canonical, *, mod_dir, buffers,
                               default_index_size, geometry_convention,
                               source):
    """Prove index translation and identical contiguous UV records."""
    used = canonical.used_vertices
    if not used or used[-1] - used[0] + 1 != len(used):
        return None
    resolve = source.resolve_resource if source is not None else \
        lambda value: safe_resource_path(mod_dir, value)
    exists = source.is_file if source is not None else os.path.exists
    ib_path = resolve(draw.ib_file)
    tc_path = resolve(draw.texcoord_file)
    if not ib_path or not tc_path or not exists(ib_path) or not exists(tc_path):
        return None
    same = source.same_reference if source is not None else \
        lambda left, right: left == right
    if not same(tc_path, canonical.texcoord_path):
        return None
    raw = buffers.indices(ib_path, draw.start, draw.count,
                          draw.index_size if draw.index_size is not None
                          else default_index_size)
    expected = canonical.raw_indices
    if (not raw or len(raw) != len(expected)
            or draw.count is None or len(raw) != draw.count
            or len(raw) % 3):
        return None
    delta = raw[0] + draw.base - expected[0]
    if geometry_convention.reverse_winding:
        for index in range(0, len(raw), 3):
            if (raw[index] + draw.base != expected[index] + delta
                    or raw[index + 1] + draw.base != expected[index + 2] + delta
                    or raw[index + 2] + draw.base != expected[index + 1] + delta):
                return None
    elif any(value + draw.base != original + delta
             for value, original in zip(raw, expected)):
        return None
    translated = [value + delta for value in used]
    if translated[0] < 0:
        return None
    stride = canonical.streams.texcoord_stride
    if (draw.texcoord_stride or stride) != stride:
        return None
    original = canonical.streams.texcoord_data
    left = used[0] * stride
    right = translated[0] * stride
    size = len(used) * stride
    if (left < 0 or right < 0 or left + size > len(original)
            or right + size > len(original)
            or original[left:left + size] != original[right:right + size]):
        return None
    return translated


def _same_float32_attributes(left, right):
    """Compare emitted values exactly, treating signed zero as the same value."""
    return len(left) == len(right) and (
        left == right or all(a == b for a, b in zip(
            struct.iter_unpack("<f", left), struct.iter_unpack("<f", right))))


def _prepare_animation_family(family, *, canonical_prepared, canonical_packed,
                              mod_dir, group, default_streams,
                              default_index_size, buffers,
                              geometry_convention, source, geometry,
                              diagnostics=None, frame_cache=None):
    """Validate and pack changing attributes for one draw family."""
    start = int(family["frame_start"])
    end = int(family["frame_end"])
    frames = family["draws"]
    if set(frames) != set(range(start, end + 1)):
        return None

    cache_key = None
    if frame_cache is not None and family.get("position_switching"):
        # Only positions/normals vary on this proven path. Mesh topology,
        # materials, visibility and playback identity remain owned by each draw.
        cache_key = (
            tuple(canonical_prepared.used_vertices),
            canonical_packed.positions, bytes(canonical_packed.normals)
            if canonical_packed.normals is not None else None,
            tuple((frame, frames[frame].position_file,
                   frames[frame].position_stride, frames[frame].normal_source)
                  for frame in range(start, end + 1)),
        )
        if cache_key in frame_cache:
            cached = frame_cache[cache_key]
            if diagnostics is not None:
                diagnostics["animation_reused_family_count"] += 1
            return ({**cached, "track_id": family["track_id"],
                     "clock_ids": list(family["clock_ids"]), "draws": frames}
                    if cached is not None else None)

    def remember(value):
        if cache_key is not None:
            frame_cache[cache_key] = value
        return value

    if (family.get("position_switching")
            and _static_position_records(family, canonical_prepared,
                                         mod_dir=mod_dir, buffers=buffers,
                                         source=source)):
        return remember(None)

    binding_frames = (family["base_draw"].animation_binding_join
                      and not family.get("position_switching"))
    canonical_topology = None
    if not family.get("position_switching") and not binding_frames:
        canonical_topology = _prepared_topology(canonical_prepared)

    frame_count = end - start + 1
    frame_bytes = len(canonical_packed.positions)
    checkpoint = len(geometry)

    def reject():
        geometry.truncate(checkpoint)
        return remember(None)

    position_ref = geometry.reserve(frame_count * frame_bytes)
    normal_ref = None
    normal_possible = canonical_packed.normals is not None
    normal_frame_bytes = (len(canonical_packed.normals)
                          if normal_possible else 0)
    positions_changed = False
    normals_changed = False
    indices, texcoords = [], []
    topology_changed = False
    bounds_min = [float("inf")] * 3
    bounds_max = [float("-inf")] * 3
    canonical_bounds = (canonical_packed.bounds_min,
                        canonical_packed.bounds_max)
    pack_started = time.perf_counter()
    for frame_index, frame in enumerate(range(start, end + 1)):
        topology = (canonical_packed.indices, canonical_packed.texcoords)
        if frame == start:
            packed_frame = PackedAnimationFrame(
                canonical_packed.positions, canonical_packed.normals)
        elif family.get("position_switching") or binding_frames:
            if binding_frames:
                prepare_started = time.perf_counter()
                topology = pack_animation_topology_frame(
                    frames[frame], canonical_prepared, canonical_draw=frames[start],
                    canonical_packed=canonical_packed, mod_dir=mod_dir,
                    buffers=buffers, default_index_size=default_index_size,
                    geometry_convention=geometry_convention, source=source)
                if diagnostics is not None:
                    diagnostics["animation_prepare_calls"] += 1
                    diagnostics["animation_prepare_seconds"] += time.perf_counter() - prepare_started
                if topology is None:
                    return reject()
            packed_frame = pack_animation_position_frame(
                frames[frame], canonical_prepared.used_vertices,
                mod_dir=mod_dir, buffers=buffers, source=source,
                pack_normals=normal_possible)
        else:
            translated = _translated_frame_vertices(
                frames[frame], canonical_prepared, mod_dir=mod_dir,
                buffers=buffers, default_index_size=default_index_size,
                geometry_convention=geometry_convention, source=source)
            if translated is not None:
                draw = frames[frame]
                path = (source.resolve_resource(draw.position_file)
                        if source is not None else
                        safe_resource_path(mod_dir, draw.position_file))
                same = source.same_reference if source is not None else \
                    lambda left, right: left == right
                position_data = (canonical_prepared.streams.position_data
                                 if path and same(
                                     path, canonical_prepared.position_path)
                                 else None)
                packed_frame = pack_animation_position_frame(
                    draw, translated, mod_dir=mod_dir, buffers=buffers,
                    source=source, pack_normals=normal_possible,
                    position_data=position_data)
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
                    buffers=buffers, source=source,
                    pack_normals=normal_possible)
        if packed_frame is None or len(packed_frame.positions) != frame_bytes:
            return reject()
        if binding_frames:
            index_data, uv_data = topology
            if (len(index_data) != len(canonical_packed.indices)
                    or len(uv_data or b"") != len(canonical_packed.texcoords or b"")):
                return reject()
            indices.append(index_data)
            texcoords.append(uv_data or b"")
            topology_changed |= topology != (canonical_packed.indices, canonical_packed.texcoords)
        positions_changed = positions_changed or not _same_float32_attributes(
            packed_frame.positions, canonical_packed.positions)
        frame_bounds = (canonical_bounds if frame == start else
                        (packed_frame.bounds_min, packed_frame.bounds_max))
        if frame_bounds[0] is None or frame_bounds[1] is None:
            return reject()
        for index in range(3):
            bounds_min[index] = min(bounds_min[index], frame_bounds[0][index])
            bounds_max[index] = max(bounds_max[index], frame_bounds[1][index])
        geometry.write(
            position_ref["offset"] + frame_index * frame_bytes,
            packed_frame.positions)
        if normal_possible:
            if (packed_frame.normals is None
                    or len(packed_frame.normals) != normal_frame_bytes):
                normal_possible = False
            else:
                # Magnitude-only changes can round differently after normalization.
                normals_changed = normals_changed or (
                    packed_frame.normals != canonical_packed.normals and any(
                    abs(a[0] - b[0]) > 1e-6 for a, b in zip(
                        struct.iter_unpack("<f", packed_frame.normals),
                        struct.iter_unpack("<f", canonical_packed.normals))))
                if normal_ref is None:
                    normal_ref = geometry.reserve(frame_count * normal_frame_bytes)
                geometry.write(
                    normal_ref["offset"] + frame_index * normal_frame_bytes,
                    packed_frame.normals)
    has_normals = normal_possible and normal_ref is not None
    # Only attributes that survive publication can make a draw animate. Raw
    # record differences may belong to unused fields or unreferenced vertices.
    if not positions_changed and not (has_normals and normals_changed) and not topology_changed:
        return reject()
    if diagnostics is not None:
        diagnostics["animation_pack_seconds"] += (
            time.perf_counter() - pack_started)
        diagnostics["animation_frame_count"] += frame_count
    if not has_normals:
        if normal_ref is not None:
            geometry.truncate(normal_ref["offset"])
        normal_ref = None
    return remember({
        "track_id": family["track_id"],
        "clock_ids": list(family["clock_ids"]),
        "draws": frames,
        "positions_ref": position_ref,
        "normals_ref": normal_ref,
        "position_frame_bytes": frame_bytes,
        "normal_frame_bytes": (normal_frame_bytes if has_normals else 0),
        "indices_ref": (geometry.add(b"".join(indices)) if indices and
                        any(data != canonical_packed.indices for data in indices) else None),
        "index_frame_bytes": len(canonical_packed.indices),
        "texcoords_ref": (geometry.add(b"".join(texcoords)) if texcoords and
                          any(data != (canonical_packed.texcoords or b"") for data in texcoords) else None),
        "texcoord_frame_bytes": len(canonical_packed.texcoords or b""),
        "frame_count": frame_count,
        "frame_start": start,
        "bounds": {"min": bounds_min, "max": bounds_max},
    })

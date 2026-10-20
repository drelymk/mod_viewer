"""Compact compute-animation geometry using the build-owned blob and caches."""

import struct

from .packing import _decode_sparse_shape
from ..resource_paths import safe_resource_path


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
    if len(pose_data) < expected:
        return None
    return {
        "pose_frames": geometry.add(pose_data),
    }


def _prepare_gimi_geometry(animation, used_vertices, *, mod_dir, buffers,
                           source, geometry, shared, sparse_shape_cache):
    """Pack fixed-layout compute inputs in the draw's compact vertex order."""
    sparse = animation.get("kind") == "wwmi_sparse"
    if sparse:
        shape_entries = []
        for item in animation.get("shape_passes", ()):
            sparse_values = _decode_sparse_shape(
                item.get("sparse_shape") or {}, buffers=buffers,
                mod_dir=mod_dir, sparse_shape_cache=sparse_shape_cache,
                source=source)
            if sparse_values is None:
                return None
            deltas = bytearray(len(used_vertices) * 12)
            for output, raw_index in enumerate(used_vertices):
                values = sparse_values.get(raw_index, (0., 0., 0.))
                struct.pack_into("<3f", deltas, output * 12, *values)
            shape_entries.append({
                "deltas": geometry.add(deltas),
                "weight_operation": item.get("weight_operation"),
            })
        result = {
            "kind": "gimi_compute",
            "track_id": animation["track_id"],
            "program_id": animation.get("program_id"),
            "program": animation.get("program"),
            "shape_passes": shape_entries,
            "vertex_count": len(used_vertices),
            "position_only": True,
        }
        if animation.get("overlay"):
            result["overlay"] = True
        return result

    base_path = _gimi_path(mod_dir, animation["base_file"], source)
    pose = animation.get("pose")
    blend_path = (_gimi_path(mod_dir, pose["blend_file"], source)
                  if pose else None)
    if not base_path or (pose and not blend_path):
        return None
    base_data = buffers.raw(base_path)
    blend_data = buffers.raw(blend_path) if pose else None
    vertex_count = int(animation["vertex_count"])
    if len(base_data) % 40 or any(
            index < 0 or index >= len(base_data) // 40 for index in used_vertices):
        return None
    shape_entries = []
    base_normals = bytearray(len(used_vertices) * 12)
    for output, raw_index in enumerate(used_vertices):
        values = struct.unpack_from("<3f", base_data, raw_index * 40 + 12)
        struct.pack_into("<3f", base_normals, output * 12, *values)
    for pass_index, item in enumerate(animation.get("shape_passes", ())):
        target_path = _gimi_path(mod_dir, item["target_file"], source)
        if not target_path:
            return None
        target_data = buffers.raw(target_path)
        if len(target_data) % 40:
            return None
        limit = min(int(item["dispatch_vertices"]), vertex_count, len(target_data) // 40)
        if not any(index < limit for index in used_vertices):
            continue
        deltas = bytearray(len(used_vertices) * 24)
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
            "deltas": geometry.add(deltas),
            "weight_operation": item.get("weight_operation"),
            "program_pass": pass_index,
        })

    if not shape_entries and pose is None:
        return None
    result = {
        "kind": "gimi_compute",
        "track_id": animation["track_id"],
        "coordinate_transform": animation.get(
            "coordinate_transform", "identity"),
        "program_id": animation.get("program_id"),
        "program": animation.get("program"),
        "base_normals": geometry.add(base_normals),
        "shape_passes": shape_entries,
        "vertex_count": len(used_vertices),
    }
    if animation.get("overlay"):
        result["overlay"] = True
    if animation.get("conditions"):
        result["conditions"] = animation["conditions"]
    if pose:
        weights = bytearray(len(used_vertices) * 16)
        indices = bytearray(len(used_vertices) * 16)
        bone_count = int(pose["bone_count"])
        for output, raw_index in enumerate(used_vertices):
            offset = raw_index * 32
            values = struct.unpack_from("<4f4i", blend_data, offset)
            if any(index < 0 or index >= bone_count for index in values[4:]):
                return None
            struct.pack_into("<4f", weights, output * 16, *values[:4])
            struct.pack_into("<4i", indices, output * 16, *values[4:])
        result["pose"] = {
            "frames": shared["pose_frames"],
            "bone_count": bone_count,
            "frame_count": int(pose["frame_count"]),
            "blend": {
                "weights": geometry.add(weights),
                "indices": geometry.add(indices),
            },
        }
    else:
        result["pose"] = None
    return result

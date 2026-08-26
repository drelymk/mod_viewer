"""Per-draw geometry packing, compaction, and shape-target preparation."""

import math
import os
import struct
from dataclasses import dataclass

from .buffers import POSITION_OFFSET, BufferStore
from .conventions import GeometryConvention
from .draw_call import DrawCall
from .vertex_attributes import decode_normals
from ..resource_paths import safe_resource_path


@dataclass
class PackedShapeTarget:
    var: str
    positions: bytes
    low_positions: bytes | None = None
    mode: str | None = None


@dataclass
class PackedDrawGeometry:
    positions: bytes
    indices: bytes
    texcoords: bytes | None
    normals: bytes | None
    shape_targets: list[PackedShapeTarget]


@dataclass
class _ShapeBuffer:
    shape: dict
    target_data: bytes | dict
    target_bytes: bytearray
    sparse: bool
    low_data: bytes | None = None
    low_bytes: bytearray | None = None


def _build_shape_buffers(shape_sliders, mod_dir, effective_pos_path, used,
                         buffers, sparse_shape_cache):
    """Load and prepare dense or sparse shape targets for one draw."""
    shape_buffers = []
    for shape in shape_sliders or []:
        shape_base_path = safe_resource_path(mod_dir, shape["base_file"])
        if os.path.normcase(os.path.normpath(shape_base_path or "")) != \
                os.path.normcase(os.path.normpath(effective_pos_path)):
            continue
        if shape.get("shape_id") is not None:
            paths = tuple(safe_resource_path(mod_dir, shape[key]) for key in
                          ("offset_file", "vertex_id_file", "vertex_offset_file"))
            if not all(path and os.path.exists(path) for path in paths):
                continue
            # WWMI aligns each 127-key batch to a 128-entry container;
            # user-facing IDs omit that padding slot (SkapeKeySetter.hlsl).
            key_id = shape.get(
                "buffer_shape_id",
                shape["shape_id"] + shape["shape_id"] // 127)
            cache_key = paths + (key_id,)
            if cache_key not in sparse_shape_cache:
                offsets, vertex_ids, deltas = (buffers.raw(path) for path in paths)
                if (key_id + 2) * 4 > len(offsets):
                    continue
                begin, end = struct.unpack_from("<II", offsets, key_id * 4)
                entry_offset = shape.get("sparse_entry_offset", 0)
                begin += entry_offset
                end += entry_offset
                limit = min(end, len(vertex_ids) // 4, len(deltas) // 12)
                sparse = {}
                for index in range(begin, limit):
                    vertex_id = struct.unpack_from("<I", vertex_ids, index * 4)[0]
                    delta = struct.unpack_from("<eee", deltas, index * 12)
                    prior = sparse.get(vertex_id, (0., 0., 0.))
                    sparse[vertex_id] = tuple(
                        prior[j] + delta[j] for j in range(3))
                sparse_shape_cache[cache_key] = sparse
            shape_buffers.append(_ShapeBuffer(
                shape, sparse_shape_cache[cache_key],
                bytearray(len(used) * 12), True))
        else:
            target_path = safe_resource_path(mod_dir, shape["target_file"])
            if not target_path or not os.path.exists(target_path):
                continue
            target_data = buffers.raw(target_path)
            low_data = None
            low_bytes = None
            if shape.get("low_file"):
                low_path = safe_resource_path(mod_dir, shape["low_file"])
                if not low_path or not os.path.exists(low_path):
                    continue
                low_data = buffers.raw(low_path)
                low_bytes = bytearray(len(used) * 12)
            shape_buffers.append(_ShapeBuffer(
                shape, target_data, bytearray(len(used) * 12), False,
                low_data, low_bytes))
    return shape_buffers


def pack_draw_geometry(
    draw: DrawCall,
    group,
    *,
    mod_dir,
    default_streams,
    default_index_size,
    buffers: BufferStore,
    geometry_convention: GeometryConvention,
    sparse_shape_cache,
):
    """Pack one resolved draw into compact raw geometry bytes.

    Resource resolution, validation, triangle filtering, winding, compaction,
    authored normals, and shape targets intentionally remain one operation so
    their ordering cannot drift apart.
    """
    draw_ib_path = safe_resource_path(mod_dir, draw.ib_file)
    if not draw_ib_path or not os.path.exists(draw_ib_path):
        return None
    raw = buffers.indices(
        draw_ib_path, draw.start, draw.count,
        draw.index_size if draw.index_size is not None else default_index_size)
    if not raw:
        return None
    # DirectX resolves each index as index_buffer_value + BaseVertexLocation
    # against the vertex buffer.
    base = draw.base
    if base:
        raw = [value + base for value in raw]
    # Reject before buffer decoding, where negative offsets could be treated as
    # end-relative instead of failing safely.
    if any(index < 0 for index in raw):
        return None

    draw_pos_path = safe_resource_path(mod_dir, draw.position_file)
    draw_tc_path = safe_resource_path(mod_dir, draw.texcoord_file)
    if not (draw_pos_path and draw_tc_path
            and os.path.exists(draw_pos_path)
            and os.path.exists(draw_tc_path)):
        return None
    effective_pos_path = draw_pos_path
    draw_position_stride = (
        draw.position_stride
        if draw.position_stride is not None else default_streams.position_stride)
    draw_texcoord_stride = (
        draw.texcoord_stride
        if draw.texcoord_stride is not None else default_streams.texcoord_stride)
    if (draw_pos_path != safe_resource_path(mod_dir, group["position_file"])
            or draw_tc_path != safe_resource_path(mod_dir, group["texcoord_file"])
            or draw_position_stride != default_streams.position_stride
            or draw_texcoord_stride != default_streams.texcoord_stride):
        draw_streams = buffers.vertex_streams(
            draw_pos_path, draw_position_stride,
            draw_tc_path, draw_texcoord_stride)
    else:
        draw_streams = default_streams

    pos_data = draw_streams.position_data
    tc_data = draw_streams.texcoord_data
    uv_offset = draw_streams.uv_offset
    uv_format = draw_streams.uv_format
    uv_size = struct.calcsize(uv_format)

    def finite_vertex(index):
        pos_offset = index * draw_streams.position_stride + POSITION_OFFSET
        if pos_offset < 0 or pos_offset + 12 > len(pos_data):
            return False
        position = struct.unpack_from("<fff", pos_data, pos_offset)
        if not all(math.isfinite(value) for value in position):
            return False
        if tc_data:
            tc_offset = index * draw_streams.texcoord_stride + uv_offset
            if tc_offset < 0 or tc_offset + uv_size > len(tc_data):
                return False
            texcoord = struct.unpack_from(uv_format, tc_data, tc_offset)
            if not all(math.isfinite(value) for value in texcoord):
                return False
        return True

    valid_raw = []
    for triangle_start in range(0, len(raw) - 2, 3):
        triangle = raw[triangle_start:triangle_start + 3]
        if all(finite_vertex(index) for index in triangle):
            valid_raw.extend(triangle)
    if not valid_raw:
        return None
    raw = valid_raw
    if geometry_convention.reverse_winding:
        for triangle_start in range(0, len(raw) - 2, 3):
            raw[triangle_start + 1], raw[triangle_start + 2] = (
                raw[triangle_start + 2], raw[triangle_start + 1])

    used = sorted(set(raw))
    remap = {old: new for new, old in enumerate(used)}
    pos_bytes = bytearray(len(used) * 12)
    normal_bytes = None
    normal_source = draw.normal_source
    if normal_source is not None:
        normal_path = safe_resource_path(mod_dir, normal_source.file)
        if normal_path and os.path.exists(normal_path):
            normal_data = (pos_data if normal_path == draw_pos_path
                           else buffers.raw(normal_path))
            normal_bytes = decode_normals(normal_source, normal_data, used)

    shape_buffers = _build_shape_buffers(
        group.get("shape_sliders"), mod_dir, effective_pos_path, used,
        buffers, sparse_shape_cache)
    uv_bytes = bytearray(len(used) * 8) if tc_data else None
    for output_index, vertex_index in enumerate(used):
        pos_offset = vertex_index * draw_streams.position_stride + POSITION_OFFSET
        if pos_offset + 12 <= len(pos_data):
            x, y, z = struct.unpack_from("<fff", pos_data, pos_offset)
        else:
            x, y, z = 0., 0., 0.
        struct.pack_into("<fff", pos_bytes, output_index * 12, x, y, z)
        for item in shape_buffers:
            shape = item.shape
            if item.sparse:
                dx, dy, dz = item.target_data.get(vertex_index, (0., 0., 0.))
                tx, ty, tz = x + dx, y + dy, z + dz
            else:
                target_offset = vertex_index * shape["stride"] + POSITION_OFFSET
                if target_offset + 12 <= len(item.target_data):
                    tx, ty, tz = struct.unpack_from(
                        "<fff", item.target_data, target_offset)
                else:
                    tx, ty, tz = x, y, z
            struct.pack_into("<fff", item.target_bytes, output_index * 12,
                             tx, ty, tz)
            if item.low_data is not None:
                low_offset = vertex_index * shape["stride"] + POSITION_OFFSET
                if low_offset + 12 <= len(item.low_data):
                    lx, ly, lz = struct.unpack_from(
                        "<fff", item.low_data, low_offset)
                else:
                    lx, ly, lz = x, y, z
                struct.pack_into("<fff", item.low_bytes, output_index * 12,
                                 lx, ly, lz)
        if tc_data:
            tc_offset = vertex_index * draw_streams.texcoord_stride + uv_offset
            if tc_offset + uv_size <= len(tc_data):
                u, v = struct.unpack_from(uv_format, tc_data, tc_offset)
            else:
                u, v = 0., 0.
            struct.pack_into("<ff", uv_bytes, output_index * 8,
                             u, 1.0 - v)  # flip V for Three.js

    idx_bytes = bytearray(len(raw) * 4)
    for output_index, value in enumerate(raw):
        struct.pack_into("<I", idx_bytes, output_index * 4, remap[value])

    shape_targets = [PackedShapeTarget(
        var=item.shape["var"],
        positions=bytes(item.target_bytes),
        low_positions=bytes(item.low_bytes) if item.low_bytes is not None else None,
        mode=item.shape.get("mode"),
    ) for item in shape_buffers]
    return PackedDrawGeometry(
        positions=bytes(pos_bytes),
        indices=bytes(idx_bytes),
        texcoords=bytes(uv_bytes) if uv_bytes is not None else None,
        normals=bytes(normal_bytes) if normal_bytes is not None else None,
        shape_targets=shape_targets,
    )


__all__ = [
    "PackedShapeTarget", "PackedDrawGeometry", "pack_draw_geometry",
]

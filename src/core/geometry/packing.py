"""Per-draw geometry packing, compaction, and shape-target preparation."""

import math
import os
import struct
from dataclasses import dataclass

from .buffers import POSITION_OFFSET, POSITION_STRIDE, BufferStore, VertexStreams
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
    # Backend-only source mapping retained by the model builder for Weight.
    # This is deliberately not part of the application payload.
    used_vertices: tuple[int, ...] = ()
    bounds_min: tuple[float, float, float] | None = None
    bounds_max: tuple[float, float, float] | None = None


@dataclass
class PackedAnimationFrame:
    """Only the per-frame attributes that differ from canonical geometry."""

    positions: bytes
    normals: bytes | None
    bounds_min: tuple[float, float, float] | None = None
    bounds_max: tuple[float, float, float] | None = None


@dataclass(frozen=True)
class PreparedDrawVertices:
    """The compact source-vertex selection shared by render and skin previews."""

    raw_indices: list[int]
    used_vertices: list[int]
    remap: dict[int, int]
    decoded_vertices: dict[
        int, tuple[float, float, float, float | None, float | None] | None]
    streams: VertexStreams
    position_path: str
    texcoord_path: str


def _prepare_draw_vertices(
    draw: DrawCall,
    group,
    *,
    mod_dir,
    default_streams,
    default_index_size,
    buffers: BufferStore,
    geometry_convention: GeometryConvention,
    source=None,
):
    """Select, validate, wind, and compact the vertices used by one draw."""
    resolve = source.resolve_resource if source is not None \
        else lambda value: safe_resource_path(mod_dir, value)
    exists = source.is_file if source is not None else os.path.exists
    draw_ib_path = resolve(draw.ib_file)
    if not draw_ib_path or not exists(draw_ib_path):
        return None
    raw = buffers.indices(
        draw_ib_path, draw.start, draw.count,
        draw.index_size if draw.index_size is not None else default_index_size)
    if not raw:
        return None
    # DirectX resolves each index as index_buffer_value + BaseVertexLocation
    # against the vertex buffer.
    if draw.base:
        raw = [value + draw.base for value in raw]
    # Reject before buffer decoding, where negative offsets could be treated as
    # end-relative instead of failing safely.
    if any(index < 0 for index in raw):
        return None

    draw_pos_path = resolve(draw.position_file)
    draw_tc_path = resolve(draw.texcoord_file)
    if not (draw_pos_path and draw_tc_path
            and exists(draw_pos_path)
            and exists(draw_tc_path)):
        return None
    draw_position_stride = (
        draw.position_stride
        if draw.position_stride is not None else default_streams.position_stride)
    draw_texcoord_stride = (
        draw.texcoord_stride
        if draw.texcoord_stride is not None else default_streams.texcoord_stride)
    same = source.same_reference if source is not None \
        else lambda left, right: left == right
    if (not same(draw_pos_path, resolve(group["position_file"]))
            or not same(draw_tc_path, resolve(group["texcoord_file"]))
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
    decoded_vertices = {}
    missing = object()

    def decode_vertex(index):
        cached = decoded_vertices.get(index, missing)
        if cached is not missing:
            return cached
        pos_offset = index * draw_streams.position_stride + POSITION_OFFSET
        if pos_offset < 0 or pos_offset + 12 > len(pos_data):
            decoded_vertices[index] = None
            return None
        x, y, z = struct.unpack_from("<fff", pos_data, pos_offset)
        if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
            decoded_vertices[index] = None
            return None
        u = v = None
        if tc_data:
            tc_offset = index * draw_streams.texcoord_stride + uv_offset
            if tc_offset < 0 or tc_offset + uv_size > len(tc_data):
                decoded_vertices[index] = None
                return None
            u, v = struct.unpack_from(uv_format, tc_data, tc_offset)
            if not (math.isfinite(u) and math.isfinite(v)):
                decoded_vertices[index] = None
                return None
        decoded = (x, y, z, u, v)
        decoded_vertices[index] = decoded
        return decoded

    valid_raw = []
    append_valid = valid_raw.append
    reverse_winding = geometry_convention.reverse_winding
    for triangle_start in range(0, len(raw) - 2, 3):
        a = raw[triangle_start]
        b = raw[triangle_start + 1]
        c = raw[triangle_start + 2]
        if decode_vertex(a) is None:
            continue
        if decode_vertex(b) is None:
            continue
        if decode_vertex(c) is None:
            continue
        append_valid(a)
        if reverse_winding:
            append_valid(c)
            append_valid(b)
        else:
            append_valid(b)
            append_valid(c)
    if not valid_raw:
        return None
    raw = valid_raw

    used = sorted(set(raw))
    return PreparedDrawVertices(
        raw_indices=raw, used_vertices=used,
        remap={old: new for new, old in enumerate(used)},
        decoded_vertices=decoded_vertices,
        streams=draw_streams, position_path=draw_pos_path,
        texcoord_path=draw_tc_path)


@dataclass
class _ShapeBuffer:
    shape: dict
    target_data: bytes | dict
    target_bytes: bytearray
    sparse: bool
    low_data: bytes | None = None
    low_bytes: bytearray | None = None


def _decode_sparse_shape(shape, *, buffers, mod_dir, sparse_shape_cache,
                         source=None):
    """Decode one WWMI sparse shape into raw-vertex deltas.

    The padded key id and batch entry offset are authored by the same shape
    analysis used for ordinary sliders.  Animation packing calls this helper
    too, so both paths retain the exact WWMI id -> sparse-entry mapping.
    """
    if shape.get("shape_id") is None:
        return None
    resolve = source.resolve_resource if source is not None else \
        lambda value: safe_resource_path(mod_dir, value)
    exists = source.is_file if source is not None else os.path.exists
    try:
        paths = tuple(resolve(shape[key]) for key in
                      ("offset_file", "vertex_id_file", "vertex_offset_file"))
        key_id = shape.get(
            "buffer_shape_id",
            int(shape["shape_id"]) + int(shape["shape_id"]) // 127)
        entry_offset = int(shape.get("sparse_entry_offset", 0))
    except (KeyError, TypeError, ValueError):
        return None
    if not all(path and exists(path) for path in paths):
        return None
    cache_key = paths + (key_id, entry_offset)
    if cache_key in sparse_shape_cache:
        return sparse_shape_cache[cache_key]
    offsets, vertex_ids, deltas = (buffers.raw(path) for path in paths)
    if (key_id + 2) * 4 > len(offsets):
        return None
    begin, end = struct.unpack_from("<II", offsets, key_id * 4)
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
    return sparse


def _build_shape_buffers(shape_sliders, mod_dir, effective_pos_path, used,
                         buffers, sparse_shape_cache, source=None):
    """Load and prepare dense or sparse shape targets for one draw."""
    shape_buffers = []
    for shape in shape_sliders or []:
        resolve = source.resolve_resource if source is not None \
            else lambda value: safe_resource_path(mod_dir, value)
        exists = source.is_file if source is not None else os.path.exists
        shape_base_path = resolve(shape["base_file"])
        same = source.same_reference if source is not None \
            else lambda left, right: os.path.normcase(os.path.normpath(left or "")) == \
                os.path.normcase(os.path.normpath(right or ""))
        if not same(shape_base_path, effective_pos_path):
            continue
        if shape.get("shape_id") is not None:
            sparse = _decode_sparse_shape(
                shape, buffers=buffers, mod_dir=mod_dir,
                sparse_shape_cache=sparse_shape_cache, source=source)
            if sparse is None:
                continue
            shape_buffers.append(_ShapeBuffer(
                shape, sparse,
                bytearray(len(used) * 12), True))
        else:
            target_path = resolve(shape["target_file"])
            if not target_path or not exists(target_path):
                continue
            target_data = buffers.raw(target_path)
            low_data = None
            low_bytes = None
            if shape.get("low_file"):
                low_path = resolve(shape["low_file"])
                if not low_path or not exists(low_path):
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
    source=None,
    prepared=None,
):
    """Pack one resolved draw into compact raw geometry bytes.

    Resource resolution, validation, triangle filtering, winding, compaction,
    authored normals, and shape targets intentionally remain one operation so
    their ordering cannot drift apart.
    """
    if prepared is None:
        prepared = _prepare_draw_vertices(
            draw, group, mod_dir=mod_dir, default_streams=default_streams,
            default_index_size=default_index_size, buffers=buffers,
            geometry_convention=geometry_convention, source=source)
    if prepared is None:
        return None
    raw = prepared.raw_indices
    used = prepared.used_vertices
    remap = prepared.remap
    draw_streams = prepared.streams
    pos_data = draw_streams.position_data
    tc_data = draw_streams.texcoord_data
    pos_bytes = bytearray(len(used) * 12)
    normal_bytes = None
    normal_source = draw.normal_source
    if normal_source is not None:
        resolve = source.resolve_resource if source is not None \
            else lambda value: safe_resource_path(mod_dir, value)
        exists = source.is_file if source is not None else os.path.exists
        same = source.same_reference if source is not None \
            else lambda left, right: left == right
        normal_path = resolve(normal_source.file)
        if normal_path and exists(normal_path):
            normal_data = (pos_data if same(normal_path, prepared.position_path)
                           else buffers.raw(normal_path))
            normal_bytes = decode_normals(normal_source, normal_data, used)

    shape_buffers = _build_shape_buffers(
        group.get("shape_sliders"), mod_dir, prepared.position_path, used,
        buffers, sparse_shape_cache, source=source)
    uv_bytes = bytearray(len(used) * 8) if tc_data else None
    bounds_min = [math.inf, math.inf, math.inf]
    bounds_max = [-math.inf, -math.inf, -math.inf]
    for output_index, vertex_index in enumerate(used):
        x, y, z, u, v = prepared.decoded_vertices[vertex_index]
        struct.pack_into("<fff", pos_bytes, output_index * 12, x, y, z)
        bounds_min[0] = min(bounds_min[0], x)
        bounds_min[1] = min(bounds_min[1], y)
        bounds_min[2] = min(bounds_min[2], z)
        bounds_max[0] = max(bounds_max[0], x)
        bounds_max[1] = max(bounds_max[1], y)
        bounds_max[2] = max(bounds_max[2], z)
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
        used_vertices=tuple(used),
        bounds_min=tuple(bounds_min),
        bounds_max=tuple(bounds_max),
    )


def pack_animation_frame_attributes(
        draw: DrawCall, prepared: PreparedDrawVertices, *, mod_dir,
        buffers: BufferStore, source=None):
    """Pack one frame's positions and authored normals for a prepared draw.

    Indexes, UVs, textures, and shape targets remain owned by the canonical
    draw.  The caller validates the prepared topology before using this data.
    """
    pos_bytes = bytearray(len(prepared.used_vertices) * 12)
    bounds_min = [math.inf, math.inf, math.inf]
    bounds_max = [-math.inf, -math.inf, -math.inf]
    for output_index, vertex_index in enumerate(prepared.used_vertices):
        x, y, z, _u, _v = prepared.decoded_vertices[vertex_index]
        struct.pack_into("<fff", pos_bytes, output_index * 12, x, y, z)
        bounds_min[0] = min(bounds_min[0], x)
        bounds_min[1] = min(bounds_min[1], y)
        bounds_min[2] = min(bounds_min[2], z)
        bounds_max[0] = max(bounds_max[0], x)
        bounds_max[1] = max(bounds_max[1], y)
        bounds_max[2] = max(bounds_max[2], z)

    normal_bytes = None
    normal_source = draw.normal_source
    if normal_source is not None:
        resolve = source.resolve_resource if source is not None \
            else lambda value: safe_resource_path(mod_dir, value)
        exists = source.is_file if source is not None else os.path.exists
        same = source.same_reference if source is not None \
            else lambda left, right: left == right
        normal_path = resolve(normal_source.file)
        if normal_path and exists(normal_path):
            normal_data = (prepared.streams.position_data
                           if same(normal_path, prepared.position_path)
                           else buffers.raw(normal_path))
            normal_bytes = decode_normals(
                normal_source, normal_data, prepared.used_vertices)
    return PackedAnimationFrame(
        bytes(pos_bytes), normal_bytes,
        tuple(bounds_min), tuple(bounds_max))


def pack_animation_position_frame(
        draw: DrawCall, used_vertices, *, mod_dir, buffers: BufferStore,
        source=None):
    """Pack a frame whose only changing input is its position buffer.

    Conditional ``vb0`` animations keep the canonical index/UV mapping. Read
    the frame source transiently and extract only the canonical vertex IDs so
    frame preparation does not repeat index decoding, UV validation, or
    topology checks.
    """
    resolve = source.resolve_resource if source is not None \
        else lambda value: safe_resource_path(mod_dir, value)
    exists = source.is_file if source is not None else os.path.exists
    same = source.same_reference if source is not None \
        else lambda left, right: left == right
    position_path = resolve(draw.position_file)
    if not position_path or not exists(position_path):
        return None
    position_stride = draw.position_stride or POSITION_STRIDE
    position_data = buffers.transient(position_path)
    pos_bytes = bytearray(len(used_vertices) * 12)
    bounds_min = [math.inf, math.inf, math.inf]
    bounds_max = [-math.inf, -math.inf, -math.inf]
    for output_index, vertex_index in enumerate(used_vertices):
        offset = vertex_index * position_stride + POSITION_OFFSET
        if offset < 0 or offset + 12 > len(position_data):
            return None
        x, y, z = struct.unpack_from("<fff", position_data, offset)
        if not all(math.isfinite(value) for value in (x, y, z)):
            return None
        struct.pack_into("<fff", pos_bytes, output_index * 12, x, y, z)
        bounds_min[0] = min(bounds_min[0], x)
        bounds_min[1] = min(bounds_min[1], y)
        bounds_min[2] = min(bounds_min[2], z)
        bounds_max[0] = max(bounds_max[0], x)
        bounds_max[1] = max(bounds_max[1], y)
        bounds_max[2] = max(bounds_max[2], z)

    normal_bytes = None
    normal_source = draw.normal_source
    if normal_source is not None:
        normal_path = resolve(normal_source.file)
        if normal_path and exists(normal_path):
            normal_data = (position_data if same(normal_path, position_path)
                           else buffers.transient(normal_path))
            normal_bytes = decode_normals(
                normal_source, normal_data, used_vertices)
    return PackedAnimationFrame(
        bytes(pos_bytes), normal_bytes,
        tuple(bounds_min), tuple(bounds_max))


__all__ = [
    "PackedShapeTarget", "PackedDrawGeometry", "PackedAnimationFrame",
    "PreparedDrawVertices", "_prepare_draw_vertices", "pack_draw_geometry",
    "pack_animation_frame_attributes", "pack_animation_position_frame",
]

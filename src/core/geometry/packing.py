"""Per-draw geometry packing, compaction, and shape-target preparation."""

import math
import os
import struct
from dataclasses import dataclass

from .buffers import (
    POSITION_OFFSET, POSITION_STRIDE, BufferStore, VertexStreams, _detect_uv_best,
)
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
    zero_out_of_bounds: bool = False


def _resolve_vertex_streams(draw, group, *, mod_dir, default_streams, buffers,
                            source=None):
    """Resolve one draw's streams for both packing implementations."""
    resolve = source.resolve_resource if source is not None \
        else lambda value: safe_resource_path(mod_dir, value)
    exists = source.is_file if source is not None else os.path.exists
    draw_pos_path = resolve(draw.position_file)
    draw_tc_path = resolve(draw.texcoord_file)
    if not (draw_pos_path and draw_tc_path
            and exists(draw_pos_path) and exists(draw_tc_path)):
        return None
    position_stride = (draw.position_stride if draw.position_stride is not None
                       else default_streams.position_stride)
    texcoord_stride = (draw.texcoord_stride if draw.texcoord_stride is not None
                       else default_streams.texcoord_stride)
    same = source.same_reference if source is not None \
        else lambda left, right: left == right
    if (not same(draw_pos_path, resolve(group["position_file"]))
            or not same(draw_tc_path, resolve(group["texcoord_file"]))
            or position_stride != default_streams.position_stride
            or texcoord_stride != default_streams.texcoord_stride):
        streams = buffers.vertex_streams(
            draw_pos_path, position_stride, draw_tc_path, texcoord_stride)
    else:
        streams = default_streams
    return streams, draw_pos_path, draw_tc_path


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
    zero_out_of_bounds=False,
    vertex_union=None,
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
    # Buffer indices are unsigned; only a base offset can make them negative.
    # Reject before decoding can treat negative offsets as end-relative.
    if draw.base and any(index < 0 for index in raw):
        return None

    resolved = _resolve_vertex_streams(
        draw, group, mod_dir=mod_dir, default_streams=default_streams,
        buffers=buffers, source=source)
    if resolved is None:
        return None
    draw_streams, draw_pos_path, draw_tc_path = resolved

    pos_data = draw_streams.position_data
    tc_data = draw_streams.texcoord_data
    position_stride = draw_streams.position_stride
    texcoord_stride = draw_streams.texcoord_stride
    pos_size = len(pos_data)
    tc_size = len(tc_data) if tc_data else 0
    uv_offset = draw_streams.uv_offset
    position_unpack = struct.Struct("<fff").unpack_from
    uv_struct = struct.Struct(draw_streams.uv_format)
    uv_unpack = uv_struct.unpack_from
    uv_size = uv_struct.size
    isfinite = math.isfinite
    decoded_vertices = {}
    missing = object()
    referenced = set(raw) if vertex_union is not None else None

    def decode_vertex(index):
        cached = decoded_vertices.get(index, missing)
        if cached is not missing:
            return cached
        pos_offset = index * position_stride + POSITION_OFFSET
        if pos_offset < 0:
            decoded_vertices[index] = None
            return None
        if pos_offset + 12 > pos_size:
            if not zero_out_of_bounds:
                decoded_vertices[index] = None
                return None
            x = y = z = 0.
        else:
            x, y, z = position_unpack(pos_data, pos_offset)
        if not (isfinite(x) and isfinite(y) and isfinite(z)):
            decoded_vertices[index] = None
            return None
        u = v = None
        if tc_data:
            tc_offset = index * texcoord_stride + uv_offset
            if tc_offset < 0 or tc_offset + uv_size > tc_size:
                if referenced is None or index in referenced:
                    decoded_vertices[index] = None
                    return None
                u, v = 0., 1.
            else:
                u, v = uv_unpack(tc_data, tc_offset)
            if not (isfinite(u) and isfinite(v)):
                decoded_vertices[index] = None
                return None
        decoded = (x, y, z, u, v)
        decoded_vertices[index] = decoded
        return decoded

    valid_raw = []
    append_valid = valid_raw.append
    reverse_winding = geometry_convention.reverse_winding
    decode = decode_vertex
    cached = decoded_vertices.get
    indices = iter(raw)
    for a, b, c in zip(indices, indices, indices):
        if cached(a) is None and decode(a) is None:
            continue
        if cached(b) is None and decode(b) is None:
            continue
        if cached(c) is None and decode(c) is None:
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

    used = sorted(set(raw) if vertex_union is None else vertex_union)
    # Ordinary draws already validated every retained vertex in the loop above.
    if vertex_union is not None and any(
            decode_vertex(vertex) is None for vertex in used):
        return None
    return PreparedDrawVertices(
        raw_indices=raw, used_vertices=used,
        remap={old: new for new, old in enumerate(used)},
        decoded_vertices=decoded_vertices,
        streams=draw_streams, position_path=draw_pos_path,
        texcoord_path=draw_tc_path, zero_out_of_bounds=zero_out_of_bounds)


@dataclass
class _ShapeBuffer:
    shape: dict
    target_data: bytes | dict
    sparse: bool
    low_data: bytes | None = None
    target_bytes: bytearray | None = None
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


def _build_shape_buffers(shape_sliders, mod_dir, effective_pos_path,
                         buffers, sparse_shape_cache, source=None):
    """Load shape inputs; each packer owns its output allocation."""
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
            shape_buffers.append(_ShapeBuffer(shape, sparse, True))
        else:
            target_path = resolve(shape["target_file"])
            if not target_path or not exists(target_path):
                continue
            target_data = buffers.raw(target_path)
            low_data = None
            if shape.get("low_file"):
                low_path = resolve(shape["low_file"])
                if not low_path or not exists(low_path):
                    continue
                low_data = buffers.raw(low_path)
            shape_buffers.append(_ShapeBuffer(
                shape, target_data, False, low_data))
    return shape_buffers


def pack_draw_geometry(
    draw: DrawCall, group, *, mod_dir, default_streams, default_index_size,
    buffers: BufferStore, geometry_convention: GeometryConvention,
    sparse_shape_cache, source=None, prepared=None,
):
    """Pack ordinary static draws in batches, retaining the conservative path."""
    options = dict(
        mod_dir=mod_dir, default_streams=default_streams,
        default_index_size=default_index_size, buffers=buffers,
        geometry_convention=geometry_convention,
        sparse_shape_cache=sparse_shape_cache, source=source)
    if prepared is None:
        from .batch_packing import try_pack_draw_geometry
        packed = try_pack_draw_geometry(draw, group, **options)
        if packed is not None:
            return packed
    return _pack_draw_geometry_python(draw, group, prepared=prepared, **options)


def _pack_draw_geometry_python(
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
            normal_bytes = decode_normals(
                normal_source, normal_data, used,
                zero_out_of_bounds=prepared.zero_out_of_bounds)

    shape_buffers = _build_shape_buffers(
        group.get("shape_sliders"), mod_dir, prepared.position_path,
        buffers, sparse_shape_cache, source=source)
    for item in shape_buffers:
        item.target_bytes = bytearray(len(used) * 12)
        if item.low_data is not None:
            item.low_bytes = bytearray(len(used) * 12)
    uv_bytes = bytearray(len(used) * 8) if tc_data else None
    bounds_min = [math.inf, math.inf, math.inf]
    bounds_max = [-math.inf, -math.inf, -math.inf]
    position_struct = struct.Struct("<fff")
    uv_struct = struct.Struct("<ff")
    position_pack = position_struct.pack_into
    position_unpack = position_struct.unpack_from
    uv_pack = uv_struct.pack_into
    decoded_vertices = prepared.decoded_vertices
    # Shape layouts and buffer sizes stay fixed while packing this draw.
    shape_inputs = [
        (item, 0 if item.sparse else item.shape["stride"],
         len(item.target_data), len(item.low_data) if item.low_data is not None else 0)
        for item in shape_buffers]
    for output_index, vertex_index in enumerate(used):
        position_offset = output_index * 12
        x, y, z, u, v = decoded_vertices[vertex_index]
        position_pack(pos_bytes, position_offset, x, y, z)
        if x < bounds_min[0]:
            bounds_min[0] = x
        if y < bounds_min[1]:
            bounds_min[1] = y
        if z < bounds_min[2]:
            bounds_min[2] = z
        if x > bounds_max[0]:
            bounds_max[0] = x
        if y > bounds_max[1]:
            bounds_max[1] = y
        if z > bounds_max[2]:
            bounds_max[2] = z
        for item, shape_stride, target_size, low_size in shape_inputs:
            if item.sparse:
                dx, dy, dz = item.target_data.get(vertex_index, (0., 0., 0.))
                tx, ty, tz = x + dx, y + dy, z + dz
            else:
                target_offset = vertex_index * shape_stride + POSITION_OFFSET
                if target_offset + 12 <= target_size:
                    tx, ty, tz = position_unpack(
                        item.target_data, target_offset)
                else:
                    tx, ty, tz = x, y, z
            position_pack(
                item.target_bytes, position_offset, tx, ty, tz)
            if item.low_data is not None:
                low_offset = vertex_index * shape_stride + POSITION_OFFSET
                if low_offset + 12 <= low_size:
                    lx, ly, lz = position_unpack(
                        item.low_data, low_offset)
                else:
                    lx, ly, lz = x, y, z
                position_pack(
                    item.low_bytes, position_offset, lx, ly, lz)
        if tc_data:
            uv_pack(
                uv_bytes, output_index * 8, u, 1.0 - v)  # flip V for Three.js

    idx_bytes = bytearray(len(raw) * 4)
    # Bulk packing removes one Python call per index while bounding the
    # temporary argument tuple for unusually large draws.
    chunk_size = 65536
    for start in range(0, len(raw), chunk_size):
        chunk = raw[start:start + chunk_size]
        struct.pack_into(
            f"<{len(chunk)}I", idx_bytes, start * 4,
            *(remap[value] for value in chunk))

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
        buffers: BufferStore, source=None, pack_normals=True):
    """Pack one frame's positions and authored normals for a prepared draw.

    Indexes, UVs, textures, and shape targets remain owned by the canonical
    draw.  The caller validates the prepared topology before using this data.
    """
    packed = _pack_animation_positions(
        prepared.streams.position_data, prepared.streams.position_stride,
        prepared.used_vertices)
    if packed is None:
        return None
    pos_bytes, bounds_min, bounds_max = packed

    normal_bytes = None
    normal_source = draw.normal_source
    if pack_normals and normal_source is not None:
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
        pos_bytes, normal_bytes, bounds_min, bounds_max)


def _pack_animation_positions(data, stride, used_vertices):
    """Pack finite positions with Direct3D zero reads beyond the buffer end."""
    count = len(used_vertices)
    bounds_min = [math.inf, math.inf, math.inf]
    bounds_max = [-math.inf, -math.inf, -math.inf]
    direct = (stride == 12 and count > 0
              and used_vertices[-1] - used_vertices[0] + 1 == count
              and used_vertices[0] >= 0
              and (used_vertices[-1] + 1) * 12 <= len(data))
    if direct:
        begin = used_vertices[0] * 12
        end = begin + count * 12
        output = data[begin:end]
        positions = struct.iter_unpack("<fff", output)
    else:
        output = bytearray(count * 12)
        positions = None
    for output_index in range(count):
        if direct:
            x, y, z = next(positions)
        else:
            offset = used_vertices[output_index] * stride + POSITION_OFFSET
            if offset < 0:
                return None
            if offset + 12 > len(data):
                x = y = z = 0.
            else:
                x, y, z = struct.unpack_from("<fff", data, offset)
        if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
            return None
        if not direct:
            struct.pack_into("<fff", output, output_index * 12, x, y, z)
        if x < bounds_min[0]:
            bounds_min[0] = x
        if y < bounds_min[1]:
            bounds_min[1] = y
        if z < bounds_min[2]:
            bounds_min[2] = z
        if x > bounds_max[0]:
            bounds_max[0] = x
        if y > bounds_max[1]:
            bounds_max[1] = y
        if z > bounds_max[2]:
            bounds_max[2] = z
    return bytes(output), tuple(bounds_min), tuple(bounds_max)


def pack_animation_position_frame(
        draw: DrawCall, used_vertices, *, mod_dir, buffers: BufferStore,
        source=None, pack_normals=True, position_data=None):
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
    if position_data is None:
        position_data = buffers.transient(position_path)
    packed = _pack_animation_positions(
        position_data, position_stride, used_vertices)
    if packed is None:
        return None
    pos_bytes, bounds_min, bounds_max = packed

    normal_bytes = None
    normal_source = draw.normal_source
    if pack_normals and normal_source is not None:
        normal_path = resolve(normal_source.file)
        if normal_path and exists(normal_path):
            normal_data = (position_data if same(normal_path, position_path)
                           else buffers.transient(normal_path))
            normal_bytes = decode_normals(
                normal_source, normal_data, used_vertices, zero_out_of_bounds=True)
    return PackedAnimationFrame(
        pos_bytes, normal_bytes, bounds_min, bounds_max)


def pack_animation_topology_frame(draw, canonical, *, canonical_packed,
                                   mod_dir, buffers, raw_indices, cache, source=None,
                                   vertex_offset=0):
    """Pack IB/UV blocks after union validation and canonical preparation."""
    resolve = source.resolve_resource if source is not None else \
        lambda value: safe_resource_path(mod_dir, value)
    exists = source.is_file if source is not None else os.path.exists
    tc_path = resolve(draw.texcoord_file)
    if not tc_path or not exists(tc_path):
        return None
    raw = raw_indices
    index_key = ("indices", resolve(draw.ib_file), draw.start, draw.count,
                 draw.base, draw.index_size, vertex_offset)
    index_bytes = cache.get(index_key)
    if index_bytes is None:
        if raw == canonical.raw_indices:
            index_bytes = canonical_packed.indices
        else:
            output = bytearray(len(raw) * 4)
            for begin in range(0, len(raw), 65536):
                chunk = raw[begin:begin + 65536]
                struct.pack_into(f"<{len(chunk)}I", output, begin * 4,
                                 *(canonical.remap[value] for value in chunk))
            index_bytes = bytes(output)
        cache[index_key] = index_bytes
    stride = draw.texcoord_stride or canonical.streams.texcoord_stride
    uv_key = ("uv", tc_path, stride, index_key)
    if uv_key not in cache:
        data = buffers.raw(tc_path)
        if (not vertex_offset and data == canonical.streams.texcoord_data
                and stride == canonical.streams.texcoord_stride):
            if data and (max(raw) * stride + canonical.streams.uv_offset
                         + struct.calcsize(canonical.streams.uv_format) > len(data)):
                return None
            uv_bytes = canonical_packed.texcoords
        else:
            if data == canonical.streams.texcoord_data and stride == canonical.streams.texcoord_stride:
                uv_offset, uv_format = canonical.streams.uv_offset, canonical.streams.uv_format
            else:
                uv_offset, uv_format = _detect_uv_best(tc_path, stride, data=data)
            uv_struct = struct.Struct(uv_format)
            output = bytearray(len(canonical.used_vertices) * 8) if data else None
            referenced = set(raw)
            if output is not None:
                for index, vertex in enumerate(canonical.used_vertices):
                    offset = (vertex + vertex_offset) * stride + uv_offset
                    if offset < 0 or offset + uv_struct.size > len(data):
                        if vertex in referenced:
                            return None
                        u, v = 0., 1.
                    else:
                        u, v = uv_struct.unpack_from(data, offset)
                    if not (math.isfinite(u) and math.isfinite(v)):
                        return None
                    struct.pack_into("<ff", output, index * 8, u, 1. - v)
            uv_bytes = bytes(output) if output is not None else None
        cache[uv_key] = uv_bytes
    return index_bytes, cache[uv_key]


__all__ = [
    "PackedShapeTarget", "PackedDrawGeometry", "PackedAnimationFrame",
    "PreparedDrawVertices", "_prepare_draw_vertices", "pack_draw_geometry",
    "pack_animation_frame_attributes", "pack_animation_position_frame",
    "pack_animation_topology_frame",
]

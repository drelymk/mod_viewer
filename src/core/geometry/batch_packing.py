"""Bounded array packing for ordinary static draws; other inputs defer to Python."""

import os

from ..resource_paths import safe_resource_path
from . import packing
from .array_views import UnsupportedLayout, strided_view


_MIN_BATCH_INDICES = 4096
_MAX_BATCH_INDICES = 1048576


def _endpoint(np, data, stride, used, positions):
    view = strided_view(np, data, stride=stride, width=3, dtype="<f4",
                        offset=packing.POSITION_OFFSET)
    output = positions.copy()
    present = used < len(view)
    output[present] = view[used[present]]
    # Keep unusual endpoint values on the existing scalar conversion path.
    if not np.isfinite(output).all():
        raise UnsupportedLayout
    return output.tobytes()


def try_pack_draw_geometry(
        draw, group, *, mod_dir, default_streams, default_index_size, buffers,
        geometry_convention, sparse_shape_cache, source=None):
    """Return packed geometry, or None when the existing path is required.

    Selection remains per draw and uses source vertex IDs, never position-based
    welding. Array working sets are bounded by the authored index range.
    """
    if group.get("_compute_animation") or any(
            shape.get("shape_id") is not None
            for shape in group.get("shape_sliders", ())):
        return None
    if any(not isinstance(shape.get("stride"), int) or shape["stride"] < 12
           for shape in group.get("shape_sliders", ())):
        return None
    index_size = (draw.index_size if draw.index_size is not None
                  else default_index_size)
    if (index_size not in (2, 4) or draw.start < 0
            or not -(2 ** 31) <= draw.base < 2 ** 31):
        return None
    resolve = source.resolve_resource if source is not None \
        else lambda value: safe_resource_path(mod_dir, value)
    exists = source.is_file if source is not None else os.path.exists
    index_path = resolve(draw.ib_file)
    if not index_path or not exists(index_path):
        return None
    data = buffers.raw(index_path)
    available = len(data) // index_size - draw.start
    count = available if draw.count is None else min(draw.count, available)
    if count <= 0 or not _MIN_BATCH_INDICES <= count <= _MAX_BATCH_INDICES:
        return None

    # Import only when a draw is large enough to benefit from array setup.
    import numpy as np

    indices = np.frombuffer(
        data, dtype="<u2" if index_size == 2 else "<u4",
        count=count, offset=draw.start * index_size).astype(np.int64)
    indices += draw.base
    # Check the entire authored range before ignoring incomplete triangles.
    if np.any(indices < 0):
        return None
    indices = indices[:len(indices) // 3 * 3]
    if not len(indices):
        return None
    used, inverse = np.unique(indices, return_inverse=True)
    resolved = packing._resolve_vertex_streams(
        draw, group, mod_dir=mod_dir, default_streams=default_streams,
        buffers=buffers, source=source)
    if resolved is None:
        return None
    streams, position_path, _texcoord_path = resolved
    try:
        positions_view = strided_view(
            np, streams.position_data, stride=streams.position_stride,
            width=3, dtype="<f4", offset=packing.POSITION_OFFSET)
        if used[-1] >= len(positions_view):
            return None
        positions = positions_view[used]
        if not np.isfinite(positions).all():
            return None
        uv_bytes = None
        if streams.texcoord_data:
            if streams.uv_format not in ("<ee", "<ff"):
                return None
            uv_view = strided_view(
                np, streams.texcoord_data, stride=streams.texcoord_stride,
                width=2, dtype="<f2" if streams.uv_format == "<ee" else "<f4",
                offset=streams.uv_offset)
            if used[-1] >= len(uv_view):
                return None
            uvs = uv_view[used]
            if not np.isfinite(uvs).all():
                return None
            uv_output = np.empty((len(used), 2), dtype="<f4")
            uv_output[:, 0] = uvs[:, 0]
            # Scalar packing subtracts in Python Float64 before writing Float32.
            uv_output[:, 1] = 1.0 - uvs[:, 1].astype(np.float64)
            uv_bytes = uv_output.tobytes()

        used_list = used.tolist()
        shape_buffers = packing._build_shape_buffers(
            group.get("shape_sliders"), mod_dir, position_path,
            buffers, sparse_shape_cache, source=source)
        shapes = [packing.PackedShapeTarget(
            item.shape["var"],
            _endpoint(np, item.target_data, item.shape["stride"], used, positions),
            _endpoint(np, item.low_data, item.shape["stride"], used, positions)
            if item.low_data is not None else None,
            item.shape.get("mode")) for item in shape_buffers]
    except UnsupportedLayout:
        return None

    normal_bytes = None
    if draw.normal_source is not None:
        normal_path = resolve(draw.normal_source.file)
        if normal_path and exists(normal_path):
            same = source.same_reference if source is not None \
                else lambda left, right: left == right
            normal_data = (streams.position_data if same(normal_path, position_path)
                           else buffers.raw(normal_path))
            normal_bytes = packing.decode_normals(
                draw.normal_source, normal_data, used_list)
    triangles = inverse.reshape(-1, 3)
    if geometry_convention.reverse_winding:
        triangles = triangles[:, [0, 2, 1]]
    # argmin/argmax retain the first equal value, including signed zero ties.
    axes = np.arange(3)
    bounds_min = tuple(float(value) for value in
                       positions[positions.argmin(axis=0), axes])
    bounds_max = tuple(float(value) for value in
                       positions[positions.argmax(axis=0), axes])
    return packing.PackedDrawGeometry(
        positions.tobytes(), triangles.astype("<u4").tobytes(), uv_bytes,
        bytes(normal_bytes) if normal_bytes is not None else None, shapes,
        tuple(used_list), bounds_min, bounds_max)

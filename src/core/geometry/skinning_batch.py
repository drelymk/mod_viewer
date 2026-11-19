"""Array decoding for large Blend streams; callers exclude VertexVG remaps.

Results match the scalar decoder byte for byte: the same per-vertex failure
classes, Float32 weights, first-seen bone statistics order, and ``sum()``
rounding. Inputs that the scalar path rejects or treats specially return None
so that it can handle them, including its exceptions.
"""

from .array_views import python_float_sum


_LAYOUTS = {
    # encoding: (index dtype, index offset, value dtype, value offset, scale)
    "gimi_f32_u32_4": ("<u4", 16, "<f4", 0, None),
    "wwmi_u8_4": ("u1", 0, "u1", 4, 255.0),
    "wwmi_u8_8": ("u1", 0, "u1", 8, 255.0),
    "wwmi_u16_8": ("<u2", 0, "<u2", 16, 65535.0),
    "rigid_u32_1": ("<u4", 0, None, None, None),
}


def decode_skinning_batch(source, raw_data, used_vertices):
    """Return keyword parts for ``_decoded_skinning``, or None to use scalars."""
    layout = _LAYOUTS.get(source.encoding)
    # Other sequences stay indexed by the scalar path rather than iterated.
    if layout is None or not isinstance(used_vertices, (tuple, list)):
        return None
    import numpy as np

    try:
        used = np.array(used_vertices, dtype=np.int64)
    except (TypeError, ValueError, OverflowError):
        return None
    count = len(used)
    influences = source.influence_count
    stride = source.stride
    index_dtype, index_offset, value_dtype, value_offset, scale = layout

    records = len(raw_data) // stride
    rows = np.flatnonzero((used >= 0) & (used < records))
    table = np.frombuffer(raw_data, dtype=np.uint8, count=records * stride)
    table = table.reshape(records, stride)[used[rows]]

    def field(dtype, offset):
        width = np.dtype(dtype).itemsize * influences
        return np.ascontiguousarray(table[:, offset:offset + width]).view(dtype)

    raw_bones = field(index_dtype, index_offset).astype(np.int64)
    if value_dtype is None:
        values = np.ones((len(rows), influences), dtype=np.float64)
    else:
        values = field(value_dtype, value_offset).astype(np.float64)
        if scale is not None:
            values = values / scale

    valid = (np.isfinite(values) & (values >= 0)).all(axis=1)
    valid_rows = rows[valid]
    values = values[valid]
    bones = raw_bones[valid]
    if not (source.encoding == "wwmi_u16_8"
            or source.bone_id_namespace == "wwmi_merged"):
        bones = bones + int(source.bone_id_offset)
    if len(bones) and (int(bones.min()) < 0 or int(bones.max()) >= 2 ** 32):
        return None

    index_array = np.zeros((count, influences), dtype="<u4")
    weight_array = np.zeros((count, influences), dtype="<f4")
    index_array[valid_rows] = bones
    weight_array[valid_rows] = values

    sums = python_float_sum(np, [values[:, influence] for influence in range(influences)])

    positive = values > 0
    weighted_bones = bones[positive]
    weighted_values = values[positive]
    weighted_rows = np.broadcast_to(valid_rows[:, None], bones.shape)[positive]
    bone_stats = {}
    bone_ids = set()
    if len(weighted_bones):
        unique, first, inverse = np.unique(
            weighted_bones, return_index=True, return_inverse=True)
        totals = np.bincount(inverse, weights=weighted_values, minlength=len(unique))
        pairs = np.unique(inverse * np.int64(count) + weighted_rows)
        affected = np.bincount(pairs // count, minlength=len(unique))
        for slot in np.argsort(first, kind="stable").tolist():
            bone_stats[int(unique[slot])] = {
                "affected_vertex_count": int(affected[slot]),
                "total_weight": float(totals[slot]),
            }
        bone_ids = set(bone_stats)

    return {
        "index_bytes": index_array.tobytes(),
        "weight_bytes": weight_array.tobytes(),
        "bone_ids": bone_ids,
        "bone_stats": bone_stats,
        "invalid": int(len(rows) - len(valid_rows)),
        "zero_weight": int(np.count_nonzero(sums <= 0)),
        "truncated": int(count - len(rows)),
        "out_of_range": int(count - len(rows)),
        "vertex_vg_truncated": 0,
        "min_weight_sum": float(sums.min()) if len(sums) else 0.0,
        "max_weight_sum": float(sums.max()) if len(sums) else 0.0,
    }

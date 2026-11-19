"""Match mod vertex positions to reference vertices by unique nearest point.

A reference point maps to its index, or ``-1`` when several reference
vertices share it. A query takes the reference point with the smallest squared
distance below ``MATCH_DISTANCE`` in its grid cell and the 26 surrounding cells.
It is unmapped when that minimum is tied, absent, or non-finite. Large inputs
use bounded array batches. Small or unusual inputs use the scalar path, which
gives the same results.
"""

import math
import struct
from itertools import product


CELL_SCALE = 10000
MATCH_DISTANCE = 1e-8
_NEIGHBOR_DELTAS = tuple(product((-1, 0, 1), repeat=3))
_MIN_BATCH_VERTICES = 2048
_QUERY_CHUNK = 16384
# Cell coordinates stay exactly representable in Float64 and packed keys in int64.
_MAX_CELL = 2 ** 52
_MAX_KEY_SPAN = 2 ** 62


def match_positions(buckets, positions, cache):
    """Return one reference index (or -1) per packed ``<3f`` position.

    ``cache`` keeps the reference's sorted arrays between meshes.
    """
    if len(positions) // 12 >= _MIN_BATCH_VERTICES:
        result = _match_batch(buckets, positions, cache)
        if result is not None:
            return result
    return _match_scalar(buckets, positions)


def _match_scalar(buckets, positions):
    vertices = []
    for point in struct.iter_unpack("<3f", positions):
        if not all(math.isfinite(value) for value in point):
            vertices.append(-1)
            continue
        cell = tuple(math.floor(value * CELL_SCALE) for value in point)
        exact = buckets.get(cell, {}).get(point)
        if exact is not None:
            vertices.append(exact)
            continue
        x, y, z = point
        best, distance = -1, MATCH_DISTANCE
        for dx, dy, dz in _NEIGHBOR_DELTAS:
            neighbor = buckets.get((cell[0] + dx, cell[1] + dy, cell[2] + dz))
            if not neighbor:
                continue
            for (rx, ry, rz), index in neighbor.items():
                ex, ey, ez = x - rx, y - ry, z - rz
                squared = ex * ex + ey * ey + ez * ez
                if squared < distance:
                    best, distance = index, squared
                elif squared == distance:
                    best = -1
        vertices.append(best)
    return vertices


def _bucket_arrays(np, buckets):
    """Sorted reference arrays keyed by packed cell, or None when unpackable."""
    cells, points, indices = [], [], []
    for cell, bucket in buckets.items():
        for point, index in bucket.items():
            cells.append(cell)
            points.append(point)
            indices.append(index)
    if not cells:
        return None
    if any(abs(value) >= _MAX_CELL for cell in cells for value in cell):
        return None
    cells = np.array(cells, dtype=np.int64)
    low = cells.min(axis=0) - 1
    span = cells.max(axis=0) - low + 2
    if int(span[0]) * int(span[1]) * int(span[2]) >= _MAX_KEY_SPAN:
        return None
    keys = _pack(cells, low, span)
    order = np.argsort(keys, kind="stable")
    return {
        "low": low,
        "high": low + span,
        "span": span,
        "keys": keys[order],
        "points": np.array(points, dtype=np.float64)[order],
        "indices": np.array(indices, dtype=np.int64)[order],
    }


def _pack(cells, low, span):
    shifted = cells - low
    return (shifted[:, 0] * span[1] + shifted[:, 1]) * span[2] + shifted[:, 2]


def _match_batch(buckets, positions, cache):
    import numpy as np

    if "bucket_arrays" not in cache:
        cache["bucket_arrays"] = _bucket_arrays(np, buckets)
    arrays = cache["bucket_arrays"]
    if arrays is None:
        return None
    queries = np.frombuffer(positions, dtype="<f4", count=len(positions) // 12 * 3)
    queries = queries.reshape(-1, 3).astype(np.float64)
    finite = np.isfinite(queries).all(axis=1)
    scaled = np.where(finite[:, None], queries * CELL_SCALE, 0.0)
    if len(scaled) and float(np.abs(scaled).max()) >= _MAX_CELL:
        return None
    cells = np.floor(scaled).astype(np.int64)
    result = np.full(len(queries), -1, dtype=np.int64)
    for start in range(0, len(queries), _QUERY_CHUNK):
        stop = min(start + _QUERY_CHUNK, len(queries))
        _match_chunk(np, arrays, queries[start:stop], cells[start:stop],
                     finite[start:stop], result[start:stop])
    return result.tolist()


def _match_chunk(np, arrays, queries, cells, finite, result):
    keys, points = arrays["keys"], arrays["points"]
    query_ids, distances, matches = [], [], []
    for delta in _NEIGHBOR_DELTAS:
        neighbor = cells + np.array(delta, dtype=np.int64)
        inside = finite & ((neighbor >= arrays["low"]) & (neighbor < arrays["high"])).all(axis=1)
        rows = np.flatnonzero(inside)
        if not len(rows):
            continue
        packed = _pack(neighbor[rows], arrays["low"], arrays["span"])
        first = np.searchsorted(keys, packed, side="left")
        counts = np.searchsorted(keys, packed, side="right") - first
        present = counts > 0
        rows, first, counts = rows[present], first[present], counts[present]
        if not len(rows):
            continue
        total = int(counts.sum())
        owners = np.repeat(rows, counts)
        steps = np.arange(total, dtype=np.int64) - np.repeat(np.cumsum(counts) - counts, counts)
        candidates = np.repeat(first, counts) + steps
        difference = queries[owners] - points[candidates]
        # Same association as the scalar ``ex * ex + ey * ey + ez * ez``.
        squared = (difference[:, 0] * difference[:, 0]
                   + difference[:, 1] * difference[:, 1]
                   + difference[:, 2] * difference[:, 2])
        near = squared < MATCH_DISTANCE
        query_ids.append(owners[near])
        distances.append(squared[near])
        matches.append(arrays["indices"][candidates[near]])
    if not query_ids:
        return
    query_ids = np.concatenate(query_ids)
    if not len(query_ids):
        return
    distances = np.concatenate(distances)
    matches = np.concatenate(matches)
    order = np.lexsort((distances, query_ids))
    query_ids, distances, matches = query_ids[order], distances[order], matches[order]
    leaders = np.flatnonzero(np.r_[True, query_ids[1:] != query_ids[:-1]])
    following = leaders + 1
    tied = np.zeros(len(leaders), dtype=bool)
    has_next = following < len(query_ids)
    tied[has_next] = ((query_ids[following[has_next]] == query_ids[leaders[has_next]])
                      & (distances[following[has_next]] == distances[leaders[has_next]]))
    result[query_ids[leaders]] = np.where(tied, -1, matches[leaders])

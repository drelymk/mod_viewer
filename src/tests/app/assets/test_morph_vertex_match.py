"""Nearest-reference vertex matching for morph bindings."""

import math
import random
import struct

import pytest

from app.assets import morph_vertex_match as match


def _buckets(points):
    """Build reference buckets the way the rig loader does."""
    buckets = {}
    for index, point in enumerate(points):
        point = struct.unpack("<3f", struct.pack("<3f", *point))
        cell = tuple(math.floor(value * match.CELL_SCALE) for value in point)
        bucket = buckets.setdefault(cell, {})
        bucket[point] = -1 if point in bucket else index
    return buckets


def _pack(points):
    return b"".join(struct.pack("<3f", *point) for point in points)


REFERENCE = [
    (0., 0., 0.),          # 0: exact target
    (.5, .5, .5),          # 1: duplicated below
    (.5, .5, .5),          # 2
    (1., 0., 0.),          # 3: Float32-exact pair tied at their midpoint
    (1. + 2 ** -13, 0., 0.),  # 4
    (2.00009, 0., 0.),     # 5: nearest across a cell boundary
]


@pytest.mark.parametrize("query,expected", [
    ((0., 0., 0.), 0),
    ((-0., 0., 0.), 0),
    ((.5, .5, .5), -1),
    ((1. + 2 ** -14, 0., 0.), -1),
    ((2.000105, 0., 0.), 5),
    ((0., 0., .0002), -1),
    ((math.nan, 0., 0.), -1),
    ((math.inf, 0., 0.), -1),
], ids=["exact", "negative-zero", "duplicate", "tie", "neighbor-cell",
        "too-far", "nan", "infinite"])
def test_scalar_and_batch_matching_agree_on_contract_cases(query, expected):
    buckets = _buckets(REFERENCE)
    positions = _pack([query] * match._MIN_BATCH_VERTICES)

    assert match._match_scalar(buckets, positions)[0] == expected
    assert match._match_batch(buckets, positions, {}) == [expected] * match._MIN_BATCH_VERTICES


@pytest.mark.parametrize("occupancy", [True, False], ids=["occupancy-filter", "full-search"])
def test_batch_matching_matches_scalar_on_jittered_symmetric_mesh(monkeypatch, occupancy):
    if not occupancy:
        monkeypatch.setattr(match, "_MAX_OCCUPIED_KEYS", 0)
    rng = random.Random("morph-vertex-match")
    reference = [(rng.randrange(-300, 300) / 1e4, rng.randrange(-300, 300) / 1e4,
                  rng.randrange(0, 300) / 1e4) for _ in range(4000)]
    reference += [(-x, y, z) for x, y, z in reference[:1000]]
    queries = [(x + rng.uniform(-2e-4, 2e-4), y + rng.uniform(-2e-4, 2e-4), z)
               for x, y, z in rng.choices(reference, k=6000)]
    queries += [(0., y, z) for _x, y, z in reference[:500]]
    # Inside the sparse reference grid but mostly away from its cells, then outside it.
    queries += [(x, y, z + .0151) for x, y, z in reference[:500]]
    queries += [(x, y, z + 1.) for x, y, z in reference[:500]]
    buckets = _buckets(reference)
    positions = _pack(queries)
    cache = {}

    batch = match._match_batch(buckets, positions, cache)

    assert (cache["bucket_arrays"]["occupied"] is not None) == occupancy
    assert batch == match._match_scalar(buckets, positions)
    assert match.match_positions(buckets, positions, cache) == batch
    assert sum(index >= 0 for index in batch) > 1000
    assert batch[-500:] == [-1] * 500


def test_unpackable_coordinates_use_scalar_matching():
    buckets = _buckets([(0., 0., 0.), (3e38, 0., 0.)])
    positions = _pack([(0., 0., 0.)] * match._MIN_BATCH_VERTICES)

    assert match._match_batch(buckets, positions, {}) is None
    assert match.match_positions(buckets, positions, {}) == [0] * match._MIN_BATCH_VERTICES

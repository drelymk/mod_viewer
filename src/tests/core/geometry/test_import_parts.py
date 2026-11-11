"""Whole loose-part comparison preserves attributes and triangle multiplicity."""

import struct
from collections import Counter

import pytest

from core.geometry import import_parts


def _indices(*triangles):
    return b"".join(struct.pack("<III", *triangle) for triangle in triangles)


@pytest.mark.parametrize("scenario", ["shading", "multiplicity", "winding"])
def test_part_sharing_preserves_exact_order_and_render_distinctions(scenario):
    points = [(x, y, 0) for offset in (0, 3, 6) for x, y in
              [(offset, 0), (offset + 1, 0), (offset, 1)]]
    points += points[:3]
    positions = b"".join(struct.pack("<fff", *point) for point in points)
    a, b, c, alternate_a = (0, 1, 2), (3, 4, 5), (6, 7, 8), (9, 10, 11)
    if scenario == "shading":
        variants = [_indices(a, b, c), _indices(alternate_a, b), None]
    elif scenario == "multiplicity":
        variants = [_indices(a, b, a, c), _indices(a, b, c)]
    else:
        variants = [_indices(a, b, c), _indices((0, 2, 1), b)]
    result = import_parts.consolidate_index_ranges(variants, positions, 12)
    assert result.partitioned
    for original, ranges in zip(variants, result.variants):
        rebuilt = b"".join(result.data[part.start * 4:(part.start + part.count) * 4]
                           for part in ranges)
        assert Counter(struct.iter_unpack("<III", rebuilt)) == Counter(struct.iter_unpack("<III", original or b""))
    first, second = result.variants[:2]
    assert first[1].start == second[1].start
    if scenario == "shading":
        assert first[0].start != second[0].start
    elif scenario == "multiplicity":
        assert first[0].count == 6
        assert result.data[first[0].start * 4:(first[0].start + first[0].count) * 4] == _indices(a, a)
    else:
        assert first[0].start != second[0].start


def test_complete_loose_parts_match_despite_interleaved_draw_fragments():
    points = [(0, 0), (1, 0), (0, 1), (1, 1), (3, 0), (4, 0), (3, 1),
              (6, 0), (7, 0), (6, 1)]
    positions = b"".join(struct.pack("<fff", x, y, 0) for x, y in points)
    a, a2, b, c = (0, 1, 2), (1, 3, 2), (4, 5, 6), (7, 8, 9)
    variants = [_indices(a, b, a2, c), _indices(b, a, c, a2)]
    result = import_parts.consolidate_index_ranges(variants, positions, 12)
    assert result.partitioned
    assert result.unique_ranges == 3
    first, second = result.variants
    assert first[0].start == second[1].start
    assert first[1].start == second[0].start
    assert result.data[first[0].start * 4:(first[0].start + first[0].count) * 4] == _indices(a, a2)


@pytest.mark.parametrize("budget", ["_MAX_PART_RUNS", "_MAX_PART_TRIANGLES", "caller"])
def test_fragmentation_budget_keeps_the_original_whole_draws(monkeypatch, budget):
    positions = b"".join(struct.pack("<fff", x, y, 0) for x, y in
                         [(0, 0), (1, 0), (0, 1), (3, 0), (4, 0), (3, 1)])
    variants = [_indices((0, 1, 2), (3, 4, 5)), _indices((3, 4, 5), (0, 1, 2))]
    if budget != "caller":
        monkeypatch.setattr(import_parts, budget, 1)
    result = import_parts.consolidate_index_ranges(variants, positions, 12,
                                                  max_runs=1 if budget == "caller" else None)
    assert not result.partitioned
    assert [len(ranges) for ranges in result.variants] == [1, 1]

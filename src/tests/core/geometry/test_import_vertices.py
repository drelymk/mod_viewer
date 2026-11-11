"""Import tolerates bounded shader-vector noise without changing protected data."""

import math
import struct

import pytest

from core.geometry import import_vertices
from core.geometry.import_vertices import ImportVertices


def _record():
    return [struct.pack("<10f", 1, 2, 3, 0, 0, 1, 1, 0, 0, 1),
            struct.pack("<4f4I", 1, 0, 0, 0, 2, 0, 0, 0),
            struct.pack("<I4f", 0, 0.25, 0.75, 0, 0)]


@pytest.mark.parametrize("stream, offset, value, merges", [
    (0, 20, 1.0000001192092896, True),
    (0, 24, 1.0000001192092896, True),
    (0, 20, 1.001, False),
    (0, 0, 1.0000001192092896, False),
    (1, 0, 0.9999998807907104, False),
    (2, 4, 0.2500000298023224, False),
    (0, 36, -1, False),
    (0, 20, float('nan'), False),
])
def test_shader_rounding_only_merges_bounded_normal_and_tangent_changes(stream, offset, value, merges):
    registry = ImportVertices()
    original = _record()
    changed = list(original)
    raw = bytearray(changed[stream])
    struct.pack_into("<f", raw, offset, value)
    changed[stream] = bytes(raw)
    first = registry.add(original, 40)
    second = registry.add(changed, 40)
    assert (first == second) is merges
    assert registry.count == (1 if merges else 2)
    assert registry.canonicalized == int(merges)
    assert bytes(registry.streams[0][:40]) == original[0]


def test_rounding_compares_to_a_fixed_representative_without_transitive_drift():
    registry = ImportVertices()
    indices = []
    for value in (1, 1.0000007152557373, 1.0000014305114746):
        parts = _record()
        raw = bytearray(parts[0])
        struct.pack_into("<f", raw, 20, value)
        parts[0] = bytes(raw)
        indices.append(registry.add(parts, 40))
    assert indices == [0, 0, 1]


def test_normal_fallback_boundaries_are_never_canonicalized():
    registry = ImportVertices()
    for value in (0.5, 0.4999999701976776):
        parts = _record()
        raw = bytearray(parts[0])
        struct.pack_into("<f", raw, 20, value)
        parts[0] = bytes(raw)
        registry.add(parts, 40)
    assert registry.count == 2
    assert registry.canonicalized == 0


@pytest.mark.parametrize("budget", [1, 8])
def test_candidate_budget_preserves_exact_sharing_after_approximation_stops(monkeypatch, budget):
    monkeypatch.setattr(import_vertices, "_MAX_VECTOR_COMPARISONS", budget)
    comparisons = []

    def counted_abs(value):
        comparisons.append(value)
        return abs(value)

    monkeypatch.setattr(import_vertices, "abs", counted_abs, raising=False)
    registry = ImportVertices()
    original = _record()
    assert registry.add(original, 40) == 0
    noisy = list(original)
    noisy[0] = original[0][:20] + struct.pack("<f", 1.0000001192092896) + original[0][24:]
    assert registry.add(noisy, 40) == 0
    records = []
    for index in range(20):
        parts = list(original)
        parts[0] = (original[0][:12] + struct.pack("<3f", math.cos(index / 10), math.sin(index / 10), 0)
                    + original[0][24:])
        records.append((parts, registry.add(parts, 40)))
    assert len(comparisons) <= 6 * budget
    assert registry._comparisons_left == 0
    assert not registry._candidates
    comparison_count = len(comparisons)
    for parts, expected in records:
        assert registry.add(parts, 40) == expected
    assert registry.add(noisy, 40) == registry.add(original, 40) == 0
    later_noise = list(original)
    later_noise[0] = original[0][:20] + struct.pack("<f", 1.000000238418579) + original[0][24:]
    retained = registry.add(later_noise, 40)
    assert retained != 0
    assert registry.streams[0][retained * 40:(retained + 1) * 40] == later_noise[0]
    assert len(comparisons) == comparison_count

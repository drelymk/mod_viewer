import pytest

from core.editing.mesh_layout import (
    MeshLayoutError, drawindexed_ranges, repack_index_bytes,
    validate_triangle_partition,
)


@pytest.mark.parametrize("parts", [
    [[0], [0, 1]],
    [[0], [2]],
    [[], [0, 1]],
])
def test_triangle_partition_rejects_overlap_gaps_and_empty_parts(parts):
    with pytest.raises(MeshLayoutError):
        validate_triangle_partition(9, 2, parts)


@pytest.mark.parametrize("index_size", [2, 4])
def test_repack_preserves_raw_triangle_bytes_and_nonzero_draw_base(index_size):
    record_size = 3 * index_size
    prefix = bytes(range(8))
    triangles = [bytes([10 + i]) * record_size for i in range(4)]
    suffix = bytes(range(200, 206))
    original = prefix + b"".join(triangles) + suffix

    candidate, parts = repack_index_bytes(
        original, len(prefix) // index_size, 12, index_size,
        [[3, 0], [2, 1]])

    start = len(prefix)
    expected = prefix + triangles[3] + triangles[0] + triangles[2] + triangles[1] + suffix
    assert candidate == expected
    assert drawindexed_ranges(4, 23, parts) == (
        (6, 4, 23), (6, 10, 23))

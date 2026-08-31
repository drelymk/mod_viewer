"""Conservative texture edit-unit coverage regressions."""

import pytest

from core.textures.uv_coverage import UVCoverageError, rasterize_uv_coverage


def test_full_uv_quad_covers_every_non_multiple_block():
    result = rasterize_uv_coverage(
        [0, 1, 2, 0, 2, 3],
        [(0, 0), (1, 0), (1, 1), (0, 1)],
        10, 9, unit_width=4, unit_height=4)

    assert (result.grid_width, result.grid_height) == (3, 3)
    assert result.count == 9
    assert result.bounds == (0, 0, 2, 2)


def test_shared_edge_and_repeated_triangles_do_not_double_count():
    result = rasterize_uv_coverage(
        [0, 1, 2, 0, 2, 3, 0, 1, 2],
        [(0, 0), (1, 0), (1, 1), (0, 1)],
        8, 8, unit_width=4, unit_height=4)

    assert result.count == 4
    assert sum(result.mask) == result.count
    assert result.triangle_count == 3


def test_degenerate_triangles_are_skipped_and_reported():
    result = rasterize_uv_coverage(
        [0, 1, 2, 0, 1, 3],
        [(0, 0), (0.5, 0), (1, 0), (0, 1)],
        8, 8)

    assert result.triangle_count == 2
    assert result.degenerate_triangle_count == 1
    assert result.count > 0


def test_all_degenerate_triangles_report_no_coverage():
    with pytest.raises(UVCoverageError) as raised:
        rasterize_uv_coverage(
            [0, 1, 2], [(0, 0), (0.5, 0), (1, 0)], 8, 8)

    assert raised.value.code == "no_uv_coverage"


def test_thin_triangle_crossing_a_unit_is_not_lost_to_center_sampling():
    result = rasterize_uv_coverage(
        [0, 1, 2], [(0, 0), (1, 0), (0, 0.01)], 8, 8)

    assert result.count == 8
    assert result.bounds == (0, 0, 7, 0)


def test_source_uv_boundaries_are_inclusive():
    result = rasterize_uv_coverage(
        [0, 1, 2], [(0, 0), (1, 0), (0, 1)], 4, 4)

    assert result.count > 0
    assert result.bounds == (0, 0, 3, 3)


@pytest.mark.parametrize("uvs", [
    [(0, 0), (1.001, 0), (0, 1)],
    [(0, 0), (1, 0), (-0.001, 1)],
])
def test_substantial_out_of_range_uvs_are_rejected(uvs):
    with pytest.raises(UVCoverageError) as raised:
        rasterize_uv_coverage([0, 1, 2], uvs, 4, 4)

    assert raised.value.code == "tiled_uv_unsupported"


def test_missing_uvs_and_invalid_indices_have_stable_codes():
    with pytest.raises(UVCoverageError) as missing:
        rasterize_uv_coverage([0, 1, 2], None, 4, 4)
    assert missing.value.code == "mesh_has_no_uv"

    with pytest.raises(UVCoverageError) as invalid:
        rasterize_uv_coverage([0, 1, 3], [(0, 0), (1, 0), (0, 1)], 4, 4)
    assert invalid.value.code == "invalid_geometry"

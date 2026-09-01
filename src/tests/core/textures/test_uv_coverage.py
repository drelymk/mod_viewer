"""Conservative texture edit-unit coverage regressions."""

import pytest

from core.textures.uv_coverage import (
    UVCoverageError, _supercover_segment, collapse_pixel_mask_to_units,
    dilate_pixel_mask, rasterize_uv_coverage,
)


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


def test_all_degenerate_triangles_retain_conservative_segment_coverage():
    result = rasterize_uv_coverage(
        [0, 1, 2], [(0, 0), (0.5, 0), (1, 0)], 8, 8)

    assert result.degenerate_triangle_count == 1
    assert result.count > 0
    assert result.bounds[1] == result.bounds[3] == 0


def test_repeated_degenerate_point_retains_point_supercover():
    result = rasterize_uv_coverage(
        [0, 1, 2], [(0.5, 0.5), (0.5, 0.5), (0.5, 0.5)], 4, 4)

    assert result.count == 4
    assert result.bounds == (1, 1, 2, 2)


def test_pixel_dilation_is_clamped_to_edges_and_corners():
    corner = dilate_pixel_mask(bytearray([1, 0, 0, 0, 0, 0, 0, 0, 0]), 3, 3)
    center = dilate_pixel_mask(bytearray([0, 0, 0, 0, 1, 0, 0, 0, 0]), 3, 3)

    assert list(corner) == [1, 1, 0, 1, 1, 0, 0, 0, 0]
    assert list(center) == [1, 1, 1, 1, 1, 1, 1, 1, 1]


def test_pixel_masks_collapse_to_partial_edge_units():
    pixels = bytearray(5 * 3)
    pixels[2 * 5 + 2] = 1

    assert list(collapse_pixel_mask_to_units(pixels, 5, 3, 2, 2)) == [
        0, 0, 0, 0, 1, 0,
    ]


def test_thin_triangle_crossing_a_unit_is_not_lost_to_center_sampling():
    result = rasterize_uv_coverage(
        [0, 1, 2], [(0, 0), (1, 0), (0, 0.01)], 8, 8)

    assert result.count == 8
    assert result.bounds == (0, 0, 7, 0)


@pytest.mark.parametrize("start,end", [
    ((4, 4), (8, 4)), ((8, 4), (4, 4)),
    ((4, 4), (4, 8)), ((4, 8), (4, 4)),
])
def test_short_axis_aligned_edges_stop_at_the_endpoint(start, end):
    mask = bytearray(16 * 16)

    _supercover_segment(mask, 16, 16, start, end)

    assert [index for index, value in enumerate(mask) if value] == (
        [51, 52, 53, 54, 55, 56, 67, 68, 69, 70, 71, 72]
        if start[1] == end[1]
        else [51, 52, 67, 68, 83, 84, 99, 100, 115, 116, 131, 132])


@pytest.mark.parametrize("start,end", [
    ((4, 4), (8, 8)), ((8, 8), (4, 4)),
])
def test_short_diagonal_edges_are_symmetric_and_stop_at_endpoint(start, end):
    mask = bytearray(16 * 16)

    _supercover_segment(mask, 16, 16, start, end)

    assert [index for index, value in enumerate(mask) if value] == [
        51, 52, 67, 68, 69, 84, 85, 86,
        101, 102, 103, 118, 119, 120, 135, 136,
    ]


def test_interior_triangle_has_an_exact_local_mask():
    result = rasterize_uv_coverage(
        [0, 1, 2], [(0.25, 0.25), (0.50, 0.25), (0.25, 0.50)], 16, 16)

    assert [index for index, value in enumerate(result.mask) if value] == [
        51, 52, 53, 54, 55, 56, 67, 68, 69, 70, 71, 72,
        83, 84, 85, 86, 87, 99, 100, 101, 102,
        115, 116, 117, 131, 132,
    ]


@pytest.mark.parametrize("uvs,edge", [
    ([(0.999, 0.25), (1.0, 0.25), (0.999, 0.26)], "right"),
    ([(0.25, 0.999), (0.26, 0.999), (0.25, 1.0)], "bottom"),
])
def test_outer_texture_boundaries_do_not_mark_a_penultimate_unit(uvs, edge):
    result = rasterize_uv_coverage([0, 1, 2], uvs, 16, 16)
    occupied = [index for index, value in enumerate(result.mask) if value]
    columns = {index % 16 for index in occupied}
    rows = {index // 16 for index in occupied}

    if edge == "right":
        assert columns == {15}
        assert rows <= {3, 4}
    else:
        assert rows == {15}
        assert columns <= {3, 4}


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

"""Per-draw geometry validation, compaction, and packing regressions."""

import struct
from unittest.mock import patch

import pytest

from core.geometry.buffers import BufferStore, VertexStreams
from core.geometry.conventions import GeometryConvention
from core.geometry.draw_call import DrawCall
from core.geometry import packing
from core.geometry.packing import pack_draw_geometry


def _unpack_f32(data):
    return struct.unpack(f"<{len(data) // 4}f", data)


def _unpack_indices(data):
    return struct.unpack(f"<{len(data) // 4}I", data)


def _pack_fixture(tmp_path, indices, positions, *, uvs=None, base=0,
                  reverse_winding=False, position_data=None, uv_data=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    if position_data is None:
        position_data = b"".join(struct.pack("<3f", *point)
                                  for point in positions)
    if uv_data is None:
        uv_data = (b"" if uvs is None else b"".join(
            struct.pack("<2f", *uv) for uv in uvs))

    position_path = tmp_path / "position.buf"
    texcoord_path = tmp_path / "texcoord.buf"
    index_path = tmp_path / "body.ib"
    position_path.write_bytes(position_data)
    texcoord_path.write_bytes(uv_data)
    index_path.write_bytes(struct.pack(f"<{len(indices)}I", *indices))

    position_data = position_path.read_bytes()
    uv_data = texcoord_path.read_bytes()
    streams = VertexStreams(
        position_data, 12, uv_data, 8, 0, "<ff")
    draw = DrawCall(
        label="Body-1", count=len(indices), ib_file="body.ib",
        index_size=4, base=base, position_file="position.buf",
        position_stride=12, texcoord_file="texcoord.buf",
        texcoord_stride=8)
    group = {
        "position_file": "position.buf",
        "texcoord_file": "texcoord.buf",
    }
    return pack_draw_geometry(
        draw, group, mod_dir=str(tmp_path), default_streams=streams,
        default_index_size=4, buffers=BufferStore(),
        geometry_convention=GeometryConvention(reverse_winding=reverse_winding),
        sparse_shape_cache={})


def test_repeated_vertices_keep_exact_packed_positions_uvs_and_indices(
        tmp_path):
    positions = [(float(index), 0., 0.) for index in range(6)]
    uvs = [(0., 0.), (1., 0.), (2., .25), (3., 0.), (4., .5), (5., .75)]

    packed = _pack_fixture(
        tmp_path, (5, 2, 5, 4, 5, 2), positions, uvs=uvs)

    assert _unpack_f32(packed.positions) == (
        2., 0., 0., 4., 0., 0., 5., 0., 0.)
    assert _unpack_f32(packed.texcoords) == (
        2., .75, 4., .5, 5., .25)
    assert _unpack_indices(packed.indices) == (2, 0, 2, 1, 2, 0)


def test_compaction_remains_sorted_by_source_vertex_index(tmp_path):
    positions = [(float(index),) * 3 for index in range(6)]

    packed = _pack_fixture(tmp_path, (5, 2, 4), positions)

    assert _unpack_f32(packed.positions) == (
        2., 2., 2., 4., 4., 4., 5., 5., 5.)
    assert _unpack_indices(packed.indices) == (2, 0, 1)


def test_base_vertex_location_is_applied_before_packing(tmp_path):
    positions = [(float(index), 0., 0.) for index in range(6)]

    packed = _pack_fixture(tmp_path, (0, 1, 2), positions, base=3)

    assert _unpack_f32(packed.positions) == (
        3., 0., 0., 4., 0., 0., 5., 0., 0.)
    assert _unpack_indices(packed.indices) == (0, 1, 2)


def test_negative_effective_index_rejects_the_draw(tmp_path):
    positions = [(float(index), 0., 0.) for index in range(3)]

    packed = _pack_fixture(tmp_path, (0, 1, 2), positions, base=-1)

    assert packed is None


def test_truncated_position_removes_only_the_affected_triangle(tmp_path):
    positions = [(0., 0., 0.), (1., 0., 0.), (0., 1., 0.)]

    packed = _pack_fixture(
        tmp_path, (0, 1, 2, 0, 3, 4), positions,
        uvs=[(0., 0.)] * 5)

    assert _unpack_indices(packed.indices) == (0, 1, 2)


def test_invalid_vertex_decode_is_cached_across_triangles(tmp_path):
    positions = [
        (0., 0., 0.), (1., 0., 0.), (0., 1., 0.),
        (float("nan"), 0., 0.), (1., 1., 0.),
    ]
    position_unpack_offsets = []
    original_unpack_from = struct.unpack_from

    def track_position_unpack(format_string, data, offset=0):
        if format_string == "<fff":
            position_unpack_offsets.append(offset)
        return original_unpack_from(format_string, data, offset)

    with patch.object(
            packing.struct, "unpack_from", side_effect=track_position_unpack):
        packed = _pack_fixture(
            tmp_path, (0, 3, 4, 1, 3, 2), positions)

    assert packed is None
    assert position_unpack_offsets == [0, 36, 12]


@pytest.mark.parametrize("invalid", [float("nan"), float("inf")])
def test_nonfinite_position_removes_only_the_affected_triangle(
        tmp_path, invalid):
    positions = [
        (0., 0., 0.), (1., 0., 0.), (0., 1., 0.),
        (invalid, 0., 0.), (1., 1., 0.),
    ]

    packed = _pack_fixture(
        tmp_path, (0, 1, 2, 0, 3, 4), positions,
        uvs=[(0., 0.)] * 5)

    assert _unpack_indices(packed.indices) == (0, 1, 2)


def test_truncated_uv_removes_only_the_affected_triangle(tmp_path):
    positions = [(float(index), 0., 0.) for index in range(5)]

    packed = _pack_fixture(
        tmp_path, (0, 1, 2, 0, 3, 4), positions,
        uvs=[(0., 0.)] * 3)

    assert _unpack_indices(packed.indices) == (0, 1, 2)


@pytest.mark.parametrize("invalid", [float("nan"), float("inf")])
def test_nonfinite_uv_removes_only_the_affected_triangle(tmp_path, invalid):
    positions = [(float(index), 0., 0.) for index in range(5)]
    uvs = [(0., 0.), (0., 0.), (0., 0.), (invalid, 0.), (0., 0.)]

    packed = _pack_fixture(
        tmp_path, (0, 1, 2, 0, 3, 4), positions, uvs=uvs)

    assert _unpack_indices(packed.indices) == (0, 1, 2)


def test_reverse_winding_preserves_compact_vertex_order_and_output_shape(
        tmp_path):
    positions = [(0., 0., 0.), (1., 0., 0.), (0., 1., 0.)]
    uvs = [(0., 0.), (1., 0.), (0., 1.)]

    forward = _pack_fixture(
        tmp_path / "forward", (0, 1, 2), positions, uvs=uvs)
    reverse = _pack_fixture(
        tmp_path / "reverse", (0, 2, 1), positions, uvs=uvs,
        reverse_winding=True)

    assert reverse.positions == forward.positions
    assert reverse.texcoords == forward.texcoords
    assert reverse.indices == forward.indices


def test_incomplete_trailing_indices_are_ignored(tmp_path):
    positions = [(0., 0., 0.), (1., 0., 0.), (0., 1., 0.)]

    packed = _pack_fixture(tmp_path, (0, 1, 2, 0, 1), positions)

    assert _unpack_indices(packed.indices) == (0, 1, 2)


def test_no_uv_path_remains_valid_without_a_texcoord_payload(tmp_path):
    positions = [(0., 0., 0.), (1., 0., 0.), (0., 1., 0.)]

    packed = _pack_fixture(tmp_path, (0, 1, 2), positions, uvs=None)

    assert packed is not None
    assert packed.texcoords is None
    assert _unpack_f32(packed.positions) == (
        0., 0., 0., 1., 0., 0., 0., 1., 0.)

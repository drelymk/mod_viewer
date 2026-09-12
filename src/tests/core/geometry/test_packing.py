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
    assert packed.used_vertices == (2, 4, 5)
    assert _unpack_f32(packed.texcoords) == (
        2., .75, 4., .5, 5., .25)
    assert _unpack_indices(packed.indices) == (2, 0, 2, 1, 2, 0)


def test_base_vertex_location_is_applied_before_packing(tmp_path):
    positions = [(float(index), 0., 0.) for index in range(6)]

    packed = _pack_fixture(tmp_path, (0, 1, 2), positions, base=3)

    assert _unpack_f32(packed.positions) == (
        3., 0., 0., 4., 0., 0., 5., 0., 0.)
    assert _unpack_indices(packed.indices) == (0, 1, 2)
    assert packed.used_vertices == (3, 4, 5)


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
    assert packed.used_vertices == (0, 1, 2)


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
@pytest.mark.parametrize("component", range(3))
def test_nonfinite_position_removes_only_the_affected_triangle(
        tmp_path, invalid, component):
    invalid_position = [0., 0., 0.]
    invalid_position[component] = invalid
    positions = [
        (0., 0., 0.), (1., 0., 0.), (0., 1., 0.),
        tuple(invalid_position), (1., 1., 0.),
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
@pytest.mark.parametrize("component", range(2))
def test_nonfinite_uv_removes_only_the_affected_triangle(
        tmp_path, invalid, component):
    positions = [(float(index), 0., 0.) for index in range(5)]
    invalid_uv = [0., 0.]
    invalid_uv[component] = invalid
    uvs = [(0., 0.), (0., 0.), (0., 0.), tuple(invalid_uv), (0., 0.)]

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
    assert reverse.used_vertices == forward.used_vertices


@pytest.mark.parametrize("trailing", [(0,), (0, 1)])
def test_incomplete_trailing_indices_are_ignored(tmp_path, trailing):
    positions = [(0., 0., 0.), (1., 0., 0.), (0., 1., 0.)]

    packed = _pack_fixture(tmp_path, (0, 1, 2) + trailing, positions)

    assert _unpack_indices(packed.indices) == (0, 1, 2)


@pytest.mark.parametrize("reverse_winding, expected", [
    (False, (0, 1, 2)), (True, (0, 2, 1)),
])
@pytest.mark.parametrize("indices", [
    (0, 3, 2, 0, 1, 2), (0, 1, 2, 0, 1, 3),
])
def test_invalid_triangles_never_append_partial_indices(
        tmp_path, reverse_winding, expected, indices):
    packed = _pack_fixture(
        tmp_path, indices, [(float(i), 0., 0.) for i in range(3)],
        reverse_winding=reverse_winding)

    assert _unpack_indices(packed.indices) == expected


def test_negative_base_with_nonnegative_effective_indices_and_reverse_winding(
        tmp_path):
    packed = _pack_fixture(
        tmp_path, (3, 4, 5), [(float(i), 0., 0.) for i in range(3)],
        base=-3, reverse_winding=True)

    assert _unpack_indices(packed.indices) == (0, 2, 1)
    assert _unpack_f32(packed.positions) == (0., 0., 0., 1., 0., 0., 2., 0., 0.)


def test_shared_vertices_preserve_prepared_identity_and_decode_once(tmp_path):
    prepared_results = []
    unpack_offsets = []
    original_prepare = packing._prepare_draw_vertices
    original_unpack = struct.unpack_from

    def capture_prepare(*args, **kwargs):
        prepared = original_prepare(*args, **kwargs)
        prepared_results.append(prepared)
        return prepared

    def capture_unpack(format_string, data, offset=0):
        if format_string == "<fff":
            unpack_offsets.append(offset)
        return original_unpack(format_string, data, offset)

    with patch.object(packing, "_prepare_draw_vertices", capture_prepare), \
            patch.object(packing.struct, "unpack_from", capture_unpack):
        packed = _pack_fixture(
            tmp_path, (5, 2, 4, 4, 2, 5),
            [(float(i), 0., 0.) for i in range(6)], reverse_winding=True)

    prepared, = prepared_results
    assert prepared.raw_indices == [5, 4, 2, 4, 5, 2]
    assert prepared.used_vertices == [2, 4, 5]
    assert packed.used_vertices == (2, 4, 5)
    assert prepared.remap == {2: 0, 4: 1, 5: 2}
    assert prepared.decoded_vertices == {
        i: (float(i), 0., 0., None, None) for i in (2, 4, 5)}
    assert unpack_offsets == [60, 24, 48]
    assert _unpack_indices(packed.indices) == (2, 1, 0, 1, 2, 0)


def test_no_uv_path_remains_valid_without_a_texcoord_payload(tmp_path):
    positions = [(0., 0., 0.), (1., 0., 0.), (0., 1., 0.)]

    packed = _pack_fixture(tmp_path, (0, 1, 2), positions, uvs=None)

    assert packed is not None
    assert packed.texcoords is None
    assert _unpack_f32(packed.positions) == (
        0., 0., 0., 1., 0., 0., 0., 1., 0.)

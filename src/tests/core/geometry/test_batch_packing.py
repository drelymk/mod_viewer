"""Batch conversion equivalence and conservative dispatch contracts."""

from dataclasses import replace
import struct
from unittest.mock import patch
import zipfile

import pytest

from core.geometry import batch_packing, packing, vertex_attributes
from core.geometry.buffers import BufferStore, VertexStreams
from core.geometry.conventions import GeometryConvention
from core.geometry.draw_call import DrawCall
from core.geometry.vertex_attributes import VertexAttributeSource
from core.mod_source import mod_source_for_path


def _inputs(root, *, index_size=4, uv_format="<ff", base=0, reverse=False,
            fault=None, shapes=False):
    positions = bytearray(12 * 40)
    texcoords = bytearray(12 * 12)
    uv_values = (.25, -.75, .99999994, 1.25)
    for index in range(12):
        struct.pack_into("<6f", positions, index * 40,
                         float(index) - 3., -float(index),
                         -0. if index % 2 == 0 else 0., 0., 0., 1.)
        struct.pack_into(uv_format, texcoords, index * 12 + 4,
                         uv_values[index % 4], uv_values[(index + 1) % 4])
    if fault == "position_nan":
        struct.pack_into("<f", positions, 7 * 40, float("nan"))
    elif fault == "uv_nan":
        struct.pack_into(uv_format, texcoords, 7 * 12 + 4, float("nan"), 0.)
    elif fault == "position_truncated":
        del positions[5 * 40 + 12:]
    elif fault == "uv_truncated":
        del texcoords[5 * 12 + 4 + struct.calcsize(uv_format):]
    indices = (2, 3, 4, 4, 2, 3, 2, 4, 3, 7, 6, 5)
    (root / "position.buf").write_bytes(positions)
    (root / "texcoord.buf").write_bytes(texcoords)
    (root / "index.buf").write_bytes(struct.pack(
        "<" + ("H" if index_size == 2 else "I") * len(indices), *indices))
    draw = DrawCall(
        label="Component01-1", count=len(indices), start=0, base=base,
        ib_file="index.buf", index_size=index_size,
        position_file="position.buf", position_stride=40,
        texcoord_file="texcoord.buf", texcoord_stride=12,
        normal_source=VertexAttributeSource("position.buf", 40, 12, "f32x3"))
    group = {"position_file": "position.buf", "texcoord_file": "texcoord.buf"}
    if shapes:
        endpoint = bytearray(positions)
        low = bytearray(positions)
        for index in range(12):
            struct.pack_into("<3f", endpoint, index * 40, index + 10., 2., 3.)
            struct.pack_into("<3f", low, index * 40, index - 10., -2., -3.)
        # The last low endpoint contains XYZ but not a complete source record.
        del low[3 * 40 + 12:]
        (root / "high.buf").write_bytes(endpoint)
        (root / "low.buf").write_bytes(low)
        group["shape_sliders"] = [{
            "var": "shape_amount", "base_file": "position.buf",
            "target_file": "high.buf", "low_file": "low.buf",
            "stride": 40, "mode": "midpoint_pair",
        }]
    options = dict(
        mod_dir=str(root), default_streams=VertexStreams(
            bytes(positions), 40, bytes(texcoords), 12, 4, uv_format),
        default_index_size=4, buffers=BufferStore(),
        geometry_convention=GeometryConvention(reverse_winding=reverse),
        sparse_shape_cache={})
    return draw, group, options


def _equivalent(actual, expected):
    assert actual == expected
    if actual is not None:
        assert struct.pack("<6d", *actual.bounds_min, *actual.bounds_max) == \
            struct.pack("<6d", *expected.bounds_min, *expected.bounds_max)


@pytest.fixture(autouse=True)
def _force_batch(monkeypatch):
    monkeypatch.setattr(batch_packing, "_MIN_BATCH_INDICES", 0)
    monkeypatch.setattr(vertex_attributes, "_MIN_BATCH_NORMALS", 0)


@pytest.mark.parametrize("index_size", [2, 4])
@pytest.mark.parametrize("uv_format", ["<ee", "<ff"])
@pytest.mark.parametrize("base", [-2, 0, 2])
@pytest.mark.parametrize("reverse", [False, True])
def test_batch_preserves_layout_ranges_and_exact_bytes(
        tmp_path, index_size, uv_format, base, reverse):
    draw, group, options = _inputs(
        tmp_path, index_size=index_size, uv_format=uv_format,
        base=base, reverse=reverse, shapes=True)
    expected = packing._pack_draw_geometry_python(draw, group, **options)
    with patch.object(packing, "bytearray", create=True,
                      side_effect=AssertionError("unused scalar output allocated")):
        actual = batch_packing.try_pack_draw_geometry(draw, group, **options)
    assert actual is not None
    _equivalent(actual, expected)
    _equivalent(packing.pack_draw_geometry(draw, group, **options), expected)


@pytest.mark.parametrize("fault", [
    "position_nan", "uv_nan", "position_truncated", "uv_truncated",
    "negative_base", "unsupported_uv", "sparse", "compute",
    "shape_nan", "partial_triangle", "trailing_negative", "after_end",
])
def test_unsupported_inputs_keep_the_conservative_result(tmp_path, fault):
    draw, group, options = _inputs(tmp_path, fault=fault, shapes=fault == "shape_nan")
    if fault == "negative_base":
        draw.base = -3
    elif fault == "unsupported_uv":
        options["default_streams"] = replace(
            options["default_streams"], uv_format="<2f")
    elif fault == "sparse":
        group["shape_sliders"] = [{
            "var": "shape_amount", "base_file": "position.buf", "shape_id": 0,
        }]
    elif fault == "compute":
        group["_compute_animation"] = {"track_id": "track01"}
    elif fault == "shape_nan":
        endpoint = bytearray((tmp_path / "high.buf").read_bytes())
        struct.pack_into("<I", endpoint, 2 * 40, 0x7F800001)
        (tmp_path / "high.buf").write_bytes(endpoint)
    elif fault == "partial_triangle":
        draw.count = 2
    elif fault == "after_end":
        draw.start = 100
    elif fault == "trailing_negative":
        draw.base = -2
        draw.count = 4
        options["buffers"] = BufferStore(overrides={
            str(tmp_path / "index.buf"): struct.pack("<4I", 2, 3, 4, 1)})
    assert batch_packing.try_pack_draw_geometry(draw, group, **options) is None
    _equivalent(
        packing.pack_draw_geometry(draw, group, **options),
        packing._pack_draw_geometry_python(draw, group, **options))


@pytest.mark.parametrize("variant", ["position", "texcoord", "no_uv"])
def test_effective_streams_and_missing_uvs_match_scalar_packing(tmp_path, variant):
    draw, group, options = _inputs(tmp_path)
    if variant == "no_uv":
        options["default_streams"] = replace(
            options["default_streams"], texcoord_data=b"")
    else:
        source_name = "position.buf" if variant == "position" else "texcoord.buf"
        data = bytearray((tmp_path / source_name).read_bytes())
        struct.pack_into("<f", data, 2 * (40 if variant == "position" else 12), .75)
        (tmp_path / "alternate.buf").write_bytes(data)
        if variant == "position":
            draw.position_file = "alternate.buf"
        else:
            draw.texcoord_file = "alternate.buf"
    actual = batch_packing.try_pack_draw_geometry(draw, group, **options)
    assert actual is not None
    _equivalent(actual, packing._pack_draw_geometry_python(draw, group, **options))


@pytest.mark.parametrize("resource", ["../../../outside.buf", r"X:\outside.buf"])
def test_batch_keeps_resource_paths_inside_the_mod(tmp_path, resource):
    draw, group, options = _inputs(tmp_path)
    draw.position_file = resource
    assert batch_packing.try_pack_draw_geometry(draw, group, **options) is None
    assert packing.pack_draw_geometry(draw, group, **options) is None


def test_staged_indices_and_archive_bytes_keep_their_source_mapping(tmp_path):
    draw, group, options = _inputs(tmp_path, shapes=True)
    original = packing.pack_draw_geometry(draw, group, **options)
    original_bytes = (tmp_path / "index.buf").read_bytes()
    indices = struct.unpack("<12I", original_bytes)
    staged = struct.pack("<12I", *reversed(indices))
    options["buffers"] = BufferStore(overrides={str(tmp_path / "index.buf"): staged})
    modified = packing.pack_draw_geometry(draw, group, **options)
    assert modified.indices != original.indices
    assert modified.used_vertices == original.used_vertices
    _equivalent(modified, packing._pack_draw_geometry_python(draw, group, **options))
    assert (tmp_path / "index.buf").read_bytes() == original_bytes

    archive = tmp_path / "model.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        for name in ("position.buf", "texcoord.buf", "index.buf", "high.buf", "low.buf"):
            stream.write(tmp_path / name, name)
    source = mod_source_for_path(str(archive))
    options.update(mod_dir=str(archive), source=source,
                   buffers=BufferStore(source=source))
    options["default_streams"] = replace(
        options["default_streams"],
        position_data=options["buffers"].raw(source.resolve_resource("position.buf")),
        texcoord_data=options["buffers"].raw(source.resolve_resource("texcoord.buf")))
    _equivalent(packing.pack_draw_geometry(draw, group, **options), original)


def test_batch_limits_and_prepared_draws_do_not_enter_array_conversion(
        tmp_path, monkeypatch):
    draw, group, options = _inputs(tmp_path)
    monkeypatch.setattr(batch_packing, "_MAX_BATCH_INDICES", 3)
    with patch("numpy.unique", side_effect=AssertionError("array conversion called")):
        assert batch_packing.try_pack_draw_geometry(draw, group, **options) is None
    prepare_options = {key: value for key, value in options.items()
                       if key != "sparse_shape_cache"}
    prepared = packing._prepare_draw_vertices(draw, group, **prepare_options)
    with patch.object(batch_packing, "try_pack_draw_geometry",
                      side_effect=AssertionError("batch path called")):
        _equivalent(
            packing.pack_draw_geometry(draw, group, prepared=prepared, **options),
            packing._pack_draw_geometry_python(draw, group, prepared=prepared, **options))

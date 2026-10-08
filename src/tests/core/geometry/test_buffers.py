"""Bounded geometry-buffer access regressions."""

from unittest.mock import patch
import math
import struct
import zipfile

import pytest

from core.geometry import buffers
from core.mod_source import ZipModSource


def _texcoord_data(pairs, offset, fmt, stride, *, normals=False):
    data = bytearray()
    for index, pair in enumerate(pairs):
        record = bytearray(stride)
        if offset == 4:
            record[:4] = struct.pack("<4B", 64, 96, 0, 0)
        struct.pack_into(fmt, record, offset, *pair)
        if normals:
            struct.pack_into("<3f", record, 8,
                             math.sin(index * .7), math.cos(index * .7), 0.)
        data.extend(record)
    return bytes(data)


def _uv_grid(kind):
    pairs = [(.05 + (index % 17) * .055, .05 + (index // 17) * .055)
             for index in range(17 * 17)]
    if kind == "signed-u":
        return [(u - 1. if index % 7 == 0 else u, v)
                for index, (u, v) in enumerate(pairs)]
    if kind == "signed-v":
        return [(u, v - 1. if index % 7 == 0 else v)
                for index, (u, v) in enumerate(pairs)]
    if kind == "negative":
        return [(u - 1., v - 1.) for u, v in pairs]
    if kind == "one-axis":
        return [(.25, v) for _u, v in pairs]
    return pairs


@pytest.mark.parametrize("offset,fmt,stride", [
    (0, "<ee", 4), (4, "<ee", 20),
    (0, "<ff", 8), (4, "<ff", 12),
])
@pytest.mark.parametrize("kind", [
    "positive", "signed-u", "signed-v", "negative", "one-axis",
])
def test_uv_detection_decodes_supported_layouts_without_remapping(
        tmp_path, offset, fmt, stride, kind):
    data = _texcoord_data(_uv_grid(kind), offset, fmt, stride)
    path = tmp_path / "texcoord.buf"
    path.write_bytes(data)

    detected = buffers._detect_uv_best(path, stride)
    assert detected == (offset, fmt)
    assert buffers._detect_uv_best(path, stride, data=data) == detected
    expected = [struct.unpack_from(fmt, data, index * stride + offset)
                for index in range(len(data) // stride)]
    assert buffers.read_texcoords(path, stride, *detected) == expected


@pytest.mark.parametrize("kind,stride", [
    ("positive", 24), ("signed-v", 20), ("negative", 20),
])
def test_uv_detection_keeps_normal_attributes_out_of_uv_layout(kind, stride):
    data = _texcoord_data(_uv_grid(kind), 4, "<ee", stride, normals=True)
    assert buffers._detect_uv_best(None, stride, data=data) == (4, "<ee")


def test_signed_uv_detection_prefers_variation_on_the_weaker_axis():
    pairs = [(.2 + u * .1, -.3 - v * .5) for u, v in _uv_grid("positive")]
    data = _texcoord_data(pairs, 4, "<ee", 20, normals=True)
    assert buffers._detect_uv_best(None, 20, data=data) == (4, "<ee")


def test_signed_uv_detection_does_not_infer_an_axis_from_sparse_attributes():
    pairs = [(.25, -.1 - index * .7 / 288) for index in range(289)]
    data = bytearray(_texcoord_data(pairs, 4, "<ee", 20))
    for index in range(0, len(pairs), 31):
        struct.pack_into("<f", data, index * 20 + 8, -.9)
    # The authored UVs have one constant axis. Sparse unrelated negatives must
    # not manufacture a two-dimensional signed candidate from the next field.
    assert buffers._detect_uv_best(None, 20, data=data) == (0, "<ff")


@pytest.mark.parametrize("offset,fmt,stride", [
    (0, "<ee", 4), (4, "<ee", 20),
    (0, "<ff", 8), (4, "<ff", 12),
])
def test_uv_detection_tolerates_sparse_nonfinite_vertices(offset, fmt, stride):
    pairs = _uv_grid("signed-v")
    pairs[5] = (float("nan"), .5)
    pairs[15] = (.5, float("inf"))
    pairs[25] = (-float("inf"), .5)
    data = _texcoord_data(pairs, offset, fmt, stride)
    assert buffers._detect_uv_best(None, stride, data=data) == (offset, fmt)


def test_uv_detection_samples_beyond_an_initial_constant_region():
    pairs = [(.25, .25)] * 4096 + _uv_grid("negative") * 16
    data = _texcoord_data(pairs, 4, "<ee", 20, normals=True)
    assert buffers._detect_uv_best(None, 20, n=32, data=data) == (4, "<ee")


@pytest.mark.parametrize("data,stride,expected", [
    (b"", 20, (4, "<ee")),
    (b"\0" * 3, 4, (4, "<ee")),
    (b"\0" * 16, 4, (0, "<ee")),
    (b"\xff" * 80, 20, (4, "<ee")),
])
def test_uv_detection_keeps_safe_fallbacks(data, stride, expected):
    assert buffers._detect_uv_best(None, stride, data=data) == expected


def test_signed_uv_detection_preserves_published_mesh_coordinates(tmp_path):
    from core.geometry.draw_call import DrawCall
    from core.geometry.mesh_builder import build_mesh_result

    positions = b"".join(struct.pack("<3f", *point) + b"\0" * 28
                         for point in ((0., 0., 0.), (1., 0., 0.), (0., 1., 0.)))
    (tmp_path / "position.buf").write_bytes(positions)
    (tmp_path / "component.ib").write_bytes(struct.pack("<3I", 0, 1, 2))
    groups, expected = [], {}
    for number, (stride, pairs) in enumerate([
            (20, [(.25, -.5), (.75, .25), (.5, .75)]),
            (24, [(.25, .5), (.75, .25), (.5, .75)])], start=1):
        component = f"Component{number:02}"
        filename = f"{component}.buf"
        (tmp_path / filename).write_bytes(
            _texcoord_data(pairs, 4, "<ee", stride))
        draw = DrawCall(
            label=f"{component}-1", count=3, ib_file="component.ib",
            index_size=4, position_file="position.buf", position_stride=40,
            texcoord_file=filename, texcoord_stride=stride)
        groups.append({
            "name": component, "position_file": "position.buf",
            "position_stride": 40, "texcoord_file": filename,
            "texcoord_stride": stride, "ib_file": "component.ib",
            "index_size": 4, "draws": [draw],
        })
        expected[draw.label] = tuple(value for u, v in pairs for value in (u, 1. - v))

    built = build_mesh_result(groups, str(tmp_path))
    assert set(built.meshes) == set(expected)
    for label, mesh in built.meshes.items():
        reference = mesh["uv"]
        assert struct.unpack_from("<6f", built.geometry.data,
                                  reference["offset"]) == expected[label]
        assert reference["length"] == 24
        assert mesh["pos"]["length"] == 36
        assert struct.unpack_from("<3I", built.geometry.data,
                                  mesh["idx"]["offset"]) == (0, 1, 2)


def test_buffer_store_reads_shared_file_once(tmp_path):
    path = tmp_path / "shared.buf"
    path.write_bytes(b"shared")
    store = buffers.BufferStore()

    with patch("builtins.open", wraps=open) as reader:
        assert store.raw(str(path)) == b"shared"
        assert store.raw(str(path)) == b"shared"

    assert reader.call_count == 1


def test_buffer_store_preserves_single_file_limit(tmp_path, monkeypatch):
    path = tmp_path / "large.buf"
    path.write_bytes(b"12345")
    monkeypatch.setattr(buffers, "_MAX_BUFFER_FILE_BYTES", 4)

    with pytest.raises(ValueError, match="Buffer file is too large"):
        buffers.BufferStore().raw(str(path))


def test_buffer_store_preserves_cumulative_limit_but_not_shared_reads(
        tmp_path, monkeypatch):
    first = tmp_path / "first.buf"
    second = tmp_path / "second.buf"
    first.write_bytes(b"123")
    second.write_bytes(b"456")
    monkeypatch.setattr(buffers, "_MAX_TOTAL_BUFFER_BYTES", 5)
    store = buffers.BufferStore()

    store.raw(str(first))
    store.raw(str(first))
    with pytest.raises(ValueError, match="2 GiB safety limit"):
        store.raw(str(second))


def test_buffer_store_reads_zip_members_through_the_source(tmp_path):
    archive_path = tmp_path / "mod.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("Mod/mesh.buf", b"mesh")
    source = ZipModSource(archive_path)
    member = source.resolve_resource("mesh.buf")

    assert buffers.BufferStore(source=source).raw(member) == b"mesh"


def test_buffer_store_prefers_staged_override(tmp_path):
    path = tmp_path / "Component01.ib"
    path.write_bytes(b"disk")
    assert buffers.BufferStore(overrides={str(path): b"staged"}).raw(
        str(path)) == b"staged"

"""Bounded geometry-buffer access regressions."""

from unittest.mock import patch
import math
import struct
import zipfile

import pytest

from core.geometry import buffers
from core.mod_source import ZipModSource
from tests.support.model_data import triangle_geometry


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


@pytest.mark.parametrize("offset,fmt,stride,signed", [
    (0, "<ee", 4, False), (0, "<ff", 8, True),
    (4, "<ee", 20, True), (4, "<ee", 24, False),
])
def test_uv_detection_decodes_representative_layouts(
        tmp_path, offset, fmt, stride, signed):
    pairs = [(.05 + index % 17 * .055, .05 + index // 17 * .055 - signed)
             for index in range(289)]
    data = _texcoord_data(pairs, offset, fmt, stride, normals=stride >= 20)
    path = tmp_path / "texcoord.buf"
    path.write_bytes(data)
    detected = buffers._detect_uv_best(path, stride)
    assert detected == (offset, fmt)
    assert buffers._detect_uv_best(None, stride, data=data) == detected
    assert buffers.read_texcoords(path, stride, *detected) == [
        struct.unpack_from(fmt, data, index * stride + offset)
        for index in range(len(pairs))]


def test_signed_uv_detection_does_not_infer_an_axis_from_sparse_attributes():
    pairs = [(.25, -.1 - index * .7 / 288) for index in range(289)]
    data = bytearray(_texcoord_data(pairs, 4, "<ee", 20))
    for index in range(0, len(pairs), 31):
        struct.pack_into("<f", data, index * 20 + 8, -.9)
    assert buffers._detect_uv_best(None, 20, data=data) == (0, "<ff")


def test_signed_uv_detection_preserves_published_mesh_coordinates(tmp_path):
    from core.geometry.mesh_builder import build_mesh_result

    for filename, data in triangle_geometry().items():
        (tmp_path / filename).write_bytes(data)
    pairs = [(.25, -.5), (.75, .25), (.5, .75)]
    (tmp_path / "t.buf").write_bytes(_texcoord_data(pairs, 4, "<ee", 20))
    groups = [{
        "name": "Component01", "position_file": "p.buf", "position_stride": 12,
        "texcoord_file": "t.buf", "texcoord_stride": 20,
        "ib_file": "i.buf", "index_size": 4,
        "draws": [{"label": "Component01-1", "count": 3, "start": 0, "base": 0}],
    }]
    built = build_mesh_result(groups, str(tmp_path))
    reference = built.meshes["Component01-1"]["uv"]
    assert struct.unpack_from("<6f", built.geometry.data, reference["offset"]) == (
        .25, 1.5, .75, .75, .5, .25)


@pytest.mark.parametrize("offset,fmt,stride", [
    (0, "<ff", 8), (4, "<ff", 20), (4, "<ee", 20),
])
def test_tiled_uv_detection_preserves_coordinates_and_authored_triangles(
        tmp_path, offset, fmt, stride):
    from core.geometry.mesh_builder import build_mesh_result

    pairs = [(8.05 + column * .055, -8.95 + row * .055)
             for row in range(17) for column in range(17)]
    # This finite Float32 has a NaN in its low half when misread as Float16.
    pairs[0] = (struct.unpack("<f", struct.pack("<I", 0x41007E00))[0], -8.75)
    data = _texcoord_data(pairs, offset, fmt, stride)
    path = tmp_path / "t.buf"
    path.write_bytes(data)
    assert buffers._detect_uv_best(path, stride) == (offset, fmt)
    assert buffers._detect_uv_best(None, stride, data=data) == (offset, fmt)
    positions = [value for row in range(17) for column in range(17)
                 for value in (column / 16, row / 16, 0.)]
    indices = [index for row in range(16) for column in range(16)
               for index in (row * 17 + column, row * 17 + column + 1,
                             (row + 1) * 17 + column, row * 17 + column + 1,
                             (row + 1) * 17 + column + 1, (row + 1) * 17 + column)]
    (tmp_path / "p.buf").write_bytes(struct.pack(f"<{len(positions)}f", *positions))
    (tmp_path / "i.buf").write_bytes(struct.pack(f"<{len(indices)}I", *indices))
    built = build_mesh_result([{
        "name": "Component01", "position_file": "p.buf", "position_stride": 12,
        "texcoord_file": "t.buf", "texcoord_stride": stride,
        "ib_file": "i.buf", "index_size": 4,
        "draws": [{"label": "Component01-1", "count": len(indices), "start": 0, "base": 0}],
    }], str(tmp_path))
    mesh = built.meshes["Component01-1"]
    assert mesh["idx"]["length"] == len(indices) * 4
    expected = [value for u, v in buffers.read_texcoords(path, stride, offset, fmt)
                for value in (u, 1. - v)]
    assert bytes(built.geometry.data[
        mesh["uv"]["offset"]:mesh["uv"]["offset"] + mesh["uv"]["length"]]) == struct.pack(
            f"<{len(expected)}f", *expected)


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

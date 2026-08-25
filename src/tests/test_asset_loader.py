"""Focused direct Asset loading regressions."""

import json
import struct

import pytest

from app import asset_folders, paths, server
from app.asset_index import build_index
from app.asset_loader import hash_asset, load_asset
from app.api import ModViewerAPI
from core.geometry_transport import GeometryBlob
from core.migoto_dump import parse_index_dump, parse_vertex_dump


def _write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def _text_vb(path, stride, rows):
    lines = [f"stride: {stride}", f"vertex count: {len(rows)}",
             "element[0]: POSITION0, format: R32G32B32_FLOAT",
             "element[1]: NORMAL0, format: R32G32B32_FLOAT",
             "element[2]: TEXCOORD0, format: R32G32_FLOAT"]
    for index, (position, normal, uv) in enumerate(rows):
        lines.extend([
            f"vb0[{index}]+000 POSITION: {position[0]}, {position[1]}, {position[2]}",
            f"vb0[{index}]+012 NORMAL: {normal[0]}, {normal[1]}, {normal[2]}",
            f"vb0[{index}]+024 TEXCOORD0: {uv[0]}, {uv[1]}",
            f"vb0[{index}]+032 COLOR: 1, 0, 0, 1",
        ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fmt(path, stride, index_format, elements):
    lines = [f"stride: {stride}", "topology: trianglelist",
             f"format: {index_format}", ""]
    for ordinal, (name, index, encoding, offset) in enumerate(elements):
        lines.extend([
            f"element[{ordinal}]:",
            f"  SemanticName: {name}",
            f"  SemanticIndex: {index}",
            f"  Format: {encoding}",
            f"  AlignedByteOffset: {offset}",
            "",
        ])
    path.write_text("\n".join(lines), encoding="utf-8")


def test_migoto_dump_uses_declared_semantics_and_streams_layouts(tmp_path):
    rows = [((0, 0, 0), (0, 0, 1), (0, 0)),
            ((1, 0, 0), (0, 1, 0), (1, 0)),
            ((0, 1, 0), (1, 0, 0), (0, 1))]
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    _text_vb(first, 92, rows)
    _text_vb(second, 120, rows)

    parsed_first = parse_vertex_dump(first)
    parsed_second = parse_vertex_dump(second)

    assert parsed_first.layout.stride == 92
    assert parsed_second.layout.stride == 120
    assert parsed_first.positions == parsed_second.positions
    assert parsed_first.normals == parsed_second.normals
    assert parsed_first.uvs == parsed_second.uvs
    assert struct.unpack("<6f", parsed_first.uvs) == (
        0.0, 1.0, 1.0, 1.0, 0.0, 0.0)


def test_migoto_index_dump_separates_headers_from_index_rows(tmp_path):
    path = tmp_path / "range.txt"
    path.write_text(
        "byte offset: 0\nfirst index: 43845\nindex count: 6\n"
        "topology: trianglelist\nformat: DXGI_FORMAT_R16_UINT\n\n"
        "8 9 10\n11 12 13\n", encoding="utf-8")

    parsed = parse_index_dump(path)

    assert parsed.indices == (8, 9, 10, 11, 12, 13)
    assert parsed.first_index == 43845
    assert parsed.index_count == 6
    assert parsed.index_format == "DXGI_FORMAT_R16_UINT"


def test_hash_asset_preserves_duplicate_position_vertices_and_authored_normals(
        tmp_path, monkeypatch):
    root = tmp_path / "assets"
    asset = root / "Character"
    asset.mkdir(parents=True)
    _write_json(asset / "hash.json", [{
        "ib": "87654321", "vb0": "12345678",
        "component_name": "BodyA", "object_indexes": [0],
        "object_index_counts": [6],
        "texture_hashes": [[[
            "Diffuse", "dds", "abcdef12"]]],
    }, {
        "ib": "87654321", "vb0": "12345678",
        "component_name": "BodyB", "object_indexes": [6],
        "object_index_counts": [3],
    }])
    rows = [((0, 0, 0), (1, 0, 0), (0, 0)),
            ((1, 0, 0), (0, 1, 0), (1, 0)),
            ((0, 1, 0), (0, 0, 1), (0, 1)),
            ((0, 0, 0), (-1, 0, 0), (0, 0)),
            ((1, 0, 0), (0, -1, 0), (1, 0)),
            ((0, 1, 0), (0, 0, -1), (0, 1))]
    _text_vb(asset / "BodyA-vb0=12345678.txt", 92, rows)
    (asset / "BodyA-ib=87654321.txt").write_text(
        "byte offset: 0\nfirst index: 0\nindex count: 6\n"
        "topology: trianglelist\nformat: DXGI_FORMAT_R16_UINT\n"
        "ib[0]+000: 0\nib[1]+004: 1\n"
        "ib[2]+008: 2\nib[3]+012: 3\nib[4]+016: 4\nib[5]+020: 5\n",
        encoding="utf-8")
    (asset / "BodyB-ib=87654321.txt").write_text(
        "byte offset: 0\nfirst index: 6\nindex count: 3\n"
        "topology: trianglelist\nformat: DXGI_FORMAT_R16_UINT\n"
        "3 4 5\n", encoding="utf-8")
    (asset / "BodyADiffuse.dds").write_bytes(b"not decoded during load")

    index = build_index("ZZMI", str(root))
    parsed_vbs = []
    original_parse_vertex_dump = hash_asset.parse_vertex_dump

    def count_vertex_dump(path):
        parsed_vbs.append(path)
        return original_parse_vertex_dump(path)

    monkeypatch.setattr(hash_asset, "parse_vertex_dump", count_vertex_dump)
    result = load_asset("ZZMI", str(root), index["assets"][0],
                        geometry=GeometryBlob(),
                        texture_source=lambda path, role: f"uri:{role}:{path}")
    assert len(result.parts) == 2
    assert len(parsed_vbs) == 1
    part = result.parts[0]
    assert len(part.positions) == 6 * 3 * 4
    normals = struct.unpack("<18f", part.normals)
    assert normals[:3] == (1.0, 0.0, 0.0)
    assert normals[9:12] == (-1.0, 0.0, 0.0)
    assert part.asset_source["component_name"] == "BodyA"
    assert result.payload["textures"]
    assert all(value.startswith("uri:diffuse:")
               for value in result.payload["textures"].values())


def test_wwmi_reverses_winding_without_rewriting_authored_normals(tmp_path):
    root = tmp_path / "assets"
    asset = root / "Character"
    asset.mkdir(parents=True)
    _write_json(asset / "Metadata.json", {
        "vb0_hash": "11111111", "vertex_count": 3,
        "components": [{"vertex_offset": 0, "vertex_count": 3,
                         "index_offset": 300, "index_count": 3,
                         "name": "Body"}],
    })
    values = [((0, 0, 0), (0, 0)), ((1, 0, 0), (1, 0)),
              ((0, 1, 0), (0, 1))]
    data = bytearray()
    for position, uv in values:
        data.extend(struct.pack("<fff", *position))
        data.extend(struct.pack("<ff", *uv))
        data.extend(struct.pack("<fff", 0, 0, 1))
    _fmt(asset / "Component 0.fmt", 32, "DXGI_FORMAT_R32_UINT", [
        ("POSITION", 0, "R32G32B32_FLOAT", 0),
        ("TEXCOORD", 0, "R32G32_FLOAT", 12),
        ("NORMAL", 0, "R32G32B32_FLOAT", 20),
    ])
    (asset / "Component 0.vb").write_bytes(data)
    (asset / "Component 0.ib").write_bytes(
        struct.pack("<III", 0, 2, 1))
    (asset / "candidate.dds").write_bytes(b"not decoded during load")

    index = build_index("WWMI", str(root))
    result = load_asset("WWMI", str(root), index["assets"][0],
                        geometry=GeometryBlob(),
                        texture_source=lambda path, role: f"uri:{role}:{path}")
    part = result.parts[0]
    assert struct.unpack("<3I", part.indices) == (0, 1, 2)
    assert struct.unpack("<9f", part.normals) == (0, 0, 1) * 3
    assert part.first_index == 300
    assert struct.unpack("<6f", part.uvs) == (0, 1, 1, 1, 0, 0)
    assert part.texture_candidates
    assert result.payload["meshes"][part.key]["tex_key"] is None
    assert result.payload["textures"][part.texture_candidates[0].key].startswith(
        "uri:diffuse:")


def test_wwmi_decodes_variable_stride_and_packed_normals(tmp_path):
    root = tmp_path / "assets"
    asset = root / "Character"
    asset.mkdir(parents=True)
    _write_json(asset / "Metadata.json", {
        "vb0_hash": "33333333",
        "components": [{"vertex_count": 3, "index_offset": 300,
                         "index_count": 3, "name": "ShapeKeyPart"}],
    })
    _fmt(asset / "Component 0.fmt", 96, "DXGI_FORMAT_R16_UINT", [
        ("POSITION", 0, "R32G32B32_FLOAT", 0),
        ("NORMAL", 0, "R8G8B8A8_SNORM", 16),
        ("TEXCOORD", 0, "R16G16_FLOAT", 32),
    ])
    data = bytearray(3 * 96)
    for index, position in enumerate(((0, 0, 0), (1, 0, 0), (0, 1, 0))):
        offset = index * 96
        struct.pack_into("<fff", data, offset, *position)
        data[offset + 16:offset + 20] = bytes((127, 0, 0, 0))
        struct.pack_into("<ee", data, offset + 32, 0.25, 0.20)
    (asset / "Component 0.vb").write_bytes(data)
    (asset / "Component 0.ib").write_bytes(struct.pack("<3H", 0, 2, 1))

    index = build_index("WWMI", str(root))
    result = load_asset("WWMI", str(root), index["assets"][0],
                        geometry=GeometryBlob())
    part = result.parts[0]

    assert struct.unpack("<9f", part.normals) == (1, 0, 0) * 3
    assert struct.unpack("<6f", part.uvs) == pytest.approx(
        (0.25, 0.80) * 3, abs=1e-4)


def test_hash_asset_skips_missing_components_and_reports_warning(tmp_path):
    root = tmp_path / "assets"
    asset = root / "Character"
    asset.mkdir(parents=True)
    _write_json(asset / "hash.json", [{
        "ib": "aaaaaaaa", "vb0": "bbbbbbbb", "component_name": "Body",
        "object_indexes": [0], "object_index_counts": [3],
    }, {
        "ib": "cccccccc", "vb0": "", "component_name": "Face",
        "object_indexes": [0], "object_index_counts": [3],
    }])
    _text_vb(asset / "Body-vb0=bbbbbbbb.txt", 92, [
        ((0, 0, 0), (0, 0, 1), (0, 0)),
        ((1, 0, 0), (0, 0, 1), (1, 0)),
        ((0, 1, 0), (0, 0, 1), (0, 1)),
    ])
    (asset / "Body-ib=aaaaaaaa.txt").write_text(
        "first index: 0\nindex count: 3\ntopology: trianglelist\n"
        "0 1 2\n", encoding="utf-8")

    index = build_index("ZZMI", str(root))
    result = load_asset("ZZMI", str(root), index["assets"][0],
                        geometry=GeometryBlob())

    assert len(result.parts) == 1
    warnings = result.payload["metadata"]["asset"]["warnings"]
    assert any(item["component"] == "Face"
               and item["reason"] == "vertex_dump_missing"
               for item in warnings)


def test_api_load_asset_publishes_shared_payload_without_ini(tmp_path, monkeypatch):
    root = tmp_path / "assets"
    asset = root / "Character"
    asset.mkdir(parents=True)
    _write_json(asset / "hash.json", [{
        "ib": "87654321", "vb0": "12345678", "component_name": "Body",
        "object_indexes": [0], "object_index_counts": [3],
    }])
    _text_vb(asset / "Body-vb0=12345678.txt", 92, [
        ((0, 0, 0), (0, 0, 1), (0, 0)),
        ((1, 0, 0), (0, 0, 1), (1, 0)),
        ((0, 1, 0), (0, 0, 1), (0, 1)),
    ])
    (asset / "Body-ib=87654321.txt").write_text(
        "0: 0\n1: 1\n2: 2\n", encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "version": 1, "modFolders": [], "assetFolders": [{
            "type": "ZZMI", "path": asset_folders.normalize_path(str(root)),
            "enabled": False,
        }],
    }), encoding="utf-8")
    monkeypatch.setattr(paths, "config_path", lambda: str(config))
    index = build_index("ZZMI", str(root))
    from app.asset_index import save_index
    save_index(index)

    api = ModViewerAPI()
    result = api.load_asset(str(asset))

    assert result["metadata"]["source_kind"] == "asset"
    assert result["metadata"]["asset"]["type"] == "ZZMI"
    assert result["controls"] == {
        "toggles": {}, "menu": {}, "present": {"target_inis": [], "item": None}}
    assert result["geometry"]["url"].startswith("/geometry/")
    assert server.active_texture_publication(str(asset)) is not None


def test_api_rejects_category_and_keeps_disabled_root_browsable(tmp_path, monkeypatch):
    root = tmp_path / "assets"
    asset = root / "Character"
    asset.mkdir(parents=True)
    _write_json(asset / "hash.json", [{"ib": "87654321"}])
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "version": 1, "modFolders": [], "assetFolders": [{
            "type": "GIMI", "path": asset_folders.normalize_path(str(root)),
            "enabled": False,
        }],
    }), encoding="utf-8")
    monkeypatch.setattr(paths, "config_path", lambda: str(config))
    index = build_index("GIMI", str(root))
    from app.asset_index import save_index
    save_index(index)
    api = ModViewerAPI()

    listing = api.list_asset_subfolders(str(root))
    assert listing["folders"][0]["asset"] is True
    assert "error" in api.load_asset(str(root))

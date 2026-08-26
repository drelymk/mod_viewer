"""Focused direct Asset loading regressions."""

import json
import math
import os
import struct

import pytest

from app import asset_folders, asset_index, asset_textures, paths, server
from app.asset_index import build_index
from app.asset_loader import hash_asset, load_asset
from app.asset_loader.wwmi import _component_texture_candidates
from app.asset_loader.models import AssetLoadResult, AssetMeshPart
from app.api import ModViewerAPI
from core.component_coverage import ComponentCoverageKey
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


def _index_text(indices):
    return ("first index: 0\n"
            f"index count: {len(indices)}\n"
            "topology: trianglelist\n"
            + "\n".join(" ".join(str(value) for value in indices[index:index + 3])
                         for index in range(0, len(indices), 3)) + "\n")


def _gimi_face_asset(root, *, include_eyes=True):
    asset = root / "Character"
    asset.mkdir(parents=True)
    entries = []
    body_rows = []
    if include_eyes:
        body_rows = [
            ((-0.79, 2, 3), (0, 0, 1), (0, 0)),
            ((-0.75, 2, 3), (0, 0, 1), (1, 0)),
            ((-0.75, 2.04, 3), (0, 0, 1), (0, 1)),
            ((-0.71, 1.96, 3), (0, 0, 1), (1, 1)),
            ((0.71, 2, 3), (0, 0, 1), (0, 0)),
            ((0.75, 2, 3), (0, 0, 1), (1, 0)),
            ((0.75, 2.04, 3), (0, 0, 1), (0, 1)),
            ((0.79, 1.96, 3), (0, 0, 1), (1, 1)),
        ]
        body_indices = (0, 1, 2, 4, 5, 6)
        _text_vb(asset / "CharacterEyesA-vb0=11111111.txt", 92, body_rows)
        (asset / "CharacterEyesA-ib=aaaaaaaa.txt").write_text(
            _index_text(body_indices), encoding="utf-8")
        entries.append({
            "ib": "aaaaaaaa", "vb0": "11111111", "component_name": "Eyes",
            "object_indexes": [0], "object_index_counts": [len(body_indices)],
        })

    face_centers = ((4, -2, 6.25), (4, -2, 7.75))
    face_rows = []
    face_indices = []
    for center in face_centers:
        center_index = len(face_rows)
        face_rows.append((center, (-1, 0, 0), (0, 0)))
        ring = []
        for ordinal in range(8):
            angle = 2 * 3.141592653589793 * ordinal / 8
            ring.append(len(face_rows))
            face_rows.append((
                (center[0], center[1] + 0.15 * math.sin(angle),
                 center[2] + 0.15 * math.cos(angle)),
                (-1, 0, 0), (0, 0)))
        for ordinal, first in enumerate(ring):
            face_indices.extend((center_index, first, ring[(ordinal + 1) % 8]))
    _text_vb(asset / "CharacterFaceEyeA-vb0=22222222.txt", 92, face_rows)
    (asset / "CharacterFaceEyeA-ib=bbbbbbbb.txt").write_text(
        _index_text(face_indices), encoding="utf-8")
    entries.append({
        "ib": "bbbbbbbb", "vb0": "22222222", "component_name": "FaceEye",
        "object_indexes": [0], "object_index_counts": [len(face_indices)],
    })

    mouth_rows = [
        ((4, -2.4, 7.1), (-1, 0, 0), (0, 0)),
        ((4, -2.3, 7), (-1, 0, 0), (1, 0)),
        ((4, -2.4, 6.9), (-1, 0, 0), (0, 1)),
    ]
    mouth_indices = (0, 1, 2)
    _text_vb(asset / "CharacterFaceMouthA-vb0=33333333.txt", 92, mouth_rows)
    (asset / "CharacterFaceMouthA-ib=cccccccc.txt").write_text(
        _index_text(mouth_indices), encoding="utf-8")
    entries.append({
        "ib": "cccccccc", "vb0": "33333333", "component_name": "Mouth",
        "object_indexes": [0], "object_index_counts": [3],
    })
    _write_json(asset / "hash.json", entries)
    return asset, body_rows, face_centers, mouth_rows


def _wwmi_triangle(folder, *, component_name="Body", vb_hash="11111111",
                   image=False):
    _write_json(folder / "Metadata.json", {
        "vb0_hash": vb_hash,
        "components": [{"vertex_count": 3, "index_offset": 0,
                         "index_count": 3, "name": component_name}],
    })
    _fmt(folder / "Component 0.fmt", 32, "DXGI_FORMAT_R16_UINT", [
        ("POSITION", 0, "R32G32B32_FLOAT", 0),
        ("TEXCOORD", 0, "R32G32_FLOAT", 12),
        ("NORMAL", 0, "R32G32B32_FLOAT", 20),
    ])
    data = bytearray()
    for position, uv in [((0, 0, 0), (0, 0)),
                         ((1, 0, 0), (1, 0)),
                         ((0, 1, 0), (0, 1))]:
        data.extend(struct.pack("<fff", *position))
        data.extend(struct.pack("<ff", *uv))
        data.extend(struct.pack("<fff", 0, 0, 1))
    (folder / "Component 0.vb").write_bytes(data)
    (folder / "Component 0.ib").write_bytes(struct.pack("<3H", 0, 2, 1))
    if image:
        (folder / "Components-0 t=candidate.dds").write_bytes(
            b"not decoded during load")


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


def test_gimi_face_parts_align_to_native_eyes_and_keep_eyes_unchanged(tmp_path):
    root = tmp_path / "assets"
    asset, body_rows, face_centers, mouth_rows = _gimi_face_asset(root)
    index = build_index("GIMI", str(root))

    result = load_asset("GIMI", str(root), index["assets"][0],
                        geometry=GeometryBlob())

    parts = {part.component_name: part for part in result.parts}
    assert set(parts) == {"Eyes", "FaceEye", "Mouth"}
    assert struct.unpack("<18f", parts["Eyes"].positions) == pytest.approx(
        tuple(value for index in (0, 1, 2, 4, 5, 6)
              for value in body_rows[index][0]))
    face_positions = struct.unpack("<3f", parts["FaceEye"].positions[:12])
    assert face_positions == pytest.approx((-0.75, 2, 3))
    mouth_positions = struct.unpack("<3f", parts["Mouth"].positions[:12])
    assert mouth_positions == pytest.approx((0.1, 1.6, 3), abs=1e-5)
    assert struct.unpack("<3f", parts["Mouth"].normals[:12]) == pytest.approx(
        (0, 0, 1))
    assert not any(item["reason"] == "face_alignment_unavailable"
                   for item in result.payload["metadata"]["asset"]["warnings"])


def test_gimi_mouth_only_filter_uses_alignment_dependencies_without_emitting_them(
        tmp_path):
    root = tmp_path / "assets"
    asset, _body_rows, _face_centers, _mouth_rows = _gimi_face_asset(root)
    index = build_index("GIMI", str(root))

    result = load_asset(
        "GIMI", str(root), index["assets"][0], geometry=GeometryBlob(),
        part_filter={ComponentCoverageKey("cccccccc", 0, 3)})

    assert len(result.parts) == 1
    assert result.parts[0].component_name == "Mouth"
    assert struct.unpack("<3f", result.parts[0].positions[:12]) == \
        pytest.approx((0.1, 1.6, 3), abs=1e-5)


def test_gimi_face_detection_uses_component_metadata_over_filename_heuristics():
    record = hash_asset._HashAssetRecord(
        metadata_path="Character/hash.json", entry={},
        component_name="Eyewear", geometry_hash="aabbccdd",
        vb_hash="11223344", ranges=(),
        vb_file="CharacterFaceEyeA-vb0=11223344.txt", ib_files=())
    assert not hash_asset._is_face_local_record(record)

    unnamed = hash_asset._HashAssetRecord(
        metadata_path="Character/hash.json", entry={},
        component_name=None, geometry_hash="aabbccdd",
        vb_hash="11223344", ranges=(),
        vb_file="CharacterFaceEyeA-vb0=11223344.txt", ib_files=())
    assert hash_asset._is_face_local_record(unnamed)


def test_gimi_face_alignment_failure_keeps_raw_geometry_and_warns(tmp_path):
    root = tmp_path / "assets"
    asset, _body_rows, face_centers, _mouth_rows = _gimi_face_asset(
        root, include_eyes=False)
    index = build_index("GIMI", str(root))

    result = load_asset("GIMI", str(root), index["assets"][0],
                        geometry=GeometryBlob())

    face = next(part for part in result.parts
                if part.component_name == "FaceEye")
    assert struct.unpack("<3f", face.positions[:12]) == pytest.approx(face_centers[0])
    assert any(item["reason"] == "face_alignment_unavailable"
               for item in result.payload["metadata"]["asset"]["warnings"])


def test_zzmi_face_named_parts_are_not_gimi_aligned(tmp_path):
    root = tmp_path / "assets"
    _asset, _body_rows, _face_centers, mouth_rows = _gimi_face_asset(root)
    index = build_index("ZZMI", str(root))

    result = load_asset("ZZMI", str(root), index["assets"][0],
                        geometry=GeometryBlob())

    mouth = next(part for part in result.parts
                 if part.component_name == "Mouth")
    assert struct.unpack("<3f", mouth.positions[:12]) == pytest.approx(
        mouth_rows[0][0])


def test_hash_asset_does_not_use_same_label_wrong_hash_vertex_dump(tmp_path):
    root = tmp_path / "assets"
    asset = root / "Character"
    asset.mkdir(parents=True)
    _write_json(asset / "hash.json", [{
        "ib": "87654321", "vb0": "12345678", "component_name": "Body",
        "object_indexes": [0], "object_index_counts": [3],
    }, {
        "ib": "abcdef12", "vb0": "fedcba98", "component_name": "Face",
        "object_indexes": [0], "object_index_counts": [3],
    }])
    rows = [((0, 0, 0), (0, 0, 1), (0, 0)),
            ((1, 0, 0), (0, 0, 1), (1, 0)),
            ((0, 1, 0), (0, 0, 1), (0, 1))]
    _text_vb(asset / "Body-vb0=deadbeef.txt", 92, rows)
    _text_vb(asset / "Face-vb0=fedcba98.txt", 92, rows)
    (asset / "Body-ib=87654321.txt").write_text(
        "first index: 0\nindex count: 3\ntopology: trianglelist\n"
        "0 1 2\n", encoding="utf-8")
    (asset / "Face-ib=abcdef12.txt").write_text(
        "first index: 0\nindex count: 3\ntopology: trianglelist\n"
        "0 1 2\n", encoding="utf-8")

    index = build_index("ZZMI", str(root))
    result = load_asset("ZZMI", str(root), index["assets"][0],
                        geometry=GeometryBlob())

    assert len(result.parts) == 1
    assert result.parts[0].component_name == "Face"
    assert any(item["component"] == "Body"
               and item["reason"] == "vertex_dump_missing"
               for item in result.payload["metadata"]["asset"]["warnings"])


def test_hash_asset_skips_corrupt_same_hash_ib_and_keeps_valid_ranges(tmp_path):
    root = tmp_path / "assets"
    asset = root / "Character"
    asset.mkdir(parents=True)
    _write_json(asset / "hash.json", [{
        "ib": "87654321", "vb0": "12345678", "component_name": "Body",
        "object_indexes": [0, 3], "object_index_counts": [3, 3],
        "object_classifications": ["A", "B"],
    }])
    _text_vb(asset / "Body-vb0=12345678.txt", 92, [
        ((0, 0, 0), (0, 0, 1), (0, 0)),
        ((1, 0, 0), (0, 0, 1), (1, 0)),
        ((0, 1, 0), (0, 0, 1), (0, 1)),
        ((0, 0, 0), (0, 0, 1), (0, 0)),
        ((1, 0, 0), (0, 0, 1), (1, 0)),
        ((0, 1, 0), (0, 0, 1), (0, 1)),
    ])
    (asset / "BodyA-ib=87654321.txt").write_text(
        "this index dump is corrupt", encoding="utf-8")
    (asset / "BodyB-ib=87654321.txt").write_text(
        "first index: 3\nindex count: 3\ntopology: trianglelist\n"
        "3 4 5\n", encoding="utf-8")

    index = build_index("GIMI", str(root))
    result = load_asset("GIMI", str(root), index["assets"][0],
                        geometry=GeometryBlob())

    assert len(result.parts) == 1
    assert result.parts[0].label == "Body B 2"


def test_hash_asset_uses_resolved_ib_count_when_metadata_omits_count(tmp_path):
    root = tmp_path / "assets"
    asset = root / "Character"
    asset.mkdir(parents=True)
    _write_json(asset / "hash.json", [{
        "ib": "87654321", "vb0": "12345678", "component_name": "Hair",
        "object_indexes": [0],
    }])
    _text_vb(asset / "Hair-vb0=12345678.txt", 92, [
        ((0, 0, 0), (0, 0, 1), (0, 0)),
        ((1, 0, 0), (0, 0, 1), (1, 0)),
        ((0, 1, 0), (0, 0, 1), (0, 1)),
    ])
    (asset / "Hair-ib=87654321.txt").write_text(
        "first index: 0\nindex count: 3\ntopology: trianglelist\n"
        "0 1 2\n", encoding="utf-8")

    index = build_index("ZZMI", str(root))
    result = load_asset("ZZMI", str(root), index["assets"][0],
                        geometry=GeometryBlob())
    part = result.parts[0]

    assert part.index_count == 3
    assert result.payload["meshes"][part.key]["drawindexed"][0] == 3


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


def test_hash_asset_part_filter_skips_unrequested_ranges(tmp_path, monkeypatch):
    root = tmp_path / "assets"
    asset = root / "Character"
    asset.mkdir(parents=True)
    _write_json(asset / "hash.json", [{
        "ib": "87654321", "vb0": "12345678", "component_name": "Body",
        "object_indexes": [0, 3], "object_index_counts": [3, 3],
    }])
    _text_vb(asset / "Body-vb0=12345678.txt", 92, [
        ((0, 0, 0), (0, 0, 1), (0, 0)),
        ((1, 0, 0), (0, 0, 1), (1, 0)),
        ((0, 1, 0), (0, 0, 1), (0, 1)),
        ((0, 0, 0), (0, 0, 1), (0, 0)),
        ((1, 0, 0), (0, 0, 1), (1, 0)),
        ((0, 1, 0), (0, 0, 1), (0, 1)),
    ])
    for name, first in (("BodyA", 0), ("BodyB", 3)):
        (asset / f"{name}-ib=87654321.txt").write_text(
            f"first index: {first}\nindex count: 3\n"
            "topology: trianglelist\n0 1 2\n", encoding="utf-8")

    index = build_index("ZZMI", str(root))
    parsed_vbs = []
    original_parse = hash_asset.parse_vertex_dump

    def count_vertex_dump(path):
        parsed_vbs.append(path)
        return original_parse(path)

    monkeypatch.setattr(hash_asset, "parse_vertex_dump", count_vertex_dump)
    result = load_asset(
        "ZZMI", str(root), index["assets"][0], geometry=GeometryBlob(),
        part_filter={ComponentCoverageKey("87654321", 3, 3)})

    assert len(result.parts) == 1
    assert result.parts[0].first_index == 3
    assert len(parsed_vbs) == 1


def test_asset_parts_with_same_component_share_one_texture_pool(tmp_path):
    root = tmp_path / "assets"
    asset = root / "Character"
    asset.mkdir(parents=True)
    _write_json(asset / "hash.json", [{
        "ib": "87654321", "vb0": "12345678", "component_name": "Hair",
        "object_indexes": [0, 3], "object_index_counts": [3, 3],
        "object_classifications": ["A", "B"],
        "texture_hashes": [
            [["Diffuse", "dds", "aaaa1111"]],
            [["Diffuse", "dds", "bbbb2222"]],
        ],
    }])
    _text_vb(asset / "Hair-vb0=12345678.txt", 92, [
        ((0, 0, 0), (0, 0, 1), (0, 0)),
        ((1, 0, 0), (0, 0, 1), (1, 0)),
        ((0, 1, 0), (0, 0, 1), (0, 1)),
        ((0, 0, 0), (0, 0, 1), (0, 0)),
        ((1, 0, 0), (0, 0, 1), (1, 0)),
        ((0, 1, 0), (0, 0, 1), (0, 1)),
    ])
    (asset / "HairA-ib=87654321.txt").write_text(
        "first index: 0\nindex count: 3\ntopology: trianglelist\n"
        "0 1 2\n", encoding="utf-8")
    (asset / "HairB-ib=87654321.txt").write_text(
        "first index: 3\nindex count: 3\ntopology: trianglelist\n"
        "3 4 5\n", encoding="utf-8")
    (asset / "HairA-Diffuse-aaaa1111.dds").write_bytes(b"a")
    (asset / "HairB-Diffuse-bbbb2222.dds").write_bytes(b"b")

    index = build_index("ZZMI", str(root))
    result = load_asset("ZZMI", str(root), index["assets"][0],
                        geometry=GeometryBlob())

    entries = list(result.payload["meshes"].values())
    pool_ids = {entry["texture_pool_id"] for entry in entries}
    assert len(pool_ids) == 1
    pool = result.payload["texture_pools"][next(iter(pool_ids))]
    assert {item["tex_key"] for item in pool} == {
        entries[0]["tex_key"], entries[1]["tex_key"]}


def test_hash_asset_prefers_generic_texture_over_more_specific_suffix_match(
        tmp_path):
    root = tmp_path / "assets"
    asset = root / "Amber"
    asset.mkdir(parents=True)
    _write_json(asset / "hash.json", [{
        "ib": "87654321", "vb0": "12345678", "component_name": "",
        "object_indexes": [0], "object_classifications": ["Head"],
        "texture_hashes": [[["Diffuse", ".dds", "deadbeef"]]],
    }])
    _text_vb(asset / "Amber-vb0=12345678.txt", 92, [
        ((0, 0, 0), (0, 0, 1), (0, 0)),
        ((1, 0, 0), (0, 0, 1), (1, 0)),
        ((0, 1, 0), (0, 0, 1), (0, 1)),
    ])
    (asset / "Amber-ib=87654321.txt").write_text(
        "first index: 0\nindex count: 3\ntopology: trianglelist\n"
        "0 1 2\n", encoding="utf-8")
    (asset / "AmberHeadDiffuse.dds").write_bytes(b"generic")
    (asset / "AmberFaceHeadDiffuse.dds").write_bytes(b"specific")

    index = build_index("GIMI", str(root))
    result = load_asset("GIMI", str(root), index["assets"][0],
                        geometry=GeometryBlob())

    entry = next(iter(result.payload["meshes"].values()))
    assert entry["tex_key"].endswith("/Amber/AmberHeadDiffuse.dds")


def test_hash_asset_recovers_unique_range_texture_families(tmp_path):
    root = tmp_path / "assets"
    asset = root / "Character"
    asset.mkdir(parents=True)
    _write_json(asset / "hash.json", [{
        "ib": "87654321", "vb0": "12345678", "component_name": "HairWings",
        "object_indexes": [0, 3], "object_index_counts": [3, 3],
        "object_classifications": ["Head", "Body"],
        "texture_hashes": [[], []],
    }])
    _text_vb(asset / "HairWings-vb0=12345678.txt", 92, [
        ((0, 0, 0), (0, 0, 1), (0, 0)),
        ((1, 0, 0), (0, 0, 1), (1, 0)),
        ((0, 1, 0), (0, 0, 1), (0, 1)),
        ((0, 0, 0), (0, 0, 1), (0, 0)),
        ((1, 0, 0), (0, 0, 1), (1, 0)),
        ((0, 1, 0), (0, 0, 1), (0, 1)),
    ])
    for name, first in (
            ("HairWingsHead", 0), ("HeadPieceA", 0),
            ("HairWingsBody", 3), ("HeadPieceB", 3)):
        (asset / f"{name}-ib=87654321.txt").write_text(
            f"first index: {first}\nindex count: 3\ntopology: trianglelist\n"
            f"{first} {first + 1} {first + 2}\n", encoding="utf-8")
    (asset / "HeadPieceADiffuse.dds").write_bytes(b"head")
    (asset / "HeadPieceBDiffuse.dds").write_bytes(b"body")

    index = build_index("GIMI", str(root))
    result = load_asset("GIMI", str(root), index["assets"][0],
                        geometry=GeometryBlob())

    entries = list(result.payload["meshes"].values())
    assert entries[0]["tex_key"].endswith("/HeadPieceADiffuse.dds")
    assert entries[1]["tex_key"].endswith("/HeadPieceBDiffuse.dds")


def test_hash_asset_loads_immediate_nested_hash_metadata(tmp_path):
    root = tmp_path / "assets"
    asset = root / "Columbina"
    nested = asset / "ColumbinaFace"
    nested.mkdir(parents=True)
    _write_json(asset / "hash.json", [{
        "ib": "87654321", "vb0": "12345678", "component_name": "Body",
        "object_indexes": [0], "object_classifications": ["Head"],
    }])
    _write_json(nested / "hash.json", [{
        "ib": "abcdef12", "vb0": "fedcba98", "component_name": "Eye",
        "object_indexes": [0], "object_classifications": ["Head"],
    }])
    rows = [((0, 0, 0), (0, 0, 1), (0, 0)),
            ((1, 0, 0), (0, 0, 1), (1, 0)),
            ((0, 1, 0), (0, 0, 1), (0, 1))]
    _text_vb(asset / "Body-vb0=12345678.txt", 92, rows)
    _text_vb(nested / "Eye-vb0=fedcba98.txt", 92, rows)
    for folder, name, geometry_hash in (
            (asset, "Body", "87654321"),
            (nested, "Eye", "abcdef12")):
        (folder / f"{name}-ib={geometry_hash}.txt").write_text(
            "first index: 0\nindex count: 3\ntopology: trianglelist\n"
            "0 1 2\n", encoding="utf-8")

    index = build_index("GIMI", str(root))
    result = load_asset("GIMI", str(root), index["assets"][0],
                        geometry=GeometryBlob())

    assert {part.component_name for part in result.parts} == {"Body", "Eye"}
    assert {part.label for part in result.parts} == {"Body Head", "Eye Head"}


def test_hash_asset_loads_both_metadata_sources_for_shared_geometry_hash(tmp_path):
    root = tmp_path / "assets"
    asset = root / "Character"
    nested = asset / "Face"
    nested.mkdir(parents=True)
    _write_json(asset / "hash.json", [{
        "ib": "87654321", "vb0": "12345678", "component_name": "Body",
        "object_indexes": [0], "object_classifications": ["Head"],
    }])
    _write_json(nested / "hash.json", [{
        "ib": "87654321", "vb0": "fedcba98", "component_name": "Face",
        "object_indexes": [0], "object_classifications": ["Head"],
    }])
    rows = [((0, 0, 0), (0, 0, 1), (0, 0)),
            ((1, 0, 0), (0, 0, 1), (1, 0)),
            ((0, 1, 0), (0, 0, 1), (0, 1))]
    _text_vb(asset / "Body-vb0=12345678.txt", 92, rows)
    _text_vb(nested / "Face-vb0=fedcba98.txt", 92, rows)
    (asset / "Body-ib=87654321.txt").write_text(
        "first index: 0\nindex count: 3\ntopology: trianglelist\n"
        "0 1 2\n", encoding="utf-8")
    (nested / "Face-ib=87654321.txt").write_text(
        "first index: 0\nindex count: 3\ntopology: trianglelist\n"
        "0 1 2\n", encoding="utf-8")

    index = build_index("GIMI", str(root))
    result = load_asset("GIMI", str(root), index["assets"][0],
                        geometry=GeometryBlob())

    assert {part.component_name for part in result.parts} == {"Body", "Face"}
    assert {part.key.split("::")[1] for part in result.parts} == {
        "Character/hash.json", "Character/Face/hash.json"}


def test_wwmi_skips_corrupt_metadata_and_loads_valid_sibling(tmp_path):
    root = tmp_path / "assets"
    asset = root / "Character"
    valid = asset / "ObjectA"
    invalid = asset / "ObjectB"
    valid.mkdir(parents=True)
    invalid.mkdir(parents=True)
    _wwmi_triangle(valid)
    (invalid / "Metadata.json").write_text("{", encoding="utf-8")
    record = {"path": "Character", "geometry": [
        {"hash": "11111111", "metadata": "Character/ObjectA/Metadata.json"},
        {"hash": "22222222", "metadata": "Character/ObjectB/Metadata.json"},
    ]}

    result = load_asset("WWMI", str(root), record, geometry=GeometryBlob())

    assert len(result.parts) == 1
    assert result.parts[0].component_name == "Body"
    assert any(item["reason"] == "metadata_invalid"
               and "ObjectB/Metadata.json" in item["message"]
               for item in result.payload["metadata"]["asset"]["warnings"])


def test_wwmi_texture_candidates_use_registered_asset_root(tmp_path):
    root = tmp_path / "assets"
    asset = root / "Character"
    object_dir = asset / "ObjectA"
    object_dir.mkdir(parents=True)
    _wwmi_triangle(object_dir, image=True)
    record = {"path": "Character", "geometry": [{
        "hash": "11111111", "metadata": "Character/ObjectA/Metadata.json",
    }]}

    result = load_asset("WWMI", str(root), record, geometry=GeometryBlob())
    candidate = result.parts[0].texture_candidates[0]

    assert candidate.key == asset_textures.asset_texture_key(
        str(root), str(object_dir / "Components-0 t=candidate.dds"),
        "diffuse")
    assert candidate.key != asset_textures.asset_texture_key(
        str(object_dir), str(object_dir / "Components-0 t=candidate.dds"),
        "diffuse")


def test_wwmi_texture_candidates_are_filtered_by_component_filename(tmp_path):
    names = [
        "Components-2 t=A.dds",
        "Components-0-1-4 t=B.dds",
        "Components-0-2 t=C.dds",
        "Components-2-3 t=D.dds",
        "Components-3 t=E.dds",
        "SomeTexture.dds",
    ]
    files = []
    for name in names:
        path = tmp_path / name
        path.write_bytes(b"not decoded during load")
        files.append(str(path))
    files.append(files[0])

    def texture_source(path, role):
        return f"uri:{role}:{path}"

    def candidate_names(ordinal):
        return {os.path.basename(item.path)
                for item in _component_texture_candidates(
                    files, str(tmp_path), ordinal, texture_source)}

    assert candidate_names(0) == {"Components-0-1-4 t=B.dds",
                                  "Components-0-2 t=C.dds"}
    assert candidate_names(1) == {"Components-0-1-4 t=B.dds"}
    assert candidate_names(2) == {"Components-2 t=A.dds",
                                  "Components-0-2 t=C.dds",
                                  "Components-2-3 t=D.dds"}
    assert candidate_names(3) == {"Components-2-3 t=D.dds",
                                  "Components-3 t=E.dds"}
    assert candidate_names(4) == {"Components-0-1-4 t=B.dds"}
    assert candidate_names(5) == set()

    candidates = _component_texture_candidates(
        files, str(tmp_path), 2, texture_source)
    assert all(item.role is None and item.source == "candidate"
               for item in candidates)


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
    (asset / "Components-0 t=candidate.dds").write_bytes(
        b"not decoded during load")

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


def test_wwmi_keeps_component_with_invalid_authored_normal_stream(tmp_path):
    root = tmp_path / "assets"
    asset = root / "Character"
    asset.mkdir(parents=True)
    _write_json(asset / "Metadata.json", {
        "vb0_hash": "44444444",
        "components": [{"vertex_count": 3, "index_offset": 0,
                         "index_count": 3, "name": "Body"}],
    })
    _fmt(asset / "Component 0.fmt", 32, "DXGI_FORMAT_R16_UINT", [
        ("POSITION", 0, "R32G32B32_FLOAT", 0),
        ("NORMAL", 0, "R8G8B8A8_SNORM", 16),
    ])
    data = bytearray(3 * 32)
    for index, position in enumerate(((0, 0, 0), (1, 0, 0), (0, 1, 0))):
        offset = index * 32
        struct.pack_into("<fff", data, offset, *position)
    data[16:20] = bytes((127, 0, 0, 0))
    data[48:52] = bytes((0, 0, 0, 127))
    data[80:84] = bytes((0, 127, 0, 0))
    (asset / "Component 0.vb").write_bytes(data)
    (asset / "Component 0.ib").write_bytes(struct.pack("<3H", 0, 2, 1))

    index = build_index("WWMI", str(root))
    result = load_asset("WWMI", str(root), index["assets"][0],
                        geometry=GeometryBlob())

    assert len(result.parts) == 1
    assert result.parts[0].normals is None


def test_asset_components_with_duplicate_hash_folders_get_distinct_identity():
    geometry = GeometryBlob()
    parts = tuple(AssetMeshPart(
        key=f"part-{geometry_hash}", label="Part 1", asset_type="WWMI",
        asset_path="Character", geometry_hash=geometry_hash,
        component_name=None, classification=None, component_ordinal=0,
        first_index=0, index_count=3, positions=b"\0" * 36,
        indices=b"\0" * 12)
        for geometry_hash in ("aaaabbbb", "ccccdddd"))

    result = AssetLoadResult.from_parts(
        "WWMI", "assets", {"path": "Character"}, parts, geometry=geometry)

    assert {entry["component"] for entry in result.payload["meshes"].values()} == {
        "Part 1 [aaaabbbb]", "Part 1 [ccccdddd]"}


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


def test_api_load_missing_asset_parts_is_incremental_and_reversible(
        tmp_path, monkeypatch):
    asset_root = tmp_path / "assets"
    asset = asset_root / "Character"
    asset.mkdir(parents=True)
    _write_json(asset / "hash.json", [
        {"ib": "aaaaaaaa", "vb0": "11111111", "component_name": "Body",
         "object_indexes": [0], "object_index_counts": [3]},
        {"ib": "bbbbbbbb", "vb0": "22222222", "component_name": "Hair",
         "object_indexes": [0], "object_index_counts": [3]},
    ])
    rows = [
        ((0, 0, 0), (0, 0, 1), (0, 0)),
        ((1, 0, 0), (0, 0, 1), (1, 0)),
        ((0, 1, 0), (0, 0, 1), (0, 1)),
    ]
    _text_vb(asset / "Body-vb0=11111111.txt", 92, rows)
    _text_vb(asset / "Hair-vb0=22222222.txt", 92, rows)
    for name, hash_value in (("Body", "aaaaaaaa"), ("Hair", "bbbbbbbb")):
        (asset / f"{name}-ib={hash_value}.txt").write_text(
            "first index: 0\nindex count: 3\ntopology: trianglelist\n"
            "0 1 2\n", encoding="utf-8")
    index = build_index("ZZMI", str(asset_root))
    monkeypatch.setattr(asset_index, "load_index", lambda _type, _root: index)

    mod = tmp_path / "mod"
    mod.mkdir()
    (mod / "mod.ini").write_text(
        "[TextureOverrideBody]\n"
        "hash = aaaaaaaa\n"
        "drawindexed = 3, 0, 0\n"
        "run = CommandList\\ZZMI\\SetTextures\n", encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "version": 1,
        "modFolders": [{"name": "Test", "path": str(mod)}],
        "assetFolders": [{"type": "ZZMI", "path": str(asset_root),
                           "enabled": True}],
    }), encoding="utf-8")
    monkeypatch.setattr(paths, "config_path", lambda: str(config))

    api = ModViewerAPI()
    result = api.load_missing_asset_parts(str(mod))

    assert result["status"] == "loaded"
    old_fill_id = result["fill_id"]
    assert result["coverage"]["handled_parts"] == 1
    assert result["coverage"]["missing_parts"] == 1
    assert len(result["payload"]["meshes"]) == 1
    entry = next(iter(result["payload"]["meshes"].values()))
    assert entry["asset_fill"] is True
    assert entry["conditions"] == []
    assert result["payload"]["metadata"]["source_kind"] == "asset-fill"

    preview = api.load_asset(str(asset))
    assert preview["metadata"]["source_kind"] == "asset"

    replacement = api.load_missing_asset_parts(str(mod))
    assert replacement["status"] == "loaded"
    new_fill_id = replacement["fill_id"]
    assert new_fill_id != old_fill_id

    stale = api.remove_missing_asset_parts(str(mod), old_fill_id)
    assert stale == {"status": "removed", "removed": False, "stale": True}
    key = os.path.normcase(os.path.abspath(str(mod)))
    assert api._asset_fill_sessions[key]["fill_id"] == new_fill_id

    preview = api.load_asset(str(asset))
    assert preview["metadata"]["source_kind"] == "asset"

    removed = api.remove_missing_asset_parts(str(mod))
    assert removed == {"status": "removed", "removed": False}


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

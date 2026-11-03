"""Automatic facial grouping requires independent material and topology evidence."""

import copy
import math
from pathlib import Path
import struct

import pytest
from PIL import Image

from app.assets import index, loader
from app.assets.extractor import discovery
from app.assets.extractor.core import Analysis, Component, Context, Part
from app.assets.extractor.export import Selection, _component_folders, export, extract
from app.assets.extractor.geometry import Element, Mesh
from app.assets.extractor.profiles.gimi_faces import classify_faces
from core.geometry.migoto_dump import parse_index_dump, parse_vertex_dump
from app.assets.extractor.textures import digest
from tests.support.extraction_data import header


def _discs(centers, radius=0.1, *, annular=False, native=False):
    positions, indices = [], []
    for center in centers:
        start = len(positions)
        for r in (radius, radius * 0.7) if annular else (radius,):
            for i in range(12):
                angle = math.tau * i / 12
                horizontal = (r * math.cos(angle), 0, 0) if native else (0, 0, r * math.cos(angle))
                positions.append(tuple(center[a] + horizontal[a] + (r * math.sin(angle) if a == 1 else 0) for a in range(3)))
        if annular:
            for i in range(12):
                a, b = start + i, start + (i + 1) % 12
                indices.extend((a, b, a + 12, b, b + 12, a + 12))
        else:
            positions.append(center)
            for i in range(12):
                indices.extend((start + 12, start + i, start + (i + 1) % 12))
    return positions, indices


def _mesh(number, centers, *, annular=False, native=False, radius=0.1):
    positions, indices = _discs(centers, radius, annular=annular, native=native)
    textures = [dict(role=role, hash=f"{i + 6:08x}", sha256=str(i + 6) * 64, auxiliary=False)
                for i, role in enumerate(("Diffuse", "LightMap"))]
    sources = {role: dict(hash=f"{number * 16 + i:08x}",
                         resource=dict(address=f"buffer-{number}-{i}", version=0))
               for i, role in enumerate(("position", "texcoord"))}
    component = Component(f"surface-{number}", f"Component{number:03d}", f"{number:08x}",
                          "included", "generated geometry", sources,
                          [Part(0, len(indices), 0, "generated-ib.txt", ["000001"], textures)],
                          {} if native else {"geometry_kind": "direct"})
    return Mesh(component, [Element("POSITION", 0, "R32G32B32_FLOAT", 0),
                            Element("NORMAL", 0, "R32G32B32_FLOAT", 12),
                            Element("TEXCOORD", 0, "R32G32_FLOAT", 24)],
                [[p, (1, 0, 0), (0, 0)] for p in positions], [indices], b"", "not_detected")


def _family():
    eyes = _mesh(1, ((-0.75, 2, 3), (0.75, 2, 3)), native=True)
    anchor = _mesh(2, ((0, 0, -0.75), (0, 0, 0.75)), annular=True)
    lower = _mesh(3, ((0, -0.7, 0),), radius=0.4)
    brow = _mesh(4, ((0, 0.35, -0.75), (0, 0.35, 0.75)), radius=0.06)
    duplicate = copy.deepcopy(anchor)
    duplicate.component.id = "alternate-pass"
    duplicate.component.name = "Component005"
    return {m.component.id: m for m in (eyes, anchor, lower, brow, duplicate)}


def test_short_native_eyes_establish_face_family_through_extraction_and_loading(tmp_path, monkeypatch):
    dump = tmp_path / "capture"
    dump.mkdir()
    meshes = list(_family().values())[:-1]
    material = copy.deepcopy(meshes[0].component.parts[0].textures)
    for i, texture in enumerate(material):
        path = dump / f"material-{i}.png"
        Image.new("RGB", (16, 16), (100 + i * 20, 80, 60)).save(path)
        texture.update(file=path.name, sha256=digest(path))
    monkeypatch.setattr(discovery, "collect", lambda *_: copy.deepcopy(material))
    log = ["000001 3DMigoto [ShaderRegex\\GIMIv1\\EnableTextureOverrides]\n"]

    def stream(call, slot, identity, payload, fields, stride, count, topology):
        path = dump / f"{call}-vb{slot}={identity}.buf"
        path.write_bytes(payload)
        path.with_suffix(".txt").write_text(header(stride, slot, fields).replace(
            "vertex count: 4", f"vertex count: {count}").replace("pointlist", topology))

    positions = [("POSITION", 0, "R32G32B32_FLOAT", 0), ("NORMAL", 0, "R32G32B32_FLOAT", 12)]
    # The native reference omits tangents and uses a separate four-influence stream.
    eyes = meshes[0]
    count = len(eyes.vertices)
    stream("000001", 0, "11111111", b"".join(struct.pack("<6f", *v[0], *v[1]) for v in eyes.vertices),
           positions, 24, count, "pointlist")
    stream("000001", 1, "22222222", struct.pack("<4f4I", 1, 0, 0, 0, 3, 0, 0, 0) * count,
           [("BLENDWEIGHT", 0, "R32G32B32A32_FLOAT", 0), ("BLENDINDICES", 0, "R32G32B32A32_SINT", 16)],
           32, count, "pointlist")
    log.append("""000001 SOSetTargets(NumBuffers:1)
       0: resource=0x9000 hash=33333333
000001 IASetVertexBuffers(StartSlot:0, NumBuffers:2)
       0: resource=0x1000 hash=11111111
       1: resource=0x2000 hash=22222222
000001 Draw(VertexCount:%d, StartVertexLocation:0)
000002 SOSetTargets(NumBuffers:0)
""" % count)
    for ordinal, mesh in enumerate(meshes, 2):
        call = f"{ordinal:06d}"
        vertex_count = len(mesh.vertices)
        identity = ordinal
        geometry_hash = f"{identity * 16:08x}"
        if ordinal > 2:
            stream(call, 0, geometry_hash, b"".join(struct.pack("<6f", *v[0], *v[1]) for v in mesh.vertices),
                   positions, 24, vertex_count, "trianglelist")
        stream(call, 1, f"{identity * 16 + 1:08x}", b"".join(struct.pack("<2f", *v[2]) for v in mesh.vertices),
               [("TEXCOORD", 0, "R32G32_FLOAT", 0)], 8, vertex_count, "trianglelist")
        ib = dump / f"{call}-ib={mesh.component.ib}.txt"
        indices = mesh.indices[0]
        ib.write_text(f"first index: 0\nindex count: {len(indices)}\ntopology: trianglelist\nformat: R32_UINT\n\n")
        ib.with_suffix(".buf").write_bytes(struct.pack("<" + "I" * len(indices), *indices))
        binding = "resource=0x9000 hash=33333333" if ordinal == 2 else f"resource=0x{identity * 16:x} hash={geometry_hash}"
        log.append(f"{call} IASetVertexBuffers(StartSlot:0, NumBuffers:2)\n"
                   f"       0: {binding}\n"
                   f"       1: resource=0x{identity * 16 + 1:x} hash={identity * 16 + 1:08x}\n"
                   f"{call} IASetIndexBuffer(pIndexBuffer:0x{identity * 16 + 2:x}, Format:42, Offset:0) hash={mesh.component.ib}\n"
                   f"{call} DrawIndexed(IndexCount:{len(indices)}, StartIndexLocation:0, BaseVertexLocation:0)\n")
    (dump / "log.txt").write_text("".join(log))
    analysis = discovery.analyze(dump)
    assert all(c.status == "included" for c in analysis.components)
    assert [c.provenance["suggested_name"] for c in analysis.components] == [
        "Eyes", "FaceEye", "Face", "FaceEyebrow"]
    assert all(c.default_included for c in analysis.components)
    package = extract(analysis, Selection("Asset_001"))
    native = package.meshes[0]
    expected_positions = b"".join(struct.pack("<6f", *v[0], *v[1]) for v in eyes.vertices)
    assert native.source_position == expected_positions
    assert b"".join(struct.pack("<6f", *v[0], *v[1]) for v in native.vertices) == expected_positions
    assert "TANGENT" not in [e.label for e in native.elements]
    report = export(package, tmp_path / "exports")
    assert report.components == 4 and not report.partial
    assert (Path(report.output) / "Face" / "hash.json").is_file()
    current = index.build_index("GIMI", str(Path(report.output).parent))
    loaded = loader.load_asset_parts("GIMI", str(Path(report.output).parent), current["assets"][0])
    assert {part.component_name for part in loaded.parts} == {"Eyes", "FaceEye", "Face", "FaceEyebrow"}
    assert not loaded.warnings


@pytest.mark.parametrize("case", ["family", "normal_map", "unknown_map", "conflicting_normal", "missing_anchor", "ambiguous_eyes", "missing_material", "ambiguous_material", "different_uv", "unrelated_material", "unrelated_geometry"])
def test_face_family_requires_unique_reference_loops_and_matching_materials(case):
    meshes = _family()
    if case in ("normal_map", "unknown_map", "conflicting_normal"):
        for mesh in meshes.values():
            mesh.component.parts[0].textures.append(dict(
                role="TextureSlot03" if case == "unknown_map" else "NormalMap",
                hash="00000009", sha256="9" * 64, auxiliary=False))
        if case == "conflicting_normal":
            meshes["alternate-pass"].component.parts[0].textures[-1]["sha256"] = "a" * 64
    elif case == "missing_anchor":
        del meshes["surface-2"]
        del meshes["alternate-pass"]
    elif case == "ambiguous_eyes":
        duplicate = copy.deepcopy(meshes["surface-1"])
        duplicate.component.id = "second-reference"
        meshes[duplicate.component.id] = duplicate
    elif case == "missing_material":
        for mesh in meshes.values():
            mesh.component.parts[0].textures = mesh.component.parts[0].textures[:1]
    elif case == "ambiguous_material":
        for mesh in meshes.values():
            mesh.component.parts[0].textures.append(dict(mesh.component.parts[0].textures[0]))
    elif case == "different_uv":
        meshes["alternate-pass"].vertices[0][2] = (0.25, 0.75)
    elif case == "unrelated_material":
        meshes["surface-3"].component.parts[0].textures[0]["sha256"] = "a" * 64
    elif case == "unrelated_geometry":
        for vertex in meshes["surface-3"].vertices:
            vertex[0] = tuple(v + 99 for v in vertex[0])
    classify_faces(meshes, Context())
    if case not in ("family", "normal_map", "unknown_map", "unrelated_material", "unrelated_geometry"):
        assert not any(m.component.provenance.get("gimi_face_family") for m in meshes.values())
        assert all(m.component.requires_review for m in meshes.values()
                   if m.component.provenance.get("geometry_kind") == "direct")
        assert meshes["surface-1"].component.default_included
        return
    assert meshes["surface-1"].component.provenance["suggested_name"] == "Eyes"
    assert meshes["surface-2"].component.provenance["suggested_name"] == "FaceEye"
    assert meshes["surface-4"].component.provenance["suggested_name"] == "FaceEyebrow"
    assert not meshes["alternate-pass"].component.default_included
    assert meshes["alternate-pass"].component.provenance["duplicate_capture_pass"] == "surface-2"
    selected = [m for m in meshes.values() if m.component.default_included]
    folders = _component_folders(selected)
    assert folders["surface-2"] == folders["surface-4"] == "Face"
    assert "surface-1" not in folders
    if case in ("family", "normal_map", "unknown_map"):
        assert meshes["surface-3"].component.provenance["suggested_name"] == "Face"
        assert folders["surface-3"] == "Face"
    else:
        assert "surface-3" not in folders
        assert meshes["surface-3"].component.requires_review
        assert not meshes["surface-3"].component.default_included
    assert _component_folders([m for m in selected if m.component.id in folders]) == {}


@pytest.mark.parametrize("case", ["separate", "offset_shell", "large_offset", "rim_uv_mismatch", "collapsed_rim",
                                 "mixed_triangle", "different_uv", "different_rim", "interleaved", "no_family",
                                 "paired_mask", "paired_secondary_mismatch", "paired_primary_mismatch",
                                 "paired_rim_mismatch", "paired_no_extra_boundaries", "missing_mask_pair"])
def test_tagged_face_shell_is_separate_recoverable_and_requires_geometry_proof(case, tmp_path):
    meshes = _family()
    del meshes["alternate-pass"]
    anchor = meshes["surface-2"]
    positions, indices = _discs(((0, 0, -0.75), (0, 0, 0.75)), annular=case == "paired_no_extra_boundaries")
    for vertex in anchor.vertices:
        vertex[2] = (0.5, 0.5)
    original_vertices = copy.deepcopy(anchor.vertices)
    for vertex in anchor.vertices:
        vertex.extend(((0.5, 0.5), (0, 0)))
    start = len(anchor.vertices)
    anchor.vertices.extend([[p, (1, 0, 0), (0.5, 0.5), (0.5, 0.5), (0.5, 0.5)] for p in positions])
    anchor.elements.extend((Element("TEXCOORD", 1, "R32G32_FLOAT", 32),
                            Element("TEXCOORD", 2, "R32G32_FLOAT", 40)))
    base_indices = list(anchor.indices[0])
    overlay_indices = [i + start for i in indices]
    anchor.indices[0].extend(overlay_indices)
    anchor.component.parts[0].count = len(anchor.indices[0])
    if case.startswith("paired_") or case == "missing_mask_pair":
        # Secondary mask UVs may use a different atlas mapping from the primary UVs.
        for vertex in anchor.vertices[start:]:
            vertex[4] = (0.25, 0.75)
        if case != "missing_mask_pair":
            anchor.elements.append(Element("TEXCOORD", 3, "R32G32_FLOAT", 48))
            for vertex in anchor.vertices:
                vertex.append(vertex[4])
        if case == "paired_secondary_mismatch":
            anchor.vertices[-1][5] = (0.25, 0.5)
        elif case == "paired_primary_mismatch":
            anchor.vertices[-1][3] = (0.25, 0.5)
        elif case == "paired_rim_mismatch":
            for vertex in anchor.vertices[start:]:
                vertex[2:4] = [(0.6, 0.5)] * 2
    elif case in ("offset_shell", "large_offset", "rim_uv_mismatch"):
        offset = 0.00005 if case != "large_offset" else 0.0005
        for vertex in anchor.vertices[start:]:
            vertex[0] = (vertex[0][0] + offset, *vertex[0][1:])
            shift = 0.00002 if case != "rim_uv_mismatch" else 0.001
            vertex[2:] = [(0.5 + shift, 0.5)] * 3
        for vertex in anchor.vertices[:start]:
            vertex[2:4] = [(0.5, 0.5)] * 2
        original_vertices = [v[:3] for v in copy.deepcopy(anchor.vertices[:start])]
    elif case == "collapsed_rim":
        anchor.vertices[start + 1][0] = anchor.vertices[start][0]
    elif case == "mixed_triangle":
        anchor.indices[0][-1] = 0
    elif case == "different_uv":
        anchor.vertices[-1][3] = (0.1, 0.5)
    elif case == "different_rim":
        for vertex in anchor.vertices[start:]:
            vertex[0] = tuple(v * 0.8 for v in vertex[0])
    elif case == "interleaved":
        anchor.indices[0][:3], anchor.indices[0][-3:] = anchor.indices[0][-3:], anchor.indices[0][:3]
    elif case == "no_family":
        del meshes["surface-1"]
    original_indices = list(anchor.indices[0])
    original_count = anchor.component.parts[0].count
    overlays = classify_faces(meshes, Context())
    if case not in ("separate", "offset_shell", "paired_mask"):
        assert overlays == []
        assert anchor.indices[0] == original_indices
        assert anchor.component.parts[0].count == original_count
        return
    overlay, = overlays
    base = anchor.component
    assert anchor.indices[0] == base_indices
    assert [v[:3] for v in anchor.vertices[:start]] == original_vertices
    assert base.parts[0].count == len(base_indices)
    assert overlay.parts[0].first == base.parts[0].first + len(base_indices)
    assert overlay.parts[0].count == len(overlay_indices)
    assert base.parts[0].count + overlay.parts[0].count == original_count
    assert overlay.streams == base.streams
    assert overlay.parts[0].ib_file == base.parts[0].ib_file
    assert overlay.parts[0].textures == base.parts[0].textures
    assert base.default_included and not overlay.default_included
    assert overlay.status == "included"  # Explicit selections can recover the full source layer.
    assert overlay.provenance["source_component"] == base.id
    assert overlay.provenance["suggested_name"] == "FaceEyeOverlay"
    assert base.provenance["separated_face_overlay"]["component"] == overlay.id
    assert base.provenance["separated_face_overlay"]["uv_tag_layout"] == (
        "paired_secondary" if case == "paired_mask" else "shared_primary")
    # Exercise the saved analysis through native range decoding and both exports.
    dump = tmp_path / "capture"
    dump.mkdir()
    uv_count = sum(element.semantic == "TEXCOORD" for element in anchor.elements)
    for role, slot, payload, elements, stride in (
        ("position", 0, b"".join(struct.pack("<6f", *v[0], *v[1]) for v in anchor.vertices),
         [("POSITION", 0, "R32G32B32_FLOAT", 0), ("NORMAL", 0, "R32G32B32_FLOAT", 12)], 24),
        ("texcoord", 1, b"".join(struct.pack("<" + "f" * uv_count * 2, *(value for uv in v[2:] for value in uv))
                                  for v in anchor.vertices),
         [("TEXCOORD", i, "R32G32_FLOAT", i * 8) for i in range(uv_count)], uv_count * 8),
    ):
        source = base.streams[role]
        source["file"] = f"000001-vb{slot}={source['hash']}.buf"
        source["slot"] = slot
        (dump / source["file"]).write_bytes(payload)
        layout = header(stride, slot, elements).replace("pointlist", "trianglelist").replace(
            "vertex count: 4", f"vertex count: {len(anchor.vertices)}")
        (dump / source["file"]).with_suffix(".txt").write_text(layout)
    base.parts[0].textures = []
    base.provenance.update(root_vs="a" * 16, draw_vb="00000020")
    ib_path = dump / base.parts[0].ib_file
    ib_path.write_text(f"first index: 0\nindex count: {original_count}\ntopology: trianglelist\nformat: R32_UINT\n\n")
    ib_path.with_suffix(".buf").write_bytes(struct.pack("<" + "I" * original_count, *original_indices))
    overlay.streams = copy.deepcopy(base.streams)
    overlay.parts[0].textures = []
    overlay.provenance.update(root_vs="a" * 16, draw_vb="00000020")
    analysis = Analysis(str(dump), "stream-output", [base, overlay], [], {})
    analysis.save(tmp_path / "analysis.json")
    analysis = Analysis.read(tmp_path / "analysis.json")
    for name, selection, expected in (
        ("Default", None, [base_indices]),
        ("Complete", [base.id, overlay.id], [base_indices, overlay_indices]),
    ):
        package = extract(analysis, Selection(name, selection))
        assert [m.indices[0] for m in package.meshes] == expected
        report = export(package, tmp_path / "exports")
        for mesh in package.meshes:
            vb = next(Path(report.output).glob(f"{name}{mesh.component.name}-vb0=*.txt"))
            ib = next(Path(report.output).glob(f"{name}{mesh.component.name}A-ib=*.txt"))
            parsed = parse_vertex_dump(vb)
            assert parsed.positions == b"".join(struct.pack("<3f", *v[0]) for v in anchor.vertices)
            assert list(parse_index_dump(ib)) == mesh.indices[0]

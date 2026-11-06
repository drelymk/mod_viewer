"""Automatic facial grouping requires independent material and topology evidence."""

import copy
import json
import math
from pathlib import Path
import struct

import pytest

from app.assets import index, loader
from app.assets.loader import hash_asset
from app.assets.extractor import discovery
from app.assets.extractor.core import Analysis, Component, Context, Part, Unsupported
from app.assets.extractor.export import Selection, _component_folders, export, extract
from app.assets.extractor.geometry import Element, Mesh
from app.assets.extractor.memory_capture import MemoryCapture
from app.assets.extractor.profiles.gimi_faces import classify_faces
from app.assets.extractor.profiles.gimi_placement import apply_placement
from core.geometry.migoto_dump import parse_index_dump, parse_vertex_dump
from tests.support.extraction_data import header
from tests.support.gimi_capture import make_face_capture
from app.assets.extractor.capture import Inventory
from tests.support.memory_capture_data import capture_data


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


def _open_face_mesh(*, openings=2, extra_opening=0):
    mesh = _mesh(2, ((0, 0, -0.75), (0, 0, 0.75)), annular=True)
    mesh.vertices = [[(0, -1.2 + row * 0.1, -1.5 + column * 0.125), (1, 0, 0),
                      (column / 24, row / 24)] for row in range(25) for column in range(25)]
    indices = []
    for row in range(24):
        for column in range(24):
            hole = 10 <= row < 14 and (4 <= column < 8 or (openings == 2 and 16 <= column < 20))
            extra = 17 <= row < 17 + extra_opening and 11 <= column < 11 + extra_opening
            if (openings and hole) or extra:
                continue
            a = row * 25 + column
            indices.extend((a, a + 25, a + 1, a + 1, a + 25, a + 26))
    mesh.indices = [indices]
    mesh.component.parts[0].count = len(indices)
    return mesh


def _capture(tmp_path, monkeypatch, *, native_reference=True, case="valid"):
    meshes = list(_family().values())[:-1]
    if not native_reference:
        meshes[0] = _mesh(1, ((0, 2, 3),), native=True)
        meshes[1] = _open_face_mesh()
    dump, material = make_face_capture(tmp_path, meshes, case)
    monkeypatch.setattr(discovery, "collect", lambda *_: copy.deepcopy(material))
    return dump, meshes


def _export_and_load(package, output):
    report = export(package, output)
    root = Path(report.output).parent
    record, = index.build_index("GIMI", str(root))["assets"]
    return report, loader.load_asset_parts("GIMI", str(root), record)


def _snapshot(result):
    return {p.component_name: (p.positions, p.normals, p.indices, p.uvs) for p in result.parts}


def _points(values):
    # Compare geometry independently of float32 rounding and compact vertex order.
    return sorted({tuple(round(axis, 5) for axis in point) for point in values})


def _no_fit(*_args, **_kwargs):
    pytest.fail("New extracted faces must not use geometric alignment")


@pytest.mark.parametrize("native_reference", [True, False])
def test_captured_face_extraction_and_selection_lifecycle(tmp_path, monkeypatch, native_reference):
    dump, originals = _capture(tmp_path, monkeypatch, native_reference=native_reference)
    analysis = discovery.analyze(dump)
    package = extract(analysis, Selection("Asset_001"))
    native = package.meshes[0]
    assert not package.partial
    assert "TANGENT" not in [element.label for element in native.elements]
    assert native.source_position == b"".join(struct.pack("<6f", *v[0], *v[1]) for v in originals[0].vertices)
    monkeypatch.setattr(hash_asset, "_solve_face_alignment", _no_fit)
    report, loaded = _export_and_load(package, tmp_path / "exports")
    assert report.components == 4 and not loaded.warnings
    assert not (Path(report.output) / "Face").exists()
    assert len(list(Path(report.output).glob("*/hash.json"))) == 4
    parts = {part.component_name: part for part in loaded.parts}
    native_name = "Eyes" if native_reference else "Component001"
    for original, name in zip(originals, (native_name, "FaceEye", "Face", "FaceEyebrow")):
        part = parts[name]
        expected = _points((-original.vertices[i][0][2], 2 - original.vertices[i][0][0], 3 + original.vertices[i][0][1])
                           if name != native_name else original.vertices[i][0] for i in original.indices[0])
        assert _points(struct.iter_unpack("<3f", part.positions)) == expected
        assert part.index_count == len(original.indices[0])
        assert struct.unpack("<3f", part.normals[:12]) == pytest.approx(
            (0, -1, 0) if name != native_name else (1, 0, 0))
    for metadata in Path(report.output).glob("*/hash.json"):
        for entry in json.loads(metadata.read_text())["components"]:
            placement = entry.get("gimi_face_placement", {})
            if placement.get("status") == "baked":
                mesh = next(m for m in package.meshes if m.component.name == entry["component_name"])
                assert (metadata.parent / placement["source_position_file"]).read_bytes() == mesh.source_position
                assert mesh.source_position == (dump / mesh.component.streams["position"]["file"]).read_bytes()
    family = [c for c in analysis.components if c.provenance.get("gimi_face_family")]
    assert {c.provenance["gimi_face_placement"]["status"] for c in family} == {"proven"}
    _, faces = _export_and_load(extract(analysis, Selection("FacesOnly", ids=[c.id for c in family])),
                                tmp_path / "faces-only")
    assert _snapshot(faces) == {key: value for key, value in _snapshot(loaded).items() if key != native_name}
    native_id = analysis.components[0].id
    _, renamed = _export_and_load(extract(analysis, Selection(
        "NativeOnly", ids=[native_id], renames={native_id: "FaceNose"})), tmp_path / "native-only")
    assert len(renamed.parts) == 1 and renamed.parts[0].positions == parts[native_name].positions


@pytest.mark.parametrize("case", ["missing", "conflicting", "bad_inverse", "nonfinite",
                                 "bad_palette", "unbound", "unsupported_scale"])
def test_face_placement_rejects_missing_or_conflicting_capture_evidence(tmp_path, monkeypatch, case):
    dump, _originals = _capture(tmp_path, monkeypatch, case=case)
    analysis = discovery.analyze(dump)
    placements = [c.provenance["gimi_face_placement"] for c in analysis.components
                  if c.provenance.get("gimi_face_family")]
    assert len(placements) == 3 and {p["status"] for p in placements} == {"unresolved"}


def test_unresolved_face_export_preserves_raw_coordinates_and_warns(tmp_path, monkeypatch):
    dump, originals = _capture(tmp_path, monkeypatch, case="missing")
    monkeypatch.setattr(hash_asset, "_solve_face_alignment", _no_fit)
    report, loaded = _export_and_load(extract(discovery.analyze(dump), Selection("Unplaced")), tmp_path / "exports")
    assert report.partial
    assert len(loaded.warnings) == 3
    assert {w["reason"] for w in loaded.warnings} == {"captured_face_placement_unavailable"}
    for original, name in zip(originals[1:], ("FaceEye", "Face", "FaceEyebrow")):
        part = next(p for p in loaded.parts if p.component_name == name)
        expected = _points(original.vertices[i][0] for i in original.indices[0])
        assert _points(struct.iter_unpack("<3f", part.positions)) == expected


def test_face_placement_uses_later_available_native_output(tmp_path, monkeypatch):
    dump, _originals = _capture(tmp_path, monkeypatch, case="missing_first_snapshot")
    analysis = discovery.analyze(dump)
    placements = [c.provenance["gimi_face_placement"] for c in analysis.components
                  if c.provenance.get("gimi_face_family")]
    assert len(placements) == 3 and {p["status"] for p in placements} == {"proven"}
    assert {match["replay"]["draw"] for p in placements for match in p["matches"]} == {"000006"}
    assert not extract(analysis, Selection("AvailableOutput")).partial


@pytest.mark.parametrize("case", ["valid", "unrelated_native"])
def test_face_dependencies_validate_once_per_extraction_and_detect_later_changes(tmp_path, monkeypatch, case):
    dump, _originals = _capture(tmp_path, monkeypatch, native_reference=False, case=case)
    analysis = discovery.analyze(dump)
    family = [c for c in analysis.components if c.provenance.get("gimi_face_family")]
    assert len(family) == 3
    assert {c.provenance["gimi_face_placement"]["status"] for c in family} == {"proven"}
    if case == "unrelated_native":
        assert len([c for c in analysis.components if c.provenance.get("producer")]) == 2
        dependencies = {s["file"] for c in family for s in c.provenance["gimi_face_placement"]["sources"]}
        assert not any(name.startswith(("000006-", "000007-")) for name in dependencies)
        (dump / "000006-vs-t0=99999999.buf").write_bytes(b"changed unrelated palette")
    source = "000001-vs-t0=44444444.buf" if case == "unrelated_native" else "000003-vs-cb3=55555555.buf"
    reads = []
    original_read = Inventory.read_bytes

    def counted(inv, name, **kwargs):
        assert not name.startswith(("000006-", "000007-"))
        if name == source:
            reads.append(name)
        return original_read(inv, name, **kwargs)

    monkeypatch.setattr(Inventory, "read_bytes", counted)
    for ordinal in range(2):
        assert not extract(analysis, Selection("Asset_001", ids=[c.id for c in family])).partial
        assert len(reads) == ordinal + 1
    (dump / source).write_bytes(bytes(160))
    with pytest.raises(Unsupported, match="capture changed since analysis"):
        extract(analysis, Selection("Asset_001", ids=[c.id for c in family]))
    assert len(reads) == 3


@pytest.mark.parametrize("transport", ["memory", "dedup"])
def test_face_placement_transport_round_trip(tmp_path, monkeypatch, transport):
    dump, _originals = _capture(tmp_path, monkeypatch, native_reference=False,
                                case="dedup" if transport == "dedup" else "valid")
    analysis = discovery.analyze(dump)
    expected = extract(analysis, Selection("Asset_001"))
    monkeypatch.setattr(hash_asset, "_solve_face_alignment", _no_fit)
    if transport == "memory":
        manifest, factory = capture_data({p.name: p.read_bytes() for p in dump.iterdir()})
        with MemoryCapture(manifest, mapping_factory=factory) as capture:
            candidate = discovery.analyze(capture)
            package = extract(candidate, Selection("Asset_001"))
            report, loaded = _export_and_load(package, tmp_path / "exports")
    else:
        analysis.save(tmp_path / "analysis.json")
        package = extract(Analysis.read(tmp_path / "analysis.json"), Selection("Asset_001"))
        report, loaded = _export_and_load(package, tmp_path / "exports")
    assert [m.vertices for m in package.meshes] == [m.vertices for m in expected.meshes]
    assert not loaded.warnings and not report.partial


def test_baked_face_directions_preserve_handedness_packed_data_and_source_bytes():
    mesh = _mesh(2, ((0, 0, 0),))
    mesh.source_position = b"captured source geometry"
    mesh.elements.extend((Element("TANGENT", 0, "R32G32B32A32_FLOAT", 32),
                          Element("TANGENT", 1, "R8G8B8A8_UNORM", 48)))
    for row in mesh.vertices:
        row.extend(((0, 2, 0, -1), (1, .5, 0, 1)))
    mesh.component.provenance.update(gimi_face_family="generated-family", gimi_face_placement={
        "status": "proven", "sources": [],
        "matrix": ((0, 0, -1, 8), (-1, 0, 0, 9), (0, 1, 0, 10), (0, 0, 0, 1))})
    before = copy.deepcopy(mesh.vertices)
    apply_placement(mesh, None, Context())
    assert mesh.source_position == b"captured source geometry"
    assert mesh.indices == _mesh(2, ((0, 0, 0),)).indices
    for old, new in zip(before, mesh.vertices):
        assert new[0] == pytest.approx((8 - old[0][2], 9 - old[0][0], 10 + old[0][1]))
        assert new[1] == (0, -1, 0)
        assert new[2] == old[2]
        assert new[3] == (0, 0, 1, -1)
        assert new[4] == old[4]


@pytest.mark.parametrize("case", ["open", "transformed", "small_opening", "third_eye", "closed", "one_eye", "closed_slits", "unequal_eyes", "split_rings",
                                 "different_material", "ambiguous_face", "different_uv", "multiple_faces"])
def test_open_face_proves_family_without_native_eyes(case):
    meshes = _family()
    del meshes["surface-1"]
    mesh = _open_face_mesh(openings=0 if case == "closed" else 1 if case == "one_eye" else 2,
                           extra_opening=2 if case == "small_opening" else 4 if case == "third_eye" else 0)
    if case == "split_rings":
        mesh = meshes["surface-2"]
    elif case == "closed_slits":
        for vertex in mesh.vertices:
            x, y, z = vertex[0]
            vertex[0] = (x, y * 0.01 if abs(y) < 0.21 else y, z)
    elif case == "unequal_eyes":
        for vertex in mesh.vertices:
            x, y, z = vertex[0]
            if abs(y) < 0.21 and 0.49 < z < 1.01:
                vertex[0] = (x, y * 0.5, 0.75 + (z - 0.75) * 0.5)
    if case == "transformed":
        for vertex in mesh.vertices:
            x, y, z = vertex[0]
            vertex[0] = (z * 2 + 4, x * 2 - 3, y * 2 + 1)
        # Keep the rest of the family near the transformed anchor.
        for other in meshes.values():
            for vertex in other.vertices:
                x, y, z = vertex[0]
                vertex[0] = (z * 2 + 4, x * 2 - 3, y * 2 + 1)
    if case == "multiple_faces":
        count = len(mesh.vertices)
        mesh.vertices.extend([[tuple(value + (10 if axis == 2 else 0) for axis, value in enumerate(v[0])),
                               *v[1:]] for v in copy.deepcopy(mesh.vertices)])
        mesh.indices[0].extend([i + count for i in list(mesh.indices[0])])
        mesh.component.parts[0].count = len(mesh.indices[0])
    meshes["surface-2"] = mesh
    duplicate = copy.deepcopy(mesh)
    duplicate.component.id = "alternate-pass"
    meshes[duplicate.component.id] = duplicate
    if case == "different_uv":
        duplicate.vertices[0][2] = (0.25, 0.75)
    if case == "different_material":
        mesh.component.parts[0].textures.pop()
        duplicate.component.parts[0].textures.pop()
    if case == "ambiguous_face":
        duplicate.component.parts[0].textures[0]["sha256"] = "a" * 64
    before = copy.deepcopy([(m.vertices, m.indices) for m in meshes.values()])
    classify_faces(meshes, Context())
    assert [(m.vertices, m.indices) for m in meshes.values()] == before
    if case in ("ambiguous_face", "different_uv"):
        selected = [m.component for m in meshes.values() if m.component.default_included]
        assert len(selected) == 1
        assert selected[0].provenance["suggested_name"] == "FaceEye"
        assert selected[0].provenance["gimi_face_selection"] == dict(status="best_effort", candidates=2)
        assert "Ambiguous facial candidates" in selected[0].diagnostics[-1]
        assert not any(m.component.provenance.get("duplicate_capture_pass") for m in meshes.values())
        return
    if case not in ("open", "transformed", "small_opening"):
        assert all(m.component.requires_review for m in meshes.values())
        return
    assert mesh.component.provenance["suggested_name"] == "FaceEye"
    assert mesh.component.provenance["face_anchor_proof"]["eye_separation"] > 0
    assert mesh.component.provenance["face_anchor_proof"]["extra_boundary_count"] == (case == "small_opening")
    assert meshes["surface-3"].component.provenance["suggested_name"] == "Face"
    assert meshes["surface-4"].component.provenance["suggested_name"] == "FaceEyebrow"
    assert all(meshes[key].component.default_included for key in ("surface-2", "surface-3", "surface-4"))
    assert duplicate.component.provenance["duplicate_capture_pass"] == mesh.component.id
    assert not duplicate.component.default_included


@pytest.mark.parametrize("placement_case", ["valid", "preferred_unplaced", "all_unplaced"])
def test_ambiguous_face_fallback_survives_analysis_export_and_reload(tmp_path, monkeypatch, placement_case):
    meshes = list(_family().values())[:-1]
    meshes[1] = _open_face_mesh()
    competitor = _open_face_mesh(extra_opening=2)
    competitor.component = meshes[2].component
    competitor.component.parts[0].count = len(competitor.indices[0])
    meshes[2] = competitor
    dump, material = make_face_capture(tmp_path, meshes)
    if placement_case != "valid":
        # A valid object/inverse pair with an unsupported face basis cannot place
        # the topologically preferred anchor; the other candidate remains proven.
        (dump / "000003-vs-cb3=55555555.buf").write_bytes(
            (dump / "000002-vs-cb2=55555555.buf").read_bytes())
    if placement_case == "all_unplaced":
        (dump / "000004-vs-cb5=55555555.buf").write_bytes(
            (dump / "000002-vs-cb2=55555555.buf").read_bytes())
    monkeypatch.setattr(discovery, "collect", lambda *_: copy.deepcopy(material))
    from app.assets.extractor.profiles import gimi_placement
    replayed = []
    native = gimi_placement._Capture.native
    def replay(capture, component):
        replayed.append(component.id)
        return native(capture, component)
    monkeypatch.setattr(gimi_placement._Capture, "native", replay)
    analysis = discovery.analyze(dump)
    assert len(replayed) == 1
    selected = [c for c in analysis.components if c.provenance.get("gimi_face_selection")]
    expected_mesh = meshes[2 if placement_case == "preferred_unplaced" else 1]
    assert len(selected) == 1 and selected[0].ib == expected_mesh.component.ib
    placed = placement_case != "all_unplaced"
    assert selected[0].provenance["gimi_face_placement"]["status"] == ("proven" if placed else "unresolved")
    assert all(not c.default_included for c in analysis.components
               if c.provenance.get("geometry_kind") == "direct" and c is not selected[0])
    analysis.save(tmp_path / "analysis.json")
    restored = Analysis.read(tmp_path / "analysis.json")
    package = extract(restored, Selection("FallbackAsset"))
    assert package.partial and package.skipped == 0 and package.unproven_candidates == 2
    assert len(package.meshes) == 2
    report, loaded = _export_and_load(package, tmp_path / "exports")
    assert report.partial
    assert {w["reason"] for w in loaded.warnings} == (set() if placed else {"captured_face_placement_unavailable"})
    face = next(p for p in loaded.parts if p.component_name == "FaceEye")
    expected = _points((-expected_mesh.vertices[i][0][2], 2 - expected_mesh.vertices[i][0][0],
                        3 + expected_mesh.vertices[i][0][1]) if placed else expected_mesh.vertices[i][0]
                       for i in expected_mesh.indices[0])
    assert _points(struct.iter_unpack("<3f", face.positions)) == expected
    assert any("best-effort face" in note for note in report.diagnostics)


@pytest.mark.parametrize("difference", ["uv", "material", "resource", "coverage", "unique_family"])
def test_face_choice_is_stable_across_order_and_labels(difference):
    original = _open_face_mesh()
    competitor = copy.deepcopy(original)
    competitor.component.id = "competing-surface"
    if difference in ("uv", "unique_family"):
        competitor.vertices[0][2] = (0.25, 0.75)
    elif difference == "material":
        competitor.component.parts[0].textures[0]["sha256"] = "a" * 64
    elif difference == "resource":
        competitor.component.streams["position"]["resource"]["version"] += 1
    else:
        first = len(competitor.vertices)
        positions, indices = _discs(((0, -0.7, 0),), radius=0.1)
        competitor.vertices.extend([[p, (1, 0, 0), (0, 0)] for p in positions])
        competitor.indices[0].extend(i + first for i in indices)
        competitor.component.parts[0].count = len(competitor.indices[0])
    order = (original, competitor)
    if difference == "unique_family":
        unique = copy.deepcopy(original)
        unique.component.id = "unique-family"
        unique.component.parts[0].textures[0]["sha256"] = "a" * 64
        order += (unique,)
    choices = []
    for ordered in (order, tuple(reversed(order))):
        candidates = copy.deepcopy(ordered)
        for ordinal, mesh in enumerate(candidates):
            mesh.component.name = f"Generated{ordinal}"
        classify_faces({m.component.id: m for m in candidates}, Context())
        selected, = [m for m in candidates if m.component.default_included]
        choices.append(selected.component.id)
        if difference == "unique_family":
            assert selected.component.id == unique.component.id
            assert "gimi_face_selection" not in selected.component.provenance
        else:
            assert selected.component.provenance["gimi_face_selection"]["candidates"] == 2
        assert not any(m.component.provenance.get("duplicate_capture_pass") for m in candidates)
    assert choices[0] == choices[1]
    if difference == "coverage":
        assert choices[0] == original.component.id


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
    if case in ("conflicting_normal", "different_uv"):
        selected = [m.component for m in meshes.values()
                    if m.component.provenance.get("geometry_kind") == "direct" and m.component.default_included]
        assert len(selected) == 1 and selected[0].provenance["suggested_name"] == "FaceEye"
        assert selected[0].provenance["gimi_face_selection"]["candidates"] == 2
        assert not meshes["surface-3"].component.default_included
        assert not meshes["surface-4"].component.default_included
        return
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
    assert folders["surface-2"] == "00000020" and folders["surface-4"] == "00000040"
    assert folders["surface-1"] == "00000010"
    if case in ("family", "normal_map", "unknown_map"):
        assert meshes["surface-3"].component.provenance["suggested_name"] == "Face"
        assert folders["surface-3"] == "00000030"
    else:
        assert "surface-3" not in folders
        assert meshes["surface-3"].component.requires_review
        assert not meshes["surface-3"].component.default_included
    assert _component_folders(selected) == folders


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
            vb = next(Path(report.output).rglob(f"{name}{mesh.component.name}-vb0=*.txt"))
            ib = next(Path(report.output).rglob(f"{name}{mesh.component.name}A-ib=*.txt"))
            parsed = parse_vertex_dump(vb)
            assert parsed.positions == b"".join(struct.pack("<3f", *v[0]) for v in anchor.vertices)
            assert list(parse_index_dump(ib)) == mesh.indices[0]

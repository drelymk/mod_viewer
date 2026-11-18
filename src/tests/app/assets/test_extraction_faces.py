"""All GIMI face candidates retain captured topology and removable hash folders."""

import copy
import json
import math
from pathlib import Path
import shutil
import struct

import pytest

from app.assets import folders, index, loader, extraction
from app.assets.loader import hash_asset
from app.assets.extractor import discovery
from app.assets.extractor.core import Analysis, Component, Context, Part, Unsupported
from app.assets.extractor.export import Selection, _component_folders, export, extract
from app.assets.extractor.geometry import Element, Mesh
from app.assets.extractor.memory_capture import MemoryCapture
from app.assets.extractor.profiles.gimi_faces import classify_faces
from app.assets.extractor.profiles.gimi_placement import apply_placement
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


def _open_face_mesh(*, openings=True):
    mesh = _mesh(2, ((0, 0, -0.75), (0, 0, 0.75)), annular=True)
    mesh.vertices = [[(0, -1.2 + row * 0.1, -1.5 + column * 0.125), (1, 0, 0),
                      (column / 24, row / 24)] for row in range(25) for column in range(25)]
    indices = []
    for row in range(24):
        for column in range(24):
            if openings and 10 <= row < 14 and (4 <= column < 8 or 16 <= column < 20):
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
    native_name = "Component001"
    for original, name in zip(originals, (native_name, "Face001", "Face002", "Face003")):
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
    for original, name in zip(originals[1:], ("Face001", "Face002", "Face003")):
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


@pytest.mark.parametrize("case", ["valid", "missing", "bad_inverse", "bad_palette", "short_output", "corrupt_output", "missing_material"])
def test_captured_family_without_eye_topology(tmp_path, monkeypatch, case):
    meshes = list(_family().values())[:-1]
    meshes[0] = _mesh(1, ((0, 2, 3),), native=True)
    meshes[1] = _mesh(2, ((0, 0, -0.75), (0, 0, 0.75)))
    dump, material = make_face_capture(tmp_path, meshes, case)
    output = dump / "000002-vb0=33333333.buf"
    if output.exists():
        payload = output.read_bytes()
        if case == "short_output":
            payload = payload[:len(meshes[0].vertices) * 24 - 1]
        elif case == "corrupt_output":
            payload = struct.pack("<f", 100) + payload[4:]
        else:
            payload += b"padding"
        output.write_bytes(payload)
    if case == "missing_material":
        material.pop()
    monkeypatch.setattr(discovery, "collect", lambda *_: copy.deepcopy(material))
    analysis = discovery.analyze(dump)
    family = [c for c in analysis.components if c.provenance.get("gimi_face_family")]
    assert len(family) == 3 and all(c.default_included for c in family)
    if case not in ("valid", "missing_material"):
        assert {c.provenance["gimi_face_placement"]["status"] for c in family} == {"unresolved"}
        package = extract(analysis, Selection("CapturedFamily"))
        assert len(package.meshes) == 4 and package.partial
        return
    assert {c.provenance["gimi_face_placement"]["status"] for c in family} == {"proven"}
    monkeypatch.setattr(hash_asset, "_solve_face_alignment", _no_fit)
    package = extract(analysis, Selection("CapturedFamily"))
    assert len(package.meshes) == 4 and not package.partial
    report, loaded = _export_and_load(package, tmp_path / "exports")
    assert not report.partial and not loaded.warnings
    for mesh in meshes[1:]:
        exported = next(m for m in package.meshes if m.component.ib == mesh.component.ib)
        part = next(p for p in loaded.parts if p.component_name == exported.component.name)
        expected = _points((-v[0][2], 2 - v[0][0], 3 + v[0][1]) for v in mesh.vertices)
        assert _points(struct.iter_unpack("<3f", part.positions)) == expected


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


@pytest.mark.parametrize("case,families", [
    ("open", 1), ("missing_material", 2), ("different_material", 2), ("overlay", 1), ("outline_pass", 1),
])
def test_all_face_candidates_preserve_expressions_passes_and_topology(case, families):
    meshes = _family()
    anchor = meshes["surface-2"]
    if case == "open":
        anchor = _open_face_mesh()
        meshes[anchor.component.id] = anchor
    elif case == "missing_material":
        anchor.component.parts[0].textures.clear()
    elif case == "different_material":
        anchor.component.parts[0].textures[0]["sha256"] = "a" * 64
    elif case == "outline_pass":
        anchor.component.provenance["duplicate_outline_pass"] = True
    elif case == "overlay":
        # Keep the entire UV-tagged shell, including triangles over eye openings.
        positions, indices = _discs(((0, 0, -0.75), (0, 0, 0.75)))
        start = len(anchor.vertices)
        for vertex in anchor.vertices:
            vertex[2] = (0.5, 0.5)
            vertex.extend(((0.5, 0.5), (0, 0)))
        anchor.vertices.extend([[p, (1, 0, 0), (0.5, 0.5), (0.5, 0.5), (0.5, 0.5)] for p in positions])
        anchor.elements.extend((Element("TEXCOORD", 1, "R32G32_FLOAT", 32),
                                Element("TEXCOORD", 2, "R32G32_FLOAT", 40)))
        anchor.indices[0].extend(i + start for i in indices)
        anchor.component.parts[0].count = len(anchor.indices[0])
    before = copy.deepcopy([(m.vertices, m.indices, m.component.parts) for m in meshes.values()])
    classify_faces(meshes, Context())
    assert [(m.vertices, m.indices, m.component.parts) for m in meshes.values()] == before
    faces = [m for m in meshes.values() if m.component.provenance.get("geometry_kind") == "direct"]
    assert len(faces) == 4 and all(m.component.default_included for m in faces)
    assert len({m.component.provenance["suggested_name"] for m in faces}) == len(faces)
    assert all(m.component.provenance["gimi_face_candidate"] for m in faces)
    assert len({m.component.provenance["gimi_face_family"] for m in faces}) == families
    assert _component_folders(faces) == {m.component.id: m.component.streams["position"]["hash"] for m in faces}


@pytest.mark.parametrize("shared_position_hash", [False, True])
def test_all_faces_export_update_and_manual_hash_folder_removal(tmp_path, monkeypatch, shared_position_hash):
    meshes = list(_family().values())
    meshes[1] = _open_face_mesh(openings=False)
    dump, material = make_face_capture(tmp_path, meshes)
    # The alternate pass inherits its CB binding and needs its own captured snapshot.
    shutil.copyfile(dump / "000005-vs-cb1=55555555.buf", dump / "000006-vs-cb1=55555555.buf")
    if shared_position_hash:
        for suffix in ("buf", "txt"):
            (dump / f"000006-vb0=00000060.{suffix}").rename(dump / f"000006-vb0=00000030.{suffix}")
        log = dump / "log.txt"
        log.write_text(log.read_text().replace("resource=0x60 hash=00000060", "resource=0x60 hash=00000030"))
    monkeypatch.setattr(discovery, "collect", lambda *_: copy.deepcopy(material))
    root = tmp_path / "library"
    root.mkdir()
    monkeypatch.setattr(folders, "load_registry", lambda: [
        {"type": "GIMI", "path": folders.normalize_path(root)}])
    monkeypatch.setattr(hash_asset, "_solve_face_alignment", _no_fit)
    before = {p.name: p.read_bytes() for p in dump.iterdir()}
    saved = extraction.extract_asset(str(root), "Asset_001", str(dump))
    asset = Path(saved["path"])
    assert saved["extraction"]["components"] == 5
    assert not saved["extraction"]["partial"]
    assert not (asset / "Faces").exists() and not (asset / "Face").exists()
    metadata = list(asset.glob("*/hash.json"))
    assert len(metadata) == (4 if shared_position_hash else 5)
    details = json.loads((asset / "extraction_report.json").read_text())
    faces = [c for c in details["components"] if c["provenance"].get("gimi_face_candidate")]
    assert len(faces) == 4
    for component in faces:
        directory = asset / component["streams"]["position"]["hash"]
        entry, = [e for e in json.loads((directory / "hash.json").read_text())["components"]
                  if e["component_name"] == component["name"]]
        assert entry["object_index_counts"] == [component["parts"][0]["count"]]
        assert (directory / entry["gimi_face_placement"]["source_position_file"]).read_bytes() == (
            dump / component["streams"]["position"]["file"]).read_bytes()
        assert all((directory / t[3]).is_file() for part in entry["texture_hashes"] for t in part)
    record, = index.build_index("GIMI", str(root))["assets"]
    loaded = loader.load_asset_parts("GIMI", str(root), record)
    assert len(loaded.parts) == 5 and not loaded.warnings
    snapshots = _snapshot(loaded)
    # Disable one owning hash directory, as the existing section-removal flow does.
    removed = faces[0]
    directory = asset / removed["streams"]["position"]["hash"]
    removed_names = {c["name"] for c in faces
                     if c["streams"]["position"]["hash"] == directory.name}
    directory.rename(asset / ("DISABLED_" + directory.name))
    record, = index.build_index("GIMI", str(root))["assets"]
    loaded = loader.load_asset_parts("GIMI", str(root), record)
    assert _snapshot(loaded) == {k: v for k, v in snapshots.items() if k not in removed_names}
    # Deleting another face cannot break siblings' geometry or texture ownership.
    removed = faces[1]
    shutil.rmtree(asset / removed["streams"]["position"]["hash"])
    record, = index.build_index("GIMI", str(root))["assets"]
    loaded = loader.load_asset_parts("GIMI", str(root), record)
    assert len(loaded.parts) == (2 if shared_position_hash else 3) and not loaded.warnings
    updated = extraction.extract_asset(str(root), "Asset_001", str(dump), str(asset))
    assert updated["extraction"]["components"] == 5
    record, = index.build_index("GIMI", str(root))["assets"]
    assert _snapshot(loader.load_asset_parts("GIMI", str(root), record)) == snapshots
    assert {p.name: p.read_bytes() for p in dump.iterdir()} == before

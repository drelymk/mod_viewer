"""Dump extraction, destructive replacement, and failure recovery contracts."""

import copy
import json
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from app.assets import extraction, folders, index, loader
from app.assets.extractor.core import Cancelled, Context
from app.assets.extractor.discovery import analyze
from app.assets.extractor.export import (
    ExportOptions, Selection, _component_folders, export, extract,
)
from app.assets.extractor.textures import digest
from app.bridge.access import FolderAccess
from app.bridge.registry import AssetFolderRegistry
from app.settings import paths
from core.geometry.mesh_builder import GeometryBlob
from tests.support.extraction_data import make_capture


@pytest.fixture
def library(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"version": 1, "modFolders": [], "assetFolders": [
        {"type": "ZZMI", "path": str(root)}]}), encoding="utf-8")
    monkeypatch.setattr(paths, "config_path", lambda: str(config))
    monkeypatch.setattr(paths, "asset_index_dir", lambda: str(tmp_path / "indexes"))
    return root, make_capture(tmp_path)


@pytest.mark.parametrize("asset_type", ["ZZMI", "GIMI", "WWMI"])
def test_extract_update_and_rename_replace_entire_asset(library, monkeypatch, asset_type):
    root, dump = library
    monkeypatch.setattr(folders, "load_registry", lambda: [
        {"type": asset_type, "path": folders.normalize_path(root)}])
    log = dump / "log.txt"
    log.write_text(log.read_text(encoding="utf-8").replace(
        "000001 3DMigoto [ShaderRegex\\ZZMIv1\\EnableTextureOverrides]\n", ""), encoding="utf-8")
    with log.open("a", encoding="utf-8") as stream:
        stream.write("""000004 IASetVertexBuffers(StartSlot:0, NumBuffers:1)
       0: resource=0x60 hash=77777777
000004 IASetIndexBuffer(pIndexBuffer:0x70, Format:57, Offset:0) hash=88888888
000004 DrawIndexed(IndexCount:6, StartIndexLocation:0, BaseVertexLocation:0)
""")
    Image.new("RGBA", (16, 16), (80, 120, 160, 255)).save(
        dump / "000002-ps-t0=66666666.png")
    dump_before = {p.name: p.read_bytes() for p in dump.iterdir() if p.is_file()}
    access = FolderAccess()
    events = []
    bridge = AssetFolderRegistry(access, on_changed=lambda: events.append(True))
    access.remember_asset_picker_selection(str(dump))
    result = bridge.extract_asset(str(root), "Asset_001", str(dump))
    assert "error" not in result
    assert result["extraction"] == {
        "components": 1, "parts": 1, "partial": True, "skipped": 1}
    assert result["warnings"] == []
    asset = Path(result["path"])
    def installed_report(folder):
        filename = folder / "extraction_report.json"
        details = json.loads(filename.read_text(encoding="utf-8"))
        assert Path(details["report"]["output"]) == folder
        assert Path(details["report"]["report_path"]) == filename
        assert details["analysis"]["dump"] == str(dump.resolve())
        assert details["outputs"]
        return details
    original_report = installed_report(asset)
    original_report["previous_report_marker"] = True
    (asset / "extraction_report.json").write_text(json.dumps(original_report), encoding="utf-8")
    assert (asset / "hash.json").exists()
    assert result["folders"][0]["index"]["assetCount"] == 1
    current = index.load_index(asset_type, str(root))
    assert current["stats"]["skippedCount"] == 0
    record = index.find_asset_by_path(current, "Asset_001")
    assert record is not None
    # Read the generated geometry using the viewer's real asset loader.
    loaded = loader.load_asset(asset_type, str(root), record, geometry=GeometryBlob())
    assert loaded.payload["meshes"]
    assert list(asset.glob("Texture-*.png"))
    assert loaded.parts[0].texture_candidates
    assert not loaded.parts[0].textures
    stale = asset / "obsolete" / "old.bin"
    stale.parent.mkdir()
    stale.write_bytes(b"old")
    assert "error" in bridge.extract_asset(str(root), "Asset_001", str(dump))
    assert stale.read_bytes() == b"old"
    result = bridge.extract_asset(str(root), "Asset_001", str(dump), str(asset))
    assert "error" not in result
    assert not stale.exists()
    assert (asset / "hash.json").exists()
    assert "previous_report_marker" not in installed_report(asset)
    result = bridge.extract_asset(str(root), "Asset_002", str(dump), str(asset))
    assert "error" not in result
    assert not asset.exists()
    assert (root / "Asset_002" / "hash.json").exists()
    installed_report(root / "Asset_002")
    assert index.find_asset_by_path(index.load_index(asset_type, str(root)), "Asset_002")
    assert not list(root.glob(".asset-extract-*"))
    assert len(events) == 3
    assert {p.name: p.read_bytes() for p in dump.iterdir() if p.is_file()} == dump_before


@pytest.mark.parametrize("failure", ["extraction", "index"])
def test_failed_update_preserves_all_files_and_index(library, monkeypatch, failure):
    root, dump = library
    extraction.extract_asset(str(root), "Asset_001", str(dump))
    asset = root / "Asset_001"
    (asset / "keep.bin").write_bytes(b"preserve")
    before = {p.relative_to(asset): p.read_bytes() for p in asset.rglob("*") if p.is_file()}
    saved_index = index.snapshot_index("ZZMI", str(root))
    def fail(*_args, **_kwargs):
        raise ValueError("synthetic failure")
    monkeypatch.setattr(extraction if failure == "extraction" else index,
                        "analyze" if failure == "extraction" else "save_index", fail)
    with pytest.raises(ValueError, match="synthetic failure"):
        extraction.extract_asset(str(root), "Asset_002", str(dump), str(asset))
    assert {p.relative_to(asset): p.read_bytes() for p in asset.rglob("*") if p.is_file()} == before
    assert index.snapshot_index("ZZMI", str(root)) == saved_index
    assert not (root / "Asset_002").exists()
    assert not list(root.glob(".asset-extract-*"))


@pytest.mark.parametrize("case", ["unpicked", "outside", "traversal", "reserved", "category", "overlap"])
def test_extraction_rejects_unauthorized_or_invalid_targets(library, tmp_path, case, monkeypatch):
    root, dump = library
    access = FolderAccess()
    bridge = AssetFolderRegistry(access)
    if case != "unpicked":
        access.remember_asset_picker_selection(str(dump))
    name = {"traversal": "../escape", "reserved": "CON"}.get(case, "Asset_001")
    parent = str(tmp_path) if case == "outside" else str(root)
    original = str(root) if case == "category" else None
    if case == "overlap":
        dump = root
        access.remember_asset_picker_selection(str(dump))
    assert "error" in bridge.extract_asset(parent, name, str(dump), original)
    assert list(root.iterdir()) == []


@pytest.mark.parametrize("morph, evidence, expected", [
    ("reversed_recognized", [True, True], True),
    ("reversed_recognized", [True, False], False),
    ("captured_expression", [True], False),
    ("not_detected", [True], False),
    ("reversed_recognized", [False], False),
    ("reversed_recognized", [], False),
])
def test_facial_grouping_requires_complete_material_and_expression_evidence(
        morph, evidence, expected):
    candidate = SimpleNamespace(
        component=SimpleNamespace(id="surface-01", parts=[
            SimpleNamespace(textures=[
                {"role": "Diffuse"},
                {"role_confidence": "unsupported_face_normal_encoding" if proven else "unknown"},
            ]) for proven in evidence]), morph_status=morph)
    body = SimpleNamespace(component=SimpleNamespace(id="surface-02", parts=[]),
                           morph_status="not_detected")
    assert _component_folders([candidate, body]) == (
        {"surface-01": "Face"} if expected else {})


def _facial_package(dump):
    package = extract(analyze(dump), Selection("Asset_001"))
    template = package.meshes[0]
    images = []
    for number, color in enumerate(((30, 60, 90), (100, 120, 140), (170, 190, 210), (50, 80, 110)), 1):
        filename = f"texture-{number}.png"
        Image.new("RGB", (16, 16), color).save(dump / filename)
        images.append({"file": filename, "hash": f"{number:08x}",
                       "sha256": digest(dump / filename), "auxiliary": False})
    meshes = []
    for number in range(1, 4):
        mesh = copy.deepcopy(template)
        mesh.component.id = f"surface-{number:02d}"
        mesh.component.name = f"Surface{number:02d}"
        mesh.component.ib = f"{number + 16:08x}"
        mesh.component.streams["position"]["hash"] = f"{number + 32:08x}"
        if number > 1:
            mesh.morph_status = "reversed_recognized"
            mesh.component.morph["status"] = mesh.morph_status
        if number == 2:
            mesh.component.parts.append(copy.deepcopy(mesh.component.parts[0]))
            mesh.component.parts[-1].first += mesh.component.parts[0].count
            mesh.indices.append(list(mesh.indices[0]))
        for part in mesh.component.parts:
            part.textures = [dict(images[0], role="NormalMap"),
                             dict(images[1 if number == 1 else 2], role="Diffuse")]
            if number > 1:
                part.textures.append(dict(images[3], role="TextureSlot04", auxiliary=True,
                                          role_confidence="unsupported_face_normal_encoding"))
        meshes.append(mesh)
    package.meshes = meshes
    package.analysis.components = [copy.deepcopy(mesh.component) for mesh in meshes]
    return package


def _loaded_snapshot(result):
    return {(part.component_name, part.classification, part.first_index, part.index_count): (
        part.positions, part.indices, part.normals, part.uvs,
        {role: digest(Path(texture.path)) for role, texture in part.textures.items()},
        sorted((texture.role or "", digest(Path(texture.path)))
               for texture in part.texture_candidates),
    ) for part in result.parts}


@pytest.mark.parametrize("layout, debug", [("compact", False), ("compatible", True)])
def test_face_export_preserves_geometry_bindings_and_metadata_provenance(
        library, tmp_path, monkeypatch, layout, debug):
    _root, dump = library
    package = _facial_package(dump)
    options = ExportOptions(layout=layout, debug_assets=debug)
    with monkeypatch.context() as baseline:
        baseline.setattr("app.assets.extractor.export._component_folders", lambda _meshes: {})
        flat = export(package, tmp_path / "flat", options=options)
    report = export(package, tmp_path / "grouped", options=options)
    asset = Path(report.output)
    root_meta = json.loads((asset / "hash.json").read_text(encoding="utf-8"))
    face_meta = json.loads((asset / "Face" / "hash.json").read_text(encoding="utf-8"))
    assert [item["component_name"] for item in root_meta["components"]] == ["Surface01"]
    assert [item["component_name"] for item in face_meta["components"]] == ["Surface02", "Surface03"]
    assert report.components == flat.components == 3
    assert report.parts == flat.parts == 4
    assert report.textures == flat.textures == (4 if debug else 3)
    assert len(list(asset.glob("*-ib=*.txt"))) == 1
    assert len(list((asset / "Face").glob("*-ib=*.txt"))) == 3
    if layout == "compact":
        assert (asset / "Texture-00000001.png").exists()
    if debug:
        assert len(list((asset / "Face" / "source_geometry").glob("*.buf"))) == 2
    details = json.loads(Path(report.report_path).read_text(encoding="utf-8"))
    for output in details["outputs"]:
        is_face = output["component"] != "surface-01"
        assert output["metadata"] == ("Face/hash.json" if is_face else "hash.json")
        for key in ("vb", "ib"):
            assert output[key].startswith("Face/") == is_face
            assert (asset / output[key]).is_file()
        for texture in output["textures"]:
            assert (asset / texture["output"]).is_file()
            if texture["hash"] == "00000003":
                assert texture["output"].startswith("Face/")
            if texture["role"] == "NormalMap":
                assert not texture["output"].startswith("Face/")
    for asset_type in ("ZZMI", "GIMI", "WWMI"):
        before_index = index.build_index(asset_type, str(Path(flat.output).parent))
        after_index = index.build_index(asset_type, str(asset.parent))
        before = loader.load_asset(asset_type, str(Path(flat.output).parent),
                                   before_index["assets"][0], geometry=GeometryBlob())
        after = loader.load_asset(asset_type, str(asset.parent),
                                  after_index["assets"][0], geometry=GeometryBlob())
        assert len(after.parts) == 4
        assert _loaded_snapshot(before) == _loaded_snapshot(after)
        assert {part.key.split("::")[1] for part in after.parts} == {
            "Asset_001/hash.json", "Asset_001/Face/hash.json"}
    package.meshes = package.meshes[1:]
    standalone = export(package, tmp_path / "standalone", options=options)
    assert not (Path(standalone.output) / "Face").exists()
    assert index.build_index("ZZMI", str(Path(standalone.output).parent))["stats"]["assetCount"] == 1


def test_cancelled_nested_face_export_preserves_existing_output(library, tmp_path):
    _root, dump = library
    package = _facial_package(dump)
    output = tmp_path / "output"
    output.mkdir()
    sentinel = output / "existing.bin"
    sentinel.write_bytes(b"preserve")
    def progress(stage, *_args):
        if stage == "Writing part Surface02B":
            raise Cancelled("synthetic cancellation")
    with pytest.raises(Cancelled):
        export(package, output, Context(progress=progress))
    assert list(output.iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b"preserve"


def test_exported_payload_collisions_preserve_geometry_and_unknown_candidates(library, tmp_path):
    _root, dump = library
    package = _facial_package(dump)
    expected = {}
    for ordinal, mesh in enumerate(package.meshes):
        mesh.component.ib = "aaaaaaaa"
        mesh.component.streams["position"]["hash"] = "bbbbbbbb"
        mesh.vertices = [[(row[0][0] + ordinal * 10, *row[0][1:]), *row[1:]]
                         for row in mesh.vertices]
        filename = f"unknown-{ordinal}.png"
        Image.new("RGB", (16, 16), (50 + ordinal * 30, 80, 110)).save(dump / filename)
        identity = digest(dump / filename)
        expected[mesh.component.name] = (ordinal * 10 + 1, identity)
        for part in mesh.component.parts:
            part.textures.append({"role": "TextureSlot09", "file": filename,
                                  "hash": "cccccccc", "sha256": identity, "auxiliary": False})
    multi = package.meshes[1]
    multi.component.parts[1].first = multi.component.parts[0].first
    multi.indices[1] = list(reversed(multi.indices[0]))
    report = export(package, tmp_path / "collisions")
    root = Path(report.output).parent
    asset = Path(report.output)
    record = index.build_index("ZZMI", str(root))["assets"][0]
    loaded = loader.load_asset("ZZMI", str(root), record, geometry=GeometryBlob())
    assert len(loaded.parts) == 4
    for part in loaded.parts:
        first_x, identity = expected[part.component_name]
        source = next(mesh for mesh in package.meshes if mesh.component.name == part.component_name)
        positions = source.vertices
        # The repeated-range IBs use different vertex orders; each must retain its own mapping.
        first_vertex = source.indices[part.component_ordinal][0] + source.component.parts[part.component_ordinal].base_vertex
        assert struct.unpack_from("<f", part.positions)[0] == positions[first_vertex][0][0]
        assert first_x <= min(struct.unpack_from("<f", part.positions, offset)[0]
                              for offset in range(0, len(part.positions), 12))
        assert identity in {digest(Path(texture.path)) for texture in part.texture_candidates}
        assert all(texture.role is None for texture in part.texture_candidates)
    for metadata_file in asset.rglob("hash.json"):
        metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
        for component in metadata["components"]:
            assert (metadata_file.parent / component["vb_file"]).is_file()
            assert all((metadata_file.parent / filename).is_file() for filename in component["ib_files"])
            for textures in component["texture_hashes"]:
                for texture in textures:
                    assert len(texture) == 4
                    assert (metadata_file.parent / texture[3]).is_file()

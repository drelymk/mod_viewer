"""Dump extraction, destructive replacement, and failure recovery contracts."""

import copy
import json
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from app.assets import extraction, folders, index, loader
from app.assets.extractor.core import Cancelled, Context, Unsupported
from app.assets.extractor.budget import CaptureBudget, ResourceLimit
from app.assets.extractor.capture import Inventory, parse_log
from app.assets.extractor import capture, discovery
from app.assets.extractor.discovery import analyze
from app.assets.extractor.geometry import decode_component
from app.assets.extractor.export import (
    ExportOptions, Selection, _component_folders, export, extract,
)
from app.assets.extractor.textures import collect, digest
from app.assets.extractor.profiles.gimi import material_roles as gimi_material_roles
from app.bridge.access import FolderAccess
from app.bridge.registry import AssetFolderRegistry
from app.settings import paths
from core.geometry.mesh_builder import GeometryBlob
from tests.support.extraction_data import add_direct_mesh, header, make_capture
from tests.support.dds_data import dx10_dds


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


@pytest.mark.parametrize("case,hashed,excluded", [
    ("no_material", set(), False),
    ("outline_only", set(), False),
    ("color_only", set(), False),
    ("mixed_pass", set(), False),
    ("subset", {"44444444", "66666666"}, True),
    ("different_surface", {"44444444", "66666666"}, False),
    ("unsupported_color", {"66666666"}, False),
])
def test_outline_overlap_work_requires_outline_and_color_evidence(tmp_path, monkeypatch, case, hashed, excluded):
    dump = make_capture(tmp_path)
    source = next(dump.glob("000002-ib=*.txt"))
    log = dump / "log.txt"
    for call, identity in (("000004", "66666666"), ("000005", "77777777")):
        target = dump / f"{call}-ib={identity}.txt"
        target.write_text(source.read_text())
        # Reverse winding; position subsets also account for the base vertex.
        target.with_suffix(".buf").write_bytes(struct.pack("<6H", 3, 3, 3, 2, 1, 0))
        base = 0 if call == "000004" and case == "different_surface" else 1
        with log.open("a", encoding="utf-8") as stream:
            stream.write(f"{call} IASetIndexBuffer(pIndexBuffer:0x{call}, Format:57, Offset:0) hash={identity}\n"
                         f"{call} DrawIndexed(IndexCount:3, StartIndexLocation:3, BaseVertexLocation:{base})\n")
    if case == "subset":
        log.write_text(log.read_text().replace(
            "IndexCount:3, StartIndexLocation:3, BaseVertexLocation:1)",
            "IndexCount:6, StartIndexLocation:3, BaseVertexLocation:0)", 2))
        for path in dump.glob("*-ib=44444444*.txt"):
            path.write_text(path.read_text().replace("index count: 3", "index count: 6"))
            path.with_suffix(".buf").write_bytes(struct.pack("<9H", 3, 3, 3, 1, 2, 3, 0, 1, 2))
    if case == "unsupported_color":
        source.with_suffix(".buf").write_bytes(b"")

    def material(_inv, draws, _ctx):
        kinds = []
        if draws[0].id == "000002" and case not in ("no_material", "outline_only"):
            kinds = ["color"]
        elif draws[0].id == "000004" and case not in ("no_material", "color_only"):
            kinds = ["outline", "color"] if case == "mixed_pass" else ["outline"]
        return [dict(file="log.txt", pass_kind=kind) for kind in kinds]

    signed = []
    original = discovery._surface_signatures
    def signatures(mesh):
        signed.append(mesh.component.ib)
        return original(mesh)
    monkeypatch.setattr(discovery, "collect", material)
    monkeypatch.setattr(discovery, "_surface_signatures", signatures)
    analysis = analyze(dump)
    assert set(signed) == hashed
    color, outline, unrelated = analysis.components
    assert bool(outline.provenance.get("duplicate_outline_pass")) == excluded
    assert outline.default_included is not excluded
    assert "surface_overlap" not in unrelated.provenance
    if excluded:
        assert outline.provenance["surface_overlap"]["component"] == color.id
        assert outline.provenance["surface_overlap"]["triangle_signatures"] == 1
        assert {m.component.id for m in extract(analysis).meshes} == {color.id, unrelated.id}
        assert extract(analysis, Selection(ids=[outline.id])).meshes[0].component.id == outline.id


def test_texture_lookup_retains_log_only_snapshots_and_dynamic_alternates(tmp_path):
    dump = make_capture(tmp_path)
    dedup = dump / "deduped"
    dedup.mkdir()
    log = dump / "log.txt"
    text = log.read_text(encoding="utf-8")
    for call, color in (("000002", (40, 80, 120)), ("000003", (80, 120, 160))):
        payload = dedup / f"{call}-unknown.png"
        Image.new("RGB", (16, 16), color).save(payload)
        text += (f"{call} 3DMigoto Dumping capture\\{call}-ps-t0=66666666.png"
                 f" -> deduped\\{payload.name}\n")
    Image.new("RGB", (16, 16)).save(dedup / "color.png")
    text += "000002 3DMigoto Dumping capture\\000002-o0=77777777.png -> deduped\\color.png\n"
    text += "000003 3DMigoto Dumping capture\\000003-o0=77777777.png -> deduped\\color.png\n"
    log.write_text(text, encoding="utf-8")
    Image.new("RGB", (16, 16), (200, 200, 200)).save(dump / "000099-ps-t0=66666666.png")
    inv = Inventory(dump)
    calls = parse_log(inv)

    class DrawLookups(dict):
        def __iter__(self):
            raise AssertionError("Texture collection must not scan unrelated draws")

        def items(self):
            raise AssertionError("Texture collection must not scan unrelated draws")

    inv.entries = DrawLookups(inv.entries)
    draws = [call for call in calls if call.kind == "DrawIndexed"]
    for _ in range(2):
        textures = collect(inv, draws, Context())
        assert [t["draw"] for t in textures] == ["000002", "000003"]
        assert [t["role_confidence"] for t in textures] == ["unknown", "dynamic_alternate"]
        assert [t["sha256"] for t in textures] == [digest(dedup / f"{call}-unknown.png")
                                                   for call in ("000002", "000003")]
        assert inv.find("000002", "ib", "44444444", ".txt") == next(dump.glob("000002-ib=*.txt")).name


@pytest.mark.parametrize("role,declared,first,trim,expected", [
    ("texcoord", 3, 0, 0, "included"),
    ("texcoord", 5, 0, 0, "Partial vertex snapshot"),
    ("texcoord", -1, 0, 0, "Partial vertex snapshot"),
    ("texcoord", 3, 1, 0, "Partial vertex snapshot"),
    ("texcoord", 3, 0, 12, "Inconsistent texcoord vertex count"),
    ("texcoord", 3, 0, 1, "Truncated texcoord buffer"),
    ("position", 3, 0, 0, "Partial vertex snapshot"),
    ("blend", 3, 0, 0, "Partial vertex snapshot"),
])
def test_draw_subset_uv_header_requires_complete_matching_payload(
        tmp_path, role, declared, first, trim, expected):
    dump = make_capture(tmp_path)
    log = dump / "log.txt"
    log.write_text(log.read_text(encoding="utf-8").replace(
        "BaseVertexLocation:1", "BaseVertexLocation:0"), encoding="utf-8")
    slot = {"position": 0, "texcoord": 1, "blend": 2}[role]
    header = next(dump.glob(f"000001-vb{slot}=*.txt"))
    header.write_text(header.read_text().replace(
        "vertex count: 4", f"vertex count: {declared}").replace(
        "first vertex: 0", f"first vertex: {first}"))
    if trim:
        payload = header.with_suffix(".buf")
        payload.write_bytes(payload.read_bytes()[:-trim])
    analysis = analyze(dump)
    component, = analysis.components
    if expected == "included":
        package = extract(analysis)
        mesh, = package.meshes
        assert component.vertex_count == len(mesh.vertices) == 4
        assert mesh.indices == [[0, 1, 2]]
        assert not package.partial
    else:
        assert component.status == "unsupported"
        assert any(expected in message for message in component.diagnostics)


@pytest.mark.parametrize("case,expected", [
    ("valid", None), ("aliases", None),
    ("missing_normal", "requires declared point-list positions and normals"),
    ("normal_offset", "requires declared point-list positions and normals"),
    ("duplicate_position", "requires declared point-list positions and normals"),
    ("topology", "requires declared point-list positions and normals"),
    ("truncated", "Ambiguous or missing pre-skinning blend stream"),
    ("partial", "Partial vertex snapshot"),
    ("nonfinite", "Nonfinite position"),
])
def test_short_skinning_positions_require_complete_declared_layout(tmp_path, case, expected):
    dump = make_capture(tmp_path)
    position = next(dump.glob("000001-vb0=*.txt"))
    fields = [("POSITION", 0, "R32G32B32_FLOAT", 0),
              ("NORMAL", 0, "R32G32B32_FLOAT", 12)]
    if case == "missing_normal":
        fields.pop()
    elif case == "normal_offset":
        fields[-1] = ("NORMAL", 0, "R32G32B32_FLOAT", 0)
    elif case == "duplicate_position":
        fields.append(("POSITION", 0, "R32G32B32_FLOAT", 12))
    elif case == "aliases":
        fields.extend((semantic, 0, "R8G8B8A8_UNORM", 0) for semantic in ("COLOR", "TEXCOORD", "TANGENT"))
    text = header(24, 0, fields)
    if case == "topology":
        text = text.replace("pointlist", "trianglelist")
    elif case == "partial":
        text = text.replace("vertex count: 4", "vertex count: 3")
    position.write_text(text)
    payload = b"".join(struct.pack("<6f", i, 0, 0, 0, 0, 1) for i in range(4))
    if case == "truncated":
        payload = payload[:-1]
    elif case == "nonfinite":
        payload = struct.pack("<f", float("nan")) + payload[4:]
    position.with_suffix(".buf").write_bytes(payload)
    analysis = analyze(dump)
    component, = analysis.components
    if expected:
        assert not component.default_included
        assert expected in " ".join([component.reason, *component.diagnostics])
        return
    mesh, = extract(analysis).meshes
    assert mesh.source_position == payload
    assert [e.label for e in mesh.elements] == ["POSITION", "NORMAL", "BLENDWEIGHTS", "BLENDINDICES", "COLOR", "TEXCOORD", "TEXCOORD1"]
    assert mesh.indices == [[0, 1, 2]]
    assert [v[:2] for v in mesh.vertices] == [[(i, 0, 0), (0, 0, 1)] for i in range(4)]
    # The recognized morph adapter uses 40-byte records and cannot edit this layout.
    component.morph["dispatches"] = [{"delta": "missing", "constant": "missing"}]
    preserved, = extract(analysis).meshes
    assert preserved.vertices == mesh.vertices
    assert preserved.morph_status == "captured_expression"
    assert any("Unrecognized morph position layout" in note for note in preserved.component.diagnostics)


@pytest.mark.parametrize("case,channel,value", [
    ("valid", None, 0), ("secondary", 1, float("nan")), ("secondary", 1, float("inf")),
    ("secondary", 3, float("nan")), ("primary", 0, float("nan")), ("primary", 0, float("inf")),
    ("duplicate", 1, float("nan")), ("missing_primary", 1, float("nan")),
])
def test_secondary_uv_recovery_preserves_primary_geometry_through_export(
        tmp_path, case, channel, value):
    dump = make_capture(tmp_path)
    uv_header = next(dump.glob("000001-vb1=*.txt"))
    fields = [("COLOR", 0, "R32G32B32A32_FLOAT", 0), *[
        ("TEXCOORD", i, "R32G32_FLOAT", 16 + 8 * i) for i in range(4)]]
    if case == "duplicate":
        fields.append(("TEXCOORD", 1, "R32G32_FLOAT", 32))
    elif case == "missing_primary":
        fields = [field for field in fields if field[:2] != ("TEXCOORD", 0)]
    uv_header.write_text(header(48, 1, fields))
    payload = bytearray(b"".join(struct.pack(
        "<12f", 1, 0.5, 0.25, 1, i / 4, 0.25, 0.125, 0.375, 0.5, 0.625, 0.75, 0.875)
        for i in range(4)))
    if channel is not None:
        struct.pack_into("<f", payload, 48 + 16 + 8 * channel, value)
    uv_header.with_suffix(".buf").write_bytes(payload)
    original = {p.name: digest(p) for p in dump.iterdir()}
    analysis = analyze(dump)
    component, = analysis.components
    if case in ("primary", "duplicate", "missing_primary"):
        assert component.status == "unsupported"
        reason = "Conflicting vertex semantic channels" if case == "duplicate" else "Nonfinite texcoord"
        assert any(reason in message for message in component.diagnostics)
        assert {p.name: digest(p) for p in dump.iterdir()} == original
        return
    assert component.default_included
    package = extract(analysis)
    mesh, = package.meshes
    assert len(mesh.vertices) == 4 and mesh.indices == [[0, 1, 2]]
    expected = {0, 1, 2, 3} - ({channel} if channel is not None else set())
    assert {e.index for e in mesh.elements if e.semantic == "TEXCOORD"} == expected
    for column, element in enumerate(mesh.elements):
        if element.semantic == "TEXCOORD":
            expected_uvs = ([(i / 4, 0.25) for i in range(4)] if element.index == 0 else
                           [{1: (0.125, 0.375), 2: (0.5, 0.625), 3: (0.75, 0.875)}[element.index]] * 4)
            assert [row[column] for row in mesh.vertices] == expected_uvs
    notes = [note for note in component.diagnostics if "secondary UV" in note]
    assert len(notes) == (channel is not None)
    if channel is not None:
        assert f"TEXCOORD{channel}" in notes[0]
    report = export(package, tmp_path / "exports")
    root = str(Path(report.output).parent)
    record, = index.build_index("ZZMI", root)["assets"]
    loaded = loader.load_asset("ZZMI", root, record, geometry=GeometryBlob())
    part, = loaded.parts
    assert list(struct.iter_unpack("<3f", part.positions)) == [(1, 0, 0), (2, 0, 0), (3, 0, 0)]
    assert list(struct.iter_unpack("<2f", part.uvs)) == [(i / 4, 0.75) for i in (1, 2, 3)]
    assert {p.name: digest(p) for p in dump.iterdir()} == original


def test_direct_secondary_uv_recovery_uses_primary_from_position_stream(tmp_path):
    dump = make_capture(tmp_path)
    add_direct_mesh(dump)
    for call in ("000004", "000005"):
        position = next(dump.glob(f"{call}-vb0=*.txt"))
        position.write_text(header(36, 0, [
            ("POSITION", 0, "R32G32B32_FLOAT", 4),
            ("NORMAL", 0, "R32G32B32_FLOAT", 16),
            ("TEXCOORD", 0, "R32G32_FLOAT", 28),
        ]).replace("pointlist", "trianglelist"))
        position.with_suffix(".buf").write_bytes(b"".join(
            struct.pack("<9f", 7, i, 2, -1, 0, 0, 1, i / 4, 0.25) for i in range(4)))
        uv = next(dump.glob(f"{call}-vb1=*.txt"))
        uv.write_text(header(4, 1, [("TEXCOORD", 1, "R16G16_FLOAT", 0)]).replace(
            "pointlist", "trianglelist"))
        uv.with_suffix(".buf").write_bytes(b"".join(
            struct.pack("<2e", float("nan") if i == 1 else 0.125, 0.375) for i in range(4)))
    original = {p.name: digest(p) for p in dump.iterdir()}
    analysis = analyze(dump)
    direct, = [c for c in analysis.components if c.provenance.get("geometry_kind") == "direct"]
    assert direct.status == "included"
    assert any("Omitted TEXCOORD1" in note for note in direct.diagnostics)
    package = extract(analysis, Selection("Asset_001", ids=[direct.id]))
    mesh, = package.meshes
    assert [e.index for e in mesh.elements if e.semantic == "TEXCOORD"] == [0]
    report = export(package, tmp_path / "exports")
    root = str(Path(report.output).parent)
    record, = index.build_index("ZZMI", root)["assets"]
    part, = loader.load_asset("ZZMI", root, record, geometry=GeometryBlob()).parts
    assert list(struct.iter_unpack("<3f", part.positions)) == [(i, 2, -1) for i in (1, 2, 3)]
    assert list(struct.iter_unpack("<2f", part.uvs)) == [(i / 4, 0.75) for i in (1, 2, 3)]
    assert {p.name: digest(p) for p in dump.iterdir()} == original


def test_direct_mesh_export_keeps_coordinates_and_nested_metadata(tmp_path):
    dump = make_capture(tmp_path)
    add_direct_mesh(dump)
    image = dump / "000004-ps-t0=66666666.png"
    Image.new("RGBA", (16, 16), (80, 120, 160, 255)).save(image)
    analysis = analyze(dump)
    skinned, direct = analysis.components
    assert direct.status == "included"
    assert direct.provenance["geometry_kind"] == "direct"
    assert direct.parts[0].draws == ["000004", "000005"]
    assert "blend" not in direct.streams
    assert direct.unproven_direct and direct.requires_review and not direct.default_included
    direct.provenance["gimi_face_family"] = direct.id
    package = extract(analysis, Selection("Face", [direct.id], {direct.id: "Attachment"}))
    mesh, = package.meshes
    assert [vertex[0] for vertex in mesh.vertices] == [(i, 2, -1) for i in range(4)]
    assert not any(element.semantic.startswith("BLEND") for element in mesh.elements)
    assert mesh.indices == [[0, 1, 2]]
    combined = extract(analysis, Selection("Asset_001", renames={direct.id: "Attachment"}))
    report = export(combined, tmp_path / "exports")
    asset = Path(report.output)
    metadata = json.loads((asset / "Face" / "hash.json").read_text(encoding="utf-8"))
    component, = metadata["components"]
    assert component["blend_vb"] == ""
    assert component["position_vb"] == "77777777"
    assert component["object_indexes"] == [3]
    current = index.build_index("GIMI", str(asset.parent))
    assert current["stats"]["assetCount"] == 1
    loaded = loader.load_asset("GIMI", str(asset.parent),
                               current["assets"][0], geometry=GeometryBlob())
    assert len(loaded.parts) == 2
    attachment, = [p for p in loaded.parts if p.component_name == "Attachment"]
    assert list(struct.iter_unpack("<3f", attachment.positions)) == [(i, 2, -1) for i in (1, 2, 3)]
    assert list(struct.iter_unpack("<I", attachment.indices)) == [(0,), (1,), (2,)]
    assert len(attachment.normals) == len(attachment.positions)
    assert len(attachment.uvs) == 3 * 8
    assert len(attachment.texture_candidates) == 1
    assert digest(Path(attachment.texture_candidates[0].path)) == digest(image)
    details = json.loads(Path(report.report_path).read_text(encoding="utf-8"))
    direct_output, = [o for o in details["outputs"] if o["component"] == direct.id]
    assert direct_output["metadata"] == "Face/hash.json"
    assert (asset / direct_output["vb"]).is_file()
    assert direct_output["textures"][0]["output"].startswith("Face/")
    standalone = export(package, tmp_path / "standalone")
    assert (Path(standalone.output) / "hash.json").is_file()
    assert not (Path(standalone.output) / "Face").exists()


@pytest.mark.parametrize("asset_type", ["ZZMI", "GIMI", "WWMI"])
def test_unproven_direct_geometry_is_reported_but_requires_explicit_selection(library, monkeypatch, asset_type):
    root, dump = library
    add_direct_mesh(dump)
    log = dump / "log.txt"
    log.write_text(log.read_text(encoding="utf-8").replace("ZZMIv1", asset_type + "v1"), encoding="utf-8")
    monkeypatch.setattr(folders, "load_registry", lambda: [
        {"type": asset_type, "path": folders.normalize_path(root)}])
    analysis = analyze(dump)
    skinned, direct = analysis.components
    assert direct.status == "included" and direct.vertex_count == 4
    assert direct.unproven_direct and direct.requires_review and not direct.default_included
    assert "membership is unproven" in direct.reason
    package = extract(analysis)
    assert [m.component.id for m in package.meshes] == [skinned.id]
    assert not package.partial
    report = export(package, root / "exports")
    details = json.loads(Path(report.report_path).read_text(encoding="utf-8"))
    assert not report.partial and report.skipped == 0 and report.unproven_candidates == 1
    assert details["report"]["skipped"] == 0 and details["report"]["unproven_candidates"] == 1
    candidate, = [c for c in details["analysis"]["components"] if c["id"] == direct.id]
    assert candidate["streams"] == direct.streams and candidate["parts"][0]["count"] == 3
    assert direct.id not in {c["id"] for c in details["components"]}
    explicit = extract(analysis, Selection("Reviewed", [direct.id]))
    assert not explicit.partial
    assert explicit.skipped == explicit.unproven_candidates == 0
    assert [v[0] for v in explicit.meshes[0].vertices] == [(i, 2, -1) for i in range(4)]
    saved = extraction.extract_asset(str(root), "Asset_001", str(dump))
    assert saved["extraction"] == {
        "components": 1, "parts": 1, "partial": False, "skipped": 0, "unproven_candidates": 1}
    # An unsupported group still warns, without counting the unproven direct mesh twice.
    with log.open("a", encoding="utf-8") as stream:
        stream.write("""000006 IASetVertexBuffers(StartSlot:0, NumBuffers:1)
       0: resource=0x90 hash=aaaa0001
000006 IASetIndexBuffer(pIndexBuffer:0xa0, Format:57, Offset:0) hash=aaaa0002
000006 DrawIndexed(IndexCount:3, StartIndexLocation:0, BaseVertexLocation:0)
""")
    updated = extraction.extract_asset(str(root), "Asset_001", str(dump), saved["path"])
    assert updated["extraction"] == {
        "components": 1, "parts": 1, "partial": True, "skipped": 1, "unproven_candidates": 1}
    installed = json.loads((Path(updated["path"]) / "extraction_report.json").read_text(encoding="utf-8"))
    assert installed["report"]["partial"]
    assert installed["report"]["skipped"] == installed["report"]["unproven_candidates"] == 1


@pytest.mark.parametrize("count,first,merged", [(3, 0, True), (5, 0, False), (3, 1, False)])
def test_direct_repeated_draw_groups_complete_uv_payloads_without_hiding_invalid_headers(tmp_path, count, first, merged):
    dump = make_capture(tmp_path)
    add_direct_mesh(dump)
    layout = dump / "000005-vb1=99999999.txt"
    layout.write_text(layout.read_text().replace("vertex count: 4", f"vertex count: {count}")
                      .replace("first vertex: 0", f"first vertex: {first}"))
    analysis = analyze(dump)
    direct = [c for c in analysis.components if c.provenance.get("geometry_kind") == "direct"]
    if merged:
        component, = direct
        assert component.status == "included"
        assert component.parts[0].draws == ["000004", "000005"]
    else:
        assert len(direct) == 2
        assert sum(c.status == "included" for c in direct) == 1
        assert sum(c.status == "unsupported" for c in direct) == 1


@pytest.mark.parametrize("case,expected", [
    ("stride", "Vertex stride must be positive"),
    ("normal", "requires declared triangle-list positions and normals"),
    ("first", "Partial vertex snapshot"),
    ("uv", "Inconsistent texcoord vertex count"),
    ("index", "Index plus base vertex is outside vertex streams"),
    ("compute", "No supported stream-output skinning producer"),
])
def test_direct_geometry_rejects_incomplete_or_shader_written_streams(tmp_path, case, expected):
    dump = make_capture(tmp_path)
    add_direct_mesh(dump)
    for call_id in ("000004", "000005"):
        position = dump / f"{call_id}-vb0=77777777.txt"
        if case in ("stride", "normal", "first"):
            old, new = {"stride": ("stride: 28", "stride: 0"),
                        "normal": ("SemanticName: NORMAL", "SemanticName: UNKNOWN"),
                        "first": ("first vertex: 0", "first vertex: 1")}[case]
            position.write_text(position.read_text().replace(old, new))
        elif case == "uv":
            payload = dump / f"{call_id}-vb1=99999999.buf"
            payload.write_bytes(payload.read_bytes()[:-12])
        elif case == "index":
            (dump / f"{call_id}-ib=88888888.buf").write_bytes(struct.pack("<6H", 3, 3, 3, 0, 1, 9))
    if case == "compute":
        log = dump / "log.txt"
        log.write_text(log.read_text(encoding="utf-8").replace(
            "000004 IASetVertexBuffers", """000004 CSSetUnorderedAccessViews(StartSlot:0, NumUAVs:1)
       0: resource=0x60 hash=77777777
000004 Dispatch(ThreadGroupCountX:1, ThreadGroupCountY:1, ThreadGroupCountZ:1)
000004 IASetVertexBuffers"""), encoding="utf-8")
    analysis = analyze(dump)
    assert sum(c.status == "included" for c in analysis.components) == 1
    assert any(expected in c.reason or any(expected in note for note in c.diagnostics)
               for c in analysis.components)


@pytest.mark.parametrize("formats,diffuse_slot", [
    (("BC7_UNORM_SRGB", "BC7_UNORM"), 0),
    (("BC7_UNORM", "BC7_UNORM_SRGB"), 1),
    (("BC7_UNORM", "BC7_UNORM", "BC7_UNORM_SRGB"), 2),
    (("R8G8B8A8_UNORM_SRGB", "BC7_UNORM_SRGB"), 1),
])
def test_gimi_material_pass_exports_named_diffuse_and_loads_automatically(tmp_path, formats, diffuse_slot):
    dump = make_capture(tmp_path)
    log = dump / "log.txt"
    text = log.read_text(encoding="utf-8").replace("ZZMIv1", "GIMIv8")
    bindings = []
    dumps = []
    for slot, fmt in enumerate(formats):
        geometry_hash = f"{slot + 6:08x}"
        filename = f"000002-ps-t{slot}={geometry_hash}.dds"
        width = 4 if fmt.startswith("R8") else 128
        code = {"BC7_UNORM": 98, "BC7_UNORM_SRGB": 99, "R8G8B8A8_UNORM_SRGB": 29}[fmt]
        (dump / filename).write_bytes(dx10_dds(bytes(width * width), dxgi_format=code, width=width, height=width))
        bindings.append(f"       {slot}: resource=0x{slot + 6:x}0 hash={geometry_hash}\n")
        dumps.append(f"000002 3DMigoto Dumping capture\\{filename} -> dedup\\{geometry_hash}-{fmt}.dds\n")
    binding_text = ("000002 PSSetShader(pPixelShader:0x90) hash=cccccccccccccccc\n"
                    f"000002 PSSetShaderResources(StartSlot:0, NumViews:{len(formats)})\n"
                    + "".join(bindings) + "".join(dumps))
    text = text.replace("000002 DrawIndexed", binding_text + "000002 DrawIndexed")
    log.write_text(text, encoding="utf-8")
    for call_id in ("000002", "000003"):
        Image.new("RGB", (16, 16)).save(dump / f"{call_id}-o0=12345678.png")
    # A later effect pass has more textures, but no recognized material signature.
    for slot in range(5, 10):
        Image.new("RGB", (16, 16)).save(dump / f"000003-ps-t{slot}=aaaaaaaa.png")
    analysis = analyze(dump)
    component, = analysis.components
    diffuse, = [t for t in component.parts[0].textures if t["role"] == "Diffuse"]
    assert diffuse["slot"] == diffuse_slot
    assert diffuse["draw"] == "000002"
    report = export(extract(analysis, Selection("Asset_001")), tmp_path / "exports")
    asset = Path(report.output)
    expected = f"Asset_001-Component001-Diffuse-{diffuse['hash']}.dds"
    assert (asset / expected).is_file()
    current = index.build_index("GIMI", str(asset.parent))
    loaded = loader.load_asset("GIMI", str(asset.parent), current["assets"][0], geometry=GeometryBlob())
    part, = loaded.parts
    assert set(part.textures) == {"diffuse"}
    assert Path(part.textures["diffuse"].path).name == expected
    assert digest(Path(part.textures["diffuse"].path)) == diffuse["sha256"]
    assert all(t["role"] in ("Diffuse",) or t["role"].startswith("TextureSlot")
               for t in component.parts[0].textures)
    candidates = [t for t in component.parts[0].textures if not t.get("auxiliary")]
    assert report.texture_files == len(candidates)
    unknown = [t for t in component.parts[0].textures if t["role"].startswith("TextureSlot") and not t.get("auxiliary")]
    assert len(part.texture_candidates) == len(unknown)
    assert {digest(Path(t.path)) for t in part.texture_candidates} == {t["sha256"] for t in unknown}
    assert all(t.role is None for t in part.texture_candidates)
    assert len(unknown) == sum(fmt == "BC7_UNORM" for fmt in formats)
    debug = export(extract(analysis, Selection("Asset_001")), tmp_path / "debug",
                   options=ExportOptions(debug_assets=True))
    if report.omitted_texture_associations:
        assert list((Path(debug.output) / "assets").iterdir())
    log.write_text(text.replace("GIMIv8", "Unrecognized"), encoding="utf-8")
    unrecognized = analyze(dump)
    assert all(t["role"].startswith("TextureSlot") for c in unrecognized.components
               for p in c.parts for t in p.textures)


@pytest.mark.parametrize("case", ["no_color", "no_shader", "contaminated", "unbound", "small", "nonsquare", "ambiguous", "unknown_format"])
def test_gimi_diffuse_signatures_reject_ambiguous_evidence(case):
    textures = [dict(slot=i, hash=f"{i + 6:08x}", resource={"hash": f"{i + 6:08x}"},
                     format=fmt, width=128, height=128)
                for i, fmt in enumerate(("BC7_UNORM_SRGB", "BC7_UNORM"))]
    if case == "contaminated":
        textures[0]["contaminated"] = True
    elif case == "unbound":
        textures[0]["resource"] = None
    elif case == "small":
        textures[0].update(width=4, height=4)
    elif case == "nonsquare":
        textures[0]["height"] = 64
    elif case == "ambiguous":
        textures[1]["format"] = "BC7_UNORM_SRGB"
    elif case == "unknown_format":
        textures[1]["format"] = "unknown"
    assert gimi_material_roles(textures, case != "no_color",
                               "" if case == "no_shader" else "cccccccccccccccc") == {}


def test_gimi_complete_material_pass_precedes_larger_inherited_bindings(tmp_path):
    dump = make_capture(tmp_path)
    log = dump / "log.txt"
    text = log.read_text(encoding="utf-8").replace("ZZMIv1", "GIMIv8")
    for call_id, entries in [
        ("000002", [(0, "BC7_UNORM_SRGB"), (1, "BC7_UNORM"), *[(slot, "BC7_UNORM") for slot in range(5, 10)]]),
        ("000003", [(0, "BC7_UNORM"), (1, "BC7_UNORM"), (2, "BC7_UNORM_SRGB")]),
    ]:
        bindings, dumps = [], []
        for slot, fmt in entries:
            geometry_hash = f"{int(call_id) * 16 + slot:08x}"
            filename = f"{call_id}-ps-t{slot}={geometry_hash}.dds"
            (dump / filename).write_bytes(dx10_dds(bytes(128 * 128), width=128, height=128,
                                                  dxgi_format=99 if fmt.endswith("SRGB") else 98))
            bindings.append(f"       {slot}: resource=0x{int(call_id) * 16 + slot:x}0 hash={geometry_hash}\n")
            dumps.append(f"{call_id} 3DMigoto Dumping capture\\{filename} -> dedup\\{geometry_hash}-{fmt}.dds\n")
        setup = (f"{call_id} PSSetShader(pPixelShader:0x90) hash=cccccccccccccccc\n"
                 f"{call_id} PSSetShaderResources(StartSlot:0, NumViews:10)\n"
                 + "".join(bindings) + "".join(dumps))
        text = text.replace(f"{call_id} DrawIndexed", setup + f"{call_id} DrawIndexed")
        Image.new("RGB", (16, 16)).save(dump / f"{call_id}-o0=12345678.png")
    log.write_text(text, encoding="utf-8")
    component, = analyze(dump).components
    diffuse, = [t for t in component.parts[0].textures if t["role"] == "Diffuse"]
    assert diffuse["draw"] == "000003"
    assert diffuse["slot"] == 2
    assert all(t["draw"] == "000003" for t in component.parts[0].textures)


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
        "components": 1, "parts": 1, "partial": True, "skipped": 1, "unproven_candidates": 0}
    assert result["warnings"] == []
    asset = Path(result["path"])
    metadata_name = "Metadata.json" if asset_type == "WWMI" else "hash.json"
    def installed_report(folder):
        filename = folder / "extraction_report.json"
        details = json.loads(filename.read_text(encoding="utf-8"))
        assert details["report"]["output"] == "."
        assert details["report"]["report_path"] == filename.name
        assert "dump" not in details["analysis"]
        assert str(dump.parent) not in filename.read_text(encoding="utf-8").replace("\\\\", "\\")
        assert details["outputs"]
        return details
    original_report = installed_report(asset)
    original_report["previous_report_marker"] = True
    (asset / "extraction_report.json").write_text(json.dumps(original_report), encoding="utf-8")
    assert list(asset.rglob(metadata_name))
    assert result["folders"][0]["index"]["assetCount"] == 1
    current = index.load_index(asset_type, str(root))
    assert current["stats"]["skippedCount"] == 0
    record = index.find_asset_by_path(current, "Asset_001")
    assert record is not None
    # Read the generated geometry using the viewer's real asset loader.
    loaded = loader.load_asset(asset_type, str(root), record, geometry=GeometryBlob())
    assert loaded.payload["meshes"]
    texture_pattern = "Components-0 t=*.png" if asset_type == "WWMI" else "Asset_001-Component001-TextureSlot00-*.png"
    assert list(asset.rglob(texture_pattern))
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
    assert list(asset.rglob(metadata_name))
    assert "previous_report_marker" not in installed_report(asset)
    result = bridge.extract_asset(str(root), "Asset_002", str(dump), str(asset))
    assert "error" not in result
    assert not asset.exists()
    assert list((root / "Asset_002").rglob(metadata_name))
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
    ("not_detected", [True], True),
    ("not_detected", [True, False], False),
    ("reversed_recognized", [False], False),
    ("reversed_recognized", [], False),
])
def test_facial_grouping_requires_complete_material_and_expression_evidence(
        morph, evidence, expected):
    candidate = SimpleNamespace(
        component=SimpleNamespace(id="surface-01", provenance={}, parts=[
            SimpleNamespace(textures=[
                {"role": "Diffuse"},
                {"role_confidence": "unsupported_face_normal_encoding" if proven else "unknown"},
            ]) for proven in evidence]), morph_status=morph)
    body = SimpleNamespace(component=SimpleNamespace(id="surface-02", provenance={}, parts=[]),
                           morph_status="not_detected")
    assert _component_folders([candidate, body]) == (
        {"surface-01": "Face"} if expected else {})


def _facial_package(dump, morph_status="reversed_recognized"):
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
            mesh.morph_status = morph_status
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
@pytest.mark.parametrize("morph_status", ["reversed_recognized", "not_detected"])
def test_face_export_preserves_geometry_bindings_and_metadata_provenance(
        library, tmp_path, monkeypatch, layout, debug, morph_status):
    _root, dump = library
    package = _facial_package(dump, morph_status)
    options = ExportOptions(layout=layout, debug_assets=debug)
    with monkeypatch.context() as baseline:
        baseline.setattr("app.assets.extractor.export._component_folders", lambda _meshes: {})
        flat = export(package, tmp_path / "flat", options=options)
    report = export(package, tmp_path / "grouped", options=options)
    asset = Path(report.output)
    root_meta = json.loads((asset / "hash.json").read_text(encoding="utf-8"))
    face_meta = json.loads((asset / "Face" / "hash.json").read_text(encoding="utf-8"))
    assert root_meta["format"] == face_meta["format"] == "mod-viewer"
    assert [item["component_name"] for item in root_meta["components"]] == ["Surface01"]
    assert [item["component_name"] for item in face_meta["components"]] == ["Surface02", "Surface03"]
    assert report.components == flat.components == 3
    assert report.parts == flat.parts == 4
    assert report.textures == flat.textures == (4 if debug else 3)
    assert len(list(asset.glob("*-ib=*.txt"))) == 1
    assert len(list((asset / "Face").glob("*-ib=*.txt"))) == 3
    if layout == "compact":
        assert (asset / "Asset_001-Surface01-NormalMap-00000001.png").exists()
        assert report.texture_files == 3
        assert report.texture_associations > report.texture_files
    if debug:
        assert len(list((asset / "Face" / "source_geometry").glob("*.buf"))) == (
            2 if morph_status == "reversed_recognized" else 0)
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
    for asset_type in ("ZZMI", "GIMI"):
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


def test_compact_texture_sharing_preserves_role_and_native_hash_identity(library, tmp_path):
    _root, dump = library
    package = _facial_package(dump)
    shared = dict(package.meshes[0].component.parts[0].textures[0])
    for mesh in package.meshes[1:]:
        for part in mesh.component.parts:
            part.textures[1] = dict(shared, role="Diffuse")
    alias = dump / "normal-alias.png"
    alias.write_bytes((dump / shared["file"]).read_bytes())
    package.meshes[2].component.parts[0].textures[0] = dict(
        shared, file=alias.name, hash="dddddddd")
    report = export(package, tmp_path / "exports")
    asset = Path(report.output)
    assert report.textures == 2
    assert report.texture_files == 3
    current = index.build_index("GIMI", str(asset.parent))
    loaded = loader.load_asset("GIMI", str(asset.parent), current["assets"][0], geometry=GeometryBlob())
    assert len(loaded.parts) == 4
    part = next(p for p in loaded.parts if p.component_name == "Surface02")
    assert part.textures["diffuse"].path == part.textures["normal_map"].path
    assert part.textures["diffuse"].key != part.textures["normal_map"].key
    last = next(p for p in loaded.parts if p.component_name == "Surface03")
    assert "dddddddd" in Path(last.textures["normal_map"].path).name
    assert digest(Path(last.textures["normal_map"].path)) == digest(Path(last.textures["diffuse"].path))
    for file in asset.rglob("hash.json"):
        for component in json.loads(file.read_text(encoding="utf-8"))["components"]:
            for group in component["texture_hashes"]:
                for _role, _ext, geometry_hash, filename in group:
                    assert geometry_hash in filename
                    assert (file.parent / filename).is_file()


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


@pytest.mark.parametrize("marker", ["mod-viewer", "mod-viewer-compact"])
def test_exported_payload_collisions_preserve_geometry_and_unknown_candidates(library, tmp_path, marker):
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
    for metadata_file in asset.rglob("hash.json"):
        document = json.loads(metadata_file.read_text(encoding="utf-8"))
        assert document["format"] == "mod-viewer"
        document["format"] = marker
        for component in document["components"]:
            component["geometry_hash"] = "dddddddd"
        metadata_file.write_text(json.dumps(document), encoding="utf-8")
    record = index.build_index("ZZMI", str(root))["assets"][0]
    loaded = loader.load_asset("ZZMI", str(root), record, geometry=GeometryBlob())
    assert len(loaded.parts) == 4
    assert {part.geometry_hash for part in loaded.parts} == {"aaaaaaaa"}
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


@pytest.mark.parametrize("limit", ["source_bytes", "file_bytes", "decoded_bytes"])
def test_capture_budget_rejects_oversized_input_before_binary_allocation(tmp_path, monkeypatch, limit):
    dump = make_capture(tmp_path)
    sizes = [p.stat().st_size for p in dump.iterdir()]
    maximum = sum(sizes) - 1 if limit == "source_bytes" else max(sizes) - 1 if limit == "file_bytes" else 1
    budget = CaptureBudget(**{"max_" + limit: maximum})
    allocated = []
    original = Inventory.read_bytes
    def read(inv, name, **kwargs):
        allocated.append(name)
        return original(inv, name, **kwargs)
    monkeypatch.setattr(Inventory, "read_bytes", read)
    with pytest.raises(ResourceLimit, match="budget"):
        analyze(dump, Context(budget=budget))
    assert allocated == []


def test_geometry_budget_counts_indices_and_repeated_decoding(tmp_path):
    dump = make_capture(tmp_path)
    analysis = analyze(dump)
    component, = analysis.components
    budget = CaptureBudget()
    inv = Inventory(dump, Context(budget=budget))
    mesh = decode_component(inv, component)
    assert len(mesh.vertices) == 4
    used = budget.decoded_bytes
    source_bytes = budget.source_bytes
    Inventory(dump, Context(budget=budget))
    assert budget.source_bytes == source_bytes
    budget.max_decoded_bytes = used * 2 - 1
    with pytest.raises(ResourceLimit, match="decoded geometry budget"):
        decode_component(inv, component)
    # A tiny IB with an invented draw count must fail before expanding indices.
    component.parts[0].count = 2**32
    with pytest.raises(ResourceLimit, match="decoded geometry budget"):
        decode_component(Inventory(dump), component)
    analysis._budget.max_decoded_bytes = analysis._budget.decoded_bytes + 1
    with pytest.raises(ResourceLimit, match="decoded geometry budget"):
        extract(analysis)


def test_capture_alias_lookup_detects_cycles_and_keeps_containment(tmp_path):
    dump = make_capture(tmp_path)
    inv = Inventory(dump)
    inv.targets.update({"alias-a": "alias-b", "alias-b": "alias-a"})
    with pytest.raises(ValueError, match="Cyclic capture resource alias"):
        inv.path("alias-a")
    inv.targets["alias-b"] = "../outside"
    with pytest.raises(ValueError, match="escapes"):
        inv.path("alias-a")
    inv.targets.update({f"alias-{i}": f"alias-{i + 1}" for i in range(1500)})
    inv.targets["alias-1500"] = "log.txt"
    assert inv.path("alias-0") == dump / "log.txt"


@pytest.mark.parametrize("bad_hash", ["", "invalid", "!", "abcd-invalid"])
def test_malformed_copy_rows_do_not_leak_into_later_copy_events(tmp_path, bad_hash):
    dump = make_capture(tmp_path)
    log = dump / "log.txt"
    with log.open("a", encoding="utf-8") as f:
        f.write(f"""000004 CopyResource()
       src: resource=0x10 hash={bad_hash}
       dst: resource=0x20 hash=22222222
000005 CopyResource()
       src: resource=0x30 hash=33333333
000006 Draw(VertexCount:4)
       dst: resource=0x40 hash=44444444
000007 CopyResource()
       src: resource=0x50 hash=55555555
       dst: resource=0x60 hash=66666666
""")
    calls = parse_log(Inventory(dump))
    copies = [c for c in calls if c.kind == "CopyResource"]
    assert len(copies) == 1
    assert copies[0].id == "000007"
    assert copies[0].source.hash == "55555555"
    assert copies[0].outputs[0].hash == "66666666"
    assert any(c.id == "000006" for c in calls)


def test_saved_report_paths_are_portable_but_api_paths_remain_absolute(tmp_path):
    dump = make_capture(tmp_path)
    analysis = analyze(dump)
    package = extract(analysis, Selection("Sample"))
    output = tmp_path / "exports"
    package.diagnostics.append(f"Source: {dump / 'missing.buf'}; output: {output}")
    package.diagnostics.append(str(FileNotFoundError(2, "Missing capture resource", str(dump / "missing.buf"))))
    report = export(package, output)
    assert report.output == str(output / "Sample")
    assert Path(report.report_path).is_absolute()
    assert analysis.dump == str(dump.resolve())
    assert str(dump) in report.diagnostics[-2]
    details = json.loads(Path(report.report_path).read_text(encoding="utf-8"))
    assert details["report"]["output"] == "Sample"
    assert details["report"]["report_path"] == "Sample.extraction_report.json"
    assert "dump" not in details["analysis"]
    def strings(value):
        if isinstance(value, dict):
            return [s for v in value.values() for s in strings(v)]
        if isinstance(value, list):
            return [s for v in value for s in strings(v)]
        return [value] if isinstance(value, str) else []
    assert all(str(tmp_path) not in s.replace("\\\\", "\\") and tmp_path.as_posix() not in s for s in strings(details))
    assert details["outputs"][0]["original_range"]["ib_file"] == analysis.components[0].parts[0].ib_file



def test_geometry_growth_after_preflight_cannot_bypass_decoded_budget(tmp_path, monkeypatch):
    dump = make_capture(tmp_path)
    component, = analyze(dump).components
    inv = Inventory(dump)
    original = inv.read_bytes
    def grow(name, **kwargs):
        path = inv.path(name)
        path.write_bytes(path.read_bytes() * 2)
        return original(name, **kwargs)
    monkeypatch.setattr(inv, "read_bytes", grow)
    with pytest.raises(ValueError, match="changed during decoding"):
        decode_component(inv, component)


@pytest.mark.parametrize("op,args,valid", [
    ("IASetVertexBuffers", "StartSlot:0, NumBuffers:1000000000", False),
    ("SOSetTargets", "NumBuffers:1000000000", False),
    ("CSSetUnorderedAccessViews", "StartSlot:0, NumUAVs:1000000000", False),
    ("CSSetShaderResources", "StartSlot:0, NumViews:1000000000", False),
    ("CSSetConstantBuffers", "StartSlot:0, NumBuffers:1000000000", False),
    ("PSSetShaderResources", "StartSlot:0, NumViews:1000000000", False),
    ("IASetVertexBuffers", "StartSlot:-1, NumBuffers:1", False),
    ("IASetVertexBuffers", "StartSlot:0, NumBuffers:-1", False),
    ("PSSetShaderResources", "StartSlot:128, NumViews:0", False),
    ("PSSetShaderResources", "StartSlot:127, NumViews:2", False),
    ("PSSetShaderResources", "StartSlot:0, NumViews:128", True),
    ("PSSetShaderResources", "StartSlot:127, NumViews:1", True),
    ("SOSetTargets", "NumBuffers:0", True),
])
def test_log_binding_ranges_reject_excessive_work(tmp_path, monkeypatch, op, args, valid):
    (tmp_path / "log.txt").write_text(
        f"000001 {op}({args})\n000002 Draw(VertexCount:0)\n", encoding="utf-8")
    # Fail promptly if a regression lets a malicious range reach iteration.
    def bounded_range(*values):
        result = range(*values)
        if len(result) > 128:
            pytest.fail("Unbounded capture binding loop")
        return result
    monkeypatch.setattr(capture, "range", bounded_range, raising=False)
    inv = Inventory(tmp_path)
    if valid:
        call, = parse_log(inv)
        assert call.id == "000002"
    else:
        with pytest.raises(Unsupported, match="D3D11 slots"):
            parse_log(inv)


@pytest.mark.parametrize("last_event", ["Draw", "Dispatch", "CopyResource"])
def test_log_events_share_a_budget_before_constructing_calls(tmp_path, monkeypatch, last_event):
    log = tmp_path / "log.txt"
    log.write_text("""000001 SOSetTargets(NumBuffers:1)
       0: resource=0x10 hash=11111111
000002 Draw()
000003 CSSetUnorderedAccessViews(StartSlot:0, NumUAVs:1)
       0: resource=0x20 hash=22222222
000004 Dispatch()
000005 CopyResource()
       src: resource=0x10 hash=11111111
       dst: resource=0x20 hash=22222222
""", encoding="utf-8")
    budget = CaptureBudget(max_events=3)
    calls = parse_log(Inventory(tmp_path, Context(budget=budget)))
    expected = ["Draw", "Dispatch", "CopyResource"]
    assert [call.kind for call in calls] == expected
    assert [call.sequence for call in calls] == [0, 1, 2]
    assert budget.events == 3
    with log.open("a", encoding="utf-8") as stream:
        stream.write(f"000006 {last_event}()\n")
        if last_event == "CopyResource":
            stream.write("       src: resource=0x10 hash=11111111\n"
                         "       dst: resource=0x20 hash=22222222\n")
    constructed = []
    original = capture.Call
    def call(*args, **kwargs):
        constructed.append(args[2])
        return original(*args, **kwargs)
    monkeypatch.setattr(capture, "Call", call)
    budget = CaptureBudget(max_events=3)
    with pytest.raises(ResourceLimit, match="log event budget"):
        parse_log(Inventory(tmp_path, Context(budget=budget)))
    assert constructed == expected
    assert budget.events == 3

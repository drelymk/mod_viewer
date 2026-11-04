"""Split ATTRIBUTE extraction retains native identity and captured geometry."""

import json
import struct
import shutil
from pathlib import Path

from PIL import Image
import pytest

from app.assets import index, loader
from app.assets.extractor.discovery import analyze
from app.assets.extractor.export import ExportOptions, Selection, export, extract
from app.assets.extractor.textures import digest
from app.assets.extractor.profiles.wwmi import material_roles
from core.geometry.mesh_builder import GeometryBlob
from core.geometry.component_coverage import ComponentCoverageKey
from tests.support.extraction_data import make_attribute_capture
from tests.support.dds_data import dx10_dds
from app.assets.wuwa_texture_names import texture_component_ordinals


@pytest.mark.parametrize("influences", [4, 8])
def test_attribute_skinning_round_trip_preserves_ranges_weights_and_texture_identity(tmp_path, influences, monkeypatch):
    dump = make_attribute_capture(tmp_path, influences)
    image = dump / "000001-ps-t0=66666666(77777777).png"
    Image.new("RGB", (16, 16), (80, 120, 160)).save(image)
    shutil.copyfile(image, dump / "000002-ps-t0=66666666.png")
    before = {p.name: digest(p) for p in dump.iterdir()}
    analysis = analyze(dump)
    component, = analysis.components
    assert component.default_included and not component.requires_review
    assert component.provenance["geometry_kind"] == "attribute_skinning"
    package = extract(analysis, Selection("Asset_001", renames={component.id: "ObjectA"}))
    assert len(package.meshes) == 2
    mesh = package.meshes[0]
    assert not package.partial and component.vertex_count == 4
    assert [(p.first, p.count, p.base_vertex) for p in component.parts] == [(0, 3, 0), (3, 3, 1)]
    weights = [i for i, e in enumerate(mesh.elements) if e.semantic == "BLENDWEIGHTS"]
    joints = [i for i, e in enumerate(mesh.elements) if e.semantic == "BLENDINDICES"]
    assert len(weights) == len(joints) == influences // 4
    assert [v for i in joints for v in mesh.vertices[1][i]] == list(range(influences))
    assert sum(v for i in weights for v in mesh.vertices[0][i]) == 0
    assert sum(v for i in weights for v in mesh.vertices[1][i]) == pytest.approx(1)
    def reject_hash_layout(_meshes):
        raise AssertionError("Native WWMI export must choose its own object folders")
    monkeypatch.setattr("app.assets.extractor.export._component_folders", reject_hash_layout)
    report = export(package, tmp_path / "exports")
    asset = Path(report.output)
    assert not (asset / "hash.json").exists()
    metadata = json.loads((asset / "11111111/Metadata.json").read_text(encoding="utf-8"))
    entry, second = metadata["components"]
    assert metadata["format"] == "mod-viewer"
    assert not (asset / "11111111/hash.json").exists()
    assert all(item["metadata"] == "11111111/Metadata.json" for item in json.loads(
        Path(report.report_path).read_text())["outputs"])
    assert entry["component_name"] == "ObjectA Component001"
    assert second["component_name"] == "ObjectA Component002"
    assert metadata["vb0_hash"] == "11111111" and entry["ib_hash"] == "55555555"
    assert [entry["index_offset"], second["index_offset"]] == [0, 3]
    assert all((asset / "11111111" / f"Component {i}.{kind}").is_file()
               for i in range(2) for kind in ("fmt", "vb", "ib"))
    assert struct.unpack("<3I", (asset / "11111111/Component 0.ib").read_bytes()) == (0, 1, 2)
    assert struct.unpack("<3I", (asset / "11111111/Component 1.ib").read_bytes()) == (0, 1, 2)
    usage = json.loads((asset / "11111111/TextureUsage.json").read_text())
    assert usage == {f"Component {i}": {"ps-t0": [
        "66666666-vs=aaaaaaaaaaaaaaaa-ps=bbbbbbbbbbbbbbbb"]} for i in range(2)}
    from core.geometry.migoto_format import parse_fmt
    from app.assets.extractor.geometry import codec
    layout = parse_fmt(asset / "11111111/Component 0.fmt")
    data = (asset / "11111111/Component 0.vb").read_bytes()
    for local, source in enumerate((0, 1, 2)):
        for element, values in zip(mesh.elements, mesh.vertices[source]):
            assert codec(element.format)[1](data, local * layout.stride + element.offset) == pytest.approx(values)
    current = index.build_index("WWMI", str(asset.parent))
    record, = current["assets"]
    assert index.lookup_geometry(current, "11111111") and not index.lookup_geometry(current, "55555555")
    loaded = loader.load_asset("WWMI", str(asset.parent), record, geometry=GeometryBlob())
    assert not loaded.payload["metadata"].get("warnings") and len(loaded.parts) == 2
    first = loaded.parts[0]
    assert first.geometry_hash == "11111111"
    assert list(struct.iter_unpack("<3f", first.positions)) == [(0, 0, 0), (2, 0, 0), (1, 1, 0)]
    assert list(struct.iter_unpack("<3f", first.normals)) == [(0, 0, 1)] * 3
    candidate, = first.texture_candidates
    assert candidate.role is None and digest(Path(candidate.path)) == digest(image)
    assert entry["texture_hashes"][0][0][2:] == ["66666666", "Components-0-1 t=66666666.png"]
    assert len(list((asset / "11111111").glob("*.png"))) == 1
    selected = loader.load_asset("WWMI", str(asset.parent), record, geometry=GeometryBlob(),
                                 part_filter=[ComponentCoverageKey("11111111", 3, 3)])
    assert len(selected.parts) == 1 and selected.parts[0].first_index == 3
    assert {p.name: digest(p) for p in dump.iterdir()} == before
    # Separate generated components remain independently editable even when
    # their format, vertex, and index payloads are identical.
    first_mesh, second_mesh = package.meshes
    second_mesh.vertices = first_mesh.vertices
    second_mesh.component.parts[0].base_vertex = first_mesh.component.parts[0].base_vertex
    second_mesh.indices = first_mesh.indices
    independent = Path(export(package, tmp_path / "independent").output) / "11111111"
    for kind in ("fmt", "vb", "ib"):
        first_file = independent / f"Component 0.{kind}"
        second_file = independent / f"Component 1.{kind}"
        original = second_file.read_bytes()
        assert first_file.read_bytes() == original
        assert not first_file.samefile(second_file)
        first_file.write_bytes(b"edited")
        assert second_file.read_bytes() == original


@pytest.mark.parametrize("extension", [".dds", ".png", ".jpg", ".jpeg"])
def test_native_texture_names_preserve_membership_collisions_roles_and_encoding(tmp_path, extension):
    dump = make_attribute_capture(tmp_path)
    for ordinal, call in enumerate(("000001", "000002")):
        path = dump / f"{call}-ps-t0=66666666{extension}"
        if extension == ".dds":
            path.write_bytes(dx10_dds(bytes([ordinal * 80, 40, 80, 255]) * 16,
                                     dxgi_format=28, width=4, height=4))
        else:
            Image.new("RGB", (16, 16), (80 + ordinal * 80, 120, 160)).save(path)
    package = extract(analyze(dump), Selection("Asset_001"))
    shared = dump / ("shared" + extension)
    shutil.copyfile(dump / f"000001-ps-t0=66666666{extension}", shared)
    for ordinal, mesh in enumerate(package.meshes):
        texture, = mesh.component.parts[0].textures
        texture["role"] = "Diffuse" if ordinal == 0 else "NormalMap"
        texture["auxiliary"] = False
        mesh.component.parts[0].textures.append({**texture, "file": shared.name,
                                               "hash": "77777777", "sha256": digest(shared),
                                               "role": "TextureSlot09"})
    report = export(package, tmp_path / "exports", options=ExportOptions(
        layout="compatible" if extension == ".jpeg" else "compact"))
    asset = Path(report.output) / "11111111"
    metadata = json.loads((asset / "Metadata.json").read_text())
    assert report.texture_files == 3
    shared_name = f"Components-0-1 t=77777777{extension}"
    assert digest(asset / shared_name) == digest(shared)
    for ordinal, component in enumerate(metadata["components"]):
        own, common = component["texture_hashes"][0]
        source = dump / f"{ordinal + 1:06}-ps-t0=66666666{extension}"
        assert own[3] == f"Components-{ordinal} t=66666666-{digest(source)[:12]}{extension}"
        assert common[3] == shared_name
        assert texture_component_ordinals(own[3]) == {ordinal}
        assert texture_component_ordinals(shared_name) == {0, 1}
        assert digest(asset / own[3]) == digest(source)
    root = str(Path(report.output).parent)
    loaded = loader.load_asset("WWMI", root, index.build_index("WWMI", root)["assets"][0], geometry=GeometryBlob())
    for ordinal, part in enumerate(loaded.parts):
        row = metadata["components"][ordinal]["texture_hashes"][0]
        assert {Path(item.path).name for item in part.texture_candidates} == {item[3] for item in row}
        role = "diffuse" if ordinal == 0 else "normal_data"
        assert Path(part.textures[role].path).name == row[0][3]
        assert part.textures[role].role == role


def test_multiple_object_folders_are_self_contained_and_remain_one_asset(tmp_path):
    dump = make_attribute_capture(tmp_path)
    Image.new("RGB", (16, 16), (80, 120, 160)).save(dump / "000001-ps-t0=66666666.png")
    for path in list(dump.iterdir()):
        if path.name.startswith(("000001-", "000002-")):
            name = path.name.replace("000001-", "000003-").replace("000002-", "000004-")
            shutil.copyfile(path, dump / name.replace("11111111", "88888888"))
    log = dump / "log.txt"
    text = log.read_text()
    log.write_text(text + text.replace("000001", "000003").replace("000002", "000004").replace(
        "resource=0x10 hash=11111111", "resource=0x70 hash=88888888"))
    analysis = analyze(dump)
    assert len(analysis.components) == 2
    package = extract(analysis, Selection("Asset_001", renames={
        c.id: "ObjectA" if c.streams["position"]["hash"] == "11111111" else "ObjectB"
        for c in analysis.components}))
    report = export(package, tmp_path / "exports")
    asset = Path(report.output)
    assert report.components == report.parts == 4
    assert not list(asset.glob("*.*"))
    for folder in ("11111111", "88888888"):
        assert len(list((asset / folder).glob("Component *.vb"))) == 2
        document = json.loads((asset / folder / "Metadata.json").read_text())
        assert len(document["components"]) == 2
        relative = document["components"][0]["texture_hashes"][0][0][3]
        assert Path(relative).parent == Path(".")
        assert (asset / folder / relative).is_file()
    first_image, = (asset / "11111111").glob("*.png")
    second_image, = (asset / "88888888").glob("*.png")
    assert digest(first_image) == digest(second_image) and not first_image.samefile(second_image)
    assert not next((asset / "11111111").glob("Component *.vb")).samefile(
        next((asset / "88888888").glob("Component *.vb")))
    current = index.build_index("WWMI", str(asset.parent))
    record, = current["assets"]
    assert {g["hash"] for g in record["geometry"]} == {"11111111", "88888888"}
    loaded = loader.load_asset("WWMI", str(asset.parent), record, geometry=GeometryBlob())
    assert len(loaded.parts) == 4 and len({p.component_name for p in loaded.parts}) == 4
    assert len({t.path for p in loaded.parts for t in p.texture_candidates}) == 2
    # A copied object can load without the parent or its sibling's files.
    detached_root = tmp_path / "detached"
    detached = detached_root / "Asset_003"
    shutil.copytree(asset / "11111111", detached)
    detached_record, = index.build_index("WWMI", str(detached_root))["assets"]
    copied = loader.load_asset("WWMI", str(detached_root), detached_record, geometry=GeometryBlob())
    assert len(copied.parts) == 2 and copied.parts[0].texture_candidates
    assert all(Path(t.path).parent == detached for p in copied.parts for t in p.texture_candidates)


@pytest.mark.parametrize("case", ["named_folder", "nested_folder", "missing_hash", "shared_hash_filename"])
def test_native_object_discovery_requires_hash_folder_and_native_fields(tmp_path, case):
    root = tmp_path / "assets"
    asset = root / "Asset_001"
    folder = {"named_folder": "ObjectA", "nested_folder": "Nested/11111111", "missing_hash": "11111111", "shared_hash_filename": "11111111"}[case]
    child = asset / folder
    child.mkdir(parents=True)
    document = {"format": "mod-viewer", "components": [{"index_offset": 0, "index_count": 3}]}
    if case != "missing_hash":
        document["vb0_hash"] = "11111111"
    (child / ("hash.json" if case == "shared_hash_filename" else "Metadata.json")).write_text(json.dumps(document))
    with pytest.raises(index.NoValidAssetsError):
        index.build_index("WWMI", str(root))


@pytest.mark.parametrize("case,expected", [
    ("main", 3), ("main_with_shifted_face", 3), ("small_color_mask", 3),
    ("simple", 1), ("smaller_atlas", 2), ("hair", 1),
    ("hair_uncompressed", 1), ("hair_half_vector", 1),
    ("hair_quarter_vector", None), ("hair_larger_vector", None),
    ("eye", 2), ("eye_unbound", None), ("eye_wrong_size", None),
    ("face", 1), ("face_unbound", None), ("face_distinct_data", None),
    ("face_separate_resource", None), ("face_missing_data", None),
    ("face_wrong_size", None), ("face_contaminated", None),
    ("face_no_color", None), ("face_no_shader", None),
    ("face_late_data", 1), ("face_larger_scale", 1), ("face_shifted", 5),
    ("face_ambiguous", None), ("face_nonsquare", None), ("face_non_power_scale", None),
    ("face_reordered", None), ("face_data_gap", None), ("face_oversized_data", None),
    ("face_bc1", 1), ("face_bc1_equal_atlas", 1),
    ("layered", 3), ("layered_inherited", None), ("layered_distinct_mask", None),
    ("layered_separate_resource", None), ("layered_wrong_scale", None),
    ("layered_unbound", None), ("layered_contaminated", None),
    ("layered_missing_mask", None), ("layered_nonsquare", None),
    ("layered_no_color", None), ("layered_no_shader", None),
    ("no_color", None), ("no_shader", None), ("unbound", None), ("contaminated", None),
    ("wrong_size", None), ("wrong_mask", None),
])
def test_wwmi_diffuse_requires_material_family_evidence(case, expected):
    formats = ["BC7_UNORM", "BC3_UNORM", "R8_UNORM", "BC7_UNORM_SRGB"]
    sizes = [(512, 512)] * 4
    if case == "main_with_shifted_face":
        formats.extend(["BC3_UNORM", "BC7_UNORM_SRGB", "B8G8R8A8_UNORM", "B8G8R8A8_UNORM"])
        sizes.extend([(128, 128), (512, 512), (256, 256), (256, 256)])
    elif case == "small_color_mask":
        formats[2] = "BC7_UNORM_SRGB"
        sizes[2] = (128, 128)
    elif case == "simple":
        formats = ["BC7_UNORM", "BC7_UNORM_SRGB"]
    elif case == "smaller_atlas":
        formats = ["BC3_UNORM", "R8_UNORM", "BC7_UNORM_SRGB"]
        sizes[2] = (256, 256)
    elif case.startswith("hair"):
        formats = ["BC3_UNORM", "BC7_UNORM_SRGB", "B8G8R8A8_UNORM", "BC1_UNORM", "R8_UNORM", "BC7_UNORM"]
        sizes = [(256, 256), (512, 512), (128, 128), (256, 128), (128, 128), (512, 512)]
        if case == "hair_uncompressed":
            formats[3] = "B8G8R8A8_UNORM"
        elif case == "hair_half_vector":
            sizes[5] = (256, 256)
        elif case == "hair_quarter_vector":
            sizes[5] = (128, 128)
        elif case == "hair_larger_vector":
            sizes[5] = (1024, 1024)
    elif case.startswith("layered"):
        formats = ["B8G8R8A8_UNORM_SRGB", "BC1_UNORM", "BC3_UNORM", "BC7_UNORM_SRGB",
                   "BC3_UNORM", "BC7_UNORM_SRGB", "BC7_UNORM_SRGB", "BC3_UNORM"]
        sizes = [(1, 1), (512, 512), (2048, 2048), (2048, 2048),
                 (1024, 1024), (1024, 1024), (128, 128), (1024, 1024)]
    elif case.startswith("eye"):
        formats = ["BC1_UNORM", "BC3_UNORM_SRGB", "BC7_UNORM_SRGB"]
        sizes = [(256, 256), (256, 256), (512, 512)]
    elif case.startswith("face"):
        formats = ["BC3_UNORM", "BC7_UNORM_SRGB", "B8G8R8A8_UNORM", "B8G8R8A8_UNORM"]
        sizes = [(128, 128), (512, 512), (256, 256), (256, 256)]
        if case == "face_late_data":
            formats[2:2] = ["BC7_UNORM_SRGB", "R8_UNORM", "R8_UNORM"]
            sizes = [(128, 128), (256, 256), (256, 256), (128, 128),
                     (128, 128), (256, 256), (256, 256)]
        elif case == "face_larger_scale":
            sizes = [(128, 128), (1024, 1024), (512, 512), (512, 512)]
        elif case.startswith("face_bc1"):
            formats[0] = "BC1_UNORM"
            if case == "face_bc1_equal_atlas":
                sizes[1] = (256, 256)
    textures = [dict(slot=i, hash=f"{i + 1:08x}", resource={"hash": f"{i + 1:08x}"},
                     format=fmt, width=sizes[i][0], height=sizes[i][1]) for i, fmt in enumerate(formats)]
    if case == "main_with_shifted_face":
        textures[7].update(hash=textures[6]["hash"], resource=dict(textures[6]["resource"]))
        # Both signatures match; retain the established primary-family priority.
        assert material_roles(textures[4:], True, "a" * 16) == {5: "Diffuse"}
    if case.startswith("face"):
        data_slot = next(i for i, fmt in enumerate(formats) if fmt == "B8G8R8A8_UNORM")
        if case != "face_distinct_data":
            textures[data_slot + 1].update(hash=textures[data_slot]["hash"],
                                          resource=dict(textures[data_slot]["resource"]))
    if case.startswith("layered"):
        textures[7].update(hash=textures[4]["hash"], resource=dict(textures[4]["resource"]))
        if case == "layered_inherited":
            textures[0].update(format="BC7_UNORM_SRGB", width=512, height=512)
        elif case == "layered_distinct_mask":
            textures[7].update(hash="00000009", resource={"hash": "00000009"})
        elif case == "layered_separate_resource":
            textures[7]["resource"]["address"] = "0x90"
        elif case == "layered_wrong_scale":
            for slot in (4, 5, 7):
                textures[slot].update(width=768, height=768)
        elif case == "layered_unbound":
            textures[3]["resource"] = None
        elif case == "layered_contaminated":
            textures[4]["contaminated"] = True
        elif case == "layered_missing_mask":
            textures.pop()
        elif case == "layered_nonsquare":
            textures[3]["height"] = 1024
    if case == "unbound":
        textures[3]["resource"] = None
    elif case == "contaminated":
        textures[3]["contaminated"] = True
    elif case == "wrong_size":
        textures[3]["width"] = 256
    elif case == "wrong_mask":
        textures[2]["format"] = "R16_FLOAT"
    elif case == "eye_unbound":
        textures[2]["resource"] = None
    elif case == "eye_wrong_size":
        textures[2]["width"] = textures[2]["height"] = 256
    elif case == "face_unbound":
        textures[1]["resource"] = None
    elif case == "face_wrong_size":
        textures[2]["width"] = textures[2]["height"] = 128
    elif case == "face_contaminated":
        textures[2]["contaminated"] = True
    elif case == "face_separate_resource":
        textures[2]["resource"]["address"] = "0x60"
        textures[3]["resource"]["address"] = "0x70"
    elif case == "face_missing_data":
        textures.pop()
    elif case == "face_shifted":
        for t in textures:
            t["slot"] += 4
    elif case == "face_ambiguous":
        extra = [dict(t, slot=t["slot"] + 4) for t in textures]
        extra[1].update(hash="00000009", resource={"hash": "00000009"})
        textures.extend(extra)
    elif case == "face_nonsquare":
        textures[1]["height"] = 256
    elif case == "face_non_power_scale":
        for t in textures[2:]:
            t.update(width=192, height=192)
    elif case == "face_reordered":
        textures[0]["slot"], textures[1]["slot"] = 1, 0
    elif case == "face_data_gap":
        textures[3]["slot"] = 4
    elif case == "face_oversized_data":
        for t in textures[2:]:
            t.update(width=1024, height=1024)
    assert material_roles(textures, not case.endswith("no_color"),
                          "" if case.endswith("no_shader") else "a" * 16) == (
        {expected: "Diffuse"} if expected is not None else {})


_ADDITIONAL_FAMILIES = ("compact_main", "color_led_hair", "layered_eye", "masked_eye",
                        "unlit_constant", "unlit_alias", "face_equal_mask_lookup")


def _additional_material_family(family):
    aliases = {}
    if family == "compact_main":
        textures = [("BC7_UNORM", 512, 512), ("BC1_UNORM", 256, 256),
                    ("BC7_UNORM_SRGB", 512, 512), ("BC7_UNORM_SRGB", 128, 128)]
        slot = 2
    elif family == "color_led_hair":
        textures = [("BC7_UNORM_SRGB", 512, 512), ("BC7_UNORM", 512, 512),
                    ("B8G8R8A8_UNORM_SRGB", 128, 8)]
        slot = 0
    elif family in ("layered_eye", "masked_eye"):
        textures = [("BC1_UNORM", 256, 256), ("BC3_UNORM_SRGB", 256, 256),
                    ("BC7_UNORM_SRGB", 1024, 1024), ("B8G8R8A8_UNORM", 512, 512)]
        if family == "masked_eye":
            textures[:2] = [("BC3_UNORM_SRGB", 256, 256), ("BC1_UNORM_SRGB", 128, 128)]
        slot = 2
    elif family == "face_equal_mask_lookup":
        textures = [("BC1_UNORM", 256, 256), ("BC7_UNORM_SRGB", 512, 512),
                    ("B8G8R8A8_UNORM", 256, 256), ("B8G8R8A8_UNORM", 256, 256),
                    ("B8G8R8A8_UNORM_SRGB", 128, 8)]
        aliases, slot = {3: 2}, 1
    else:
        textures = [None, ("B8G8R8A8_UNORM_SRGB", 1, 1), ("BC7_UNORM_SRGB", 1024, 1024),
                    ("BC7_UNORM_SRGB", 128, 128), ("BC7_UNORM_SRGB", 256, 256),
                    ("BC7_UNORM_SRGB", 64, 64), ("BC7_UNORM_SRGB", 64, 64)]
        aliases = {6: 5}
        slot = 2
        if family == "unlit_alias":
            textures[1:3] = [textures[2], textures[3]]
            aliases[3] = 2
            slot = 1
    return textures, aliases, slot


@pytest.mark.parametrize("family", _ADDITIONAL_FAMILIES)
@pytest.mark.parametrize("change", ["none", "unbound", "hash_mismatch", "contaminated", "no_color", "no_shader",
                                   "nonsquare", "neighbor_format", "missing_neighbor"])
def test_additional_wwmi_families_require_complete_bound_evidence(family, change):
    specs, aliases, slot = _additional_material_family(family)
    textures = []
    for ordinal, spec in enumerate(specs):
        if spec is None:
            continue
        source = aliases.get(ordinal, ordinal)
        fmt, width, height = spec
        textures.append(dict(slot=ordinal, hash=f"{source + 1:08x}",
                             resource={"hash": f"{source + 1:08x}", "address": f"0x{source + 1:x}0"},
                             format=fmt, width=width, height=height))
    by_slot = {t["slot"]: t for t in textures}
    neighbor = (2 if family == "color_led_hair" else 6 if family.startswith("unlit")
                else 4 if family == "face_equal_mask_lookup" else 3)
    if change == "unbound":
        by_slot[slot]["resource"] = None
    elif change == "hash_mismatch":
        by_slot[neighbor]["resource"] = {"hash": "ffffffff"}
    elif change == "contaminated":
        by_slot[neighbor]["contaminated"] = True
    elif change == "nonsquare":
        by_slot[slot]["height"] //= 2
    elif change == "neighbor_format":
        by_slot[neighbor]["format"] = "R16_FLOAT"
    elif change == "missing_neighbor":
        textures.remove(by_slot[neighbor])
    assert material_roles(textures, change != "no_color", "" if change == "no_shader" else "a" * 16) == (
        {slot: "Diffuse"} if change == "none" else {})


@pytest.mark.parametrize("family,change", [
    ("compact_main", "mask_scale"), ("compact_main", "color_scale"),
    ("color_led_hair", "vector_scale"), ("color_led_hair", "square_lookup"),
    ("color_led_hair", "tall_lookup"), ("color_led_hair", "narrow_lookup"),
    ("layered_eye", "packed_scale"), ("layered_eye", "mask_scale"),
    ("masked_eye", "packed_scale"), ("masked_eye", "mask_scale"),
    ("unlit_constant", "constant_size"), ("unlit_constant", "repeat_hash"),
    ("unlit_alias", "alias_resource"), ("unlit_alias", "alias_size"),
    ("unlit_alias", "repeat_size"), ("unlit_alias", "mask_scale"),
    ("unlit_constant", "non_power_scale"), ("unlit_constant", "extra_prefix"),
    ("unlit_constant", "repeat_separate_resource"), ("unlit_alias", "repeat_separate_resource"),
    ("unlit_alias", "alias_version"),
    ("face_equal_mask_lookup", "mask_scale"), ("face_equal_mask_lookup", "atlas_scale"),
    ("face_equal_mask_lookup", "packed_mismatch"), ("face_equal_mask_lookup", "alias_resource"),
    ("face_equal_mask_lookup", "alias_version"), ("face_equal_mask_lookup", "square_lookup"),
])
def test_additional_wwmi_families_validate_neighbor_identity_and_dimensions(family, change):
    specs, aliases, slot = _additional_material_family(family)
    textures = {i: dict(slot=i, hash=f"{aliases.get(i, i) + 1:08x}",
                       resource={"hash": f"{aliases.get(i, i) + 1:08x}"},
                       format=spec[0], width=spec[1], height=spec[2])
                for i, spec in enumerate(specs) if spec is not None}
    if change in ("mask_scale", "color_scale", "vector_scale", "packed_scale", "alias_size", "repeat_size"):
        target = ({"compact_main": 1, "layered_eye": 0, "masked_eye": 1,
                   "face_equal_mask_lookup": 0}.get(family, 4)
                  if change == "mask_scale" else {"color_scale": 3, "vector_scale": 1,
                      "packed_scale": 3, "alias_size": 2, "repeat_size": 6}[change])
        textures[target].update(width=textures[target]["width"] * 2, height=textures[target]["height"] * 2)
    elif change in ("square_lookup", "tall_lookup", "narrow_lookup"):
        textures[4 if family == "face_equal_mask_lookup" else 2].update(
            width=128, height={"square_lookup": 128, "tall_lookup": 256, "narrow_lookup": 64}[change])
    elif change == "atlas_scale":
        textures[1].update(width=1024, height=1024)
    elif change == "packed_mismatch":
        textures[3].update(width=512, height=512)
    elif change == "constant_size":
        textures[1].update(width=4, height=4)
    elif change == "repeat_hash":
        textures[6].update(hash="00000009", resource={"hash": "00000009"})
    elif change == "alias_resource":
        textures[2]["resource"] = dict(textures[2]["resource"], address="0x90")
    elif change == "alias_version":
        textures[2]["resource"] = dict(textures[2]["resource"], version=1)
    elif change == "repeat_separate_resource":
        textures[6]["resource"] = dict(textures[6]["resource"], address="0x90")
    elif change == "non_power_scale":
        textures[4].update(width=192, height=192)
    elif change == "extra_prefix":
        textures[0] = dict(textures[2], slot=0)
    assert material_roles(list(textures.values()), True, "a" * 16) == (
        {slot: "Diffuse"} if change == "repeat_separate_resource" else {})


def _material_capture(dump, call_id, textures, aliases=None):
    codes = {"BC7_UNORM": 98, "BC7_UNORM_SRGB": 99, "BC3_UNORM": 77,
             "BC3_UNORM_SRGB": 78, "BC1_UNORM": 71, "BC1_UNORM_SRGB": 72, "R8_UNORM": 61,
             "B8G8R8A8_UNORM": 87, "B8G8R8A8_UNORM_SRGB": 91}
    bindings, dump_lines = [], []
    for slot, spec in enumerate(textures):
        if spec is None:
            continue
        fmt, width, height = spec
        source_slot = (aliases or {}).get(slot, slot)
        h = f"{0x10000000 + int(call_id) * 16 + source_slot:08x}"
        name = f"{call_id}-ps-t{slot}={h}.dds"
        size = width * height * (4 if fmt.startswith("B8G8R8A8_") else 1)
        if fmt in ("BC1_UNORM", "BC1_UNORM_SRGB"):
            size //= 2
        (dump / name).write_bytes(dx10_dds(bytes(size), dxgi_format=codes[fmt], width=width, height=height))
        bindings.append(f"       {slot}: resource=0x{source_slot + 6:x}0 hash={h}\n")
        dump_lines.append(f"{call_id} 3DMigoto Dumping capture\\{name} -> deduped\\{h}-{fmt}.dds\n")
    return (f"{call_id} PSSetShader(pPixelShader:0xa0) hash=cccccccccccccccc\n"
            + f"{call_id} PSSetShaderResources(StartSlot:0, NumViews:{len(textures)})\n"
            + "".join(bindings) + "".join(dump_lines))


@pytest.mark.parametrize("family", ["main", "eye", "face", "face_late_data", "face_bc1", "hair_half_vector", "layered", *_ADDITIONAL_FAMILIES])
def test_wwmi_diffuse_signature_survives_export_and_loading_without_pixel_guessing(tmp_path, family):
    dump = make_attribute_capture(tmp_path)
    log = dump / "log.txt"
    text = log.read_text()
    text = "000001 3DMigoto [ShaderRegex\\WWMIv1\\EnableTextureOverrides]\n" + text
    textures = [(fmt, 128, 128) for fmt in ("BC7_UNORM", "BC3_UNORM", "R8_UNORM", "BC7_UNORM_SRGB")]
    if family == "eye":
        textures = [("BC1_UNORM", 128, 128), ("BC3_UNORM_SRGB", 128, 128), ("BC7_UNORM_SRGB", 256, 256)]
    elif family.startswith("face"):
        textures = [("BC3_UNORM", 128, 128), ("BC7_UNORM_SRGB", 512, 512),
                    ("B8G8R8A8_UNORM", 256, 256), ("B8G8R8A8_UNORM", 256, 256)]
        if family == "face_late_data":
            textures = [("BC3_UNORM", 128, 128), ("BC7_UNORM_SRGB", 256, 256),
                        ("BC7_UNORM_SRGB", 256, 256), ("R8_UNORM", 128, 128),
                        ("R8_UNORM", 128, 128), ("B8G8R8A8_UNORM", 256, 256),
                        ("B8G8R8A8_UNORM", 256, 256)]
        elif family == "face_bc1":
            textures[0] = ("BC1_UNORM", 128, 128)
            textures[1] = ("BC7_UNORM_SRGB", 256, 256)
    elif family == "hair_half_vector":
        textures = [("BC3_UNORM", 128, 128), ("BC7_UNORM_SRGB", 512, 512),
                    ("B8G8R8A8_UNORM", 128, 128), ("B8G8R8A8_UNORM", 256, 128),
                    ("R8_UNORM", 128, 128), ("BC7_UNORM", 256, 256)]
    elif family == "layered":
        textures = [("B8G8R8A8_UNORM_SRGB", 1, 1), ("BC1_UNORM", 512, 512),
                    ("BC3_UNORM", 2048, 2048), ("BC7_UNORM_SRGB", 2048, 2048),
                    ("BC3_UNORM", 1024, 1024), ("BC7_UNORM_SRGB", 1024, 1024),
                    ("BC7_UNORM_SRGB", 128, 128), ("BC3_UNORM", 1024, 1024)]
    aliases = ({3: 2} if family in ("face", "face_bc1") else {6: 5} if family == "face_late_data"
               else {7: 4} if family == "layered" else None)
    if family in _ADDITIONAL_FAMILIES:
        textures, aliases, _ = _additional_material_family(family)
    text = text.replace("000001 PSSetShaderResources(StartSlot:0, NumViews:1)\n       0: resource=0x60 hash=66666666\n",
                        _material_capture(dump, "000001", textures, aliases))
    log.write_text(text)
    Image.new("RGB", (16, 16)).save(dump / "000001-o0=aaaaaaaa.png")
    analysis = analyze(dump)
    diffuse, = [t for t in analysis.components[0].parts[0].textures if t["role"] == "Diffuse"]
    assert diffuse["role_confidence"] == "inferred_wwmi_pass_format_bindings"
    expected_slot = (_additional_material_family(family)[2] if family in _ADDITIONAL_FAMILIES else
                     {"main": 3, "eye": 2, "face": 1, "face_late_data": 1,
                      "face_bc1": 1, "hair_half_vector": 1, "layered": 3}[family])
    assert diffuse["slot"] == expected_slot
    if family == "eye":
        assert analysis.components[0].parts[0].textures[1]["role"].startswith("TextureSlot")
    elif family == "face_late_data":
        assert all(t["role"].startswith("TextureSlot")
                   for t in analysis.components[0].parts[0].textures if t["slot"] != 1)
    report = export(extract(analysis, Selection("Asset_001")), tmp_path / "exports")
    root = str(Path(report.output).parent)
    loaded = loader.load_asset("WWMI", root, index.build_index("WWMI", root)["assets"][0], geometry=GeometryBlob())
    assert digest(Path(loaded.parts[0].textures["diffuse"].path)) == diffuse["sha256"]


@pytest.mark.parametrize("competing_family,target_family", [
    ("simple", "hair"), ("main", "hair"), ("unrecognized", "hair_half_vector"),
    ("unrecognized", "face_bc1"), ("unrecognized", "layered"),
    ("layered_eye", "color_led_hair"),
    ("color_led_hair", "face_equal_mask_lookup"),
    *[("unrecognized", family) for family in _ADDITIONAL_FAMILIES],
])
def test_complete_material_pass_outweighs_less_specific_pass_with_more_resources(tmp_path, competing_family, target_family):
    dump = make_attribute_capture(tmp_path)
    log = dump / "log.txt"
    text = "000001 3DMigoto [ShaderRegex\\WWMIv1\\EnableTextureOverrides]\n" + log.read_text()
    textures = [("BC7_UNORM", 256, 256), ("BC7_UNORM_SRGB", 256, 256)]
    if competing_family == "main":
        textures = [(fmt, 256, 256) for fmt in ("BC7_UNORM", "BC3_UNORM", "R8_UNORM", "BC7_UNORM_SRGB")]
    elif competing_family == "unrecognized":
        textures = [("BC3_UNORM_SRGB", 256, 256), ("BC7_UNORM_SRGB", 256, 256)]
    elif competing_family in _ADDITIONAL_FAMILIES:
        textures, _, _ = _additional_material_family(competing_family)
    competing = _material_capture(dump, "000001", textures)
    for slot in range(6, 10):
        Image.new("RGB", (16, 16)).save(dump / f"000001-ps-t{slot}={slot:08x}.png")
    target_textures = [
        ("BC3_UNORM", 128, 128), ("BC7_UNORM_SRGB", 256, 256),
        ("B8G8R8A8_UNORM", 128, 128), ("B8G8R8A8_UNORM", 256, 128),
        ("R8_UNORM", 128, 128), ("BC7_UNORM", 256, 256),
        ("B8G8R8A8_UNORM", 256, 256), ("B8G8R8A8_UNORM", 256, 256),
    ]
    aliases, expected_slot = {7: 6}, 1
    if target_family == "hair_half_vector":
        target_textures[1] = ("BC7_UNORM_SRGB", 512, 512)
    elif target_family == "face_bc1":
        target_textures = [("BC1_UNORM", 128, 128), ("BC7_UNORM_SRGB", 256, 256),
                           ("B8G8R8A8_UNORM", 256, 256), ("B8G8R8A8_UNORM", 256, 256)]
        aliases = {3: 2}
    elif target_family == "layered":
        target_textures = [("B8G8R8A8_UNORM_SRGB", 1, 1), ("BC1_UNORM", 512, 512),
                           ("BC3_UNORM", 2048, 2048), ("BC7_UNORM_SRGB", 2048, 2048),
                           ("BC3_UNORM", 1024, 1024), ("BC7_UNORM_SRGB", 1024, 1024),
                           ("BC7_UNORM_SRGB", 128, 128), ("BC3_UNORM", 1024, 1024)]
        aliases, expected_slot = {7: 4}, 3
    elif target_family in _ADDITIONAL_FAMILIES:
        target_textures, aliases, expected_slot = _additional_material_family(target_family)
    target = _material_capture(dump, "000002", target_textures, aliases)
    # Add enough unrelated snapshots for the competing pass to win by slot count.
    Image.new("RGB", (16, 16)).save(dump / "000001-ps-t10=aaaaaaaa.png")
    text = text.replace("000001 PSSetShaderResources(StartSlot:0, NumViews:1)\n       0: resource=0x60 hash=66666666\n", competing)
    text = text.replace("000002 DrawIndexed(IndexCount:3, StartIndexLocation:3, BaseVertexLocation:1)",
                        target + "000002 DrawIndexed(IndexCount:3, StartIndexLocation:0, BaseVertexLocation:0)")
    log.write_text(text)
    second_ib = dump / "000002-ib=55555555.txt"
    second_ib.write_text(second_ib.read_text().replace("first index: 3", "first index: 0"))
    for call in ("000001", "000002"):
        Image.new("RGB", (16, 16)).save(dump / f"{call}-o0=aaaaaaaa.png")
    component, = analyze(dump).components
    part, = component.parts
    assert part.draws == ["000001", "000002"]
    diffuse, = [t for t in part.textures if t["role"] == "Diffuse"]
    assert diffuse["draw"] == "000002" and diffuse["slot"] == expected_slot


@pytest.mark.parametrize("case", ["normal", "duplicate", "stride", "first", "count", "truncated", "weights",
                                "alias_slot", "alias_index_format", "alias_weight_format",
                                "alias_index_offset", "alias_weight_offset", "alias_step", "alias_missing"])
def test_attribute_skinning_rejects_unproven_or_incomplete_layouts(tmp_path, case):
    dump = make_attribute_capture(tmp_path, 4 if case.startswith("alias_") else 8)
    for call in ("000001", "000002"):
        normal = dump / f"{call}-vb1=22222222.txt"
        uv = dump / f"{call}-vb2=33333333.txt"
        if case == "normal":
            normal.write_text(normal.read_text().replace("SemanticIndex: 2", "SemanticIndex: 20"))
        elif case == "duplicate":
            normal.write_text(normal.read_text().replace("SemanticIndex: 2", "SemanticIndex: 1"))
        elif case == "stride":
            normal.write_text(normal.read_text().replace("stride: 8", "stride: 12"))
        elif case == "first":
            uv.write_text(uv.read_text().replace("first vertex: 0", "first vertex: 1"))
        elif case == "count":
            uv.write_text(uv.read_text().replace("vertex count: 3", "vertex count: 5"))
        elif case == "truncated":
            uv.with_suffix(".buf").write_bytes(uv.with_suffix(".buf").read_bytes()[:-1])
        elif case == "weights":
            blend = dump / f"{call}-vb4=44444444.buf"
            blend.write_bytes(bytes([255]) * blend.stat().st_size)
        else:
            blend = dump / f"{call}-vb4=44444444.txt"
            primary, aliases = blend.read_text().split("element[2]:", 1)
            if case == "alias_slot":
                aliases = aliases.replace("InputSlot: 4", "InputSlot: 5")
            elif case == "alias_index_format":
                aliases = aliases.replace("R8G8B8A8_UINT", "R8G8B8A8_UNORM")
            elif case == "alias_weight_format":
                aliases = aliases.replace("R8G8B8A8_UNORM", "R8G8B8A8_UINT")
            elif case == "alias_index_offset":
                aliases = aliases.replace("AlignedByteOffset: 0", "AlignedByteOffset: 4")
            elif case == "alias_weight_offset":
                aliases = aliases.replace("AlignedByteOffset: 4", "AlignedByteOffset: 0")
            elif case == "alias_step":
                aliases = aliases.replace("per-vertex", "per-instance")
            elif case == "alias_missing":
                aliases = aliases.split("element[3]:", 1)[0] + "\nvertex-data:\n"
            blend.write_text(primary + "element[2]:" + aliases)
    analysis = analyze(dump)
    assert not any(c.default_included for c in analysis.components)
    with pytest.raises(ValueError, match="No supported components"):
        extract(analysis)

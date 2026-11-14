import copy
import hashlib
import json
import os
from types import SimpleNamespace

import pytest

from app.assets.resolver import AssetComponentBinding
from app.assets.rig import resolve_game_rig, validate_rig
from core.geometry.skinning import SkinningSource


def rig_fixture(tmp_path):
    root = tmp_path / "assets"
    asset = root / "Model01"
    geometry = asset / "10101010"
    geometry.mkdir(parents=True)
    vertex, index, blend = b"reference vertices", b"reference indices", b"skin stream"
    (geometry / "Component 0.vb").write_bytes(vertex)
    (geometry / "Component 0.ib").write_bytes(index)
    (geometry / "Metadata.json").write_text(json.dumps({
        "vb0_hash": "10101010", "components": [{"index_offset": 12, "index_count": 6}],
    }))
    def digest(value):
        return hashlib.sha256(value).hexdigest()
    raw = {
        "format": "mod-viewer-rig", "version": 1, "coordinate_space": "asset",
        "bones": [
            {"name": "Root", "parent": -1, "translation": [0, 0, 0], "rotation": [0, 0, 0, 1]},
            {"name": "Joint01", "parent": 0, "translation": [1, 2, 3], "rotation": [0, 0, 0, 1]},
        ],
        "components": [{"geometry_hash": "10101010", "component_ordinal": 0,
                        "first_index": 12, "index_count": 6, "bone_map": [1],
                        "vertex_sha256": digest(vertex), "index_sha256": digest(index)}],
        "skin_bindings": [{"sha256": digest(blend), "encoding": "wwmi_u8_4", "bone_map": [1, 0]}],
    }
    (asset / "Rig.json").write_text(json.dumps(raw))
    binding = AssetComponentBinding(
        status="exact", range_status="exact", asset_type="WWMI", root=str(root), asset="Model01",
        geometry_hash="10101010", component_ordinal=0, first_index=12, index_count=6,
        metadata="Model01/10101010/Metadata.json")
    source = SkinningSource("Meshes/Blend.buf", 8, 4, "wwmi_u8_4", bone_id_offset=7)
    return asset, raw, binding, source, digest(blend)


def test_rig_resolution_fingerprint_palette_and_reference_lifecycle(tmp_path):
    asset, raw, binding, source, fingerprint = rig_fixture(tmp_path)
    enabled = {os.path.normcase(binding.root)}

    def read(**kwargs):
        return resolve_game_rig(binding, source, (7, 8), enabled, {},
                                blend_sha256=kwargs.get("fingerprint", fingerprint))

    before = (asset / "Rig.json").read_bytes()
    first = read()
    assert first["bone_map"] == {"7": 1, "8": 0}
    assert first["bones"][1]["translation"] == [1, 2, 3]
    assert read()["id"] == first["id"]
    raw["skin_bindings"].append({
        **raw["skin_bindings"][0], "sha256": "a" * 64, "bone_map": [0, 1],
    })
    (asset / "Rig.json").write_text(json.dumps(raw))
    assert read()["id"] == first["id"]
    assert read(fingerprint="a" * 64)["bone_map"] == {"7": 0, "8": 1}
    assert read(fingerprint="a" * 64)["id"] == first["id"]
    assert read(fingerprint="f" * 64) is None
    assert resolve_game_rig(binding, source, (9,), enabled, {}, blend_sha256=fingerprint) is None
    assert resolve_game_rig(binding, source, (7,), set(), {}, blend_sha256=fingerprint) is None
    (asset / "10101010" / "Component 0.vb").write_bytes(b"changed")
    assert read() is None
    (asset / "10101010" / "Component 0.vb").write_bytes(b"reference vertices")
    raw["bones"][1]["translation"][0] = 4
    (asset / "Rig.json").write_text(json.dumps(raw))
    assert read()["id"] != first["id"]
    (asset / "Rig.json").write_bytes(before)
    assert read()["id"] == first["id"]
    (asset / "Rig.json").unlink()
    assert read() is None


@pytest.mark.parametrize("field,value", [
    ("version", True), ("coordinate_space", "unknown"),
    ("parent", 1), ("rotation", [0, 0, 0, 0]),
    ("translation", [float("nan"), 0, 0]), ("scale", [2, 1, 1]),
    ("scale", [0, 0, 0]), ("scale", [-1, 1, 1]),
    ("scale", [1e-7, 1e-7, 1e-7]), ("scale", [float("inf")] * 3),
    ("translation", [10 ** 400, 0, 0]),
    ("bone_map", [99]), ("vertex_sha256", "bad"),
])
def test_rig_rejects_invalid_schema(tmp_path, field, value):
    _asset, original, _binding, _source, _fingerprint = rig_fixture(tmp_path)
    raw = copy.deepcopy(original)
    target = raw if field in ("version", "coordinate_space") else (
        raw["components"][0] if field in ("bone_map", "vertex_sha256") else raw["bones"][1])
    target[field] = value
    with pytest.raises(ValueError):
        validate_rig(raw)


def test_rig_rejects_metadata_outside_the_exact_asset(tmp_path):
    asset, raw, binding, source, fingerprint = rig_fixture(tmp_path)
    other = asset.parent / "Other01"
    other.mkdir()
    (other / "Metadata.json").write_text(json.dumps({"vb0_hash": "10101010", "components": []}))
    from dataclasses import replace
    outside = replace(binding, metadata="Other01/Metadata.json")
    assert resolve_game_rig(outside, source, (7,), {os.path.normcase(binding.root)}, {},
                            blend_sha256=fingerprint) is None
    (asset / "Rig.json").write_text(json.dumps({**raw, "bones": raw["bones"] * 4097}))
    assert resolve_game_rig(binding, source, (7,), {os.path.normcase(binding.root)}, {},
                            blend_sha256=fingerprint) is None


def test_stock_merged_palette_admits_changed_weights_with_full_reference_proof(tmp_path):
    from dataclasses import replace

    from core.ini.sections import parse_sections
    from core.ini.wwmi_skeleton import stock_wwmi_merged_layouts
    from tests.support.wwmi_skeleton import stock_merged_skeleton_ini

    asset, raw, binding, source, _fingerprint = rig_fixture(tmp_path)
    source = replace(source, bone_id_offset=0)
    raw["skin_bindings"] = []
    raw["components"][0]["bone_map"] = [1, 0]
    raw["components"].append({**raw["components"][0], "component_ordinal": 1,
                              "first_index": 18, "index_count": 3, "bone_map": [1]})
    geometry = asset / "10101010"
    for extension in ("vb", "ib"):
        (geometry / f"Component 1.{extension}").write_bytes((geometry / f"Component 0.{extension}").read_bytes())
    metadata = json.loads((geometry / "Metadata.json").read_text())
    metadata["components"].append({"index_offset": 18, "index_count": 3})
    (geometry / "Metadata.json").write_text(json.dumps(metadata))
    identity = validate_rig(raw)["id"]
    declaration = [{"geometry_hash": "10101010", "component_ordinal": i, "vg_offset": offset}
                   for i, offset in enumerate((0, 2))]
    raw["wwmi_merged_palette"] = declaration
    assert validate_rig(raw)["id"] == identity
    (asset / "Rig.json").write_text(json.dumps(raw))
    sections = parse_sections("source-01.ini", text=stock_merged_skeleton_ini())
    layout = stock_wwmi_merged_layouts(sections)["textureoverridepart01"][1]

    def resolve(proof=layout, current_source=source, digest="f" * 64):
        return resolve_game_rig(binding, current_source, (0, 1, 2),
                                {os.path.normcase(binding.root)}, {},
                                blend_sha256=digest, merged_layout=proof)

    first = resolve()
    assert first["bone_map"] == {"0": 1, "1": 0, "2": 1}
    assert first["id"] == resolve(digest="e" * 64)["id"] == identity
    shared_source = replace(source, bone_id_offset=2, bone_id_namespace="wwmi_merged")
    other_binding = replace(binding, component_ordinal=1, first_index=18, index_count=3)
    shared = resolve_game_rig(other_binding, shared_source, (0, 1, 2),
                              {os.path.normcase(binding.root)}, {}, merged_layout=layout)
    assert shared["bone_map"] == first["bone_map"]
    from core.geometry.skinning import WwmiBlendRemap
    proxy_source = replace(shared_source, bone_id_namespace="wwmi_vertex_vg", vertex_vg_file="full-vg.buf",
                           blend_remap=WwmiBlendRemap("reverse.buf", "forward.buf", 0, 512, 3, 3))
    proxy = resolve_game_rig(other_binding, proxy_source, (0, 2),
                             {os.path.normcase(binding.root)}, {}, merged_layout=layout)
    assert proxy["bone_map"] == first["bone_map"]
    assert resolve(()) is None
    assert resolve(layout[:1]) is None
    assert resolve(((*layout[0][:4], 3), layout[1])) is None
    assert resolve(current_source=replace(source, bone_id_offset=2)) is None
    assert resolve(current_source=replace(source, encoding="wwmi_u16_8")) is None
    assert resolve(current_source=replace(source, vertex_vg_file="remap.buf")) is None
    (geometry / "Component 1.vb").write_bytes(b"changed undrawn reference")
    assert resolve() is None
    for invalid in (declaration[:1], [declaration[0], declaration[0]],
                    [declaration[0], {**declaration[1], "vg_offset": 3}],
                    [declaration[0], {**declaration[1], "vg_offset": True}]):
        with pytest.raises(ValueError):
            validate_rig({**raw, "wwmi_merged_palette": invalid})


def test_explicit_humanoid_mapping_validation_and_fingerprint_lifecycle(tmp_path):
    asset, raw, binding, source, fingerprint = rig_fixture(tmp_path)
    chains = [
        ["pelvis", "chest", "neck", "head"],
        ["chest", "leftShoulder", "leftElbow", "leftHand"],
        ["chest", "rightShoulder", "rightElbow", "rightHand"],
        ["pelvis", "leftHip", "leftKnee", "leftFoot"],
        ["pelvis", "rightHip", "rightKnee", "rightFoot"],
    ]
    mapping = {}
    raw["bones"] = []
    for chain in chains:
        for position, key in enumerate(chain):
            if key in mapping:
                continue
            mapping[key] = len(raw["bones"])
            raw["bones"].append({
                "name": f"Joint{mapping[key]:02}",
                "parent": mapping[chain[position - 1]] if position else -1,
                "translation": [0, 1, 0], "rotation": [0, 0, 0, 1],
            })
    before = validate_rig(raw)["id"]
    raw["humanoid_controls"] = mapping
    normalized = validate_rig(raw)
    assert normalized["humanoid_controls"] == mapping
    assert normalized["id"] != before
    (asset / "Rig.json").write_text(json.dumps(raw))
    resolved = resolve_game_rig(binding, source, (7,), {os.path.normcase(binding.root)}, {},
                                blend_sha256=fingerprint)
    assert resolved["humanoid_controls"] == mapping
    for invalid in ({}, {**mapping, "leftElbow": True},
                    {**mapping, "leftElbow": 999},
                    {**mapping, "leftElbow": mapping["rightElbow"],
                     "rightElbow": mapping["leftElbow"]}):
        with pytest.raises(ValueError):
            validate_rig({**raw, "humanoid_controls": invalid})


def test_lazy_skin_preview_publishes_verified_rig_without_remapping_weights(tmp_path, monkeypatch):
    from app.bridge.mod_preview import ModPreview
    from core.geometry.skinning import SkinningManifestEntry

    asset, raw, binding, source, _fingerprint = rig_fixture(tmp_path)
    mod = tmp_path / "mod"
    (mod / "Meshes").mkdir(parents=True)
    blend = bytes([0, 1, 0, 0, 128, 127, 0, 0]) * 3
    (mod / "Meshes" / "Blend.buf").write_bytes(blend)
    raw["skin_bindings"][0]["sha256"] = hashlib.sha256(blend).hexdigest()
    (asset / "Rig.json").write_text(json.dumps(raw))
    context = SimpleNamespace(mod_dir=str(mod), metadata={}, source=None,
                              asset_folders=[{"type": "WWMI", "path": binding.root, "enabled": True}])
    preview = ModPreview(SimpleNamespace())
    preview._skinning_manifests[str(mod)] = {
        "Mesh01": SkinningManifestEntry.from_vertices(
            "Mesh01", source, (2, 0), asset_binding=binding)}
    monkeypatch.setattr(preview, "authoritative_context", lambda _: (str(mod), {}, context))
    monkeypatch.setattr("app.bridge.mod_preview.server.publish_geometry",
                        lambda *_args, **_kwargs: "/geometry/fixture")
    result = preview.get_model_skinning_preview(str(mod))
    assert result["status"] == "ok"
    assert len(result["game_rigs"]) == 1
    entry = result["meshes"]["Mesh01"]
    assert entry["bone_ids"] == [7, 8]
    assert entry["game_bone_map"] == {"7": 1, "8": 0}
    assert entry["game_rig_id"] in result["game_rigs"]
    assert entry["vertex_count"] == 2
    (mod / "Meshes" / "Blend.buf").write_bytes(blend + bytes(8))
    fallback = preview.get_model_skinning_preview(str(mod))
    assert fallback["status"] == "ok"
    assert fallback["game_rigs"] == {}
    assert fallback["meshes"]["Mesh01"]["bone_ids"] == [7, 8]

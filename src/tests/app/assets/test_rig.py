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
    import struct
    from .test_loader import _fmt

    reference = struct.pack("<9f", 1, 2, 3, 4, 5, 6, 7, 8, 9)
    (asset / "10101010/Component 0.vb").write_bytes(reference)
    _fmt(asset / "10101010/Component 0.fmt", 12, "R16_UINT", [("POSITION", 0, "R32G32B32_FLOAT", 0)])
    raw["components"][0]["vertex_sha256"] = hashlib.sha256(reference).hexdigest()
    (asset / "Rig.json").write_text(json.dumps(raw))
    context = SimpleNamespace(mod_dir=str(mod), metadata={}, source=None,
                              asset_folders=[{"type": "WWMI", "path": binding.root, "enabled": True}])
    preview = ModPreview(SimpleNamespace())
    preview._skinning_manifests[str(mod)] = {
        "Mesh01": SkinningManifestEntry.from_vertices(
            "Mesh01", source, (2, 0, 1), asset_binding=binding,
            positions=struct.pack("<9f", 7.00001, 8, 9, 1, 2, 3, 20, 20, 20))}
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
    assert entry["vertex_count"] == 3
    assert entry["game_morph_binding"] == dict(
        vertex_sha256=hashlib.sha256(reference).hexdigest(),
        index_sha256=raw["components"][0]["index_sha256"], vertices=[2, 0, -1])
    (mod / "Meshes" / "Blend.buf").write_bytes(blend + bytes(8))
    fallback = preview.get_model_skinning_preview(str(mod))
    assert fallback["status"] == "ok"
    assert fallback["game_rigs"] == {}
    assert fallback["meshes"]["Mesh01"]["bone_ids"] == [7, 8]


@pytest.mark.parametrize("case", ["half", "float", "uv", "topology", "count", "missing", "no_uv", "invalid_uv", "truncated"])
def test_morph_topology_fallback_preserves_modified_rest_positions(tmp_path, monkeypatch, case):
    import struct
    from unittest.mock import patch
    from core.geometry.migoto_format import parse_fmt
    from .test_loader import _fmt

    asset, raw, binding, source, fingerprint = rig_fixture(tmp_path)
    directory = asset / "10101010"
    uv_format = "<2f" if case == "float" else "<2e"
    stride = 12 + struct.calcsize(uv_format)
    reference = b"".join(struct.pack("<3f", *point) + struct.pack(uv_format, *uv)
                         for point, uv in [((0, 0, 0), (0, 0)), ((1, 0, 0), (1, 0)), ((0, 1, 0), (0, 1))])
    if case == "invalid_uv":
        reference = reference[:12] + struct.pack(uv_format, float("nan"), 0) + reference[stride:]
    indices = struct.pack("<6H" if case == "float" else "<6I", 0, 1, 2, 2, 1, 0)
    if case == "truncated":
        indices = indices[:-4]
    (directory / "Component 0.vb").write_bytes(reference)
    (directory / "Component 0.ib").write_bytes(indices)
    elements = [("POSITION", 0, "R32G32B32_FLOAT", 0)]
    if case != "no_uv":
        elements.append(("TEXCOORD", 0, "R32G32_FLOAT" if case == "float" else "R16G16_FLOAT", 12))
    _fmt(directory / "Component 0.fmt", stride, "R16_UINT" if case == "float" else "R32_UINT", elements)
    raw["components"][0].update(vertex_sha256=hashlib.sha256(reference).hexdigest(),
                                index_sha256=hashlib.sha256(indices).hexdigest())
    (asset / "Rig.json").write_text(json.dumps(raw))
    positions = struct.pack("<9f", 0, 0, 0, 1, .25, 0, 0, 1, 0)
    if case == "count":
        positions += struct.pack("<3f", -5, -5, 0)
    mod_indices = struct.pack("<6I", 0, 2, 1, 2, 0, 1)
    mod_uv = struct.pack("<6f", 0, 1, 1, 1, 0, 0)
    signature = (hashlib.sha256(mod_indices).hexdigest(), hashlib.sha256(mod_uv).hexdigest())
    if case == "uv":
        signature = signature[0], "0" * 64
    elif case == "topology":
        signature = "0" * 64, signature[1]
    elif case == "missing":
        signature = ()
    cache = {}

    def resolve():
        return resolve_game_rig(binding, source, (7,), {os.path.normcase(binding.root)}, cache,
                                blend_sha256=fingerprint, positions=positions, morph_signature=signature)

    with patch("app.assets.rig.parse_fmt", wraps=parse_fmt) as reader:
        result = resolve()
    assert reader.call_count == 1
    expected = [0, 1, 2] if case in ("half", "float") else [0, -1, 2]
    if case == "count":
        expected.append(-1)
    assert result["morph_binding"]["vertices"] == expected
    if case == "count":
        corrected = resolve_game_rig(binding, source, (7,), {os.path.normcase(binding.root)}, cache,
                                     blend_sha256=fingerprint, positions=positions[:36], morph_signature=signature)
        assert corrected["morph_binding"]["vertices"] == [0, 1, 2]
    monkeypatch.setattr("app.assets.rig.parse_fmt", lambda *_: (_ for _ in ()).throw(
        AssertionError("repeated draws must reuse cached reference geometry")))
    assert resolve()["morph_binding"] == result["morph_binding"]


@pytest.mark.parametrize("case", ["split", "cyclic", "added", "ambiguous", "conflict", "wrong_uv", "invalid_index", "nonrigid"])
def test_morph_uv_triangles_recover_split_vertices_without_guessing_ambiguity(tmp_path, monkeypatch, case):
    import struct
    from .test_loader import _fmt

    asset, raw, binding, source, fingerprint = rig_fixture(tmp_path)
    directory = asset / "10101010"
    points = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0)]
    uvs = [(0, 1), (1, 1), (0, 0), (1, 0)]
    triangles = [0, 1, 2, 2, 1, 3]
    if case == "ambiguous":
        points += [(10, 0, 0), (11, 0, 0), (10, 1, 0)]
        uvs += uvs[:3]
        triangles += [4, 5, 6]
    elif case == "conflict":
        points.append((10, 0, 0))
        uvs.append(uvs[1])
        triangles = [0, 1, 2, 2, 4, 3]
    reference = b"".join(struct.pack("<5f", *point, uv[0], 1 - uv[1]) for point, uv in zip(points, uvs))
    index_data = struct.pack("<" + "I" * len(triangles), *triangles)
    (directory / "Component 0.vb").write_bytes(reference)
    (directory / "Component 0.ib").write_bytes(index_data)
    _fmt(directory / "Component 0.fmt", 20, "R32_UINT", [
        ("POSITION", 0, "R32G32B32_FLOAT", 0), ("TEXCOORD", 0, "R32G32_FLOAT", 12)])
    raw["components"][0].update(vertex_sha256=hashlib.sha256(reference).hexdigest(),
                                index_sha256=hashlib.sha256(index_data).hexdigest(), index_count=len(triangles))
    if case == "nonrigid":
        raw["components"][0]["bone_map"] = [0, 1]
    (asset / "Rig.json").write_text(json.dumps(raw))
    from dataclasses import replace
    binding = replace(binding, index_count=len(triangles))
    metadata_file = directory / "Metadata.json"
    metadata = json.loads(metadata_file.read_text())
    metadata["components"][0]["index_count"] = len(triangles)
    metadata_file.write_text(json.dumps(metadata))
    mod_uvs = uvs[:4] + [uvs[1]]
    mod_triangles = [0, 2, 4, 2, 3, 1]
    if case == "cyclic":
        mod_triangles[:3] = [2, 4, 0]
    elif case == "added":
        mod_uvs += [(.25, .25), uvs[0]]
        mod_triangles += [0, 2, 5]
    elif case == "conflict":
        mod_triangles = [0, 2, 1, 2, 3, 1]
    elif case == "wrong_uv":
        mod_uvs = [(u + 2, v) for u, v in mod_uvs]
    elif case == "invalid_index":
        mod_triangles[-1] = 999
    positions = struct.pack("<" + "fff" * len(mod_uvs), *([20, 20, 20] * len(mod_uvs)))
    topology = (struct.pack("<" + "I" * len(mod_triangles), *mod_triangles),
                b"".join(struct.pack("<2f", *uv) for uv in mod_uvs))
    cache = {}

    def resolve():
        return resolve_game_rig(binding, source, (7,), {os.path.normcase(binding.root)}, cache,
                                blend_sha256=fingerprint, positions=positions, morph_topology=topology)

    result = resolve()
    mapping = result["morph_binding"]["vertices"] if result["morph_binding"] else None
    if case in ("wrong_uv", "invalid_index", "nonrigid"):
        assert mapping is None
    elif case == "ambiguous":
        assert mapping == [-1, 1, 2, 3, -1]
    elif case == "conflict":
        assert mapping == [0, -1, 2, 3, -1]
    else:
        assert mapping == [0, 1, 2, 3, 1] + ([-1, 0] if case == "added" else [])
    monkeypatch.setattr("app.assets.rig.parse_fmt", lambda *_: (_ for _ in ()).throw(
        AssertionError("reference UV topology must be cached across draws")))
    assert resolve()["morph_binding"] == result["morph_binding"]

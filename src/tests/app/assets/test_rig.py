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
    assert first["bone_map"] == {"7": 1, "8": 0} and first["unmapped_bone_ids"] == []
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
    # A used ID the proven palette lacks binds anyway; it is reported and stays at rest.
    partial = resolve_game_rig(binding, source, (7, 9), enabled, {}, blend_sha256=fingerprint)
    assert partial["bone_map"] == first["bone_map"] and partial["unmapped_bone_ids"] == [9]
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


def test_native_component_palette_maps_local_ids_only_for_its_bound_component(tmp_path):
    from dataclasses import replace

    from core.geometry.skinning import wwmi_component_domain

    asset, raw, binding, source, _fingerprint = rig_fixture(tmp_path)
    raw["skin_bindings"] = []
    raw["components"][0]["bone_map"] = [1, 0]
    (asset / "Rig.json").write_text(json.dumps(raw))
    native = replace(source, bone_id_offset=0, bone_id_namespace="wwmi_component",
                     bone_id_domain=wwmi_component_domain("10101010", 12, 6))

    def resolve(current=native, ids=(0, 1)):
        return resolve_game_rig(binding, current, ids, {os.path.normcase(binding.root)}, {},
                                blend_sha256="f" * 64)

    assert resolve()["bone_map"] == {"0": 1, "1": 0}
    assert resolve(ids=(0, 2))["unmapped_bone_ids"] == [2]
    assert resolve(replace(native, bone_id_domain=wwmi_component_domain("10101010", 18, 6))) is None
    assert resolve(replace(native, bone_id_namespace="model", bone_id_domain="model")) is None
    (asset / "10101010" / "Component 0.vb").write_bytes(b"changed")
    assert resolve() is None


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
    assert entry["game_bone_map"] == {"7": 1, "8": 0} and "game_unmapped_bone_ids" not in entry
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


@pytest.mark.parametrize("case", ["half", "float", "uv", "topology", "count", "missing", "no_uv", "invalid_uv", "truncated",
                                  "duplicate", "duplicate_missing"])
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
                         for point, uv in [((0, 0, 0), (0, 0)),
                                           ((0 if case.startswith("duplicate") else 1, 0, 0), (1, 0)),
                                           ((0, 1, 0), (0, 1))])
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
    if case.startswith("duplicate"):
        positions = struct.pack("<9f", 0, 0, 0, 0, 0, 0, 0, 1, 0)
    if case == "count":
        positions += struct.pack("<3f", -5, -5, 0)
    mod_indices = struct.pack("<6I", 0, 2, 1, 2, 0, 1)
    mod_uv = struct.pack("<6f", 0, 1, 1, 1, 0, 0)
    signature = (hashlib.sha256(mod_indices).hexdigest(), hashlib.sha256(mod_uv).hexdigest())
    if case == "uv":
        signature = signature[0], "0" * 64
    elif case == "topology":
        signature = "0" * 64, signature[1]
    elif case in ("missing", "duplicate_missing"):
        signature = ()
    cache = {}

    def resolve():
        return resolve_game_rig(binding, source, (7,), {os.path.normcase(binding.root)}, cache,
                                blend_sha256=fingerprint, positions=positions, morph_signature=signature)

    with patch("app.assets.rig.parse_fmt", wraps=parse_fmt) as reader:
        result = resolve()
    assert reader.call_count == 1
    expected = [0, 1, 2] if case in ("half", "float", "duplicate") else [0, -1, 2]
    if case == "duplicate_missing":
        expected = [-1, -1, 2]
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


@pytest.mark.parametrize("case", ["split", "cyclic", "added", "ambiguous", "conflict", "wrong_uv", "invalid_index", "multi_bone",
                                  "duplicate_positions"])
def test_morph_uv_triangles_recover_split_vertices_without_guessing_ambiguity(tmp_path, monkeypatch, case):
    import struct
    from .test_loader import _fmt

    asset, raw, binding, source, fingerprint = rig_fixture(tmp_path)
    directory = asset / "10101010"
    points = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0)]
    uvs = [(0, 1), (1, 1), (0, 0), (1, 0)]
    triangles = [0, 1, 2, 2, 1, 3]
    if case == "duplicate_positions":
        points[1] = points[0]
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
    if case == "multi_bone":
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
    if case == "conflict":
        positions = positions[:12] + struct.pack("<3f", *points[1]) + positions[24:]
    topology = (struct.pack("<" + "I" * len(mod_triangles), *mod_triangles),
                b"".join(struct.pack("<2f", *uv) for uv in mod_uvs))
    cache = {}

    def resolve():
        return resolve_game_rig(binding, source, (7,), {os.path.normcase(binding.root)}, cache,
                                blend_sha256=fingerprint, positions=positions, morph_topology=topology)

    result = resolve()
    mapping = result["morph_binding"]["vertices"] if result["morph_binding"] else None
    if case in ("wrong_uv", "invalid_index"):
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


def test_zzz_merged_ids_map_through_each_captured_native_blend_stream(monkeypatch):
    from app.assets import rig

    streams = {("hash.json", 0): {"blend_vb": "33333333"}, ("hash.json", 1): {"blend_vb": "44444444"}}
    monkeypatch.setattr(rig, "_zzz_metadata", lambda _directory, palette, _cache: streams.get(
        (palette["metadata_file"], palette["metadata_entry"])))

    def component(entry, bone_map):
        return dict(metadata_file="hash.json", metadata_entry=entry, bone_map=bone_map)

    # Components of one stream share its palette; local IDs past a segment's
    # copied rows and segments of uncaptured streams stay unmapped.
    model = {"components": [component(0, {"0": 5, "1": 6}), component(0, {"1": 6}),
                            component(1, {"0": 7, "2": 8, "3": 9})]}
    layout = (("33333333", 0, 2), ("44444444", 2, 3), ("55555555", 5, 1))
    own = streams["hash.json", 0]
    assert rig._zzz_merged_bone_map(model, "", own, layout, {}) == {"0": 5, "1": 6, "2": 7, "4": 8}
    # The drawn component's own stream must be part of the merged palette.
    assert rig._zzz_merged_bone_map(model, "", {"blend_vb": "66666666"}, layout, {}) is None
    model["components"].append(component(0, {"1": 9}))
    assert rig._zzz_merged_bone_map(model, "", own, layout, {}) is None


@pytest.mark.parametrize("asset_type, native", [("ZZMI", "11111111"), ("GIMI", "33333333")])
def test_zzz_native_stream_palette_is_shared_by_every_component_and_drawing_override(tmp_path, monkeypatch, asset_type, native):
    # ZZZ proves the stream by its position input; GIMI by its skinning pass's Blend input.
    from app.assets import rig

    streams = {0: {"blend_vb": "33333333", "position_vb": "11111111"},
               1: {"blend_vb": "44444444", "position_vb": "22222222"}}
    (tmp_path / "hash.json").write_text("{}")

    def component(geometry_hash, ordinal, entry, bone_map):
        return dict(geometry_hash=geometry_hash, component_ordinal=ordinal, first_index=ordinal * 3, index_count=3,
                    metadata_file="hash.json", metadata_entry=entry, bone_map=bone_map,
                    vertex_sha256="a" * 64, index_sha256="b" * 64)

    model = {"id": "rig", "version": 2, "bones": [], "skin_bindings": [], "components": [
        component("aaaaaaaa", 0, 0, {"0": 5, "1": 6}), component("aaaaaaaa", 1, 0, {"2": 7}),
        component("bbbbbbbb", 0, 1, {"0": 8})]}
    monkeypatch.setattr(rig, "_admitted_rig", lambda *_: (str(tmp_path), model))
    monkeypatch.setattr(rig, "_zzz_metadata", lambda _directory, palette, _cache: streams[palette["metadata_entry"]])
    monkeypatch.setattr(rig, "_zzz_morph_reference", lambda *_: object())
    monkeypatch.setattr(rig, "_mod_morph_vertices", lambda *_args, **_kwargs: [0])

    def resolve(geometry_hash, ordinal, ids, hashes=(native,), merged_layout=(), count=3, positions=(),
                layout=("gimi_f32_u32_4", 32)):
        binding = SimpleNamespace(asset_type=asset_type, root=str(tmp_path), metadata="hash.json",
                                  geometry_hash=geometry_hash, first_index=ordinal * 3, index_count=count,
                                  component_ordinal=ordinal, metadata_entry=None)
        source = SkinningSource("blend.buf", layout[1], 4, layout[0], native_palette_hashes=hashes,
                                native_position_hashes=positions)
        return resolve_game_rig(binding, source, ids, set(), {}, merged_layout=merged_layout)

    # Each captured component holds only the slice of the stream palette it
    # used; a component of that stream skins with the whole captured palette.
    own = resolve("aaaaaaaa", 1, (0, 1, 2))
    assert own["bone_map"] == {"0": 5, "1": 6, "2": 7} and own["morph_binding"]["vertices"] == [0]
    # An exact range known only by its first index (no metadata count) finds the same
    # unique component; a contradicting count does not.
    assert resolve("aaaaaaaa", 1, (0, 1, 2), count=None)["bone_map"] == own["bone_map"]
    assert resolve("aaaaaaaa", 1, (0, 1, 2), count=4) is None
    # Another component's override drawing that stream uses the stream palette,
    # but its own morph reference describes different geometry.
    drawn_elsewhere = resolve("bbbbbbbb", 0, (0, 2))
    assert drawn_elsewhere["bone_map"] == {"0": 5, "1": 6, "2": 7} and drawn_elsewhere["morph_binding"] is None
    beyond = resolve("aaaaaaaa", 1, (0, 3))
    assert beyond["bone_map"] == own["bone_map"] and beyond["unmapped_bone_ids"] == [3]
    assert resolve("aaaaaaaa", 1, (0,), (native, "22222222")) is None
    # Hashes the Asset never captured (a patch-changed Blend hash) are no evidence; a captured position hash may name the stream.
    assert resolve("aaaaaaaa", 1, (0, 1, 2), ("99999999",)) is None
    # That position hash names stream 1, whose palette then binds; IDs it lacks stay at rest.
    other_stream = resolve("aaaaaaaa", 1, (0, 1, 2), ("99999999",), positions=("22222222",))
    assert other_stream["bone_map"] == {"0": 8} and other_stream["unmapped_bone_ids"] == [1, 2]
    assert other_stream["morph_binding"] is None
    assert resolve("aaaaaaaa", 1, (0, 1, 2), ("99999999",), positions=("11111111",))["bone_map"] == own["bone_map"]
    # A Blend hash the Asset knows for another stream is a conflict, not a patch change.
    assert resolve("aaaaaaaa", 1, (0, 1, 2), ("44444444", "99999999"), positions=("11111111",)) is None
    # A Blend also fed to streams the Asset never captured (level-of-detail copies) keeps the captured stream's
    # palette; every captured hash, position hashes included, must still name that stream.
    assert resolve("aaaaaaaa", 1, (0, 1, 2), (native, "99999999"))["bone_map"] == own["bone_map"]
    assert resolve("aaaaaaaa", 1, (0, 1, 2), (native, "99999999"), positions=("22222222",)) is None
    # Packed and rigid Blend layouts carry raw palette IDs of the same stream.
    for layout in (("wwmi_u8_4", 8), ("rigid_u32_1", 4)):
        assert resolve("aaaaaaaa", 1, (0, 1, 2), layout=layout)["bone_map"] == own["bone_map"]
    if asset_type == "GIMI":
        # Merged skeleton layouts are a ZZZ program; GIMI keeps the native stream palette.
        assert resolve("aaaaaaaa", 1, (0, 1, 2), merged_layout=(("44444444", 0, 4),))["bone_map"] == own["bone_map"]
    model["components"].append(component("aaaaaaaa", 2, 0, {"0": 9}))
    assert resolve("aaaaaaaa", 1, (0,)) is None


ATTACHMENT = dict(weights=[.75, .25, 0, 0, 1, 0, 0, 0], slots=[1, 0, 0, 0, 0, 0, 0, 0], expression={"7": 1.0})


def test_attachment_palette_admits_only_the_assets_own_attachment_stream(tmp_path, monkeypatch):
    from app.assets import rig

    (tmp_path / "hash.json").write_text("{}")
    attached = dict(geometry_hash="cccccccc", component_ordinal=0, first_index=0, index_count=6, metadata_file="hash.json",
                    metadata_entry=0, bone_map={"0": 1, "1": 0}, attachment=ATTACHMENT,
                    vertex_sha256="a" * 64, index_sha256="b" * 64)
    skinned = {**attached, "geometry_hash": "dddddddd", "metadata_entry": 1}
    del skinned["attachment"]
    model = {"id": "rig", "version": 2, "bones": [], "skin_bindings": [], "components": [attached, skinned]}
    monkeypatch.setattr(rig, "_admitted_rig", lambda *_: (str(tmp_path), model))
    monkeypatch.setattr(rig, "_zzz_metadata", lambda *_: {})

    def binding(geometry_hash, asset_type="GIMI"):
        return SimpleNamespace(asset_type=asset_type, root=str(tmp_path), metadata="hash.json",
                               geometry_hash=geometry_hash, first_index=0, index_count=6, component_ordinal=0,
                               metadata_entry=None)

    def resolve(geometry_hash, attachment, asset_type="GIMI", captured=True):
        source = SkinningSource("part.txt", 32, 4, "gimi_f32_u32_4",
                                bone_id_namespace=rig.ATTACHMENT_NAMESPACE if attachment else "model")
        return resolve_game_rig(binding(geometry_hash, asset_type), source, (0, 1), set(), {}, captured_palette=captured)

    assert rig.game_attachment(binding("cccccccc"), set(), {}) == ATTACHMENT
    assert rig.game_attachment(binding("dddddddd"), set(), {}) is None
    assert rig.game_attachment(binding("cccccccc", "ZZMI"), set(), {}) is None
    resolved = resolve("cccccccc", True)
    assert resolved["bone_map"] == {"0": 1, "1": 0}
    assert resolved["morph_binding"] == dict(vertex_sha256="a" * 64, index_sha256="b" * 64)
    # Attachment weights never bind a skinned palette, a mod draw or another game;
    # captured Blend weights never bind an attachment palette.
    assert resolve("dddddddd", True) is None
    assert resolve("cccccccc", True, captured=False) is None
    assert resolve("cccccccc", True, asset_type="ZZMI") is None
    assert resolve("cccccccc", False) is None
    assert resolve("dddddddd", False)["bone_map"] == {"0": 1, "1": 0}


def test_alternate_passes_sharing_an_index_range_are_told_apart_by_metadata_entry(tmp_path, monkeypatch):
    from app.assets import rig

    (tmp_path / "hash.json").write_text("{}")
    bones = [{"name": "Root", "parent": -1, "translation": [0, 0, 0], "rotation": [0, 0, 0, 1], "scale": [1, 1, 1]},
             {"name": "Joint01", "parent": 0, "translation": [0, 1, 0], "rotation": [0, 0, 0, 1], "scale": [1, 1, 1]}]
    alternate = {**ATTACHMENT, "expression": {"8": 1.0}}
    first = dict(geometry_hash="cccccccc", component_ordinal=0, first_index=0, index_count=6, metadata_file="hash.json",
                 metadata_entry=0, bone_map={"0": 1, "1": 0}, attachment=ATTACHMENT,
                 vertex_sha256="a" * 64, index_sha256="b" * 64)
    second = {**first, "metadata_entry": 1, "attachment": alternate, "vertex_sha256": "c" * 64}
    raw = dict(format="mod-viewer-rig", version=2, coordinate_space="asset", bones=bones, skin_bindings=[],
               components=[first, second])
    model = validate_rig(raw)
    with pytest.raises(ValueError, match="Duplicate rig palette"):
        validate_rig({**raw, "components": [first, {**second, "metadata_entry": 0}]})
    monkeypatch.setattr(rig, "_admitted_rig", lambda *_: (str(tmp_path), model))
    monkeypatch.setattr(rig, "_zzz_metadata", lambda *_: {})

    def binding(entry):
        return SimpleNamespace(asset_type="GIMI", root=str(tmp_path), metadata="hash.json", geometry_hash="cccccccc",
                               first_index=0, index_count=6, component_ordinal=0, metadata_entry=entry)

    assert rig.game_attachment(binding(0), set(), {}) == ATTACHMENT
    assert rig.game_attachment(binding(1), set(), {}) == alternate
    # A mod draw cannot name the pass, so the shared range stays unbound.
    assert rig.game_attachment(binding(None), set(), {}) is None


@pytest.mark.parametrize("change", [
    dict(weights=[.75, .25, 0, 0, .5, 0, 0, 0]), dict(weights=[.75, .25, 0, 0, 1, 0, 0]),
    dict(slots=[2, 0, 0, 0, 0, 0, 0, 0]), dict(weights=[1.5, -.5, 0, 0, 1, 0, 0, 0]),
    dict(expression={"name": 1.0}), dict(expression={"7": float("nan")}), dict(extra=1)])
def test_rig_rejects_invalid_attachment_palettes(change):
    bones = [{"name": "Root", "parent": -1, "translation": [0, 0, 0], "rotation": [0, 0, 0, 1], "scale": [1, 1, 1]},
             {"name": "Joint01", "parent": 0, "translation": [0, 1, 0], "rotation": [0, 0, 0, 1], "scale": [1, 1, 1]}]
    component = dict(geometry_hash="cccccccc", component_ordinal=0, first_index=0, index_count=6,
                     bone_map={"0": 1, "1": 0}, vertex_sha256="a" * 64, index_sha256="b" * 64)
    raw = dict(format="mod-viewer-rig", version=2, coordinate_space="asset", bones=bones, skin_bindings=[],
               components=[{**component, "attachment": ATTACHMENT}])
    assert validate_rig(raw)["components"][0]["attachment"] == ATTACHMENT
    raw["components"] = [{**component, "attachment": {**ATTACHMENT, **change}}]
    with pytest.raises(ValueError):
        validate_rig(raw)

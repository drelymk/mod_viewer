import json
import os
import struct

import pytest

from app.assets import index as asset_index
from app.assets.enrichment import apply
from app.assets.resolver import (AssetComponentBinding, resolve_component,
                                resolve_groups, summarize_groups)
from core.geometry.draw_call import DrawCall, SlotTextureBinding
from core.geometry.identity import GeometryMatch
from core.textures import classifier as dds_classifier
from core.ini.parser import TextureOverrideIndex, TextureReplacement
from core.geometry.mesh_builder import GeometryBlob, build_mesh_result
from tests.support.asset_data import standard_asset_index


def _index(root, asset_type="GIMI", metadata=None, *, asset="Asset01",
           first_index=12, classification="B"):
    return standard_asset_index(
        root, asset_type=asset_type, asset=asset, metadata=metadata,
        first_index=first_index, classification=classification)


def test_asset_hash_applies_conditional_mod_replacement(tmp_path):
    root = os.path.normcase(os.path.abspath(str(tmp_path / "assets")))
    asset_dir = tmp_path / "assets" / "Asset01"
    asset_dir.mkdir(parents=True)
    (asset_dir / "hash.json").write_text(json.dumps([{
        "ib": "10101010", "object_indexes": [12],
        "object_classifications": ["B"],
        "texture_hashes": [[
            ["Diffuse", ".dds", "11111111"],
        ]],
    }]), encoding="utf-8")
    replacement = TextureReplacement.from_dnf(
        "11111111", "ResourceAsset02Diffuse", [[{
            "var": "style", "value": "1", "negate": False}]],
        "TextureOverrideDiffuse")
    replacement = TextureReplacement(
        replacement.original_hash, replacement.resource, replacement.conditions,
        replacement.source_section, "Asset02Diffuse.dds")
    index = TextureOverrideIndex(
        replacements_by_hash={"11111111": (replacement,)})
    draw = DrawCall()
    binding = AssetComponentBinding(
        status="exact", asset_type="GIMI", asset="Asset01", root=root,
        component_status="exact", range_status="exact",
        geometry_hash="10101010", component_name="Body",
        classification="B", first_index=12,
        metadata="Asset01/hash.json")

    apply([{"draws": [draw]}], [[binding]], texture_index=index)

    assert draw.texture_rules("diffuse") == [{
        "conditions": [[{
            "var": "style", "value": "1", "negate": False}]],
        "file": "Asset02Diffuse.dds",
        "texture_hashes": ("11111111",),
    }]
    assert draw.texture_provenance == {"diffuse": "mod_texture_hash"}
    assert draw.texture_hashes["diffuse"] == ["11111111"]


def test_resolver_uses_enabled_indexes_and_range_evidence(tmp_path, monkeypatch):
    root = os.path.normcase(os.path.abspath(str(tmp_path / "assets")))
    entries = [{"type": "GIMI", "path": root, "enabled": True}]
    monkeypatch.setattr(
        asset_index, "load_index", lambda asset_type, path: _index(root))

    binding = resolve_component(
        GeometryMatch("10101010", 12, 24), "genshin", entries)

    assert binding.status == "exact"
    assert binding.asset == "Asset01"
    assert binding.component_name == "Body"
    assert binding.classification == "B"

    entries[0]["enabled"] = False
    assert resolve_component(
        GeometryMatch("10101010", 12, 24), "genshin", entries).status == \
        "not_found"


def test_resolver_marks_duplicate_enabled_roots_ambiguous(tmp_path, monkeypatch):
    roots = [os.path.normcase(os.path.abspath(str(tmp_path / name)))
             for name in ("one", "two")]
    entries = [{"type": "GIMI", "path": root, "enabled": True}
               for root in roots]
    monkeypatch.setattr(
        asset_index, "load_index", lambda asset_type, path: _index(path))

    binding = resolve_component(
        GeometryMatch("10101010", 12, None), "genshin", entries)

    assert binding.status == "ambiguous"
    assert binding.geometry_hash == "10101010"


def test_unknown_game_accepts_exact_match_but_rejects_hash_only_enrichment(
        tmp_path, monkeypatch):
    root = os.path.normcase(os.path.abspath(str(tmp_path / "zzmi")))
    entries = [{"type": "ZZMI", "path": root, "enabled": True}]
    monkeypatch.setattr(
        asset_index, "load_index",
        lambda asset_type, path: _index(path, asset_type=asset_type))

    exact_binding = resolve_component(
        GeometryMatch("10101010", 12, 24), "unknown", entries)
    hash_only_binding = resolve_component(
        GeometryMatch("10101010"), "unknown", entries)

    assert exact_binding.status == "exact"
    assert exact_binding.asset_type == "ZZMI"
    assert hash_only_binding.status == "not_found"


def test_unknown_game_cross_type_exact_matches_are_ambiguous(
        tmp_path, monkeypatch):
    entries = [{
        "type": asset_type,
        "path": os.path.normcase(os.path.abspath(str(tmp_path / asset_type))),
        "enabled": True,
    } for asset_type in ("GIMI", "ZZMI")]
    monkeypatch.setattr(
        asset_index, "load_index",
        lambda asset_type, path: _index(path, asset_type=asset_type))

    binding = resolve_component(
        GeometryMatch("10101010", 12, 24), "unknown", entries)

    assert binding.status == "ambiguous"
    assert binding.asset_type is None


def test_known_genshin_does_not_probe_matching_zzmi_index(
        tmp_path, monkeypatch):
    root = os.path.normcase(os.path.abspath(str(tmp_path / "zzmi")))
    entries = [{"type": "ZZMI", "path": root, "enabled": True}]
    calls = []

    def load_index(asset_type, path):
        calls.append((asset_type, path))
        return _index(path, asset_type=asset_type)

    monkeypatch.setattr(asset_index, "load_index", load_index)

    binding = resolve_component(
        GeometryMatch("10101010", 12, 24), "genshin", entries)

    assert binding.status == "not_found"
    assert calls == []


@pytest.mark.parametrize("offsets, first_index, status, asset, range_status", [
    ((0, 12), 12, "exact", "Asset02", "exact"),
    ((12, 12), 12, "ambiguous", None, "ambiguous"),
    ((12, 12), None, "ambiguous", None, "unknown"),
], ids=["unique-range", "duplicate-range", "no-range"])
def test_same_hash_range_resolution(tmp_path, monkeypatch, offsets, first_index,
                                    status, asset, range_status):
    roots = [os.path.normcase(os.path.abspath(str(tmp_path / name)))
             for name in ("asset-01", "asset-02")]
    entries = [{"type": "GIMI", "path": root, "enabled": True}
               for root in roots]

    def load_index(asset_type, path):
        ordinal = roots.index(path)
        return _index(path, asset=f"Asset{ordinal + 1:02}",
                      first_index=offsets[ordinal])

    monkeypatch.setattr(asset_index, "load_index", load_index)
    binding = resolve_component(GeometryMatch("10101010", first_index),
                                "genshin", entries)
    assert binding.status == status
    if asset is not None:
        assert binding.asset == asset
    assert binding.range_status == range_status


def test_draw_count_does_not_become_asset_range_evidence(
        tmp_path, monkeypatch):
    roots = [os.path.normcase(os.path.abspath(str(tmp_path / name)))
             for name in ("assets-one", "assets-two")]
    entries = [{"type": "ZZMI", "path": root, "enabled": True}
               for root in roots]
    index = _index(roots[0], asset_type="ZZMI", asset="Asset02", first_index=0)
    index["assets"].append({
        "path": "Asset03",
        "geometry": [{
            "hash": "10101010",
            "ranges": [{"firstIndex": 0, "indexCount": 8}],
            "metadata": "Asset03/hash.json",
        }],
    })
    index["byGeometryHash"]["10101010"].append({
        "asset": 1, "geometry": 0,
    })
    second = _index(roots[1], asset_type="ZZMI", asset="Other",
                    first_index=0)
    monkeypatch.setattr(
        asset_index, "load_index",
        lambda asset_type, path: index if path == roots[0] else second)

    bindings = resolve_groups([{
        "name": "Asset02HairA",
        "draws": [DrawCall(
            count=12, geometry_match=GeometryMatch("10101010", 0))],
    }],
        "zzz", entries)

    assert bindings[0][0].status == "ambiguous"


def test_resolve_groups_scopes_narrowing_to_shared_ini_provenance(
        tmp_path, monkeypatch):
    root = os.path.normcase(os.path.abspath(str(tmp_path / "zzmi")))
    entries = [{"type": "ZZMI", "path": root, "enabled": True}]
    index = _index(root, asset_type="ZZMI", asset="AssetA",
                   first_index=0)
    shared_a = {
        "hash": "aabbccdd",
        "ranges": [{"firstIndex": 100, "indexCount": 12}],
        "metadata": "AssetA/hash.json",
        "componentName": "Hair",
    }
    index["assets"][0]["geometry"].append(shared_a)
    index["assets"].append({
        "path": "AssetB",
        "geometry": [{
            "hash": "bbbbcccc",
            "ranges": [{"firstIndex": 50, "indexCount": 8}],
            "metadata": "AssetB/hash.json",
            "componentName": "Component03",
        }, {
            "hash": "aabbccdd",
            "ranges": [{"firstIndex": 100, "indexCount": 12}],
            "metadata": "AssetB/hash.json",
            "componentName": "Hair",
        }],
    })
    index["byGeometryHash"]["aabbccdd"] = [
        {"asset": 0, "geometry": 1},
        {"asset": 1, "geometry": 1},
    ]
    index["byGeometryHash"]["bbbbcccc"] = [{"asset": 1, "geometry": 0}]
    monkeypatch.setattr(asset_index, "load_index",
                        lambda asset_type, path: index)

    bindings = resolve_groups([
        {"draws": [DrawCall(
            sources=[{"ini_path": "ini-a.ini"}],
            geometry_match=GeometryMatch("10101010", 0, 24))]},
        {"draws": [DrawCall(
            sources=[{"ini_path": "ini-c.ini"}],
            geometry_match=GeometryMatch("aabbccdd", 100, 12))]},
        {"draws": [
            DrawCall(
                sources=[{"ini_path": "ini-b.ini"}],
                geometry_match=GeometryMatch("bbbbcccc", 50, 8)),
        ]},
        {"draws": [DrawCall(
            sources=[{"ini_path": "ini-b.ini"}],
            geometry_match=GeometryMatch("aabbccdd", 100, 12))]},
    ], "zzz", entries)

    assert bindings[0][0].asset == "AssetA"
    assert bindings[1][0].status == "ambiguous"
    assert bindings[2][0].asset == "AssetB"
    assert bindings[3][0].status == "exact"
    assert bindings[3][0].asset == "AssetB"


def test_resolve_groups_keeps_conflicting_exact_assets_ambiguous(
        tmp_path, monkeypatch):
    root = os.path.normcase(os.path.abspath(str(tmp_path / "zzmi")))
    entries = [{"type": "ZZMI", "path": root, "enabled": True}]
    index = _index(root, asset_type="ZZMI", asset="AssetA",
                   first_index=0)
    index["assets"].append({
        "path": "AssetB",
        "geometry": [{
            "hash": "bbbbcccc",
            "ranges": [{"firstIndex": 50, "indexCount": 8}],
            "metadata": "AssetB/hash.json",
            "componentName": "Component03",
        }],
    })
    index["byGeometryHash"]["bbbbcccc"] = [{"asset": 1, "geometry": 0}]
    for asset_number, asset_name in enumerate(("AssetA", "AssetB")):
        geometry = {
            "hash": "aabbccdd",
            "ranges": [{"firstIndex": 100, "indexCount": 12}],
            "metadata": f"{asset_name}/hash.json",
            "componentName": "Hair",
        }
        index["assets"][asset_number]["geometry"].append(geometry)
        index["byGeometryHash"].setdefault("aabbccdd", []).append({
            "asset": asset_number, "geometry": 1,
        })
    monkeypatch.setattr(asset_index, "load_index",
                        lambda asset_type, path: index)

    bindings = resolve_groups([{"draws": [
        DrawCall(geometry_match=GeometryMatch("10101010", 0, 24)),
        DrawCall(geometry_match=GeometryMatch("bbbbcccc", 50, 8)),
        DrawCall(geometry_match=GeometryMatch("aabbccdd", 100, 12)),
    ]}], "zzz", entries)

    assert [item.status for item in bindings[0]] == [
        "exact", "exact", "ambiguous"]


def test_resolve_groups_loads_each_enabled_index_once(tmp_path, monkeypatch):
    roots = [os.path.normcase(os.path.abspath(str(tmp_path / name)))
             for name in ("one", "two")]
    entries = [{"type": "GIMI", "path": root, "enabled": True}
               for root in roots]
    calls = []

    def load_index(asset_type, path):
        calls.append(path)
        return _index(path)

    monkeypatch.setattr(asset_index, "load_index", load_index)
    groups = [{"draws": [
        DrawCall(geometry_match=GeometryMatch("10101010"))
        for _ in range(10)
    ]}]

    resolve_groups(groups, "genshin", entries)

    assert calls == roots


def test_equivalent_hash_metadata_collapses_same_root_asset_records(
        tmp_path, monkeypatch):
    root = tmp_path / "zzmi"
    entries = [{"type": "ZZMI", "path": str(root), "enabled": True}]
    common = {
        "ib": "10101010",
        "blend_vb": "blend",
        "draw_vb": "draw",
        "position_vb": "position",
        "texcoord_vb": "texcoord",
        "root_vs": "root",
        "component_name": "Hair",
        "object_indexes": [0, 4368],
        "object_classifications": ["A", "B"],
        "texture_hashes": [
            [["Diffuse", ".dds", "11111111"]],
            [["Diffuse", ".dds", "11111111"]],
        ],
    }
    for asset, counts in (("Asset02", None),
                          ("Asset03", [4368, 15783])):
        asset_dir = root / asset
        asset_dir.mkdir(parents=True)
        payload = dict(common)
        if counts is not None:
            payload["object_index_counts"] = counts
        (asset_dir / "hash.json").write_text(
            json.dumps([payload]), encoding="utf-8")

    index = asset_index.build_index("ZZMI", str(root))
    fingerprints = {
        geometry["componentFingerprint"]
        for asset in index["assets"]
        for geometry in asset["geometry"]
    }
    assert len(fingerprints) == 1
    monkeypatch.setattr(asset_index, "load_index",
                        lambda asset_type, path: index)
    monkeypatch.setattr(
        "builtins.open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("runtime resolution reread metadata")))

    binding = resolve_component(
        GeometryMatch("10101010", 0), "zzz", entries)

    assert binding.status == "exact"
    assert binding.asset == "Asset02"
    assert binding.metadata == "Asset02/hash.json"


def test_resolve_groups_reports_partial_index_coverage(tmp_path, monkeypatch):
    roots = [os.path.normcase(os.path.abspath(str(tmp_path / name)))
             for name in ("one", "two")]
    entries = [{"type": "GIMI", "path": root, "enabled": True}
               for root in roots]

    def load_index(asset_type, path):
        return None if path == roots[0] else _index(path)

    monkeypatch.setattr(asset_index, "load_index", load_index)
    availability = {}
    resolve_groups(
        [{"draws": [DrawCall(geometry_match=GeometryMatch("10101010"))]}],
        "genshin", entries, availability=availability)

    assert availability == {
        "asset_type": "GIMI", "configured_roots": 2,
        "ready_roots": 1, "unavailable_roots": 1,
    }


def test_asset_original_fallback_fills_only_missing_roles(tmp_path):
    root = os.path.normcase(os.path.abspath(str(tmp_path / "assets")))
    asset_dir = tmp_path / "assets" / "Asset01"
    asset_dir.mkdir(parents=True)
    (asset_dir / "Asset01BodyBDiffuse.dds").write_bytes(b"diffuse")
    (asset_dir / "Asset01BodyBNormalMap.dds").write_bytes(b"normal")
    metadata = asset_dir / "hash.json"
    metadata.write_text(json.dumps([{
        "ib": "10101010",
        "object_indexes": [12],
        "object_classifications": ["B"],
        "texture_hashes": [[
            ["Diffuse", ".dds", "11111111"],
            ["NormalMap", ".dds", "22222222"],
        ]],
    }]), encoding="utf-8")
    draw = DrawCall(
        geometry_match=GeometryMatch("10101010", 12, None),
        texture_default_file="mod-diffuse.dds",
        slot_textures=[SlotTextureBinding(1, "ResourceMystery")],
    )
    binding = AssetComponentBinding(
        status="exact", asset_type="GIMI", asset="Asset01", root=root,
        component_status="exact", range_status="exact",
        geometry_hash="10101010", component_name="Body",
        classification="B", first_index=12,
        metadata="Asset01/hash.json",
    )

    apply([{"draws": [draw]}], [[binding]])

    assert draw.asset_texture_defaults["normal_map"]["path"].endswith(
        "Asset01BodyBNormalMap.dds")
    assert "diffuse" not in draw.asset_texture_defaults
    assert draw.texture_provenance == {
        "diffuse": "mod_semantic",
        "normal_map": "asset_original_fallback",
    }


def test_asset_locator_uses_component_and_classification(tmp_path):
    root = os.path.normcase(os.path.abspath(str(tmp_path / "assets")))
    asset_dir = tmp_path / "assets" / "Asset01"
    asset_dir.mkdir(parents=True)
    (asset_dir / "Asset01BodyADiffuse.dds").write_bytes(b"A")
    (asset_dir / "Asset01BodyBDiffuse.dds").write_bytes(b"B")
    metadata = asset_dir / "hash.json"
    metadata.write_text(json.dumps([{
        "ib": "10101010", "object_indexes": [12],
        "object_classifications": ["B"],
        "texture_hashes": [[
            ["Diffuse", ".dds", "11111111"],
        ]],
    }]), encoding="utf-8")
    binding = AssetComponentBinding(
        status="exact", component_status="exact", range_status="exact",
        asset_type="GIMI", asset="Asset01", root=root,
        geometry_hash="10101010", component_name="Body",
        classification="B", first_index=12,
        metadata="Asset01/hash.json")

    draw = DrawCall()
    apply([{"draws": [draw]}], [[binding]])
    assert draw.asset_texture_defaults["diffuse"]["path"].endswith(
        "Asset01BodyBDiffuse.dds")

    (asset_dir / "Asset01BodyBDiffuse.dds").unlink()
    (asset_dir / "UnrelatedDiffuse.dds").write_bytes(b"unrelated")
    draw = DrawCall()
    apply([{"draws": [draw]}], [[binding]])
    assert draw.asset_texture_defaults == {}


def test_hash_only_geometry_resolves_component_but_not_range(tmp_path,
                                                             monkeypatch):
    root = os.path.normcase(os.path.abspath(str(tmp_path / "assets")))
    entries = [{"type": "GIMI", "path": root, "enabled": True}]
    monkeypatch.setattr(
        asset_index, "load_index", lambda asset_type, path: _index(root))

    binding = resolve_component(
        GeometryMatch("10101010"), "genshin", entries)

    assert binding.status == "exact"
    assert binding.component_status == "exact"
    assert binding.range_status == "unknown"
    assert binding.component_name == "Body"


def test_hash_only_component_does_not_enable_object_texture_fallback(tmp_path,
                                                                     monkeypatch):
    root = os.path.normcase(os.path.abspath(str(tmp_path / "assets")))
    asset_dir = tmp_path / "assets" / "Asset01"
    asset_dir.mkdir(parents=True)
    (asset_dir / "Asset01BodyADiffuse.dds").write_bytes(b"A")
    (asset_dir / "Asset01BodyBDiffuse.dds").write_bytes(b"B")
    (asset_dir / "hash.json").write_text(json.dumps([{
        "ib": "10101010", "object_indexes": [1, 12],
        "object_classifications": ["A", "B"],
        "texture_hashes": [
            [["Diffuse", ".dds", "11111111"]],
            [["Diffuse", ".dds", "22222222"]],
        ],
    }]), encoding="utf-8")
    entries = [{"type": "GIMI", "path": root, "enabled": True}]
    monkeypatch.setattr(
        asset_index, "load_index", lambda asset_type, path: _index(root))

    binding = resolve_component(GeometryMatch("10101010"), "genshin",
                                entries)
    draw = DrawCall()
    apply([{"draws": [draw]}], [[binding]])

    assert binding.range_status == "unknown"
    assert draw.asset_texture_defaults == {}


def test_slot_role_hash_conflict_does_not_assign_asset_role(tmp_path):
    root = os.path.normcase(os.path.abspath(str(tmp_path / "assets")))
    asset_dir = tmp_path / "assets" / "Asset01"
    asset_dir.mkdir(parents=True)
    (asset_dir / "Asset01BodyBNormalMap.dds").write_bytes(b"normal")
    metadata = asset_dir / "hash.json"
    metadata.write_text(json.dumps([{
        "ib": "10101010", "object_indexes": [12],
        "object_classifications": ["B"],
        "texture_hashes": [[
            ["NormalMap", ".dds", "22222222"],
        ]],
    }]), encoding="utf-8")
    draw = DrawCall(
        texture_default_file="mod-diffuse.dds",
        texture_provenance={"diffuse": "mod_slot_semantic"},
        slot_textures=[SlotTextureBinding(
            slot=0, resource="ResourceOpaque", file="mod-diffuse.dds",
            texture_hashes=("22222222",), role_hint="diffuse")])
    binding = AssetComponentBinding(
        status="exact", asset_type="GIMI", asset="Asset01", root=root,
        component_status="exact", range_status="exact",
        geometry_hash="10101010", component_name="Body",
        classification="B", first_index=12,
        metadata="Asset01/hash.json")

    apply([{"draws": [draw]}], [[binding]])

    assert draw.texture_default("diffuse") == "mod-diffuse.dds"
    assert draw.texture_default("normal_map") is None
    assert draw.asset_texture_defaults == {}
    assert draw.asset_slot_evidence == [{
        "resource": "ResourceOpaque", "slot": 0,
        "texture_hash": "22222222", "role": "diffuse",
        "role_source": "mod_slot_mapping",
        "asset_hash_role": "normal_map", "conflict": True,
    }]


def test_wwmi_textureusage_does_not_trigger_dds_classification(
        tmp_path, monkeypatch):
    root = os.path.normcase(os.path.abspath(str(tmp_path / "assets")))
    mod_dir = tmp_path / "mod"
    texture = mod_dir / "textures" / "replacement.dds"
    texture.parent.mkdir(parents=True)
    texture.write_bytes(b"synthetic dds")
    asset_dir = tmp_path / "assets" / "Asset01"
    asset_dir.mkdir(parents=True)
    (asset_dir / "TextureUsage.json").write_text(json.dumps({
        "Component 1": {
            "ps-t3": ["d1d1d1d1-vs=aaaaaaaa-ps=bbbbbbbb"],
        },
    }), encoding="utf-8")
    replacement = TextureReplacement(
        "d1d1d1d1", "ResourceTexture0", (), "TextureOverrideTexture0",
        "textures/replacement.dds")
    index = TextureOverrideIndex(
        replacements_by_hash={"d1d1d1d1": (replacement,)})
    calls = []

    def classify(path):
        calls.append(path)
        return dds_classifier.DDSClassification(
            "diffuse", "color", "high", ("test",))

    monkeypatch.setattr("app.assets.enrichment.classify_dds", classify)
    binding = AssetComponentBinding(
        status="exact", asset_type="WWMI", asset="Asset01", root=root,
        component_status="exact", range_status="exact",
        geometry_hash="10101010", component_ordinal=1,
        detail_metadata="Asset01/TextureUsage.json")
    cache = {}

    first = DrawCall()
    apply([{"draws": [first]}], [[binding]], texture_index=index,
         mod_dir=str(mod_dir), dds_classification_cache=cache)
    second = DrawCall()
    apply([{"draws": [second]}], [[binding]], texture_index=index,
         mod_dir=str(mod_dir), dds_classification_cache=cache)

    assert calls == []
    assert first.texture_default("diffuse") is None
    assert second.texture_default("diffuse") is None


def test_roleless_gimi_hash_uses_generic_dds_fallback(tmp_path, monkeypatch):
    root = os.path.normcase(os.path.abspath(str(tmp_path / "assets")))
    mod_dir = tmp_path / "mod"
    replacement_file = mod_dir / "textures" / "replacement.dds"
    replacement_file.parent.mkdir(parents=True)
    replacement_file.write_bytes(b"replacement")
    asset_dir = tmp_path / "assets" / "Asset01"
    asset_dir.mkdir(parents=True)
    (asset_dir / "hash.json").write_text(json.dumps([{
        "ib": "10101010", "object_indexes": [12],
        "texture_hashes": [[
            ["UnknownUsage", ".dds", "11111111"],
        ]],
    }]), encoding="utf-8")
    replacement = TextureReplacement(
        "11111111", "ResourceTexture0", (), "TextureOverrideTexture0",
        "textures/replacement.dds")
    index = TextureOverrideIndex(
        replacements_by_hash={"11111111": (replacement,)})
    monkeypatch.setattr(
        "app.assets.enrichment.classify_dds",
        lambda _path: dds_classifier.DDSClassification(
            None, "effect", "medium", ("synthetic_color",),
            color_score=0.5))
    draw = DrawCall()
    binding = AssetComponentBinding(
        status="exact", asset_type="GIMI", asset="Asset01", root=root,
        component_status="exact", range_status="exact",
        geometry_hash="10101010", component_name="Body",
        first_index=12, metadata="Asset01/hash.json")

    apply([{"draws": [draw]}], [[binding]], texture_index=index,
         mod_dir=str(mod_dir))

    assert draw.texture_default("diffuse") == "textures/replacement.dds"
    assert draw.asset_slot_evidence[0]["role_source"] == "dds_analysis"


def test_wwmi_replacements_use_component_local_dds_roles(tmp_path, monkeypatch):
    root = os.path.normcase(os.path.abspath(str(tmp_path / "assets")))
    mod_dir = tmp_path / "mod"
    diffuse_file = mod_dir / "diffuse.dds"
    normal_file = mod_dir / "normal.dds"
    mod_dir.mkdir()
    diffuse_file.write_bytes(b"diffuse")
    normal_file.write_bytes(b"normal")
    asset_dir = tmp_path / "assets" / "Asset01"
    asset_dir.mkdir(parents=True)
    (asset_dir / "TextureUsage.json").write_text(json.dumps({
        "Component 1": {
            "ps-t0": ["11111111-vs=aaaaaaaa-ps=bbbbbbbb"],
            "ps-t1": ["22222222-vs=aaaaaaaa-ps=bbbbbbbb"],
        },
    }), encoding="utf-8")
    index = TextureOverrideIndex(replacements_by_hash={
        "11111111": (TextureReplacement(
            "11111111", "ResourceDiffuse", (), "TextureOverrideDiffuse",
            "diffuse.dds"),),
        "22222222": (TextureReplacement(
            "22222222", "ResourceNormal", (), "TextureOverrideNormal",
            "normal.dds"),),
    })

    def classify(path):
        role = "normal_map" if os.path.basename(path) == "normal.dds" \
            else "diffuse"
        return dds_classifier.DDSClassification(
            role, "packed_normal" if role == "normal_map" else "color",
            "high", ("synthetic",))

    monkeypatch.setattr("app.assets.enrichment.classify_dds", classify)
    draw = DrawCall()
    binding = AssetComponentBinding(
        status="exact", asset_type="WWMI", asset="Asset01", root=root,
        component_status="exact", range_status="exact",
        geometry_hash="10101010", component_ordinal=1,
        detail_metadata="Asset01/TextureUsage.json")

    apply([{"draws": [draw]}], [[binding]], texture_index=index,
         mod_dir=str(mod_dir))

    assert draw.texture_default("diffuse") is None
    assert draw.texture_default("normal_map") is None
    assert {item["texture_hash"] for item in draw.asset_slot_evidence} == {
        "11111111", "22222222"}


def test_asset_fallback_uses_trusted_source_and_keeps_diagnostic(tmp_path):
    (tmp_path / "position.buf").write_bytes(struct.pack(
        "<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0))
    (tmp_path / "texcoord.buf").write_bytes(struct.pack(
        "<6f", 0, 0, 1, 0, 0, 1))
    (tmp_path / "index.buf").write_bytes(struct.pack("<3I", 0, 1, 2))
    asset_texture = tmp_path / "asset-root" / "Asset01Diffuse.dds"
    asset_texture.parent.mkdir()
    asset_texture.write_bytes(b"asset texture")
    draw = DrawCall(
        label="Body-1", count=3, start=0, base=0,
        position_file="position.buf", position_stride=12,
        texcoord_file="texcoord.buf", texcoord_stride=8,
        ib_file="index.buf", index_size=4,
        asset_texture_defaults={"diffuse": {
            "path": str(asset_texture), "key": "asset/root/Asset01Diffuse.dds"}},
        asset_binding=AssetComponentBinding(
            status="exact", asset_type="GIMI", asset="Asset01",
            geometry_hash="10101010", classification="B"),
        texture_provenance={"diffuse": "asset_original_fallback"},
    )

    built = build_mesh_result(
        [{"name": "Body", "display_name": "Body",
          "position_file": "position.buf", "position_stride": 12,
          "texcoord_file": "texcoord.buf", "texcoord_stride": 8,
          "ib_file": "index.buf", "index_size": 4, "draws": [draw]}],
        str(tmp_path), geometry=GeometryBlob(),
        texture_source=lambda path, role: f"uri:{role}:{path}")

    entry = built.meshes["Body-1"]
    assert entry["tex_key"] == "diffuse::asset/root/Asset01Diffuse.dds"
    assert built.textures[entry["tex_key"]].startswith("uri:diffuse:")
    assert entry["asset_binding"]["status"] == "exact"
    assert entry["texture_resolution"] == {
        "diffuse": "asset_original_fallback"}


def test_asset_resolution_summary_aggregates_draws_without_changing_identity():
    groups = [{"name": "Body", "display_name": "Body", "draws": [None, None, None]}]
    bindings = [[
        AssetComponentBinding(
            status="exact", component_status="exact", range_status="exact",
            asset_type="GIMI", asset="Asset01", component_name="Body",
            classification="A", first_index=10, index_count=20),
        AssetComponentBinding(
            status="exact", component_status="exact", range_status="exact",
            asset_type="GIMI", asset="Asset01", component_name="Body",
            classification="B", first_index=30, index_count=40),
        AssetComponentBinding(
            status="not_found", component_status="not_found",
            range_status="unknown", asset_type="GIMI"),
    ]]

    summary = summarize_groups(
        groups, bindings,
        {"configured_roots": 1, "ready_roots": 1,
         "unavailable_roots": 0}).to_dict()

    assert summary["exact_draws"] == 2
    assert summary["unmatched_draws"] == 1
    assert summary["assets"] == ["Asset01"]
    assert summary["components"] == [{
        "mod_component": "Body", "status": "partial",
        "asset": "Asset01", "component": "Body", "draws": 3,
        "exact_draws": 2, "partial_draws": 0, "ambiguous_draws": 0,
        "unmatched_draws": 1, "ranges_vary": True,
    }]


@pytest.mark.parametrize("ready_roots, index_status", [
    (0, "unavailable"), (1, "partial"),
])
def test_asset_summary_does_not_count_unavailable_coverage_as_unmatched(
        ready_roots, index_status):
    bindings = [AssetComponentBinding(
        status="not_found", component_status="not_found",
        range_status="unknown", asset_type="GIMI")]
    if ready_roots:
        bindings.insert(0, AssetComponentBinding(
            status="exact", component_status="exact", range_status="exact",
            asset_type="GIMI", asset="Asset01", component_name="Body",
            first_index=10, index_count=20))
    summary = summarize_groups(
        [{"name": "Body", "draws": [None] * len(bindings)}], [bindings],
        {"configured_roots": ready_roots + 1, "ready_roots": ready_roots,
         "unavailable_roots": 1}).to_dict()
    assert summary["index_status"] == index_status
    assert summary["index_unavailable_draws"] == 1
    assert summary["unmatched_draws"] == 0
    if ready_roots:
        assert summary["exact_draws"] == 1
        assert summary["components"][0]["ranges_vary"] is False


def test_not_found_binding_is_published_only_after_a_ready_index_query():
    draw = DrawCall(label="Body-1")
    groups = [{"draws": [draw]}]
    binding = AssetComponentBinding(
        status="not_found", component_status="not_found",
        range_status="unknown", asset_type="GIMI")

    apply(groups, [[binding]])
    assert draw.asset_binding is None
    apply(groups, [[binding]], include_not_found=True)
    assert draw.asset_binding is binding


def test_slot_hash_role_precedence_lifecycle(tmp_path):
    asset = tmp_path / "asset-01"
    asset.mkdir()
    (asset / "hash.json").write_text(json.dumps([{
        "ib": "10101010", "object_indexes": [12],
        "texture_hashes": [[["Diffuse", ".dds", "11111111"]]],
    }]), encoding="utf-8")
    texture = tmp_path / "texture-01.dds"
    texture.write_bytes(b"fixture-01")
    binding = AssetComponentBinding(
        status="exact", component_status="exact", range_status="exact",
        asset_type="GIMI", asset="asset-01", root=str(tmp_path),
        geometry_hash="10101010", first_index=12, metadata="asset-01/hash.json")
    draw = DrawCall(slot_textures=[SlotTextureBinding(
        7, "ResourceTexture01", str(texture), ("11111111",))])
    apply([{"draws": [draw]}], [[binding]])
    assert draw.texture_default("diffuse") == str(texture)
    assert draw.texture_provenance["diffuse"] == "mod_texture_hash"
    draw.texture_default_file = "authored.dds"
    draw.texture_provenance = {"diffuse": "mod_semantic"}
    apply([{"draws": [draw]}], [[binding]])
    assert draw.texture_default("diffuse") == "authored.dds"
    assert draw.texture_provenance["diffuse"] == "mod_semantic"
    assert draw.asset_texture_defaults == {}

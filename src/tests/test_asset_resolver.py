import json
import os
import struct

from app import asset_index
from app.asset_enrichment import apply
from app.asset_resolver import (AssetComponentBinding, resolve_component,
                                resolve_groups, summarize_groups)
from core.draw_call import DrawCall, SlotTextureBinding
from core.geometry_identity import GeometryMatch, normalize_geometry_hash
from core import wwmi_texture_roles
from core.ini_parser import (TextureOverrideIndex, TextureReplacement,
                              _scan_sections_for_draws, build_draw_groups,
                              extract_resources, parse_sections)
from core.mesh_builder import (GeometryBlob, build_mesh_result,
                               build_mesh_semantics)


def _index(root, asset_type="GIMI", metadata=None, *, asset="Alice",
           first_index=43845, classification="B"):
    metadata = metadata or f"{asset}/hash.json"
    return {
        "version": 1,
        "type": asset_type,
        "root": root,
        "builtAt": "2026-01-01T00:00:00Z",
        "stats": {
            "assetCount": 1, "geometryRecordCount": 1,
            "geometryHashCount": 1, "skippedCount": 0,
        },
        "assets": [{"path": asset, "geometry": [{
            "hash": "73c8cae2",
            "ranges": [{
                "firstIndex": first_index, "indexCount": None,
                "classification": classification, "componentOrdinal": 1,
            }],
            "metadata": metadata,
            "componentName": "Body",
        }]}],
        "byGeometryHash": {"73c8cae2": [{"asset": 0, "geometry": 0}]},
    }


def test_geometry_identity_is_shared_and_draw_scan_keeps_raw_evidence():
    assert normalize_geometry_hash("0X73C8CAE2") == "73c8cae2"
    sections = parse_sections(
        "fixture.ini", "[TextureOverrideBody]\n"
        "hash = 0x73c8cae2\n"
        "match_first_index = 43845\n"
        "match_index_count = 24\n"
        "Resource\\GIMI\\Diffuse = ResourceDiffuseOpaque\n"
        "ps-t1 = ResourceMystery\n"
        "drawindexed = 3, 0, 0\n"
        "[TextureOverrideDiffuse]\n"
        "hash = 11111111\n"
        "this = ResourceDiffuseOpaque\n"
        "[TextureOverrideMystery]\n"
        "hash = 22222222\n"
        "this = ResourceMystery\n")

    draw = _scan_sections_for_draws(sections)["TextureOverrideBody"]["draws"][0]

    assert draw.geometry_match == GeometryMatch("73c8cae2", 43845, 24)
    assert draw.slot_textures == [SlotTextureBinding(
        1, "ResourceMystery", texture_hashes=("22222222",))]
    assert draw.diffuse_variants[0]["texture_hashes"] == ("11111111",)


def test_resource_name_hash_is_not_texture_evidence():
    sections = parse_sections(
        "fixture.ini", "[TextureOverrideBody]\n"
        "hash = 73c8cae2\n"
        "Resource\\GIMI\\Diffuse = ResourceFoo_11111111\n"
        "drawindexed = 3, 0, 0\n")

    draw = _scan_sections_for_draws(sections)["TextureOverrideBody"][
        "draws"][0]

    assert draw.slot_textures == []
    assert "texture_hashes" not in draw.diffuse_variants[0]


def test_texture_hash_maps_all_conditional_this_resources():
    sections = parse_sections(
        "fixture.ini", "[TextureOverrideBody]\n"
        "hash = 73c8cae2\n"
        "ps-t1 = ResourceA\n"
        "Resource\\GIMI\\Diffuse = ResourceB\n"
        "drawindexed = 3, 0, 0\n"
        "[TextureOverrideOriginalTexture]\n"
        "hash = 11111111\n"
        "if $toggle == 0\n"
        "this = ResourceA\n"
        "else\n"
        "this = ResourceB\n"
        "endif\n")

    draw = _scan_sections_for_draws(sections)["TextureOverrideBody"][
        "draws"][0]

    assert draw.slot_textures == [SlotTextureBinding(
        1, "ResourceA", texture_hashes=("11111111",))]
    assert draw.diffuse_variants[0]["texture_hashes"] == ("11111111",)


def test_texture_override_index_preserves_conditional_replacements():
    sections = parse_sections(
        "fixture.ini", "[KeyPanties]\n"
        "type = cycle\n"
        "$Panties = 0,1\n"
        "[TextureOverrideAstraLegDiffuse]\n"
        "hash = 11111111\n"
        "if $Panties == 0\n"
        "this = ResourceAstraLegADiffuse\n"
        "else\n"
        "this = ResourceAstraLegADiffuseNSFW\n"
        "endif\n")

    index = _scan_sections_for_draws(sections).texture_override_index
    replacements = index.replacements_by_hash["11111111"]

    assert [(item.resource, item.dnf) for item in replacements] == [
        ("ResourceAstraLegADiffuse", [[{
            "var": "Panties", "value": "0", "negate": False}]]),
        ("ResourceAstraLegADiffuseNSFW", [[{
            "var": "Panties", "value": "0", "negate": True}]])]


def test_draw_group_index_resolves_replacement_resource_file():
    sections = parse_sections(
        "fixture.ini", "[TextureOverrideBody]\n"
        "vb0 = ResourcePosition\n"
        "vb1 = ResourceTexcoord\n"
        "ib = ResourceBodyIB\n"
        "drawindexed = 3, 0, 0\n"
        "[TextureOverrideOriginal]\n"
        "hash = 11111111\n"
        "this = ResourceAstraDiffuse\n"
        "[ResourcePosition]\n"
        "filename = position.buf\n"
        "stride = 40\n"
        "[ResourceTexcoord]\n"
        "filename = texcoord.buf\n"
        "stride = 20\n"
        "[ResourceBodyIB]\n"
        "filename = body.ib\n"
        "format = DXGI_FORMAT_R32_UINT\n"
        "[ResourceAstraDiffuse]\n"
        "filename = textures/astra-diffuse.dds\n")

    groups = build_draw_groups(sections, extract_resources(sections))
    index = groups[0]["_texture_override_index"]

    assert index.replacements_by_hash["11111111"][0].file == \
        "textures/astra-diffuse.dds"


def test_asset_hash_applies_conditional_mod_replacement(tmp_path):
    root = os.path.normcase(os.path.abspath(str(tmp_path / "assets")))
    asset_dir = tmp_path / "assets" / "Alice"
    asset_dir.mkdir(parents=True)
    (asset_dir / "hash.json").write_text(json.dumps([{
        "ib": "73c8cae2", "object_indexes": [43845],
        "object_classifications": ["B"],
        "texture_hashes": [[
            ["Diffuse", ".dds", "11111111"],
        ]],
    }]), encoding="utf-8")
    replacement = TextureReplacement.from_dnf(
        "11111111", "ResourceAstraDiffuse", [[{
            "var": "style", "value": "1", "negate": False}]],
        "TextureOverrideDiffuse")
    replacement = TextureReplacement(
        replacement.original_hash, replacement.resource, replacement.conditions,
        replacement.source_section, "AstraDiffuse.dds")
    index = TextureOverrideIndex(
        replacements_by_hash={"11111111": (replacement,)})
    draw = DrawCall()
    binding = AssetComponentBinding(
        status="exact", asset_type="GIMI", asset="Alice", root=root,
        component_status="exact", range_status="exact",
        geometry_hash="73c8cae2", component_name="Body",
        classification="B", first_index=43845,
        metadata="Alice/hash.json")

    apply([{"draws": [draw]}], [[binding]], texture_index=index)

    assert draw.texture_rules("diffuse") == [{
        "conditions": [[{
            "var": "style", "value": "1", "negate": False}]],
        "file": "AstraDiffuse.dds",
        "texture_hashes": ("11111111",),
    }]
    assert draw.texture_provenance == {"diffuse": "mod_texture_hash"}
    assert draw.texture_hashes["diffuse"] == ["11111111"]


def test_shared_asset_hash_does_not_apply_another_component_replacement(
        tmp_path):
    root = os.path.normcase(os.path.abspath(str(tmp_path / "assets")))
    asset_dir = tmp_path / "assets" / "Alice"
    asset_dir.mkdir(parents=True)
    (asset_dir / "AliceHairADiffuse.dds").write_bytes(b"asset diffuse")
    (asset_dir / "hash.json").write_text(json.dumps([{
        "ib": "73c8cae2", "object_indexes": [0],
        "texture_hashes": [[[
            "Diffuse", ".dds", "11111111",
        ]]],
    }]), encoding="utf-8")
    replacement = TextureReplacement(
        "11111111", "ResourceAliceBodyDiffuse", (),
        "TextureOverrideAliceBodyDiffuse", "AliceBodyDiffuse.dds")
    index = TextureOverrideIndex(
        replacements_by_hash={"11111111": (replacement,)})
    draw = DrawCall(label="AliceHairA-1")
    binding = AssetComponentBinding(
        status="exact", asset_type="GIMI", asset="Alice", root=root,
        component_status="exact", range_status="exact",
        geometry_hash="73c8cae2", first_index=0,
        metadata="Alice/hash.json")

    apply([{"draws": [draw]}], [[binding]], texture_index=index)

    assert draw.texture_default("diffuse") is None
    assert draw.texture_rules("diffuse") == []
    assert draw.asset_texture_defaults["diffuse"]["path"].endswith(
        "AliceHairADiffuse.dds")


def test_resolver_uses_enabled_indexes_and_range_evidence(tmp_path, monkeypatch):
    root = os.path.normcase(os.path.abspath(str(tmp_path / "assets")))
    entries = [{"type": "GIMI", "path": root, "enabled": True}]
    monkeypatch.setattr(
        asset_index, "load_index", lambda asset_type, path: _index(root))

    binding = resolve_component(
        GeometryMatch("73c8cae2", 43845, 24), "genshin", entries)

    assert binding.status == "exact"
    assert binding.asset == "Alice"
    assert binding.component_name == "Body"
    assert binding.classification == "B"

    entries[0]["enabled"] = False
    assert resolve_component(
        GeometryMatch("73c8cae2", 43845, 24), "genshin", entries).status == \
        "not_found"


def test_resolver_marks_duplicate_enabled_roots_ambiguous(tmp_path, monkeypatch):
    roots = [os.path.normcase(os.path.abspath(str(tmp_path / name)))
             for name in ("one", "two")]
    entries = [{"type": "GIMI", "path": root, "enabled": True}
               for root in roots]
    monkeypatch.setattr(
        asset_index, "load_index", lambda asset_type, path: _index(path))

    binding = resolve_component(
        GeometryMatch("73c8cae2", 43845, None), "genshin", entries)

    assert binding.status == "ambiguous"
    assert binding.geometry_hash == "73c8cae2"


def test_unknown_game_uses_one_exact_match_from_any_asset_type(
        tmp_path, monkeypatch):
    root = os.path.normcase(os.path.abspath(str(tmp_path / "zzmi")))
    entries = [{"type": "ZZMI", "path": root, "enabled": True}]
    monkeypatch.setattr(
        asset_index, "load_index",
        lambda asset_type, path: _index(path, asset_type=asset_type))

    binding = resolve_component(
        GeometryMatch("73c8cae2", 43845, 24), "unknown", entries)

    assert binding.status == "exact"
    assert binding.asset_type == "ZZMI"


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
        GeometryMatch("73c8cae2", 43845, 24), "unknown", entries)

    assert binding.status == "ambiguous"
    assert binding.asset_type is None


def test_unknown_game_hash_only_match_is_not_bound_for_enrichment(
        tmp_path, monkeypatch):
    root = os.path.normcase(os.path.abspath(str(tmp_path / "zzmi")))
    entries = [{"type": "ZZMI", "path": root, "enabled": True}]
    monkeypatch.setattr(
        asset_index, "load_index",
        lambda asset_type, path: _index(path, asset_type=asset_type))

    binding = resolve_component(
        GeometryMatch("73c8cae2"), "unknown", entries)

    assert binding.status == "not_found"


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
        GeometryMatch("73c8cae2", 43845, 24), "genshin", entries)

    assert binding.status == "not_found"
    assert calls == []


def test_range_evidence_disambiguates_same_hash_candidates(tmp_path, monkeypatch):
    roots = [os.path.normcase(os.path.abspath(str(tmp_path / name)))
             for name in ("one", "two")]
    entries = [{"type": "GIMI", "path": root, "enabled": True}
               for root in roots]

    def load_index(asset_type, path):
        return _index(
            path, asset="AssetA" if path == roots[0] else "AssetB",
            first_index=0 if path == roots[0] else 43845)

    monkeypatch.setattr(asset_index, "load_index", load_index)

    binding = resolve_component(
        GeometryMatch("73c8cae2", 43845), "genshin", entries)

    assert binding.status == "exact"
    assert binding.asset == "AssetB"
    assert binding.range_status == "exact"


def test_same_hash_same_range_remains_ambiguous(tmp_path, monkeypatch):
    roots = [os.path.normcase(os.path.abspath(str(tmp_path / name)))
             for name in ("one", "two")]
    entries = [{"type": "GIMI", "path": root, "enabled": True}
               for root in roots]
    monkeypatch.setattr(
        asset_index, "load_index", lambda asset_type, path: _index(path))

    binding = resolve_component(
        GeometryMatch("73c8cae2", 43845), "genshin", entries)

    assert binding.status == "ambiguous"
    assert binding.range_status == "ambiguous"


def test_same_hash_without_range_evidence_remains_ambiguous(tmp_path,
                                                            monkeypatch):
    roots = [os.path.normcase(os.path.abspath(str(tmp_path / name)))
             for name in ("one", "two")]
    entries = [{"type": "GIMI", "path": root, "enabled": True}
               for root in roots]
    monkeypatch.setattr(
        asset_index, "load_index", lambda asset_type, path: _index(path))

    binding = resolve_component(
        GeometryMatch("73c8cae2"), "genshin", entries)

    assert binding.status == "ambiguous"
    assert binding.range_status == "unknown"


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
        DrawCall(geometry_match=GeometryMatch("73c8cae2"))
        for _ in range(10)
    ]}]

    resolve_groups(groups, "genshin", entries)

    assert calls == roots


def test_unknown_group_reuses_unique_asset_identity_for_ambiguous_draws(
        tmp_path, monkeypatch):
    root = os.path.normcase(os.path.abspath(str(tmp_path / "zzmi")))
    entries = [{"type": "ZZMI", "path": root, "enabled": True}]
    index = _index(root, asset_type="ZZMI", first_index=43845)
    index["assets"][0]["geometry"].append({
        "hash": "73c8cae2",
        "ranges": [{"firstIndex": 0, "indexCount": None}],
        "metadata": "Alice/hash.json",
    })
    index["assets"].append({
        "path": "AliceChandelier",
        "geometry": [{
            "hash": "73c8cae2",
            "ranges": [{"firstIndex": 0, "indexCount": None}],
            "metadata": "AliceChandelier/hash.json",
        }],
    })
    index["byGeometryHash"]["73c8cae2"] = [
        {"asset": 0, "geometry": 0},
        {"asset": 0, "geometry": 1},
        {"asset": 1, "geometry": 0},
    ]
    monkeypatch.setattr(asset_index, "load_index",
                        lambda asset_type, path: index)
    groups = [{"draws": [
        DrawCall(geometry_match=GeometryMatch("73c8cae2", 43845, 24)),
        DrawCall(geometry_match=GeometryMatch("73c8cae2", 0, 24)),
    ]}]

    bindings = resolve_groups(groups, "unknown", entries)

    assert [item.status for item in bindings[0]] == ["exact", "exact"]
    assert [item.asset for item in bindings[0]] == ["Alice", "Alice"]
    assert [item.asset_type for item in bindings[0]] == ["ZZMI", "ZZMI"]


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
        [{"draws": [DrawCall(geometry_match=GeometryMatch("73c8cae2"))]}],
        "genshin", entries, availability=availability)

    assert availability == {
        "asset_type": "GIMI", "configured_roots": 2,
        "ready_roots": 1, "unavailable_roots": 1,
    }


def test_semantic_refresh_publishes_asset_diagnostics_without_render_fields(
        tmp_path):
    draw = DrawCall(
        label="Body-1",
        asset_binding=AssetComponentBinding(
            status="exact", component_status="exact", range_status="exact",
            asset_type="GIMI", asset="Alice", component_name="Body"),
        texture_provenance={"diffuse": "mod_semantic"},
        asset_slot_evidence=[{"resource": "ps-t1"}],
    )

    result = build_mesh_semantics(
        [{"draws": [draw]}], str(tmp_path), active_mesh_keys={"Body-1"})

    assert result["Body-1"]["asset_binding"]["asset"] == "Alice"
    assert result["Body-1"]["texture_resolution"] == {
        "diffuse": "mod_semantic"}
    assert result["Body-1"]["asset_slot_evidence"] == [{
        "resource": "ps-t1"}]
    assert result["Body-1"]["conditions"] == []


def test_asset_original_fallback_fills_only_missing_roles(tmp_path):
    root = os.path.normcase(os.path.abspath(str(tmp_path / "assets")))
    asset_dir = tmp_path / "assets" / "Alice"
    asset_dir.mkdir(parents=True)
    (asset_dir / "AliceBodyBDiffuse.dds").write_bytes(b"diffuse")
    (asset_dir / "AliceBodyBNormalMap.dds").write_bytes(b"normal")
    metadata = asset_dir / "hash.json"
    metadata.write_text(json.dumps([{
        "ib": "73c8cae2",
        "object_indexes": [43845],
        "object_classifications": ["B"],
        "texture_hashes": [[
            ["Diffuse", ".dds", "11111111"],
            ["NormalMap", ".dds", "22222222"],
        ]],
    }]), encoding="utf-8")
    draw = DrawCall(
        geometry_match=GeometryMatch("73c8cae2", 43845, None),
        texture_default_file="mod-diffuse.dds",
        slot_textures=[SlotTextureBinding(1, "ResourceMystery")],
    )
    binding = AssetComponentBinding(
        status="exact", asset_type="GIMI", asset="Alice", root=root,
        component_status="exact", range_status="exact",
        geometry_hash="73c8cae2", component_name="Body",
        classification="B", first_index=43845,
        metadata="Alice/hash.json",
    )

    apply([{"draws": [draw]}], [[binding]])

    assert draw.asset_texture_defaults["normal_map"]["path"].endswith(
        "AliceBodyBNormalMap.dds")
    assert "diffuse" not in draw.asset_texture_defaults
    assert draw.texture_provenance == {
        "diffuse": "mod_semantic",
        "normal_map": "asset_original_fallback",
    }


def test_asset_locator_uses_component_and_classification(tmp_path):
    root = os.path.normcase(os.path.abspath(str(tmp_path / "assets")))
    asset_dir = tmp_path / "assets" / "Alice"
    asset_dir.mkdir(parents=True)
    (asset_dir / "AliceBodyADiffuse.dds").write_bytes(b"A")
    (asset_dir / "AliceBodyBDiffuse.dds").write_bytes(b"B")
    metadata = asset_dir / "hash.json"
    metadata.write_text(json.dumps([{
        "ib": "73c8cae2", "object_indexes": [43845],
        "object_classifications": ["B"],
        "texture_hashes": [[
            ["Diffuse", ".dds", "11111111"],
        ]],
    }]), encoding="utf-8")
    binding = AssetComponentBinding(
        status="exact", component_status="exact", range_status="exact",
        asset_type="GIMI", asset="Alice", root=root,
        geometry_hash="73c8cae2", component_name="Body",
        classification="B", first_index=43845,
        metadata="Alice/hash.json")

    draw = DrawCall()
    apply([{"draws": [draw]}], [[binding]])
    assert draw.asset_texture_defaults["diffuse"]["path"].endswith(
        "AliceBodyBDiffuse.dds")

    (asset_dir / "AliceBodyBDiffuse.dds").unlink()
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
        GeometryMatch("73c8cae2"), "genshin", entries)

    assert binding.status == "exact"
    assert binding.component_status == "exact"
    assert binding.range_status == "unknown"
    assert binding.component_name == "Body"


def test_hash_only_component_does_not_enable_object_texture_fallback(tmp_path,
                                                                     monkeypatch):
    root = os.path.normcase(os.path.abspath(str(tmp_path / "assets")))
    asset_dir = tmp_path / "assets" / "Alice"
    asset_dir.mkdir(parents=True)
    (asset_dir / "AliceBodyADiffuse.dds").write_bytes(b"A")
    (asset_dir / "AliceBodyBDiffuse.dds").write_bytes(b"B")
    (asset_dir / "hash.json").write_text(json.dumps([{
        "ib": "73c8cae2", "object_indexes": [1, 43845],
        "object_classifications": ["A", "B"],
        "texture_hashes": [
            [["Diffuse", ".dds", "11111111"]],
            [["Diffuse", ".dds", "22222222"]],
        ],
    }]), encoding="utf-8")
    entries = [{"type": "GIMI", "path": root, "enabled": True}]
    monkeypatch.setattr(
        asset_index, "load_index", lambda asset_type, path: _index(root))

    binding = resolve_component(GeometryMatch("73c8cae2"), "genshin",
                                entries)
    draw = DrawCall()
    apply([{"draws": [draw]}], [[binding]])

    assert binding.range_status == "unknown"
    assert draw.asset_texture_defaults == {}


def test_explicit_mod_texture_hash_does_not_override_semantic_assignment(
        tmp_path):
    root = os.path.normcase(os.path.abspath(str(tmp_path / "assets")))
    asset_dir = tmp_path / "assets" / "Alice"
    asset_dir.mkdir(parents=True)
    (asset_dir / "AliceDiffuse.dds").write_bytes(b"diffuse")
    (asset_dir / "hash.json").write_text(json.dumps([{
        "ib": "73c8cae2", "object_indexes": [43845],
        "object_classifications": ["B"],
        "texture_hashes": [[
            ["Diffuse", ".dds", "11111111"],
        ]],
    }]), encoding="utf-8")
    draw = DrawCall(
        texture_default_file="mod-diffuse.dds",
        texture_hashes={"diffuse": ["11111111"]},
    )
    binding = AssetComponentBinding(
        status="exact", asset_type="GIMI", asset="Alice", root=root,
        component_status="exact", range_status="exact",
        geometry_hash="73c8cae2", component_name="Body",
        classification="B", first_index=43845,
        metadata="Alice/hash.json",
    )

    apply([{"draws": [draw]}], [[binding]])

    assert draw.asset_texture_defaults == {}
    assert draw.texture_provenance == {"diffuse": "mod_semantic"}


def test_component_local_slot_hash_resolves_role_without_global_slot_guess(
        tmp_path):
    root = os.path.normcase(os.path.abspath(str(tmp_path / "assets")))
    asset_dir = tmp_path / "assets" / "Alice"
    asset_dir.mkdir(parents=True)
    mod_texture = tmp_path / "mod-diffuse.dds"
    mod_texture.write_bytes(b"mod diffuse")
    (asset_dir / "AliceDiffuse.dds").write_bytes(b"original diffuse")
    (asset_dir / "hash.json").write_text(json.dumps([{
        "ib": "73c8cae2", "object_indexes": [43845],
        "object_classifications": ["B"],
        "texture_hashes": [[
            ["Diffuse", ".dds", "11111111"],
        ]],
    }]), encoding="utf-8")
    draw = DrawCall(slot_textures=[SlotTextureBinding(
        7, "ResourceMystery", str(mod_texture), ("11111111",))])
    binding = AssetComponentBinding(
        status="exact", asset_type="GIMI", asset="Alice", root=root,
        component_status="exact", range_status="exact",
        geometry_hash="73c8cae2", component_name="Body",
        classification="B", first_index=43845,
        metadata="Alice/hash.json")

    apply([{"draws": [draw]}], [[binding]])

    assert draw.texture_default("diffuse") == str(mod_texture)
    assert draw.texture_provenance == {"diffuse": "mod_texture_hash"}
    assert draw.asset_texture_defaults == {}


def test_slot_role_and_matching_asset_hash_keep_one_role(tmp_path):
    root = os.path.normcase(os.path.abspath(str(tmp_path / "assets")))
    asset_dir = tmp_path / "assets" / "Alice"
    asset_dir.mkdir(parents=True)
    metadata = asset_dir / "hash.json"
    metadata.write_text(json.dumps([{
        "ib": "73c8cae2", "object_indexes": [43845],
        "object_classifications": ["B"],
        "texture_hashes": [[
            ["Diffuse", ".dds", "11111111"],
        ]],
    }]), encoding="utf-8")
    mod_texture = tmp_path / "mod-diffuse.dds"
    mod_texture.write_bytes(b"mod diffuse")
    draw = DrawCall(slot_textures=[SlotTextureBinding(
        slot=0, resource="ResourceOpaque", file=str(mod_texture),
        texture_hashes=("11111111",), role_hint="diffuse")])
    binding = AssetComponentBinding(
        status="exact", asset_type="GIMI", asset="Alice", root=root,
        component_status="exact", range_status="exact",
        geometry_hash="73c8cae2", component_name="Body",
        classification="B", first_index=43845,
        metadata="Alice/hash.json")

    apply([{"draws": [draw]}], [[binding]])

    assert draw.texture_default("diffuse") == str(mod_texture)
    assert draw.texture_provenance == {"diffuse": "mod_slot_semantic"}
    assert draw.asset_texture_defaults == {}
    assert not [item for item in draw.asset_slot_evidence
                 if item.get("conflict")]


def test_slot_role_hash_conflict_does_not_assign_asset_role(tmp_path):
    root = os.path.normcase(os.path.abspath(str(tmp_path / "assets")))
    asset_dir = tmp_path / "assets" / "Alice"
    asset_dir.mkdir(parents=True)
    (asset_dir / "AliceBodyBNormalMap.dds").write_bytes(b"normal")
    metadata = asset_dir / "hash.json"
    metadata.write_text(json.dumps([{
        "ib": "73c8cae2", "object_indexes": [43845],
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
        status="exact", asset_type="GIMI", asset="Alice", root=root,
        component_status="exact", range_status="exact",
        geometry_hash="73c8cae2", component_name="Body",
        classification="B", first_index=43845,
        metadata="Alice/hash.json")

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


def test_legacy_slot_role_hash_conflict_preserves_legacy_source(tmp_path):
    root = os.path.normcase(os.path.abspath(str(tmp_path / "assets")))
    asset_dir = tmp_path / "assets" / "Alice"
    asset_dir.mkdir(parents=True)
    metadata = asset_dir / "hash.json"
    metadata.write_text(json.dumps([{
        "ib": "73c8cae2", "object_indexes": [43845],
        "object_classifications": ["B"],
        "texture_hashes": [[
            ["NormalMap", ".dds", "22222222"],
        ]],
    }]), encoding="utf-8")
    draw = DrawCall(
        texture_provenance={"diffuse": "mod_slot_legacy"},
        slot_textures=[SlotTextureBinding(
            slot=0, resource="ResourceBodyDiffuse.0",
            file="body-diffuse.dds", texture_hashes=("22222222",),
            role_hint="diffuse", role_hint_source="legacy_slot_mapping")])
    binding = AssetComponentBinding(
        status="exact", asset_type="GIMI", asset="Alice", root=root,
        component_status="exact", range_status="exact",
        geometry_hash="73c8cae2", component_name="Body",
        classification="B", first_index=43845,
        metadata="Alice/hash.json")

    apply([{"draws": [draw]}], [[binding]])

    assert draw.asset_slot_evidence == [{
        "resource": "ResourceBodyDiffuse.0", "slot": 0,
        "texture_hash": "22222222", "role": "diffuse",
        "role_source": "legacy_slot_mapping",
        "asset_hash_role": "normal_map", "conflict": True,
    }]


def test_wwmi_slot_context_is_retained_without_guessing_a_role(tmp_path):
    root = os.path.normcase(os.path.abspath(str(tmp_path / "assets")))
    asset_dir = tmp_path / "assets" / "Alice"
    asset_dir.mkdir(parents=True)
    detail = asset_dir / "TextureUsage.json"
    detail.write_text(json.dumps({"Component 1": {
        "ps-t1": ["11111111-vs=aaaaaaaa-ps=bbbbbbbb"],
    }}), encoding="utf-8")
    draw = DrawCall(
        slot_textures=[SlotTextureBinding(1, "ResourceMystery")])
    binding = AssetComponentBinding(
        status="exact", asset_type="WWMI", asset="Alice", root=root,
        component_status="exact", range_status="exact",
        geometry_hash="73c8cae2", component_ordinal=1,
        detail_metadata="Alice/TextureUsage.json",
    )

    apply([{"draws": [draw]}], [[binding]])

    assert draw.asset_slot_evidence == [{
        "resource": "ResourceMystery", "slot": 1,
        "texture_hash": "11111111", "vs_hash": "aaaaaaaa",
        "ps_hash": "bbbbbbbb",
    }]
    assert draw.asset_texture_defaults == {}


def test_verified_wwmi_profile_resolves_diffuse_and_normal_roles():
    assert wwmi_texture_roles.resolve_texture_role(
        vs_hash="dc8efba6073d61bf", ps_hash="1f0d1da54f8f19c2",
        slot=0) == "diffuse"
    assert wwmi_texture_roles.resolve_texture_role(
        vs_hash="f59379b10554d2ab", ps_hash="6d947d37ebbd2bae",
        slot=0) == "normal_map"
    assert wwmi_texture_roles.resolve_texture_role(
        vs_hash="dc8efba6073d61bf", ps_hash="1f0d1da54f8f19c2",
        slot=1) is None


def test_wwmi_slot_context_preserves_mod_role_hint(tmp_path):
    root = os.path.normcase(os.path.abspath(str(tmp_path / "assets")))
    asset_dir = tmp_path / "assets" / "Alice"
    asset_dir.mkdir(parents=True)
    detail = asset_dir / "TextureUsage.json"
    detail.write_text(json.dumps({"Component 1": {
        "ps-t1": ["11111111-vs=aaaaaaaa-ps=bbbbbbbb"],
    }}), encoding="utf-8")
    draw = DrawCall(slot_textures=[SlotTextureBinding(
        slot=1, resource="ResourceOpaque", role_hint="normal_map")])
    binding = AssetComponentBinding(
        status="exact", asset_type="WWMI", asset="Alice", root=root,
        component_status="exact", range_status="exact",
        geometry_hash="73c8cae2", component_ordinal=1,
        detail_metadata="Alice/TextureUsage.json",
    )

    apply([{"draws": [draw]}], [[binding]])

    assert draw.asset_slot_evidence == [{
        "resource": "ResourceOpaque", "slot": 1,
        "texture_hash": "11111111", "vs_hash": "aaaaaaaa",
        "ps_hash": "bbbbbbbb", "role": "normal_map",
        "role_source": "mod_slot_mapping",
    }]


def test_wwmi_hash_replacement_is_component_diagnostic_without_role_guess(
        tmp_path):
    root = os.path.normcase(os.path.abspath(str(tmp_path / "assets")))
    asset_dir = tmp_path / "assets" / "Alice"
    asset_dir.mkdir(parents=True)
    detail = asset_dir / "TextureUsage.json"
    detail.write_text(json.dumps({"Component 1": {
        "ps-t3": ["553ed32b-vs=aaaaaaaa-ps=bbbbbbbb"],
    }}), encoding="utf-8")
    replacement = TextureReplacement(
        "553ed32b", "ResourceTexture0", (), "TextureOverrideTexture0",
        "textures/texture0.dds")
    index = TextureOverrideIndex(
        replacements_by_hash={"553ed32b": (replacement,)})
    draw = DrawCall()
    binding = AssetComponentBinding(
        status="exact", asset_type="WWMI", asset="Alice", root=root,
        component_status="exact", range_status="exact",
        geometry_hash="73c8cae2", component_ordinal=1,
        detail_metadata="Alice/TextureUsage.json")

    apply([{"draws": [draw]}], [[binding]], texture_index=index)

    assert draw.asset_slot_evidence == [{
        "slot": 3, "texture_hash": "553ed32b",
        "vs_hash": "aaaaaaaa", "ps_hash": "bbbbbbbb",
        "replacement_resource": "ResourceTexture0",
        "replacement_file": "textures/texture0.dds",
        "replacement_conditions": [],
        "source": "mod_texture_hash",
    }]
    assert draw.texture_provenance == {}
    assert draw.asset_texture_defaults == {}


def test_verified_wwmi_shader_role_applies_hash_replacement(
        tmp_path, monkeypatch):
    root = os.path.normcase(os.path.abspath(str(tmp_path / "assets")))
    asset_dir = tmp_path / "assets" / "Alice"
    asset_dir.mkdir(parents=True)
    detail = asset_dir / "TextureUsage.json"
    detail.write_text(json.dumps({"Component 1": {
        "ps-t3": ["553ed32b-vs=aaaaaaaa-ps=bbbbbbbb"],
    }}), encoding="utf-8")
    replacement = TextureReplacement(
        "553ed32b", "ResourceTexture0", (), "TextureOverrideTexture0",
        "textures/texture0.dds")
    index = TextureOverrideIndex(
        replacements_by_hash={"553ed32b": (replacement,)})
    monkeypatch.setitem(
        wwmi_texture_roles._PROFILES,
        ("aaaaaaaa", "bbbbbbbb", 3), "diffuse")
    # WWMI's generated mod uses CheckTextureOverride for these resources;
    # there is no direct ps-t assignment in the draw section.
    draw = DrawCall()
    binding = AssetComponentBinding(
        status="exact", asset_type="WWMI", asset="Alice", root=root,
        component_status="exact", range_status="exact",
        geometry_hash="73c8cae2", component_ordinal=1,
        detail_metadata="Alice/TextureUsage.json")

    apply([{"draws": [draw]}], [[binding]], texture_index=index)

    assert draw.texture_default("diffuse") == "textures/texture0.dds"
    assert draw.texture_provenance == {"diffuse": "mod_texture_hash"}
    assert draw.asset_slot_evidence[0]["role"] == "diffuse"
    assert draw.asset_slot_evidence[0]["role_source"] == \
        "wwmi_shader_profile"


def test_asset_fallback_uses_trusted_source_and_keeps_diagnostic(tmp_path):
    (tmp_path / "position.buf").write_bytes(struct.pack(
        "<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0))
    (tmp_path / "texcoord.buf").write_bytes(struct.pack(
        "<6f", 0, 0, 1, 0, 0, 1))
    (tmp_path / "index.buf").write_bytes(struct.pack("<3I", 0, 1, 2))
    asset_texture = tmp_path / "asset-root" / "AliceDiffuse.dds"
    asset_texture.parent.mkdir()
    asset_texture.write_bytes(b"asset texture")
    draw = DrawCall(
        label="Body-1", count=3, start=0, base=0,
        position_file="position.buf", position_stride=12,
        texcoord_file="texcoord.buf", texcoord_stride=8,
        ib_file="index.buf", index_size=4,
        asset_texture_defaults={"diffuse": {
            "path": str(asset_texture), "key": "asset/root/AliceDiffuse.dds"}},
        asset_binding=AssetComponentBinding(
            status="exact", asset_type="GIMI", asset="Alice",
            geometry_hash="73c8cae2", classification="B"),
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
    assert entry["tex_key"] == "diffuse::asset/root/AliceDiffuse.dds"
    assert built.textures[entry["tex_key"]].startswith("uri:diffuse:")
    assert entry["asset_binding"]["status"] == "exact"
    assert entry["texture_resolution"] == {
        "diffuse": "asset_original_fallback"}


def test_asset_resolution_summary_aggregates_draws_without_changing_identity():
    groups = [{"name": "Body", "display_name": "Body", "draws": [None, None, None]}]
    bindings = [[
        AssetComponentBinding(
            status="exact", component_status="exact", range_status="exact",
            asset_type="GIMI", asset="Alice", component_name="Body",
            classification="A", first_index=10, index_count=20),
        AssetComponentBinding(
            status="exact", component_status="exact", range_status="exact",
            asset_type="GIMI", asset="Alice", component_name="Body",
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
    assert summary["assets"] == ["Alice"]
    assert summary["components"] == [{
        "mod_component": "Body", "status": "partial",
        "asset": "Alice", "component": "Body", "draws": 3,
        "exact_draws": 2, "partial_draws": 0, "ambiguous_draws": 0,
        "unmatched_draws": 1, "ranges_vary": True,
    }]


def test_asset_resolution_summary_marks_missing_indexes_informational():
    groups = [{"name": "Body", "draws": [None]}]
    bindings = [[AssetComponentBinding(
        status="not_found", component_status="not_found",
        range_status="unknown", asset_type="GIMI")]]

    summary = summarize_groups(
        groups, bindings,
        {"configured_roots": 1, "ready_roots": 0,
         "unavailable_roots": 1}).to_dict()

    assert summary["index_status"] == "unavailable"
    assert summary["index_unavailable_draws"] == 1
    assert summary["unmatched_draws"] == 0


def test_asset_resolution_summary_does_not_call_partial_coverage_unmatched():
    groups = [{"name": "Body", "draws": [None, None]}]
    bindings = [[
        AssetComponentBinding(
            status="exact", component_status="exact", range_status="exact",
            asset_type="GIMI", asset="Alice", component_name="Body",
            first_index=10, index_count=20),
        AssetComponentBinding(
            status="not_found", component_status="not_found",
            range_status="unknown", asset_type="GIMI"),
    ]]

    summary = summarize_groups(
        groups, bindings,
        {"configured_roots": 2, "ready_roots": 1,
         "unavailable_roots": 1}).to_dict()

    assert summary["index_status"] == "partial"
    assert summary["exact_draws"] == 1
    assert summary["unmatched_draws"] == 0
    assert summary["index_unavailable_draws"] == 1
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

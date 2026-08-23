import json
import os
import struct

from app import asset_index
from app.asset_enrichment import apply
from app.asset_resolver import AssetComponentBinding, resolve_component
from core.draw_call import DrawCall, SlotTextureBinding
from core.geometry_identity import GeometryMatch, normalize_geometry_hash
from core.ini_parser import _scan_sections_for_draws, parse_sections
from core.mesh_builder import GeometryBlob, build_mesh_result


def _index(root, asset_type="GIMI", metadata="Alice/hash.json"):
    return {
        "version": 1,
        "type": asset_type,
        "root": root,
        "builtAt": "2026-01-01T00:00:00Z",
        "stats": {
            "assetCount": 1, "geometryRecordCount": 1,
            "geometryHashCount": 1, "skippedCount": 0,
        },
        "assets": [{"path": "Alice", "geometry": [{
            "hash": "73c8cae2",
            "ranges": [{
                "firstIndex": 43845, "indexCount": None,
                "classification": "B", "componentOrdinal": 1,
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

import os
import struct
import json
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.mods import metadata as metadata
from app.mods.analysis import analyze_mod_inis, build_mod_ini_snapshot
from app.mods.enrichment import _apply_texture_enrichment
from app.mods.loader import ModLoadContext, load_mod
from app.assets import index as asset_index
from app.assets.textures import collect_texture_inventory
from app.assets.resolver import AssetComponentBinding
from app.assets.textures import asset_texture_key
from core.geometry.mesh_builder import GeometryBlob, build_mesh_result
from core.geometry.draw_call import DrawCall
from core.ini import draw_scan
from core.mod_discovery import discover_ini_paths
from core.mod_source import DirectoryModSource, ZipModSource
from tests.support.model_data import standard_component_resources
from core.materials.game_profile import GameDetection


def _write_geometry(root):
    (root / "p.buf").write_bytes(struct.pack(
        "<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0))
    (root / "t.buf").write_bytes(struct.pack(
        "<6f", 0, 0, 1, 0, 0, 1))
    (root / "i.buf").write_bytes(struct.pack("<3I", 0, 1, 2))


@pytest.mark.parametrize("archived", [False, True], ids=["directory", "zip"])
def test_cross_ini_candidates_keep_branch_references_manual_and_source_local(
        tmp_path, monkeypatch, archived):
    named = "Textures/Components-2 t=aaaaaaaa.dds"
    foreign = "nested/Textures/Components-2 t=bbbbbbbb.dds"
    shared = "Textures/Components-0-2 t=shared.dds"
    root_ini = f"""[TextureOverrideComponent2]
hash = 12345678
ib = ResourceIB
vb0 = ResourcePosition
vb1 = ResourceTexcoord
Resource\\WWMI\\Diffuse = ref ResourceExisting
if ps-t0->format == 98
    ps-t0 = ref ResourceChoice
elif ps-t0->format == 99
    ps-t0 = ref ResourceAlternateChoice
endif
run = CommandListExtraTextures
ps-t3 = null
drawindexed = 3, 0, 0
[CommandListExtraTextures]
ps-t3 = ref ResourceExtraChoice
ps-t4 = ref resourcechoice
ps-t5 = ref ResourceUnsafeChoice
ps-t6 = ref ResourceBuffer
ps-t7 = ref ResourceMissingChoice
[ResourceChoice]
filename = Textures/map-01.dds
[ResourceAlternateChoice]
filename = Textures/map-02.dds
[ResourceExtraChoice]
filename = Textures/map-03.dds
[ResourceUnsafeChoice]
filename = ../../outside.dds
[ResourceBuffer]
filename = p.buf
[ResourceMissingChoice]
filename = missing.dds
[ResourceIB]
filename = i.buf
format = DXGI_FORMAT_R32_UINT
[ResourcePosition]
filename = p.buf
stride = 12
[ResourceTexcoord]
filename = t.buf
stride = 8
[ResourceExisting]
filename = existing.dds
[ResourceTexture]
filename = {named}
[ResourceShared]
filename = {shared}
[TextureOverrideRootSkin]
hash = aaaaaaaa
this = ResourceTexture
"""
    texture_ini = """[Constants]
global persist $variant = 0
[KeyVariant]
key = x
type = cycle
$variant = 0,1
[TextureOverrideSkin]
hash = aaaaaaaa
if $variant == 0
    this = ResourceTexture
else
    this = ResourceAlternate
endif
[ResourceTexture]
filename = custom_skin.png
[ResourceAlternate]
filename = ../alternate/custom_skin.png
[ResourceComponent]
filename = Textures/Components-2 t=bbbbbbbb.dds
[ResourceShared]
filename = ../Textures/Components-0-2 t=shared.dds
[ResourceWrongComponent]
filename = Textures/Components-3 t=cccccccc.dds
[ResourceMissing]
filename = Textures/Components-2 t=missing.dds
[ResourceChoice]
filename = sibling-map.dds
"""
    files = {
        "Body.ini": root_ini,
        "nested/Textures.ini": texture_ini,
        "DISABLED-textures.ini": (
            "[ResourceDisabled]\nfilename = Components-2 t=disabled.dds\n"),
        named: b"synthetic", foreign: b"synthetic", shared: b"synthetic",
        "nested/custom_skin.png": b"synthetic",
        "alternate/custom_skin.png": b"synthetic",
        "nested/Textures/Components-3 t=cccccccc.dds": b"synthetic",
        "Textures/Components-2 t=loose.dds": b"undeclared",
        "Components-2 t=disabled.dds": b"inactive",
        "existing.dds": b"synthetic",
        **{f"Textures/map-{index:02d}.dds": b"synthetic"
           for index in range(1, 4)},
        "nested/sibling-map.dds": b"unrelated",
        "p.buf": struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0),
        "t.buf": struct.pack("<6f", 0, 0, 1, 0, 0, 1),
        "i.buf": struct.pack("<3I", 0, 1, 2),
    }
    if archived:
        mod = tmp_path / "mod.zip"
        with zipfile.ZipFile(mod, "w") as archive:
            for name, data in files.items():
                archive.writestr("Wrapped/" + name, data)
        source = ZipModSource(mod)
    else:
        mod = tmp_path / "mod"
        for name, data in files.items():
            path = mod / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data.encode() if isinstance(data, str) else data)
        source = DirectoryModSource(mod)
    inis = discover_ini_paths(str(mod), source=source)
    assert [source.logical_path(path) for path in inis] == [
        "Body.ini", "nested/Textures.ini"]
    with patch.object(draw_scan, "_collect_texture_override_index",
                      wraps=draw_scan._collect_texture_override_index) as collect:
        parsed = analyze_mod_inis(inis, str(mod), source=source)
    assert collect.call_count == len(inis)
    assert len(parsed.groups) == 1
    assert len(parsed.texture_override_indexes) == 2
    group = parsed.groups[0]
    assert group["_texture_override_index"] is parsed.texture_override_indexes[0]
    resolved = [
        [item.file.replace("\\", "/")
         for item in index.replacements_by_hash["aaaaaaaa"]]
        for index in parsed.texture_override_indexes]
    assert resolved == [
        [named], ["nested/custom_skin.png", "alternate/custom_skin.png"]]

    assets = tmp_path / "assets"
    matched = assets / "Asset01"
    matched.mkdir(parents=True)
    asset_file = matched / "Components-2 t=aaaaaaaa.dds"
    asset_file.write_bytes(b"synthetic asset")
    (matched / "unrelated_cccccccc.dds").write_bytes(b"unrelated asset")
    (matched / "Metadata.json").write_text("{}", encoding="utf-8")
    (matched / "TextureUsage.json").write_text(json.dumps({
        "Component 2": {"ps-t0": ["aaaaaaaa-vs=12345678-ps=87654321"]},
    }), encoding="utf-8")
    binding = AssetComponentBinding(
        status="exact", component_status="exact", range_status="exact",
        asset_type="WWMI", root=str(assets), asset="Asset01",
        component_ordinal=2, metadata="Asset01/Metadata.json",
        detail_metadata="Asset01/TextureUsage.json")
    context = SimpleNamespace(
        mod_dir=str(mod), source=source, dds_classification_cache={})
    parsed.game = SimpleNamespace(game="wuwa")

    def unexpected(*args, **kwargs):
        raise AssertionError("Discovery must not decode textures")

    monkeypatch.setattr("core.textures.pipeline._open_texture_image",
                        unexpected)
    monkeypatch.setattr("app.assets.enrichment.classify_dds", unexpected)
    with patch.object(source, "list_files", wraps=source.list_files) as enumerate_files:
        _apply_texture_enrichment(parsed, context, [[binding]], complete_index=True)
    enumerate_files.assert_called_once()
    expected_files = [
        named, shared, "Textures/Components-2 t=loose.dds", foreign,
        "Components-2 t=disabled.dds",
        "Textures/map-01.dds", "Textures/map-02.dds", "Textures/map-03.dds",
        "nested/custom_skin.png",
        "alternate/custom_skin.png"]
    candidates = group["texture_candidates"]
    assert {item["file"].replace("\\", "/") for item in candidates[:-1]} == (
        {"existing.dds", *expected_files})
    assert candidates[-1]["source"] == "asset"
    assert all(item["source"] == "mod" for item in candidates[:-1])
    draw = group["draws"][0]
    assert draw.texture_default("diffuse") == "existing.dds"
    assert draw.texture_provenance == {"diffuse": "mod_semantic"}
    assert draw.asset_texture_defaults == {}
    assert draw.texture_default("normal_map") is None
    published = []

    def register(path, role):
        published.append((path, role))
        return f"/texture/{len(published)}"

    built = build_mesh_result(
        parsed.groups, str(mod), geometry=GeometryBlob(),
        texture_source=register, game_profile="wuwa", source=source)
    asset_key = asset_texture_key(str(assets), str(asset_file))
    assert asset_key in built.textures
    assert not any("custom_skin" in str(path) for path, _role in published)
    payload = {"meshes": built.meshes, "textures": built.textures}
    metadata.hydrate_textures(
        str(mod), payload, data={}, texture_source=register,
        texture_profile="wuwa", source=source)
    entry = payload["meshes"]["Component2-1"]
    assert entry["tex_key"] == "diffuse::existing.dds"
    assert list(payload["texture_pools"]) == [entry["texture_pool_id"]]
    pool = payload["texture_pools"][entry["texture_pool_id"]]
    assert {item["tex_key"] for item in pool} == {
        "diffuse::existing.dds",
        *["diffuse::" + filename for filename in expected_files], asset_key}
    assert pool[-1]["label"] == "Components-2 t=aaaaaaaa (Asset)"
    assert all(item["tex_key"] in payload["textures"] for item in pool)
    assert sum(os.path.normcase(str(path)) == os.path.normcase(str(asset_file))
               for path, _role in published) == 1
    assert all(not {"normal_map", "normal_data", "light_map",
                    "material_map", "emission_map"}.intersection(item)
               for item in pool)
    assert draw.texture_default("diffuse") == "existing.dds"
    assert draw.texture_provenance == {"diffuse": "mod_semantic"}
    assert draw.asset_texture_defaults == {}


def test_hash_wide_references_reach_only_same_ini_family_candidates(tmp_path):
    mod = tmp_path / "mod"
    mod.mkdir()
    _write_geometry(mod)
    root_ini = """[TextureOverrideFamilyIB]
hash = 0x1234ABCD
run = CommandListShared
[CommandListShared]
run = CommandListAssignShared
[CommandListAssignShared]
ps-t3 = ref ResourceShared
[TextureOverrideFamilyA]
hash = 1234abcd
match_first_index = 0
ib = ResourceComponent01IB
vb0 = ResourceComponent01Position
vb1 = ResourceComponent01Texcoord
ps-t4 = ref ResourcePrivate
drawindexed = 3, 0, 0
[TextureOverrideFamilyB]
hash = 1234abcd
match_first_index = 3
ib = ResourceComponent01IB
vb0 = ResourceComponent01Position
vb1 = ResourceComponent01Texcoord
drawindexed = 3, 0, 0
[TextureOverrideCountScoped]
hash = 1234abcd
match_index_count = 6
ps-t6 = ResourceCountScoped
[TextureOverrideOtherFamily]
hash = 99999999
ps-t7 = ResourceOtherFamily
[ResourceShared]
filename = shared.dds
[ResourcePrivate]
filename = private.dds
[ResourceCountScoped]
filename = count-scoped.dds
[ResourceOtherFamily]
filename = other-family.dds
""" + standard_component_resources(
        position_file="p.buf", texcoord_file="t.buf", ib_file="i.buf")
    (mod / "Body.ini").write_text(root_ini, encoding="utf-8")
    nested = mod / "nested"
    nested.mkdir()
    (nested / "Textures.ini").write_text("""[TextureOverrideForeignFamily]
hash = 1234abcd
run = CommandListShared
[CommandListShared]
ps-t3 = ResourceShared
[ResourceShared]
filename = foreign.dds
""", encoding="utf-8")
    for filename in ("shared.dds", "private.dds", "count-scoped.dds",
                     "other-family.dds", "nested/foreign.dds"):
        (mod / filename).write_bytes(b"synthetic texture")
    source = DirectoryModSource(mod)
    parsed = analyze_mod_inis(
        discover_ini_paths(str(mod), source=source), str(mod), source=source)
    assert [group["name"] for group in parsed.groups] == ["FamilyA", "FamilyB"]
    expected = [["shared.dds", "private.dds"], ["shared.dds"]]
    assert [group["referenced_texture_files"] for group in parsed.groups] == expected
    parsed.game = SimpleNamespace(game="wuwa")
    context = SimpleNamespace(
        mod_dir=str(mod), source=source, dds_classification_cache={})
    _apply_texture_enrichment(parsed, context, [], complete_index=False)
    built = build_mesh_result(
        parsed.groups, str(mod), geometry=GeometryBlob(),
        texture_source=lambda path, role: "/texture/" + os.path.basename(path),
        game_profile="wuwa", source=source)
    for group, filenames in zip(parsed.groups, expected):
        entry = built.meshes[group["name"] + "-1"]
        choices = entry["texture_options"]
        assert [choice["tex_key"] for choice in choices] == [
            "diffuse::" + filename for filename in filenames]
        assert all(choice["candidate_source"] == "mod"
                   for choice in choices)
        assert entry["tex_key"] is None
        assert all(entry.get(role + "_key") is None for role in (
            "normal_map", "normal_data", "light_map", "material_map", "emission_map"))


@pytest.mark.parametrize("game, asset_type", [
    ("genshin", "GIMI"), ("zzz", "ZZMI"), ("wuwa", "WWMI")])
@pytest.mark.parametrize("configured_assets", [False, True], ids=["mod", "asset"])
@pytest.mark.parametrize("archived", [False, True], ids=["directory", "zip"])
def test_unified_texture_load_and_saved_choice_lifecycle(
        tmp_path, monkeypatch, game, asset_type, configured_assets, archived):
    names = ["Component0", "Component1", "Component2"] if game == "wuwa" else [
        "Body", "BodySuit", "Face"]
    loose = (["Components-0-1 t=loose.dds", "Components-1 t=detail.dds",
              "Components-2 t=face.dds"] if game == "wuwa" else [
        "CharacterBodyDiffuse.PNG", "CharacterBodySuitNormalMap.jpg",
        "CharacterFaceNormalMap.jpeg"])
    ini = "[Constants]\nglobal persist $variant = 0\n"
    ini += "[KeyVariant]\nkey = x\ntype = cycle\n$variant = 0,1\n"
    for ordinal, name in enumerate(names):
        ini += f"""[TextureOverride{name}]
hash = {0x10101010 if game == "wuwa" else (ordinal + 1) * 0x10101010:08x}
match_first_index = {ordinal * 6 if game == "wuwa" else 0}
ib = ResourceComponent01IB
vb0 = ResourceComponent01Position
vb1 = ResourceComponent01Texcoord
"""
        if ordinal == 0:
            ini += rf"""Resource\{asset_type}\Diffuse = ResourceFirst
Resource\{asset_type}\NormalMap = ResourceNormal
ps-t7 = ResourceSlot
drawindexed = 3,0,0
if $variant == 1
Resource\{asset_type}\Diffuse = ResourceAlternate
endif
drawindexed = 3,3,0
"""
        else:
            ini += "drawindexed = 3,0,0\n"
    ini += standard_component_resources(
        position_file="p.buf", texcoord_file="t.buf", ib_file="i.buf")
    ini += """[ResourceFirst]
filename = base.dds
[ResourceAlternate]
filename = alternate.dds
[ResourceNormal]
filename = normal.dds
[ResourceSlot]
filename = slot.png
[TextureOverrideSkin]
hash = 11111111
this = ResourceReplacement
[ResourceReplacement]
filename = replacement.dds
"""
    files = {"mod.ini": ini.encode(),
             "p.buf": struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0),
             "t.buf": struct.pack("<6f", 0, 0, 1, 0, 0, 1),
             "i.buf": struct.pack("<6I", 0, 1, 2, 0, 1, 2)}
    for filename in [*loose, "base.dds", "alternate.dds", "normal.dds",
                     "slot.png", "replacement.dds", "unmatched.png",
                     "CharacterBodyFace.png", "variants/" + loose[0]]:
        files[filename] = b"synthetic texture"
    mod = tmp_path / ("mod.zip" if archived else "mod")
    if archived:
        with zipfile.ZipFile(mod, "w") as archive:
            for filename, data in files.items():
                archive.writestr("Wrapped/" + filename, data)
        source = ZipModSource(mod)
    else:
        for filename, data in files.items():
            path = mod / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        source = DirectoryModSource(mod)
    context = ModLoadContext(str(mod), build_mod_ini_snapshot(
        discover_ini_paths(str(mod), source=source), str(mod), source=source))
    monkeypatch.setattr("app.mods.analysis.resolve_game_detection", lambda *_args: GameDetection(
        game=game, runtime="unknown", texture_api=asset_type.casefold(),
        confidence="high", scores={}))
    root = tmp_path / "assets"
    asset = root / "Asset01"
    asset.mkdir(parents=True)
    asset_filename = "Components-0-2 t=original.dds" if game == "wuwa" else "Asset01FaceDiffuse.dds"
    (asset / asset_filename).write_bytes(b"synthetic original")
    (asset / "Asset01FaceNormalMap.dds").write_bytes(b"synthetic normal")
    if game == "wuwa":
        (asset / "Metadata.json").write_text(json.dumps({
            "vb0_hash": "10101010", "components": [
                {"name": name, "index_offset": ordinal * 6, "index_count": 6}
                for ordinal, name in enumerate(names)]}), encoding="utf-8")
        (asset / "TextureUsage.json").write_text(json.dumps({
            "Component 0": {"ps-t7": ["11111111-vs=12345678-ps=87654321"]},
        }), encoding="utf-8")
    else:
        (asset / "hash.json").write_text(json.dumps([
            {"ib": f"{(ordinal + 1) * 0x10101010:08x}", "component_name": name,
             "object_indexes": [0], "object_index_counts": [6],
             "texture_hashes": [[
                 ["Diffuse", ".dds", f"{(ordinal + 1) * 0x11111111:08x}"],
                 ["NormalMap", ".dds", f"{(ordinal + 1) * 0x12121212:08x}"],
             ]]}
            for ordinal, name in enumerate(names)]), encoding="utf-8")
    index = asset_index.build_index(asset_type, str(root))
    monkeypatch.setattr(asset_index, "load_index", lambda *_args: index)
    if configured_assets:
        context.asset_folders = [{"type": asset_type, "path": str(root), "enabled": True}]
    published = []

    def publish(path, role):
        published.append((path, role))
        return f"/texture/{len(published)}"

    def no_decode(*_args, **_kwargs):
        raise AssertionError("Texture discovery and publication must stay lazy")

    monkeypatch.setattr("app.assets.enrichment.classify_dds", no_decode)
    monkeypatch.setattr("core.textures.pipeline._open_texture_image", no_decode)
    with patch.object(context.source, "list_files", wraps=context.source.list_files) as enumerate_files:
        payload = load_mod(context=context, geometry=GeometryBlob(), texture_source=publish)
    assert not payload.get("error")
    enumerate_files.assert_called_once()
    first = payload["meshes"][names[0] + "-1"]
    second = payload["meshes"][names[0] + "-2"]
    face = payload["meshes"][names[2] + "-1"]
    assert first["tex_key"] == "diffuse::base.dds"
    assert second["tex_key"] == "diffuse::alternate.dds"
    assert {item["tex_key"] for item in second["texture_variants"]} == {
        "diffuse::base.dds", "diffuse::alternate.dds"}
    normal_role = "normal_data" if game == "wuwa" else "normal_map"
    assert first[normal_role + "_key"] == normal_role + "::normal.dds"
    saved_key = "diffuse::variants/" + loose[0]
    saved = {"textures": {first["identity"]["key"]: {
        "tex_key": saved_key, "label": "Saved choice", "manual": True}}}
    metadata.hydrate_textures(str(mod), payload, data=saved,
                              texture_source=publish, texture_profile=game,
                              source=context.source)
    pools = payload["texture_pools"]
    assert first["texture_pool_id"] == second["texture_pool_id"]
    assert first["saved_texture_override"] == saved_key
    assert first["tex_key"] == "diffuse::base.dds"
    component_files = [{item["file"] for item in pools[payload["meshes"][name + "-1"]["texture_pool_id"]]}
                       for name in names]
    assert loose[0] in component_files[0]
    assert loose[1] in component_files[1]
    assert loose[2] in component_files[2]
    assert "slot.png" in component_files[0]
    assert all("slot.png" not in pool for pool in component_files[1:])
    assert loose[1] not in component_files[0]
    assert all("unmatched.png" not in pool and "CharacterBodyFace.png" not in pool
               for pool in component_files)
    assert all(len(pool) == len({item["tex_key"] for item in pool}) for pool in pools.values())
    asset_key = asset_texture_key(str(root), str(asset / asset_filename))
    if configured_assets:
        assert payload["asset_resolution"]["exact_draws"] == 4
        assert "replacement.dds" in component_files[0]
        assert all("replacement.dds" not in pool for pool in component_files[1:])
        assert asset_key in payload["textures"]
        assert face["tex_key"] == (None if game == "wuwa" else asset_key)
        assert (face.get("normal_map_key") is not None) == (game != "wuwa")
        assert not any("asset/" in str(first.get(role + "_key", "")) for role in (
            "normal_map", "normal_data", "light_map", "material_map"))
    else:
        assert face["tex_key"] is None
        assert asset_key not in payload["textures"]

    if game != "wuwa":
        sibling_ini = rf"""[TextureOverrideBody]
hash = 10101010
match_first_index = 0
ib = ResourceComponent01IB
vb0 = ResourceComponent01Position
vb1 = ResourceComponent01Texcoord
Resource\{asset_type}\Diffuse = ResourceOwned
drawindexed = 3,0,0
[ResourceOwned]
filename = {loose[0]}
""" + standard_component_resources(
            position_file="p.buf", texcoord_file="t.buf", ib_file="i.buf")
        if archived:
            with zipfile.ZipFile(mod, "a") as archive:
                archive.writestr("Wrapped/Other.ini", sibling_ini)
            source = ZipModSource(mod)
        else:
            (mod / "Other.ini").write_text(sibling_ini, encoding="utf-8")
            source = DirectoryModSource(mod)
        reloaded_context = ModLoadContext(str(mod), build_mod_ini_snapshot(
            discover_ini_paths(str(mod), source=source), str(mod), source=source),
            asset_folders=context.asset_folders)
        reloaded = load_mod(context=reloaded_context, geometry=GeometryBlob(),
                            texture_source=publish)
        assert not reloaded.get("error")
        metadata.hydrate_textures(str(mod), reloaded, data={},
                                  texture_source=publish, texture_profile=game,
                                  source=reloaded_context.source)
        original = next(entry for entry in reloaded["meshes"].values()
                        if entry["identity"]["source"] == "mod.ini"
                        and entry["tex_key"] == "diffuse::base.dds")
        sibling = next(entry for entry in reloaded["meshes"].values()
                       if entry["identity"]["source"] == "Other.ini")
        for entry, owned in ((original, False), (sibling, True)):
            choices = {item["file"] for item in reloaded["texture_pools"][entry["texture_pool_id"]]}
            assert (loose[0] in choices) == owned
            assert "variants/" + loose[0] not in choices
        assert sibling["tex_key"] == "diffuse::" + loose[0]


def test_wwmi_asset_candidates_keep_metadata_object_and_draw_scope(tmp_path):
    mod = tmp_path / "mod"
    root = tmp_path / "assets"
    asset = root / "Asset01"
    mod.mkdir()
    files = {
        "ObjectA/Components-0 t=owned-a.dds": b"named",
        "ObjectA/Components-1 t=unrelated.dds": b"other ordinal",
        "ObjectA/opaque_aaaaaaaa.dds": b"hash",
        "ObjectA/opaque_cccccccc.dds": b"other object hash",
        "ObjectB/Components-0 t=owned-b.dds": b"named",
        "ObjectB/Components-1-2 t=shared-b.dds": b"multi component",
        "ObjectB/opaque_bbbbbbbb.dds": b"hash",
        "ObjectB/opaque_cccccccc.dds": b"hash",
        "ObjectB/opaque_aaaaaaaa.dds": b"other object hash",
        "Components-0 t=parent.dds": b"parent object",
        "ObjectA/Nested/Components-0 t=aaaaaaaa.dds": b"nested object",
        "ObjectC/Components-0 t=aaaaaaaa.dds": b"sibling object",
    }
    for relative, data in files.items():
        path = asset / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    for name, hashes in (("ObjectA", ("aaaaaaaa",)),
                         ("ObjectB", ("bbbbbbbb", "cccccccc"))):
        directory = asset / name
        (directory / "Metadata.json").write_text("{}", encoding="utf-8")
        (directory / "TextureUsage.json").write_text(json.dumps({
            f"Component {ordinal}": {"ps-t0": [
                f"{value}-vs=12345678-ps=87654321"]}
            for ordinal, value in enumerate(hashes)}), encoding="utf-8")

    def binding(object_name, ordinal):
        return AssetComponentBinding(
            status="exact", component_status="exact", range_status="exact",
            asset_type="WWMI", root=str(root), asset="Asset01",
            component_ordinal=ordinal,
            metadata=f"Asset01/{object_name}/Metadata.json",
            detail_metadata=f"Asset01/{object_name}/TextureUsage.json")

    groups = [{"name": "Component0", "draws": [DrawCall(), DrawCall()]},
              {"name": "Component0_2", "display_name": "Component0",
               "draws": [DrawCall()]}]
    parsed = SimpleNamespace(groups=groups, resource_files=[],
                             texture_override_indexes=[],
                             game=SimpleNamespace(game="wuwa"))
    context = SimpleNamespace(mod_dir=str(mod), source=DirectoryModSource(mod),
                              dds_classification_cache={})
    _apply_texture_enrichment(parsed, context, [
        [binding("ObjectA", 0), binding("ObjectB", 1)],
        [binding("ObjectB", 0)]], complete_index=True)

    assert [{os.path.relpath(item["path"], asset).replace("\\", "/")
             for item in group["texture_candidates"]} for group in groups] == [
        {"ObjectA/Components-0 t=owned-a.dds", "ObjectA/opaque_aaaaaaaa.dds",
         "ObjectB/Components-1-2 t=shared-b.dds", "ObjectB/opaque_cccccccc.dds"},
        {"ObjectB/Components-0 t=owned-b.dds", "ObjectB/opaque_bbbbbbbb.dds"},
    ]
    assert all(draw.texture_default("diffuse") is None
               and not draw.asset_texture_defaults
               for group in groups for draw in group["draws"])


def test_inventory_boundaries_and_matched_asset_scan_lifecycle(tmp_path):
    mod = tmp_path / "mod"
    root = tmp_path / "assets"
    matched = root / "Matched"
    mod.mkdir()
    (matched / "textures").mkdir(parents=True)
    for filename in ("Body.dds", "buffer.buf"):
        (mod / filename).write_bytes(b"synthetic")
    for filename in ("Body.dds", "textures/Body.png"):
        (matched / filename).write_bytes(b"synthetic")
    (root / "Sibling").mkdir()
    (root / "Sibling" / "Body.dds").write_bytes(b"unrelated")
    binding = AssetComponentBinding(
        status="exact", component_status="exact", range_status="exact",
        asset_type="GIMI", root=str(root), asset="Matched",
        metadata="Matched/textures/hash.json")
    with patch.object(DirectoryModSource, "list_files", autospec=True,
                      side_effect=DirectoryModSource.list_files) as scans:
        inventory = collect_texture_inventory(str(mod), [[binding, binding]], resource_files=[
            "./Body.dds", "Body.dds", "../../outside.dds", str(mod / "Body.dds"),
            "buffer.buf", "missing.dds"])
    assert [os.path.normcase(call.args[0].root) for call in scans.call_args_list] == [
        os.path.normcase(str(mod)), os.path.normcase(str(matched.resolve()))]
    assert len(inventory) == 3
    assert {item["source"] for item in inventory.values()} == {"mod", "asset"}
    invalid = AssetComponentBinding(
        status="exact", component_status="exact", range_status="exact",
        root=str(root), asset="../", metadata="../hash.json")
    assert len(collect_texture_inventory(str(mod), [[invalid]])) == 1
    ambiguous = AssetComponentBinding(status="ambiguous", root=str(root), asset="Matched")
    assert len(collect_texture_inventory(str(mod), [[ambiguous]])) == 1

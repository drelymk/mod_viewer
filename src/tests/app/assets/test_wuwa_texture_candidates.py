import os
import struct
import json
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.mods import metadata as metadata
from app.mods.analysis import analyze_mod_inis
from app.mods.enrichment import _apply_texture_enrichment
from app.assets.resolver import AssetComponentBinding
from app.assets.textures import asset_texture_key
from core.geometry.mesh_builder import GeometryBlob, build_mesh_result
from core.ini import draw_scan
from core.mod_discovery import discover_ini_paths
from core.mod_source import DirectoryModSource, ZipModSource


def _write_geometry(root):
    (root / "p.buf").write_bytes(struct.pack(
        "<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0))
    (root / "t.buf").write_bytes(struct.pack(
        "<6f", 0, 0, 1, 0, 0, 1))
    (root / "i.buf").write_bytes(struct.pack("<3I", 0, 1, 2))


def _group(root):
    discovered = ("B.dds", "normal-a.dds", "normal-b.dds")
    for filename in ("A.dds", *discovered):
        (root / filename).write_bytes(b"synthetic dds")
    return [{
        "name": "Component4", "display_name": "Component4",
        "position_file": "p.buf", "position_stride": 12,
        "texcoord_file": "t.buf", "texcoord_stride": 8,
        "ib_file": "i.buf", "index_size": 4,
        "diffuse_pool_files": [{"res": "ResourceA", "file": "A.dds"}],
        "discovered_textures": [
            {"file": "A.dds", "source": "wuwa_filename"},
            *[{"file": filename, "source": "wuwa_filename"}
              for filename in discovered],
        ],
        "draws": [{"label": "Component4-1", "count": 3,
                   "start": 0, "base": 0}],
    }]


def _build(root):
    _write_geometry(root)

    def register(path, role):
        return f"/texture/{role}/{os.path.basename(path)}"

    return build_mesh_result(
        _group(root), str(root), geometry=GeometryBlob(),
        texture_source=register, game_profile="wuwa")


def test_manage_texture_pool_deduplicates_parser_and_discovered_files(
        tmp_path):
    built = _build(tmp_path)
    entry = built.meshes["Component4-1"]

    assert [item["file"] for item in entry["texture_options"]] == [
        "A.dds", "B.dds", "normal-a.dds", "normal-b.dds"]
    assert all("normal_map" not in item for item in entry["texture_options"])
    assert all("normal_data" not in item for item in entry["texture_options"])

    payload = {"meshes": built.meshes, "textures": {}}
    metadata.hydrate_textures(
        str(tmp_path), payload, texture_profile="wuwa")

    pool = payload["texture_pools"]["p0"]
    assert [item["file"] for item in pool] == [
        "A.dds", "B.dds", "normal-a.dds", "normal-b.dds"]
    assert all("normal_map" not in item for item in pool)
    assert all("normal_data" not in item for item in pool)


@pytest.mark.parametrize("archived", [False, True], ids=["directory", "zip"])
def test_cross_ini_three_source_candidates_reach_one_texture_pool(
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
drawindexed = 3, 0, 0
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
        raise AssertionError("Discovery must not scan the mod or decode textures")

    monkeypatch.setattr(source, "list_files", unexpected)
    monkeypatch.setattr("core.geometry.texture_bindings.encode_texture_data_uri",
                        unexpected)
    monkeypatch.setattr("app.assets.enrichment.classify_dds", unexpected)
    _apply_texture_enrichment(parsed, context, [[binding]], complete_index=True)
    expected_files = [
        named, shared, foreign, "nested/custom_skin.png",
        "alternate/custom_skin.png"]
    candidates = group["discovered_textures"]
    assert [item["file"].replace("\\", "/") for item in candidates[:-1]] == (
        expected_files)
    assert [item["source"] for item in candidates] == [
        "wuwa_filename", "wuwa_filename", "wuwa_filename",
        "wuwa_hash", "wuwa_hash", "wuwa_asset_hash"]
    draw = group["draws"][0]
    assert draw.texture_default("diffuse") == "existing.dds"
    assert draw.texture_provenance == {"diffuse": "mod_semantic"}
    assert draw.asset_texture_defaults == {}
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
    assert [item["tex_key"] for item in pool] == [
        "diffuse::existing.dds",
        *["diffuse::" + filename for filename in expected_files], asset_key]
    assert pool[-1]["label"] == "Components-2 t=aaaaaaaa (Asset)"
    assert all(item["tex_key"] in payload["textures"] for item in pool)
    assert sum(str(path) == str(asset_file) for path, _role in published) == 1
    assert all(not {"normal_map", "normal_data", "light_map",
                    "material_map", "emission_map"}.intersection(item)
               for item in pool)
    assert draw.texture_default("diffuse") == "existing.dds"
    assert draw.texture_provenance == {"diffuse": "mod_semantic"}
    assert draw.asset_texture_defaults == {}

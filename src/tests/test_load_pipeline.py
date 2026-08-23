"""Focused checks for the load pipeline's cross-module contracts."""

import os
import json
import struct
import tempfile
import base64
import io
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app import edit_session, metadata, mod_loader, present_api, server
from app.asset_resolver import AssetComponentBinding
from app.api import ModViewerAPI
from core.ini_analysis import analyze_ini
from core.ini_document import IniDocument
from core.ini_sections import (extract_resources, sections_from_document)
from core.draw_call import DrawCall
from core.mesh_builder import (GeometryBlob, MeshBuildResult,
                               build_mesh_payload, build_mesh_result,
                               build_mesh_semantics)
from core.textures import encode_texture_file


def test_document_projection_keeps_authoritative_text_and_source():
    path = os.path.join(tempfile.gettempdir(), "staged-refactor.ini")
    doc = IniDocument.from_string(
        "[TextureOverrideBody]\n"
        "drawindexed = 3, 0, 0 ; inline comment\n",
        path=path)
    sections = sections_from_document(doc)
    line = sections["TextureOverrideBody"][0]
    assert (str(line) == "drawindexed = 3, 0, 0"), ("document projection uses the staged line text")
    assert (line.ini_path == path and line.line_no == 2), ("document projection retains one-based source provenance")


def test_semantic_analysis_shares_one_canonical_scan():
    sections = {"Constants": ["global $Body = 0"]}
    with patch("core.ini_analysis.canonical_var_names",
               wraps=lambda value: {"body": "Body"}) as canonical:
        analysis = analyze_ini(sections, resources=extract_resources(sections))
    assert (canonical.call_count == 1), ("one semantic analysis computes canonical variables once")
    assert (analysis.canonical_vars == {"body": "Body"}), ("analysis exposes the shared canonical spelling map")
    assert (analysis.toggles == {} and analysis.menu == {} and analysis.draw_groups == []), ("one analysis handles control-free INIs without synthetic geometry")


def test_geometry_blob_bypasses_base64_intermediate():
    with tempfile.TemporaryDirectory() as root:
        with open(os.path.join(root, "p.buf"), "wb") as fh:
            fh.write(struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0))
        with open(os.path.join(root, "t.buf"), "wb") as fh:
            fh.write(struct.pack("<6f", 0, 0, 1, 0, 0, 1))
        with open(os.path.join(root, "i.buf"), "wb") as fh:
            fh.write(struct.pack("<3I", 0, 1, 2))
        ini_path = os.path.join(root, "mod.ini")
        with open(ini_path, "w", encoding="utf-8") as fh:
            fh.write(
                "[TextureOverrideBodyPosition]\n"
                "vb0 = ResourceBodyPosition\n"
                "[TextureOverrideBodyTexcoord]\n"
                "vb1 = ResourceBodyTexcoord\n"
                "[TextureOverrideBody]\n"
                "ib = ResourceBodyIB\n"
                "drawindexed = 3, 0, 0\n"
                "[ResourceBodyPosition]\nfilename = p.buf\nstride = 12\n"
                "[ResourceBodyTexcoord]\nfilename = t.buf\nstride = 8\n"
                "[ResourceBodyIB]\nfilename = i.buf\nformat = R32_UINT\n")
        groups = [{
            "name": "Body", "display_name": "Body",
            "position_file": "p.buf", "texcoord_file": "t.buf",
            "position_stride": 12, "texcoord_stride": 8,
            "ib_file": "i.buf", "index_size": 4,
            "draws": [{"label": "Body-1", "count": 3,
                        "start": 0, "base": 0, "conditions": []}],
        }]
        geometry = GeometryBlob()
        built = build_mesh_result(groups, root, geometry=geometry)
        entry = built.meshes["Body-1"]
        assert (isinstance(built, MeshBuildResult) and built.geometry is geometry), ("the mesh pipeline returns a named result with its geometry owner")
        assert (isinstance(entry["pos"], dict) and isinstance(entry["idx"], dict)), ("binary geometry fields are offsets, not base64 strings")
        assert (len(geometry) == (entry["pos"]["length"] +
                                entry["idx"]["length"] +
                                entry["uv"]["length"])), ("all mesh buffers append into one shared blob")
        view_payload = {
            "meshes": built.meshes,
            "textures": built.textures,
        }
        server.publish_payload_geometry(view_payload, geometry)
        assert (view_payload["geometry"]["length"] == len(geometry)), ("the server publishes the builder-owned binary blob directly")

        legacy = build_mesh_payload(groups, root)
        assert (isinstance(legacy["Body-1"]["pos"], str)), ("direct callers retain the legacy base64 geometry contract")

        context = mod_loader.ModLoadContext(
            root, [ini_path], {ini_path: IniDocument.load(ini_path)}, {})
        loaded_geometry = GeometryBlob()
        loaded = mod_loader.load_mod(context=context, geometry=loaded_geometry)
        assert ({"meshes", "textures", "controls", "state", "geometry",
               "metadata", "health"}.issubset(loaded)), ("the application loader returns the structured payload contract")
        assert (not loaded.get("error") and
              isinstance(loaded["meshes"]["Body-1"]["pos"], dict)), ("the context-based loader keeps authoritative documents and raw geometry refs")
        loaded_entry = loaded["meshes"]["Body-1"]
        assert (loaded_entry["material_kind"] == "body"
                and loaded_entry["material_kind_reliable"] is False
                and loaded_entry["material_profile_id"] == "none"), ("mesh material identity is assigned conservatively")
        assert set(loaded["metadata"]["material_profiles"]) == {"none"}, ("profile metadata is deduplicated at payload scope")


def test_mesh_semantics_include_conditional_texture_roles_without_geometry(
        tmp_path):
    for name in ("base.png", "alt.png", "normal.png", "normal-alt.png",
                 "light.png", "light-alt.png", "material.png",
                 "material-alt.png"):
        (tmp_path / name).touch()
    condition = [{"var": "skin", "value": "1", "negate": False}]
    groups = [{
        "name": "Body", "draws": [{
            "label": "Body-1", "count": 3, "start": 0, "base": 0,
            "conditions": [],
            "texture_default_file": "base.png",
            "texture_assignments": [
                {"conditions": [], "file": "base.png"},
                {"conditions": [condition], "file": "alt.png"},
            ],
            "normal_map_default_file": "normal.png",
            "normal_map_variants": [{"conditions": [condition], "file": "normal-alt.png"}],
            "light_map_default_file": "light.png",
            "light_map_variants": [{"conditions": [condition], "file": "light-alt.png"}],
            "material_map_default_file": "material.png",
            "material_map_variants": [{"conditions": [condition], "file": "material-alt.png"}],
        }],
        "ib_file": "i.buf", "index_size": 4,
        "position_file": "p.buf", "position_stride": 12,
        "texcoord_file": "t.buf", "texcoord_stride": 8,
    }]

    entry = build_mesh_semantics(groups, str(tmp_path), game_profile="wuwa")["Body-1"]

    assert entry["tex_key"] == "diffuse::base.png"
    assert {variant["tex_key"] for variant in entry["texture_variants"]} == {
        "diffuse::base.png", "diffuse::alt.png"}
    assert entry["normal_map_key"] is None
    assert entry["normal_data_key"] == "normal_data::normal.png"
    assert entry["normal_data_variants"][0]["tex_key"] == "normal_data::normal-alt.png"
    assert entry["light_map_key"] == "light_map::light.png"
    assert entry["material_map_key"] == "material_map::material.png"


def test_mesh_semantics_returns_asset_resolution_summary(tmp_path):
    draw = DrawCall(label="Body-1")
    parsed = mod_loader.ParsedModAnalysis(
        groups=[{"name": "Body", "draws": [draw]}],
        toggles={}, menu={}, defaults={}, state_rules=[], present={},
        game=SimpleNamespace(game="genshin"),
    )
    binding = AssetComponentBinding(
        status="exact", component_status="exact", range_status="exact",
        asset_type="GIMI", asset="Alice", component_name="Body",
        first_index=10, index_count=20)

    def resolve(_groups, _game, _entries, *, availability):
        availability.update({
            "asset_type": "GIMI", "configured_roots": 1,
            "ready_roots": 1, "unavailable_roots": 0,
        })
        return [[binding]]

    context = mod_loader.ModLoadContext(
        str(tmp_path), [str(tmp_path / "mod.ini")], {}, {})
    with patch.object(mod_loader, "_parse_inis", return_value=parsed), \
            patch.object(mod_loader.asset_resolver, "resolve_groups",
                         side_effect=resolve), \
            patch.object(mod_loader.asset_enrichment, "apply"), \
            patch.object(mod_loader, "build_mesh_semantics",
                         return_value={"Body-1": {}}):
        result = mod_loader.load_mesh_semantics(context)

    assert result["meshes"] == {"Body-1": {}}
    assert result["asset_resolution"]["index_status"] == "ready"
    assert result["asset_resolution"]["exact_draws"] == 1


def test_control_semantics_filter_wired_toggles_to_displayed_meshes(
        tmp_path):
    def toggle_info(section, variable):
        return {
            "name": section, "key_display": "", "key": "",
            "source": None, "ini_path": str(tmp_path / "mod.ini"),
            "section": section, "vars": {variable: ["0", "1"]},
        }

    parsed = mod_loader.ParsedModAnalysis(
        groups=[{"draws": []}],
        toggles={
            "KeyVisible": toggle_info("KeyVisible", "visible"),
            "KeyPhantom": toggle_info("KeyPhantom", "phantom"),
        },
        menu={}, defaults={"visible": "0", "phantom": "0"},
        state_rules=[], present={},
        game=SimpleNamespace(game="unknown"),
    )
    context = mod_loader.ModLoadContext(
        str(tmp_path), [str(tmp_path / "mod.ini")], {}, {})
    with patch.object(mod_loader, "_parse_inis", return_value=parsed), \
            patch.object(mod_loader, "build_mesh_semantics", return_value={
                "Body-1": {"conditions": [[{
                    "var": "visible", "value": "1", "negate": False,
                }]]},
                "Broken-1": {"conditions": [[{
                    "var": "phantom", "value": "1", "negate": False,
                }]]},
            }):
        result = mod_loader.load_control_state(
            context, active_mesh_keys={"Body-1"})

    assert set(result["controls"]["toggles"]) == {"KeyVisible"}


def test_diagnostics_cache_tracks_authoritative_revision():
    with tempfile.TemporaryDirectory() as root:
        path = os.path.join(root, "mod.ini")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("[Constants]\n$active = 0\n")
        try:
            edit_session.load_documents(root, [path])
            revision = edit_session.current_revision(root)
            report = {"summary": {"issues": 0}, "issues": []}
            edit_session.cache_diagnostics(root, report)
            cached = edit_session.cached_diagnostics(root)
            assert (cached == report and cached is not report), ("diagnostics are cached as a detached report")

            edit_session.update_text(root, "mod.ini", "[Constants]\n$active = 1\n")
            assert (edit_session.current_revision(root) > revision
                  and edit_session.cached_diagnostics(root) is None), ("a staged document edit invalidates the diagnostics cache")
            edit_session.cache_diagnostics(root, report)
            edit_session.invalidate_diagnostics(root)
            assert (edit_session.cached_diagnostics(root) is None), ("viewer metadata changes can invalidate diagnostics independently")
        finally:
            edit_session.discard(root)


def test_present_state_read_uses_staged_documents_without_geometry(
        tmp_path):
    root = os.path.normcase(os.path.abspath(str(tmp_path)))
    ini_path = os.path.join(root, "mod.ini")
    with open(ini_path, "w", encoding="utf-8") as fh:
        fh.write(
            "[KeyOutfit]\n"
            "key = 1\n"
            "type = cycle\n"
            "$Outfit = 0,1\n"
            "[TextureOverrideBody]\n"
            "if $Outfit == 0\n"
            "drawindexed = 3, 0, 0\n"
            "endif\n")

    edit_session.load_documents(root, [ini_path])
    added = present_api.add_present(
        root, "ctrl p", "", {"mod.ini": {"Outfit": "0"}})
    assert added.get("ok") is True

    api = ModViewerAPI()
    api._authorized_folders.add(os.path.normcase(os.path.abspath(root)))
    with patch.object(mod_loader, "build_mesh_result",
                      side_effect=AssertionError("geometry must not be built")):
        result = api.get_present_state(root)

    try:
        assert result.get("error") is None
        present = result["present"]
        assert present["item"]["key_raw"] == "ctrl p"
        assert present["item"]["count"] == 1
        assert present["item"]["names"] == ["Present 1"]
    finally:
        edit_session.discard(root)


def test_wuwa_publishes_one_intact_normal_data_source():
    from PIL import Image

    def uri_mode(uri):
        payload = base64.b64decode(uri.split(",", 1)[1])
        return Image.open(io.BytesIO(payload)).mode

    with tempfile.TemporaryDirectory() as root:
        Image.new("RGBA", (1, 1), (128, 128, 12, 34)).save(
            os.path.join(root, "normal.png"))
        with open(os.path.join(root, "p.buf"), "wb") as fh:
            fh.write(struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0))
        with open(os.path.join(root, "t.buf"), "wb") as fh:
            fh.write(struct.pack("<6f", 0, 0, 1, 0, 0, 1))
        with open(os.path.join(root, "i.buf"), "wb") as fh:
            fh.write(struct.pack("<3I", 0, 1, 2))
        group = [{
            "name": "Body", "display_name": "Body",
            "position_file": "p.buf", "texcoord_file": "t.buf",
            "position_stride": 12, "texcoord_stride": 8,
            "ib_file": "i.buf", "index_size": 4,
            "draws": [{"label": "Body-1", "count": 3,
                        "start": 0, "base": 0, "conditions": [],
                        "normal_map_default_file": "normal.png"}],
        }]
        built = build_mesh_result(group, root, geometry=GeometryBlob(),
                                  game_profile="wuwa")
        entry = built.meshes["Body-1"]

        assert entry["normal_data_key"] == "normal_data::normal.png"
        assert "normal_map_key" not in entry
        assert entry["normal_map_enabled"] is False
        assert "ao_map_key" not in entry
        assert set(built.textures) == {"normal_data::normal.png"}
        assert uri_mode(built.textures[entry["normal_data_key"]]) == "RGBA"




@pytest.mark.parametrize("saved_normals", [
    {"normal_map": "normal.png"},
])
def test_wuwa_metadata_migrates_legacy_normal_map_to_normal_data(
        tmp_path, saved_normals):
    from PIL import Image

    Image.new("RGBA", (1, 1), (128, 128, 255, 255)).save(
        tmp_path / "normal.png")
    Image.new("RGB", (1, 1), (128, 128, 128)).save(
        tmp_path / "shared.png")
    data = {"textures": {"Body::3,0,0": {
        "tex_key": "shared.png", "label": "Shared", "manual": True,
        **saved_normals,
    }}}
    payload = {"meshes": {"Body-1": {
        "component": "Body", "drawindexed": [3, 0, 0],
        "texture_options": [],
    }}, "textures": {}}
    registered = []

    def register(source, role):
        registered.append(role)
        return f"/texture/test/{role}"

    restored = metadata.hydrate_textures(
        str(tmp_path), payload, data, texture_source=register,
        texture_profile="wuwa")
    migrated = restored["Body::3,0,0"]
    assert migrated["normal_data"] == "normal_data::normal.png"
    assert "normal_map" not in migrated
    assert registered == ["diffuse", "normal_data"]
    assert payload["textures"] == {
        "diffuse::shared.png": "/texture/test/diffuse",
        "normal_data::normal.png": "/texture/test/normal_data",
    }


def test_wuwa_normal_data_tombstone_removes_ini_pool_value_on_hydration(
        tmp_path):
    from PIL import Image

    Image.new("RGB", (1, 1), (128, 128, 128)).save(
        tmp_path / "shared.png")
    data = {"textures": {"Body::3,0,0": {
        "tex_key": "shared.png", "label": "Shared", "manual": True,
        "normal_data": None, "normal_data_manual": True,
    }}}
    payload = {"meshes": {"Body-1": {
        "component": "Body", "drawindexed": [3, 0, 0],
        "texture_options": [{
            "tex_key": "diffuse::shared.png", "file": "shared.png",
            "label": "Shared",
            "normal_data": "normal_data::BodyNormal.dds",
        }],
    }}, "textures": {}}

    restored = metadata.hydrate_textures(
        str(tmp_path), payload, data, texture_profile="wuwa")
    assert payload["meshes"]["Body-1"]["texture_pool_id"] == "p0"
    assert "texture_options" not in payload["meshes"]["Body-1"]
    option = payload["texture_pools"]["p0"][0]
    assert restored["Body::3,0,0"]["normal_data_manual"] is True
    assert "normal_data" not in option
    assert option["normal_data_manual"] is True


def test_component_material_kind_overrides_apply_to_every_draw_and_auto_removes(
        tmp_path):
    root = str(tmp_path)

    assert metadata.save_component_material_kind(
        root, "SourceA", "Component3", "body")["saved"]
    saved = metadata.load(root)
    assert saved["component_material_kinds"] == {
        "SourceA": {"Component3": "body"}}
    assert metadata.component_material_kinds(root, {
        "component_material_kinds": {
            "SourceA": {"Component3": "body", "stale": "not-a-kind"},
        },
    }) == {"SourceA": {"Component3": "body"}}

    payload = {"meshes": {
        "Jacket-0": {"source": "SourceA", "component": "Component3",
                     "drawindexed": [3, 0, 0]},
        "Jacket-1": {"source": "SourceA", "component": "Component3",
                     "drawindexed": [3, 1, 0]},
        "Jacket-2": {"source": "SourceA", "component": "Component3",
                     "drawindexed": [3, 2, 0]},
        "Other-0": {"source": "SourceB", "component": "Component3",
                    "drawindexed": [3, 3, 0]},
    }}
    hydrated = metadata.hydrate_component_material_kinds(
        payload["meshes"], saved)
    assert hydrated == {"SourceA": {"Component3": "body"}}
    for name in ("Jacket-0", "Jacket-1", "Jacket-2"):
        assert payload["meshes"][name]["material_kind_evidence"] == {
            "kind": "body", "reliable": True,
            "reason": "viewer component material-kind override",
        }
        assert payload["meshes"][name]["material_kind_override"] == "body"
    assert payload["meshes"]["Other-0"]["material_kind_override"] is None
    from core.game_profile import GameDetection
    from app.mod_loader import _assign_material_profiles
    _assign_material_profiles(payload["meshes"], GameDetection(
        game="wuwa", runtime="rabbitfx", texture_api="rabbitfx",
        confidence="high", scores={}))
    assert all(payload["meshes"][name]["material_profile_id"] ==
               "wuwa:rabbitfx:body"
               for name in ("Jacket-0", "Jacket-1", "Jacket-2"))
    assert payload["meshes"]["Other-0"]["material_profile_id"] == (
        "wuwa:rabbitfx")

    assert metadata.save_component_material_kind(
        root, "SourceA", "Component3", "auto")["saved"]
    assert "component_material_kinds" not in metadata.load(root)

    reset = {
        name: {key: value for key, value in entry.items()
               if key not in ("material_kind_evidence",
                              "material_kind_override")}
        for name, entry in payload["meshes"].items()
    }
    metadata.hydrate_component_material_kinds(reset, metadata.load(root))
    assert all(entry["material_kind_override"] is None
               for entry in reset.values())
    assert all("material_kind_evidence" not in entry
               for entry in reset.values())

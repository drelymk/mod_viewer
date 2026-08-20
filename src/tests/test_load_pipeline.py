"""Focused checks for the load pipeline's cross-module contracts."""

import os
import json
import struct
import tempfile
from unittest.mock import patch


from app import edit_session, metadata, mod_loader, server
from core.ini_analysis import analyze_ini
from core.ini_document import IniDocument
from core.ini_sections import (extract_resources, sections_from_document)
from core.mesh_builder import (GeometryBlob, MeshBuildResult,
                               build_mesh_payload, build_mesh_result,
                               encode_texture_file)


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


def test_texture_registry_identity_includes_role():
    from PIL import Image

    with tempfile.TemporaryDirectory() as root:
        path = os.path.join(root, "shared.png")
        Image.new("RGB", (1, 1), (128, 128, 32)).save(path)
        diffuse = encode_texture_file(root, path)
        normal = encode_texture_file(root, path, "normal_map",
                                     texture_profile="zzz")
        light = encode_texture_file(root, path, "light_map")
        assert (diffuse["tex_key"] == "diffuse::shared.png"
              and normal["tex_key"] == "normal_map::shared.png"
              and light["tex_key"] == "light_map::shared.png"), ("texture registry keys include their usage role")
        assert (len({diffuse["uri"], normal["uri"]}) == 2
                and light["uri"] == diffuse["uri"]), ("shared sources use explicit profile transforms while packed LightMaps remain unchanged")

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
                        "texture_default_file": "shared.png",
                        "normal_map_default_file": "shared.png"}],
        }]
        built = build_mesh_result(group, root, geometry=GeometryBlob())
        entry = built.meshes["Body-1"]
        assert (set(built.textures) >= {
                  "diffuse::shared.png", "normal_map::shared.png"}
              and entry["tex_key"] == "diffuse::shared.png"
              and entry["normal_map_key"] == "normal_map::shared.png"), ("mesh building keeps shared diffuse and normal roles separate")


def test_legacy_texture_metadata_is_normalized_by_role():
    from PIL import Image

    with tempfile.TemporaryDirectory() as root:
        path = os.path.join(root, "shared.png")
        Image.new("RGB", (1, 1), (128, 128, 32)).save(path)
        with open(os.path.join(root, ".mod_viewer.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"textures": {"Body::3,0,0": {
                "tex_key": "shared.png", "label": "Shared", "manual": True,
                "normal_map": "shared.png",
            }}}, fh)
        payload = {"meshes": {"Body-1": {
            "component": "Body", "drawindexed": [3, 0, 0],
            "texture_options": [{"tex_key": "diffuse::shared.png",
                                  "file": "shared.png", "label": "Shared"}],
        }}, "textures": {}}
        metadata.hydrate_textures(root, payload)
        entry = payload["meshes"]["Body-1"]
        assert (entry.get("saved_texture_override") == "diffuse::shared.png"
              and "normal_map::shared.png" in payload["textures"]), ("legacy texture metadata is upgraded to role-aware keys")

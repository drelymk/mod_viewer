"""On-demand skin preview integration coverage."""

import struct
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.bridge.mod_preview import ModPreview
from core.geometry.mesh_builder import GeometryBlob, build_mesh_result
from core.geometry.skinning import SkinningSource
from core.ini.draw_groups import build_draw_groups
from core.ini.sections import extract_resources, merge_sections
from tests.support.skinning_data import write_gimi_skinning_mod, write_wwmi_remap_mod


class _Access:
    def mod_folder(self, path):
        return path


def test_modern_proxy_preview_uses_retained_program_and_retries_invalid_maps(tmp_path, monkeypatch):
    from core.ini.sections import parse_sections
    from tests.support.wwmi_skeleton import modern_merged_skeleton_ini

    text = modern_merged_skeleton_ini(remapped=True, influences=8, map_size=1024)
    sections = parse_sections("source-01.ini", text=text)
    groups = build_draw_groups(sections, extract_resources(sections))
    (tmp_path / "mesh.pos").write_bytes(struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0))
    (tmp_path / "mesh.idx").write_bytes(struct.pack("<6I", 0, 1, 2, 0, 2, 1))
    (tmp_path / "mesh.uv").write_bytes(bytes(16 * 3))
    (tmp_path / "mesh.vec").write_bytes(bytes(8 * 3))
    raw = bytes([0] * 8 + [128, 127] + [0] * 6) * 3
    (tmp_path / "mesh.blend").write_bytes(raw)
    (tmp_path / "full-vg.buf").write_bytes(struct.pack("<8H", 0, 256, 0, 0, 0, 0, 0, 0) * 3)
    reverse, forward = [0] * 2048, [0] * 2048
    reverse[1024 + 256] = 1
    forward[1024 + 1] = 256
    (tmp_path / "reverse.buf").write_bytes(struct.pack("<2048H", *reverse))
    forward_bytes = struct.pack("<2048H", *forward)
    (tmp_path / "forward.buf").write_bytes(forward_bytes)
    rendered = build_mesh_result(groups, str(tmp_path), geometry_only=True)
    assert len(rendered.skinning_manifest) == 2
    assert all(entry.skinning_source.blend_remap for entry in rendered.skinning_manifest.values())
    context = SimpleNamespace(mod_dir=str(tmp_path), metadata={}, asset_folders=[])
    preview = ModPreview(_Access())
    preview._skinning_manifests[str(tmp_path)] = rendered.skinning_manifest
    monkeypatch.setattr(preview, "authoritative_context", lambda _path: (str(tmp_path), {}, context))
    monkeypatch.setattr("app.bridge.mod_preview.server.publish_geometry", lambda *_args, **_kwargs: "/geometry/fixture")
    monkeypatch.setattr("core.geometry.packing._prepare_draw_vertices",
                        lambda *_args, **_kwargs: pytest.fail("Weight must reuse the loaded manifest"))
    first = preview.get_model_skinning_preview(str(tmp_path))
    assert first["status"] == "ok"
    assert all(entry["bone_ids"] == [0, 256] for entry in first["meshes"].values())
    damaged = bytearray(forward_bytes)
    struct.pack_into("<H", damaged, (1024 + 1) * 2, 255)
    (tmp_path / "forward.buf").write_bytes(damaged)
    failed = preview.get_model_skinning_preview(str(tmp_path))
    assert failed["status"] == "error"
    assert all(entry["code"] == "invalid_blend_remap" for entry in failed["meshes"].values())
    (tmp_path / "forward.buf").write_bytes(forward_bytes)
    restored = preview.get_model_skinning_preview(str(tmp_path))
    assert restored["status"] == "ok"
    assert all(entry["bone_ids"] == [0, 256] for entry in restored["meshes"].values())
    assert (tmp_path / "mesh.blend").read_bytes() == raw


def test_model_skinning_preview_matches_rendered_compaction(tmp_path, monkeypatch):
    ini = write_gimi_skinning_mod(tmp_path)
    sections = merge_sections([str(ini)])
    groups = build_draw_groups(sections, extract_resources(sections))
    geometry = GeometryBlob()
    rendered = build_mesh_result(groups, str(tmp_path), geometry=geometry)
    entry = rendered.meshes["Component01Blend-1"]
    assert entry["skinning_available"] is True
    published = {}

    def publish(blob, *, replace=True):
        published["blob"] = bytes(blob)
        published["replace"] = replace
        return "/geometry/skin-test"

    context = SimpleNamespace(
        mod_dir=str(tmp_path), ini_paths=[str(ini)], docs={}, metadata={},
        asset_folders=[])
    preview = ModPreview(_Access())
    preview._active_mesh_keys[str(tmp_path)] = set(rendered.meshes)
    preview._skinning_manifests[str(tmp_path)] = rendered.skinning_manifest
    monkeypatch.setattr(
        preview, "authoritative_context",
        lambda _path: (str(tmp_path), {}, context))
    monkeypatch.setattr(
        "app.bridge.mod_preview.server.publish_geometry", publish)
    monkeypatch.setattr(
        "app.mods.analysis.resolved_draws",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("loaded-model Weight must not resolve draws")),
    )
    monkeypatch.setattr(
        "core.geometry.packing._prepare_draw_vertices",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("loaded-model Weight must not prepare geometry")),
    )

    result = preview.get_model_skinning_preview(str(tmp_path))
    result_entry = result["meshes"]["Component01Blend-1"]

    position_length = entry["pos"]["length"]
    assert result["status"] == "ok"
    assert result_entry["vertex_count"] == position_length // 12
    assert result_entry["encoding"] == "gimi_f32_u32_4"
    assert result_entry["bone_ids"] == [7, 8, 9]
    assert result_entry["weight_stats"] == {
        "7": {"affected_vertex_count": 4, "total_weight": pytest.approx(2.4)},
        "8": {"affected_vertex_count": 4, "total_weight": pytest.approx(1.2)},
        "9": {"affected_vertex_count": 4, "total_weight": pytest.approx(.4)},
    }
    assert result_entry["data"]["indices"]["offset"] == 0
    assert result_entry["data"]["weights"]["offset"] == 4 * 4 * 4
    assert result["data"]["length"] == len(published["blob"])
    assert published["replace"] is False
    assert struct.unpack_from("<4I", published["blob"], 0) == (7, 8, 9, 0)
    assert struct.unpack_from("<4f", published["blob"], 4 * 4 * 4) == pytest.approx(
        (.6, .3, .1, 0.))
    assert list(rendered.skinning_manifest["Component01Blend-1"].used_vertices) == [
        0, 1, 2, 3]
    assert result_entry["source"]["file"] == "component01.blend"
    assert preview._last_skinning_diagnostics[str(tmp_path)][
        "mapping_source"] == "loaded_model_manifest"
    assert preview._last_skinning_diagnostics[str(tmp_path)][
        "resolve_draw_count"] == 0
    assert preview._last_skinning_diagnostics[str(tmp_path)][
        "prepare_draw_vertices_calls"] == 0


def test_model_skinning_preview_uses_wwmi_vertex_vg_identity(
        tmp_path, monkeypatch):
    ini = write_wwmi_remap_mod(tmp_path)
    sections = merge_sections([str(ini)])
    groups = build_draw_groups(sections, extract_resources(sections))
    geometry = GeometryBlob()
    rendered = build_mesh_result(groups, str(tmp_path), geometry=geometry)
    assert rendered.meshes["Component01Blend-1"]["skinning_available"] is True
    published = {}

    def publish(blob, *, replace=True):
        published["blob"] = bytes(blob)
        published["replace"] = replace
        return "/geometry/wwmi-skin-test"

    context = SimpleNamespace(
        mod_dir=str(tmp_path), ini_paths=[str(ini)], docs={}, metadata={},
        asset_folders=[])
    preview = ModPreview(_Access())
    preview._active_mesh_keys[str(tmp_path)] = set(rendered.meshes)
    preview._skinning_manifests[str(tmp_path)] = rendered.skinning_manifest
    monkeypatch.setattr(
        preview, "authoritative_context",
        lambda _path: (str(tmp_path), {}, context))
    monkeypatch.setattr(
        "app.bridge.mod_preview.server.publish_geometry", publish)
    monkeypatch.setattr(
        "app.mods.analysis.resolved_draws",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("loaded-model Weight must not resolve draws")),
    )

    result = preview.get_model_skinning_preview(str(tmp_path))
    entry = result["meshes"]["Component01Blend-1"]

    assert result["status"] == "ok"
    assert entry["bone_ids"] == [3, 259]
    assert entry["weight_stats"]["259"] == {
        "affected_vertex_count": 3,
        "total_weight": pytest.approx(3 * 128 / 255),
    }
    assert entry["diagnostics"]["bone_id_namespace"] == "wwmi_vertex_vg"
    assert entry["diagnostics"]["vertex_vg_remap"] is True
    assert entry["diagnostics"]["vertex_vg_source"] == "component01.vertex_vg"
    assert entry["diagnostics"]["vertex_vg_truncated_vertices"] == 0
    assert entry["source"]["bone_id_namespace"] == "wwmi_vertex_vg"
    assert entry["source"]["bone_ids_model_wide"] is True
    manifest_source = rendered.skinning_manifest[
        "Component01Blend-1"].skinning_source
    assert manifest_source.vertex_vg_file == "component01.vertex_vg"
    assert manifest_source.vertex_vg_stride == 16
    assert manifest_source.bone_id_namespace == "wwmi_vertex_vg"
    assert struct.unpack_from("<8I", published["blob"], 0) == (
        3, 259, 0, 0, 0, 0, 0, 0)
    assert struct.unpack_from("<8f", published["blob"], 3 * 8 * 4) == pytest.approx(
        (1., 128 / 255, 0., 0., 0., 0., 0., 0.))


def test_get_model_skinning_preview_batches_successes_and_keeps_partial_errors(
        tmp_path, monkeypatch):
    ini = write_gimi_skinning_mod(tmp_path)
    sections = merge_sections([str(ini)])
    groups = build_draw_groups(sections, extract_resources(sections))
    geometry = GeometryBlob()
    rendered = build_mesh_result(groups, str(tmp_path), geometry=geometry)
    published = []

    def publish(blob, *, replace=True):
        published.append((bytes(blob), replace))
        return "/geometry/model-skin-test"

    context = SimpleNamespace(
        mod_dir=str(tmp_path), ini_paths=[str(ini)], docs={}, metadata={},
        asset_folders=[])
    preview = ModPreview(_Access())
    monkeypatch.setattr(
        preview, "authoritative_context",
        lambda _path: (str(tmp_path), {}, context))
    monkeypatch.setattr(
        "app.bridge.mod_preview.server.publish_geometry", publish)
    entry = rendered.skinning_manifest["Component01Blend-1"]
    broken = replace(entry, skinning_source=SkinningSource(
        "missing.blend", 32, 4, "gimi_f32_u32_4"))
    preview._skinning_manifests[str(tmp_path)] = {
        "Component01Blend-1": entry, "BrokenBlend-1": broken,
    }
    preview._active_mesh_keys[str(tmp_path)] = {
        "Component01Blend-1", "BrokenBlend-1", "Static-1",
    }

    result = preview.get_model_skinning_preview(str(tmp_path))

    assert result["status"] == "partial"
    assert result["meshes"]["Component01Blend-1"]["status"] == "ok"
    assert result["meshes"]["BrokenBlend-1"]["status"] == "error"
    assert "Static-1" not in result["meshes"]
    assert result["data"]["url"] == "/geometry/model-skin-test"
    assert result["data"]["length"] == len(published[0][0])
    assert published[0][1] is False

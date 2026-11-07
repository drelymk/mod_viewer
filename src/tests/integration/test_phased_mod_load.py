"""Geometry-first loading through the ordinary editable model pipeline."""

from unittest.mock import patch
import zipfile

import pytest
from PIL import Image

from app.bridge.api import ModViewerAPI
from app.mods import loader
from app.session import edit
from core.geometry import mesh_builder
from core.geometry import batch_packing
from core.geometry.transport import GeometryBlob
from tests.support.animations import compute_mod, joined_baked_mod
from tests.support.model_data import basic_model_ini, triangle_geometry


def _bytes_for_refs(value, geometry):
    if isinstance(value, dict):
        if set(value) == {"offset", "length"}:
            start = value["offset"]
            return bytes(geometry.data[start:start + value["length"]])
        return {key: _bytes_for_refs(item, geometry) for key, item in value.items()}
    if isinstance(value, list):
        return [_bytes_for_refs(item, geometry) for item in value]
    return value


def _write_model(root, *, duplicate=False):
    text = basic_model_ini()
    if duplicate:
        text = text.replace("drawindexed = 3, 0, 0\n",
                            "drawindexed = 3, 0, 0\ndrawindexed = 3, 0, 0\n")
    (root / "mod.ini").write_text(text, encoding="utf-8")
    for name, data in triangle_geometry().items():
        (root / name).write_bytes(data)


@pytest.mark.parametrize("distinct_materials", [False, True])
@pytest.mark.parametrize("archive", [False, True])
@pytest.mark.parametrize("batch", [False, True])
def test_geometry_precedes_assets_and_final_draws_reuse_one_packing(
        tmp_path, monkeypatch, distinct_materials, archive, batch):
    if batch:
        monkeypatch.setattr(batch_packing, "_MIN_BATCH_INDICES", 0)
    _write_model(tmp_path, duplicate=True)
    for index in range(2):
        Image.new("RGB", (4, 4), (index * 200, 50, 100)).save(
            tmp_path / f"texture-{index:02}.png")
    api = ModViewerAPI()
    selected = tmp_path
    if archive:
        selected = tmp_path / "sample.zip"
        with zipfile.ZipFile(selected, "w") as source:
            for name in ["mod.ini", *triangle_geometry()]:
                source.write(tmp_path / name, name)
    root = api._access.remember_mod_picker_selection(selected)
    events = []

    def registry():
        events.append("registry")
        assert pack.call_count == 1
        return []

    original_enrich = loader.enrich_mod_analysis
    original_bind = mesh_builder.apply_draw_texture_bindings

    def bind(*args, **kwargs):
        assert events[:2] == ["registry", "assets"]
        events.append("textures")
        return original_bind(*args, **kwargs)

    def enrich(parsed, context):
        original_enrich(parsed, context)
        events.append("assets")
        assert events == ["registry", "assets"]
        for group in parsed.groups:
            for index, draw in enumerate(group["draws"]):
                texture = f"texture-{index if distinct_materials else 0:02}.png"
                draw.asset_texture_defaults["diffuse"] = {
                    "path": str(tmp_path / texture), "key": "asset/" + texture}
        return [], {"index_status": "ready", "configured_roots": 1}

    monkeypatch.setattr("app.bridge.mod_preview.asset_folders.load_registry", registry)
    monkeypatch.setattr(loader, "enrich_mod_analysis", enrich)
    monkeypatch.setattr(mesh_builder, "apply_draw_texture_bindings", bind)
    try:
        with patch.object(loader, "analyze_mod_inis", wraps=loader.analyze_mod_inis) as analyze, \
                patch.object(mesh_builder, "pack_draw_geometry",
                             wraps=mesh_builder.pack_draw_geometry) as pack:
            final = api.load_mod(root)
            assert not final.get("error")
            assert len(final["meshes"]) == (2 if distinct_materials else 1)
            assert len(final["textures"]) == (2 if distinct_materials else 1)
            assert all(mesh["tex_key"] for mesh in final["meshes"].values())
            assert analyze.call_count == pack.call_count == 1
            assert events == ["registry", "assets"] + ["textures"] * len(final["meshes"])
            assert not final["metadata"].get("assets_pending")
            assert final["metadata"]["source_read_only"] is archive
            assert api._mod_preview._active_mesh_keys[root] == set(final["meshes"])
            assert api._mod_preview._current_model_folder == root
            if not distinct_materials:
                assert len(next(iter(final["meshes"].values()))["sources"]) == 2
    finally:
        edit.discard(root)


@pytest.mark.parametrize("kind", ["baked", "compute"])
def test_geometry_first_animation_reuses_packed_bytes(
        tmp_path, monkeypatch, kind):
    root_path = tmp_path / "mod"
    if kind == "baked":
        root_path.mkdir()
        joined_baked_mod(root_path)
    else:
        compute_mod(root_path)
    monkeypatch.setattr("app.bridge.mod_preview.asset_folders.load_registry", lambda: [])
    reference_geometry = GeometryBlob()
    context = loader._resolve_context(str(root_path))
    parsed = loader.analyze_mod_inis(context.ini)
    loader.enrich_mod_analysis(parsed, context)
    reference = mesh_builder.build_mesh_result(
        parsed.groups, str(root_path), geometry=reference_geometry,
        game_profile=parsed.game.game, source=context.source,
        animations=parsed.animations)
    assert any(mesh.get("animation_geometry") for mesh in reference.meshes.values())
    api = ModViewerAPI()
    root = api._access.remember_mod_picker_selection(root_path)

    try:
        with patch.object(mesh_builder, "pack_draw_geometry",
                          wraps=mesh_builder.pack_draw_geometry) as pack, \
                patch.object(mesh_builder, "_prepare_animation_family",
                             wraps=mesh_builder._prepare_animation_family) as baked, \
                patch.object(mesh_builder, "_prepare_gimi_geometry",
                             wraps=mesh_builder._prepare_gimi_geometry) as compute:
            captured = {}
            original_enrich = loader.enrich_mod_analysis

            def enrich(parsed, context):
                captured["calls"] = (pack.call_count, baked.call_count, compute.call_count)
                return original_enrich(parsed, context)

            monkeypatch.setattr(loader, "enrich_mod_analysis", enrich)
            geometry = GeometryBlob()
            with patch("app.bridge.mod_preview.GeometryBlob", return_value=geometry):
                final = api.load_mod(root)
            assert not final.get("error")
            assert (pack.call_count, baked.call_count, compute.call_count) == captured["calls"]
        assert (final.get("animations") or {}) == reference.animations
        for name, mesh in reference.meshes.items():
            for field in ("pos", "idx", "uv", "normal", "shape_targets", "animation_geometry"):
                assert _bytes_for_refs(final["meshes"][name].get(field), geometry) == \
                    _bytes_for_refs(mesh.get(field), reference_geometry)
    finally:
        edit.discard(root)


@pytest.mark.parametrize("distinct_materials", [False, True])
def test_asset_material_regrouping_does_not_orphan_animation_geometry(
        tmp_path, monkeypatch, distinct_materials):
    ini = joined_baked_mod(tmp_path)
    ini.write_text("""[Constants]
$start = 0
$end = 2
[Present]
$frame = (time * 30 % ($end - $start + 1) + $start) // 1
[TextureOverrideModelA]
ib = ResourceModelAIB
vb1 = ResourceModelTexcoord
""" + "\n".join(
        f"{'if' if frame == 0 else 'elif'} $frame == {frame}\n"
        f"vb0 = ResourceModelPosition{frame}\ndrawindexed = 3, 0, 0"
        for frame in range(3)) + "\nendif\n" + "\n".join(
        f"[ResourceModelPosition{frame}]\nfilename = position{frame}.buf\nstride = 40"
        for frame in range(3)) + """
[ResourceModelTexcoord]
filename = texcoord0.buf
stride = 8
[ResourceModelAIB]
filename = index0.buf
format = DXGI_FORMAT_R32_UINT
""", encoding="utf-8")
    for number in range(2):
        Image.new("RGB", (4, 4), (number * 200, 50, 100)).save(tmp_path / f"asset-{number}.png")
    original_enrich = loader.enrich_mod_analysis

    def enrich(parsed, context):
        result = original_enrich(parsed, context)
        for group in parsed.groups:
            for number, draw in enumerate(group["draws"]):
                filename = f"asset-{int(distinct_materials and number > 0)}.png"
                draw.asset_texture_defaults["diffuse"] = {
                    "path": str(tmp_path / filename), "key": "asset/" + filename}
        return result

    monkeypatch.setattr(loader, "enrich_mod_analysis", enrich)
    context = loader._resolve_context(str(tmp_path))
    parsed = loader.analyze_mod_inis(context.ini)
    enrich(parsed, context)
    reference_geometry = GeometryBlob()
    reference_geometry.add(b"existing bytes")
    reference = mesh_builder.build_mesh_result(
        parsed.groups, str(tmp_path), source=context.source, animations=parsed.animations,
        geometry=reference_geometry)
    geometry = GeometryBlob()
    geometry.add(b"existing bytes")
    final = loader.load_mod(context=context, geometry=geometry)
    assert not final.get("error")
    assert set(final["meshes"]) == set(reference.meshes)
    assert (final.get("animations") or {}) == reference.animations
    for name, mesh in reference.meshes.items():
        assert final["meshes"][name]["tex_key"] == mesh["tex_key"]
        for field in ("pos", "idx", "uv", "normal", "shape_targets", "animation_geometry"):
            assert _bytes_for_refs(final["meshes"][name].get(field), geometry) == \
                _bytes_for_refs(mesh.get(field), reference.geometry)
    assert geometry.data[:len(b"existing bytes")] == b"existing bytes"
    assert len(geometry) == len(reference.geometry)

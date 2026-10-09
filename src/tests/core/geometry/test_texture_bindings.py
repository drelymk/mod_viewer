"""Texture registry identity and lazy-option regressions."""

import io
import zipfile
from unittest.mock import Mock, patch

from core.geometry.draw_call import DrawCall
from core.geometry.texture_bindings import (
    TextureRegistry, apply_draw_texture_bindings, build_texture_options,
    finalize_texture_candidates,
)
from core.mod_source import DirectoryModSource, ZipModSource
from core.textures.profiles import texture_profile_for
from core.textures.pipeline import encode_texture_file


def test_texture_pool_publication_and_reload_lifecycle(tmp_path):
    for name in ("pool.dds", "discovered.dds", "resolved.dds", "variant.dds",
                 "inactive.dds", "unrelated.dds", "data.dds"):
        (tmp_path / name).write_bytes(b"texture")
    source = DirectoryModSource(tmp_path)
    publish = Mock(side_effect=lambda path, role: f"/texture/{role}")

    def registry():
        return TextureRegistry(str(tmp_path), texture_profile_for("genshin"),
                               source=source, texture_source=publish)

    with patch.object(source, "resolve_resource",
                      wraps=source.resolve_resource) as resolve:
        first = registry()
        draw = DrawCall(
            texture_default_file="resolved.dds",
            texture_variants=[
                {"file": "resolved.dds", "conditions": []},
                {"file": "variant.dds", "conditions": []}],
            normal_map_default_file="data.dds")
        before = draw.render_identity()
        entry = {}
        apply_draw_texture_bindings(entry, draw, registry=first)
        conditional = DrawCall(
            texture_variants=[{"file": "inactive.dds", "conditions": [[{
                "var": "style", "value": "1", "negate": False}]]}],
            normal_map_default_file="data.dds")
        conditional_entry = {}
        apply_draw_texture_bindings(conditional_entry, conditional, registry=first)
        assert conditional_entry["tex_key"] is None
        assert conditional_entry["texture_variants"] == [{
            "tex_key": "diffuse::inactive.dds", "conditions": [[{
                "var": "style", "value": "1", "negate": False}]]}]
        rendered_sources = first.sources
        assert len(rendered_sources) == 4
        publish.reset_mock()
        group = {
            "texture_candidates": [
                *[{"file": filename, "res": f"ResourceChoice{ordinal}"}
                for ordinal, filename in enumerate((
                    "pool.dds", "missing.dds", "../outside.dds"))],
                {"file": "discovered.dds", "source": "mod"}],
            "draws": [draw, conditional],
        }
        finalize_texture_candidates(group)
        options = build_texture_options(group, first)
        assert [item["tex_key"] for item in options] == [
            "diffuse::pool.dds", "diffuse::discovered.dds",
            "diffuse::resolved.dds", "diffuse::variant.dds",
            "diffuse::data.dds", "diffuse::inactive.dds"]
        assert draw.render_identity() == before
        assert first.sources == rendered_sources
        publish.assert_not_called()
        assert [item.get("normal_map") for item in options] == [
            None, None, "normal_map::data.dds", "normal_map::data.dds",
            None, "normal_map::data.dds"]

        resolve.reset_mock()
        for _ in range(2):
            path = first.resolve("pool.dds")
            assert first.ensure(path, "diffuse") == "diffuse::pool.dds"
            assert first.ensure(path, "normal_map") == "normal_map::pool.dds"
        resolve.assert_not_called()
        assert publish.call_count == 2

        resolve.reset_mock()
        publish.reset_mock()
        second = registry()
        assert second.ensure(second.resolve("pool.dds")) == "diffuse::pool.dds"
        resolve.assert_called_once_with("pool.dds")
        publish.assert_called_once()


def test_zip_texture_registry_and_picker_read_member_bytes(tmp_path):
    from PIL import Image

    image_bytes = io.BytesIO()
    Image.new("RGB", (1, 1), (20, 40, 60)).save(image_bytes, format="PNG")
    archive_path = tmp_path / "sample.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("Wrapped/textures/component01.png", image_bytes.getvalue())

    source = ZipModSource(archive_path)
    texture_path = source.resolve_resource("textures/component01.png")
    registry = TextureRegistry(
        str(archive_path), texture_profile_for("genshin"), source=source)

    entry = {}
    apply_draw_texture_bindings(entry, DrawCall(
        texture_default_file="textures/component01.png"), registry=registry)
    group = {"draws": [DrawCall(texture_default_file="textures/component01.png")]}
    finalize_texture_candidates(group)
    options = build_texture_options(group, registry)
    assert [item["tex_key"] for item in options] == [
        "diffuse::textures/component01.png"]
    assert registry.ensure(texture_path) == "diffuse::textures/component01.png"
    assert registry.sources == {}
    assert encode_texture_file(str(archive_path), texture_path, source=source)["error_code"] == "texture_load_failed"
    selected = encode_texture_file(
        str(archive_path), texture_path, source=source,
        texture_source=lambda _path, _role: "/texture/test/0.png")
    assert selected["file"] == "textures/component01.png"
    assert selected["uri"] == "/texture/test/0.png"

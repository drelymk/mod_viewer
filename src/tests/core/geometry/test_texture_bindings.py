"""Texture registry identity and lazy-option regressions."""

import io
import zipfile
from unittest.mock import Mock, patch

from core.geometry.texture_bindings import TextureRegistry, build_texture_options
from core.mod_source import DirectoryModSource, ZipModSource
from core.textures.profiles import texture_profile_for
from core.textures.pipeline import encode_texture_file


def test_texture_pool_publication_and_reload_lifecycle(tmp_path):
    for name in ("pool.dds", "discovered.dds"):
        (tmp_path / name).write_bytes(b"texture")
    source = DirectoryModSource(tmp_path)
    publish = Mock(side_effect=lambda path, role: f"/texture/{role}")

    def registry():
        return TextureRegistry(str(tmp_path), texture_profile_for("genshin"),
                               source=source, texture_source=publish)

    with patch.object(source, "resolve_resource",
                      wraps=source.resolve_resource) as resolve:
        first = registry()
        options = build_texture_options({
            "diffuse_pool_files": [{"file": "pool.dds", "res": "ResourcePool"}],
            "discovered_textures": [{"file": "discovered.dds", "source": "scan"}],
        }, first)
        assert [item["tex_key"] for item in options] == [
            "diffuse::pool.dds", "diffuse::discovered.dds"]
        assert first.sources == {}

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

    assert registry.ensure(texture_path) == "diffuse::textures/component01.png"
    assert registry.sources["diffuse::textures/component01.png"].startswith(
        "data:image/png;base64,")
    selected = encode_texture_file(
        str(archive_path), texture_path, source=source)
    assert selected["file"] == "textures/component01.png"
    assert selected["uri"].startswith("data:image/png;base64,")

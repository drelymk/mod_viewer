"""Texture registry identity and lazy-option regressions."""

import io
import os
import zipfile

from core.geometry.texture_bindings import TextureRegistry, build_texture_options
from core.mod_source import ZipModSource
from core.textures.profiles import texture_profile_for
from core.textures.pipeline import encode_texture_file


def test_pool_only_texture_options_are_not_published(tmp_path):
    pool = tmp_path / "pool.dds"
    discovered = tmp_path / "discovered.dds"
    pool.write_bytes(b"pool")
    discovered.write_bytes(b"discovered")
    registry = TextureRegistry(
        str(tmp_path), texture_profile_for("genshin"),
        texture_source=lambda path, role, **kwargs:
        f"/texture/{os.path.basename(path)}")

    options = build_texture_options({
        "diffuse_pool_files": [{"file": "pool.dds", "res": "ResourcePool"}],
        "discovered_textures": [{"file": "discovered.dds", "source": "scan"}],
    }, registry)

    assert [item["tex_key"] for item in options] == [
        "diffuse::pool.dds", "diffuse::discovered.dds"]
    assert registry.sources == {}

    assert registry.ensure(str(pool), "diffuse") == "diffuse::pool.dds"
    assert registry.sources == {"diffuse::pool.dds": "/texture/pool.dds"}


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

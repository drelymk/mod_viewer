"""Texture registry identity and lazy-option regressions."""

import os

from core.geometry.texture_bindings import TextureRegistry, build_texture_options
from core.textures.profiles import texture_profile_for


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

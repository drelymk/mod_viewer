"""Asset texture identity regressions."""

from app.assets.textures import (
    asset_texture_key, is_asset_texture_key,
)
from core.textures import texture_key


def test_asset_texture_key_detection_uses_role_aware_relative_identity(tmp_path):
    root = tmp_path / "assets"
    root.mkdir()
    texture = root / "body.dds"
    texture.write_bytes(b"dds")

    key = asset_texture_key(root, texture)

    assert key.startswith("diffuse::asset/")
    assert is_asset_texture_key(key)
    assert is_asset_texture_key(texture_key(key.split("::", 1)[1], "normal_map"))
    assert not is_asset_texture_key("diffuse::textures/body.dds")
    assert not is_asset_texture_key("body.dds")

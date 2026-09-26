"""Asset-root path containment regressions."""

import os

from app.assets.paths import safe_asset_dir, safe_asset_path


def test_safe_asset_path_rejects_parent_escape_and_missing_files(tmp_path):
    root = tmp_path / "assets"
    root.mkdir()
    (root / "inside.dds").write_bytes(b"texture")

    assert safe_asset_path(str(root), "inside.dds") == os.path.realpath(
        root / "inside.dds")
    assert safe_asset_path(str(root), "../inside.dds") is None
    assert safe_asset_path(str(root), "missing.dds") is None
    assert safe_asset_dir(str(root), ".") == os.path.realpath(root)

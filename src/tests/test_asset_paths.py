"""Asset-root path containment regressions."""

import os

import pytest

from app.asset_paths import safe_asset_dir, safe_asset_path


def test_safe_asset_path_rejects_parent_escape_and_missing_files(tmp_path):
    root = tmp_path / "assets"
    root.mkdir()
    (root / "inside.dds").write_bytes(b"texture")

    assert safe_asset_path(str(root), "inside.dds") == os.path.realpath(
        root / "inside.dds")
    assert safe_asset_path(str(root), "../inside.dds") is None
    assert safe_asset_path(str(root), "missing.dds") is None
    assert safe_asset_dir(str(root), ".") == os.path.realpath(root)


def test_safe_asset_path_rejects_symlink_escape_when_supported(tmp_path):
    root = tmp_path / "assets"
    outside = tmp_path / "outside.dds"
    root.mkdir()
    outside.write_bytes(b"outside")
    link = root / "link.dds"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink fixtures are unavailable")
    assert safe_asset_path(str(root), "link.dds") is None

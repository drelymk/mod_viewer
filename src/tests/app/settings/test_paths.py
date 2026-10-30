"""Runtime data roots for source checkouts and frozen bundles."""

import os
from pathlib import Path

import pytest

import build
from app.settings import paths


def test_source_data_paths_resolve_from_source_root():
    source_root = Path(paths.__file__).resolve().parents[2]

    assert Path(paths.app_root()) == source_root
    assert Path(paths.web_dir()) == source_root / "web"
    assert Path(paths.vendor_dir()) == source_root / "assets"


def test_config_path_is_next_to_executable_when_frozen(monkeypatch, tmp_path):
    monkeypatch.setattr(paths.sys, "_MEIPASS", str(tmp_path / "bundle"),
                        raising=False)
    monkeypatch.setattr(paths.sys, "executable", str(tmp_path / "ModViewer.exe"))

    assert paths.config_path() == os.path.join(str(tmp_path), "config.json")


@pytest.mark.parametrize("missing", [
    "addons/exporters/GLTFExporter.js",
    "addons/tsl/display/SSAONode.js",
    "addons/tsl/display/depthAwareBlur.js",
    "LICENSE",
])
def test_vendored_three_rejects_incomplete_ao_dependencies(monkeypatch, tmp_path, missing):
    monkeypatch.setattr(paths, "vendor_dir", lambda: str(tmp_path))
    for name in build.ASSET_FILES:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("fixture", encoding="utf-8")
    assert paths.has_vendored_three()
    target = tmp_path / missing
    target.unlink()
    assert not paths.has_vendored_three()
    target.write_text("fixture", encoding="utf-8")
    assert paths.has_vendored_three()

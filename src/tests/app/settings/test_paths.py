"""Runtime data roots for source checkouts and frozen bundles."""

import os
from pathlib import Path

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


def test_vendored_three_requires_glb_exporter_and_license(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "vendor_dir", lambda: str(tmp_path))
    for name in build.ASSET_FILES:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("fixture", encoding="utf-8")
    assert paths.has_vendored_three()
    for name in ["addons/exporters/GLTFExporter.js", "LICENSE"]:
        target = tmp_path / name
        target.unlink()
        assert not paths.has_vendored_three()
        target.write_text("fixture", encoding="utf-8")
        assert paths.has_vendored_three()

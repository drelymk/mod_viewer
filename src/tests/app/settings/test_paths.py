"""Runtime data roots for source checkouts and frozen bundles."""

import os
from pathlib import Path

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

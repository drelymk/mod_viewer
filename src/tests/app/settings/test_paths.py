"""Runtime data roots for source checkouts and frozen bundles."""

from pathlib import Path

from app.settings import paths


def test_source_data_paths_resolve_from_source_root():
    source_root = Path(paths.__file__).resolve().parents[2]

    assert Path(paths.app_root()) == source_root
    assert Path(paths.web_dir()) == source_root / "web"
    assert Path(paths.vendor_dir()) == source_root / "assets"

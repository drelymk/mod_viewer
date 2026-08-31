import json
import os

import pytest

from app.bridge.access import FolderAccess
from app.settings import paths
from app.settings import mod_folders


def _config(tmp_path, *, mod_entries=None, asset_entries=None):
    filename = tmp_path / "config.json"
    filename.write_text(json.dumps({
        "version": 1,
        "modFolders": mod_entries or [],
        "assetFolders": asset_entries or [],
    }), encoding="utf-8")
    return filename


def test_folder_access_preserves_picker_asymmetry_and_root_lifecycles(
        tmp_path, monkeypatch):
    mod_root = tmp_path / "mods"
    mod_child = mod_root / "character"
    asset_root = tmp_path / "assets"
    asset_child = asset_root / "character"
    mod_child.mkdir(parents=True)
    asset_child.mkdir(parents=True)
    config = _config(tmp_path)
    monkeypatch.setattr(paths, "config_path", lambda: str(config))

    access = FolderAccess()
    selected = access.remember_mod_picker_selection(str(mod_root))

    assert selected == mod_folders.normalize_path(str(mod_root))
    assert access.mod_folder(str(mod_root)) == selected
    with pytest.raises(PermissionError):
        access.asset_folder(str(mod_root))

    selected = access.remember_asset_picker_selection(str(asset_root))

    assert selected == mod_folders.normalize_path(str(asset_root))
    assert access.was_picker_selected(str(asset_root))
    with pytest.raises(PermissionError):
        access.mod_folder(str(asset_root))

    access.refresh_mod_roots([{"name": "Mods", "path": str(mod_root)}])
    normalized_child = access.mod_folder(str(mod_child))
    access.refresh_mod_roots([])

    assert access.mod_folder(str(mod_child)) == normalized_child
    with pytest.raises(PermissionError):
        access.mod_folder(str(tmp_path / "outside"))

    entry = {"type": "GIMI", "path": str(asset_root), "enabled": True}
    access.refresh_asset_roots([entry])
    assert access.asset_folder(str(asset_child)) == os.path.normcase(os.path.abspath(asset_child))
    access.refresh_asset_roots([])

    with pytest.raises(PermissionError):
        access.asset_folder(str(asset_child))


def test_malformed_optional_config_does_not_block_access_construction(
        tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    config.write_text("{", encoding="utf-8")
    monkeypatch.setattr(paths, "config_path", lambda: str(config))

    access = FolderAccess()

    assert isinstance(access, FolderAccess)


def test_launch_selection_is_exact_and_not_picker_authorized(
        tmp_path, monkeypatch):
    config = _config(tmp_path)
    launch_folder = tmp_path / "mods" / "launch"
    sibling = tmp_path / "mods" / "sibling"
    launch_folder.mkdir(parents=True)
    sibling.mkdir()
    monkeypatch.setattr(paths, "config_path", lambda: str(config))

    access = FolderAccess()
    selected = access.remember_mod_launch_selection(str(launch_folder))

    assert selected == mod_folders.normalize_path(str(launch_folder))
    assert access.mod_folder(str(launch_folder)) == selected
    assert access.was_picker_selected(str(launch_folder)) is False
    with pytest.raises(PermissionError):
        access.mod_folder(str(sibling))
    with pytest.raises(PermissionError):
        access.mod_folder(str(launch_folder.parent))
    assert access._authorized_roots == set()
    assert json.loads(config.read_text(encoding="utf-8"))["modFolders"] == []


def test_launch_selection_rejects_relative_and_missing_paths(
        tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "config_path", lambda: str(_config(tmp_path)))
    access = FolderAccess()

    with pytest.raises(ValueError, match="Startup mod path must be absolute"):
        access.remember_mod_launch_selection("relative-mod")
    with pytest.raises(ValueError, match="Startup mod folder does not exist"):
        access.remember_mod_launch_selection(str(tmp_path / "missing"))

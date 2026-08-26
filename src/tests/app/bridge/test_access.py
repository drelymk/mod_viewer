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


def test_mod_picker_grants_mod_access_but_not_asset_access(tmp_path, monkeypatch):
    root = tmp_path / "mods"
    root.mkdir()
    config = _config(tmp_path)
    monkeypatch.setattr(paths, "config_path", lambda: str(config))

    access = FolderAccess()
    selected = access.remember_mod_picker_selection(str(root))

    assert selected == mod_folders.normalize_path(str(root))
    assert access.mod_folder(str(root)) == selected
    with pytest.raises(PermissionError):
        access.asset_folder(str(root))


def test_asset_picker_uses_shared_picker_proof_without_mod_access(
        tmp_path, monkeypatch):
    root = tmp_path / "assets"
    root.mkdir()
    config = _config(tmp_path)
    monkeypatch.setattr(paths, "config_path", lambda: str(config))

    access = FolderAccess()
    selected = access.remember_asset_picker_selection(str(root))

    assert selected == mod_folders.normalize_path(str(root))
    assert access.was_picker_selected(str(root))
    with pytest.raises(PermissionError):
        access.mod_folder(str(root))


def test_mod_descendant_access_is_cached_after_registered_root_changes(
        tmp_path, monkeypatch):
    root = tmp_path / "mods"
    child = root / "character"
    child.mkdir(parents=True)
    config = _config(tmp_path)
    monkeypatch.setattr(paths, "config_path", lambda: str(config))
    access = FolderAccess()

    access.refresh_mod_roots([{"name": "Mods", "path": str(root)}])
    normalized_child = access.mod_folder(str(child))
    access.refresh_mod_roots([])

    assert access.mod_folder(str(child)) == normalized_child
    with pytest.raises(PermissionError):
        access.mod_folder(str(tmp_path / "outside"))


def test_asset_descendant_access_is_revoked_when_root_is_removed(
        tmp_path, monkeypatch):
    root = tmp_path / "assets"
    child = root / "character"
    child.mkdir(parents=True)
    config = _config(tmp_path)
    monkeypatch.setattr(paths, "config_path", lambda: str(config))
    access = FolderAccess()
    entry = {"type": "GIMI", "path": str(root), "enabled": True}

    access.refresh_asset_roots([entry])
    assert access.asset_folder(str(child)) == os.path.normcase(os.path.abspath(child))
    access.refresh_asset_roots([])

    with pytest.raises(PermissionError):
        access.asset_folder(str(child))


def test_malformed_optional_config_does_not_block_access_construction(
        tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    config.write_text("{", encoding="utf-8")
    monkeypatch.setattr(paths, "config_path", lambda: str(config))

    access = FolderAccess()

    assert isinstance(access, FolderAccess)

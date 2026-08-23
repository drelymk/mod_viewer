import json
import os

import pytest

from app import asset_folders, mod_folders, paths
from app.api import ModViewerAPI


def _config(tmp_path, entries=None):
    filename = str(tmp_path / "config.json")
    with open(filename, "w", encoding="utf-8") as stream:
        json.dump({"version": 1, "modFolders": entries or []}, stream)
    return filename


def _directory(parent, name):
    path = parent / name
    path.mkdir()
    return str(path)


def test_missing_asset_folders_defaults_to_empty_and_preserves_existing_config(tmp_path):
    filename = _config(tmp_path)
    assert asset_folders.load_registry(filename) == []
    root = _directory(tmp_path, "gimi")
    asset_folders.add_folder("GIMI", root, filename)
    saved = json.loads(open(filename, encoding="utf-8").read())
    assert saved["assetFolders"] == [{"type": "GIMI", "path": mod_folders.normalize_path(root)}]
    assert saved["modFolders"] == []


@pytest.mark.parametrize("asset_type", ["GIMI", "ZZMI", "WWMI"])
def test_supported_asset_types_and_multiple_roots(tmp_path, asset_type):
    filename = _config(tmp_path)
    first = _directory(tmp_path, f"{asset_type}-one")
    second = _directory(tmp_path, f"{asset_type}-two")
    entries = asset_folders.add_folder(asset_type, first, filename)
    entries = asset_folders.add_folder(asset_type, second, filename)
    assert [entry["type"] for entry in entries] == [asset_type, asset_type]


@pytest.mark.parametrize("asset_type", ["gimi", "SRMI", ""])
def test_invalid_asset_type_is_rejected(tmp_path, asset_type):
    with pytest.raises(asset_folders.AssetFolderError, match="GIMI, ZZMI or WWMI"):
        asset_folders.add_folder(asset_type, _directory(tmp_path, "root"), _config(tmp_path))


def test_duplicate_normalized_path_is_rejected(tmp_path):
    filename = _config(tmp_path)
    root = _directory(tmp_path, "root")
    asset_folders.add_folder("GIMI", root, filename)
    with pytest.raises(asset_folders.AssetFolderError, match="already registered"):
        asset_folders.add_folder("ZZMI", os.path.join(root, "."), filename)


def test_edit_type_and_delete_preserve_files(tmp_path):
    filename = _config(tmp_path)
    root = _directory(tmp_path, "root")
    asset_folders.add_folder("GIMI", root, filename)
    entries = asset_folders.edit_folder(root, "WWMI", root, filename)
    assert entries == [{"type": "WWMI", "path": mod_folders.normalize_path(root)}]
    assert asset_folders.delete_folder(root, filename) == []
    assert os.path.isdir(root)


def test_asset_child_listing_is_lazy_and_contained(tmp_path):
    filename = _config(tmp_path)
    root = _directory(tmp_path, "root")
    child = _directory(tmp_path / "root", "Character")
    nested = _directory(tmp_path / "root" / "Character", "Nested")
    asset_folders.add_folder("GIMI", root, filename)
    result = asset_folders.list_subfolders(root, root)
    assert [item["path"] for item in result] == [mod_folders.normalize_path(child)]
    assert nested not in [item["path"] for item in result]


def test_api_asset_authorization_is_separate_from_mod_roots(tmp_path, monkeypatch):
    filename = _config(tmp_path)
    mod_root = _directory(tmp_path, "mods")
    asset_root = _directory(tmp_path, "assets")
    monkeypatch.setattr(paths, "config_path", lambda: filename)
    api = ModViewerAPI()
    api._authorized_folders.add(mod_folders.normalize_path(mod_root))
    api._picker_authorized_folders.update({
        mod_folders.normalize_path(mod_root),
        mod_folders.normalize_path(asset_root),
    })
    assert api.add_mod_folder("Mods", mod_root).get("folders")
    assert api.add_asset_folder("GIMI", asset_root).get("folders")
    with pytest.raises(PermissionError):
        api._folder(asset_root)
    with pytest.raises(PermissionError):
        api._asset_folder(mod_root)
    assert api._asset_folder(asset_root) == mod_folders.normalize_path(asset_root)

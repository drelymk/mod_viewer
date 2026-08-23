import json
import os
from types import SimpleNamespace

import pytest

from app import asset_folders, mod_folders, paths
from app.api import ModViewerAPI


def _config(tmp_path, entries=None, asset_entries=None):
    filename = str(tmp_path / "config.json")
    with open(filename, "w", encoding="utf-8") as stream:
        json.dump({"version": 1, "modFolders": entries or [],
                   "assetFolders": asset_entries or []}, stream)
    return filename


def _directory(parent, name):
    path = parent / name
    path.mkdir()
    return str(path)


def _gimi_asset(root, name="Character"):
    asset = os.path.join(root, name)
    os.makedirs(asset)
    with open(os.path.join(asset, "hash.json"), "w", encoding="utf-8") as stream:
        json.dump([{"ib": "3d7b9c89", "object_indexes": [0]}], stream)
    return asset


def test_missing_asset_folders_defaults_to_empty_and_preserves_existing_config(tmp_path):
    filename = str(tmp_path / "config.json")
    with open(filename, "w", encoding="utf-8") as stream:
        json.dump({"version": 1, "modFolders": []}, stream)
    assert asset_folders.load_registry(filename) == []
    root = _directory(tmp_path, "gimi")
    asset_folders.add_folder("GIMI", root, filename)
    saved = json.loads(open(filename, encoding="utf-8").read())
    assert saved["assetFolders"] == [{
        "type": "GIMI", "path": mod_folders.normalize_path(root), "enabled": True}]
    assert saved["modFolders"] == []


def test_legacy_asset_entry_defaults_to_enabled(tmp_path):
    root = _directory(tmp_path, "legacy")
    filename = _config(tmp_path, asset_entries=[{
        "type": "GIMI", "path": root}])

    assert asset_folders.load_registry(filename) == [{
        "type": "GIMI", "path": mod_folders.normalize_path(root), "enabled": True}]


@pytest.mark.parametrize("enabled", [None, 0, 1, "true", []])
def test_non_boolean_asset_enabled_is_rejected(tmp_path, enabled):
    root = _directory(tmp_path, "invalid-enabled")
    filename = _config(tmp_path, asset_entries=[{
        "type": "GIMI", "path": root, "enabled": enabled}])

    with pytest.raises(asset_folders.AssetFolderError, match="enabled must be a boolean"):
        asset_folders.load_registry(filename)


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
    asset_folders.set_enabled(root, False, filename)
    entries = asset_folders.edit_folder(root, "WWMI", root, filename)
    assert entries == [{
        "type": "WWMI", "path": mod_folders.normalize_path(root), "enabled": False}]
    assert asset_folders.delete_folder(root, filename) == []
    assert os.path.isdir(root)


def test_set_enabled_changes_only_matching_participation(tmp_path):
    filename = _config(tmp_path)
    root = _directory(tmp_path, "root")
    asset_folders.add_folder("GIMI", root, filename)

    disabled = asset_folders.set_enabled(root, False, filename)
    assert disabled == [{
        "type": "GIMI", "path": mod_folders.normalize_path(root), "enabled": False}]
    assert asset_folders.set_enabled(root, True, filename)[0]["enabled"] is True
    with pytest.raises(asset_folders.AssetFolderError, match="enabled must be a boolean"):
        asset_folders.set_enabled(root, 1, filename)


def test_enabled_entries_filter_by_type_without_dropping_registry_state():
    entries = [
        {"type": "GIMI", "path": "gimi-current", "enabled": True},
        {"type": "GIMI", "path": "gimi-old", "enabled": False},
        {"type": "ZZMI", "path": "zzmi-current", "enabled": True},
        {"type": "WWMI", "path": "wwmi-legacy"},
    ]

    assert asset_folders.enabled_entries(entries) == [entries[0], entries[2], entries[3]]
    assert asset_folders.enabled_entries_for_type(entries, "GIMI") == [entries[0]]
    assert entries[1] in entries


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
    _gimi_asset(asset_root)
    monkeypatch.setattr(paths, "config_path", lambda: filename)
    api = ModViewerAPI()
    api._authorized_folders.add(mod_folders.normalize_path(mod_root))
    api._picker_authorized_folders.update({
        mod_folders.normalize_path(mod_root),
        mod_folders.normalize_path(asset_root),
    })
    assert api.add_mod_folder("Mods", mod_root).get("folders")
    assert api.add_asset_folder("GIMI", asset_root).get("folders")[0]["enabled"] is True
    with pytest.raises(PermissionError):
        api._folder(asset_root)
    with pytest.raises(PermissionError):
        api._asset_folder(mod_root)
    assert api._asset_folder(asset_root) == mod_folders.normalize_path(asset_root)
    api._authorized_asset_folders.clear()
    assert api.set_asset_folder_enabled(asset_root, False)["folders"][0]["enabled"] is False
    assert api._asset_folder(os.path.join(asset_root, "Character")) == \
        mod_folders.normalize_path(os.path.join(asset_root, "Character"))
    assert [item["name"] for item in
            api.list_asset_subfolders(asset_root)["folders"]] == ["Character"]


def test_asset_picker_does_not_grant_mod_folder_access(tmp_path, monkeypatch):
    filename = _config(tmp_path)
    asset_root = _directory(tmp_path, "assets")
    _gimi_asset(asset_root)
    monkeypatch.setattr(paths, "config_path", lambda: filename)
    api = ModViewerAPI()
    api._window = SimpleNamespace(
        create_file_dialog=lambda *_args: [asset_root])

    assert api.select_asset_folder() == mod_folders.normalize_path(asset_root)
    assert mod_folders.normalize_path(asset_root) in api._picker_authorized_folders
    assert mod_folders.normalize_path(asset_root) not in api._authorized_folders
    with pytest.raises(PermissionError):
        api._folder(asset_root)


def test_api_asset_authorization_is_revoked_when_root_is_deleted(tmp_path, monkeypatch):
    filename = _config(tmp_path)
    asset_root = _directory(tmp_path, "assets")
    _gimi_asset(asset_root)
    monkeypatch.setattr(paths, "config_path", lambda: filename)
    api = ModViewerAPI()
    api._picker_authorized_folders.add(mod_folders.normalize_path(asset_root))

    assert api.add_asset_folder("GIMI", asset_root).get("folders")
    cached_child = os.path.join(asset_root, "Character")
    assert api._asset_folder(cached_child) == mod_folders.normalize_path(cached_child)

    assert api.delete_asset_folder(asset_root).get("folders") == []
    with pytest.raises(PermissionError):
        api._asset_folder(cached_child)

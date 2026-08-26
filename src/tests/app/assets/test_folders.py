import json
import os
import pytest

from app.assets import folders as asset_folders
from app.settings import mod_folders as mod_folders


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


def test_asset_folder_registry_lifecycle(tmp_path):
    filename = str(tmp_path / "config.json")
    with open(filename, "w", encoding="utf-8") as stream:
        json.dump({"version": 1, "modFolders": []}, stream)
    assert asset_folders.load_registry(filename) == []
    root = _directory(tmp_path, "gimi")
    entries = asset_folders.add_folder("GIMI", root, filename)
    saved = json.loads(open(filename, encoding="utf-8").read())
    assert saved["assetFolders"] == [{
        "type": "GIMI", "path": mod_folders.normalize_path(root), "enabled": True}]
    assert saved["modFolders"] == []
    assert asset_folders.set_enabled(root, False, filename)[0]["enabled"] is False
    entries = asset_folders.edit_folder(root, "WWMI", root, filename)
    assert entries == [{
        "type": "WWMI", "path": mod_folders.normalize_path(root), "enabled": False}]
    assert asset_folders.set_enabled(root, True, filename)[0]["enabled"] is True
    assert asset_folders.delete_folder(root, filename) == []
    assert os.path.isdir(root)

    legacy = _directory(tmp_path, "legacy")
    filename = _config(tmp_path, asset_entries=[{"type": "GIMI", "path": legacy}])
    assert asset_folders.load_registry(filename) == [{
        "type": "GIMI", "path": mod_folders.normalize_path(legacy), "enabled": True}]


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


def test_set_enabled_rejects_non_boolean_value(tmp_path):
    filename = _config(tmp_path)
    root = _directory(tmp_path, "root")
    asset_folders.add_folder("GIMI", root, filename)
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

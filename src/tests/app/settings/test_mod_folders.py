"""Persistent Mod Folder registry behavior."""

import json
import os

import pytest

from app.settings import mod_folders as mod_folders


def _config(tmp_path):
    return str(tmp_path / "config.json")


def _directory(tmp_path, name):
    path = tmp_path / name
    path.mkdir()
    return str(path)


def test_mod_folder_registry_lifecycle(tmp_path):
    filename = _config(tmp_path)
    assert mod_folders.load_registry(filename) == []
    assert not os.path.exists(filename)
    first = _directory(tmp_path, "first")
    second = _directory(tmp_path, "second")
    replacement = _directory(tmp_path, "replacement")

    entries = mod_folders.add_folder("First", first, filename)
    entries = mod_folders.add_folder("Second", second, filename)

    assert entries == [
        {"name": "First", "path": mod_folders.normalize_path(first)},
        {"name": "Second", "path": mod_folders.normalize_path(second)},
    ]
    assert json.loads(open(filename, encoding="utf-8").read()) == {
        "version": 1, "modFolders": entries,
    }
    assert not os.path.exists(filename + ".tmp")

    entries = mod_folders.edit_folder(first, "Renamed", first, filename)
    assert entries[0]["name"] == "Renamed"
    entries = mod_folders.edit_folder(first, "Moved", replacement, filename)
    assert [entry["name"] for entry in entries] == ["Moved", "Second"]
    assert entries[0]["path"] == mod_folders.normalize_path(replacement)

    retained_file = os.path.join(replacement, "keep.txt")
    open(retained_file, "w", encoding="utf-8").close()
    assert mod_folders.delete_folder(replacement, filename) == [entries[1]]
    assert os.path.isdir(replacement)
    assert os.path.isfile(retained_file)

    missing = str(tmp_path / "offline")
    with open(filename, "w", encoding="utf-8") as stream:
        json.dump({"version": 1, "modFolders": [{"name": "Offline", "path": missing}]}, stream)
    assert mod_folders.edit_folder(missing, "Archive", missing, filename)[0]["name"] == "Archive"
    with pytest.raises(mod_folders.ModFolderError, match="existing directory"):
        mod_folders.edit_folder(missing, "Broken", str(tmp_path / "new"), filename)


def test_panel_opacity_persists_explicit_values_and_survives_registry_edits(
        tmp_path):
    filename = _config(tmp_path)
    assert mod_folders.load_panel_opacity(filename) == 58
    assert not os.path.exists(filename)

    assert mod_folders.save_panel_opacity(35, filename) == 35
    assert json.loads(open(filename, encoding="utf-8").read()) == {
        "version": 1, "modFolders": [], "panelOpacity": 35,
    }

    root = _directory(tmp_path, "root")
    mod_folders.add_folder("Root", root, filename)
    saved = json.loads(open(filename, encoding="utf-8").read())
    assert saved["panelOpacity"] == 35
    assert len(saved["modFolders"]) == 1

    assert mod_folders.save_panel_opacity(58, filename) == 58
    saved = json.loads(open(filename, encoding="utf-8").read())
    assert saved["panelOpacity"] == 58
    assert len(saved["modFolders"]) == 1


@pytest.mark.parametrize("value", [-1, 101, 42.5, True, "50"])
def test_panel_opacity_rejects_values_outside_whole_percent_range(
        tmp_path, value):
    with pytest.raises(mod_folders.ModFolderError, match="0 to 100"):
        mod_folders.save_panel_opacity(value, _config(tmp_path))


@pytest.mark.parametrize("name", ["", "   ", None])
def test_add_rejects_empty_name(tmp_path, name):
    with pytest.raises(mod_folders.ModFolderError):
        mod_folders.add_folder(name, _directory(tmp_path, "root"), _config(tmp_path))


def test_add_rejects_missing_duplicate_and_relative_paths(tmp_path):
    filename = _config(tmp_path)
    root = _directory(tmp_path, "root")
    mod_folders.add_folder("Root", root, filename)

    with pytest.raises(mod_folders.ModFolderError, match="already registered"):
        mod_folders.add_folder("Again", os.path.join(root, "."), filename)
    with pytest.raises(mod_folders.ModFolderError, match="existing directory"):
        mod_folders.add_folder("Missing", str(tmp_path / "missing"), filename)
    with pytest.raises(mod_folders.ModFolderError, match="absolute"):
        mod_folders.add_folder("Relative", "relative", filename)


@pytest.mark.parametrize("bad_config", ["{", {"version": 2, "modFolders": []}])
def test_invalid_config_is_reported_without_overwrite(tmp_path, bad_config):
    filename = _config(tmp_path)
    original = bad_config if isinstance(bad_config, str) else json.dumps(bad_config)
    with open(filename, "w", encoding="utf-8") as stream:
        stream.write(original)

    with pytest.raises(mod_folders.ModFolderError):
        mod_folders.load_registry(filename)
    with pytest.raises(mod_folders.ModFolderError):
        mod_folders.add_folder("Root", _directory(tmp_path, "root"), filename)
    with pytest.raises(mod_folders.ModFolderError):
        mod_folders.save_panel_opacity(40, filename)
    assert open(filename, encoding="utf-8").read() == original


def test_list_subfolders_is_immediate_sorted_and_non_recursive(tmp_path):
    filename = _config(tmp_path)
    root = _directory(tmp_path, "root")
    _directory(tmp_path / "root", "zeta")
    _directory(tmp_path / "root", "Alpha")
    nested = tmp_path / "root" / "Alpha" / "nested"
    nested.mkdir()
    open(tmp_path / "root" / "file.txt", "w", encoding="utf-8").close()
    mod_folders.add_folder("Root", root, filename)

    result = mod_folders.list_subfolders(root, root)

    assert [item["name"] for item in result] == ["Alpha", "zeta"]
    assert all("nested" not in item["path"] for item in result)


def test_containment_rejects_prefix_parent_and_other_drive():
    assert mod_folders.normalize_path("") == ""
    assert mod_folders.normalize_path("   ") == ""
    assert mod_folders.is_within(r"X:\fixture\mods\Alice", r"X:\fixture\mods")
    assert mod_folders.is_within(r"X:\fixture\mods", r"X:\fixture\mods")
    assert not mod_folders.is_within(
        r"X:\fixture\mods-backup", r"X:\fixture\mods")
    assert not mod_folders.is_within(
        r"X:\fixture\other\mods", r"X:\fixture\mods")
    assert not mod_folders.is_within(
        r"Y:\fixture\mods\Alice", r"X:\fixture\mods")

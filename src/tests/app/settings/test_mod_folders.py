"""Persistent Mod Folder registry and bridge authorization boundaries."""

import json
import os

import pytest

from app.settings import mod_folders as mod_folders
from app.settings import paths as paths
from app.bridge.api import ModViewerAPI


def _config(tmp_path):
    return str(tmp_path / "config.json")


def _directory(tmp_path, name):
    path = tmp_path / name
    path.mkdir()
    return str(path)


def test_missing_config_is_empty_without_creating_file(tmp_path):
    filename = _config(tmp_path)

    assert mod_folders.load_registry(filename) == []
    assert not os.path.exists(filename)


def test_add_preserves_order_and_writes_atomic_schema(tmp_path):
    filename = _config(tmp_path)
    first = _directory(tmp_path, "first")
    second = _directory(tmp_path, "second")

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


def test_edit_name_and_path_preserve_registry_position(tmp_path):
    filename = _config(tmp_path)
    first = _directory(tmp_path, "first")
    second = _directory(tmp_path, "second")
    replacement = _directory(tmp_path, "replacement")
    mod_folders.add_folder("First", first, filename)
    mod_folders.add_folder("Second", second, filename)

    entries = mod_folders.edit_folder(first, "Renamed", first, filename)
    assert entries[0]["name"] == "Renamed"
    entries = mod_folders.edit_folder(first, "Moved", replacement, filename)
    assert [entry["name"] for entry in entries] == ["Moved", "Second"]
    assert entries[0]["path"] == mod_folders.normalize_path(replacement)


def test_missing_root_can_be_renamed_but_new_path_must_exist(tmp_path):
    filename = _config(tmp_path)
    missing = str(tmp_path / "offline")
    config = {"version": 1, "modFolders": [{"name": "Offline", "path": missing}]}
    with open(filename, "w", encoding="utf-8") as stream:
        json.dump(config, stream)

    entries = mod_folders.edit_folder(missing, "Archive", missing, filename)
    assert entries[0]["name"] == "Archive"
    with pytest.raises(mod_folders.ModFolderError, match="existing directory"):
        mod_folders.edit_folder(missing, "Broken", str(tmp_path / "new"), filename)


def test_delete_removes_only_registry_entry_and_keeps_files(tmp_path):
    filename = _config(tmp_path)
    root = _directory(tmp_path, "root")
    file_path = os.path.join(root, "keep.txt")
    open(file_path, "w", encoding="utf-8").close()
    mod_folders.add_folder("Root", root, filename)

    assert mod_folders.delete_folder(root, filename) == []
    assert os.path.isdir(root)
    assert os.path.isfile(file_path)
    assert json.loads(open(filename, encoding="utf-8").read()) == {
        "version": 1, "modFolders": [],
    }


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


def test_api_registry_authorizes_descendants_and_keeps_active_exact_path(
        tmp_path, monkeypatch):
    filename = _config(tmp_path)
    root = _directory(tmp_path, "root")
    child = _directory(tmp_path / "root", "child")
    sibling = _directory(tmp_path, "sibling")
    monkeypatch.setattr("app.settings.paths.config_path", lambda: filename)
    api = ModViewerAPI()
    normalized_root = mod_folders.normalize_path(root)
    api._access.remember_mod_picker_selection(normalized_root)
    assert api.add_mod_folder("Root", root)["folders"][0]["name"] == "Root"

    assert api._access.mod_folder(child) == mod_folders.normalize_path(child)
    with pytest.raises(PermissionError):
        api._access.mod_folder(sibling)
    assert api.delete_mod_folder(root)["folders"] == []
    # The descendant was promoted to an exact session authorization before the
    # root was removed, so existing open-mod operations remain usable.
    assert api._access.mod_folder(child) == mod_folders.normalize_path(child)


def test_api_listing_requires_registered_root(tmp_path, monkeypatch):
    filename = _config(tmp_path)
    root = _directory(tmp_path, "root")
    child = _directory(tmp_path / "root", "child")
    outside = _directory(tmp_path, "outside")
    monkeypatch.setattr("app.settings.paths.config_path", lambda: filename)
    api = ModViewerAPI()
    normalized_root = mod_folders.normalize_path(root)
    api._access.remember_mod_picker_selection(normalized_root)
    api.add_mod_folder("Root", root)

    assert [item["path"] for item in api.list_subfolders(root)["folders"]] == [
        mod_folders.normalize_path(child)]
    assert "error" in api.list_subfolders(outside)


def test_api_add_and_edit_require_native_picker_for_new_paths(tmp_path, monkeypatch):
    filename = _config(tmp_path)
    first = _directory(tmp_path, "first")
    second = _directory(tmp_path, "second")
    invented = _directory(tmp_path, "invented")
    monkeypatch.setattr("app.settings.paths.config_path", lambda: filename)
    api = ModViewerAPI()
    normalized_first = mod_folders.normalize_path(first)
    normalized_second = mod_folders.normalize_path(second)
    api._access.remember_mod_picker_selection(normalized_first)
    assert api.add_mod_folder("First", first).get("folders")
    assert "error" in api.add_mod_folder("Invented", invented)
    assert "error" in api.edit_mod_folder(first, "Second", second)
    api._access.remember_mod_picker_selection(normalized_second)
    assert api.edit_mod_folder(first, "Second", second).get("folders")


def test_descendant_runtime_authorization_cannot_persist_without_picker(
        tmp_path, monkeypatch):
    filename = _config(tmp_path)
    root = _directory(tmp_path, "root")
    child = _directory(tmp_path / "root", "child")
    replacement = _directory(tmp_path, "replacement")
    monkeypatch.setattr("app.settings.paths.config_path", lambda: filename)
    api = ModViewerAPI()
    normalized_root = mod_folders.normalize_path(root)
    api._access.remember_mod_picker_selection(normalized_root)
    assert api.add_mod_folder("Root", root).get("folders")

    # Browsing the child grants runtime access only; it is not picker proof.
    assert api._access.mod_folder(child) == mod_folders.normalize_path(child)
    assert "error" in api.add_mod_folder("Child", child)
    assert "error" in api.edit_mod_folder(root, "Child", child)

    api._access.remember_mod_picker_selection(replacement)
    assert api.edit_mod_folder(root, "Replacement", replacement).get("folders")


def test_config_path_is_next_to_executable_when_frozen(monkeypatch, tmp_path):
    monkeypatch.setattr(paths.sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
    monkeypatch.setattr(paths.sys, "executable", str(tmp_path / "ModViewer.exe"))

    assert paths.config_path() == os.path.join(str(tmp_path), "config.json")

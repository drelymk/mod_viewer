"""Persistent Mod Folders registry and safe one-level enumeration.

The registry stores only user-approved roots.  This module deliberately does
not decide whether a directory is a loadable mod; that remains the loader's
responsibility.  The API layer supplies the native-picker authorization needed
before a root can be added or changed.
"""

import json
import os

from . import paths


CONFIG_VERSION = 1


class ModFolderError(ValueError):
    """A readable validation, config or enumeration failure."""


def normalize_path(value):
    """Return the canonical comparison form for a filesystem path."""
    if value is None:
        return ""
    try:
        value = os.fspath(value)
    except TypeError:
        return ""
    if not isinstance(value, str):
        return ""
    if not value.strip():
        return ""
    return os.path.normcase(os.path.realpath(os.path.abspath(value)))


def is_within(path, root):
    """Return whether *path* is *root* or a canonical descendant of it."""
    path = normalize_path(path)
    root = normalize_path(root)
    if not path or not root:
        return False
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        # Windows drives (and other incompatible path roots) have no common
        # path and must never be treated as descendants.
        return False


def _config_file(config_file=None):
    return os.fspath(config_file or paths.config_path())


def _read_entries(config_file=None):
    filename = _config_file(config_file)
    if not os.path.exists(filename):
        return []
    try:
        with open(filename, encoding="utf-8") as stream:
            config = json.load(stream)
    except (OSError, json.JSONDecodeError) as error:
        raise ModFolderError(f"Could not read config.json: {error}") from error

    if not isinstance(config, dict) or config.get("version") != CONFIG_VERSION:
        raise ModFolderError(
            f"Unsupported config.json version; expected {CONFIG_VERSION}.")
    raw_entries = config.get("modFolders")
    if not isinstance(raw_entries, list):
        raise ModFolderError("config.json modFolders must be a list.")

    entries = []
    seen = set()
    for raw in raw_entries:
        if not isinstance(raw, dict):
            raise ModFolderError("Each Mod Folder entry must be an object.")
        name = raw.get("name")
        folder = raw.get("path")
        if not isinstance(name, str) or not name.strip():
            raise ModFolderError("Each Mod Folder needs a non-empty name.")
        if not isinstance(folder, str) or not os.path.isabs(folder):
            raise ModFolderError("Each Mod Folder path must be absolute.")
        normalized = normalize_path(folder)
        if normalized in seen:
            raise ModFolderError("config.json contains duplicate Mod Folder paths.")
        seen.add(normalized)
        entries.append({"name": name.strip(), "path": normalized})
    return entries


def load_registry(config_file=None):
    """Return registry entries without creating config.json when absent."""
    return _read_entries(config_file)


def _validated_entry(name, folder, *, require_exists):
    if not isinstance(name, str) or not name.strip():
        raise ModFolderError("Mod Folder name must not be empty.")
    if not isinstance(folder, str) or not os.path.isabs(folder):
        raise ModFolderError("Mod Folder path must be absolute.")
    normalized = normalize_path(folder)
    if require_exists and not os.path.isdir(normalized):
        raise ModFolderError("Mod Folder path must be an existing directory.")
    return {"name": name.strip(), "path": normalized}


def _write_entries(entries, config_file=None):
    filename = _config_file(config_file)
    temp_name = filename + ".tmp"
    config = {"version": CONFIG_VERSION, "modFolders": entries}
    try:
        with open(temp_name, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(config, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, filename)
    except OSError as error:
        raise ModFolderError(f"Could not write config.json: {error}") from error
    finally:
        if os.path.exists(temp_name):
            try:
                os.remove(temp_name)
            except OSError:
                pass


def add_folder(name, folder, config_file=None):
    entries = _read_entries(config_file)
    entry = _validated_entry(name, folder, require_exists=True)
    if any(item["path"] == entry["path"] for item in entries):
        raise ModFolderError("That Mod Folder path is already registered.")
    entries.append(entry)
    _write_entries(entries, config_file)
    return entries


def edit_folder(original_folder, name, folder, config_file=None):
    entries = _read_entries(config_file)
    original = normalize_path(original_folder)
    index = next((i for i, item in enumerate(entries)
                  if item["path"] == original), None)
    if index is None:
        raise ModFolderError("That Mod Folder is not registered.")

    target = normalize_path(folder)
    entry = _validated_entry(
        name, folder, require_exists=(target != original))
    if any(i != index and item["path"] == entry["path"]
           for i, item in enumerate(entries)):
        raise ModFolderError("That Mod Folder path is already registered.")
    entries[index] = entry
    _write_entries(entries, config_file)
    return entries


def delete_folder(folder, config_file=None):
    entries = _read_entries(config_file)
    target = normalize_path(folder)
    filtered = [item for item in entries if item["path"] != target]
    if len(filtered) == len(entries):
        raise ModFolderError("That Mod Folder is not registered.")
    _write_entries(filtered, config_file)
    return filtered


def list_subfolders(folder, authorized_root):
    """Return immediate safe directory children of one authorized root."""
    folder = normalize_path(folder)
    authorized_root = normalize_path(authorized_root)
    if not is_within(folder, authorized_root):
        raise ModFolderError("That folder is outside the registered Mod Folder.")
    if not os.path.isdir(folder):
        raise ModFolderError("Folder not found or is not a directory.")

    children = []
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                try:
                    if not entry.is_dir(follow_symlinks=True):
                        continue
                    child = normalize_path(entry.path)
                    if is_within(child, authorized_root):
                        children.append({"name": entry.name, "path": child})
                except OSError:
                    # A disappearing or inaccessible child should not make
                    # the entire registered root unusable.
                    continue
    except OSError as error:
        raise ModFolderError(f"Unable to read this folder: {error}") from error
    return sorted(children, key=lambda item: (item["name"].casefold(), item["name"]))


def registered_paths(entries):
    """Return canonical root paths from a registry entry list."""
    return {entry["path"] for entry in entries}

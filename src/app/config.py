"""Shared persistence and filesystem comparison helpers for app config."""

import json
import os

from . import paths


CONFIG_VERSION = 1


def normalize_path(value):
    """Return the canonical comparison form for a filesystem path."""
    if value is None:
        return ""
    try:
        value = os.fspath(value)
    except TypeError:
        return ""
    if not isinstance(value, str) or not value.strip():
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
        return False


def read_config(config_file=None):
    """Read the versioned config, preserving optional fields as authored."""
    filename = config_file_path(config_file)
    if not os.path.exists(filename):
        return {"version": CONFIG_VERSION, "modFolders": []}
    try:
        with open(filename, encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Could not read config.json: {error}") from error
    if not isinstance(value, dict) or value.get("version") != CONFIG_VERSION:
        raise ValueError(
            f"Unsupported config.json version; expected {CONFIG_VERSION}.")
    if not isinstance(value.get("modFolders"), list):
        raise ValueError("config.json modFolders must be a list.")
    return value


def config_file_path(config_file=None):
    return os.fspath(config_file or paths.config_path())


def write_bytes_atomic(filename, payload):
    """Replace *filename* after writing and syncing its complete payload."""
    temp_name = filename + ".tmp"
    try:
        with open(temp_name, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, filename)
    finally:
        if os.path.exists(temp_name):
            try:
                os.remove(temp_name)
            except OSError:
                pass


def write_config(value, config_file=None):
    """Atomically replace config.json after fully writing and syncing a temp."""
    filename = config_file_path(config_file)
    try:
        payload = (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode(
            "utf-8")
        write_bytes_atomic(filename, payload)
    except OSError as error:
        raise ValueError(f"Could not write config.json: {error}") from error

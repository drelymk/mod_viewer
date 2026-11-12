"""Persistent per-tool game installation folders used to prefill extraction."""

import os

from . import config


GAME_FOLDERS_KEY = "gameFolders"
GAME_KINDS = ("GIMI", "ZZMI", "WWMI")


def _validated(value):
    if not isinstance(value, dict):
        raise ValueError("Game folders must be an object.")
    result = {}
    for kind, path in value.items():
        if kind not in GAME_KINDS:
            raise ValueError(f"Unknown game folder: {kind}.")
        if not isinstance(path, str) or not os.path.isabs(path) or len(path) > 4096:
            raise ValueError(f"Invalid game folder: {kind}.")
        result[kind] = path
    return result


def load_game_folders(config_file=None):
    value = config.read_config(config_file).get(GAME_FOLDERS_KEY, {})
    return _validated(value)


@config.transaction()
def save_game_folder(kind, path, config_file=None):
    """Set or (when *path* is None) clear one folder, keeping all other config."""
    if kind not in GAME_KINDS:
        raise ValueError(f"Unknown game folder: {kind}.")
    value = config.read_config(config_file)
    folders = _validated(value.get(GAME_FOLDERS_KEY, {}))
    if path is None:
        folders.pop(kind, None)
    else:
        folders.update(_validated({kind: path}))
    if folders:
        value[GAME_FOLDERS_KEY] = folders
    else:
        value.pop(GAME_FOLDERS_KEY, None)
    config.write_config(value, config_file)
    return folders

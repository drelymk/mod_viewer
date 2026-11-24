"""Per-game config: ``gameFolders[kind] = {"path": <folder>, "Settings": {"DefaultAni": <name>}}``.

Both parts are optional and an empty entry is dropped. A bare path string is the legacy
entry form; it is still read and rewritten as an object on the next save."""

import os

from . import config


GAME_FOLDERS_KEY = "gameFolders"
PATH_KEY = "path"
SETTINGS_KEY = "Settings"
DEFAULT_ANI_KEY = "DefaultAni"
GAME_KINDS = ("GIMI", "ZZMI", "WWMI")
MAX_DEFAULT_ANIMATION_LENGTH = 500


def _validated_path(kind, path):
    if not isinstance(path, str) or not os.path.isabs(path) or len(path) > 4096:
        raise ValueError(f"Invalid game folder: {kind}.")
    return path


def _validated_default_animation(kind, name):
    if not isinstance(name, str) or not name.strip() or len(name) > MAX_DEFAULT_ANIMATION_LENGTH:
        raise ValueError(f"Invalid default animation: {kind}.")
    return name.strip()


def _validated(value):
    """Return normalized entries (object form, empty entries dropped)."""
    if not isinstance(value, dict):
        raise ValueError("Game folders must be an object.")
    result = {}
    for kind, entry in value.items():
        if kind not in GAME_KINDS:
            raise ValueError(f"Unknown game folder: {kind}.")
        if isinstance(entry, str):
            entry = {PATH_KEY: entry}
        if not isinstance(entry, dict) or not set(entry) <= {PATH_KEY, SETTINGS_KEY}:
            raise ValueError(f"Invalid game folder: {kind}.")
        settings = entry.get(SETTINGS_KEY, {})
        if not isinstance(settings, dict) or not set(settings) <= {DEFAULT_ANI_KEY}:
            raise ValueError(f"Invalid game settings: {kind}.")
        normalized = {}
        if PATH_KEY in entry:
            normalized[PATH_KEY] = _validated_path(kind, entry[PATH_KEY])
        if DEFAULT_ANI_KEY in settings:
            normalized[SETTINGS_KEY] = {
                DEFAULT_ANI_KEY: _validated_default_animation(kind, settings[DEFAULT_ANI_KEY])}
        if normalized:
            result[kind] = normalized
    return result


def _paths(entries):
    return {kind: entry[PATH_KEY] for kind, entry in entries.items() if PATH_KEY in entry}


def _default_animations(entries):
    return {kind: entry[SETTINGS_KEY][DEFAULT_ANI_KEY]
            for kind, entry in entries.items() if SETTINGS_KEY in entry}


def _read_entries(config_file=None):
    return _validated(config.read_config(config_file).get(GAME_FOLDERS_KEY, {}))


def load_game_folders(config_file=None):
    return _paths(_read_entries(config_file))


def load_default_animations(config_file=None):
    return _default_animations(_read_entries(config_file))


def _update_entry(kind, key, value, config_file):
    """Set one field of a kind's entry (None clears it), keeping all other config."""
    if kind not in GAME_KINDS:
        raise ValueError(f"Unknown game folder: {kind}.")
    config_value = config.read_config(config_file)
    entries = _validated(config_value.get(GAME_FOLDERS_KEY, {}))
    entry = {**entries.get(kind, {}), key: value}
    if value is None:
        del entry[key]
    entries = _validated({**entries, kind: entry})
    if entries:
        config_value[GAME_FOLDERS_KEY] = entries
    else:
        config_value.pop(GAME_FOLDERS_KEY, None)
    config.write_config(config_value, config_file)
    return entries


@config.transaction()
def save_game_folder(kind, path, config_file=None):
    """Set or (when *path* is None) clear one folder; its settings are kept."""
    return _paths(_update_entry(kind, PATH_KEY, path, config_file))


@config.transaction()
def save_default_animation(kind, name, config_file=None):
    """Set or (when *name* is blank) clear one game's default animation."""
    if not isinstance(name, str):
        raise ValueError(f"Invalid default animation: {kind}.")
    setting = {DEFAULT_ANI_KEY: name} if name.strip() else None
    return _default_animations(_update_entry(kind, SETTINGS_KEY, setting, config_file))

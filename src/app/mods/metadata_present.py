"""PRESENT names and sparse position updates in viewer metadata."""

from copy import deepcopy

from .metadata_storage import (
    _LOCK,
    load,
    _save,
)


PRESENT_NAMES_KEY = "__all__"


def present_names(folder_path, ini_rel, data=None):
    data = (load(folder_path) if data is None else data).get("present_names", {})
    names = data.get(ini_rel, {}) if isinstance(data, dict) else {}
    if not isinstance(names, dict):
        return {}
    return {str(index): name for index, name in names.items()
            if str(index).isdigit() and isinstance(name, str) and name.strip()}


def all_present_names(folder_path, source=None):
    """Return a detached snapshot suitable for edit-session rollback."""
    names = load(folder_path, source=source).get("present_names")
    return deepcopy(names) if isinstance(names, dict) else None


def restore_present_names(folder_path, names):
    """Restore only PRESENT metadata, preserving unrelated viewer settings."""
    with _LOCK:
        data = load(folder_path)
        if isinstance(names, dict) and names:
            data["present_names"] = deepcopy(names)
        else:
            data.pop("present_names", None)
        return _save(folder_path, data)


def apply_present_name(data, ini_rel, position, name):
    """Apply one sparse PRESENT name to an in-memory metadata mapping."""
    position = int(position)
    name = str(name or "").strip()
    if not name:
        raise ValueError("a present name is required")
    default = f"Present {position + 1}"
    all_names = data.get("present_names")
    if not isinstance(all_names, dict):
        all_names = {}
    names = all_names.get(ini_rel)
    if not isinstance(names, dict):
        names = {}
    if name == default:
        if str(position) not in names:
            return False
        names.pop(str(position), None)
    else:
        if names.get(str(position)) == name:
            return False
        names[str(position)] = name
    if names:
        all_names[ini_rel] = names
    else:
        all_names.pop(ini_rel, None)
    if all_names:
        data["present_names"] = all_names
    else:
        data.pop("present_names", None)
    return True


def save_present_name(folder_path, ini_rel, position, name):
    """Persist only names that differ from their implicit ``Present N``."""
    with _LOCK:
        data = load(folder_path)
        if not apply_present_name(data, ini_rel, position, name):
            return {"saved": False}
        return _save(folder_path, data)


def apply_clear_present_names(data, ini_rel):
    """Remove one PRESENT name mapping from an in-memory metadata mapping."""
    all_names = data.get("present_names")
    if not isinstance(all_names, dict) or ini_rel not in all_names:
        return False
    all_names.pop(ini_rel, None)
    if all_names:
        data["present_names"] = all_names
    else:
        data.pop("present_names", None)
    return True


def clear_present_names(folder_path, ini_rel):
    with _LOCK:
        data = load(folder_path)
        if not apply_clear_present_names(data, ini_rel):
            return {"saved": False}
        return _save(folder_path, data)


def apply_delete_present_name(data, ini_rel, position, old_count):
    """Shift sparse PRESENT names after deleting one position in memory."""
    position = int(position)
    old_count = int(old_count)
    all_names = data.get("present_names")
    if not isinstance(all_names, dict):
        return False
    names = all_names.get(ini_rel)
    if not isinstance(names, dict):
        return False
    shifted = {}
    for old_index in range(old_count):
        if old_index == position:
            continue
        old_name = names.get(str(old_index))
        if not old_name:
            continue
        new_index = old_index if old_index < position else old_index - 1
        if old_name != f"Present {new_index + 1}":
            shifted[str(new_index)] = old_name
    if shifted:
        all_names[ini_rel] = shifted
    else:
        all_names.pop(ini_rel, None)
    if all_names:
        data["present_names"] = all_names
    else:
        data.pop("present_names", None)
    return True


def delete_present_name(folder_path, ini_rel, position, old_count):
    """Remove one name and shift sparse overrides with their value positions."""
    with _LOCK:
        data = load(folder_path)
        if not apply_delete_present_name(data, ini_rel, position, old_count):
            return {"saved": False}
        return _save(folder_path, data)


def hydrate_present(folder_path, present, data=None):
    item = present.get("item") if isinstance(present, dict) else None
    if not isinstance(item, dict):
        return
    count = int(item.get("count") or 0)
    data = load(folder_path) if data is None else data
    custom = present_names(folder_path, PRESENT_NAMES_KEY, data)
    if not custom:
        for ini in item.get("inis", []):
            custom = present_names(folder_path, ini, data)
            if custom:
                break
    item["names"] = [custom.get(str(index), f"Present {index + 1}")
                     for index in range(count)]

"""Viewer-only mesh labels and texture choices stored beside a mod."""
import json
import os
import threading
from copy import deepcopy

from core.mesh_builder import (encode_texture_key, normalize_texture_key,
                               split_texture_key)

METADATA_NAME = ".mod_viewer.json"
PRESENT_NAMES_KEY = "__all__"
_LOCK = threading.RLock()


def _mesh_key(name, entry):
    component = entry.get("component")
    if not component:
        component = name.rsplit("-", 1)[0] if name.rsplit("-", 1)[-1].isdigit() else name
    draw = entry.get("drawindexed")
    draw_key = ",".join(str(value) for value in draw) if draw else "whole"
    return f"{component}::{draw_key}"


def load(folder_path):
    try:
        with open(os.path.join(folder_path, METADATA_NAME), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _save(folder_path, data):
    path = os.path.join(folder_path, METADATA_NAME)
    temp_path = path + ".tmp"
    with open(temp_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(temp_path, path)
    return {"saved": True, "path": path}


def save_mesh_names(folder_path, names):
    with _LOCK:
        data = load(folder_path)
        data["mesh_names"] = names if isinstance(names, dict) else {}
        return _save(folder_path, data)


def save_textures(folder_path, textures):
    with _LOCK:
        data = load(folder_path)
        data["textures"] = textures if isinstance(textures, dict) else {}
        return _save(folder_path, data)


def present_names(folder_path, ini_rel, data=None):
    data = (load(folder_path) if data is None else data).get("present_names", {})
    names = data.get(ini_rel, {}) if isinstance(data, dict) else {}
    if not isinstance(names, dict):
        return {}
    return {str(index): name for index, name in names.items()
            if str(index).isdigit() and isinstance(name, str) and name.strip()}


def all_present_names(folder_path):
    """Return a detached snapshot suitable for edit-session rollback."""
    names = load(folder_path).get("present_names")
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


def save_present_name(folder_path, ini_rel, position, name):
    """Persist only names that differ from their implicit ``Present N``."""
    position = int(position)
    name = str(name or "").strip()
    if not name:
        raise ValueError("a present name is required")
    default = f"Present {position + 1}"
    with _LOCK:
        data = load(folder_path)
        all_names = data.get("present_names")
        if not isinstance(all_names, dict):
            all_names = {}
        names = all_names.get(ini_rel)
        if not isinstance(names, dict):
            names = {}
        if name == default:
            if str(position) not in names:
                return {"saved": False}
            names.pop(str(position), None)
        else:
            if names.get(str(position)) == name:
                return {"saved": False}
            names[str(position)] = name
        if names:
            all_names[ini_rel] = names
        else:
            all_names.pop(ini_rel, None)
        if all_names:
            data["present_names"] = all_names
        else:
            data.pop("present_names", None)
        return _save(folder_path, data)


def clear_present_names(folder_path, ini_rel):
    with _LOCK:
        data = load(folder_path)
        all_names = data.get("present_names")
        if not isinstance(all_names, dict) or ini_rel not in all_names:
            return {"saved": False}
        all_names.pop(ini_rel, None)
        if all_names:
            data["present_names"] = all_names
        else:
            data.pop("present_names", None)
        return _save(folder_path, data)


def delete_present_name(folder_path, ini_rel, position, old_count):
    """Remove one name and shift sparse overrides with their value positions."""
    position = int(position)
    old_count = int(old_count)
    with _LOCK:
        data = load(folder_path)
        all_names = data.get("present_names")
        if not isinstance(all_names, dict):
            return {"saved": False}
        names = all_names.get(ini_rel)
        if not isinstance(names, dict):
            return {"saved": False}
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


def hydrate_textures(folder_path, payload, data=None, texture_source=None):
    """Restore sparse highlighted boundaries, then rebuild component pools.

    ``payload`` is the structured application payload; only its ``meshes``
    and ``textures`` fields are mutated here.
    """
    data = load(folder_path) if data is None else data
    meshes = payload.setdefault("meshes", {})
    textures = payload.setdefault("textures", {})
    saved = data.get("textures")
    if not isinstance(saved, dict):
        saved = {}

    highlighted = {}
    for name, state in saved.items():
        if not isinstance(state, dict):
            continue
        key, label, manual = (normalize_texture_key(
                                  state.get("tex_key"), "diffuse"),
                              state.get("label"), state.get("manual"))
        if (not isinstance(key, str) or not key
                or not isinstance(label, str) or not label
                or not isinstance(manual, bool)):
            continue
        _role, relative_path = split_texture_key(key)
        item = {"tex_key": key, "file": relative_path,
                "label": label, "manual": manual}
        for field in ("normal_map", "light_map", "material_map"):
            value = normalize_texture_key(state.get(field), field)
            if value:
                item[field] = value
            manual = state.get(f"{field}_manual")
            if isinstance(manual, bool) and manual:
                item[f"{field}_manual"] = True
        highlighted[name] = item

    restored = {}
    for name, entry in meshes.items():
        if not isinstance(entry, dict) or entry.get("error"):
            continue
        mesh_key = _mesh_key(name, entry)
        state = highlighted.get(mesh_key)
        if state:
            restored[mesh_key] = state
            if state["manual"]:
                entry["saved_texture_override"] = state["tex_key"]

    # Rebuild each shared component pool from ini options plus saved boundary
    # textures. The pool no longer needs to be duplicated for every draw in JSON.
    pools = {}
    for name, entry in meshes.items():
        if not isinstance(entry, dict) or entry.get("error"):
            continue
        group = (entry.get("source"), entry.get("component"))
        pool = pools.setdefault(group, [])
        candidates = list(entry.get("texture_options") or [])
        mesh_key = _mesh_key(name, entry)
        if mesh_key in restored:
            state = restored[mesh_key]
            candidates.append({key: value for key, value in state.items()
                               if key != "manual"})
        for opt in candidates:
            if not isinstance(opt, dict) or not isinstance(opt.get("tex_key"), str):
                continue
            old = next((item for item in pool
                        if item["tex_key"] == opt["tex_key"]), None)
            if old is None:
                pool.append(opt)
            else:
                for field in ("normal_map", "light_map", "material_map"):
                    if opt.get(field):
                        old[field] = opt[field]
                    if opt.get(f"{field}_manual"):
                        old[f"{field}_manual"] = True

    for name, entry in meshes.items():
        if not isinstance(entry, dict) or entry.get("error"):
            continue
        options = pools.get((entry.get("source"), entry.get("component")), [])
        if options:
            entry["texture_options"] = options
        else:
            entry.pop("texture_options", None)
        state = restored.get(_mesh_key(name, entry))
        texture_roles = [(state["tex_key"], None)] if state else []
        if state:
            texture_roles.extend((state.get(field), field) for field in
                                 ("normal_map", "light_map", "material_map"))
        for key, role in texture_roles:
            if not key:
                continue
            if key in textures:
                continue
            encoded = encode_texture_key(
                folder_path, key, role, texture_source=texture_source)
            if encoded and not encoded.get("error"):
                textures[encoded["tex_key"]] = encoded["uri"]

    return restored

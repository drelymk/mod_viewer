"""Viewer-only mesh labels and texture choices stored beside a mod."""
import json
import os
import threading

from core.mesh_builder import encode_texture_file

METADATA_NAME = ".mod_viewer.json"
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


def hydrate_textures(folder_path, payload):
    """Restore sparse highlighted boundaries, then rebuild component pools."""
    data = load(folder_path)
    saved = data.get("textures")
    if not isinstance(saved, dict):
        saved = {}

    highlighted = {}
    for name, state in saved.items():
        if not isinstance(state, dict):
            continue
        key, label, manual = (state.get("tex_key"), state.get("label"),
                              state.get("manual"))
        if (not isinstance(key, str) or not key
                or not isinstance(label, str) or not label
                or not isinstance(manual, bool)):
            continue
        highlighted[name] = {"tex_key": key, "label": label, "manual": manual}

    discovered = {}
    seen_defaults = set()
    for name, entry in payload.items():
        if name.startswith("__") or not isinstance(entry, dict) or entry.get("error"):
            continue
        mesh_key = _mesh_key(name, entry)
        state = highlighted.get(mesh_key)
        if state is None and not saved:
            key = entry.get("tex_key")
            marker = ((entry.get("source"), entry.get("component")), key)
            if key and marker not in seen_defaults:
                seen_defaults.add(marker)
                options = entry.get("texture_options") or []
                match = next((opt for opt in options if opt.get("tex_key") == key), None)
                label = (match.get("label") if match else
                         os.path.splitext(os.path.basename(key))[0])
                state = {"tex_key": key, "label": label, "manual": False}
        if state:
            discovered[mesh_key] = state
            if state["manual"]:
                entry["saved_texture_override"] = state["tex_key"]

    # Rebuild each shared component pool from ini options plus saved boundary
    # textures. The pool no longer needs to be duplicated for every draw in JSON.
    pools = {}
    for name, entry in payload.items():
        if name.startswith("__") or not isinstance(entry, dict) or entry.get("error"):
            continue
        group = (entry.get("source"), entry.get("component"))
        pool = pools.setdefault(group, [])
        candidates = list(entry.get("texture_options") or [])
        mesh_key = _mesh_key(name, entry)
        if mesh_key in discovered:
            state = discovered[mesh_key]
            candidates.append({"tex_key": state["tex_key"], "label": state["label"]})
        for opt in candidates:
            if (isinstance(opt, dict) and isinstance(opt.get("tex_key"), str)
                    and not any(old["tex_key"] == opt["tex_key"] for old in pool)):
                pool.append(opt)

    for name, entry in payload.items():
        if name.startswith("__") or not isinstance(entry, dict) or entry.get("error"):
            continue
        options = pools.get((entry.get("source"), entry.get("component")), [])
        if options:
            entry["texture_options"] = options
        else:
            entry.pop("texture_options", None)
        for key in [opt["tex_key"] for opt in options]:
            if key in payload["__textures__"]:
                continue
            encoded = encode_texture_file(folder_path, os.path.join(folder_path, key))
            if encoded and not encoded.get("error"):
                payload["__textures__"][encoded["tex_key"]] = encoded["uri"]

    if discovered or "textures" in data:
        save_textures(folder_path, discovered)
    return discovered

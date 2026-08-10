"""Persistent viewer-only mesh labels stored beside a mod."""
import json
import os

METADATA_NAME = ".mod_viewer.json"

def load(folder_path):
    try:
        with open(os.path.join(folder_path, METADATA_NAME), encoding="utf-8") as fh:
            names = json.load(fh).get("mesh_names", {})
        return names if isinstance(names, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}

def save(folder_path, names):
    path = os.path.join(folder_path, METADATA_NAME)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"mesh_names": names}, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return {"saved": True, "path": path}

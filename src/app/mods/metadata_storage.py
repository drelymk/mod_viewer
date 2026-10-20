"""Shared metadata loading, atomic file replacement and transaction lock."""

import json
import os
import threading

from core.mod_source import is_archive_path


METADATA_NAME = ".mod_viewer.json"

_LOCK = threading.RLock()


def load(folder_path, source=None):
    try:
        if source is not None:
            data = json.loads(source.read_text(
                source.resolve_resource(METADATA_NAME)))
        elif is_archive_path(folder_path):
            return {}
        else:
            with open(os.path.join(folder_path, METADATA_NAME), encoding="utf-8") as fh:
                data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError, UnicodeError):
        return {}


def _save(folder_path, data):
    if is_archive_path(folder_path):
        return {"saved": False,
                "error": "Viewer metadata cannot be saved for compressed mods."}
    path = os.path.join(folder_path, METADATA_NAME)
    temp_path = path + ".tmp"
    with open(temp_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(temp_path, path)
    return {"saved": True, "path": path}

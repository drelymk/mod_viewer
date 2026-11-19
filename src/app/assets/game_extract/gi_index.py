"""Complete, versioned Genshin Impact metadata index; native reads still prove each binding."""

import hashlib
import json
import os
from pathlib import Path

from app.settings import config, paths
from . import gi_runtime
from .runtime import check_cancelled
from .zzz_index import identity

MAX_BYTES = 256 * 1024 * 1024
FORMAT = 1
TYPES = ("Mesh", "Avatar", "AnimationClip", "AnimatorController", "SkinnedMeshRenderer")


def _pointer(value):
    return (isinstance(value, dict) and isinstance(value.get("cab"), str) and len(value["cab"]) <= 256
            and type(value.get("path_id")) is int
            and all(type(value[key]) is int and value[key] >= 0 for key in ("offset", "file_ordinal") if key in value))


def _renderer(value):
    """Facial routes keep only the renderer path and explicit mesh/Avatar pointers."""
    return (isinstance(value, dict) and set(value) == {"path", "mesh", "avatar", "blend_shape_count"}
            and isinstance(value["path"], str) and len(value["path"]) <= 4096
            and _pointer(value["mesh"]) and _pointer(value["avatar"])
            and type(value["blend_shape_count"]) is int and 0 <= value["blend_shape_count"] <= 256)


def _route(row):
    # Null-mesh renderers need full bone-path proof, which GI does not index; they stay unrouted.
    renderer = row.get("renderer")
    if not isinstance(renderer, dict) or not isinstance(renderer.get("mesh"), dict) or not renderer["mesh"].get("path_id"):
        return None
    route = {key: renderer.get(key) for key in ("path", "mesh", "avatar", "blend_shape_count")}
    return {**row, "renderer": route} if _renderer(route) else None


def fingerprint(folder):
    root = gi_runtime.validate_folder(folder)
    digest = hashlib.sha256(gi_runtime.INDEX_VERSION.encode())
    for file in gi_runtime.archives(root):
        info = file.stat()
        digest.update(f"{file.relative_to(root).as_posix()}|{info.st_size}|{info.st_mtime_ns}\n".encode())
    return digest.hexdigest()


def index_path(folder):
    key = os.path.normcase(os.path.realpath(folder))
    return Path(paths.asset_index_dir()) / f"game-gimi-{hashlib.sha256(key.encode()).hexdigest()[:32]}.json"


def valid_rows(rows):
    if not isinstance(rows, list) or len(rows) > 1000000:
        return False
    seen = set()
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get("name"), str) or len(row["name"]) > 1024
                or row.get("type") not in TYPES
                or not isinstance(row.get("block"), str) or len(row["block"]) > 1024
                or not isinstance(row.get("cab"), str) or len(row["cab"]) > 256
                or any(type(row.get(key)) is not int for key in ("offset", "file_ordinal", "path_id"))
                or row["offset"] < 0 or not 0 <= row["file_ordinal"] < 4096):
            return False
        shape = row.get("shape")
        if row["type"] == "Mesh" and (not isinstance(shape, list) or len(shape) > 4096 or any(
                not isinstance(item, list) or len(item) != 2 or any(type(number) is not int or not 0 <= number <= 1500000 for number in item)
                for item in shape)):
            return False
        if (row["type"] == "SkinnedMeshRenderer") != ("renderer" in row) or (
                "renderer" in row and not _renderer(row["renderer"])):
            return False
        key = identity(row)
        if key in seen:
            return False
        seen.add(key)
    return True


def read(folder):
    try:
        file = index_path(folder)
        if file.stat().st_size > MAX_BYTES:
            return "stale", None, 0
        value = json.loads(file.read_text(encoding="utf-8"))
        if (not isinstance(value, dict) or value.get("format") != FORMAT or not valid_rows(value.get("rows"))
                or value.get("fingerprint") != fingerprint(folder)):
            return "stale", None, 0
        rows = value["rows"]
        return "ready", rows, sum(row["type"] == "Mesh" for row in rows)
    except FileNotFoundError:
        return "missing", None, 0
    except (OSError, ValueError, TypeError):
        return "stale", None, 0


def status(folder):
    state, _index, count = read(folder)
    return dict(state=state, meshes=count)


def remove(folder):
    try:
        index_path(folder).unlink(missing_ok=True)
    except OSError:
        pass


def scan(client, folder, progress, cancel):
    root = gi_runtime.validate_folder(folder)
    files = gi_runtime.archives(root)
    rows = []
    for number in range(0, len(files), 16):
        progress("index", number, len(files))
        check_cancelled(cancel)
        for row in client.scan_many(files[number:number + 16]):
            if row.get("type") == "SkinnedMeshRenderer":
                row = _route(row)
                if row is None:
                    continue
            rows.append(row)
    progress("index", len(files), len(files))
    if not valid_rows(rows):
        raise ValueError("The Genshin Impact metadata index is incomplete or ambiguous.")
    return rows


def build(client, folder, progress, cancel):
    current = fingerprint(folder)
    rows = scan(client, folder, progress, cancel)
    if fingerprint(folder) != current:
        raise ValueError("The Genshin Impact installation changed during indexing. Retry after the game update finishes.")
    payload = json.dumps(dict(format=FORMAT, fingerprint=current, rows=rows), separators=(",", ":")).encode()
    if len(payload) > MAX_BYTES:
        raise ValueError("The Genshin Impact index exceeds its size limit.")
    file = index_path(folder)
    file.parent.mkdir(parents=True, exist_ok=True)
    config.write_bytes_atomic(str(file), payload)
    return rows, sum(row["type"] == "Mesh" for row in rows)


def ensure(client, folder, progress, cancel, rebuild=False):
    if not rebuild:
        state, rows, _count = read(folder)
        if state == "ready":
            return rows
    return build(client, folder, progress, cancel)[0]


def by_cab(rows):
    index = {}
    for row in rows:
        index.setdefault(row["cab"].casefold(), []).append(row)
    return index


def resolve_pointer(cab_index, cab, path_id, kind="AnimationClip"):
    """Resolve a pointer by case-insensitive CAB name; a CAB present in several blocks is ambiguous, never guessed."""
    candidates = cab_index.get(cab.casefold(), [])
    if len({row["block"] for row in candidates}) > 1:
        return None
    matches = [row for row in candidates if row["path_id"] == path_id and row["type"] == kind]
    return matches[0] if len(matches) == 1 else None

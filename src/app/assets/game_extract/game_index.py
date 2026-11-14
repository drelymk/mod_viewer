"""Persistent per-game metadata index that accelerates game-folder extraction.

The index only records SkeletalMesh section shapes so mesh lookup can run locally. It is an accelerator:
extraction still proves every mesh through native exports, and a missing, stale or invalid index falls
back to native searches.
"""

import hashlib
import json
import os
from pathlib import Path

from app.settings import config, paths
from . import pipeline
from .runtime import SearchTruncated, VERSION, check_cancelled, find_paks

FORMAT = 1
KIND = "WWMI"
SCAN_ROOTS = (pipeline.ROLE_ROOT, "Client/Content/Aki/Character/Prop/RoleProp")
MAX_BYTES = 64 * 1024 * 1024
BATCH_SIZE = 250


def fingerprint(game_folder):
    """Identify the installed game data: runtime version plus every archive's name, size and mtime."""
    paks = find_paks(game_folder)
    digest = hashlib.sha256(VERSION.encode("utf-8"))
    for file in sorted(paks.iterdir(), key=lambda item: item.name.lower()):
        if file.suffix.lower() in (".pak", ".utoc", ".ucas") and file.is_file():
            stat = file.stat()
            digest.update(f"{file.name.lower()}|{stat.st_size}|{stat.st_mtime_ns}\n".encode("utf-8"))
    return digest.hexdigest()


def index_path(game_folder):
    key = os.path.normcase(os.path.realpath(game_folder))
    return Path(paths.asset_index_dir()) / f"game-{KIND.lower()}-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:32]}.json"


class GameIndex:
    def __init__(self, meshes):
        self.meshes = meshes
        self._lower = {package: package.lower() for package in meshes}

    def search(self, query, under):
        prefix = under.rstrip("/") + "/"
        query = query.lower()
        return [package for package, lowered in self._lower.items() if package.startswith(prefix) and query in lowered]

    def shapes(self, package):
        return {tuple(shape) for shape in self.meshes.get(package, ())}


def valid_meshes(value):
    if not isinstance(value, dict):
        return False
    for package, shapes in value.items():
        if not isinstance(package, str) or not package.startswith("Client/Content/") or not isinstance(shapes, list):
            return False
        if any(not isinstance(shape, list) or len(shape) != 2 or any(type(number) is not int for number in shape) for shape in shapes):
            return False
    return True


def read(game_folder):
    """Return (state, index, count): state is 'missing', 'stale' or 'ready'."""
    file = index_path(game_folder)
    try:
        if file.stat().st_size > MAX_BYTES:
            return "stale", None, 0
        value = json.loads(file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "missing", None, 0
    except (OSError, ValueError):
        return "stale", None, 0
    if not isinstance(value, dict) or value.get("format") != FORMAT or not valid_meshes(value.get("meshes")):
        return "stale", None, 0
    try:
        current = fingerprint(game_folder)
    except (OSError, ValueError):
        return "stale", None, 0
    if value.get("fingerprint") != current:
        return "stale", None, 0
    return "ready", GameIndex(value["meshes"]), len(value["meshes"])


def status(game_folder):
    state, _index, count = read(game_folder)
    return dict(state=state, meshes=count)


def remove(game_folder):
    try:
        index_path(game_folder).unlink()
    except OSError:
        pass


def list_meshes(client, folder, cancel):
    check_cancelled(cancel)
    try:
        return client.search("", "SkeletalMesh", folder)
    except SearchTruncated:
        # Child recursion cannot recover direct packages omitted by a broad search.
        # A truncated direct search must fail the build, never publish a partial index.
        try:
            packages = client.search("", "SkeletalMesh", folder, recursive=False)
        except SearchTruncated as error:
            raise SearchTruncated("The game index is incomplete: a folder exceeds the native search limit.") from error
        for name in client.folders(folder):
            packages += list_meshes(client, folder + "/" + name, cancel)
        return packages


def build(client, game_folder, progress, cancel):
    current = fingerprint(game_folder)
    progress("index", 0, 0)
    packages = []
    for root in SCAN_ROOTS:
        packages += list_meshes(client, root, cancel)
    packages = sorted(dict.fromkeys(packages))
    directory = client.work / "index-shapes"
    meshes = {}
    for start in range(0, len(packages), BATCH_SIZE):
        progress("index", start, len(packages))
        chunk = packages[start:start + BATCH_SIZE]
        files = client.batch("json", chunk, directory)
        for package in chunk:
            check_cancelled(cancel)
            file = pipeline.exported_file(files.get(package), directory, ".json")
            try:
                shapes = pipeline.section_shapes(file) if file else None
            except (ValueError, TypeError, AttributeError, OSError):
                shapes = None
            if shapes is None or any(type(number) is not int for shape in shapes for number in shape):
                raise ValueError("The game index is incomplete: mesh metadata could not be read. Existing index data was preserved.")
            if shapes:
                meshes[package] = sorted([vertices, indices] for vertices, indices in shapes)
    progress("index", len(packages), len(packages))
    payload = json.dumps(dict(format=FORMAT, fingerprint=current, meshes=meshes), separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_BYTES:
        raise ValueError("The game index exceeds its size limit.")
    file = index_path(game_folder)
    file.parent.mkdir(parents=True, exist_ok=True)
    config.write_bytes_atomic(str(file), payload)
    return GameIndex(meshes), len(meshes)


def ensure(client, game_folder, progress, cancel, rebuild=False):
    """Return the ready index, building it first when it is missing or stale."""
    if not rebuild:
        state, index, _count = read(game_folder)
        if state == "ready":
            return index
    return build(client, game_folder, progress, cancel)[0]

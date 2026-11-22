"""Versioned Genshin Impact character metadata index; native reads still prove each binding.

The scan covers every block, but the index keeps only what character extraction reads:
every Avatar, the meshes of character Avatar bundles, the renderers drawing them, the
character controllers and their clips, and the locations of the material and texture
files those renderers lead to. Kept meshes, clips and files retain every copy of their
serialized file (CAB), so a CAB found in several blocks stays as ambiguous as before.
Skill objects named for a character family (``SkillObj_<token>_<object>``) keep their
models, renderers, ``Ani_SkillObj_<token>_`` clips and controllers for name pairing,
plus the effect prefabs whose Follow component mounts them on a character point.
"""

import hashlib
import json
import math
import os
from pathlib import Path
import re

from app.settings import config, paths
from . import gi_runtime
from .runtime import ExtractionCancelled, check_cancelled
from .zzz_index import IndexRows, MAX_FILES, _file_table, _pack_files, _unpack_files, identity, scan_batches
from .zzz_pipeline import _family

MAX_BYTES = 256 * 1024 * 1024
FORMAT = 2
MAX_CONTROLLERS = 4096
SKILL_OBJECT = re.compile(r"SkillObj_(?P<token>[^_]+)_(?P<thing>.+)_ModelAvatar")
SKILL_CLIP = re.compile(r"Ani_SkillObj_(?P<token>[^_]+)_.+")
# The scan sees every material/texture file in the game before limiting it to MAX_FILES.
SCAN_FILES = 8 * MAX_FILES
TYPES = ("Mesh", "Avatar", "AnimationClip", "AnimatorController", "SkinnedMeshRenderer", "Follow")
MAX_FOLLOW_ANIMATORS = 64
MAX_FOLLOW_DEPTH = 64


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


def _numbers(value, count):
    return (isinstance(value, list) and len(value) == count
            and all(type(item) in (int, float) and math.isfinite(item) and abs(item) <= 1e6 for item in value))


def _follow(row):
    """An effect prefab root's Follow point (None when unreadable) and the Animators beneath it,
    each with its Avatar and controller pointers (either may be null, not both) and the local
    transforms from the root's child down to it."""
    point, animators = row.get("follow"), row.get("animators")
    return ((point is None or (isinstance(point, str) and len(point) <= 256)) and type(row.get("enabled")) is bool
            and isinstance(animators, list) and 0 < len(animators) <= MAX_FOLLOW_ANIMATORS and all(
                isinstance(item, dict) and set(item) == {"avatar", "controller", "path", "chain"}
                and (item["avatar"] is not None or item["controller"] is not None)
                and all(item[key] is None or _pointer(item[key]) for key in ("avatar", "controller"))
                and isinstance(item["path"], str) and len(item["path"]) <= 4096
                and isinstance(item["chain"], list) and 0 < len(item["chain"]) <= MAX_FOLLOW_DEPTH and all(
                    isinstance(node, dict) and set(node) == {"translation", "rotation", "scale"}
                    and _numbers(node["translation"], 3) and _numbers(node["rotation"], 4) and _numbers(node["scale"], 3)
                    for node in item["chain"])
                for item in animators))


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
        if (row["type"] == "Follow") != ("animators" in row) or ("animators" in row and not _follow(row)):
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
        rows = IndexRows(value["rows"], _unpack_files(value.get("files")))
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


def _names(value):
    return {item.casefold() for item in value if isinstance(item, str)} if isinstance(value, list) else set()


def _clip_files(client, controllers, cancel):
    """CABs of every clip the character controllers point to."""
    if len(controllers) > MAX_CONTROLLERS:
        raise ValueError("The Genshin Impact installation has too many character controllers to index.")
    cabs = set()
    for row in controllers:
        check_cancelled(cancel)
        try:
            controller = client.read(row)
        except ExtractionCancelled:
            raise
        except (ValueError, KeyError, TypeError):
            # Extraction reads the same controller and fails visibly there.
            continue
        states = controller.get("states") if isinstance(controller, dict) else None
        for state in states if isinstance(states, list) else []:
            motions = state.get("motions") if isinstance(state, dict) else None
            for motion in motions if isinstance(motions, list) else []:
                leaves = motion.get("leaves") if isinstance(motion, dict) else None
                for leaf in leaves if isinstance(leaves, list) else []:
                    if isinstance(leaf, dict) and isinstance(leaf.get("cab"), str):
                        cabs.add(leaf["cab"].casefold())
    return cabs


def limit(client, rows, entries, material_textures, cancel):
    """Keep only the rows and file locations character extraction reads."""
    avatars = [row for row in rows if row["type"] == "Avatar"]
    characters = [row for row in avatars if _family(row["name"])]
    families = {_family(row["name"]) for row in characters}
    # A family's last name token also names its skill objects (paired by name only).
    tokens = {family.rsplit("_", 1)[-1] for family in families}
    objects = [row for row in avatars if (match := SKILL_OBJECT.fullmatch(row["name"])) and match["token"] in tokens]
    bundles = {(row["block"], row["offset"]) for row in (*characters, *objects)}
    mesh_files = {row["cab"].casefold() for row in rows
                  if row["type"] == "Mesh" and (row["block"], row["offset"]) in bundles}
    meshes = [row for row in rows if row["type"] == "Mesh" and row["cab"].casefold() in mesh_files]
    drawn = {(row["cab"].casefold(), row["path_id"]) for row in meshes}
    renderers, material_files = [], set()
    for row in rows:
        if row["type"] != "SkinnedMeshRenderer":
            continue
        route = _route(row)
        mesh = route["renderer"]["mesh"] if route else None
        if mesh is None or (mesh["cab"].casefold(), mesh["path_id"]) not in drawn:
            continue
        # Material files feed only the file table below; routes keep their indexed fields.
        renderers.append({key: value for key, value in route.items() if key != "materials"})
        material_files |= _names(row.get("materials"))
    controllers = [row for row in rows if row["type"] == "AnimatorController" and row["name"] in families]
    # Skill objects' own controllers (named like their clips) chain object clips, which may
    # carry other names; like character controllers, they keep every clip they point to.
    object_controllers = [row for row in rows if row["type"] == "AnimatorController"
                          and (match := SKILL_CLIP.fullmatch(row["name"])) and match["token"] in tokens]
    controllers += object_controllers
    clip_files = _clip_files(client, controllers, cancel)
    clip_files |= {row["cab"].casefold() for row in rows if row["type"] == "AnimationClip"
                   and (match := SKILL_CLIP.fullmatch(row["name"])) and match["token"] in tokens}
    clips = [row for row in rows if row["type"] == "AnimationClip" and row["cab"].casefold() in clip_files]
    # Effect prefabs whose Animators have a kept skill object's Avatar or play one of their
    # controllers (resolved again, unambiguously, on use).
    held = {(row["cab"].casefold(), row["path_id"]) for row in (*objects, *object_controllers)}
    follows = [row for row in rows if row["type"] == "Follow" and any(
        (item[key]["cab"].casefold(), item[key]["path_id"]) in held
        for item in row["animators"] for key in ("avatar", "controller") if item[key] is not None)]
    wanted = set(material_files)
    for cab in material_files:
        wanted |= material_textures.get(cab, set())
    kept = [entry for entry in entries if entry[3].casefold() in wanted]
    if len(kept) > MAX_FILES:
        raise ValueError("The Genshin Impact file table exceeds its limits.")
    files = _file_table(kept)
    return IndexRows([*avatars, *meshes, *renderers, *controllers, *clips, *follows], files)


def scan(client, folder, progress, cancel):
    root = gi_runtime.validate_folder(folder)
    files = gi_runtime.archives(root)
    rows, entries, material_textures = [], [], {}

    def consume(batch):
        for row in batch:
            kind = row.get("type")
            if kind == "File":
                entries.append((row["block"], row["offset"], row["file_ordinal"], row["cab"]))
                if len(entries) > SCAN_FILES:
                    raise ValueError("The Genshin Impact file table exceeds its limits.")
            elif kind == "Material":
                # Every material of a referenced file counts; unreadable ones add nothing.
                material_textures.setdefault(row["cab"].casefold(), set()).update(_names(row.get("textures")))
            elif kind in TYPES:
                rows.append(row)
            else:
                raise ValueError("The Genshin Impact metadata index is incomplete or ambiguous.")

    scan_batches(client, files, progress, cancel, consume)
    progress("index", len(files), len(files))
    rows = limit(client, rows, entries, material_textures, cancel)
    if not valid_rows(rows):
        raise ValueError("The Genshin Impact metadata index is incomplete or ambiguous.")
    return rows


def build(client, folder, progress, cancel):
    current = fingerprint(folder)
    rows = scan(client, folder, progress, cancel)
    if fingerprint(folder) != current:
        raise ValueError("The Genshin Impact installation changed during indexing. Retry after the game update finishes.")
    payload = json.dumps(dict(format=FORMAT, fingerprint=current, rows=rows, files=_pack_files(rows.files)),
                         separators=(",", ":")).encode()
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

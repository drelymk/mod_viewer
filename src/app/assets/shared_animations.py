"""Game-wide animation libraries stored once at an Asset Folder root.

Body-type locomotion is authored once per game skeleton and played by every
character whose body matches it. Each library keeps its clips in its own source
skeleton's space; playback maps them onto a loaded rig through bones proven to
be the same joint (same name, mapped parent and reference transform), so one
copy serves every compatible character.
"""

import hashlib
import json
import math
import os
import re

import numpy as np

from . import paths
from .animation_conversion import _near, _same_rotation


DIRECTORY = "SharedAnimation"
FORMAT = "mod-viewer-shared-animations"
VERSION = 1
LIBRARY_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}")
MAX_LIBRARIES = 64
MAX_CLIPS = 1024
MAX_BONES = 4096
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
# Clip metadata sits directly in its library; loading re-checks real containment.
_CLIP_FILE = re.compile(r"[A-Za-z0-9_-]{1,64}\.json")


def _vector(value, size):
    if (not isinstance(value, list) or len(value) != size
            or any(type(item) not in (int, float) or not math.isfinite(item) or abs(item) > 1e6 for item in value)):
        raise ValueError("Invalid shared animation skeleton.")
    return [float(item) for item in value]


def validate_skeleton(bones):
    """Normalize a bounded, topologically ordered source skeleton."""
    if not isinstance(bones, list) or not 1 <= len(bones) <= MAX_BONES:
        raise ValueError("Invalid shared animation skeleton.")
    names, result = set(), []
    for index, bone in enumerate(bones):
        name = bone.get("name") if isinstance(bone, dict) else None
        parent = bone.get("parent") if isinstance(bone, dict) else None
        if (not isinstance(name, str) or not 1 <= len(name) <= 256 or name in names
                or type(parent) is not int or not -1 <= parent < index or (parent < 0) != (index == 0)):
            raise ValueError("Invalid shared animation skeleton.")
        names.add(name)
        rotation = _vector(bone.get("rotation"), 4)
        scale = _vector(bone.get("scale", [1, 1, 1]), 3)
        if abs(math.sqrt(sum(value * value for value in rotation)) - 1) > 0.001 or min(abs(v) for v in scale) < 1e-6:
            raise ValueError("Invalid shared animation skeleton.")
        result.append(dict(name=name, parent=parent, translation=_vector(bone.get("translation"), 3),
                           rotation=rotation, scale=scale))
    return result


def skeleton_id(bones):
    return hashlib.sha256(json.dumps(bones, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def bone_mapping(source, target):
    """Map source bones to the target bones proven to be the same joint.

    A bone maps only when the target has its name, its parent maps to the
    target's parent (roots map to roots) and its reference rotation,
    translation and scale agree. Character-specific chains that reuse a name
    with another parent or rest pose, and everything below them, stay unmapped.
    """
    index = {bone["name"]: number for number, bone in enumerate(target)}
    mapping = {}
    for number, bone in enumerate(source):
        other = index.get(bone["name"])
        if other is None:
            continue
        right = target[other]
        parent, other_parent = bone["parent"], right["parent"]
        if (parent < 0) != (other_parent < 0) or (parent >= 0 and mapping.get(parent) != other_parent):
            continue
        if (_same_rotation(bone["rotation"], right["rotation"]) and _near(bone["translation"], right["translation"])
                and _near(bone.get("scale", [1, 1, 1]), right.get("scale", [1, 1, 1]))):
            mapping[number] = other
    return mapping


def _json(file, limit):
    if not file:
        raise ValueError("Shared animation metadata is unavailable.")
    with open(file, "rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Shared animation metadata is too large.")
    return json.loads(data)


def read_library(directory):
    """Validate one library manifest; clip binaries stay lazy."""
    raw = _json(paths.safe_asset_path(directory, "manifest.json"), MAX_MANIFEST_BYTES)
    if (not isinstance(raw, dict) or raw.get("format") != FORMAT or type(raw.get("version")) is not int
            or raw["version"] != VERSION or not isinstance(raw.get("label"), str) or not 1 <= len(raw["label"]) <= 64
            or not isinstance(raw.get("clips"), list) or not 1 <= len(raw["clips"]) <= MAX_CLIPS):
        raise ValueError("Invalid shared animation library.")
    bones = validate_skeleton(raw.get("skeleton"))
    if raw.get("skeleton_id") != skeleton_id(bones):
        raise ValueError("Shared animation skeleton fingerprint differs.")
    clips, seen = [], set()
    for entry in raw["clips"]:
        if not isinstance(entry, dict):
            raise ValueError("Invalid shared animation clip.")
        key, name, file, duration, keyed = (entry.get(field) for field in ("id", "name", "file", "duration", "keyed_bones"))
        if (not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", key) or key in seen
                or not isinstance(name, str) or not 1 <= len(name) <= 128
                or type(duration) not in (int, float) or not math.isfinite(duration) or not 0 < duration <= 3600
                or not isinstance(file, str) or not _CLIP_FILE.fullmatch(file)
                or not isinstance(keyed, list) or len(set(keyed)) != len(keyed)
                or any(type(bone) is not int or not 0 <= bone < len(bones) for bone in keyed)):
            raise ValueError("Invalid shared animation clip.")
        seen.add(key)
        clips.append(dict(id=key, name=name, file=file, duration=duration, keyed_bones=sorted(keyed)))
    fingerprint = raw.get("fingerprint")
    return dict(label=raw["label"], skeleton_id=raw["skeleton_id"], bones=bones, clips=clips,
                fingerprint=fingerprint if isinstance(fingerprint, str) and len(fingerprint) <= 128 else None)


def structural_bones(bones, keyed):
    """The root plus every keyed bone that is the parent of another keyed bone.

    These form the animated chain itself. Keyed end bones (corrective helpers,
    weapon props) that a character re-authors or lacks may hold its rest pose.
    """
    keyed = set(keyed)
    return {0} | {bones[bone]["parent"] for bone in keyed if bones[bone]["parent"] in keyed}


def core_bones(library):
    """The structural chain of the bones keyed by more than half of the library's clips.

    This is the body the set animates; accessory chains keyed by a few clips stay
    outside it. A rig without the whole core is another body type.
    """
    counts = {}
    for clip in library["clips"]:
        for bone in clip["keyed_bones"]:
            counts[bone] = counts.get(bone, 0) + 1
    return structural_bones(library["bones"], [bone for bone, count in counts.items() if 2 * count > len(library["clips"])])


def discover(root, rig):
    """Shared clips a loaded rig can play, each with its proven bone mapping.

    A library applies only when its whole core (see ``core_bones``) maps; a clip
    is offered only when its structural chain maps, so no animated chain is
    dropped, while unproven keyed end bones hold the rig's rest pose. Invalid
    libraries are skipped independently.
    """
    shared = paths.safe_asset_dir(root, DIRECTORY) if root else None
    if not shared:
        return []
    try:
        with os.scandir(shared) as entries:
            names = [entry.name for entry in entries
                     if LIBRARY_NAME.fullmatch(entry.name) and entry.is_dir(follow_symlinks=False)]
    except OSError:
        return []
    names = sorted(names, key=lambda value: (value.casefold(), value))[:MAX_LIBRARIES]
    found = []
    for name in names:
        try:
            directory = paths.safe_asset_dir(shared, name)
            library = read_library(directory)
            mapping = bone_mapping(library["bones"], rig["bones"])
            if not core_bones(library) <= set(mapping):
                continue
            clips = [clip for clip in library["clips"]
                     if structural_bones(library["bones"], clip["keyed_bones"]) <= set(mapping)]
            if not clips:
                continue
            ordered = sorted(mapping)
            found.append(dict(name=name, directory=directory, label=library["label"], clips=clips,
                              fingerprint=library["fingerprint"],
                              source=dict(id=library["skeleton_id"], bones=library["bones"]), mapping=mapping,
                              source_bones=np.array(ordered, dtype=np.intp),
                              target_bones=np.array([mapping[bone] for bone in ordered], dtype=np.intp)))
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            continue
    return found


def catalog_id(library, clip):
    """Stable opaque identity of a shared clip, distinct from Asset clip IDs."""
    return "shared-" + hashlib.sha256(f"{library}/{clip}".encode()).hexdigest()[:16]


def retarget(data, frames, library, rig):
    """Frame-major (frames, rig bones, 7) rows: source motion on mapped joints, rig rest elsewhere."""
    source = len(library["source"]["bones"])
    rows = np.frombuffer(data, "<f4", count=frames * source * 7).reshape(frames, source, 7)
    rest = np.array([[*bone["translation"], *bone["rotation"]] for bone in rig["bones"]], dtype="<f4")
    output = np.repeat(rest[np.newaxis], frames, axis=0)
    output[:, library["target_bones"]] = rows[:, library["source_bones"]]
    return output

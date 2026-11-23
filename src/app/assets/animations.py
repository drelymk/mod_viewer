"""Lazy, contained animation libraries attached to verified Asset skeletons."""

import hashlib
import json
import math
import re
import uuid
from threading import RLock

import numpy as np

from app.runtime import server
from . import animation_keys, paths, shared_animations
from .animation_morphs import bounded, idle_blink, morph_names, read_morphs


_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_MAX_BYTES = 128 * 1024 * 1024


_SEQUENCE_SLACK = 1e-6


def _sequence_number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("Invalid animation sequence number.")
    return float(value)


# Libraries extracted before ``by_name`` flagged name-ordered previews in their names.
_LEGACY_BY_NAME = " (by name)"


def _sequences(raw_sequences, entries):
    """Return cleaned sequences, or [] when the key is absent or any sequence is invalid.

    ``by_name`` marks a preview paired by clip names rather than game data.
    """
    try:
        if not isinstance(raw_sequences, list) or len(raw_sequences) > 256:
            return []
        seen = set()
        sequences = []
        for raw in raw_sequences:
            if not isinstance(raw, dict):
                raise ValueError("Invalid animation sequence.")
            key = raw.get("id")
            name, cyclic, steps = raw.get("name"), raw.get("cyclic"), raw.get("steps")
            if (not isinstance(key, str) or not _ID.fullmatch(key) or key in seen or key in entries
                    or not isinstance(name, str) or not 1 <= len(name) <= 128
                    or type(cyclic) is not bool or not isinstance(steps, list) or not 2 <= len(steps) <= 64):
                raise ValueError("Invalid animation sequence.")
            by_name = raw.get("by_name", False)
            if type(by_name) is not bool:
                raise ValueError("Invalid animation sequence flag.")
            if not by_name and name.endswith(_LEGACY_BY_NAME) and len(name) > len(_LEGACY_BY_NAME):
                name, by_name = name[:-len(_LEGACY_BY_NAME)], True
            restart = _sequence_number(raw.get("restart"))
            if restart < 0:
                raise ValueError("Invalid animation sequence restart.")
            cleaned = []
            for step in steps:
                if not isinstance(step, dict):
                    raise ValueError("Invalid animation sequence step.")
                clip = step.get("clip")
                if not isinstance(clip, str) or clip not in entries:
                    raise ValueError("Invalid animation sequence clip.")
                duration = _sequence_number(entries[clip]["duration"])
                start = _sequence_number(step.get("start"))
                leave = _sequence_number(step.get("leave"))
                fade = _sequence_number(step.get("fade"))
                if (not 0 <= start < leave or leave > duration + _SEQUENCE_SLACK
                        or fade < 0 or fade > duration - leave + _SEQUENCE_SLACK):
                    raise ValueError("Invalid animation sequence step timing.")
                cleaned.append({"clip": clip, "start": start, "leave": leave, "fade": fade})
            if restart >= _sequence_number(entries[cleaned[0]["clip"]]["duration"]):
                raise ValueError("Invalid animation sequence restart.")
            # Playback crossfades into the following step (the head after the last step), so a fade
            # may never outlast that step's own leave; a cyclic restart must precede the head's leave.
            for number, step in enumerate(cleaned):
                wraps = number == len(cleaned) - 1
                following = cleaned[0] if wraps else cleaned[number + 1]
                following_start = restart if wraps and cyclic else following["start"]
                if step["fade"] > following["leave"] - following_start + _SEQUENCE_SLACK:
                    raise ValueError("Invalid animation sequence fade.")
            if cyclic and restart >= cleaned[0]["leave"]:
                raise ValueError("Invalid animation sequence restart.")
            seen.add(key)
            sequences.append({"id": key, "name": name, "cyclic": cyclic, "restart": restart, "steps": cleaned,
                              **({"by_name": True} if by_name else {})})
        return sequences
    except (ValueError, TypeError, KeyError):
        return []


def _json(file, limit=1024 * 1024):
    if not file:
        raise ValueError("Animation metadata is unavailable.")
    with open(file, "rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Animation metadata is too large.")
    return json.loads(data)


def _valid_transforms(data, size, width):
    """Bounded rows whose rotation quaternions are unit length within 0.001.

    Summing the squares left to right in float64 matches the scalar check exactly.
    """
    rows = np.frombuffer(data, "<f4", count=size // 4).reshape(-1, width)
    if not bounded(rows):
        return False
    q = rows[:, 3:7].astype(np.float64)
    length = q[:, 0] * q[:, 0] + q[:, 1] * q[:, 1] + q[:, 2] * q[:, 2] + q[:, 3] * q[:, 3]
    return bool((np.abs(length - 1) <= 0.001).all())


def _root_motion_bone(value, bones):
    """The bone whose local space holds a clip's root-motion track, or None without one."""
    if value is None:
        return None
    bone = value.get("bone") if isinstance(value, dict) and set(value) == {"bone"} else None
    if type(bone) is not int or not 0 <= bone < bones:
        raise ValueError("Animation root motion does not match the loaded rig.")
    return bone


def _read_clip(directory, entry, rig, *, validated_morphs=None, full_validation=True):
    """Read bounded playback data, with full validation for extraction."""
    raw = _json(paths.safe_asset_path(directory, entry["file"]))
    if not isinstance(raw, dict):
        raise ValueError("Invalid animation metadata.")
    frames, bones, duration = raw.get("frame_count"), raw.get("bone_count"), raw.get("duration")
    width = {"frame-major-tq-f32le": 7, "frame-major-tqs-f32le": 10}.get(raw.get("layout"))
    if (raw.get("format") != "mod-viewer-animation" or type(raw.get("version")) is not int or raw["version"] not in (1, 2)
            or raw.get("coordinate_space") != "asset" or width != (7 if raw["version"] == 1 else 10)
            or raw.get("skeleton_id") != rig["id"]
            or type(frames) is not int or not 2 <= frames <= 60000
            or type(bones) is not int or bones != len(rig["bones"])
            or duration != entry["duration"] or type(duration) not in (int, float)
            or not math.isfinite(duration) or not 0 < duration <= 3600):
        raise ValueError("Animation does not match the loaded rig.")
    bone_size = frames * bones * width * 4
    names = morph_names(raw.get("morphs"))
    morph_size = frames * len(names) * 4
    # An optional per-frame MotionT.xyz/MotionQ.xyzw track follows the morph weights.
    motion = _root_motion_bone(raw.get("root_motion"), bones)
    size = bone_size + morph_size + (frames * 7 * 4 if motion is not None else 0)
    if size > _MAX_BYTES:
        raise ValueError("Animation data is too large.")
    file = raw.get("data_file")
    path = paths.safe_asset_path(directory, file) if isinstance(file, str) else None
    if not path:
        raise ValueError("Animation data is unavailable.")
    with open(path, "rb") as stream:
        data = stream.read(size + 1)
    if len(data) != size or (full_validation and hashlib.sha256(data).hexdigest() != raw.get("sha256")):
        raise ValueError("Animation data is incomplete or has changed.")
    if full_validation and not _valid_transforms(data, bone_size, width):
        raise ValueError("Animation contains invalid transforms.")
    if full_validation and motion is not None and not _valid_transforms(data[bone_size + morph_size:], frames * 7 * 4, 7):
        raise ValueError("Animation contains an invalid root-motion track.")
    morph_data = None
    if names:
        if full_validation and not bounded(np.frombuffer(data, "<f4", count=morph_size // 4, offset=bone_size)):
            raise ValueError("Animation contains invalid morph weights.")
        morph_data = read_morphs(directory, raw["morphs"], validated=validated_morphs, full_validation=full_validation)
    return raw, data, morph_data


def read_clip(directory, entry, rig, *, validated_morphs=None):
    """Validate one contained clip without publishing it."""
    raw, data, _morph_data = _read_clip(directory, entry, rig, validated_morphs=validated_morphs)
    return raw, data


_LAYER_FILE = re.compile(r"SharedLayers/[0-9a-f]{32}\.bin")


def _shared_layers(raw, bone_count):
    """The Asset's baked ribbon layers for shared clips by (library, clip); invalid data yields none."""
    try:
        masks = raw.get("shared_layer_masks", [])
        layers = raw.get("shared_layers", [])
        if not isinstance(masks, list) or not isinstance(layers, list) or len(masks) > 4096 or len(layers) > 4096:
            return {}
        masks = [animation_keys.bone_list(mask, bone_count) for mask in masks]
        if any(not mask for mask in masks):
            return {}
        result = {}
        for layer in layers:
            library, clip, mask = layer.get("library"), layer.get("clip"), layer.get("mask")
            fingerprint, file, frames = layer.get("fingerprint"), layer.get("file"), layer.get("frame_count")
            if (not isinstance(library, str) or not shared_animations.LIBRARY_NAME.fullmatch(library)
                    or not isinstance(clip, str) or not _ID.fullmatch(clip)
                    or not isinstance(fingerprint, str) or not isinstance(file, str) or not _LAYER_FILE.fullmatch(file)
                    or type(frames) is not int or not 2 <= frames <= 60000
                    or type(mask) is not int or not 0 <= mask < len(masks) or (library, clip) in result):
                return {}
            result[library, clip] = dict(fingerprint=fingerprint, file=file, frame_count=frames, bone_ids=masks[mask])
        return result
    except (AttributeError, TypeError):
        return {}


def _read_shared_clip(library, entry, rig, animation_dir=None):
    """Read a source-skeleton clip, map it onto the loaded rig's proven joints and add the Asset's ribbon layer."""
    raw, data, morph_data = _read_clip(library["directory"], entry, library["source"], full_validation=False)
    if raw["version"] != 1 or morph_data is not None or raw.get("root_motion") is not None:
        raise ValueError("Shared animations carry only skeletal tracks.")
    keyed = animation_keys.bone_list(raw.get("keyed_bones"), raw["bone_count"])
    mapping = library["mapping"]
    if keyed is None or not shared_animations.structural_bones(library["source"]["bones"], keyed) <= set(mapping):
        raise ValueError("Shared animation does not match the loaded rig.")
    frames = shared_animations.retarget(data, raw["frame_count"], library, rig)
    keyed = {mapping[bone] for bone in keyed if bone in mapping}
    layer = entry.get("layer")
    if layer is not None:
        if layer["frame_count"] != raw["frame_count"] or not animation_dir:
            raise ValueError("The ribbon layer does not match its shared animation.")
        bones = layer["bone_ids"]
        size = raw["frame_count"] * len(bones) * 28
        path = paths.safe_asset_path(animation_dir, layer["file"])
        if not path:
            raise ValueError("The ribbon layer is unavailable.")
        with open(path, "rb") as stream:
            rows = stream.read(size + 1)
        if len(rows) != size:
            raise ValueError("The ribbon layer is incomplete or has changed.")
        frames[:, bones] = np.frombuffer(rows, "<f4").reshape(raw["frame_count"], len(bones), 7)
        keyed.update(bones)
    raw = {**raw, "skeleton_id": rig["id"], "bone_count": len(rig["bones"]), "keyed_bones": sorted(keyed)}
    return raw, frames.tobytes(), None


_PROP_FILE = re.compile(r"Props/[0-9a-f]{32}\.bin")
PROP_LAYOUT = "prop-v1"
# Per vertex: f32 position/normal/uv record (32 bytes), u16x4 joints, f32x4 weights; then u32 indices.
_PROP_VERTEX_BYTES = 56
# Each material section is one prop; geometry stays within the extractors' byte budgets.
MAX_PROPS = 64
MAX_PROP_VERTICES = 250000
MAX_PROP_INDICES = 1500000
_PROP_TEXTURE_FILE = re.compile(r"Props/[0-9a-f]{32}\.dds")
# Roles the viewer's game material binds; extraction maps the game's texture slots onto them.
_PROP_TEXTURE_ROLES = ("diffuse", "light_map", "material_map", "normal_map")
_MAX_PROP_TEXTURE_BYTES = 96 * 1024 * 1024
# Animated props carry their own skeleton: per paired clip, frame-major f32 records of
# each prop bone's asset-space transform (xyz translation, xyzw quaternion) relative to
# its placement (a socket's rest pose); the socket's clip motion is applied on top.
# Each track carries the clip window it shows in (``start``/``end`` seconds); its
# animation plays from the window's start and holds its last frame.
PROP_TRACK_LAYOUT = "prop-track-v1"
# Scaling props (objects that grow in or shrink away) store each bone's full asset-space
# affine delta instead: f32 3x4 rows (row-major), the same placement-relative transform.
PROP_AFFINE_TRACK_LAYOUT = "prop-track-v2"
_PROP_TRACK_BYTES = {PROP_TRACK_LAYOUT: 28, PROP_AFFINE_TRACK_LAYOUT: 48}
MAX_PROP_BONES = 256
MAX_PROP_TRACKS = 1024
MAX_PROP_TRACK_BYTES = 128 * 1024 * 1024
# A rigid prop may show only inside clip windows: each clip lists its [start, end] seconds.
MAX_PROP_WINDOWS = 64
_DIGEST = re.compile(r"[0-9a-f]{64}")


def pack_prop(positions, normals, texcoords, joints, weights, triangles):
    """``PROP_LAYOUT`` bytes: per-vertex f32 position/normal/uv records, u16x4 rig joints,
    f32x4 weights, then u32 triangle indices."""
    return b"".join([
        np.hstack([positions, normals, texcoords]).astype("<f4").tobytes(),
        np.asarray(joints).astype("<u2").tobytes(),
        np.asarray(weights).astype("<f4").tobytes(),
        np.asarray(triangles).astype("<u4").tobytes(),
    ])


def _props(raw):
    """The catalog's game props (shown during clip playback); invalid data yields none."""
    try:
        props = raw.get("props", [])
        if not isinstance(props, list) or len(props) > MAX_PROPS:
            return {}
        result = {}
        for prop in props:
            key, name, file = prop.get("id"), prop.get("name"), prop.get("file")
            vertices, indices, digest = prop.get("vertex_count"), prop.get("index_count"), prop.get("sha256")
            if (not isinstance(key, str) or not _ID.fullmatch(key) or key in result
                    or not isinstance(name, str) or not 1 <= len(name) <= 128
                    or not isinstance(file, str) or not _PROP_FILE.fullmatch(file) or prop.get("layout") != PROP_LAYOUT
                    or type(vertices) is not int or not 0 < vertices <= MAX_PROP_VERTICES
                    or type(indices) is not int or not 0 < indices <= MAX_PROP_INDICES or indices % 3
                    or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
                return {}
            textures = prop.get("textures", {})
            if not isinstance(textures, dict) or any(
                    role not in _PROP_TEXTURE_ROLES or not isinstance(texture, dict)
                    or not isinstance(texture.get("file"), str) or not _PROP_TEXTURE_FILE.fullmatch(texture["file"])
                    or not isinstance(texture.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", texture["sha256"])
                    for role, texture in textures.items()):
                return {}
            animated = _prop_animation(prop)
            if animated is None:
                return {}
            visible = _prop_windows(prop)
            if visible is None:
                return {}
            result[key] = dict(id=key, name=name, file=file, vertex_count=vertices, index_count=indices, sha256=digest,
                               textures={role: dict(file=texture["file"], sha256=texture["sha256"])
                                         for role, texture in textures.items()}, **animated,
                               **({"visible": visible} if visible else {}))
        return result
    except (AttributeError, TypeError):
        return {}


def _prop_windows(prop):
    """A rigid prop's clip windows {clip: [[start, end], ...]}; {} when absent, None when invalid.

    Only a rigid prop has windows: an animated prop (with ``tracks``) never carries them.
    """
    if "visible" not in prop:
        return {}
    raw = prop["visible"]
    if "tracks" in prop or not isinstance(raw, dict) or not 0 < len(raw) <= MAX_PROP_TRACKS:
        return None
    result = {}
    for clip, windows in raw.items():
        if (not isinstance(clip, str) or not _ID.fullmatch(clip) or not isinstance(windows, list)
                or not 1 <= len(windows) <= MAX_PROP_WINDOWS):
            return None
        cleaned = []
        for window in windows:
            if (not isinstance(window, list) or len(window) != 2
                    or any(type(value) not in (int, float) or not math.isfinite(value) for value in window)):
                return None
            start, end = float(window[0]), float(window[1])
            if not 0 <= start <= end <= 3600 or (cleaned and start <= cleaned[-1][1]):
                return None
            cleaned.append([start, end])
        result[clip] = cleaned
    return result


def _prop_animation(prop):
    """An animated prop's socket, skeleton size and clip tracks; {} for a rigid prop, None when invalid.

    An animated prop's socket is a rig bone, or null for the character mesh's origin.
    """
    socket, bones, tracks = prop.get("socket"), prop.get("bone_count"), prop.get("tracks")
    layout = prop.get("track_layout", PROP_TRACK_LAYOUT)
    if tracks is None:
        return {} if socket is None and bones is None and "track_layout" not in prop else None
    if (layout not in _PROP_TRACK_BYTES or (socket is not None and (type(socket) is not int or socket < 0)) or type(bones) is not int or not 0 < bones <= MAX_PROP_BONES
            or not isinstance(tracks, dict) or not 0 < len(tracks) <= MAX_PROP_TRACKS or any(
                not isinstance(clip, str) or not _ID.fullmatch(clip) or not isinstance(track, dict)
                or not isinstance(track.get("file"), str) or not _PROP_FILE.fullmatch(track["file"])
                or not isinstance(track.get("sha256"), str) or not _DIGEST.fullmatch(track["sha256"])
                or type(track.get("frame_count")) is not int or not 1 < track["frame_count"] <= 60000
                or type(track.get("duration")) not in (int, float) or not 0 < track["duration"] <= 3600
                or type(track.get("start")) not in (int, float) or type(track.get("end")) not in (int, float)
                or not 0 <= track["start"] < track["end"] <= 3600
                for clip, track in tracks.items())):
        return None
    return dict(socket=socket, bone_count=bones, track_layout=layout, tracks={
        clip: {key: track[key] for key in ("file", "sha256", "frame_count", "duration", "start", "end")}
        for clip, track in tracks.items()})


def _read_prop_tracks(directory, prop, clips):
    """One blob of the prop's checksummed, bounded tracks for the session's ``clips``,
    and clip id -> {offset, frame_count, duration, start, end} into it."""
    blobs, index, offset = [], {}, 0
    for clip, track in sorted(prop["tracks"].items()):
        if clip not in clips:
            continue
        size = track["frame_count"] * prop["bone_count"] * _PROP_TRACK_BYTES[prop["track_layout"]]
        path = paths.safe_asset_path(directory, track["file"])
        if not path or offset + size > MAX_PROP_TRACK_BYTES:
            raise ValueError("The prop animation is unavailable.")
        with open(path, "rb") as stream:
            data = stream.read(size + 1)
        if len(data) != size or hashlib.sha256(data).hexdigest() != track["sha256"] or not bounded(np.frombuffer(data, "<f4")):
            raise ValueError("The prop animation is incomplete or has changed.")
        blobs.append(data)
        index[clip] = dict(offset=offset, **{key: track[key] for key in ("frame_count", "duration", "start", "end")})
        offset += size
    return b"".join(blobs), index


def _read_prop(directory, prop, bone_count):
    """Bounded, checksummed prop geometry whose joints and triangles stay in range.

    An animated prop's joints index its own skeleton and its socket a ``bone_count`` rig bone.
    """
    if "tracks" in prop and prop["socket"] is not None and prop["socket"] >= bone_count:
        raise ValueError("The prop does not match the loaded rig.")
    vertices, indices = prop["vertex_count"], prop["index_count"]
    size = vertices * _PROP_VERTEX_BYTES + indices * 4
    path = paths.safe_asset_path(directory, prop["file"])
    if not path:
        raise ValueError("The prop geometry is unavailable.")
    with open(path, "rb") as stream:
        data = stream.read(size + 1)
    if len(data) != size or hashlib.sha256(data).hexdigest() != prop["sha256"]:
        raise ValueError("The prop geometry is incomplete or has changed.")
    records = np.frombuffer(data, "<f4", count=vertices * 8)
    joints = np.frombuffer(data, "<u2", count=vertices * 4, offset=vertices * 32)
    weights = np.frombuffer(data, "<f4", count=vertices * 4, offset=vertices * 40)
    triangles = np.frombuffer(data, "<u4", count=indices, offset=vertices * 56)
    if (not bounded(records) or not bounded(weights) or int(joints.max()) >= prop.get("bone_count", bone_count)
            or int(triangles.max()) >= vertices):
        raise ValueError("The prop geometry does not match the loaded rig.")
    return data


def _prop_textures(directory, prop):
    """Checksummed prop texture paths by role; a changed or missing texture is left out."""
    result = {}
    for role, texture in prop["textures"].items():
        path = paths.safe_asset_path(directory, texture["file"])
        if not path:
            continue
        with open(path, "rb") as stream:
            data = stream.read(_MAX_PROP_TEXTURE_BYTES + 1)
        if len(data) <= _MAX_PROP_TEXTURE_BYTES and hashlib.sha256(data).hexdigest() == texture["sha256"]:
            result[role] = path
    return result


def _release(session):
    for url in (*session["urls"], *session["prop_urls"]):
        server.release_geometry(url)
    server.release_texture_publication(session["prop_textures"])


def _idle_face(session):
    """Blink shapes shared by a library, for clips that carry no facial curves.

    The first catalog clip whose sparse targets include an eye-closing shape
    supplies the shared target data; the clip's own bones and weights are unused.
    Any problem leaves the clip without an idle face rather than failing it.
    """
    try:
        source = session.get("idle_source")
        if source is None:
            source = False
            for entry in session["entries"].values():
                if entry.get("shared"):
                    continue
                try:
                    raw = _json(paths.safe_asset_path(session["directory"], entry["file"]))
                    blink = idle_blink(raw["morphs"])
                    if blink:
                        morph_names(raw["morphs"])
                        source = (entry, blink)
                        break
                except (OSError, ValueError, TypeError, KeyError):
                    continue
            session["idle_source"] = source
        if not source:
            return None
        entry, (names, scales) = source
        morphs = _json(paths.safe_asset_path(session["directory"], entry["file"]))["morphs"]
        data = read_morphs(session["directory"], morphs, full_validation=False)
        descriptor = dict(names=names, components=morphs["components"])
        if scales is not None:
            descriptor["scales"] = scales
        return descriptor, data
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        return None


class AnimationSessions:
    def __init__(self):
        self._lock = RLock()
        self._sessions = {}

    def __contains__(self, token):
        with self._lock:
            return token in self._sessions

    def clear(self):
        with self._lock:
            for session in self._sessions.values():
                _release(session)
            self._sessions.clear()

    @staticmethod
    def _asset_catalog(root, asset, rig):
        """The Asset's own clips, or no entries when it has no catalog for this rig."""
        directory = paths.safe_asset_dir(root, asset)
        animation_dir = paths.safe_asset_dir(directory, "Animation") if directory else None
        manifest = paths.safe_asset_path(animation_dir, "manifest.json") if animation_dir else None
        if not manifest:
            return None, {}, None
        raw = _json(manifest)
        if (not isinstance(raw, dict) or raw.get("format") != "mod-viewer-animations" or type(raw.get("version")) is not int or raw["version"] != 1
                or raw.get("skeleton_id") != rig["id"] or not isinstance(raw.get("clips"), list)
                or len(raw["clips"]) > 1024):
            return None, {}, None
        entries = {}
        for entry in raw["clips"]:
            if not isinstance(entry, dict):
                raise ValueError("Invalid animation catalog.")
            key = entry.get("id")
            name, file, duration = entry.get("name"), entry.get("file"), entry.get("duration")
            if (not isinstance(key, str) or not _ID.fullmatch(key) or key in entries
                    or not isinstance(name, str) or not 1 <= len(name) <= 128
                    or type(duration) not in (int, float) or not math.isfinite(duration) or not 0 < duration <= 3600
                    or not isinstance(file, str) or not paths.safe_asset_path(animation_dir, file)):
                raise ValueError("Invalid animation catalog entry.")
            entries[key] = dict(entry)
        return animation_dir, entries, raw

    def catalog(self, root, asset, rig, is_current):
        """Issue opaque clip IDs only after the caller proved its loaded rig."""
        empty = {"animations": [], "animation_session": None, "animation_sequences": []}
        try:
            animation_dir, entries, raw = self._asset_catalog(root, asset, rig)
            sequences = _sequences(raw.get("sequences"), entries) if raw else []
            runtime = animation_keys.bone_list(raw.get("runtime_bones"), len(rig["bones"])) if raw else None
            layers = _shared_layers(raw, len(rig["bones"])) if raw else {}
            layer_bones = (animation_keys.bone_list(raw.get("layer_bones"), len(rig["bones"])) or []) if raw else []
            props = _props(raw) if raw else {}
            # Game-wide libraries at the Asset Folder root follow the Asset's own clips.
            for library in shared_animations.discover(root, rig):
                targets = set(library["mapping"].values())
                # Shared clips drive the Asset's runtime and layer-authored chains with physics where no
                # baked layer covers them; mapped body joints always follow the clip.
                library["secondary_pool"] = sorted((set(runtime or ()) | set(layer_bones)) - targets)
                for clip in library["clips"]:
                    key = shared_animations.catalog_id(library["name"], clip["id"])
                    if key in entries or len(entries) >= 4096:
                        continue
                    entry = dict(id=key, name=f"{library['label']} / {clip['name']}"[:128], file=clip["file"],
                                 duration=clip["duration"], shared=library)
                    layer = layers.get((library["name"], clip["id"]))
                    # A layer baked against an older library build, or one overlapping the body, never applies.
                    if (layer is not None and layer["fingerprint"] == library["fingerprint"]
                            and not targets.intersection(layer["bone_ids"])):
                        entry["layer"] = layer
                    entries[key] = entry
            if not entries or not is_current():
                return empty
            token = uuid.uuid4().hex
            with self._lock:
                if len(self._sessions) >= 32:
                    _release(self._sessions.pop(next(iter(self._sessions))))
                self._sessions[token] = dict(directory=animation_dir, entries=entries, rig=rig,
                                              is_current=is_current, urls=set(), runtime=runtime,
                                              props=props, prop_urls=[], prop_textures=None)
            return {"animation_session": token,
                    "animations": [{"id": e["id"], "name": e["name"], "duration": e["duration"],
                                    **({"shared": True} if e.get("shared") else {})}
                                   for e in entries.values()],
                    "animation_sequences": sequences,
                    "animation_props": [{"id": prop["id"], "name": prop["name"]} for prop in props.values()]}
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            return empty

    def load_props(self, token):
        """Publish the session's props: fresh single-use geometry (and animated props'
        track) URLs on each call, and textures through one session texture publication
        released with the session. An animated prop without a track for any of the
        session's clips is left out. A rig prop with clip windows is left out when none of
        the session's clips shows it."""
        try:
            with self._lock:
                session = self._sessions.get(token) if isinstance(token, str) else None
                if not session or not session["is_current"]():
                    raise ValueError("The animation preview is no longer active.")
                props = list(session["props"].values())
                clips = set(session["entries"])
            loaded = []
            for prop in props:
                tracks = _read_prop_tracks(session["directory"], prop, clips) if "tracks" in prop else None
                if tracks is not None and not tracks[1]:
                    continue
                windows = None
                if "visible" in prop:
                    windows = {clip: value for clip, value in prop["visible"].items() if clip in clips}
                    if not windows:
                        continue
                loaded.append((prop, _read_prop(session["directory"], prop, len(session["rig"]["bones"])),
                               _prop_textures(session["directory"], prop), tracks, windows))
            with self._lock:
                if self._sessions.get(token) is not session or not session["is_current"]():
                    raise ValueError("The animation preview is no longer active.")
                if session["prop_textures"] is None and any(textures for _prop, _data, textures, _tracks, _windows in loaded):
                    publication = server.begin_texture_publication()
                    publication.commit(replace=False)
                    session["prop_textures"] = publication
                for url in session["prop_urls"]:
                    server.release_geometry(url)
                session["prop_urls"] = []
                result = []
                for prop, data, textures, tracks, windows in loaded:
                    url = server.publish_geometry(data, replace=False)
                    session["prop_urls"].append(url)
                    urls = {role: session["prop_textures"].register(path, role) for role, path in textures.items()}
                    item = {"id": prop["id"], "name": prop["name"], "layout": PROP_LAYOUT,
                            "vertex_count": prop["vertex_count"], "index_count": prop["index_count"],
                            "data": {"url": url, "length": len(data)},
                            "textures": {role: url for role, url in urls.items() if url}}
                    if windows is not None:
                        item["visible"] = windows
                    if tracks is not None:
                        track_url = server.publish_geometry(tracks[0], replace=False)
                        session["prop_urls"].append(track_url)
                        item.update(socket=prop["socket"], bone_count=prop["bone_count"], tracks={
                            "layout": prop["track_layout"], "url": track_url, "length": len(tracks[0]), "clips": tracks[1]})
                    result.append(item)
            return {"status": "ok", "props": result}
        except (OSError, ValueError, TypeError, KeyError) as error:
            return {"status": "error", "error": str(error)}

    def load(self, token, clip_id):
        try:
            with self._lock:
                session = self._sessions.get(token) if isinstance(token, str) else None
                if not session or not session["is_current"]():
                    raise ValueError("The animation preview is no longer active.")
                entry = session["entries"].get(clip_id) if isinstance(clip_id, str) else None
                if not entry:
                    raise ValueError("Choose an animation from the current Asset.")
            shared = entry.get("shared")
            if shared:
                raw, data, morph_data = _read_shared_clip(shared, entry, session["rig"], session["directory"])
            else:
                raw, data, morph_data = _read_clip(session["directory"], entry, session["rig"], full_validation=False)
            idle = _idle_face(session) if morph_data is None else None
            with self._lock:
                if self._sessions.get(token) is not session or not session["is_current"]():
                    raise ValueError("The animation preview is no longer active.")
                published = []
                try:
                    for payload in (data, morph_data, idle[1] if idle else None):
                        published.append(server.publish_geometry(payload, replace=False) if payload is not None else None)
                except BaseException:
                    for url in published:
                        if url:
                            server.release_geometry(url)
                    raise
                url, morph_url, idle_url = published
                for previous in session["urls"]:
                    server.release_geometry(previous)
                session["urls"] = {item for item in published if item}
            clip = {"id": clip_id, "name": entry["name"], "skeleton_id": raw["skeleton_id"],
                    "version": raw["version"], "layout": raw["layout"],
                    "frame_count": raw["frame_count"], "bone_count": raw["bone_count"], "duration": raw["duration"],
                    "data": {"url": url, "length": len(data)}}
            # Bones the game leaves to runtime physics that this clip does not key.
            keyed = animation_keys.bone_list(raw.get("keyed_bones"), raw["bone_count"])
            pool = shared["secondary_pool"] if shared else session["runtime"]
            if pool and keyed is not None:
                secondary = sorted(set(pool) - set(keyed))
                if secondary:
                    clip["secondary_bones"] = secondary
            if raw.get("root_motion") is not None:
                clip["root_motion"] = {"bone": raw["root_motion"]["bone"]}
            if morph_data is not None:
                clip["morphs"] = {"names": raw["morphs"]["names"], "components": raw["morphs"]["components"],
                                  "data": {"url": morph_url, "length": len(morph_data)}}
            elif idle:
                clip["idle_morphs"] = {**idle[0], "data": {"url": idle_url, "length": len(idle[1])}}
            return {"status": "ok", "clip": clip}
        except (OSError, ValueError, TypeError, KeyError, RecursionError) as error:
            return {"status": "error", "error": str(error)}

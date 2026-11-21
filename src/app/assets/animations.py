"""Lazy, contained animation libraries attached to verified Asset skeletons."""

import hashlib
import json
import math
import re
import uuid
from threading import RLock

import numpy as np

from app.runtime import server
from . import animation_keys, paths
from .animation_morphs import bounded, idle_blink, morph_names, read_morphs


_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_MAX_BYTES = 128 * 1024 * 1024


_SEQUENCE_SLACK = 1e-6


def _sequence_number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("Invalid animation sequence number.")
    return float(value)


def _sequences(raw_sequences, entries):
    """Return cleaned sequences, or [] when the key is absent or any sequence is invalid."""
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
            sequences.append({"id": key, "name": name, "cyclic": cyclic, "restart": restart, "steps": cleaned})
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
                for url in session["urls"]:
                    server.release_geometry(url)
            self._sessions.clear()

    def catalog(self, root, asset, rig, is_current):
        """Issue opaque clip IDs only after the caller proved its loaded rig."""
        empty = {"animations": [], "animation_session": None, "animation_sequences": []}
        try:
            directory = paths.safe_asset_dir(root, asset)
            animation_dir = paths.safe_asset_dir(directory, "Animation") if directory else None
            raw = _json(paths.safe_asset_path(animation_dir, "manifest.json") if animation_dir else None)
            if (not isinstance(raw, dict) or raw.get("format") != "mod-viewer-animations" or type(raw.get("version")) is not int or raw["version"] != 1
                    or raw.get("skeleton_id") != rig["id"] or not isinstance(raw.get("clips"), list)
                    or len(raw["clips"]) > 1024):
                return empty
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
            if not entries or not is_current():
                return empty
            sequences = _sequences(raw.get("sequences"), entries)
            runtime = animation_keys.bone_list(raw.get("runtime_bones"), len(rig["bones"]))
            token = uuid.uuid4().hex
            with self._lock:
                if len(self._sessions) >= 32:
                    old = self._sessions.pop(next(iter(self._sessions)))
                    for url in old["urls"]:
                        server.release_geometry(url)
                self._sessions[token] = dict(directory=animation_dir, entries=entries, rig=rig,
                                              is_current=is_current, urls=set(), runtime=runtime)
            return {"animation_session": token,
                    "animations": [{"id": e["id"], "name": e["name"], "duration": e["duration"]}
                                   for e in entries.values()],
                    "animation_sequences": sequences}
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            return empty

    def load(self, token, clip_id):
        try:
            with self._lock:
                session = self._sessions.get(token) if isinstance(token, str) else None
                if not session or not session["is_current"]():
                    raise ValueError("The animation preview is no longer active.")
                entry = session["entries"].get(clip_id) if isinstance(clip_id, str) else None
                if not entry:
                    raise ValueError("Choose an animation from the current Asset.")
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
            if session["runtime"] and keyed is not None:
                secondary = sorted(set(session["runtime"]) - set(keyed))
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

"""Lazy, contained animation libraries attached to verified Asset skeletons."""

import hashlib
import json
import math
import re
import struct
import uuid
from threading import RLock

from app.runtime import server
from . import paths


_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_MAX_BYTES = 128 * 1024 * 1024


def _json(file, limit=1024 * 1024):
    if not file:
        raise ValueError("Animation metadata is unavailable.")
    with open(file, "rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Animation metadata is too large.")
    return json.loads(data)


def read_clip(directory, entry, rig):
    """Validate one contained clip without publishing it."""
    raw = _json(paths.safe_asset_path(directory, entry["file"]))
    if not isinstance(raw, dict):
        raise ValueError("Invalid animation metadata.")
    frames, bones, duration = raw.get("frame_count"), raw.get("bone_count"), raw.get("duration")
    if (raw.get("format") != "mod-viewer-animation" or type(raw.get("version")) is not int or raw["version"] != 1
            or raw.get("coordinate_space") != "asset" or raw.get("layout") != "frame-major-tq-f32le"
            or raw.get("skeleton_id") != rig["id"]
            or type(frames) is not int or not 2 <= frames <= 60000
            or type(bones) is not int or bones != len(rig["bones"])
            or duration != entry["duration"] or type(duration) not in (int, float)
            or not math.isfinite(duration) or not 0 < duration <= 3600):
        raise ValueError("Animation does not match the loaded rig.")
    size = frames * bones * 28
    if size > _MAX_BYTES:
        raise ValueError("Animation data is too large.")
    file = raw.get("data_file")
    path = paths.safe_asset_path(directory, file) if isinstance(file, str) else None
    if not path:
        raise ValueError("Animation data is unavailable.")
    with open(path, "rb") as stream:
        data = stream.read(size + 1)
    if len(data) != size or hashlib.sha256(data).hexdigest() != raw.get("sha256"):
        raise ValueError("Animation data is incomplete or has changed.")
    for row in struct.iter_unpack("<7f", data):
        if (not all(math.isfinite(v) and abs(v) <= 1e6 for v in row)
                or abs(sum(v * v for v in row[3:]) - 1) > 0.001):
            raise ValueError("Animation contains invalid transforms.")
    return raw, data


class AnimationSessions:
    def __init__(self):
        self._lock = RLock()
        self._sessions = {}

    def clear(self):
        with self._lock:
            for session in self._sessions.values():
                for url in session["urls"]:
                    server.release_geometry(url)
            self._sessions.clear()

    def catalog(self, root, asset, rig, is_current):
        """Issue opaque clip IDs only after the caller proved its loaded rig."""
        empty = {"animations": [], "animation_session": None}
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
            token = uuid.uuid4().hex
            with self._lock:
                if len(self._sessions) >= 32:
                    old = self._sessions.pop(next(iter(self._sessions)))
                    for url in old["urls"]:
                        server.release_geometry(url)
                self._sessions[token] = dict(directory=animation_dir, entries=entries, rig=rig,
                                              is_current=is_current, urls=set())
            return {"animation_session": token,
                    "animations": [{"id": e["id"], "name": e["name"], "duration": e["duration"]}
                                   for e in entries.values()]}
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
            raw, data = read_clip(session["directory"], entry, session["rig"])
            with self._lock:
                if self._sessions.get(token) is not session or not session["is_current"]():
                    raise ValueError("The animation preview is no longer active.")
                url = server.publish_geometry(data, replace=False)
                for previous in session["urls"]:
                    server.release_geometry(previous)
                session["urls"] = {url}
            return {"status": "ok", "clip": {"id": clip_id, "name": entry["name"], "skeleton_id": raw["skeleton_id"],
                    "frame_count": raw["frame_count"], "bone_count": raw["bone_count"], "duration": raw["duration"],
                    "data": {"url": url, "length": len(data)}}}
        except (OSError, ValueError, TypeError, KeyError, RecursionError) as error:
            return {"status": "error", "error": str(error)}

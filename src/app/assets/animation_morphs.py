"""Validate optional, contained sparse morph targets for skeletal clips."""

import hashlib
import json
import math
import os
import re
import struct

from . import paths


def _file_identity(file):
    stat = os.stat(file)
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def morph_names(raw):
    if raw is None:
        return []
    names = raw.get("names") if isinstance(raw, dict) else None
    if (not isinstance(names, list) or not 1 <= len(names) <= 256
            or any(not isinstance(name, str) or not 1 <= len(name) <= 256 for name in names)
            or len(set(names)) != len(names)):
        raise ValueError("Invalid animation morph names.")
    return names


def _glb_targets(data, raw):
    """Read only the embedded runtime view; never resolve GLB URI resources."""
    if len(data) < 28 or struct.unpack_from("<3I", data) != (0x46546C67, 2, len(data)):
        raise ValueError("Invalid facial GLB header.")
    length, kind = struct.unpack_from("<2I", data, 12)
    if kind != 0x4E4F534A or length % 4 or not 0 < length <= 4 * 1024 * 1024 or length + 28 > len(data):
        raise ValueError("Invalid facial GLB metadata.")
    document = json.loads(data[20:20 + length])
    size, kind = struct.unpack_from("<2I", data, 20 + length)
    start = 28 + length
    if kind != 0x004E4942 or size % 4 or size != len(data) - start or not isinstance(document, dict):
        raise ValueError("Invalid facial GLB buffer.")
    try:
        runtime = document["extras"]["mod_viewer_morphs"]
        if (runtime["version"] != 1 or runtime["components"] != raw["components"]
                or runtime["names"] != raw["names"] or type(runtime["buffer_view"]) is not int
                or not 0 <= runtime["buffer_view"] < len(document["bufferViews"])):
            raise ValueError("Facial GLB does not match its clip.")
        buffers = document["buffers"]
        if (len(buffers) != 1 or set(buffers[0]) != {"byteLength"}
                or type(buffers[0]["byteLength"]) is not int or not size - 3 <= buffers[0]["byteLength"] <= size):
            raise ValueError("Invalid facial GLB buffer.")
        view = document["bufferViews"][runtime["buffer_view"]]
        offset, count = view["byteOffset"], view["byteLength"]
        if (view["buffer"] != 0 or type(offset) is not int or type(count) is not int
                or offset < 0 or offset % 4 or count < 1 or offset + count > buffers[0]["byteLength"]):
            raise ValueError("Invalid facial GLB runtime range.")
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError("Invalid facial GLB runtime view.") from error
    return data[start + offset:start + offset + count]


def read_morphs(directory, raw, *, validated=None):
    """Read targets, or reuse a validation marker during one extraction operation.

    A cache hit returns no bytes; publication callers must use the default fresh read.
    """
    morph_names(raw)
    file = paths.safe_asset_path(directory, raw.get("file")) if isinstance(raw.get("file"), str) else None
    if not file:
        raise ValueError("Animation morph data is unavailable.")
    key = None
    if validated is not None:
        descriptor = json.dumps([raw.get("sha256"), raw.get("components"), raw.get("format"), raw.get("names")], sort_keys=True,
                                separators=(",", ":"), allow_nan=False).encode()
        key = (file, _file_identity(file), hashlib.sha256(descriptor).digest())
        if key in validated:
            return None
    with open(file, "rb") as stream:
        data = stream.read(128 * 1024 * 1024 + 1)
    if len(data) > 128 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != raw.get("sha256"):
        raise ValueError("Animation morph data is incomplete or has changed.")
    if raw.get("format") == "glb":
        data = _glb_targets(data, raw)
    elif raw.get("format") is not None:
        raise ValueError("Unsupported animation morph format.")
    components = raw.get("components")
    if not isinstance(components, list) or not 1 <= len(components) <= 4096:
        raise ValueError("Invalid animation morph components.")
    cursor, identities = 0, set()
    for component in components:
        if not isinstance(component, dict):
            raise ValueError("Invalid animation morph component.")
        identity = tuple(component.get(key) for key in ("vertex_sha256", "index_sha256"))
        if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in identity):
            raise ValueError("Invalid morph geometry fingerprint.")
        count, targets = component.get("vertex_count"), component.get("targets")
        if (identity in identities or type(count) is not int or not 1 <= count <= 1000000
                or not isinstance(targets, list) or not 1 <= len(targets) <= 256):
            raise ValueError("Invalid morph geometry.")
        identities.add(identity)
        seen = set()
        for target in targets:
            if not isinstance(target, dict):
                raise ValueError("Invalid morph target.")
            name, offset, length = target.get("name"), target.get("offset"), target.get("count")
            if (not isinstance(name, str) or not 1 <= len(name) <= 256 or name in seen
                    or type(offset) is not int or offset != cursor
                    or type(length) is not int or not 1 <= length <= count or cursor + length * 28 > len(data)):
                raise ValueError("Invalid morph target range.")
            seen.add(name)
            vertices = set()
            for row in struct.iter_unpack("<I6f", memoryview(data)[cursor:cursor + length * 28]):
                if (row[0] >= count or row[0] in vertices
                        or any(not math.isfinite(value) or abs(value) > 1e6 for value in row[1:])):
                    raise ValueError("Invalid morph vertex delta.")
                vertices.add(row[0])
            cursor += length * 28
    if cursor != len(data):
        raise ValueError("Invalid morph data length.")
    if key is not None and _file_identity(file) == key[1] and len(validated) < 1024:
        validated.add(key)
    return data

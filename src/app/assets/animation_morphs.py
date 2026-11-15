"""Validate optional, contained sparse morph targets for skeletal clips."""

import hashlib
import math
import re
import struct

from . import paths


def morph_names(raw):
    if raw is None:
        return []
    names = raw.get("names") if isinstance(raw, dict) else None
    if (not isinstance(names, list) or not 1 <= len(names) <= 256
            or any(not isinstance(name, str) or not 1 <= len(name) <= 256 for name in names)
            or len(set(names)) != len(names)):
        raise ValueError("Invalid animation morph names.")
    return names


def read_morphs(directory, raw):
    morph_names(raw)
    file = paths.safe_asset_path(directory, raw.get("file")) if isinstance(raw.get("file"), str) else None
    if not file:
        raise ValueError("Animation morph data is unavailable.")
    with open(file, "rb") as stream:
        data = stream.read(128 * 1024 * 1024 + 1)
    if len(data) > 128 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != raw.get("sha256"):
        raise ValueError("Animation morph data is incomplete or has changed.")
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
    return data

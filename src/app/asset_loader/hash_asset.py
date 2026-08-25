"""Shared GIMI/ZZMI hash.json and 3DMigoto text-dump loader."""

import json
import os
import re

from core.geometry_identity import normalize_geometry_hash
from core.migoto_dump import (MigotoDumpError, pack_indices,
                               parse_index_dump, parse_vertex_dump)
from core.textures import normalize_texture_role

from .. import asset_paths
from .models import AssetLoadError, AssetMeshPart, make_texture


_ROLE_NAMES = {
    "diffuse": "diffuse", "normalmap": "normal_map",
    "normal_map": "normal_map", "lightmap": "light_map",
    "light_map": "light_map", "materialmap": "material_map",
    "material_map": "material_map",
}
_DUMP_RE = re.compile(
    r"(?:^|[-_])(?P<kind>vb\d*|ib)=(?P<hash>[0-9a-f]+)", re.I)


def _entries(value):
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("objects", "components", "entries", "records"):
            candidate = value.get(key)
            if isinstance(candidate, list):
                return candidate
    return []


def _values(entry, keys):
    for key in keys:
        value = entry.get(key)
        if isinstance(value, list):
            return value
        if value is not None:
            return [value]
    return []


def _string(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def _integer(value, minimum=0):
    if isinstance(value, bool):
        return None
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if value >= minimum else None


def _file_list(root):
    result = []

    def visit(folder, depth):
        if depth > 2:
            return
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    try:
                        if entry.is_file(follow_symlinks=False):
                            result.append(os.path.abspath(entry.path))
                        elif entry.is_dir(follow_symlinks=False):
                            visit(entry.path, depth + 1)
                    except OSError:
                        continue
        except OSError:
            return

    visit(root, 0)
    return sorted(result, key=lambda value: (value.casefold(), value))


def _dump_hash(path):
    match = _DUMP_RE.search(os.path.splitext(os.path.basename(path))[0])
    return (match.group("kind").casefold(),
            normalize_geometry_hash(match.group("hash"))) if match else (None, None)


def _find_dump(files, kind, hash_value=None, label=None):
    kind = kind.casefold()
    hash_value = normalize_geometry_hash(hash_value)
    matching = []
    for path in files:
        if not path.casefold().endswith(".txt"):
            continue
        file_kind, file_hash = _dump_hash(path)
        if file_kind and file_kind.startswith("vb") and kind == "vb":
            file_kind = "vb"
        if file_kind != kind:
            continue
        if hash_value and file_hash == hash_value:
            matching.append(path)
    if matching:
        return matching[0]
    lowered = (label or "").casefold()
    for path in files:
        name = os.path.basename(path).casefold()
        if not name.endswith(".txt") or f"-{kind}" not in name:
            continue
        if not lowered or lowered in name:
            return path
    return None


def _entry_hash(entry, keys):
    for key in keys:
        value = entry.get(key)
        if isinstance(value, list):
            value = value[0] if value else None
        normalized = normalize_geometry_hash(value)
        if normalized:
            return normalized
    return None


def _texture_file(files, texture_hash, extension, component, classification, role):
    texture_hash = normalize_geometry_hash(texture_hash)
    extension = str(extension or "").casefold()
    candidates = []
    for path in files:
        name = os.path.basename(path).casefold()
        if name.endswith(".txt") or name.endswith(".json"):
            continue
        if extension and not name.endswith(extension):
            continue
        if texture_hash and texture_hash in name:
            candidates.append(path)
    if len(candidates) == 1:
        return candidates[0]
    suffixes = {
        "diffuse": "Diffuse", "normal_map": "NormalMap",
        "light_map": "LightMap", "material_map": "MaterialMap",
    }
    stem = f"{component or ''}{classification or ''}{suffixes.get(role, '')}".casefold()
    candidates = []
    for path in files:
        name = os.path.basename(path).casefold()
        if (not name.endswith((".dds", ".png", ".jpg", ".jpeg", ".tga"))
                or (extension and not name.endswith(extension))):
            continue
        if stem and os.path.splitext(name)[0].endswith(stem):
            candidates.append(path)
    return candidates[0] if len(candidates) == 1 else None


def _texture_records(entry, position, files, root, component, classification,
                     texture_source):
    textures = entry.get("texture_hashes")
    if not isinstance(textures, list) or position >= len(textures):
        textures = entry.get("textureHashes")
    if not isinstance(textures, list) or position >= len(textures):
        return {}
    result = {}
    for item in textures[position] or []:
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            continue
        role = _ROLE_NAMES.get(str(item[0]).replace(" ", "").casefold())
        if not role:
            continue
        filename = _texture_file(
            files, item[2], item[1], component, classification, role)
        if not filename:
            continue
        texture = make_texture(
            root, filename, normalize_texture_role(role),
            texture_source=texture_source)
        if texture:
            result[role] = texture
    return result


def _ranges(entry):
    first_values = _values(entry, (
        "object_indexes", "objectIndexes", "first_indices", "firstIndices",
        "first_index", "firstIndex")) or [0]
    counts = _values(entry, (
        "object_index_counts", "objectIndexCounts", "index_counts",
        "indexCounts", "index_count", "indexCount"))
    classifications = _values(entry, (
        "object_classifications", "objectClassifications", "classifications"))
    result = []
    for ordinal, raw_first in enumerate(first_values):
        first = _integer(raw_first)
        if first is None:
            continue
        count = _integer(counts[ordinal]) if ordinal < len(counts) else None
        if ordinal < len(counts) and counts[ordinal] is not None and count is None:
            continue
        classification = (_string(classifications[ordinal])
                          if ordinal < len(classifications) else None)
        result.append((ordinal, first, count, classification))
    return result or [(0, 0, None, None)]


def _remap(vertex_dump, indices):
    positions = vertex_dump.positions
    normals = vertex_dump.normals
    uvs = vertex_dump.uvs
    vertex_count = vertex_dump.layout.vertex_count
    unique = []
    mapping = {}
    valid_indices = []
    for offset in range(0, len(indices), 3):
        triangle = indices[offset:offset + 3]
        if len(triangle) != 3 or any(item >= vertex_count for item in triangle):
            continue
        for item in triangle:
            if item not in mapping:
                mapping[item] = len(unique)
                unique.append(item)
        valid_indices.extend(mapping[item] for item in triangle)
    if not valid_indices:
        raise AssetLoadError("A render part has no valid indexed triangles.")

    def select(data, width):
        if data is None:
            return None
        result = bytearray(len(unique) * width * 4)
        for new, old in enumerate(unique):
            begin = old * width * 4
            end = begin + width * 4
            if end > len(data):
                return None
            result[new * width * 4:(new + 1) * width * 4] = data[begin:end]
        return bytes(result)

    return (select(positions, 3), select(normals, 3), select(uvs, 2),
            pack_indices(valid_indices))


def load_hash_asset(asset_type, root, record, *, texture_source=None):
    asset_path = record.get("path") if isinstance(record, dict) else None
    metadata_path = None
    geometries = record.get("geometry", ()) if isinstance(record, dict) else ()
    if geometries:
        metadata_path = geometries[0].get("metadata")
    asset_dir = asset_paths.safe_asset_dir(root, asset_path)
    metadata_file = asset_paths.safe_asset_path(root, metadata_path)
    if not asset_dir or not metadata_file:
        raise AssetLoadError("The indexed hash.json is missing from this Asset.")
    try:
        with open(metadata_file, encoding="utf-8") as stream:
            raw = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise AssetLoadError(f"hash.json could not be parsed: {error}") from error
    files = _file_list(asset_dir)
    vb_cache = {}
    ib_cache = {}
    parts = []
    entries = _entries(raw)
    if not entries:
        raise AssetLoadError("hash.json contains no renderable component records.")
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        geometry_hash = _entry_hash(
            entry, ("ib", "ib_hash", "geometry_hash", "geometryHash"))
        if not geometry_hash:
            continue
        component = _string(entry.get("component_name") or entry.get("componentName"))
        vb_hash = _entry_hash(entry, (
            "vb0", "vb0_hash", "vertex_buffer", "vertexBuffer",
            "position_vb", "positionVB", "draw_vb", "drawVB"))
        vb_file = _find_dump(files, "vb", vb_hash, component)
        ib_file = _find_dump(files, "ib", geometry_hash, component)
        if not vb_file:
            raise AssetLoadError(f"{component or geometry_hash} vertex dump is missing.")
        if not ib_file:
            raise AssetLoadError(f"{component or geometry_hash} index dump is missing.")
        try:
            if vb_file not in vb_cache:
                vb_cache[vb_file] = parse_vertex_dump(vb_file)
            vertex_dump = vb_cache[vb_file]
            if ib_file not in ib_cache:
                ib_cache[ib_file] = parse_index_dump(
                    ib_file, vertex_count=vertex_dump.layout.vertex_count)
            all_indices = ib_cache[ib_file]
        except MigotoDumpError as error:
            raise AssetLoadError(f"{component or geometry_hash}: {error}") from error
        for ordinal, first, count, classification in _ranges(entry):
            end = len(all_indices) if count is None else first + count
            if first >= len(all_indices):
                continue
            selected = all_indices[first:min(end, len(all_indices))]
            if len(selected) < 3:
                continue
            positions, normals, uvs, indices = _remap(vertex_dump, selected)
            textures = _texture_records(
                entry, ordinal, files, root, component, classification,
                texture_source)
            label = component or os.path.basename(asset_path)
            if classification:
                label = f"{label} {classification}"
            if len(_ranges(entry)) > 1:
                label = f"{label} {ordinal + 1}"
            key = f"{asset_path}::{component or 'part'}::{geometry_hash}::{ordinal}"
            parts.append(AssetMeshPart(
                key=key, label=label, asset_type=asset_type,
                asset_path=asset_path, geometry_hash=geometry_hash,
                component_name=component, classification=classification,
                component_ordinal=ordinal, first_index=first,
                index_count=count, positions=positions, indices=indices,
                uvs=uvs, normals=normals, textures=textures))
    if not parts:
        raise AssetLoadError("Asset contains no complete renderable parts.")
    return tuple(parts)


__all__ = ["load_hash_asset"]

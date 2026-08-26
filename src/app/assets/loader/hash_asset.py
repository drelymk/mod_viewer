"""Shared GIMI/ZZMI hash.json and 3DMigoto text-dump loader."""

import json
import os
import re
from dataclasses import dataclass

from core.geometry.identity import normalize_geometry_hash
from core.geometry.migoto_dump import (MigotoDumpError, pack_indices,
                               parse_index_dump, parse_vertex_dump)
from core.textures import normalize_texture_role

from app.assets import paths as asset_paths
from . import gimi_face_alignment
from .models import (AssetAdapterResult, AssetLoadError, AssetMeshPart,
                     make_texture)


_ROLE_NAMES = {
    "diffuse": "diffuse", "normalmap": "normal_map",
    "normal_map": "normal_map", "lightmap": "light_map",
    "light_map": "light_map", "materialmap": "material_map",
    "material_map": "material_map",
}
_DUMP_RE = re.compile(
    r"(?:^|[-_])(?P<kind>vb\d*|ib)=(?P<hash>[0-9a-f]+)", re.I)
_TEXTURE_SUFFIXES = (
    ("diffuse", "diffuse"),
    ("normal_map", "normalmap"),
    ("light_map", "lightmap"),
    ("material_map", "materialmap"),
)


@dataclass(frozen=True, slots=True)
class _HashAssetRecord:
    metadata_path: str
    entry: dict
    component_name: str | None
    geometry_hash: str
    vb_hash: str | None
    ranges: tuple
    vb_file: str | None
    ib_files: tuple[str, ...]


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


def _dump_prefix(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    match = _DUMP_RE.search(stem)
    return stem[:match.start()] if match else None


def _find_dumps(files, kind, hash_value=None, label=None):
    kind = kind.casefold()
    hash_value = normalize_geometry_hash(hash_value)
    if not hash_value:
        return []
    matching = []
    for path in files:
        if not path.casefold().endswith(".txt"):
            continue
        file_kind, file_hash = _dump_hash(path)
        if file_kind and file_kind.startswith("vb") and kind == "vb":
            file_kind = "vb"
        if file_kind != kind:
            continue
        if file_hash == hash_value:
            matching.append(path)
    return matching


def _find_dump(files, kind, hash_value=None, label=None):
    matches = _find_dumps(files, kind, hash_value, label)
    return matches[0] if matches else None


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
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        return None
    # Some GIMI exports omit the texture hash from filenames.  When a
    # component name is empty, a more specific component (for example
    # FaceHeadDiffuse) can also match the generic HeadDiffuse suffix.  Prefer
    # the shortest unique suffix match; equal-length candidates remain
    # ambiguous and are intentionally left unresolved.
    stem_lengths = [len(os.path.splitext(os.path.basename(path))[0])
                    for path in candidates]
    shortest = min(stem_lengths)
    preferred = [path for path, length in zip(candidates, stem_lengths)
                 if length == shortest]
    return preferred[0] if len(preferred) == 1 else None


def _range_texture_records(files, ib_candidates, first, count, vertex_count,
                           ib_cache, root, texture_source):
    families = {}
    for path in ib_candidates:
        dump, _error = _cached_index_dump(
            path, vertex_count, ib_cache)
        if dump is None:
            continue
        if (dump.first_index != first
                or (count is not None and dump.index_count != count)):
            continue
        prefix = _dump_prefix(path)
        if not prefix:
            continue
        prefix_folded = prefix.casefold()
        family = {}
        for candidate in files:
            extension = os.path.splitext(candidate)[1].casefold()
            if extension not in {".dds", ".png", ".jpg", ".jpeg", ".tga"}:
                continue
            texture_stem = os.path.splitext(
                os.path.basename(candidate))[0].casefold()
            if not texture_stem.startswith(prefix_folded):
                continue
            suffix = texture_stem[len(prefix_folded):]
            for role, expected in _TEXTURE_SUFFIXES:
                if suffix == expected:
                    family.setdefault(role, []).append(candidate)
                    break
        if family:
            families[prefix] = family
    if len(families) != 1:
        return {}
    family = next(iter(families.values()))
    result = {}
    for role, candidates in family.items():
        if len(candidates) != 1:
            continue
        texture = make_texture(
            root, candidates[0], role, texture_source=texture_source,
            source="asset_geometry_family")
        if texture:
            result[role] = texture
    return result


def _texture_records(entry, position, files, root, component, classification,
                     texture_source, *, ib_candidates=(), first=0, count=None,
                     vertex_count=0, ib_cache=None):
    textures = entry.get("texture_hashes")
    if not isinstance(textures, list) or position >= len(textures):
        textures = entry.get("textureHashes")
    items = (textures[position] if isinstance(textures, list)
             and position < len(textures) else ()) or ()
    result = {}
    for item in items:
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
    if not items and ib_candidates and ib_cache is not None:
        result.update(_range_texture_records(
            files, ib_candidates, first, count, vertex_count, ib_cache,
            root, texture_source))
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


def _warning(component, classification, reason, message):
    return {"component": component, "classification": classification,
            "reason": reason, "message": message}


def _resolve_ib_dump(candidates, first, count, vertex_count, cache):
    """Choose a range-local IB by its parsed header, not only its hash."""
    parsed = []
    invalid = False
    for path in candidates:
        dump, error = _cached_index_dump(path, vertex_count, cache)
        if dump is None:
            invalid = invalid or error is not None
            continue
        parsed.append((path, dump))
    matching = [item for item in parsed if item[1].first_index == first
                and (count is None or item[1].index_count == count)]
    return (matching[0] if matching else None, invalid, bool(parsed))


def _cached_index_dump(path, vertex_count, cache):
    if path not in cache:
        try:
            cache[path] = (
                parse_index_dump(path, vertex_count=vertex_count), None)
        except MigotoDumpError as error:
            cache[path] = (None, error)
    return cache[path]


def _filter_matches(part_filter, geometry_hash, first, count, ordinal=None):
    if part_filter is None:
        return True
    for item in part_filter:
        if getattr(item, "geometry_hash", None) != geometry_hash:
            continue
        expected_first = getattr(item, "first_index", None)
        expected_count = getattr(item, "index_count", None)
        expected_ordinal = getattr(item, "component_ordinal", None)
        if expected_first is not None and expected_first != first:
            continue
        if (expected_count is not None and count is not None
                and expected_count != count):
            continue
        if (expected_ordinal is not None and ordinal is not None
                and expected_ordinal != ordinal):
            continue
        return True
    return False


def _compact_name(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _record_prefix(record):
    return _compact_name(_dump_prefix(record.vb_file) if record.vb_file else "")


def _record_name(record):
    return _compact_name(
        f"{record.component_name or ''}{_record_prefix(record)}")


def _component_face_kind(value):
    component = _compact_name(value)
    exact = {
        "face": "face", "facemesh": "face", "facehead": "face",
        "eye": "eye", "eyes": "eyes", "mouth": "mouth",
        "nose": "nose", "brow": "brow", "eyebrows": "brow",
    }
    if component in exact:
        return exact[component]
    for prefix, kind in (
        ("faceeyebrow", "brow"), ("facebrow", "brow"),
        ("faceeye", "eye"), ("facemouth", "mouth"),
        ("facenose", "nose"),
    ):
        if component.startswith(prefix):
            return kind
    return None


def _filename_face_kind(record):
    prefix = _record_prefix(record)
    for token, kind in (
        ("faceeyebrow", "brow"), ("facebrow", "brow"),
        ("faceeye", "eye"), ("facemouth", "mouth"),
        ("facenose", "nose"),
    ):
        if token in prefix:
            return kind
    return "face" if "face" in prefix else None


def _is_native_eyes_record(record):
    component = _compact_name(record.component_name)
    if component:
        return component == "eyes"
    return "eyes" in _record_prefix(record)


def _is_face_local_record(record):
    component = _compact_name(record.component_name)
    if component:
        return _component_face_kind(component) in {
            "face", "eye", "mouth", "nose", "brow"
        }
    return _filename_face_kind(record) is not None


def _is_face_anchor_record(record):
    component = _compact_name(record.component_name)
    if component:
        return _component_face_kind(component) in {"face", "eye"}
    return _filename_face_kind(record) in {"face", "eye"}


def _landmark_kind(record):
    component = _compact_name(record.component_name)
    kind = (_component_face_kind(component) if component
            else _filename_face_kind(record))
    return kind if kind in {"brow", "mouth"} else None


def _reference_score(record):
    name = _record_name(record)
    component = _compact_name(record.component_name)
    return (0 if "eyesa" in name else 1,
            0 if component in {"eye", "eyes"} else 1,
            name)


def _anchor_score(record):
    name = _record_name(record)
    component = _compact_name(record.component_name)
    return (0 if "faceeye" in name else 1 if component == "face" else 2,
            name)


def _alignment_vertex(record, vertex_cache):
    if not record.vb_file:
        return None
    if record.vb_file not in vertex_cache:
        try:
            vertex_cache[record.vb_file] = parse_vertex_dump(record.vb_file)
        except MigotoDumpError:
            vertex_cache[record.vb_file] = None
    return vertex_cache[record.vb_file]


def _alignment_mesh(record, vertex_cache, ib_cache, *, with_indices,
                    name_override=None):
    vertex_dump = _alignment_vertex(record, vertex_cache)
    if vertex_dump is None:
        return None
    indices = []
    if with_indices:
        for _ordinal, first, count, _classification in record.ranges:
            resolved, _had_invalid, _had_valid = _resolve_ib_dump(
                record.ib_files, first, count,
                vertex_dump.layout.vertex_count, ib_cache)
            if resolved is not None:
                indices.extend(resolved[1].indices)
        if len(indices) < 3:
            return None
    try:
        positions = gimi_face_alignment.unpack_f32x3(vertex_dump.positions)
    except ValueError:
        return None
    return gimi_face_alignment.AlignmentMesh(
        name_override or record.component_name or "GIMI alignment", positions,
        tuple(indices))


def _solve_face_alignment(records, vertex_cache, ib_cache):
    references = sorted(
        (record for record in records if _is_native_eyes_record(record)),
        key=_reference_score)
    anchors = sorted(
        (record for record in records if _is_face_anchor_record(record)),
        key=_anchor_score)
    if not references or not anchors:
        return None
    landmarks = {"brow": [], "mouth": []}
    for record in records:
        kind = _landmark_kind(record)
        if kind:
            landmarks[kind].append(record)
    for kind in landmarks:
        landmarks[kind].sort(key=_anchor_score)

    for reference in references:
        body_mesh = _alignment_mesh(
            reference, vertex_cache, ib_cache, with_indices=False)
        if body_mesh is None:
            continue
        for anchor in anchors:
            face_mesh = _alignment_mesh(
                anchor, vertex_cache, ib_cache, with_indices=True)
            if face_mesh is None:
                continue
            landmark_mesh = None
            landmark_kind = None
            for kind in ("brow", "mouth"):
                if not landmarks[kind]:
                    continue
                landmark_mesh = _alignment_mesh(
                    landmarks[kind][0], vertex_cache, ib_cache,
                    with_indices=False, name_override=kind)
                if landmark_mesh is not None:
                    landmark_kind = kind
                    break
            alignment = gimi_face_alignment.solve(
                body_mesh, face_mesh, landmark_mesh,
                landmark_kind=landmark_kind)
            if alignment is not None:
                return alignment
    return None


def load_hash_asset(asset_type, root, record, *, texture_source=None,
                    part_filter=None):
    asset_path = record.get("path") if isinstance(record, dict) else None
    geometries = record.get("geometry", ()) if isinstance(record, dict) else ()
    asset_dir = asset_paths.safe_asset_dir(root, asset_path)
    if not asset_dir or not geometries:
        raise AssetLoadError("The indexed hash.json is missing from this Asset.")
    metadata_paths = []
    for geometry in geometries:
        if not isinstance(geometry, dict):
            continue
        candidates = geometry.get("metadataPaths")
        if not isinstance(candidates, list):
            candidates = []
        for metadata_path in [*candidates, geometry.get("metadata")]:
            if metadata_path and metadata_path not in metadata_paths:
                metadata_paths.append(metadata_path)
    if not metadata_paths:
        raise AssetLoadError("The indexed hash.json is missing from this Asset.")
    metadata_paths.sort(key=lambda value: (
        os.path.dirname(value).casefold() != str(asset_path or "").casefold(),
        value.casefold(), value))
    files = _file_list(asset_dir)
    vb_cache = {}
    ib_cache = {}
    warnings = []
    records = []
    for metadata_path in metadata_paths:
        metadata_file = asset_paths.safe_asset_path(root, metadata_path)
        if not metadata_file:
            raise AssetLoadError("The indexed hash.json is missing from this Asset.")
        try:
            with open(metadata_file, encoding="utf-8") as stream:
                raw = json.load(stream)
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            if len(metadata_paths) == 1 or metadata_path == metadata_paths[0]:
                raise AssetLoadError(f"hash.json could not be parsed: {error}") from error
            warnings.append(_warning(
                None, None, "metadata_invalid",
                f"Nested hash.json skipped: {error}"))
            continue
        entries = _entries(raw)
        if not entries:
            if len(metadata_paths) == 1 or metadata_path == metadata_paths[0]:
                raise AssetLoadError("hash.json contains no renderable component records.")
            warnings.append(_warning(
                None, None, "metadata_empty",
                "Nested hash.json contains no renderable component records."))
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            geometry_hash = _entry_hash(
                entry, ("ib", "ib_hash", "geometry_hash", "geometryHash"))
            component = _string(entry.get("component_name") or entry.get("componentName"))
            if not geometry_hash:
                warnings.append(_warning(
                    component, None, "geometry_hash_missing",
                    f"{component or 'Component'} has no usable index-buffer hash."))
                continue
            ranges = _ranges(entry)
            vb_hash = _entry_hash(entry, (
                "vb0", "vb0_hash", "vertex_buffer", "vertexBuffer",
                "position_vb", "positionVB", "draw_vb", "drawVB"))
            records.append(_HashAssetRecord(
                metadata_path=metadata_path, entry=entry,
                component_name=component, geometry_hash=geometry_hash,
                vb_hash=vb_hash, ranges=tuple(ranges),
                vb_file=_find_dump(files, "vb", vb_hash, component),
                ib_files=tuple(_find_dumps(
                    files, "ib", geometry_hash, component))))

    selected_records = []
    for item in records:
        selected_ranges = tuple(
            value for value in item.ranges
            if _filter_matches(part_filter, item.geometry_hash,
                               value[1], value[2], value[0]))
        if selected_ranges:
            selected_records.append((item, selected_ranges))

    face_alignment = None
    face_alignment_warning = False
    if (asset_type == "GIMI"
            and any(_is_face_local_record(item)
                    for item, _ranges_for_output in selected_records)):
        try:
            face_alignment = _solve_face_alignment(
                records, vb_cache, ib_cache)
        except Exception:
            # Alignment is an optional compatibility improvement.  A malformed
            # dependency must not make an otherwise renderable Asset unloadable.
            face_alignment = None
        if face_alignment is None:
            warnings.append(_warning(
                "Face", None, "face_alignment_unavailable",
                "GIMI face geometry could not be aligned to the native Eyes component."))
            face_alignment_warning = True

    parts = []
    for item, selected_ranges in selected_records:
        component = item.component_name
        geometry_hash = item.geometry_hash
        vb_file = item.vb_file
        ib_files = item.ib_files
        if not vb_file:
            warnings.append(_warning(
                component, None, "vertex_dump_missing",
                f"{component or geometry_hash} has no source vertex dump."))
            continue
        if not ib_files:
            warnings.append(_warning(
                component, None, "index_dump_missing",
                f"{component or geometry_hash} has no source index dump."))
            continue
        try:
            if vb_file not in vb_cache or vb_cache[vb_file] is None:
                vb_cache[vb_file] = parse_vertex_dump(vb_file)
            vertex_dump = vb_cache[vb_file]
        except MigotoDumpError as error:
            warnings.append(_warning(
                component, None, "vertex_dump_invalid",
                f"{component or geometry_hash} vertex dump skipped: {error}"))
            continue
        for ordinal, first, count, classification in selected_ranges:
            resolved, had_invalid, had_valid = _resolve_ib_dump(
                ib_files, first, count, vertex_dump.layout.vertex_count,
                ib_cache)
            if resolved is None:
                reason = "index_dump_invalid" if had_invalid and not had_valid \
                    else "index_range_missing"
                message = (f"{component or geometry_hash} index dump skipped"
                           if reason == "index_dump_invalid" else
                           f"{component or geometry_hash} range {first} has "
                           "no matching index dump.")
                warnings.append(_warning(
                    component, classification, reason, message))
                continue
            _ib_file, ib_dump = resolved
            selected = ib_dump.indices
            if len(selected) < 3:
                warnings.append(_warning(
                    component, classification, "index_range_empty",
                    f"{component or geometry_hash} range {first} has no complete triangles."))
                continue
            try:
                positions, normals, uvs, indices = _remap(vertex_dump, selected)
            except (AssetLoadError, MigotoDumpError) as error:
                warnings.append(_warning(
                    component, classification, "part_invalid",
                    f"{component or geometry_hash} part skipped: {error}"))
                continue
            if (face_alignment is not None
                    and _is_face_local_record(item)):
                try:
                    aligned_positions = (
                        gimi_face_alignment.transform_position_bytes(
                            positions, face_alignment.matrix))
                    aligned_normals = (
                        gimi_face_alignment.transform_normal_bytes(
                            normals, face_alignment.matrix))
                    positions, normals = aligned_positions, aligned_normals
                except Exception as error:
                    if not face_alignment_warning:
                        warnings.append(_warning(
                            component, classification,
                            "face_alignment_failed",
                            f"GIMI face alignment was skipped: {error}"))
                        face_alignment_warning = True
            effective_count = (
                count if count is not None
                else ib_dump.index_count or len(ib_dump.indices))
            textures = _texture_records(
                item.entry, ordinal, files, root, component, classification,
                texture_source, ib_candidates=ib_files, first=first,
                count=effective_count,
                vertex_count=vertex_dump.layout.vertex_count,
                ib_cache=ib_cache)
            label = component or os.path.basename(asset_path)
            if classification:
                label = f"{label} {classification}"
            if len(item.ranges) > 1:
                label = f"{label} {ordinal + 1}"
            key = (f"{asset_path}::{item.metadata_path}::"
                   f"{component or 'part'}::{geometry_hash}::{ordinal}")
            parts.append(AssetMeshPart(
                key=key, label=label, asset_type=asset_type,
                asset_path=asset_path, geometry_hash=geometry_hash,
                component_name=component, classification=classification,
                component_ordinal=ordinal, first_index=first,
                index_count=effective_count, positions=positions,
                indices=indices,
                uvs=uvs, normals=normals, textures=textures))
    if not parts:
        raise AssetLoadError("Asset contains no complete renderable parts.")
    return AssetAdapterResult(tuple(parts), tuple(warnings))


__all__ = ["load_hash_asset"]

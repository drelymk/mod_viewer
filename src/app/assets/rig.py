"""Optional authored skeletons attached to exact, registered game Assets."""

import hashlib
import json
import math
import os
import re
import struct

from core.geometry.migoto_format import MigotoBinaryLayout, MigotoElement, parse_fmt
from core.geometry.migoto_dump import parse_vertex_dump, parse_index_dump, pack_indices
from core.geometry.skinning import wwmi_component_domain

from . import paths
from .morph_vertex_match import CELL_SCALE, match_positions
from .metadata import entries as metadata_entries


RIG_FILENAME = "Rig.json"
# Proven native weights of a weightless GIMI capture use their own bone-ID namespace.
ATTACHMENT_NAMESPACE = "gimi_attachment"
_MAX_BYTES = 2 * 1024 * 1024
_MAX_BONES = 4096
_SHA256 = re.compile(r"[0-9a-f]{64}")
_HUMANOID_CHAINS = (
    ("pelvis", "chest", "neck", "head"),
    ("chest", "leftShoulder", "leftElbow", "leftHand"),
    ("chest", "rightShoulder", "rightElbow", "rightHand"),
    ("pelvis", "leftHip", "leftKnee", "leftFoot"),
    ("pelvis", "rightHip", "rightKnee", "rightFoot"),
)


def _humanoid_controls(raw, bones):
    if raw is None:
        return None
    keys = {key for chain in _HUMANOID_CHAINS for key in chain}
    if (not isinstance(raw, dict) or set(raw) != keys
            or any(type(v) is not int or not 0 <= v < len(bones) for v in raw.values())
            or len(set(raw.values())) != len(keys)):
        raise ValueError("Invalid humanoid joint mapping.")
    for chain in _HUMANOID_CHAINS:
        for start, end in zip(chain, chain[1:]):
            parent = bones[raw[end]]["parent"]
            while parent >= 0 and parent != raw[start]:
                parent = bones[parent]["parent"]
            if parent != raw[start]:
                raise ValueError("Humanoid joints must follow the skeleton hierarchy.")
    return dict(raw)


def _integer(value, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError("Invalid rig index.")
    return value


def _vector(value, size):
    if (not isinstance(value, list) or len(value) != size
            or any(type(v) not in (int, float) or abs(v) > 1e6
                   or not math.isfinite(v) for v in value)):
        raise ValueError("Invalid rig transform.")
    return value


def _bone_mapping(value, count, *, sparse=False, maximum=4096, unique=True):
    if sparse and isinstance(value, dict):
        if (not 1 <= len(value) <= maximum or any(not isinstance(key, str) or not key.isascii()
                or not key.isdigit() or str(int(key)) != key or int(key) >= maximum for key in value)):
            raise ValueError("Invalid sparse rig bone mapping.")
        values = list(value.values())
    elif isinstance(value, list) and 1 <= len(value) <= maximum:
        values = value
    else:
        raise ValueError("Invalid rig bone mapping.")
    if any(type(v) is not int or not 0 <= v < count for v in values) or (unique and len(set(values)) != len(values)):
        raise ValueError("Invalid rig bone mapping.")
    return dict(value) if isinstance(value, dict) else list(value)


def _validate_attachment(value, mapping, version):
    """Native weights of a weightless capture: four fully weighted influences per vertex."""
    if (version != 2 or not isinstance(mapping, dict) or not isinstance(value, dict)
            or set(value) != {"weights", "slots", "expression"}):
        raise ValueError("Invalid attachment palette.")
    weights, slots, expression = value["weights"], value["slots"], value["expression"]
    if (not isinstance(weights, list) or not isinstance(slots, list) or len(weights) != len(slots)
            or not 4 <= len(weights) <= 4 * 250000 or len(weights) % 4
            or any(type(w) not in (int, float) or not 0 <= w <= 1 for w in weights)
            or any(type(s) is not int or s < 0 for s in slots)):
        raise ValueError("Invalid attachment weights.")
    for start in range(0, len(weights), 4):
        row = weights[start:start + 4]
        if abs(sum(row) - 1) > 1e-3 or any(w > 0 and str(s) not in mapping
                                           for w, s in zip(row, slots[start:start + 4])):
            raise ValueError("Invalid attachment weights.")
    if (not isinstance(expression, dict) or len(expression) > 256 or any(
            not isinstance(key, str) or not key.isdigit() or not int(key) < 2 ** 32
            or type(weight) not in (int, float) or not math.isfinite(weight) or abs(weight) > 2
            for key, weight in expression.items())):
        raise ValueError("Invalid attachment expression.")


def _merged_palette(raw, palettes):
    if raw is None:
        return []
    if not isinstance(raw, list) or not 1 <= len(raw) <= 4096:
        raise ValueError("Invalid merged palette.")
    result = []
    seen = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError("Invalid merged palette component.")
        geometry_hash = entry.get("geometry_hash")
        ordinal = _integer(entry.get("component_ordinal"))
        offset = _integer(entry.get("vg_offset"))
        identity = geometry_hash, ordinal
        candidates = [p for p in palettes if
                      (p["geometry_hash"], p["component_ordinal"]) == identity]
        if len(candidates) != 1 or identity in seen:
            raise ValueError("Ambiguous merged palette component.")
        seen.add(identity)
        palette = candidates[0]
        result.append(dict(geometry_hash=geometry_hash, component_ordinal=ordinal,
                           first_index=palette["first_index"], index_count=palette["index_count"],
                           vg_offset=offset, vg_count=len(palette["bone_map"])))
    result.sort(key=lambda item: (item["geometry_hash"], item["vg_offset"]))
    for geometry_hash in {item["geometry_hash"] for item in result}:
        cursor = 0
        family = [item for item in result if item["geometry_hash"] == geometry_hash]
        if len(family) != sum(p["geometry_hash"] == geometry_hash for p in palettes):
            raise ValueError("Merged palette must include every captured component.")
        for item in family:
            if item["vg_offset"] != cursor or cursor + item["vg_count"] > 1024:
                raise ValueError("Merged palette must be contiguous and fit the shared palette.")
            cursor += item["vg_count"]
    return result


def validate_rig(raw):
    """Normalize a bounded, topologically ordered skeleton and local palettes."""
    if (not isinstance(raw, dict) or raw.get("format") != "mod-viewer-rig"
            or type(raw.get("version")) is not int or raw["version"] not in (1, 2)
            or raw.get("coordinate_space") != "asset"):
        raise ValueError("Unsupported rig format.")
    bones = raw.get("bones")
    if not isinstance(bones, list) or not 1 <= len(bones) <= _MAX_BONES:
        raise ValueError("Invalid rig skeleton.")
    normalized = []
    names = set()
    for index, bone in enumerate(bones):
        name = bone.get("name") if isinstance(bone, dict) else None
        if (not isinstance(name, str) or not name or len(name) > 256
                or name in names):
            raise ValueError("Invalid rig bone name.")
        names.add(name)
        parent = _integer(bone.get("parent"), -1)
        if parent >= index:
            raise ValueError("Rig parents must precede their children.")
        translation = _vector(bone.get("translation"), 3)
        scale = _vector(bone.get("scale", [1, 1, 1]), 3)
        # Game skeletons carry float noise (up to ~1e-3) on otherwise uniform scales, and mirrored
        # bones use a uniform negative scale (for example -1 on every axis).
        if (min(abs(value) for value in scale) < 1e-6
                or (raw["version"] == 1 and any(abs(value - scale[0]) > 2e-3 * abs(scale[0]) for value in scale))):
            raise ValueError("Reference bone scales must be uniform with a magnitude of at least 0.000001.")
        rotation = _vector(bone.get("rotation"), 4)
        length = math.sqrt(sum(v * v for v in rotation))
        if abs(length - 1) > 0.001:
            raise ValueError("Rig rotation is not normalized.")
        # Re-normalizing an already unit quaternion perturbs its last bits; keep
        # it so the skeleton fingerprint is stable across Rig.json round trips.
        if abs(length - 1) > 1e-9:
            rotation = [v / length for v in rotation]
        normalized.append(dict(name=name, parent=parent, translation=translation,
                               rotation=rotation, scale=scale))
    components = raw.get("components")
    if not isinstance(components, list) or not 1 <= len(components) <= 4096:
        raise ValueError("Invalid rig palettes.")
    palettes = []
    identities = set()
    for component in components:
        if not isinstance(component, dict):
            raise ValueError("Invalid rig palette.")
        geometry_hash = component.get("geometry_hash")
        if not isinstance(geometry_hash, str) or not re.fullmatch(r"[0-9a-f]{8}", geometry_hash):
            raise ValueError("Invalid rig geometry identity.")
        ordinal = _integer(component.get("component_ordinal"))
        first = _integer(component.get("first_index"))
        count = _integer(component.get("index_count"), 3)
        if count % 3:
            raise ValueError("Invalid rig triangle range.")
        # Alternate draw passes of one hash Asset share an index range; only
        # their captured metadata entry tells them apart.
        entry = component.get("metadata_entry")
        identity = geometry_hash, ordinal, first, count, None if entry is None else _integer(entry)
        if identity in identities:
            raise ValueError("Duplicate rig palette.")
        identities.add(identity)
        mapping = component.get("bone_map")
        mapping = _bone_mapping(mapping, len(bones), sparse=raw["version"] == 2)
        if "attachment" in component:
            _validate_attachment(component["attachment"], mapping, raw["version"])
        for field in ("vertex_sha256", "index_sha256"):
            if not isinstance(component.get(field), str) or not _SHA256.fullmatch(component[field]):
                raise ValueError("Invalid rig geometry fingerprint.")
        palettes.append({**component, "bone_map": mapping})
    skin_bindings = raw.get("skin_bindings", [])
    if not isinstance(skin_bindings, list) or len(skin_bindings) > 1024:
        raise ValueError("Invalid skin bindings.")
    profiles = []
    profile_keys = set()
    for profile in skin_bindings:
        if not isinstance(profile, dict):
            raise ValueError("Invalid skin binding.")
        digest, encoding = profile.get("sha256"), profile.get("encoding")
        mapping = profile.get("bone_map")
        if (not isinstance(digest, str) or not _SHA256.fullmatch(digest)
                or encoding not in ("wwmi_u8_4", "wwmi_u8_8", "gimi_f32_u32_4", "rigid_u32_1")
                or (digest, encoding) in profile_keys):
            raise ValueError("Invalid or duplicate skin binding.")
        mapping = _bone_mapping(mapping, len(bones), sparse=raw["version"] == 2,
                                maximum=256 if encoding.startswith("wwmi_") else _MAX_BONES, unique=False)
        profile_keys.add((digest, encoding))
        profiles.append(dict(sha256=digest, encoding=encoding, bone_map=mapping))
    document = dict(format="mod-viewer-rig", version=raw["version"], coordinate_space="asset",
                    bones=normalized, components=palettes, skin_bindings=profiles)
    merged = _merged_palette(raw.get("wwmi_merged_palette"), palettes)
    if merged:
        document["wwmi_merged_palette"] = merged
    humanoid = _humanoid_controls(raw.get("humanoid_controls"), normalized)
    if humanoid is not None:
        document["humanoid_controls"] = humanoid
    # Palette/profile admission can grow without changing joint-ID meaning.
    # Saved control mappings belong to the skeleton and its semantic controls.
    identity = {key: value for key, value in document.items()
                if key not in ("components", "skin_bindings", "wwmi_merged_palette")}
    fingerprint = hashlib.sha256(json.dumps(
        identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {**document, "id": fingerprint}


def _json(path):
    if os.path.getsize(path) > _MAX_BYTES:
        raise ValueError("Rig metadata is too large.")
    with open(path, encoding="utf-8") as stream:
        return json.load(stream)


def _morph_reference(directory, reference_dir, palette, cache):
    """Read each contained reference layout/vertex buffer once during Weight loading."""
    key = "morph-reference", reference_dir, palette["component_ordinal"]
    if key not in cache:
        cache[key] = None
        files = [paths.safe_asset_path(directory, os.path.relpath(os.path.join(
            reference_dir, f"Component {palette['component_ordinal']}.{extension}"), directory))
            for extension in ("fmt", "vb", "ib")]
        if not all(files) or os.path.getsize(files[0]) > 1024 * 1024:
            return None
        layout = parse_fmt(files[0])
        if layout.stride <= 0 or os.path.getsize(files[1]) > 64 * 1024 * 1024:
            return None
        with open(files[1], "rb") as stream:
            data = stream.read(64 * 1024 * 1024 + 1)
        if len(data) > 64 * 1024 * 1024 or len(data) % layout.stride:
            return None
        cache[key] = dict(layout=layout, vertices=data, count=len(data) // layout.stride, index_file=files[2])
    return cache[key]


def _reference_morph_topology(reference, palette):
    """Share one canonical topology/UV decode between exact and triangle matching."""
    if "topology" not in reference:
        reference["topology"] = None
        layout, vertices = reference["layout"], reference["vertices"]
        uv = layout.semantic("TEXCOORD", 0)
        uv_format = {"R16G16_FLOAT": "<2e", "R32G32_FLOAT": "<2f"}.get(
            uv.format.removeprefix("DXGI_FORMAT_")) if uv else None
        index_format = {"R16_UINT": "<3H", "R32_UINT": "<3I"}.get(layout.index_format.removeprefix("DXGI_FORMAT_"))
        if (not uv_format or not index_format or uv.offset < 0
                or uv.offset + struct.calcsize(uv_format) > layout.stride
                or os.path.getsize(reference["index_file"]) > 64 * 1024 * 1024):
            return None
        with open(reference["index_file"], "rb") as stream:
            raw_indices = stream.read(64 * 1024 * 1024 + 1)
        if (len(raw_indices) > 64 * 1024 * 1024
                or len(raw_indices) != palette["index_count"] * struct.calcsize(index_format) // 3):
            return None
        topology_hash, uvs = hashlib.sha256(), bytearray()
        for a, b, c in struct.iter_unpack(index_format, raw_indices):
            if max(a, b, c) >= reference["count"]:
                return None
            topology_hash.update(struct.pack("<3I", a, c, b))
        for offset in range(0, len(vertices), layout.stride):
            u, v = struct.unpack_from(uv_format, vertices, offset + uv.offset)
            if not math.isfinite(u) or not math.isfinite(v):
                return None
            uvs.extend(struct.pack("<2f", u, 1 - v))
        reference["topology"] = raw_indices, bytes(uvs)
        reference["index_format"] = index_format
        reference["signature"] = topology_hash.hexdigest(), hashlib.sha256(uvs).hexdigest()
    return reference["topology"]


def _mod_morph_uv_triangles(reference, vertex_count, topology):
    """Recover split/reordered vertices from unambiguous UV triangles."""
    indices, uvs = topology
    if (vertex_count > 100000 or reference["count"] > 100000
            or not indices or len(indices) % 12 or len(indices) > 1200000
            or len(uvs) != vertex_count * 8 or len(reference["topology"][0]) > 1200000):
        return None
    if "triangles" not in reference:
        original_indices, original_uvs = reference["topology"]
        points = [original_uvs[offset:offset + 8] for offset in range(0, len(original_uvs), 8)]
        unique = {}
        for index, point in enumerate(points):
            unique[point] = None if point in unique else index
        triangles = {}
        for a, b, c in struct.iter_unpack(reference["index_format"], original_indices):
            for order in reference.get("triangle_orders", ((0, 2, 1),)):
                ids = tuple((a, b, c)[index] for index in order)
                for shift in range(3):
                    row = ids[shift:] + ids[:shift]
                    triangle = tuple(points[index] for index in row)
                    if triangle not in triangles:
                        triangles[triangle] = row
                    elif triangles[triangle] != row:
                        triangles[triangle] = None
        reference["triangles"], reference["unique_uvs"] = triangles, unique
    triangles, unique = reference["triangles"], reference["unique_uvs"]
    points = [uvs[offset:offset + 8] for offset in range(0, len(uvs), 8)]
    mapping, conflicts = [-1] * vertex_count, set()
    for row in struct.iter_unpack("<3I", indices):
        if max(row) >= vertex_count:
            return None
        source = triangles.get(tuple(points[index] for index in row))
        if source is None:
            continue
        for index, target in zip(row, source):
            if mapping[index] not in (-1, target):
                conflicts.add(index)
            mapping[index] = target
    for index in conflicts:
        mapping[index] = -1
    proven = {points[index] for index, target in enumerate(mapping) if target >= 0}
    for index, point in enumerate(points):
        if mapping[index] < 0 and index not in conflicts and point in proven and unique.get(point) is not None:
            mapping[index] = unique[point]
    return mapping, conflicts


def _mod_morph_vertices(directory, reference_dir, palette, positions, cache, signature=(), topology=(), *, reference=None):
    """Prefer proven topology, leaving ambiguous position/UV matches unmapped."""
    if not positions or len(positions) % 12:
        return None
    try:
        if reference is None:
            reference = _morph_reference(directory, reference_dir, palette, cache)
        if reference is None:
            return None
        exact_topology = len(signature) == 2 and len(positions) // 12 == reference["count"]
        uv_triangles = len(topology) == 2
        reference_topology = None
        if exact_topology or uv_triangles:
            try:
                reference_topology = _reference_morph_topology(reference, palette)
            except (OSError, ValueError, TypeError, struct.error):
                pass
            if reference_topology is not None and exact_topology and signature == reference["signature"]:
                return list(range(reference["count"]))
        if "buckets" not in reference:
            reference["buckets"] = None
            layout, data = reference["layout"], reference["vertices"]
            element = layout.semantic("POSITION")
            if (not element or element.format.removeprefix("DXGI_FORMAT_") != "R32G32B32_FLOAT"
                    or element.offset < 0 or element.offset + 12 > layout.stride):
                return None
            buckets = {}
            for index in range(reference["count"]):
                point = struct.unpack_from("<3f", data, index * layout.stride + element.offset)
                if not all(math.isfinite(value) for value in point):
                    return None
                cell = tuple(math.floor(value * CELL_SCALE) for value in point)
                bucket = buckets.setdefault(cell, {})
                bucket[point] = -1 if point in bucket else index
                if len(bucket) > 64:
                    return None
            reference["buckets"] = buckets
        buckets = reference["buckets"]
        if buckets is None:
            return None
        vertices = match_positions(buckets, positions, reference)
        if uv_triangles and reference_topology is not None:
            result = _mod_morph_uv_triangles(reference, len(vertices), topology)
            if result is not None:
                uv_mapping, conflicts = result
                vertices = [-1 if index in conflicts else target if target >= 0 else original
                            for index, (target, original) in enumerate(zip(uv_mapping, vertices))]
        return vertices if any(vertex >= 0 for vertex in vertices) else None
    except (OSError, ValueError, TypeError, struct.error):
        return None


def _admitted_rig(binding, enabled_roots, cache):
    """Return ``(Asset directory, validated rig)`` for an exact, enabled binding."""
    if (binding is None or binding.status != "exact" or binding.range_status != "exact"
            or os.path.normcase(binding.root or "") not in enabled_roots):
        return None
    directory = paths.safe_asset_dir(binding.root, binding.asset)
    filename = paths.safe_asset_path(directory, RIG_FILENAME) if directory else None
    if not filename:
        return None
    if filename not in cache:
        cache[filename] = validate_rig(_json(filename))
    return directory, cache[filename]


def _file_matches(path, fingerprint, cache, max_bytes):
    """Whether a bounded reference file has the recorded SHA-256 (cached)."""
    key = path, fingerprint
    if key not in cache:
        if os.path.getsize(path) > max_bytes:
            return False
        with open(path, "rb") as stream:
            cache[key] = hashlib.file_digest(stream, "sha256").hexdigest() == fingerprint
    return cache[key]


def _game_bone_mapping(bone_map, bone_ids, offset=0):
    """Map decoded IDs (palette index + offset) to skeleton joints, or None."""
    items = bone_map.items() if isinstance(bone_map, dict) else enumerate(bone_map)
    mapping = {str(int(index) + offset): joint for index, joint in items}
    return mapping if all(str(bone) in mapping for bone in bone_ids) else None


def resolve_game_rig(binding, source, bone_ids, enabled_roots, cache, *,
                     blend_sha256=None, merged_layout=(), captured_palette=False, positions=b"", morph_signature=(), morph_topology=()):
    """Read optional rig data only for a retained exact Asset/range binding.

    Unsupported skin namespaces, changed reference geometry and incomplete
    palettes keep the ordinary inferred-rig path. Authored weights are untouched.
    """
    if binding is not None and binding.asset_type in ("ZZMI", "GIMI"):
        # GIMI shares the hash-Asset palette proof; only ZZZ has a merged skeleton layout.
        return _resolve_zzz_rig(binding, source, bone_ids, enabled_roots, cache, blend_sha256, captured_palette,
                                merged_layout if binding.asset_type == "ZZMI" else (), positions, morph_signature,
                                morph_topology)
    if (binding is None or binding.asset_type != "WWMI"
            or source.encoding not in ("wwmi_u8_4", "wwmi_u8_8")
            or (source.vertex_vg_file and not source.blend_remap)):
        return None
    try:
        admitted = _admitted_rig(binding, enabled_roots, cache)
        if not admitted:
            return None
        directory, rig = admitted
        profiles = [profile for profile in rig["skin_bindings"] if
                    profile["sha256"] == blend_sha256
                    and profile["encoding"] == source.encoding]
        matches = [c for c in rig["components"]
                   if (c["geometry_hash"], c["component_ordinal"],
                       c["first_index"], c["index_count"])
                   == (binding.geometry_hash, binding.component_ordinal,
                       binding.first_index, binding.index_count)]
        if len(matches) != 1:
            return None
        palette = matches[0]
        references = [palette]
        if captured_palette:
            bone_map = palette["bone_map"]
        elif source.bone_id_namespace == "wwmi_component":
            # Native per-component skinning: the INI proof names the game
            # component whose palette is bound, which must be this binding.
            if source.bone_id_domain != wwmi_component_domain(
                    binding.geometry_hash, binding.first_index, binding.index_count):
                return None
            bone_map = palette["bone_map"]
        elif len(profiles) == 1 and not source.blend_remap:
            bone_map = profiles[0]["bone_map"]
        else:
            family = [item for item in rig.get("wwmi_merged_palette", [])
                      if item["geometry_hash"] == binding.geometry_hash]
            expected = tuple((p["geometry_hash"], p["first_index"], p["index_count"],
                              p["vg_offset"], p["vg_count"]) for p in family)
            if (not expected or merged_layout != expected
                    or not any(p["component_ordinal"] == binding.component_ordinal
                               and p["vg_offset"] == source.bone_id_offset for p in family)):
                return None
            references = [next(p for p in rig["components"] if
                               (p["geometry_hash"], p["component_ordinal"])
                               == (item["geometry_hash"], item["component_ordinal"])) for item in family]
            if any(not isinstance(p["bone_map"], list) for p in references):
                return None
            bone_map = [bone for p in references for bone in p["bone_map"]]
        metadata_path = paths.safe_asset_path(binding.root, binding.metadata)
        if not metadata_path or paths.relative_asset_path(directory, metadata_path) is None:
            return None
        metadata_key = "metadata", metadata_path
        if metadata_key not in cache:
            cache[metadata_key] = _json(metadata_path)
        metadata = cache[metadata_key]
        if metadata.get("vb0_hash") != binding.geometry_hash:
            return None
        reference_dir = os.path.dirname(metadata_path)
        for reference_palette in references:
            ordinal = reference_palette["component_ordinal"]
            component = metadata["components"][ordinal]
            if (component.get("index_offset") != reference_palette["first_index"]
                    or component.get("index_count") != reference_palette["index_count"]):
                return None
            for extension, fingerprint in (("vb", "vertex_sha256"), ("ib", "index_sha256")):
                reference = paths.safe_asset_path(
                    directory, os.path.relpath(os.path.join(
                        reference_dir, f"Component {ordinal}.{extension}"), directory))
                if not reference or not _file_matches(reference, reference_palette[fingerprint], cache, 64 * 1024 * 1024):
                    return None
        offset = 0 if source.bone_id_namespace == "wwmi_merged" or source.blend_remap else source.bone_id_offset
        mapping = _game_bone_mapping(bone_map, bone_ids, offset)
        if mapping is None:
            return None
        morph_binding = {key: palette[key] for key in ("vertex_sha256", "index_sha256")}
        if not captured_palette:
            vertices = _mod_morph_vertices(directory, reference_dir, palette, positions, cache, morph_signature, morph_topology)
            morph_binding = {**morph_binding, "vertices": vertices} if vertices is not None else None
        return dict(id=rig["id"], version=rig["version"], bones=rig["bones"], bone_map=mapping,
                    humanoid_controls=rig.get("humanoid_controls"),
                    morph_binding=morph_binding)
    except (OSError, ValueError, TypeError, KeyError, IndexError, RecursionError):
        return None


def _zzz_morph_reference(directory, palette, cache):
    key = "zzz-morph-reference", directory, palette["vertex_file"], palette["index_file"], palette["first_index"], palette["index_count"]
    if key not in cache:
        cache[key] = None
        vb = paths.safe_asset_path(directory, palette["vertex_file"])
        ib = paths.safe_asset_path(directory, palette["index_file"])
        if not vb or not ib:
            return None
        dump_key = "zzz-morph-dump", vb
        if dump_key not in cache:
            cache[dump_key] = parse_vertex_dump(vb)
        dump = cache[dump_key]
        if dump.layout.vertex_count > 100000 or dump.uvs is None:
            return None
        index_key = "zzz-morph-indices", ib
        if index_key not in cache:
            cache[index_key] = parse_index_dump(ib, vertex_count=dump.layout.vertex_count)
        index = cache[index_key]
        first = palette["first_index"] - index.first_index
        count = palette["index_count"]
        if first < 0 or first % 3 or first + count > len(index.indices):
            return None
        packed = pack_indices(index.indices[first:first + count])
        layout = MigotoBinaryLayout(12, "trianglelist", "R32_UINT", (MigotoElement("POSITION", 0, "R32G32B32_FLOAT", 0),))
        cache[key] = dict(layout=layout, vertices=dump.positions, count=dump.layout.vertex_count,
            topology=(packed, dump.uvs), index_format="<3I",
            triangle_orders=((0, 1, 2), (0, 2, 1)),
            signature=(hashlib.sha256(packed).hexdigest(), hashlib.sha256(dump.uvs).hexdigest()))
    return cache[key]


def _zzz_metadata(directory, palette, cache):
    """Verified metadata entry of one captured ZZZ palette, or None."""
    # Every mesh scans every captured palette; verify each palette once per request.
    key = ("zzz-palette-metadata", directory, *(palette[name] for name in (
        "metadata_file", "metadata_sha256", "vertex_file", "vertex_sha256",
        "index_file", "index_sha256", "metadata_entry")))
    if key not in cache:
        cache[key] = _verified_zzz_metadata(directory, palette, cache)
    return cache[key]


def _verified_zzz_metadata(directory, palette, cache):
    for relative, fingerprint in ((palette["metadata_file"], palette["metadata_sha256"]),
                                  (palette["vertex_file"], palette["vertex_sha256"]),
                                  (palette["index_file"], palette["index_sha256"])):
        reference = paths.safe_asset_path(directory, relative)
        if (not reference or not isinstance(fingerprint, str) or not _SHA256.fullmatch(fingerprint)
                or not _file_matches(reference, fingerprint, cache, 512 * 1024 * 1024)):
            return None
    key = "zzz-metadata", paths.safe_asset_path(directory, palette["metadata_file"])
    if key not in cache:
        cache[key] = metadata_entries(_json(key[1]))
    return cache[key][palette["metadata_entry"]]


def _zzz_merged_bone_map(rig, directory, own, layout, cache):
    """Map merged palette IDs through each segment's captured native Blend stream.

    A segment names the native Blend hash whose palette was copied; every
    captured component of that one stream shares its palette. Segments whose
    stream is not uniquely captured stay unmapped, so their IDs reject the rig.
    """
    if own.get("blend_vb") not in {segment[0] for segment in layout}:
        return None
    mapping = {}
    for blend_hash, offset, count in layout:
        palettes = _zzz_stream_palettes(rig, directory, (blend_hash,), cache)
        if len({(p["metadata_file"], p["metadata_entry"]) for p in palettes}) != 1:
            continue
        segment = _zzz_stream_bone_map(palettes, offset, count)
        if segment is None:
            return None
        mapping.update(segment)
    return mapping


def _zzz_stream_palettes(rig, directory, hashes, cache):
    """Captured components whose native stream matches every hash in ``hashes``.

    ZZZ skins a whole stream once, at its Blend pass, with one palette. Each
    captured component of that stream records the part of it its draw used.
    """
    palettes = []
    if not hashes:
        return palettes
    for palette in rig["components"]:
        # Weightless attachments have no native Blend stream for a mod to share.
        if not palette.get("metadata_file") or "metadata_entry" not in palette or "attachment" in palette:
            continue
        stream = _zzz_metadata(directory, palette, cache) or {}
        if set(hashes) <= {stream.get("blend_vb"), stream.get("position_vb")}:
            palettes.append(palette)
    return palettes


def _zzz_stream_bone_map(palettes, offset=0, count=None):
    """Union of one stream's captured bone maps; conflicting joints reject it."""
    mapping = {}
    for palette in palettes:
        items = palette["bone_map"].items() if isinstance(palette["bone_map"], dict) else enumerate(palette["bone_map"])
        for local, joint in items:
            if count is None or int(local) < count:
                if mapping.setdefault(str(offset + int(local)), joint) != joint:
                    return None
    return mapping


def _hash_asset_palette(binding, enabled_roots, cache):
    """The unique verified version-2 palette of an exact hash-Asset binding, or None.

    Returns (directory, rig, palette, metadata component).
    """
    admitted = _admitted_rig(binding, enabled_roots, cache)
    if not admitted:
        return None
    directory, rig = admitted
    if rig["version"] != 2:
        return None
    # Hash Assets may identify an exact range by its first index alone (GIMI
    # metadata has no counts); the captured component still has to be unique.
    matches = [p for p in rig["components"]
               if (p["geometry_hash"], p["first_index"]) == (binding.geometry_hash, binding.first_index)
               and (binding.index_count is None or p["index_count"] == binding.index_count)
               and (binding.component_ordinal is None or p["component_ordinal"] == binding.component_ordinal)
               and (binding.metadata_entry is None or p.get("metadata_entry") == binding.metadata_entry)]
    if len(matches) != 1:
        return None
    palette = matches[0]
    metadata = paths.safe_asset_path(binding.root, binding.metadata)
    expected = paths.safe_asset_path(directory, palette.get("metadata_file", ""))
    if not metadata or not expected or os.path.normcase(metadata) != os.path.normcase(expected):
        return None
    component = _zzz_metadata(directory, palette, cache)
    return None if component is None else (directory, rig, palette, component)


def game_attachment(binding, enabled_roots, cache):
    """Proven native weights for an Asset's own weightless GIMI capture, or None."""
    if binding is None or binding.asset_type != "GIMI":
        return None
    try:
        found = _hash_asset_palette(binding, enabled_roots, cache)
        return found[2]["attachment"] if found is not None and "attachment" in found[2] else None
    except (OSError, ValueError, TypeError, KeyError, IndexError, RecursionError):
        return None


def _resolve_zzz_rig(binding, source, bone_ids, enabled_roots, cache, digest, captured, merged_layout,
                     positions, signature, topology):
    # Attachment weights exist only for an Asset's own weightless GIMI capture.
    attachment = source.bone_id_namespace == ATTACHMENT_NAMESPACE
    if (source.encoding != "gimi_f32_u32_4" or (attachment and not (captured and binding.asset_type == "GIMI"))
            or source.blend_remap or source.vertex_vg_file or source.bone_id_offset):
        return None
    try:
        found = _hash_asset_palette(binding, enabled_roots, cache)
        if found is None or ("attachment" in found[2]) != attachment:
            return None
        directory, rig, palette, component = found
        mapping = palette["bone_map"]
        own_geometry = True
        # Captured palettes are proven during extraction. Edited mod streams
        # require a verified INI palette proof, not just matching numeric IDs.
        if merged_layout and not captured:
            mapping = _zzz_merged_bone_map(rig, directory, component, merged_layout, cache)
            if mapping is None:
                return None
        elif not captured:
            profiles = [p for p in rig["skin_bindings"] if p["encoding"] == source.encoding and p["sha256"] == digest]
            if len(profiles) == 1:
                mapping = profiles[0]["bone_map"]
            else:
                # The proven native stream decides the palette, even when
                # another component's override draws that stream.
                stream = (_zzz_stream_palettes(rig, directory, source.native_palette_hashes, cache)
                          or _zzz_stream_palettes(rig, directory, source.native_position_hashes, cache))
                mapping = _zzz_stream_bone_map(stream) if stream else None
                if mapping is None:
                    return None
                own_geometry = any(p is palette for p in stream)
        mapping = _game_bone_mapping(mapping, bone_ids)
        if mapping is None:
            return None
        if not own_geometry:
            # The bound component's morph reference describes different geometry.
            return dict(id=rig["id"], version=2, bones=rig["bones"], bone_map=mapping,
                        humanoid_controls=rig.get("humanoid_controls"), morph_binding=None)
        morph_binding = {key: palette[key] for key in ("vertex_sha256", "index_sha256")}
        if not captured:
            reference = _zzz_morph_reference(directory, palette, cache)
            vertices = (_mod_morph_vertices(directory, directory, palette, positions, cache, signature, topology,
                                            reference=reference) if reference is not None else None)
            morph_binding = {**morph_binding, "vertices": vertices} if vertices is not None else None
        return dict(id=rig["id"], version=2, bones=rig["bones"], bone_map=mapping,
                    humanoid_controls=rig.get("humanoid_controls"), morph_binding=morph_binding)
    except (OSError, ValueError, TypeError, KeyError, IndexError, RecursionError):
        return None

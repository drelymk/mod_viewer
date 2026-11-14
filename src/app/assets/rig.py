"""Optional authored skeletons attached to exact, registered WWMI Assets."""

import hashlib
import json
import math
import os
import re

from . import paths


RIG_FILENAME = "Rig.json"
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
            or type(raw.get("version")) is not int or raw["version"] != 1
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
                or any(abs(value - scale[0]) > 2e-3 * abs(scale[0]) for value in scale)):
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
        identity = geometry_hash, ordinal, first, count
        if identity in identities:
            raise ValueError("Duplicate rig palette.")
        identities.add(identity)
        mapping = component.get("bone_map")
        if (not isinstance(mapping, list) or not 1 <= len(mapping) <= _MAX_BONES
                or any(type(v) is not int or not 0 <= v < len(bones) for v in mapping)
                or len(set(mapping)) != len(mapping)):
            raise ValueError("Invalid rig bone mapping.")
        for field in ("vertex_sha256", "index_sha256"):
            if not isinstance(component.get(field), str) or not _SHA256.fullmatch(component[field]):
                raise ValueError("Invalid rig geometry fingerprint.")
        palettes.append({**component, "bone_map": list(mapping)})
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
                or encoding not in ("wwmi_u8_4", "wwmi_u8_8")
                or not isinstance(mapping, list) or not 1 <= len(mapping) <= 256
                or any(type(v) is not int or not 0 <= v < len(bones) for v in mapping)
                or (digest, encoding) in profile_keys):
            raise ValueError("Invalid or duplicate skin binding.")
        profile_keys.add((digest, encoding))
        profiles.append(dict(sha256=digest, encoding=encoding, bone_map=list(mapping)))
    document = dict(format="mod-viewer-rig", version=1, coordinate_space="asset",
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


def resolve_game_rig(binding, source, bone_ids, enabled_roots, cache, *,
                     blend_sha256=None, merged_layout=(), captured_palette=False):
    """Read optional rig data only for a retained exact Asset/range binding.

    Unsupported skin namespaces, changed reference geometry and incomplete
    palettes keep the ordinary inferred-rig path. Authored weights are untouched.
    """
    if (binding is None or binding.status != "exact"
            or binding.range_status != "exact" or binding.asset_type != "WWMI"
            or os.path.normcase(binding.root or "") not in enabled_roots
            or source.encoding not in ("wwmi_u8_4", "wwmi_u8_8")
            or (source.vertex_vg_file and not source.blend_remap)):
        return None
    try:
        directory = paths.safe_asset_dir(binding.root, binding.asset)
        if not directory:
            return None
        filename = paths.safe_asset_path(directory, RIG_FILENAME)
        if not filename:
            return None
        if filename not in cache:
            cache[filename] = validate_rig(_json(filename))
        rig = cache[filename]
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
                if not reference:
                    return None
                key = reference, reference_palette[fingerprint]
                if key not in cache:
                    if os.path.getsize(reference) > 64 * 1024 * 1024:
                        return None
                    with open(reference, "rb") as stream:
                        cache[key] = hashlib.file_digest(stream, "sha256").hexdigest() == reference_palette[fingerprint]
                if not cache[key]:
                    return None
        offset = 0 if source.bone_id_namespace == "wwmi_merged" or source.blend_remap else source.bone_id_offset
        for bone_id in bone_ids:
            local = bone_id - offset
            if not 0 <= local < len(bone_map):
                return None
        mapping = {str(index + offset): target for index, target
                   in enumerate(bone_map)}
        return dict(id=rig["id"], bones=rig["bones"], bone_map=mapping,
                    humanoid_controls=rig.get("humanoid_controls"))
    except (OSError, ValueError, TypeError, KeyError, IndexError, RecursionError):
        return None

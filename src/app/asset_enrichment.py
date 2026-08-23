"""Lazy, conservative texture evidence for exact Asset component matches."""

from dataclasses import dataclass
import hashlib
import json
import os
import re

from core.geometry_identity import normalize_geometry_hash

from . import asset_folders


_ROLE_NAMES = {
    "diffuse": "diffuse",
    "normalmap": "normal_map",
    "lightmap": "light_map",
    "materialmap": "material_map",
}
_ROLE_SUFFIXES = {
    "diffuse": "Diffuse",
    "normal_map": "NormalMap",
    "light_map": "LightMap",
    "material_map": "MaterialMap",
}
_WWMI_TEXTURE_RE = re.compile(
    r"^(?P<texture>[0-9a-f]{8})-vs=(?P<vs>[0-9a-f]+)-ps=(?P<ps>[0-9a-f]+)$",
    re.I,
)


@dataclass(frozen=True, slots=True)
class AssetTextureEvidence:
    role: str
    texture_hash: str
    extension: str | None = None
    file: str | None = None


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


def _integer(value):
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_asset_path(root, relative):
    if not isinstance(root, str) or not isinstance(relative, str):
        return None
    candidate = asset_folders.normalize_path(os.path.join(root, relative))
    if not candidate or not asset_folders.is_within(candidate, root):
        return None
    return candidate


def _json(cache, filename):
    if filename in cache:
        return cache[filename]
    try:
        with open(filename, encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError):
        value = None
    cache[filename] = value
    return value


def _texture_records(binding, raw):
    if binding.range_status != "exact":
        return []
    records = []
    for entry in _entries(raw):
        if not isinstance(entry, dict):
            continue
        geometry_hash = normalize_geometry_hash(
            entry.get("ib") or entry.get("ib_hash")
            or entry.get("geometry_hash") or entry.get("geometryHash"))
        if geometry_hash != binding.geometry_hash:
            continue
        first_values = _values(entry, (
            "object_indexes", "objectIndexes", "first_indices",
            "firstIndices", "first_index", "firstIndex")) or [0]
        classifications = _values(entry, (
            "object_classifications", "objectClassifications",
            "classifications"))
        positions = [
            position for position, value in enumerate(first_values)
            if _integer(value) == binding.first_index
            and (not binding.classification or
                 (position < len(classifications) and
                  classifications[position] == binding.classification))
        ]
        if len(positions) != 1:
            continue
        textures = entry.get("texture_hashes")
        if not isinstance(textures, list) or positions[0] >= len(textures):
            continue
        for item in textures[positions[0]] or []:
            if not isinstance(item, list) or len(item) < 3:
                continue
            role = _ROLE_NAMES.get(str(item[0]).replace("_", "").lower())
            texture_hash = normalize_geometry_hash(item[2])
            extension = item[1] if isinstance(item[1], str) else None
            if role and texture_hash:
                records.append(AssetTextureEvidence(
                    role, texture_hash, extension))
        if records:
            return records
    return records


def _locate_texture(binding, evidence):
    asset_dir = _safe_asset_path(binding.root, binding.asset)
    component = binding.component_name
    if (not asset_dir or not os.path.isdir(asset_dir)
            or not isinstance(component, str) or not component.strip()):
        return None
    extension = (evidence.extension or "").casefold()
    suffix = (component.strip()
              + str(binding.classification or "").strip()
              + _ROLE_SUFFIXES.get(evidence.role, "")).casefold()
    if not suffix:
        return None
    candidates = []
    try:
        with os.scandir(asset_dir) as entries:
            for entry in entries:
                if not entry.is_file(follow_symlinks=False):
                    continue
                name = entry.name.casefold()
                if extension and not name.endswith(extension):
                    continue
                stem, _extension = os.path.splitext(name)
                if not stem.endswith(suffix):
                    continue
                candidates.append(os.path.abspath(entry.path))
    except OSError:
        return None
    return candidates[0] if len(candidates) == 1 else None


def _logical_key(binding, filename):
    root_key = hashlib.sha256(
        asset_folders.normalize_path(binding.root).encode("utf-8")).hexdigest()[:16]
    relative = os.path.relpath(filename, binding.root).replace(os.sep, "/")
    return f"asset/{root_key}/{relative}"


def _gimi_evidence(binding, cache):
    filename = _safe_asset_path(binding.root, binding.metadata)
    raw = _json(cache, filename) if filename else None
    return _texture_records(binding, raw)


def _wwmi_slot_evidence(binding, cache):
    filename = _safe_asset_path(binding.root, binding.detail_metadata)
    raw = _json(cache, filename) if filename else None
    if not isinstance(raw, dict) or binding.component_ordinal is None:
        return {}
    component = raw.get(f"Component {binding.component_ordinal}")
    if not isinstance(component, dict):
        return {}
    result = {}
    for key, values in component.items():
        match = re.fullmatch(r"ps-t(\d+)", str(key), re.I)
        if not match or not isinstance(values, list):
            continue
        parsed = []
        for value in values:
            item = _WWMI_TEXTURE_RE.match(str(value))
            if item:
                parsed.append({
                    "slot": int(match.group(1)),
                    "texture_hash": item.group("texture").lower(),
                    "vs_hash": item.group("vs").lower(),
                    "ps_hash": item.group("ps").lower(),
                })
        if parsed:
            result[int(match.group(1))] = parsed
    return result


def _has_mod_texture(draw, role):
    return bool(draw.texture_default(role) or draw.texture_rules(role))


def _apply_slot_hashes(draw, evidence):
    """Use a component-local texture hash to identify a slot's role."""
    by_hash = {}
    for item in evidence:
        by_hash.setdefault(item.texture_hash, []).append(item)
    for binding in draw.slot_textures:
        if not binding.texture_hashes or not binding.file:
            continue
        matches = {
            item
            for texture_hash in binding.texture_hashes
            for item in by_hash.get(texture_hash, [])
        }
        roles = {item.role for item in matches}
        if binding.role_hint:
            conflicting = roles - {binding.role_hint}
            if conflicting:
                for item in matches:
                    if item.role not in conflicting:
                        continue
                    draw.asset_slot_evidence.append({
                        "resource": binding.resource,
                        "slot": binding.slot,
                        "texture_hash": item.texture_hash,
                        "role": binding.role_hint,
                        "role_source": "mod_slot_mapping",
                        "asset_hash_role": item.role,
                        "conflict": True,
                    })
                continue
            if (roles == {binding.role_hint} and binding.file
                    and not _has_mod_texture(draw, binding.role_hint)):
                draw.set_texture_default(binding.role_hint, binding.file)
                draw.texture_hashes.setdefault(binding.role_hint, []).append(
                    next(iter(matches)).texture_hash)
                draw.texture_provenance.setdefault(
                    binding.role_hint, "mod_slot_semantic")
            continue
        if len(roles) != 1:
            continue
        item = next(iter(matches))
        if _has_mod_texture(draw, item.role):
            continue
        draw.set_texture_default(item.role, binding.file)
        draw.texture_hashes.setdefault(item.role, []).append(
            item.texture_hash)
        draw.texture_provenance[item.role] = "mod_texture_hash"


def apply(groups, bindings, metadata_cache=None, *, include_not_found=False):
    """Apply Asset diagnostics and exact-component texture evidence.

    A not-found binding is published only when at least one ready index was
    queried.  With no configured or usable index, omitting it preserves the
    legacy no-Asset presentation while the aggregate report explains why
    matching was unavailable.
    """
    metadata_cache = metadata_cache if metadata_cache is not None else {}
    for group, group_bindings in zip(groups, bindings):
        for draw, binding in zip(group.get("draws", []), group_bindings):
            if binding.status == "not_found" and not include_not_found:
                continue
            draw.asset_binding = binding
            if binding.status == "not_found":
                continue
            if (binding.status != "exact"
                    or binding.component_status != "exact"
                    or binding.range_status != "exact"):
                continue

            for role in ("diffuse", "normal_map", "light_map", "material_map"):
                if _has_mod_texture(draw, role):
                    draw.texture_provenance.setdefault(role, "mod_semantic")

            evidence = []
            if binding.asset_type in ("GIMI", "ZZMI"):
                evidence = _gimi_evidence(binding, metadata_cache)
            elif binding.asset_type == "WWMI":
                slots = _wwmi_slot_evidence(binding, metadata_cache)
                draw.asset_slot_evidence = []
                for item in draw.slot_textures:
                    for usage in slots.get(item.slot, []):
                        slot_evidence = {"resource": item.resource, **usage}
                        if item.file:
                            slot_evidence["file"] = item.file
                        if item.role_hint:
                            slot_evidence.update({
                                "role": item.role_hint,
                                "role_source": "mod_slot_mapping",
                            })
                        draw.asset_slot_evidence.append(slot_evidence)

            for item in evidence:
                if (item.texture_hash in draw.texture_hashes.get(item.role, [])
                        and item.role not in draw.texture_provenance):
                    draw.texture_provenance[item.role] = "mod_texture_hash"
            _apply_slot_hashes(draw, evidence)
            conflicting_asset_roles = {
                item.get("asset_hash_role")
                for item in draw.asset_slot_evidence
                if item.get("conflict") and item.get("asset_hash_role")
            }
            for item in evidence:
                if item.role in conflicting_asset_roles:
                    continue
                if _has_mod_texture(draw, item.role):
                    continue
                filename = _locate_texture(binding, item)
                if not filename:
                    continue
                draw.asset_texture_defaults[item.role] = {
                    "path": filename,
                    "key": _logical_key(binding, filename),
                }
                draw.texture_provenance[item.role] = \
                    "asset_original_fallback"

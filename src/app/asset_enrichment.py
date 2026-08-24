"""Lazy, conservative texture evidence for exact Asset component matches."""

from dataclasses import dataclass
import hashlib
import json
import os
import re

from core.geometry_identity import normalize_geometry_hash
from core.ini_parser import TextureOverrideIndex, _condition_difference
from core.dds_classifier import (DDSClassification, classification_cache_key,
                                 classify_dds)
from core.resource_paths import safe_resource_path

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
class TextureSemanticEvidence:
    role: str | None
    texture_hash: str
    extension: str | None = None
    file: str | None = None
    source: str = "mod_semantic"
    texture_class: str | None = None
    confidence: str | None = None
    evidence: tuple[str, ...] = ()
    component_name: str | None = None
    classification: str | None = None


# Compatibility for callers that imported the old evidence name. The
# enrichment path itself uses source-independent semantic evidence.
AssetTextureEvidence = TextureSemanticEvidence


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
        position = positions[0]
        component_name = entry.get("component_name") or entry.get(
            "componentName")
        classification = (
            classifications[position]
            if position < len(classifications)
            and isinstance(classifications[position], str) else None)
        textures = entry.get("texture_hashes")
        if not isinstance(textures, list) or position >= len(textures):
            continue
        for item in textures[position] or []:
            if not isinstance(item, list) or len(item) < 3:
                continue
            role = _ROLE_NAMES.get(str(item[0]).replace("_", "").lower())
            texture_hash = normalize_geometry_hash(item[2])
            extension = item[1] if isinstance(item[1], str) else None
            if texture_hash:
                records.append(AssetTextureEvidence(
                    role, texture_hash, extension,
                    component_name=component_name,
                    classification=classification))
        if records:
            return records
    return records


def _locate_texture(binding, evidence):
    asset_dir = _safe_asset_path(binding.root, binding.asset)
    component = binding.component_name or evidence.component_name
    classification = binding.classification or evidence.classification
    if (not asset_dir or not os.path.isdir(asset_dir)
            or not isinstance(component, str) or not component.strip()):
        return None
    extension = (evidence.extension or "").casefold()
    suffix = (component.strip()
              + str(classification or "").strip()
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


def _authored_texture_rules(draw, role):
    rules = [dict(item) for item in draw.texture_rules(role)]
    if rules:
        return rules
    default = draw.texture_default(role)
    return ([{"conditions": [], "file": default}]
            if default else [])


def _texture_variant_list(draw, role):
    return getattr(draw, {
        "diffuse": "texture_variants",
        "normal_map": "normal_map_variants",
        "light_map": "light_map_variants",
        "material_map": "material_map_variants",
    }[role])


def _apply_hash_replacements(draw, evidence, texture_index):
    """Apply exact Asset hash roles to conditional mod replacements."""
    if not isinstance(texture_index, TextureOverrideIndex):
        return
    for item in evidence:
        if not item.role:
            continue
        replacements = texture_index.replacements_by_hash.get(
            item.texture_hash, ())
        if not replacements:
            continue
        existing = _authored_texture_rules(draw, item.role)
        additions = []
        for replacement in replacements:
            if not replacement.file:
                continue
            conditions = replacement.dnf
            protected = existing + additions
            for higher in protected:
                conditions = _condition_difference(
                    conditions, higher.get("conditions") or [])
                if conditions is None:
                    break
            if conditions is None:
                continue
            addition = {
                "conditions": conditions,
                "file": replacement.file,
                "texture_hashes": (replacement.original_hash,),
            }
            additions.append(addition)
        if not additions:
            continue
        if item.role == "diffuse":
            if draw.texture_assignments:
                draw.texture_assignments.extend(additions)
            else:
                _texture_variant_list(draw, item.role).extend(additions)
        else:
            _texture_variant_list(draw, item.role).extend(additions)
        if not draw.texture_default(item.role):
            unconditional = next(
                (rule for rule in additions if not rule["conditions"]), None)
            if unconditional:
                draw.set_texture_default(item.role, unconditional["file"])
        draw.texture_hashes.setdefault(item.role, [])
        for replacement in replacements:
            if replacement.file and replacement.original_hash not in \
                    draw.texture_hashes[item.role]:
                draw.texture_hashes[item.role].append(
                    replacement.original_hash)
        draw.texture_provenance.setdefault(item.role, "mod_texture_hash")


def _unique_evidence(items):
    result = []
    seen = set()
    for item in items:
        key = (item.role, item.texture_hash, item.source)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _cached_dds_classification(path, cache):
    key = classification_cache_key(path)
    if key is None:
        return DDSClassification(None, "unknown", "low", ("unavailable",))
    if key not in cache:
        cache[key] = classify_dds(path)
    return cache[key]


def _classify_replacements(texture_hash, replacements, mod_dir, cache):
    """Classify all files replacing one original hash.

    A single original hash may have conditional variants. A role is accepted
    only when every classified role agrees; unknown variants are tolerated,
    while conflicting roles remain unresolved.
    """
    classifications = []
    by_replacement = {}
    for replacement in replacements:
        result = DDSClassification(None, "unknown", "low", ("unavailable",))
        if (mod_dir and replacement.file
                and str(replacement.file).casefold().endswith(".dds")):
            path = safe_resource_path(mod_dir, replacement.file)
            if path:
                result = _cached_dds_classification(path, cache)
        classifications.append(result)
        by_replacement[id(replacement)] = result
    role_results = [result for result in classifications if result.role]
    roles = {result.role for result in role_results}
    role = (next(iter(roles))
            if len(roles) == 1
            and all(result.confidence == "high" for result in role_results)
            else None)
    return role, by_replacement, len(roles) > 1


def _append_replacement_evidence(draw, usage, replacement, classification,
                                 *, role=None, conflict=False):
    item = {
        **usage,
        "replacement_resource": replacement.resource,
        "replacement_file": replacement.file,
        "replacement_conditions": replacement.dnf,
        "source": "mod_texture_hash",
    }
    if role and not conflict:
        item.update({
            "role": role,
            "role_source": "dds_analysis",
            "texture_class": classification.texture_class,
            "confidence": classification.confidence,
            "evidence": list(classification.evidence),
        })
    elif classification.role:
        item.update({
            "texture_class": classification.texture_class,
            "confidence": classification.confidence,
            "evidence": list(classification.evidence),
            "role_candidate": classification.role,
        })
        if conflict:
            item["conflict"] = True
    elif (classification.texture_class != "unknown"
          or classification.evidence != ("unavailable",)):
        item.update({
            "texture_class": classification.texture_class,
            "confidence": classification.confidence,
            "evidence": list(classification.evidence),
        })
    draw.asset_slot_evidence.append(item)


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
        roles = {item.role for item in matches if item.role}
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
                        "role_source": (
                            binding.role_hint_source or "mod_slot_mapping"),
                        "asset_hash_role": item.role,
                        "conflict": True,
                    })
                continue
            if (roles == {binding.role_hint} and binding.file
                    and not _has_mod_texture(draw, binding.role_hint)):
                draw.set_texture_default(binding.role_hint, binding.file)
                draw.texture_hashes.setdefault(binding.role_hint, []).append(
                    next(iter(matches)).texture_hash)
                provenance = (
                    "mod_slot_legacy"
                    if binding.role_hint_source == "legacy_slot_mapping"
                    else "mod_slot_semantic")
                draw.texture_provenance.setdefault(
                    binding.role_hint, provenance)
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


def apply(groups, bindings, metadata_cache=None, *, include_not_found=False,
          texture_index=None, mod_dir=None, dds_classification_cache=None):
    """Apply Asset diagnostics and exact-component texture evidence.

    A not-found binding is published only when at least one ready index was
    queried.  With no configured or usable index, omitting it preserves the
    legacy no-Asset presentation while the aggregate report explains why
    matching was unavailable.
    """
    metadata_cache = metadata_cache if metadata_cache is not None else {}
    dds_classification_cache = (
        dds_classification_cache
        if dds_classification_cache is not None else {})
    for group, group_bindings in zip(groups, bindings):
        index = group.get("_texture_override_index") or texture_index
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

            evidence = []
            role_hint_evidence = []
            roleless_evidence = []
            dds_evidence = []
            diagnostic_contexts = {}
            draw.asset_slot_evidence = []
            for role in ("diffuse", "normal_map", "light_map", "material_map"):
                if _has_mod_texture(draw, role):
                    draw.texture_provenance.setdefault(role, "mod_semantic")

            known_roles = {
                role for role in ("diffuse", "normal_map", "light_map",
                                  "material_map")
                if _has_mod_texture(draw, role)
            }
            if binding.asset_type in ("GIMI", "ZZMI"):
                evidence = _gimi_evidence(binding, metadata_cache)
                known_roles.update(item.role for item in evidence if item.role)
                roleless_evidence.extend(
                    item for item in evidence if item.role is None)
            if binding.asset_type == "WWMI":
                slots = _wwmi_slot_evidence(binding, metadata_cache)
                for item in draw.slot_textures:
                    for usage in slots.get(item.slot, []):
                        texture_hash = usage["texture_hash"]
                        diagnostic_contexts.setdefault(
                            texture_hash, []).append(usage)
                        slot_evidence = {"resource": item.resource, **usage}
                        if item.file:
                            slot_evidence["file"] = item.file
                        if item.role_hint:
                            slot_evidence.update({
                                "role": item.role_hint,
                                "role_source": (
                                    item.role_hint_source
                                    or "mod_slot_mapping"),
                            })
                            known_roles.add(item.role_hint)
                            # The slot mapping remains diagnostic and the
                            # parsed mod binding remains authoritative. Do
                            # not bridge WWMI TextureUsage hashes into the
                            # replacement/rendering evidence path.
                        draw.asset_slot_evidence.append(slot_evidence)
                for usage_values in slots.values():
                    for usage in usage_values:
                        texture_hash = usage["texture_hash"]
                        contexts = diagnostic_contexts.setdefault(
                            texture_hash, [])
                        if usage not in contexts:
                            contexts.append(usage)
                        if not any(
                                item.get("texture_hash") == texture_hash
                                and item.get("slot") == usage.get("slot")
                                for item in draw.asset_slot_evidence):
                            draw.asset_slot_evidence.append(dict(usage))
                        # WWMI TextureUsage is diagnostic context only. WuWa
                        # render-role recovery is owned by the component
                        # filename + DDS fallback, not Asset JSON.

            # Raw slot hashes provide diagnostic context for every adapter.
            # Only an Asset-proven association may trigger DDS role recovery;
            # an explicit semantic slot hint remains authoritative on its own.
            for item in draw.slot_textures:
                for texture_hash in item.texture_hashes:
                    context = {
                        "resource": item.resource,
                        "slot": item.slot,
                        "texture_hash": texture_hash,
                    }
                    if item.file:
                        context["file"] = item.file
                    contexts = diagnostic_contexts.setdefault(
                        texture_hash, [])
                    if (binding.asset_type != "WWMI"
                            and context not in contexts):
                        contexts.append(context)
                    if item.role_hint:
                        known_roles.add(item.role_hint)
                        role_hint_evidence.append(TextureSemanticEvidence(
                            item.role_hint, texture_hash,
                            source=(item.role_hint_source
                                    or "mod_slot_mapping")))

            authoritative_hashes = {
                item.texture_hash
                for item in evidence + role_hint_evidence
                if item.role
            }
            associations_by_hash = {}
            for item in roleless_evidence:
                if item.texture_hash in authoritative_hashes:
                    continue
                associations_by_hash.setdefault(item.texture_hash, item)
            associations = list(associations_by_hash.values())

            # Classify each replacement variant only for hashes associated
            # with this exact Asset component. No folder-wide scan or name
            # matching is involved.
            classified_by_hash = {}
            if isinstance(index, TextureOverrideIndex):
                for association in associations:
                    texture_hash = association.texture_hash
                    replacements = index.replacements_by_hash.get(
                        texture_hash, ())
                    if replacements:
                        classified_by_hash[texture_hash] = \
                            _classify_replacements(
                                texture_hash, replacements, mod_dir,
                                dds_classification_cache)

                role_hashes = {}
                for texture_hash, (role, _details, conflict) in \
                        classified_by_hash.items():
                    if role and not conflict:
                        role_hashes.setdefault(role, set()).add(texture_hash)
                accepted_hashes = {
                    (role, texture_hash)
                    for role, hashes in role_hashes.items()
                    if role not in known_roles and len(hashes) == 1
                    for texture_hash in hashes
                }
                for association in associations:
                    texture_hash = association.texture_hash
                    replacements = index.replacements_by_hash.get(
                        texture_hash, ())
                    role, details, conflict = classified_by_hash.get(
                        texture_hash, (None, {}, False))
                    accepted = (role, texture_hash) in accepted_hashes
                    contexts = diagnostic_contexts.get(texture_hash) or [{}]
                    for replacement in replacements:
                        classification = details.get(id(replacement))
                        if classification is None:
                            classification = DDSClassification(
                                None, "unknown", "low", ("unavailable",))
                        for context in contexts:
                            _append_replacement_evidence(
                                draw, context, replacement, classification,
                                role=role if accepted else None,
                                conflict=conflict or (
                                    bool(classification.role)
                                    and not accepted))
                    if accepted:
                        role_result = next(
                            (item for item in details.values()
                             if item.role == role), None)
                        dds_evidence.append(TextureSemanticEvidence(
                            role, texture_hash, source="dds_analysis",
                            texture_class=(role_result.texture_class
                                           if role_result else None),
                            confidence=(role_result.confidence
                                        if role_result else None),
                            evidence=(role_result.evidence
                                      if role_result else ())))

            for item in evidence:
                if (item.role
                        and item.texture_hash in draw.texture_hashes.get(
                            item.role, [])
                        and item.role not in draw.texture_provenance):
                    draw.texture_provenance[item.role] = "mod_texture_hash"
            all_evidence = _unique_evidence(
                evidence + role_hint_evidence + dds_evidence)
            _apply_hash_replacements(draw, all_evidence, index)
            _apply_slot_hashes(draw, evidence + role_hint_evidence)
            conflicting_asset_roles = {
                item.get("asset_hash_role")
                for item in draw.asset_slot_evidence
                if item.get("conflict") and item.get("asset_hash_role")
            }
            for item in evidence:
                if not item.role:
                    continue
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

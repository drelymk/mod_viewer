"""Lazy, conservative texture evidence for exact Asset component matches."""

from dataclasses import dataclass, replace
import json
import os
import re

from core.geometry.identity import normalize_geometry_hash
from core.geometry.draw_call import DrawCall
from core.ini.parser import (TextureOverrideIndex, _condition_difference,
                             _condition_group_is_consistent)
from core.ini.dnf import DNF_TRUE, dnf_and, dnf_or

from . import paths as asset_paths
from . import textures as asset_textures
from .package_format import FORMAT, is_viewer_metadata
from .wuwa_texture_names import texture_component_ordinals


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
    component_name: str | None = None
    classification: str | None = None
    # None permits legacy naming; an empty reference is explicitly invalid.
    file_reference: str | None = None


# Preserve the AssetTextureEvidence import name for existing callers.
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
    return asset_paths.safe_asset_path(root, relative) or \
        asset_paths.safe_asset_dir(root, relative)


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
    compact = is_viewer_metadata(raw)
    native_wwmi = compact and binding.asset_type == "WWMI" and raw.get("vb0_hash")
    for entry in _entries(raw):
        if not isinstance(entry, dict):
            continue
        geometry_hash = normalize_geometry_hash(
            (raw.get("vb0_hash") if native_wwmi else None)
            or entry.get("ib") or entry.get("ib_hash")
            or entry.get("geometry_hash") or entry.get("geometryHash"))
        if geometry_hash != binding.geometry_hash:
            continue
        first_values = _values(entry, (
            "object_indexes", "objectIndexes", "first_indices",
            "firstIndices", "first_index", "firstIndex")) or [0]
        classifications = _values(entry, (
            "object_classifications", "objectClassifications",
            "classifications"))
        if native_wwmi:
            first_values = [entry.get("index_offset", 0)]
            classifications = [entry.get("component_name")]
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
            file_reference = None
            if compact and len(item) > 3:
                relative = item[3]
                file_reference = (asset_paths.safe_asset_path(
                    binding.root, os.path.join(os.path.dirname(binding.metadata), relative))
                    if isinstance(relative, str) else None) or ""
            if texture_hash:
                records.append(AssetTextureEvidence(
                    role, texture_hash, extension,
                    component_name=component_name,
                    classification=classification, file_reference=file_reference))
        if records:
            return records
    return records


def _matches_original_texture(binding, evidence, filename, *, require_component=False):
    """Match a file to a role and range independently established by metadata."""
    if evidence.file_reference is not None:
        return bool(evidence.file_reference) and os.path.normcase(filename) == \
            os.path.normcase(evidence.file_reference)
    component = binding.component_name or evidence.component_name
    if component is not None and not isinstance(component, str):
        return False
    component = (component or "").strip()
    classification = str(binding.classification or evidence.classification or "").strip()
    role_suffix = _ROLE_SUFFIXES.get(evidence.role)
    if (not role_suffix or not (component or classification)
            or (require_component and not component)):
        return False
    extension = (evidence.extension or "").casefold()
    suffix = (component + classification + role_suffix).casefold()
    name = os.path.basename(filename).casefold()
    return ((not extension or name.endswith(extension))
            and os.path.splitext(name)[0].endswith(suffix))


def _locate_texture(binding, evidence, candidates):
    """Select a metadata-proven role from the component's picker candidates."""
    asset_dir = _safe_asset_path(binding.root, binding.asset)
    if not asset_dir or not os.path.isdir(asset_dir):
        return None
    wwmi = binding.asset_type == "WWMI"
    directories = asset_textures.texture_asset_directories(binding) if wwmi else ()
    paths = set()
    for candidate in candidates:
        if candidate["source"] != "asset":
            continue
        path = candidate["path"]
        if asset_paths.relative_asset_path(asset_dir, path) is None:
            continue
        if wwmi and (not asset_textures.component_texture_matches(
                path, {0: binding.component_ordinal}, wwmi=True)
                or not any(os.path.normcase(os.path.dirname(path)) == os.path.normcase(directory)
                           for directory in directories)):
            continue
        if _matches_original_texture(binding, evidence, path):
            paths.add(path)
    return next(iter(paths)) if len(paths) == 1 else None


def _logical_key(binding, filename):
    return asset_textures.asset_logical_key(binding.root, filename)


def _hash_evidence(binding, cache):
    filename = _safe_asset_path(binding.root, binding.metadata)
    raw = _json(cache, filename) if filename else None
    if binding.asset_type == "WWMI" and (
            not isinstance(raw, dict) or raw.get("format") != FORMAT or not raw.get("vb0_hash")):
        return []
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


def _mod_texture_conditions(draw, role):
    if draw.texture_default(role):
        return DNF_TRUE
    conditions = []
    for rule in draw.texture_rules(role):
        conditions = dnf_or(conditions, rule.get("conditions") or DNF_TRUE)
    return conditions


def _clear_null_texture_assignments(draw):
    """Leave null states unassigned so labelled Asset defaults can apply."""
    for role in ("diffuse", "normal_map", "light_map", "material_map"):
        rules = draw.texture_rules(role)
        if not any(rule.get("file") is None for rule in rules):
            continue
        remaining = []
        for rule in rules:
            if rule.get("file") is not None:
                remaining.append(rule)
                continue
            kept = []
            for previous in remaining:
                conditions = _condition_difference(
                    previous["conditions"], rule["conditions"])
                if conditions is not None:
                    kept.append({**previous, "conditions": conditions})
            remaining = kept
        default = next((rule["file"] for rule in reversed(remaining)
                        if not rule["conditions"]), None)
        draw.set_texture_default(role, default)
        if role == "diffuse":
            draw.texture_assignments = []
        draw.set_texture_variants(role, remaining)


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
        activation = DNF_TRUE
        if draw.texture_checks is not None:
            activation = []
            for check in draw.texture_checks:
                hashes = check.get("texture_hashes", ())
                if (item.texture_hash in hashes
                        or (not hashes and "resource" in check and check["resource"] is None)):
                    activation = dnf_or(
                        activation, check.get("conditions") or DNF_TRUE)
            if not activation:
                continue
        existing = _authored_texture_rules(draw, item.role)
        additions = []
        for replacement in replacements:
            if not replacement.file:
                continue
            conditions = [group for group in dnf_and(
                replacement.dnf or DNF_TRUE, activation)
                if _condition_group_is_consistent(group)]
            if not conditions:
                continue
            if any(not group for group in conditions):
                conditions = []
            # Authored role bindings outrank hash recovery. Replacements for
            # this hash share one ordered history: later matching writes win.
            for higher in existing:
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


def _apply_slot_hashes(draw, evidence, declarations=(), geometry_hash=None, *, asset_slots=None):
    """Promote one draw's slot history using corroborated component evidence."""
    by_hash = {}
    for item in evidence:
        by_hash.setdefault(item.texture_hash, []).append(item)
    diffuse_slots = {slot for slot, usages in (asset_slots or {}).items()
                     if any(item.role == "diffuse" for usage in usages
                            for item in by_hash.get(usage["texture_hash"], ()))}
    diffuse_slot = next(iter(diffuse_slots)) if len(diffuse_slots) == 1 else None
    component = getattr(draw.asset_binding, "component_ordinal", None)
    component_slots = [item for item in draw.slot_textures
                       if item.slot == diffuse_slot and (
                           item.resource is None or (item.file and (
                               texture_component_ordinals(item.file) is None
                               or asset_textures.component_texture_matches(
                                   item.file, {0: component}, wwmi=True))))]
    # Multiple untracked runtime routes cannot be selected by slot number alone.
    defaults = {item.file for item in component_slots if not item.conditions}
    if len(defaults) > 1:
        component_slots = []
    declared_roles = {}
    for geometry, texture_hash, role, filename in declarations:
        roles = {item.role for item in by_hash.get(texture_hash, ()) if item.role}
        if geometry == geometry_hash and roles == {role}:
            declared_roles.setdefault(filename, set()).add(role)
    slot_roles = {}
    for binding in draw.slot_textures:
        matches = {item for texture_hash in binding.texture_hashes
                   for item in by_hash.get(texture_hash, ())}
        roles = {item.role for item in matches if item.role}
        if binding.file:
            roles.update(declared_roles.get(_texture_file_identity(binding.file), ()))
        if binding.role_hint:
            for item in matches:
                if item.role and item.role != binding.role_hint:
                    draw.asset_slot_evidence.append({
                        "resource": binding.resource, "slot": binding.slot,
                        "texture_hash": item.texture_hash, "role": binding.role_hint,
                        "role_source": binding.role_hint_source or "mod_slot_mapping",
                        "asset_hash_role": item.role, "conflict": True,
                    })
            roles.add(binding.role_hint)
        slot_roles.setdefault(binding.slot, set()).update(roles)
    resolved = {}
    for binding in draw.slot_textures:
        roles = slot_roles[binding.slot] | ({"diffuse"} if binding in component_slots else set())
        if len(roles) != 1 or (binding.resource is not None and not binding.file):
            continue
        role = next(iter(roles))
        if _has_mod_texture(draw, role):
            continue
        conditions = binding.dnf
        if draw.conditions and conditions and not any(
                _condition_group_is_consistent([*left, *right])
                for left in draw.conditions for right in conditions):
            continue
        resolved.setdefault(role, []).append({
            "conditions": conditions, "file": binding.file,
            "texture_hashes": binding.texture_hashes,
        })
    for role, rules in resolved.items():
        unconditional = next((rule for rule in reversed(rules)
                              if not rule["conditions"]), None)
        if unconditional:
            draw.set_texture_default(role, unconditional["file"])
        if len(rules) > 1 or (rules and (rules[0]["conditions"] or rules[0]["file"] is None)):
            draw.set_texture_variants(role, rules)
        draw.texture_hashes[role] = list(dict.fromkeys(
            texture_hash for rule in rules for texture_hash in rule["texture_hashes"]))
        draw.texture_provenance.setdefault(role, "mod_texture_hash")


def _texture_file_identity(filename):
    return os.path.normcase(os.path.normpath(filename.replace("\\", "/")).replace("\\", "/"))


def _texture_declarations(resources, inventory):
    # Exported declarations are evidence only: the draw's exact Asset must
    # independently confirm geometry, texture hash and role, and a bound file
    # must match the declaration. The ordinal is not a shader slot number.
    result = []
    pattern = re.compile(
        r"Resource_([0-9a-f]{8})_\d+_([0-9a-f]{8})_Slot_(Diffuse|NormalMap|LightMap|MaterialMap)$", re.I)
    for name, resource in resources.items():
        match = pattern.fullmatch(name)
        filename = resource.get("filename")
        if not match or not filename:
            continue
        identity = _texture_file_identity(filename)
        if inventory.get(identity, {}).get("source") == "mod":
            result.append((match[1].casefold(), match[2].casefold(),
                           _ROLE_NAMES[match[3].casefold()], identity))
    return result


def _associate_textures(group, records, inventory, filename_matches,
                        asset_names, texture_indexes):
    """Put discovery, slot, hash and filename evidence into one component pool."""
    candidates = {}

    def add_mod(filename, **metadata):
        if not isinstance(filename, str):
            return
        identity = os.path.normpath(filename.replace("\\", "/")).replace("\\", "/")
        identity = os.path.normcase(identity)
        candidate = inventory.get(identity)
        if candidate and candidate["source"] == "mod":
            candidates.setdefault(identity, {**candidate, **metadata})

    for candidate in group.get("texture_candidates", ()):
        add_mod(candidate.get("file"), **{
            key: value for key, value in candidate.items()
            if key not in ("file", "path", "identity", "source", "maps")})
    for filename in group.get("referenced_texture_files", ()):
        add_mod(filename)
    hashes = set()
    for raw_draw in group.get("draws", ()):
        draw = DrawCall.from_mapping(raw_draw, group)
        hashes.update(value for values in draw.texture_hashes.values()
                      for value in values)
        hashes.update(value for role in draw._TEXTURE_PREFIX
                      for rule in draw.texture_rules(role)
                      for value in rule.get("texture_hashes", ()))
        for slot in draw.slot_textures:
            add_mod(slot.file)
            hashes.update(slot.texture_hashes)
    exact_records = [record for record in records
                     if asset_textures.exact_texture_binding(record[1])]
    for _draw, _binding, evidence, slots in exact_records:
        hashes.update(item.texture_hash for item in evidence)
        hashes.update(usage["texture_hash"]
                      for usages in slots.values() for usage in usages)
    for identity, matches in filename_matches.items():
        if id(group) in matches:
            candidates.setdefault(identity, dict(inventory[identity]))
    for index in texture_indexes:
        if not isinstance(index, TextureOverrideIndex):
            continue
        for texture_hash in sorted(hashes):
            for replacement in index.replacements_by_hash.get(texture_hash, ()):
                add_mod(replacement.file)
    associated_assets = set()
    for _draw, binding, evidence, _slots in exact_records:
        wwmi = binding.asset_type == "WWMI"
        if binding in associated_assets:
            continue
        associated_assets.add(binding)
        directories = asset_textures.texture_asset_directories(binding)
        names = ({id(group): binding.component_ordinal} if wwmi else
                 asset_names.get((binding.root, binding.asset), {}))
        for identity, candidate in inventory.items():
            if candidate["source"] != "asset":
                continue
            path = candidate["path"]
            relative = asset_paths.relative_asset_path(binding.root, path)
            if relative is None or identity != os.path.normcase(
                    asset_textures.asset_logical_key(binding.root, path)):
                continue
            if not any((os.path.normcase(os.path.dirname(path)) == os.path.normcase(directory)
                        if wwmi else os.path.commonpath((directory, path)) == directory)
                       for directory in directories):
                continue
            filename = os.path.basename(path)
            matches = asset_textures.component_texture_matches(
                filename, names, wwmi=wwmi)
            if not wwmi and (any(value in filename.casefold() for value in hashes)
                    or any(_matches_original_texture(binding, item, path, require_component=True)
                           for item in evidence)):
                matches.add(id(group))
            if id(group) in matches:
                candidates.setdefault(identity, dict(candidate))
    group["texture_candidates"] = list(candidates.values())


def apply(groups, bindings, metadata_cache=None, *, include_not_found=False,
          texture_index=None, mod_dir=None,
          source=None, inventory=None, texture_indexes=(), game=None):
    """Apply Asset diagnostics and exact-component texture evidence.

    A not-found binding is published only when at least one ready index was
    queried.  With no configured or usable index, omitting it preserves the
    no-Asset presentation while the aggregate report explains why
    matching was unavailable.
    """
    metadata_cache = metadata_cache if metadata_cache is not None else {}
    inventory = (inventory if inventory is not None else
                 asset_textures.collect_texture_inventory(
                     mod_dir, bindings, source=source))
    wwmi = str(getattr(game, "game", game) or "").casefold() == "wuwa"
    names = {id(group): asset_textures.component_texture_name(group, wwmi=wwmi)
             for group in groups}
    filename_matches = {
        identity: asset_textures.component_texture_matches(
            candidate["file"], names, wwmi=wwmi)
        for identity, candidate in inventory.items()
        if candidate["source"] == "mod"}
    asset_names = {}
    original_files = {}
    declarations_by_ini = {}
    for group, group_bindings in zip(groups, bindings):
        for binding in group_bindings:
            if (asset_textures.exact_texture_binding(binding)
                    and binding.asset_type != "WWMI"):
                asset_names.setdefault((binding.root, binding.asset), {})[id(group)] = (
                    binding.component_name or names[id(group)])
    for position, group in enumerate(groups):
        group_bindings = bindings[position] if position < len(bindings) else ()
        index = group.get("_texture_override_index") or texture_index
        resources = group.get("_texture_resources", {})
        if id(resources) not in declarations_by_ini:
            declarations_by_ini[id(resources)] = _texture_declarations(resources, inventory)
        declarations = declarations_by_ini[id(resources)]
        records = []
        for draw, binding in zip(group.get("draws", ()), group_bindings):
            exact = asset_textures.exact_texture_binding(binding)
            evidence = _hash_evidence(binding, metadata_cache) if exact else []
            slots = (_wwmi_slot_evidence(binding, metadata_cache)
                     if exact and binding.asset_type == "WWMI" else {})
            records.append((draw, binding, evidence, slots))
        _associate_textures(
            group, records, inventory, filename_matches, asset_names,
            [index, *texture_indexes])
        for draw, binding, evidence, slots in records:
            located_evidence = []
            for item in evidence:
                cache_key = (id(group), binding, item)
                if item.role and cache_key not in original_files:
                    original_files[cache_key] = _locate_texture(
                        binding, item, group["texture_candidates"])
                located_evidence.append(replace(item, file=original_files.get(cache_key)))
            evidence = located_evidence
            if binding.status == "not_found" and not include_not_found:
                continue
            draw.asset_binding = binding
            if binding.status == "not_found":
                continue
            if (binding.status != "exact"
                    or binding.component_status != "exact"
                    or binding.range_status != "exact"):
                continue

            # Recompute automatic defaults so a newly resolved mod diffuse
            # cannot retain maps from an earlier Asset fallback.
            for role in draw.asset_texture_defaults:
                if draw.texture_provenance.get(role) == "asset_original_fallback":
                    draw.texture_provenance.pop(role)
            draw.asset_texture_defaults.clear()

            role_hint_evidence = []
            draw.asset_slot_evidence = []
            for role in ("diffuse", "normal_map", "light_map", "material_map"):
                if _has_mod_texture(draw, role):
                    draw.texture_provenance.setdefault(role, "mod_semantic")

            if binding.asset_type == "WWMI":
                for item in draw.slot_textures:
                    for usage in slots.get(item.slot, []):
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
                            # The slot mapping remains diagnostic and the
                            # parsed mod binding remains authoritative. Do
                            # not bridge WWMI TextureUsage hashes into the
                            # replacement/rendering evidence path.
                        draw.asset_slot_evidence.append(slot_evidence)
                for usage_values in slots.values():
                    for usage in usage_values:
                        texture_hash = usage["texture_hash"]
                        if not any(
                                item.get("texture_hash") == texture_hash
                                and item.get("slot") == usage.get("slot")
                                for item in draw.asset_slot_evidence):
                            draw.asset_slot_evidence.append(dict(usage))
                        # WWMI TextureUsage supplies candidate hashes and
                        # diagnostics, without inventing rendering roles.

            # Explicit semantic slot hints remain authoritative without
            # inspecting replacement image pixels.
            for item in draw.slot_textures:
                if item.role_hint:
                    role_hint_evidence.extend(
                        TextureSemanticEvidence(
                            item.role_hint, texture_hash,
                            source=(item.role_hint_source or "mod_slot_mapping"))
                        for texture_hash in item.texture_hashes)

            for item in evidence:
                if (item.role
                        and item.texture_hash in draw.texture_hashes.get(
                            item.role, [])
                        and item.role not in draw.texture_provenance):
                    draw.texture_provenance[item.role] = "mod_texture_hash"
            all_evidence = _unique_evidence(
                evidence + role_hint_evidence)
            _apply_slot_hashes(draw, evidence + role_hint_evidence, declarations, binding.geometry_hash,
                               asset_slots=slots)
            _apply_hash_replacements(draw, all_evidence, index)
            _clear_null_texture_assignments(draw)
            # Use the existing Asset defaults only in states with no applicable
            # mod binding. Authored nulls clear the mod assignment.
            diffuse_conditions = _mod_texture_conditions(draw, "diffuse")
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
                role_conditions = _mod_texture_conditions(draw, item.role)
                covered = (role_conditions if item.role == "diffuse" else
                           dnf_or(diffuse_conditions, role_conditions))
                if covered and _condition_difference([], covered) is None:
                    continue
                filename = item.file
                if not filename:
                    continue
                draw.asset_texture_defaults[item.role] = {
                    "path": filename,
                    "key": _logical_key(binding, filename),
                }
                draw.texture_provenance[item.role] = \
                    "asset_original_fallback"
                if item.role != "diffuse" and diffuse_conditions:
                    clear = (_condition_difference(diffuse_conditions, role_conditions)
                             if role_conditions else diffuse_conditions)
                    if clear is not None:
                        draw.set_texture_variants(item.role, [
                            *draw.texture_rules(item.role), {"conditions": clear, "file": None}])

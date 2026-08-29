"""Execution-order scanning of authored draw state."""

import re

from ..geometry.draw_call import AuthoredDrawCall, SlotTextureBinding
from ..geometry.identity import (DrawOccurrence, GeometryMatch,
                                  normalize_geometry_hash)
from .dnf import (DNF_TRUE, build_bool_alias_map, dnf_and, dnf_not, dnf_or,
                  normalize_dnf, parse_condition_dnf)
from .menu import extract_menu_var_names
from .state import extract_state_rules
from .toggles import extract_toggle_var_names
from .texture_roles import (
    TextureOverrideIndex,
    _collect_structural_slot_role_hints, _collect_texture_override_index,
    _effective_role_assignments, _legacy_texture_evidence,
    _semantic_texture_role,
)
from .sections import line_source


_RUN_SKIP_PREFIXES = (
    "TextureOverride", "ShaderOverride", "Resource", "Present", "Key",
    "Constants")


class _ScannedSections(dict):
    """Dict-compatible scan result carrying its one texture index."""

    def __init__(self, *args, texture_override_index=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.texture_override_index = texture_override_index


def _run_target_name(line, section_lookup):
    """Return a traversable ``run =`` target using scanner rules."""
    match = re.match(r"run\s*=\s*(\S+)", line, re.I)
    if not match:
        return None
    target_name = section_lookup.get(match.group(1).lower())
    target_low = target_name.lower() if target_name else ""
    if (not target_name
            or any(target_low.startswith(prefix.lower())
                   for prefix in _RUN_SKIP_PREFIXES)):
        return None
    return target_name


def _reachable_execution_sections(sections, root, section_lookup):
    """Return one root section and its traversable command-list closure."""
    reachable = []
    visiting = {root}
    pending = [root]
    while pending:
        current = pending.pop()
        reachable.append(current)
        for raw in sections[current]:
            line = raw.split(";", 1)[0].strip()
            target_name = _run_target_name(line, section_lookup)
            if target_name and target_name not in visiting:
                visiting.add(target_name)
                pending.append(target_name)
    return reachable


def _collect_legacy_scope_roles(sections, root, section_lookup):
    """Infer legacy resource roles within one reachable execution scope."""
    family_slots = {}
    family_roles = {}
    family_anchors = {}
    assigned_resources = {}
    slot_roles = {}
    for section_name in _reachable_execution_sections(
            sections, root, section_lookup):
        for raw in sections[section_name]:
            line = raw.split(";", 1)[0].strip()
            if not line:
                continue
            slot = re.match(
                r"^ps-t(?P<slot>\d+)\s*=\s*(?:ref\s+)?(?P<resource>\S+)",
                line, re.I)
            if not slot:
                continue
            resource = slot.group("resource")
            evidence = _legacy_texture_evidence(
                resource, sections, section_lookup)
            if evidence is None:
                resource_key = str(resource).casefold()
                section_resource = section_lookup.get(resource_key)
                if section_resource is None:
                    continue
                if not any(re.match(r"^filename\s*=\s*\S+", raw, re.I)
                           for raw in sections[section_resource]):
                    continue
                role = None
                family = None
            else:
                role, family = evidence
            slot_number = int(slot.group("slot"))
            resource_key = str(resource).casefold()
            assigned_resources.setdefault(resource_key, set()).add(slot_number)
            if role is None:
                continue
            family_slots.setdefault(family, set()).add(slot_number)
            family_roles.setdefault(family, set()).add(role)
            family_anchors.setdefault(family, set()).add(resource_key)
            slot_roles.setdefault(slot_number, set()).add(role)

    result = {}
    for family, anchors in family_anchors.items():
        slots = set(family_slots.get(family, set()))
        roles = family_roles.get(family, set())
        for resource, resource_slots in assigned_resources.items():
            if (resource in anchors
                    or any(resource.startswith(anchor)
                           for anchor in anchors)):
                slots.update(resource_slots)
        if len(slots) != 1 or len(roles) != 1:
            continue
        slot = next(iter(slots))
        if len(slot_roles.get(slot, ())) != 1:
            continue
        role = next(iter(roles))
        for resource, resource_slots in assigned_resources.items():
            if resource_slots != {slot}:
                continue
            if (resource in anchors
                    or any(resource.startswith(anchor)
                           for anchor in anchors)):
                result[resource] = role
    return result


def gating_var_names(sections, var_prefix=None, *, toggle_keys=None,
                     menu=None, state_rules=None):
    """Return cycle, menu, and safe state variables used as draw gates."""
    toggle_vars = extract_toggle_var_names(
        sections, var_prefix=var_prefix, toggle_keys=toggle_keys)
    menu_vars = extract_menu_var_names(
        sections, var_prefix=var_prefix, menu=menu)
    state_rules = (state_rules if state_rules is not None else
                   extract_state_rules(sections, var_prefix=var_prefix))
    return toggle_vars | menu_vars | {rule["var"] for rule in state_rules}


def _scan_sections_for_draws(sections, var_prefix=None, gating_vars=None):
    """Scan TextureOverride and CommandList execution state into snapshots."""
    toggle_vars = (gating_vars if gating_vars is not None else
                   gating_var_names(sections))
    section_lookup = {str(name).lower(): name for name in sections}
    alias_map = build_bool_alias_map(sections)
    texture_override_index = _collect_texture_override_index(
        sections, toggle_vars, alias_map, var_prefix)
    resource_texture_hashes = texture_override_index.hashes_by_resource
    structural_slot_roles = _collect_structural_slot_role_hints(sections)
    seq_counter = [0]
    scope_legacy_resource_roles = {}

    def geometry_match(info):
        geometry_hash = info.get("_geometry_hash")
        if geometry_hash is None:
            return None
        return GeometryMatch(
            geometry_hash,
            info.get("_match_first_index"),
            info.get("_match_index_count"),
        )

    def slot_snapshot(info):
        result = []
        for slot, resource in sorted(
                info.get("_cur_slot_textures", {}).items()):
            structural_role = structural_slot_roles.get(slot)
            legacy_role = scope_legacy_resource_roles.get(resource.casefold())
            role_hint = structural_role or legacy_role
            result.append(SlotTextureBinding(
                slot=slot,
                resource=resource,
                texture_hashes=resource_texture_hashes.get(
                    resource.casefold(), ()),
                role_hint=role_hint,
                role_hint_source=(
                    "mod_slot_mapping" if structural_role
                    else "legacy_slot_mapping" if legacy_role else None),
            ))
        return result

    def record_texture_assignment(info, role, res, cond_stack, *, source):
        combined = DNF_TRUE
        for frame in cond_stack:
            combined = dnf_and(combined, frame["cur"])
        cond = normalize_dnf(combined, toggle_vars, var_prefix)
        if role == "diffuse":
            if not info["diffuse"]:
                info["diffuse"] = res
            if res not in info["diffuse_pool"]:
                info["diffuse_pool"].append(res)
            state = {
                "variants": info.get("_cur_diffuse_variants") or [],
                "history": info.get("_diffuse_history") or [],
                "chain_key": info.get("_diffuse_chain_key"),
                "last_cond": info.get("_diffuse_last_cond"),
            }
        else:
            state = info["_aux_maps"].setdefault(role, {
                "variants": [], "history": [], "chain_key": None,
                "last_cond": None,
            })
        chain_key = (cond_stack[-1]["seq"] if cond_stack
                     else ("bare", role))
        if chain_key != state["chain_key"]:
            state["variants"] = []
            state["chain_key"] = chain_key
        elif (state["last_cond"] == cond and state["variants"]
              and state["variants"][-1].get("source") == source):
            state["variants"].pop()
        variant = {"res": res, "cond": cond, "source": source}
        texture_hashes = resource_texture_hashes.get(res.casefold(), ())
        if texture_hashes:
            variant["texture_hashes"] = texture_hashes
        state["variants"].append(variant)
        state["last_cond"] = cond
        history = {"res": res, "cond": cond, "source": source}
        if texture_hashes:
            history["texture_hashes"] = texture_hashes
        state["history"].append(history)
        if role == "diffuse":
            info["_cur_diffuse_variants"] = state["variants"]
            info["_diffuse_chain_key"] = state["chain_key"]
            info["_diffuse_last_cond"] = state["last_cond"]
            info["_diffuse_history"] = state["history"]
        if source == "slot":
            info["_texture_provenance"][role] = "mod_slot_semantic"
        elif source == "legacy_slot":
            info["_texture_provenance"][role] = "mod_slot_legacy"

    def aux_snapshot(info):
        return {
            channel: {
                "variants": _effective_role_assignments(
                    state.get("variants") or []),
                "history": _effective_role_assignments(
                    state.get("history") or []),
            }
            for channel, state in info.get("_aux_maps", {}).items()
            if state.get("variants") or state.get("history")
        }

    def texture_provenance_snapshot(info):
        result = dict(info.get("_texture_provenance") or {})
        assignments = {"diffuse": info.get("_diffuse_history") or []}
        assignments.update({
            channel: state.get("history") or []
            for channel, state in info.get("_aux_maps", {}).items()
        })
        for role, history in assignments.items():
            effective = _effective_role_assignments(history)
            source_by_provenance = {
                "mod_slot_semantic": "slot",
                "mod_slot_legacy": "legacy_slot",
            }
            source = source_by_provenance.get(result.get(role))
            if (source and not any(item.get("source") == source
                                   for item in effective)):
                result.pop(role, None)
        return result

    def scan(lines, info, cond_stack, visiting, section_name,
             execution_path=()):
        draw_ordinal = 0
        run_ordinal = 0
        for raw in lines:
            line = raw.split(";")[0].strip()
            if not line:
                continue
            if info["src"] is None:
                info["src"] = line_source(raw)
            low = line.lower()
            match_elif = re.match(r"(?:else\s+if|elif)\s+(.*)$", line, re.I)
            if match_elif:
                if cond_stack:
                    frame = cond_stack[-1]
                    branch = parse_condition_dnf(
                        match_elif.group(1).strip(), alias_map)
                    frame["cur"] = dnf_and(dnf_not(frame["seen"]), branch)
                    frame["seen"] = dnf_or(frame["seen"], branch)
                continue
            if low.startswith("if "):
                branch = parse_condition_dnf(line[3:].strip(), alias_map)
                seq_counter[0] += 1
                cond_stack.append({
                    "cur": branch, "seen": branch, "seq": seq_counter[0]})
                continue
            if low == "else":
                if cond_stack:
                    frame = cond_stack[-1]
                    frame["cur"] = dnf_not(frame["seen"])
                continue
            if low == "endif":
                if cond_stack:
                    cond_stack.pop()
                continue
            match = re.match(r"hash\s*=\s*(\S+)", line, re.I)
            if match:
                info["_geometry_hash"] = normalize_geometry_hash(
                    match.group(1))
            match = re.match(r"match_first_index\s*=\s*(\d+)", line, re.I)
            if match:
                info["_match_first_index"] = int(match.group(1))
            match = re.match(r"match_index_count\s*=\s*(\d+)", line, re.I)
            if match:
                info["_match_index_count"] = int(match.group(1))
            match = re.match(
                r"ps-t(\d+)\s*=\s*(?:ref\s+)?(\S+)", line, re.I)
            if match:
                slot = int(match.group(1))
                resource = match.group(2)
                if resource.lower() == "null":
                    info["_cur_slot_textures"].pop(slot, None)
                else:
                    info["_cur_slot_textures"][slot] = resource
                    structural_role = structural_slot_roles.get(slot)
                    legacy_role = scope_legacy_resource_roles.get(
                        resource.casefold())
                    role = structural_role or legacy_role
                    if role and _semantic_texture_role(resource) is None:
                        record_texture_assignment(
                            info, role, resource, cond_stack,
                            source=("slot" if structural_role
                                    else "legacy_slot"))
            match = re.match(r"vb(\d+)\s*=\s*(?:ref\s+)?(\S+)", line, re.I)
            if match:
                slot = int(match.group(1))
                resource = match.group(2)
                value = None if resource.lower() == "null" else resource
                if slot <= 2 and value and not info[f"vb{slot}"]:
                    info[f"vb{slot}"] = value
                info["_cur_vertex_resources"][slot] = value
            match = re.match(r"ib\s*=\s*(\S+)", line, re.I)
            if match:
                if not info["ib"]:
                    info["ib"] = match.group(1)
                info["_cur_ib"] = match.group(1)
            if re.match(r"handling\s*=\s*skip\b", line, re.I):
                info["handling_skip"] = True
            match = re.fullmatch(
                r"drawindexed\s*=\s*(\d+)\s*,\s*(\d+)\s*,\s*(-?\d+)\s*",
                line, re.I)
            if match:
                occurrence = DrawOccurrence(
                    section_name, draw_ordinal, execution_path)
                draw_ordinal += 1
                combined = DNF_TRUE
                for frame in cond_stack:
                    combined = dnf_and(combined, frame["cur"])
                conditions = normalize_dnf(combined, toggle_vars, var_prefix)
                info["draws"].append(AuthoredDrawCall(
                    count=int(match.group(1)), start=int(match.group(2)),
                    base=int(match.group(3)), conditions=conditions,
                    source=line_source(raw),
                    occurrence=occurrence,
                    index_resource=info.get("_cur_ib"),
                    diffuse_variants=_effective_role_assignments(
                        info.get("_cur_diffuse_variants") or []),
                    diffuse_history=_effective_role_assignments(
                        info.get("_diffuse_history") or []),
                    vertex_resources=dict(info["_cur_vertex_resources"]),
                    auxiliary_maps=aux_snapshot(info),
                    texture_provenance=texture_provenance_snapshot(info),
                    geometry_match=geometry_match(info),
                    slot_textures=slot_snapshot(info),
                ))
            semantic = re.match(
                r"^(Resource[\\/]"
                r"(?:GIMI|ZZMI|RabbitFX|WWMI)[\\/]"
                r"(?:Diffuse|NormalMap|LightMap|MaterialMap|GlowMap))"
                r"\s*=\s*(?:ref\s+)?(\S+)", line, re.I)
            if semantic:
                role = _semantic_texture_role(semantic.group(1))
                if role:
                    record_texture_assignment(
                        info, role, semantic.group(2), cond_stack,
                        source="semantic")
            run_match = re.match(r"run\s*=\s*(\S+)", line, re.I)
            if run_match:
                current_run = run_ordinal
                run_ordinal += 1
                target_name = _run_target_name(line, section_lookup)
                if target_name and target_name not in visiting:
                    visiting.add(target_name)
                    scan(sections[target_name], info, cond_stack, visiting,
                         target_name,
                         execution_path + ((section_name, current_run),))
                    visiting.discard(target_name)

    scanned = _ScannedSections(texture_override_index=texture_override_index)
    for name, lines in sections.items():
        name_low = name.lower()
        if not (name_low.startswith("textureoverride")
                or name_low.startswith("commandlist")):
            continue
        scope_legacy_resource_roles = _collect_legacy_scope_roles(
            sections, name, section_lookup)
        info = {
            "vb0": None, "vb1": None, "vb2": None, "ib": None,
            "draws": [], "diffuse": None, "diffuse_pool": [], "src": None,
            "handling_skip": False, "_cur_diffuse_variants": [],
            "_diffuse_chain_key": None, "_diffuse_history": [],
            "_aux_maps": {}, "_texture_provenance": {},
            "_cur_vertex_resources": {}, "_cur_slot_textures": {},
            "_geometry_hash": None, "_match_first_index": None,
            "_match_index_count": None,
        }
        scan(lines, info, [], {name}, name)
        info.pop("_cur_ib", None)
        info["diffuse_variants_at_end"] = _effective_role_assignments(
            info.get("_cur_diffuse_variants") or [])
        info["diffuse_history_at_end"] = _effective_role_assignments(
            info.get("_diffuse_history") or [])
        info["aux_maps_at_end"] = aux_snapshot(info)
        info["texture_provenance_at_end"] = texture_provenance_snapshot(info)
        info["geometry_match_at_end"] = geometry_match(info)
        info["vertex_resources_at_end"] = dict(
            info.get("_cur_vertex_resources") or {})
        info.pop("_cur_vertex_resources", None)
        info["slot_textures_at_end"] = slot_snapshot(info)
        for key in (
                "_cur_slot_textures", "_cur_diffuse_variants",
                "_diffuse_chain_key", "_diffuse_last_cond",
                "_diffuse_history", "_aux_maps", "_texture_provenance"):
            info.pop(key, None)
        scanned[name] = info
    return scanned


__all__ = [
    "gating_var_names", "_scan_sections_for_draws",
    "_reachable_execution_sections",
]

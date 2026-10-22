"""Execution-order scanning of authored draw state."""

import re
from dataclasses import replace

from .draw_arguments import immutable_draw_constants, resolve_drawindexed
from .animation_clocks import frame_condition
from .command_phases import parse_command_line, run_target_phases

from ..geometry.draw_call import AuthoredDrawCall, SlotTextureBinding
from ..geometry.identity import (DrawOccurrence, GeometryMatch,
                                  normalize_geometry_hash)
from .dnf import (DNF_TRUE, build_bool_alias_map, dnf_and, dnf_not, dnf_or,
                  normalize_dnf, parse_condition_dnf)
from .menu import extract_menu_var_names
from .state import extract_state_rules
from .toggles import extract_toggle_var_names
from .texture_roles import (
    _collect_structural_slot_role_hints, _collect_texture_override_index,
    _effective_role_assignments, _legacy_texture_evidence,
    _semantic_texture_role,
    _condition_difference, _condition_group_is_consistent,
)
from .sections import canonical_var_names, line_source


_RUN_SKIP_PREFIXES = (
    "TextureOverride", "ShaderOverride", "Resource", "Present", "Key",
    "Constants")
_WWMI_BONE_OFFSET_RE = re.compile(
    r"^\$\\WWMIv1\\vg_offset\s*=\s*(\d+)\s*$", re.I)


class _ScannedSections(dict):
    """Dict-compatible scan result carrying its one texture index."""

    def __init__(self, *args, texture_override_index=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.texture_override_index = texture_override_index
        self.global_compute_resources = {}


def _run_target_name(line, section_lookup):
    """Return a traversable ``run =`` target using scanner rules."""
    match = re.match(r"run\s*=\s*(\S+)", parse_command_line(line).text, re.I)
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
            line = parse_command_line(raw).text
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
            line = parse_command_line(raw).text
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
        family_resources = {
            resource: resource_slots
            for resource, resource_slots in assigned_resources.items()
            if (resource in anchors
                or any(resource.startswith(anchor) for anchor in anchors))
        }
        for resource_slots in family_resources.values():
            slots.update(resource_slots)
        if not slots or len(roles) != 1:
            continue
        role = next(iter(roles))
        # Duplicate slot bindings are safe only when every family member uses
        # the same slot set and all of those slots agree on the role.
        if (any(resource_slots != slots
                for resource_slots in family_resources.values())
                or any(slot_roles.get(slot) != {role} for slot in slots)):
            continue
        for resource in family_resources:
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


def _split_animation_conditions(conditions, animation_vars):
    """Separate known frame clauses from ordinary visibility clauses."""
    known = {str(value).casefold() for value in animation_vars or ()}
    ordinary, animated = [], []
    for group in conditions:
        normal_group = [clause for clause in group
                        if clause.get("var", "").casefold() not in known]
        animation_group = [clause for clause in group
                           if clause.get("var", "").casefold() in known]
        if normal_group not in ordinary:
            ordinary.append(normal_group)
        if animation_group not in animated:
            animated.append(animation_group)
    # A condition that contained only an animation clause is unconditional from
    # the viewer's visibility perspective. Preserve the frame branch separately.
    if any(not group for group in ordinary):
        ordinary = []
    return ordinary, animated


def _condition_path(cond_stack):
    """Retain branch syntax, including runtime and namespaced conditions."""
    return tuple(tuple(frame["branches"]) for frame in cond_stack)


def _buffer_resource_at_draw(history, conditions, fallback):
    """Use the latest assignment implied by the draw, not an unknown write."""
    return next((item["resource"] for item in reversed(history)
                 if _condition_difference(conditions, item["conditions"]) is None), fallback)


def _index_draws_at_join(authored, history, visibility_vars, vertex_history,
                         animation_vars=()):
    """Resolve conditional IB and direct VB snapshots at the authored draw."""
    if any(authored.animation_conditions):
        return [authored]
    vertex_histories = {}
    for item in vertex_history:
        vertex_histories.setdefault(item["slot"], []).append(item)
    candidates = {}
    for item in [*history, *vertex_history]:
        _, animated = _split_animation_conditions(item["conditions"], animation_vars)
        candidate = frame_condition(animated, animation_vars)
        if candidate is not None:
            candidates[candidate] = animated
    # Partition ordinary guards only. Frame writes are an ordered program on
    # this draw, not independent renderable alternatives.
    def ordinary(item):
        conditions, _ = _split_animation_conditions(item["conditions"], animation_vars)
        return {**item, "conditions": conditions}

    ordinary_history = [ordinary(item) for item in history]
    ordinary_vertices = {
        slot: [ordinary(item) for item in vertex_histories.get(slot, ())]
        for slot in authored.vertex_resources}
    draws = [authored]
    for assignments in [ordinary_history, *ordinary_vertices.values()]:
        resolved = []
        for draw in draws:
            remaining = draw.binding_conditions
            branches = []
            for item in reversed(assignments):
                applicable = [group for group in dnf_and(
                    remaining or DNF_TRUE, item["conditions"] or DNF_TRUE)
                    if _condition_group_is_consistent(group)]
                if not applicable:
                    continue
                unknown = normalize_dnf(item["conditions"], {
                    clause["var"] for group in item["conditions"] for clause in group
                    if clause["var"] not in visibility_vars})
                if unknown and _condition_difference(draw.binding_conditions, unknown) is not None:
                    # An unknown write cannot prove a replacement; retain earlier
                    # supported alternatives instead of the last scanned resource.
                    continue
                branches.append(replace(
                    draw, binding_conditions=applicable,
                    conditions=normalize_dnf(applicable, visibility_vars)))
                remaining = _condition_difference(remaining, item["conditions"])
                if remaining is None:
                    break
            else:
                if remaining is not None:
                    branches.append(replace(
                        draw, binding_conditions=remaining,
                        conditions=normalize_dnf(remaining, visibility_vars)))
            resolved.extend(reversed(branches))
        draws = resolved
    # Select every binding from the same guard. An absent slot stays untouched;
    # an explicit null remains an occupied slot.
    missing = object()
    for draw in draws:
        draw.conditions, draw.animation_conditions = _split_animation_conditions(
            draw.conditions, animation_vars)
        draw.index_resource = _buffer_resource_at_draw(
            ordinary_history, draw.binding_conditions, None
        ) if history else authored.index_resource
        draw.vertex_resources = {}
        for slot, assignments in ordinary_vertices.items():
            value = _buffer_resource_at_draw(
                assignments, draw.binding_conditions, missing)
            if value is not missing:
                draw.vertex_resources[slot] = value
        draw.animation_bindings = _animation_binding_snapshots(
            draw, history, vertex_histories, candidates)
        if draw.animation_bindings:
            first = draw.animation_bindings[0]
            draw.index_resource = first["index_resource"]
            draw.vertex_resources = first["vertex_resources"]
    return draws


def _animation_binding_snapshots(authored, history, vertex_histories, candidates):
    """Capture compact frame selections without copying authored draw state."""
    result = []
    missing = object()
    for _, animated in sorted(candidates.items()):
        conditions = [group for group in dnf_and(
            authored.binding_conditions or DNF_TRUE, animated)
            if _condition_group_is_consistent(group)]
        if not conditions:
            continue
        vertices = dict(authored.vertex_resources)
        for slot, assignments in vertex_histories.items():
            value = _buffer_resource_at_draw(
                assignments, conditions, missing)
            if value is not missing:
                vertices[slot] = value
            else:
                vertices.pop(slot, None)
        result.append({
            "animation_conditions": animated,
            "binding_conditions": conditions,
            "index_resource": (_buffer_resource_at_draw(history, conditions, None)
                               if history else authored.index_resource),
            "vertex_resources": vertices,
        })
    return result


def _scan_sections_for_draws(sections, var_prefix=None, gating_vars=None,
                             animation_vars=None, qualified_vars=None, *,
                             resources=None, condition_aliases=None,
                             canonical_vars=None):
    """Scan TextureOverride and CommandList execution state into snapshots."""
    toggle_vars = (gating_vars if gating_vars is not None else
                   gating_var_names(sections))
    animation_vars = set(animation_vars or ())
    tracked_vars = set(toggle_vars) | animation_vars
    canon = (canonical_vars if canonical_vars is not None
             else canonical_var_names(sections))
    vertex_vars = tracked_vars | set(canon.values())
    visibility_vars = {f"{var_prefix or ''}{name}" for name in tracked_vars}
    visibility_vars.update((qualified_vars or {}).values())
    section_lookup = {str(name).lower(): name for name in sections}
    commands = {name: tuple(command for raw in lines
                            if (command := parse_command_line(raw)).text)
                for name, lines in sections.items()}
    alias_map = (condition_aliases if condition_aliases is not None
                 else build_bool_alias_map(sections))
    draw_constants = immutable_draw_constants(sections)
    texture_override_index = _collect_texture_override_index(
        sections, toggle_vars, alias_map, var_prefix, qualified_vars)
    if resources is not None:
        texture_override_index = texture_override_index.with_resource_files(
            resources)
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
        cond = normalize_dnf(
            combined, toggle_vars, var_prefix, qualified_vars)
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

    def scan(info, cond_stack, visiting, section_name, phase,
             execution_path=()):
        draw_ordinal = 0
        run_ordinal = 0
        for command in commands[section_name]:
            raw, line = command.raw, command.text
            if not line:
                continue
            # Ordinals describe authored commands, independent of execution
            # phase. Filtering first would give pre/post draws the same ID.
            current_draw = draw_ordinal
            if re.fullmatch(r"drawindexed\s*=\s*(.*)", line, re.I):
                draw_ordinal += 1
            current_run = run_ordinal
            if re.match(r"run\s*=\s*(\S+)", line, re.I):
                run_ordinal += 1
            if not command.runs_in(phase):
                continue
            if info["src"] is None:
                info["src"] = line_source(raw)
            low = line.lower()
            bone_offset = _WWMI_BONE_OFFSET_RE.match(line)
            if bone_offset:
                info["_cur_skinning_bone_offset"] = int(
                    bone_offset.group(1))
            match_elif = re.match(r"(?:else\s+if|elif)\s+(.*)$", line, re.I)
            if match_elif:
                if cond_stack:
                    frame = cond_stack[-1]
                    branch = parse_condition_dnf(
                        match_elif.group(1).strip(), alias_map)
                    frame["cur"] = dnf_and(dnf_not(frame["seen"]), branch)
                    frame["seen"] = dnf_or(frame["seen"], branch)
                    frame["branches"].append(line)
                continue
            if low.startswith("if "):
                branch = parse_condition_dnf(line[3:].strip(), alias_map)
                seq_counter[0] += 1
                cond_stack.append({
                    "cur": branch, "seen": branch, "seq": seq_counter[0],
                    "branches": [line]})
                continue
            if low == "else":
                if cond_stack:
                    frame = cond_stack[-1]
                    frame["cur"] = dnf_not(frame["seen"])
                    frame["branches"].append(line)
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
                    # Manual candidates retain every authored branch, independent
                    # of the effective slot snapshot and semantic role bindings.
                    info["referenced_textures"].setdefault(
                        resource.casefold(), resource)
                    structural_role = structural_slot_roles.get(slot)
                    legacy_role = scope_legacy_resource_roles.get(
                        resource.casefold())
                    role = structural_role or legacy_role
                    if role and _semantic_texture_role(resource) is None:
                        record_texture_assignment(
                            info, role, resource, cond_stack,
                            source=("slot" if structural_role
                                    else "legacy_slot"))
            match = re.match(
                r"cs-t(\d+)\s*=\s*(?:(?:ref|copy)\s+)?(\S+)",
                line, re.I)
            if match:
                slot = int(match.group(1))
                resource = match.group(2)
                info["_cur_compute_resources"][slot] = (
                    None if resource.lower() == "null" else resource)
            match = re.match(r"vb(\d+)\s*=\s*(?:ref\s+)?(\S+)", line, re.I)
            if match:
                slot = int(match.group(1))
                resource = match.group(2)
                value = None if resource.lower() == "null" else resource
                if cond_stack:
                    info["vertex_bindings_conditional"] = True
                combined = DNF_TRUE
                for frame in cond_stack:
                    combined = dnf_and(combined, frame["cur"])
                info["vertex_binding_history"].append({
                    "slot": slot, "resource": value,
                    "conditions": normalize_dnf(
                        combined, vertex_vars, var_prefix, qualified_vars),
                })
                if slot == 0 and value and animation_vars:
                    # Command lists often put the animated vb0 assignment in
                    # an ``elif DRAW_TYPE`` branch.  Unknown runtime branch
                    # expressions are represented as a false DNF by the
                    # conservative parser, which would otherwise erase the
                    # known ``$swapvar == N`` frame condition.  Keep only
                    # tracked clauses for this metadata and treat an
                    # untracked branch as unconstrained.
                    combined = DNF_TRUE
                    for frame in cond_stack:
                        current = normalize_dnf(
                            frame["cur"], tracked_vars,
                            qualified_vars=qualified_vars)
                        if current:
                            combined = dnf_and(combined, current)
                    conditions = normalize_dnf(
                        combined, tracked_vars, var_prefix, qualified_vars)
                    public_animation_vars = {
                        f"{var_prefix or ''}{item}"
                        for item in animation_vars
                    }
                    conditions, animation_conditions = \
                        _split_animation_conditions(
                            conditions, public_animation_vars)
                    if animation_conditions:
                        info["animation_vertex_bindings"].append({
                            "slot": slot,
                            "resource": value,
                            "conditions": conditions,
                            "animation_conditions": animation_conditions,
                            "source": line_source(raw),
                        })
                if slot <= 2 and value and not info[f"vb{slot}"]:
                    info[f"vb{slot}"] = value
                info["_cur_vertex_resources"][slot] = value
            match = re.match(r"ib\s*=\s*(\S+)", line, re.I)
            if match:
                if not info["ib"]:
                    info["ib"] = match.group(1)
                info["_cur_ib"] = match.group(1)
                combined = DNF_TRUE
                for frame in cond_stack:
                    combined = dnf_and(combined, frame["cur"])
                info["index_binding_history"].append({
                    "resource": match.group(1),
                    "phase": phase, "conditional": bool(cond_stack),
                    "conditions": normalize_dnf(
                        combined, vertex_vars, var_prefix, qualified_vars),
                })
            match = re.fullmatch(r"drawindexed\s*=\s*(.*)", line, re.I)
            if match:
                occurrence = DrawOccurrence(
                    section_name, current_draw, execution_path)
                automatic = match[1].strip().casefold() == "auto"
                if automatic:
                    info["drawindexed_auto"] = True
                    info["auto_guards"].append(_condition_path(cond_stack))
                arguments = ((None, 0, 0) if automatic else
                             resolve_drawindexed(match[1], draw_constants))
                if arguments is None:
                    info["unresolved_draws"] = True
                    continue
                combined = DNF_TRUE
                for frame in cond_stack:
                    combined = dnf_and(combined, frame["cur"])
                conditions = normalize_dnf(
                    combined, tracked_vars, var_prefix, qualified_vars)
                public_animation_vars = {
                    f"{var_prefix or ''}{value}" for value in animation_vars}
                conditions, animation_conditions = _split_animation_conditions(
                    conditions, public_animation_vars)
                source = line_source(raw)
                if source:
                    source = {
                        **source,
                        "occurrence": occurrence.to_dict(),
                    }
                    if automatic:
                        source.update(phase=phase, conditional=bool(cond_stack))
                        info["auto_sources"].append(source)
                authored = AuthoredDrawCall(
                    count=arguments[0], start=arguments[1],
                    base=arguments[2], conditions=conditions,
                    binding_conditions=normalize_dnf(
                        combined, vertex_vars, var_prefix, qualified_vars),
                    animation_conditions=animation_conditions,
                    source=source,
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
                    skinning_bone_offset=info.get(
                        "_cur_skinning_bone_offset", 0),
                    skinning_remap_resources=dict(
                        info.get("_cur_compute_resources") or {}),
                    slot_textures=slot_snapshot(info),
                )
                info["auto_draws" if automatic else "draws"].extend(_index_draws_at_join(
                    authored, info["index_binding_history"], visibility_vars,
                    info["vertex_binding_history"], public_animation_vars))
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
                target_name = _run_target_name(line, section_lookup)
                if target_name and target_name not in visiting:
                    visiting.add(target_name)
                    for target_phase in run_target_phases(
                            command, target_name, phase):
                        scan(info, list(cond_stack), visiting,
                             target_name, target_phase,
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
            "drawindexed_auto": False, "_cur_diffuse_variants": [],
            "auto_sources": [],
            "auto_draws": [], "auto_guards": [],
            "_diffuse_chain_key": None, "_diffuse_history": [],
            "_aux_maps": {}, "_texture_provenance": {},
            "_cur_vertex_resources": {}, "_cur_slot_textures": {},
            "referenced_textures": {},
            "animation_vertex_bindings": [],
            "vertex_bindings_conditional": False,
            "vertex_binding_history": [],
            "index_binding_history": [],
            "_cur_compute_resources": {},
            "_geometry_hash": None, "_match_first_index": None,
            "_match_index_count": None,
            "_cur_skinning_bone_offset": 0,
        }
        for phase in ("pre", "post"):
            scan(info, [], {name}, name, phase)
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
    global_compute_candidates = {}
    for name, info in scanned.items():
        if not name.lower().startswith("commandlist"):
            continue
        for slot, resource in (info.get("_cur_compute_resources") or {}).items():
            if resource is None:
                continue
            global_compute_candidates.setdefault(slot, set()).add(resource)
    scanned.global_compute_resources = {
        slot: next(iter(candidates))
        for slot, candidates in global_compute_candidates.items()
        if len(candidates) == 1
    }
    return scanned


__all__ = [
    "gating_var_names", "_scan_sections_for_draws",
    "_reachable_execution_sections",
]

"""Assemble independent INI scans into one mod-level semantic graph."""

import os
import re
from dataclasses import dataclass, field

from core.geometry.semantics import deduplicate_draws
from core.editing.present import SECTION_NAME as PRESENT_SECTION
from core.ini.analysis import analyze_ini
from core.ini.control_graph import (
    CONTROL_GRAPH_SCHEMA_VERSION, ControlGraph, build_control_graph,
)
from core.ini.dnf import filter_dnf
from core.ini.menu import attach_menu_images
from core.ini.program import _format_key_combo
from core.ini.variables import VariableId, VariableResolver, source_from_path
from core.materials.game_profile import GameDetection, resolve_game_detection


@dataclass
class ParsedModAnalysis:
    """Named result of one per-INI scan plus the unified control graph."""

    groups: list
    toggles: dict
    menu: dict
    defaults: dict
    state_rules: list
    present: dict
    game: GameDetection
    control_graph: ControlGraph | None = None
    control_projection: dict = field(default_factory=dict)
    actions: list = field(default_factory=list)

    def __iter__(self):
        """Keep old six-value helper callers source-compatible."""
        yield self.groups
        yield self.toggles
        yield self.menu
        yield self.defaults
        yield self.state_rules
        yield self.present


def _attach_shape_effects(groups, shape_effects):
    """Attach every matching morph effect to every compatible position buffer."""
    def path_key(path):
        return os.path.normcase(os.path.normpath(path)) if path else None

    for group in groups:
        position_files = {path_key(group.get("position_file"))}
        position_files.update(path_key(draw.get("position_file"))
                              for draw in group.get("draws", []))
        position_files.discard(None)
        matches = [effect for effect in shape_effects
                   if path_key(effect.get("base_file")) in position_files]
        if matches:
            group["shape_effects"] = matches


def _ini_scope(ini_path, folder_path, multi):
    """Return a compact UI source label, never a semantic variable identity."""
    parent_dir = os.path.dirname(ini_path)
    if os.path.normpath(parent_dir) != os.path.normpath(folder_path):
        source = os.path.relpath(parent_dir, folder_path).replace(os.sep, "/")
    else:
        source = os.path.splitext(os.path.basename(ini_path))[0]
    identity = os.path.splitext(_ini_rel(ini_path, folder_path))[0]
    return (f"{identity}::" if multi else None), source


def _ini_rel(ini_path, folder_path):
    return os.path.relpath(ini_path, folder_path).replace(os.sep, "/")


def _rebase_resources(resources, ini_path, folder_path):
    """Make filenames authored relative to a nested INI root-relative."""
    rel_dir = os.path.relpath(os.path.dirname(ini_path), folder_path)
    if rel_dir == os.curdir:
        return resources
    for info in resources.values():
        filename = info.get("filename")
        if filename:
            info["filename"] = os.path.normpath(os.path.join(rel_dir, filename))
    return resources


def _staged_document(path, documents):
    if not documents:
        return None
    return documents.get(path) or documents.get(
        os.path.normcase(os.path.abspath(path)))


def _source_for(path, folder_path, overrides, documents):
    document = _staged_document(path, documents)
    text = None if document is not None else (overrides or {}).get(path)
    source = source_from_path(path, folder_path, text=text, document=document)
    source.resources = _rebase_resources(source.resources, path, folder_path)
    return source


def _authored_name(graph, variable):
    node = graph.variables.get(variable)
    if node and node.authored_names:
        return sorted(node.authored_names, key=lambda value: (value.casefold(), value))[0]
    return variable.name


def _display_ids(graph, sources, folder_path):
    """Map internal IDs to stable UI state IDs while retaining provenance."""
    multi = len(sources) > 1
    namespaces = {
        source.namespace_key: source.namespace for source in sources
        if source.namespace
    }
    result = {}
    for variable in graph.variables:
        name = _authored_name(graph, variable)
        if variable.kind == "namespace":
            owner = namespaces.get(variable.owner, variable.owner)
            result[variable.key] = f"{owner}/{name}"
            continue
        if not multi:
            result[variable.key] = name
            continue
        owner = variable.owner.split("::", 1)[0]
        result[variable.key] = f"{os.path.splitext(owner)[0]}::{name}"
    return result


def _project_conditions(conditions, ids):
    return [[{**clause, "var": ids.get(clause["var"], clause["var"])}
             for clause in group]
            for group in (conditions or [])]


def _project_source(source, folder_path):
    """Expose source provenance without leaking absolute filesystem paths."""
    if not source:
        return None
    result = dict(source)
    path = result.get("ini_path")
    if path and os.path.isabs(path):
        result["ini_path"] = _ini_rel(path, folder_path)
    return result


def _project_capture_bindings(bindings, ids, folder_path):
    result = []
    for binding in bindings or ():
        item = dict(binding)
        ini = item.get("ini")
        if ini and os.path.isabs(ini):
            item["ini"] = _ini_rel(ini, folder_path)
        control_id = item.get("control_id")
        item["control_id"] = ids.get(control_id, control_id)
        marker = (item.get("ini"), str(item.get("authored_var", "")).casefold(),
                  item.get("control_id"))
        if marker not in {(entry.get("ini"),
                           str(entry.get("authored_var", "")).casefold(),
                           entry.get("control_id")) for entry in result}:
            result.append(item)
    return result


def _project_operation(write, ids):
    """Project only the assignment forms the frontend can execute safely."""
    if write.literal is not None:
        return {"kind": "set", "value": str(write.literal)}
    if write.exact_copy and write.dependencies:
        return {"kind": "copy", "source": ids.get(
            write.dependencies[0].key, write.dependencies[0].key)}
    if write.cycle_values:
        return {"kind": "cycle", "values": [str(value)
                for value in write.cycle_values]}
    expression = re.sub(r"\s+", "", write.expression)
    if re.fullmatch(r"1-\$[a-z0-9_.${}-]+", expression, re.I):
        return {"kind": "toggle"}
    match = re.fullmatch(
        r"\(?\$[a-z0-9_.${}-]+([+-])1\)?(?:%(\d+))?", expression, re.I)
    if match:
        operation = {"kind": "step",
                     "delta": 1 if match.group(1) == "+" else -1}
        if match.group(2):
            operation["modulo"] = int(match.group(2))
        return operation
    match = re.fullmatch(r"\$[a-z0-9_.${}-]+%(\d+)", expression, re.I)
    if match:
        return {"kind": "modulo", "modulo": int(match.group(1))}
    return None


def _project_assignment(write, ids, folder_path=None):
    return {
        "target": ids.get(write.target.key, write.target.key),
        "authored_target": write.authored_target,
        "expression": write.expression,
        "dependencies": [ids.get(item.key, item.key)
                         for item in write.dependencies],
        "literal": write.literal,
        "cycle_values": list(write.cycle_values),
        "conditions": _project_conditions(write.conditions, ids),
        "section": write.section,
        "phase": write.phase,
        "source": _project_source(write.source, folder_path),
        "exact_copy": write.exact_copy,
        "operation": _project_operation(write, ids),
    }


def _project_selector(selector, ids):
    if not selector:
        return None
    result = dict(selector)
    variable = result.get("var")
    key = variable.key if isinstance(variable, VariableId) else variable
    result["var"] = ids.get(key, key)
    result["value"] = str(result.get("value", ""))
    return result


def _selector_names(graph, aliases):
    names = []
    for variable in aliases or ():
        node = graph.variables.get(variable)
        authored = sorted(
            node.authored_names if node and node.authored_names else
            {variable.name},
            key=lambda value: (value.casefold(), value),
        )[0]
        if authored.casefold() not in {item.casefold() for item in names}:
            names.append(authored)
    return names


def _project_action(action, ids, folder_path):
    return {
        "kind": action.kind,
        "trigger": action.trigger,
        "conditions": _project_conditions(action.conditions, ids),
        "writes": [ids.get(variable.key, variable.key)
                   for variable in action.writes],
        "assignments": [_project_assignment(write, ids, folder_path)
                        for write in action.assignments],
        "source": _project_source(action.source, folder_path),
        "selector": _project_selector(action.selector, ids),
        "selector_aliases": [ids.get(variable.key, variable.key)
                             for variable in action.selector_aliases],
        "user_facing": action.user_facing,
        "routing_conditions": _project_conditions(
            action.routing_conditions, ids),
        "routing_selectors": [
            _project_selector(selector, ids)
            for selector in action.routing_selectors
        ],
    }


def _project_provenance(graph, ids, folder_path):
    """Expose source-scoped input/influence evidence for diagnostics."""
    roots = []
    for root in graph.input_roots:
        roots.append({
            "kind": root.kind,
            "section": root.section,
            "source": _project_source(root.source, folder_path),
            "writes": [ids.get(variable.key, variable.key)
                       for variable in root.writes],
            "runs": list(root.runs),
            "influences": [ids.get(variable.key, variable.key)
                           for variable in root.influences],
            "path_conditions": {
                section: _project_conditions(conditions, ids)
                for section, conditions in root.path_conditions
            },
        })
    influences = [
        {
            "source": ids.get(edge.source.key, edge.source.key),
            "target": ids.get(edge.target.key, edge.target.key),
            "kind": edge.kind,
            "source_info": _project_source(edge.source_info, folder_path),
        }
        for edge in graph.influences
    ]
    selector_flow = [
        {
            "source": ids.get(edge.source.key, edge.source.key),
            "target": ids.get(edge.target.key, edge.target.key),
            "conditions": _project_conditions(edge.conditions, ids),
            "source_info": _project_source(edge.source_info, folder_path),
            "scale": edge.scale,
            "offset": edge.offset,
        }
        for edge in graph.selector_flow
    ]
    return {
        "input_roots": roots,
        "influences": influences,
        "modeled_state_variables": [
            ids.get(variable.key, variable.key)
            for variable in sorted(graph.modeled_state_variables,
                                   key=lambda item: item.key)
        ],
        "selector_flow": selector_flow,
    }


def _source_path(source):
    if not source or not source.get("ini_path"):
        return None
    return os.path.normcase(os.path.abspath(source["ini_path"]))


def _selector_identity(selector):
    if not selector:
        return None
    variable = selector.get("var")
    variable = variable.key if isinstance(variable, VariableId) else variable
    return (str(variable).casefold(), str(selector.get("value", "")))


def _action_matches_controller(action, controller):
    """Require action/controller provenance to agree before attaching it."""
    if action.trigger != controller.trigger:
        return False
    action_source = _source_path(action.source)
    controller_source = _source_path(controller.source)
    if action_source and controller_source and action_source != controller_source:
        return False
    if controller.selector:
        return (_selector_identity(action.selector)
                == _selector_identity(controller.selector))
    return action.selector is None


def _action_for_control(graph, control, controllers):
    """Find one unambiguous operation for a projected control.

    A namespaced variable may be shared by several INIs.  Matching only the
    target variable can therefore attach a sibling INI's command list or
    selector.  Return no operation when provenance leaves more than one
    possible answer; the control remains visible but cannot run a guessed
    action.
    """
    candidates = [action for action in graph.actions
                  if control.state_var in action.writes
                  and action.assignments
                  and action.user_facing]
    selector_controllers = [controller for controller in controllers
                            if controller.selector]
    # A slot-specific operation is the most precise menu behavior.  Do not
    # let an unrelated preset action for the same state variable make that
    # slot ambiguous; the preset remains available in the action list.
    controllers = selector_controllers or list(controllers)
    matched = []
    for controller in controllers:
        matched.extend(
            action for action in candidates
            if _action_matches_controller(action, controller))
    unique = []
    for action in matched:
        if action not in unique:
            unique.append(action)
    if len(unique) == 1:
        return unique[0]
    if not matched and len(candidates) == 1:
        # Backward-compatible fallback for old graph producers that omitted
        # controller provenance.  Never use it when candidates are ambiguous.
        return candidates[0]
    return None


def _is_pure_domain_cycle(action, variable):
    """Use a projected controller domain for a one-state cycle action."""
    if (action is None or action.routing_conditions
            or len(action.writes) != 1 or action.writes[0] != variable):
        return False
    operations = [_project_operation(write, {})
                  for write in action.assignments]
    kinds = {operation.get("kind") for operation in operations if operation}
    return (bool(operations) and kinds <= {"set", "step", "toggle", "cycle"}
            and bool(kinds & {"step", "toggle", "cycle"})
            and all(write.target == variable for write in action.assignments))


def _filter_and_project_groups(groups, graph, ids):
    """Drop unmodelled runtime gates only after graph classification."""
    allowed = graph.modeled_state_variables
    allowed_keys = {variable.key for variable in allowed}
    for group in groups:
        for shape in group.get("shape_effects") or []:
            # Packed shape targets share the same public state IDs as draw
            # conditions and control panels.  Keep the graph's internal
            # identity only until the mod-level projection is complete.
            shape["var"] = ids.get(shape.get("var"), shape.get("var"))
        for draw in group.get("draws", []):
            draw.conditions = _project_conditions(
                filter_dnf(draw.conditions, allowed_keys), ids)
            for role in ("diffuse", "normal_map", "light_map",
                         "material_map", "emission_map"):
                for variant in draw.texture_rules(role):
                    variant["conditions"] = _project_conditions(
                        filter_dnf(variant.get("conditions"), allowed_keys), ids)


def _write_values(program, section, variable):
    result = []
    for write in program.writes:
        if write.section.casefold() != section.casefold() or write.target != variable:
            continue
        if write.cycle_values:
            result.extend(write.cycle_values)
        elif write.literal is not None:
            result.append(write.literal)
    # Cycle/PRESENT positions are authored data. Repeated values carry
    # meaning because companion variables must stay aligned by position.
    return result


def _key_projection(program, key, graph, ids, source_label, folder_path,
                    multi, *, semantic_only=False):
    vars_by_id = {}
    selected = (variable for variable in key.writes
                if not semantic_only or variable in graph.control_variables)
    for variable in selected:
        if variable not in graph.variables:
            continue
        values = _write_values(program, key.section, variable)
        if values:
            vars_by_id[ids.get(variable.key, variable.key)] = values
    if not vars_by_id:
        return None
    prefix = f"{os.path.splitext(program.source.relative_path)[0]}::" if multi else ""
    section_key = f"{prefix}{key.section}"
    source = next((line.get("ini_path") for line in [key.source or {}]
                   if line.get("ini_path")), program.source.path)
    return section_key, {
        "name": key.section[3:] if key.section[:3].casefold() == "key" else key.section,
        "key": key.key,
        "key_display": _format_key_combo(key.key),
        "back": key.back,
        "vars": vars_by_id,
        "variable_kinds": {
            ids.get(variable.key, variable.key): variable.kind
            for variable in key.writes if variable in graph.variables
        },
        "source": source_label,
        "ini_path": source,
        "section": key.section,
        "wired": any(variable in graph.control_variables
                      for variable in key.writes),
        "controller_kind": "direct_key",
        # Internal projection metadata used by control-state filtering.  It is
        # consumed before the public panel payload is built.
        "_semantic_ids": [variable.key for variable in key.writes
                           if variable in graph.variables],
    }


def _unified_projection(graph, sources, ids, folder_path,
                        pending_new_sections=None):
    toggles = {}
    menu = {}
    emitted_controls = set()
    pending_new_sections = pending_new_sections or {}
    for program in graph.facts:
        prefix, source_label = _ini_scope(
            program.source.path, folder_path, len(sources) > 1)
        relative = program.source.relative_path
        pending = {
            str(section).casefold()
            for section in pending_new_sections.get(relative, ())
        }
        for key in program.key_inputs:
            if key.section.casefold() == PRESENT_SECTION.casefold():
                # The reserved Present section is capture/editing machinery,
                # never an ordinary Toggle even when it writes render state.
                continue
            is_pending = key.section.casefold() in pending
            item = _key_projection(
                program, key, graph, ids, source_label, folder_path,
                len(sources) > 1, semantic_only=not is_pending)
            if item is None:
                continue
            key_name, info = item
            info["pending"] = is_pending
            if (is_pending or any(controller.kind == "direct_key"
                                  for variable in key.writes
                                  for controller in _controllers_for(graph, variable))):
                toggles[key_name] = info
        for control in graph.controls.values():
            if control.id in emitted_controls:
                continue
            interactive = [controller for controller in control.controllers
                           if controller.kind == "interactive"]
            interactive.extend(
                controller for controller in control.controllers
                if controller.kind == "compound_action")
            if not interactive:
                continue
            controller_path = (interactive[0].source or {}).get("ini_path")
            if not controller_path:
                controller_path = next(
                    (effect.source.get("ini_path")
                     for effect in control.effects if effect.source
                     and effect.source.get("ini_path")), None)
            controller_source = next(
                (candidate for candidate in sources
                 if controller_path and os.path.normcase(
                     os.path.abspath(candidate.path)) == os.path.normcase(
                         os.path.abspath(controller_path))),
                None)
            effective_source_label = source_label
            effective_ini_path = program.source.path
            if controller_source is not None:
                _unused, effective_source_label = _ini_scope(
                    controller_source.path, folder_path, len(sources) > 1)
                effective_ini_path = controller_source.path
            variable_id = ids.get(control.state_var.key, control.state_var.key)
            domain = next(
                (dict(controller.domain) for controller in interactive
                 if controller.domain),
                dict(control.domain),
            )
            info = {
                "name": _authored_name(graph, control.state_var),
                "source": effective_source_label,
                "ini_path": effective_ini_path,
                "section": (interactive[0].source or {}).get(
                    "section", interactive[0].trigger),
                "var": variable_id,
                "domain": domain,
                "controllers": [controller.kind for controller in interactive],
                "capture_bindings": _project_capture_bindings(
                    control.capture_bindings, ids, folder_path),
                "_semantic_id": control.id,
            }
            selectors = [controller.selector for controller in interactive
                         if controller.selector]
            if selectors:
                info["selector"] = _project_selector(selectors[0], ids)
                info["slot"] = info["selector"].get("value")
                selector_family = set()
                for selector in selectors:
                    variable = selector.get("var")
                    selector_family.update(graph.selector_family(variable))
                    if variable is not None:
                        selector_family.add(variable)
                info["_selector_names"] = _selector_names(
                    graph, sorted(selector_family, key=lambda item: item.key))
                info["_selector_flow"] = [
                    ids.get(variable.key, variable.key)
                    for variable in sorted(selector_family,
                                            key=lambda item: item.key)
                ]
                selector_states = set()
                for selector in selectors:
                    selector_states.update(graph.selector_states(selector))
                state_items = (
                    [(state.variable, state.value) for state in selector_states]
                    if selector_states else
                    [(selectors[0]["var"],
                      str(selectors[0].get("value", "")))]
                )
                info["_selector_states"] = []
                for variable, value in sorted(
                        state_items,
                        key=lambda item: (
                            item[0].key if isinstance(item[0], VariableId)
                            else str(item[0]), item[1])):
                    public = (ids.get(variable.key, variable.key)
                              if isinstance(variable, VariableId) else variable)
                    info["_selector_states"].append({
                        "var": public, "value": str(value),
                    })
            operation = _action_for_control(graph, control, interactive)
            if operation is not None and not _is_pure_domain_cycle(
                    operation, control.state_var):
                info["action"] = _project_action(operation, ids, folder_path)
            if domain.get("kind") == "discrete":
                info["values"] = domain.get("values", [])
            else:
                info.update({key: domain.get(key)
                             for key in ("min", "max")})
                info["step"] = 0.01
            key_source = controller_source or program.source
            key = f"{key_source.relative_path}::{control.state_var.name}"
            menu.setdefault(key, info)
            emitted_controls.add(control.id)
    actions = [_project_action(action, ids, folder_path)
               for action in graph.actions]
    return {
        "schema_version": CONTROL_GRAPH_SCHEMA_VERSION,
        "toggles": toggles,
        "menu": menu,
        "actions": actions,
        "provenance": _project_provenance(graph, ids, folder_path),
    }


def _controllers_for(graph, variable):
    control = graph.controls.get(variable.key)
    return control.controllers if control else ()


def _state_rules(graph, ids, folder_path):
    modeled = graph.modeled_state_variables
    modeled_keys = {variable.key for variable in modeled}
    rules = []
    for program in graph.facts:
        for write in program.writes:
            if (write.section.casefold() != "present"
                    or write.phase == "post"
                    or write.target not in modeled):
                continue
            conditions = write.conditions or ()
            if any(clause.get("var") not in modeled_keys
                   for group in conditions for clause in group):
                # A derived value guarded by runtime/framework state cannot
                # be reproduced by the viewer and must stay out of JS state.
                continue
            operation = _project_operation(write, ids)
            if operation is None:
                continue
            rules.append({
                "var": ids.get(write.target.key, write.target.key),
                "value": write.literal,
                "conditions": _project_conditions(conditions, ids),
                "phase": write.phase,
                "source": _project_source(write.source, folder_path),
                "operation": operation,
            })
    return rules


def _present_projection(graph, sources, ids, defaults, folder_path):
    infos = []
    targets = []
    for program in graph.facts:
        rel = program.source.relative_path
        # Capture eligibility is sourced from the unified facts, including
        # direct Key state that may currently gate an unconditional draw. This
        # keeps PRESENT useful without making that state an always-visible UI
        # control.
        local_vars = []
        capture_bindings = []
        forwarded_dependencies = {
            write.dependencies[0]
            for write in program.writes
            if (write.section.casefold() == PRESENT_SECTION.casefold()
                and write.exact_copy and write.dependencies
                and write.dependencies[0].kind == "ini")
        }
        for key in program.key_inputs:
            if key.section.casefold() == PRESENT_SECTION.casefold():
                continue
            for variable in key.writes:
                if variable.kind != "ini" or variable in forwarded_dependencies:
                    continue
                public = ids.get(variable.key, variable.key)
                local_vars.append(public)
                capture_bindings.append({
                    "ini": rel,
                    "authored_var": _authored_name(graph, variable),
                    "control_id": public,
                })
            for action in graph.actions:
                action_source = (action.source or {}).get("ini_path")
                if (action_source != program.source.path
                        or action.trigger not in (key.key, key.section)):
                    continue
                for assignment in action.assignments:
                    if (assignment.target.kind != "ini"
                            or assignment.target in forwarded_dependencies):
                        continue
                    public = ids.get(assignment.target.key,
                                     assignment.target.key)
                    local_vars.append(public)
                    capture_bindings.append({
                        "ini": rel,
                        "authored_var": _authored_name(
                            graph, assignment.target),
                        "control_id": public,
                    })
        for write in program.writes:
            if (write.section.casefold() != PRESENT_SECTION.casefold()
                    or not write.exact_copy or not write.dependencies):
                continue
            dependency = write.dependencies[0]
            if dependency.kind != "ini":
                continue
            control_id = ids.get(write.target.key, write.target.key)
            local_vars.append(control_id)
            capture_bindings.append({
                "ini": rel,
                "authored_var": _authored_name(graph, dependency),
                "control_id": control_id,
            })
        capture_bindings = _project_capture_bindings(
            capture_bindings, ids, folder_path)
        local_vars = list(dict.fromkeys(local_vars))
        targets.append({"value": rel, "label": rel, "vars": local_vars,
                        "capture_bindings": capture_bindings,
                        "has_present": any(
                            key.section.casefold() == PRESENT_SECTION.casefold()
                            for key in program.key_inputs)})
        for key in program.key_inputs:
            if key.section.casefold() != PRESENT_SECTION.casefold():
                continue
            variables = []
            for variable in key.writes:
                values = _write_values(program, key.section, variable)
                if values:
                    public = ids.get(variable.key, variable.key)
                    variables.append({
                        "var": public,
                        "values": values,
                        "default": defaults.get(public, values[0]),
                    })
            if variables:
                counts = {len(item["values"]) for item in variables}
                infos.append({
                    "ini": rel, "source": _project_source(
                        key.source, folder_path),
                    "section": key.section, "key": _format_key_combo(key.key),
                    "key_raw": key.key, "back": key.back, "vars": variables,
                    "capture_vars": local_vars,
                    "capture_bindings": capture_bindings,
                    "count": max(len(item["values"]) for item in variables),
                    "aligned": len(counts) == 1,
                })
    item = None
    if infos:
        first = infos[0]
        counts = {entry["count"] for entry in infos}
        aligned = all(entry["aligned"] for entry in infos) and len(counts) == 1
        item = {
            "inis": [entry["ini"] for entry in infos],
            "target_inis": targets,
            "key": first["key"], "key_raw": first["key_raw"],
            "back": first["back"],
            "vars": [var for entry in infos for var in entry["vars"]],
            "capture_vars": [var for target in targets for var in target["vars"]],
            "capture_bindings": [binding for target in targets
                                 for binding in target["capture_bindings"]],
            "count": first["count"] if aligned else 0,
            "missing_inis": [target["value"] for target in targets
                             if not target["has_present"]],
            "sync_error": None if aligned else (
                "PRESENT keys have different position counts. Edit the INIs "
                "so their cycle lists align."),
        }
    return {"target_inis": targets, "item": item}


def analyze_mod_inis(ini_paths, folder_path, overrides=None, documents=None,
                     pending_new_sections=None):
    """Analyze all selected INIs independently, then build one control graph."""
    ini_paths = list(ini_paths)
    overrides = overrides or {}
    documents = documents or {}
    sources = [_source_for(path, folder_path, overrides, documents)
               for path in ini_paths]
    resolver = VariableResolver(sources)
    groups, programs, all_effects = [], [], []
    game_evidence, runtime_evidence, texture_api_evidence = [], [], []
    seen_labels = {}
    multi = len(sources) > 1

    for source in sources:
        _unused_scope, source_label = _ini_scope(
            source.path, folder_path, multi)
        # analyze_ini performs the same per-source scan with an independent
        # resource table; the resolver identities are already mod-stable.
        analysis = analyze_ini(
            source.sections, resources=source.resources, source=source_label,
            seen=seen_labels, ini_source=source, resolver=resolver)
        for group in analysis.draw_groups:
            group["identity_source"] = source.relative_path
        _attach_shape_effects(analysis.draw_groups, analysis.shapes)
        groups.extend(analysis.draw_groups)
        programs.append(analysis.program)
        all_effects.extend(analysis.render_effects)
        game_evidence.extend(analysis.game_evidence)
        runtime_evidence.extend(analysis.runtime_evidence)
        texture_api_evidence.extend(analysis.texture_api_evidence)
    graph = build_control_graph(programs, all_effects,
                                shape_vars=(
                                    VariableId.from_key(shape["var"])
                                    for program in programs
                                    for shape in _shape_effects(program, all_effects)))
    ids = _display_ids(graph, sources, folder_path)
    _filter_and_project_groups(groups, graph, ids)
    projection = _unified_projection(
        graph, sources, ids, folder_path, pending_new_sections)
    # Image files are optional UI enrichment. They are associated after the
    # semantic graph is complete, so generic graph controls get the same
    # authored menu artwork as the compatibility projection.
    for source in sources:
        entries = {
            key: info for key, info in projection["menu"].items()
            if info.get("ini_path") and os.path.normcase(
                os.path.abspath(info["ini_path"])) == os.path.normcase(
                    os.path.abspath(source.path))
        }
        attach_menu_images(entries, source.sections, source.resources)

    modeled = graph.modeled_state_variables
    defaults = {}
    for node in graph.variables.values():
        if node.id not in modeled:
            continue
        for declaration in node.declarations:
            if declaration.default is not None:
                defaults[ids.get(node.id.key, node.id.key)] = declaration.default
    # An exact namespace forwarding edge makes the source's initial value the
    # effective viewer default.  The model-side declaration is still retained
    # as provenance, but must not overwrite the Menu-side persisted state
    # before the first Present pass runs.
    for control in graph.controls.values():
        target_default = ids.get(control.state_var.key, control.state_var.key)
        node = graph.variables.get(control.state_var)
        for write in node.writes if node else ():
            if not write.exact_copy or not write.dependencies:
                continue
            source_default = defaults.get(
                ids.get(write.dependencies[0].key, write.dependencies[0].key))
            if source_default is not None:
                defaults[target_default] = source_default
                break
    state_rules = _state_rules(graph, ids, folder_path)
    present = _present_projection(graph, sources, ids, defaults, folder_path)
    game = resolve_game_detection(
        game_evidence, runtime_evidence, texture_api_evidence)
    return ParsedModAnalysis(
        groups=groups, toggles={}, menu={}, defaults=defaults,
        state_rules=state_rules, present=present, game=game,
        control_graph=graph, control_projection=projection,
        actions=graph.actions,
    )


def _shape_effects(program, effects):
    source_path = program.source.path
    return [effect.metadata["shape"] for effect in effects
            if effect.kind == "shape" and effect.source
            and effect.source.get("ini_path") == source_path]


def resolved_draws(context, overrides=None):
    """Resolve the current staged draw map once for analysis consumers."""
    parsed = analyze_mod_inis(
        context.ini_paths, context.mod_dir, overrides, context.docs)
    draws = {}
    for group in parsed.groups:
        for draw in deduplicate_draws(group):
            draws.setdefault(draw.label, (draw, group))
    return parsed, draws

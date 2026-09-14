"""Mod-level render/control graph.

The graph deliberately models only the relationships needed by the viewer:
render effects, variable writers, input roots, and conservative domains.  It
does not attempt to execute arbitrary 3DMigoto programs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Iterable, Mapping

from .program import KeyInput, ProgramFacts, VariableWrite
from .variables import VariableId


_RUNTIME_NAMES = {
    "active", "done", "modactive", "mod_enabled", "modenabled",
    "object_detected", "part", "menu", "clickedslot", "hoveredslot",
    "enable_mods", "draw_type", "cursor_x", "cursor_y",
}
_EXTERNAL_ROOTS = {
    "wwmi", "wwmiv1", "zzmi", "zzmiv1", "gimi", "gimiv1", "srmi",
    "srmi1", "srmiiv1", "rabbitfx",
}


@dataclass(frozen=True, slots=True)
class RenderEffect:
    kind: str
    variables: tuple[VariableId, ...]
    target: object = None
    source: dict | None = None
    metadata: Mapping = field(default_factory=dict)


@dataclass(slots=True)
class VariableNode:
    id: VariableId
    authored_names: set[str] = field(default_factory=set)
    declarations: list = field(default_factory=list)
    writes: list[VariableWrite] = field(default_factory=list)
    readers: set[VariableId] = field(default_factory=set)
    render_effects: list[RenderEffect] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Controller:
    kind: str
    source: dict | None = None
    trigger: str = ""
    writes: tuple[VariableId, ...] = ()

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "source": dict(self.source) if self.source else None,
            "trigger": self.trigger,
            "writes": [variable.key for variable in self.writes],
        }


@dataclass(frozen=True, slots=True)
class Action:
    trigger: str
    conditions: tuple = ()
    writes: tuple[VariableId, ...] = ()
    source: dict | None = None
    # Keep the source-ordered assignments so a compound action can be
    # projected without inventing a synthetic state variable.
    assignments: tuple[VariableWrite, ...] = ()

    @property
    def kind(self) -> str:
        return "compound_action" if len(self.writes) > 1 else "interactive"

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "trigger": self.trigger,
            "conditions": [
                [dict(clause) for clause in group]
                for group in (self.conditions or ())
            ],
            "writes": [variable.key for variable in self.writes],
            "assignments": [_write_to_dict(write)
                            for write in self.assignments],
            "source": dict(self.source) if self.source else None,
        }


@dataclass(slots=True)
class Control:
    state_var: VariableId
    domain: dict
    effects: list[RenderEffect] = field(default_factory=list)
    controllers: list[Controller] = field(default_factory=list)
    capture_bindings: list[dict] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.state_var.key


@dataclass(slots=True)
class ControlGraph:
    variables: dict[VariableId, VariableNode] = field(default_factory=dict)
    controls: dict[str, Control] = field(default_factory=dict)
    actions: list[Action] = field(default_factory=list)
    effects: list[RenderEffect] = field(default_factory=list)
    facts: list[ProgramFacts] = field(default_factory=list)
    internal_controllers: dict[str, list[Controller]] = field(default_factory=dict)

    @property
    def control_variables(self) -> set[VariableId]:
        return {control.state_var for control in self.controls.values()}

    def variable_for(self, value) -> VariableId | None:
        if isinstance(value, VariableId):
            return value if value in self.variables else None
        return next((item for item in self.variables if item.key == str(value)), None)

    def to_dict(self) -> dict:
        return {
            "controls": {
                key: {
                    "state_var": control.state_var.key,
                    "domain": dict(control.domain),
                    "effects": [effect.kind for effect in control.effects],
                    "controllers": [controller.to_dict()
                                    for controller in control.controllers],
                    "capture_bindings": list(control.capture_bindings),
                }
                for key, control in self.controls.items()
            },
            "actions": [action.to_dict() for action in self.actions],
            "internal_controllers": {
                key: [controller.to_dict() for controller in controllers]
                for key, controllers in self.internal_controllers.items()
            },
        }


def _var(value) -> VariableId | None:
    return value if isinstance(value, VariableId) else None


def _write_to_dict(write: VariableWrite) -> dict:
    return {
        "target": write.target.key,
        "expression": write.expression,
        "dependencies": [variable.key for variable in write.dependencies],
        "literal": write.literal,
        "cycle_values": list(write.cycle_values),
        "conditions": [
            [dict(clause) for clause in group]
            for group in (write.conditions or ())
        ],
        "section": write.section,
        "phase": write.phase,
        "source": dict(write.source) if write.source else None,
        "authored_target": write.authored_target,
        "exact_copy": write.exact_copy,
    }


def _numeric(value):
    try:
        number = float(str(value))
        return int(number) if number.is_integer() else number
    except (TypeError, ValueError):
        return None


def _merge_values(values: Iterable[str]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        text = str(value)
        if text.casefold() not in seen:
            seen.add(text.casefold())
            result.append(text)
    return result


def _is_runtime(var: VariableId) -> bool:
    return (var.name.casefold() in _RUNTIME_NAMES or
            var.owner.split("/", 1)[0].casefold() in _EXTERNAL_ROOTS or
            var.is_external)


def _condition_vars(conditions) -> set[VariableId]:
    result = set()
    for group in conditions or ():
        for clause in group:
            value = clause.get("var")
            if isinstance(value, VariableId):
                result.add(value)
                continue
            if isinstance(value, str):
                try:
                    result.add(VariableId.from_key(value))
                except ValueError:
                    continue
    return result


def _dedupe(items):
    result = []
    seen = set()
    for item in items:
        marker = repr(item)
        if marker not in seen:
            seen.add(marker)
            result.append(item)
    return result


def _write_domain(var: VariableId, writes: Iterable[VariableWrite],
                  shape: bool = False, dependency_writes=None) -> dict:
    """Infer a small, conservative domain from writer evidence."""
    cycle_values: list[str] = []
    literal_values: list[str] = []
    continuous = shape
    minimum = maximum = None
    step_writes = []
    all_writes = list(writes)
    for write in all_writes + list(dependency_writes or ()):
        cycle_values.extend(write.cycle_values)
        if write.literal is not None:
            literal_values.append(write.literal)
        expression = write.expression.casefold().replace(" ", "")
        if re.fullmatch(r"1-\$[a-z0-9_.${}-]+", expression):
            cycle_values.extend(("0", "1"))
        step = re.fullmatch(r"\(?\$[a-z0-9_.${}-]+([+-])1\)?", expression)
        if step and var.name in expression:
            step_writes.append((write, step.group(1)))
        if re.search(r"\$[^=]+[+\-*/]", expression):
            continuous = continuous or any(token in expression
                                            for token in ("cursor", "range", "dt", "td"))
        if re.search(r"%\s*\d+", write.expression):
            match = re.search(r"%\s*(\d+)", write.expression)
            if match:
                cycle_values.extend(str(i) for i in range(int(match.group(1))))
        for group in write.conditions or ():
            for clause in group:
                if clause.get("var") != var.key:
                    continue
                number = _numeric(clause.get("value"))
                if number is None:
                    continue
                op = clause.get("op")
                if op in ("<", "<="):
                    # Continuous clamps use the authored boundary itself;
                    # only discrete cycle ranges need an integer-exclusive
                    # conversion for ``<``/``>``.
                    candidate = (number if continuous or op == "<="
                                 else number - 1)
                    maximum = (candidate if maximum is None else
                               min(maximum, candidate))
                elif op in (">", ">="):
                    candidate = (number if continuous or op == ">="
                                 else number + 1)
                    minimum = (candidate if minimum is None else
                               max(minimum, candidate))

    # A bounded +/- 1 writer is the generic wrapped discrete form.  The
    # comparison operator is retained on program facts without changing the
    # historical public DNF shape, so this works for both ``<`` and ``>=``
    # reset idioms.
    if step_writes and not cycle_values:
        reset_values = [value for write in all_writes
                        for value in ([write.literal]
                                      if write.literal is not None else [])]
        low = min((_numeric(value) for value in reset_values
                   if _numeric(value) is not None), default=None)
        high = None
        for write in all_writes:
            for group in write.conditions or ():
                for clause in group:
                    if clause.get("var") != var.key:
                        continue
                    number = _numeric(clause.get("value"))
                    if number is None:
                        continue
                    op = clause.get("op")
                    candidate = (number - 1 if op == "<" else number
                                 if op == "<=" else number
                                 if op == ">" else number - 1
                                 if op == ">=" else None)
                    if candidate is not None:
                        high = candidate if high is None else max(high, candidate)
        if low is not None and high is not None and high >= low:
            cycle_values.extend(str(value) for value in range(
                int(low), int(high) + 1))

    if continuous:
        result = {"kind": "continuous"}
        if minimum is not None:
            result["min"] = minimum
        if maximum is not None:
            result["max"] = maximum
        else:
            result.setdefault("max", 1.0 if shape else None)
        result.setdefault("min", 0.0 if shape else None)
        return result

    # An authored finite cycle is the strongest discrete evidence.  Numeric
    # values are kept in authored spelling because frontend state is textual.
    return {"kind": "discrete", "values": _merge_values(
        cycle_values + literal_values)}


def _build_indexes(facts: Iterable[ProgramFacts]):
    writes_by_target: dict[VariableId, list[VariableWrite]] = {}
    reverse: dict[VariableId, set[VariableId]] = {}
    nodes: dict[VariableId, VariableNode] = {}
    for program in facts:
        for declaration in program.declarations:
            node = nodes.setdefault(declaration.var, VariableNode(declaration.var))
            node.declarations.append(declaration)
            if declaration.authored_name:
                node.authored_names.add(declaration.authored_name)
        for write in program.writes:
            node = nodes.setdefault(write.target, VariableNode(write.target))
            node.writes.append(write)
            if write.authored_target:
                node.authored_names.add(write.authored_target)
            writes_by_target.setdefault(write.target, []).append(write)
            for dependency in write.dependencies:
                nodes.setdefault(dependency, VariableNode(dependency)).readers.add(write.target)
                reverse.setdefault(dependency, set()).add(write.target)
    return nodes, writes_by_target, reverse


def _descendants(root: VariableId, reverse: Mapping[VariableId, set[VariableId]]):
    found, pending = set(), [root]
    while pending:
        current = pending.pop()
        if current in found:
            continue
        found.add(current)
        pending.extend(reverse.get(current, ()))
    return found


def _reachable_from_keys(facts: list[ProgramFacts], reverse_runs):
    """Return sections reachable from authored Key/input roots."""
    reachable = set()
    for program in facts:
        lookup = {name.casefold(): name for name in program.source.sections}
        edges = {}
        for edge in program.run_edges:
            edges.setdefault(edge.source_section, set()).add(edge.target_section)
        roots = [item.section for item in program.key_inputs]
        pending = list(roots)
        local = set()
        while pending:
            section = pending.pop()
            if section in local:
                continue
            local.add(section)
            pending.extend(edges.get(section, ()))
        reachable.update((program.source.path, section) for section in local)
    return reachable


def _looks_like_external_input(writes: Iterable[VariableWrite], known_vars):
    """Recognize command lists that have evidence of an external input root.

    A command list with no caller is not automatically a viewer action: many
    mods keep ordinary helper writes in unreferenced sections.  Input-like
    evidence is either a cycle expression or a condition/dependency supplied
    outside the program's declared state (for example ``$choice`` or a
    framework runtime variable).
    """
    for write in writes:
        condition_vars = _condition_vars(write.conditions)
        if any(_is_runtime(variable) or variable not in known_vars
               for variable in condition_vars):
            return True
        if any(_is_runtime(variable) for variable in write.dependencies):
            return True
        expression = write.expression.casefold().replace(" ", "")
        if write.cycle_values:
            return True
        if (re.fullmatch(r"1-\$[a-z0-9_.${}-]+", expression)
                or re.fullmatch(
                    r"\(?\$[a-z0-9_.${}-]+[+-]1\)?(?:%\d+)?",
                    expression)
                or re.fullmatch(r"\$[a-z0-9_.${}-]+%\d+", expression)):
            return True
    return False


def build_control_graph(facts: Iterable[ProgramFacts], effects: Iterable[RenderEffect],
                        shape_vars: Iterable[VariableId] = ()) -> ControlGraph:
    """Build one graph from all independent INI scans."""
    facts = list(facts)
    effects = list(effects)
    graph = ControlGraph(effects=effects, facts=facts)
    nodes, writes_by_target, reverse = _build_indexes(facts)
    graph.variables = nodes
    for effect in effects:
        for var in effect.variables:
            graph.variables.setdefault(var, VariableNode(var)).render_effects.append(effect)

    render_vars = {var for effect in effects for var in effect.variables
                   if not _is_runtime(var)}
    shape_set = set(shape_vars)
    reachable_sections = _reachable_from_keys(facts, reverse)
    incoming_sections = {}
    for program in facts:
        for edge in program.run_edges:
            incoming_sections.setdefault(
                (program.source.path, edge.target_section), set()).add(
                    edge.source_section)

    # A Key is direct only when it writes a semantic state. A Key that merely
    # runs a helper remains an input root for interactive tracing, never a
    # false outfit toggle. We keep the complete map here because a safe
    # Present-derived render flag may be gated by one of these upstream states.
    direct_by_var: dict[VariableId, list[Controller]] = {}
    interactive_by_var: dict[VariableId, list[Controller]] = {}
    actions = []
    for program in facts:
        for key in program.key_inputs:
            direct = tuple(key.writes)
            if direct:
                for var in direct:
                    direct_by_var.setdefault(var, []).append(Controller(
                        "direct_key", key.source, key.key, direct))
            reachable_writes = [write for write in program.writes
                                if write.phase != "post"
                                and (program.source.path, write.section)
                                in reachable_sections]
            changed = tuple(dict.fromkeys(
                target for write in reachable_writes
                for target in _descendants(write.target, reverse)
                if target in render_vars))
            if key.runs and changed:
                action = Action(
                    key.key or key.section, key.conditions, changed,
                    key.source, tuple(reachable_writes))
                actions.append(action)
                controller_kind = (
                    "compound_action" if len(changed) > 1 else "interactive")
                for var in changed:
                    interactive_by_var.setdefault(var, []).append(Controller(
                        controller_kind, key.source, key.key or key.section,
                        changed))

    # Framework menu command lists can be entry points even when their Key
    # trigger is outside the selected INI.  Their writes are still structural
    # evidence for an interactive controller, without relying on slot names.
    for program in facts:
        command_writes = {}
        for write in program.writes:
            if (write.phase != "post"
                    and write.section.casefold().startswith("commandlist")):
                command_writes.setdefault(write.section, []).append(write)
        for section, section_writes in command_writes.items():
            section_key = (program.source.path, section)
            callers = incoming_sections.get(section_key, set())
            if (callers and section_key not in reachable_sections
                    and not any(str(caller).casefold().startswith("key")
                                for caller in callers)):
                # A command list invoked by a TextureOverride or another
                # runtime path is execution machinery, not a user controller.
                continue
            if (not callers and not _looks_like_external_input(
                    section_writes, program.variables)):
                # An unreferenced helper is not evidence of a user action just
                # because it writes a variable that also affects rendering.
                continue
            changed = tuple(dict.fromkeys(
                target for write in section_writes
                for target in _descendants(write.target, reverse)
                if target in render_vars))
            if not changed:
                continue
            source = next((write.source for write in section_writes
                           if write.source), None)
            controller_kind = (
                "compound_action" if len(changed) > 1 else "interactive")
            controller = Controller(controller_kind, source, section, changed)
            actions.append(Action(
                section, (), changed, source, tuple(section_writes)))
            for var in changed:
                interactive_by_var.setdefault(var, []).append(controller)

    graph.actions = _dedupe(actions)
    controls_by_var: dict[VariableId, tuple[list, list]] = {}
    for var in sorted(render_vars, key=lambda item: item.key):
        controllers = []
        controllers.extend(direct_by_var.get(var, ()))
        controllers.extend(interactive_by_var.get(var, ()))
        # A safe derived render flag can be selected by an upstream direct
        # state (for example outfit -> Present piece). Expose the upstream
        # state while retaining the render flag in its effect set.
        upstream = []
        for write in writes_by_target.get(var, ()):
            if write.phase == "post":
                continue
            upstream.extend(_condition_vars(write.conditions))
            if write.exact_copy:
                # Exact namespace forwarding gets its own controller on the
                # render-facing target. The source variable remains a
                # provenance/capture node, not a duplicate UI state.
                if (write.dependencies and
                        any(dep in direct_by_var for dep in write.dependencies)):
                    controllers.append(Controller(
                        "interactive", write.source, write.section, (var,)))
                else:
                    upstream.extend(write.dependencies)
        for parent in upstream:
            if parent in direct_by_var and not _is_runtime(parent):
                controls_by_var.setdefault(parent, ([], []))[0].extend(
                    direct_by_var[parent])
                controls_by_var[parent][1].extend(
                    effect for effect in effects if var in effect.variables)
        controls_by_var.setdefault(var, ([], []))[0].extend(controllers)
        controls_by_var[var][1].extend(
            effect for effect in effects if var in effect.variables)

    # Recognized shape effects are render-facing continuous state even when a
    # mod relies on framework cursor input rather than an ordinary Key block.
    for var in shape_set & render_vars:
        controllers = controls_by_var.setdefault(var, ([], []))[0]
        if not any(controller.kind in ("interactive", "compound_action")
                   for controller in controllers):
            controls_by_var.setdefault(var, ([], []))[0].append(
                Controller("interactive", None, "shape_effect", (var,)))

    for var in sorted(render_vars, key=lambda item: item.key):
        if not controls_by_var.get(var, ([], []))[0]:
            writes = writes_by_target.get(var, [])
            source = next((write.source for write in writes if write.source), None)
            graph.internal_controllers[var.key] = [
                Controller("runtime/internal", source, "", (var,))]

    for var in sorted(controls_by_var, key=lambda item: item.key):
        controllers, var_effects = controls_by_var[var]
        if _is_runtime(var) or not controllers:
            continue
        # A render variable with only an internal/runtime writer is not a
        # viewer control.  Upstream user state is represented by the render
        # variable's control, preserving derived-state replay semantics.
        if not controllers:
            continue
        writes = writes_by_target.get(var, [])
        dependency_writes = [
            dependency_write
            for write in writes
            if write.phase != "post" and write.exact_copy
            for dependency in write.dependencies
            for dependency_write in writes_by_target.get(dependency, ())
        ]
        domain = _write_domain(
            var, writes, shape=var in shape_set,
            dependency_writes=dependency_writes)
        capture_bindings = []
        for write in writes:
            if (write.phase == "post" or not write.exact_copy
                    or not write.dependencies):
                continue
            dependency = write.dependencies[0]
            if dependency.kind != "ini":
                continue
            dependency_node = nodes.get(dependency)
            authored = (sorted(dependency_node.authored_names,
                               key=lambda value: (value.casefold(), value))[0]
                         if dependency_node and dependency_node.authored_names
                         else dependency.name)
            capture_bindings.append({
                "ini": (write.source or {}).get("ini_path"),
                "authored_var": authored,
                "control_id": var.key,
            })
        graph.controls[var.key] = Control(
            state_var=var, domain=domain,
            effects=_dedupe(var_effects),
            controllers=_dedupe(controllers),
            capture_bindings=_dedupe(capture_bindings),
        )
    return graph


__all__ = [
    "Action", "Control", "ControlGraph", "Controller", "RenderEffect",
    "VariableNode", "build_control_graph",
]

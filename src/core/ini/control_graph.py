"""Mod-level render/control graph.

The graph deliberately models only the relationships needed by the viewer:
render effects, variable writers, input roots, and conservative domains.  It
does not attempt to execute arbitrary 3DMigoto programs.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import re
from typing import Iterable, Mapping

from .dnf import DNF_TRUE, dnf_and, dnf_or
from .program import KeyInput, ProgramFacts, VariableWrite
from .variables import VariableId


_RUNTIME_NAMES = {
    "active", "done", "modactive", "mod_enabled", "modenabled",
    "object_detected", "part", "menu", "mouse_clicked", "slot",
    "hoveredslot", "hovered_slot", "clickedslot", "clicked_slot",
    "button_number", "first_run",
    "first_run_done", "merge_status", "merge_status_id",
    "enable_mods", "draw_type", "cursor_x", "cursor_y",
}
_EXTERNAL_ROOTS = {
    "wwmi", "wwmiv1", "zzmi", "zzmiv1", "gimi", "gimiv1", "srmi",
    "srmi1", "srmiiv1", "rabbitfx",
}

# Bump when the serialized control/action contract gains fields or changes
# execution semantics. Consumers may continue accepting older payloads with
# the optional fields below omitted.
CONTROL_GRAPH_SCHEMA_VERSION = 4


@dataclass(frozen=True, slots=True)
class RenderEffect:
    kind: str
    variables: tuple[VariableId, ...]
    target: object = None
    source: dict | None = None
    metadata: Mapping = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class InfluenceEdge:
    """A conservative influence edge in the write/control graph."""

    source: VariableId
    target: VariableId
    kind: str
    source_info: dict | None = None


@dataclass(frozen=True, slots=True)
class SelectorState:
    """A selector variable/value pair reached through UI provenance."""

    variable: VariableId
    value: str


@dataclass(frozen=True, slots=True)
class SelectorFlowEdge:
    """A UI-only path between selector variables.

    This is deliberately separate from semantic aliases: a conditional copy
    or a small affine transform can explain how a menu hit-test value reaches
    an action dispatcher without making the two variables interchangeable
    render state.
    """

    source: VariableId
    target: VariableId
    conditions: tuple = ()
    source_info: dict | None = None
    scale: int = 1
    offset: int = 0

    def to_dict(self) -> dict:
        return {
            "source": self.source.key,
            "target": self.target.key,
            "conditions": [
                [_clause_to_dict(clause) for clause in group]
                for group in (self.conditions or ())
            ],
            "source_info": (dict(self.source_info)
                            if self.source_info else None),
            "scale": self.scale,
            "offset": self.offset,
        }


@dataclass(frozen=True, slots=True)
class InputRoot:
    """An authored input entry point, distinct from a visible control."""

    kind: str
    section: str
    source: dict | None = None
    writes: tuple[VariableId, ...] = ()
    runs: tuple[str, ...] = ()
    influences: tuple[VariableId, ...] = ()
    path_conditions: tuple = ()


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
    selector: dict | None = None
    selector_aliases: tuple[VariableId, ...] = ()
    user_facing: bool = False
    routing_conditions: tuple = ()
    domain: dict | None = None
    routing_selectors: tuple = ()

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "source": dict(self.source) if self.source else None,
            "trigger": self.trigger,
            "writes": [variable.key for variable in self.writes],
            "selector": _selector_to_dict(self.selector),
            "selector_aliases": [variable.key
                                 for variable in self.selector_aliases],
            "user_facing": self.user_facing,
            "routing_conditions": _conditions_to_dict(self.routing_conditions),
            "domain": dict(self.domain) if self.domain else None,
            "routing_selectors": [
                _selector_to_dict(selector)
                for selector in self.routing_selectors
            ],
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
    selector: dict | None = None
    selector_aliases: tuple[VariableId, ...] = ()
    user_facing: bool = False
    routing_conditions: tuple = ()
    routing_selectors: tuple = ()

    @property
    def kind(self) -> str:
        return "compound_action" if len(self.writes) > 1 else "interactive"

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "trigger": self.trigger,
            "conditions": [
                [_clause_to_dict(clause) for clause in group]
                for group in (self.conditions or ())
            ],
            "writes": [variable.key for variable in self.writes],
            "assignments": [_write_to_dict(write)
                            for write in self.assignments],
            "source": dict(self.source) if self.source else None,
            "selector": _selector_to_dict(self.selector),
            "selector_aliases": [variable.key
                                 for variable in self.selector_aliases],
            "user_facing": self.user_facing,
            "routing_conditions": _conditions_to_dict(self.routing_conditions),
            "routing_selectors": [
                _selector_to_dict(selector)
                for selector in self.routing_selectors
            ],
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
    input_roots: list[InputRoot] = field(default_factory=list)
    influences: list[InfluenceEdge] = field(default_factory=list)
    selector_flow: list[SelectorFlowEdge] = field(default_factory=list)

    @property
    def control_variables(self) -> set[VariableId]:
        return {control.state_var for control in self.controls.values()}

    @property
    def modeled_state_variables(self) -> set[VariableId]:
        """Return state the viewer can intentionally reproduce.

        Raw render conditions also mention runtime/framework values.  Those
        values must remain unknown to the browser, so only visible controls
        and safe Present-derived render flags enter the modeled state.
        """
        modeled = set(self.control_variables)
        render_vars = {
            variable
            for effect in self.effects
            for variable in effect.variables
            if not _is_runtime(variable)
        }
        changed = True
        while changed:
            changed = False
            for program in self.facts:
                for write in program.writes:
                    if (write.phase == "post"
                            or write.section.casefold() != "present"
                            or write.target not in render_vars
                            or write.target in modeled):
                        continue
                    if not _condition_vars(write.conditions) <= modeled:
                        continue
                    safe_copy = (write.exact_copy
                                 and len(write.dependencies) == 1
                                 and write.dependencies[0] in modeled)
                    if write.literal is None and not safe_copy:
                        continue
                    modeled.add(write.target)
                    changed = True
        return modeled

    def variable_for(self, value) -> VariableId | None:
        if isinstance(value, VariableId):
            return value if value in self.variables else None
        return next((item for item in self.variables if item.key == str(value)), None)

    def selector_family(self, variable) -> set[VariableId]:
        """Return selector-flow neighbors without changing semantic identity."""
        return _selector_family(variable, self.selector_flow)

    def selector_states(self, selector) -> set[SelectorState]:
        """Resolve a selector value through UI-only copy/affine provenance."""
        if not selector:
            return set()
        variable = selector.get("var")
        if isinstance(variable, str):
            variable = self.variable_for(variable)
        if variable is None:
            return set()
        return _selector_states(variable, str(selector.get("value", "")),
                                self.selector_flow)

    def to_dict(self) -> dict:
        return {
            "schema_version": CONTROL_GRAPH_SCHEMA_VERSION,
            "modeled_state_variables": [
                variable.key for variable in sorted(
                    self.modeled_state_variables, key=lambda item: item.key)
            ],
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
            "input_roots": [
                {
                    "kind": root.kind,
                    "section": root.section,
                    "source": dict(root.source) if root.source else None,
                    "writes": [variable.key for variable in root.writes],
                    "runs": list(root.runs),
                    "influences": [variable.key for variable in root.influences],
                    "path_conditions": {
                        section: [
                            [_clause_to_dict(clause) for clause in group]
                            for group in conditions
                        ]
                        for section, conditions in root.path_conditions
                    },
                }
                for root in self.input_roots
            ],
            "influences": [
                {
                    "source": edge.source.key,
                    "target": edge.target.key,
                    "kind": edge.kind,
                    "source_info": (dict(edge.source_info)
                                    if edge.source_info else None),
                }
                for edge in self.influences
            ],
            "selector_flow": [edge.to_dict() for edge in self.selector_flow],
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
            [_clause_to_dict(clause) for clause in group]
            for group in (write.conditions or ())
        ],
        "section": write.section,
        "phase": write.phase,
        "source": dict(write.source) if write.source else None,
        "authored_target": write.authored_target,
        "exact_copy": write.exact_copy,
        "interaction_selectors": [
            _selector_to_dict(selector)
            for selector in write.interaction_selectors
        ],
        "operation": _write_operation(write),
    }


def _selector_to_dict(selector):
    if not selector:
        return None
    result = dict(selector)
    variable = result.get("var")
    if isinstance(variable, VariableId):
        result["var"] = variable.key
    return result


def _conditions_to_dict(conditions):
    return [
        [_clause_to_dict(clause) for clause in group]
        for group in (conditions or ())
    ]


def _write_operation(write):
    """Serialize the safe assignment vocabulary shared by frontend actions."""
    if write.literal is not None:
        return {"kind": "set", "value": str(write.literal)}
    if write.exact_copy and write.dependencies:
        return {"kind": "copy", "source": write.dependencies[0].key}
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


def _clause_to_dict(clause):
    result = dict(clause)
    variable = result.get("var")
    if isinstance(variable, VariableId):
        result["var"] = variable.key
    return result


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


def _condition_dnf(conditions):
    """Return a mutable DNF view, treating an empty condition as true."""
    if not conditions:
        return DNF_TRUE
    return [list(group) for group in conditions]


def _combine_conditions(left, right):
    """Combine two condition DNFs without losing the true sentinel."""
    return tuple(tuple(group) for group in dnf_and(
        _condition_dnf(left), _condition_dnf(right)))


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
        if any(token in expression for token in (
                "cursor", "mouse", "range", "delta", "lerp", "interpol",
                "dt", "td", "time", "slider")):
            continuous = True
        if re.search(r"\$[^=]+[+\-*/]", expression):
            continuous = continuous or any(token in expression for token in (
                "cursor", "range", "delta", "lerp", "interpol", "dt", "td",
                "time", "slider"))
        if re.search(r"%\s*\d+", write.expression):
            match = re.search(r"%\s*(\d+)", write.expression)
            if match:
                cycle_values.extend(str(i) for i in range(int(match.group(1))))
        # A comparison is a range bound only when this same branch writes the
        # compared boundary back to the same variable.  Conditions by
        # themselves are often menu dispatch or visibility logic and do not
        # describe a slider domain.
        if write.literal is not None:
            literal = _numeric(write.literal)
            if literal is not None:
                for group in write.conditions or ():
                    for clause in group:
                        if clause.get("var") != var.key:
                            continue
                        boundary = _numeric(clause.get("value"))
                        if boundary is None or literal != boundary:
                            continue
                        op = clause.get("op")
                        if op in ("<", "<="):
                            minimum = (boundary if minimum is None else
                                       max(minimum, boundary))
                        elif op in (">", ">="):
                            maximum = (boundary if maximum is None else
                                       min(maximum, boundary))

    # A bounded +/- 1 writer is the generic wrapped discrete form.  It is
    # distinct from continuous clamp inference above: here the reset literal
    # is a different state value and the step is bounded by the branch guard.
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


_AFFINE_VAR = r"\$[a-z0-9_.${}\\-]+"


def _selector_transform(write: VariableWrite):
    """Return ``target = scale * source + offset`` for safe UI copies."""
    if len(write.dependencies) != 1:
        return None
    expression = re.sub(r"\s+", "", write.expression)
    if write.exact_copy or re.fullmatch(_AFFINE_VAR, expression, re.I):
        return 1, 0
    match = re.fullmatch(
        rf"{_AFFINE_VAR}([+-])(-?\d+)", expression, re.I)
    if match:
        amount = int(match.group(2))
        return 1, amount if match.group(1) == "+" else -amount
    match = re.fullmatch(rf"(-?\d+)\+({_AFFINE_VAR})", expression, re.I)
    if match:
        return 1, int(match.group(1))
    return None


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


def _run_closure(program: ProgramFacts, roots: Iterable[str]):
    """Return ``section -> path conditions`` for one set of run roots.

    The old implementation collapsed every Key in an INI into one reachable
    section set.  Keeping conditions per root lets callers build a Key action
    from only its own ``run=`` closure, while still allowing the separate
    input/influence pass to reason about Present-driven execution.
    """
    edges = {}
    for edge in program.run_edges:
        edges.setdefault(edge.source_section.casefold(), []).append(edge)
    paths = {}
    pending = []
    for root in roots:
        key = str(root)
        if key.casefold() not in paths:
            paths[key.casefold()] = tuple(tuple(group) for group in DNF_TRUE)
            pending.append(key)
    while pending:
        section = pending.pop()
        current = paths[section.casefold()]
        for edge in edges.get(section.casefold(), ()):
            path = _combine_conditions(current, edge.conditions)
            target_key = edge.target_section.casefold()
            merged = tuple(tuple(group) for group in dnf_or(
                _condition_dnf(paths.get(target_key)),
                _condition_dnf(path)))
            if target_key not in paths or merged != paths[target_key]:
                paths[target_key] = merged
                pending.append(edge.target_section)
    return paths


def _path_selector_context(conditions):
    """Extract one exact interaction branch inherited through ``run=``."""
    values = {}
    for group in conditions or ():
        for clause in group:
            if (clause.get("negate")
                    or clause.get("op", "==") != "=="
                    or _numeric(clause.get("value")) is None):
                continue
            variable = clause.get("var")
            if isinstance(variable, str):
                try:
                    variable = VariableId.from_key(variable)
                except ValueError:
                    continue
            if isinstance(variable, VariableId):
                values.setdefault(variable, set()).add(
                    str(clause.get("value")))
    return tuple({"var": variable, "value": next(iter(items)),
                  "role": "interaction"}
                 for variable, items in sorted(
                     values.items(), key=lambda item: item[0].key)
                 if len(items) == 1)


def _reachable_from_section(program: ProgramFacts, root_section: str):
    """Compatibility-friendly section closure for one authored root."""
    return _run_closure(program, (root_section,))


def _writes_from_closure(program: ProgramFacts, roots: Iterable[str]):
    paths = _run_closure(program, roots)
    result = []
    for write in program.writes:
        path = paths.get(write.section.casefold())
        if path is None or write.phase == "post":
            continue
        result.append(replace(
            write, conditions=_combine_conditions(path, write.conditions),
            interaction_selectors=_path_selector_context(path)))
    return result


def _exact_alias_map(writes_by_target):
    aliases = {}
    for target, writes in writes_by_target.items():
        for write in writes:
            # Conditional copies are execution-dependent state forwarding,
            # not stable aliases.  Treating them as aliases can connect two
            # unrelated selector branches and attach the wrong artwork.
            if (write.phase != "post" and _conditions_are_true(write.conditions)
                    and write.exact_copy and write.dependencies):
                aliases.setdefault(target, set()).update(write.dependencies)
    return aliases


def _conditions_are_true(conditions):
    """Accept both empty and normalized ``[[]]`` true DNFs."""
    return not conditions or any(not group for group in conditions)


def _alias_family(variable, aliases):
    """Return variables connected by exact-copy forwarding."""
    reverse = {}
    for target, dependencies in aliases.items():
        for dependency in dependencies:
            reverse.setdefault(dependency, set()).add(target)
    found, pending = set(), [variable]
    while pending:
        current = pending.pop()
        if current in found:
            continue
        found.add(current)
        pending.extend(aliases.get(current, ()))
        pending.extend(reverse.get(current, ()))
    return found


def _selector_candidates(writes: Iterable[VariableWrite]):
    values = {}
    for write in writes:
        for group in write.conditions or ():
            for clause in group:
                if (clause.get("negate") or clause.get("op", "==") != "=="
                        or _numeric(clause.get("value")) is None):
                    continue
                variable = clause.get("var")
                if isinstance(variable, str):
                    try:
                        variable = VariableId.from_key(variable)
                    except ValueError:
                        continue
                if isinstance(variable, VariableId):
                    values.setdefault(variable, set()).add(
                        str(clause.get("value")))
    return {
        variable: values_for_var for variable, values_for_var in values.items()
        if len(values_for_var) >= 2
    }


def _unsupported_selector_dispatch(writes: Iterable[VariableWrite],
                                   numeric_candidates):
    """Detect equality dispatch that cannot be represented safely.

    A nonnumeric or multi-variable selector must not silently degrade into a
    whole-CommandList compound action.  That would expose a button whose
    assignments are not tied to one proven branch.
    """
    values = {}
    for write in writes:
        for group in write.conditions or ():
            for clause in group:
                if (clause.get("negate")
                        or clause.get("op", "==") != "=="):
                    continue
                variable = clause.get("var")
                if isinstance(variable, str):
                    try:
                        variable = VariableId.from_key(variable)
                    except ValueError:
                        continue
                if isinstance(variable, VariableId):
                    values.setdefault(variable, set()).add(
                        str(clause.get("value")))
    return any(len(values_for_var) >= 2
               and variable not in numeric_candidates
               for variable, values_for_var in values.items())


def _selector_for_write(write, candidates):
    """Find the innermost simple equality dispatch for one write."""
    matches = []
    for group in write.conditions or ():
        for clause in group:
            variable = clause.get("var")
            if isinstance(variable, str):
                try:
                    variable = VariableId.from_key(variable)
                except ValueError:
                    continue
            if (variable in candidates and not clause.get("negate")
                    and clause.get("op", "==") == "=="):
                matches.append({"var": variable,
                                "value": str(clause.get("value")),
                                "role": "interaction"})
    return matches[-1] if matches else None


def _without_selector(conditions, selector):
    """Remove dispatch clauses from a branch's executable conditions.

    A branch action already represents ``selector == value``.  Requiring the
    hidden dispatcher variable to be present in the frontend state would make
    otherwise valid preset/slot actions fail closed forever.  Other guards
    remain strict and continue to protect side effects.
    """
    if not selector or not conditions:
        return conditions
    selector_var = selector["var"]
    selector_value = str(selector["value"])
    result = []
    for group in conditions:
        kept = []
        for clause in group:
            variable = clause.get("var")
            if isinstance(variable, str):
                try:
                    variable = VariableId.from_key(variable)
                except ValueError:
                    pass
            if (variable == selector_var
                    and str(clause.get("value")) == selector_value):
                continue
            # The negated clauses which make an elif branch exclusive use the
            # same selector variable but a different value; they are also
            # part of dispatch, not a frontend guard.
            if variable == selector_var:
                continue
            kept.append(clause)
        result.append(kept)
    return tuple(tuple(group) for group in result)


def _action_groups(assignments, allow_context=True):
    """Split assignments, preferring inherited interaction selectors.

    A selector carried through a conditional ``run=`` is the user choice for
    the action. Equality branches inside that action are routing context and
    must not become additional menu controls.
    """
    assignments = tuple(assignments)
    if allow_context:
        context_groups = {}
        uncontextualized = []
        for assignment in assignments:
            contexts = {
                (selector.get("var"), str(selector.get("value", ""))): selector
                for selector in assignment.interaction_selectors
            }
            if len(contexts) > 1:
                return None
            if contexts:
                marker, selector = next(iter(contexts.items()))
                context_groups.setdefault(marker, (selector, []))[1].append(
                    assignment)
            else:
                uncontextualized.append(assignment)
        if context_groups:
            result = []
            for selector, branch in context_groups.values():
                candidates = _selector_candidates(branch)
                result.append((
                    {**selector, "role": "interaction"}, tuple(branch),
                    set(candidates) - {selector["var"]}))
            if uncontextualized:
                result.extend(_action_groups(
                    uncontextualized, allow_context=False) or ())
            return result

    candidates = _selector_candidates(assignments)
    if len(candidates) > 1:
        # Independent selectors require a product of branch conditions.  Do
        # not collapse that product into whichever clause happened to be last.
        return None
    if not candidates:
        if _unsupported_selector_dispatch(assignments, candidates):
            return None
        return [(None, assignments, set())]
    groups = {}
    order = []
    positions = {id(assignment): index
                 for index, assignment in enumerate(assignments)}
    for assignment in assignments:
        selector = _selector_for_write(assignment, candidates)
        marker = (selector["var"], selector["value"]) if selector else None
        if marker not in groups:
            groups[marker] = []
            order.append(marker)
        groups[marker].append(assignment)
    common = groups.get(None, ())
    branch_markers = [marker for marker in order if marker is not None]
    if not branch_markers:
        return [(None, assignments, set())]
    result = []
    for marker in branch_markers:
        branch = tuple(sorted(
            tuple(common) + tuple(groups[marker]),
            key=lambda assignment: positions[id(assignment)]))
        result.append((
            {"var": marker[0], "value": marker[1],
             "role": "interaction"}, branch, set()))
    return result


def _is_input_section(section):
    low = str(section).casefold()
    return low.startswith("key") or low in {"present", "keymodviewerpresent"}


def _looks_like_external_input(writes: Iterable[VariableWrite], known_vars):
    """Recognize input-driven command lists without a name allow-list.

    Simple numeric selector dispatch is strong evidence on its own.  Other
    evidence comes from runtime/external dependencies or an undeclared
    condition variable.  A plain helper with only literal assignments does
    not become a viewer action.
    """
    writes = list(writes)
    if _selector_candidates(writes):
        return True
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


def _build_influences(facts: Iterable[ProgramFacts]):
    """Build data, branch-condition, and run-condition influence edges."""
    edges = []
    for program in facts:
        writes_by_section = {}
        for write in program.writes:
            writes_by_section.setdefault(write.section.casefold(), []).append(write)
            for dependency in write.dependencies:
                edges.append(InfluenceEdge(
                    dependency, write.target, "data", write.source))
            for variable in _condition_vars(write.conditions):
                edges.append(InfluenceEdge(
                    variable, write.target, "condition", write.source))
        for run in program.run_edges:
            for variable in _condition_vars(run.conditions):
                for write in writes_by_section.get(
                        run.target_section.casefold(), ()):
                    edges.append(InfluenceEdge(
                        variable, write.target, "run_condition", run.source))
    return _dedupe(edges)


def _build_selector_flow(facts: Iterable[ProgramFacts]):
    """Build UI selector provenance from exact copies and small transforms."""
    edges = []
    for program in facts:
        for write in program.writes:
            transform = _selector_transform(write)
            if transform is None:
                continue
            # In-place increments/toggles are state updates, not provenance
            # between selector variables.  Treating them as flow edges makes
            # numeric selector traversal unbounded (for example
            # ``$slot = $slot + 1`` in a menu draw loop).
            if write.dependencies[0] == write.target:
                continue
            scale, offset = transform
            edges.append(SelectorFlowEdge(
                source=write.dependencies[0], target=write.target,
                conditions=write.conditions, source_info=write.source,
                scale=scale, offset=offset))
    return _dedupe(edges)


def _selector_family(variable, edges):
    """Return a cycle-safe undirected family for UI selector association."""
    if isinstance(variable, str):
        try:
            variable = VariableId.from_key(variable)
        except ValueError:
            return set()
    adjacent = {}
    for edge in edges:
        adjacent.setdefault(edge.source, set()).add(edge.target)
        adjacent.setdefault(edge.target, set()).add(edge.source)
    found, pending = set(), [variable]
    while pending:
        current = pending.pop()
        if current in found:
            continue
        found.add(current)
        pending.extend(adjacent.get(current, ()))
    return found


def _numeric_text(value):
    number = _numeric(value)
    if number is None:
        return None
    return str(number)


def _selector_states(variable, value, edges):
    """Traverse selector flow while carrying values through affine edges."""
    start = SelectorState(variable, str(value))
    found = {start}
    pending = [start]
    while pending:
        current = pending.pop()
        for edge in edges:
            if current.variable == edge.source:
                number = _numeric(current.value)
                if number is None and (edge.scale != 1 or edge.offset != 0):
                    continue
                transformed = (number * edge.scale + edge.offset
                               if number is not None else current.value)
                candidate_value = _numeric_text(transformed)
                if candidate_value is None:
                    continue
                candidate = SelectorState(edge.target, candidate_value)
            elif current.variable == edge.target:
                number = _numeric(current.value)
                if number is None or not edge.scale:
                    continue
                transformed = (number - edge.offset) / edge.scale
                candidate_value = _numeric_text(transformed)
                if candidate_value is None:
                    continue
                candidate = SelectorState(edge.source, candidate_value)
            else:
                continue
            if candidate not in found:
                found.add(candidate)
                pending.append(candidate)
    return found


def _changed_render_variables(assignments, reverse, render_vars):
    return tuple(dict.fromkeys(
        target for write in assignments
        for target in _descendants(write.target, reverse)
        if target in render_vars))


def _actions_for_assignments(trigger, conditions, assignments, source,
                             reverse, render_vars, aliases,
                             user_facing=False):
    """Create one action per selector branch, preserving write order."""
    actions = []
    groups = _action_groups(assignments)
    if groups is None:
        return actions
    for selector, branch, routing_vars in groups:
        changed = _changed_render_variables(branch, reverse, render_vars)
        if not changed:
            continue
        selector_aliases = tuple(sorted(
            _alias_family(selector["var"], aliases) if selector else (),
            key=lambda variable: variable.key))
        branch_assignments = tuple(
            replace(write, conditions=_without_selector(
                write.conditions, selector))
            for write in branch
            if _descendants(write.target, reverse) & render_vars)
        branch_conditions = (_without_selector(
            conditions, selector) if selector else conditions)
        visible = (user_facing(changed, selector)
                   if callable(user_facing) else bool(user_facing))
        routing_keys = {variable.key for variable in routing_vars}
        routing_conditions = (tuple(
            tuple(clause for clause in group
                  if str(clause.get("var")) in routing_keys)
            for write in branch for group in (write.conditions or ())
        ) if routing_vars else ())
        routing_values = {variable: set() for variable in routing_vars}
        for write in branch:
            for group in write.conditions or ():
                for clause in group:
                    variable_key = str(clause.get("var"))
                    if variable_key not in routing_keys:
                        continue
                    if (clause.get("negate")
                            or clause.get("op", "==") != "=="):
                        continue
                    variable = next(
                        item for item in routing_vars
                        if item.key == variable_key)
                    routing_values[variable].add(str(clause.get("value")))
        routing_selectors = tuple(
            {"var": variable, "value": value, "role": "routing"}
            for variable in sorted(routing_values, key=lambda item: item.key)
            for value in sorted(routing_values[variable])
        )
        actions.append(Action(
            trigger=trigger,
            conditions=branch_conditions,
            writes=changed,
            source=source,
            assignments=branch_assignments,
            selector=selector,
            selector_aliases=selector_aliases,
            user_facing=bool(visible and changed and not routing_vars),
            routing_conditions=routing_conditions,
            routing_selectors=routing_selectors,
        ))
    return actions


def _controller_domain(variable, assignments, reverse, shape=False):
    """Infer the values reachable by one controller's own assignments."""
    assignments = tuple(assignments or ())
    writes = [write for write in assignments
              if variable in _descendants(write.target, reverse)]
    if not writes:
        return None
    domain = _write_domain(variable, writes, shape=shape)
    if domain.get("kind") == "discrete" and not domain.get("values"):
        return None
    return domain


def _root_influences(program, roots, reverse, render_vars):
    """Return render variables influenced by one input-root closure."""
    paths = _run_closure(program, roots)
    influenced = set()
    for write in program.writes:
        if write.phase == "post" or write.section.casefold() not in paths:
            continue
        influenced.update(_descendants(write.target, reverse) & render_vars)
    return tuple(sorted(influenced, key=lambda variable: variable.key))


def _path_conditions(program, roots):
    paths = _run_closure(program, roots)
    return tuple(sorted(paths.items(), key=lambda item: item[0]))


def build_control_graph(facts: Iterable[ProgramFacts], effects: Iterable[RenderEffect],
                        shape_vars: Iterable[VariableId] = ()) -> ControlGraph:
    """Build one graph from all independent INI scans."""
    facts = list(facts)
    effects = list(effects)
    graph = ControlGraph(effects=effects, facts=facts)
    nodes, writes_by_target, reverse = _build_indexes(facts)
    aliases = _exact_alias_map(writes_by_target)
    graph.variables = nodes
    for effect in effects:
        for var in effect.variables:
            graph.variables.setdefault(var, VariableNode(var)).render_effects.append(effect)

    render_vars = {var for effect in effects for var in effect.variables
                   if not _is_runtime(var)}
    shape_set = set(shape_vars)
    graph.influences = _build_influences(facts)
    graph.selector_flow = _build_selector_flow(facts)
    incoming_sections = {}
    input_paths = {}
    for program in facts:
        for edge in program.run_edges:
            incoming_sections.setdefault(
                (program.source.path, edge.target_section), set()).add(
                    edge.source_section)
        present_sections = [section for section in program.source.sections
                            if section.casefold() == "present"]
        roots = [key.section for key in program.key_inputs]
        roots.extend(present_sections)
        input_paths[program.source.path] = _run_closure(program, roots)
        for key in program.key_inputs:
            graph.input_roots.append(InputRoot(
                kind=("reserved_present"
                      if key.section.casefold() == "keymodviewerpresent"
                      else "key"),
                section=key.section,
                source=key.source,
                writes=key.writes,
                runs=key.runs,
                influences=_root_influences(
                    program, (key.section,), reverse, render_vars),
                path_conditions=_path_conditions(program, (key.section,)),
            ))
        for section in present_sections:
            section_writes = tuple(
                write.target for write in program.writes
                if write.section.casefold() == section.casefold()
                and write.phase != "post")
            section_runs = tuple(
                edge.target_section for edge in program.run_edges
                if edge.source_section.casefold() == section.casefold())
            source = next((write.source for write in program.writes
                           if write.section.casefold() == section.casefold()
                           and write.source), None)
            graph.input_roots.append(InputRoot(
                kind="present",
                section=section,
                source=source,
                writes=section_writes,
                runs=section_runs,
                influences=_root_influences(
                    program, (section,), reverse, render_vars),
                path_conditions=_path_conditions(program, (section,)),
            ))

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
            reserved_present = key.section.casefold() == "keymodviewerpresent"
            if direct and not reserved_present:
                for var in direct:
                    key_writes = tuple(
                        write for write in program.writes
                        if write.section.casefold() == key.section.casefold()
                        and write.target == var and write.phase != "post")
                    direct_by_var.setdefault(var, []).append(Controller(
                        "direct_key", key.source, key.key, direct,
                        domain=_controller_domain(
                            var, key_writes, reverse, var in shape_set)))
            if key.runs and not reserved_present:
                reachable_writes = _writes_from_closure(program, key.runs)
                key_actions = _actions_for_assignments(
                    key.key or key.section, key.conditions, reachable_writes,
                    key.source, reverse, render_vars, aliases,
                    user_facing=lambda changed, selector: True)
                actions.extend(key_actions)
                for action in key_actions:
                    if action.routing_conditions:
                        continue
                    controller_kind = action.kind
                    for var in action.writes:
                        interactive_by_var.setdefault(var, []).append(Controller(
                            controller_kind, key.source,
                            key.key or key.section, action.writes,
                            action.selector, action.selector_aliases,
                            action.user_facing,
                            action.routing_conditions,
                            _controller_domain(
                                var, action.assignments, reverse,
                                var in shape_set),
                            action.routing_selectors))

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
            paths = input_paths.get(program.source.path, {})
            path_conditions = paths.get(section.casefold())
            input_caller = any(_is_input_section(caller)
                               for caller in callers)
            if callers and path_conditions is None and not input_caller:
                # A command list invoked by a TextureOverride or another
                # runtime path is execution machinery, not a user controller.
                continue
            if (not callers and not _looks_like_external_input(
                    section_writes, program.variables)):
                # An unreferenced helper is not evidence of a user action just
                # because it writes a variable that also affects rendering.
                continue
            source = next((write.source for write in section_writes
                           if write.source), None)
            assignments = [replace(
                write,
                conditions=_combine_conditions(path_conditions, write.conditions)
                if path_conditions else write.conditions,
                interaction_selectors=_path_selector_context(path_conditions)
                if path_conditions else write.interaction_selectors)
                for write in section_writes]
            section_actions = _actions_for_assignments(
                section, (), assignments, source, reverse, render_vars, aliases,
                user_facing=lambda changed, selector: bool(
                    changed and selector
                    and selector.get("role") == "interaction"))
            if any(str(caller).casefold().startswith("key")
                   for caller in callers):
                # A Key-rooted closure already has the user-facing action;
                # this command-list projection only contributes the shared
                # interactive controller and must not duplicate its button.
                section_actions = [replace(action, user_facing=False)
                                   for action in section_actions]
            actions.extend(section_actions)
            for action in section_actions:
                if action.routing_conditions:
                    # Preserve the routing action in graph diagnostics, but it
                    # is not a standalone viewer control.
                    continue
                controller = Controller(
                    action.kind, source, section, action.writes,
                    action.selector, action.selector_aliases,
                    action.user_facing,
                    action.routing_conditions,
                    _controller_domain(
                        action.writes[0], action.assignments, reverse)
                    if len(action.writes) == 1 else None,
                    action.routing_selectors)
                for var in action.writes:
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
        writes = [write for write in writes_by_target.get(var, ())
                  if write.phase != "post"]
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
    "CONTROL_GRAPH_SCHEMA_VERSION", "InfluenceEdge", "InputRoot",
    "SelectorFlowEdge", "SelectorState", "VariableNode",
    "build_control_graph",
]

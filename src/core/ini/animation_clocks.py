"""Baked clock discovery and shared ordered animation program compilation."""

from dataclasses import dataclass
import hashlib
import json
import re

from .condition import compile_expression
from .dnf import (DNF_TRUE, build_bool_alias_map, dnf_and, dnf_not, dnf_or,
                  normalize_dnf, ordered_conditions_supported,
                  parse_condition_dnf)
from .sections import canonical_var_names


_ASSIGN_RE = re.compile(
    r"^(?:(?:global\s+)?persist\s+|global\s+)?"
    r"\$(?P<var>\w+)\s*=\s*(?P<value>.+)$",
    re.I,
)

_NUMBER_RE = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"

_CLOCK_RE = re.compile(
    rf"^(?:(?:global\s+)?persist\s+|global\s+)?\$(?P<frame>\w+)\s*=\s*\(*\s*time\s*\*\s*"
    rf"(?P<fps>\$(?:\w+)|{_NUMBER_RE})\s*"
    rf"(?:\*\s*(?P<speed>\$(?:\w+)|{_NUMBER_RE})\s*)?%\s*\(?\s*"
    rf"(?P<end>\$(?:\w+)|{_NUMBER_RE})\s*-\s*"
    rf"(?P<start>\$(?:\w+)|{_NUMBER_RE})\s*\+\s*1\s*\)?\s*"
    rf"\+\s*(?P<start_add>\$(?:\w+)|{_NUMBER_RE})\s*\)*\s*//\s*1\s*$",
    re.I,
)

_CLOCK_LITERAL_RANGE_RE = re.compile(
    rf"^(?:(?:global\s+)?persist\s+|global\s+)?\$(?P<frame>\w+)\s*=\s*\(*\s*time\s*\*\s*"
    rf"(?P<fps>\$(?:\w+)|{_NUMBER_RE})\s*"
    rf"(?:\*\s*(?P<speed>\$(?:\w+)|{_NUMBER_RE})\s*)?%\s*"
    rf"(?P<end>{_NUMBER_RE})\s*\+\s*(?P<start>{_NUMBER_RE})"
    rf"\s*\)*\s*//\s*1\s*$",
    re.I,
)

_CLOCK_ELIF_RE = re.compile(r"(?:else\s+if|elif)\s+(.*)$", re.I)

_COMPUTE_ASSIGN_RE = re.compile(
    r"^\s*(?P<lhs>\$?\w+)\s*=\s*(?P<rhs>.+?)\s*$", re.I)

_COMPUTE_RUN_RE = re.compile(
    r"^\s*run\s*=\s*(?P<section>\S+)\s*$", re.I)

_COMPUTE_DISPATCH_RE = re.compile(
    r"^\s*dispatch\s*=\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*$",
    re.I)

_COMPUTE_UNSUPPORTED_CONDITION_RE = re.compile(
    r"[<>+*/%]|\btime\b", re.I)


def _compute_condition_is_supported(expression, aliases=None):
    """Reject condition syntax the DNF activation state cannot represent."""
    return (_COMPUTE_UNSUPPORTED_CONDITION_RE.search(str(expression)) is None
            and ordered_conditions_supported(
                str(expression), aliases if aliases is not None else {}))


def _compile_expression(value, canonical, var_prefix=None):
    def variable_name(name):
        local = _canonical(name, canonical)
        return "dt" if local.casefold() == "dt" else f"{var_prefix or ''}{local}"

    try:
        return compile_expression(value, variable_name)
    except (TypeError, ValueError):
        return None


def _compile_condition(value, canonical, var_prefix=None):
    return _compile_expression(value, canonical, var_prefix)


def _expression_variables(value, result=None):
    if result is None:
        result = set()
    if not isinstance(value, dict):
        return result
    if value.get("kind") == "variable":
        result.add(value["variable"])
    for child in value.values():
        if isinstance(child, dict):
            _expression_variables(child, result)
        elif isinstance(child, list):
            for item in child:
                _expression_variables(item, result)
    return result


def _program_assignment(line, canonical, var_prefix):
    match = _COMPUTE_ASSIGN_RE.fullmatch(line)
    if not match or not match.group("lhs").startswith("$"):
        return None
    expression = _compile_expression(
        match.group("rhs"), canonical, var_prefix)
    if expression is None:
        return None
    variable = f"{var_prefix or ''}{_canonical(match.group('lhs'), canonical)}"
    return {"op": "set", "variable": variable, "expression": expression}


def _compile_animation_program(sections, animations, canonical, var_prefix=None,
                               condition_aliases=None):
    """Compile verified dispatches and child calls in authored branch order."""
    dispatches = {}
    compile_sections = set()
    for animation in animations:
        track_id = animation["track_id"]
        items = [(item, {"pass": index, "kind": "shape"})
                 for index, item in enumerate(animation.get("shape_passes", ()))]
        if animation.get("pose") is not None:
            items.append((animation["pose"], {"kind": "pose"}))
        for item, metadata in items:
            section, line_index = tuple(item["dispatch_key"])
            call_key = tuple(item.get("call_key", ()))
            compile_sections.add(call_key[0] if call_key else section)
            dispatches.setdefault((section, line_index, call_key), []).append({
                "track_id": track_id, "phase": item["phase_expr"], **metadata})

    initials = _literal_constant_assignments(sections, canonical)
    unsupported = getattr(condition_aliases, "unsupported", set())
    key_variables = set()
    for section, lines in sections.items():
        if str(section).casefold().startswith("key"):
            for raw in lines:
                match = _COMPUTE_ASSIGN_RE.fullmatch(str(raw).split(";", 1)[0].strip())
                if match and match.group("lhs").startswith("$"):
                    key_variables.add(f"{var_prefix or ''}{_canonical(match.group('lhs'), canonical)}")

    commands, variables, assigned = [], set(), set()
    lookup = {str(name).casefold(): lines for name, lines in sections.items()}

    def compile_section(section, call_key=()):
        branches = []
        for line_index, raw in enumerate(lookup[section]):
            line = str(raw).split(";", 1)[0].strip()
            low = line.casefold()
            if not line or low.startswith(("global ", "persist ")):
                continue
            branch = _CLOCK_ELIF_RE.fullmatch(line)
            if low.startswith("if ") or branch:
                if branch and (not branches or branches[-1]):
                    return False
                condition = _compile_condition(branch.group(1) if branch else line[3:],
                                               canonical, var_prefix)
                if condition is None or any(
                        _unprefix(name, var_prefix).casefold() in unsupported
                        for name in _expression_variables(condition)):
                    return False
                commands.append({"op": "elif" if branch else "if", "condition": condition})
                variables.update(_expression_variables(condition))
                if not branch:
                    branches.append(False)
                continue
            if low in {"else", "endif"}:
                if not branches or (low == "else" and branches[-1]):
                    return False
                commands.append({"op": low})
                if low == "else":
                    branches[-1] = True
                else:
                    branches.pop()
                continue
            raw_assignment = _COMPUTE_ASSIGN_RE.fullmatch(line)
            if raw_assignment and raw_assignment.group("lhs").startswith("$"):
                if _canonical(raw_assignment.group("lhs"), canonical).casefold() in {"dt", "ts"}:
                    continue
                assignment = _program_assignment(line, canonical, var_prefix)
                if assignment is None:
                    return False
                commands.append(assignment)
                variables.add(assignment["variable"])
                variables.update(_expression_variables(assignment["expression"]))
                assigned.add(assignment["variable"])
            elif match := _COMPUTE_RUN_RE.fullmatch(line):
                child = match.group("section").casefold()
                child_key = (section, line_index)
                if any(key[2] == child_key for key in dispatches):
                    if call_key or child not in lookup or not compile_section(child, child_key):
                        return False
            elif _COMPUTE_DISPATCH_RE.fullmatch(line):
                for item in dispatches.get((section, line_index, call_key), ()):
                    commands.append({"op": "dispatch", **item})
                    variables.update(_expression_variables(item["phase"]))
        return not branches

    for section in lookup:
        if section in compile_sections and not compile_section(section):
            return None
    return {
        "external_variables": sorted((variables - assigned) | (variables & key_variables)),
        "initials": {f"{var_prefix or ''}{_canonical(key, canonical)}": value
                     for key, value in initials.items()
                     if f"{var_prefix or ''}{_canonical(key, canonical)}" in variables},
        "commands": commands,
    }


def _unprefix(value, var_prefix):
    value = str(value)
    if var_prefix and value.startswith(var_prefix):
        return value[len(var_prefix):]
    return value


def _numeric(value):
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number.is_integer() else number


def _integer(value):
    number = _numeric(value)
    if number is None or not float(number).is_integer():
        return None
    return int(number)


@dataclass(frozen=True)
class AnimationClock:
    """One supported discrete frame clock discovered in an INI."""

    frame_var: str
    fps_var: str | None
    fps_value: float | None
    frame_start: int
    frame_end: int
    conditions: list
    source_section: str
    speed_var: str | None = None
    speed_value: float | None = 1.0

    @property
    def animation_id(self):
        # One Present section may drive several frame families through the
        # same variable.  Include the complete clock definition: same-range
        # clocks can still differ by FPS, speed, or activation conditions.
        identity = json.dumps({
            "source_section": self.source_section,
            "frame_var": self.frame_var,
            "fps_var": self.fps_var,
            "fps": self.fps_value,
            "frame_start": self.frame_start,
            "frame_end": self.frame_end,
            "conditions": self.conditions,
            "speed_var": self.speed_var,
            "speed": self.speed_value,
        }, sort_keys=True, separators=(",", ":"), default=str)
        digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:12]
        return (f"{self.source_section}::{self.frame_var}::"
                f"{self.frame_start}-{self.frame_end}::"
                f"{digest}")

    def to_dict(self):
        result = {
            "frame_var": self.frame_var,
            "fps_var": self.fps_var,
            "fps": self.fps_value,
            "frame_start": self.frame_start,
            "frame_end": self.frame_end,
            "conditions": self.conditions,
            "source_section": self.source_section,
            "speed_var": self.speed_var,
            "speed": self.speed_value,
        }
        return result


@dataclass(frozen=True)
class AnimationAnalysis:
    clocks: tuple[AnimationClock, ...] = ()
    frame_vars: frozenset[str] = frozenset()


def _canonical(value, canonical_vars):
    raw = str(value).lstrip("$")
    return canonical_vars.get(raw.casefold(), raw)


def _operand_value(operand, literals, canonical_vars):
    text = str(operand).strip()
    if text.startswith("$"):
        return literals.get(_canonical(text, canonical_vars).casefold())
    return _numeric(text)


def _literal_assignments(sections, canonical_vars):
    values = {}
    for lines in sections.values():
        for raw in lines:
            match = _ASSIGN_RE.fullmatch(str(raw).split(";", 1)[0].strip())
            if not match:
                continue
            value = _numeric(match.group("value"))
            if value is None:
                continue
            key = _canonical(match.group("var"), canonical_vars).casefold()
            values.setdefault(key, value)
    return values


def _literal_constant_assignments(sections, canonical_vars):
    """Return numeric initial values authored in the INI's Constants section."""
    constants = next(
        (lines for name, lines in sections.items()
         if str(name).casefold() == "constants"), ())
    return _literal_assignments({"Constants": constants}, canonical_vars)


def _condition_stack_line(line, stack, aliases):
    """Advance a small if/elif/else stack and report control lines."""
    low = line.lower()
    match = _CLOCK_ELIF_RE.fullmatch(line)
    if match:
        if stack:
            frame = stack[-1]
            branch = parse_condition_dnf(match.group(1), aliases)
            frame["cur"] = dnf_and(dnf_not(frame["seen"]), branch)
            frame["seen"] = dnf_or(frame["seen"], branch)
        return True
    if low.startswith("if "):
        branch = parse_condition_dnf(line[3:], aliases)
        stack.append({"cur": branch, "seen": branch})
        return True
    if low == "else":
        if stack:
            stack[-1]["cur"] = dnf_not(stack[-1]["seen"])
        return True
    if low == "endif":
        if stack:
            stack.pop()
        return True
    return False


def discover_animation_clocks(sections, *, var_prefix=None,
                              canonical_vars=None, qualified_vars=None,
                              condition_aliases=None):
    """Discover supported clocks and the source-spelling frame variables.

    The returned ``frame_vars`` intentionally uses the local INI spelling;
    the draw scanner consumes source conditions before namespacing.  Clock
    payloads use the public, namespaced spelling.
    """
    canonical = canonical_vars or canonical_var_names(sections)
    literals = _literal_assignments(sections, canonical)
    aliases = (condition_aliases if condition_aliases is not None
               else build_bool_alias_map(sections))
    all_vars = set(canonical.values())
    clocks = []
    seen = set()

    for section_name, lines in sections.items():
        stack = []
        condition_support = []
        for raw in lines:
            line = str(raw).split(";", 1)[0].strip()
            if not line:
                continue
            elif_match = _CLOCK_ELIF_RE.fullmatch(line)
            if elif_match:
                if condition_support:
                    condition_support[-1] = (
                        condition_support[-1]
                        and ordered_conditions_supported(
                            elif_match.group(1), aliases))
            elif line.casefold().startswith("if "):
                condition_support.append(
                    ordered_conditions_supported(line[3:], aliases))
            elif line.casefold() == "endif":
                if condition_support:
                    condition_support.pop()
            if _condition_stack_line(line, stack, aliases):
                continue
            match = (_CLOCK_RE.fullmatch(line)
                     or _CLOCK_LITERAL_RANGE_RE.fullmatch(line))
            if not match:
                continue
            if not all(condition_support):
                continue
            frame_local = _canonical(match.group("frame"), canonical)
            start_token = match.group("start")
            add_token = match.groupdict().get("start_add") or start_token
            if (start_token.startswith("$")
                    and add_token.startswith("$")
                    and _canonical(start_token, canonical).casefold()
                    != _canonical(add_token, canonical).casefold()):
                continue
            frame_start = _integer(_operand_value(
                start_token, literals, canonical))
            frame_end = _integer(_operand_value(
                match.group("end"), literals, canonical))
            if (frame_start is None or frame_end is None
                    or frame_end < frame_start):
                continue

            fps_token = match.group("fps")
            fps_var = None
            fps_value = None
            if fps_token.startswith("$"):
                fps_local = _canonical(fps_token, canonical)
                fps_var = f"{var_prefix or ''}{fps_local}"
                fps_value = literals.get(fps_local.casefold())
            else:
                fps_value = _numeric(fps_token)

            speed_token = match.groupdict().get("speed")
            speed_var = None
            speed_value = 1.0
            if speed_token:
                if speed_token.startswith("$"):
                    speed_local = _canonical(speed_token, canonical)
                    speed_var = f"{var_prefix or ''}{speed_local}"
                    speed_value = literals.get(speed_local.casefold())
                else:
                    speed_value = _numeric(speed_token)

            combined = DNF_TRUE
            for frame in stack:
                combined = dnf_and(combined, frame["cur"])
            conditions = normalize_dnf(
                combined, all_vars, var_prefix, qualified_vars)
            public_frame = f"{var_prefix or ''}{frame_local}"
            source_section = f"{var_prefix or ''}{section_name}"
            clock = AnimationClock(
                frame_var=public_frame, fps_var=fps_var,
                fps_value=fps_value, frame_start=frame_start,
                frame_end=frame_end, conditions=conditions,
                source_section=source_section,
                speed_var=speed_var, speed_value=speed_value,
            )
            key = (clock.animation_id, clock.frame_start, clock.frame_end,
                   clock.fps_var, clock.fps_value, clock.speed_var,
                   clock.speed_value,
                   repr(clock.conditions))
            if key in seen:
                continue
            seen.add(key)
            clocks.append(clock)

    return AnimationAnalysis(
        clocks=tuple(clocks),
        frame_vars=frozenset(
            _unprefix(clock.frame_var, var_prefix) for clock in clocks),
    )


def frame_condition(conditions, frame_vars):
    """Return one unambiguous ``(var, integer)`` frame condition.

    Alternatives or negated comparisons are deliberately rejected.  This
    keeps ``else`` branches and arithmetic conditions from being guessed.
    """
    matches = set()
    known = {str(value).casefold() for value in frame_vars}
    for group in conditions or []:
        if len(group) != 1:
            return None
        clause = group[0]
        if clause.get("negate") or clause.get("var", "").casefold() not in known:
            return None
        value = _integer(clause.get("value"))
        if value is None:
            return None
        matches.add((clause["var"], value))
    return next(iter(matches)) if len(matches) == 1 else None


def compute_animation_control_vars(animations, state_rules=()):
    """Return controls that can affect a normalized compute program."""
    dependencies = set()

    def add_conditions(conditions):
        for group in conditions or ():
            for clause in group:
                dependencies.add(str(clause.get("var", "")))

    for animation in animations or ():
        add_conditions(animation.get("conditions"))
        dependencies.update(animation.get("program", {}).get(
            "external_variables", ()))

    from .state import control_dependencies
    return control_dependencies(dependencies, state_rules or ())

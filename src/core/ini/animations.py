"""Small, deliberately narrow analysis of baked and compute animations.

Compute animation discovery emits a typed, ordered program for the limited
numeric/control subset used by verified mods.  It is not an INI interpreter;
unsupported expressions or branches reject the compute animation safely.
"""

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import os
import re
import struct

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
_COMPUTE_RESOURCE_RE = re.compile(
    r"^\s*(Resource\S+)\s*=\s*ref\s+cs-u(?P<slot>\d+)\s*$", re.I)
_COMPUTE_U_COPY_RE = re.compile(
    r"^\s*cs-u(?P<slot>\d+)\s*=\s*copy\s+(Resource\S+)\s*$", re.I)
_COMPUTE_U_NULL_RE = re.compile(
    r"^\s*cs-u(?P<slot>\d+)\s*=\s*null\s*$", re.I)
_COMPUTE_T_COPY_RE = re.compile(
    r"^\s*cs-t(?P<slot>\d+)\s*=\s*copy\s+(\S+)\s*$", re.I)
_COMPUTE_RUN_RE = re.compile(
    r"^\s*run\s*=\s*(?P<section>\S+)\s*$", re.I)
_COMPUTE_SHADER_RE = re.compile(r"^\s*cs\s*=\s*(\S+)\s*$", re.I)
_COMPUTE_DISPATCH_RE = re.compile(
    r"^\s*dispatch\s*=\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*$",
    re.I)
_X88_RE = re.compile(r"^\s*x88\s*=\s*(?P<expr>.+?)\s*$", re.I)
_X89_RE = re.compile(r"^\s*x89\s*=\s*(?P<expr>.+?)\s*$", re.I)
_COMPUTE_UNSUPPORTED_CONDITION_RE = re.compile(
    r"[<>+*/%]|\btime\b", re.I)
_WWMI_U5_RE = re.compile(
    r"^\s*cs-u5\s*=\s*(?:copy\s+)?(Resource\S+)\s*$", re.I)
_WWMI_REGISTER_RE = re.compile(
    r"^\s*(?P<register>[xyz]0)\s*=\s*(?P<value>.*?)\s*$", re.I)
_WWMI_TOGGLE_RE = re.compile(
    r"^\s*\$(?P<var>\w+)\s*==\s*1\s*$", re.I)
_WWMI_PHASE_RE = re.compile(
    r"^\s*\$(?P<phase>\w+)\s*=\s*\$(?P=phase)\s*\+\s*"
    r"\$(?P<speed>\w+)\s*\*\s*\$dt\s*$", re.I)


def _compute_condition_is_supported(expression, aliases=None):
    """Reject condition syntax the DNF activation state cannot represent."""
    return (_COMPUTE_UNSUPPORTED_CONDITION_RE.search(str(expression)) is None
            and ordered_conditions_supported(
                str(expression), aliases if aliases is not None else {}))


class _ExpressionParser:
    """Parse the deliberately small numeric language used by compute mods."""

    _TOKEN_RE = re.compile(
        r"\s*(?:(?P<number>(?:\d+(?:\.\d*)?|\.\d+))|"
        r"(?P<name>\$?[A-Za-z_]\w*)|"
        r"(?P<operator>[+*\-]))")

    def __init__(self, text, canonical, var_prefix=None):
        self.text = str(text).strip()
        self.canonical = canonical
        self.var_prefix = var_prefix or ""
        self.tokens = []
        position = 0
        while position < len(self.text):
            match = self._TOKEN_RE.match(self.text, position)
            if match is None:
                raise ValueError("unsupported expression token")
            self.tokens.append(next(
                (value for value in match.groups() if value is not None),
                None))
            position = match.end()
        self.index = 0

    def parse(self):
        if not self.tokens:
            raise ValueError("empty expression")
        result = self._additive()
        if self.index != len(self.tokens):
            raise ValueError("trailing expression tokens")
        return result

    def _peek(self):
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def _take(self):
        value = self._peek()
        self.index += 1
        return value

    def _additive(self):
        result = self._multiplicative()
        while self._peek() in ("+", "-"):
            operator = self._take()
            result = {"kind": "binary", "op": operator,
                      "left": result, "right": self._multiplicative()}
        return result

    def _multiplicative(self):
        result = self._unary()
        while self._peek() == "*":
            operator = self._take()
            result = {"kind": "binary", "op": operator,
                      "left": result, "right": self._unary()}
        return result

    def _unary(self):
        token = self._take()
        if token is None:
            raise ValueError("missing expression operand")
        number = _numeric(token)
        if number is not None:
            return {"kind": "literal", "value": number}
        if token.casefold() == "dt":
            return {"kind": "dt"}
        if not token.startswith("$"):
            raise ValueError("bare names are not numeric operands")
        local = str(token).lstrip("$")
        local = _canonical(local, self.canonical)
        if local.casefold() == "dt":
            return {"kind": "dt"}
        return {"kind": "variable",
                "variable": f"{self.var_prefix}{local}"}


def _compile_expression(value, canonical, var_prefix=None):
    try:
        return _ExpressionParser(value, canonical, var_prefix).parse()
    except (TypeError, ValueError):
        return None


_COMPARISON_RE = re.compile(r"(==|>)")


def _compile_condition(value, canonical, var_prefix=None):
    text = str(value).strip()
    match = _COMPARISON_RE.search(text)
    if match is None:
        return None
    left = _compile_expression(
        text[:match.start()], canonical, var_prefix)
    right = _compile_expression(
        text[match.end():], canonical, var_prefix)
    if left is None or right is None:
        return None
    return {"kind": "compare", "op": match.group(1),
            "left": left, "right": right}


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


def _compile_animation_program(sections, animations, canonical, var_prefix=None):
    """Compile only the authored CustomShader statements with dispatches."""
    dispatches = {}
    compile_sections = set()
    for animation in animations:
        track_id = animation["track_id"]
        for index, item in enumerate(animation.get("shape_passes", ())):
            section, line_index = tuple(item["dispatch_key"])
            compile_sections.add(section)
            dispatches.setdefault((section, line_index), []).append({
                "track_id": track_id, "pass": index,
                "phase": item["phase_expr"], "kind": "shape",
            })
        pose = animation.get("pose")
        if pose is not None:
            section, line_index = tuple(pose["dispatch_key"])
            compile_sections.add(section)
            dispatches.setdefault((section, line_index), []).append({
                "track_id": track_id, "phase": pose["phase_expr"],
                "kind": "pose",
            })

    initials = _literal_constant_assignments(sections, canonical)
    key_variables = set()
    for section, lines in sections.items():
        if not str(section).casefold().startswith("key"):
            continue
        for raw in lines:
            line = str(raw).split(";", 1)[0].strip()
            match = _COMPUTE_ASSIGN_RE.fullmatch(line)
            if match is not None and match.group("lhs").startswith("$"):
                key_variables.add(
                    f"{var_prefix or ''}"
                    f"{_canonical(match.group('lhs'), canonical)}")

    commands = []
    variables = set()
    assigned = set()
    for section, lines in sections.items():
        section_key = str(section).casefold()
        if section_key not in compile_sections:
            continue
        conditions = []
        for line_index, raw in enumerate(lines):
            line = str(raw).split(";", 1)[0].strip()
            if not line:
                continue
            low = line.casefold()
            if low.startswith("global ") or low.startswith("persist ") \
                    or low.startswith("global persist "):
                continue
            if low.startswith("if "):
                condition = _compile_condition(line[3:], canonical, var_prefix)
                if condition is None:
                    return None
                conditions.append(condition)
                variables.update(_expression_variables(condition))
                continue
            if low == "endif":
                if not conditions:
                    return None
                conditions.pop()
                continue
            if re.match(r"(?:else|elif)\b", low):
                return None
            raw_assignment = _COMPUTE_ASSIGN_RE.fullmatch(line)
            if (raw_assignment is not None
                    and raw_assignment.group("lhs").startswith("$")
                    and _canonical(raw_assignment.group("lhs"), canonical)
                    .casefold() in {"dt", "ts"}):
                continue
            assignment = _program_assignment(line, canonical, var_prefix)
            if assignment is not None:
                assignment["conditions"] = list(conditions)
                commands.append(assignment)
                variables.add(assignment["variable"])
                variables.update(_expression_variables(assignment["expression"]))
                assigned.add(assignment["variable"])
                continue
            if raw_assignment is not None and raw_assignment.group("lhs").startswith("$"):
                return None
            if _COMPUTE_DISPATCH_RE.fullmatch(line):
                if conditions:
                    return None
                for item in dispatches.get((section_key, line_index), ()):
                    commands.append({"op": "dispatch", **item})
                    variables.update(_expression_variables(item["phase"]))
        if conditions:
            return None
    normalized_initials = {
        f"{var_prefix or ''}{_canonical(key, canonical)}": value
        for key, value in initials.items()
        if f"{var_prefix or ''}{_canonical(key, canonical)}" in variables
    }
    return {
        "external_variables": sorted(
            (variables - assigned) | (variables & key_variables)),
        "initials": normalized_initials,
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


def _resource_get(resources, name):
    if not name:
        return {}
    getter = getattr(resources, "get_ci", None)
    if getter is not None:
        return getter(name)
    for key, value in (resources or {}).items():
        if str(key).casefold() == str(name).casefold():
            return value
    return {}


def _read_resource_bytes(path, source):
    if not path:
        return None
    try:
        if source is not None and getattr(source, "virtual", False):
            return source.read_bytes(path)
        with open(path, "rb") as stream:
            return stream.read()
    except (OSError, TypeError, ValueError):
        return None


def _resource_size(path, source):
    if not path:
        return None
    try:
        return (source.size(path) if source is not None
                else os.path.getsize(path))
    except (OSError, TypeError, ValueError):
        return None


def _resource_path(mod_dir, filename, source):
    if not filename:
        return None
    if source is not None:
        return source.resolve_resource(filename)
    if mod_dir is None:
        return None
    from ..resource_paths import safe_resource_path
    return safe_resource_path(mod_dir, filename)


def _shader_path(mod_dir, ini_path, value, source):
    """Resolve a shader reference without introducing an alternate loader."""
    value = str(value).strip().strip('"')
    if not value:
        return None
    if source is not None and source.is_resource_reference(ini_path):
        ini_name = source.logical_path(ini_path)
        relative = os.path.normpath(os.path.join(os.path.dirname(ini_name), value))
        return source.resolve_resource(relative)
    if mod_dir is None:
        return None
    from ..resource_paths import safe_resource_path
    return safe_resource_path(mod_dir, value)


def _strip_hlsl_comments(text):
    text = re.sub(r"//[^\r\n]*", "", text)
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


_NUMTHREADS_RE = re.compile(
    r"\[\s*numthreads\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)\s*\]",
    re.I)


def _identify_shape_weight_operation(
        text, expected_ini_input, *, require_delta_application):
    """Recognize supported weights only when they use the supplied input."""
    if not text:
        return None
    source = _strip_hlsl_comments(text)
    compact = re.sub(r"\s+", "", source).lower()
    slot, component = expected_ini_input
    aliases = {name.lower() for name in re.findall(
        rf"^\s*#define\s+([a-z_]\w*)\s+IniParams\s*\[\s*"
        rf"{int(slot)}\s*\]\s*\.\s*{re.escape(component)}\b",
        source, re.I | re.M)}
    phase_names = set(aliases)
    for value, source_name in re.findall(
            r"(?:float|half)([a-z_]\w*)=float\(([a-z_]\w*)\)",
            compact, re.I):
        if source_name in aliases:
            phase_names.add(value)

    sine_formula = re.compile(
        r"0\.5f?\*\(sin\((?P<phase>[a-z_]\w*)\*30(?:\.0*)?f?\)"
        r"\+1(?:\.0*)?f?\)", re.I)
    for match in sine_formula.finditer(compact):
        if match.group("phase") not in phase_names:
            continue
        formula = match.group(0)
        weight_names = set(re.findall(
            rf"(?:float|half)([a-z_]\w*)=\(*{re.escape(formula)}\)*",
            compact))
        if not require_delta_application and weight_names:
            return {
                "kind": "sine", "scale": 30.0,
                "amplitude": 0.5, "offset": 0.5,
            }
        expressions = [re.escape(name) for name in weight_names]
        expressions.append(rf"\(?{re.escape(formula)}\)?")
        if all(any(re.search(
                rf"diff\.{axis}\*{expression}(?:\W|$)", compact)
                for expression in expressions)
               for axis in ("position", "normal")):
            return {
                "kind": "sine", "scale": 30.0,
                "amplitude": 0.5, "offset": 0.5,
            }

    if require_delta_application:
        for alias in phase_names:
            if all(re.search(
                    rf"diff\.{axis}\*{re.escape(alias)}(?:\W|$)", compact)
                   for axis in ("position", "normal")):
                return {"kind": "linear"}
    return None


def _pose_kernel_signature(body, names, index_field="indicies"):
    """Normalize one known operation sequence, without interpreting HLSL."""
    body = re.sub(r"\((int|uint)\)\s*(\w+)", r"int(\2)", body)
    # The only branch in this adapter protects quaternion normalization.
    body = re.sub(r"if\s*\(([^{};]+)\)\s*\{\s*([^{}]+;)\s*\}", r"if(\1)\2", body)
    locals_by_name = {}
    pattern = r"(?<!\w)(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?[fF]?|\b[a-zA-Z_]\w*\b"
    def token(match):
        value = match.group()
        if value[0].isdigit() or value[0] == ".":
            return format(float(value.rstrip("fF")), ".12g")
        if match.start() and body[:match.start()].rstrip().endswith("."):
            return "indicies" if value.lower() == index_field.lower() else value.lower()
        if value in names:
            return names[value]
        if re.fullmatch(r"(?:u?int|float)[1-4]?", value):
            return value.removeprefix("u") if value.startswith("uint") else value
        if value in {"frac", "sign",
                     "dot", "length", "normalize", "if"}:
            return value
        return locals_by_name.setdefault(value, f"local{len(locals_by_name)}")
    return re.sub(r"\s+", "", re.sub(pattern, token, body))


@lru_cache(maxsize=2)
def _pose_kernel_contract(coordinate_transform):
    """The shared unrolled DLB adapter: four influences and two adjacent frames."""
    axes = ("x", "-z", "y") if coordinate_transform == "swap_yz_negate" else ("x", "y", "z")
    def components(name, fields):
        return ",".join(("-" if axis.startswith("-") else "") + name + "." + axis[-1]
                        for axis in fields)
    body = ["uint i=thread.x", "Blend b=blend[i]", "Vertex v=base[i]",
            f"float4 pos=float4({components('v.position', axes)},1)",
            f"float4 normal=float4({components('v.normal', axes)},0)",
            "int frame=int(time)", "float inter=frac(time)", "float inter_prev=1-inter", "int count=int(bones)",
            "int4 prev=frame*count+b.indicies", "int4 next=(frame+1)*count+b.indicies"]
    for frame in ("prev", "next"):
        body.extend(f"Pose {frame}{index}=pose[{frame}.{axis}]" for index, axis in enumerate("xyzw"))
    body.append("float4 weights=b.weights")
    for name, field in (("scale", "S"), ("bias", "T")):
        sums = ["+".join(f"{frame}{index}.{field}*weights.{axis}"
                        for index, axis in enumerate("xyzw")) for frame in ("prev", "next")]
        body.append(f"float3 {name}=({sums[0]})*inter_prev+({sums[1]})*inter")
    body.extend(["pos.xyz=pos.xyz*scale+bias",
                 "float4 qr=prev0.QR*weights.x*inter_prev", "float4 qd=prev0.QD*weights.x*inter_prev"])
    terms = [(frame, index, axis, alpha)
             for frame, alpha in (("prev", "inter_prev"), ("next", "inter"))
             for index, axis in enumerate("xyzw") if (frame, index) != ("prev", 0)]
    body.extend(f"float sign_{frame}{index}=sign(dot(prev0.QR,{frame}{index}.QR))"
                for frame, index, _, _ in terms)
    for frame, index, axis, alpha in terms:
        body.extend(f"{name}+={frame}{index}.{field}*weights.{axis}*{alpha}*sign_{frame}{index}"
                    for name, field in (("qr", "QR"), ("qd", "QD")))
    body.extend(["float size=length(qr)", "if(size<1e-6)size=1e-6", "qr/=size", "qd/=size",
                 "float qx=qr.x,qy=qr.y,qz=qr.z,qw=qr.w", "float dx=qd.x,dy=qd.y,dz=qd.z,dw=qd.w",
                 "float m00=1-2*qy*qy-2*qz*qz", "float m10=2*(qx*qy+qw*qz)", "float m20=2*(qx*qz-qw*qy)",
                 "float t0=2*(-dw*qx+dx*qw-dy*qz+dz*qy)",
                 "float m01=2*(qx*qy-qw*qz)", "float m11=1-2*qx*qx-2*qz*qz", "float m21=2*(qy*qz+qw*qx)",
                 "float t1=2*(-dw*qy+dx*qz+dy*qw-dz*qx)",
                 "float m02=2*(qx*qz+qw*qy)", "float m12=2*(qy*qz-qw*qx)", "float m22=1-2*qx*qx-2*qy*qy",
                 "float t2=2*(-dw*qz-dx*qy+dy*qx+dz*qw)"])
    for result, vector, w in (("pos_result", "pos", 1), ("normal_result", "normal", 0)):
        body.append("float4 " + result)
        for row, axis in enumerate("xyz"):
            terms = "+".join(f"m{row}{col}*{vector}.{component}"
                             for col, component in enumerate("xyz"))
            body.append(f"{result}.{axis}={terms}" + (f"+t{row}*pos.w" if w else ""))
        body.append(f"{result}.w={w}")
    output_axes = ("x", "z", "-y") if coordinate_transform == "swap_yz_negate" else axes
    for result, field in (("pos_result", "position"), ("normal_result", "normal")):
        vector = f"float3({components(result, output_axes)})"
        body.append(f"output[i].{field}=" + (f"normalize({vector})" if field == "normal" else vector))
    return _pose_kernel_signature(";".join(body) + ";", {
        "base": "buffer50", "blend": "buffer51", "pose": "buffer52",
        "output": "buffer5", "thread": "thread", "time": "phase88", "bones": "phase89",
        "Vertex": "vertex", "Blend": "blend", "Pose": "pose"})


def _pose_shader_signature(source, buffers, index_field):
    match = re.search(r"\bvoid\s+main\s*\(\s*uint3\s+(\w+)\s*:\s*SV_DispatchThreadID\s*\)\s*\{", source)
    if match is None:
        return None
    depth, end = 1, match.end()
    while end < len(source) and depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    if depth:
        return None
    names = {buffer[2]: f"buffer{slot}" for slot, buffer in buffers.items()}
    names.update({buffers[slot][1]: kind for slot, kind in (
        (5, "vertex"), (50, "vertex"), (51, "blend"), (52, "pose"))})
    names[match.group(1)] = "thread"
    aliases = re.findall(r"^\s*#define\s+(\w+)\s+IniParams\s*\[\s*(88|89)\s*\]\s*\.\s*x\s*$", source, re.M)
    if (len(re.findall(r"^\s*#", source, re.M)) != len(aliases)
            or len(dict(aliases)) != len(aliases)
            or {slot for _, slot in aliases} != {"88", "89"}
            or not re.search(r"Texture1D\s*<\s*float4\s*>\s*IniParams\s*:\s*register\s*\(\s*t120\s*\)", source)):
        return None
    for alias, slot in aliases:
        names[alias] = f"phase{slot}"
    return _pose_kernel_signature(source[match.end():end - 1], names, index_field)


def _identify_pose_transform(source):
    """Match the fixed vertex/blend/pose adapter, including its output writes."""
    buffers = {int(slot): (kind.lower(), struct_name, name, register.lower())
               for kind, struct_name, name, register, slot in re.findall(
                   r"\b(RWStructuredBuffer|StructuredBuffer)\s*<\s*(\w+)\s*>"
                   r"\s*(\w+)\s*:\s*register\s*\(\s*([tu])(\d+)\s*\)",
                   source, re.I)}
    layouts = {}
    fields = re.compile(r"\b(\w+)\s+(\w+)\s*;")
    for name, body in re.findall(r"\bstruct\s+(\w+)\s*\{([^}]*)\}", source, re.I | re.S):
        if not fields.sub("", body).strip():
            layouts[name] = [(kind.lower(), field.lower())
                             for kind, field in fields.findall(body)]
    expected = {
        5: [("float3", "position"), ("float3", "normal"), ("float4", "tangent")],
        50: [("float3", "position"), ("float3", "normal"), ("float4", "tangent")],
        52: [("float3", "s"), ("float3", "t"), ("float4", "qr"), ("float4", "qd")],
    }
    if not {5, 50, 51, 52} <= buffers.keys():
        return None
    if any(layouts.get(buffers[slot][1]) != fields
           for slot, fields in expected.items()):
        return None
    blend = layouts.get(buffers[51][1], ())
    if (len(blend) != 2 or blend[0] != ("float4", "weights")
            or blend[1][0] not in {"int4", "uint4"}
            or (buffers[5][0], buffers[5][3]) != ("rwstructuredbuffer", "u")
            or any((buffers[slot][0], buffers[slot][3]) != ("structuredbuffer", "t")
                   for slot in (50, 51, 52))):
        return None
    if not all(re.search(
            rf"\b{re.escape(buffers[slot][2])}\s*\[", source)
               for slot in (50, 51, 52)):
        return None
    signature = _pose_shader_signature(source, buffers, blend[1][1])
    for transform in ("identity", "swap_yz_negate"):
        if signature == _pose_kernel_contract(transform):
            return transform
    return None


def _identify_compute_shader(text):
    """Read the small adapter surface needed by GIMI compute shaders."""
    if not text:
        return None
    source = _strip_hlsl_comments(text)
    match = _NUMTHREADS_RE.search(source)
    if match is None:
        return None
    threads = int(match.group(1))
    if threads <= 0 or tuple(map(int, match.groups()[1:])) != (1, 1):
        return None
    weight_operation = _identify_shape_weight_operation(
        source, (88, "x"), require_delta_application=True)
    coordinate_transform = "identity" if weight_operation is not None else _identify_pose_transform(source)
    kind = "shape" if weight_operation is not None else ("pose" if coordinate_transform else None)
    if kind is None:
        return None
    return {
        "kind": kind,
        "threads": threads,
        "coordinate_transform": coordinate_transform,
        "weight_operation": weight_operation,
    }


def _resolved_resource(resources, copy_sources, name, visiting=None):
    info = _resource_get(resources, name)
    if info.get("filename"):
        return info
    key = str(name).casefold()
    visiting = set(visiting or ())
    if key in visiting:
        return {}
    visiting.add(key)
    for candidate in copy_sources.get(key, ()):
        resolved = _resolved_resource(resources, copy_sources, candidate, visiting)
        if resolved.get("filename"):
            return resolved
    return {}


class _AnimationResources:
    """Read and resolve immutable inputs once within one INI discovery."""

    def __init__(self, mod_dir, ini_path, source, resources=None, copy_sources=None):
        self.mod_dir, self.ini_path, self.source = mod_dir, ini_path, source
        self.resources, self.copy_sources = resources, copy_sources
        self._resolved, self._sizes, self._texts, self._adapters = {}, {}, {}, {}

    def resource(self, name):
        key = str(name).casefold()
        if key not in self._resolved:
            self._resolved[key] = _resolved_resource(
                self.resources, self.copy_sources, name)
        return self._resolved[key]

    def size(self, filename):
        if filename not in self._sizes:
            path = _resource_path(self.mod_dir, filename, self.source)
            self._sizes[filename] = _resource_size(path, self.source)
        return self._sizes[filename]

    def shader_text(self, value):
        path = _shader_path(self.mod_dir, self.ini_path, value, self.source)
        if path not in self._texts:
            data = _read_resource_bytes(path, self.source)
            self._texts[path] = data.decode("utf-8", errors="ignore") if data else None
        return self._texts[path]

    def shader(self, value):
        if value not in self._adapters:
            self._adapters[value] = _identify_compute_shader(self.shader_text(value))
        return self._adapters[value]


def _validate_compute_layout(inputs, shape_passes, pose=None, *, bone_count=None):
    """Validate shared GIMI layouts before exposing a descriptor."""
    base_resource = (shape_passes[0]["base_resource"] if shape_passes
                     else pose["base_resource"])
    base_info = inputs.resource(base_resource)
    if base_info.get("stride") != 40:
        return None
    base_size = inputs.size(base_info.get("filename"))
    if base_size is None or base_size % 40:
        return None
    vertex_count = base_size // 40
    if vertex_count <= 0:
        return None
    for item in shape_passes:
        info = inputs.resource(item["target_resource"])
        if (info.get("stride") != 40
                or str(item["base_resource"]).casefold() != str(base_resource).casefold()):
            return None
        if inputs.size(info.get("filename")) != base_size:
            return None
    result = {
        "base_file": base_info["filename"],
        "vertex_count": vertex_count,
    }
    if pose is None:
        return result
    blend_info = inputs.resource(pose["blend_resource"])
    pose_info = inputs.resource(pose["pose_resource"])
    if (blend_info.get("stride") != 32 or pose_info.get("stride") != 56
            or not bone_count):
        return None
    blend_size = inputs.size(blend_info.get("filename"))
    pose_size = inputs.size(pose_info.get("filename"))
    if (blend_size != vertex_count * 32
            or pose_size is None or pose_size % (bone_count * 56)):
        return None
    frame_count = pose_size // (bone_count * 56)
    if frame_count < 2:
        return None
    result.update({
        "blend_file": blend_info["filename"],
        "pose_file": pose_info["filename"],
        "frame_count": frame_count,
    })
    return result


def _wwmi_present_runs(sections, canonical, var_prefix):
    present_name = next((name for name in sections
                         if str(name).casefold() == "present"), None)
    if present_name is None:
        return []
    lookup = {str(name).casefold(): name for name in sections}
    stack, records = [], []
    for raw in sections[present_name]:
        line = str(raw).split(";", 1)[0].strip()
        low = line.casefold()
        if not line:
            continue
        if low.startswith("if "):
            expression = line[3:]
            # These gates belong to the host game's object/registration lifecycle.
            # Every other enclosing condition must remain in the viewer program.
            condition = ([] if expression.casefold() in {
                "$object_detected", "$mod_enabled"} else
                [_compile_condition(expression, canonical, var_prefix)])
            stack.append({"toggle": bool(_WWMI_TOGGLE_RE.fullmatch(expression)),
                          "conditions": condition})
        elif re.match(r"(?:else|elif)\b", low):
            if stack:
                stack[-1] = None
        elif low == "endif":
            if stack:
                stack.pop()
        else:
            match = _COMPUTE_RUN_RE.fullmatch(line)
            if not match:
                continue
            conditions = None
            if stack and all(frame is not None for frame in stack) and stack[-1]["toggle"]:
                conditions = [condition for frame in stack
                              for condition in frame["conditions"]]
                if any(condition is None for condition in conditions):
                    conditions = None
            child = lookup.get(match.group("section").casefold())
            if child is not None and str(child).casefold().startswith("commandlist"):
                records.append({"section": child, "condition": conditions})
    return records


def _wwmi_phase_update(lines, canonical, var_prefix):
    found = None
    for raw in lines:
        line = str(raw).split(";", 1)[0].strip()
        if re.match(r"(?:if|elif|else|endif)\b", line, re.I):
            return None
        match = _WWMI_PHASE_RE.fullmatch(line)
        if match is None:
            continue
        if found is not None:
            return None
        phase = _canonical(match.group("phase"), canonical)
        speed = _canonical(match.group("speed"), canonical)
        found = {"phase_var": f"{var_prefix or ''}{phase}",
                 "speed_var": f"{var_prefix or ''}{speed}"}
    return found


def _wwmi_animation_shader(sections, child_section, *, inputs, canonical, var_prefix):
    u5_resource = None
    shader_value = None
    registers = {}
    dispatch = None
    for raw in sections.get(child_section, ()):
        line = str(raw).split(";", 1)[0].strip()
        if not line:
            continue
        if re.match(r"(?:if|elif|else|endif)\b", line, re.I):
            return None
        match = _WWMI_U5_RE.fullmatch(line)
        if match:
            u5_resource = match.group(1)
            continue
        match = _COMPUTE_SHADER_RE.fullmatch(line)
        if match:
            shader_value = match.group(1)
            continue
        match = _WWMI_REGISTER_RE.fullmatch(line)
        if match:
            registers[match.group("register").lower()] = match.group("value")
            continue
        match = _COMPUTE_DISPATCH_RE.fullmatch(line)
        if match:
            if dispatch is not None:
                return None
            dispatch = tuple(int(match.group(index)) for index in range(1, 4))
    if not u5_resource or not shader_value or dispatch != (1, 1, 1):
        return None
    x0 = _integer(registers.get("x0"))
    phase_expr = _compile_expression(
        registers.get("z0"), canonical, var_prefix)
    if (x0 != 0 or "y0" not in registers
            or registers["y0"].strip()
            or phase_expr is None):
        return None
    if phase_expr.get("kind") != "variable":
        return None
    shader_source = inputs.shader_text(shader_value)
    if not shader_source:
        return None
    stripped_shader = _strip_hlsl_comments(shader_source)
    threads = _NUMTHREADS_RE.search(stripped_shader)
    compact = re.sub(r"\s+", "", stripped_shader).lower()
    phase_macro = re.search(
        r"#define(?P<phase>[a-z_]\w*)iniparams\[0\]\.z", compact, re.I)
    if (threads is None or tuple(map(int, threads.groups())) != (1, 1, 1)
            or phase_macro is None):
        return None
    phase_name = phase_macro.group("phase")
    if not re.search(
            rf"(?:float|half)[a-z_]\w*=float\({re.escape(phase_name)}\)",
            compact, re.I):
        return None
    weight_operation = _identify_shape_weight_operation(
        shader_source, (0, "z"), require_delta_application=False)
    if weight_operation is None:
        return None
    return {
        "phase_var": phase_expr["variable"],
        "weight_operation": weight_operation,
    }


def _wwmi_shape_key_template_key(shape):
    try:
        shape_id = int(shape["shape_id"])
        return tuple(str(shape[key]).casefold() for key in (
            "base_file", "offset_file", "vertex_id_file",
            "vertex_offset_file")) + (
                shape_id // 127, int(shape.get("sparse_entry_offset", 0)))
    except (KeyError, TypeError, ValueError):
        return None


def _wwmi_resolve_shape_ids(candidates, template, shape_sliders, *, mod_dir,
                            source):
    template_key = _wwmi_shape_key_template_key(template)
    if template_key is None:
        return None
    known = []
    for shape in shape_sliders or ():
        if _wwmi_shape_key_template_key(shape) != template_key:
            continue
        try:
            shape_id = int(shape["shape_id"])
            known.append(int(shape.get("buffer_shape_id",
                                      shape_id + shape_id // 127)))
        except (KeyError, TypeError, ValueError):
            continue
    known = sorted(set(known))
    if not known or known != list(range(known[0], known[-1] + 1)):
        return None
    data = _read_resource_bytes(_resource_path(
        mod_dir, template["offset_file"], source), source)
    try:
        entry_offset = int(template.get("sparse_entry_offset", 0))
    except (TypeError, ValueError):
        return None
    if data is None:
        return None

    def populated(container):
        if (container + 2) * 4 > len(data):
            return False
        begin, end = struct.unpack_from("<II", data, container * 4)
        return begin + entry_offset < end + entry_offset

    inferred = range(known[-1] + 1, known[-1] + 1 + len(candidates))
    if not all(populated(container) for container in inferred):
        return None
    resolved = []
    for item, container_shape_id in zip(candidates, inferred):
        user_shape_id = container_shape_id - container_shape_id // 128
        if user_shape_id + user_shape_id // 127 != container_shape_id:
            user_shape_id = None
        if user_shape_id is None:
            return None
        resolved.append({**item, "shape_id": user_shape_id,
                         "container_shape_id": container_shape_id})
    return resolved


def discover_wwmi_sparse_animations(sections, shape_sliders, *, mod_dir=None,
                                    ini_path=None, source=None,
                                    var_prefix=None, canonical_vars=None):
    """Discover the narrow WWMI sparse shape-key animation contract."""
    canonical = canonical_vars or canonical_var_names(sections)
    present_runs = _wwmi_present_runs(sections, canonical, var_prefix)
    if not present_runs:
        return []
    section_lookup = {
        str(name).casefold(): name for name in sections
    }
    inputs = _AnimationResources(mod_dir, ini_path, source)

    candidates = []
    for record in present_runs:
        phase_update = _wwmi_phase_update(
            sections.get(record["section"], ()), canonical, var_prefix)
        if phase_update is None:
            if any(_WWMI_PHASE_RE.fullmatch(str(raw).split(";", 1)[0].strip())
                   for raw in sections[record["section"]]):
                return []
            continue
        run_sections = [section_lookup.get(match.group("section").casefold())
                        for raw in sections[record["section"]]
                        for match in [_COMPUTE_RUN_RE.fullmatch(
                            str(raw).split(";", 1)[0].strip())]
                        if match is not None]
        if (len(run_sections) != 1
                or not str(run_sections[0]).casefold().startswith("customshader")):
            return []
        child_section = run_sections[0]
        shader = _wwmi_animation_shader(
            sections, child_section, inputs=inputs,
            canonical=canonical, var_prefix=var_prefix)
        if (shader is None or shader["phase_var"] != phase_update["phase_var"]
                or record["condition"] is None):
            return []
        candidates.append({
            **shader, **phase_update,
            "conditions": record["condition"],
        })

    if not candidates:
        return []

    literals = _literal_constant_assignments(sections, canonical)
    for item in candidates:
        local_speed = _unprefix(item["speed_var"], var_prefix)
        speed = literals.get(local_speed.casefold())
        if speed is None:
            return []
        item["speed"] = speed

    templates = []
    seen_templates = set()
    for shape in shape_sliders or ():
        if shape.get("shape_id") is None:
            continue
        key = _wwmi_shape_key_template_key(shape)
        if key is None or not all(shape.get(name) for name in (
                "base_file", "offset_file", "vertex_id_file",
                "vertex_offset_file")):
            continue
        if key in seen_templates:
            continue
        seen_templates.add(key)
        templates.append(shape)
    if not templates:
        return []

    identity_name = (source.logical_path(ini_path) if source is not None
                     and source.is_resource_reference(ini_path)
                     else os.path.basename(str(ini_path or "")))
    result = []
    for template in templates:
        template_batch = int(template["shape_id"]) // 127
        resolved = _wwmi_resolve_shape_ids(
            candidates, template, shape_sliders, mod_dir=mod_dir,
            source=source)
        if resolved is None:
            continue
        if any(item["shape_id"] // 127 != template_batch
               for item in resolved):
            continue
        identity = json.dumps({
            "ini": identity_name,
            "base": template["base_file"],
            "shape_ids": sorted(item["shape_id"] for item in resolved),
            "container_shape_ids": sorted(
                item["container_shape_id"] for item in resolved),
        }, sort_keys=True, separators=(",", ":"))
        track_id = "wwmi-sparse::" + hashlib.sha1(
            identity.encode("utf-8")).hexdigest()[:12]
        program_id = "wwmi-sparse-program::" + hashlib.sha1(
            identity.encode("utf-8")).hexdigest()[:12]
        commands = []
        initials = {}
        passes = []
        for index, item in enumerate(resolved):
            phase_var = item["phase_var"]
            initials.setdefault(phase_var, 0.0)
            initials[item["speed_var"]] = item["speed"]
            command_condition = item["conditions"]
            commands.append({
                "op": "set", "variable": phase_var,
                "expression": {"kind": "binary", "op": "+",
                                "left": {"kind": "variable",
                                         "variable": phase_var},
                                "right": {"kind": "binary", "op": "*",
                                           "left": {"kind": "variable",
                                                    "variable": item["speed_var"]},
                                           "right": {"kind": "dt"}}},
                "conditions": command_condition,
            })
            commands.append({
                "op": "dispatch", "track_id": track_id, "kind": "shape",
                "pass": index,
                "phase": {"kind": "variable", "variable": phase_var},
                "conditions": command_condition,
            })
            animated_shape = dict(template)
            shape_id = item["shape_id"]
            animated_shape["shape_id"] = shape_id
            animated_shape["buffer_shape_id"] = item["container_shape_id"]
            passes.append({
                "sparse_shape": animated_shape,
                "weight_operation": item["weight_operation"],
            })
        result.append({
            "kind": "wwmi_sparse",
            "track_id": track_id,
            "base_file": template["base_file"],
            "shape_passes": passes,
            "overlay": True,
            "program_id": program_id,
            "program": {
                "external_variables": sorted(
                    {item["speed_var"] for item in resolved}
                    | {variable for item in resolved
                       for condition in item["conditions"]
                       for variable in _expression_variables(condition)}),
                "initials": initials,
                "commands": commands,
            },
        })
    return result


def _compute_binding_events(section, lines, inputs, inherited_base=None):
    """Scan authored bindings once, with a restricted inherited-child mode."""
    sources = {50: inherited_base} if inherited_base else {}
    shader, phase, bone_count = None, None, None
    for line_index, raw in enumerate(lines):
        line = str(raw).split(";", 1)[0].strip()
        if not line:
            continue
        event, value = "statement", None
        if inherited_base and re.match(r"cs-(?:u5|t50)\s*=", line, re.I):
            yield line, "invalid", None
            return
        match = _COMPUTE_T_COPY_RE.fullmatch(line)
        if match:
            sources[int(match.group("slot"))] = match.group(2)
        elif match := _COMPUTE_SHADER_RE.fullmatch(line):
            shader = inputs.shader(match.group(1))
        elif match := _X88_RE.fullmatch(line):
            phase = match.group("expr").strip()
        elif match := _X89_RE.fullmatch(line):
            bone_count = match.group("expr").strip()
        elif match := _COMPUTE_U_COPY_RE.fullmatch(line):
            if int(match.group("slot")) == 5:
                event, value = "begin", match.group(2)
        elif match := _COMPUTE_U_NULL_RE.fullmatch(line):
            if int(match.group("slot")) == 5:
                event = "end"
        elif match := _COMPUTE_RESOURCE_RE.fullmatch(line):
            if int(match.group("slot")) == 5:
                event, value = "output", match.group(1)
        elif match := _COMPUTE_RUN_RE.fullmatch(line):
            event, value = "run", (match.group("section"), sources.get(50))
        elif match := _COMPUTE_DISPATCH_RE.fullmatch(line):
            kind = "pose" if sources.get(52) else "shape"
            if (shader is None or shader["kind"] != kind
                    or not sources.get(50) or not sources.get(51)
                    or tuple(map(int, match.groups()[1:])) != (1, 1)):
                event = "invalid"
            else:
                event = "dispatch"
                value = {
                    "kind": kind,
                    "dispatch_key": (str(section).casefold(), line_index),
                    "base_resource": sources[50],
                    "dispatch_vertices": int(match.group(1)) * shader["threads"],
                    "phase_expr": phase,
                }
                if kind == "shape":
                    value.update(target_resource=sources[51],
                                 weight_operation=shader["weight_operation"])
                else:
                    value.update(blend_resource=sources[51],
                                 pose_resource=sources[52],
                                 bone_count_expr=bone_count,
                                 coordinate_transform=shader["coordinate_transform"])
        elif binding := re.match(r"cs-(u5|t5[012])\s*=", line, re.I):
            if binding.group(1).casefold() == "u5":
                event = "end"
            else:
                sources.pop(int(binding.group(1)[1:]), None)
                event = "invalid"
        yield line, event, value


def _compute_chains(sections, inputs, canonical, var_prefix, aliases):
    lookup = {str(name).casefold(): name for name in sections}
    tracked_vars = set(canonical.values())
    chains = []

    def nested_passes(child, base, u5):
        if not base or str(base).casefold() != str(u5).casefold():
            return []
        passes = []
        for _, event, value in _compute_binding_events(
                child, sections[child], inputs, inherited_base=base):
            if event in {"invalid", "begin", "end", "run"}:
                return []
            if event == "dispatch":
                if value["kind"] != "shape":
                    return []
                passes.append(value)
        return passes

    for section, lines in sections.items():
        if not str(section).casefold().startswith("customshader"):
            continue
        chain, stack, supported = None, [], []
        for line, event, value in _compute_binding_events(section, lines, inputs):
            elif_match = _CLOCK_ELIF_RE.fullmatch(line)
            if elif_match and supported:
                supported[-1] &= _compute_condition_is_supported(
                    elif_match.group(1), aliases)
            elif line.casefold().startswith("if "):
                supported.append(_compute_condition_is_supported(line[3:], aliases))
            elif line.casefold() == "endif" and supported:
                supported.pop()
            if _condition_stack_line(line, stack, aliases):
                continue
            if event in {"begin", "end"}:
                if chain is not None:
                    chains.append(chain)
                chain = ({"u5_resource": value, "passes": [],
                          "output_resource": None} if event == "begin" else None)
            elif chain is not None:
                if event == "output":
                    chain["output_resource"] = value
                elif event == "dispatch":
                    chain["passes"].append(value)
                elif event == "invalid":
                    chain["unsupported"] = True
                elif event == "run" and all(supported):
                    child = lookup.get(value[0].casefold())
                    if child is None or not str(child).casefold().startswith("customshader"):
                        continue
                    passes = nested_passes(child, value[1], chain["u5_resource"])
                    if not passes:
                        continue
                    combined = DNF_TRUE
                    for frame in stack:
                        combined = dnf_and(combined, frame["cur"])
                    nested = {"child_section": child, "passes": passes,
                              "conditions": normalize_dnf(
                                  combined, tracked_vars, var_prefix)}
                    if chain.get("nested_run") is not None:
                        chain["nested_ambiguous"] = True
                    chain["nested_run"] = nested
        if chain is not None:
            chains.append(chain)
    return chains


def discover_compute_animations(sections, resources, *, mod_dir=None,
                               ini_path=None, source=None, var_prefix=None,
                               canonical_vars=None, condition_aliases=None):
    """Capture bindings, resolve chains, validate tracks, then compile a program."""
    from .draw_resources import _collect_resource_copy_sources
    canonical = (canonical_vars if canonical_vars is not None
                 else canonical_var_names(sections))
    aliases = (condition_aliases if condition_aliases is not None
               else build_bool_alias_map(sections))
    inputs = _AnimationResources(
        mod_dir, ini_path, source, resources,
        _collect_resource_copy_sources(sections, resources))
    chains = _compute_chains(sections, inputs, canonical, var_prefix, aliases)
    output_chains = {}
    pose_inputs = set()
    for chain in chains:
        if chain["output_resource"]:
            output_chains.setdefault(str(chain["output_resource"]).casefold(), []).append(chain)
        pose_inputs.update(str(item["base_resource"]).casefold()
                           for item in chain["passes"] if item["kind"] == "pose")
    literals = _literal_constant_assignments(sections, canonical)
    ini_identity = (source.logical_path(ini_path) if source is not None
                    and source.is_resource_reference(ini_path)
                    else os.path.basename(str(ini_path or "")))

    def build_track(chain, index, shapes, pose=None, nested=None):
        if not shapes and pose is None:
            return None
        base = pose["base_resource"] if pose else shapes[0]["base_resource"]
        if str(base).casefold() != str(chain["u5_resource"]).casefold():
            return None
        bone_count = (_integer(_operand_value(pose["bone_count_expr"], literals, canonical))
                      if pose else None)
        if pose and bone_count is None:
            return None
        validated = _validate_compute_layout(inputs, shapes, pose, bone_count=bone_count)
        if validated is None:
            return None
        phases = [_compile_expression(item["phase_expr"], canonical, var_prefix)
                  for item in shapes]
        pose_phase = (_compile_expression(pose["phase_expr"], canonical, var_prefix)
                      if pose else None)
        if any(phase is None for phase in phases) or (pose and pose_phase is None):
            return None
        identity = {"ini": ini_identity, "output": chain["output_resource"],
                    "base": base,
                    "chain": index}
        if nested:
            identity["child"] = nested["child_section"]
        track_id = "gimi::" + hashlib.sha1(json.dumps(
            identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:12]
        animation = {
            "track_id": track_id, "position_resource": chain["output_resource"],
            "base_file": validated["base_file"], "vertex_count": validated["vertex_count"],
            "shape_passes": [{
                "target_file": inputs.resource(item["target_resource"])["filename"],
                "dispatch_vertices": item["dispatch_vertices"],
                "phase_expr": phase, "dispatch_key": item["dispatch_key"],
                "weight_operation": item["weight_operation"],
            } for item, phase in zip(shapes, phases)],
            "pose": None,
        }
        if pose:
            animation.update(coordinate_transform=pose["coordinate_transform"], pose={
                "blend_file": validated["blend_file"], "file": validated["pose_file"],
                "bone_count": bone_count, "frame_count": validated["frame_count"],
                "phase_expr": pose_phase, "dispatch_key": pose["dispatch_key"],
            })
        if nested:
            animation.update(overlay=True, conditions=nested["conditions"])
        return animation

    animations = []
    for index, chain in enumerate(chains):
        output = chain["output_resource"]
        if not output:
            continue
        poses = [item for item in chain["passes"] if item["kind"] == "pose"]
        shapes = [item for item in chain["passes"] if item["kind"] == "shape"]
        animation = None
        if not chain.get("unsupported") and not (poses and shapes):
            if not poses and str(output).casefold() not in pose_inputs:
                animation = build_track(chain, index, shapes)
            elif len(poses) == 1:
                matching = output_chains.get(str(poses[0]["base_resource"]).casefold(), [])
                if not matching:
                    animation = build_track(chain, index, [], poses[0])
                elif (len(matching) == 1 and not matching[0].get("unsupported")
                      and all(item["kind"] == "shape" for item in matching[0]["passes"])):
                    animation = build_track(chain, index, matching[0]["passes"], poses[0])
        nested = chain.get("nested_run")
        if animation is None and nested and not chain.get("nested_ambiguous"):
            animation = build_track(chain, index, nested["passes"], nested=nested)
        if animation is not None:
            animations.append(animation)
    if not animations:
        return []
    program = _compile_animation_program(sections, animations, canonical, var_prefix)
    if program is None:
        return []
    program_id = "gimi-program::" + hashlib.sha1(
        str(ini_identity).encode("utf-8")).hexdigest()[:12]
    for animation in animations:
        for item in [*animation["shape_passes"], *([animation["pose"]] if animation["pose"] else [])]:
            item.pop("phase_expr")
            item.pop("dispatch_key")
        animation.update(program_id=program_id, program=program)
    return animations


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


__all__ = [
    "AnimationAnalysis", "AnimationClock", "discover_animation_clocks",
    "frame_condition", "discover_compute_animations",
    "discover_wwmi_sparse_animations",
    "compute_animation_control_vars",
]

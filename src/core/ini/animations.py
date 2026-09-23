"""Small, deliberately narrow analysis of baked and compute animations.

Compute animation discovery emits a typed, ordered program for the limited
numeric/control subset used by verified mods.  It is not an INI interpreter;
unsupported expressions or branches reject the compute animation safely.
"""

from dataclasses import dataclass
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
        rf"\s*(?:(?P<number>(?:\d+(?:\.\d*)?|\.\d+))|"
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
            dispatches[(section, line_index)] = {
                "track_id": track_id, "pass": index,
                "phase": item["phase_expr"], "kind": "shape",
            }
        pose = animation.get("pose")
        if pose is not None:
            section, line_index = tuple(pose["dispatch_key"])
            compile_sections.add(section)
            dispatches[(section, line_index)] = {
                "track_id": track_id, "phase": pose["phase_expr"],
                "kind": "pose",
            }

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
            if low in {"else", "elif"} or low.startswith("else "):
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
                item = dispatches.get((section_key, line_index))
                if item is not None:
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


def _identify_wwmi_animation_shader(text):
    """Recognize Yangyang's direct WWMI phase-to-shape formula."""
    if not text:
        return None
    source = _strip_hlsl_comments(text)
    threads = _NUMTHREADS_RE.search(source)
    if threads is None or tuple(map(int, threads.groups())) != (1, 1, 1):
        return None
    compact = re.sub(r"\s+", "", source).lower()
    macro = re.search(
        r"#define(?P<phase>[a-z_]\w*)iniParams\[0\]\.z", compact, re.I)
    if macro is None:
        return None
    phase_name = macro.group("phase")
    value_assignment = re.search(
        rf"(?:float|half)(?P<value>[a-z_]\w*)=float\({re.escape(phase_name)}\)",
        compact, re.I)
    if value_assignment is None:
        return None
    value_name = value_assignment.group("value")
    number = r"(?:\.\d*)?f?"
    formula = re.compile(
        rf"0\.5f?\*\(sin\(({re.escape(value_name)}|"
        rf"{re.escape(phase_name)})\*30{number}\)\+1{number}\)",
        re.I)
    if formula.search(compact) is None:
        return None
    return True


def _identify_compute_shader(text):
    """Read the small adapter surface needed by GIMI compute shaders."""
    if not text:
        return None
    source = _strip_hlsl_comments(text)
    match = _NUMTHREADS_RE.search(source)
    if match is None:
        return None
    threads = int(match.group(1))
    if threads <= 0:
        return None
    compact = re.sub(r"\s+", "", source).lower()
    columbina_markers = (
        "float4pos=float4(v.position.x,-v.position.z,v.position.y,1.0f)",
        "float4normal=float4(v.normal.x,-v.normal.z,v.normal.y,0.0f)",
        "rw_buffer[i].position=float3(pos_result.x,pos_result.z,-pos_result.y)",
        "rw_buffer[i].normal=normalize(float3(normal_result.x,normal_result.z,-normal_result.y)",
    )
    coordinate_variant = (
        "columbina_basis" if all(marker in compact for marker in columbina_markers)
        else "standard")
    return {"threads": threads, "coordinate_variant": coordinate_variant}


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


def _validate_compute_layout(resources, copy_sources, shape_passes, pose=None,
                             *, mod_dir, source, bone_count=None):
    """Validate shared GIMI layouts before exposing a descriptor."""
    base_resource = (shape_passes[0]["base_resource"] if shape_passes
                     else pose["base_resource"])
    base_info = _resolved_resource(resources, copy_sources, base_resource)
    if base_info.get("stride") != 40:
        return None
    base_path = _resource_path(mod_dir, base_info.get("filename"), source)
    base_size = _resource_size(base_path, source)
    if base_size is None or base_size % 40:
        return None
    vertex_count = base_size // 40
    if vertex_count <= 0:
        return None
    for item in shape_passes:
        info = _resolved_resource(resources, copy_sources,
                                  item["target_resource"])
        if info.get("stride") != 40:
            return None
        path = _resource_path(mod_dir, info.get("filename"), source)
        if _resource_size(path, source) != base_size:
            return None
    result = {
        "base_file": base_info["filename"],
        "vertex_count": vertex_count,
    }
    if pose is None:
        return result
    blend_info = _resolved_resource(resources, copy_sources,
                                    pose["blend_resource"])
    pose_info = _resolved_resource(resources, copy_sources,
                                   pose["pose_resource"])
    if (blend_info.get("stride") != 32 or pose_info.get("stride") != 56
            or not bone_count):
        return None
    blend_path = _resource_path(mod_dir, blend_info.get("filename"), source)
    pose_path = _resource_path(mod_dir, pose_info.get("filename"), source)
    blend_size = _resource_size(blend_path, source)
    pose_size = _resource_size(pose_path, source)
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


def _wwmi_toggle_condition(expression, canonical, var_prefix):
    match = _WWMI_TOGGLE_RE.fullmatch(expression)
    if match is None:
        return None
    variable = f"{var_prefix or ''}{_canonical(match.group('var'), canonical)}"
    return {
        "toggle_var": variable,
        "condition": [{
            "kind": "compare", "op": "==",
            "left": {"kind": "variable", "variable": variable},
            "right": {"kind": "literal", "value": 1},
        }],
    }


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
            stack.append(_wwmi_toggle_condition(line[3:], canonical, var_prefix))
        elif low.startswith("elif ") or low.startswith("else if "):
            if stack:
                expression = line.split(None, 1)[1]
                stack[-1] = _wwmi_toggle_condition(
                    expression[3:] if low.startswith("else if ") else expression,
                    canonical, var_prefix)
        elif low == "else":
            if stack:
                stack[-1] = None
        elif low == "endif":
            if stack:
                stack.pop()
        else:
            match = _COMPUTE_RUN_RE.fullmatch(line)
            if not match or not stack or stack[-1] is None:
                continue
            child = lookup.get(match.group("section").casefold())
            if child is not None and str(child).casefold().startswith("commandlist"):
                records.append({"section": child, **stack[-1]})
    return records


def _wwmi_phase_update(lines, canonical, var_prefix):
    found = None
    for raw in lines:
        line = str(raw).split(";", 1)[0].strip()
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


def _wwmi_animation_shader(sections, child_section, *, mod_dir, ini_path,
                           source, canonical, var_prefix):
    u5_resource = None
    shader_value = None
    registers = {}
    dispatch = None
    for raw in sections.get(child_section, ()):
        line = str(raw).split(";", 1)[0].strip()
        if not line:
            continue
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
    shader_path = _shader_path(mod_dir, ini_path, shader_value, source)
    shader_text = _read_resource_bytes(shader_path, source)
    if (not shader_text
            or _identify_wwmi_animation_shader(
                shader_text.decode("utf-8", errors="ignore")) is None):
        return None
    return {
        "phase_var": phase_expr["variable"],
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

    candidates = []
    for record in present_runs:
        phase_update = _wwmi_phase_update(
            sections.get(record["section"], ()), canonical, var_prefix)
        if phase_update is None:
            continue
        run_sections = [section_lookup.get(match.group("section").casefold())
                        for raw in sections[record["section"]]
                        for match in [_COMPUTE_RUN_RE.fullmatch(
                            str(raw).split(";", 1)[0].strip())]
                        if match is not None]
        if (len(run_sections) != 1
                or not str(run_sections[0]).casefold().startswith("customshader")):
            continue
        child_section = run_sections[0]
        shader = _wwmi_animation_shader(
            sections, child_section, mod_dir=mod_dir,
            ini_path=ini_path, source=source, canonical=canonical,
            var_prefix=var_prefix)
        if shader is None:
            continue
        if shader["phase_var"] != phase_update["phase_var"]:
            continue
        candidates.append({
            **shader, **phase_update,
            "toggle_var": record["toggle_var"],
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
                    {variable for item in resolved
                     for variable in (item["toggle_var"],
                                       item["speed_var"])}),
                "initials": initials,
                "commands": commands,
            },
        })
    return result


def discover_compute_animations(sections, resources, *, mod_dir=None,
                               ini_path=None, source=None, var_prefix=None,
                               canonical_vars=None, condition_aliases=None):
    """Discover the conservative fixed-layout compute-animation contract."""
    canonical = (canonical_vars if canonical_vars is not None
                 else canonical_var_names(sections))
    from .draw_resources import _collect_resource_copy_sources
    copy_sources = _collect_resource_copy_sources(sections, resources)
    aliases = (condition_aliases if condition_aliases is not None
               else build_bool_alias_map(sections))
    tracked_vars = set(canonical.values())
    section_lookup = {
        str(name).casefold(): name for name in sections
    }

    def current_conditions(stack, support_stack):
        combined = DNF_TRUE
        for frame in stack:
            combined = dnf_and(combined, frame["cur"])
        if not all(support_stack):
            return None
        return normalize_dnf(combined, tracked_vars, var_prefix)

    def nested_shape_passes(child_section, inherited_base, inherited_u5):
        """Read one child that inherits the parent's t50/u5 bindings."""
        if (not inherited_base or not inherited_u5
                or str(inherited_base).casefold()
                != str(inherited_u5).casefold()):
            return []
        t_sources = {50: inherited_base}
        active = None
        phase_expr = None
        passes = []
        for line_index, raw in enumerate(sections.get(child_section, ())):
            line = str(raw).split(";", 1)[0].strip()
            if not line:
                continue
            match = _COMPUTE_U_COPY_RE.fullmatch(line)
            if match and int(match.group("slot")) == 5:
                return []
            match = _COMPUTE_U_NULL_RE.fullmatch(line)
            if match and int(match.group("slot")) == 5:
                return []
            match = _COMPUTE_T_COPY_RE.fullmatch(line)
            if match:
                slot = int(match.group("slot"))
                if slot == 50:
                    return []
                t_sources[slot] = match.group(2)
                continue
            match = _X88_RE.fullmatch(line)
            if match:
                phase_expr = match.group("expr").strip()
                continue
            match = _COMPUTE_SHADER_RE.fullmatch(line)
            if match:
                shader_path = _shader_path(
                    mod_dir, ini_path, match.group(1), source)
                text = _read_resource_bytes(shader_path, source)
                try:
                    text = text.decode("utf-8", errors="ignore") if text else None
                except AttributeError:
                    text = None
                active = _identify_compute_shader(text)
                continue
            match = _COMPUTE_DISPATCH_RE.fullmatch(line)
            if not match or active is None:
                continue
            target = t_sources.get(51)
            if not target or 52 in t_sources:
                continue
            passes.append({
                "kind": "shape",
                "dispatch_key": (str(child_section).casefold(), line_index),
                "base_resource": inherited_base,
                "target_resource": target,
                "dispatch_vertices": int(match.group(1)) * active["threads"],
                "phase_expr": phase_expr,
            })
        return passes

    chains = []
    for section, lines in sections.items():
        if not str(section).casefold().startswith("customshader"):
            continue
        t_sources = {}
        active = None
        phase_expr = None
        bone_count_expr = None
        current_chain = None
        condition_stack = []
        condition_support = []

        def close_chain():
            nonlocal current_chain
            if current_chain is not None:
                chains.append(current_chain)
                current_chain = None

        for line_index, raw in enumerate(lines):
            line = str(raw).split(";", 1)[0].strip()
            if not line:
                continue
            elif_match = _CLOCK_ELIF_RE.fullmatch(line)
            if elif_match:
                if condition_support:
                    condition_support[-1] = (
                        condition_support[-1]
                        and _compute_condition_is_supported(
                            elif_match.group(1), aliases))
            elif line.casefold().startswith("if "):
                condition_support.append(
                    _compute_condition_is_supported(line[3:], aliases))
            elif line.casefold() == "endif":
                if condition_support:
                    condition_support.pop()
            if _condition_stack_line(line, condition_stack, aliases):
                continue
            match = _COMPUTE_T_COPY_RE.fullmatch(line)
            if match:
                t_sources[int(match.group("slot"))] = match.group(2)
                continue
            match = _COMPUTE_U_COPY_RE.fullmatch(line)
            if match:
                slot = int(match.group("slot"))
                if slot == 5:
                    close_chain()
                    current_chain = {
                        "output_resource": None,
                        "u5_resource": match.group(2),
                        "passes": [],
                    }
                continue
            match = _COMPUTE_U_NULL_RE.fullmatch(line)
            if match:
                slot = int(match.group("slot"))
                if slot == 5:
                    close_chain()
                continue
            match = _X88_RE.fullmatch(line)
            if match:
                phase_expr = match.group("expr").strip()
                continue
            match = _X89_RE.fullmatch(line)
            if match:
                bone_count_expr = match.group("expr").strip()
                continue
            match = _COMPUTE_RUN_RE.fullmatch(line)
            if match and current_chain is not None:
                child_section = section_lookup.get(
                    match.group("section").casefold())
                if (child_section is not None
                        and str(child_section).casefold().startswith(
                            "customshader")):
                    child_passes = nested_shape_passes(
                        child_section, t_sources.get(50),
                        current_chain.get("u5_resource"))
                    conditions = current_conditions(
                        condition_stack, condition_support)
                    if child_passes and conditions is not None:
                        nested = {
                            "child_section": child_section,
                            "conditions": conditions,
                            "passes": child_passes,
                        }
                        if current_chain.get("nested_run") is not None:
                            current_chain["nested_ambiguous"] = True
                        else:
                            current_chain["nested_run"] = nested
                continue
            match = _COMPUTE_SHADER_RE.fullmatch(line)
            if match:
                shader_path = _shader_path(
                    mod_dir, ini_path, match.group(1), source)
                text = _read_resource_bytes(shader_path, source)
                try:
                    text = text.decode("utf-8", errors="ignore") if text else None
                except AttributeError:
                    text = None
                active = _identify_compute_shader(text)
                continue
            match = _COMPUTE_DISPATCH_RE.fullmatch(line)
            if match:
                if current_chain is None:
                    continue
                if active is None:
                    continue
                base = t_sources.get(50)
                blend = t_sources.get(51)
                pose = t_sources.get(52)
                if not base or not blend:
                    continue
                kind = "pose" if pose else "shape"
                dispatch = int(match.group(1)) * active["threads"]
                snapshot = {
                    "kind": kind,
                    "dispatch_key": (str(section).casefold(), line_index),
                    "base_resource": base,
                    "dispatch_vertices": dispatch,
                    "phase_expr": phase_expr,
                }
                if kind == "shape":
                    snapshot["target_resource"] = blend
                else:
                    snapshot.update({
                        "coordinate_variant": active["coordinate_variant"],
                        "blend_resource": blend,
                        "pose_resource": pose,
                        "bone_count_expr": bone_count_expr,
                    })
                current_chain["passes"].append(snapshot)
                continue
            match = _COMPUTE_RESOURCE_RE.fullmatch(line)
            if match:
                if (current_chain is not None
                        and int(match.group("slot")) == 5):
                    current_chain["output_resource"] = match.group(1)
        close_chain()

    output_chains = {}
    for chain in chains:
        output = chain.get("output_resource")
        if output:
            output_chains.setdefault(str(output).casefold(), []).append(chain)

    pose_inputs = {
        str(item.get("base_resource", "")).casefold()
        for chain in chains
        for item in chain["passes"]
        if item.get("kind") == "pose"
    }

    literals = _literal_constant_assignments(sections, canonical)

    def phase_expression(expression):
        return _compile_expression(expression, canonical, var_prefix)

    def parse_shape_passes(items):
        parsed = []
        for item in items:
            expression = phase_expression(item.get("phase_expr", ""))
            if expression is None:
                return []
            parsed.append((item, expression))
        return parsed

    animations = []
    for chain_index, chain in enumerate(chains):
        output_resource = chain.get("output_resource")
        if not output_resource:
            continue
        pose_passes = [item for item in chain["passes"]
                       if item.get("kind") == "pose"]
        parent_animation = None
        if not pose_passes:
            shape_passes = [item for item in chain["passes"]
                            if item.get("kind") == "shape"]
            if (shape_passes
                    and str(output_resource).casefold() not in pose_inputs):
                validated = _validate_compute_layout(
                    resources, copy_sources, shape_passes,
                    mod_dir=mod_dir, source=source)
                parsed_shape_passes = parse_shape_passes(shape_passes)
                if validated is not None and parsed_shape_passes:
                    identity = json.dumps({
                        "ini": (source.logical_path(ini_path)
                                if source is not None
                                and source.is_resource_reference(ini_path)
                                else os.path.basename(str(ini_path or ""))),
                        "output": output_resource,
                        "base": shape_passes[0]["base_resource"],
                        "chain": chain_index,
                    }, sort_keys=True, separators=(",", ":"))
                    track_id = "gimi::" + hashlib.sha1(
                        identity.encode()).hexdigest()[:12]
                    parent_animation = {
                        "track_id": track_id,
                        "position_resource": output_resource,
                        "base_file": validated["base_file"],
                        "vertex_count": validated["vertex_count"],
                        "shape_passes": [{
                            "target_file": _resolved_resource(
                                resources, copy_sources,
                                item["target_resource"])["filename"],
                            "dispatch_vertices": item["dispatch_vertices"],
                            "phase_expr": expression,
                            "dispatch_key": item["dispatch_key"],
                        } for (item, expression) in parsed_shape_passes],
                        "pose": None,
                    }
        else:
            pose_pass = pose_passes[0]
            matching_shapes = output_chains.get(
                str(pose_pass["base_resource"]).casefold(), ())
            shape_passes = []
            parent_valid = True
            if matching_shapes:
                shape_chain = matching_shapes[0]
                shape_passes = [item for item in shape_chain["passes"]
                                if item.get("kind") == "shape"]
                parent_valid = bool(shape_passes)

            bone_count = _integer(_operand_value(
                pose_pass.get("bone_count_expr"), literals, canonical))
            if parent_valid and bone_count is not None:
                validated = _validate_compute_layout(
                    resources, copy_sources, shape_passes, pose_pass,
                    mod_dir=mod_dir, source=source, bone_count=bone_count)
                pose_expression = phase_expression(
                    pose_pass.get("phase_expr", ""))
                parsed_shape_passes = parse_shape_passes(shape_passes)
                if (validated is not None and pose_expression is not None
                        and (not shape_passes or parsed_shape_passes)):
                    identity = json.dumps({
                        "ini": (source.logical_path(ini_path)
                                if source is not None
                                and source.is_resource_reference(ini_path)
                                else os.path.basename(str(ini_path or ""))),
                        "output": output_resource,
                        "base": pose_pass["base_resource"],
                        "chain": chain_index,
                    }, sort_keys=True, separators=(",", ":"))
                    track_id = "gimi::" + hashlib.sha1(
                        identity.encode()).hexdigest()[:12]
                    parent_animation = {
                        "track_id": track_id,
                        "position_resource": output_resource,
                        "base_file": validated["base_file"],
                        "vertex_count": validated["vertex_count"],
                        "coordinate_variant": pose_pass.get(
                            "coordinate_variant", "standard"),
                        "shape_passes": [{
                            "target_file": _resolved_resource(
                                resources, copy_sources,
                                item["target_resource"])["filename"],
                            "dispatch_vertices": item["dispatch_vertices"],
                            "phase_expr": expression,
                            "dispatch_key": item["dispatch_key"],
                        } for (item, expression) in parsed_shape_passes],
                        "pose": {
                            "blend_file": validated["blend_file"],
                            "file": validated["pose_file"],
                            "bone_count": bone_count,
                            "frame_count": validated["frame_count"],
                            "phase_expr": pose_expression,
                            "dispatch_key": pose_pass["dispatch_key"],
                        },
                    }
        if parent_animation is not None:
            animations.append(parent_animation)
            continue

        nested = chain.get("nested_run")
        if chain.get("nested_ambiguous") or nested is None:
            continue
        shape_passes = nested["passes"]
        validated = _validate_compute_layout(
            resources, copy_sources, shape_passes,
            mod_dir=mod_dir, source=source)
        parsed_shape_passes = parse_shape_passes(shape_passes)
        if validated is None or not parsed_shape_passes:
            continue
        identity = json.dumps({
            "ini": (source.logical_path(ini_path) if source is not None
                    and source.is_resource_reference(ini_path)
                    else os.path.basename(str(ini_path or ""))),
            "output": output_resource,
            "base": shape_passes[0]["base_resource"],
            "child": nested["child_section"],
            "chain": chain_index,
        }, sort_keys=True, separators=(",", ":"))
        track_id = "gimi::" + hashlib.sha1(identity.encode()).hexdigest()[:12]
        animations.append({
            "track_id": track_id,
            "position_resource": output_resource,
            "base_file": validated["base_file"],
            "vertex_count": validated["vertex_count"],
            "shape_passes": [{
                "target_file": _resolved_resource(
                    resources, copy_sources, item["target_resource"])[
                        "filename"],
                "dispatch_vertices": item["dispatch_vertices"],
                "phase_expr": expression,
                "dispatch_key": item["dispatch_key"],
            } for (item, expression) in parsed_shape_passes],
            "pose": None,
            "overlay": True,
            "conditions": nested["conditions"],
        })
    if not animations:
        return []
    program = _compile_animation_program(
        sections, animations, canonical, var_prefix)
    if program is None:
        return []
    identity = (source.logical_path(ini_path) if source is not None
                and source.is_resource_reference(ini_path)
                else os.path.basename(str(ini_path or "")))
    program_id = "gimi-program::" + hashlib.sha1(
        str(identity).encode("utf-8")).hexdigest()[:12]
    for animation in animations:
        for item in animation.get("shape_passes", ()):
            item.pop("phase_expr", None)
            item.pop("dispatch_key", None)
        pose = animation.get("pose")
        if pose is not None:
            pose.pop("phase_expr", None)
            pose.pop("dispatch_key", None)
        animation["program_id"] = program_id
        animation["program"] = program
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

"""Small, deliberately narrow analysis of frame-baked mesh animations.

This module recognizes the clock expression used by the supported baked-mesh
mods.  It is not an expression evaluator: anything outside the supported
shape is ignored so ordinary INI analysis keeps its existing fail-open
behavior.
"""

from dataclasses import dataclass
import hashlib
import json
import os
import re

from .dnf import (DNF_TRUE, build_bool_alias_map, dnf_and, dnf_not, dnf_or,
                  normalize_dnf, parse_condition_dnf)
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
_ELIF_RE = re.compile(r"(?:else\s+if|elif)\s+(.*)$", re.I)
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
_COMPUTE_SHADER_RE = re.compile(r"^\s*cs\s*=\s*(\S+)\s*$", re.I)
_COMPUTE_DISPATCH_RE = re.compile(
    r"^\s*dispatch\s*=\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*$",
    re.I)
_NUMTHREADS_RE = re.compile(
    r"\[\s*numthreads\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)\s*\]",
    re.I)
_REGISTER_RE = re.compile(
    r"\b(?P<kind>rwstructuredbuffer|structuredbuffer)\s*<[^>]+>\s+"
    r"(?P<name>\w+)\s*:\s*register\s*\(\s*(?P<reg>[ut]\d+)\s*\)",
    re.I)
_X88_RE = re.compile(r"^\s*x88\s*=\s*(?P<expr>.+?)\s*$", re.I)
_X89_RE = re.compile(r"^\s*x89\s*=\s*(?P<expr>.+?)\s*$", re.I)
_RUNTIME_UPDATE_RE = re.compile(
    r"^\s*\$(?P<var>\w+)\s*=\s*\$(?P=var)\s*\+\s*"
    r"(?P<speed>\$\w+|[-+]?\d+(?:\.\d+)?)\s*\*\s*"
    r"(?P<dt>\$\w+|[-+]?\d+(?:\.\d+)?)\s*$", re.I)
_PHASE_WRAP_RE = re.compile(
    r"^\s*if\s+\$(?P<var>\w+)\s*>\s*(?P<limit>.+?)\s*$", re.I)
_SHADER_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"


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


def _condition_stack_line(line, stack, aliases):
    """Advance a small if/elif/else stack and report control lines."""
    low = line.lower()
    match = _ELIF_RE.fullmatch(line)
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
                              canonical_vars=None, qualified_vars=None):
    """Discover supported clocks and the source-spelling frame variables.

    The returned ``frame_vars`` intentionally uses the local INI spelling;
    the draw scanner consumes source conditions before namespacing.  Clock
    payloads use the public, namespaced spelling.
    """
    canonical = canonical_vars or canonical_var_names(sections)
    literals = _literal_assignments(sections, canonical)
    aliases = build_bool_alias_map(sections)
    all_vars = set(canonical.values())
    clocks = []
    seen = set()

    for section_name, lines in sections.items():
        stack = []
        for raw in lines:
            line = str(raw).split(";", 1)[0].strip()
            if not line:
                continue
            if _condition_stack_line(line, stack, aliases):
                continue
            match = (_CLOCK_RE.fullmatch(line)
                     or _CLOCK_LITERAL_RANGE_RE.fullmatch(line))
            if not match:
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


def _shape_constants(text):
    """Read the small sinusoid form used by the supported shape kernel."""
    compact = re.sub(r"\s+", "", _strip_hlsl_comments(text)).lower()
    number = _SHADER_NUMBER
    match = re.search(
        rf"(?P<amplitude>{number})\*\(sin\([^()]+\*(?P<angular>{number})\)\+1\)",
        compact)
    if match:
        amplitude = float(match.group("amplitude"))
        return {
            "amplitude": amplitude,
            "angular_scale": float(match.group("angular")),
            "bias": amplitude,
        }
    match = re.search(
        rf"(?P<amplitude>{number})\*sin\([^()]+\*(?P<angular>{number})\)"
        rf"\+(?P<bias>{number})", compact)
    if match:
        return {
            "amplitude": float(match.group("amplitude")),
            "angular_scale": float(match.group("angular")),
            "bias": float(match.group("bias")),
        }
    return None


def _pose_basis(text, base_name, output_name):
    """Recognize the conservative axis-basis conversion used by some kernels."""
    compact = re.sub(r"\s+", "", _strip_hlsl_comments(text)).lower()
    base = re.escape(base_name)
    output = re.escape(output_name)
    pre = (
        rf"(?P<position>[a-z_]\w*)\.x={base}\[i\]\.position\.x\*-1\.0f?"
        rf".*(?P=position)\.z={base}\[i\]\.position\.y\*-1\.0f?"
        rf".*(?P=position)\.y={base}\[i\]\.position\.z"
    )
    pre_normal = (
        rf"(?P<normal>[a-z_]\w*)\.x={base}\[i\]\.normal\.x\*-1\.0f?"
        rf".*(?P=normal)\.z={base}\[i\]\.normal\.y\*-1\.0f?"
        rf".*(?P=normal)\.y={base}\[i\]\.normal\.z"
    )
    post = (
        rf"{output}\[i\]\.position\.x=(?P<position_result>[a-z_]\w*)\.x\*-1\.0f?"
        rf".*{output}\[i\]\.position\.y=(?P=position_result)\.z\*-1\.0f?"
        rf".*{output}\[i\]\.position\.z=(?P=position_result)\.y"
    )
    post_normal = (
        rf"{output}\[i\]\.normal\.x=(?P<normal_result>[a-z_]\w*)\.x\*-1\.0f?"
        rf".*{output}\[i\]\.normal\.y=(?P=normal_result)\.z\*-1\.0f?"
        rf".*{output}\[i\]\.normal\.z=(?P=normal_result)\.y"
    )
    if not all(re.search(pattern, compact) for pattern in
               (pre, pre_normal, post, post_normal)):
        return None
    return {
        "pre": [-1, 0, 0, 0, 0, 1, 0, -1, 0],
        "post": [-1, 0, 0, 0, 0, -1, 0, 1, 0],
    }


def _shader_signature(text):
    """Classify the fixed-layout kernels by their declarations and operations."""
    if not text:
        return None
    cleaned = _strip_hlsl_comments(text)
    compact = re.sub(r"\s+", "", cleaned).lower()
    declarations = {
        match.group("reg").lower(): {
            "kind": match.group("kind").lower(),
            "name": match.group("name").lower(),
        }
        for match in _REGISTER_RE.finditer(cleaned)
    }
    threads = _NUMTHREADS_RE.search(cleaned)
    if threads is None or "sv_dispatchthreadid" not in compact:
        return None
    thread_dims = tuple(int(threads.group(index)) for index in range(1, 4))
    if thread_dims[0] <= 0 or thread_dims[1:] != (1, 1):
        return None
    required = {"u5", "t50", "t51"}
    if not required.issubset(declarations):
        return None

    def alias(reg):
        item = declarations.get(reg)
        return re.escape(item["name"]) if item else None

    output = alias("u5")
    base = alias("t50")
    target = alias("t51")
    shape = False
    shape_data = None
    if output and base and target:
        has_position_delta = re.search(
            rf"{target}\[i\]\.position-{base}\[i\]\.position", compact)
        has_normal_delta = re.search(
            rf"{target}\[i\]\.normal-{base}\[i\]\.normal", compact)
        has_position_write = re.search(
            rf"{output}\[i\]\.position\+=", compact)
        has_normal_write = re.search(
            rf"{output}\[i\]\.normal\+=", compact)
        shape = bool(has_position_delta and has_normal_delta
                     and has_position_write and has_normal_write
                     and "sin(" in compact)
        if shape:
            shape_data = _shape_constants(cleaned)

    pose = False
    if {"u5", "t50", "t51", "t52"}.issubset(declarations):
        pose_aliases = [alias(reg) for reg in ("u5", "t50", "t51", "t52")]
        pose = bool(
            all(pose_aliases)
            and re.search(r"\[[^]]*88[^]]*\]", compact)
            and re.search(r"\[[^]]*89[^]]*\]", compact)
            and ("frac(time)" in compact or "time-floor(time)" in compact)
            and ".qr" in compact and ".qd" in compact
            and ".s" in compact and ".t" in compact
            and ("normalize" in compact or "length(" in compact)
            and "dot(" in compact
            and re.search(rf"{pose_aliases[1]}\[i\]", compact)
            and re.search(rf"{pose_aliases[2]}\[i\]", compact)
            and re.search(rf"{pose_aliases[3]}\[", compact)
            and re.search(rf"{pose_aliases[0]}\[i\]\.position", compact)
            and re.search(rf"{pose_aliases[0]}\[i\]\.normal", compact)
        )
    if pose and not shape:
        return {
            "kind": "pose", "threads": thread_dims[0],
            "basis": _pose_basis(cleaned, declarations["t50"]["name"],
                                  declarations["u5"]["name"]),
        }
    if shape and not pose:
        return {"kind": "shape", "threads": thread_dims[0],
                "shape": shape_data}
    return None


def _literal_number(value, literals, canonical):
    value = str(value).strip()
    if value.startswith("$"):
        return literals.get(_canonical(value, canonical).casefold())
    return _numeric(value)


def _combined_conditions(stack, canonical, var_prefix=None):
    combined = DNF_TRUE
    for frame in stack:
        combined = dnf_and(combined, frame["cur"])
    return normalize_dnf(combined, set(canonical.values()), var_prefix)


def _operand(value, literals, canonical, var_prefix=None):
    """Normalize the intentionally tiny runtime operand grammar."""
    text = str(value).strip().strip("()")
    match = re.fullmatch(r"(\$\w+)\s*([+-])\s*(%s)" % _NUMBER_RE,
                         text, re.I)
    if match:
        local = _canonical(match.group(1), canonical)
        offset = float(match.group(3))
        if match.group(2) == "-":
            offset = -offset
        return {"kind": "variable", "variable": f"{var_prefix or ''}{local}",
                "offset": offset}
    if text.startswith("$"):
        local = _canonical(text, canonical)
        return {"kind": "variable", "variable": f"{var_prefix or ''}{local}",
                "offset": 0}
    number = _numeric(text)
    if number is None:
        return None
    return {"kind": "literal", "value": number}


def _phase_bindings(sections, canonical, *, var_prefix=None):
    """Find phase updates, their exact guards, wraps, and simple resets."""
    literals = _literal_assignments(sections, canonical)
    aliases = build_bool_alias_map(sections)
    updates = {}
    for lines in sections.values():
        stack = []
        pending_wrap = None
        pending_reset = None
        for raw in lines:
            line = str(raw).split(";", 1)[0].strip()
            if not line:
                continue
            if _condition_stack_line(line, stack, aliases):
                wrap = _PHASE_WRAP_RE.fullmatch(line)
                pending_wrap = None
                pending_reset = None
                if wrap:
                    pending_wrap = {
                        "var": _canonical(wrap.group("var"), canonical),
                        "limit": _operand(wrap.group("limit"), literals,
                                          canonical, var_prefix),
                    }
                continue
            update = _RUNTIME_UPDATE_RE.fullmatch(line)
            if update:
                local = _canonical(update.group("var"), canonical)
                rate = _operand(update.group("speed"), literals, canonical,
                                var_prefix)
                dt = update.group("dt")
                if rate is not None and (dt.startswith("$")
                                         or _numeric(dt) is not None):
                    item = updates.setdefault(local.casefold(), {
                        "variable": f"{var_prefix or ''}{local}",
                        "rate": rate,
                        "advance_conditions": _combined_conditions(
                            stack, canonical, var_prefix),
                        "reset_rules": [],
                        "wrap_limit": None,
                        "wrap_target": None,
                    })
                    item["advance_conditions"] = _combined_conditions(
                        stack, canonical, var_prefix)
                pending_wrap = None
                pending_reset = None
                continue
            assignment = _COMPUTE_ASSIGN_RE.fullmatch(line)
            if not assignment or not assignment.group("lhs").startswith("$"):
                pending_wrap = None
                pending_reset = None
                continue
            local = _canonical(assignment.group("lhs"), canonical)
            item = updates.get(local.casefold())
            if item is None:
                value = _operand(assignment.group("rhs"), literals, canonical,
                                 var_prefix)
                if (pending_reset is not None
                        and value is not None
                        and value.get("kind") == "literal"
                        and pending_reset["conditions"] ==
                        _combined_conditions(stack, canonical, var_prefix)):
                    condition_vars = {
                        str(clause.get("var", "")).casefold()
                        for group in pending_reset["conditions"]
                        for clause in group
                    }
                    trigger = f"{var_prefix or ''}{local}".casefold()
                    if condition_vars == {trigger}:
                        pending_reset["clear"] = {
                            "variable": f"{var_prefix or ''}{local}",
                            "value": value,
                        }
                pending_wrap = None
                pending_reset = None
                continue
            value = _operand(assignment.group("rhs"), literals, canonical,
                             var_prefix)
            if value is None:
                pending_wrap = None
                pending_reset = None
                continue
            if (pending_wrap is not None
                    and pending_wrap["var"].casefold() == local.casefold()):
                item["wrap_limit"] = pending_wrap["limit"]
                item["wrap_target"] = value
                pending_wrap = None
                pending_reset = None
                continue
            # A phase reset must be guarded by a real control condition. The
            # phase comparison in a wrap is not a control dependency.
            conditions = _combined_conditions(stack, canonical, var_prefix)
            phase_clause = f"{var_prefix or ''}{local}".casefold()
            conditions = [
                [clause for clause in group
                 if str(clause.get("var", "")).casefold() != phase_clause]
                for group in conditions
            ]
            conditions = [group for group in conditions if group]
            if conditions:
                reset_rule = {
                    "conditions": conditions,
                    "value": value,
                }
                item["reset_rules"].append(reset_rule)
                pending_reset = reset_rule
            else:
                pending_reset = None
            pending_wrap = None
    return updates


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


def _validate_compute_buffers(resources, copy_sources, shape_passes, pose,
                              *, mod_dir, source, bone_count):
    """Validate fixed layouts and dimensions before exposing a descriptor."""
    base_resource = (shape_passes[0]["base_resource"] if shape_passes
                     else pose["base_resource"])
    base_info = _resolved_resource(resources, copy_sources, base_resource)
    blend_info = _resolved_resource(resources, copy_sources,
                                    pose["blend_resource"])
    pose_info = _resolved_resource(resources, copy_sources,
                                   pose["pose_resource"])
    if (base_info.get("stride") != 40 or blend_info.get("stride") != 32
            or pose_info.get("stride") != 56 or not bone_count):
        return None
    base_path = _resource_path(mod_dir, base_info.get("filename"), source)
    blend_path = _resource_path(mod_dir, blend_info.get("filename"), source)
    pose_path = _resource_path(mod_dir, pose_info.get("filename"), source)
    base_size = _resource_size(base_path, source)
    blend_size = _resource_size(blend_path, source)
    pose_size = _resource_size(pose_path, source)
    if base_size is None or blend_size is None or pose_size is None:
        return None
    if base_size % 40:
        return None
    base_count = base_size // 40
    if (blend_size != base_count * 32
            or pose_size % (bone_count * 56)):
        return None
    frame_count = pose_size // (bone_count * 56)
    if base_count <= 0 or frame_count < 2:
        return None
    for item in shape_passes:
        info = _resolved_resource(resources, copy_sources,
                                  item["target_resource"])
        if info.get("stride") != 40:
            return None
        path = _resource_path(mod_dir, info.get("filename"), source)
        size = _resource_size(path, source)
        if size != base_count * 40:
            return None
    return {
        "base_file": base_info["filename"],
        "blend_file": blend_info["filename"],
        "pose_file": pose_info["filename"],
        "vertex_count": base_count,
        "frame_count": frame_count,
    }


def _validate_shape_buffers(resources, copy_sources, shape_passes, *,
                            mod_dir, source):
    """Validate a shape-only chain before exposing its descriptor."""
    if not shape_passes:
        return None
    base_info = _resolved_resource(
        resources, copy_sources, shape_passes[0]["base_resource"])
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
        info = _resolved_resource(
            resources, copy_sources, item["target_resource"])
        if info.get("stride") != 40:
            return None
        path = _resource_path(mod_dir, info.get("filename"), source)
        if _resource_size(path, source) != base_size:
            return None
    return {
        "base_file": base_info["filename"],
        "vertex_count": vertex_count,
    }


def discover_compute_animations(sections, resources, *, mod_dir=None,
                               ini_path=None, source=None, var_prefix=None,
                               canonical_vars=None):
    """Discover the conservative fixed-layout compute-animation contract."""
    canonical = canonical_vars or canonical_var_names(sections)
    from .draw_resources import _collect_resource_copy_sources
    copy_sources = _collect_resource_copy_sources(sections, resources)
    chains = []
    for section, lines in sections.items():
        if not str(section).casefold().startswith("customshader"):
            continue
        t_sources = {}
        active = None
        phase_expr = None
        bone_count_expr = None
        current_chain = None

        def close_chain():
            nonlocal current_chain
            if current_chain is not None:
                chains.append(current_chain)
                current_chain = None

        for raw in lines:
            line = str(raw).split(";", 1)[0].strip()
            if not line:
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
                        "uav_resource": match.group(2),
                        "output_resource": None,
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
            match = _COMPUTE_SHADER_RE.fullmatch(line)
            if match:
                shader_path = _shader_path(
                    mod_dir, ini_path, match.group(1), source)
                text = _read_resource_bytes(shader_path, source)
                try:
                    text = text.decode("utf-8", errors="ignore") if text else None
                except AttributeError:
                    text = None
                active = _shader_signature(text)
                continue
            match = _COMPUTE_DISPATCH_RE.fullmatch(line)
            if match:
                if current_chain is None:
                    continue
                snapshot = {"supported": False}
                if active is not None:
                    snapshot["kind"] = active["kind"]
                    if t_sources.get(50):
                        snapshot["base_resource"] = t_sources[50]
                if (active is not None and match.group(2) == "1"
                        and match.group(3) == "1"):
                    dispatch = int(match.group(1)) * active["threads"]
                    if active["kind"] == "shape":
                        base = t_sources.get(50)
                        target = t_sources.get(51)
                        if base and target and active.get("shape"):
                            snapshot = {
                                "supported": True,
                                "kind": "shape",
                                "base_resource": base,
                                "target_resource": target,
                                "uav_resource": current_chain[
                                    "uav_resource"],
                                "dispatch_vertices": dispatch,
                                "phase_expr": phase_expr,
                                "shape": active["shape"],
                            }
                    elif active["kind"] == "pose":
                        base = t_sources.get(50)
                        blend = t_sources.get(51)
                        pose = t_sources.get(52)
                        if base and blend and pose:
                            snapshot = {
                                "supported": True,
                                "kind": "pose",
                                "base_resource": base,
                                "blend_resource": blend,
                                "pose_resource": pose,
                                "uav_resource": current_chain[
                                    "uav_resource"],
                                "dispatch_vertices": dispatch,
                                "phase_expr": phase_expr,
                                "bone_count_expr": bone_count_expr,
                                "basis": active.get("basis"),
                            }
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

    literals = _literal_assignments(sections, canonical)
    updates = _phase_bindings(
        sections, canonical, var_prefix=var_prefix)
    pose_inputs = {
        str(item.get("base_resource", "")).casefold()
        for chain in chains
        for item in chain["passes"]
        if item.get("kind") == "pose"
    }

    def phase_operand(expression):
        operand = _operand(expression, literals, canonical, var_prefix)
        if operand is None or operand.get("kind") != "variable":
            return None
        return operand

    def parse_shape_passes(items):
        parsed = []
        shape_clock = None
        shape_local = None
        for item in items:
            operand = phase_operand(item.get("phase_expr", ""))
            if operand is None:
                return [], None
            local = _unprefix(operand["variable"], var_prefix)
            if shape_local is None:
                shape_local = local
                shape_clock = updates.get(local.casefold())
            if (shape_clock is None
                    or local.casefold() != shape_local.casefold()):
                return [], None
            parsed.append((item, operand))
        return parsed, shape_clock

    animations = []
    for chain_index, chain in enumerate(chains):
        output_resource = chain.get("output_resource")
        if not output_resource:
            continue
        if any(not item.get("supported") for item in chain["passes"]):
            continue
        pose_passes = [item for item in chain["passes"]
                       if item.get("supported") and item.get("kind") == "pose"]
        if not pose_passes:
            shape_passes = [item for item in chain["passes"]
                            if item.get("kind") == "shape"]
            if (not shape_passes
                    or len(shape_passes) != len(chain["passes"])
                    or str(output_resource).casefold() in pose_inputs):
                continue
            validated = _validate_shape_buffers(
                resources, copy_sources, shape_passes,
                mod_dir=mod_dir, source=source)
            if validated is None:
                continue
            parsed_shape_passes, shape_clock = parse_shape_passes(
                shape_passes)
            if not parsed_shape_passes:
                continue
            identity = json.dumps({
                "ini": (source.logical_path(ini_path) if source is not None
                        and source.is_resource_reference(ini_path)
                        else os.path.basename(str(ini_path or ""))),
                "output": output_resource,
                "base": shape_passes[0]["base_resource"],
                "chain": chain_index,
            }, sort_keys=True, separators=(",", ":"))
            track_id = "gimi::" + hashlib.sha1(identity.encode()).hexdigest()[:12]
            animations.append({
                "kind": "gimi_compute",
                "track_id": track_id,
                "position_resource": output_resource,
                "base_resource": shape_passes[0]["base_resource"],
                "base_file": validated["base_file"],
                "vertex_count": validated["vertex_count"],
                "shape_passes": [{
                    "target_resource": item["target_resource"],
                    "target_file": _resolved_resource(
                        resources, copy_sources, item["target_resource"])[
                            "filename"],
                    "dispatch_vertices": item["dispatch_vertices"],
                    "phase_offset": float(operand.get("offset", 0)),
                    **item["shape"],
                } for item, operand in parsed_shape_passes],
                "pose": None,
                "shape_clock": shape_clock,
                "pose_clock": None,
            })
            continue
        pose_pass = pose_passes[-1]
        matching_shapes = output_chains.get(
            str(pose_pass["base_resource"]).casefold(), ())
        shape_passes = []
        if matching_shapes:
            if len(matching_shapes) != 1:
                continue
            shape_chain = matching_shapes[0]
            if any(not item.get("supported") for item in shape_chain["passes"]):
                continue
            shape_passes = [item for item in shape_chain["passes"]
                            if item.get("kind") == "shape"]
            if not shape_passes or len(shape_passes) != len(
                    shape_chain["passes"]):
                continue
        elif not _resolved_resource(
                resources, copy_sources, pose_pass["base_resource"]).get(
                    "filename"):
            # A pose input that came from an unsupported compute chain cannot
            # silently become a pose-only animation.
            continue

        bone_count = _integer(_literal_number(
            pose_pass.get("bone_count_expr"), literals, canonical))
        if bone_count is None:
            continue
        validated = _validate_compute_buffers(
            resources, copy_sources, shape_passes, pose_pass,
            mod_dir=mod_dir, source=source, bone_count=bone_count)
        if validated is None:
            continue

        pose_operand = phase_operand(pose_pass.get("phase_expr", ""))
        if pose_operand is None:
            continue
        pose_local = _unprefix(pose_operand["variable"], var_prefix)
        pose_clock = updates.get(pose_local.casefold())
        if pose_clock is None:
            continue

        parsed_shape_passes, shape_clock = parse_shape_passes(shape_passes)
        if shape_passes and not parsed_shape_passes:
            continue

        identity = json.dumps({
            "ini": (source.logical_path(ini_path) if source is not None
                    and source.is_resource_reference(ini_path)
                    else os.path.basename(str(ini_path or ""))),
            "output": output_resource,
            "base": pose_pass["base_resource"],
            "chain": chain_index,
        }, sort_keys=True, separators=(",", ":"))
        track_id = "gimi::" + hashlib.sha1(identity.encode()).hexdigest()[:12]
        animations.append({
            "kind": "gimi_compute",
            "track_id": track_id,
            "position_resource": output_resource,
            "base_resource": pose_pass["base_resource"],
            "base_file": validated["base_file"],
            "vertex_count": validated["vertex_count"],
            "shape_passes": [{
                "target_resource": item["target_resource"],
                "target_file": _resolved_resource(
                    resources, copy_sources, item["target_resource"])[
                        "filename"],
                "dispatch_vertices": item["dispatch_vertices"],
                "phase_offset": float(operand.get("offset", 0)),
                **item["shape"],
            } for item, operand in parsed_shape_passes],
            "pose": {
                "base_resource": pose_pass["base_resource"],
                "blend_resource": pose_pass["blend_resource"],
                "blend_file": validated["blend_file"],
                "resource": pose_pass["pose_resource"],
                "file": validated["pose_file"],
                "bone_count": bone_count,
                "frame_count": validated["frame_count"],
                "dispatch_vertices": pose_pass["dispatch_vertices"],
                "basis": pose_pass.get("basis"),
            },
            "shape_clock": shape_clock,
            "pose_clock": pose_clock,
        })
    return animations


def compute_animation_control_vars(animations, state_rules=()):
    """Return only controls that can change an animation clock's behavior."""
    dependencies = set()

    def add_conditions(conditions):
        for group in conditions or ():
            for clause in group:
                dependencies.add(str(clause.get("var", "")))

    def add_operand(operand):
        if operand and operand.get("kind") == "variable":
            dependencies.add(str(operand.get("variable", "")))

    for animation in animations or ():
        for clock_key in ("shape_clock", "pose_clock"):
            clock = animation.get(clock_key)
            if not clock:
                continue
            add_conditions(clock.get("advance_conditions"))
            add_operand(clock.get("wrap_limit"))
            add_operand(clock.get("wrap_target"))
            for rule in clock.get("reset_rules", ()):
                add_conditions(rule.get("conditions"))
                add_operand(rule.get("value"))

    # State rules can derive one of those direct inputs from a user-facing
    # controller. Follow that small existing rule chain without introducing a
    # second dependency graph for compute animations.
    direct = {value.casefold() for value in dependencies}
    for rule in state_rules or ():
        if str(rule.get("var", "")).casefold() in direct:
            add_conditions(rule.get("conditions"))
    return dependencies


__all__ = [
    "AnimationAnalysis", "AnimationClock", "discover_animation_clocks",
    "frame_condition", "discover_compute_animations",
    "compute_animation_control_vars",
]

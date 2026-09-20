"""Small, deliberately narrow analysis of frame-baked mesh animations.

This module recognizes the clock expression used by the supported baked-mesh
mods.  It is not an expression evaluator: anything outside the supported
shape is ignored so ordinary INI analysis keeps its existing fail-open
behavior.
"""

from dataclasses import dataclass
import hashlib
import json
import math
import os
import re
import struct

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
    r"\w+\s*:\s*register\s*\(\s*(?P<reg>[ut]\d+)\s*\)", re.I)
_X88_RE = re.compile(r"^\s*x88\s*=\s*(?P<expr>.+?)\s*$", re.I)
_STATE_BRANCH_RE = re.compile(
    r"^\s*(?:if|elif|else\s+if)\s+\$(?P<var>\w+)\s*==\s*"
    r"(?P<state>\d+)\s*$", re.I)
_AUTO_BRANCH_RE = re.compile(
    r"^\s*(?:if|elif|else\s+if)\s+\$(?P<state>\w+)\s*==\s*"
    r"(?P<from>\d+)\s*&&\s*\$(?P<loop>\w+)\s*>\s*(?P<threshold>\d+)\s*$",
    re.I)
_VAR_REF_RE = re.compile(r"\$(\w+)")
_RUNTIME_UPDATE_RE = re.compile(
    r"^\s*\$(?P<var>\w+)\s*=\s*\$(?P=var)\s*\+\s*"
    r"(?P<speed>\$\w+|[-+]?\d+(?:\.\d+)?)\s*\*\s*"
    r"(?P<dt>\$\w+|[-+]?\d+(?:\.\d+)?)\s*$", re.I)


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


def _shader_signature(text):
    """Classify only the two known, fixed-layout compute shader families."""
    if not text:
        return None
    registers = {
        match.group("reg").lower()
        for match in _REGISTER_RE.finditer(text)
    }
    threads = _NUMTHREADS_RE.search(text)
    if threads is None:
        return None
    thread_dims = tuple(int(threads.group(index)) for index in range(1, 4))
    if thread_dims != (64, 1, 1) or "sv_dispatchthreadid" not in text.lower():
        return None
    low = text.lower()
    if not {"u5", "t50", "t51"}.issubset(registers):
        return None
    if ("shapekey[i].position - base[i].position" not in low
            or "shapekey[i].normal - base[i].normal" not in low
            or "sin(" not in low
            or "rw_buffer[i].position +=" not in low
            or "rw_buffer[i].normal +=" not in low):
        shape = False
    else:
        shape = True
    pose = ({"u5", "t50", "t51", "t52"}.issubset(registers)
            and "iniparams[88]" in low and "iniparams[89]" in low
            and "frac(time)" in low
            and ".qr" in low and ".qd" in low
            and ".s" in low and ".t" in low
            and "normalize" in low
            and "rw_buffer[i].position" in low
            and "rw_buffer[i].normal" in low)
    if shape and not pose:
        return {"kind": "shape", "threads": thread_dims[0]}
    if pose and not shape:
        return {"kind": "pose", "threads": thread_dims[0]}
    return None


def _literal_number(value, literals, canonical):
    value = str(value).strip()
    if value.startswith("$"):
        return literals.get(_canonical(value, canonical).casefold())
    return _numeric(value)


def _phase_bindings(sections, canonical):
    """Return runtime phase updates and the variable used for pause."""
    literals = _literal_assignments(sections, canonical)
    updates = {}
    pause_var = None
    for lines in sections.values():
        zero_candidate = None
        for raw in lines:
            line = str(raw).split(";", 1)[0].strip()
            zero_match = re.match(
                r"^\s*if\s+\$(\w+)\s*==\s*0\s*$", line, re.I)
            if zero_match:
                zero_candidate = _canonical(zero_match.group(1), canonical)
            update = _RUNTIME_UPDATE_RE.fullmatch(line)
            if update:
                local = _canonical(update.group("var"), canonical)
                speed = _literal_number(update.group("speed"), literals, canonical)
                dt = update.group("dt")
                if speed is not None and (dt.startswith("$") or _numeric(dt) is not None):
                    updates[local.casefold()] = {
                        "var": local,
                        "speed": float(speed),
                        "dt_var": (_canonical(dt, canonical)
                                   if dt.startswith("$") else None),
                    }
                if pause_var is None and zero_candidate is not None:
                    pause_var = zero_candidate
    return updates, pause_var


def _state_ranges(sections, canonical):
    ranges = {}
    current_var = None
    for section, lines in sections.items():
        if str(section).casefold() != "present":
            continue
        current = None
        for raw in lines:
            line = str(raw).split(";", 1)[0].strip()
            branch = _STATE_BRANCH_RE.fullmatch(line)
            if branch:
                current_var = _canonical(branch.group("var"), canonical)
                current = int(branch.group("state"))
                continue
            if line.casefold() in {"endif", "else"}:
                if line.casefold() == "endif":
                    current = None
                continue
            if current is None:
                continue
            assignment = _COMPUTE_ASSIGN_RE.fullmatch(line)
            if not assignment or not assignment.group("lhs").startswith("$"):
                continue
            local = _canonical(assignment.group("lhs").lstrip("$"), canonical)
            value = _integer(assignment.group("rhs"))
            if value is None:
                continue
            item = ranges.setdefault(current_var, {}).setdefault(current, {})
            if local.casefold().startswith("strat"):
                item["start"] = value
                item["start_var"] = local
            elif local.casefold().startswith("end"):
                item["end"] = value
                item["end_var"] = local
    candidates = []
    for candidate_var, candidate_ranges in ranges.items():
        result = []
        for state, item in sorted(candidate_ranges.items()):
            if ("start" not in item or "end" not in item
                    or item["end"] < item["start"]):
                continue
            result.append({"state": state, "start": item["start"],
                           "end": item["end"]})
        if result:
            candidates.append((candidate_var, result))
    if not candidates:
        return None, None
    return max(candidates, key=lambda item: len(item[1]))


def _autoplay_machine(sections, canonical):
    for section, lines in sections.items():
        if str(section).casefold() != "present":
            continue
        for index, raw in enumerate(lines):
            gate = re.fullmatch(
                r"\s*if\s+\$(\w+)\s*==\s*1\s*", str(raw), re.I)
            if not gate:
                continue
            transitions = []
            for branch_index, later in enumerate(lines[index + 1:], index + 1):
                text = str(later).split(";", 1)[0].strip()
                branch = _AUTO_BRANCH_RE.fullmatch(text)
                if not branch:
                    if text.casefold() == "endif":
                        break
                    continue
                state_var = _canonical(branch.group("state"), canonical)
                loop_var = _canonical(branch.group("loop"), canonical)
                from_state = int(branch.group("from"))
                threshold = int(branch.group("threshold"))
                destination = None
                loop_reset = False
                for candidate in lines[branch_index + 1:]:
                    candidate_text = str(candidate).split(";", 1)[0].strip()
                    if (candidate_text.casefold() == "endif"
                            or re.match(r"^(?:if|elif|else\s+if)\b",
                                        candidate_text, re.I)):
                        break
                    assignment = _COMPUTE_ASSIGN_RE.fullmatch(candidate_text)
                    if not assignment:
                        continue
                    lhs = assignment.group("lhs").lstrip("$")
                    rhs = assignment.group("rhs").strip()
                    if _canonical(lhs, canonical).casefold() == state_var.casefold():
                        destination = _integer(rhs)
                    elif (_canonical(lhs, canonical).casefold()
                          == loop_var.casefold() and _integer(rhs) == 0):
                        loop_reset = True
                    if destination is not None and loop_reset:
                        break
                if destination is None or not loop_reset:
                    continue
                transitions.append({"from": from_state, "to": destination,
                                    "threshold": threshold})
            normalized = {(item["from"], item["to"], item["threshold"])
                          for item in transitions}
            expected = {(0, 1, 50), (1, 2, 50), (2, 0, 1)}
            if normalized == expected and len(transitions) == 3:
                return {
                    "var": _canonical(gate.group(1), canonical),
                    "state_var": state_var,
                    "loop_var": loop_var,
                    "transitions": transitions,
                }
    return None


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
    base_info = _resolved_resource(resources, copy_sources,
                                   shape_passes[0]["base_resource"])
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
    base_data = _read_resource_bytes(base_path, source)
    blend_data = _read_resource_bytes(blend_path, source)
    pose_data = _read_resource_bytes(pose_path, source)
    if not base_data or not blend_data or not pose_data:
        return None
    base_count = len(base_data) // 40
    if (len(base_data) != base_count * 40
            or len(blend_data) != base_count * 32
            or len(pose_data) % (bone_count * 56)):
        return None
    frame_count = len(pose_data) // (bone_count * 56)
    if base_count <= 0 or frame_count < 2:
        return None
    for offset in range(0, len(blend_data), 32):
        weights = struct.unpack_from("<4f", blend_data, offset)
        indices = struct.unpack_from("<4i", blend_data, offset + 16)
        if (not all(math.isfinite(value) for value in weights)
                or any(index < 0 or index >= bone_count for index in indices)):
            return None
    for item in shape_passes:
        info = _resolved_resource(resources, copy_sources,
                                  item["target_resource"])
        if info.get("stride") != 40:
            return None
        path = _resource_path(mod_dir, info.get("filename"), source)
        data = _read_resource_bytes(path, source)
        if data is None or len(data) != base_count * 40:
            return None
    return {
        "base_file": base_info["filename"],
        "blend_file": blend_info["filename"],
        "pose_file": pose_info["filename"],
        "vertex_count": base_count,
        "frame_count": frame_count,
    }


def discover_compute_animations(sections, resources, *, mod_dir=None,
                               ini_path=None, source=None, var_prefix=None,
                               canonical_vars=None):
    """Recognize the conservative fixed-layout GIMI compute-animation family.

    This deliberately describes one known data contract instead of attempting
    to interpret arbitrary HLSL.  The result is backend metadata consumed by
    the existing mesh/GeometryBlob animation path.
    """
    canonical = canonical_vars or canonical_var_names(sections)
    from .draw_resources import _collect_resource_copy_sources
    copy_sources = _collect_resource_copy_sources(sections, resources)
    shape_passes = []
    pose_pass = None
    for section, lines in sections.items():
        if not str(section).casefold().startswith("customshader"):
            continue
        u_sources = {}
        t_sources = {}
        active = None
        pending = None
        pending_output = None
        phase_expr = None
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
                u_sources[int(match.group("slot"))] = match.group(2)
                continue
            match = _COMPUTE_U_NULL_RE.fullmatch(line)
            if match:
                u_sources.pop(int(match.group("slot")), None)
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
                active = _shader_signature(text)
                pending = None
                pending_output = None
                continue
            match = _COMPUTE_DISPATCH_RE.fullmatch(line)
            if match and active is not None:
                dispatch = int(match.group(1)) * active["threads"]
                if active["kind"] == "shape":
                    base = t_sources.get(50)
                    target = t_sources.get(51)
                    uav = u_sources.get(5)
                    if base and target and uav:
                        pending = {
                            "base_resource": base,
                            "target_resource": target,
                            "uav_resource": uav,
                            "dispatch_vertices": dispatch,
                            "phase_expr": phase_expr,
                            **(pending_output or {}),
                        }
                        shape_passes.append(pending)
                elif active["kind"] == "pose":
                    base = t_sources.get(50)
                    blend = t_sources.get(51)
                    pose = t_sources.get(52)
                    uav = u_sources.get(5)
                    if base and blend and pose and uav:
                        pending = {
                            "base_resource": base,
                            "blend_resource": blend,
                            "pose_resource": pose,
                            "uav_resource": uav,
                            "dispatch_vertices": dispatch,
                            "phase_expr": phase_expr,
                            **(pending_output or {}),
                        }
                        pose_pass = pending
                continue
            match = _COMPUTE_RESOURCE_RE.fullmatch(line)
            if match:
                output = {"output_resource": match.group(1),
                          "uav_slot": int(match.group("slot"))}
                if pending is not None:
                    pending.update(output)
                else:
                    pending_output = output

    if not shape_passes or pose_pass is None:
        return []
    if "output_resource" not in pose_pass:
        return []
    shape_outputs = {
        str(item.get("output_resource", "")).casefold()
        for item in shape_passes if item.get("output_resource")
    }
    if str(pose_pass["base_resource"]).casefold() not in shape_outputs:
        return []
    if not all(item["dispatch_vertices"] > 0 for item in shape_passes):
        return []
    bone_count = None
    literals = _literal_assignments(sections, canonical)
    for name, value in literals.items():
        if name == "vg_count":
            bone_count = _integer(value)
            break
    if bone_count is None:
        return []
    validated = _validate_compute_buffers(
        resources, copy_sources, shape_passes, pose_pass,
        mod_dir=mod_dir, source=source, bone_count=bone_count)
    if validated is None:
        return []

    updates, pause_var = _phase_bindings(sections, canonical)
    shape_freq = None
    pose_freq = None
    shape_offsets = []
    for item in shape_passes:
        expr = item.get("phase_expr", "")
        vars_found = _VAR_REF_RE.findall(expr)
        if vars_found:
            freq = _canonical(vars_found[0], canonical)
            shape_freq = shape_freq or freq
        offset_match = re.search(
            r"(?P<sign>[+-])?\s*(?P<value>\d+(?:\.\d+)?)\s*$", expr)
        if offset_match and offset_match.group("sign"):
            value = float(offset_match.group("value"))
            if offset_match.group("sign") == "-":
                value = -value
            shape_offsets.append(value)
        else:
            shape_offsets.append(0.0)
    vars_found = _VAR_REF_RE.findall(pose_pass.get("phase_expr", ""))
    if vars_found:
        pose_freq = _canonical(vars_found[0], canonical)
    if (shape_freq is None or pose_freq is None
            or shape_freq.casefold() not in updates
            or pose_freq.casefold() not in updates
            or pause_var is None):
        return []
    state_var, state_ranges = _state_ranges(sections, canonical)
    if state_var is None or not state_ranges:
        return []
    autoplay = _autoplay_machine(sections, canonical)
    control_vars = [
        f"{var_prefix or ''}{pause_var}",
        f"{var_prefix or ''}{state_var}",
    ]
    if autoplay is not None:
        control_vars.append(f"{var_prefix or ''}{autoplay['var']}")

    identity = json.dumps({
        "ini": (source.logical_path(ini_path) if source is not None
                and source.is_resource_reference(ini_path)
                else os.path.basename(str(ini_path or ""))),
        "output": pose_pass["output_resource"],
        "base": shape_passes[0]["base_resource"],
    }, sort_keys=True, separators=(",", ":"))
    track_id = "gimi::" + hashlib.sha1(identity.encode()).hexdigest()[:12]
    return [{
        "kind": "gimi_compute",
        "track_id": track_id,
        "position_resource": pose_pass["output_resource"],
        "base_resource": shape_passes[0]["base_resource"],
        "base_file": validated["base_file"],
        "vertex_count": validated["vertex_count"],
        "shape_passes": [{
            "target_resource": item["target_resource"],
            "target_file": _resolved_resource(
                resources, copy_sources, item["target_resource"])["filename"],
            "dispatch_vertices": item["dispatch_vertices"],
            "phase_offset": offset,
        } for item, offset in zip(shape_passes, shape_offsets)],
        "pose_blend_resource": pose_pass["blend_resource"],
        "pose_blend_file": validated["blend_file"],
        "pose_resource": pose_pass["pose_resource"],
        "pose_file": validated["pose_file"],
        "pose_bone_count": bone_count,
        "pose_frame_count": validated["frame_count"],
        "pose_dispatch_vertices": pose_pass["dispatch_vertices"],
        "shape_frequency_var": f"{var_prefix or ''}{shape_freq}",
        "pose_frequency_var": f"{var_prefix or ''}{pose_freq}",
        "pause_var": f"{var_prefix or ''}{pause_var}",
        "state_var": f"{var_prefix or ''}{state_var}",
        "autoplay": ({
            **autoplay,
            "var": f"{var_prefix or ''}{autoplay['var']}",
            "state_var": f"{var_prefix or ''}{autoplay['state_var']}",
        } if autoplay is not None else None),
        "state_ranges": state_ranges,
        "shape_speed": updates[shape_freq.casefold()]["speed"],
        "pose_speed": updates[pose_freq.casefold()]["speed"],
        "shape_wrap": 5.236,
        "control_vars": control_vars,
    }]


__all__ = [
    "AnimationAnalysis", "AnimationClock", "discover_animation_clocks",
    "frame_condition", "discover_compute_animations",
]

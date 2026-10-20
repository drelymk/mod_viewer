"""Conservative Present/two-pass WWMI sparse animation discovery."""

import hashlib
import json
import os
import re
import struct

from .animation_clocks import (
    _COMPUTE_DISPATCH_RE,
    _COMPUTE_RUN_RE,
    _canonical,
    _compile_condition,
    _compile_expression,
    _expression_variables,
    _integer,
    _literal_constant_assignments,
    _unprefix,
)
from .animation_resources import (
    _COMPUTE_SHADER_RE,
    _read_resource_bytes,
    _resource_path,
    _strip_hlsl_comments,
    _NUMTHREADS_RE,
    _identify_shape_weight_operation,
    _AnimationResources,
)
from .sections import canonical_var_names


_WWMI_U5_RE = re.compile(
    r"^\s*cs-u5\s*=\s*(?:copy\s+)?(Resource\S+)\s*$", re.I)

_WWMI_REGISTER_RE = re.compile(
    r"^\s*(?P<register>[xyz]0)\s*=\s*(?P<value>.*?)\s*$", re.I)

_WWMI_TOGGLE_RE = re.compile(
    r"^\s*\$(?P<var>\w+)\s*==\s*1\s*$", re.I)

_WWMI_PHASE_RE = re.compile(
    r"^\s*\$(?P<phase>\w+)\s*=\s*\$(?P=phase)\s*\+\s*"
    r"\$(?P<speed>\w+)\s*\*\s*\$dt\s*$", re.I)


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

"""General compute binding chains and ordered animation track discovery."""

import hashlib
import json
import os
import re

from .animation_clocks import (
    _CLOCK_ELIF_RE,
    _COMPUTE_DISPATCH_RE,
    _COMPUTE_RUN_RE,
    _compile_animation_program,
    _compile_expression,
    _compute_condition_is_supported,
    _condition_stack_line,
    _integer,
    _literal_constant_assignments,
    _operand_value,
)
from .animation_resources import (
    _COMPUTE_SHADER_RE,
    _AnimationResources,
    _validate_compute_layout,
)
from .dnf import DNF_TRUE, build_bool_alias_map, dnf_and, normalize_dnf
from .sections import ResourceTable, canonical_var_names


_COMPUTE_RESOURCE_RE = re.compile(
    r"^\s*(Resource\S+)\s*=\s*ref\s+cs-u(?P<slot>\d+)\s*$", re.I)

_COMPUTE_U_COPY_RE = re.compile(
    r"^\s*cs-u(?P<slot>\d+)\s*=\s*copy\s+(Resource\S+)\s*$", re.I)

_COMPUTE_U_NULL_RE = re.compile(
    r"^\s*cs-u(?P<slot>\d+)\s*=\s*null\s*$", re.I)

_COMPUTE_T_COPY_RE = re.compile(
    r"^\s*cs-t(?P<slot>\d+)\s*=\s*copy\s+(\S+)\s*$", re.I)

_X88_RE = re.compile(r"^\s*x88\s*=\s*(?P<expr>.+?)\s*$", re.I)

_X89_RE = re.compile(r"^\s*x89\s*=\s*(?P<expr>.+?)\s*$", re.I)


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
            binding = _COMPUTE_T_COPY_RE.fullmatch(line)
            if (binding and binding.group("slot") == "50"
                    and binding.group(2).casefold() == str(inherited_base).casefold()):
                continue
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
            event, value = "run", (match.group("section"), sources.get(50), line_index)
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
        if event == "run":
            # A child can change phase, shader and target bindings. A later
            # parent dispatch must explicitly establish them again.
            sources.pop(51, None)
            sources.pop(52, None)
            shader, phase, bone_count = None, None, None


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
                          "output_resource": None,
                          "conditional_begin": bool(stack)} if event == "begin" else None)
            elif chain is not None:
                if event == "output":
                    chain["output_resource"] = value
                elif event == "dispatch":
                    chain["passes"].append(value)
                elif event == "invalid":
                    chain["unsupported"] = True
                elif event == "run":
                    child = lookup.get(value[0].casefold())
                    if child is None or not str(child).casefold().startswith("customshader"):
                        continue
                    passes = nested_passes(child, value[1], chain["u5_resource"])
                    if not passes:
                        chain["unsupported"] = True
                        continue
                    combined = DNF_TRUE
                    for frame in stack:
                        combined = dnf_and(combined, frame["cur"])
                    call_key = (str(section).casefold(), value[2])
                    chain.setdefault("child_passes", []).extend(
                        {**item, "call_key": call_key} for item in passes)
                    nested = {"child_section": child, "passes": passes,
                              "overlay_supported": all(supported),
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
    resources = ResourceTable.normalize(resources)
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
                "call_key": item.get("call_key", ()),
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
        shapes.extend(chain.get("child_passes", ()))
        animation = None
        if (not chain.get("unsupported") and not chain.get("conditional_begin")
                and not (poses and shapes)):
            if not poses and str(output).casefold() not in pose_inputs:
                animation = build_track(chain, index, shapes)
            elif len(poses) == 1:
                matching = output_chains.get(str(poses[0]["base_resource"]).casefold(), [])
                if not matching:
                    animation = build_track(chain, index, [], poses[0])
                elif (len(matching) == 1 and not matching[0].get("unsupported")
                      and all(item["kind"] == "shape" for item in matching[0]["passes"])):
                    animation = build_track(chain, index, [
                        *matching[0]["passes"], *matching[0].get("child_passes", ())], poses[0])
        nested = chain.get("nested_run")
        if (animation is None and nested and nested["overlay_supported"]
                and not chain.get("nested_ambiguous")
                and not chain.get("unsupported")):
            animation = build_track(chain, index, nested["passes"], nested=nested)
        if animation is not None:
            animations.append(animation)
    if not animations:
        return []
    program = _compile_animation_program(
        sections, animations, canonical, var_prefix, condition_aliases=aliases)
    if program is None:
        return []
    program_id = "gimi-program::" + hashlib.sha1(
        str(ini_identity).encode("utf-8")).hexdigest()[:12]
    for animation in animations:
        for item in [*animation["shape_passes"], *([animation["pose"]] if animation["pose"] else [])]:
            item.pop("phase_expr")
            item.pop("dispatch_key")
            item.pop("call_key", None)
        animation.update(program_id=program_id, program=program)
    return animations

"""Discover simple compute-shader shape sliders.

Supported pattern: a shader receives a scalar ini variable, a file-backed base
position buffer and a same-layout target buffer.  This is the common 3DMigoto
``base + (target - base) * value`` shape-key convention.  The viewer does not
attempt to execute arbitrary HLSL.
"""

import re

from .sections import canonical_var_names, first_source

_SHAPE_BUFFER_RE = re.compile(r"^cs-t(50|51)\s*=\s*copy\s+(\S+)\s*$", re.I)
_SHAPE_X_RE = re.compile(r"^x88\s*=\s*(.+?)\s*$", re.I)
_U5_ASSIGN_RE = re.compile(r"^cs-u5\s*=\s*(.*?)\s*$", re.I)
_U5_COPY_RE = re.compile(r"^copy\s+(\S+)\s*$", re.I)
_U5_OUTPUT_RE = re.compile(
    r"^(Resource\S+)\s*=\s*ref\s+cs-u5\s*$", re.I)
_NEGATED_VALUE_RE = re.compile(r"^\$(\w+)\s*\*\s*-1$", re.I)
_REMAP_RE = re.compile(
    r"^\$(\w+)\s*=\s*\(?\s*\$(\w+)\s*\*\s*2\s*-\s*1\s*\)?$", re.I)
_SLIDER_RE = re.compile(r"^x87\s*=\s*\$(\w+)\s*\*\s*x87\s*$", re.I)
_SLIDER_ANY_RE = re.compile(r"^x87\s*=.*\$(\w+)\s*$", re.I)
_SHAPE_ID_RE = re.compile(r"^\$\\WWMIv1\\shapekey_id\s*=\s*(\d+)\s*$", re.I)
_SHAPE_VALUE_RE = re.compile(
    r"^\$\\WWMIv1\\shapekey_value\s*=\s*\$(\w+)\s*$", re.I)
_BIND_RE = re.compile(r"^(cs-t(?:0|1|6|33))\s*=\s*(?:copy\s+|ref\s+)?(\S+)\s*$", re.I)
_BATCH_OFFSET_RE = re.compile(
    r"^global\s+\$shapekey_vertex_offset_batch(\d+)\s*=\s*(\d+)\s*$", re.I)


def _writable_u5_outputs(lines):
    """Map each copied u5 source resource to resources written from it."""
    outputs = {}
    current_source = None
    for raw in lines:
        line = str(raw).split(";", 1)[0].strip()
        assignment = _U5_ASSIGN_RE.fullmatch(line)
        if assignment:
            copied = _U5_COPY_RE.fullmatch(assignment.group(1).strip())
            current_source = copied.group(1).casefold() if copied else None
            continue
        output = _U5_OUTPUT_RE.fullmatch(line)
        if output and current_source:
            outputs.setdefault(current_source, set()).add(
                output.group(1).casefold())
    return outputs


def extract_shape_sliders(sections, resources, var_prefix=None, source=None,
                          canonical_vars=None):
    """Return slider descriptions for conservative two-buffer shape shaders."""
    canon = (canonical_vars if canonical_vars is not None
             else canonical_var_names(sections))
    found = []

    # When a mod has authored slider drawing sections, they are a strong
    # signal for which x88 variables are user controls. This prevents an
    # internal remapping variable from becoming a duplicate UI slider.
    authored_sliders = {}
    remapped_vars = {}
    for section, lines in sections.items():
        for raw in lines:
            line = str(raw).split(";", 1)[0].strip()
            if section.lower().startswith("commandlistdrawslider"):
                match = _SLIDER_RE.fullmatch(line)
                if match:
                    variable = canon.get(match.group(1).lower(), match.group(1))
                    authored_sliders.setdefault(variable.lower(), section)
                    if authored_sliders[variable.lower()] != section:
                        authored_sliders[variable.lower()] = None
            match = _REMAP_RE.fullmatch(line)
            if match:
                alias = canon.get(match.group(1).lower(), match.group(1))
                original = canon.get(match.group(2).lower(), match.group(2))
                remapped_vars[alias.lower()] = original

    def resource(name):
        """3DMigoto resource identifiers are case-insensitive."""
        if not name:
            return {}
        lookup = getattr(resources, "get_ci", None)
        if lookup is not None:
            return lookup(name)
        lowered = name.lower()
        for key, value in resources.items():
            if key.lower() == lowered:
                return value
        return {}

    def shape_base_resources(base_name, writable_outputs):
        """Resolve shader input and writable runtime output for a base."""
        shader_base = resource(base_name)
        base = shader_base
        runtime_names = writable_outputs.get(base_name.casefold(), ())
        if len(runtime_names) == 1:
            runtime_base = resource(next(iter(runtime_names)))
            if runtime_base.get("filename"):
                base = runtime_base
        return base, shader_base

    # Some generated ZZMI shaders apply several ordinary full-buffer morphs
    # in sequence.  Each block binds one scalar, the same base at t50, and a
    # different target at t51. Resource names are exporter-defined. Require
    # multiple complete blocks sharing one file-backed base, except that one
    # block is accepted when its variable has an authored slider section.
    for section, lines in sections.items():
        if not section.lower().startswith("customshader"):
            continue
        candidates = []
        remapped_targets = {}
        writable_outputs = _writable_u5_outputs(lines)
        variable = base_name = None
        remap_side = None
        for raw in lines:
            line = str(raw).split(";", 1)[0].strip()
            x_value = _SHAPE_X_RE.fullmatch(line)
            if x_value:
                value = x_value.group(1).strip()
                match = re.fullmatch(r"\$(\w+)", value)
                negative = _NEGATED_VALUE_RE.fullmatch(value)
                if match or negative:
                    token = (match or negative).group(1)
                    variable = canon.get(token.lower(), token)
                    remap_side = "low" if negative else "high"
                else:
                    # A literal or arbitrary expression owns subsequent t51
                    # writes until another scalar binding appears. Do not let
                    # the preceding slider accidentally claim that target.
                    variable = None
                    remap_side = None
                continue
            match = _SHAPE_BUFFER_RE.fullmatch(line)
            if not match:
                continue
            if match.group(1) == "50":
                base_name = match.group(2)
            else:
                target_name = match.group(2)
                if not base_name or variable is None:
                    continue
                original = remapped_vars.get(variable.lower())
                if original:
                    pair = remapped_targets.setdefault(
                        variable.lower(), {"var": original, "base": base_name})
                    if pair["base"].lower() == base_name.lower():
                        pair[remap_side] = target_name
                else:
                    candidates.append((variable, base_name, target_name))

        base_keys = {name.lower() for _var, name, _target in candidates}
        complete_remaps = [pair for pair in remapped_targets.values()
                           if pair.get("low") and pair.get("high")]
        base_keys.update(pair["base"].lower() for pair in complete_remaps)
        candidate_count = len(candidates) + len(complete_remaps)
        one_authored_candidate = (
            candidate_count == 1 and len(candidates) == 1
            and candidates[0][0].lower() in authored_sliders)
        if ((candidate_count < 2 and not one_authored_candidate)
                or len(base_keys) != 1):
            continue
        src = first_source(lines) or {}
        existing_pairs = {(item.get("var", "").lower(),
                           item.get("base_file"), item.get("target_file"))
                          for item in found}
        for variable, base_name, target_name in candidates:
            if authored_sliders and variable.lower() not in authored_sliders:
                continue
            base, shader_base = shape_base_resources(
                base_name, writable_outputs)
            target = resource(target_name)
            # When this t50 input is also copied to u5 and written through a
            # resource ref, attach the slider to that runtime output while
            # retaining the shader input as its animation identity.
            base_stride = base.get("stride", 40)
            target_stride = target.get("stride", base_stride)
            pair = (f"{var_prefix or ''}{variable}".lower(),
                    base.get("filename"), target.get("filename"))
            if (not all(pair[1:]) or pair in existing_pairs
                    or pair[1] == pair[2]
                    or base_stride != target_stride or base_stride < 12):
                continue
            found.append({
                "kind": "shape_slider",
                "name": variable,
                "var": f"{var_prefix or ''}{variable}",
                "authored_slider": variable.lower() in authored_sliders,
                "ui_section": authored_sliders.get(variable.lower()),
                "min": 0.0, "max": 1.0, "step": 0.01,
                "base_file": pair[1],
                "shader_base_file": shader_base.get("filename"),
                "target_file": pair[2],
                "stride": base_stride,
                "source": source,
                "ini_path": src.get("ini_path"),
                "section": section,
            })
            existing_pairs.add(pair)

        for item in complete_remaps:
            variable = item["var"]
            if authored_sliders and variable.lower() not in authored_sliders:
                continue
            base, _shader_base = shape_base_resources(
                item["base"], writable_outputs)
            low = resource(item["low"])
            high = resource(item["high"])
            strides = {base.get("stride", 40), low.get("stride", 40),
                       high.get("stride", 40)}
            if (not base.get("filename") or not low.get("filename")
                    or not high.get("filename") or len(strides) != 1
                    or next(iter(strides)) < 12):
                continue
            found.append({
                "kind": "shape_slider", "mode": "midpoint_pair",
                "name": variable, "var": f"{var_prefix or ''}{variable}",
                "authored_slider": variable.lower() in authored_sliders,
                "ui_section": authored_sliders.get(variable.lower()),
                "min": 0.0, "max": 1.0, "step": 0.01,
                "base_file": base["filename"],
                "low_file": low["filename"],
                "target_file": high["filename"],
                "stride": next(iter(strides)), "source": source,
                "ini_path": src.get("ini_path"), "section": section,
            })

    # WWMI shape keys are sparse rather than full target buffers.  The menu
    # still advertises them with the same `$value * x87` slider idiom, while
    # SetShapeKey command lists map each value variable to an integer key ID.
    sliders = []
    shape_ids = {}
    bindings = {}
    batch_offsets = {}
    for lines in sections.values():
        for raw in lines:
            match = _BATCH_OFFSET_RE.fullmatch(
                str(raw).split(";", 1)[0].strip())
            if match:
                batch_offsets[int(match.group(1))] = int(match.group(2))
    for section, lines in sections.items():
        cleaned = [str(raw).split(";", 1)[0].strip() for raw in lines]
        if section.lower().startswith("commandlistdrawslider"):
            for line in cleaned:
                match = _SLIDER_RE.fullmatch(line)
                if match:
                    sliders.append((canon.get(match.group(1).lower(), match.group(1)),
                                    section, first_source(lines) or {}))
                    break
        pending_id = None
        for line in cleaned:
            match = _SHAPE_ID_RE.fullmatch(line)
            if match:
                pending_id = int(match.group(1))
                continue
            match = _SHAPE_VALUE_RE.fullmatch(line)
            if match and pending_id is not None:
                var = canon.get(match.group(1).lower(), match.group(1))
                shape_ids[var.lower()] = pending_id
                pending_id = None
            match = _BIND_RE.fullmatch(line)
            if match:
                bindings.setdefault(match.group(1).lower(), match.group(2))

    sparse_resources = {
        "base_file": resource(bindings.get("cs-t6")).get("filename"),
        "offset_file": resource(bindings.get("cs-t33")).get("filename"),
        "vertex_id_file": resource(bindings.get("cs-t0")).get("filename"),
        "vertex_offset_file": resource(bindings.get("cs-t1")).get("filename"),
    }
    sparse_ready = all(sparse_resources.values())
    existing = {item["var"].lower() for item in found}
    prefix = var_prefix or ""
    for variable, section, src in sliders:
        full_var = f"{prefix}{variable}"
        if full_var.lower() in existing:
            continue
        item = {
            "kind": "shape_slider",
            "name": variable,
            "var": full_var,
            "authored_slider": variable.lower() in authored_sliders,
            "ui_section": authored_sliders.get(variable.lower()),
            "min": 0.0,
            "max": 1.0,
            "step": 0.01,
            "source": source,
            "ini_path": src.get("ini_path"),
            "section": section,
        }
        shape_id = shape_ids.get(variable.lower())
        if shape_id is not None and sparse_ready:
            batch = shape_id // 127
            item.update(sparse_resources)
            item["shape_id"] = shape_id
            item["buffer_shape_id"] = shape_id + batch
            item["sparse_entry_offset"] = batch_offsets.get(batch, 0)
            item["stride"] = resource(bindings.get("cs-t6")).get("stride", 12)
        found.append(item)
        existing.add(full_var.lower())

    # Multi-target ZZMI menus bind five full buffers and two scalar inputs:
    # base, bigger/smaller A, bigger/smaller B. Preserve the shader's midpoint
    # curve rather than pretending these are independent base->target morphs.
    menu_vars = set()
    for section, lines in sections.items():
        if not section.lower().startswith("commandlist"):
            continue
        for raw in lines:
            line = str(raw).split(";", 1)[0].strip()
            match = _SLIDER_ANY_RE.fullmatch(line)
            if match:
                menu_vars.add(canon.get(match.group(1).lower(), match.group(1)))

    multi_sets = []
    for section, lines in sections.items():
        current = {}
        section_sets = []
        scalar_vars = {}
        for raw in lines:
            line = str(raw).split(";", 1)[0].strip()
            match = re.fullmatch(r"cs-t(5[0-4])\s*=\s*copy\s+(\S+)", line, re.I)
            if match:
                slot = int(match.group(1)) - 50
                if slot == 0 and len(current) == 5:
                    section_sets.append(current)
                    current = {}
                current[slot] = match.group(2)
                continue
            match = re.fullmatch(r"x(88|89)\s*=\s*\$(\w+)", line, re.I)
            if match:
                scalar_vars[int(match.group(1))] = canon.get(
                    match.group(2).lower(), match.group(2))
        if len(current) == 5:
            section_sets.append(current)
        # Buffer registers and their scalar inputs are shader-local state.
        # Never combine a five-buffer set from one CommandList with x88/x89
        # assignments found in an unrelated UI/effect CommandList.
        for buffers in section_sets:
            multi_sets.append((buffers, dict(scalar_vars), lines))

    roles = {88: (1, 2), 89: (3, 4)}
    for buffers, scalar_vars, source_lines in multi_sets:
        base = resource(buffers.get(0))
        if not base.get("filename"):
            continue
        for register, (high_slot, low_slot) in roles.items():
            variable = scalar_vars.get(register)
            high = resource(buffers.get(high_slot))
            low = resource(buffers.get(low_slot))
            if (not variable or variable not in menu_vars or
                    not high.get("filename") or not low.get("filename")):
                continue
            src = first_source(source_lines) or {}
            found.append({
                "kind": "shape_slider",
                "mode": "midpoint_pair",
                "name": variable,
                "var": f"{var_prefix or ''}{variable}",
                "authored_slider": variable.lower() in authored_sliders,
                "ui_section": authored_sliders.get(variable.lower()),
                "min": 0.0, "max": 1.0, "step": 0.01,
                "base_file": base["filename"],
                "low_file": low["filename"],
                "target_file": high["filename"],
                "stride": base.get("stride", 40),
                "source": source,
                "ini_path": src.get("ini_path"),
                "section": "CommandListComputeShapeKeys",
            })
    return found

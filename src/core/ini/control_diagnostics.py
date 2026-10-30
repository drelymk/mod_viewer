"""Explain conservative control discovery gaps without executing mod code."""

import re

from .menu import (
    _ASSIGN_RE,
    _conditional_blocks,
    _guard,
    _same_var,
    _resolve_actions,
    _slot_chains,
    _static_numeric_defaults,
    extract_menu_toggles,
)
from .sections import canonical_var_names, extract_resources, sections_from_document
from .shapes import extract_shape_sliders


def control_support_issues(document, ini):
    """Report authored menu cycles and clamped compute controls we cannot expose.

    Clamps and scalar bindings establish a user-control candidate, not shader
    semantics. An unsupported shader must never become an invented morph.
    """
    sections = sections_from_document(document)
    resources = extract_resources(sections)
    canonical = canonical_var_names(sections)
    menu = extract_menu_toggles(sections, resources=resources, canonical_vars=canonical)
    shapes = extract_shape_sliders(sections, resources, canonical_vars=canonical)
    recognized = {
        (item["section"].casefold(), item["slot"], item["var"].casefold())
        for item in menu.values()
    }
    shape_vars = {
        item["var"].casefold()
        for item in shapes
        if item.get("base_file")
        and (item.get("target_file") or item.get("shape_id") is not None)
    }
    locations = {
        section.name.casefold(): section.header_no + 1 for section in document.sections
    }
    issues, seen = [], set()
    blocks = {
        name.casefold(): _conditional_blocks(lines)
        for name, lines in sections.items()
        if name.casefold().startswith("commandlist")
    }
    defaults = _static_numeric_defaults(sections)
    persisted = {
        match.group(1).casefold()
        for name, lines in sections.items()
        if name.casefold() == "constants"
        for line in lines
        if (match := re.match(r"global\s+persist\s+\$(\w+)\b", str(line), re.I))
    }

    def add(code, section, variable, message, **details):
        identity = (code, section.casefold(), variable.casefold(), details.get("slot"))
        if identity in seen:
            return
        seen.add(identity)
        variable = canonical.get(variable.casefold(), variable)
        issues.append(
            {
                "code": code,
                "severity": "warning",
                "category": "viewer",
                "message": message,
                "ini": ini,
                "section": section,
                "line": locations.get(section.casefold()),
                "variable": variable,
                **details,
            }
        )

    clamps = {}

    def scan_clamps(nodes):
        for node in nodes:
            if isinstance(node, str):
                continue
            for branch in node["branches"]:
                guard = _guard(branch["condition"] or "")
                if guard and guard["op"] in ("<", "<=", ">", ">="):
                    for line in branch["body"]:
                        assignment = (
                            _ASSIGN_RE.fullmatch(line)
                            if isinstance(line, str)
                            else None
                        )
                        if not assignment or not _same_var(
                            assignment.group(1), guard["var"]
                        ):
                            continue
                        try:
                            bound = float(guard["value"])
                            reset = float(assignment.group(2))
                        except ValueError:
                            continue
                        if bound == reset:
                            direction = "min" if guard["op"].startswith("<") else "max"
                            clamps.setdefault(guard["var"].casefold(), {}).setdefault(
                                direction, set()
                            ).add(bound)
                scan_clamps(branch["body"])

    for section, lines in sections.items():
        parsed = _conditional_blocks(lines)
        if parsed is not None:
            scan_clamps(parsed[1])
        if not section.casefold().startswith("commandlist"):
            continue
        for chain in _slot_chains(parsed):
            for _selector, slot, body, nodes in chain:
                finite = _resolve_actions(nodes, blocks, defaults, allow_effects=True)
                for line in body:
                    assignment = _ASSIGN_RE.fullmatch(line)
                    if not assignment:
                        continue
                    variable, rhs = assignment.groups()
                    if (
                        re.search(rf"\${re.escape(variable)}\b", rhs, re.I)
                        and not re.fullmatch(rf"\${re.escape(variable)}", rhs, re.I)
                        and (section.casefold(), int(slot), variable.casefold())
                        not in recognized
                    ):
                        add(
                            "unsupported_menu_action",
                            section,
                            variable,
                            (
                                f"The viewer cannot prove a finite menu cycle for ${variable}. "
                                if not finite
                                else f"The dispatch containing ${variable} has fewer than two recognized menu actions. "
                            )
                            + "This action is omitted from Controls; the INI is unchanged.",
                            slot=int(slot),
                            reason=(
                                "unproven_finite_cycle"
                                if not finite
                                else "insufficient_menu_actions"
                            ),
                        )

    for section, lines in sections.items():
        if not section.casefold().startswith("customshader"):
            continue
        cleaned = [str(line).split(";", 1)[0].strip() for line in lines]
        if not (
            any(re.match(r"cs\s*=", line, re.I) for line in cleaned)
            and any(re.match(r"dispatch\s*=", line, re.I) for line in cleaned)
        ):
            continue
        for line in cleaned:
            binding = re.fullmatch(r"([xyzw]\d+)\s*=\s*\$(\w+)", line, re.I)
            if not binding:
                continue
            register, variable = binding.groups()
            limits = clamps.get(variable.casefold(), {})
            if (
                variable.casefold() not in persisted
                or variable.casefold() in shape_vars
                or set(limits) != {"min", "max"}
                or any(len(values) != 1 for values in limits.values())
            ):
                continue
            lower, upper = next(iter(limits["min"])), next(iter(limits["max"]))
            if lower >= upper:
                continue
            add(
                "unsupported_shader_control",
                section,
                variable,
                f"Clamped control ${variable} is supplied to a compute shader, "
                "but the viewer has no verified deformation adapter for it. "
                "Its slider is omitted; the INI is unchanged.",
                register=register.lower(),
                min=lower,
                max=upper,
                reason="unverified_deformation",
            )
    return issues

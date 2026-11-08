"""Remove discovered clickable menus from an in-memory INI document."""

import re

from ..ini.document import IF, ELIF, ELSE, ENDIF, DRAW, BLANK, COMMENT
from ..ini.menu import extract_menu_toggles, extract_controller_toggles
from ..ini.sections import sections_from_document, extract_resources
from ..ini.shapes import extract_shape_sliders
from .toggle import ToggleEditError, _VARIABLE_WRITE_RE, remove_variables
from .cleanup import DeletionCleanup


_VAR_RE = re.compile(r"\$([\\\w]+)")


def _variables(text):
    if text.startswith("["):
        return set()
    return {name.lower() for name in _VAR_RE.findall(text) if "\\" not in name}


def _remove_lines(doc, removed):
    """Prune branches made empty by removal, then splice original lines once."""
    for sec in doc.sections:
        stack = []
        for line in sec.lines:
            if line.kind == IF:
                stack.append(line.no)
            elif line.kind == ENDIF and stack:
                start = stack.pop()
                body = doc.lines[start:line.no + 1]
                if (any(item.no in removed for item in body)
                        and all(item.no in removed or item.kind in
                                (IF, ELIF, ELSE, ENDIF, BLANK, COMMENT) for item in body)):
                    removed.update(range(start, line.no + 1))
    variables = {name for no in removed for name in _variables(doc.lines[no].text)}
    for no in sorted(removed, reverse=True):
        doc.delete_lines(no, no + 1)
    return variables



def _remove_shared_menu_images(doc, image_resources, reference, protected_vars):
    """Remove image-only slot chains and their literal UI call sites."""
    removed = set()
    selectors = {}
    image_helpers = set()
    for sec in doc.sections:
        stack = []
        for line in sec.lines:
            if line.kind == IF:
                stack.append([line])
            elif line.kind in (ELIF, ELSE) and stack:
                stack[-1].append(line)
            elif line.kind == ENDIF and stack:
                branches = stack.pop()
                body = doc.lines[branches[0].no + 1:line.no]
                bindings = [item for item in body if item.kind not in
                            (IF, ELIF, ELSE, ENDIF, BLANK, COMMENT)]
                slots = [re.fullmatch(r"(?:if|elif|else\s+if)\s+\$(\w+)\s*==\s*(\d+)",
                                     item.text, re.I) for item in branches]
                if (bindings and all(slots) and len({match[1].lower() for match in slots}) == 1
                        and all(re.fullmatch(r"ps-t\d+\s*=\s*.+", item.text, re.I)
                                and any(match[1].lower() in image_resources
                                        for match in reference.finditer(item.text)) for item in bindings)):
                    removed.update(range(branches[0].no, line.no + 1))
                    selectors.setdefault(slots[0][1].lower(), set()).update(int(match[2]) for match in slots)
                    image_helpers.add(sec.name.lower())
    if not removed:
        return set(), []
    calls = {}
    for sec in doc.sections:
        calls[sec.name.lower()] = {match[1].strip().lower() for line in sec.lines
                                  if (match := re.fullmatch(r"(?:pre\s+|post\s+)?run\s*=\s*(.+)", line.text, re.I))}
    geometry_helpers = {sec.name.lower() for sec in doc.sections if any(
        item.kind == DRAW and item.text.lower().startswith("drawindexed") for item in sec.lines)}
    while True:
        expanded = geometry_helpers | {owner for owner, targets in calls.items() if targets & geometry_helpers}
        if expanded == geometry_helpers:
            break
        geometry_helpers = expanded
    reaches_images = set(image_helpers)
    while True:
        expanded = reaches_images | {owner for owner, targets in calls.items() if targets & reaches_images}
        if expanded == reaches_images:
            break
        reaches_images = expanded
    orphan_candidates = set(image_resources)
    for sec in doc.sections:
        literal_slots = {}
        for line in sec.lines:
            if line.kind in (IF, ELIF, ELSE, ENDIF):
                literal_slots.clear()
            assignment = re.fullmatch(r"\$(\w+)\s*=\s*(\d+)", line.text)
            if assignment and assignment[1].lower() in selectors:
                literal_slots[assignment[1].lower()] = (int(assignment[2]), line.no)
            elif (write := _VARIABLE_WRITE_RE.fullmatch(line.text)):
                literal_slots.pop(write[1].lower(), None)
            run = re.fullmatch(r"(?:pre\s+|post\s+)?run\s*=\s*(.+)", line.text, re.I)
            if run and run[1].lower() in reaches_images and any(
                    value in selectors[name] for name, (value, _) in literal_slots.items()):
                # Never remove a model draw helper based only on a slot number.
                targets = [item for item in doc.sections if item.name.lower() == run[1].lower()]
                target_lines = [item for target in targets for item in target.lines]
                if (targets and run[1].lower() not in geometry_helpers
                        and not any(_variables(item.text) & protected_vars for item in target_lines)
                        and not any((write := _VARIABLE_WRITE_RE.fullmatch(item.text))
                                    and write[1].lower() in selectors for item in target_lines)):
                    removed.add(line.no)
                    removed.update(no for name, (value, no) in literal_slots.items() if value in selectors[name])
                    orphan_candidates.add(run[1].lower())
            if run:
                literal_slots.clear()
    support = _remove_lines(doc, removed)
    removed_names = []
    while True:
        orphaned = set()
        for sec in doc.sections:
            if sec.name.lower() not in orphan_candidates:
                continue
            if not any(match[1].lower() == sec.name.lower() for line in doc.lines
                       if line.section is not sec for match in reference.finditer(line.text)):
                orphaned.add(sec.name.lower())
        if not orphaned:
            break
        removed = set()
        for sec in doc.sections:
            if sec.name.lower() in orphaned:
                removed_names.append(sec.name)
                orphan_candidates.update(match[1].lower() for line in sec.lines
                                         for match in reference.finditer(line.text)
                                         if match[1].lower().startswith(("commandlist", "customshader", "resource")))
                removed.update(range(sec.start, sec.end))
        support.update(_remove_lines(doc, removed))
        orphan_candidates -= orphaned
    return support, removed_names

def delete_menu(doc, *, controllers=(), variables=()):
    """Delete clickable controls, exclusive UI helpers and image references.

    The bridge derives controller/target identities from the same authorized
    staged snapshot. Shape sliders and shared consumers remain intact.
    The session owner provides transaction rollback and eventual Export.
    """
    cleanup = DeletionCleanup(doc)
    sections = sections_from_document(doc)
    resources = extract_resources(sections)
    menu = extract_menu_toggles(sections, resources=resources)
    controllers = set(controllers)
    menu.update({f"controller:{key}": entry for key, entry in extract_controller_toggles(
        sections, controllers, resources=resources).items()})
    if not menu and not variables:
        return {"controls_removed": 0, "vars_removed": [], "sections_removed": []}
    if doc.structure_errors() or doc.syntax_errors():
        raise ToggleEditError("menu deletion requires unambiguous INI structure")
    names = {sec.name.lower(): sec.name for sec in doc.sections}
    outputs = {cycle["var"].lower() for entry in menu.values()
               for cycle in entry.get("cycles", [entry])}
    outputs.update(effect["var"].lower() for entry in menu.values() for effect in entry.get("effects", ()))
    outputs.update(name.lower() for name in variables)
    protected_vars = {item["var"].lower() for item in extract_shape_sliders(sections, resources)}
    if outputs & protected_vars:
        raise ToggleEditError("a clickable menu and shape slider share a variable")
    # Use exact section identities, including spaces and Unicode, for local
    # helper/resource references. Files are never read or removed by this edit.
    reference = re.compile(r"(?<![\w\\.])(" + "|".join(
        re.escape(name) for name in sorted(names, key=len, reverse=True)) + r")(?![\w\\.])", re.I)
    edges = {name: set() for name in names}
    callers = {name: set() for name in names}
    for sec in doc.sections:
        owner = sec.name.lower()
        for line in sec.lines:
            for match in reference.finditer(line.text):
                target = match[1].lower()
                if target != owner:
                    edges[owner].add(target)
                    callers[target].add(owner)
    blocked = {sec.name.lower() for sec in doc.sections if any(
        (line.kind == DRAW and re.match(r"^drawindexed\s*=", line.text, re.I))
        or bool(_variables(line.text) & protected_vars) for line in sec.lines)
        and not sec.name.lower().startswith(("constants", "key"))}
    while True:
        expanded = blocked | {owner for owner, targets in edges.items() if targets & blocked}
        if expanded == blocked:
            break
        blocked = expanded
    image_files = {str(entry["image_file"]).replace("\\", "/").lower()
                   for entry in menu.values() if entry.get("image_file")}
    image_resources = {name.lower() for name, value in resources.items()
                       if str(value.get("filename", "")).replace("\\", "/").lower() in image_files}
    roots = {entry["section"].lower() for entry in menu.values()}
    pulse_vars = {entry["_pulse_var"].lower() for entry in menu.values() if entry.get("_pulse_var")}
    roots.update(sec.name.lower() for sec in doc.sections if any(
        (match := _VARIABLE_WRITE_RE.fullmatch(line.text))
        and match[1].lower() in outputs | pulse_vars for line in sec.lines))
    roots.update(owner for owner, targets in edges.items() if targets & image_resources)
    roots = {name for name in roots if name.startswith(("commandlist", "customshader")) and name not in blocked}
    while True:
        expanded = roots | {owner for owner, targets in edges.items()
                            if owner.startswith(("commandlist", "customshader"))
                            and owner not in blocked and targets & roots}
        if roots == expanded:
            break
        roots = expanded
    removed_sections = set(roots)
    while True:
        expanded = removed_sections | {target for owner in removed_sections for target in edges[owner]
                                       if target.startswith(("commandlist", "customshader", "resource"))
                                       and target not in blocked}
        if expanded == removed_sections:
            break
        removed_sections = expanded
    while True:
        shared = {name for name in removed_sections - roots if callers[name] - removed_sections}
        if not shared:
            break
        removed_sections -= shared
    support = set()
    removed_lines = set()
    removed_names = []
    for sec in doc.sections:
        if sec.name.lower() in removed_sections:
            removed_names.append(sec.name)
            removed_lines.update(range(sec.start, sec.end))
            support.update(name for line in sec.lines for name in _variables(line.text))
        else:
            for line in sec.lines:
                forwarding = re.fullmatch(r"\$\\[\w\\]+\s*=\s*\$(\w+)", line.text)
                if (any(match[1].lower() in removed_sections for match in reference.finditer(line.text))
                        or forwarding and forwarding[1].lower() in {name.lower() for name in controllers}):
                    removed_lines.add(line.no)
    support.update(_remove_lines(doc, removed_lines))
    shared_support, shared_removed = _remove_shared_menu_images(doc, image_resources, reference, protected_vars)
    support.update(shared_support)
    removed_names.extend(shared_removed)
    # Supporting mouse/selector state is removable only when no surviving
    # statement reads it outside the menu's own writes and declarations.
    support -= outputs | protected_vars
    while True:
        used = set()
        for line in doc.lines:
            assignment = _VARIABLE_WRITE_RE.fullmatch(line.text)
            if assignment and assignment[1].lower() in support | outputs:
                continue
            used.update(_variables(line.text) & support)
        if not used:
            break
        support -= used
    report = remove_variables(doc, sorted(outputs | support))
    if report["unsafe_gates"]:
        raise ToggleEditError("some menu draw conditions cannot be safely flattened")
    remaining = {name for line in doc.lines for name in _variables(line.text)}
    if remaining & (outputs | support):
        raise ToggleEditError("a deleted menu variable is still used by another command")
    report["sections_removed"] = removed_names + report["sections_removed"] + cleanup.finish(doc)
    report["controls_removed"] = len(menu)
    # Reanalysis must not rediscover any removed clickable control.
    final_sections = sections_from_document(doc)
    if (extract_menu_toggles(final_sections)
            or extract_controller_toggles(final_sections, controllers)):
        raise ToggleEditError("some clickable menu controls could not be removed")
    return report

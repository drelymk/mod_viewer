"""Lossless in-memory editing for the viewer's reserved preset cycle key."""

import re

from .ini_document import ASSIGN, BLANK
from .ini_menu import extract_menu_toggles
from .ini_sections import extract_resources, parse_sections
from .ini_shapes import extract_shape_sliders
from .ini_toggles import extract_toggle_keys
from .toggle_editor import ToggleEditError, cycle_vars, is_cycle_section


SECTION_NAME = "KeyModViewerPresent"
MAX_PRESENTS = 10
_PLAIN_VAR_RE = re.compile(r"^\w+$")


class DuplicatePresentError(ToggleEditError):
    def __init__(self, positions):
        self.positions = positions
        labels = ", ".join(f"Present {position + 1}" for position in positions)
        super().__init__(f"the captured values duplicate {labels}")


def _assignment(sec, name):
    wanted = name.lower()
    result = None
    for line in sec.lines:
        if line.kind != ASSIGN or "=" not in line.text:
            continue
        lhs, rhs = (part.strip() for part in line.text.split("=", 1))
        if lhs.lower() == wanted:
            result = (line, rhs)
    return result


def details(doc):
    """The reserved section's editable fields and aligned preset values."""
    sec = doc.section(SECTION_NAME)
    if sec is None:
        return None
    if not is_cycle_section(sec):
        raise ToggleEditError(f"[{SECTION_NAME}] must use type = cycle")
    variables = cycle_vars(sec)
    if not variables:
        raise ToggleEditError(f"[{SECTION_NAME}] does not capture any variables")
    count = max(len(values) for values in variables.values())
    if any(len(values) != count for values in variables.values()):
        raise ToggleEditError(f"[{SECTION_NAME}] has variable lists of different lengths")
    key = _assignment(sec, "key")
    back = _assignment(sec, "back")
    condition = _assignment(sec, "condition")
    return {
        "section": sec.name,
        "key": key[1] if key else "",
        "back": back[1] if back else "",
        "condition": condition[1] if condition else "",
        "vars": variables,
        "count": count,
    }


def capturable_variables(doc):
    """Local variables controlled by keys, menus, or recognized sliders."""
    sections = parse_sections(doc.path or "", text=doc.to_string())
    found = []
    seen = set()

    def add(name):
        low = name.lower()
        if (_PLAIN_VAR_RE.fullmatch(name) and low not in seen):
            seen.add(low)
            found.append(name)

    for info in extract_toggle_keys(sections).values():
        if info.get("section", "").lower() == SECTION_NAME.lower():
            continue
        for name in info.get("vars", {}):
            add(name)
    for info in extract_menu_toggles(sections).values():
        add(info.get("var", ""))
    resources = extract_resources(sections)
    for info in extract_shape_sliders(sections, resources):
        add(info.get("var", ""))
    return found


def _snapshot_values(doc, snapshot):
    allowed = capturable_variables(doc)
    supplied = {}
    for name, value in (snapshot or {}).items():
        raw = str(name).split("::")[-1].lstrip("$")
        supplied[raw.lower()] = str(value).strip()
    values = []
    for name in allowed:
        value = supplied.get(name.lower())
        if value is None:
            raise ToggleEditError(f"the current value of ${name} was not supplied")
        if not value or any(mark in value for mark in (",", ";", "\r", "\n")):
            raise ToggleEditError(f"${name} has an invalid preset value {value!r}")
        values.append((name, value))
    if not values:
        raise ToggleEditError("this INI has no key or menu toggle values to capture")
    return values


def _existing_condition(doc):
    for sec in doc.sections:
        if (not sec.name.lower().startswith("key")
                or sec.name.lower() == SECTION_NAME.lower()):
            continue
        condition = _assignment(sec, "condition")
        if condition:
            return condition[1]
    constants = doc.section("Constants")
    if constants is not None:
        for preferred in ("object_detected", "active"):
            pattern = re.compile(
                rf"^(?:global\s+)?(?:persist\s+)?\$({preferred})\s*=", re.I)
            for line in constants.lines:
                match = pattern.match(line.text)
                if match:
                    return f"${match.group(1)} == 1"
    return ""


def add(doc, key_combo, back_combo, snapshot):
    if doc.section(SECTION_NAME) is not None:
        raise ToggleEditError(f"[{SECTION_NAME}] already exists")
    key_combo = str(key_combo or "").strip()
    if not key_combo:
        raise ToggleEditError("a key binding is required")
    captured = _snapshot_values(doc, snapshot)
    body = [f"[{SECTION_NAME}]"]
    condition = _existing_condition(doc)
    if condition:
        body.append(f"condition = {condition}")
    body.append(f"key = {key_combo}")
    back_combo = str(back_combo or "").strip()
    if back_combo:
        body.append(f"back = {back_combo}")
    body.append("type = cycle")
    body.extend(f"${name} = {value}" for name, value in captured)

    constants = doc.section("Constants")
    at = constants.end if constants is not None else 0
    if at > 0 and doc.lines[at - 1].kind != BLANK:
        body.insert(0, "")
    if at < len(doc.lines):
        body.append("")
    doc.insert_lines(at, body)
    return details(doc)


def edit_binding(doc, key_combo, back_combo):
    details(doc)
    key_combo = str(key_combo or "").strip()
    if not key_combo:
        raise ToggleEditError("a key binding is required")
    sec = doc.section(SECTION_NAME)
    key = _assignment(sec, "key")
    if key:
        doc.replace_lines(key[0].no, key[0].no + 1, [f"key = {key_combo}"])
    else:
        doc.insert_lines(sec.header_no + 1, [f"key = {key_combo}"])
    sec = doc.section(SECTION_NAME)
    back = _assignment(sec, "back")
    back_combo = str(back_combo or "").strip()
    if back:
        if back_combo:
            doc.replace_lines(back[0].no, back[0].no + 1, [f"back = {back_combo}"])
        else:
            doc.delete_lines(back[0].no, back[0].no + 1)
    elif back_combo:
        key = _assignment(doc.section(SECTION_NAME), "key")
        doc.insert_lines(key[0].no + 1, [f"back = {back_combo}"])
    return details(doc)


def duplicate_positions(doc, snapshot, position=None):
    current = details(doc)
    captured = _snapshot_values(doc, snapshot)
    if position is not None:
        position = int(position)
        if position < 0 or position >= current["count"]:
            raise ToggleEditError(f"preset position {position + 1} does not exist")
    captured_by_low = {name.lower(): value for name, value in captured}
    candidate = {}
    for name, values in current["vars"].items():
        candidate[name.lower()] = captured_by_low.get(
            name.lower(), values[position] if position is not None else values[-1])
    for name, value in captured:
        candidate.setdefault(name.lower(), value)

    duplicates = []
    for other in range(current["count"]):
        if other == position:
            continue
        matches = True
        for name, value in candidate.items():
            old_name = next((old for old in current["vars"]
                             if old.lower() == name), None)
            other_value = (current["vars"][old_name][other]
                           if old_name is not None else value)
            if other_value != value:
                matches = False
                break
        if matches:
            duplicates.append(other)
    return duplicates


def capture(doc, snapshot, position=None, allow_duplicate=False):
    """Append a preset, or replace one existing position when supplied."""
    current = details(doc)
    if position is None and current["count"] >= MAX_PRESENTS:
        raise ToggleEditError(f"a PRESENT key can contain at most {MAX_PRESENTS} presents")
    duplicates = duplicate_positions(doc, snapshot, position=position)
    if duplicates and not allow_duplicate:
        raise DuplicatePresentError(duplicates)
    captured = _snapshot_values(doc, snapshot)
    if position is not None:
        position = int(position)
        if position < 0 or position >= current["count"]:
            raise ToggleEditError(f"preset position {position + 1} does not exist")

    sec = doc.section(SECTION_NAME)
    existing = current["vars"]
    captured_by_low = {name.lower(): (name, value) for name, value in captured}
    ordered = list(existing)
    ordered.extend(name for name, _value in captured if name.lower() not in {
        old.lower() for old in existing})

    replacements = []
    for name in ordered:
        old_name = next((old for old in existing if old.lower() == name.lower()), None)
        values = list(existing.get(old_name, ()))
        captured_item = captured_by_low.get(name.lower())
        value = captured_item[1] if captured_item else (values[-1] if values else None)
        if value is None:
            continue
        if not values:
            values = [value] * current["count"]
        if position is None:
            values.append(value)
        else:
            values[position] = value
        line = next((line for line in sec.lines if line.kind == ASSIGN
                     and line.text.partition("=")[0].strip().lower() == f"${name.lower()}"), None)
        replacements.append((line, name, values))

    existing_replacements = [item for item in replacements if item[0] is not None]
    new_replacements = [item for item in replacements if item[0] is None]
    for line, name, values in reversed(existing_replacements):
        text = f"${name} = {','.join(values)}"
        doc.replace_lines(line.no, line.no + 1, [text])
    for _line, name, values in new_replacements:
        sec = doc.section(SECTION_NAME)
        doc.insert_lines(sec.end, [f"${name} = {','.join(values)}"])
    return details(doc)


def delete_position(doc, position):
    current = details(doc)
    position = int(position)
    if current["count"] <= 1:
        raise ToggleEditError("the only present cannot be deleted; delete the PRESENT key instead")
    if position < 0 or position >= current["count"]:
        raise ToggleEditError(f"preset position {position + 1} does not exist")
    sec = doc.section(SECTION_NAME)
    replacements = []
    for name, values in current["vars"].items():
        kept = values[:position] + values[position + 1:]
        line = next(line for line in sec.lines if line.kind == ASSIGN
                    and line.text.partition("=")[0].strip().lower() == f"${name.lower()}")
        replacements.append((line, name, kept))
    for line, name, values in reversed(replacements):
        doc.replace_lines(line.no, line.no + 1,
                          [f"${name} = {','.join(values)}"])
    return details(doc)


def delete(doc):
    sec = doc.section(SECTION_NAME)
    if sec is None:
        raise ToggleEditError(f"no section named {SECTION_NAME!r}")
    start, end = sec.header_no, sec.end
    while end < len(doc.lines) and doc.lines[end].kind == BLANK:
        end += 1
    doc.delete_lines(start, end)
    return SECTION_NAME

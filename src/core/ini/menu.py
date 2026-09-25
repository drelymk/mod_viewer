"""Discovery of in-game *clickable menu* toggles.

The chain is recognised structurally (an `if $X == <int>` / `elif` chain whose
branches assign back to themselves) rather than by section name, since only the
layout is a convention — `$clickedSlot` is not.
"""

import re

from . import condition
from .sections import canonical_var_names, first_source, line_source

# A branch head that dispatches on an integer slot: `$clickedSlot == 3`.
_SLOT_RE = re.compile(r'\$(\w+)\s*={2,3}\s*(\d+)$')

_ASSIGN_RE  = re.compile(r'^\$(\w+)\s*=\s*(.+)$')
_FLIP_RE    = re.compile(r'^1\s*-\s*\$(\w+)$')       # $v = 1 - $v
_INCR_RE    = re.compile(r'^\$(\w+)\s*\+\s*1$')      # $v = $v + 1
_INCR_REV_RE = re.compile(r'^1\s*\+\s*\$(\w+)$')     # $v = 1 + $v
_INCR_MOD_RE = re.compile(                              # $v = ($v + 1) % N
    r'^\(\s*\$(\w+)\s*\+\s*1\s*\)\s*%\s*(\d+)$')
_STEP_RE    = re.compile(r'^\$(\w+)\s*([+-])\s*1$')  # $v = $v +/- 1
_MOD_RE     = re.compile(r'^\$(\w+)\s*%\s*(\d+)$')   # $v = $v % N
_GUARD_RE   = re.compile(r'^\$(\w+)\s*(==|!=|>=|<=|>|<)\s*(-?\d+|\$\w+)$')
_LITERAL_RE = re.compile(r'^-?\d+(?:\.\d+)?$')
_ELSE_RE    = re.compile(r'(?:else\s+if|elif)\s+(.*)$', re.I)
_STATE_ADD_RE = re.compile(
    r'^\$(\w+)\s*=\s*\$(\w+)\s*\+\s*\$(\w+)$')

_NEGATED_OP = {"==": "!=", "!=": "==", "<": ">=", ">=": "<", ">": "<=", "<=": ">"}

# Minimum branches that must actually cycle something before a chain counts as
# a menu — one lone self-assignment is far more likely to be ordinary state
# bookkeeping than a clickable slot list.
_MIN_SLOTS = 2


def _conditional_blocks(lines):
    """Return cleaned lines and ordered if/elif/else blocks, or None if malformed."""
    cleaned = [str(raw).split(";", 1)[0].strip() for raw in lines]
    root, stack = [], []
    body = root
    for index, line in enumerate(cleaned):
        low = line.casefold()
        alternative = _ELSE_RE.fullmatch(line)
        if low.startswith("if "):
            branch = {"condition": line[3:].strip(), "start": index + 1,
                      "body": []}
            block = {"start": index, "branches": [branch]}
            body.append(block)
            stack.append((block, body))
            body = branch["body"]
        elif alternative or low == "else":
            if (not stack or stack[-1][0]["branches"][-1]["condition"] is None
                    or alternative and not alternative.group(1).strip()):
                return None
            block = stack[-1][0]
            block["branches"][-1]["end"] = index
            branch = {"condition": alternative.group(1).strip() if alternative else None,
                      "start": index + 1, "body": []}
            block["branches"].append(branch)
            body = branch["body"]
        elif low == "endif":
            if not stack:
                return None
            block, body = stack.pop()
            block["branches"][-1]["end"] = index
        else:
            body.append(line)
    return (cleaned, root) if not stack else None


def _split_slot_branches(parsed, image_chains=None):
    """Find slot dispatch chains, including nested page menus."""
    if parsed is None:
        return []
    cleaned, root = parsed

    def scan(nodes):
        found = []
        for block in nodes:
            if not isinstance(block, dict):
                continue
            branches = block["branches"]
            nested = [item for branch in branches for item in scan(branch["body"])]
            first = _SLOT_RE.fullmatch(branches[0]["condition"] or "")
            parts = []
            if first:
                for branch in branches:
                    match = _SLOT_RE.fullmatch(branch["condition"] or "")
                    if match and match.group(1).casefold() == first.group(1).casefold():
                        body = [line for line in cleaned[branch["start"]:branch["end"]]
                                if line]
                        parts.append((match.group(1), match.group(2), body,
                                      branch["body"]))
            if len(parts) >= _MIN_SLOTS and image_chains is not None:
                image_chains.append(parts)
            if len(parts) >= _MIN_SLOTS and not nested:
                found.extend(part[:3] for part in parts)
            found.extend(nested)
        return found

    return scan(root)


def _cycle_values(lo, hi):
    return [str(i) for i in range(lo, hi + 1)]


def _resource_file(resources, name):
    if not resources or not name:
        return None
    lookup = getattr(resources, "get_ci", None)
    if lookup is not None:
        return (lookup(name) or {}).get("filename")
    return next((info.get("filename") for key, info in resources.items()
                 if key.casefold() == name.casefold()), None)


def _branch_image(nodes, resources):
    bindings = [re.fullmatch(r"ps-t100\s*=\s*(\S+)", line, re.I)
                for line in nodes if isinstance(line, str)]
    names = [match.group(1) for match in bindings if match]
    return _resource_file(resources, names[0]) if len(names) == 1 else None


def _guard(text, numeric_defaults=None):
    m = _GUARD_RE.fullmatch(text.strip())
    if not m:
        return None
    value = m.group(3)
    if value.startswith("$"):
        value = (numeric_defaults or {}).get(value[1:].casefold())
        if value is None:
            return None
    return {"var": m.group(1), "op": m.group(2), "value": value}


def _negate(guard):
    return None if not guard else {**guard, "op": _NEGATED_OP[guard["op"]]}


def _parse_branch(body, numeric_defaults=None, require_finite=False):
    """Return (var, values, effects) for one slot, or None if it cycles nothing.

    `effects` are the branch's other assignments — the mutual-exclusion rules a
    real click also applies (`if $bikinitop == 0 then $nipplepasties = 1`) —
    as [{when: {var, op, value} | None, var, value}] in source order.
    """
    var, values, effects = None, None, []
    cycle_kind, finite = None, False
    stack = []                    # {guard, branches} per open `if`
    wrap, in_wrap_else = None, False   # see the `$v < N` idiom below

    for line in body:
        low = line.lower()
        if low.startswith("if "):
            stack.append({"guard": _guard(line[3:], numeric_defaults),
                          "branches": 1})
            continue
        if low == "endif":
            if stack:
                stack.pop()
            if wrap and len(stack) < wrap[1]:
                wrap, in_wrap_else = None, False
            continue
        m_elif = _ELSE_RE.match(line)
        if m_elif or low == "else":
            in_wrap_else = bool(wrap) and len(stack) == wrap[1] and not m_elif
            if stack:
                frame = stack[-1]
                if m_elif:
                    # The earlier branches' exclusion isn't modelled, so this is
                    # a necessary condition for the body, not a sufficient one.
                    frame["guard"] = _guard(m_elif.group(1), numeric_defaults)
                else:
                    # Negating `else` is only exact while there was one branch.
                    frame["guard"] = (_negate(frame["guard"])
                                      if frame["branches"] == 1 else None)
                frame["branches"] += 1
            continue

        m = _ASSIGN_RE.fullmatch(line)
        if not m:
            continue
        lhs, rhs = m.group(1), m.group(2).strip()
        guard = stack[-1]["guard"] if stack else None

        flip = _FLIP_RE.fullmatch(rhs)
        if flip and flip.group(1) == lhs:
            var, values = lhs, ["0", "1"]
            cycle_kind = "flip"
            finite = True
            continue
        incr_mod = _INCR_MOD_RE.fullmatch(rhs)
        if incr_mod and incr_mod.group(1) == lhs:
            count = int(incr_mod.group(2))
            if count > 0:
                var, values = lhs, _cycle_values(0, count - 1)
                cycle_kind = "increment_mod"
                finite = True
            continue
        incr = (_INCR_RE.fullmatch(rhs) or _INCR_REV_RE.fullmatch(rhs))
        if incr and incr.group(1) == lhs:
            var, values = lhs, ["0", "1"]   # replaced below once the wrap is seen
            cycle_kind = "increment"
            finite = False
            if guard and guard["var"] == lhs and guard["op"] in ("<", "<="):
                wrap = (guard, len(stack))
            continue
        mod = _MOD_RE.fullmatch(rhs)
        if mod and mod.group(1) == lhs and lhs == var:
            count = int(mod.group(2))
            if count > 0:
                values = _cycle_values(0, count - 1)
                finite = True
            continue

        if not _LITERAL_RE.fullmatch(rhs):
            continue
        # `if $v < 2 / $v = $v + 1 / else / $v = 0 / endif`. Checked before the
        # trailing-`if` idiom below, which the negated else guard also matches.
        if (cycle_kind == "increment" and in_wrap_else and lhs == var
                and wrap[0]["var"] == var):
            hi = int(wrap[0]["value"]) + (1 if wrap[0]["op"] == "<=" else 0)
            lo = int(rhs)
            if hi >= lo:
                values = _cycle_values(lo, hi)
                finite = True
            continue
        # `if $v > 2 / $v = 0 / endif` closes the cycle opened by `$v = $v + 1`.
        if (cycle_kind == "increment" and guard and lhs == var
                and guard["var"] == var
                and guard["op"] in (">", ">=")):
            hi = int(guard["value"]) - (1 if guard["op"] == ">=" else 0)
            lo = int(rhs)
            if hi >= lo:
                values = _cycle_values(lo, hi)
                finite = True
            continue
        # A binary flip's reset is bookkeeping only when its guard is
        # demonstrably unreachable for the flip's known range. Reachable
        # same-variable assignments are real effects and must be replayed.
        if (cycle_kind == "flip" and lhs == var and guard
                and guard["var"] == var
                and guard["op"] in (">", ">=")):
            boundary = int(guard["value"])
            max_value = max(int(value) for value in values)
            unreachable = (
                (guard["op"] == ">" and max_value <= boundary)
                or (guard["op"] == ">=" and max_value < boundary)
            )
            if unreachable:
                continue
        effects.append({"when": guard, "var": lhs, "value": rhs})

    if var is None or (require_finite and not finite):
        return None
    return var, values, effects


def _prefixed(name, var_prefix):
    return f"{var_prefix}{name}" if var_prefix else name


def _parse_arrow_button(lines):
    """Return (var, values) for one ButtonNLeft/Right command list.

    Some image menus implement every item as two independent hit regions
    instead of dispatching a clicked-slot number.  One side decrements and
    wraps at the low end, while the other increments and wraps at the high
    end.  Either side fully describes the finite value range::

        $Hair = $Hair - 1
        if $Hair < 1
            $Hair = 5
        endif
    """
    cleaned = [str(raw).split(";", 1)[0].strip() for raw in lines]
    variable = direction = None
    for line in cleaned:
        match = _ASSIGN_RE.fullmatch(line)
        if not match:
            continue
        lhs, rhs = match.group(1), match.group(2).strip()
        step = _STEP_RE.fullmatch(rhs)
        if step and step.group(1).lower() == lhs.lower():
            variable, direction = lhs, step.group(2)
            break
    if variable is None:
        return None

    guard = reset = None
    for index, line in enumerate(cleaned):
        if not line.lower().startswith("if "):
            continue
        candidate = _guard(line[3:])
        if not candidate or candidate["var"].lower() != variable.lower():
            continue
        valid_ops = ("<", "<=") if direction == "-" else (">", ">=")
        if candidate["op"] not in valid_ops:
            continue
        for later in cleaned[index + 1:]:
            if later.lower() == "endif":
                break
            assignment = _ASSIGN_RE.fullmatch(later)
            if (assignment and assignment.group(1).lower() == variable.lower()
                    and _LITERAL_RE.fullmatch(assignment.group(2).strip())):
                guard, reset = candidate, assignment.group(2).strip()
                break
        if guard:
            break
    if not guard or reset is None:
        return None

    boundary = int(guard["value"])
    reset_value = int(float(reset))
    if direction == "-":
        lo = boundary + (1 if guard["op"] == "<=" else 0)
        hi = reset_value
    else:
        lo = reset_value
        hi = boundary - (1 if guard["op"] == ">=" else 0)
    if hi < lo:
        return None
    return variable, _cycle_values(lo, hi)


def _static_numeric_defaults(sections):
    """Numeric globals with one declaration and no runtime assignments."""
    defaults, mutable = {}, set()
    declaration = re.compile(r'^global\s+\$(\w+)\s*=\s*(-?\d+)\s*$', re.I)
    any_declaration = re.compile(r'^global\s+(?:persist\s+)?\$(\w+)\b', re.I)
    assignment = re.compile(r'^(?:post\s+)?\$(\w+)\s*(?:=|\+=|-=)', re.I)
    for section, lines in sections.items():
        for raw in lines:
            line = str(raw).split(";", 1)[0].strip()
            match = declaration.fullmatch(line) if section.casefold() == "constants" else None
            if match:
                name, value = match.groups()
                name = name.casefold()
                if name in defaults:
                    mutable.add(name)
                defaults[name] = value
            else:
                match = assignment.match(line) or any_declaration.match(line)
                if match:
                    mutable.add(match.group(1).casefold())
    return {name: value for name, value in defaults.items()
            if name not in mutable}


def _mouse_press_vars(sections):
    """Find variables set by mouse-bound Key sections."""
    pressed = {}
    mouse_key = re.compile(r'VK_(?:[LRM]BUTTON|XBUTTON[12])$', re.I)
    for section, lines in sections.items():
        if not str(section).casefold().startswith("key"):
            continue
        cleaned = [str(raw).split(";", 1)[0].strip() for raw in lines]
        keys = [line.partition("=")[2].strip() for line in cleaned
                if line.partition("=")[0].strip().casefold() == "key"]
        if not any(mouse_key.fullmatch(token) for key in keys
                   for token in key.split()):
            continue
        for line in cleaned:
            assignment = _ASSIGN_RE.fullmatch(line)
            if assignment and _LITERAL_RE.fullmatch(assignment.group(2).strip()):
                value = assignment.group(2).strip()
                if float(value) != 0:
                    pressed[assignment.group(1).casefold()] = value
    return pressed


def _condition_facts(text, defaults, pressed):
    """Cursor bounds, static impossibility, and mouse activation for a guard."""
    if text is None:
        return 0, False, False
    try:
        node = condition.parse(text)
    except condition.ConditionError:
        return 0, False, False

    def cursor_bounds(part):
        if isinstance(part, condition.Paren):
            return cursor_bounds(part.inner)
        if isinstance(part, (condition.And, condition.Or)):
            counts = [cursor_bounds(child) for child in part.parts]
            return sum(counts) if isinstance(part, condition.And) else max(counts)
        if isinstance(part, condition.Cmp) and part.op in ("<", "<=", ">", ">="):
            return bool(re.search(r'\bcursor_[xy]\b', part.render(), re.I))
        return 0

    names = node.variables()
    bindings = {name: defaults[name.casefold()] for name in names
                if name.casefold() in defaults}
    inactive = condition.reduce(node, bindings) == condition.FALSE
    mouse = any(
        condition.reduce(node, {name: "0"}) == condition.FALSE
        and condition.reduce(node, {name: pressed[name.casefold()]}) != condition.FALSE
        for name in names if name.casefold() in pressed)
    return cursor_bounds(node), inactive, mouse


def _mouse_button_items(sections, blocks):
    """Find finite click actions inside bounded cursor hit regions."""
    defaults = _static_numeric_defaults(sections)
    pressed = _mouse_press_vars(sections)
    if not pressed:
        return []
    found, ordinal = [], 0
    for section, parsed in blocks.items():
        if parsed is None:
            continue
        cleaned, root = parsed
        lines = sections[section]

        def walk(nodes, cursor_bounds=0, inactive=False):
            nonlocal ordinal
            for block in nodes:
                if not isinstance(block, dict):
                    continue
                for branch in block["branches"]:
                    bounds, impossible, mouse = _condition_facts(
                        branch["condition"], defaults, pressed)
                    total_bounds = cursor_bounds + bounds
                    if mouse and total_bounds >= 2:
                        action = _parse_branch(
                            cleaned[branch["start"]:branch["end"]],
                            numeric_defaults=defaults, require_finite=True)
                        if action:
                            slot = ordinal
                            ordinal += 1
                            if not (inactive or impossible):
                                found.append((slot, section, *action,
                                              line_source(lines[block["start"]])
                                              or first_source(lines) or {}))
                            continue
                    walk(branch["body"], total_bounds, inactive or impossible)

        walk(root)
    return found if len(found) >= _MIN_SLOTS else []


def _controller_records(sections, section_filter=None):
    """Return cleaned lines from selected sections with source provenance."""
    records = []
    for section, lines in sections.items():
        if section_filter is not None and not section_filter(str(section)):
            continue
        for raw in lines:
            line = str(raw).split(";", 1)[0].strip()
            if line:
                records.append((section, line, raw))
    return records


def _controller_wrap_values(records, variable):
    """Return the range from an authored ``if state > N`` reset block."""
    ranges = []
    variable = variable.casefold()
    for index, (_section, line, _raw) in enumerate(records):
        if not line.lower().startswith("if "):
            continue
        guard = _guard(line[3:])
        if (not guard or guard["var"].casefold() != variable
                or guard["op"] not in (">", ">=")):
            continue
        depth = 1
        reset = None
        for _section2, later, _raw2 in records[index + 1:]:
            low = later.lower()
            if low.startswith("if "):
                depth += 1
                continue
            if low == "endif":
                depth -= 1
                if depth == 0:
                    break
                continue
            if depth != 1:
                continue
            assignment = _ASSIGN_RE.fullmatch(later)
            if (assignment
                    and assignment.group(1).casefold() == variable
                    and _LITERAL_RE.fullmatch(assignment.group(2).strip())):
                reset = assignment.group(2).strip()
                break
        if reset is None:
            continue
        try:
            lower = int(float(reset))
            upper = int(guard["value"]) - (1 if guard["op"] == ">=" else 0)
        except ValueError:
            continue
        if upper >= lower:
            ranges.append(tuple(_cycle_values(lower, upper)))
    unique = set(ranges)
    return list(next(iter(unique))) if len(unique) == 1 else None


def extract_controller_toggles(sections, forwarded_vars, var_prefix=None,
                               source=None, canonical_vars=None, resources=None):
    """Find the small pulse/state controller pattern used by namespace menus.

    ``forwarded_vars`` is the set of local variables that have already been
    proven to write into a selected INI namespace. Limiting discovery to that
    set keeps ordinary UI bookkeeping invisible and avoids interpreting the
    broader 3DMigoto language.

    The returned mapping is keyed by the controller's unprefixed canonical
    variable. Its payload mirrors a normal menu entry; the caller can remap
    ``var`` to the resolved destination identity after checking model gates.
    """
    canon = (canonical_vars if canonical_vars is not None
             else canonical_var_names(sections))

    def declared(name):
        return canon.get(name.casefold(), name)

    allowed = {
        declared(str(name)).casefold() for name in (forwarded_vars or ())
    }
    if not allowed:
        return {}

    present_records = _controller_records(
        sections, lambda name: name.casefold() == "present")
    flips, image_candidates = {}, {}
    for section, lines in sections.items():
        if not section.casefold().startswith("commandlist"):
            continue
        for raw in lines:
            line = str(raw).split(";", 1)[0].strip()
            assignment = _ASSIGN_RE.fullmatch(line)
            if assignment:
                lhs, rhs = assignment.group(1), assignment.group(2).strip()
                flip = _FLIP_RE.fullmatch(rhs)
                if flip and flip.group(1).casefold() == lhs.casefold():
                    flips.setdefault(lhs.casefold(), (lhs, section, raw))
        if resources is None:
            continue
        parsed = _conditional_blocks(lines)
        if parsed is None:
            continue

        def collect(nodes):
            for block in nodes:
                if not isinstance(block, dict):
                    continue
                branches = block["branches"]
                first = _SLOT_RE.fullmatch(branches[0]["condition"] or "")
                if (first and first.group(2) == "0" and len(branches) == 2
                        and branches[1]["condition"] is None):
                    images = [_branch_image(branch["body"], resources)
                              for branch in branches]
                    image = (images[0] if all(images) and
                             images[0].casefold() == images[1].casefold()
                             else None)
                    image_candidates.setdefault(first.group(1).casefold(), []).append(image)
                for branch in branches:
                    collect(branch["body"])

        collect(parsed[1])
    pulse_images = {
        pulse: images[0] for pulse, images in image_candidates.items()
        if all(images) and len({image.casefold() for image in images}) == 1
    }

    found = {}

    def add(local, values, section, raw, pulse_var=None):
        local = declared(local)
        if local.casefold() not in allowed:
            return
        src = line_source(raw) or first_source(sections.get(section, ())) or {}
        controller = {
            "name": local,
            "var": _prefixed(local, var_prefix),
            "values": values,
            "effects": [],
            "source": source,
            "ini_path": src.get("ini_path"),
            "section": section,
        }
        if pulse_var is not None:
            controller["_pulse_var"] = declared(pulse_var)
            image = pulse_images.get(pulse_var.casefold())
            if image:
                controller["image_file"] = image
        found[local] = controller

    for local_key in allowed:
        flip = flips.get(local_key)
        if flip:
            add(flip[0], ["0", "1"], flip[1], flip[2])

    for section, line, raw in present_records:
        match = _STATE_ADD_RE.fullmatch(line)
        if not match:
            continue
        lhs, state, pulse = match.groups()
        if (lhs.casefold() != state.casefold()
                or lhs.casefold() not in allowed
                or pulse.casefold() not in flips):
            continue
        values = _controller_wrap_values(present_records, lhs)
        if values:
            add(lhs, values, section, raw, pulse_var=pulse)
    return found


def extract_menu_toggles(sections, var_prefix=None, source=None,
                         canonical_vars=None, resources=None):
    """Return {entry key: {name, slot, var, values, effects, source, ini_path,
    section}} for every clickable menu slot found in the mod's CommandLists.

    The ini carries no human-readable label for a slot (its on-screen caption
    is a .dds image), so the variable name doubles as the display name.
    """
    menu = {}
    canon = (canonical_vars if canonical_vars is not None
             else canonical_var_names(sections))
    blocks = {name: _conditional_blocks(lines) for name, lines in sections.items()
              if name.casefold().startswith("commandlist")}
    section_lookup = {name.casefold(): lines for name, lines in sections.items()}
    image_chains = []

    def declared(name):
        return canon.get(name.lower(), name)

    def add_entry(section, slot, variable, values, effects=(), *,
                  src=None, key_section=None, kind=None, image=None):
        if image is None and kind != "mouse_region":
            for raw in section_lookup.get(f"commandlisticon{slot}", ()):
                binding = re.fullmatch(
                    r"ps-t100\s*=\s*(\S+)",
                    str(raw).split(";", 1)[0].strip(), re.I)
                if binding:
                    image = _resource_file(resources, binding.group(1))
                    if image:
                        break
        variable = declared(variable)
        base_key = _prefixed(f"{key_section or section}#{slot}", var_prefix)
        key = base_key
        suffix = 2
        while key in menu:
            key = f"{base_key}_{suffix}"
            suffix += 1
        entry = {
            "name": variable,
            "slot": int(slot),
            "var": _prefixed(variable, var_prefix),
            "values": values,
            "effects": [
                {
                    "when": (None if effect["when"] is None else
                             {**effect["when"], "var": _prefixed(
                                 declared(effect["when"]["var"]), var_prefix)}),
                    "var": _prefixed(declared(effect["var"]), var_prefix),
                    "value": effect["value"],
                }
                for effect in effects
            ],
            "source": source,
            "ini_path": (src or {}).get("ini_path"),
            "section": section,
        }
        if kind is not None:
            entry["kind"] = kind
        if image:
            entry["image_file"] = image
        menu[key] = entry

    for name, parsed_blocks in blocks.items():
        parsed = []
        for _slot_var, slot_value, body in _split_slot_branches(
                parsed_blocks, image_chains if resources is not None else None):
            info = _parse_branch(body)
            if info:
                parsed.append((slot_value, info))
        if len(parsed) < _MIN_SLOTS:
            continue

        lines = sections[name]
        src = first_source(lines) or {}
        for slot_value, (var, values, effects) in parsed:
            add_entry(name, slot_value, var, values, effects, src=src)

    # Arrow-pair image menus have no clicked-slot dispatch chain.  Their
    # numeric ButtonNLeft/ButtonNRight sections each mutate one variable and
    # wrap it at the authored bounds.  Require at least two distinct numbered
    # items before treating this naming/behaviour combination as a menu; a
    # lone step button elsewhere in a mod should remain ordinary bookkeeping.
    arrow_items = {}
    button_re = re.compile(r'^CommandListButton(\d+)(Left|Right)$', re.I)
    for name, lines in sections.items():
        match = button_re.fullmatch(name)
        if not match:
            continue
        parsed = _parse_arrow_button(lines)
        if not parsed:
            continue
        slot = int(match.group(1))
        variable, values = parsed
        arrow_items.setdefault(slot, []).append(
            (variable, values, name, first_source(lines) or {}))

    if len(arrow_items) >= _MIN_SLOTS:
        for slot, candidates in sorted(arrow_items.items()):
            # Both directions normally agree. Prefer the first range and only
            # merge candidates that drive the same case-insensitive variable.
            variable, values, section, src = candidates[0]
            same_var = [item for item in candidates
                        if item[0].lower() == variable.lower()]
            if len(same_var) > 1:
                ranges = {tuple(item[1]) for item in same_var}
                if len(ranges) == 1:
                    values = same_var[0][1]
            button_section = re.sub(r"(?:Left|Right)$", "", section,
                                    flags=re.I)
            add_entry(section, slot, variable, values, src=src,
                      key_section=button_section)
    for slot, section, variable, values, effects, src in _mouse_button_items(
            sections, blocks):
        image = None
        for raw in section_lookup.get(f"commandlistdrawbutton_{slot}", ()):
            binding = re.fullmatch(
                r"ps-t100\s*=\s*(\S+)",
                str(raw).split(";", 1)[0].strip(), re.I)
            if binding:
                image = _resource_file(resources, binding.group(1)) or image
        add_entry(section, slot, variable, values, effects, src=src,
                  kind="mouse_region", image=image)
    known_slots = {info["slot"] for info in menu.values()}
    candidates = {}
    for chain in image_chains:
        mapped = [(int(slot), _branch_image(nodes, resources))
                  for _var, slot, _body, nodes in chain
                  if int(slot) in known_slots]
        if sum(bool(image) for _slot, image in mapped) >= _MIN_SLOTS:
            for slot, image in mapped:
                candidates.setdefault(slot, set()).add(image)
    for info in menu.values():
        images = candidates.get(info["slot"], ())
        if (info.get("kind") != "mouse_region" and len(images) == 1
                and None not in images):
            info["image_file"] = next(iter(images))
    return menu


def extract_menu_var_names(sections, var_prefix=None, menu=None,
                          canonical_vars=None):
    """Flat set of every variable a clickable menu can change — the cycled
    variables plus the ones their mutual-exclusion rules write."""
    found = set()
    menu = (menu if menu is not None else
            extract_menu_toggles(sections, var_prefix=var_prefix,
                                 canonical_vars=canonical_vars))
    for info in menu.values():
        found.add(info["var"])
        found.update(e["var"] for e in info["effects"])
    return found

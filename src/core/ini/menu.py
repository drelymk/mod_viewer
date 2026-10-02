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
_GUARD_RE   = re.compile(r'^\$(\w+)\s*(==|!=|>=|<=|>|<)\s*(-?\d+(?:\.\d+)?|\$\w+)$')
_LITERAL_RE = re.compile(r'^-?\d+(?:\.\d+)?$')
_INTEGER_RE = re.compile(r'(-?\d+)(?:\.0+)?$')
_ELSE_RE    = re.compile(r'(?:else\s+if|elif)\s+(.*)$', re.I)
_STATE_ADD_RE = re.compile(
    r'^\$(\w+)\s*=\s*\$(\w+)\s*\+\s*\$(\w+)$')
_BUTTON_RE = re.compile(r'CommandListButton(\d+)(Left|Right)', re.I)
_IMAGE_BINDING_RE = re.compile(r'ps-t100\s*=\s*(\S+)', re.I)

_NEGATED_OP = {"==": "!=", "!=": "==", "<": ">=", ">=": "<", ">": "<=", "<=": ">"}

# Minimum branches that must actually cycle something before a chain counts as
# a menu — one lone self-assignment is far more likely to be ordinary state
# bookkeeping than a clickable slot list.
_MIN_SLOTS = 2


def _clean_line(raw):
    return str(raw).split(";", 1)[0].strip()


def _conditional_blocks(lines):
    """Return cleaned lines and ordered if/elif/else blocks, or None if malformed."""
    cleaned = [_clean_line(raw) for raw in lines]
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


def _slot_chains(parsed, image_chains=None):
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
                found.append([part[:3] for part in parts])
            found.extend(nested)
        return found

    return scan(root)


def _cycle_values(lo, hi):
    return [str(i) for i in range(lo, hi + 1)]


def _same_var(left, right):
    return (left is not None and right is not None
            and left.casefold() == right.casefold())


def _integer_value(text):
    match = _INTEGER_RE.fullmatch(str(text))
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            pass
    return None


def _wrap_values(guard, reset, *, decrement=False):
    """Return an exact integer cycle; unsupported or reversed bounds have none."""
    boundary, reset = _integer_value(guard["value"]), _integer_value(reset)
    if boundary is None or reset is None:
        return None
    if decrement:
        lo, hi = boundary + (guard["op"] == "<="), reset
    else:
        lo = reset
        hi = boundary + {"<": 0, "<=": 1, ">": 0, ">=": -1}[guard["op"]]
    return _cycle_values(lo, hi) if hi >= lo else None


def _conditional_bodies(nodes):
    """Visit each body separately, preserving its execution order and guards."""
    yield nodes
    for block in nodes:
        if isinstance(block, dict):
            for branch in block["branches"]:
                yield from _conditional_bodies(branch["body"])


def _wrap_ranges(nodes, variable, *, decrement=False):
    """Find direct resets in wrap blocks at the current conditional depth."""
    ranges = set()
    for block in nodes:
        if not isinstance(block, dict):
            continue
        branch = block["branches"][0]
        guard = _guard(branch["condition"])
        ops = ("<", "<=") if decrement else (">", ">=")
        if not guard or not _same_var(guard["var"], variable) or guard["op"] not in ops:
            continue
        resets = []
        for line in branch["body"]:
            assignment = _ASSIGN_RE.fullmatch(line) if isinstance(line, str) else None
            if assignment and _same_var(assignment.group(1), variable):
                resets.append(assignment.group(2).strip())
        if len(resets) == 1:
            values = _wrap_values(guard, resets[0], decrement=decrement)
            if values:
                ranges.add(tuple(values))
    return ranges


def _resource_file(resources, name):
    if not resources or not name:
        return None
    lookup = getattr(resources, "get_ci", None)
    if lookup is not None:
        return (lookup(name) or {}).get("filename")
    return next((info.get("filename") for key, info in resources.items()
                 if key.casefold() == name.casefold()), None)


def _branch_image(nodes, resources):
    bindings = [_IMAGE_BINDING_RE.fullmatch(line)
                for line in nodes if isinstance(line, str)]
    names = [match.group(1) for match in bindings if match]
    return _resource_file(resources, names[0]) if len(names) == 1 else None


def _section_image(lines, resources, *, last=False):
    """Resolve the first or last usable direct artwork binding in a section."""
    image = None
    for raw in lines:
        binding = _IMAGE_BINDING_RE.fullmatch(_clean_line(raw))
        if binding:
            image = _resource_file(resources, binding.group(1)) or image
            if image and not last:
                break
    return image


def _slot_images(image_chains, known_slots, slot_groups, resources):
    """Match dispatch artwork to slots, omitting missing or conflicting images."""
    candidates = {}
    for chain in image_chains:
        artwork = [(int(slot), _branch_image(nodes, resources))
                   for _var, slot, _body, nodes in chain]
        mapped = [(slot, image) for slot, image in artwork if slot in known_slots]
        if sum(bool(image) for _slot, image in mapped) < _MIN_SLOTS:
            # Offset click IDs can use the same complete numbering pattern as
            # artwork indices. Accept only one whole-group translation.
            image_slots = {slot for slot, _image in artwork}
            offsets = set()
            for group in slot_groups:
                if len(group) != len(image_slots):
                    continue
                offset = min(group) - min(image_slots)
                if {slot + offset for slot in image_slots} == group:
                    offsets.add(offset)
            if len(offsets) == 1:
                offset = next(iter(offsets))
                mapped = [(slot + offset, image) for slot, image in artwork]
        if sum(bool(image) for _slot, image in mapped) >= _MIN_SLOTS:
            for slot, image in mapped:
                candidates.setdefault(slot, set()).add(image)
    return {slot: next(iter(images)) for slot, images in candidates.items()
            if len(images) == 1 and None not in images}


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
    stack = []                    # {guard, branches, body} per open `if`
    step_body = ()
    wrap, in_wrap_else = None, False   # see the `$v < N` idiom below

    for index, line in enumerate(body):
        low = line.lower()
        if low.startswith("if "):
            stack.append({"guard": _guard(line[3:], numeric_defaults),
                          "branches": 1, "body": index})
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
                frame["body"] = index
            continue

        m = _ASSIGN_RE.fullmatch(line)
        if not m:
            continue
        lhs, rhs = m.group(1), m.group(2).strip()
        guard = stack[-1]["guard"] if stack else None

        flip = _FLIP_RE.fullmatch(rhs)
        if flip and _same_var(flip.group(1), lhs):
            var, values = lhs, ["0", "1"]
            cycle_kind = "flip"
            finite = True
            continue
        incr_mod = _INCR_MOD_RE.fullmatch(rhs)
        if incr_mod and _same_var(incr_mod.group(1), lhs):
            count = int(incr_mod.group(2))
            if count > 0:
                var, values = lhs, _cycle_values(0, count - 1)
                cycle_kind = "increment_mod"
                finite = True
            continue
        incr = (_INCR_RE.fullmatch(rhs) or _INCR_REV_RE.fullmatch(rhs))
        if incr and _same_var(incr.group(1), lhs):
            var, values = lhs, ["0", "1"]   # Placeholder until the wrap count is known.
            cycle_kind = "increment"
            finite = False
            step_body = tuple(frame["body"] for frame in stack)
            if guard and _same_var(guard["var"], lhs) and guard["op"] in ("<", "<="):
                wrap = (guard, len(stack))
            continue
        mod = _MOD_RE.fullmatch(rhs)
        if mod and _same_var(mod.group(1), lhs) and _same_var(lhs, var):
            count = int(mod.group(2))
            if count > 0:
                values = _cycle_values(0, count - 1)
                finite = True
            continue

        if not _LITERAL_RE.fullmatch(rhs):
            continue
        if cycle_kind == "increment" and _same_var(lhs, var) and stack:
            reset_body = tuple(frame["body"] for frame in stack)
            # Only the cycle's own wrap branch may separate its step and reset.
            before_step = (in_wrap_else and len(reset_body) == len(step_body)
                           and reset_body[:-1] == step_body[:-1])
            if not before_step and reset_body[:-1] != step_body:
                return None
        # `if $v < 2 / $v = $v + 1 / else / $v = 0 / endif`. Checked before the
        # trailing-`if` idiom below, which the negated else guard also matches.
        if (cycle_kind == "increment" and in_wrap_else and _same_var(lhs, var)
                and _same_var(wrap[0]["var"], var)):
            values = _wrap_values(wrap[0], rhs)
            if values is None:
                return None
            finite = True
            continue
        # `if $v > 2 / $v = 0 / endif` closes the cycle opened by `$v = $v + 1`.
        if (cycle_kind == "increment" and guard and _same_var(lhs, var)
                and _same_var(guard["var"], var)
                and guard["op"] in (">", ">=")):
            values = _wrap_values(guard, rhs)
            if values is None:
                return None
            finite = True
            continue
        # A binary flip's reset is bookkeeping only when its guard is
        # demonstrably unreachable for the flip's known range. Reachable
        # same-variable assignments are real effects and must be replayed.
        if (cycle_kind == "flip" and _same_var(lhs, var) and guard
                and _same_var(guard["var"], var)
                and guard["op"] in (">", ">=")):
            boundary = _integer_value(guard["value"])
            max_value = max(int(value) for value in values)
            unreachable = boundary is not None and (
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
    parsed = _conditional_blocks(lines)
    if parsed is None:
        return None
    for body in _conditional_bodies(parsed[1]):
        for index, line in enumerate(body):
            assignment = _ASSIGN_RE.fullmatch(line) if isinstance(line, str) else None
            if not assignment:
                continue
            lhs, rhs = assignment.group(1), assignment.group(2).strip()
            step = _STEP_RE.fullmatch(rhs)
            if step and _same_var(step.group(1), lhs):
                ranges = _wrap_ranges(body[index + 1:], lhs,
                                      decrement=step.group(2) == "-")
                if len(ranges) == 1:
                    return lhs, list(next(iter(ranges)))
    return None


def _arrow_button_items(sections):
    """Recognise numbered arrow menus, retaining each item's first valid range."""
    items = {}
    for section, lines in sections.items():
        match = _BUTTON_RE.fullmatch(section)
        if not match:
            continue
        action = _parse_arrow_button(lines)
        if action:
            slot = int(match.group(1))
            items.setdefault(slot, (section, *action, first_source(lines) or {}))
    # A lone step button is more likely ordinary bookkeeping than a menu.
    if len(items) < _MIN_SLOTS:
        return []
    return [(slot, *item) for slot, item in sorted(items.items())]


def _static_numeric_defaults(sections):
    """Numeric globals with one declaration and no runtime assignments."""
    defaults, mutable = {}, set()
    declaration = re.compile(r'^global\s+\$(\w+)\s*=\s*(-?\d+)\s*$', re.I)
    any_declaration = re.compile(r'^global\s+(?:persist\s+)?\$(\w+)\b', re.I)
    assignment = re.compile(r'^(?:post\s+)?\$(\w+)\s*(?:=|\+=|-=)', re.I)
    for section, lines in sections.items():
        for raw in lines:
            line = _clean_line(raw)
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
        cleaned = [_clean_line(raw) for raw in lines]
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
            line = _clean_line(raw)
            if line:
                records.append((section, line, raw))
    return records


def _controller_wrap_values(records, variable):
    """Return a range whose state-add and reset share an execution body."""
    parsed = _conditional_blocks(line for _section, line, _raw in records)
    if parsed is None:
        return None

    def is_step(line):
        match = _STATE_ADD_RE.fullmatch(line) if isinstance(line, str) else None
        return (match and _same_var(match.group(1), variable)
                and _same_var(match.group(2), variable))

    ranges = set()
    for body in _conditional_bodies(parsed[1]):
        for index, node in enumerate(body):
            if is_step(node):
                ranges.update(_wrap_ranges(body[index + 1:], variable))
            elif isinstance(node, dict):
                branches = node["branches"]
                # The pre-step idiom puts the state-add directly in wrap's else.
                if (len(branches) == 2 and branches[1]["condition"] is None
                        and any(is_step(line) for line in branches[1]["body"])):
                    ranges.update(_wrap_ranges([node], variable))
    return list(next(iter(ranges))) if len(ranges) == 1 else None


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
            line = _clean_line(raw)
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
    slot_groups = []

    def declared(name):
        return canon.get(name.lower(), name)

    def add_entry(section, slot, variable, values, effects=(), *,
                  src=None, key_section=None, kind=None, image=None):
        if image is None and kind != "mouse_region":
            image = _section_image(
                section_lookup.get(f"commandlisticon{slot}", ()), resources)
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
        for chain in _slot_chains(
                parsed_blocks, image_chains if resources is not None else None):
            parsed = []
            for _slot_var, slot_value, body in chain:
                info = _parse_branch(body)
                if info:
                    parsed.append((slot_value, info))
            if len(parsed) < _MIN_SLOTS:
                continue

            slot_groups.append({int(slot) for slot, _info in parsed})
            src = first_source(sections[name]) or {}
            for slot_value, (var, values, effects) in parsed:
                add_entry(name, slot_value, var, values, effects, src=src)

    for slot, section, variable, values, src in _arrow_button_items(sections):
        button_section = re.sub(r"(?:Left|Right)$", "", section, flags=re.I)
        add_entry(section, slot, variable, values, src=src,
                  key_section=button_section)
    for slot, section, variable, values, effects, src in _mouse_button_items(
            sections, blocks):
        image = _section_image(
            section_lookup.get(f"commandlistdrawbutton_{slot}", ()),
            resources, last=True)
        add_entry(section, slot, variable, values, effects, src=src,
                  kind="mouse_region", image=image)
    images = _slot_images(image_chains, {info["slot"] for info in menu.values()},
                          slot_groups, resources)
    for info in menu.values():
        if info.get("kind") != "mouse_region" and info["slot"] in images:
            info["image_file"] = images[info["slot"]]
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

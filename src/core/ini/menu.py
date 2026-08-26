"""Discovery of in-game *clickable menu* toggles.

The chain is recognised structurally (an `if $X == <int>` / `elif` chain whose
branches assign back to themselves) rather than by section name, since only the
layout is a convention — `$clickedSlot` is not.
"""

import re

from .sections import canonical_var_names, first_source

# A branch head that dispatches on an integer slot: `$clickedSlot == 3`.
_SLOT_RE = re.compile(r'\$(\w+)\s*={2,3}\s*(\d+)$')

_ASSIGN_RE  = re.compile(r'^\$(\w+)\s*=\s*(.+)$')
_FLIP_RE    = re.compile(r'^1\s*-\s*\$(\w+)$')       # $v = 1 - $v
_INCR_RE    = re.compile(r'^\$(\w+)\s*\+\s*1$')      # $v = $v + 1
_INCR_MOD_RE = re.compile(                              # $v = ($v + 1) % N
    r'^\(\s*\$(\w+)\s*\+\s*1\s*\)\s*%\s*(\d+)$')
_STEP_RE    = re.compile(r'^\$(\w+)\s*([+-])\s*1$')  # $v = $v +/- 1
_MOD_RE     = re.compile(r'^\$(\w+)\s*%\s*(\d+)$')   # $v = $v % N
_GUARD_RE   = re.compile(r'^\$(\w+)\s*(==|!=|>=|<=|>|<)\s*(-?\d+)$')
_LITERAL_RE = re.compile(r'^-?\d+(?:\.\d+)?$')
_ELSE_RE    = re.compile(r'(?:else\s+if|elif)\s+(.*)$', re.I)

_NEGATED_OP = {"==": "!=", "!=": "==", "<": ">=", ">=": "<", ">": "<=", "<=": ">"}

# Minimum branches that must actually cycle something before a chain counts as
# a menu — one lone self-assignment is far more likely to be ordinary state
# bookkeeping than a clickable slot list.
_MIN_SLOTS = 2


def _split_slot_branches(lines):
    """[(slot_var, slot_value, body)] for an `if $X == N / elif ...` chain.

    Every chain is matched, including chains nested inside a branch of another
    slot/navigation chain. Body lines keep their nested if/endif so
    _parse_branch can read the guards inside.
    """
    cleaned = [raw.split(";")[0].strip() for raw in lines]

    def scan(block):
        found, i = [], 0
        while i < len(block):
            line = block[i]
            if not line.lower().startswith("if "):
                i += 1
                continue

            depth, j = 1, i + 1
            parts = [(line[3:].strip(), i + 1, None)]
            end = None
            while j < len(block):
                cur = block[j]
                low = cur.lower()
                if low.startswith("if "):
                    depth += 1
                elif low == "endif":
                    depth -= 1
                    if depth == 0:
                        cond, start, _ = parts[-1]
                        parts[-1] = (cond, start, j)
                        end = j
                        break
                elif depth == 1:
                    m_elif = _ELSE_RE.match(cur)
                    if m_elif or low == "else":
                        cond, start, _ = parts[-1]
                        parts[-1] = (cond, start, j)
                        parts.append((m_elif.group(1).strip() if m_elif else None,
                                      j + 1, None))
                j += 1

            if end is None:
                # Tolerate malformed nesting the same way the broader reader
                # does: keep looking inside instead of claiming a partial chain.
                i += 1
                continue

            # Page/mode menus commonly put another clicked-slot chain inside
            # an outer navigation chain. Search each branch recursively first;
            # if it contains a real multi-slot chain, the outer integer chain
            # is navigation/page dispatch rather than a clickable menu itself.
            nested = []
            for _cond, start, stop in parts:
                nested.extend(scan(block[start:stop]))

            first = _SLOT_RE.fullmatch(parts[0][0] or "")
            slot_parts = []
            if first:
                slot_var = first.group(1)
                for cond, start, stop in parts:
                    match = _SLOT_RE.fullmatch(cond or "")
                    if match and match.group(1).lower() == slot_var.lower():
                        body = [text for text in block[start:stop] if text]
                        slot_parts.append((match.group(1), match.group(2), body))
            if len(slot_parts) >= _MIN_SLOTS and not nested:
                found.extend(slot_parts)
            found.extend(nested)
            i = end + 1
        return found

    return scan(cleaned)


def _cycle_values(lo, hi):
    return [str(i) for i in range(lo, hi + 1)]


def _guard(text):
    m = _GUARD_RE.fullmatch(text.strip())
    return {"var": m.group(1), "op": m.group(2), "value": m.group(3)} if m else None


def _negate(guard):
    return None if not guard else {**guard, "op": _NEGATED_OP[guard["op"]]}


def _parse_branch(body):
    """Return (var, values, effects) for one slot, or None if it cycles nothing.

    `effects` are the branch's other assignments — the mutual-exclusion rules a
    real click also applies (`if $bikinitop == 0 then $nipplepasties = 1`) —
    as [{when: {var, op, value} | None, var, value}] in source order.
    """
    var, values, effects = None, None, []
    stack = []                    # {guard, branches} per open `if`
    wrap, in_wrap_else = None, False   # see the `$v < N` idiom below

    for line in body:
        low = line.lower()
        if low.startswith("if "):
            stack.append({"guard": _guard(line[3:]), "branches": 1})
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
                    frame["guard"] = _guard(m_elif.group(1))
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
            continue
        incr_mod = _INCR_MOD_RE.fullmatch(rhs)
        if incr_mod and incr_mod.group(1) == lhs:
            count = int(incr_mod.group(2))
            if count > 0:
                var, values = lhs, _cycle_values(0, count - 1)
            continue
        incr = _INCR_RE.fullmatch(rhs)
        if incr and incr.group(1) == lhs:
            var, values = lhs, ["0", "1"]   # replaced below once the wrap is seen
            if guard and guard["var"] == lhs and guard["op"] in ("<", "<="):
                wrap = (guard, len(stack))
            continue
        mod = _MOD_RE.fullmatch(rhs)
        if mod and mod.group(1) == lhs and lhs == var:
            count = int(mod.group(2))
            if count > 0:
                values = _cycle_values(0, count - 1)
            continue

        if not _LITERAL_RE.fullmatch(rhs):
            continue
        # `if $v < 2 / $v = $v + 1 / else / $v = 0 / endif`. Checked before the
        # trailing-`if` idiom below, which the negated else guard also matches.
        if in_wrap_else and lhs == var and wrap[0]["var"] == var:
            hi = int(wrap[0]["value"]) + (1 if wrap[0]["op"] == "<=" else 0)
            lo = int(rhs)
            if hi >= lo:
                values = _cycle_values(lo, hi)
            continue
        # `if $v > 2 / $v = 0 / endif` closes the cycle opened by `$v = $v + 1`.
        if (guard and lhs == var and guard["var"] == var
                and guard["op"] in (">", ">=")):
            hi = int(guard["value"]) - (1 if guard["op"] == ">=" else 0)
            lo = int(rhs)
            if hi >= lo:
                values = _cycle_values(lo, hi)
            continue
        effects.append({"when": guard, "var": lhs, "value": rhs})

    if var is None:
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


def extract_menu_toggles(sections, var_prefix=None, source=None,
                         canonical_vars=None):
    """Return {entry key: {name, slot, var, values, effects, source, ini_path,
    section}} for every clickable menu slot found in the mod's CommandLists.

    The ini carries no human-readable label for a slot (its on-screen caption
    is a .dds image), so the variable name doubles as the display name.
    """
    menu = {}
    canon = (canonical_vars if canonical_vars is not None
             else canonical_var_names(sections))

    def declared(name):
        return canon.get(name.lower(), name)

    for name, lines in sections.items():
        # 3DMigoto section names are case-insensitive. Preserve the original
        # spelling in the payload, but never skip a lowercase/mixed-case
        # CommandList section during discovery.
        if not name.lower().startswith("commandlist"):
            continue
        parsed = []
        for _slot_var, slot_value, body in _split_slot_branches(lines):
            info = _parse_branch(body)
            if info:
                parsed.append((slot_value, info))
        if len(parsed) < _MIN_SLOTS:
            continue

        src = first_source(lines) or {}
        for slot_value, (var, values, effects) in parsed:
            var = declared(var)
            base_key = _prefixed(f"{name}#{slot_value}", var_prefix)
            key = base_key
            suffix = 2
            while key in menu:
                key = f"{base_key}_{suffix}"
                suffix += 1
            menu[key] = {
                "name": var,
                "slot": int(slot_value),
                "var": _prefixed(var, var_prefix),
                "values": values,
                "effects": [
                    {
                        "when": (None if e["when"] is None else
                                 {**e["when"],
                                  "var": _prefixed(declared(e["when"]["var"]), var_prefix)}),
                        "var": _prefixed(declared(e["var"]), var_prefix),
                        "value": e["value"],
                    }
                    for e in effects
                ],
                "source": source,
                "ini_path": src.get("ini_path"),
                "section": name,
            }

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
            variable = declared(variable)
            button_section = re.sub(r"(?:Left|Right)$", "", section,
                                    flags=re.I)
            base_key = _prefixed(f"{button_section}#{slot}", var_prefix)
            key = base_key
            suffix = 2
            while key in menu:
                key = f"{base_key}_{suffix}"
                suffix += 1
            menu[key] = {
                "name": variable,
                "slot": slot,
                "var": _prefixed(variable, var_prefix),
                "values": values,
                "effects": [],
                "source": source,
                "ini_path": src.get("ini_path"),
                "section": section,
            }
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


def attach_menu_images(menu, sections, resources):
    """Attach authored menu-item filenames to recognized slots/sliders."""
    slot_images = {}

    def resource(name):
        if not name:
            return {}
        lookup = getattr(resources, "get_ci", None)
        if lookup is not None:
            return lookup(name)
        lowered = name.lower()
        return next((info for key, info in resources.items()
                     if key.lower() == lowered), {})

    # Arrow-pair menus render item N in its own CommandListIconN section.
    for name, lines in sections.items():
        match = re.fullmatch(r"CommandListIcon(\d+)", name, re.I)
        if not match:
            continue
        slot = int(match.group(1))
        for raw in lines:
            line = str(raw).split(";", 1)[0].strip()
            icon = re.match(r"ps-t100\s*=\s*(\S+)", line, re.I)
            if not icon:
                continue
            info = resource(icon.group(1))
            if info.get("filename"):
                slot_images.setdefault(slot, info["filename"])
                break

    for name, lines in sections.items():
        if "slotitemimage" not in name.lower():
            continue
        current_slot = None
        for raw in lines:
            line = str(raw).split(";", 1)[0].strip()
            match = re.match(r"(?:if|elif|else\s+if)\s+\$slot\s*==\s*(\d+)", line, re.I)
            if match:
                current_slot = int(match.group(1))
                continue
            match = re.match(r"ps-t100\s*=\s*(\S+)", line, re.I)
            if match and current_slot is not None:
                info = resource(match.group(1))
                if info.get("filename") and current_slot not in slot_images:
                    slot_images[current_slot] = info["filename"]

    # Responsive/MCMI grids draw their buttons by incrementing a counter and
    # dispatching the icon in a separate CommandList.  Unlike the older
    # `$slot` convention, the counter name is author-defined (commonly
    # `$Button_number`) and many slots may intentionally share one frame/icon.
    # Only accept integer-dispatch CommandLists that actually bind ps-t100;
    # this keeps ordinary state chains out of image recognition.
    for name, lines in sections.items():
        if not name.lower().startswith("commandlist"):
            continue
        current_slot = None
        saw_icon = False
        candidates = {}
        for raw in lines:
            line = str(raw).split(";", 1)[0].strip()
            match = re.match(
                r"(?:if|elif|else\s+if)\s+\$\w+\s*==\s*(\d+)", line, re.I)
            if match:
                current_slot = int(match.group(1))
                continue
            match = re.match(r"ps-t100\s*=\s*(\S+)", line, re.I)
            if match and current_slot is not None:
                info = resource(match.group(1))
                if info.get("filename"):
                    candidates.setdefault(current_slot, info["filename"])
                    saw_icon = True
        if saw_icon and len(candidates) >= _MIN_SLOTS:
            for slot, filename in candidates.items():
                slot_images.setdefault(slot, filename)

    def compact(text):
        return re.sub(r"[^a-z0-9]", "", text.lower())

    image_resources = [(compact(name.replace("Resource", "")), info["filename"])
                       for name, info in resources.items()
                       if info.get("filename") and
                       ("menuitem" in name.lower() or "resourceitem" in name.lower())]
    aliases = {
        "currflat": ("menuflat", "itemflat", "flat"),
        "boobssize": ("itemboobs", "boobs"),
        "nipplesize": ("itemnipple", "nipple"),
        "shortclo": ("itemshort", "short"),
        "pussy": ("itempussy", "pussy"),
    }
    for info in menu.values():
        if info.get("slot") in slot_images:
            info["image_file"] = slot_images[info["slot"]]
            continue
        if info.get("kind") != "shape_slider":
            continue
        var = compact(info["name"])
        needles = list(aliases.get(var, ()))
        needles += [var, var.replace("swapvarslider", ""), var.replace("size", "")]
        for resource_name, filename in image_resources:
            if any(needle and needle in resource_name for needle in needles):
                info["image_file"] = filename
                break

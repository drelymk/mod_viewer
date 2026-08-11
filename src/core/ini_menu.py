"""Discovery of in-game *clickable menu* toggles.

The chain is recognised structurally (an `if $X == <int>` / `elif` chain whose
branches assign back to themselves) rather than by section name, since only the
layout is a convention — `$clickedSlot` is not.
"""

import re

from .ini_sections import canonical_var_names, first_source

# A branch head that dispatches on an integer slot: `$clickedSlot == 3`.
_SLOT_RE = re.compile(r'\$(\w+)\s*={2,3}\s*(\d+)$')

_ASSIGN_RE  = re.compile(r'^\$(\w+)\s*=\s*(.+)$')
_FLIP_RE    = re.compile(r'^1\s*-\s*\$(\w+)$')       # $v = 1 - $v
_INCR_RE    = re.compile(r'^\$(\w+)\s*\+\s*1$')      # $v = $v + 1
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
        incr = _INCR_RE.fullmatch(rhs)
        if incr and incr.group(1) == lhs:
            var, values = lhs, ["0", "1"]   # replaced below once the wrap is seen
            if guard and guard["var"] == lhs and guard["op"] in ("<", "<="):
                wrap = (guard, len(stack))
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


def extract_menu_toggles(sections, var_prefix=None, source=None):
    """Return {entry key: {name, slot, var, values, effects, source, ini_path,
    section}} for every clickable menu slot found in the mod's CommandLists.

    The ini carries no human-readable label for a slot (its on-screen caption
    is a .dds image), so the variable name doubles as the display name.
    """
    menu = {}
    canon = canonical_var_names(sections)

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
    return menu


def extract_menu_var_names(sections, var_prefix=None):
    """Flat set of every variable a clickable menu can change — the cycled
    variables plus the ones their mutual-exclusion rules write."""
    found = set()
    for info in extract_menu_toggles(sections, var_prefix=var_prefix).values():
        found.add(info["var"])
        found.update(e["var"] for e in info["effects"])
    return found

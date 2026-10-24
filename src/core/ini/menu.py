"""Discovery of in-game *clickable menu* toggles.

The chain is recognised structurally (an `if $X == <int>` / `elif` chain whose
branches assign back to themselves) rather than by section name, since only the
layout is a convention — `$clickedSlot` is not.
"""

import re

from . import condition
from .sections import ResourceTable, canonical_var_names, first_source, line_source

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
_RUN_RE = re.compile(r'run\s*=\s*(CommandList\w+)$', re.I)
_RECT_BINDING_RE = re.compile(r'([xyzw]87)\s*=\s*(.*)', re.I)

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
            if len(parts) >= _MIN_SLOTS:
                found.append(parts)
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
    return resources.get_ci(name).get("filename")


def _branch_image(nodes, resources):
    bindings = [_IMAGE_BINDING_RE.fullmatch(line)
                for line in nodes if isinstance(line, str)]
    names = [match.group(1) for match in bindings if match]
    return _resource_file(resources, names[0]) if len(names) == 1 else None


def _variable_images(blocks, resources, variables, region_vars=None, *, regions=None):
    """Resolve first artwork through shared variable and hit-region evidence."""
    if resources is None or not variables:
        return {}
    roots = [parsed[1] for parsed in blocks if parsed is not None]
    regions = {var: set(values) for var, values in (regions or {}).items()}
    images = {}
    for root in roots:
        for _block, branch, _guard_node, _axes, _count, region in _walk_branches(root):
            if region:
                for node in branch["body"]:
                    assignment = _ASSIGN_RE.fullmatch(node) if isinstance(node, str) else None
                    if assignment:
                        regions.setdefault(assignment.group(1).casefold(), set()).add(region)
    for variable, pulse in (region_vars or {}).items():
        if pulse in regions:
            regions.setdefault(variable, set()).update(regions[pulse])

    def rectangle_region(state):
        return tuple(frozenset(state.get(register) for register in pair)
                     for pair in (("x87", "z87"), ("y87", "w87")))

    def publish(variable, names, region):
        if variable not in variables or variable in images:
            return
        if variable in regions and region not in regions[variable]:
            return
        for name in names:
            image = _resource_file(resources, name)
            if image:
                images[variable] = image
                break

    def collect(nodes, state, variable=None):
        for node in nodes:
            if isinstance(node, str):
                rect = _RECT_BINDING_RE.fullmatch(node)
                binding = _IMAGE_BINDING_RE.fullmatch(node)
                if rect:
                    register, value = rect.groups()
                    # Pressed-button displacement retains the authored rectangle.
                    name = re.fullmatch(r'\$(\w+)(?:\s*[+-]\s*\d+(?:\.\d+)?)?', value.strip())
                    state[register.casefold()] = name.group(1).casefold() if name else None
                elif binding:
                    state["images"] = (binding.group(1),)
                    publish(variable, state["images"], rectangle_region(state))
                elif re.fullmatch(r'run\s*=\s*CustomShader\w+', node, re.I):
                    region = rectangle_region(state)
                    for target in variables:
                        if region in regions.get(target, ()):
                            publish(target, state.get("images", ()), region)
                continue
            branches = node["branches"]
            try:
                names = {name.casefold() for branch in branches
                         if branch["condition"] is not None
                         for name in condition.parse(branch["condition"]).variables()}
            except condition.ConditionError:
                names = set()
            branch_variable = next(iter(names)) if len(names) == 1 else None
            alternatives = [collect(branch["body"], dict(state), branch_variable)
                            for branch in branches]
            if branches[-1]["condition"] is not None:
                alternatives.append(dict(state))
            for register in set().union(*(part.keys() for part in alternatives)):
                values = [part.get(register) for part in alternatives]
                if register == "images":
                    state[register] = tuple(dict.fromkeys(
                        name for value in values for name in (value or ())))
                else:
                    state[register] = values[0] if all(value == values[0] for value in values) else None
        return state

    for root in roots:
        collect(root, {})
    return images


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
    if not known_slots:
        return {}
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


def _literal_cycle(block):
    branches = block["branches"]
    if len(branches) < 2 or branches[-1]["condition"] is not None:
        return None
    variable, transitions, outputs = None, {}, []
    for branch in branches:
        body = [node for node in branch["body"] if node != ""]
        assignment = _ASSIGN_RE.fullmatch(body[0]) if len(body) == 1 and isinstance(body[0], str) else None
        if not assignment:
            return None
        name, value = assignment.groups()
        value = _integer_value(value)
        variable = variable or name
        if value is None or not _same_var(name, variable):
            return None
        outputs.append(str(value))
        if branch["condition"] is not None:
            guard = _guard(branch["condition"])
            if not guard or guard["op"] != "==" or not _same_var(guard["var"], variable):
                return None
            state = _integer_value(guard["value"])
            if state is None or str(state) in transitions:
                return None
            transitions[str(state)] = outputs[-1]
    missing = set(outputs) - transitions.keys()
    if len(missing) != 1 or len(set(outputs)) != len(branches):
        return None
    transitions[missing.pop()] = outputs[-1]
    values, current = [], min(transitions, key=int)
    while current not in values and current in transitions:
        values.append(current)
        current = transitions[current]
    if len(values) != len(transitions) or current != values[0]:
        return None
    return variable, values


def _parse_branch(body, numeric_defaults=None, require_finite=False, *, nodes=None,
                  strict=False):
    """Return (var, values, effects) for one slot, or None if it cycles nothing.

    `effects` are the branch's other assignments — the mutual-exclusion rules a
    real click also applies (`if $bikinitop == 0 then $nipplepasties = 1`) —
    as [{when: {var, op, value} | None, var, value}] in source order.
    """
    if nodes is None:
        parsed = _conditional_blocks(body)
        nodes = parsed[1] if parsed is not None else []
    meaningful = [node for node in nodes if node != ""]
    if len(meaningful) == 1 and isinstance(meaningful[0], dict):
        literal = _literal_cycle(meaningful[0])
        if literal:
            return *literal, []
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
            if strict and line:
                return None
            continue
        lhs, rhs = m.group(1), m.group(2).strip()
        guard = stack[-1]["guard"] if stack else None

        flip = _FLIP_RE.fullmatch(rhs)
        if flip and _same_var(flip.group(1), lhs):
            if strict and var is not None:
                return None
            var, values = lhs, ["0", "1"]
            cycle_kind = "flip"
            finite = True
            continue
        incr_mod = _INCR_MOD_RE.fullmatch(rhs)
        if incr_mod and _same_var(incr_mod.group(1), lhs):
            if strict and var is not None:
                return None
            count = int(incr_mod.group(2))
            if count > 0:
                var, values = lhs, _cycle_values(0, count - 1)
                cycle_kind = "increment_mod"
                finite = True
            continue
        incr = (_INCR_RE.fullmatch(rhs) or _INCR_REV_RE.fullmatch(rhs))
        if incr and _same_var(incr.group(1), lhs):
            if strict and var is not None:
                return None
            var, values = lhs, ["0", "1"]   # Placeholder until the wrap count is known.
            cycle_kind = "increment"
            finite = False
            step_body = tuple(frame["body"] for frame in stack)
            if guard and _same_var(guard["var"], lhs) and guard["op"] in ("<", "<="):
                wrap = (guard, len(stack))
            continue
        mod = _MOD_RE.fullmatch(rhs)
        if mod and _same_var(mod.group(1), lhs) and _same_var(lhs, var):
            if strict and (cycle_kind != "increment" or finite):
                return None
            count = int(mod.group(2))
            if count > 0:
                values = _cycle_values(0, count - 1)
                finite = True
            continue

        if not _LITERAL_RE.fullmatch(rhs):
            if strict:
                return None
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


def _body_lines(nodes):
    for node in nodes:
        if isinstance(node, str):
            yield node
            continue
        for index, branch in enumerate(node["branches"]):
            test = branch["condition"]
            yield (f"{'if' if index == 0 else 'elif'} {test}" if test else "else")
            yield from _body_lines(branch["body"])
        yield "endif"


def _without_shader_refreshes(nodes):
    """Copy action nodes without shader refreshes at any conditional depth."""
    result = []
    for node in nodes:
        if isinstance(node, str):
            if re.fullmatch(r'run\s*=\s*CustomShader\w+', node, re.I):
                continue
        else:
            node = {**node, "branches": [
                {**branch, "body": _without_shader_refreshes(branch["body"])}
                for branch in node["branches"]]}
        result.append(node)
    return result


def _resolve_actions(nodes, blocks, defaults, *, allow_effects=False,
                     require_finite=True, active=(), budget=None,
                     helper_context=False):
    """Resolve finite actions, following only bounded, pure local helper calls."""
    budget = [128] if budget is None else budget
    budget[0] -= 1
    if len(active) >= 16 or budget[0] < 0:
        return None
    nodes = _without_shader_refreshes(nodes)
    lines = [line for line in _body_lines(nodes) if line]
    assignments = [_ASSIGN_RE.fullmatch(line) for line in lines]
    writers = {match.group(1).casefold() for match in assignments if match}
    cycle_vars = {match.group(1).casefold() for match in assignments if match
                  and re.search(rf'\${re.escape(match.group(1))}\b', match.group(2), re.I)}
    cycle_vars.update(cycle[0].casefold()
                      for body in _conditional_bodies(nodes) for node in body
                      if isinstance(node, dict) and (cycle := _literal_cycle(node)))
    syntax = all(match or line.lower().startswith(("if ", "elif ", "else if "))
                 or line.lower() in ("else", "endif")
                 for line, match in zip(lines, assignments))
    meaningful = [node for node in nodes if node != ""]
    wrapped = (len(meaningful) == 1 and isinstance(meaningful[0], dict)
               and len(meaningful[0]["branches"]) == 1)
    if syntax and not wrapped and len(cycle_vars) <= 1 and (allow_effects or len(writers) == 1):
        action = _parse_branch(lines, defaults, require_finite=require_finite, nodes=nodes, strict=True)
        if action and (allow_effects or not action[2]):
            return [action]
    result = []
    groups = []
    for node in nodes:
        if node == "":
            continue
        names = {match.group(1).casefold() for line in _body_lines([node])
                 if (match := _ASSIGN_RE.fullmatch(line))}
        if len(names) == 1 and groups and groups[-1][0] == names:
            groups[-1][1].append(node)
        else:
            groups.append((names, [node]))
    for names, group in groups:
        wrapped = (len(group) == 1 and isinstance(group[0], dict)
                   and len(group[0]["branches"]) == 1)
        action = (_parse_branch(list(_body_lines(group)), defaults, nodes=group,
                               require_finite=require_finite, strict=True)
                  if len(names) == 1 and not wrapped else None)
        if action and not action[2]:
            result.append(action)
            continue
        if len(group) != 1:
            return None
        node = group[0]
        if isinstance(node, str):
            call = _RUN_RE.fullmatch(node)
            target = call.group(1).casefold() if call else None
            if target in active or blocks.get(target) is None:
                return None
            actions = _resolve_actions(blocks[target][1], blocks, defaults,
                                       active=(*active, target), budget=budget,
                                       helper_context=True)
        else:
            literal = _literal_cycle(node)
            if literal:
                actions = [(*literal, [])]
            elif len(node["branches"]) == 1:
                branch = node["branches"][0]
                try:
                    guard = condition.parse(branch["condition"])
                except condition.ConditionError:
                    return None
                actions = _resolve_actions(branch["body"], blocks, defaults,
                                           allow_effects=allow_effects,
                                           require_finite=require_finite,
                                           active=active, budget=budget,
                                           helper_context=helper_context)
                names = {name.casefold() for name in guard.variables()}
                guard_ok = (_satisfied(guard, defaults) if helper_context
                            else not _inactive(guard, defaults))
                if (not guard_ok
                        or actions and any(var.casefold() in names for var, _values, _effects in actions)):
                    return None
            else:
                return None
        if not actions:
            return None
        result.extend(actions)
    # Independent cycles may share one button; repeated writes and interleaved
    # effects require a richer execution model and must remain unresolved.
    if (result and len({var.casefold() for var, _values, _effects in result}) == len(result)
            and (len(result) == 1 or not any(effects for _var, _values, effects in result))):
        return result
    return None


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
    """Recognise numbered arrow menus, retaining distinct side actions."""
    items = {}
    for section, lines in sections.items():
        match = _BUTTON_RE.fullmatch(section)
        if not match:
            continue
        action = _parse_arrow_button(lines)
        if action:
            slot = int(match.group(1))
            identity = (slot, action[0].casefold(), tuple(action[1]))
            items.setdefault(identity, (section, *action, first_source(lines) or {}))
    # A lone step button is more likely ordinary bookkeeping than a menu.
    if len({identity[0] for identity in items}) < _MIN_SLOTS:
        return []
    return [(identity[0], *item) for identity, item in sorted(
        items.items(), key=lambda item: item[0][0])]


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


def _cursor_facts(node):
    """Collect conjunctive cursor evidence; OR cannot prove a hit region."""
    axes, count, sides = {"x": set(), "y": set()}, 0, set()
    if isinstance(node, condition.Paren):
        return _cursor_facts(node.inner)
    if isinstance(node, condition.And):
        for child in node.parts:
            bounds, size, directions = _cursor_facts(child)
            count += size
            sides.update(directions)
            for axis in axes:
                axes[axis].update(bounds[axis])
    elif isinstance(node, condition.Cmp) and node.op in ("<", "<=", ">", ">="):
        for axis in axes:
            if re.search(rf'\bcursor_{axis}\b', node.render(), re.I):
                axes[axis].update(name.casefold() for name in node.variables())
                count += 1
                if node.left.render().casefold() == f"cursor_{axis}":
                    sides.add((axis, node.op[0]))
                elif node.right.render().casefold() == f"cursor_{axis}":
                    sides.add((axis, "<" if node.op[0] == ">" else ">"))
    return axes, count, sides


def _region(axes):
    return tuple(frozenset(axes[axis]) for axis in ("x", "y"))


def _walk_branches(nodes, guards=(), axes=None, count=0, sides=frozenset()):
    """Walk the shared condition tree with ancestors and sibling exclusions."""
    axes = axes or {"x": set(), "y": set()}
    for block in nodes:
        if not isinstance(block, dict):
            continue
        exclusions = []
        for branch in block["branches"]:
            try:
                test = condition.parse(branch["condition"]) if branch["condition"] else None
            except condition.ConditionError:
                break
            bounds, size, directions = _cursor_facts(test)
            combined = {axis: axes[axis] | bounds[axis] for axis in axes}
            combined_sides = sides | directions
            inherited = guards + tuple(exclusions) + ((test,) if test else ())
            region = (_region(combined) if len(combined_sides) == 4
                      and all(len(names) == 2 for names in combined.values()) else None)
            yield block, branch, condition.And(list(inherited)), combined, count + size, region
            yield from _walk_branches(branch["body"], inherited, combined, count + size, combined_sides)
            if test:
                exclusions.append(condition.Not(test))


def _requires(guard, name, value):
    return (condition.reduce(guard, {name: "0"}) == condition.FALSE
            and condition.reduce(guard, {name: value}) != condition.FALSE)


def _inactive(guard, defaults):
    return condition.reduce(guard, {
        name: defaults[name.casefold()] for name in guard.variables()
        if name.casefold() in defaults}) == condition.FALSE


def _satisfied(guard, defaults):
    """Return whether a guard is provably true in the modeled context."""
    return condition.reduce(guard, {
        name: defaults[name.casefold()] for name in guard.variables()
        if name.casefold() in defaults}) == condition.TRUE


def _mouse_button_items(sections, blocks, defaults, pressed):
    """Discover inline and helper actions through the same mouse hit regions."""
    if not pressed:
        return []
    branches = [(section, *record) for section, parsed in blocks.items()
                if parsed is not None for record in _walk_branches(parsed[1])]
    latches = {}
    for _section, _block, branch, guard, _axes, _count, region in branches:
        if (region is None
                or not any(_requires(guard, name, pressed[name.casefold()])
                           for name in guard.variables() if name.casefold() in pressed)):
            continue
        for line in branch["body"]:
            match = _ASSIGN_RE.fullmatch(line) if isinstance(line, str) else None
            if match and _integer_value(match.group(2)) == 1:
                latches.setdefault(match.group(1).casefold(), set()).add(region)

    found, ordinal, consumed = [], 0, {}
    lookup = {name.casefold(): parsed for name, parsed in blocks.items()}
    for section, _block, branch, guard, _axes, count, region in branches:
        if branch["start"] < consumed.get(section, -1) or count < 2:
            continue
        activated = any(
            _requires(guard, name, pressed[name.casefold()]) if name.casefold() in pressed else
            region in latches.get(name.casefold(), ()) and _requires(guard, name, "1")
            for name in guard.variables())
        if not activated:
            continue
        actions = _resolve_actions(branch["body"], lookup, defaults, allow_effects=True)
        if not actions:
            continue
        consumed[section] = branch["end"]
        slot, ordinal = ordinal, ordinal + 1
        if not _inactive(guard, defaults):
            src = line_source(sections[section][branch["start"] - 1]) or first_source(sections[section]) or {}
            found.append((slot, section, actions, src, region))
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
    resources = ResourceTable.normalize(resources)
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
    flips, blocks = {}, []
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
        blocks.append(_conditional_blocks(lines))
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
    variables = {name.casefold() for name in found}
    variables.update(info["_pulse_var"].casefold() for info in found.values()
                     if "_pulse_var" in info)
    region_vars = {name.casefold(): info["_pulse_var"].casefold()
                   for name, info in found.items() if "_pulse_var" in info}
    images = _variable_images(blocks, resources, variables, region_vars)
    for local, info in found.items():
        image = (images.get(info.get("_pulse_var", local).casefold())
                 or images.get(local.casefold()))
        if image:
            info["image_file"] = image
    return found


def extract_menu_toggles(sections, var_prefix=None, source=None,
                         canonical_vars=None, resources=None):
    """Return {entry key: {name, slot, var, values, effects, source, ini_path,
    section}} for every clickable menu slot found in the mod's CommandLists.

    The ini carries no human-readable label for a slot (its on-screen caption
    is a .dds image), so the variable name doubles as the display name.
    """
    resources = ResourceTable.normalize(resources)
    menu = {}
    canon = (canonical_vars if canonical_vars is not None
             else canonical_var_names(sections))
    blocks = {name: _conditional_blocks(lines) for name, lines in sections.items()
              if name.casefold().startswith("commandlist") or name.casefold() == "present"}
    section_lookup = {name.casefold(): lines for name, lines in sections.items()}
    image_chains = []
    slot_groups = []
    missing_images = []

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
        else:
            missing_images.append((variable.casefold(), entry))
        menu[key] = entry
        return entry

    def add_actions(section, slot, actions, **options):
        variable, values, effects = actions[0]
        entry = add_entry(section, slot, variable, values, effects, **options)
        if len(actions) > 1:
            entry["cycles"] = [{"var": _prefixed(declared(var), var_prefix),
                                "values": vals} for var, vals, _effects in actions]
        return entry

    defaults, pressed = _static_numeric_defaults(sections), _mouse_press_vars(sections)
    lookup = {name.casefold(): parsed for name, parsed in blocks.items()}
    for name, parsed_blocks in blocks.items():
        if name.casefold() == "present":
            continue
        for chain in _slot_chains(
                parsed_blocks, image_chains if resources is not None else None):
            parsed = []
            for _slot_var, slot_value, _body, nodes in chain:
                actions = _resolve_actions(nodes, lookup, defaults, allow_effects=True,
                                           require_finite=False)
                if actions:
                    parsed.append((slot_value, actions))
            if len(parsed) < _MIN_SLOTS:
                continue

            slot_groups.append({int(slot) for slot, _info in parsed})
            src = first_source(sections[name]) or {}
            for slot_value, actions in parsed:
                add_actions(name, slot_value, actions, src=src)

    mouse_items = _mouse_button_items(sections, blocks, defaults, pressed)
    mouse_arrows = {}
    for index, item in enumerate(mouse_items):
        _slot, section, actions, _src, _region_value = item
        match = _BUTTON_RE.fullmatch(section)
        if match:
            variable, values, _effects = actions[0]
            identity = (int(match.group(1)), variable.casefold(), tuple(values))
            mouse_arrows.setdefault(identity, []).append((index, item))
    consumed_mouse = set()
    for slot, section, variable, values, src in _arrow_button_items(sections):
        button_section = re.sub(r"(?:Left|Right)$", "", section, flags=re.I)
        candidates = mouse_arrows.get((slot, variable.casefold(), tuple(values)), [])
        if candidates:
            _index, (_slot, section, actions, src, _region_value) = candidates[0]
            # Reuse the richer mouse actions without duplicating the same cycle.
            # Different side effects remain independent menu entries.
            consumed_mouse.update(index for index, item in candidates
                                  if item[2] == actions)
            add_actions(section, slot, actions, src=src, key_section=button_section)
        else:
            add_entry(section, slot, variable, values, src=src,
                      key_section=button_section)
    regions = {}
    for index, (slot, section, actions, src, region) in enumerate(mouse_items):
        if region:
            regions.setdefault(actions[0][0].casefold(), set()).add(region)
        if index in consumed_mouse:
            continue
        image = _section_image(
            section_lookup.get(f"commandlistdrawbutton_{slot}", ()), resources, last=True)
        add_actions(section, slot, actions, src=src, kind="mouse_region", image=image)
    images = _slot_images(image_chains, {info["slot"] for info in menu.values()},
                          slot_groups, resources)
    for info in menu.values():
        if info.get("kind") != "mouse_region" and info["slot"] in images:
            info["image_file"] = images[info["slot"]]
    missing_images = [(variable, info) for variable, info in missing_images
                      if "image_file" not in info]
    variable_images = _variable_images(
        blocks.values(), resources, {variable for variable, _info in missing_images},
        regions=regions)
    for variable, info in missing_images:
        if variable in variable_images:
            info["image_file"] = variable_images[variable]
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
        found.update(cycle["var"] for cycle in info.get("cycles", ()))
        found.update(e["var"] for e in info["effects"])
    return found

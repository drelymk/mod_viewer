"""Bounded, read-only data flow for control inputs and menu artwork.

Unknown runtime values stay unknown. Branches join finite value sets, local
calls retain caller state, and recursion or exhausted budgets discard evidence.
This is discovery evidence, never a replacement for the draw execution scan.
"""

import itertools
import math
import re
from dataclasses import dataclass

from . import condition


_ASSIGN = re.compile(r"^(\$\w+|ps-t\d+|Resource[^\s=]+)\s*=\s*(.*)$", re.I)
_RUN = re.compile(r"^run\s*=\s*(.+?)\s*$", re.I)
_RESOURCE = re.compile(r"^(?:ref\s+)?(Resource[^\s=]+)$", re.I)
_REFERENCE = re.compile(r"^(?:ref\s+)?(ps-t\d+|Resource[^\s=]+)$", re.I)
_LIMIT = 16


@dataclass(frozen=True)
class _ImageBinding:
    resource: str
    occurrence: int


def _domain(values):
    values = frozenset(values)
    if len(values) > _LIMIT or any(
        isinstance(value, (int, float))
        and (not math.isfinite(value) or abs(value) > 1e12)
        for value in values
    ):
        return None
    return values or None


def _combine(left, right, operation):
    if left is None or right is None or len(left) * len(right) > _LIMIT * _LIMIT:
        return None
    try:
        return _domain(operation(a, b) for a, b in itertools.product(left, right))
    except (ArithmeticError, TypeError, ValueError):
        return None


def numeric_value(node, state):
    """Evaluate the shared expression AST over finite numeric input sets."""
    if isinstance(node, condition.Paren):
        return numeric_value(node.inner, state)
    if isinstance(node, condition.Operand):
        if node.is_var:
            return state.get("$" + node.var.casefold())
        try:
            return _domain([float(node.text)])
        except ValueError:
            return None
    if isinstance(node, (condition.Arith, condition.Cmp)):
        operations = {
            "+": lambda a, b: a + b,
            "-": lambda a, b: a - b,
            "*": lambda a, b: a * b,
            "/": lambda a, b: a / b,
            "//": lambda a, b: a // b,
            "%": lambda a, b: a % b,
            "==": lambda a, b: a == b,
            "===": lambda a, b: a == b,
            "!=": lambda a, b: a != b,
            "!==": lambda a, b: a != b,
            "<": lambda a, b: a < b,
            "<=": lambda a, b: a <= b,
            ">": lambda a, b: a > b,
            ">=": lambda a, b: a >= b,
        }
        return _combine(
            numeric_value(node.left, state),
            numeric_value(node.right, state),
            operations[node.op],
        )
    if isinstance(node, condition.Not):
        values = numeric_value(node.operand, state)
        return _domain(not value for value in values) if values is not None else None
    if isinstance(node, (condition.And, condition.Or)):
        values = [numeric_value(part, state) for part in node.parts]
        decisive = False if isinstance(node, condition.And) else True
        if any(
            value is not None and {bool(item) for item in value} == {decisive}
            for value in values
        ):
            return frozenset([decisive])
        if any(value is None for value in values):
            return None
        result = frozenset([not decisive])
        for value in values:
            result = _combine(
                result,
                value,
                lambda a, b: (
                    bool(a and b) if isinstance(node, condition.And) else bool(a or b)
                ),
            )
        return result
    return None


def _join(states):
    return {
        key: (
            _domain(value for state in states for value in state[key])
            if all(state.get(key) is not None for state in states)
            else None
        )
        for key in set().union(*(state.keys() for state in states))
    }


class ControlFlow:
    """One per-INI analysis shared by action and artwork discovery."""

    def __init__(self, blocks, constants, selectors=(), renderers=()):
        self.blocks = {
            name.casefold(): parsed[1]
            for name, parsed in blocks.items()
            if parsed is not None
        }
        self.selectors = {name.casefold() for name in selectors}
        self.renderers = {name.casefold() for name in renderers}
        self.steps = {}
        self.artwork = {}
        self.complete = True
        self._expressions = {}
        self._binding_id = 0
        self._branch_id = 0
        self._identity_inputs = {
            "$" + variable.casefold()
            for nodes in self.blocks.values()
            for line in self._lines(nodes)
            if (match := _ASSIGN.fullmatch(line))
            and match.group(1)[1:].casefold() in self.selectors
            for variable in re.findall(r"\$(\w+)", match.group(2))
        }
        called = {
            match.group(1).casefold()
            for nodes in self.blocks.values()
            for line in self._lines(nodes)
            if (match := _RUN.fullmatch(line))
        }
        roots = [name for name in self.blocks if name not in called]
        initial = {
            "$" + name.casefold(): frozenset([float(value)])
            for name, value in constants.items()
        }
        for name in roots:
            self._budget = 16384
            self._calls = 1024
            previous_steps = dict(self.steps)
            previous_artwork = {
                key: set(values) for key, values in self.artwork.items()
            }
            try:
                self._call(name, dict(initial), (), [], ())
            except (ValueError, RecursionError):
                self.complete = False
                self.steps = previous_steps
                self.artwork = previous_artwork

    @staticmethod
    def _lines(nodes):
        for node in nodes:
            if isinstance(node, str):
                yield node
            else:
                for branch in node["branches"]:
                    yield from ControlFlow._lines(branch["body"])

    def expression(self, text):
        if text not in self._expressions:
            try:
                self._expressions[text] = (
                    condition.parse(text) if len(text) <= 2048 else None
                )
            except (condition.ConditionError, RecursionError):
                self._expressions[text] = None
        return self._expressions[text]

    def _step(self, expression, variable, state):
        """Prove an affine self-step; never substitute a mutable default."""

        def affine(node):
            if isinstance(node, condition.Paren):
                return affine(node.inner)
            if isinstance(node, condition.Operand):
                if node.is_var and node.var.casefold() == variable:
                    return 1, frozenset([0])
                return 0, numeric_value(node, state)
            if not isinstance(node, condition.Arith):
                return None, None
            a, b = affine(node.left), affine(node.right)
            if a[0] is None or b[0] is None:
                return None, None
            if node.op in ("+", "-"):
                sign = 1 if node.op == "+" else -1
                return a[0] + sign * b[0], _combine(
                    a[1], b[1], lambda x, y: x + sign * y
                )
            if node.op == "*" and (a[0] == 0 or b[0] == 0):
                scalar, other = (a, b) if a[0] == 0 else (b, a)
                if scalar[1] is not None and len(scalar[1]) == 1:
                    return other[0] * next(iter(scalar[1])), _combine(
                        a[1], b[1], lambda x, y: x * y
                    )
            if node.op == "/" and b[0] == 0 and b[1] is not None and len(b[1]) == 1:
                divisor = next(iter(b[1]))
                if divisor:
                    return a[0] / divisor, _combine(a[1], b[1], lambda x, y: x / y)
            return None, None

        coefficient, delta = affine(expression)
        return delta if coefficient == 1 and delta and delta <= {-1, 1} else None

    def step(self, section, variable, rhs):
        return self.steps.get((section.casefold(), variable.casefold(), rhs))

    def _call(self, section, state, active, parents, path):
        self._calls -= 1
        if section in active or len(active) >= 16 or self._calls < 0:
            raise ValueError("control call budget exhausted")
        if section not in self.blocks:
            # An unresolved call may change any value used by a later control.
            return dict.fromkeys(state)
        frame = {"hits": set(), "renders": {}}
        result = self._walk(
            self.blocks[section], state, (*active, section), [*parents, frame], path
        )
        for selector, slot, image, inputs, hit_path in frame["hits"]:
            # One event must be unconditional relative to the other. Separate
            # unknown branch choices cannot prove that both events can occur.
            if any(
                inputs <= rendered
                and (
                    hit_path[: len(render_path)] == render_path
                    or render_path[: len(hit_path)] == hit_path
                )
                for rendered, render_path in frame["renders"].get(image, ())
            ):
                self.artwork.setdefault((selector, slot), set()).add(image.resource)
        return result

    def _walk(self, nodes, state, active, frames, path):
        for node in nodes:
            self._budget -= 1
            if self._budget < 0:
                raise ValueError("control statement budget exhausted")
            if not isinstance(node, str):
                alternatives, remaining = [], True
                self._branch_id += 1
                branch_id, forked = self._branch_id, False
                for index, branch in enumerate(node["branches"]):
                    test = (
                        numeric_value(self.expression(branch["condition"]), state)
                        if branch["condition"] is not None
                        else frozenset([True])
                    )
                    truth = (
                        {bool(value) for value in test}
                        if test is not None
                        else {False, True}
                    )
                    forked = forked or len(truth) > 1
                    if True in truth:
                        alternatives.append(
                            self._walk(
                                branch["body"],
                                dict(state),
                                active,
                                frames,
                                (*path, (branch_id, index)) if forked else path,
                            )
                        )
                    if False not in truth:
                        remaining = False
                        break
                if remaining:
                    alternatives.append(dict(state))
                state = _join(alternatives)
                continue
            assignment = _ASSIGN.fullmatch(node)
            if assignment:
                name, rhs = assignment.groups()
                name = name.casefold()
                if name.startswith("$"):
                    expression = self.expression(rhs)
                    if expression is not None and any(
                        var.casefold() == name[1:] for var in expression.variables()
                    ):
                        key = (active[-1], name[1:], rhs)
                        delta = self._step(expression, name[1:], state)
                        if key in self.steps:
                            delta = (
                                self.steps[key] | delta
                                if self.steps[key] is not None and delta is not None
                                else None
                            )
                        self.steps[key] = delta
                    state[name] = numeric_value(expression, state)
                    if name[1:] in self.selectors and state[name] is not None:
                        images = {
                            value
                            for key, values in state.items()
                            if key.startswith("ps-t") and values is not None
                            for value in values
                            if isinstance(value, _ImageBinding)
                        }
                        inputs = frozenset(
                            (
                                "$" + variable.casefold(),
                                state.get("$" + variable.casefold()),
                            )
                            for variable in expression.variables()
                        )
                        for slot in state[name]:
                            if float(slot).is_integer():
                                for frame in frames:
                                    frame["hits"].update(
                                        (name[1:], int(slot), image, inputs, path)
                                        for image in images
                                    )
                else:
                    resource = _RESOURCE.fullmatch(rhs)
                    reference = _REFERENCE.fullmatch(rhs)
                    target = reference.group(1).casefold() if reference else None
                    if target in state:
                        state[name] = state[target]
                    elif resource:
                        self._binding_id += 1
                        state[name] = frozenset(
                            [
                                _ImageBinding(
                                    resource.group(1).casefold(), self._binding_id
                                )
                            ]
                        )
                    else:
                        state[name] = None
                continue
            call = _RUN.fullmatch(node)
            if call:
                target = call.group(1).casefold()
                if target.startswith("customshader"):
                    images = {
                        value
                        for key, values in state.items()
                        if key.startswith("ps-t") and values is not None
                        for value in values
                        if isinstance(value, _ImageBinding)
                    }
                    if target in self.renderers:
                        inputs = frozenset(
                            (name, state.get(name)) for name in self._identity_inputs
                        )
                        for frame in frames:
                            for image in images:
                                frame["renders"].setdefault(image, set()).add(
                                    (inputs, path)
                                )
                    # Shader and external state changes are outside this
                    # control analysis. Later statements must reestablish it.
                    state = dict.fromkeys(state)
                else:
                    state = self._call(target, state, active, frames, path)
                continue
            unknown_write = re.match(
                r"^(?:(?:pre|post|local)\s+)?(\$\w+|ps-t\d+|Resource[^\s=]+)\b",
                node,
                re.I,
            )
            if unknown_write:
                state[unknown_write.group(1).casefold()] = None
            elif re.match(r"^(?:pre|post)\s+run\s*=", node, re.I):
                state = dict.fromkeys(state)
        return state

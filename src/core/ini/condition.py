"""Parse, partially evaluate and re-render 3DMigoto `if` conditions.

Unlike core.ini.parser's DNF form (good for "is this mesh visible?" but lossy for
writing), this keeps a condition as a syntax tree that renders back to text,
so removing one variable leaves the rest of the expression exactly as
written. Deleting a toggle needs that: `if $swapvar == 1 && $DRAW_TYPE == 1`
must become `if $DRAW_TYPE == 1`, not be rebuilt from scratch.

    node = parse("$v == 1 && $other == 2")
    reduce(node, {"v": "1"})   ->  node rendering as "$other == 2"
    reduce(node, {"v": "0"})   ->  FALSE
"""

import re

# Sentinels for a condition that partial evaluation has fully decided.
TRUE = "TRUE"
FALSE = "FALSE"

_TOKEN_RE = re.compile(r"""
      (?P<ws>\s+)
    | (?P<op>&&|\|\||===|!==|==|!=|<=|>=|<|>|//|[!()+\-*/%])
    | (?P<var>\$[\w\\]+)
    | (?P<num>0[xX][0-9a-fA-F]+|\d+(?:\.\d+)?)
    | (?P<word>[A-Za-z_]\w*(?:-[A-Za-z]\w*)*)
    | (?P<other>\S)
""", re.X)

# 3DMigoto spells strict comparison `===` / `!==` as well as `==` / `!=`.
_CMP_OPS = ("===", "!==", "==", "!=", "<=", ">=", "<", ">")
_ADD_OPS = ("+", "-")
_MUL_OPS = ("*", "/", "//", "%")


class ConditionError(ValueError):
    """The expression could not be parsed as a boolean condition."""


# -- nodes ------------------------------------------------------------------

class Node:
    def render(self):
        raise NotImplementedError

    def variables(self):
        """Set of `$` variable names (without the `$`) this node reads."""
        return set()

    def __repr__(self):
        return f"<{type(self).__name__} {self.render()!r}>"


class Operand(Node):
    """A leaf: `$var`, a number, or a bare word such as DRAW_TYPE."""

    def __init__(self, text):
        self.text = text

    @property
    def is_var(self):
        return self.text.startswith("$")

    @property
    def var(self):
        return self.text[1:] if self.is_var else None

    def render(self):
        return self.text

    def variables(self):
        return {self.var} if self.is_var else set()


class Cmp(Node):
    def __init__(self, left, op, right):
        self.left, self.op, self.right = left, op, right

    def render(self):
        return f"{self.left.render()} {self.op} {self.right.render()}"

    def variables(self):
        return self.left.variables() | self.right.variables()


class Arith(Node):
    """`a + b`, `a * b`, … — never evaluated, only carried through so a
    condition containing arithmetic can still be re-rendered faithfully."""

    def __init__(self, left, op, right):
        self.left, self.op, self.right = left, op, right

    def render(self):
        return f"{self.left.render()} {self.op} {self.right.render()}"

    def variables(self):
        return self.left.variables() | self.right.variables()


class Paren(Node):
    """An explicit `( … )` the author wrote. Kept so rendering doesn't quietly
    restructure an expression we only meant to inspect."""

    def __init__(self, inner):
        self.inner = inner

    def render(self):
        return f"({self.inner.render()})"

    def variables(self):
        return self.inner.variables()


class Not(Node):
    def __init__(self, operand):
        self.operand = operand

    def render(self):
        inner = self.operand.render()
        if isinstance(self.operand, (And, Or)):
            inner = f"({inner})"
        return f"!{inner}"

    def variables(self):
        return self.operand.variables()


class And(Node):
    def __init__(self, parts):
        self.parts = parts

    def render(self):
        return " && ".join(
            f"({p.render()})" if isinstance(p, Or) else p.render()
            for p in self.parts)

    def variables(self):
        return set().union(*(p.variables() for p in self.parts))


class Or(Node):
    def __init__(self, parts):
        self.parts = parts

    def render(self):
        return " || ".join(p.render() for p in self.parts)

    def variables(self):
        return set().union(*(p.variables() for p in self.parts))


# -- parsing ----------------------------------------------------------------

def tokenize(text):
    tokens = []
    pos = 0
    while pos < len(text):
        m = _TOKEN_RE.match(text, pos)
        if not m:
            raise ConditionError(f"cannot tokenize at offset {pos}: {text[pos:]!r}")
        pos = m.end()
        if m.lastgroup == "ws":
            continue
        if m.lastgroup == "other":
            raise ConditionError(f"unexpected character {m.group()!r} in {text!r}")
        tokens.append(m.group())
    return tokens


def parse(text):
    """Parse a condition expression. Raises ConditionError on anything unusual.

    Callers should treat a ConditionError as "leave this gate alone" rather
    than guessing: a condition we can't re-render is one we must not rewrite.
    """
    tokens = tokenize(text)
    if not tokens:
        raise ConditionError("empty condition")
    pos = 0

    def peek():
        return tokens[pos] if pos < len(tokens) else None

    def parse_or():
        nonlocal pos
        parts = [parse_and()]
        while peek() == "||":
            pos += 1
            parts.append(parse_and())
        return parts[0] if len(parts) == 1 else Or(parts)

    def parse_and():
        nonlocal pos
        parts = [parse_unary()]
        while peek() == "&&":
            pos += 1
            parts.append(parse_unary())
        return parts[0] if len(parts) == 1 else And(parts)

    def parse_unary():
        nonlocal pos
        if peek() == "!":
            pos += 1
            return Not(parse_unary())
        return parse_cmp()

    def parse_cmp():
        nonlocal pos
        left = parse_add()
        if peek() in _CMP_OPS:
            op = tokens[pos]
            pos += 1
            right = parse_add()
            return Cmp(left, op, right)
        return left

    def parse_add():
        nonlocal pos
        node = parse_mul()
        while peek() in _ADD_OPS:
            op = tokens[pos]
            pos += 1
            node = Arith(node, op, parse_mul())
        return node

    def parse_mul():
        nonlocal pos
        node = parse_primary()
        while peek() in _MUL_OPS:
            op = tokens[pos]
            pos += 1
            node = Arith(node, op, parse_primary())
        return node

    def parse_primary():
        nonlocal pos
        tok = peek()
        if tok is None:
            raise ConditionError(f"unexpected end of condition in {text!r}")
        if tok == "(":
            pos += 1
            node = parse_or()
            if peek() != ")":
                raise ConditionError(f"unbalanced parentheses in {text!r}")
            pos += 1
            return Paren(node)
        if tok == "-":
            # Unary minus, as in `$x == -1`.
            pos += 1
            return Operand("-" + parse_primary().render())
        if tok in _CMP_OPS or tok in ("&&", "||", ")", "!", "+", "*", "/", "//", "%"):
            raise ConditionError(f"unexpected token {tok!r} in {text!r}")
        pos += 1
        return Operand(tok)

    node = parse_or()
    if pos != len(tokens):
        raise ConditionError(f"trailing tokens {tokens[pos:]} in {text!r}")
    return node


# -- partial evaluation -----------------------------------------------------

def _as_number(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _compare(left, op, right):
    """Evaluate a comparison of two literals, or None if undecidable."""
    ln, rn = _as_number(left), _as_number(right)
    if ln is not None and rn is not None:
        a, b = ln, rn
    elif op in ("==", "!=", "===", "!=="):
        a, b = left, right
    else:
        return None   # ordering comparison on non-numbers: don't guess
    if op in ("==", "==="):  return a == b
    if op in ("!=", "!=="):  return a != b
    if op == "<":   return a < b
    if op == ">":   return a > b
    if op == "<=":  return a <= b
    if op == ">=":  return a >= b
    return None


def _literal(node, bindings):
    """The known value of an operand, or None if it can't be decided here.

    Only two things are knowable: a variable we were given a binding for, and
    a numeric literal. A bare word such as `vs-cb3`, `null` or `DRAW_TYPE` is a
    *runtime* value — treating it as a string literal would fold
    `vs-cb3 == 3381.7777` to FALSE and silently delete a live gate.
    """
    if not isinstance(node, Operand):
        return None
    if node.is_var:
        return bindings.get(node.var)
    return node.text if _as_number(node.text) is not None else None


def _fold(node, leaf_fn):
    """Shared post-order walk behind reduce() and eliminate(). `leaf_fn`
    decides each Cmp/Operand/Arith leaf (TRUE/FALSE/unchanged); And/Or/Not/
    Paren combine those results the same way either way.
    """
    if node is TRUE or node is FALSE:
        return node

    if isinstance(node, (Cmp, Operand, Arith)):
        return leaf_fn(node)

    if isinstance(node, Paren):
        inner = _fold(node.inner, leaf_fn)
        if inner is TRUE or inner is FALSE:
            return inner
        return Paren(inner)

    if isinstance(node, Not):
        inner = _fold(node.operand, leaf_fn)
        if inner is TRUE:  return FALSE
        if inner is FALSE: return TRUE
        return Not(inner)

    if isinstance(node, And):
        kept = []
        for part in node.parts:
            r = _fold(part, leaf_fn)
            if r is FALSE:
                return FALSE
            if r is not TRUE:
                kept.append(r)
        if not kept:
            return TRUE
        return kept[0] if len(kept) == 1 else And(kept)

    if isinstance(node, Or):
        kept = []
        for part in node.parts:
            r = _fold(part, leaf_fn)
            if r is TRUE:
                return TRUE
            if r is not FALSE:
                kept.append(r)
        if not kept:
            return FALSE
        return kept[0] if len(kept) == 1 else Or(kept)

    raise ConditionError(f"unknown node {node!r}")


def reduce(node, bindings):
    """Partially evaluate `node` with `bindings` ({var name: value string}).

    Returns TRUE, FALSE, or a Node with the decided parts folded away. Anything
    not mentioned in `bindings` is left untouched — that is the whole point:
    conditions routinely mix a toggle variable with vars this app never models
    (`$DRAW_TYPE`, master swap vars), and those must survive verbatim.
    """
    def leaf(n):
        if isinstance(n, Arith):
            # Arithmetic is carried, never folded: `$x + 1` has no boolean
            # value here and its operands may be anything.
            return n
        if isinstance(n, Cmp):
            left = _literal(n.left, bindings)
            right = _literal(n.right, bindings)
            if left is not None and right is not None:
                result = _compare(left, n.op, right)
                if result is not None:
                    return TRUE if result else FALSE
            return n
        # Operand: a bare `if $x` is true for any non-zero value.
        if n.is_var and n.var in bindings:
            return FALSE if _as_number(bindings[n.var]) == 0 else TRUE
        return n

    return _fold(node, leaf)


def eliminate(node, dead_vars):
    """Fold away every clause reading any of `dead_vars`, treating it as
    always-satisfied — used when a toggle is deleted and whatever it used to
    gate should become unconditional.

    A Cmp/Arith leaf counts as reading a dead var if either side's full
    variable set (recursing through Arith/Paren) intersects `dead_vars`, not
    just a bare `$dead_var` operand — otherwise a condition like
    `cursor_x < $img_x + $norm_width` would never be recognised as
    referencing `$img_x`, causing an infinite rewrite loop. Each such leaf
    folds only to TRUE.

    That doesn't extend to the whole tree: `!$v` still inverts that TRUE to
    FALSE via ordinary And/Or/Not algebra, so `if !$v` becomes `if 0` once
    `$v` is deleted — one of two mutually-exclusive branches must "win", and
    a FALSE result at the top level is a real, expected outcome callers must
    handle, not restructured away (out of scope here; see toggle_editor.py).
    """
    dead = set(dead_vars)

    def leaf(n):
        if isinstance(n, Cmp):
            if (n.left.variables() & dead) or (n.right.variables() & dead):
                return TRUE
            return n
        if isinstance(n, Arith):
            # A bare arithmetic condition (`if $img_x + 1`, no comparison at
            # all) is unusual but grammatically legal; treat it exactly like
            # a Cmp operand — any dead var anywhere inside it decides it.
            return TRUE if (n.variables() & dead) else n
        # Operand
        if n.is_var and n.var in dead:
            return TRUE
        return n

    return _fold(node, leaf)


def render(node):
    """Render a reduce() result. Sentinels come back as themselves."""
    if node is TRUE or node is FALSE:
        return node
    return node.render()


def references(text, var):
    """True if the condition text reads `$var`. Returns False on a parse error,
    so an unparseable condition is never assumed to depend on the variable."""
    try:
        return var in parse(text).variables()
    except ConditionError:
        return False


def find_comparisons(node, var):
    """List of (op, literal_text) for every `$var <op> literal` (or the
    mirrored `literal <op> $var`) comparison in the tree.

    Used to discover which cycle values a variable is actually gated on, e.g.
    to warn before removing a value that some `if`/`elif` still tests for.
    """
    out = []

    def walk(n):
        if isinstance(n, Cmp):
            l, r = n.left, n.right
            if isinstance(l, Operand) and l.is_var and l.var == var \
                    and isinstance(r, Operand) and not r.is_var:
                out.append((n.op, r.text))
            elif isinstance(r, Operand) and r.is_var and r.var == var \
                    and isinstance(l, Operand) and not l.is_var:
                out.append((n.op, l.text))
            return
        if isinstance(n, Not):
            walk(n.operand)
        elif isinstance(n, Paren):
            walk(n.inner)
        elif isinstance(n, (And, Or)):
            for part in n.parts:
                walk(part)
        # Arith and bare Operand carry no comparison to record.

    walk(node)
    return out


def is_namespaced(var):
    """`$\\Remielle\\Master\\swapvar` is a cross-ini global owned by another
    file. Those are read-only here: rewriting one would change a mod the user
    isn't editing."""
    return "\\" in (var or "")

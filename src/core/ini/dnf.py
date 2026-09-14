"""Conditions in DNF ("disjunctive normal form") — the READ-path view of an
`if` expression: good for answering "is this mesh visible right now?".

A condition is a list of OR'd alternatives, each alternative a list of AND'd
{var, value, negate} clauses. Two sentinel values matter:
    DNF_TRUE  = [[]]  -> one alternative with no constraints (always visible)
    DNF_FALSE = []    -> no satisfiable alternative (never visible)
`[]` doubles as "no tracked constraint" once untracked vars are filtered out,
which is why normalize_dnf() collapses an always-true result back to [].

core/ini/condition.py is the complementary WRITE-path view: a syntax tree
that renders back to the original text.
"""

import re

_CLAUSE_RE = re.compile(r'\$(\w+)\s*(==|!=)\s*(-?[\w.]+)')
_ASSIGN_BOOL_RE = re.compile(r'^\$(\w+)\s*=\s*(.+)$')
_STRUCT_RE = re.compile(r'(\(|\)|&&|\|\||!(?!=))')

DNF_TRUE:  list = [[]]
DNF_FALSE: list = []

# Cap DNF growth: AND-ing/negating deeply nested ||-expressions can blow up
# combinatorially. Past this many alternatives the condition is treated as
# unconstrained (always visible), which fails open rather than hiding meshes.
_MAX_DNF_GROUPS = 128


def dnf_or(a, b):
    out = list(a)
    for g in b:
        if g not in out:
            out.append(g)
    return out if len(out) <= _MAX_DNF_GROUPS else DNF_TRUE


def _simplify_group(group):
    """Drop `$v != x` clauses made redundant by a `$v == y` clause on the same
    variable. An elif chain accumulates the negation of every earlier branch, so
    `$v != 0 AND $v != 1 AND $v == 2` is common -- and `$v == 2` alone says it.

    Deliberately conservative: contradictions (`$v == 1 AND $v != 1`, or two
    different `==` values) are left intact rather than collapsed to an empty
    group, because an empty group is DNF_TRUE ("always visible") and would flip
    an impossible condition into an unconditional one."""
    eq: dict = {}
    for c in group:
        if not c["negate"]:
            eq.setdefault(c["var"], set()).add(c["value"])
    redundant = {v: vals.pop() for v, vals in eq.items() if len(vals) == 1}
    if not redundant:
        return group
    return [c for c in group
            if not (c["negate"] and redundant.get(c["var"], c["value"]) != c["value"])]


def dnf_and(a, b):
    if len(a) * len(b) > _MAX_DNF_GROUPS:
        return DNF_TRUE
    out: list = []
    for ga in a:
        for gb in b:
            merged = list(ga)
            for c in gb:
                if c not in merged:
                    merged.append(c)
            merged = _simplify_group(merged)
            if merged not in out:
                out.append(merged)
    return out


def dnf_not(dnf):
    """NOT of a DNF, via De Morgan: NOT(g1 OR g2) == NOT(g1) AND NOT(g2),
    and NOT(c1 AND c2) == (NOT c1) OR (NOT c2)."""
    result = DNF_TRUE
    for group in dnf:
        neg_group = [[{"var": c["var"], "value": c["value"], "negate": not c["negate"]}]
                     for c in group]
        result = dnf_and(result, neg_group)
    return result


def _atom_to_dnf(atom, alias_map):
    """Convert a single comparison / bare-boolean token into DNF. Anything that
    can't be traced to a real variable (numeric literals, DRAW_TYPE, unsupported
    operators like <=) becomes DNF_TRUE so it never hides a mesh."""
    atom = atom.strip()
    if not atom:
        return DNF_TRUE
    negate_atom = False
    while atom.startswith("!"):
        negate_atom = not negate_atom
        atom = atom[1:].strip()

    m = _CLAUSE_RE.fullmatch(atom)
    if m:
        v, op, val = m.group(1), m.group(2), m.group(3)
        dnf = [[{"var": v, "value": val, "negate": op == "!="}]]
    else:
        m = re.fullmatch(r'\$(\w+)', atom)
        if m:
            # Alias-map values are already DNF. A non-alias bare variable is
            # an ordinary 3DMigoto truthiness test (`if $hat` means non-zero),
            # not an untracked runtime expression. normalize_dnf() will still
            # discard it later when the variable is not a viewer control.
            name = m.group(1)
            dnf = alias_map.get(name)
            if dnf is None:
                dnf = [[{"var": name, "value": "0", "negate": True}]]
        else:
            dnf = DNF_TRUE
    return dnf_not(dnf) if negate_atom else dnf


def parse_condition_dnf(content, alias_map):
    """Parse an `if <expr>` expression into DNF, honouring &&, || and
    parentheses. Previously every comparison found anywhere in the expression
    was blindly AND'd together, so `$x == 0 || $x == 2` became the impossible
    `$x == 0 && $x == 2` and its mesh could never be shown."""
    tokens = [t.strip() for t in _STRUCT_RE.split(content) if t and t.strip()]
    pos = 0

    def parse_or():
        nonlocal pos
        node = parse_and()
        while pos < len(tokens) and tokens[pos] == "||":
            pos += 1
            node = dnf_or(node, parse_and())
        return node

    def parse_and():
        nonlocal pos
        node = parse_atom()
        while pos < len(tokens) and tokens[pos] == "&&":
            pos += 1
            node = dnf_and(node, parse_atom())
        return node

    def parse_atom():
        nonlocal pos
        if pos >= len(tokens):
            return DNF_TRUE
        tok = tokens[pos]
        if tok == "!":
            pos += 1
            return dnf_not(parse_atom())
        if tok == "(":
            pos += 1
            node = parse_or()
            if pos < len(tokens) and tokens[pos] == ")":
                pos += 1
            return node
        if tok in (")", "&&", "||"):
            pos += 1
            return DNF_TRUE
        pos += 1
        return _atom_to_dnf(tok, alias_map)

    try:
        return parse_or()
    except RecursionError:
        return DNF_TRUE


def normalize_dnf(dnf, toggle_vars, var_prefix=None):
    """Drop clauses on untracked variables (they're assumed satisfied, matching
    long-standing behaviour), then apply var_prefix. An alternative left with no
    clauses is unconditionally true, which makes the whole condition true -> [].

    Matching is case-insensitive and rewrites each clause to the tracked
    spelling: 3DMigoto doesn't care whether a draw is gated on `$hair` or
    `$Hair`, but a mod that spells it one way in [Constants] and the other in
    the draw would otherwise leave the mesh untracked, hence always visible.
    """
    tracked = {v.lower(): v for v in toggle_vars}
    out: list = []
    for group in dnf:
        kept = [{"var": tracked[c["var"].lower()], "value": c["value"],
                 "negate": c["negate"]}
                for c in group if c["var"].lower() in tracked]
        if not kept:
            return []
        if var_prefix:
            kept = [{**c, "var": f"{var_prefix}{c['var']}"} for c in kept]
        if kept not in out:
            out.append(kept)
    return out


def build_bool_alias_map(sections):
    """Resolve WWMI-style boolean aliases such as
    `$draw_component_4_heels_flat = ($swapvar_heels == 1)` into a map of
    alias_var -> DNF, so a later bare `if $draw_component_4_heels_flat` can
    be traced back to the real toggle var. The RHS is parsed as a full
    boolean expression (not just AND'd clauses) so an ||-alias like
    `($swapvar_arm == 0) || ($swapvar_arm == 2)` doesn't collapse to the
    impossible `== 0 && == 2`. Two passes let an alias reference an earlier one."""
    raw_defs: dict = {}
    for lines in sections.values():
        for raw in lines:
            line = raw.split(";")[0].strip()
            m = _ASSIGN_BOOL_RE.match(line)
            if not m: continue
            alias, rhs = m.group(1), m.group(2).strip()
            # Only boolean expressions are aliases; `$swapvar = 0` is a value init.
            if "==" not in rhs and "!=" not in rhs: continue
            if alias not in raw_defs:
                raw_defs[alias] = rhs

    alias_map: dict = {}
    for _ in range(2):
        for alias, rhs in raw_defs.items():
            dnf = parse_condition_dnf(rhs, alias_map)
            if dnf and dnf != DNF_TRUE:
                alias_map[alias] = dnf
    return alias_map

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
from decimal import Decimal, InvalidOperation
import operator

from .toggles import extract_toggle_keys

_VAR_TOKEN = r'(?:\\[^\\\s()&|!=<>]+(?:\\[^\\\s()&|!=<>]+)+|\w+)'
_CLAUSE_RE = re.compile(rf'\$({_VAR_TOKEN})\s*(==|!=)\s*(-?[\w.]+)')
_ORDER_RE = re.compile(rf'\$({_VAR_TOKEN})\s*(<=|>=|<|>)\s*([-+\w.]+)')
_ORDER_OPERATORS = {"<": operator.lt, "<=": operator.le,
                    ">": operator.gt, ">=": operator.ge}
_ASSIGN_BOOL_RE = re.compile(rf'^\$({_VAR_TOKEN})\s*=\s*(.+)$')
_STRUCT_RE = re.compile(r'(\(|\)|&&|\|\||!(?!=))')

DNF_TRUE:  list = [[]]
DNF_FALSE: list = []

# Cap DNF growth: AND-ing/negating deeply nested ||-expressions can blow up
# combinatorially. Past this many alternatives the condition is treated as
# unconstrained (always visible), which fails open rather than hiding meshes.
_MAX_DNF_GROUPS = 128


class _BoolAliases(dict):
    """Boolean aliases and finite numeric cycle domains from the same INI."""

    def __init__(self, domains):
        super().__init__()
        self.domains = domains


def _numeric_value(value):
    try:
        number = Decimal(value)
        return number if number.is_finite() else None
    except InvalidOperation:
        return None


def _cycle_domains(sections, toggle_keys, menu, var_prefix):
    values = {}
    for info in toggle_keys.values():
        for name, cycle in info["vars"].items():
            if var_prefix and name.startswith(var_prefix):
                name = name[len(var_prefix):]
            domain = values.setdefault(name.casefold(), [])
            domain.extend(value for value in cycle if value not in domain)
    for info in menu.values():
        name = info["var"]
        if var_prefix and name.startswith(var_prefix):
            name = name[len(var_prefix):]
        domain = values.setdefault(name.casefold(), [])
        domain.extend(value for value in info["values"] if value not in domain)
    for section, lines in sections.items():
        if section.casefold() != "constants":
            continue
        for line in lines:
            match = re.fullmatch(
                r"global\s+(?:persist\s+)?\$(\w+)\s*=\s*(.+)", line, re.I)
            if match and match[1].casefold() in values:
                domain = values[match[1].casefold()]
                value = match[2].strip()
                if value not in domain:
                    domain.append(value)
    return {name: domain for name, domain in values.items()
            if 0 < len(domain) <= _MAX_DNF_GROUPS
            and all(_numeric_value(value) is not None for value in domain)}


def _ordered_comparison(match, alias_map):
    name, op, threshold = match.groups()
    domain = getattr(alias_map, "domains", {}).get(name.casefold())
    number = _numeric_value(threshold)
    if not domain or number is None:
        return DNF_TRUE
    selected = [value for value in domain
                if _ORDER_OPERATORS[op](_numeric_value(value), number)]
    if selected:
        return [[{"var": name, "value": value, "negate": False}]
                for value in selected]
    # Empty draw DNF means untracked/visible downstream. Keep an explicit
    # contradiction so an impossible numeric comparison stays invisible.
    return [[{"var": name, "value": domain[0], "negate": False},
             {"var": name, "value": domain[0], "negate": True}]]


def ordered_conditions_supported(content, alias_map):
    """Require a known finite domain for every numeric comparison in a guard.

    Draw discovery can ignore unknown runtime guards. State-rule replay cannot
    safely do that, because it would turn a conditional write into a real one.
    """
    domains = getattr(alias_map, "domains", {})
    for token in _STRUCT_RE.split(content):
        if "<" not in token and ">" not in token:
            continue
        match = _ORDER_RE.fullmatch(token.strip())
        if (not match or not domains.get(match[1].casefold())
                or _numeric_value(match[3]) is None):
            return False
    return True


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
    operators without a known finite domain) becomes DNF_TRUE so it never
    hides a mesh."""
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
    elif (ordered := _ORDER_RE.fullmatch(atom)):
        dnf = _ordered_comparison(ordered, alias_map)
    else:
        m = re.fullmatch(rf'\$({_VAR_TOKEN})', atom)
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


def normalize_dnf(dnf, toggle_vars, var_prefix=None, qualified_vars=None):
    """Drop clauses on untracked variables (they're assumed satisfied, matching
    long-standing behaviour), then apply var_prefix. An alternative left with no
    clauses is unconditionally true, which makes the whole condition true -> [].

    Matching is case-insensitive and rewrites each clause to the tracked
    spelling: 3DMigoto doesn't care whether a draw is gated on `$hair` or
    `$Hair`, but a mod that spells it one way in [Constants] and the other in
    the draw would otherwise leave the mesh untracked, hence always visible.
    """
    tracked = {str(v).casefold(): v for v in toggle_vars}
    qualified = {
        str(key).casefold(): value
        for key, value in (qualified_vars or {}).items()
    }
    # A scan may normalize the same DNF more than once. Keep resolved provider
    # identities valid on subsequent passes without applying the consumer's
    # prefix to them.
    qualified.update({
        str(value).casefold(): value
        for value in (qualified_vars or {}).values()
    })
    out: list = []
    for group in dnf:
        kept = []
        for clause in group:
            key = str(clause["var"]).casefold()
            if key in tracked:
                variable = tracked[key]
                if var_prefix:
                    variable = f"{var_prefix}{variable}"
            elif key in qualified:
                variable = qualified[key]
            else:
                continue
            kept.append({"var": variable, "value": clause["value"],
                         "negate": clause["negate"]})
        if not kept:
            return []
        if kept not in out:
            out.append(kept)
    return out


def build_bool_alias_map(sections, *, toggle_keys=None, menu=None, var_prefix=None):
    """Resolve WWMI-style boolean aliases such as
    `$draw_component_4_heels_flat = ($swapvar_heels == 1)` into a map of
    alias_var -> DNF, so a later bare `if $draw_component_4_heels_flat` can
    be traced back to the real toggle var. The RHS is parsed as a full
    boolean expression (not just AND'd clauses) so an ||-alias like
    `($swapvar_arm == 0) || ($swapvar_arm == 2)` doesn't collapse to the
    impossible `== 0 && == 2`. Two passes let an alias reference an earlier one."""
    toggle_keys = (toggle_keys if toggle_keys is not None
                   else extract_toggle_keys(sections))
    if menu is None:
        from .menu import extract_menu_toggles
        menu = extract_menu_toggles(sections)
    alias_map = _BoolAliases(_cycle_domains(sections, toggle_keys, menu, var_prefix))
    raw_defs: dict = {}
    for lines in sections.values():
        for raw in lines:
            line = raw.split(";")[0].strip()
            m = _ASSIGN_BOOL_RE.match(line)
            if not m: continue
            alias, rhs = m.group(1), m.group(2).strip()
            # Only boolean expressions are aliases; `$swapvar = 0` is a value init.
            if not any(op in rhs for op in ("==", "!=", "<", ">")): continue
            if alias not in raw_defs:
                raw_defs[alias] = rhs

    for _ in range(2):
        for alias, rhs in raw_defs.items():
            dnf = parse_condition_dnf(rhs, alias_map)
            if dnf and dnf != DNF_TRUE:
                alias_map[alias] = dnf
    return alias_map

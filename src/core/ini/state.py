"""Read-only simulation rules for literal variables derived in [Present]."""

import re

from .dnf import (DNF_TRUE, build_bool_alias_map, dnf_and, dnf_not,
                      dnf_or, normalize_dnf, parse_condition_dnf)
from .sections import canonical_var_names

_ASSIGN_RE = re.compile(r"^\$(\w+)\s*=\s*(-?\d+(?:\.\d+)?)\s*$")
_ELIF_RE = re.compile(r"(?:else\s+if|elif)\s+(.*)$", re.I)


def _possible_groups(groups):
    """Drop impossible AND groups from state rules.

    The shared DNF simplifier preserves contradictions because an empty draw
    condition means always-visible. Here an empty result means a rule can never
    run, so retaining `$v==1 AND $v!=1` only bloats elif exclusions and risks a
    fail-open frontend evaluation.
    """
    out = []
    for group in groups:
        equals = {}
        excluded = {}
        impossible = False
        for cond in group:
            var, value = cond["var"], cond["value"]
            if cond["negate"]:
                excluded.setdefault(var, set()).add(value)
                if equals.get(var) == value:
                    impossible = True
            else:
                if var in equals and equals[var] != value:
                    impossible = True
                equals[var] = value
                if value in excluded.get(var, ()):
                    impossible = True
        if not impossible and group not in out:
            out.append(group)
    return out


def extract_state_rules(sections, var_prefix=None, canonical_vars=None):
    """Return ordered literal assignments guarded by conditions in Present.

    This intentionally models only deterministic numeric assignments. It is
    enough for common WWMI menu vars that derive draw flags every frame, while
    avoiding an unsafe attempt to interpret arbitrary 3DMigoto commands.
    """
    lines = next((v for k, v in sections.items() if k.lower() == "present"), None)
    if not lines:
        return []
    canon = (canonical_vars if canonical_vars is not None
             else canonical_var_names(sections))
    tracked = set(canon.values())
    aliases = build_bool_alias_map(sections)
    stack = []
    rules = []

    for raw in lines:
        line = str(raw).split(";", 1)[0].strip()
        if not line:
            continue
        low = line.lower()
        match = _ELIF_RE.fullmatch(line)
        if match:
            if stack:
                frame = stack[-1]
                branch = parse_condition_dnf(match.group(1), aliases)
                frame["cur"] = dnf_and(dnf_not(frame["seen"]), branch)
                frame["seen"] = dnf_or(frame["seen"], branch)
            continue
        if low.startswith("if "):
            branch = parse_condition_dnf(line[3:], aliases)
            stack.append({"cur": branch, "seen": branch})
            continue
        if low == "else":
            if stack:
                stack[-1]["cur"] = dnf_not(stack[-1]["seen"])
            continue
        if low == "endif":
            if stack:
                stack.pop()
            continue
        match = _ASSIGN_RE.fullmatch(line)
        if not match:
            continue
        combined = DNF_TRUE
        for frame in stack:
            combined = dnf_and(combined, frame["cur"])
        conditions = _possible_groups(normalize_dnf(combined, tracked, var_prefix))
        if combined != DNF_TRUE and not conditions:
            continue
        var = canon.get(match.group(1).lower(), match.group(1))
        rules.append({
            "var": f"{var_prefix or ''}{var}",
            "value": match.group(2),
            "conditions": conditions,
        })
    return rules

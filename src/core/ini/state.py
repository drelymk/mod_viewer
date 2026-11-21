"""Read-only simulation rules for literal variables derived in [Present]."""

import re

from .dnf import (DNF_TRUE, build_bool_alias_map, dnf_and, dnf_not,
                      dnf_or, normalize_dnf, ordered_conditions_supported,
                      parse_condition_dnf)
from .sections import canonical_var_names

_ASSIGN_RE = re.compile(r"^\$(\w+)\s*=\s*(-?\d+(?:\.\d+)?)\s*$")
_ELIF_RE = re.compile(r"(?:else\s+if|elif)\s+(.*)$", re.I)
_UNSUPPORTED_CONDITION_RE = re.compile(r"[+*/%]|\btime\b", re.I)


def control_dependencies(variables, state_rules=()):
    """Trace derived inputs back to their controllers, including cyclic rules."""
    dependencies = {}
    for rule in state_rules:
        target = str(rule.get("var", "")).casefold()
        inputs = dependencies.setdefault(target, set())
        inputs.update(clause["var"] for group in rule.get("conditions", ())
                      for clause in group)
    found = {str(variable).casefold(): variable for variable in variables}
    pending = list(found)
    while pending:
        for variable in dependencies.get(pending.pop(), ()):
            key = variable.casefold()
            if key not in found:
                found[key] = variable
                pending.append(key)
    return set(found.values())


def _condition_is_supported(expression, aliases):
    """Reject branches whose truth value cannot be represented by DNF."""
    return (_UNSUPPORTED_CONDITION_RE.search(str(expression)) is None
            and ordered_conditions_supported(str(expression), aliases))


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


def _literal_rules(lines, tracked, aliases, var_prefix, canon, targets=None):
    """Return ordered literal writes in one section with their guard DNF.

    With ``targets`` (draw sections) only those variables are recorded, and a
    guard testing no tracked control is runtime evidence that the section
    runs: its first branch is unconditional, its elif/else branches and guards
    mixing runtime and control tests are not represented.
    """
    tracked_keys = {var.casefold() for var in tracked}

    def guard_supported(expression):
        return ((targets is None
                 or _guard_kind(expression, tracked_keys) == "tracked")
                and _condition_is_supported(expression, aliases))

    stack = []
    rules = []
    for raw in lines:
        line = str(raw).split(";", 1)[0].strip()
        if not line:
            continue
        low = line.lower()
        match = _ELIF_RE.fullmatch(line)
        if match or low == "else":
            if not stack:
                continue
            frame = stack[-1]
            if frame["runtime"]:
                frame["supported"] = False
            elif match:
                branch = parse_condition_dnf(match.group(1), aliases)
                frame["cur"] = dnf_and(dnf_not(frame["seen"]), branch)
                frame["seen"] = dnf_or(frame["seen"], branch)
                frame["supported"] = (frame["supported"]
                                      and guard_supported(match.group(1)))
            else:
                frame["cur"] = dnf_not(frame["seen"])
            continue
        if low.startswith("if "):
            expression = line[3:]
            runtime = (targets is not None
                       and _guard_kind(expression, tracked_keys) == "runtime")
            branch = (DNF_TRUE if runtime
                      else parse_condition_dnf(expression, aliases))
            stack.append({"runtime": runtime, "cur": branch, "seen": branch,
                          "supported": runtime or guard_supported(expression)})
            continue
        if low == "endif":
            if stack:
                stack.pop()
            continue
        match = _ASSIGN_RE.fullmatch(line)
        if (not match or not all(frame["supported"] for frame in stack)
                or (targets is not None
                    and match.group(1).casefold() not in targets)):
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


def extract_state_rules(sections, var_prefix=None, canonical_vars=None, *,
                        condition_aliases=None):
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
    aliases = (condition_aliases if condition_aliases is not None
               else build_bool_alias_map(sections))
    return _literal_rules(lines, set(canon.values()), aliases, var_prefix, canon)


_WRITE_RE = re.compile(r"^(?:\w+\s+)*\$(\w+)\s*=(?!=)")
_GUARD_VAR_RE = re.compile(r"\$[\w\\]+")
_GUARD_SYNTAX_RE = re.compile(r"-?\d+(?:\.\d+)?|[()!&|=<>]")


def _guard_kind(expression, tracked_keys):
    """Classify a guard as control-only, runtime-only or mixed.

    Runtime guards (``$mod_enabled``, ``ResourceX !== null``) are matching
    evidence that the draw section executes, the same way draw discovery
    treats them. A guard that mixes them with controls is not representable.
    """
    names = [token[1:].casefold() for token in _GUARD_VAR_RE.findall(expression)]
    residue = _GUARD_SYNTAX_RE.sub(" ", _GUARD_VAR_RE.sub(" ", expression))
    runtime = (re.search(r"[A-Za-z_]", residue) is not None
               or any(name not in tracked_keys for name in names))
    tracked = any(name in tracked_keys for name in names)
    if runtime and tracked:
        return "mixed"
    return "runtime" if runtime else "tracked"


def extract_draw_derived_rules(sections, targets, control_vars, var_prefix=None,
                               canonical_vars=None, *, condition_aliases=None):
    """Return literal ``targets`` writes made inside TextureOverride sections.

    WWMI mods commonly derive shape-key weights from toggles while a component
    draws, for example ``if $legs == 0 || $legs == 1`` -> ``$thighShape = 1``,
    and consume them from ``[Present]`` on the next frame. A target qualifies
    only when every write to it outside ``[Constants]`` is a literal in a
    TextureOverride section. Rules follow section order and are replayed after
    the ``[Present]`` rules.
    """
    canon = (canonical_vars if canonical_vars is not None
             else canonical_var_names(sections))
    tracked = {canon.get(str(var).casefold(), str(var)) for var in control_vars}
    targets = ({str(var).casefold() for var in targets}
               - {var.casefold() for var in tracked})
    for section, lines in sections.items():
        name = section.casefold()
        if name == "constants":
            continue
        for raw in lines:
            line = str(raw).split(";", 1)[0].strip()
            write = _WRITE_RE.match(line)
            if write and not (name.startswith("textureoverride")
                              and _ASSIGN_RE.fullmatch(line)):
                targets.discard(write.group(1).casefold())
    if not targets:
        return []
    aliases = (condition_aliases if condition_aliases is not None
               else build_bool_alias_map(sections))
    rules = []
    for section, lines in sections.items():
        if section.casefold().startswith("textureoverride"):
            rules.extend(_literal_rules(
                lines, tracked, aliases, var_prefix, canon, targets))
    return rules

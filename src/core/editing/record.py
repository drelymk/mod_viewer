"""Rewrite toggle-controlled draw visibility for Record mode.

Only unambiguous structures are rewritten; unsupported or ambiguous cases are
reported without guessing. Draw targets resolve by stable section, occurrence,
and draw tuple identity rather than submitted line numbers.
"""

import re

from ..ini import condition as ic
from ..ini.parser import _scan_sections_for_draws
from . import toggle as te
from ..ini.document import (IniDocument, IF, ELIF, ELSE, ENDIF, DRAW, BLANK,
                           COMMENT)
from ..ini.sections import sections_from_document

_COND_RE = re.compile(r"^(if|else\s+if|elif)\s+(.*)$", re.I)


class _Unsupported(Exception):
    """Signal that a chain cannot be rewritten safely; callers report it as skipped."""


def _skip(report, var, line, reason, code=None, params=None):
    item = {"var": var, "line": line, "reason": reason}
    if code:
        item["reason_code"] = code
        item["reason_params"] = params or {}
    report["skipped"].append(item)


def _split_cond(line):
    m = _COND_RE.match(line.text)
    return (m.group(1), m.group(2).strip()) if m else None


def _refs(doc, line, var):
    """Whether this branch is gated by `var`.

    An `else` inherits its earlier sibling conditions, so treat it as a
    reference when an earlier `if`/`elif` in the same chain reads `var`.
    Otherwise a new inner wrapper could make the draw unreachable.
    """
    if line.kind == ELSE:
        try:
            leader = _chain_leader(doc, line)
        except _Unsupported:
            return False
        return any(_refs(doc, doc.lines[no], var)
                   for no in range(leader.no, line.no)
                   if doc.lines[no].depth == line.depth and doc.lines[no].kind in (IF, ELIF))
    cond = _split_cond(line)
    return bool(cond and ic.references(cond[1], var))


def _ancestors(doc, line):
    """Branch-open lines (if/elif/else) enclosing `line`, innermost first."""
    sec = line.section
    out = []
    depth = line.depth
    no = line.no - 1
    while no >= sec.start and depth > 0:
        cur = doc.lines[no]
        if cur.kind in (IF, ELIF, ELSE) and cur.depth == depth - 1:
            out.append(cur)
            depth -= 1
        no -= 1
    return out


def _existing_owners(doc, line, writable_names):
    """Return selected writable vars already gating ``line``.

    A co-driven variable with the same cycle length is not an owner.
    """
    owners = set()
    for ancestor in _ancestors(doc, line):
        for var in writable_names:
            if _refs(doc, ancestor, var):
                owners.add(var)
    return owners


def _chain_leader(doc, branch_line):
    """The `if` line that opens `branch_line`'s chain (itself, if it already
    is one)."""
    if branch_line.kind == IF:
        return branch_line
    sec = branch_line.section
    depth = branch_line.depth
    for no in range(branch_line.no - 1, sec.start - 1, -1):
        cur = doc.lines[no]
        if cur.depth == depth and cur.kind == IF:
            return cur
    raise _Unsupported(f"line {branch_line.no + 1}: elif/else with no matching if")


def _chain_of(if_line):
    """(branch_lines, endif_line) for the chain `if_line` opens: every `elif`
    at the same depth up to the matching `endif`. Raises _Unsupported if the
    chain has an `else` or never closes."""
    sec = if_line.section
    depth = if_line.depth
    branches = [if_line]
    for line in sec.lines:
        if line.no <= if_line.no or line.depth != depth:
            continue
        if line.kind == ENDIF:
            return branches, line
        if line.kind == ELSE:
            raise _Unsupported("chain has an else branch")
        if line.kind == ELIF:
            branches.append(line)
    raise _Unsupported("chain never closes (unmatched if)")


def _branch_body_range(branches, endif_line, index):
    start = branches[index].no + 1
    end = branches[index + 1].no if index + 1 < len(branches) else endif_line.no
    return start, end


def _chain_content(doc, var, branches, endif_line, desired):
    """The flat, ordered list of drawindexed Lines across every branch, or
    raises _Unsupported if this chain doesn't match the safe pattern."""
    for b in branches:
        cond = _split_cond(b)
        if not cond:
            raise _Unsupported(f"line {b.no + 1}: not a simple if/elif condition")
        try:
            node = ic.parse(cond[1])
        except ic.ConditionError:
            raise _Unsupported(f"line {b.no + 1}: condition does not parse")
        if node.variables() != {var}:
            raise _Unsupported(
                f"line {b.no + 1}: condition mixes ${var} with something else")

    content = []
    for i in range(len(branches)):
        start, end = _branch_body_range(branches, endif_line, i)
        for no in range(start, end):
            line = doc.lines[no]
            if line.kind in (BLANK, COMMENT):
                continue
            if line.kind != DRAW:
                raise _Unsupported(
                    f"line {line.no + 1}: non-drawindexed content inside a ${var} branch")
            if line.no not in desired:
                raise _Unsupported(
                    f"line {line.no + 1}: no recorded data for this draw "
                    f"(shown at more than one ini location, or not covered by this session)")
            content.append(line)
    return content


def _cycle_value(values, position):
    return values[min(max(position, 0), len(values) - 1)]


def _or_expr(var, values, pos_set, all_positions):
    if not pos_set:
        # A bare numeric condition is treated as an unknown runtime expression
        # by the read-path DNF parser and therefore fails open. Use a
        # same-variable contradiction so both the runtime and the viewer's
        # condition model represent a target hidden at every position.
        value = values[0]
        return f"${var} == {value} && ${var} != {value}"
    by_value = {}
    for position in all_positions:
        by_value.setdefault(_cycle_value(values, position), set()).add(position)
    selected = set(pos_set)
    partial = [positions for positions in by_value.values()
               if positions & selected and not positions <= selected]
    if partial:
        raise _Unsupported(
            f"this visibility differs between cycle positions where ${var} "
            "has the same value")
    selected_values = [value for value, positions in by_value.items()
                       if positions <= selected]
    return " || ".join(f"${var} == {value}" for value in selected_values)


def _regenerate_chain(doc, var, values, branches, endif_line, desired, all_positions):
    """(start, end_exclusive, new_lines) replacing the chain's whole span, or
    None if regenerating would produce exactly what's already there.

    Raises _Unsupported (propagated from _chain_content) if the chain isn't a
    safe pattern.
    """
    content = _chain_content(doc, var, branches, endif_line, desired)

    groups = []   # [(frozenset(positions), [Line, ...])]
    for line in content:
        pos_set = frozenset(desired[line.no])
        if groups and groups[-1][0] == pos_set:
            groups[-1][1].append(line)
        else:
            groups.append((pos_set, [line]))

    new_lines = []
    for pos_set, lines in groups:
        if pos_set == all_positions:
            new_lines.extend(line.raw for line in lines)
        else:
            new_lines.append(
                f"if {_or_expr(var, values, pos_set, all_positions)}")
            new_lines.extend(line.raw for line in lines)
            new_lines.append("endif")

    start, end = branches[0].no, endif_line.no + 1
    original = [doc.lines[no].raw for no in range(start, end)]
    if new_lines == original:
        return None
    return start, end, new_lines


def _analyze_var(doc, var, values, desired, report, unsafe_sections,
                 max_positions, target_owners=None, target_paths=None):
    """Analyze one variable's edits without mutating `doc`.

    Return chain edits, bare-line wraps, and lines whose visibility is
    controlled by this variable alone. Refused targets are added to `report`.
    """
    all_positions = frozenset(range(max_positions))
    target_owners = target_owners or {}
    target_paths = target_paths or {}
    chain_leaders = {}     # leader.no -> Line
    leader_lines = {}      # leader.no -> [desired line_no, ...]
    bare_targets = []      # Lines with no ancestor referencing var
    nested_bare = set()    # subset of bare_targets' line_no's that still sit
                            # inside some OTHER, unrelated ancestor condition
                            # (so their final gating isn't var-alone-clean)

    for line_no in sorted(desired):
        line = doc.lines[line_no]
        owners = target_owners.get(line_no, set())
        if target_paths.get(line_no) and var not in owners:
            _skip(report, var, line.no + 1,
                  "draw is reached through a run= command-list execution path "
                  "without a physical owner for this variable; edit the caller "
                  "branch manually", "command_path")
            continue
        if owners and var not in owners:
            continue
        if line.section is not None and line.section.name.lower() in unsafe_sections:
            _skip(report, var, line.no + 1,
                  "this section's if/elif/endif nesting is ambiguous (see "
                  "IniDocument.structure_errors); edit the ini directly",
                  "ambiguous_nesting")
            continue
        ancestors = _ancestors(doc, line)
        if not ancestors:
            bare_targets.append(line)
            continue

        immediate = ancestors[0]
        if _refs(doc, immediate, var):
            try:
                leader = _chain_leader(doc, immediate)
            except _Unsupported as e:
                _skip(report, var, line.no + 1, str(e), "unsupported",
                      {"detail": str(e)})
                continue
            chain_leaders[leader.no] = leader
            leader_lines.setdefault(leader.no, []).append(line_no)
            continue

        if any(_refs(doc, a, var) for a in ancestors[1:]):
            _skip(report, var, line.no + 1,
                  "gated by this variable at an outer nesting level; edit the ini "
                  "directly", "outer_gate")
        else:
            bare_targets.append(line)
            nested_bare.add(line.no)

    chain_edits = []
    verified = {}
    for leader_no in sorted(chain_leaders):
        leader = chain_leaders[leader_no]
        try:
            branches, endif_line = _chain_of(leader)
            edit = _regenerate_chain(doc, var, values, branches, endif_line, desired, all_positions)
        except _Unsupported as e:
            _skip(report, var, leader.no + 1, str(e), "unsupported",
                  {"detail": str(e)})
            continue
        if edit is not None:
            chain_edits.append(edit)
        # An untouched outer condition still affects visibility, so only a
        # top-level chain can be verified against this variable alone.
        if not _ancestors(doc, leader):
            for ln in leader_lines.get(leader_no, []):
                verified[ln] = sorted(desired[ln])

    bare_edits = {}
    for line in bare_targets:
        pos_set = frozenset(desired.get(line.no, ()))
        if line.no not in nested_bare:
            verified[line.no] = sorted(pos_set)
        if pos_set != all_positions:
            try:
                bare_edits[line.no] = _or_expr(
                    var, values, pos_set, all_positions)
            except _Unsupported as exc:
                verified.pop(line.no, None)
                _skip(report, var, line.no + 1, str(exc), "unsupported",
                      {"detail": str(exc)})

    return chain_edits, bare_edits, verified


def writable_cycle_vars(doc, section_name):
    """Return writable local vars and the full cycle length for this section.

    Namespaced vars are read-only but still contribute to the position count.
    Raise ToggleEditError if the section has no writable variable.
    """
    sec = te.find_cycle_section(doc, section_name)
    cvars = te.cycle_vars(sec, include_read_only=True)
    writable = {v: vals for v, vals in cvars.items() if not ic.is_namespaced(v)}
    if not writable:
        raise te.ToggleEditError(
            f"{section_name!r} is not a cycle toggle with any writable variable")
    return writable, max(len(v) for v in cvars.values())


def record_toggle(doc, section_name, position_lines, target_lines,
                  target_ini=None):
    """Rewrite draw visibility for an explicit Record scope.

    Resolve targets by stable draw identity because staged edits can make
    submitted line numbers stale. `position_lines` lists visible targets and
    must include every reachable cycle position.

    The report includes rewritten draws for independent verification.
    """
    writable, max_positions = writable_cycle_vars(doc, section_name)
    target_line_map = _resolve_target_refs(doc, target_lines, target_ini)
    try:
        normalized_positions = {
            int(pos): line_nos
            for pos, line_nos in (position_lines or {}).items()
        }
    except (AttributeError, TypeError, ValueError) as exc:
        raise te.ToggleEditError(
            "recorded positions and target lines must be numeric") from exc

    got = set(normalized_positions)
    if got != set(range(max_positions)):
        raise te.ToggleEditError(
            f"expected recorded data for positions 0..{max_positions - 1}, got {sorted(got)}")

    target_numbers = set(target_line_map.values())
    target_paths = {}
    for raw_ref in target_lines or []:
        line_no = target_line_map[int(raw_ref["line"])]
        path = _target_path(raw_ref)
        if path:
            target_paths.setdefault(line_no, []).append(path)
    normalized_visible = {}
    for pos, line_nos in normalized_positions.items():
        normalized_visible[pos] = []
        for ln in line_nos or []:
            try:
                submitted_line_no = int(ln)
            except (TypeError, ValueError) as exc:
                raise te.ToggleEditError(
                    "recorded positions and target lines must be numeric") from exc
            line_no = target_line_map.get(submitted_line_no)
            if line_no is None:
                raise te.ToggleEditError(
                    f"line {submitted_line_no} is visible at position {pos} but is not "
                    "an explicit Record target")
            normalized_visible[pos].append(line_no)

    # Include targets hidden at every position so Record can add their first gate.
    desired = {line_no: set() for line_no in target_numbers}
    for pos, line_nos in normalized_visible.items():
        for line_no in line_nos:
            desired[line_no].add(pos)

    report = {"vars_updated": [], "chains_rewritten": 0, "wraps_added": 0, "skipped": []}

    target_owners = {
        no: _existing_owners(doc, doc.lines[no], writable)
        for no in desired
    }

    unsafe_sections = {p["section"].lower() for p in doc.structure_errors() if p["section"]}

    all_chain_edits = []
    all_bare_claims = {}   # line_no -> [(var, expr), ...]
    per_var_verify = {}    # var -> (values, {line_no: [position, ...]})
    for var, values in writable.items():
        chain_edits, bare_edits, verified = _analyze_var(
            doc, var, values, desired, report, unsafe_sections, max_positions,
            target_owners, target_paths)
        all_chain_edits.extend(chain_edits)
        for line_no, expr in bare_edits.items():
            all_bare_claims.setdefault(line_no, []).append((var, expr))
        per_var_verify[var] = (values, verified)
        report["vars_updated"].append(var)

    final_edits = list(all_chain_edits)
    report["chains_rewritten"] = len(all_chain_edits)
    # Track refusals per variable so one failed wrap cannot suppress another
    # variable's successful rewrite of the same line.
    refused = set()
    for line_no, claims in all_bare_claims.items():
        if len(claims) > 1:
            _skip(report, "/".join(v for v, _ in claims), line_no + 1,
                  "targeted by more than one variable in this recording session",
                  "multiple_variables")
            refused.update((v, line_no) for v, _ in claims)
            continue
        var, expr = claims[0]
        # Both edits use original line offsets; reject a wrap inside another
        # variable's chain to avoid overlapping splices.
        if any(start <= line_no < end for start, end, _ in all_chain_edits):
            _skip(report, var, line_no + 1,
                  "sits inside another variable's gate being rewritten in this "
                  "same save; edit the ini directly to nest this condition",
                  "nested_rewrite")
            refused.add((var, line_no))
            continue
        line = doc.lines[line_no]
        final_edits.append((line.no, line.no + 1, [f"if {expr}", line.raw, "endif"]))
        report["wraps_added"] += 1

    # Keep successfully encoded targets for verification; draw identity stays
    # stable when the edits shift line numbers.
    report["verify"] = {}
    for var, (values, verified) in per_var_verify.items():
        draws = []
        for ln, positions in verified.items():
            if (var, ln) in refused:
                continue
            key = _draw_key(doc, ln)
            if key is None:
                continue
            sec_name, count, start, base = key
            draws.append({"section": sec_name, "count": count, "start": start,
                          "base": base, "positions": positions})
        if draws:
            report["verify"][var] = {"values": values, "draws": draws}

    # Apply later spans first to preserve earlier offsets; safe spans do not
    # overlap, and wraps inside regenerated chains were refused above.
    for start, end, new_lines in sorted(final_edits, key=lambda e: -e[0]):
        doc.replace_lines(start, end, new_lines)

    return report


# -- post-save self-check ---------------------------------------------------
#
# record_toggle above only proves *in-memory* that a rewritten chain encodes
# the recorded positions correctly -- the actual bytes on disk go through a
# separate path (IniDocument.save). verify_recording closes that gap: it
# re-parses the saved file through the same trusted DNF machinery
# (core.ini.parser/build_draw_groups) used everywhere else, and confirms every
# draw record_toggle touched is still visible at exactly its recorded
# positions. The caller (app.bridge.toggle.record_toggle) restores the
# just-made backup if not.

_DRAW_RE = re.compile(
    r"drawindexed\s*=\s*(\d+)\s*,\s*(\d+)\s*,\s*(-?\d+)", re.I)


def _draw_key(doc, line_no):
    """Return a draw identity that remains stable when rewrites shift line numbers."""
    if not 0 <= line_no < len(doc.lines):
        return None
    line = doc.lines[line_no]
    if line.kind != DRAW or line.section is None:
        return None
    m = _DRAW_RE.search(line.text)
    if not m:
        return None
    return (line.section.name, int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _draw_occurrence(doc, line_no):
    """Return the authored draw ordinal for a staged document line."""
    if not 0 <= line_no < len(doc.lines):
        return None
    line = doc.lines[line_no]
    if _draw_key(doc, line_no) is None or line.section is None:
        return None
    ordinal = sum(
        1 for item in doc.lines[line.section.start:line.no + 1]
        if _draw_key(doc, item.no) is not None
    ) - 1
    return {
        "section": line.section.name,
        "ordinal": ordinal,
        "path": [],
    }


def _draw_identity(doc, line_no):
    """Case-insensitive section, drawindexed tuple, and authored ordinal."""
    key = _draw_key(doc, line_no)
    occurrence = _draw_occurrence(doc, line_no)
    if key is None or occurrence is None:
        return None
    return (key[0].casefold(), *key[1:], occurrence["ordinal"])


def _ini_identity(value):
    return str(value).replace("\\", "/").strip().casefold()


def _target_identity(ref, target_ini=None):
    """Normalize one browser-supplied target reference."""
    if not isinstance(ref, dict):
        raise te.ToggleEditError(
            "Record targets must include their source line, section, and "
            "drawindexed identity")
    try:
        ini = ref["ini"]
        if not isinstance(ini, str) or not ini.strip():
            raise TypeError
        line_no = int(ref["line"])
        section = ref["section"]
        if not isinstance(section, str):
            raise TypeError
        section = section.strip()
        drawindexed = ref["drawindexed"]
        if isinstance(drawindexed, (str, bytes)) or len(drawindexed) != 3:
            raise ValueError
        triple = tuple(int(value) for value in drawindexed)
        occurrence = ref["occurrence"]
        if not isinstance(occurrence, dict):
            raise ValueError
        occurrence_section = occurrence["section"]
        occurrence_ordinal = int(occurrence["ordinal"])
        occurrence_path = occurrence.get("path", [])
        if not isinstance(occurrence_path, list):
            raise ValueError
        if any(not isinstance(item, list) or len(item) != 2
               for item in occurrence_path):
            raise ValueError
        if not isinstance(occurrence_section, str):
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise te.ToggleEditError(
            "Record targets must include a numeric source line, section, and "
            "three numeric drawindexed values plus draw occurrence") from exc
    if (line_no < 1 or not section or occurrence_ordinal < 0
            or not occurrence_section.strip()
            or occurrence_section.casefold() != section.casefold()):
        raise te.ToggleEditError(
            "Record targets must include a positive source line, matching "
            "section, and non-negative draw occurrence")
    if target_ini is not None and _ini_identity(ini) != _ini_identity(target_ini):
        raise te.ToggleEditError(
            f"Record target ini {ini!r} does not match the edited ini "
            f"{target_ini!r}")
    return line_no, (section.casefold(), *triple, occurrence_ordinal)


def _target_path(ref):
    return ref["occurrence"].get("path") or []


def _resolve_target_refs(doc, target_lines, target_ini=None):
    """Resolve submitted targets by stable identity, refusing missing or ambiguous draws.

    Payload line numbers can become stale after edits; silently skipping a
    target could record visibility against the wrong draw.
    """
    resolved = {}
    used_current = {}
    seen_submitted = {}
    lines_by_identity = {}
    for line in doc.lines:
        identity = _draw_identity(doc, line.no)
        if identity is not None:
            lines_by_identity.setdefault(identity, []).append(line.no)
    for raw_ref in target_lines or []:
        submitted_line, expected = _target_identity(raw_ref, target_ini)
        path = _target_path(raw_ref)
        previous_submission = seen_submitted.get(submitted_line)
        if previous_submission is not None:
            if (previous_submission["expected"] != expected
                    or any(previous_path == path
                           for previous_path in previous_submission["paths"])):
                raise te.ToggleEditError(
                    f"Record target line {submitted_line} was submitted more than once")

        current_line = submitted_line - 1
        if _draw_identity(doc, current_line) == expected:
            resolved_line = current_line
        else:
            candidates = lines_by_identity.get(expected, [])
            if len(candidates) != 1:
                if not candidates:
                    detail = "the expected draw is not present in the staged ini"
                else:
                    detail = ("the expected draw appears at multiple staged lines: "
                              + ", ".join(str(no + 1) for no in candidates))
                raise te.ToggleEditError(
                    f"Record target line {submitted_line} is stale: expected "
                    f"[{expected[0]}] drawindexed = {expected[1]}, {expected[2]}, "
                    f"{expected[3]} (occurrence {expected[4]}); {detail}")
            resolved_line = candidates[0]

        previous = used_current.get(resolved_line)
        if (previous is not None
                and previous["submitted"] != submitted_line):
            raise te.ToggleEditError(
                f"Record targets {previous['submitted']} and {submitted_line} resolve to the "
                "same staged draw")
        resolved[submitted_line] = resolved_line
        if previous is None:
            used_current[resolved_line] = {"submitted": submitted_line}
        if previous_submission is None:
            seen_submitted[submitted_line] = {
                "expected": expected, "paths": [path],
            }
        else:
            previous_submission["paths"].append(path)
    return resolved


def resolve_draw_references(doc, target_lines, target_ini=None):
    """Public stable authored-draw resolver shared by mesh editing."""
    return _resolve_target_refs(doc, target_lines, target_ini)


def _dnf_satisfied(conds, bindings):
    """True if a DNF condition list (conds; [] means unconditional) is
    satisfied given `bindings` ({var: value string})."""
    if conds == []:
        return True
    return any(all((bindings.get(c["var"]) == c["value"]) != c["negate"] for c in group)
               for group in conds)


def verify_recording(path, report, text=None, document=None):
    """Reparse the current document and return mismatches against recorded visibility.

    Prefer the authoritative staged document or lossless text; otherwise load
    the saved document from `path`.
    """
    verify = report.get("verify") or {}
    if not verify:
        return []

    try:
        if document is None:
            document = (IniDocument.from_string(text, path=path)
                        if text is not None else IniDocument.load(path))
        sections = sections_from_document(document)
        draw_info = _scan_sections_for_draws(sections)
    except Exception as e:
        return [{"var": None, "reason": f"file failed to re-parse after saving: {e!r}"}]

    conds_by_draw = {}
    for section_name, info in draw_info.items():
        for draw in info["draws"]:
            key = (section_name, draw.count, draw.start, draw.base)
            conds_by_draw[key] = draw.conditions

    mismatches = []
    for var, spec in verify.items():
        values = spec["values"]
        for d in spec["draws"]:
            key = (d["section"], d["count"], d["start"], d["base"])
            conds = conds_by_draw.get(key)
            if conds is None:
                mismatches.append({
                    "var": var, "section": d["section"],
                    "draw": [d["count"], d["start"], d["base"]],
                    "reason": "this draw no longer appears in its section after saving"})
                continue
            expected = set(d["positions"])
            for p in range(len(values)):
                actual = _dnf_satisfied(
                    conds, {var: _cycle_value(values, p)})
                if (p in expected) != actual:
                    mismatches.append({
                        "var": var, "section": d["section"],
                        "draw": [d["count"], d["start"], d["base"]], "position": p,
                        "expected": p in expected, "actual": actual})
    return mismatches

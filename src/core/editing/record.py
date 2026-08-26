"""Record mode: assign which meshes are visible at each cycle position of a
`[Key...]` toggle, and rewrite the ini's `if`/`elif`/`endif` gates to match.

A cycle toggle steps through positions 0..N-1; at each position every var the
section cycles uses its value at that row. If its list is shorter than the
section's longest list, 3Dmigoto keeps its final value for the remaining rows.
The caller
(the frontend, which already has the mesh payload's `conditions`) decides,
per position, which drawindexed *lines* should be visible -- this module
only has to make the ini agree.

This is deliberately conservative: only a well-defined "safe pattern" is
rewritten automatically; everything else is refused and reported rather than
guessed at. A chain is safe to regenerate only if:

    - it has no `else` branch (ambiguous to invert without full DNF work);
    - every branch's condition reads exactly `{var}` and nothing else;
    - no branch contains a nested `if` of its own;
    - every non-blank/comment line in every branch is a `drawindexed` line
      with recorded data for it;
    - `IniDocument.is_safe_to_rewrite` agrees the section's nesting isn't
      already ambiguous.

A safe chain is regenerated wholesale (its whole `if`..`endif` span replaced
in one splice) rather than edited branch-by-branch, since `elif` branches
are mutually exclusive and evaluated in order -- surgically OR-ing a value
into one branch can silently do nothing if an earlier sibling already
claims it.

A drawindexed line whose entire ancestor chain never references this var at
all gets a brand-new private `if <expr> ... endif` nested directly around
just that line -- this is what lets a previously-unrelated or freshly-added
mesh become gated by a toggle for the first time. If the var is referenced
only through some outer (non-immediate) ancestor, this module declines to
guess and reports it instead.
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
    """Internal signal: this chain/line can't be safely recorded. Always
    caught within this module and turned into a `skipped` report entry —
    never propagated to callers."""


def _split_cond(line):
    m = _COND_RE.match(line.text)
    return (m.group(1), m.group(2).strip()) if m else None


def _refs(doc, line, var):
    """True if a branch-open line's own condition reads `var`. An `else` has
    no condition of its own, but is implicitly gated by the negation of every
    earlier sibling in its own chain — so treat it as referencing `var`
    whenever any if/elif before it in that chain does.

    Without this, a value recorded against a line inside an `else` branch
    would fall through to the "bare line" path and get wrapped in a
    brand-new `if $var == ...`, nested *inside* an else whose entry condition
    already implies `$var` took some other value — producing an unreachable,
    permanently-hidden line instead of the refusal this case actually needs
    (the whole chain, else included, must be rewritten atomically or not at
    all; see `_chain_of`'s own else check)."""
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
        return "0"   # permanently false — mirrors toggle_editor's own convention
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
            new_lines.extend(l.raw for l in lines)
        else:
            new_lines.append(
                f"if {_or_expr(var, values, pos_set, all_positions)}")
            new_lines.extend(l.raw for l in lines)
            new_lines.append("endif")

    start, end = branches[0].no, endif_line.no + 1
    original = [doc.lines[no].raw for no in range(start, end)]
    if new_lines == original:
        return None
    return start, end, new_lines


def _analyze_var(doc, var, values, desired, report, unsafe_sections,
                 max_positions):
    """Everything this var's recorded data implies, without mutating `doc`.

    Returns (chain_edits, bare_edits, verified):
      chain_edits  [(start, end_exclusive, new_lines), ...] -- whole if/elif/
                   endif spans to regenerate.
      bare_edits   {line_no: expr} -- single lines with no var-referencing
                   ancestor at all, needing a brand-new private wrap.
      verified     {line_no: [position, ...]} -- lines whose desired
                   visibility is provably driven by this var alone (no outer
                   ancestor of its own), for record_toggle's post-save
                   self-check (see verify_recording). A chain or bare wrap
                   nested inside some other untouched ancestor condition
                   ends up gated by `ancestor AND our_expr`, not var alone,
                   so it's excluded here even though the rewrite itself is
                   still correct.

    Anything refused is appended to `report["skipped"]`.
    """
    all_positions = frozenset(range(max_positions))
    chain_leaders = {}     # leader.no -> Line
    leader_lines = {}      # leader.no -> [desired line_no, ...]
    bare_targets = []      # Lines with no ancestor referencing var
    nested_bare = set()    # subset of bare_targets' line_no's that still sit
                            # inside some OTHER, unrelated ancestor condition
                            # (so their final gating isn't var-alone-clean)

    for line_no in sorted(desired):
        line = doc.lines[line_no]
        if line.section is not None and line.section.name.lower() in unsafe_sections:
            report["skipped"].append({
                "var": var, "line": line.no + 1,
                "reason": "this section's if/elif/endif nesting is ambiguous "
                          "(see IniDocument.structure_errors); edit the ini directly"})
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
                report["skipped"].append({"var": var, "line": line.no + 1, "reason": str(e)})
                continue
            chain_leaders[leader.no] = leader
            leader_lines.setdefault(leader.no, []).append(line_no)
            continue

        if any(_refs(doc, a, var) for a in ancestors[1:]):
            report["skipped"].append({
                "var": var, "line": line.no + 1,
                "reason": "gated by this variable at an outer nesting level; "
                          "edit the ini directly"})
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
            report["skipped"].append({"var": var, "line": leader.no + 1, "reason": str(e)})
            continue
        if edit is not None:
            chain_edits.append(edit)
        # A chain nested inside some OUTER, untouched ancestor (whatever it
        # references) ends up gated by that outer condition AND this
        # chain's own regenerated expression -- not var alone -- so only a
        # top-level chain (no ancestors of its own) is safe to verify below.
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
                report["skipped"].append({
                    "var": var, "line": line.no + 1, "reason": str(exc)})

    return chain_edits, bare_edits, verified


def writable_cycle_vars(doc, section_name):
    """(writable, max_positions) for a cycle section: the subset of its vars
    this module can actually rewrite, and how many positions a caller must
    supply data for.

    Namespaced/master vars are cross-ini and read-only, so they're excluded
    from the returned write set. They still participate in the section's
    cycle, however, so `max_positions` covers every co-driven variable. The
    recorder must preview that complete tuple while only rewriting locals.

    Raises ToggleEditError if the section isn't a cycle toggle with at least
    one writable variable.
    """
    sec = te.find_cycle_section(doc, section_name)
    cvars = te.cycle_vars(sec, include_read_only=True)
    writable = {v: vals for v, vals in cvars.items() if not ic.is_namespaced(v)}
    if not writable:
        raise te.ToggleEditError(
            f"{section_name!r} is not a cycle toggle with any writable variable")
    return writable, max(len(v) for v in cvars.values())


def record_toggle(doc, section_name, position_lines):
    """Rewrite `doc`'s gates so every position's recorded set of drawindexed
    lines matches what's actually visible.

    `position_lines`: {position (0-based, possibly a JSON string key): [ini
    line number (1-based), ...]}. Every reachable position should be
    present, even one that just repeats what's already on disk -- a
    position missing from the input is indistinguishable from "not visible
    there". See `writable_cycle_vars` for how many positions that is.

    Returns {"vars_updated": [...], "chains_rewritten": N, "wraps_added": N,
    "skipped": [{"var", "line", "reason"}, ...], "verify": {var: {"values":
    [...], "draws": [{"section", "count", "start", "base", "positions":
    [...]}, ...]}}}. "verify" is the post-save self-check's ground truth
    (see verify_recording), keyed by each draw's own (section, count,
    start, base) identity rather than line number, since a chain
    regeneration can shift line numbers.
    """
    writable, max_positions = writable_cycle_vars(doc, section_name)
    got = {int(pos) for pos in (position_lines or {})}
    if got != set(range(max_positions)):
        raise te.ToggleEditError(
            f"expected recorded data for positions 0..{max_positions - 1}, got {sorted(got)}")

    desired = {}   # 0-based line no -> set of 0-based positions
    for pos, line_nos in (position_lines or {}).items():
        for ln in line_nos or []:
            desired.setdefault(int(ln) - 1, set()).add(int(pos))

    report = {"vars_updated": [], "chains_rewritten": 0, "wraps_added": 0, "skipped": []}

    # A line the caller claims is drawindexed but the document disagrees
    # (stale payload, section edited on disk since it was loaded, or simply
    # out of range) is dropped once here — every var's analysis can then
    # assume every line in `desired` genuinely is an in-range DRAW line.
    for no in [n for n in desired if n < 0 or n >= len(doc.lines) or doc.lines[n].kind != DRAW]:
        report["skipped"].append({
            "var": None, "line": no + 1,
            "reason": "not a drawindexed line in the current ini (stale data?)"})
        del desired[no]

    unsafe_sections = {p["section"].lower() for p in doc.structure_errors() if p["section"]}

    all_chain_edits = []
    all_bare_claims = {}   # line_no -> [(var, expr), ...]
    per_var_verify = {}    # var -> (values, {line_no: [position, ...]})
    for var, values in writable.items():
        chain_edits, bare_edits, verified = _analyze_var(
            doc, var, values, desired, report, unsafe_sections, max_positions)
        all_chain_edits.extend(chain_edits)
        for line_no, expr in bare_edits.items():
            all_bare_claims.setdefault(line_no, []).append((var, expr))
        per_var_verify[var] = (values, verified)
        report["vars_updated"].append(var)

    final_edits = list(all_chain_edits)
    report["chains_rewritten"] = len(all_chain_edits)
    # (var, line_no) pairs whose *bare-wrap* claim was refused here — scoped
    # per-var (not a flat set of lines) because a refused claim from one var
    # must never disqualify a *different* var's own, separately-successful
    # chain rewrite of that same physical line (see OVERLAP-shaped fixtures:
    # $upper's chain genuinely gets regenerated while $tt's bare-wrap attempt
    # on the very same lines is refused for overlapping it).
    refused = set()
    for line_no, claims in all_bare_claims.items():
        if len(claims) > 1:
            report["skipped"].append({
                "var": "/".join(v for v, _ in claims), "line": line_no + 1,
                "reason": "targeted by more than one variable in this recording session"})
            refused.update((v, line_no) for v, _ in claims)
            continue
        var, expr = claims[0]
        # A bare line for this var can still sit inside a *different* var's
        # chain that's being regenerated in the same pass (legitimate nested
        # multi-var gating) — that chain edit's span was already computed
        # against the untouched document, so splicing both would corrupt it.
        # Leave the chain edit alone and refuse only this narrower wrap.
        if any(start <= line_no < end for start, end, _ in all_chain_edits):
            report["skipped"].append({
                "var": var, "line": line_no + 1,
                "reason": "sits inside another variable's gate being rewritten in this "
                          "same save; edit the ini directly to nest this condition"})
            refused.add((var, line_no))
            continue
        line = doc.lines[line_no]
        final_edits.append((line.no, line.no + 1, [f"if {expr}", line.raw, "endif"]))
        report["wraps_added"] += 1

    # Every (var, line) whose desired visibility genuinely ended up encoded
    # above — i.e. still present once anything refused (inside _analyze_var,
    # or in the bare-claim pass just above) is excluded — is fair game for
    # verify_recording's post-save self-check. Captured as each draw's own
    # (section, count, start, base) identity, *before* final_edits below can
    # shift anything, since that identity (unlike a line number) survives a
    # rewrite and is what a fresh re-parse can still find the draw by.
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

    # Bottom-to-top: every edit here only ever shifts lines *after* it, so
    # applying the lowest (latest) edit first keeps every not-yet-applied
    # edit's line numbers valid. No two edit spans can overlap: two chains
    # can't nest or interleave (a safe chain's body may not contain a nested
    # if), and a bare wrap that would land inside a chain edit was just
    # refused above.
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
    """(section_name, count, start, base) identity for a DRAW line — stable
    across a rewrite even when regenerating a chain shifts the line number
    itself (unlike the line number, the drawindexed args never change).
    None if the line isn't a recognizable drawindexed line inside a section.
    """
    line = doc.lines[line_no]
    if line.section is None:
        return None
    m = _DRAW_RE.search(line.text)
    if not m:
        return None
    return (line.section.name, int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _dnf_satisfied(conds, bindings):
    """True if a DNF condition list (conds; [] means unconditional) is
    satisfied given `bindings` ({var: value string})."""
    if conds == []:
        return True
    return any(all((bindings.get(c["var"]) == c["value"]) != c["negate"] for c in group)
               for group in conds)


def verify_recording(path, report, text=None, document=None):
    """Project the current document and confirm it shows the gating
    recorded in `report["verify"]`. Returns a list of mismatch dicts, empty
    if every recorded draw's freshly re-parsed gating matches.

    `document`, when supplied, is the authoritative staged document. The
    `text` fallback is converted to an ``IniDocument`` rather than sent
    through the lossy text parser; without either, the saved document is
    loaded losslessly from `path`.
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

"""Add/edit/delete 3DMigoto toggles in place on a single ini file.

A "toggle" is three coupled pieces, edited together so the UI's "one
toggle" never drifts from what's on disk:

    1. `[KeyFoo]` -- the cycle section (`key = ...`, `type = cycle`, `$var = 0,1,2`)
    2. `$var` declaration in `[Constants]` (`global persist $var = 0`)
    3. `if $var == N ... endif` gates around `drawindexed` lines elsewhere

Every function here takes an already-loaded IniDocument and mutates it in
memory; callers call `.save()` themselves, which keeps this testable
without touching disk.

A brand-new `[KeyFoo]` section also gets a `condition = ...` line so its key
binding is inert until the object it belongs to is on screen -- real
WWMI/GIMI mods hand-author exactly this pattern. See
`_existing_detection_var`/`_mark_active_in_overrides` for how the detection
var (`$object_detected` or `$active`) is chosen or built from scratch.

Deleting a toggle never restructures if/elif/endif chains or removes a
`drawindexed` line -- it only rewrites the *condition text* of lines that
reference the deleted variable, via ic.eliminate(). A branch whose condition
collapses entirely becomes `1` (always taken) or `0` (never taken), per
ordinary De Morgan logic (`if !$v` -> `0`, plain `if $v` -> `1`); both are
reported back from delete_toggle() since a now-unreachable branch is worth a
UI warning.
"""

import re

from . import ini_condition as ic
from .ini_document import IF, ELIF, ELSE, ENDIF, ASSIGN, BLANK

_VAR_ASSIGN_RE = re.compile(r"^\$(\w+)\s*=\s*(.+)$")
_ALL_VAR_ASSIGN_RE = re.compile(r"^\$([\\\w]+)\s*=\s*(.+)$")
_CONST_VAR_RE = re.compile(r"^(?:global\s+)?(?:persist\s+)?\$(\w+)\s*=\s*([^,]+)$", re.I)
_COND_LINE_RE = re.compile(r"^(if|else\s+if|elif)\s+(.*)$", re.I)

# The two on-screen-detection variable names real WWMI/GIMI mods use to gate
# a Key section's `condition = ...` (see add_toggle). `_OBJECT_DETECTED_VAR`
# takes priority when both happen to be declared, since it's the newer WWMI
# exporter's own name for the same concept `_ACTIVE_VAR` represents.
_OBJECT_DETECTED_VAR = "object_detected"
_ACTIVE_VAR = "active"


class ToggleEditError(ValueError):
    """A CRUD operation could not be performed; the document is untouched."""


# ── shared helpers ───────────────────────────────────────────────────────────

def _norm_section_name(name):
    name = name.strip()
    if not name:
        raise ToggleEditError("a toggle name is required")
    return name if name[:3].lower() == "key" else f"Key{name}"


def find_cycle_section(doc, section_name):
    sec = doc.section(section_name)
    if sec is None:
        raise ToggleEditError(f"no section named {section_name!r}")
    return sec


def is_cycle_section(sec):
    return any(re.match(r"^type\s*=\s*cycle$", line.text, re.I) for line in sec.lines)


def cycle_vars(sec, include_read_only=False):
    """Return cycle values, optionally including cross-file variables."""
    out = {}
    for line in sec.lines:
        if line.kind != ASSIGN:
            continue
        m = _ALL_VAR_ASSIGN_RE.match(line.text)
        if not m:
            continue
        variable = m.group(1)
        if ic.is_namespaced(variable) and not include_read_only:
            continue
        values = [p.strip() for p in m.group(2).split(",") if p.strip()]
        if values:
            out[variable] = values
    return out


def list_cycle_toggles(doc):
    """[(section_name, {var: [values]})] for every cycle-type Key section."""
    out = []
    for sec in doc.sections:
        if not sec.name.lower().startswith("key"):
            continue
        if not is_cycle_section(sec):
            continue
        out.append((sec.name, cycle_vars(sec)))
    return out


def _var_declared_elsewhere(doc, var, exclude_section):
    """True if some other cycle Key section in this document still drives `var`."""
    for name, cvars in list_cycle_toggles(doc):
        if name != exclude_section and var in cvars:
            return True
    return False


def _find_key_lines(sec):
    """Every `key = …` line in a section, in file order (3DMigoto allows more
    than one, binding alternate combos to the same cycle)."""
    out = []
    for line in sec.lines:
        if line.kind != ASSIGN:
            continue
        k, _, v = line.text.partition("=")
        if k.strip().lower() == "key":
            out.append((line, v.strip()))
    return out


# ── add ──────────────────────────────────────────────────────────────────────

def add_toggle(doc, name, key_combo, var, values, default=None, back_combo=None):
    """Create a new cycle toggle: a `[Key<name>]` section plus a `$var`
    declaration in `[Constants]` (added only if not already declared).

    A fresh toggle gates nothing by itself -- Record mode is what assigns
    meshes to its values. The new section also gets a `condition = ...`
    line so its key binding is inert until the object it belongs to is on
    screen (see module docstring for the priority order and what gets
    built if this ini has neither `$object_detected` nor `$active` yet).
    The new section is inserted right after `[Constants]`, not at the end
    of the file. Returns the new section's name.
    """
    section_name = _norm_section_name(name)
    if doc.section(section_name) is not None:
        raise ToggleEditError(f"a section named {section_name!r} already exists")

    var = var.strip().lstrip("$")
    if not re.fullmatch(r"\w+", var):
        raise ToggleEditError(f"invalid variable name {var!r}")
    if ic.is_namespaced(var):
        raise ToggleEditError("cannot create a namespaced (cross-file) variable")
    if var in (_OBJECT_DETECTED_VAR, _ACTIVE_VAR):
        raise ToggleEditError(
            f"${var} is reserved for this ini's on-screen detection variable; "
            f"choose another name")
    if _var_declared_elsewhere(doc, var, exclude_section=None) or _constant_line(doc, var):
        raise ToggleEditError(f"${var} is already used by this ini; choose another name")

    values = [str(v).strip() for v in values]
    if any(not v for v in values):
        raise ToggleEditError("cycle values must not be blank")
    if len(values) < 2:
        raise ToggleEditError("a cycle toggle needs at least two values")
    if len(set(values)) != len(values):
        raise ToggleEditError("cycle values must be unique")
    if not (key_combo or "").strip():
        raise ToggleEditError("a key binding is required")

    # Which existing detection var (if any) this ini already uses -- computed
    # before this toggle's own var is declared, so it can't be confused with it.
    detection_var = _existing_detection_var(doc)

    _ensure_var_declared(doc, var, values[0] if default is None else str(default))

    just_built_active = detection_var is None
    if just_built_active:
        # Neither $object_detected nor $active exists yet -- build $active's
        # plumbing from scratch (see module docstring).
        detection_var = _ACTIVE_VAR
        _ensure_var_declared(doc, _ACTIVE_VAR, "0", persist=False)
        _ensure_present_reset(doc, _ACTIVE_VAR)
        _mark_active_in_overrides(doc, _ACTIVE_VAR)

    body = [f"[{section_name}]", f"condition = ${detection_var} == 1",
            f"key = {key_combo.strip()}"]
    if back_combo and back_combo.strip():
        body.append(f"back = {back_combo.strip()}")
    body += ["type = cycle", f"${var} = {','.join(values)}"]

    # Constants always exists by now (_ensure_var_declared creates it if
    # missing), so the new section lands right after it -- and after the new
    # [Present] section too, when this call just built it right there.
    at = doc.section("Present").end if just_built_active else doc.section("Constants").end
    if at > 0 and doc.lines[at - 1].kind != BLANK:
        body = [""] + body
    if at < len(doc.lines):
        body = body + [""]   # separate from whatever section follows
    doc.insert_lines(at, body)
    return section_name


def _constant_line(doc, var):
    const = doc.section("Constants")
    if const is None:
        return None
    for line in const.lines:
        m = _CONST_VAR_RE.match(line.text)
        if m and m.group(1) == var:
            return line
    return None


def _existing_detection_var(doc):
    """`$object_detected` or `$active`, whichever this ini's [Constants]
    already declares (`$object_detected` wins if both are). None if neither
    is declared yet."""
    if _constant_line(doc, _OBJECT_DETECTED_VAR) is not None:
        return _OBJECT_DETECTED_VAR
    if _constant_line(doc, _ACTIVE_VAR) is not None:
        return _ACTIVE_VAR
    return None


def _append_after_last_content_line(doc, sec, text):
    """Insert `text` as a new line at the end of `sec`'s body, after its last
    non-blank line — not at `sec.end`, which would land after any trailing
    blank lines and butt straight up against the next section header."""
    at = sec.header_no + 1
    for line in sec.lines:
        if line.kind != BLANK:
            at = line.no + 1
    doc.insert_lines(at, [text])


def _ensure_var_declared(doc, var, default_value, persist=True):
    const = doc.section("Constants")
    prefix = "global persist" if persist else "global"
    if const is not None:
        if _constant_line(doc, var) is None:
            _append_after_last_content_line(doc, const, f"{prefix} ${var} = {default_value}")
        return
    # No [Constants] section at all (rare, but small inis sometimes lack one).
    doc.insert_lines(0, ["[Constants]", f"{prefix} ${var} = {default_value}", ""])


def _ensure_present_reset(doc, var):
    """Add `post $var = 0` to `[Present]` (creating the section right after
    [Constants], if it doesn't exist yet).

    `post` defers the assignment until after the rest of this frame's
    Present command list has run, so anything reading `$var` earlier in the
    same block still sees this frame's value; only the *next* frame starts
    from 0 unless a TextureOverride section (_mark_active_in_overrides)
    sets it back to 1 first."""
    sec = doc.section("Present")
    if sec is None:
        const = doc.section("Constants")
        at = const.end if const is not None else len(doc.lines)
        body = ["[Present]", f"post ${var} = 0"]
        if at > 0 and doc.lines[at - 1].kind != BLANK:
            body = [""] + body
        if at < len(doc.lines):
            body = body + [""]   # separate from whatever section follows
        doc.insert_lines(at, body)
        return
    _append_after_last_content_line(doc, sec, f"post ${var} = 0")


def _texture_override_sections(doc):
    """Every `[TextureOverride*]` section, in file order."""
    return [sec for sec in doc.sections if sec.name.startswith("TextureOverride")]


def _mark_active_in_overrides(doc, var):
    """Insert `$var = 1` into the first (and second, if present)
    `[TextureOverride*]` section -- the hand-authored convention real
    WWMI/GIMI mods use to flip the detection flag from inside a section
    3DMigoto only invokes while this mod's geometry is being drawn. Landed
    after the section's leading plain assignments and before its first
    nested if/elif/else/endif block, so it fires regardless of which inner
    branch a given draw call takes."""
    sections = _texture_override_sections(doc)[:2]
    # Bottom-up: inserting into a later section never shifts line numbers
    # for sections above it.
    for sec in reversed(sections):
        at = sec.header_no + 1
        for line in sec.lines:
            if line.kind in (IF, ELIF, ELSE, ENDIF):
                break
            if line.kind != BLANK:
                at = line.no + 1
        doc.insert_lines(at, [f"${var} = 1"])


# ── edit ─────────────────────────────────────────────────────────────────────

def value_conflicts(doc, var, new_values):
    """{removed_value: [(section, line_no1based), ...]} for every value that
    would disappear from `var`'s cycle list and is still tested by some
    `if`/`elif` in this file."""
    new_set = set(str(v).strip() for v in new_values)
    hits = {}
    for line in doc.lines:
        if line.kind not in (IF, ELIF):
            continue
        m = _COND_LINE_RE.match(line.text)
        if not m:
            continue
        try:
            node = ic.parse(m.group(2).strip())
        except ic.ConditionError:
            continue
        for op, value in ic.find_comparisons(node, var):
            if op in ("==", "===") and value not in new_set:
                hits.setdefault(value, []).append(
                    (line.section.name if line.section else None, line.no + 1))
    return hits


def edit_toggle(doc, section_name, *, new_name=None, key_combo=None,
                 back_combo=None, var_values=None, allow_value_conflicts=False):
    """Edit fields of an existing cycle toggle in place.

    `var_values` is an optional {var: [new values...]}; only vars present in
    it are changed, replacing each one's cycle list wholesale. Shrinking a
    cycle so a still-gated value disappears raises ToggleEditError (with the
    conflicts) unless `allow_value_conflicts=True` -- resolving what happens
    to that value's meshes is the caller's job (record mode owns
    reassignment). If the var's `[Constants]` default is a value this edit
    removes, it's rewritten to the new first value instead of being left
    pointing at a value the cycle no longer has.

    All fields are validated up front, before any line is touched, so a
    ToggleEditError always leaves the document exactly as it was.

    Returns the (possibly renamed) section name.
    """
    sec = find_cycle_section(doc, section_name)
    existing_vars = cycle_vars(sec)

    # -- validate everything first; no mutation happens above this line ----
    target_name = section_name
    if new_name is not None:
        target_name = _norm_section_name(new_name)
        if target_name.lower() != section_name.lower() and doc.section(target_name) is not None:
            raise ToggleEditError(f"a section named {target_name!r} already exists")

    if key_combo is not None and not key_combo.strip():
        raise ToggleEditError("a key binding is required")

    if var_values:
        conflicts = {}
        for var, new_vals in var_values.items():
            if var not in existing_vars:
                raise ToggleEditError(f"{section_name!r} does not cycle ${var}")
            if len(set(str(v).strip() for v in new_vals)) < 2:
                raise ToggleEditError(f"${var} needs at least two distinct values")
            hits = value_conflicts(doc, var, new_vals)
            if hits:
                conflicts[var] = hits
        if conflicts and not allow_value_conflicts:
            raise ToggleEditError(
                "removing these values would orphan existing gates: "
                + "; ".join(f"${v}={list(h)}" for v, h in conflicts.items()))

    # -- apply: every check above already passed -----------------------------
    if new_name is not None and target_name != section_name:
        header = sec.header_no
        doc.replace_lines(header, header + 1, [f"[{target_name}]"])
        sec = find_cycle_section(doc, target_name)
        section_name = target_name

    if key_combo is not None:
        key_lines = _find_key_lines(sec)
        if key_lines:
            # 3DMigoto tolerates several `key =` bindings on one section;
            # extract_toggle_keys() takes the *last* one as the display
            # value, so that's the one edit_toggle updates to match the UI.
            line, _ = key_lines[-1]
            doc.replace_lines(line.no, line.no + 1, [f"key = {key_combo.strip()}"])
        else:
            doc.insert_lines(sec.header_no + 1, [f"key = {key_combo.strip()}"])
            sec = find_cycle_section(doc, section_name)

    if back_combo is not None:
        back_line = next((l for l in sec.lines
                          if l.kind == ASSIGN
                          and l.text.partition("=")[0].strip().lower() == "back"), None)
        if back_line:
            doc.replace_lines(back_line.no, back_line.no + 1, [f"back = {back_combo.strip()}"])
        elif back_combo.strip():
            doc.insert_lines(sec.header_no + 1, [f"back = {back_combo.strip()}"])
            sec = find_cycle_section(doc, section_name)

    if var_values:
        for var, new_vals in var_values.items():
            sec = find_cycle_section(doc, section_name)
            line = next(l for l in sec.lines
                        if l.kind == ASSIGN and _VAR_ASSIGN_RE.match(l.text)
                        and _VAR_ASSIGN_RE.match(l.text).group(1) == var)
            values = [str(v).strip() for v in new_vals]
            doc.replace_lines(line.no, line.no + 1, [f"${var} = {','.join(values)}"])

            # If the value the Constants declaration currently defaults to
            # was itself removed by this edit, it would otherwise be left
            # pointing at a value the cycle no longer has — fall back to the
            # new first value, same as a brand-new toggle's default.
            const_line = _constant_line(doc, var)
            if const_line is not None:
                m = _CONST_VAR_RE.match(const_line.text)
                if m and m.group(2).strip() not in values:
                    prefix = const_line.text[:m.start(2)]
                    doc.replace_lines(const_line.no, const_line.no + 1,
                                      [f"{prefix}{values[0]}"])

    return section_name


# ── delete ───────────────────────────────────────────────────────────────────

def _split_condition_line(line):
    m = _COND_LINE_RE.match(line.text)
    return (m.group(1), m.group(2).strip()) if m else None


def _rebuild_condition_line(line, keyword, new_expr):
    """Reassemble a rewritten if/elif line, preserving indentation and any
    inline comment exactly as the original had it."""
    indent = line.raw[:len(line.raw) - len(line.raw.lstrip())]
    stripped = line.raw.strip()
    comment = " " + stripped[stripped.index(";"):] if ";" in stripped else ""
    return f"{indent}{keyword} {new_expr}{comment}"


def _strip_vars_from_gates(doc, dead_vars):
    """Rewrite every if/elif condition in the document that references any of
    `dead_vars`, via ic.eliminate(). Never touches branch structure or line
    count -- see module docstring.

    Skips any section IniDocument.structure_errors() flags as having
    ambiguous if/elif/endif nesting: with a stray or missing endif, which
    branch a gate actually controls is already unclear, so rewriting one
    there risks guessing wrong. Left untouched and reported instead.

    Targets are collected in a single upfront pass rather than rescanned
    from scratch after each rewrite: replacing one line with exactly one
    line never shifts any other line's index, and eliminate() fully resolves
    a condition's dead-var references in one call, so no line needs
    revisiting. (A prior rescan-every-time version was quadratic.)

    Returns a dict:
        rewritten     total lines rewritten (to `1`, `0`, or a simplified
                      expression with the dead var's clauses removed)
        always_false  [(section, line_no_1based), ...] rewritten to the
                      literal `0` -- these branches are now permanently
                      unreachable (see ic.eliminate() for why this can
                      legitimately happen, e.g. `if !$v`), worth a UI warning.
        always_true   [(section, line_no_1based), ...] rewritten to the
                      literal `1` -- these branches (and whatever they draw)
                      are now permanently shown instead of only for one cycle
                      value, equally worth flagging to the user.
        unsafe        [(section, line_no_1based), ...] left untouched because
                      their section's nesting was ambiguous.
    """
    targets = []
    unsafe = []
    for line in doc.lines:
        if line.kind not in (IF, ELIF):
            continue
        split = _split_condition_line(line)
        if not split or not any(ic.references(split[1], v) for v in dead_vars):
            continue
        sec = line.section
        if sec is not None and not doc.is_safe_to_rewrite(sec.name):
            unsafe.append((sec.name if sec else None, line.no + 1))
            continue
        targets.append(line.no)

    changed = 0
    always_false = []
    always_true = []
    for no in targets:
        target = doc.lines[no]   # re-fetch: _reindex() replaces Line objects on every splice
        keyword, expr = _split_condition_line(target)
        node = ic.parse(expr)   # already proven parseable by references() above
        reduced = ic.eliminate(node, dead_vars)
        if reduced is ic.TRUE:
            new_expr = "1"
        elif reduced is ic.FALSE:
            # A real, expected outcome (not just a defensive fallback) — see
            # ic.eliminate()'s docstring: a negated dead-var clause such as
            # `!$v` inverts to permanently false, retiring this branch for
            # good in favour of whichever sibling now always wins instead.
            new_expr = "0"
        else:
            new_expr = ic.render(reduced)

        doc.replace_lines(target.no, target.no + 1,
                          [_rebuild_condition_line(target, keyword, new_expr)])
        changed += 1
        if new_expr == "0":
            always_false.append((target.section.name if target.section else None,
                                 target.no + 1))
        elif new_expr == "1":
            always_true.append((target.section.name if target.section else None,
                                target.no + 1))

    return {"rewritten": changed, "always_false": always_false,
            "always_true": always_true, "unsafe": unsafe}


def delete_toggle(doc, section_name):
    """Remove a cycle toggle entirely: its `[Key...]` section, its `$var`
    declaration(s) in `[Constants]` (unless another Key section in this same
    file still cycles them), and every reference to those vars in the file's
    `if`/`elif` gates -- via ic.eliminate() rather than restructured away.

    Namespaced (cross-file) variables are never touched even if declared
    here, since they belong to another file and are read-only.

    Returns {"section": name, "vars_removed": [...], "gates_rewritten": N,
    "always_false_gates": [(section, line_no_1based), ...],
    "always_true_gates": [(section, line_no_1based), ...],
    "unsafe_gates": [(section, line_no_1based), ...]}.

    `always_false_gates` lists branches that became permanently unreachable
    (e.g. a `!$v` gate) -- the mesh(es) they used to draw are now hidden for
    good, the one outcome of a delete worth surfacing to the user.

    `always_true_gates` lists branches that became permanently reachable
    (e.g. a plain `$v` gate) -- the mesh(es) they draw are now always shown
    instead of only for one cycle value, equally worth surfacing.

    `unsafe_gates` lists leftover references to a removed var that couldn't
    be rewritten because their section's if/elif/endif nesting is ambiguous;
    those still declare `$var` nowhere, so 3DMigoto treats it as always 0,
    but the caller/UI should warn rather than silently leave it dangling.
    """
    sec = find_cycle_section(doc, section_name)
    my_vars = [v for v in cycle_vars(sec) if not ic.is_namespaced(v)]

    doc.delete_lines(sec.start, sec.end)

    # Decide up front which vars are truly orphaned (some multi-var sections
    # share a var with another Key section, per the corpus survey), then fold
    # them all away in one pass — a condition naming two dead vars from the
    # same deleted section then only needs a single rewrite, not one per var.
    vars_removed = [v for v in my_vars
                    if not _var_declared_elsewhere(doc, v, exclude_section=section_name)]
    gate_report = _strip_vars_from_gates(doc, vars_removed) if vars_removed else \
        {"rewritten": 0, "always_false": [], "always_true": [], "unsafe": []}

    for var in vars_removed:
        line = _constant_line(doc, var)
        if line is not None:
            doc.delete_lines(line.no, line.no + 1)

    return {"section": section_name, "vars_removed": vars_removed,
            "gates_rewritten": gate_report["rewritten"],
            "always_false_gates": gate_report["always_false"],
            "always_true_gates": gate_report["always_true"],
            "unsafe_gates": gate_report["unsafe"]}

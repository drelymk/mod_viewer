"""Create, edit, and delete cycle toggles in an in-memory ``IniDocument``.

Callers own transaction and export; deletion opens every affected branch.
"""

import re

from ..ini import condition as ic
from .cleanup import DeletionCleanup
from ..ini.document import (IniDocument, IF, ELIF, ELSE, ENDIF, ASSIGN, BLANK,
                           COMMENT, DRAW)

_VAR_ASSIGN_RE = re.compile(r"^\$(\w+)\s*=\s*(.+)$")
_ALL_VAR_ASSIGN_RE = re.compile(r"^\$([\\\w]+)\s*=\s*(.+)$")
_CONST_VAR_RE = re.compile(r"^(?:global\s+)?(?:persist\s+)?\$(\w+)\s*=\s*([^,]+)$", re.I)
_VARIABLE_WRITE_RE = re.compile(
    r"^(?:(?:global|local|persist|pre|post)\s+)*\$([\\\w]+)(?:\s*=.*)?$", re.I)
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
        if exclude_section is not None and name.lower() == exclude_section.lower():
            continue
        if any(candidate.lower() == var.lower() for candidate in cvars):
            return True
    return False


def _find_key_lines(sec):
    """Return all `key =` bindings in source order; 3DMigoto allows several per section."""
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

    Record assigns draws to its values. The section condition uses this INI's
    detection variable so the key is active only for its object. Insert it
    after `[Constants]` and return the new section name.
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
    """Insert after the last nonblank line so trailing blanks remain between sections."""
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

    `post` runs after the current Present command list, so the current frame
    keeps its value and the next starts at 0 unless a TextureOverride sets it
    back to 1."""
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
    """Set the detection var in up to two TextureOverride sections before nested conditions."""
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

    `var_values` replaces selected cycle lists. Removing a value still used
    by a gate is rejected unless `allow_value_conflicts=True`; a removed
    default is reset to the new first value. Validate all fields before edits
    and return the possibly renamed section.
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
        back_line = next((line for line in sec.lines
                          if line.kind == ASSIGN
                          and line.text.partition("=")[0].strip().lower() == "back"), None)
        if back_line:
            doc.replace_lines(back_line.no, back_line.no + 1, [f"back = {back_combo.strip()}"])
        elif back_combo.strip():
            doc.insert_lines(sec.header_no + 1, [f"back = {back_combo.strip()}"])
            sec = find_cycle_section(doc, section_name)

    if var_values:
        for var, new_vals in var_values.items():
            sec = find_cycle_section(doc, section_name)
            line = next(line for line in sec.lines
                        if line.kind == ASSIGN and _VAR_ASSIGN_RE.match(line.text)
                        and _VAR_ASSIGN_RE.match(line.text).group(1) == var)
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


def _join_conditions(parts, *, either=False):
    identity, absorbing = (ic.FALSE, ic.TRUE) if either else (ic.TRUE, ic.FALSE)
    if absorbing in parts:
        return absorbing
    kept = [part for part in parts if part is not identity]
    if not kept:
        return identity
    if len(kept) == 1:
        return kept[0]
    return (ic.Or if either else ic.And)(kept)


def _open_deleted_conditions(node, dead, negated=False):
    """Remove deleted predicates in either polarity, including implicit else guards."""
    if isinstance(node, ic.Paren):
        return _open_deleted_conditions(node.inner, dead, negated)
    if isinstance(node, ic.Not):
        return _open_deleted_conditions(node.operand, dead, not negated)
    if isinstance(node, (ic.And, ic.Or)):
        return _join_conditions(
            [_open_deleted_conditions(part, dead, negated) for part in node.parts],
            either=isinstance(node, ic.Or) != negated)
    if any(name.lower() in dead for name in node.variables()):
        return ic.TRUE
    return ic.Not(ic.Paren(node)) if negated else node


def _strip_vars_from_gates(doc, dead_vars):
    """Open affected chains in place; never copy their draw instructions.

    Each branch retains its surviving explicit and implicit predicates. Draws
    newly sharing a scope are deduplicated only across other draws/comments;
    any command or gate is a barrier because it may change execution state.
    """
    dead = {var.lower() for var in dead_vars}
    view = IniDocument.from_string(doc.to_string())
    # Accept the shorthand `else $variable == value` when deleting its gate.
    # Normalize only the analysis copy; unrelated source text stays untouched.
    for line in list(view.lines):
        match = re.match(r"^else\s+(\$.*)$", line.text, re.I)
        if match and any(name.lower() in dead
                         for name in re.findall(r"\$([\\\w]+)", match[1])):
            view.replace_lines(line.no, line.no + 1,
                               [_rebuild_condition_line(line, "elif", match[1])])

    original_scopes = {}
    scope = []
    for line in view.lines:
        if line.section and line.no == line.section.header_no:
            scope = []
        elif line.kind == IF:
            scope.append(line.no)
        elif line.kind in (ELIF, ELSE) and scope:
            scope[-1] = line.no
        elif line.kind == ENDIF and scope:
            scope.pop()
        elif line.kind == DRAW:
            original_scopes[line.no] = tuple(scope)

    edits = {}
    unindent = {}
    promoted = set()
    changed = 0
    unsafe = []
    bad_sections = {problem.get("section", "").lower()
                    for problem in view.structure_errors() + view.syntax_errors()
                    if problem.get("section")}
    for sec in view.sections:
        targets = []
        nodes = {}
        for line in sec.lines:
            split = _split_condition_line(line)
            if not split:
                continue
            names = re.findall(r"\$([\\\w]+)", split[1])
            if any(name.lower() in dead for name in names):
                targets.append(line.no)
            try:
                nodes[line.no] = ic.parse(split[1])
            except ic.ConditionError:
                pass
        if not targets:
            continue
        if sec.name.lower() in bad_sections:
            unsafe.extend((sec.name, no + 1) for no in targets)
            continue

        stack = []
        chains = []
        for line in sec.lines:
            if line.kind == IF:
                stack.append([line])
            elif line.kind in (ELIF, ELSE):
                stack[-1].append(line)
            elif line.kind == ENDIF:
                chains.append((stack.pop(), line))
        for branches, closing in chains:
            affected = [branch.no for branch in branches if branch.no in targets]
            if not affected:
                continue
            if any(branch.kind != ELSE and branch.no not in nodes for branch in branches):
                unsafe.extend((sec.name, no + 1) for no in affected)
                continue
            # Independent surviving guards must not observe writes made by an
            # earlier branch. Refuse that ambiguity instead of changing its order.
            live = {name.lower() for branch in branches
                    for name in (nodes[branch.no].variables() if branch.no in nodes else ())
                    if name.lower() not in dead}
            writes = {match[1].lower() for line in sec.lines
                      if branches[0].no < line.no < closing.no
                      and (match := re.match(r"^(?:pre\s+|post\s+)?\$([\\\w]+)\s*=", line.text, re.I))}
            has_calls = any(re.match(r"^(?:(?:pre|post)\s+)?run\s*=", line.text, re.I)
                            for line in view.lines[branches[0].no + 1:closing.no])
            has_surviving_guards = any(_open_deleted_conditions(nodes[branch.no], dead) is not ic.TRUE
                                      for branch in branches if branch.no in nodes)
            if len(branches) > 1 and ((live & writes) or (has_calls and has_surviving_guards)):
                unsafe.extend((sec.name, no + 1) for no in affected)
                continue
            changed += len(affected)
            previous = []
            close_previous = False
            for index, branch in enumerate(branches):
                own = nodes.get(branch.no)
                parts = [_open_deleted_conditions(node, dead, True) for node in previous]
                if own is not None:
                    parts.append(_open_deleted_conditions(own, dead))
                    previous.append(own)
                guard = _join_conditions(parts)
                prefix = branch.raw[:len(branch.raw) - len(branch.raw.lstrip())]
                replacement = [prefix + "endif"] if close_previous else []
                close_previous = guard is not ic.TRUE
                if close_previous:
                    replacement.append(_rebuild_condition_line(
                        doc.lines[branch.no], "if", ic.render(guard)))
                elif ";" in branch.raw:
                    replacement.append(prefix + branch.raw[branch.raw.index(";"):])
                edits[branch.no] = replacement
                end = branches[index + 1].no if index + 1 < len(branches) else closing.no
                body = view.lines[branch.no + 1:end]
                promoted.update(line.no for line in body if line.kind == DRAW)
                if guard is ic.TRUE:
                    first = next((line for line in body if line.kind not in (BLANK, COMMENT)), None)
                    indent = first.raw[:len(first.raw) - len(first.raw.lstrip())] if first else ""
                    if indent.startswith(prefix) and len(indent) > len(prefix):
                        for line in body:
                            if line.raw.startswith(indent):
                                unindent[line.no] = unindent.get(line.no, 0) + len(indent) - len(prefix)
            edits[closing.no] = [closing.raw] if close_previous else []
            if not close_previous and ";" in closing.raw:
                edits[closing.no] = [closing.raw[:len(closing.raw) - len(closing.raw.lstrip())]
                                     + closing.raw[closing.raw.index(";"):]]

    for line in doc.lines:
        assignment = _VARIABLE_WRITE_RE.fullmatch(line.text)
        if assignment and assignment[1].lower() in dead:
            edits[line.no] = []
        condition = re.match(r"^condition\s*=\s*(.+)$", line.text, re.I)
        if not condition:
            continue
        try:
            node = ic.parse(condition[1])
        except ic.ConditionError:
            continue
        if not any(name.lower() in dead for name in node.variables()):
            continue
        reduced = _open_deleted_conditions(node, dead)
        edits[line.no] = ([] if reduced is ic.TRUE else
                          [_rebuild_condition_line(line, "condition =", ic.render(reduced))])
        changed += 1

    # Preserve each surviving line's own terminator by applying single-line
    # splices bottom-up, including indentation changes inside flattened blocks.
    for no, count in unindent.items():
        texts = edits.get(no, [doc.lines[no].raw])
        edits[no] = [text[min(count, len(text) - len(text.lstrip())):] for text in texts]
    planned = [(line.no, text) for line in doc.lines
               for text in edits.get(line.no, [line.raw])]
    result = IniDocument.from_string("\n".join(text for _, text in planned))
    seen = {}
    for line, (origin, _) in zip(result.lines, planned):
        if line.kind == DRAW and re.match(r"^drawindexed\s*=", line.text, re.I):
            key = re.sub(r"\s+", "", line.text).lower()
            duplicate = (key in seen
                         and (origin in promoted or seen[key] in promoted)
                         and original_scopes[origin] != original_scopes[seen[key]])
            if duplicate:
                edits[origin] = []
            else:
                seen[key] = origin
        elif line.kind not in (DRAW, BLANK, COMMENT):
            seen.clear()
    for no in sorted(edits, reverse=True):
        doc.replace_lines(no, no + 1, edits[no])
    return {"rewritten": changed, "always_false": [], "always_true": [], "unsafe": unsafe}


def remove_variables(doc, variables):
    """Remove plain variables and open their gates throughout one staged INI."""
    cleanup = DeletionCleanup(doc)
    variables = list(dict.fromkeys(variables))
    if any(ic.is_namespaced(var) for var in variables):
        raise ToggleEditError("cross-file variables cannot be removed here")
    dead = {var.lower() for var in variables}
    dependent_ordinals = {
        index for index, other in enumerate(doc.sections)
        if other.name.lower().startswith("key")
        and any(var.lower() in dead for var in cycle_vars(other))
    }
    report = _strip_vars_from_gates(doc, variables) if variables else \
        {"rewritten": 0, "always_false": [], "always_true": [], "unsafe": []}
    removed_sections = []
    metadata = {"key", "back", "type", "condition"}
    for index, other in reversed(list(enumerate(doc.sections))):
        if index not in dependent_ordinals or cycle_vars(other, include_read_only=True):
            continue
        if all(line.kind in (BLANK, COMMENT) or
               (line.kind == ASSIGN and line.text.partition("=")[0].strip().lower() in metadata)
               for line in other.lines):
            removed_sections.append(other.name)
            doc.delete_lines(other.start, other.end)
    removed_sections.extend(cleanup.finish(doc))
    return {"vars_removed": variables, "sections_removed": removed_sections,
            "gates_rewritten": report["rewritten"],
            "always_false_gates": report["always_false"],
            "always_true_gates": report["always_true"],
            "unsafe_gates": report["unsafe"]}


def delete_toggle(doc, section_name):
    """Remove a cycle section and all of its local variables from this INI."""
    cleanup = DeletionCleanup(doc)
    sec = find_cycle_section(doc, section_name)
    variables = list(cycle_vars(sec))
    name = sec.name
    doc.delete_lines(sec.start, sec.end)
    report = remove_variables(doc, variables)
    report["section"] = section_name
    report["sections_removed"].insert(0, name)
    report["sections_removed"].extend(cleanup.finish(doc))
    return report

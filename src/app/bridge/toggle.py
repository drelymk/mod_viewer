"""Toggle authoring: add/edit/delete a cycle toggle, staged in memory until
the user clicks Export.

Thin orchestration over app.session.edit (pending-edits cache),
core.ini.document (load/save-with-backup) and core.editing.toggle (the actual CRUD, which only ever
mutates an in-memory IniDocument). app.bridge.api is just the pywebview bridge,
app.mods.loader is the read-only payload builder, and this module is the write
path.

Every public function here returns a plain dict, never raises, so it's safe
to call across the JS bridge (an uncaught exception there surfaces as an
opaque rejection). A ToggleEditError becomes {"error": "..."}; anything
unexpected becomes {"error": "<full traceback>"} for debugging.
"""

import os
import traceback

from core.mod_discovery import discover_ini_paths
from core.editing import record as record_editor
from core.editing import toggle as te
from app.mods import loader as mod_loader
from app.session import edit as edit_session


def _unexpected_error():
    traceback.print_exc()
    return {"error": "Unexpected backend error. See the application log for details."}


def _ini_path(mod_dir, ini_rel):
    """Resolve a payload's relative ini name back to an absolute path,
    constrained to actually be one of this mod folder's own ini files (never
    an arbitrary path the JS side might pass in)."""
    paths = edit_session.document_paths(mod_dir) or discover_ini_paths(mod_dir)
    candidates = {os.path.relpath(p, mod_dir).replace(os.sep, "/"): p
                  for p in paths}
    name = str(ini_rel or "").replace("\\", "/")
    path = candidates.get(name)
    if path is None:
        raise te.ToggleEditError(f"{ini_rel!r} is not an ini file in this mod folder")
    return path


def _last_assign(sec, key_name):
    """The value of the last `key_name = ...` line in a section, or "" —
    mirrors edit_toggle's own "last one wins" convention for key/back."""
    value = ""
    for line in sec.lines:
        k, sep, v = line.text.partition("=")
        if sep and k.strip().lower() == key_name:
            value = v.strip()
    return value


def list_source_inis(mod_dir):
    """[{value, label}] for every ini file directly in mod_dir — the choices
    offered when adding a new toggle to a multi-ini ("AllInOne") mod. Lists
    every ini regardless of whether it has any toggles yet, unlike deriving
    the list from an already-loaded payload."""
    return [{"value": os.path.relpath(p, mod_dir).replace(os.sep, "/"),
             "label": os.path.relpath(p, mod_dir).replace(os.sep, "/")}
            for p in (edit_session.document_paths(mod_dir)
                      or discover_ini_paths(mod_dir))]


def get_toggle_details(mod_dir, ini_rel, section_name):
    """Ground-truth {name, key, back, vars: {var: [values]}} for an existing
    toggle — from the pending in-memory edit if staged this session, else
    read fresh from disk. Used to pre-fill the Edit form with real
    (un-namespaced) variable names.
    """
    try:
        path = _ini_path(mod_dir, ini_rel)
        doc = edit_session.peek(mod_dir, path)
        sec = te.find_cycle_section(doc, section_name)
        label = section_name[3:] if section_name[:3].lower() == "key" else section_name
        return {"ok": True, "name": label,
                "key": _last_assign(sec, "key"), "back": _last_assign(sec, "back"),
                "vars": te.cycle_vars(sec)}
    except te.ToggleEditError as e:
        return {"error": str(e)}
    except Exception:
        return _unexpected_error()


def _run(mod_dir, ini_rel, fn, on_commit=None):
    """Stage fn(doc)'s mutation into this ini's pending edit, without
    writing to disk. Shared by add/edit/delete below. A raised
    ToggleEditError (or any other exception) rolls the ini back to exactly
    what it was before this call.

    `on_commit(path, result)`, if given, runs right after a successful
    commit — used to update edit_session's "newly added, not yet wired"
    tracking (mark_added/rename_added/mark_removed).
    """
    try:
        path = _ini_path(mod_dir, ini_rel)
        sess, key, doc, was_pending, snapshot = edit_session.begin(mod_dir, path)
        try:
            result = fn(doc)
        except BaseException:
            edit_session.rollback(sess, key, was_pending, snapshot, path)
            raise
        edit_session.commit(sess, key, doc)
        if on_commit is not None:
            on_commit(path, result)
        return {"ok": True, "result": result, "pending": True}
    except te.ToggleEditError as e:
        return {"error": str(e)}
    except Exception:
        return _unexpected_error()


def add_toggle(mod_dir, ini_rel, name, key_combo, var, values, options=None):
    options = options or {}
    return _run(mod_dir, ini_rel, lambda doc: te.add_toggle(
        doc, name, key_combo, var, values,
        default=options.get("default"), back_combo=options.get("back_combo")),
        on_commit=lambda path, result: edit_session.mark_added(mod_dir, path, result))


def edit_toggle(mod_dir, ini_rel, section_name, changes=None):
    changes = changes or {}
    return _run(mod_dir, ini_rel, lambda doc: te.edit_toggle(
        doc, section_name,
        new_name=changes.get("new_name"),
        key_combo=changes.get("key_combo"),
        back_combo=changes.get("back_combo"),
        var_values=changes.get("var_values"),
        allow_value_conflicts=bool(changes.get("allow_value_conflicts"))),
        on_commit=lambda path, result: edit_session.rename_added(mod_dir, path, section_name, result))


def delete_toggle(mod_dir, ini_rel, section_name):
    return _run(mod_dir, ini_rel, lambda doc: te.delete_toggle(doc, section_name),
                on_commit=lambda path, result: edit_session.mark_removed(mod_dir, path, section_name))


# -- export / discard (Phase 6) --------------------------------------------
#
# Every add/edit/delete/record_toggle call above only ever mutates the
# pending in-memory session (app/session/edit.py) -- nothing reaches disk
# until one of these is called.

def has_pending_changes(mod_dir):
    """True if mod_dir has at least one staged, not-yet-exported edit."""
    return edit_session.has_pending(mod_dir)


def export_changes(mod_dir):
    """Write every pending edit for mod_dir to disk: one timestamped backup
    per changed ini, best-effort so one failure doesn't block the rest. See
    edit_session.export.

    Refuses up front (returns {"error": ..., "unwired": {ini: [section,
    ...]}}, writes nothing) if a toggle added this session still isn't
    wired to any mesh — Record it (or delete it) first.
    """
    pending_new = edit_session.new_sections_for(mod_dir)
    if pending_new:
        unwired = mod_loader.unwired_pending_sections(
            mod_dir, edit_session.overrides_for(mod_dir), pending_new,
            ini_paths=edit_session.document_paths(mod_dir),
            documents=edit_session.documents_for(mod_dir))
        if unwired:
            names = ", ".join(f"{sec} ({ini})" for ini, secs in unwired.items() for sec in secs)
            return {"error": "Can't export yet: newly-added toggle(s) aren't wired to any "
                              f"mesh — Record or delete them first: {names}",
                    "unwired": unwired}
    return edit_session.export(mod_dir)


def discard_changes(mod_dir):
    """Drop every pending edit for mod_dir without writing anything."""
    edit_session.discard(mod_dir)
    return {"ok": True}


# -- record mode (Phase 4) -------------------------------------------------
#
# The frontend already holds the full mesh payload (conditions + source
# identities) and the Toggle panel model, so it derives per-position visibility
# and the position_lines/target_lines maps to send back entirely on its own. This layer only
# answers "how many positions do you need to record" up front (get_record_
# positions) and applies the recorded result (record_toggle) — it never
# computes visibility itself.

def get_record_positions(mod_dir, ini_rel, section_name):
    """{"ok": True, "positions": N, "vars": [var, ...]}: how many cycle
    positions a Record-mode session for this section must supply, and which
    of its variables are actually writable (namespaced/master vars are
    read-only and excluded from ``vars``). The position count still includes
    every co-driven variable so Record can preview the complete tuple. Call
    this before starting a session rather than reusing the toggle panel's own
    cycle length, which can disagree.
    Reads the pending in-memory edit if one is staged this session.
    """
    try:
        path = _ini_path(mod_dir, ini_rel)
        doc = edit_session.peek(mod_dir, path)
        writable, positions = record_editor.writable_cycle_vars(doc, section_name)
        return {"ok": True, "positions": positions, "vars": sorted(writable)}
    except te.ToggleEditError as e:
        return {"error": str(e)}
    except Exception:
        return _unexpected_error()


def record_toggle(mod_dir, ini_rel, section_name, position_lines, target_lines):
    """Rewrite section_name's gates from an explicit Record scope.

    ``target_lines`` owns every draw source intentionally included in the
    recording. Each target includes its source line, section, and literal
    ``drawindexed`` triple so the core can resolve stale line numbers against
    the staged document. ``position_lines`` contains only the owned lines
    visible at each position. Stages the result like add/edit/delete above, then
    immediately re-checks the mutated text against what was recorded
    (record_editor.verify_recording). On any mismatch the pending edit is
    rolled back and {"error": ...} is returned instead of {"ok": True, ...}.
    Returns record_editor.record_toggle's report dict
    (vars_updated/chains_rewritten/wraps_added/skipped) under "result".
    """
    try:
        path = _ini_path(mod_dir, ini_rel)
        sess, key, doc, was_pending, snapshot = edit_session.begin(mod_dir, path)
        try:
            result = record_editor.record_toggle(
                doc, section_name, position_lines, target_lines)
            # Pass the authoritative staged text; verify_recording converts it
            # to an IniDocument projection instead of invoking parse_sections.
            mismatches = record_editor.verify_recording(path, result,
                                                         text=doc.to_string())
        except BaseException:
            edit_session.rollback(sess, key, was_pending, snapshot, path)
            raise
        # "verify" only exists to drive the check just above -- an internal
        # contract between record_editor's two halves, not part of the
        # UI-facing report.
        result.pop("verify", None)
        if mismatches:
            edit_session.rollback(sess, key, was_pending, snapshot, path)
            return {"error": "the rewritten gating didn't match what was recorded, so "
                              "the pending change was discarded; nothing was changed "
                              f"(first mismatch: {mismatches[0]})",
                    "mismatches": mismatches}
        edit_session.commit(sess, key, doc)
        return {"ok": True, "result": result, "pending": True}
    except te.ToggleEditError as e:
        return {"error": str(e)}
    except Exception:
        return _unexpected_error()

"""Bridge cycle-toggle CRUD and Record operations.

Edits stay staged in the session until Export. Public functions return error
dictionaries across the JS bridge and log unexpected failures.
"""

import os
import traceback

from core.mod_discovery import discover_ini_paths
from core.mod_source import mod_source_for_path
from core.editing import record as record_editor
from core.editing import toggle as te
from app.mods.analysis import build_mod_ini_snapshot
from app.mods import loader as mod_loader
from app.session import edit as edit_session


class _RecordVerificationFailure(Exception):
    def __init__(self, mismatches):
        self.mismatches = mismatches
        super().__init__("record verification failed")


def _unexpected_error():
    traceback.print_exc()
    return {"error": "Unexpected backend error. See the application log for details."}


def _toggle_error(error):
    payload = {"error": str(error)}
    if str(error).startswith("removing these values would orphan existing gates"):
        payload["error_code"] = "orphan_existing_gates"
    return payload


def _record_error(error):
    return {"error": "the rewritten gating didn't match what was recorded, so "
                     "the pending change was discarded; nothing was changed "
                     f"(first mismatch: {error.mismatches[0]})",
            "mismatches": error.mismatches}


def _record_and_verify(path, doc, ini_rel, section_name, position_lines,
                       target_lines, *, require_all=False):
    result = record_editor.record_toggle(
        doc, section_name, position_lines, target_lines, target_ini=ini_rel)
    if require_all and result["skipped"]:
        raise te.ToggleEditError(
            "Can't create toggle: not every selected draw can be wired safely "
            f"({result['skipped'][0]['reason']})")
    mismatches = record_editor.verify_recording(path, result, text=doc.to_string())
    result.pop("verify", None)
    if mismatches:
        raise _RecordVerificationFailure(mismatches)
    return result


def _ini_path(mod_dir, ini_rel):
    """Resolve a payload's relative ini name back to an absolute path,
    constrained to actually be one of this mod folder's own ini files (never
    an arbitrary path the JS side might pass in)."""
    paths = edit_session.document_paths(mod_dir)
    source = edit_session.source_for(mod_dir) or mod_source_for_path(mod_dir)
    paths = paths or discover_ini_paths(mod_dir, source=source)
    candidates = {(
        source.logical_path(p)
        if source.is_resource_reference(p)
        else os.path.relpath(p, mod_dir).replace(os.sep, "/")): p
        for p in paths}
    name = str(ini_rel or "").replace("\\", "/")
    path = candidates.get(name)
    if path is None:
        raise te.ToggleEditError(f"{ini_rel!r} is not an ini file in this mod folder")
    return path


def _last_assign(sec, key_name):
    """Return the last matching assignment, or an empty string."""
    value = ""
    for line in sec.lines:
        k, sep, v = line.text.partition("=")
        if sep and k.strip().lower() == key_name:
            value = v.strip()
    return value


def list_source_inis(mod_dir):
    """List source INIs for the add-toggle selector, including files without existing toggles."""
    paths = edit_session.document_paths(mod_dir)
    source = edit_session.source_for(mod_dir) or mod_source_for_path(mod_dir)
    paths = paths or discover_ini_paths(mod_dir, source=source)
    return [{"value": (source.logical_path(p)
                        if source.is_resource_reference(p)
                        else os.path.relpath(p, mod_dir).replace(os.sep, "/")),
             "label": (source.logical_path(p)
                        if source.is_resource_reference(p)
                        else os.path.relpath(p, mod_dir).replace(os.sep, "/"))}
            for p in paths]


def _binding_identity(binding):
    tokens = binding.lower().split()
    modifiers = {"ctrl", "no_ctrl", "shift", "no_shift", "alt", "no_alt"}
    keys = [token for token in tokens if token not in modifiers]
    return ("ctrl" in tokens, "shift" in tokens, "alt" in tokens, tuple(keys))


def next_toggle_key(mod_dir):
    """Choose the first unused generated binding across the staged mod."""
    used = set()
    for ini in list_source_inis(mod_dir):
        doc = edit_session.peek(mod_dir, _ini_path(mod_dir, ini["value"]))
        for section in doc.sections:
            if not section.name.lower().startswith("key"):
                continue
            for line in section.lines:
                key, sep, value = line.text.partition("=")
                if sep and key.strip().lower() in {"key", "back"}:
                    used.add(_binding_identity(value.strip()))
    groups = ("no_ctrl no_Shift no_alt", "ctrl no_Shift no_alt",
              "no_ctrl Shift no_alt", "no_ctrl no_Shift alt",
              "ctrl no_Shift alt", "ctrl Shift no_alt", "ctrl Shift alt")
    for group in groups:
        for character in ("'", "l", "p", ";", "o", "[", "]"):
            binding = f"{group} {character}"
            if _binding_identity(binding) not in used:
                return {"key": binding}
    return {"key": ""}


def get_toggle_details(mod_dir, ini_rel, section_name):
    """Read toggle details from staged state when available; otherwise read from disk."""
    try:
        path = _ini_path(mod_dir, ini_rel)
        doc = edit_session.peek(mod_dir, path)
        sec = te.find_cycle_section(doc, section_name)
        label = section_name[3:] if section_name[:3].lower() == "key" else section_name
        return {"ok": True, "name": label,
                "key": _last_assign(sec, "key"), "back": _last_assign(sec, "back"),
                "vars": te.cycle_vars(sec)}
    except te.ToggleEditError as e:
        return _toggle_error(e)
    except Exception:
        return _unexpected_error()


def _run(mod_dir, ini_rel, fn, on_commit=None):
    """Run one mutation in a session transaction.

    Roll back on errors and update new-toggle tracking only after commit.
    """
    try:
        path = _ini_path(mod_dir, ini_rel)
        with edit_session.transaction(mod_dir, [path]) as transaction:
            doc = transaction.document(path)
            result = fn(doc)
        if on_commit is not None:
            on_commit(path, result)
        return {"ok": True, "result": result, "pending": True}
    except _RecordVerificationFailure as e:
        return _record_error(e)
    except te.ToggleEditError as e:
        return _toggle_error(e)
    except Exception:
        return _unexpected_error()


def add_toggle(mod_dir, ini_rel, name, key_combo, var, values, options=None):
    options = options or {}
    def add(doc):
        targets = options.get("record_targets")
        if targets is not None:
            if not isinstance(targets, list) or not targets:
                raise te.ToggleEditError("Create Toggle needs selected authored draws")
            if [str(value) for value in values] != ["0", "1"]:
                raise te.ToggleEditError("Create Toggle requires values 0,1")
            if str(options.get("default")) != "0":
                raise te.ToggleEditError("Create Toggle requires default 0")
            record_editor.resolve_draw_references(doc, targets, ini_rel)
        section = te.add_toggle(
            doc, name, key_combo, var, values,
            default=options.get("default"), back_combo=options.get("back_combo"))
        if targets is not None:
            _record_and_verify(
                doc.path, doc, ini_rel, section,
                {0: sorted({int(target["line"]) for target in targets}), 1: []},
                targets, require_all=True)
        return section

    return _run(mod_dir, ini_rel, add,
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


# -- export / discard -------------------------------------------------------

def has_pending_changes(mod_dir):
    """True if mod_dir has at least one staged, not-yet-exported edit."""
    return edit_session.has_pending(mod_dir)


def export_changes(mod_dir):
    """Export pending edits, refusing while a newly added toggle is unwired."""
    source = edit_session.source_for(mod_dir) or mod_source_for_path(mod_dir)
    if source.read_only:
        return {"error": "Export is unavailable for compressed mods."}
    pending_new = edit_session.new_sections_for(mod_dir)
    if pending_new:
        snapshot = build_mod_ini_snapshot(
            edit_session.document_paths(mod_dir), mod_dir,
            edit_session.documents_for(mod_dir), source=source,
            require_documents=True)
        unwired = mod_loader.unwired_pending_sections(
            snapshot, pending_new)
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


# -- record mode ------------------------------------------------------------

def get_record_positions(mod_dir, ini_rel, section_name):
    """Return the full cycle length and writable vars for Record using staged state when present."""
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
    """Stage a Record rewrite and verify it; roll back if verification fails."""
    try:
        path = _ini_path(mod_dir, ini_rel)
        with edit_session.transaction(mod_dir, [path]) as transaction:
            doc = transaction.document(path)
            result = _record_and_verify(
                path, doc, ini_rel, section_name, position_lines, target_lines)
        return {"ok": True, "result": result, "pending": True}
    except _RecordVerificationFailure as exc:
        return _record_error(exc)
    except te.ToggleEditError as e:
        return {"error": str(e)}
    except Exception:
        return _unexpected_error()

"""In-memory "pending edits" for the currently open mod.

Toggle authoring (add/edit/delete) and Record mode stage their changes here
instead of writing straight to disk — nothing touches a real ini file until
the user clicks Export. mod_loader.load_mod layers `overrides_for()`'s
in-memory text over the real files so the UI can preview pending edits.

The app has exactly one window and one mod open at a time, so a single
module-level slot is enough: opening a different mod folder just doesn't
match the existing session's `mod_dir`, and is treated as empty (see
`has_pending`/`overrides_for`). The caller (app/api.py, driven by the
frontend's confirm-before-switching-mods flow) decides when to drop a
mismatched session, via `discard()`.

Typical flow, one edit action (add/edit/delete/record_toggle in
app/toggle_api.py):

    sess, key, doc, was_pending, snapshot = begin(mod_dir, ini_path)
    try:
        result = <mutate doc in place>
    except Exception:
        rollback(sess, key, was_pending, snapshot, ini_path)
        raise
    commit(sess, key, doc)

`begin`/`commit`/`rollback` make each action atomic: a rejected edit always
leaves the session's ini exactly as it was before that action started.

Separately, `mark_added`/`rename_added`/`mark_removed`/`new_sections_for`
track which [Key...] sections were freshly created by add_toggle this
session and haven't been exported yet — so a just-added, not-yet-wired
toggle can still show in the Toggle panel (and keep Export disabled)
without also surfacing every other already-on-disk, never-gating
[Key...] section (e.g. a $menu/$skin utility key). See
mod_loader.build_toggle_panel/unwired_pending_sections.
"""

import os

from core.ini_document import IniDocument


class _Session:
    __slots__ = ("mod_dir", "docs", "new_sections")

    def __init__(self, mod_dir):
        self.mod_dir = mod_dir
        self.docs = {}          # ini basename -> IniDocument; only entries with pending edits
        self.new_sections = {}  # ini basename -> {section name, ...} added via add_toggle
                                 # this session and not yet exported -- see mark_added


_session = None


def _same_mod(mod_dir):
    return _session is not None and os.path.normpath(_session.mod_dir) == os.path.normpath(mod_dir)


def _get_or_create(mod_dir):
    global _session
    if not _same_mod(mod_dir):
        _session = _Session(mod_dir)
    return _session


def begin(mod_dir, ini_path):
    """Get `ini_path`'s pending doc for mutation, loading it into the
    session on first touch (a session for a different mod folder is
    replaced — the frontend confirms with the user before that happens).

    Returns (session, key, doc, was_pending, snapshot); pass everything back
    to `commit()` on success or `rollback()` on failure.
    """
    sess = _get_or_create(mod_dir)
    key = os.path.basename(ini_path)
    was_pending = key in sess.docs
    doc = sess.docs.get(key) or IniDocument.load(ini_path)
    snapshot = doc.to_string() if was_pending else None
    return sess, key, doc, was_pending, snapshot


def commit(sess, key, doc):
    """Record a successful mutation as this ini's new pending state."""
    sess.docs[key] = doc


def rollback(sess, key, was_pending, snapshot, ini_path):
    """Undo a failed mutation: restore the ini's previous pending text, or
    drop it if this action would have been its first pending edit.
    """
    if was_pending:
        sess.docs[key] = IniDocument.from_string(snapshot, path=ini_path)
    else:
        sess.docs.pop(key, None)


def peek(mod_dir, ini_path):
    """The pending doc for `ini_path` if one exists, else load fresh from
    disk. For read-only queries that must see an already-staged edit
    without themselves staging anything new.
    """
    if _same_mod(mod_dir):
        doc = _session.docs.get(os.path.basename(ini_path))
        if doc is not None:
            return doc
    return IniDocument.load(ini_path)


def has_pending(mod_dir):
    """True if mod_dir has at least one staged, not-yet-exported edit."""
    return _same_mod(mod_dir) and bool(_session.docs)


def mark_added(mod_dir, ini_path, section_name):
    """Record that `section_name` was just created by add_toggle and
    doesn't gate anything yet — the only way an unwired [Key...] section is
    allowed to surface in the Toggle panel or block Export (see
    mod_loader.build_toggle_panel / unwired_pending_sections).
    """
    sess = _get_or_create(mod_dir)
    sess.new_sections.setdefault(os.path.basename(ini_path), set()).add(section_name)


def rename_added(mod_dir, ini_path, old_name, new_name):
    """Keep a tracked not-yet-wired section's name in sync with a rename
    from edit_toggle (which returns the possibly-changed section name)."""
    if old_name == new_name or not _same_mod(mod_dir):
        return
    names = _session.new_sections.get(os.path.basename(ini_path))
    if names and old_name in names:
        names.discard(old_name)
        names.add(new_name)


def mark_removed(mod_dir, ini_path, section_name):
    """Stop tracking a section removed via delete_toggle."""
    if not _same_mod(mod_dir):
        return
    names = _session.new_sections.get(os.path.basename(ini_path))
    if names:
        names.discard(section_name)


def new_sections_for(mod_dir):
    """{ini basename: {section name, ...}} for every toggle added via
    add_toggle this session and not yet exported (see mark_added). Doesn't
    mean "still unwired" — callers re-derive wired-ness fresh each time.
    """
    if not _same_mod(mod_dir):
        return {}
    return {k: set(v) for k, v in _session.new_sections.items() if v}


def overrides_for(mod_dir):
    """{ini_path: text} for every pending-edited ini in mod_dir's session —
    what mod_loader.load_mod layers over the real files to preview pending
    edits without touching disk.
    """
    if not _same_mod(mod_dir):
        return {}
    return {doc.path: doc.to_string() for doc in _session.docs.values()}


def discard(mod_dir):
    """Drop every pending edit for mod_dir without writing anything."""
    global _session
    if _same_mod(mod_dir):
        _session = None


def export(mod_dir):
    """Save every pending doc for mod_dir to disk (one timestamped backup
    per ini, however many edits accumulated). Best-effort per ini: one
    failing save doesn't block the others and stays pending for retry.

    Returns {"saved": [ini basename, ...], "failed": [{"ini": ..., "error":
    ...}, ...]}.
    """
    if not _same_mod(mod_dir) or not _session.docs:
        return {"saved": [], "failed": []}

    saved, failed = [], []
    for key, doc in list(_session.docs.items()):
        try:
            doc.save()
            saved.append(key)
            del _session.docs[key]
        except Exception as e:
            failed.append({"ini": key, "error": str(e)})
    return {"saved": saved, "failed": failed}

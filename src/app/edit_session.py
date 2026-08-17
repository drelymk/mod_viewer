"""Authoritative in-memory INI versions for the currently open mod.

Every active INI is loaded here once. The text editor, toggle authoring and
Record mode all read and mutate these same documents. Nothing touches a real
INI until the user clicks Export; mod_loader.load_mod always layers the
in-memory versions over disk, including versions that are currently clean.

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
    __slots__ = ("mod_dir", "docs", "baselines", "dirty", "new_sections",
                 "present_names_baseline")

    def __init__(self, mod_dir):
        self.mod_dir = mod_dir
        self.docs = {}          # ini basename -> authoritative in-memory IniDocument
        self.baselines = {}     # ini basename -> text last loaded/exported
        self.dirty = set()      # ini basenames whose text differs from baseline
        self.new_sections = {}  # ini basename -> {section name, ...} added via add_toggle
                                 # this session and not yet exported -- see mark_added
        self.present_names_baseline = _NO_METADATA_BASELINE


_session = None
_NO_METADATA_BASELINE = object()


def _same_mod(mod_dir):
    return _session is not None and os.path.normpath(_session.mod_dir) == os.path.normpath(mod_dir)


def _get_or_create(mod_dir):
    global _session
    if not _same_mod(mod_dir):
        _session = _Session(mod_dir)
    return _session


def _key(mod_dir, path):
    """Stable, browser-safe identity for an INI, including nested folders."""
    return os.path.relpath(os.path.abspath(path), os.path.abspath(mod_dir)).replace(os.sep, "/")


def load_documents(mod_dir, ini_paths):
    """Load every active INI into the authoritative in-memory session.

    Re-loading the same mod never re-reads disk: text edits and toggle edits
    must continue operating on the exact same documents until Export,
    Discard, a mod switch, or application restart. A new mod replaces the old
    session; the frontend confirms before allowing that switch when dirty.
    """
    sess = _get_or_create(mod_dir)
    for path in ini_paths:
        key = _key(mod_dir, path)
        if key in sess.docs:
            continue
        doc = IniDocument.load(path)
        sess.docs[key] = doc
        sess.baselines[key] = doc.to_string()
    return sess


def begin(mod_dir, ini_path):
    """Get `ini_path`'s authoritative doc for mutation, loading it into the
    session on first touch (a session for a different mod folder is
    replaced — the frontend confirms with the user before that happens).

    Returns (session, key, doc, was_pending, snapshot); pass everything back
    to `commit()` on success or `rollback()` on failure.
    """
    sess = _get_or_create(mod_dir)
    key = _key(mod_dir, ini_path)
    if key not in sess.docs:
        load_documents(mod_dir, [ini_path])
    was_pending = key in sess.dirty
    doc = sess.docs[key]
    snapshot = doc.to_string()
    return sess, key, doc, was_pending, snapshot


def commit(sess, key, doc):
    """Record a successful mutation as this ini's new pending state."""
    sess.docs[key] = doc
    if doc.to_string() == sess.baselines[key]:
        sess.dirty.discard(key)
        sess.new_sections.pop(key, None)
    else:
        sess.dirty.add(key)


def rollback(sess, key, was_pending, snapshot, ini_path):
    """Undo a failed mutation and restore its previous dirty state."""
    sess.docs[key] = IniDocument.from_string(snapshot, path=ini_path)
    if was_pending:
        sess.dirty.add(key)
    else:
        sess.dirty.discard(key)


def peek(mod_dir, ini_path):
    """Return the authoritative document for a read-only query."""
    key = _key(mod_dir, ini_path)
    if not _same_mod(mod_dir) or key not in _session.docs:
        load_documents(mod_dir, [ini_path])
    return _session.docs[key]


def has_pending(mod_dir):
    """True if mod_dir has at least one staged, not-yet-exported edit."""
    return (_same_mod(mod_dir)
            and (bool(_session.dirty)
                 or _session.present_names_baseline is not _NO_METADATA_BASELINE))


def list_documents(mod_dir):
    """Active INI basenames in stable load order."""
    if not _same_mod(mod_dir):
        return []
    return list(_session.docs)


def dirty_documents(mod_dir):
    """Copy of the dirty basename set for UI/API status."""
    return set(_session.dirty) if _same_mod(mod_dir) else set()


def document(mod_dir, ini_name):
    """Return a loaded document by basename, rejecting browser-made paths."""
    if not _same_mod(mod_dir):
        raise KeyError("no INI session is loaded for this mod")
    key = str(ini_name or "").replace("\\", "/")
    if (not key or os.path.isabs(key) or key.startswith("../")
            or "/../" in f"/{key}/" or key not in _session.docs):
        raise KeyError(f"{ini_name!r} is not an active INI in this mod")
    return key, _session.docs[key]


def editable_text(doc):
    """Browser-editor text: no BOM and normalized LF line endings."""
    text = doc.to_string()
    if doc.has_bom:
        text = text[1:]
    return text.replace("\r\n", "\n").replace("\r", "\n")


def update_text(mod_dir, ini_name, text):
    """Replace one loaded document from editor text and update dirty state.

    Existing per-line terminators are reused positionally by IniDocument, so
    opening and applying an unchanged CRLF/mixed-EOL file is a true no-op.
    """
    key, doc = document(mod_dir, ini_name)
    normalized = str(text).replace("\r\n", "\n").replace("\r", "\n")
    if normalized == editable_text(doc):
        return False
    snapshot = doc.to_string()
    was_pending = key in _session.dirty
    try:
        doc.replace_lines(0, len(doc.lines), normalized.splitlines())
        commit(_session, key, doc)
        tracked = _session.new_sections.get(key)
        if tracked:
            present = {sec.name for sec in doc.sections}
            tracked.intersection_update(present)
    except BaseException:
        rollback(_session, key, was_pending, snapshot, doc.path)
        raise
    return True


def mark_added(mod_dir, ini_path, section_name):
    """Record that `section_name` was just created by add_toggle and
    doesn't gate anything yet — the only way an unwired [Key...] section is
    allowed to surface in the Toggle panel or block Export (see
    mod_loader.build_toggle_panel / unwired_pending_sections).
    """
    sess = _get_or_create(mod_dir)
    sess.new_sections.setdefault(_key(mod_dir, ini_path), set()).add(section_name)


def rename_added(mod_dir, ini_path, old_name, new_name):
    """Keep a tracked not-yet-wired section's name in sync with a rename
    from edit_toggle (which returns the possibly-changed section name)."""
    if old_name == new_name or not _same_mod(mod_dir):
        return
    names = _session.new_sections.get(_key(mod_dir, ini_path))
    if names and old_name in names:
        names.discard(old_name)
        names.add(new_name)


def mark_removed(mod_dir, ini_path, section_name):
    """Stop tracking a section removed via delete_toggle."""
    if not _same_mod(mod_dir):
        return
    names = _session.new_sections.get(_key(mod_dir, ini_path))
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
    """Every loaded {ini_path: text}, used instead of disk while open."""
    if not _same_mod(mod_dir):
        return {}
    return {doc.path: doc.to_string() for doc in _session.docs.values()}


def stage_present_metadata(mod_dir):
    """Remember PRESENT names before their first staged authoring change."""
    from . import metadata
    sess = _get_or_create(mod_dir)
    if sess.present_names_baseline is _NO_METADATA_BASELINE:
        sess.present_names_baseline = metadata.all_present_names(mod_dir)


def _restore_present_metadata(sess):
    if sess.present_names_baseline is _NO_METADATA_BASELINE:
        return
    from . import metadata
    metadata.restore_present_names(sess.mod_dir, sess.present_names_baseline)


def discard(mod_dir):
    """Drop every pending edit for mod_dir without writing anything."""
    global _session
    if _same_mod(mod_dir):
        _restore_present_metadata(_session)
        _session = None


def export(mod_dir):
    """Save every pending doc for mod_dir to disk (one timestamped backup
    per ini, however many edits accumulated). Best-effort per ini: one
    failing save doesn't block the others and stays pending for retry.

    Returns {"saved": [ini basename, ...], "failed": [{"ini": ..., "error":
    ...}, ...]}.
    """
    if not _same_mod(mod_dir):
        return {"saved": [], "failed": []}
    if not _session.dirty:
        _session.present_names_baseline = _NO_METADATA_BASELINE
        return {"saved": [], "failed": []}

    saved, failed = [], []
    for key in [name for name in _session.docs if name in _session.dirty]:
        doc = _session.docs[key]
        try:
            doc.save()
            saved.append(key)
            _session.baselines[key] = doc.to_string()
            _session.dirty.discard(key)
            _session.new_sections.pop(key, None)
        except Exception as e:
            failed.append({"ini": key, "error": str(e)})
    if not _session.dirty:
        _session.present_names_baseline = _NO_METADATA_BASELINE
    return {"saved": saved, "failed": failed}

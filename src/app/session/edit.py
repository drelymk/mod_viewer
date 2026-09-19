"""Authoritative in-memory INI versions for the currently open mod.

Every active INI is loaded here once. The text editor, toggle authoring and
Record mode all read and mutate these same documents. Nothing touches a real
INI until the user clicks Export; mod_loader.load_mod always layers the
in-memory versions over disk, including versions that are currently clean.

The app has exactly one window and one mod open at a time, so a single
module-level slot is enough: opening a different mod folder just doesn't
match the existing session's `mod_dir`, and is treated as empty (see
`has_pending`/`overrides_for`). The caller (app.bridge.api, driven by the
frontend's confirm-before-switching-mods flow) decides when to drop a
mismatched session, via `discard()`.

Typical flow, one edit action (add/edit/delete/record_toggle in
app/bridge/toggle.py):

    with transaction(mod_dir, [ini_path]) as edit:
        result = <mutate edit.document(ini_path) in place>

Transactions make each action atomic: a rejected edit always leaves every
document and any requested session metadata exactly as it was before that
action started.

Separately, `mark_added`/`rename_added`/`mark_removed`/`new_sections_for`
track which [Key...] sections were freshly created by add_toggle this
session and haven't been exported yet — so a just-added, not-yet-wired
toggle can still show in the Toggle panel (and keep Export disabled)
without also surfacing every other already-on-disk, never-gating
[Key...] section (e.g. a $menu/$skin utility key). See
mod_loader.build_toggle_panel/unwired_pending_sections.
"""

import os
from copy import deepcopy

from core.ini.document import IniDocument
from core.mod_source import mod_source_for_path


class _Session:
    __slots__ = ("mod_dir", "source", "docs", "baselines", "dirty", "new_sections",
                 "present_names_baseline", "present_names", "revision",
                 "diagnostics_cache")

    def __init__(self, mod_dir, source=None):
        self.mod_dir = mod_dir
        self.source = source or mod_source_for_path(mod_dir)
        self.docs = {}          # ini basename -> authoritative in-memory IniDocument
        self.baselines = {}     # ini basename -> text last loaded/exported
        self.dirty = set()      # ini basenames whose text differs from baseline
        self.new_sections = {}  # ini basename -> {section name, ...} added via add_toggle
                                 # this session and not yet exported -- see mark_added
        self.present_names_baseline = _NO_METADATA_BASELINE
        self.present_names = _NO_METADATA_BASELINE
        self.revision = 0
        self.diagnostics_cache = None


_session = None
_NO_METADATA_BASELINE = object()


def _copy_metadata_state(value):
    return value if value is _NO_METADATA_BASELINE else deepcopy(value)


class _EditTransaction:
    """Atomic mutation of one or more authoritative staged documents."""

    def __init__(self, mod_dir, paths, *, present_metadata=False):
        self.mod_dir = mod_dir
        self.sess = _get_or_create(mod_dir)
        self.present_metadata = bool(present_metadata)
        self.entries = {}
        self._revision = None
        self._diagnostics_cache = None
        self._present_baseline = _NO_METADATA_BASELINE
        self._present_names = _NO_METADATA_BASELINE
        self._metadata_on_disk = _NO_METADATA_BASELINE
        self._metadata_sidecar_exists = False
        self._metadata_mutated = False

        requested = list(paths or [])
        missing = [path for path in requested
                   if _key(mod_dir, path, source=self.sess.source)
                   not in self.sess.docs]
        if missing:
            load_documents(mod_dir, missing, source=self.sess.source)
        self._revision = self.sess.revision
        self._diagnostics_cache = self.sess.diagnostics_cache
        for path in requested:
            key = _key(mod_dir, path, source=self.sess.source)
            if key not in self.sess.docs:
                raise KeyError(f"{path!r} is not an active INI in this mod")
            doc = self.sess.docs[key]
            self.entries.setdefault(key, {
                "path": doc.path,
                "doc": doc,
                "text": doc.to_string(),
                "dirty": key in self.sess.dirty,
                "new_sections": set(self.sess.new_sections.get(key, set())),
            })
        if self.present_metadata:
            self._present_baseline = _copy_metadata_state(
                self.sess.present_names_baseline)
            self._present_names = _copy_metadata_state(self.sess.present_names)
            if not self.sess.source.read_only:
                from app.mods import metadata
                self._metadata_on_disk = metadata.all_present_names(
                    mod_dir, source=self.sess.source)
                self._metadata_sidecar_exists = os.path.isfile(
                    os.path.join(mod_dir, metadata.METADATA_NAME))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            self._commit()
        else:
            self._rollback()
        return False

    def document(self, path):
        """Return the authoritative staged document for ``path``."""
        key = _key(self.mod_dir, path, source=self.sess.source)
        try:
            return self.entries[key]["doc"]
        except KeyError as error:
            raise KeyError(f"{path!r} is not part of this transaction") from error

    def mark_metadata_mutation(self):
        """Mark that this transaction is about to change PRESENT metadata."""
        if self.present_metadata:
            self._metadata_mutated = True

    def _commit(self):
        try:
            for key, entry in self.entries.items():
                doc = entry["doc"]
                self.sess.docs[key] = doc
                if doc.to_string() == self.sess.baselines[key]:
                    self.sess.dirty.discard(key)
                    self.sess.new_sections.pop(key, None)
                else:
                    self.sess.dirty.add(key)
            # A transaction is one logical user action, so derived state is
            # invalidated once even when it spans several INIs.
            _touch(self.sess)
        except BaseException:
            self._rollback()
            raise

    def _rollback(self):
        for key, entry in self.entries.items():
            self.sess.docs[key] = IniDocument.from_string(
                entry["text"], path=entry["path"])
            if entry["dirty"]:
                self.sess.dirty.add(key)
            else:
                self.sess.dirty.discard(key)
            if entry["new_sections"]:
                self.sess.new_sections[key] = set(entry["new_sections"])
            else:
                self.sess.new_sections.pop(key, None)
        if self.present_metadata:
            self.sess.present_names_baseline = _copy_metadata_state(
                self._present_baseline)
            self.sess.present_names = _copy_metadata_state(self._present_names)
            self.sess.revision = self._revision
            self.sess.diagnostics_cache = self._diagnostics_cache
            if (self._metadata_mutated
                    and self._metadata_on_disk is not _NO_METADATA_BASELINE):
                from app.mods import metadata
                metadata.restore_present_names(
                    self.mod_dir, self._metadata_on_disk)
                if not self._metadata_sidecar_exists:
                    try:
                        os.remove(os.path.join(self.mod_dir, metadata.METADATA_NAME))
                    except FileNotFoundError:
                        pass


def transaction(mod_dir, paths, *, present_metadata=False):
    """Return an atomic staged-edit context for one or more active INIs."""
    return _EditTransaction(mod_dir, paths,
                            present_metadata=present_metadata)


def _same_mod(mod_dir):
    return _session is not None and os.path.normpath(_session.mod_dir) == os.path.normpath(mod_dir)


def _get_or_create(mod_dir, source=None):
    global _session
    if not _same_mod(mod_dir):
        _session = _Session(mod_dir, source=source)
    elif source is not None:
        current = _session.source
        if (getattr(current, "kind", None) != getattr(source, "kind", None)
                or getattr(current, "source_path", None)
                != getattr(source, "source_path", None)):
            _session.source = source
    return _session


def _key(mod_dir, path, source=None):
    """Stable, browser-safe identity for an INI, including nested folders."""
    source = source or (getattr(_session, "source", None)
                        if _same_mod(mod_dir) else None)
    if source is not None and (source.virtual
                               or source.is_resource_reference(path)):
        return source.logical_path(path)
    return os.path.relpath(os.path.abspath(path), os.path.abspath(mod_dir)).replace(os.sep, "/")


def _touch(sess):
    """Advance the authoritative document revision and invalidate derived data."""
    sess.revision += 1
    sess.diagnostics_cache = None


def load_documents(mod_dir, ini_paths, *, source=None):
    """Load every active INI into the authoritative in-memory session.

    Re-loading the same mod never re-reads disk: text edits and toggle edits
    must continue operating on the exact same documents until Export,
    Discard, a mod switch, or application restart. A new mod replaces the old
    session; the frontend confirms before allowing that switch when dirty.
    """
    sess = _get_or_create(mod_dir, source=source)
    added = False
    for path in ini_paths:
        key = _key(mod_dir, path, source=sess.source)
        if key in sess.docs:
            continue
        if not sess.source.virtual:
            doc = IniDocument.load(path)
        else:
            doc = IniDocument.from_string(
                sess.source.read_text(path), path=path)
        sess.docs[key] = doc
        sess.baselines[key] = doc.to_string()
        added = True
    if added:
        _touch(sess)
    return sess


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


def document_paths(mod_dir):
    """Absolute paths for the active INIs already loaded in this session."""
    if not _same_mod(mod_dir):
        return []
    return [doc.path for doc in _session.docs.values()]


def source_for(mod_dir):
    """Return the source object owned by the active edit session."""
    return _session.source if _same_mod(mod_dir) else None


def documents_for(mod_dir):
    """Map absolute INI paths to their authoritative staged documents."""
    if not _same_mod(mod_dir):
        return {}
    return {doc.path: doc for doc in _session.docs.values()}


def current_revision(mod_dir):
    """Return the revision of the authoritative document set, or ``None``."""
    return _session.revision if _same_mod(mod_dir) else None


def cached_diagnostics(mod_dir):
    """Return a detached diagnostics report for the current revision, if any."""
    if not _same_mod(mod_dir) or _session.diagnostics_cache is None:
        return None
    revision, report = _session.diagnostics_cache
    if revision != _session.revision:
        return None
    return deepcopy(report)


def cache_diagnostics(mod_dir, report):
    """Cache and return a detached diagnostics report for this revision."""
    if not _same_mod(mod_dir):
        return deepcopy(report)
    _session.diagnostics_cache = (_session.revision, deepcopy(report))
    return deepcopy(report)


def invalidate_diagnostics(mod_dir):
    """Invalidate diagnostics derived from viewer metadata without editing INIs."""
    if _same_mod(mod_dir):
        _session.diagnostics_cache = None


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
    with transaction(mod_dir, [doc.path]) as edit:
        staged = edit.document(doc.path)
        staged.replace_lines(0, len(staged.lines), normalized.splitlines())
    tracked = _session.new_sections.get(key)
    if tracked:
        present = {sec.name for sec in doc.sections}
        tracked.intersection_update(present)
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
    from app.mods import metadata
    sess = _get_or_create(mod_dir)
    if sess.present_names_baseline is _NO_METADATA_BASELINE:
        source = sess.source if sess.source.read_only else None
        sess.present_names_baseline = metadata.all_present_names(
            mod_dir, source=source)
        sess.present_names = deepcopy(sess.present_names_baseline) \
            if isinstance(sess.present_names_baseline, dict) else {}


def update_present_names(mod_dir, mutate):
    """Apply a PRESENT metadata mutation without writing a read-only source."""
    sess = _get_or_create(mod_dir)
    stage_present_metadata(mod_dir)
    data = {}
    if isinstance(sess.present_names, dict) and sess.present_names:
        data["present_names"] = deepcopy(sess.present_names)
    result = mutate(data)
    current = data.get("present_names")
    sess.present_names = deepcopy(current) if isinstance(current, dict) else {}
    _touch(sess)
    return result


def staged_present_names(mod_dir):
    """Return current staged PRESENT names, or None when none are staged."""
    if not _same_mod(mod_dir) or _session.present_names is _NO_METADATA_BASELINE:
        return None
    return deepcopy(_session.present_names)


def _restore_present_metadata(sess):
    if (sess.source.read_only
            or sess.present_names_baseline is _NO_METADATA_BASELINE):
        return
    from app.mods import metadata
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
    if _session.source.read_only:
        return {
            "saved": [],
            "failed": [{"ini": key, "error":
                        "Export is unavailable for compressed mods."}
                       for key in _session.dirty],
            "error": "Export is unavailable for compressed mods.",
        }
    if not _session.dirty:
        _session.present_names_baseline = _NO_METADATA_BASELINE
        _session.present_names = _NO_METADATA_BASELINE
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
        _session.present_names = _NO_METADATA_BASELINE
    return {"saved": saved, "failed": failed}

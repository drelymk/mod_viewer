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

import hashlib
import os
import tempfile
from datetime import datetime, timedelta
from copy import deepcopy

from core.ini.document import IniDocument, load_ini_document
from core.mod_source import mod_source_for_path


class _Session:
    __slots__ = ("mod_dir", "source", "docs", "baselines", "dirty", "new_sections",
                 "present_names_baseline", "present_names", "revision",
                 "diagnostics_cache", "ib_edits")

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
        self.ib_edits = {}


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
        self._ib_edits = _clone_ib_edits(self.sess.ib_edits)

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

    def stage_ib_edit(self, path, candidate, original_hash, ranges,
                      dependent_inis=()):
        """Stage disjoint byte ranges in one shared physical index buffer."""
        key = _ib_key(path)
        normalized_ranges = tuple((int(start), int(end))
                                  for start, end in ranges)
        record = self.sess.ib_edits.get(key)
        if record is not None:
            if record["original_hash"] != original_hash:
                raise ValueError("The staged index buffer baseline changed.")
            for start, end in normalized_ranges:
                if any(start < other_end and other_start < end
                       for other_start, other_end in record["ranges"]):
                    raise ValueError("Mesh edits overlap in the same index buffer.")
            record["candidate"] = bytes(candidate)
            record["ranges"].extend(normalized_ranges)
            record["dependent_inis"].update(dependent_inis)
            return
        self.sess.ib_edits[key] = {
            "path": os.path.abspath(path),
            "original_hash": original_hash,
            "candidate": bytes(candidate),
            "ranges": list(normalized_ranges),
            "dependent_inis": set(dependent_inis),
            "committed": False,
            "backup": None,
        }

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
        self.sess.ib_edits = _clone_ib_edits(self._ib_edits)
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


def _ib_key(path):
    return os.path.normcase(os.path.abspath(path))


def _clone_ib_edits(records):
    return {
        key: {
            **record,
            "candidate": bytes(record["candidate"]),
            "ranges": list(record["ranges"]),
            "dependent_inis": set(record["dependent_inis"]),
        }
        for key, record in records.items()
    }


def load_documents(mod_dir, ini_paths, *, source=None, documents=None):
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
        doc = documents.get(path) if documents is not None else None
        if doc is None:
            doc = load_ini_document(path, sess.source)
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
                 or bool(_session.ib_edits)
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


def ib_overrides_for(mod_dir):
    """Return staged physical buffer bytes for the normal load path."""
    if not _same_mod(mod_dir):
        return {}
    return {record["path"]: bytes(record["candidate"])
            for record in _session.ib_edits.values()}


def ib_edits_for(mod_dir):
    """Return detached staged-buffer records for export and diagnostics."""
    if not _same_mod(mod_dir):
        return {}
    return _clone_ib_edits(_session.ib_edits)


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
    had_buffers = bool(_session.ib_edits)
    if _session.source.read_only:
        result = {
            "saved": [],
            "failed": [{"ini": key, "error":
                        "Export is unavailable for compressed mods."}
                       for key in _session.dirty],
            "error": "Export is unavailable for compressed mods.",
        }
        if had_buffers:
            result.update({"buffers_saved": [], "buffers_failed": [
                {"buffer": record["path"], "error": result["error"]}
                for record in _session.ib_edits.values()],
                "buffer_backups": []})
        return result
    if not _session.dirty and not _session.ib_edits:
        _session.present_names_baseline = _NO_METADATA_BASELINE
        _session.present_names = _NO_METADATA_BASELINE
        return {"saved": [], "failed": []}

    saved, failed = [], []
    buffers_saved, buffers_failed, buffer_backups = [], [], []
    blocked_inis = set()
    for record in list(_session.ib_edits.values()):
        if record["committed"]:
            try:
                if _sha256(_read_bytes(record["path"])) != _sha256(
                        record["candidate"]):
                    raise ValueError(
                        "The committed index buffer changed outside the viewer.")
            except Exception as error:
                buffers_failed.append({"buffer": record["path"],
                                       "error": str(error)})
                blocked_inis.update(record["dependent_inis"])
            continue
        try:
            path = record["path"]
            current = _read_bytes(path)
            if _sha256(current) != record["original_hash"]:
                raise ValueError("The index buffer changed outside the viewer.")
            backup = _write_buffer_backup(path, current)
            if _sha256(_read_bytes(path)) != record["original_hash"]:
                raise ValueError("The index buffer changed outside the viewer.")
            _atomic_replace_buffer(path, record["candidate"],
                                    record["original_hash"])
            record["committed"] = True
            record["backup"] = backup
            buffers_saved.append(path)
            buffer_backups.append(backup)
        except Exception as error:
            buffers_failed.append({"buffer": record["path"],
                                   "error": str(error)})
            blocked_inis.update(record["dependent_inis"])
    for key in [name for name in _session.docs if name in _session.dirty]:
        if key in blocked_inis:
            continue
        doc = _session.docs[key]
        try:
            doc.save()
            saved.append(key)
            _session.baselines[key] = doc.to_string()
            _session.dirty.discard(key)
            _session.new_sections.pop(key, None)
        except Exception as e:
            failed.append({"ini": key, "error": str(e)})
    for key, record in list(_session.ib_edits.items()):
        if record["committed"] and not any(
                ini in _session.dirty for ini in record["dependent_inis"]):
            _session.ib_edits.pop(key, None)
    if not _session.dirty and not _session.ib_edits:
        _session.present_names_baseline = _NO_METADATA_BASELINE
        _session.present_names = _NO_METADATA_BASELINE
    result = {"saved": saved, "failed": failed}
    if had_buffers:
        result.update({"buffers_saved": buffers_saved,
                       "buffers_failed": buffers_failed,
                       "buffer_backups": buffer_backups})
    return result


def _read_bytes(path):
    with open(path, "rb") as stream:
        return stream.read()


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _write_buffer_backup(path, data):
    directory = os.path.dirname(path)
    stem, extension = os.path.splitext(os.path.basename(path))
    moment = datetime.now()
    for _index in range(10000):
        backup = os.path.join(
            directory, f"{stem}-{moment.strftime('%Y%m%d%H%M%S')}{extension}")
        try:
            with open(backup, "xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            return backup
        except FileExistsError:
            moment += timedelta(seconds=1)
    raise OSError("Could not create a unique index-buffer backup name.")


def _atomic_replace_buffer(path, candidate, original_hash):
    directory = os.path.dirname(path)
    fd, temporary = tempfile.mkstemp(prefix=f".{os.path.basename(path)}.",
                                     suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(candidate)
            stream.flush()
            os.fsync(stream.fileno())
        if len(candidate) != os.path.getsize(path):
            raise ValueError("The staged index buffer changed length.")
        if _sha256(_read_bytes(path)) != original_hash:
            raise ValueError("The index buffer changed outside the viewer.")
        if _sha256(_read_bytes(temporary)) != _sha256(candidate):
            raise ValueError("The temporary index buffer could not be verified.")
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass

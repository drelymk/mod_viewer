"""Atomic staged editing of one logical PRESENT cycle spanning several INIs."""

import os
import traceback

from core.mod_discovery import discover_ini_paths
from core.mod_source import mod_source_for_path
from core.editing import present as present_editor
from core.editing.toggle import ToggleEditError

from app.mods import metadata
from app.session import edit as edit_session


def _ini_rel(mod_dir, path, source=None):
    if source is None:
        source = getattr(path, "source", None)
    if source is not None and source.is_resource_reference(path):
        return source.logical_path(path)
    return os.path.relpath(path, mod_dir).replace(os.sep, "/")


def _documents(mod_dir):
    paths = edit_session.document_paths(mod_dir)
    source = edit_session.source_for(mod_dir) or mod_source_for_path(mod_dir)
    if not paths:
        paths = discover_ini_paths(mod_dir, source=source)
        edit_session.load_documents(mod_dir, paths, source=source)
    return [(_ini_rel(mod_dir, path, source), path, edit_session.peek(mod_dir, path))
            for path in paths]


def _unexpected_error():
    traceback.print_exc()
    return {"error": "Unexpected backend error. See the application log for details."}


def _metadata_change(mod_dir, source, mutate, persist):
    """Keep read-only metadata staged while persisting directory metadata."""
    if source.read_only:
        return edit_session.update_present_names(mod_dir, mutate)
    return persist()


def _batch_run(mod_dir, targets, mutate, metadata_change=None):
    source = edit_session.source_for(mod_dir) or mod_source_for_path(mod_dir)
    try:
        paths = [path for _ini_rel, path, _doc in targets]
        with edit_session.transaction(
                mod_dir, paths, present_metadata=metadata_change is not None) as transaction:
            results = []
            for ini_rel, path, _doc in targets:
                results.append(mutate(ini_rel, transaction.document(path)))
            if metadata_change:
                transaction.mark_metadata_mutation()
                edit_session.stage_present_metadata(mod_dir)
                metadata_change(results, source)
        count = results[0].get("count") if results and isinstance(results[0], dict) else None
        return {"ok": True, "result": {"count": count, "files": len(results)},
                "pending": True}
    except (ToggleEditError, ValueError) as exc:
        return {"error": str(exc)}
    except Exception:
        return _unexpected_error()


def _eligible(mod_dir):
    return [entry for entry in _documents(mod_dir)
            if present_editor.capturable_variables(entry[2])]


def _present_docs(mod_dir):
    return [entry for entry in _documents(mod_dir)
            if entry[2].section(present_editor.SECTION_NAME) is not None]


def _snapshot(snapshots, ini_rel):
    value = (snapshots or {}).get(ini_rel)
    if not isinstance(value, dict):
        raise ToggleEditError(f"the current toggle values for {ini_rel} were not supplied")
    return value


def add_present(mod_dir, key_combo, back_combo, snapshots):
    targets = _eligible(mod_dir)
    if not targets:
        return {"error": "this mod has no INI with key or menu toggles"}
    existing = [entry for entry in targets
                if entry[2].section(present_editor.SECTION_NAME) is not None]
    missing = [entry for entry in targets
               if entry[2].section(present_editor.SECTION_NAME) is None]
    if not missing:
        return {"error": f"[{present_editor.SECTION_NAME}] already exists in this mod"}

    try:
        existing_details = _aligned_details(existing) if existing else []
        target_count = existing_details[0][1]["count"] if existing_details else 1
    except (ToggleEditError, ValueError) as exc:
        return {"error": str(exc)}
    except Exception:
        return _unexpected_error()

    def mutate(ini_rel, doc):
        if doc.section(present_editor.SECTION_NAME) is not None:
            return present_editor.edit_binding(doc, key_combo, back_combo)
        result = present_editor.add(
            doc, key_combo, back_combo, _snapshot(snapshots, ini_rel))
        for _position in range(1, target_count):
            result = present_editor.capture(
                doc, _snapshot(snapshots, ini_rel), allow_duplicate=True)
        return result

    def clear_names(_results, source):
        return _metadata_change(
            mod_dir, source,
            lambda data: metadata.apply_clear_present_names(
                data, metadata.PRESENT_NAMES_KEY),
            lambda: metadata.clear_present_names(
                mod_dir, metadata.PRESENT_NAMES_KEY))

    return _batch_run(mod_dir, targets, mutate,
                      metadata_change=None if existing else clear_names)


def edit_present(mod_dir, key_combo, back_combo):
    targets = _present_docs(mod_dir)
    if not targets:
        return {"error": "this mod has no PRESENT key"}
    return _batch_run(
        mod_dir, targets,
        lambda _ini_rel, doc: present_editor.edit_binding(doc, key_combo, back_combo))


def delete_present(mod_dir):
    targets = _present_docs(mod_dir)
    if not targets:
        return {"error": "this mod has no PRESENT key"}

    def clear_names(_results, source):
        def mutate(data):
            metadata.apply_clear_present_names(
                data, metadata.PRESENT_NAMES_KEY)
            for ini_rel, _path, _doc in targets:
                metadata.apply_clear_present_names(data, ini_rel)
        return _metadata_change(
            mod_dir, source, mutate,
            lambda: (
                metadata.clear_present_names(
                    mod_dir, metadata.PRESENT_NAMES_KEY),
                [metadata.clear_present_names(mod_dir, ini_rel)
                 for ini_rel, _path, _doc in targets]))

    return _batch_run(mod_dir, targets,
                      lambda _ini_rel, doc: present_editor.delete(doc),
                      metadata_change=clear_names)


def _aligned_details(targets):
    values = [(ini_rel, present_editor.details(doc))
              for ini_rel, _path, doc in targets]
    counts = {info["count"] for _ini_rel, info in values}
    if len(counts) != 1:
        raise ToggleEditError("PRESENT keys have different position counts")
    return values


def capture_present(mod_dir, snapshots, name, position=None,
                    allow_duplicate=False):
    try:
        targets = _present_docs(mod_dir)
        if not targets:
            return {"error": "this mod has no PRESENT key"}
        details = _aligned_details(targets)
        duplicate_sets = []
        for ini_rel, _info in details:
            doc = next(doc for rel, _path, doc in targets if rel == ini_rel)
            duplicate_sets.append(set(present_editor.duplicate_positions(
                doc, _snapshot(snapshots, ini_rel), position=position)))
        duplicates = sorted(set.intersection(*duplicate_sets)) if duplicate_sets else []
        if duplicates and not allow_duplicate:
            return {"warning": "the captured values duplicate another present",
                    "duplicate_positions": duplicates}
    except (ToggleEditError, ValueError) as exc:
        return {"error": str(exc)}
    except Exception:
        return _unexpected_error()

    def save_name(results, source):
        target = results[0]["count"] - 1 if position is None else int(position)
        return _metadata_change(
            mod_dir, source,
            lambda data: metadata.apply_present_name(
                data, metadata.PRESENT_NAMES_KEY, target, name),
            lambda: metadata.save_present_name(
                mod_dir, metadata.PRESENT_NAMES_KEY, target, name))

    return _batch_run(
        mod_dir, targets,
        lambda ini_rel, doc: present_editor.capture(
            doc, _snapshot(snapshots, ini_rel), position=position,
            allow_duplicate=True),
        metadata_change=save_name)


def delete_present_position(mod_dir, position):
    try:
        targets = _present_docs(mod_dir)
        if not targets:
            return {"error": "this mod has no PRESENT key"}
        details = _aligned_details(targets)
        old_count = details[0][1]["count"]
    except (ToggleEditError, ValueError) as exc:
        return {"error": str(exc)}
    except Exception:
        return _unexpected_error()

    return _batch_run(
        mod_dir, targets,
        lambda _ini_rel, doc: present_editor.delete_position(doc, position),
        metadata_change=lambda _results, source: _metadata_change(
            mod_dir, source,
            lambda data: metadata.apply_delete_present_name(
                data, metadata.PRESENT_NAMES_KEY, position, old_count),
            lambda: metadata.delete_present_name(
                mod_dir, metadata.PRESENT_NAMES_KEY, position, old_count)))

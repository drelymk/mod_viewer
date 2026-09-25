"""Discovery of selected INI files from a directory or virtual archive."""

import os
import re

from .ini.document import load_ini_document
from .mod_source import (DirectoryModSource, ModSourceError,
                         mod_source_for_path)

_MAX_INI_FILES = 10
_MAX_INI_DEPTH = 2
_DRAW_RE = re.compile(r"^drawindexed\s*=", re.I)
_IB_RE = re.compile(r"^ib\s*=", re.I)


def _selected(name, *, disabled=False):
    is_disabled = name.upper().startswith("DISABLED")
    return (is_disabled == disabled and name.lower().endswith(".ini"))


def _ini_names(folder, source, *, disabled=False):
    if isinstance(source, DirectoryModSource):
        try:
            names = os.listdir(folder)
        except OSError:
            return []
        selected = []
        for name in sorted(names):
            if not _selected(name, disabled=disabled):
                continue
            if os.path.isfile(os.path.join(folder, name)):
                selected.append(os.path.join(folder, name))
        return selected

    prefix = str(folder or "").replace("\\", "/").strip("/")
    selected = []
    for logical in source.list_files():
        parent, _, name = logical.rpartition("/")
        if parent != prefix or not _selected(name, disabled=disabled):
            continue
        selected.append(source.document_path(logical))
    return selected


def _has_geometry_sections(document):
    """Return whether an INI looks like a mod root without resolving geometry.

    Discovery must not call the full draw/mesh analyzer.  The root anchor only
    decides whether bounded nested INIs belong to this selection, so a
    conservative command-shape check is sufficient and avoids the old
    ``core.ini.sections -> core.ini.parser`` dependency.  Missing buffers are allowed;
    geometry loading reports those later.
    """
    has_draw = False
    has_index = False
    for section in document.sections:
        if section.name.lower().startswith("textureoverride"):
            for raw in section.lines:
                line = raw.text
                if _DRAW_RE.match(line):
                    has_draw = True
                elif _IB_RE.match(line):
                    has_index = True
        elif section.name.lower().startswith("commandlist"):
            for raw in section.lines:
                if _IB_RE.match(raw.text):
                    has_index = True
    return has_draw or has_index


def discover_ini_paths(mod_dir, *, disabled=False, source=None,
                       documents=None):
    """Return selected INIs from a directory or virtual archive.

    Directory discovery retains its historical bounded nested search. Virtual
    archives have no filesystem root to infer, so they scan all members
    deterministically. ``disabled`` selects
    only filenames beginning with ``DISABLED`` (case-insensitively); active and
    disabled files are never combined.
    """
    source = source or mod_source_for_path(mod_dir)
    if source.virtual:
        return [
            source.document_path(logical)
            for logical in source.list_files()
            if _selected(logical.rsplit("/", 1)[-1], disabled=disabled)
        ]

    direct = _ini_names(mod_dir if not source.virtual else "",
                        source, disabled=disabled)
    def has_geometry(path):
        try:
            document = documents.get(path) if documents is not None else None
            if document is None:
                document = load_ini_document(path, source)
                if documents is not None:
                    documents[path] = document
            return _has_geometry_sections(document)
        except (OSError, UnicodeError, ValueError, ModSourceError):
            return False

    if not any(has_geometry(path) for path in direct):
        return direct

    found = list(direct)
    if len(found) >= _MAX_INI_FILES:
        return found
    if not source.virtual:
        for base, dirs, _files in os.walk(mod_dir):
            rel = os.path.relpath(base, mod_dir)
            depth = 0 if rel == os.curdir else len(rel.split(os.sep))
            dirs[:] = sorted(dirs) if depth < _MAX_INI_DEPTH else []
            if depth == 0:
                continue
            for path in _ini_names(base, source, disabled=disabled):
                found.append(path)
                if len(found) >= _MAX_INI_FILES:
                    return found
    return found

"""Discovery of selected INI files from a directory or ZIP mod source."""

import os
import re

from .ini.sections import parse_sections
from .mod_source import DirectoryModSource, ModSourceError

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


def _has_geometry_sections(path, source):
    """Return whether an INI looks like a mod root without resolving geometry.

    Discovery must not call the full draw/mesh analyzer.  The root anchor only
    decides whether bounded nested INIs belong to this selection, so a
    conservative command-shape check is sufficient and avoids the old
    ``core.ini.sections -> core.ini.parser`` dependency.  Missing buffers are allowed;
    geometry loading reports those later.
    """
    try:
        sections = parse_sections(path, text=source.read_text(path))
    except (OSError, UnicodeError, ValueError, ModSourceError):
        return False
    has_draw = False
    has_index = False
    for name, lines in sections.items():
        if name.lower().startswith("textureoverride"):
            for raw in lines:
                line = str(raw).strip()
                if _DRAW_RE.match(line):
                    has_draw = True
                elif _IB_RE.match(line):
                    has_index = True
        elif name.lower().startswith("commandlist"):
            for raw in lines:
                if _IB_RE.match(str(raw).strip()):
                    has_index = True
    return has_draw or has_index


def discover_ini_paths(mod_dir, *, disabled=False, source=None):
    """Return selected INIs from a directory or every ZIP archive depth.

    Directory discovery retains its historical bounded nested search.  ZIP
    discovery has no filesystem root to infer, so it scans all archive members
    deterministically.  ``disabled`` selects
    only filenames beginning with ``DISABLED`` (case-insensitively); active and
    disabled files are never combined.
    """
    source = source or DirectoryModSource(mod_dir)
    if source.kind == "zip":
        return [
            source.document_path(logical)
            for logical in source.list_files()
            if _selected(logical.rsplit("/", 1)[-1], disabled=disabled)
        ]

    direct = _ini_names(mod_dir if source.kind == "directory" else "",
                        source, disabled=disabled)
    if not any(_has_geometry_sections(path, source) for path in direct):
        return direct

    found = list(direct)
    if len(found) >= _MAX_INI_FILES:
        return found
    if source.kind == "directory":
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

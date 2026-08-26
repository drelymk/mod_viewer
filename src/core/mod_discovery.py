"""Filesystem-only discovery of active INI files for a selected mod folder."""

import os
import re

from .ini.sections import parse_sections

_MAX_INI_FILES = 10
_MAX_INI_DEPTH = 2
_DRAW_RE = re.compile(r"^drawindexed\s*=", re.I)
_IB_RE = re.compile(r"^ib\s*=", re.I)


def _active_ini_names(folder):
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    return [name for name in sorted(names)
            if not name.upper().startswith("DISABLED")
            and name.lower().endswith(".ini")
            and os.path.isfile(os.path.join(folder, name))]


def _has_geometry_sections(path):
    """Return whether an INI looks like a mod root without resolving geometry.

    Discovery must not call the full draw/mesh analyzer.  The root anchor only
    decides whether bounded nested INIs belong to this selection, so a
    conservative command-shape check is sufficient and avoids the old
    ``core.ini.sections -> core.ini.parser`` dependency.  Missing buffers are allowed;
    geometry loading reports those later.
    """
    try:
        sections = parse_sections(path)
    except (OSError, UnicodeError):
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


def discover_ini_paths(mod_dir):
    """Return active direct INIs and bounded nested INIs for ``mod_dir``.

    Direct files are always retained.  Nested files are considered only when
    a direct INI contains a geometry command, and are capped at two directory
    levels and ten total files.  This keeps category/library folders from
    accidentally combining unrelated nested mods.
    """
    direct = [os.path.join(mod_dir, name) for name in _active_ini_names(mod_dir)]
    if not any(_has_geometry_sections(path) for path in direct):
        return direct

    found = list(direct)
    if len(found) >= _MAX_INI_FILES:
        return found
    for base, dirs, _files in os.walk(mod_dir):
        rel = os.path.relpath(base, mod_dir)
        depth = 0 if rel == os.curdir else len(rel.split(os.sep))
        dirs[:] = sorted(dirs) if depth < _MAX_INI_DEPTH else []
        if depth == 0:
            continue
        for name in _active_ini_names(base):
            found.append(os.path.join(base, name))
            if len(found) >= _MAX_INI_FILES:
                return found
    return found

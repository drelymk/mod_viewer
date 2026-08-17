"""Ini file discovery and section parsing — the lowest layer of the READ path.

Everything above (ini_toggles, ini_menu, ini_parser) consumes the
{section name: [SrcLine, ...]} dicts produced here.
"""

import os
import re

_DECL_RE = re.compile(r'^global\s+(?:persist\s+)?\$(\w+)\b', re.I)
_VAR_RE  = re.compile(r'\$(\w+)')

_MAX_INI_FILES = 10
_MAX_INI_DEPTH = 2


def canonical_var_names(sections):
    """{lowercased variable name: the one spelling everything should use}.

    3DMigoto variable names are case-insensitive, and mods lean on that: one
    ini routinely declares `$Body`, binds `[Key$body]`, cycles `$body` from its
    menu and gates a draw on `$Body`. Everything above this layer keys dicts by
    name, so without a single agreed spelling a toggle drives a variable no
    draw is gated on and the mesh is left permanently visible. A `global`
    declaration wins; failing that, the first spelling seen does.
    """
    declared, seen = {}, {}
    for lines in sections.values():
        for line in lines:
            text = line.strip()
            m = _DECL_RE.match(text)
            if m:
                declared.setdefault(m.group(1).lower(), m.group(1))
            for name in _VAR_RE.findall(text):
                seen.setdefault(name.lower(), name)
    for low, name in seen.items():
        declared.setdefault(low, name)
    return declared


def _active_ini_names(folder):
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    return [name for name in sorted(names)
            if not name.upper().startswith("DISABLED")
            and name.lower().endswith(".ini")
            and os.path.isfile(os.path.join(folder, name))]


def _ini_has_geometry(path):
    """Whether one INI describes at least one resolvable draw group.

    Buffer files do not have to exist yet; this is the same structural
    geometry test the loader performs before it tries to read those files.
    The import is intentionally local because ini_parser re-exports this
    discovery function.
    """
    try:
        from .ini_parser import build_draw_groups
        sections = parse_sections(path)
        return bool(build_draw_groups(sections, extract_resources(sections)))
    except Exception:
        return False


def find_inis(mod_dir):
    """Return active direct INIs, optionally including bounded nested files.

    A selected folder is a mod root only when one of its direct INIs has
    geometry. In that case active INIs are discovered up to two directories
    below it, up to ten files total unless the direct files alone exceed that
    limit. Direct INIs are never truncated: without that root anchor, retain
    the complete flat behavior so selecting a library/category folder cannot
    accidentally combine several nested mods.
    """
    direct = [os.path.join(mod_dir, name) for name in _active_ini_names(mod_dir)]
    root_geometry = next((path for path in direct if _ini_has_geometry(path)), None)
    if root_geometry is None:
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


def merge_sections(ini_paths, overrides=None):
    """Parse all ini files and merge sections with the same name.

    `overrides`, if given, is {ini_path: text} — parse that ini from this
    in-memory text instead of reading it from disk. Used to preview pending,
    not-yet-exported edits (see app/edit_session.py) without writing
    anything to the real file first; an ini not present in `overrides` is
    read from disk exactly as before.
    """
    overrides = overrides or {}
    combined: dict = {}
    for path in ini_paths:
        for name, lines in parse_sections(path, text=overrides.get(path)).items():
            combined.setdefault(name, []).extend(lines)
    return combined


class SrcLine(str):
    """A section line that remembers where it came from.

    Sections are merged across every ini in a mod folder, so by the time a
    draw is built the originating file is otherwise unrecoverable. Subclassing
    `str` keeps every existing string operation working unchanged; only code
    that actually wants provenance has to know this type exists.
    """
    __slots__ = ("ini_path", "line_no", "section")

    def __new__(cls, text, ini_path=None, line_no=None, section=None):
        obj = super().__new__(cls, text)
        obj.ini_path = ini_path
        obj.line_no  = line_no
        obj.section  = section
        return obj

    def source(self):
        return {"ini_path": self.ini_path, "line_no": self.line_no,
                "section": self.section}


def line_source(line):
    """Provenance dict for a section line, or None if it carries none."""
    if isinstance(line, SrcLine) and line.ini_path is not None:
        return line.source()
    return None


def first_source(lines):
    """Provenance of the first line in `lines` that carries any, or None."""
    for line in lines:
        src = line_source(line)
        if src:
            return src
    return None


def parse_sections(ini_path, text=None):
    """Parse one ini file's sections, either from disk (the default) or from
    `text` directly. The in-memory path lets a caller preview a pending,
    not-yet-exported edit (see app/edit_session.py) — `ini_path` still tags
    every SrcLine's provenance either way, it's just not opened when `text`
    is supplied.
    """
    sections, current = {}, None

    def feed(line_no, raw):
        nonlocal current
        line = raw.strip()
        if not line or line.startswith(";"): return
        # ';' is a legitimate 3DMigoto key binding, so it must not be
        # treated as an inline comment on key/back lines — e.g.
        # "key = no_ctrl no_Shift no_alt ;" binds the semicolon key.
        lhs = line.split("=", 1)[0].strip().lower()
        if lhs not in ("key", "back"):
            line = line.split(";")[0].strip()
        if not line: return
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip()
            sections.setdefault(current, [])
        elif current is not None:
            sections[current].append(SrcLine(line, ini_path, line_no, current))

    if text is not None:
        for line_no, raw in enumerate(text.splitlines(), 1):
            feed(line_no, raw)
    else:
        with open(ini_path, encoding="utf-8", errors="ignore") as f:
            for line_no, raw in enumerate(f, 1):
                feed(line_no, raw)
    return sections


def extract_resources(sections):
    """Return {resource_name: {filename, stride, format}} from Resource sections."""
    SKIP = ("TextureOverride", "CommandList", "ShaderOverride", "Present", "Key", "Constants")
    resources = {}
    for name, lines in sections.items():
        if any(name.startswith(p) for p in SKIP): continue
        res = {}
        for line in lines:
            if "=" not in line: continue
            k, _, v = line.partition("=")
            k = k.strip().lower(); v = v.strip()
            if   k == "filename": res["filename"] = v
            elif k == "stride":
                try: res["stride"] = int(v)
                except: pass
            elif k == "format":   res["format"] = v
        if "filename" in res:
            resources[name] = res
    return resources

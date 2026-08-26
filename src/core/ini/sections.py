"""Ini file discovery and section parsing — the lowest layer of the READ path.

Everything above (ini_toggles, ini_menu, ini_parser) consumes the
{section name: [SrcLine, ...]} dicts produced here.
"""

import os
import re

_DECL_RE = re.compile(r'^global\s+(?:persist\s+)?\$(\w+)\b', re.I)
_VAR_RE  = re.compile(r'\$(\w+)')

class ResourceTable(dict):
    """Resource records plus one canonical case-insensitive lookup index.

    Resource identifiers are case-insensitive in 3DMigoto.  Keeping the
    original spelling as the dict key preserves the existing analysis API,
    while the lower-case index avoids repeating a linear scan in every
    resolver.
    """

    def __init__(self, records=None):
        super().__init__(records or {})
        self._by_name = {}
        for name, value in self.items():
            self._by_name.setdefault(name.lower(), value)

    def get_ci(self, name):
        if not name:
            return {}
        return self.get(name) or self._by_name.get(str(name).lower(), {})


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


def merge_sections(ini_paths, overrides=None, documents=None):
    """Parse all ini files and merge sections with the same name.

    `overrides`, if given, is {ini_path: text} — parse that ini from this
    in-memory text instead of reading it from disk. Used to preview pending,
    not-yet-exported edits (see app/edit_session.py) without writing
    anything to the real file first; an ini not present in `overrides` is
    read from disk exactly as before.
    """
    overrides = overrides or {}
    documents = documents or {}
    combined: dict = {}
    for path in ini_paths:
        document = documents.get(path)
        if document is None:
            document = documents.get(os.path.normcase(os.path.abspath(path)))
        if document is not None:
            parsed = sections_from_document(document)
        else:
            parsed = parse_sections(path, text=overrides.get(path))
        for name, lines in parsed.items():
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


def sections_from_document(document):
    """Project one authoritative :class:`IniDocument` into read-path sections.

    The lossless document remains the source of truth for editing and staged
    reloads.  Analysis only needs stripped, comment-free ``SrcLine`` values,
    so this small projection lets every semantic pass consume the same parsed
    file instead of reparsing its serialized text.
    """
    sections = {}
    for section in document.sections:
        lines = sections.setdefault(section.name, [])
        for line in section.lines:
            text = line.text
            if text:
                lines.append(SrcLine(text, document.path, line.no + 1,
                                     section.name))
    return sections


def extract_resources(sections):
    """Return {resource_name: {filename, stride, format}} from Resource sections."""
    skip = ("textureoverride", "commandlist", "shaderoverride", "present",
            "key", "constants")
    resources = {}
    for name, lines in sections.items():
        if name.lower().startswith(skip): continue
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
    return ResourceTable(resources)


def find_inis(mod_dir):
    """Compatibility wrapper; new code should use mod_discovery directly."""
    from ..mod_discovery import discover_ini_paths
    return discover_ini_paths(mod_dir)

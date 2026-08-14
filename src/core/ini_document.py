"""Lossless, line-preserving model of a 3DMigoto ini file.

`ini_parser` is the read/analysis path: it strips comments, blank lines and
indentation, merges sections across files, and discards line numbers — good
for analysis, useless for writing.

This module is the write path: every source line is kept verbatim with a
structural index layered on top, so edits apply as line splices and every
untouched line survives byte-for-byte. Real mod inis are inconsistent enough
(mixed CRLF/LF within one file, missing trailing newline, occasional BOM)
that each line must carry its own terminator rather than the file having one
global setting — otherwise a save would rewrite lines nobody touched.

Typical use:

    doc = IniDocument.load(path)
    for sec in doc.sections:
        ...inspect sec.lines...
    doc.replace_lines(start, end, ["if $x == 1", "endif"])
    doc.save()          # backs up first, writes atomically
"""

import os
import re
import shutil
from datetime import datetime

BOM = "\ufeff"

# Line kinds. Deliberately coarse: this module reports structure, and leaves
# meaning (which resource, which condition) to ini_parser.
BLANK = "blank"
COMMENT = "comment"
SECTION = "section"
IF = "if"
ELIF = "elif"
ELSE = "else"
ENDIF = "endif"
DRAW = "draw"
ASSIGN = "assign"
OTHER = "other"

# Kept in step with build_draw_groups in ini_parser.py — `elif` is a real
# 3DMigoto spelling and missing it silently collapses branch conditions.
_RE_IF = re.compile(r"^if\s+(.*)$", re.I)
_RE_ELIF = re.compile(r"^(?:else\s+if|elif)\s+(.*)$", re.I)
_RE_ELSE = re.compile(r"^else$", re.I)
_RE_ENDIF = re.compile(r"^endif$", re.I)
_RE_DRAW = re.compile(r"^draw(indexed|)\s*=", re.I)
_RE_SECTION = re.compile(r"^\[(.+)\]$")


def strip_inline_comment(text):
    """Remove a trailing `;` comment, except on key bindings.

    `;` is a legitimate 3DMigoto key binding, so it must not be treated as an
    inline comment on key/back lines — e.g. `key = no_ctrl no_Shift no_alt ;`
    binds the semicolon key. Mirrors the rule in ini_parser.parse_sections.
    """
    lhs = text.split("=", 1)[0].strip().lower()
    if lhs in ("key", "back"):
        return text.strip()
    return text.split(";")[0].strip()


class Line:
    """One physical source line.

    `raw + eol` always reproduces the original bytes exactly, which is what
    makes a no-op save byte-identical.
    """

    __slots__ = ("no", "raw", "eol", "kind", "depth", "section")

    def __init__(self, no, raw, eol):
        self.no = no            # 0-based index into IniDocument.lines
        self.raw = raw          # source text, no line terminator
        self.eol = eol          # this line's own terminator ('\r\n', '\n', or '')
        self.kind = OTHER
        self.depth = 0          # if/endif nesting depth of the line itself
        self.section = None     # owning Section, or None before the first header

    @property
    def text(self):
        """Content with indentation and any inline comment removed."""
        return strip_inline_comment(self.raw.strip())

    def __repr__(self):
        return f"<Line {self.no + 1} {self.kind} {self.raw[:40]!r}>"


class Section:
    """A `[Name]` header and the lines belonging to it."""

    __slots__ = ("name", "header_no", "lines")

    def __init__(self, name, header_no):
        self.name = name
        self.header_no = header_no   # index of the `[Name]` line itself
        self.lines = []              # body lines, excluding the header

    @property
    def start(self):
        """First line index of the section, including its header."""
        return self.header_no

    @property
    def end(self):
        """Index one past the section's last line (slice-style)."""
        return (self.lines[-1].no + 1) if self.lines else self.header_no + 1

    def find(self, kind):
        return [ln for ln in self.lines if ln.kind == kind]

    def __repr__(self):
        return f"<Section {self.name!r} lines {self.start + 1}-{self.end}>"


class IniDocument:
    """A parsed ini that can be edited and written back losslessly."""

    def __init__(self, path, lines, has_bom):
        self.path = path
        self.lines = lines
        self.has_bom = has_bom
        # A file-level trait, captured before any edit can disturb it.
        self.ends_without_newline = bool(lines) and not lines[-1].eol
        self.sections = []
        self._reindex()

    # -- loading ---------------------------------------------------------

    @classmethod
    def load(cls, path):
        with open(path, "rb") as fh:
            data = fh.read()

        # errors="replace" would corrupt bytes on save. The survey found every
        # real ini decodes as UTF-8; anything else should fail loudly rather
        # than be silently mangled.
        text = data.decode("utf-8")

        has_bom = text.startswith(BOM)
        if has_bom:
            text = text[len(BOM):]

        lines = []
        for no, chunk in enumerate(text.splitlines(keepends=True)):
            raw = chunk.rstrip("\r\n")
            lines.append(Line(no, raw, chunk[len(raw):]))
        return cls(path, lines, has_bom)

    @classmethod
    def from_string(cls, text, path="<string>"):
        has_bom = text.startswith(BOM)
        if has_bom:
            text = text[len(BOM):]
        lines = []
        for no, chunk in enumerate(text.splitlines(keepends=True)):
            raw = chunk.rstrip("\r\n")
            lines.append(Line(no, raw, chunk[len(raw):]))
        return cls(path, lines, has_bom)

    # -- structure -------------------------------------------------------

    def _reindex(self):
        """Recompute line numbers, kinds, nesting depth and section spans.

        Called after every mutation, so a spliced document is never observed
        with stale indices.
        """
        self.sections = []
        current = None
        depth = 0

        for no, line in enumerate(self.lines):
            line.no = no
            stripped = line.raw.strip()
            text = line.text

            if not stripped:
                line.kind = BLANK
            elif stripped.startswith(";"):
                line.kind = COMMENT
            elif _RE_SECTION.match(stripped):
                line.kind = SECTION
            elif not text:
                # Was only an inline comment once stripped.
                line.kind = COMMENT
            elif _RE_ENDIF.match(text):
                line.kind = ENDIF
            elif _RE_ELIF.match(text):
                line.kind = ELIF
            elif _RE_ELSE.match(text):
                line.kind = ELSE
            elif _RE_IF.match(text):
                line.kind = IF
            elif _RE_DRAW.match(text):
                line.kind = DRAW
            elif "=" in text:
                line.kind = ASSIGN
            else:
                line.kind = OTHER

            # `endif` closes at the parent's depth; `elif`/`else` sit at the
            # depth of the `if` they continue, not inside it.
            if line.kind == ENDIF:
                depth = max(0, depth - 1)
                line.depth = depth
            elif line.kind in (ELIF, ELSE):
                line.depth = max(0, depth - 1)
            else:
                line.depth = depth
                if line.kind == IF:
                    depth += 1

            if line.kind == SECTION:
                name = _RE_SECTION.match(stripped).group(1).strip()
                current = Section(name, no)
                self.sections.append(current)
                line.section = current
                depth = 0   # sections don't nest, so never leak depth across one
            else:
                line.section = current
                if current is not None:
                    current.lines.append(line)

    def section(self, name):
        """First section with this name, case-insensitively, else None.

        3DMigoto section names are case-insensitive, and a single file can
        legitimately repeat a name — callers that care should use `sections`.
        """
        lowered = name.lower()
        for sec in self.sections:
            if sec.name.lower() == lowered:
                return sec
        return None

    def structure_errors(self):
        """Report ambiguous if/elif/else/endif nesting and branch order.

        Roughly 5% of real mod files contain at least one such section: a
        stray `endif` that closes an already-closed block, an `else if` with
        no open `if`, or an `if` that is never closed. 3DMigoto tolerates
        these, so they are not load errors — but they make the intended
        structure ambiguous, and rewriting a gate inside one could change
        which draws are conditional.

        Returns [{section, line, problem}]; callers that rewrite gates should
        refuse to touch any section named here.
        """
        problems = []
        for sec in self.sections:
            open_ifs = []  # [{line: opening Line, saw_else: bool}, ...]
            for line in sec.lines:
                if line.kind == IF:
                    open_ifs.append({"line": line, "saw_else": False})
                elif line.kind == ENDIF:
                    if open_ifs:
                        open_ifs.pop()
                    else:
                        problems.append({"section": sec.name, "line": line.no,
                                         "problem": "endif without a matching if"})
                elif line.kind in (ELIF, ELSE):
                    if not open_ifs:
                        problems.append({"section": sec.name, "line": line.no,
                                         "problem": f"{line.kind} without an open if"})
                    elif line.kind == ELIF and open_ifs[-1]["saw_else"]:
                        problems.append({"section": sec.name, "line": line.no,
                                         "problem": "elif after else"})
                    elif line.kind == ELSE:
                        if open_ifs[-1]["saw_else"]:
                            problems.append({"section": sec.name, "line": line.no,
                                             "problem": "duplicate else"})
                        else:
                            open_ifs[-1]["saw_else"] = True
            if open_ifs:
                # Point at the first opening line that never closes, rather
                # than the section boundary. The latter made a blank line or
                # the next header look responsible for the error.
                problems.append({"section": sec.name, "line": open_ifs[0]["line"].no,
                                 "problem": f"{len(open_ifs)} unclosed if"})
        return problems

    def syntax_errors(self):
        """Report high-confidence lexical errors in headers and conditions.

        This intentionally does not validate arbitrary assignments or section
        types: 3DMigoto extensions evolve, and unfamiliar syntax is not proof
        of an error. Returns [{code, section, line, problem}].
        """
        problems = []

        def add(code, line, problem, section=True):
            problems.append({
                "code": code,
                "section": (line.section.name
                            if section and line.section is not None else None),
                "line": line.no,
                "problem": problem,
            })

        for line in self.lines:
            text = line.text
            if not text:
                continue

            # A header ends at its first `]`; anything except whitespace or a
            # stripped comment after that is not part of the section name.
            if text.startswith("["):
                close = text.find("]")
                if close < 0:
                    add("malformed_section_header", line,
                        "section header is missing a closing ]", section=False)
                elif not text[1:close].strip():
                    add("malformed_section_header", line,
                        "section header has an empty name", section=False)
                elif text[close + 1:].strip():
                    add("malformed_section_header", line,
                        "unexpected content after section header", section=False)
                continue

            lowered = text.lower()
            if re.match(r"^elseif\b", lowered):
                add("malformed_condition_syntax", line,
                    "use 'elif' or 'else if', not 'elseif'")
                continue
            if re.match(r"^(?:if|elif)\s*$", lowered):
                add("malformed_condition_syntax", line,
                    "condition is missing an expression")
                continue
            if re.match(r"^else\s+if\s*$", lowered):
                add("malformed_condition_syntax", line,
                    "else if is missing an expression")
                continue
            if re.match(r"^(?:if|elif)(?=[$!(])", lowered):
                add("malformed_condition_syntax", line,
                    "condition keyword must be followed by a space")
                continue
            if re.match(r"^else\s+if(?=[$!(])", lowered):
                add("malformed_condition_syntax", line,
                    "else if must be followed by a space")
                continue
            if re.match(r"^else\s+if\b", lowered):
                condition = re.sub(r"^else\s+if\s+", "", text,
                                   count=1, flags=re.I)
            elif re.match(r"^(?:if|elif)\s+", lowered):
                condition = re.sub(r"^(?:if|elif)\s+", "", text,
                                   count=1, flags=re.I)
            else:
                condition = None

            if condition is not None:
                depth = 0
                unmatched_close = False
                for char in condition:
                    if char == "(":
                        depth += 1
                    elif char == ")":
                        if depth == 0:
                            unmatched_close = True
                            break
                        depth -= 1
                if unmatched_close:
                    add("unbalanced_condition_parentheses", line,
                        "condition has a closing ) without a matching (")
                elif depth:
                    add("unbalanced_condition_parentheses", line,
                        f"condition has {depth} unclosed (")
                continue

            if re.match(r"^else\s+.+", lowered):
                add("malformed_condition_syntax", line,
                    "else must not have trailing content")
            elif re.match(r"^endif\s+.+", lowered):
                add("malformed_condition_syntax", line,
                    "endif must not have trailing content")

        return problems

    def is_safe_to_rewrite(self, section_name):
        """True if this section's gate structure is unambiguous."""
        lowered = section_name.lower()
        return not any(p.get("section") and p["section"].lower() == lowered
                       for p in self.structure_errors() + self.syntax_errors())

    # -- editing ---------------------------------------------------------

    def _default_eol(self):
        """The dominant terminator, used for inserted lines.

        Inserted lines have no original terminator to preserve, so they adopt
        the file's prevailing one rather than the platform's.
        """
        for line in self.lines:
            if line.eol:
                return line.eol
        return "\r\n"

    def replace_lines(self, start, end, new_texts):
        """Replace lines[start:end] with `new_texts`.

        Terminators are reused positionally from the replaced lines, so an edit
        that keeps the line count cannot alter line endings; any extra inserted
        lines adopt the file's dominant terminator.
        """
        if start < 0 or end > len(self.lines) or start > end:
            raise IndexError(f"invalid line range {start}:{end}")

        old_eols = [ln.eol for ln in self.lines[start:end]]
        default = self._default_eol()

        replacement = [
            Line(start + i, text, old_eols[i] if i < len(old_eols) else default)
            for i, text in enumerate(new_texts)
        ]

        self.lines[start:end] = replacement
        self._fix_terminators()
        self._reindex()
        return replacement

    def _fix_terminators(self):
        """Maintain two invariants after a splice.

        1. Only the final line may lack a terminator — otherwise two lines
           would fuse into one.
        2. Whether the *file* ends with a newline is a property of the file,
           not of whichever line is currently last. 798 of 5,524 real inis end
           without one; appending to such a file must not silently add it.
        """
        if not self.lines:
            return
        default = self._default_eol()
        for line in self.lines[:-1]:
            if not line.eol:
                line.eol = default
        last = self.lines[-1]
        last.eol = "" if self.ends_without_newline else (last.eol or default)

    def insert_lines(self, at, new_texts):
        return self.replace_lines(at, at, new_texts)

    def delete_lines(self, start, end):
        self.replace_lines(start, end, [])

    # -- output ----------------------------------------------------------

    def to_string(self):
        body = "".join(ln.raw + ln.eol for ln in self.lines)
        return (BOM + body) if self.has_bom else body

    def to_bytes(self):
        return self.to_string().encode("utf-8")

    def backup_path(self, when=None):
        """`mod.ini` -> `mod.ini_2026-08-01 20-43-09.BAK`.

        find_inis() only accepts names ending in `.ini`, so a `.BAK` is never
        re-loaded by the app — and 3DMigoto ignores it too.
        """
        stamp = (when or datetime.now()).strftime("%Y-%m-%d %H-%M-%S")
        return f"{self.path}_{stamp}.BAK"

    def save(self, backup=True):
        """Write the document back, atomically, after backing it up.

        Returns the backup path, or None when no backup was taken.

        The temp file is written in the same directory so os.replace is a true
        atomic rename (it is not atomic across volumes); an interrupted save
        therefore leaves the original intact rather than truncated.
        """
        made = None
        if backup and os.path.exists(self.path):
            made = self.backup_path()
            shutil.copy2(self.path, made)

        tmp = f"{self.path}.tmp"
        try:
            with open(tmp, "wb") as fh:
                fh.write(self.to_bytes())
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
        return made

    def __repr__(self):
        return f"<IniDocument {os.path.basename(self.path)} " \
               f"{len(self.lines)} lines, {len(self.sections)} sections>"

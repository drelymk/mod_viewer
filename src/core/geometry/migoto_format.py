"""Bounded semantic parser for 3DMigoto binary ``.fmt`` files."""

from dataclasses import dataclass
import os
import re

from .migoto_dump import MAX_LINE_LENGTH, MAX_SEMANTICS, MAX_TEXT_FILE_BYTES


class MigotoFormatError(ValueError):
    """A malformed or unsupported binary layout description."""


@dataclass(frozen=True, slots=True)
class MigotoElement:
    semantic_name: str
    semantic_index: int
    format: str
    offset: int


@dataclass(frozen=True, slots=True)
class MigotoBinaryLayout:
    stride: int
    topology: str
    index_format: str
    elements: tuple[MigotoElement, ...]

    def semantic(self, name, index=0):
        name = str(name).upper()
        return next((item for item in self.elements
                     if item.semantic_name == name
                     and item.semantic_index == index), None)


_HEADER_RE = re.compile(
    r"^\s*(stride|topology|format|semanticname|semanticindex|"
    r"alignedbyteoffset|byteoffset|offset)\s*[:=]\s*(.*?)\s*$", re.I)
_ELEMENT_RE = re.compile(r"^\s*element\s*\[\s*\d+\s*\]\s*:\s*(.*)$", re.I)
_SEMANTIC_RE = re.compile(r"\b(POSITION|NORMAL|TEXCOORD)(\d*)\b", re.I)
_FORMAT_VALUE_RE = re.compile(r"\bformat\s*[:=]\s*([A-Za-z0-9_]+)", re.I)
_OFFSET_VALUE_RE = re.compile(
    r"(?:aligned\s*byte\s*offset|byte\s*offset|offset)\s*[:=]\s*(\d+)",
    re.I)


def _read_lines(path):
    try:
        if os.path.getsize(path) > MAX_TEXT_FILE_BYTES:
            raise MigotoFormatError(".fmt file exceeds the text-file safety limit.")
        with open(path, "r", encoding="utf-8", errors="replace") as stream:
            for line_number, raw_line in enumerate(stream, 1):
                if len(raw_line) > MAX_LINE_LENGTH:
                    raise MigotoFormatError(
                        f".fmt line {line_number} exceeds the safety limit.")
                yield raw_line.strip()
    except OSError as error:
        raise MigotoFormatError(
            f"Could not read .fmt file: {os.path.basename(path)}.") from error


def _canonical_topology(value):
    return re.sub(r"[\s_-]", "", str(value).casefold())


def _format(value):
    return str(value or "").strip().upper().removeprefix("DXGI_FORMAT_")


def _inline_element(value):
    semantic = _SEMANTIC_RE.search(value)
    format_match = _FORMAT_VALUE_RE.search(value)
    offset_match = _OFFSET_VALUE_RE.search(value)
    if not semantic or not format_match or not offset_match:
        return None
    return MigotoElement(
        semantic.group(1).upper(), int(semantic.group(2) or 0),
        _format(format_match.group(1)), int(offset_match.group(1)))


def parse_fmt(path):
    """Parse the stride, index format, and declared vertex semantics."""
    stride = None
    topology = None
    index_format = None
    elements = []
    current = None

    def flush():
        nonlocal current
        if not current:
            return
        if (current.get("name") and current.get("format")
                and current.get("offset") is not None):
            elements.append(MigotoElement(
                current["name"], current.get("index", 0),
                _format(current["format"]), current["offset"]))
        current = None

    for line in _read_lines(path):
        if not line or line.startswith(("#", ";", "//")):
            continue
        element_match = _ELEMENT_RE.match(line)
        if element_match:
            flush()
            current = {}
            inline = _inline_element(element_match.group(1))
            if inline:
                elements.append(inline)
                current = None
            continue
        match = _HEADER_RE.match(line)
        if not match:
            continue
        key, value = match.group(1).casefold(), match.group(2).strip()
        if key == "stride":
            try:
                stride = int(value)
            except ValueError as error:
                raise MigotoFormatError(".fmt stride is invalid.") from error
        elif key == "topology":
            topology = _canonical_topology(value)
        elif key == "format":
            if current is None:
                index_format = _format(value)
            else:
                current["format"] = value
        elif current is not None:
            if key == "semanticname":
                semantic = _SEMANTIC_RE.search(value)
                if semantic:
                    current["name"] = semantic.group(1).upper()
                    current["index"] = int(semantic.group(2) or 0)
            elif key == "semanticindex":
                try:
                    current["index"] = int(value)
                except ValueError:
                    pass
            elif key in {"alignedbyteoffset", "byteoffset", "offset"}:
                try:
                    current["offset"] = int(value)
                except ValueError:
                    pass
        if len(elements) > MAX_SEMANTICS:
            raise MigotoFormatError(".fmt has too many semantic elements.")
    flush()
    if not stride or stride <= 0:
        raise MigotoFormatError(".fmt stride is missing or invalid.")
    if topology and topology not in {"trianglelist", "triangles"}:
        raise MigotoFormatError(".fmt is not triangle-list topology.")
    if not index_format:
        raise MigotoFormatError(".fmt index format is missing.")
    if not elements:
        raise MigotoFormatError(".fmt contains no vertex semantic elements.")
    return MigotoBinaryLayout(
        stride, topology or "trianglelist", index_format, tuple(elements))


__all__ = [
    "MigotoBinaryLayout", "MigotoElement", "MigotoFormatError", "parse_fmt",
]

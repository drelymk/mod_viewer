"""Bounded, format-tolerant readers for 3DMigoto text dumps.

3DMigoto dump headers have changed slightly between tools and game forks.  The
semantic names and the values themselves are the stable contract, so this
parser keys off those declarations instead of a hard-coded vertex stride.
"""

from dataclasses import dataclass, field
import math
import os
import re
import struct


MAX_TEXT_FILE_BYTES = 512 * 1024 * 1024
MAX_VERTEX_COUNT = 5_000_000
MAX_INDEX_COUNT = 15_000_000
MAX_LINE_LENGTH = 16_384
MAX_SEMANTIC_VALUES = 8
MAX_SEMANTICS = 128


class MigotoDumpError(ValueError):
    """A malformed, unsupported, or unsafe text dump."""


@dataclass(frozen=True, slots=True)
class DumpSemantic:
    name: str
    index: int
    offset: int | None = None
    format: str | None = None


@dataclass(frozen=True, slots=True)
class DumpVertexLayout:
    stride: int
    vertex_count: int
    semantics: tuple[DumpSemantic, ...]


@dataclass(slots=True)
class ParsedVertexDump:
    layout: DumpVertexLayout
    positions: bytes
    normals: bytes | None
    uvs: bytes | None


_SEMANTIC_RE = re.compile(
    r"\b(POSITION|NORMAL|TEXCOORD)(\d*)\b", re.I)
_INDEX_RE = re.compile(r"(?:vb\d+\s*\[\s*(\d+)\s*\]|vertex\s*(\d+)|"
                       r"\b(?:v|vertex)\s*[:=]?\s*(\d+))", re.I)
_NUMBER_RE = re.compile(
    r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?|"
    r"[-+]?(?:nan|inf(?:inity)?)", re.I)
_HEADER_INT = re.compile(
    r"^\s*(stride|vertex\s*count|vertices|count)\s*[:=]\s*(\d+)\b",
    re.I)
_OFFSET_RE = re.compile(
    r"(?:aligned[_ ]byte[_ ]offset|byte[_ ]offset|offset)\s*[:=]\s*(\d+)",
    re.I)
_FORMAT_RE = re.compile(r"\bformat\s*[:=]\s*([A-Za-z0-9_]+)", re.I)


def _readable_path(path):
    if not isinstance(path, (str, os.PathLike)):
        raise MigotoDumpError("Dump path is invalid.")
    path = os.fspath(path)
    try:
        size = os.path.getsize(path)
    except OSError as error:
        raise MigotoDumpError(f"Dump is missing: {os.path.basename(path)}.") from error
    if size > MAX_TEXT_FILE_BYTES:
        raise MigotoDumpError("Dump exceeds the text-file safety limit.")
    return path


def _semantic_from_line(line):
    match = _SEMANTIC_RE.search(line)
    if not match:
        return None
    name = match.group(1).upper()
    index = int(match.group(2) or 0)
    return name, index


def _float_values(text):
    values = []
    for value in _NUMBER_RE.findall(text):
        try:
            parsed = float(value)
        except ValueError:
            continue
        if not math.isfinite(parsed):
            raise MigotoDumpError("Dump contains a non-finite vertex value.")
        values.append(parsed)
    return values


def _vertex_index(line):
    match = _INDEX_RE.search(line)
    if not match:
        return None
    for value in match.groups():
        if value is not None:
            return int(value)
    return None


def _layout_declaration(line, semantics, state):
    header = _HEADER_INT.match(line)
    if header:
        key, value = header.group(1).casefold(), int(header.group(2))
        if key == "stride":
            state["stride"] = value
        else:
            state["count"] = value
            if value > MAX_VERTEX_COUNT:
                raise MigotoDumpError("Dump vertex count exceeds the safety limit.")
        return
    semantic = _semantic_from_line(line)
    if not semantic:
        return
    name, index = semantic
    # A declaration is distinguished from a vertex row by the absence of a
    # concrete vertex index.  Compact declarations such as
    # `POSITION0: offset 0` are supported alongside element blocks.
    if _vertex_index(line) is not None:
        return
    offset_match = _OFFSET_RE.search(line)
    format_match = _FORMAT_RE.search(line)
    if len(semantics) >= MAX_SEMANTICS and (name, index) not in semantics:
        raise MigotoDumpError("Dump semantic declaration exceeds the safety limit.")
    semantics[(name, index)] = DumpSemantic(
        name, index,
        int(offset_match.group(1)) if offset_match else None,
        format_match.group(1) if format_match else None)


def parse_vertex_dump(path, *, max_vertices=MAX_VERTEX_COUNT):
    """Stream one text VB dump and return canonical XYZ/UV byte arrays."""
    path = _readable_path(path)
    max_vertices = min(int(max_vertices), MAX_VERTEX_COUNT)
    state = {"stride": 0, "count": None}
    declarations = {}
    values = {"POSITION": {}, "NORMAL": {}, "TEXCOORD": {}}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as stream:
            for line_number, raw_line in enumerate(stream, 1):
                if len(raw_line) > MAX_LINE_LENGTH:
                    raise MigotoDumpError(
                        f"Dump line {line_number} exceeds the safety limit.")
                line = raw_line.strip()
                if not line or line.startswith(("#", ";", "//")):
                    continue
                _layout_declaration(line, declarations, state)
                semantic = _semantic_from_line(line)
                vertex = _vertex_index(line)
                if not semantic or vertex is None:
                    continue
                name, index = semantic
                if name not in values or index != 0:
                    continue
                text = line.split(":", 1)[1] if ":" in line else line
                parsed = _float_values(text)
                needed = 2 if name == "TEXCOORD" else 3
                if len(parsed) < needed:
                    raise MigotoDumpError(
                        f"Dump line {line_number} has an incomplete {name} value.")
                if vertex < 0 or vertex >= max_vertices:
                    raise MigotoDumpError("Dump vertex count exceeds the safety limit.")
                if len(values[name]) >= max_vertices and vertex not in values[name]:
                    raise MigotoDumpError("Dump vertex count exceeds the safety limit.")
                values[name][vertex] = tuple(parsed[:needed])
    except OSError as error:
        raise MigotoDumpError(f"Could not read dump: {error}") from error

    declared_count = state["count"]
    highest = max((index for group in values.values() for index in group), default=-1)
    vertex_count = declared_count or highest + 1
    if vertex_count <= 0 or vertex_count > max_vertices:
        raise MigotoDumpError("Dump contains no usable vertices.")
    if state["count"] is not None and highest >= vertex_count:
        raise MigotoDumpError("Dump contains a vertex outside its declared range.")
    positions = values["POSITION"]
    if len(positions) < vertex_count or any(i not in positions for i in range(vertex_count)):
        raise MigotoDumpError("Dump is missing POSITION0 values.")

    def pack(name, width):
        source = values[name]
        if not source or any(i not in source for i in range(vertex_count)):
            return None
        result = bytearray(vertex_count * width * 4)
        for index in range(vertex_count):
            struct.pack_into(f"<{width}f", result, index * width * 4,
                             *source[index])
        return bytes(result)

    semantic_items = tuple(declarations.values())
    layout = DumpVertexLayout(
        int(state["stride"] or 0), vertex_count, semantic_items)
    return ParsedVertexDump(layout, pack("POSITION", 3),
                             pack("NORMAL", 3), pack("TEXCOORD", 2))


def parse_index_dump(path, *, vertex_count=None, max_indices=MAX_INDEX_COUNT):
    """Stream a text IB dump, keeping complete valid triangles only."""
    path = _readable_path(path)
    values = []
    topology = None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as stream:
            for line_number, raw_line in enumerate(stream, 1):
                if len(raw_line) > MAX_LINE_LENGTH:
                    raise MigotoDumpError(
                        f"Index dump line {line_number} exceeds the safety limit.")
                line = raw_line.strip()
                if not line or line.startswith(("#", ";", "//")):
                    continue
                topology_match = re.search(r"topology\s*[:=]\s*([^\s]+)", line, re.I)
                if topology_match:
                    topology = topology_match.group(1).casefold()
                    continue
                if "index count" in line.casefold() or "format" in line.casefold():
                    continue
                if ":" in line:
                    line = line.split(":", 1)[1]
                elif "=" in line:
                    line = line.split("=", 1)[1]
                else:
                    marker = re.search(r"\]\s*\+\d+\s+", line)
                    if marker:
                        line = line[marker.end():]
                numbers = re.findall(r"[-+]?\d+", line)
                if not numbers:
                    continue
                for raw_value in numbers:
                    value = int(raw_value)
                    if value < 0:
                        raise MigotoDumpError("Index dump contains a negative index.")
                    values.append(value)
                    if len(values) > max_indices:
                        raise MigotoDumpError("Index count exceeds the safety limit.")
    except OSError as error:
        raise MigotoDumpError(f"Could not read index dump: {error}") from error
    if topology and topology not in {"trianglelist", "triangle_list", "triangles"}:
        raise MigotoDumpError("Index dump is not triangle-list topology.")
    usable = len(values) - (len(values) % 3)
    values = values[:usable]
    valid = []
    for start in range(0, len(values), 3):
        triangle = values[start:start + 3]
        if vertex_count is not None and any(index >= vertex_count for index in triangle):
            continue
        valid.extend(triangle)
    if not valid:
        raise MigotoDumpError("Index dump contains no complete valid triangles.")
    return list(valid)


def pack_indices(indices):
    values = tuple(int(value) for value in indices)
    if len(values) % 3 or any(value < 0 for value in values):
        raise MigotoDumpError("Canonical indices must contain complete triangles.")
    return struct.pack(f"<{len(values)}I", *values)


# Short aliases keep the transport layer convenient for format adapters and
# preserve the vocabulary used by existing 3DMigoto tooling.
parse_vb_dump = parse_vertex_dump
parse_ib_dump = parse_index_dump


__all__ = [
    "DumpSemantic", "DumpVertexLayout", "MigotoDumpError",
    "ParsedVertexDump", "pack_indices", "parse_index_dump",
    "parse_ib_dump", "parse_vb_dump", "parse_vertex_dump",
]

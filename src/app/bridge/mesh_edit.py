"""Authoritative staging of viewer loose-part edits."""

import hashlib
import os
import re

from app.session import edit as edit_session
from app.mods.analysis import resolved_draws
from core.editing.mesh_layout import (
    MeshLayoutError, drawindexed_ranges, repack_index_bytes,
)
from core.editing.record import resolve_draw_references
from core.geometry.identity import mesh_identity_for_draw
from core.mod_source import ModSourceError
from core.ini.document import BLANK, COMMENT, DRAW
from core.ini.command_phases import parse_command_line
from core.geometry.identity import normalize_geometry_hash
from core.resource_paths import safe_resource_path


_DRAW_LINE = re.compile(
    r"^(?P<indent>\s*)(?P<lhs>drawindexed\s*=\s*)"
    r"(?P<count>\d+)\s*,\s*(?P<start>-?\d+)\s*,\s*(?P<base>-?\d+)"
    r"(?P<tail>\s*(?:;.*)?)$", re.I)
_AUTO_LINE = re.compile(
    r"^(?P<indent>\s*)(?P<lhs>drawindexed\s*=\s*)auto"
    r"(?P<tail>\s*(?:;.*)?)$", re.I)


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _source_key(source):
    ini = str(source.get("ini", "")).replace("\\", "/")
    return (ini.casefold(), str(source.get("section", "")).casefold(),
            _occurrence_key(source.get("occurrence")))


def _occurrence_key(value):
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    if not isinstance(value, dict):
        return ("", -1, ())
    path = []
    for item in value.get("path") or []:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            path.append((str(item[0] or "").casefold(), item[1]))
    return (str(value.get("section", "")).casefold(),
            int(value.get("ordinal", -1)), tuple(path))


def _authoritative_source_key(mod_dir, source, source_object):
    path = source.get("ini_path")
    if source_object is not None and getattr(source_object, "virtual", False):
        ini = source_object.logical_path(path)
    else:
        ini = os.path.relpath(path, mod_dir).replace(os.sep, "/")
    occurrence = source.get("occurrence") or {}
    return (ini.casefold(), str(source.get("section", "")).casefold(),
            _occurrence_key(occurrence))


def _resolve_ib_path(context, draw):
    path = draw.ib_file
    source = getattr(context, "source", None)
    resolved = source.resolve_resource(path) if source is not None else path
    if not resolved or not os.path.isfile(resolved):
        raise ValueError("The authored index buffer is unavailable.")
    return os.path.abspath(resolved)


def _resolve_ib_reference(context, draw):
    """Resolve another draw's IB without requiring the resource to exist."""
    path = draw.ib_file
    if not path:
        return None
    source = getattr(context, "source", None)
    try:
        resolved = source.resolve_resource(path) if source is not None else path
        return os.path.abspath(resolved) if resolved else None
    except (ModSourceError, OSError, TypeError, ValueError):
        return None


def _ib_path_key(path):
    return os.path.normcase(os.path.abspath(path))


def _draw_byte_range(draw, data_length):
    index_size = int(draw.index_size)
    if index_size <= 0:
        raise ValueError("The authored index size is invalid.")
    if draw.count is None:
        return 0, data_length
    start = int(draw.start)
    count = int(draw.count)
    return start * index_size, (start + count) * index_size


def _validate_draw_overlap(context, authoritative, edited_draw, edited_path,
                           edited_range, data_length):
    edited_path_key = _ib_path_key(edited_path)
    for other_draw, _group in authoritative.values():
        other_path = _resolve_ib_reference(context, other_draw)
        if other_path is None:
            continue
        if _ib_path_key(other_path) != edited_path_key:
            continue
        other_range = _draw_byte_range(other_draw, data_length)
        if not (edited_range[0] < other_range[1]
                and other_range[0] < edited_range[1]):
            continue
        if edited_draw.auto_draw and other_draw is not edited_draw:
            raise ValueError("The automatic draw has incompatible buffer ownership.")
        same_authored_range = (
            other_draw.start == edited_draw.start
            and other_draw.count == edited_draw.count
            and other_draw.index_size == edited_draw.index_size)
        if not same_authored_range:
            raise ValueError(
                "The edited index range overlaps another draw in the same "
                "index buffer.")


def _draw_lines(doc, line_no, ranges):
    match = (_DRAW_LINE.match(doc.lines[line_no].raw)
             or _AUTO_LINE.match(doc.lines[line_no].raw))
    if match is None:
        raise ValueError("The authored draw line is no longer writable.")
    prefix = match.group("indent") + match.group("lhs")
    tail = match.group("tail")
    lines = []
    for index, (count, start, base) in enumerate(ranges):
        lines.append(f"{prefix}{count}, {start}, {base}"
                     + (tail if index == 0 else ""))
    return lines


def _values(section, key):
    return [line for line in section.lines
            if line.text.partition("=")[0].strip().casefold() == key]


def _value(section, key, default=None):
    lines = _values(section, key)
    if not lines:
        return default
    if len(lines) != 1 or lines[0].depth:
        raise ValueError("Automatic draw declarations are ambiguous.")
    return lines[0].text.partition("=")[2].strip()


def _section(doc, name):
    matches = [section for section in doc.sections
               if section.name.casefold() == str(name).casefold()]
    if len(matches) != 1:
        raise ValueError("Automatic draw sections are missing or ambiguous.")
    return matches[0]


def _validate_auto_commands(doc, section, visiting=(), draw_line=None,
                            protected_resources=()):
    """Prove that calls before a relocated draw cannot draw or run post work."""
    if section.name.casefold() in visiting or len(visiting) >= 64:
        raise ValueError("Automatic draws cannot migrate recursive command lists.")
    for line in section.lines:
        if line.kind in (BLANK, COMMENT):
            continue
        if line is draw_line:
            continue
        command = parse_command_line(line.raw)
        key, _, value = command.text.partition("=")
        key = key.strip().casefold()
        if key in protected_resources:
            raise ValueError("Automatic draw commands mutate replacement index buffers.")
        if command.phase is not None:
            raise ValueError("Automatic draw migration cannot preserve phased commands.")
        if key == "run":
            target = value.strip()
            if not target.casefold().startswith("commandlist"):
                raise ValueError("Automatic draw migration requires plain command lists.")
            _validate_auto_commands(
                doc, _section(doc, target), (*visiting, section.name.casefold()),
                protected_resources=protected_resources)
        elif key == "handling" and value.strip().casefold() == "skip" and not line.depth:
            continue
        elif re.fullmatch(r"(?:hash|match_first_index|match_priority|ib|vb\d+|ps-t\d+|"
                          r"resource[\\/].+|\$[\w\\]+)", key):
            if visiting and key in {"ib", "hash", "match_first_index", "match_priority"}:
                raise ValueError("Automatic draw command lists change buffer ownership.")
        elif (line.kind != DRAW and
              (command.text.casefold().startswith(("if ", "elif ", "else if "))
               or command.text.casefold() in {"else", "endif"})):
            continue
        else:
            raise ValueError("Automatic draw execution dependencies are unsupported.")


def _auto_buffer(context, doc, component):
    """Resolve a literal replacement IB with no offsets, copies or guessed format."""
    resource = _value(component, "ib")
    if not resource:
        raise ValueError("Automatic draws require one exact replacement index buffer.")
    section = _section(doc, resource)
    allowed = {"filename", "format", "type"}
    if any(line.kind not in (BLANK, COMMENT) and
           line.text.partition("=")[0].strip().casefold() not in allowed
           for line in section.lines):
        raise ValueError("Automatic index buffer layout is unsupported.")
    if _value(section, "type", "buffer").casefold() != "buffer":
        raise ValueError("Automatic draws require a buffer resource.")
    fmt = re.sub(r"^DXGI_FORMAT_", "", _value(section, "format", "").upper())
    if fmt not in {"R16_UINT", "R32_UINT"}:
        raise ValueError("Only explicit 16-bit and 32-bit index formats are supported.")
    filename = _value(section, "filename")
    if not filename:
        raise ValueError("Automatic index buffers require an authored filename.")
    # Resource paths are relative to the declaring INI, through the shared sandbox.
    path = safe_resource_path(os.path.dirname(doc.path), filename)
    if not path or not os.path.isfile(path):
        raise ValueError("The automatic replacement index buffer is unavailable.")
    path = os.path.abspath(path)
    size = 2 if fmt == "R16_UINT" else 4
    data = edit_session.ib_overrides_for(context.mod_dir).get(path)
    length = len(data) if data is not None else os.path.getsize(path)
    if not length or length % (size * 3):
        raise ValueError("Automatic index buffers must contain complete triangles.")
    return path, size


def _auto_patches(context, draw, authoritative, ranges):
    """Validate the complete affected family before planning any document splice."""
    if not draw.auto_draw or len(draw.sources) != 1:
        raise ValueError("The automatic draw has ambiguous authored provenance.")
    source = draw.sources[0]
    occurrence = source.get("occurrence") or {}
    if (source.get("phase") != "pre" or source.get("conditional")
            or occurrence.get("path")):
        raise ValueError("The automatic draw has unsupported execution dependencies.")
    docs = edit_session.documents_for(context.mod_dir)
    doc = docs.get(source.get("ini_path"))
    if doc is None or doc.structure_errors():
        raise ValueError("The automatic draw document is unavailable or malformed.")
    owner = _section(doc, source.get("section"))
    auto_lines = [line for line in owner.lines if line.kind == DRAW]
    if (len(auto_lines) != 1 or auto_lines[0].depth
            or not _AUTO_LINE.match(auto_lines[0].raw)
            or occurrence.get("ordinal") != 0):
        raise ValueError("The automatic draw instruction is missing or conflicting.")
    component = _section(doc, draw.auto_draw["component"])
    path, size = _auto_buffer(context, doc, component)
    if _ib_path_key(path) != _ib_path_key(_resolve_ib_path(context, draw)) or size != draw.index_size:
        raise ValueError("The automatic replacement index buffer is ambiguous.")
    if owner is component:
        # Rewrite in place, preserving its complete command and guard order.
        if _values(component, "ib")[0].no >= auto_lines[0].no:
            raise ValueError("The replacement index buffer is not bound at the automatic draw.")
        geometry_hash = _value(component, "hash")
        for current_doc in docs.values():
            for section in current_doc.sections:
                if section is component or not section.name.casefold().startswith("textureoverride"):
                    continue
                if not geometry_hash or not any(
                        normalize_geometry_hash(line.text.partition("=")[2].strip()) ==
                        normalize_geometry_hash(geometry_hash)
                        for line in _values(section, "hash")):
                    continue
                if (not _values(section, "match_first_index")
                        and any(line.kind == DRAW or
                                parse_command_line(line.raw).text.casefold().startswith("run")
                                for line in section.lines)):
                    raise ValueError("The local automatic draw conflicts with a shared draw.")
        _validate_auto_commands(
            doc, component, draw_line=auto_lines[0],
            protected_resources={_value(component, "ib").casefold()})
        return [(doc.path, auto_lines[0].no, auto_lines[0].no + 1,
                 _draw_lines(doc, auto_lines[0].no, ranges))]

    geometry_hash = _value(owner, "hash")
    if not geometry_hash or _value(owner, "handling", "").casefold() != "skip":
        raise ValueError("Shared automatic draws require an unconditional skip override.")
    allowed = {"hash", "handling", "drawindexed", "match_priority"}
    if any(line.kind not in (BLANK, COMMENT) and
           (line.depth or line.text.partition("=")[0].strip().casefold() not in allowed)
           for line in owner.lines):
        raise ValueError("Shared automatic draw execution dependencies are unsupported.")
    for key in allowed:
        _value(owner, key)
    family = []
    for current_doc in docs.values():
        for section in current_doc.sections:
            if not section.name.casefold().startswith("textureoverride"):
                continue
            hashes = _values(section, "hash")
            if not any(normalize_geometry_hash(line.text.partition("=")[2].strip()) ==
                       normalize_geometry_hash(geometry_hash) for line in hashes):
                continue
            if current_doc is not doc or len(hashes) != 1 or hashes[0].depth:
                raise ValueError("Shared automatic draw families cross ambiguous INI ownership.")
            if section is not owner:
                family.append(section)
    if not family or component not in family:
        raise ValueError("The shared automatic draw family is incomplete.")
    indices, paths = set(), set()
    protected_resources = {_value(member, "ib", "").casefold() for member in family}
    patches = [(doc.path, auto_lines[0].no, auto_lines[0].no + 1, [])]
    owner_order = (int(_value(owner, "match_priority", "0")), owner.name.casefold())
    for member in family:
        first = _value(member, "match_first_index", "")
        if not first.isdecimal() or int(first) in indices:
            raise ValueError("Shared automatic component matching is ambiguous.")
        indices.add(int(first))
        member_order = (int(_value(member, "match_priority", "0")), member.name.casefold())
        # 3DMigoto orders by ascending priority then section name. Moving a draw
        # earlier is safe only across the shared skip, never across bindings.
        if member_order >= owner_order:
            raise ValueError("Shared automatic draw order cannot be preserved.")
        _validate_auto_commands(doc, member, protected_resources=protected_resources)
        member_path, _size = _auto_buffer(context, doc, member)
        path_key = _ib_path_key(member_path)
        if path_key in paths:
            raise ValueError("Shared automatic draws have incompatible buffer ownership.")
        paths.add(path_key)
        matches = [item for item, _group in authoritative.values()
                   if item.auto_draw and item.auto_draw["component"] == member.name
                   and item.sources == draw.sources]
        if len(matches) != 1 or matches[0].count is not None:
            raise ValueError("The shared automatic draw family is incomplete or conflicting.")
        for other, _group in authoritative.values():
            other_path = _resolve_ib_reference(context, other)
            if other is not matches[0] and other_path and _ib_path_key(other_path) == path_key:
                raise ValueError("Shared automatic draws have incompatible buffer ownership.")
        lines = ([f"drawindexed = {count}, {start}, {base}" for count, start, base in ranges]
                 if member is component else ["drawindexed = auto"])
        patches.append((doc.path, member.end, member.end, lines))
    return patches


def _payload_entry(request):
    if not isinstance(request, dict):
        raise ValueError("Mesh changes must be an object.")
    entry = request.get("mesh")
    if not isinstance(entry, dict):
        raise ValueError("Mesh changes must include one mesh.")
    return entry


def apply_component_mesh_changes(context, request):
    """Validate and stage one component's INI and index-buffer changes."""
    source = getattr(context, "source", None)
    if source is None or source.read_only:
        return {"error": "Mesh changes require a writable mod folder."}

    try:
        entry = _payload_entry(request)
        _parsed, authoritative = resolved_draws(context)
        component = request.get("component")
        key = entry.get("key")
        if not isinstance(key, str) or not key:
            raise ValueError("Mesh changes require a canonical mesh identity.")
        match = [(draw, group) for draw, group in authoritative.values()
                 if mesh_identity_for_draw(draw, group).key == key]
        if len(match) != 1:
            raise ValueError("The mesh identity is stale or synthetic.")
        draw, group = match[0]
        if component is not None and (
                group.get("display_name") or group.get("name")) != component:
            raise ValueError("The mesh does not belong to this component.")
        sources = entry.get("sources")
        if not isinstance(sources, list) or not sources:
            raise ValueError("Mesh changes require authored source provenance.")
        expected = {
            _authoritative_source_key(context.mod_dir, item, source)
            for item in draw.sources
        }
        submitted = {_source_key(item) for item in sources}
        if submitted != expected or len(submitted) != len(sources):
            raise ValueError("Mesh source provenance is stale.")
        parts = entry.get("parts")
        path = _resolve_ib_path(context, draw)
        data = edit_session.ib_overrides_for(context.mod_dir).get(path)
        if data is None:
            with open(path, "rb") as stream:
                data = stream.read()
        existing = edit_session.ib_edits_for(context.mod_dir).get(
            _ib_path_key(path))
        if existing is not None:
            original = existing["original_hash"]
        else:
            original = _sha256(data)
        count, start, base = draw.count, draw.start, draw.base
        if count is None:
            if not draw.auto_draw:
                raise ValueError("The draw has no editable automatic provenance.")
            if draw.index_size not in (2, 4) or len(data) % draw.index_size:
                raise ValueError("The automatic index buffer format or length is invalid.")
            count, start, base = len(data) // draw.index_size, 0, 0
        candidate, normalized = repack_index_bytes(
            data, start, count, draw.index_size, parts)
        byte_range = _draw_byte_range(draw, len(data))
        _validate_draw_overlap(
            context, authoritative, draw, path, byte_range, len(data))
        if existing is not None and any(
                byte_range[0] < end and start < byte_range[1]
                for start, end in existing["ranges"]):
            raise ValueError("Mesh edits overlap in the same index buffer.")

        resolved_lines = []
        ranges = drawindexed_ranges(start, base, normalized)
        if draw.count is None:
            resolved_lines = _auto_patches(context, draw, authoritative, ranges)
        else:
            for source_ref in entry["sources"]:
                _ini_key, doc = edit_session.document(
                    context.mod_dir, source_ref.get("ini"))
                target = dict(source_ref)
                target["drawindexed"] = [draw.count, draw.start, draw.base]
                resolved = resolve_draw_references(
                    doc, [target], target_ini=source_ref.get("ini"))
                line_no = resolved[int(source_ref["line"])]
                resolved_lines.append((doc.path, line_no, line_no + 1,
                                       _draw_lines(doc, line_no, ranges)))
        ini_paths = list(dict.fromkeys(item[0] for item in resolved_lines))
        ib_path = path
        with edit_session.transaction(context.mod_dir, ini_paths) as edit:
            patches = {}
            for ini_path, line_no, end, lines in resolved_lines:
                by_line = patches.setdefault(ini_path, {})
                if line_no in by_line:
                    raise ValueError("A source draw was submitted more than once.")
                by_line[line_no] = (end, lines)
            for ini_path, by_line in patches.items():
                doc = edit.document(ini_path)
                for line_no in sorted(by_line, reverse=True):
                    end, lines = by_line[line_no]
                    doc.replace_lines(line_no, end, lines)

            edit.stage_ib_edit(
                ib_path, candidate, original, [byte_range],
                dependent_inis={edit_session.document(
                    context.mod_dir, source_ref.get("ini"))[0]
                    for source_ref in entry.get("sources", [])})
        return {"ok": True, "component": component}
    except (KeyError, TypeError, ValueError, OSError, ModSourceError,
            MeshLayoutError) as error:
        return {"error": str(error)}

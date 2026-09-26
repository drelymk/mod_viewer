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


_DRAW_LINE = re.compile(
    r"^(?P<indent>\s*)(?P<lhs>drawindexed\s*=\s*)"
    r"(?P<count>\d+)\s*,\s*(?P<start>-?\d+)\s*,\s*(?P<base>-?\d+)"
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
        same_authored_range = (
            other_draw.start == edited_draw.start
            and other_draw.count == edited_draw.count
            and other_draw.index_size == edited_draw.index_size)
        if not same_authored_range:
            raise ValueError(
                "The edited index range overlaps another draw in the same "
                "index buffer.")


def _rewrite_draw_line(doc, line_no, ranges):
    match = _DRAW_LINE.match(doc.lines[line_no].raw)
    if match is None:
        raise ValueError("The authored draw line is no longer writable.")
    prefix = match.group("indent") + match.group("lhs")
    tail = match.group("tail")
    lines = []
    for index, (count, start, base) in enumerate(ranges):
        lines.append(f"{prefix}{count}, {start}, {base}"
                     + (tail if index == 0 else ""))
    doc.replace_lines(line_no, line_no + 1, lines)


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
        if draw.count is None:
            raise ValueError("Synthetic draws cannot receive mesh changes.")
        sources = entry.get("sources")
        if not isinstance(sources, list) or not sources:
            raise ValueError("Mesh changes require authored source provenance.")
        expected = {
            _authoritative_source_key(context.mod_dir, item, source)
            for item in draw.sources
        }
        submitted = {_source_key(item) for item in sources}
        if submitted != expected:
            raise ValueError("Mesh source provenance is stale.")
        parts = entry.get("parts")
        path = _resolve_ib_path(context, draw)
        data = edit_session.ib_overrides_for(context.mod_dir).get(path)
        if data is None:
            with open(path, "rb") as stream:
                data = stream.read()
        candidate, normalized = repack_index_bytes(
            data, draw.start, draw.count, draw.index_size, parts)
        byte_range = _draw_byte_range(draw, len(data))
        _validate_draw_overlap(
            context, authoritative, draw, path, byte_range, len(data))
        existing = edit_session.ib_edits_for(context.mod_dir).get(
            _ib_path_key(path))
        if existing is not None and any(
                byte_range[0] < end and start < byte_range[1]
                for start, end in existing["ranges"]):
            raise ValueError("Mesh edits overlap in the same index buffer.")

        ini_paths = []
        resolved_lines = []
        ranges = drawindexed_ranges(draw.start, draw.base, normalized)
        for source_ref in entry["sources"]:
            _ini_key, doc = edit_session.document(
                context.mod_dir, source_ref.get("ini"))
            if doc.path not in ini_paths:
                ini_paths.append(doc.path)
            target = dict(source_ref)
            target["drawindexed"] = [draw.count, draw.start, draw.base]
            resolved = resolve_draw_references(
                doc, [target], target_ini=source_ref.get("ini"))
            line_no = resolved[int(source_ref["line"])]
            resolved_lines.append((doc.path, line_no, ranges))
        ib_path = path

        with edit_session.transaction(context.mod_dir, ini_paths) as edit:
            patches = {}
            for path, line_no, ranges in resolved_lines:
                by_line = patches.setdefault(path, {})
                if line_no in by_line:
                    raise ValueError("A source draw was submitted more than once.")
                by_line[line_no] = ranges
            for path, by_line in patches.items():
                doc = edit.document(path)
                for line_no in sorted(by_line, reverse=True):
                    _rewrite_draw_line(doc, line_no, by_line[line_no])

            record = edit_session.ib_edits_for(context.mod_dir).get(
                os.path.normcase(os.path.abspath(ib_path)))
            if record is not None:
                original = record["original_hash"]
            else:
                with open(ib_path, "rb") as stream:
                    original = _sha256(stream.read())
            edit.stage_ib_edit(
                ib_path, candidate, original, [byte_range],
                dependent_inis={edit_session.document(
                    context.mod_dir, source_ref.get("ini"))[0]
                    for source_ref in entry.get("sources", [])})
        return {"ok": True, "component": component}
    except (KeyError, TypeError, ValueError, ModSourceError,
            MeshLayoutError) as error:
        return {"error": str(error)}

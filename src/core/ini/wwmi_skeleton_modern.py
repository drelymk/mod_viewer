"""Adapters for stock merge-status and paired blend/skeleton proxy programs."""

import re
from dataclasses import dataclass

from .wwmi_skeleton import (
    _API, _assignment, _control, _draw_body, _draw_lists, _local_flag, _match, _neutral_lists, _pixel_line, _plain,
    _MERGER, _OFFSET, _COUNT, _PIXEL_HELPERS, WwmiSkeletonProof,
)


_BLEND_REMAP = r"customshader\wwmiv1\blendremapper"
_SKELETON_REMAP = r"customshader\wwmiv1\skeletonremapper"
# A literal flag set, including namespaced variables but never the WWMI API.
_FLAG = r"\$(?!\\wwmiv1\\)[\w\\]+ = -?\d+(?:\.\d+)?"
_CHECK = r"checktextureoverride = (?:ps-t\d+|vs-cb[34])"


@dataclass(frozen=True, slots=True)
class WwmiRemapProof:
    vertex_resource: str
    reverse_resource: str
    forward_resource: str
    no_stride_resource: str
    remap_id: int
    bone_count: int
    compact_count: int
    vertex_count: int
    influence_count: int


def _merge_steps(lines, source=None, done=0):
    """Return the RW targets of the merger runs in a straight-line step list.

    Mesh scale and the CB a merge reads affect pose data only, never palette
    IDs. Older exporters omit the vs-cb3 source, so the second merge rereads
    vs-cb4; that is accepted, while any other command rejects the program.
    """
    target, runs = None, []
    for line in lines:
        cb = _match(r"cs-cb8 = ref (vs-cb[34])", line)
        uav = _match(r"cs-u6 = (resource\w+)", line)
        if cb:
            source = cb[1]
        elif uav:
            target = uav[1]
        elif line == f"run = {_MERGER}":
            if not target or (not runs and not done and source != "vs-cb4") or source is None:
                return None
            runs.append(target)
            target = None
        elif not _match(re.escape(_API) + r"custom_mesh_scale = (?:\d+(?:\.\d+)?|\$\w+)", line):
            return None
    return runs, source


def _merger_program(lines):
    """Return (merge state or None, CB marker or None, RW targets)."""
    if not lines:
        return None
    if not lines[0].startswith("if "):
        steps = _merge_steps(lines)
        return (None, None, tuple(steps[0])) if steps and len(steps[0]) == 2 else None
    state = _match(r"if (\$\w+) == 0", lines[0])
    marker = _match(r"if vs-cb4 == (\d+(?:\.\d+)?)", lines[1]) if len(lines) > 1 else None
    if not state or not marker:
        return None
    s, m = state[1], marker[1]
    try:
        second = lines.index(f"if {s} == 1")
    except ValueError:
        return None
    first_block, second_block = lines[:second], lines[second:]
    if first_block[-3:] != [f"{s} = 1", "endif", "endif"]:
        return None
    if (len(second_block) < 5 or second_block[1] != f"if vs-cb4 == {m} && vs-cb3 == {m}"
            or second_block[-3:] != [f"{s} = 2", "endif", "endif"]):
        return None
    first = _merge_steps(first_block[2:-3])
    if not first or len(first[0]) != 1:
        return None
    rest = _merge_steps(second_block[2:-3], first[1], 1)
    if not rest or len(rest[0]) != 1:
        return None
    return s, m, (first[0][0], rest[0][0])


def _cb_bindings(marker, main, extra):
    return [f"if vs-cb4 == {marker}", f"vs-cb4 = ref {main}", f"if vs-cb3 == {marker}",
            f"vs-cb3 = ref {extra}", "endif", f"elif vs-cb3 == {marker}",
            f"vs-cb3 = ref {main}", "endif"]


def _parse_cb(lines, marker):
    # A custom resource assigned to a pipeline slot is bound with or without "ref".
    lines = [re.sub(r"^(vs-cb[34]) = (resource\w+)$", r"\1 = ref \2", line) for line in lines]
    if len(lines) == 8:
        main = _match(r"vs-cb4 = ref (resource\w+)", lines[1])
        extra = _match(r"vs-cb3 = ref (resource\w+)", lines[3])
        if main and extra and lines == _cb_bindings(marker, main[1], extra[1]):
            return main[1], extra[1]
    if len(lines) == 6:
        extra = _match(r"vs-cb3 = ref (resource\w+)", lines[1])
        main = _match(r"vs-cb4 = ref (resource\w+)", lines[4])
        if main and extra and lines == [f"if vs-cb3 == {marker}", f"vs-cb3 = ref {extra[1]}", "endif",
                                        f"if vs-cb4 == {marker}", f"vs-cb4 = ref {main[1]}", "endif"]:
            return main[1], extra[1]
    return None


def _status_binder(lines, marker, outputs):
    if not lines:
        return None
    lines = [line for line in lines if line != "vb6 = null"]
    bypass = _match(r"(resource\w+) = ref vb0", lines[0])
    if not bypass or len(lines) < 11 or not _match(r"ib = resource\w+", lines[1]):
        return None
    cursor = 2
    seen = set()
    while cursor < len(lines) and (item := _match(r"vb([0-3]) = resource\w+", lines[cursor])):
        if item[1] in seen:
            return None
        seen.add(item[1])
        cursor += 1
    if "0" not in seen:
        return None
    tail = lines[cursor:]
    if not tail:
        return None
    blend = _match(r"vb4 = (resource\w+)", tail[0])
    if blend and _parse_cb(tail[1:], marker) == outputs:
        return bypass[1], blend[1], None
    proxy = _match(r"if (resource\w+) === null", tail[0])
    if not proxy or "else" not in tail or tail[-1] != "endif":
        return None
    boundary = tail.index("else")
    blend = _match(r"vb4 = (resource\w+)", tail[1])
    remapped = _parse_cb(tail[boundary + 2:-1], marker)
    if (not blend or _parse_cb(tail[2:boundary], marker) != outputs or not remapped
            or tail[boundary + 1] != f"vb4 = ref {proxy[1]}"):
        return None
    return bypass[1], blend[1], (proxy[1], *remapped)


def _block_end(lines, start, allowed=None):
    """Index of the endif closing the block opened at ``start``; with
    ``allowed``, every non-control line inside must satisfy it."""
    depth = 0
    for index in range(start, len(lines)):
        line = lines[index]
        if line.startswith("if "):
            depth += 1
        elif line == "endif":
            depth -= 1
            if not depth:
                return index
        elif allowed and not (_control(line) or allowed(line)):
            return None
    return None


def _pixel_state(line):
    return _pixel_line(line) or bool(_match(_FLAG, line))


def _pixel_run(lines, index):
    """First index at or after ``index`` that is not pixel or flag state."""
    while index < len(lines):
        if lines[index].startswith("if "):
            end = _block_end(lines, index, _pixel_state)
            if end is None:
                return index
            index = end
        elif not _pixel_state(lines[index]):
            return index
        index += 1
    return index


def _status_component(lines, merger, state, output, frame=None):
    # 3DMigoto ignores unrecognised bare entries such as a stray "CP3".
    lines = [line for line in lines or () if "=" in line
             or line.split(" ", 1)[0] in {"if", "elif", "else", "endif", "local"}]
    if len(lines) < 12:
        return None
    # Header: the match keys in any order, interleaved with literal flag sets
    # and pixel-only state, which may sit inside toggle conditionals.
    keys, cursor = {}, 0
    while cursor < len(lines):
        line = lines[cursor]
        item = _match(r"(hash) = ([0-9a-f]{8})|(match_first_index|match_index_count) = (\d+)", line)
        if item:
            key = item[1] or item[3]
            if key in keys:
                return None
            keys[key] = item[2] or item[4]
        elif line.startswith("if "):
            end = _block_end(lines, cursor, _pixel_state)
            if end is None:
                break
            cursor = end
        elif not (_match(_FLAG + r"|match_priority = -?\d+", line) or _pixel_line(line)):
            break
        cursor += 1
    if set(keys) != {"hash", "match_first_index", "match_index_count"}:
        return None
    fields = [(None, keys[name]) for name in ("hash", "match_first_index", "match_index_count")]
    # The outer gate is any condition; pixel state may precede the merge guard.
    if cursor >= len(lines) or not lines[cursor].startswith("if "):
        return None
    lines = lines[:cursor + 1] + lines[_pixel_run(lines, cursor + 1):]
    if len(lines) < cursor + 9:
        return None
    offset = count = None
    if state is None:
        # Frame-state form: a per-component local mirrors the toggled frame id.
        local = _match(r"local (\$\w+)", lines[cursor + 1])
        if not local:
            return None
        cursor += 1
        offset = _match(re.escape(_OFFSET) + r" = (\d+)", lines[cursor + 3])
        count = _match(re.escape(_COUNT) + r" = (\d+)", lines[cursor + 4])
        guard = [f"if {local[1]} != {frame}", f"{local[1]} = {frame}"]
        if lines[cursor + 1:cursor + 3] != guard or lines[cursor + 5:cursor + 7] != [f"run = {merger}", "endif"]:
            return None
        merged = cursor + 7
    else:
        local = _match(r"if (\$\w+) != 2", lines[cursor + 1])
        offset = _match(re.escape(_OFFSET) + r" = (\d+)", lines[cursor + 2])
        count = _match(re.escape(_COUNT) + r" = (\d+)", lines[cursor + 3])
        if not local or lines[cursor + 4:cursor + 8] != [f"{state} = {local[1]}", f"run = {merger}",
                                                         f"{local[1]} = {state}", "endif"]:
            return None
        merged = cursor + 8
    if not offset or not count:
        return None
    row = fields[0][1], int(fields[1][1]), int(fields[2][1]), int(offset[1]), int(count[1])
    if lines[merged:merged + 1] == ["endif"]:
        # Merge-only component: it contributes its bones to the shared skeleton
        # but issues no draw; only pixel state may follow.
        return (row, local[1], None) if _pixel_run(lines, merged + 1) == len(lines) else None
    if lines[merged:merged + 2] != [f"if {output} !== null", "handling = skip"]:
        return None
    end = _block_end(lines, merged)
    if end is None:
        return None
    # Pixel or flag state may follow the draw block, inside and after the gate.
    close = _pixel_run(lines, end + 1)
    if lines[close:close + 1] != ["endif"] or _pixel_run(lines, close + 1) != len(lines):
        return None
    return row, local[1], lines[merged + 2:end]


def _proxy_start(body, proxies, binder):
    # Exporters may emit pixel-texture setup before the proxy triple; it must
    # still be unconditional and precede the shared binder.
    depth = 0
    for index, line in enumerate(body):
        if line == f"run = {binder}":
            return None
        if line.startswith("if "):
            depth += 1
        elif line == "endif":
            depth -= 1
        elif line.startswith(f"{proxies[0]} = "):
            return index if depth == 0 else None
    return None


def _resource_width(lines):
    if not lines or len(lines) != 3 or lines[0] != "type = buffer":
        return None
    fmt = _match(r"format = (?:dxgi_format_)?r(16|32)_uint", lines[1])
    return int(fmt[1]) // 8 if fmt and lines[2].startswith("filename = ") else None


def _default_remap_counts(lines, *, pose=False):
    result, reads = [], 0
    for index, line in enumerate(lines):
        if line.startswith(f"{_API}blend_remap_id = "):
            reads = 0
            result.append(line)
            if index + 1 < len(lines) and not lines[index + 1].startswith(f"{_API}blend_remap_bone_count = "):
                result.append(f"{_API}blend_remap_bone_count = 512")
            continue
        if pose and line.startswith("cs-t38 = "):
            if reads == 1 and not result[-1].startswith(f"{_API}blend_remap_bone_count = "):
                result.append(f"{_API}blend_remap_bone_count = 512")
            reads += 1
        result.append(line)
    return result


def _carry_pose_source(lines):
    # Some exporters omit the extra skeleton's cs-t38 rebind, so the remapper
    # rereads the bound copy. That changes pose data only, never palette IDs.
    result, source = [], None
    for line in lines:
        if line.startswith("cs-t38 = "):
            source = line
        elif (line.startswith("cs-u5 = ") and source and result
              and not result[-1].startswith("cs-t38 = ")):
            result.append(source)
        result.append(line)
    return result


def _remap_program(plain, initializer, remapper, targets, blend, component_proxies):
    init = plain.get(initializer)
    remap = plain.get(remapper)
    init = _default_remap_counts(init) if init else init
    remap = _default_remap_counts(_carry_pose_source(remap), pose=True) if remap else remap
    if not init or not remap or len(init) < 17 or len(remap) < 15:
        return None
    once = _match(r"local (\$\w+)", init[0])
    temp_main = _match(r"(resource\w+) = copy " + re.escape(targets[0]), init[2])
    temp_extra = _match(r"(resource\w+) = copy " + re.escape(targets[1]), init[3])
    vertex_var = _match(re.escape(_API) + r"custom_vertex_count = (\$\w+)", init[4])
    influence = _match(re.escape(_API) + r"weights_per_vertex_count = (4|8)", init[5])
    reverse = _match(r"cs-t34 = ref (resource\w+)", init[6])
    vertex = _match(r"cs-t35 = ref (resource\w+)", init[7])
    if not all((once, temp_main, temp_extra, vertex_var, influence, reverse, vertex)):
        return None
    if init[1] != f"if !{once[1]}" or init[-2:] != [f"{once[1]} = 1", "endif"]:
        return None
    vertices = [_match(r"global " + re.escape(vertex_var[1]) + r" = (\d+)", line)
                for line in plain.get("constants", [])]
    vertices = [int(item[1]) for item in vertices if item]
    if len(vertices) != 1 or not 1 <= vertices[0] <= 10_000_000:
        return None
    if any(_assignment(line)[0] == vertex_var[1] for name, lines in plain.items()
           if name != "constants" and lines for line in lines):
        return None
    groups = init[8:-2]
    if len(groups) % 7:
        return None
    prepared = {}
    for start in range(0, len(groups), 7):
        group = groups[start:start + 7]
        remap_id = _match(re.escape(_API) + r"blend_remap_id = (\d+)", group[0])
        count = _match(re.escape(_API) + r"blend_remap_bone_count = (512|1024)", group[1])
        temp = _match(r"(resource\w+) = copy (resource\w+)", group[2])
        output = _match(r"(resource\w+) = copy (resource\w+)", group[5])
        if not all((remap_id, count, temp, output)):
            return None
        if int(remap_id[1]) >= 1024:
            return None
        if (group[3:5] != [f"cs-u4 = ref {temp[1]}", f"run = {_BLEND_REMAP}"]
                or output[2] != temp[1] or group[6] != f"{output[1]} = copy_desc {blend}"
                or output[1] in prepared or plain.get(temp[1]) != [] or plain.get(output[1]) != []):
            return None
        prepared[output[1]] = int(remap_id[1]), int(count[1]), temp[2]
    copies = [_match(r"(resource\w+) = copy " + re.escape(targets[i]), remap[i]) for i in range(2)]
    forward = _match(r"cs-t37 = (resource\w+)", remap[2])
    if not all(copies) or not forward or len(remap[3:]) % 12:
        return None
    if not _resource_width(plain.get(reverse[1])) or not _resource_width(plain.get(forward[1])):
        return None
    results = {}
    for start in range(3, len(remap), 12):
        group = remap[start:start + 12]
        remap_id = _match(re.escape(_API) + r"blend_remap_id = (\d+)", group[0])
        count = _match(re.escape(_API) + r"blend_remap_bone_count = (512|1024)", group[1])
        size = _match(re.escape(_API) + r"vg_count = (\d+)", group[2])
        out_main = _match(r"(resource\w+) = copy " + re.escape(temp_main[1]), group[6])
        out_extra = _match(r"(resource\w+) = copy " + re.escape(temp_extra[1]), group[11])
        if not all((remap_id, count, size, out_main, out_extra)) or not 1 <= int(size[1]) <= 256:
            return None
        sources = (f"cs-t38 = {copies[0][1]}", f"cs-t38 = {copies[1][1]}")
        main_source = group[3] if group[3] in sources else sources[0]
        extra_source = group[8] if group[8] in sources else sources[1]
        expected = [group[0], group[1], group[2], main_source, f"cs-u5 = {temp_main[1]}",
                    f"run = {_SKELETON_REMAP}", group[6], group[1], extra_source,
                    f"cs-u5 = {temp_extra[1]}", f"run = {_SKELETON_REMAP}", group[11]]
        if group != expected or any(plain.get(name) != [] for name in (temp_main[1], temp_extra[1], out_main[1], out_extra[1])):
            return None
        pairs = [values for values in component_proxies.values()
                 if values and values[1:] == (out_main[1], out_extra[1])]
        if not pairs and not any(out_main[1] in line or out_extra[1] in line
                                 for name, lines in plain.items() if lines and name not in (initializer, remapper)
                                 for line in lines):
            # Prepared for a component that no longer exists; nothing consumes it.
            continue
        if len(pairs) != 1 or pairs[0][0] not in prepared:
            return None
        key = pairs[0][0]
        identity, bone_count, no_stride = prepared[key]
        if identity != int(remap_id[1]) or bone_count != int(count[1]) or key in results:
            return None
        no_stride_fields = dict(_assignment(line) for line in plain.get(no_stride, []))
        blend_fields = dict(_assignment(line) for line in plain.get(blend, []))
        if (no_stride_fields.get("filename") != blend_fields.get("filename")
                or no_stride_fields.get("format") != blend_fields.get("format")
                or "stride" in no_stride_fields):
            return None
        results[key] = WwmiRemapProof(vertex[1], reverse[1], forward[1], no_stride, identity,
                                     bone_count, int(size[1]), vertices[0], int(influence[1]))
    unused = set(prepared) - set(results)
    if (any(name in line for name in unused for section, lines in plain.items()
            if lines and section not in (initializer, remapper) for line in lines)
            or set(results) != {values[0] for values in component_proxies.values() if values}):
        return None
    return results


def modern_wwmi_merged_layouts(commands):
    """Recognize complete current stock programs; leave custom programs unproven."""
    if any(name in commands for name in (_MERGER, _BLEND_REMAP, _SKELETON_REMAP, *_PIXEL_HELPERS)):
        return {}
    plain = {name: _plain(lines) for name, lines in commands.items()}
    mergers = [(name, value) for name, lines in plain.items()
               if name.startswith("commandlist") and (value := _merger_program(lines))]
    if len(mergers) != 1:
        return {}
    merger, (state, marker, targets) = mergers[0]
    if len(set(targets)) != 2:
        return {}
    publications = []
    for name, lines in plain.items():
        if not name.startswith("commandlist") or not lines:
            continue
        tail = lines[-3:] if lines[-1].startswith("run = ") else lines[-2:]
        copies = [_match(r"(resource\w+) = copy " + re.escape(targets[i]), tail[i]) for i in range(min(2, len(tail)))]
        if len(copies) == 2 and all(copies):
            publications.append((name, lines[:-len(tail)], tuple(item[1] for item in copies), tail[2:]))
    if len(publications) != 1:
        return {}
    publication, resets, outputs, remap_call = publications[0]
    if len(set(outputs)) != 2 or set(outputs).intersection(targets):
        return {}
    frame = None
    if len(resets) >= 5:
        toggle = _match(r"if (\$\w+)", resets[-5])
        if toggle and resets[-4:] == [f"{toggle[1]} = 0", "else", f"{toggle[1]} = 1", "endif"]:
            resets, frame = resets[:-5], toggle[1]
    if state is None and (frame is None or resets):
        return {}
    if (None, f"run = {publication}") not in commands.get("present", ()) or any(plain.get(name) != [] for name in outputs):
        return {}
    markers = [marker] if marker else sorted({item[1] for lines in plain.values() if lines for line in lines
                                              if (item := _match(r"if vs-cb[34] == (\d+(?:\.\d+)?)", line))})
    binders = [(name, value, candidate) for candidate in markers for name, lines in plain.items()
               if name.startswith("commandlist") and (value := _status_binder(lines, candidate, outputs))]
    if len(binders) != 1:
        return {}
    binder, (bypass, blend, proxies), marker = binders[0]
    blend_fields = dict(_assignment(line) for line in plain.get(blend) or ())
    if blend_fields.get("type") != "buffer" or not blend_fields.get("filename"):
        return {}
    if proxies and (len(set(proxies)) != 3 or set(proxies).intersection((*outputs, *targets, blend))):
        return {}
    cleanup_body = [f"vb0 = ref {bypass}"]
    if proxies:
        cleanup_body += [f"if {proxies[0]} !== null", *(f"{name} = null" for name in proxies), "endif"]
        if any(plain.get(name) != [] for name in proxies):
            return {}
    cleanups = [name for name, lines in plain.items() if name.startswith("commandlist") and lines
                and [line for line in lines if not _match(r"\$\w+ = -?\d+(?:\.\d+)?", line)] == cleanup_body]
    if len(cleanups) != 1:
        return {}
    checks = {name for name, lines in plain.items() if name.startswith("commandlist") and lines
              and any(_match(_CHECK, line) for line in lines)
              and all(_match(_CHECK, line) or _control(line) or _local_flag(line) for line in lines)}
    neutral = _neutral_lists(plain)
    checks |= neutral
    marked = [lines for name, lines in plain.items() if name.startswith("textureoverride") and lines
              and f"filter_index = {marker}" in lines]
    if not marked or any(not any(_match(r"hash = [0-9a-f]{8}", line) for line in lines) for lines in marked):
        return {}
    shaders = {name for name in commands if name.startswith("customshader")} | _draw_lists(plain, neutral)
    components, component_proxies, statuses = {}, {}, []
    for name, lines in plain.items():
        if not name.startswith("textureoverride") or not lines or not any(_OFFSET in line or _COUNT in line for line in lines):
            continue
        parsed = _status_component(lines, merger, state, outputs[0], frame)
        if not parsed:
            return {}
        row, status, body = parsed
        values = None
        if body is None:
            components[name], component_proxies[name] = row, None
            statuses.append(status)
            continue
        start = _proxy_start(body, proxies, binder) if proxies else None
        if start is not None:
            assignments = [_match(re.escape(proxy) + r" = ref (resource\w+)", line)
                           for proxy, line in zip(proxies, body[start:start + 3])]
            if len(assignments) != 3 or not all(assignments):
                return {}
            values = tuple(item[1] for item in assignments)
            body = body[:start] + body[start + 3:]
        if not _draw_body(body, binder, checks, cleanups[0], shaders):
            return {}
        components[name], component_proxies[name] = row, values
        statuses.append(status)
    expected_resets = [f"{status} = 0" for status in statuses] if state else []
    if not components or len(statuses) != len(set(statuses)) or resets != expected_resets:
        return {}
    if len({row[0] for row in components.values()}) != 1:
        return {}
    layout = tuple(sorted(components.values(), key=lambda row: row[3]))
    cursor = 0
    for _hash, _first, count, offset, size in layout:
        if count < 3 or count % 3 or offset != cursor or not 1 <= size <= 256:
            return {}
        cursor += size
    if cursor > 1024 or len({row[1:3] for row in layout}) != len(layout):
        return {}
    # The merger drops writes past the RW buffer, and native u8 Blend indices can
    # only reach the first 256 merged bones. The buffer must cover every bone a
    # palette ID can address, not the whole layout.
    addressable = min(cursor, 1024 if proxies else 256)
    for target in targets:
        lines = plain.get(target)
        array = _match(r"array = (768|1536|3072)", lines[2]) if lines and len(lines) == 3 else None
        if (not array or lines[:2] != ["type = rwbuffer", "format = r32g32b32a32_float"]
                or int(array[1]) < addressable * 3):
            return {}
    if proxies:
        assigned = {_assignment(line)[0] for lines in plain.values() if lines for line in lines}
        for name, values in component_proxies.items():
            # A ref to undeclared or never-filled resources leaves the proxy
            # null, so that component keeps the native Blend and merged skeleton.
            if values and all(plain.get(value) in (None, []) and value not in assigned for value in values):
                component_proxies[name] = None
    remaps, allowed = {}, {merger, publication, binder, cleanups[0], *components}
    if proxies:
        if len(remap_call) != 1:
            return {}
        remapper = _assignment(remap_call[0])[1]
        initializers = [name for name, lines in plain.items() if name.startswith("commandlist") and lines
                        and f"run = {_BLEND_REMAP}" in lines]
        if len(initializers) != 1 or (None, f"run = {initializers[0]}") not in commands.get("present", ()):
            return {}
        remaps = _remap_program(plain, initializers[0], remapper, targets, blend, component_proxies)
        if remaps is None:
            return {}
        allowed.update((initializers[0], remapper))
    elif remap_call or any(component_proxies.values()):
        return {}
    protected = set(targets) | set(outputs) | {blend} | set(proxies or ())
    if proxies:
        protected.update(resource for name in (initializers[0], remapper)
                         for line in plain[name] for resource in re.findall(r"resource\w+", line))
    for name, lines in commands.items():
        if name in allowed or name.startswith("resource"):
            continue
        for _phase, line in lines:
            if line.startswith(("if ", "elif ", "else if ")):
                continue
            key, value = _assignment(line)
            if (key in {_OFFSET, _COUNT, "vs-cb3", "vs-cb4", "vb4"}
                    or protected.intersection(re.findall(r"resource[\w.\-\\]+", line))
                    or (key == "run" and value in {merger, binder, _MERGER, _BLEND_REMAP, _SKELETON_REMAP})):
                return {}
    helpers = frozenset(shaders)
    return {name: WwmiSkeletonProof(blend, layout, remaps.get(values[0]) if values else None, helpers)
            for name, values in component_proxies.items()}

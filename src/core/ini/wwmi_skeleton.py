"""Recognize the stock WWMI R8 merged-skeleton template within one INI.

This is a bounded template adapter, not shader execution or numeric palette
guessing. Only component draws with unchanged shared bindings qualify, either
direct or inside verified local draw helpers.
"""

import re
from typing import NamedTuple

from .command_phases import parse_command_line
from .sections import line_source


_MERGER = r"customshader\wwmiv1\skeletonmerger"
_PIXEL_HELPERS = {r"commandlist\rabbitfx\run", r"commandlist\rabbitfx\settextures"}
_PIXEL_RUNS = {f"run = {name}" for name in _PIXEL_HELPERS}
_API = "$\\wwmiv1\\"
_OFFSET = _API + "vg_offset"
_COUNT = _API + "vg_count"


class WwmiSkeletonProof(NamedTuple):
    blend_resource: str
    layout: tuple
    remap: object = None
    # Lowercase local sections whose draws run inside the proven bound state.
    helpers: frozenset = frozenset()


def _match(pattern, text):
    return re.fullmatch(pattern, text)


def _assignment(line):
    parts = line.split("=", 1)
    return tuple(part.strip() for part in parts) if len(parts) == 2 else (None, None)


def _commands(sections):
    result, origins = {}, set()
    for name, lines in sections.items():
        key = name.casefold()
        if key in result:
            return {}
        commands = []
        for raw in lines:
            source = line_source(raw)
            if source:
                origins.add(source["ini_path"])
            command = parse_command_line(raw)
            if command.text:
                text = re.sub(r"\s+", " ", command.text).casefold()
                if not text.startswith(("if ", "elif ", "else if ")):
                    # 3DMigoto trims both sides of an assignment.
                    text = re.sub(r"^([^=!<>]*?) ?= ?(?!=)", r"\1 = ", text, count=1)
                commands.append((command.phase, text))
        result[key] = commands
    # Sibling INIs never establish a shared palette contract.
    return result if len(origins) <= 1 else {}


def _plain(commands):
    if any(phase is not None for phase, _line in commands):
        return None
    return [line for _phase, line in commands]


def _merger(lines):
    if lines is None or len(lines) != 7:
        return None
    if (not _match(r"\$\\wwmiv1\\custom_mesh_scale = 1(?:\.0+)?", lines[0])
            or lines[1] != "cs-cb8 = ref vs-cb4"
            or lines[3] != f"run = {_MERGER}"
            or lines[4] != "cs-cb8 = ref vs-cb3"
            or lines[6] != f"run = {_MERGER}"):
        return None
    targets = [_match(r"cs-u6 = (resource[\w]+)", lines[i]) for i in (2, 5)]
    return tuple(item[1] for item in targets) if all(targets) else None


def _copies(lines, targets):
    if lines is None or len(lines) != 7:
        return None
    state = _match(r"if (\$\w+)", lines[0])
    if (not state or lines[1:5] != [f"{state[1]} = 0", "else", f"{state[1]} = 1", "endif"]):
        return None
    outputs = [_match(r"(resource\w+) = copy " + re.escape(targets[i]), lines[5 + i]) for i in range(2)]
    if not all(outputs) or outputs[0][1] == outputs[1][1]:
        return None
    return state[1], tuple(item[1] for item in outputs)


def _binder(lines, outputs):
    if lines is None or len(lines) < 10:
        return None
    bypass = _match(r"(resource\w+) = ref vb0", lines[0])
    if not bypass or not _match(r"ib = resource\w+", lines[1]):
        return None
    bindings = [_match(r"vb(\d+) = (resource\w+|null)", line) for line in lines[2:-6]]
    if (not all(bindings) or len([item for item in bindings if item[1] == "0"]) != 1
            or len([item for item in bindings if item[1] == "4"]) != 1):
        return None
    blend = next(item[2] for item in bindings if item[1] == "4")
    if blend == "null":
        return None
    marker = _match(r"if vs-cb3 == (\d+(?:\.\d+)?)", lines[-6])
    if (not marker or lines[-5:] != [f"vs-cb3 = {outputs[1]}", "endif",
            f"if vs-cb4 == {marker[1]}", f"vs-cb4 = {outputs[0]}", "endif"]):
        return None
    return bypass[1], blend, marker[1]


def _component(lines, merger, binder, checks, cleanup, state, output):
    if lines is None or len(lines) < 15:
        return None
    fields = [_match(pattern, lines[i]) for i, pattern in enumerate((
        r"hash = ([0-9a-f]{8})", r"match_first_index = (\d+)", r"match_index_count = (\d+)",
    ))]
    if not all(fields):
        return None
    cursor = 3
    if _match(r"\$\w+ = 1", lines[cursor]):
        cursor += 1
    enabled = _match(r"if \$\w+", lines[cursor])
    local = _match(r"local (\$\w+)", lines[cursor + 1])
    if not enabled or not local:
        return None
    prefix = lines[cursor + 2:cursor + 10]
    offset = _match(re.escape(_OFFSET) + r" = (\d+)", prefix[2])
    count = _match(re.escape(_COUNT) + r" = (\d+)", prefix[3])
    if (not offset or not count or prefix[:2] != [f"if {local[1]} != {state}", f"{local[1]} = {state}"]
            or prefix[4:] != [f"run = {merger}", "endif", f"if {output} !== null", "handling = skip"]):
        return None
    body = lines[cursor + 10:]
    if body[-2:] != ["endif", "endif"]:
        return None
    body = body[:-2]
    if not _draw_body(body, binder, checks, cleanup):
        return None
    return fields[0][1], int(fields[1][1]), int(fields[2][1]), int(offset[1]), int(count[1])


def _pixel_line(line):
    """Pixel-shader state that cannot affect geometry or skinning bindings."""
    resource = r"(?:ref )?(?:resource[\w.\-\\]+|null)"
    return bool(_match(r"ps-t\d+ = " + resource, line)
                or _match(r"resource\\rabbitfx\\(?:diffuse|lightmap|normalmap|materialmap|glowmap|fxmap|cutoutmap|specialmap) = " + resource, line)
                or line in _PIXEL_RUNS)


def _control(line):
    return line.startswith(("if ", "elif ", "else if ")) or line in {"else", "endif"}


def _local_flag(line):
    """A mod-local variable write (however malformed); never the WWMI API."""
    return line.startswith("$") and "=" in line and not line.startswith(_API)


def _local_lists(plain, allowed, known=frozenset()):
    """Local command lists whose lines are control flow, ``allowed`` or calls to
    ``known`` or already accepted lists, solved to a fixed point."""
    candidates = {name for name, lines in plain.items() if name.startswith("commandlist") and lines is not None}
    result, changed = set(), True
    while changed:
        changed = False
        for name in sorted(candidates - result - known):
            if all(_control(line) or allowed(line)
                   or (line.startswith("run = ") and line[6:] in known | result) for line in plain[name]):
                result.add(name)
                changed = True
    return result


def _neutral_lists(plain):
    """Local command lists that only set variables or pixel state."""
    return _local_lists(plain, lambda line: _pixel_line(line) or _local_flag(line))


def _draw_lists(plain, neutral):
    """Local command lists that draw, optionally with pixel/variable state."""
    return _local_lists(plain, lambda line: _pixel_line(line) or _local_flag(line)
                        or bool(_match(r"drawindexed = .+", line)), neutral)


def _draw_body(body, binder, checks, cleanup, shaders=()):
    # Pixel state can precede binding or be toggle-dependent. The shared binder
    # and cleanup must remain unconditional, with every draw between them.
    # Local custom shaders draw with the bound state; callers verify that no
    # unlisted section rebinds Blend or skeleton resources.
    stack, bound, cleaned = [], False, False
    for line in body:
        if line.startswith("if "):
            stack.append(False)
        elif line.startswith(("elif ", "else if ")):
            if not stack or stack[-1]:
                return None
        elif line == "else":
            if not stack or stack[-1]:
                return None
            stack[-1] = True
        elif line == "endif":
            if not stack:
                return None
            stack.pop()
        elif line == f"run = {binder}":
            if stack or bound or cleaned:
                return False
            bound = True
        elif line == f"run = {cleanup}":
            if stack or not bound:
                return False
            bound, cleaned = False, True
        elif _match(r"drawindexed = .+", line) or (line.startswith("run = ") and line[6:] in shaders):
            if not bound:
                return False
        elif (line.startswith("run = ") and line[6:] in checks) or _pixel_line(line) or _local_flag(line):
            continue
        else:
            return None
    if stack or bound:
        return None
    return True


def stock_wwmi_merged_layouts(sections):
    """Return root-section -> (Blend resource, full component layout) proofs."""
    commands = _commands(sections)
    if not commands or _MERGER in commands or any(name in commands for name in _PIXEL_HELPERS):
        return {}
    from .wwmi_skeleton_modern import modern_wwmi_merged_layouts

    return _legacy_layouts(commands) or modern_wwmi_merged_layouts(commands)


def _legacy_layouts(commands):
    plain = {name: _plain(lines) for name, lines in commands.items()}
    mergers = [(name, value) for name, lines in plain.items()
               if name.startswith("commandlist") and (value := _merger(lines))]
    if len(mergers) != 1:
        return {}
    merger, targets = mergers[0]
    if len(set(targets)) != 2:
        return {}
    for target in targets:
        lines = plain.get(target)
        if (lines is None or len(lines) != 3 or lines[:2] != ["type = rwbuffer", "format = r32g32b32a32_float"]
                or not _match(r"array = (?:768|[1-9]\d{3,4})", lines[2])):
            return {}
    copies = [(name, value) for name, lines in plain.items()
              if name.startswith("commandlist") and (value := _copies(lines, targets))]
    if len(copies) != 1:
        return {}
    copy, (state, outputs) = copies[0]
    if any(plain.get(output) != [] for output in outputs):
        return {}
    # Present must actually publish the merged data. Its phase-prefixed state
    # reset is permitted; the copy call itself must execute in pre phase.
    if (None, f"run = {copy}") not in commands.get("present", ()):
        return {}
    binders = [(name, value) for name, lines in plain.items()
               if name.startswith("commandlist") and (value := _binder(lines, outputs))]
    if len(binders) != 1:
        return {}
    binder, (bypass, blend, marker) = binders[0]
    if blend not in plain:
        return {}
    checks = {name for name, lines in plain.items() if name.startswith("commandlist") and lines
              and all(_match(r"checktextureoverride = (?:ps-t\d+|vs-cb[34])", line) for line in lines)}
    cleanups = [name for name, lines in plain.items() if name.startswith("commandlist")
                and lines == [f"vb0 = ref {bypass}"]]
    if len(cleanups) != 1:
        return {}
    marked = [lines for name, lines in plain.items() if name.startswith("textureoverride") and lines
              and f"filter_index = {marker}" in lines]
    if not marked or any(not any(_match(r"hash = [0-9a-f]{8}", line) for line in lines) for lines in marked):
        return {}
    components = {}
    for name, lines in plain.items():
        if name.startswith("textureoverride") and lines and any(_OFFSET in line or _COUNT in line for line in lines):
            row = _component(lines, merger, binder, checks, cleanups[0], state, outputs[0])
            if row is None:
                return {}
            components[name] = row
    if not components:
        return {}
    # One pair of RW outputs cannot prove independent overlapping palettes for
    # multiple geometry objects in the same INI.
    if len({row[0] for row in components.values()}) != 1:
        return {}
    # No other local command may overwrite the merged buffers, palette inputs,
    # native skeleton CBs or Blend binding behind the template's back.
    protected = set(targets) | set(outputs) | {blend}
    for name, lines in commands.items():
        if name in {merger, copy, binder, *components} or name.startswith("resource"):
            continue
        for _phase, line in lines:
            if line.startswith(("if ", "elif ", "else if ")):
                continue
            key, value = _assignment(line)
            if (key in {_OFFSET, _COUNT, "vs-cb3", "vs-cb4", "vb4"}
                    or protected.intersection(re.findall(r"resource\w+", line))
                    or (key == "run" and value in {merger, binder, _MERGER})):
                return {}
    result = {}
    for geometry_hash in {row[0] for row in components.values()}:
        layout = tuple(sorted((row for row in components.values() if row[0] == geometry_hash), key=lambda row: row[3]))
        cursor = 0
        for _hash, _first, count, offset, size in layout:
            # Bones past 256 are unreachable by u8 Blend indices; the merger
            # drops those writes, so they do not invalidate the palette proof.
            if (count < 3 or count % 3 or not 1 <= size <= 256 or offset != cursor
                    or offset + size > 1024):
                return {}
            cursor += size
        if len({(row[1], row[2]) for row in layout}) != len(layout):
            return {}
        result.update({name: WwmiSkeletonProof(blend, layout) for name, row in components.items() if row[0] == geometry_hash})
    return result

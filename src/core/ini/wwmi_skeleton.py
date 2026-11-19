"""Prove which WWMI skeleton palette one INI's component draws skin against.

Either the stock R8 merged-skeleton template (see ``wwmi_skeleton_modern``) or
WWMI's native per-component skeleton left untouched by the INI. These are
bounded INI proofs, not shader execution or numeric palette guessing.
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


def _pixel_line(line):
    """Pixel-shader state that cannot affect geometry or skinning bindings."""
    resource = r"(?:ref )?(?:resource[\w.\-\\]+|null)"
    return bool(_match(r"ps-t\d+ = " + resource, line)
                or _match(r"resource\\rabbitfx\\(?:diffuse|lightmap|normalmap|materialmap|glowmap|fxmap|cutoutmap|specialmap) = " + resource, line)
                or line in _PIXEL_RUNS)


def _pixel_scratch_line(line, resources):
    key, value = _assignment(line)
    return key in resources and (value == "null" or bool(_match(r"ref ps-t\d+", value or "")))


def _pixel_scratch_resources(commands):
    """Prove empty placeholders are used exclusively for pixel-slot save/restore.

    Check every consumer, including phase-prefixed commands. Resource aliases,
    buffer declarations and geometry/compute consumers cannot establish this role.
    """
    resources = set()
    for lines in commands.values():
        for _phase, line in lines:
            key, value = _assignment(line)
            if (key and _match(r"resource[\w.\-\\]+", key) and _match(r"ref ps-t\d+", value or "")
                    and commands.get(key) == []):
                resources.add(key)
    for lines in commands.values():
        for _phase, line in lines:
            used = resources.intersection(re.findall(r"\bresource[\w.\-\\]+", line))
            if used and not (_control(line) or _pixel_line(line) or _pixel_scratch_line(line, resources)):
                resources.difference_update(used)
    return frozenset(resources)


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


def _without_pixel_state(commands):
    """Project proven pixel-only work out of structural skeleton validation.

    Keep the original commands for the independent protected-resource checks.
    Variable writes and geometry/compute work are never removed by this projection.
    """
    plain = {name: _plain(lines) for name, lines in commands.items()}
    resources = _pixel_scratch_resources(commands)
    def pixel(line):
        return _pixel_line(line) or _pixel_scratch_line(line, resources)
    helpers = _local_lists(plain, pixel)
    result = {}
    for name, lines in plain.items():
        if lines is None or name.startswith("resource"):
            result[name] = lines
            continue
        output, stack, malformed = [], [], False
        for line in lines:
            if pixel(line) or (line.startswith("run = ") and line[6:] in helpers):
                continue
            if line.startswith("if "):
                stack.append([len(output), False, False])
            elif line.startswith(("elif ", "else if ")) or line == "else":
                if not stack or stack[-1][2]:
                    malformed = True
                elif line == "else":
                    stack[-1][2] = True
            elif line == "endif":
                if not stack:
                    malformed = True
                    output.append(line)
                    continue
                start, meaningful, _else = stack.pop()
                if not meaningful:
                    del output[start:]
                    continue
                if stack:
                    stack[-1][1] = True
            elif not _control(line) and stack:
                stack[-1][1] = True
            output.append(line)
        # Malformed control structure cannot become valid through projection.
        result[name] = lines if stack or malformed else output
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


_SKELETON_SHADERS = {_MERGER, r"customshader\wwmiv1\skeletonremapper", r"customshader\wwmiv1\blendremapper"}
_SKELETON_KEYS = {"vs", "vs-cb3", "vs-cb4", _OFFSET, _COUNT, _API + "blend_remap_id"}
_COMPONENT_FIELDS = ("hash", "match_first_index", "match_index_count")
# Pixel-stage bindings, output-merger state and draws. None of them can change
# the vertex buffers, vertex shader or skeleton CBs a later draw skins with.
_PIXEL_DRAW_KEY = re.compile(
    r"ps|ps-(?:t|cb)\d+|o\d|od|[xyzw]\d*"
    r"|(?:blend|alpha|mask|blend_factor)(?:\[\d+\])?|depth_\w+|stencil_\w+|\w+_state_merge"
    r"|cull|fill|front|scissor_enable|multisample_enable|antialiased_line_enable|alpha_to_coverage"
    r"|sample_mask|topology|draw(?:indexed)?(?:instanced)?|drawauto")
# 3DMigoto built-ins that only touch output-merger state.
_OUTPUT_BUILTINS = {"builtincommandlistunbindallrendertargets"}
# Vertex-pipeline shaders and their slots; custom shaders restore them after running.
_GEOMETRY_STAGE_KEY = re.compile(r"(?:vs|hs|ds|gs)(?:-(?:t|cb)\d+)?")


def pixel_draw_lists(sections, own_resources=True):
    """Return this INI's CommandList/CustomShader sections that only do pixel work.

    Their closure may branch, write unqualified local or RabbitFX variables
    and IniParams, bind pixel-stage and output-merger state (including RabbitFX
    maps and render targets), check texture overrides on pixel/output slots,
    draw and call other such sections, the RabbitFX pixel helpers or output-only
    built-ins. Custom shader sections may also set geometry-stage shaders and
    slots, which 3DMigoto restores after they run. With ``own_resources`` the
    closure may also write and clear resources declared in this INI: a
    namespaced INI's resources are private to it. Compute, UAVs, vertex/index
    buffers and qualified names are never allowed, so another INI calling such
    a list inside a proven draw scope cannot change which Blend stream or
    skeleton palette that draw skins with.
    """
    commands = _commands(sections)
    owned = {name for name in commands if name.startswith("resource")} if own_resources else set()
    result = {name for name in commands if name.startswith(("commandlist", "customshader"))}

    def allowed(name, line):
        if _control(line) or _match(r"local \$\w+", line):
            return True
        key, value = _assignment(line)
        if not key:
            return False
        if key == "run":
            return value in result or value in _PIXEL_HELPERS or value in _OUTPUT_BUILTINS
        if key.startswith(("$", "local ")):
            return bool(_match(r"(?:local )?\$(?:\\rabbitfx\\)?\w+", key))
        if _pixel_line(line):
            return True
        if "\\" in key or re.search(r"\b(?:resource|commandlist|customshader)\\", value):
            return False
        if key == "checktextureoverride":
            return bool(_match(r"ps-t\d+|o\d|od", value))
        if key == "clear":
            return value.split(" ", 1)[0] in owned
        return bool(key in owned or _PIXEL_DRAW_KEY.fullmatch(key)
                    or (name.startswith("customshader") and _GEOMETRY_STAGE_KEY.fullmatch(key)))

    changed = True
    while changed:
        changed = False
        for name in sorted(result):
            if not all(allowed(name, line) for _phase, line in commands[name]):
                result.discard(name)
                changed = True
    return frozenset(result)


def native_wwmi_component_palettes(sections, external_neutral=frozenset()):
    """Return ``(root section -> (hash, first, count), altered resources)``.

    WWMI keeps the game's per-component skeleton bound in ``vs-cb3``/``vs-cb4``
    unless a mod merges or replaces it. An INI that never runs a skeleton or
    Blend remapper, never assigns the vertex shader, its skeleton CBs, Blend
    input layout or the WWMI palette variables, therefore skins every draw of a
    ``match_first_index`` component override with that component's native
    palette. Custom shader sections restore their pipeline state, so only their
    resource use counts. Calls outside the INI must be WWMI core helpers, the
    RabbitFX pixel helpers, output-only 3DMigoto built-ins or sibling
    ``external_neutral`` pixel/draw lists.
    Resources assigned anywhere, or read other than as a ``vbN`` binding, are
    returned as altered and cannot back a native Blend.
    """
    commands = _commands(sections)
    if not commands:
        return {}, frozenset()
    altered, components = set(), {}
    for name, lines in commands.items():
        if name.startswith("resource"):
            continue
        shader = name.startswith("customshader")
        fields = {}
        for _phase, line in lines:
            if _control(line):
                continue
            key, value = _assignment(line)
            if key is None:
                continue
            if key == "run" and (value in _SKELETON_SHADERS or not (
                    value in commands or "\\wwmiv1\\" in value or value in _PIXEL_HELPERS
                    or value in _OUTPUT_BUILTINS or value in external_neutral)):
                return {}, frozenset()
            if not shader and (key in _SKELETON_KEYS or (key.startswith("vb") and "->" in key)):
                return {}, frozenset()
            if key.startswith("resource"):
                altered.add(key)
            if not re.fullmatch(r"vb\d+", key):
                altered.update(re.findall(r"\bresource[\w.\-\\]+", value))
            if key in _COMPONENT_FIELDS:
                fields.setdefault(key, []).append(value)
        if name.startswith("textureoverride") and all(len(fields.get(key, ())) == 1 for key in _COMPONENT_FIELDS):
            geometry_hash, first, count = (fields[key][0] for key in _COMPONENT_FIELDS)
            if _match(r"[0-9a-f]{8}", geometry_hash) and first.isdigit() and count.isdigit():
                components[name] = geometry_hash, int(first), int(count)
    return components, frozenset(altered)


def stock_wwmi_merged_layouts(sections, external_neutral=frozenset()):
    """Return root-section -> (Blend resource, full component layout) proofs.

    ``external_neutral`` names sibling-INI pixel/draw lists (see
    ``pixel_draw_lists``) that a proven draw scope may call.
    """
    commands = _commands(sections)
    if not commands or _MERGER in commands or any(name in commands for name in _PIXEL_HELPERS):
        return {}
    if not any(line == f"run = {_MERGER}" for lines in commands.values() for _phase, line in lines):
        return {}
    from .wwmi_skeleton_modern import modern_wwmi_merged_layouts

    plain = _without_pixel_state(commands)
    return modern_wwmi_merged_layouts(commands, plain, external_neutral)

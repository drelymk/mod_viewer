"""Prove merged ZZZ skeleton palettes assembled by a compute shader in one INI.

Some ZZZ mods skin one component against several native palettes at once. At
each component's Blend pass (``DRAW_TYPE == 1``) the bound ``vs-t0`` palette is
copied into a resource, a compute shader concatenates those palettes into one
buffer, and a fixed skinning shader reads the mod's Blend file whose IDs index
that concatenated buffer. This is a bounded template proof: the concatenation
shader must be the exact contiguous row-copy form and the skinning shader the
exact reference kernel below. Nothing is executed and no palette is guessed
from numeric ID ranges; each segment is named by its native Blend hash.
"""

from dataclasses import dataclass
import re

from ..geometry.skinning import normalize_skinning_source_file
from .animation_resources import _strip_hlsl_comments
from .command_phases import parse_command_line
from .wwmi_skeleton import _commands

# The reference skinning kernel: t0 palette rows (float4, three per bone),
# t1 40-byte position/normal/tangent vertices, t2 32-byte Blend vertices
# (four float weights then four 32-bit IDs), u0 skinned vertices.
SKINNING_TEMPLATE = """
Texture1D<float4> IniParams : register(t120);
Buffer<float4> t0 : register(t0);
Buffer<float> t1 : register(t1);
Buffer<float> t2 : register(t2);
RWBuffer<float> u0 : register(u0);
[numthreads(64, 1, 1)]
void main(uint3 thread_id : SV_DispatchThreadID)
{
    uint id = thread_id.x;
    uint vertex_count = asuint(IniParams[0].x);
    if (id >= vertex_count)
        return;
    uint base10 = id * 10;
    float3 position = float3(t1[base10], t1[base10 + 1], t1[base10 + 2]);
    float3 normal = float3(t1[base10 + 3], t1[base10 + 4], t1[base10 + 5]);
    float4 tangent = float4(t1[base10 + 6], t1[base10 + 7], t1[base10 + 8], t1[base10 + 9]);
    uint base8 = id * 8;
    float4 weights = float4(t2[base8], t2[base8 + 1], t2[base8 + 2], t2[base8 + 3]);
    int4 indices = int4(asint(t2[base8 + 4]), asint(t2[base8 + 5]), asint(t2[base8 + 6]), asint(t2[base8 + 7]));
    int4 row0_indices = indices * 3;
    float4 blend = t0.Load(row0_indices.y) * weights.yyyy;
    blend = weights.xxxx * t0.Load(row0_indices.x) + blend;
    blend = weights.zzzz * t0.Load(row0_indices.z) + blend;
    float4 row0 = weights.wwww * t0.Load(row0_indices.w) + blend;
    int4 row12_a = indices.xxyy * 3 + int4(2, 1, 2, 1);
    int4 row12_b = indices.zzww * 3 + int4(2, 1, 2, 1);
    blend = t0.Load(row12_a.w) * weights.yyyy;
    blend = weights.xxxx * t0.Load(row12_a.y) + blend;
    blend = weights.zzzz * t0.Load(row12_b.y) + blend;
    float4 row1 = weights.wwww * t0.Load(row12_b.w) + blend;
    blend = t0.Load(row12_a.z) * weights.yyyy;
    blend = weights.xxxx * t0.Load(row12_a.x) + blend;
    blend = weights.zzzz * t0.Load(row12_b.x) + blend;
    float4 row2 = weights.wwww * t0.Load(row12_b.z) + blend;
    float4 p = float4(position, 1.0);
    float3 out_position = float3(dot(p, row0), dot(p, row1), dot(p, row2));
    float3 out_normal = float3(dot(normal, row0.xyz), dot(normal, row1.xyz), dot(normal, row2.xyz));
    float3 out_tangent = float3(dot(tangent.xyz, row0.xyz), dot(tangent.xyz, row1.xyz), dot(tangent.xyz, row2.xyz));
    u0[base10] = out_position.x;
    u0[base10 + 1] = out_position.y;
    u0[base10 + 2] = out_position.z;
    u0[base10 + 3] = out_normal.x;
    u0[base10 + 4] = out_normal.y;
    u0[base10 + 5] = out_normal.z;
    u0[base10 + 6] = out_tangent.x;
    u0[base10 + 7] = out_tangent.y;
    u0[base10 + 8] = out_tangent.z;
    u0[base10 + 9] = tangent.w;
}
"""
_MAX_BONES = 1024
_MERGE_BLOCK = re.compile(r"if\(id<(\d+)\)\{u0\[id\]=t(\d+)\[id-(\d+)\];return;\}")
_MERGE_MAIN = re.compile(
    r"\[numthreads\(64,1,1\)\]voidmain\(uint3(\w+):SV_DispatchThreadID\)\{uintid=\1\.x;"
    r"((?:" + _MERGE_BLOCK.pattern + r")+)\}")
_RESOURCE = r"resource[\w.\-\\]+"


@dataclass(frozen=True, slots=True)
class ZzzMergedProof:
    """One merged palette: Blend/position files and ``(blend hash, first bone, bone count)`` segments."""

    blend_file: str
    position_file: str
    layout: tuple[tuple[str, int, int], ...]


def _compact(text):
    return re.sub(r"\s+", "", _strip_hlsl_comments(text or ""))


_SKINNING = _compact(SKINNING_TEMPLATE)


def merge_segments(text):
    """Return ``[(input slot, first row, end row)]`` for the exact row-copy shader, or None."""
    source = _compact(text)
    if not re.search(r"RWBuffer<float4>u0:register\(u0\);", source):
        return None
    main = _MERGE_MAIN.search(source)
    if main is None or source[main.end():]:
        return None
    # Declarations and unused helper functions may precede the fixed kernel,
    # which calls none of them. Preprocessor directives could rename its
    # buffers or identifiers, so none are allowed.
    if "#" in source[:main.start()]:
        return None
    segments, cursor = [], 0
    for end, slot, start in _MERGE_BLOCK.findall(main[2]):
        end, slot, start = int(end), int(slot), int(start)
        if (start != cursor or end <= start or start % 3 or end % 3 or end > 3 * _MAX_BONES
                or f"Buffer<float4>t{slot}:register(t{slot});" not in source
                or any(slot == other for other, _, _ in segments)):
            return None
        segments.append((slot, start, end))
        cursor = end
    return segments


def is_skinning_shader(text):
    return _compact(text) == _SKINNING


def _custom_shader(sections, commands, name, read_shader):
    """Shader text of a custom shader section that only selects and dispatches it."""
    rows = commands.get(name)
    if rows is None or any(phase or not text.startswith(("cs = ", "dispatch = ")) for phase, text in rows):
        return None
    # Shader paths keep their authored case for case-sensitive sources.
    raw = next((lines for key, lines in sections.items() if key.casefold() == name), ())
    values = []
    for line in raw:
        key, sep, value = parse_command_line(line).text.partition("=")
        if sep and key.strip().casefold() == "cs":
            values.append(value.strip())
    return read_shader(values[0]) if len(values) == 1 else None


def _runs(rows):
    """Yield ``(bindings at call, target)`` for each run, tracking cs/vb slots in order.

    Explicit ``pre`` commands run before the draw like unprefixed ones; only
    ``post`` commands run after it.
    """
    bindings = {}
    for phase, text in rows:
        key, sep, value = text.partition(" = ")
        if not sep or phase == "post":
            continue
        value = value.removeprefix("ref ")
        if key == "run":
            yield dict(bindings), value
        elif re.fullmatch(r"cs-[tu]\d+|vb\d+", key):
            bindings[key] = value


def zzz_merged_palettes(sections, resources, resolve_vertex_info, read_shader):
    """Return ``root section -> ZzzMergedProof`` for draws that run a proven merged skin.

    ``read_shader(value)`` returns the text of an INI shader reference or None.
    """
    commands = _commands(sections)
    if not commands:
        return {}
    shaders = {}

    def shader(name):
        if name not in shaders:
            shaders[name] = _custom_shader(sections, commands, name, read_shader)
        return shaders[name]

    # Every way a resource can be written: assignment, UAV binding or any
    # phased command naming it. Read-only SRV/vertex bindings are harmless.
    writes = {}
    for section, rows in commands.items():
        for phase, text in rows:
            key, sep, value = text.partition(" = ")
            if not sep:
                continue
            targets = re.findall(_RESOURCE, text) if phase else []
            if re.fullmatch(_RESOURCE, key):
                targets.append(key)
            elif re.fullmatch(r"\w+-u\d+", key):
                targets += re.findall(_RESOURCE, value)
            for target in targets:
                writes.setdefault(target, []).append((section, text))

    def captured_palette(resource):
        """Native Blend hash whose Blend pass alone copies ``vs-t0`` into ``resource``."""
        if [text for _section, text in writes.get(resource, ())] != [f"{resource} = copy vs-t0"]:
            return None
        section = writes[resource][0][0]
        hashes, stack, guarded = [], [], False
        for phase, text in commands[section]:
            if text.startswith("if "):
                stack.append(text[3:])
            elif text.startswith(("elif ", "else if ")) and stack:
                stack[-1] = text.split(" ", 2 if text.startswith("else if ") else 1)[-1]
            elif text == "else" and stack:
                stack[-1] = "else"
            elif text == "endif" and stack:
                stack.pop()
            elif text.startswith("hash = "):
                hashes.append(text[7:])
            elif text == f"{resource} = copy vs-t0":
                guarded = "draw_type == 1" in stack
        valid = section.startswith("textureoverride") and len(hashes) == 1 and re.fullmatch(r"[0-9a-f]{8}", hashes[0])
        return hashes[0] if valid and guarded else None

    def merged_layout(section):
        """``(output resource, layout)`` for a command list that runs one merge dispatch."""
        calls = [(bindings, target) for bindings, target in _runs(commands[section])
                 if target.startswith("customshader")]
        if len(calls) != 1:
            return None
        bindings, target = calls[0]
        segments = merge_segments(shader(target))
        output = bindings.get("cs-u0")
        if not segments or not output or [text for _s, text in writes.get(output, ())] != [f"cs-u0 = ref {output}"]:
            return None
        layout = []
        for slot, start, end in segments:
            native = captured_palette(bindings.get(f"cs-t{slot}"))
            if native is None:
                return None
            layout.append((native, start // 3, (end - start) // 3))
        return output, tuple(layout)

    def file_buffer(resource, stride):
        info = resolve_vertex_info(resource) or {}
        if info.get("stride") != stride or resource in writes:
            return None
        return normalize_skinning_source_file(info.get("filename"))

    # A skin list runs one merge list and the skinning kernel against it, then
    # binds the kernel's output as the drawn positions.
    skins = {}
    for section, rows in commands.items():
        if not section.startswith("commandlist"):
            continue
        merges, kernels, after = [], [], {}
        for bindings, target in _runs(rows):
            if target in commands and target.startswith("commandlist") and (layout := merged_layout(target)):
                merges.append(layout)
            elif target.startswith("customshader") and is_skinning_shader(shader(target)):
                kernels.append(bindings)
        if len(merges) != 1 or len(kernels) != 1:
            continue
        bindings = kernels[0]
        for _phase, text in rows:
            key, _sep, value = text.partition(" = ")
            if key in ("vb0", "vb2"):
                after[key] = value.removeprefix("ref ")
        output = bindings.get("cs-u0")
        blend = file_buffer(bindings.get("cs-t2"), 32)
        position = file_buffer(bindings.get("cs-t1"), 40)
        if (bindings.get("cs-t0") != merges[0][0] or not output or after.get("vb0") != output or "vb2" in after
                or [text for _s, text in writes.get(output, ())] != [f"cs-u0 = ref {output}"]
                or not blend or not position):
            continue
        skins[section] = ZzzMergedProof(blend, position, merges[0][1])
    if not skins:
        return {}

    # A draw belongs to the root override whose call closure reaches exactly
    # one skin list; other vertex rebinding in that closure voids the proof.
    def closure(section, seen):
        if section in seen or section not in commands or len(seen) > 64:
            return
        seen.add(section)
        for _bindings, target in _runs(commands[section]):
            closure(target, seen)

    result = {}
    for section in commands:
        if not section.startswith("textureoverride"):
            continue
        reached = set()
        closure(section, reached)
        if len(reached) > 64:
            continue  # An unexplored closure cannot prove the absence of rebinding.
        proofs = {skins[name] for name in reached if name in skins}
        rebinds = any(text.partition(" = ")[0] in ("vb0", "vb2") for name in reached - set(skins)
                      for _phase, text in commands[name])
        if len(proofs) == 1 and not rebinds:
            result[section] = proofs.pop()
    return result

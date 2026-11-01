"""Validated DXGI decoding, stream assembly and conservative morph reversal."""
from dataclasses import dataclass
import math
import re
import struct
from .core import Unsupported, Context


@dataclass
class Element:
    semantic: str
    index: int
    format: str
    offset: int

    @property
    def label(self):
        return self.semantic + (str(self.index) if self.index else "")


def codec(fmt):
    fmt = fmt.removeprefix("DXGI_FORMAT_")
    fields, kind = fmt.rsplit("_", 1)
    bits = [int(x) for x in re.findall(r"[RGBA](\d+)", fields)]
    if not bits or len(set(bits)) != 1:
        raise Unsupported(f"Unsupported DXGI format {fmt}")
    width = bits[0]
    code = {("FLOAT", 16): "e", ("FLOAT", 32): "f", ("UINT", 8): "B",
            ("UINT", 16): "H", ("UINT", 32): "I", ("SINT", 8): "b",
            ("SINT", 16): "h", ("SINT", 32): "i", ("UNORM", 8): "B",
            ("UNORM", 16): "H", ("SNORM", 8): "b", ("SNORM", 16): "h"}.get((kind, width))
    if not code:
        raise Unsupported(f"Unsupported DXGI format {fmt}")
    st = struct.Struct("<" + code * len(bits))
    def decode(data, offset):
        values = st.unpack_from(data, offset)
        if kind == "UNORM":
            return tuple(x / ((1 << width) - 1) for x in values)
        if kind == "SNORM":
            return tuple(max(-1.0, x / ((1 << (width - 1)) - 1)) for x in values)
        return values
    return st.size, decode


def stream_layout(inv, name, slot, role, vertex_count=None):
    header, raw = inv.header(name.replace(".buf", ".txt"))
    stride = int(header["stride"])
    if stride <= 0:
        raise Unsupported("Vertex stride must be positive")
    elements = []
    if role == "position":
        if stride != 40:
            raise Unsupported(f"ZZZ pre-skinning position stride {stride} is not supported")
        elements = [Element("POSITION", 0, "R32G32B32_FLOAT", 0),
                    Element("NORMAL", 0, "R32G32B32_FLOAT", 12),
                    Element("TANGENT", 0, "R32G32B32A32_FLOAT", 24)]
    elif role == "blend":
        if stride == 4:
            elements = [Element("BLENDINDICES", 0, "R32_UINT", 0)]
        elif stride in (16, 32):
            prefix = "R32G32" + ("B32A32" if stride == 32 else "")
            elements = [Element("BLENDWEIGHTS", 0, prefix + "_FLOAT", 0),
                        Element("BLENDINDICES", 0, prefix + "_UINT", stride // 2)]
        else:
            raise Unsupported(f"Unsupported ZZZ blend stride {stride}")
    else:
        for e in raw:
            if int(e.get("InputSlot", -1)) == slot and e.get("InputSlotClass") == "per-vertex":
                elements.append(Element(e["SemanticName"], int(e["SemanticIndex"]), e["Format"], int(e["AlignedByteOffset"])))
        if role == "direct_position":
            if header.get("topology") != "trianglelist" or any(
                not any(e.semantic == semantic and e.index == 0
                        and e.format.removeprefix("DXGI_FORMAT_") == "R32G32B32_FLOAT"
                        for e in elements)
                for semantic in ("POSITION", "NORMAL")
            ):
                raise Unsupported("Direct geometry requires declared triangle-list positions and normals")
            elements.sort(key=lambda e: (e.semantic != "POSITION", e.index))
        elif not elements or not any(e.semantic == "TEXCOORD" for e in elements):
            raise Unsupported("Missing declared texture coordinate layout")
    for e in elements:
        size, _ = codec(e.format)
        if e.offset < 0 or e.offset + size > stride:
            raise Unsupported(f"Element {e.label} lies outside stride {stride}")
    return stride, elements, int(header.get("vertex count", 0)), int(header.get("first vertex", 0))


def reverse_morph(data, inv, morph, context):
    if not morph or not morph.get("dispatches"):
        return data, "not_detected", []
    # Atomic fallback: failure in any operation leaves the entire captured expression intact.
    try:
        result = bytearray(data)
        operations = []
        shaders = {d.get("shader") for d in morph["dispatches"] if d.get("shader")}
        if len(shaders) > 1:
            raise Unsupported("Unrecognized mixed-shader expression sequence")
        for dispatch in morph["dispatches"]:
            context.tick("Validating expression metadata")
            delta = inv.path(dispatch["delta"]).read_bytes()
            meta = inv.path(dispatch["constant"]).read_bytes()
            import hashlib
            for payload, key in ((delta, "delta_sha256"), (meta, "constant_sha256")):
                if dispatch.get(key) and hashlib.sha256(payload).hexdigest() != dispatch[key]:
                    raise Unsupported("Morph capture changed since analysis")
            if len(meta) != 16 or len(delta) % 40:
                raise Unsupported("Unrecognized morph metadata layout")
            start, count, scale, _ = struct.unpack("<IIff", meta)
            if not math.isfinite(scale) or start + count > len(delta) // 40:
                raise Unsupported("Invalid morph range or multiplier")
            for i in range(start, start + count):
                index, *change = struct.unpack_from("<I9f", delta, i * 40)
                if index >= len(result) // 40 or not all(math.isfinite(v) for v in change):
                    raise Unsupported("Morph references invalid geometry")
                operations.append((index, change, scale))
        if not operations:
            raise Unsupported("No recognized morph operations")
        # Reverse operation order to respect ordered floating point updates.
        for index, change, scale in reversed(operations):
            values = struct.unpack_from("<9f", result, index * 40)
            struct.pack_into("<9f", result, index * 40, *(a - b * scale for a, b in zip(values, change)))
        if not all(math.isfinite(v) for row in struct.iter_unpack("<10f", result) for v in row):
            raise Unsupported("Morph reversal produced nonfinite values")
        replay = bytearray(result)
        for index, change, scale in operations:
            values = struct.unpack_from("<9f", replay, index * 40)
            struct.pack_into("<9f", replay, index * 40, *(a + b * scale for a, b in zip(values, change)))
        error = max(abs(a[0] - b[0]) for a, b in zip(struct.iter_unpack("<f", data), struct.iter_unpack("<f", replay)))
        if error > 1e-5:
            raise Unsupported("Expression reversal did not pass forward-replay validation")
        morph["forward_replay_max_error"] = error
        return bytes(result), "reversed_recognized", []
    except (OSError, ValueError, struct.error, KeyError) as error:
        return data, "captured_expression", [f"Expression preserved: {error}"]


@dataclass
class Mesh:
    component: object
    elements: list
    vertices: list
    indices: list
    source_position: bytes
    morph_status: str


def decode_component(inv, component, context=None):
    ctx = context or Context()
    streams, count, columns, layouts = {}, None, [], []
    source_position = b""
    morph_status = "not_detected"
    notes = []
    direct = component.provenance.get("geometry_kind") == "direct"
    roles = ("position", "texcoord") if direct else ("position", "blend", "texcoord")
    for role in roles:
        source = component.streams[role]
        name, slot = source["file"], source["slot"]
        layout_role = "direct_position" if direct and role == "position" else role
        stride, elements, declared_count, first_vertex = stream_layout(inv, name, slot, layout_role)
        data = inv.path(name).read_bytes()
        if len(data) % stride:
            raise Unsupported(f"Truncated {role} buffer")
        n = len(data) // stride
        if count is None:
            count = n
        if not n or n != count:
            raise Unsupported(f"Inconsistent {role} vertex count: {n}, expected {count}")
        # UV headers can describe only the drawn subset of a full resource dump.
        # Its byte-derived count must still match the position stream.
        complete_uv_payload = role == "texcoord" and 0 <= declared_count <= n
        if first_vertex != 0 or (declared_count not in (0, n) and not complete_uv_payload):
            raise Unsupported(f"Partial vertex snapshot in {role}: first={first_vertex}, count={declared_count}, payload={n}")
        if role == "position":
            source_position = data
            if not direct:
                data, morph_status, notes = reverse_morph(data, inv, component.morph, ctx)
        decoded = []
        decoders = [(e.offset, codec(e.format)[1]) for e in elements]
        for i in range(n):
            if i % 1024 == 0:
                ctx.tick("Decoding " + component.name, i, n)
            row = [decoder(data, i * stride + offset) for offset, decoder in decoders]
            if not all(math.isfinite(v) for value in row for v in value):
                raise Unsupported(f"Nonfinite {role} vertex {i}")
            decoded.append(row)
        if role == "blend":
            if stride == 4:
                decoded = [[(1.0,), row[0]] for row in decoded]
                elements.insert(0, Element("BLENDWEIGHTS", 0, "R32_FLOAT", 0))
            for row in decoded:
                if any(v < -0.00001 or v > 1.00001 for v in row[0]) or abs(sum(row[0]) - 1) > 0.02:
                    raise Unsupported("Invalid or unsupported blend weights")
        streams[role] = decoded
        layouts.extend(elements)
        columns.append(decoded)
    vertices = [sum((rows[i] for rows in columns), []) for i in range(count)]
    # Merged buffers retain semantic types; offsets become contiguous.
    offset, elements = 0, []
    for e in layouts:
        elements.append(Element(e.semantic, e.index, e.format, offset))
        offset += codec(e.format)[0]
    if len({e.label for e in elements}) != len(elements):
        raise Unsupported("Conflicting vertex semantic channels")
    indices = []
    for part in component.parts:
        header, _ = inv.header(part.ib_file)
        fmt = header.get("format", "").removeprefix("DXGI_FORMAT_")
        if header.get("topology") != "trianglelist" or fmt not in ("R16_UINT", "R32_UINT"):
            raise Unsupported("Only declared triangle-list R16/R32 index buffers are supported")
        width = 2 if fmt == "R16_UINT" else 4
        data = inv.path(part.ib_file.replace(".txt", ".buf")).read_bytes()
        # Binary dumps contain the whole resource; the text header supplies the draw range.
        first = part.first * width + part.index_offset
        end = first + part.count * width
        if end > len(data) or part.count % 3:
            raise Unsupported("Truncated or non-triangular index range")
        raw_indices = [v[0] for v in struct.iter_unpack("<H" if width == 2 else "<I", data[first:end])]
        if any(i + part.base_vertex < 0 or i + part.base_vertex >= count for i in raw_indices):
            raise Unsupported("Index plus base vertex is outside vertex streams")
        indices.append(raw_indices)
    component.vertex_count = count
    component.triangle_count = sum(len(x) // 3 for x in indices)
    positions = [v[0] for v in vertices]
    component.bounds = [[min(p[a] for p in positions) for a in range(3)], [max(p[a] for p in positions) for a in range(3)]]
    component.diagnostics = list(dict.fromkeys(component.diagnostics + notes))
    component.morph["status"] = morph_status
    return Mesh(component, elements, vertices, indices, source_position, morph_status)

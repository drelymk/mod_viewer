"""Native WWMI component geometry and captured slot-usage output."""

import struct

from .geometry import codec, encoder


def texture_plan(meshes, refs, folders):
    """Name each object-local payload for every native component that uses it."""
    ordinals, users, identities, contents = {}, {}, {}, {}
    for mesh in meshes:
        c = mesh.component
        group = folders[c.id]
        for part_index, part in enumerate(c.parts):
            ordinal = ordinals.get(group, 0)
            ordinals[group] = ordinal + 1
            for texture in part.textures:
                key = c.id, part_index, texture["file"]
                if key not in refs:
                    continue
                ext, selected = refs[key]
                identity = group, texture["hash"], texture["sha256"], ext, selected
                identities[key] = identity
                users.setdefault(identity, set()).add(ordinal)
                contents.setdefault((group, texture["hash"]), set()).add(texture["sha256"])
    filenames = {}
    for identity, components in users.items():
        group, native_hash, payload, ext, selected = identity
        suffix = "-" + payload[:12] if len(contents[group, native_hash]) > 1 else ""
        name = "Components-" + "-".join(map(str, sorted(components))) + " t=" + native_hash + suffix + ext
        filenames[identity] = group + "/" + (name if selected else "assets/" + name)
    return {key: filenames[identity] for key, identity in identities.items()}


def write_component(directory, ordinal, mesh, part, indices, context):
    """Keep every vertex channel while giving each native component local indices."""
    used = sorted({index + part.base_vertex for index in indices})
    remap = {index: local for local, index in enumerate(used)}
    names = {kind: f"Component {ordinal}.{kind}" for kind in ("fmt", "vb", "ib")}
    stride = sum(codec(element.format)[0] for element in mesh.elements)
    lines = [f"stride: {stride}", "topology: trianglelist", "format: DXGI_FORMAT_R32_UINT"]
    for index, element in enumerate(mesh.elements):
        lines.extend((f"element[{index}]:", f"  SemanticName: {element.semantic}",
                      f"  SemanticIndex: {element.index}", f"  Format: {element.format}",
                      "  InputSlot: 0", f"  AlignedByteOffset: {element.offset}",
                      "  InputSlotClass: per-vertex", "  InstanceDataStepRate: 0"))
    (directory / names["fmt"]).write_text("\n".join(lines) + "\n", encoding="utf-8")
    encoders = [encoder(element.format) for element in mesh.elements]
    with (directory / names["vb"]).open("wb") as stream:
        for local, source in enumerate(used):
            if local % 1024 == 0:
                context.tick("Writing " + mesh.component.name, local, len(used))
            stream.write(b"".join(encode(values) for encode, values in zip(encoders, mesh.vertices[source])))
    mapped = [remap[index + part.base_vertex] for index in indices]
    (directory / names["ib"]).write_bytes(struct.pack(f"<{len(mapped)}I", *mapped))
    return names, len(used)


def texture_usage(component, part):
    draws = {item["draw"]: item for item in component.provenance.get("draw_evidence", ())}
    usage = {}
    for texture in part.textures:
        shaders = draws.get(texture.get("draw"), {}).get("shaders", {})
        vs, ps = texture.get("vertex_shader") or shaders.get("vs"), texture.get("shader")
        if vs and ps:
            key = f"ps-t{texture['slot']}"
            value = f"{texture['hash']}-vs={vs}-ps={ps}"
            if value not in usage.setdefault(key, []):
                usage[key].append(value)
    return usage

"""Declared split ATTRIBUTE skinning layouts, without shader reconstruction."""

from dataclasses import replace
import hashlib

from ..core import Unsupported


def split_components(mesh):
    """Expose source draw ranges independently without decoding their shared vertices again."""
    c = mesh.component
    for ordinal, (part, indices) in enumerate(zip(c.parts, mesh.indices), 1):
        identity = (c.id, part.first, part.count, part.base_vertex, part.index_offset, part.sha256)
        points = [mesh.vertices[i + part.base_vertex][0] for i in set(indices)]
        child = replace(c, id=hashlib.sha256(repr(identity).encode()).hexdigest()[:12],
                        name=f"{c.name} Component{ordinal:03d}", parts=[part],
                        triangle_count=len(indices) // 3,
                        bounds=[[min(p[a] for p in points) for a in range(3)],
                                [max(p[a] for p in points) for a in range(3)]],
                        provenance={**c.provenance, "object_folder": c.streams["position"]["hash"],
                                    "draw_evidence": [d for d in c.provenance["draw_evidence"] if d["draw"] in part.draws]})
        yield replace(mesh, component=child, indices=[indices])


def material_roles(textures, color_target, shader):
    """Recognize color atlases from complete bound WWMI material families."""
    if not color_target or not shader:
        return {}
    by_slot = {t["slot"]: t for t in textures if not t.get("contaminated")
               and (t.get("resource") or {}).get("hash") == t["hash"]}

    def image(slot, formats):
        t = by_slot.get(slot, {})
        return (t.get("format") in formats and t.get("width", 0) >= 128
                and t.get("width") == t.get("height"))

    def same_size(first, second):
        return by_slot[first]["width"] == by_slot[second]["width"]

    color = {"BC7_UNORM_SRGB"}
    # Main material: packed vector, two linear masks, then the color atlas.
    if (image(0, {"BC7_UNORM"}) and image(1, {"BC1_UNORM", "BC3_UNORM"})
            and image(3, color) and same_size(0, 3)
            and (image(2, {"BC1_UNORM", "R8_UNORM"})
                 or (image(2, color) and by_slot[2]["width"] <= by_slot[3]["width"] / 4))):
        return {3: "Diffuse"}
    # Simpler materials keep the color atlas beside the full-size vector map.
    if image(0, {"BC7_UNORM"}) and image(1, color) and same_size(0, 1):
        return {1: "Diffuse"}
    # Eye materials pair a mask and colored data map with a larger iris atlas.
    if (image(0, {"BC1_UNORM"}) and image(1, {"BC3_UNORM_SRGB"})
            and image(2, color) and same_size(0, 1)
            and by_slot[2]["width"] == 2 * by_slot[0]["width"]):
        return {2: "Diffuse"}
    # This family has a full-size packed vector/mask pair and a smaller atlas.
    if (image(0, {"BC3_UNORM"}) and image(1, {"R8_UNORM"}) and image(2, color)
            and same_size(0, 1) and 1 <= by_slot[0]["width"] / by_slot[2]["width"] <= 2):
        return {2: "Diffuse"}
    # Hair may bind its vector atlas later, after smaller auxiliary resources.
    mask = by_slot.get(3, {})
    if (image(0, {"BC3_UNORM"}) and image(1, color)
            and image(2, {"B8G8R8A8_UNORM"}) and mask.get("format") in {"BC1_UNORM", "B8G8R8A8_UNORM"}
            and mask.get("height", 0) >= 128 and mask.get("width") == 2 * mask["height"]
            and image(5, {"BC7_UNORM"}) and same_size(1, 5)
            and by_slot[2]["width"] < by_slot[1]["width"]):
        return {1: "Diffuse"}
    return {}


def layout(header, raw, slot, role):
    from ..geometry import Element

    fields = [e for e in raw if int(e.get("InputSlot", -1)) == slot]
    if (header.get("topology") != "trianglelist" or not fields or any(
        e.get("SemanticName") != "ATTRIBUTE" or e.get("InputSlotClass") != "per-vertex"
        or int(e.get("InstanceDataStepRate", 0)) != 0 for e in fields
    )):
        raise Unsupported("Unsupported split ATTRIBUTE vertex layout")
    attrs = {int(e["SemanticIndex"]): e for e in fields}
    if len(attrs) != len(fields):
        raise Unsupported("Conflicting ATTRIBUTE declarations")
    stride = int(header["stride"])

    def element(attribute, semantic, index, fmt, offset):
        e = attrs.get(attribute, {})
        if (e.get("Format", "").removeprefix("DXGI_FORMAT_") != fmt
                or int(e.get("AlignedByteOffset", -1)) != offset):
            raise Unsupported(f"Unsupported ATTRIBUTE{attribute} {role} layout")
        return Element(semantic, index, fmt, offset)

    if role == "position" and stride == 12 and set(attrs) == {0}:
        return [element(0, "POSITION", 0, "R32G32B32_FLOAT", 0)]
    if role == "normal" and stride == 8 and set(attrs) == {1, 2}:
        return [element(1, "TANGENT", 0, "R8G8B8A8_SNORM", 0),
                element(2, "NORMAL", 0, "R8G8B8A8_SNORM", 4)]
    if role == "texcoord" and stride in (4, 8, 12, 16) and set(attrs) == set(range(5, 5 + stride // 4)):
        return [element(5 + n, "TEXCOORD", n, "R16G16_FLOAT", n * 4)
                for n in range(stride // 4)]
    if role == "blend" and stride in (8, 16) and set(attrs) in ({3, 4}, {3, 4, 14, 15}):
        result = [element(4, "BLENDWEIGHTS", 0, "R8G8B8A8_UNORM", stride // 2),
                  element(3, "BLENDINDICES", 0, "R8G8B8A8_UINT", 0)]
        if 14 in attrs:
            second = [element(15, "BLENDWEIGHTS", 1, "R8G8B8A8_UNORM", stride - 4),
                      element(14, "BLENDINDICES", 1, "R8G8B8A8_UINT", stride // 2 - 4)]
            # Four-influence layouts can declare two aliases for the same bytes.
            if stride == 16:
                result.extend(second)
        elif stride == 16:
            raise Unsupported("Missing second ATTRIBUTE blend group")
        return result
    raise Unsupported(f"Unsupported split ATTRIBUTE {role} stride or channels")

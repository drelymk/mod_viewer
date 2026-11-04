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
                        provenance={**c.provenance,
                                    "draw_evidence": [d for d in c.provenance["draw_evidence"] if d["draw"] in part.draws]})
        yield replace(mesh, component=child, indices=[indices])


def material_roles(textures, color_target, shader):
    """Recognize color atlases from complete bound WWMI material families."""
    return material_evidence(textures, color_target, shader)[0]


def material_evidence(textures, color_target, shader):
    """Return roles and signature specificity before comparing inherited slot counts."""
    if not color_target or not shader:
        return {}, 0
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
        return {3: "Diffuse"}, 2
    # The compact main family omits one mask and ends with a smaller color input.
    if (image(0, {"BC7_UNORM"}) and image(1, {"BC1_UNORM"})
            and image(2, color) and same_size(0, 2)
            and by_slot[1]["width"] * 2 == by_slot[2]["width"]
            and image(3, color) and by_slot[3]["width"] * 4 == by_slot[2]["width"]):
        return {2: "Diffuse"}, 2
    # Color-led hair pairs equal atlases with a short, wide lookup texture.
    # This complete family outranks inherited eye inputs on the same range.
    if (image(0, color) and image(1, {"BC7_UNORM"}) and same_size(0, 1)
            and _color_lookup(by_slot.get(2, {}))):
        return {0: "Diffuse"}, 3
    # Simpler materials keep the color atlas beside the full-size vector map.
    if image(0, {"BC7_UNORM"}) and image(1, color) and same_size(0, 1):
        return {1: "Diffuse"}, 1
    # Eye materials pair a mask and colored data map with a larger iris atlas.
    if (image(0, {"BC1_UNORM"}) and image(1, {"BC3_UNORM_SRGB"})
            and image(2, color) and same_size(0, 1)
            and by_slot[2]["width"] == 2 * by_slot[0]["width"]):
        return {2: "Diffuse"}, 2
    # Layered eyes retain the larger iris and a half-size packed atlas. The
    # leading mask/data pair can trade places, with a smaller sRGB mask.
    if (image(2, color) and image(3, {"B8G8R8A8_UNORM"})
            and by_slot[3]["width"] * 2 == by_slot[2]["width"]
            and ((image(0, {"BC1_UNORM"}) and image(1, {"BC3_UNORM_SRGB"})
                  and same_size(0, 1) and by_slot[1]["width"] * 4 == by_slot[2]["width"])
                 or (image(0, {"BC3_UNORM_SRGB"}) and image(1, {"BC1_UNORM_SRGB"})
                     and by_slot[1]["width"] * 2 == by_slot[0]["width"]
                     and by_slot[0]["width"] * 4 == by_slot[2]["width"]))):
        return {2: "Diffuse"}, 2
    # This family has a full-size packed vector/mask pair and a smaller atlas.
    if (image(0, {"BC3_UNORM"}) and image(1, {"R8_UNORM"}) and image(2, color)
            and same_size(0, 1) and 1 <= by_slot[0]["width"] / by_slot[2]["width"] <= 2):
        return {2: "Diffuse"}, 2
    # Hair may bind its vector atlas later, after smaller auxiliary resources.
    mask = by_slot.get(3, {})
    if (image(0, {"BC3_UNORM"}) and image(1, color)
            and image(2, {"B8G8R8A8_UNORM"}) and mask.get("format") in {"BC1_UNORM", "B8G8R8A8_UNORM"}
            and mask.get("height", 0) >= 128 and mask.get("width") == 2 * mask["height"]
            and image(5, {"BC7_UNORM"})
            and by_slot[1]["width"] in (by_slot[5]["width"], 2 * by_slot[5]["width"])
            and by_slot[2]["width"] < by_slot[1]["width"]):
        # This late-vector family proves more of its binding layout than the
        # generic main family, which can also appear over the same hair range.
        return {1: "Diffuse"}, 3
    # A constant-led layered family has two scaled mask/color atlases and a
    # repeated packed mask. Requiring the constant rejects inherited bindings
    # on passes whose first slot is another surface's color texture.
    constant = by_slot.get(0, {})
    if (constant.get("format") == "B8G8R8A8_UNORM_SRGB"
            and constant.get("width") == constant.get("height") == 1
            and image(1, {"BC1_UNORM"}) and image(2, {"BC3_UNORM"})
            and image(3, color) and same_size(2, 3)
            and image(4, {"BC3_UNORM"}) and image(5, color) and same_size(4, 5)
            and image(6, color) and by_slot[6]["width"] <= by_slot[1]["width"] / 4
            and image(7, {"BC3_UNORM"}) and same_size(4, 7)
            and by_slot[4]["resource"] == by_slot[7]["resource"]
            and by_slot[1]["width"] * 2 == by_slot[4]["width"]
            and by_slot[4]["width"] * 2 == by_slot[3]["width"]):
        return {3: "Diffuse"}, 2
    face_slot, specificity = _face_atlas_evidence(by_slot)
    if face_slot is not None:
        return {face_slot: "Diffuse"}, specificity
    unlit_slot = _unlit_atlas_slot(by_slot)
    if unlit_slot is not None:
        return {unlit_slot: "Diffuse"}, 2
    return {}, 0


def _unlit_atlas_slot(by_slot):
    """Recognize constant/aliased-mask prefixes and a repeated small color input."""
    def square(slot, minimum=128):
        t = by_slot.get(slot, {})
        return (t.get("format") == "BC7_UNORM_SRGB" and t.get("width", 0) >= minimum
                and t.get("width") == t.get("height"))

    if 0 in by_slot or not all(square(slot, 16 if slot >= 5 else 128) for slot in (3, 4, 5, 6)):
        return None
    small, repeat = by_slot[5], by_slot[6]
    if small["hash"] != repeat["hash"] or small["width"] != repeat["width"]:
        return None
    constant = by_slot.get(1, {})
    if (constant.get("format") == "B8G8R8A8_UNORM_SRGB"
            and constant.get("width") == constant.get("height") == 1 and square(2)):
        slot = 2
    elif (square(1) and square(2) and by_slot[2]["resource"] == by_slot[3]["resource"]
          and by_slot[2]["width"] == by_slot[3]["width"]):
        slot = 1
    else:
        return None
    atlas = by_slot[slot]["width"]
    for other in (3, 4, 5):
        ratio, remainder = divmod(atlas, by_slot[other]["width"])
        if remainder or ratio < 4 or ratio & (ratio - 1):
            return None
    return slot if small["width"] <= by_slot[3]["width"] <= by_slot[4]["width"] else None


def _color_lookup(texture):
    return (texture.get("format") == "B8G8R8A8_UNORM_SRGB"
            and texture.get("width", 0) >= 128 and 1 < texture.get("height", 0) < 128
            and texture["width"] >= 4 * texture["height"])


def _face_atlas_evidence(by_slot):
    """Match ordered mask/color and shared-data pairs across optional material inputs."""
    def square(slot, fmt):
        texture = by_slot.get(slot, {})
        return (texture.get("format") == fmt and texture.get("width", 0) >= 128
                and texture.get("width") == texture.get("height"))

    def scaled(large, small):
        ratio, remainder = divmod(large, small)
        return not remainder and ratio > 0 and not ratio & (ratio - 1)

    candidates = {}
    for slot, mask in by_slot.items():
        if not ((square(slot, "BC1_UNORM") or square(slot, "BC3_UNORM"))
                and square(slot + 1, "BC7_UNORM_SRGB")):
            continue
        atlas = by_slot[slot + 1]
        for data_slot, data in by_slot.items():
            if data_slot <= slot + 1 or not (
                square(data_slot, "B8G8R8A8_UNORM") and square(data_slot + 1, "B8G8R8A8_UNORM")
            ):
                continue
            alias = by_slot[data_slot + 1]
            if data["resource"] != alias["resource"] or data["width"] != alias["width"]:
                continue
            specificity = 0
            if (mask["width"] < data["width"] <= atlas["width"]
                    and scaled(data["width"], mask["width"]) and scaled(atlas["width"], data["width"])):
                specificity = 2
            # Equal-size mask/data maps need the trailing lookup and a double-size
            # color atlas. This full face family outranks color-led hair passes.
            elif (mask["width"] == data["width"] and atlas["width"] == 2 * data["width"]
                  and _color_lookup(by_slot.get(data_slot + 2, {}))):
                specificity = 4
            if specificity:
                candidates[slot + 1] = max(candidates.get(slot + 1, 0), specificity)
    return next(iter(candidates.items())) if len(candidates) == 1 else (None, 0)


def layout(header, raw, slot, role):
    from ..geometry import Element

    if role == "blend" and any(
            e.get("SemanticName") == "ATTRIBUTE" and e.get("SemanticIndex") in ("14", "15")
            and int(e.get("InputSlot", -1)) != slot for e in raw):
        raise Unsupported("ATTRIBUTE14/15 aliases must use the declared blend stream")
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

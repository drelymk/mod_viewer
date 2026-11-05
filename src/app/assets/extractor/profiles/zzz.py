"""Conservative ZZZ material signatures, derived from public SlotFix conventions.

No shader or character hashes are hardcoded. Color target + shader + texture format,
dimensions and complete neighboring bindings are required. Other roles stay unknown.
Reference: leotorrez/ZZMI-Package, SlotFix.ini and Matches.ini (see docs/asset-extraction.md).
"""


def material_roles(textures, color_target, shader):
    by_slot = {t["slot"]: t for t in textures}
    def fmt(slot):
        return by_slot.get(slot, {}).get("format", "")
    def square(slot, minimum=128):
        t = by_slot.get(slot, {})
        return t.get("width", 0) == t.get("height", -1) and t.get("width", 0) >= minimum
    roles = {}
    if color_target and shader and fmt(3) == "BC7_UNORM_SRGB" and square(3, 256):
        if fmt(4) == "R8G8B8A8_UNORM" and square(4):
            roles = {3: "Diffuse", 4: "NormalMap"}
            if fmt(5) in ("BC7_UNORM", "BC6H_UF16") and square(5, 256):
                roles[5] = "LightMap"
                if fmt(6) in ("BC7_UNORM", "BC6H_UF16") and square(6, 256):
                    roles[6] = "MaterialMap"
        elif fmt(4) == "R16G16B16A16_FLOAT" and square(4) and by_slot[4].get("width") in (128, 256):
            # The face shader's floating-point normal resource is not validated
            # for tangent-space sampling. A JPEG dump also loses float/alpha data.
            roles = {3: "Diffuse"}
    # Outline-only captured parts are still valid skinned geometry, but these bindings
    # are a different material signature from the main color pass.
    if not roles and shader and fmt(2) == "BC7_UNORM_SRGB" and square(2, 256) and fmt(4) == "R16_UNORM":
        if fmt(3) in ("BC7_UNORM", "BC6H_UF16") and square(3, 256):
            roles = {2: "Diffuse", 3: "LightMap"}
        elif (_material_constant(by_slot.get(0, {})) and fmt(3) == "R16_UNORM"
              and by_slot[3].get("resource") and by_slot[3]["resource"] == by_slot[4].get("resource")
              and all(not by_slot[slot].get("contaminated")
                      and by_slot[slot]["resource"].get("hash") == by_slot[slot].get("hash") for slot in (3, 4))
              and square(3, 1) and square(4, 1) and by_slot[3]["width"] == by_slot[4]["width"] <= 4
              and all(fmt(slot) == "BC6H_UF16" and square(slot, 256)
                      and by_slot[slot]["width"] == by_slot[2]["width"] for slot in (5, 6))):
            # The face outline uses one tiny depth resource twice instead of a light atlas.
            roles = {2: "Diffuse"}
    return roles


def _material_constant(texture):
    return (texture.get("format") in {"R8G8B8A8_UNORM_SRGB", "B8G8R8A8_UNORM_SRGB"}
            and 0 < texture.get("width", 0) == texture.get("height", 0) <= 4
            and not texture.get("contaminated")
            and (texture.get("resource") or {}).get("hash") == texture.get("hash"))


def material_evidence(textures, color_target, shader):
    """Prefer proven primary material inputs over later screen-resource passes."""
    roles = material_roles(textures, color_target, shader)
    if not roles:
        return {}, 0
    by_slot = {t["slot"]: t for t in textures}
    mask, diffuse = by_slot.get(2, {}), by_slot.get(3, {})
    primary = (color_target and roles.get(3) == "Diffuse" and _material_constant(by_slot.get(0, {}))
               and mask.get("format") == "BC1_UNORM"
               and not mask.get("contaminated")
               and (mask.get("resource") or {}).get("hash") == mask.get("hash")
               and 128 <= mask.get("width", 0) == mask.get("height", 0) <= diffuse["width"] / 4)
    return roles, 2 if primary else 1


def auxiliary(texture):
    return texture["format"] in ("R16_UNORM", "R16_FLOAT", "R16_TYPELESS", "R10G10B10A2_UNORM") or (
        texture.get("width", 0) <= 4 and texture.get("height", 0) <= 4 and texture.get("width", 0) > 0)

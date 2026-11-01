"""GIMI material signatures and fallback channel evidence.

GIMI's ORFix supports several slot layouts. Capture evidence, color output,
bound resource identity and neighboring texture formats are required together.
"""


def material_roles(textures, color_target, shader):
    if not color_target or not shader:
        return {}
    by_slot = {t["slot"]: t for t in textures
               if not t.get("contaminated") and (t.get("resource") or {}).get("hash") == t["hash"]}

    def image(slot, fmt):
        t = by_slot.get(slot, {})
        return (t.get("format") == fmt and t.get("width", 0) >= 128
                and t.get("width") == t.get("height"))

    color = "BC7_UNORM_SRGB"
    linear = "BC7_UNORM"
    if image(0, color) and image(1, linear):
        return {0: "Diffuse"}
    if image(1, color):
        constant = by_slot.get(0, {})
        if image(0, linear) or (constant.get("format") == "R8G8B8A8_UNORM_SRGB"
                               and constant.get("width") == constant.get("height") == 4):
            return {1: "Diffuse"}
    if image(0, linear) and image(1, linear) and image(2, color):
        return {2: "Diffuse"}
    return {}


def pixel_role(sample):
    """Distinguish packed GIMI light channels from centered normal XY data."""
    means = sample.get("active_rg_means", ())
    if (len(means) == 2 and all(0.35 <= value <= 0.65 for value in means)
            and sample["active_xy_valid"] >= 0.9
            and sample["active_xy_centered"] >= 0.7):
        return "NormalMap"
    if (sample.get("active_xy_centered", 1) <= 0.25
            and sample.get("active_red_extremes", 0) >= 0.55
            and sample.get("active_chroma", 0) >= 20
            and sample.get("active_gray_fraction", 1) < 0.5):
        return "LightMap"
    return None

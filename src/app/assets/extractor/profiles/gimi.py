"""GIMI material signatures and fallback channel evidence.

GIMI's ORFix supports several slot layouts. Capture evidence, color output,
bound resource identity and neighboring texture formats are required together.
"""


def _bound(textures):
    return {t["slot"]: t for t in textures
            if not t.get("contaminated") and (t.get("resource") or {}).get("hash") == t["hash"]}


def _layered_eye(by_slot):
    """Recognize a base iris with scaled reflection and separate effect layers."""
    def color(slot, width):
        t = by_slot.get(slot, {})
        return (t.get("format") == "BC7_UNORM_SRGB"
                and t.get("width") == t.get("height") == width)

    base = by_slot.get(1, {}).get("width", 0)
    first, last = (by_slot.get(slot, {}) for slot in (3, 9))
    return (128 <= base <= 4096 and color(1, base) and color(2, base * 2)
            and all(color(slot, base) for slot in (5, 7, 8)) and color(6, base / 2)
            and first.get("format") == last.get("format") == "R8G8B8A8_UNORM_SRGB"
            and 0 < first.get("width", 0) == first.get("height") <= 4
            and first.get("width") == last.get("width") == last.get("height")
            and first.get("resource", {}).get("address")
            and type(first["resource"].get("version")) is int and first["resource"]["version"] >= 0
            and first["resource"] == last.get("resource")
            and first.get("sha256") and first["sha256"] == last.get("sha256"))


def material_roles(textures, color_target, shader):
    if not color_target or not shader:
        return {}
    by_slot = _bound(textures)
    if _layered_eye(by_slot):
        return {1: "Diffuse"}

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


def material_evidence(textures, color_target, shader):
    """Rank complete layered eyes and primary triplets before inherited inputs."""
    roles = material_roles(textures, color_target, shader)
    if not roles:
        return {}, 0
    if _layered_eye(_bound(textures)):
        return roles, 2
    primary = [t for t in textures if t["slot"] <= 2]
    diffuse = next(t for t in primary if roles.get(t["slot"]) == "Diffuse")
    complete = len(primary) == 3 and all(
        t["format"] == ("BC7_UNORM_SRGB" if roles.get(t["slot"]) == "Diffuse" else "BC7_UNORM")
        and t["width"] >= diffuse["width"] / 4 and t["height"] >= diffuse["height"] / 4
        and (t.get("resource") or {}).get("hash") == t["hash"] and not t.get("contaminated")
        for t in primary)
    return roles, int(complete)


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

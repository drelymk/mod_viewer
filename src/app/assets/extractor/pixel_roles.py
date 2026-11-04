"""Extraction-only pixel fallback; reliable captured roles always take priority."""

import math
from collections import Counter

from core.textures import load_texture_image
from core.textures.classifier import classify_dds
from .profiles.gimi import pixel_role as gimi_pixel_role


def _edges(pixels, width):
    edges = []
    for i, pixel in enumerate(pixels):
        neighbors = []
        if i % width + 1 < width:
            neighbors.append(pixels[i + 1])
        if i + width < len(pixels):
            neighbors.append(pixels[i + width])
        edges.append(max((max(abs(a - b) for a, b in zip(pixel, other))
                          for other in neighbors), default=0) / 255)
    return edges


def _coherence(pixels, width):
    means = [sum(p[c] for p in pixels) / len(pixels) for c in range(3)]
    variance = sum(sum((p[c] - means[c])**2 for c in range(3)) for p in pixels) / len(pixels)
    differences = [sum((a - b)**2 for a, b in zip(pixels[i], pixels[j]))
                   for i in range(len(pixels)) for j in (i + 1, i + width)
                   if j < len(pixels) and (j != i + 1 or i % width + 1 < width)]
    return max(0.0, 1 - sum(differences) / (len(differences) * 2 * variance)) if variance else 0.0


def _active_channels(image):
    raw = image.convert("RGB").tobytes()
    # Unused atlas padding is not a normal vector or a material region.
    all_pixels = list(zip(raw[0::3], raw[1::3], raw[2::3]))
    width, height = image.size
    border = [p for i, p in enumerate(all_pixels)
              if i % width in (0, width - 1) or i // width in (0, height - 1)]
    padding = Counter(tuple(v // 16 for v in p) for p in border).most_common(1)[0][0]
    def matches_padding(pixel):
        return all(abs(value // 16 - base) <= 1 for value, base in zip(pixel, padding))
    colored_padding = (any(not 76 <= value * 16 + 8 <= 179 for value in padding[:2])
                       and sum(matches_padding(p) for p in border) >= 0.7 * len(border))
    pixels = [p for p in all_pixels if max(p) > 16 and not (colored_padding and matches_padding(p))]
    if len(pixels) < 64:
        return {}
    count = len(pixels)
    return dict(
        excluded_padding_fraction=1 - count / len(all_pixels),
        active_rg_means=[sum(p[c] for p in pixels) / (255 * count) for c in (0, 1)],
        active_xy_valid=sum((2*p[0]/255-1)**2 + (2*p[1]/255-1)**2 <= 1.02 for p in pixels) / count,
        active_xy_centered=sum(76 <= p[0] <= 179 and 76 <= p[1] <= 179 for p in pixels) / count,
        active_red_extremes=sum(p[0] < 26 or p[0] > 229 for p in pixels) / count,
        active_chroma=sum(max(p) - min(p) for p in pixels) / count,
        active_gray_fraction=sum(max(p) - min(p) < 8 for p in pixels) / count)


def _sample(inv, texture, context):
    cache = inv.__dict__.setdefault("pixel_role_cache", {})
    identity = texture["sha256"]
    if identity in cache:
        return cache[identity]
    context.tick("Inspecting unclassified texture pixels")
    cache[identity] = None
    source = inv.path(texture["file"])
    if inv.source is not None:
        source = source.read_bytes()
    image = load_texture_image(source, max_size=64, preserve_alpha=False,
                               source_name=texture["file"])
    if image is None:
        return None
    try:
        classification = classify_dds(source, decoded_image=image)
        active_channels = _active_channels(image)
        image = image.convert("RGB").resize((32, 32))
        raw = image.tobytes()
        pixels = list(zip(raw[0::3], raw[1::3], raw[2::3]))
        vectors = [tuple(2 * value / 255 - 1 for value in pixel) for pixel in pixels]
        rgb_normal = sum(z > 0.5 and abs(x*x + y*y + z*z - 1) < 0.12
                         for x, y, z in vectors) / len(vectors)
        means = [sum(p[i] for p in pixels) / (255 * len(pixels)) for i in (0, 1)]
        if not all(0.35 <= mean <= 0.65 for mean in means):
            rgb_normal = 0
        cache[identity] = dict(texture_class=classification.texture_class,
                               color_score=classification.color_score,
                               normal_score=max(classification.normal_score, rgb_normal),
                               spatial_coherence=_coherence(pixels, 32),
                               evidence=list(classification.evidence), edges=_edges(pixels, 32),
                               **active_channels)
    except (OSError, ValueError, NotImplementedError):
        pass
    return cache[identity]


def _layout_match(left, right):
    # Center the edge strengths so unrelated uniformly noisy maps do not match.
    left_mean, right_mean = sum(left) / len(left), sum(right) / len(right)
    left = [x - left_mean for x in left]
    right = [x - right_mean for x in right]
    denominator = math.sqrt(sum(x*x for x in left) * sum(x*x for x in right))
    return max(0.0, sum(a*b for a, b in zip(left, right)) / denominator) if denominator else 0.0


def _eligible(texture):
    fmt = texture.get("format", "")
    return (not texture.get("auxiliary") and not texture.get("contaminated")
            and "FLOAT" not in fmt and "TYPELESS" not in fmt and not fmt.startswith("BC6H")
            and texture.get("role_confidence") == "unknown"
            and texture.get("role", "").startswith("TextureSlot")
            and 128 <= texture.get("width", 0) <= 4096
            and 128 <= texture.get("height", 0) <= 4096)


def _bound(texture):
    return (texture.get("resource") or {}).get("hash") == texture["hash"]


def apply_fallback(inv, textures, color_target, context):
    """Analyze unknown maps without changing confirmed roles or inventing mask semantics."""
    confirmed = {t["sha256"] for t in textures
                 if not t["role"].startswith("TextureSlot")}
    unknown = [t for t in textures if _eligible(t) and t["sha256"] not in confirmed]
    if not unknown or not color_target:
        return
    samples = {t["file"]: _sample(inv, t, context) for t in unknown}
    samples = {name: sample for name, sample in samples.items() if sample is not None}
    for t in unknown:
        sample = samples.get(t["file"])
        if sample:
            t["pixel_analysis"] = {key: value for key, value in sample.items() if key != "edges"}
    diffuse = [t for t in textures if t["role"] == "Diffuse" and not t.get("auxiliary")]
    if not diffuse:
        colors = [t for t in unknown if t["file"] in samples
                  and samples[t["file"]]["texture_class"] == "color"
                  and samples[t["file"]]["color_score"] >= 0.75
                  and samples[t["file"]]["spatial_coherence"] >= 0.45 and _bound(t)]
        other_scores = sorted((samples[t["file"]]["color_score"] for t in unknown
                               if t["file"] in samples and _bound(t)), reverse=True)
        separated = len(other_scores) < 2 or other_scores[0] - other_scores[1] >= 0.15
        if len(colors) == 1 and separated:
            selected = colors[0]
            selected.update(role="Diffuse", role_confidence="inferred_pixels_high",
                            role_note="Diffuse inferred from unique strong color-pixel evidence in the selected material pass.")
            diffuse = colors
        else:
            for t in colors:
                t["pixel_analysis"]["suggested_roles"] = ["Diffuse"]
    if len(diffuse) != 1:
        return
    reference = _sample(inv, diffuse[0], context)
    if reference is None:
        return
    normal_candidates = []
    light_candidates = []
    gimi = inv.gimi_evidence and not inv.zzz_evidence
    for t in unknown:
        if t["role"] == "Diffuse" or t["file"] not in samples:
            continue
        sample = samples[t["file"]]
        match = _layout_match(reference["edges"], sample["edges"])
        evidence = t["pixel_analysis"]
        evidence["diffuse_layout_match"] = round(match, 4)
        linear = not t["format"].endswith("_SRGB") and sample["color_score"] == 0
        gimi_role = gimi_pixel_role(sample) if gimi and linear else None
        if gimi_role:
            evidence["suggested_roles"] = [gimi_role]
            # The primary material family supplies association even when a face
            # SDF or sparse atlas has few boundaries in common with the diffuse.
            primary = t["slot"] in (0, 1, 2) and (
                t["width"] >= diffuse[0]["width"] / 4
                and t["height"] >= diffuse[0]["height"] / 4)
            if primary and _bound(t) and not any(v["role"] == gimi_role for v in textures):
                evidence["game_profile"] = "gimi_packed_channels"
                (normal_candidates if gimi_role == "NormalMap" else light_candidates).append(t)
        elif linear and sample["normal_score"] >= 0.95:
            evidence["suggested_roles"] = ["NormalMap"]
            if match >= 0.8 and _bound(t) and not any(v["role"] == "NormalMap" for v in textures):
                normal_candidates.append(t)
        elif linear and match >= 0.4 and sample["texture_class"] in {"packed_mask", "packed_data", "single_channel_mask", "unknown"}:
            evidence["suggested_roles"] = ["LightMap"] if gimi else ["LightMap", "MaterialMap"]
            evidence["confidence"] = "ambiguous"
    if len(normal_candidates) == 1:
        normal_candidates[0].update(
            role="NormalMap", role_confidence="inferred_pixels_high",
            role_note="Normal map inferred from vector-like pixels and material association; captured roles retain priority.")
    if len(light_candidates) == 1:
        light_candidates[0].update(
            role="LightMap", role_confidence="inferred_pixels_high",
            role_note="Light map inferred from packed GIMI channels in the primary material family; captured roles retain priority.")

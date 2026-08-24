"""Game-agnostic structural analysis for DDS texture candidates."""

from dataclasses import dataclass
import math
import os

from .dds import inspect_dds
from .textures import load_texture_image


@dataclass(frozen=True, slots=True)
class DDSClassification:
    """Structural evidence available without mod or game naming schemes.

    ``role`` remains as a compatibility field for genuinely strong normal-map
    evidence and older callers. Color analysis intentionally does not assign
    ``diffuse``; a contextual resolver must combine it with association
    evidence before choosing that semantic role.
    """

    role: str | None
    texture_class: str
    confidence: str
    evidence: tuple[str, ...] = ()
    color_score: float = 0.0
    normal_score: float = 0.0
    mask_score: float = 0.0
    data_score: float = 0.0


def is_color_candidate(classification):
    """Return whether analysis supports using a file as a color candidate."""
    if classification.role == "normal_map":
        return False
    if classification.role == "diffuse":
        # Compatibility with older test doubles/callers. Production analysis
        # no longer emits this role for color images.
        return classification.confidence in {"medium", "high"}
    if classification.texture_class not in {"color", "effect"}:
        return False
    # A zero score denotes a legacy four-field DDSClassification constructed
    # by an adapter. Its class/confidence fields remain authoritative.
    if classification.color_score == 0:
        return classification.confidence in {"medium", "high"}
    return classification.color_score >= 0.35


def classification_cache_key(path):
    """Return a filesystem identity suitable for a per-load classification cache."""
    try:
        stat = os.stat(path)
        return (os.path.normcase(os.path.abspath(path)), stat.st_size,
                stat.st_mtime_ns)
    except (OSError, TypeError, ValueError):
        return None


def _unknown(texture_class="unknown", *evidence):
    return DDSClassification(None, texture_class, "low", tuple(evidence))


def _color_score(stats, is_srgb):
    if not is_srgb:
        return 0.0
    deviations = stats["deviations"]
    score = 0.35
    score += min(min(deviations) / 64.0, 1.0) * 0.20
    score += min(stats["chroma"] / 100.0, 1.0) * 0.15
    score += min(stats["color_entropy"] / 5.0, 1.0) * 0.15
    score += min(stats["spatial_detail"] / 40.0, 1.0) * 0.10
    score -= max(0.0, stats["gray_fraction"] - 0.40) * 0.30
    if stats["dominant_channel_fraction"] >= 0.90:
        score -= 0.35
    return max(0.0, min(1.0, score))


def _score_confidence(score):
    if score >= 0.70:
        return "high"
    if score >= 0.35:
        return "medium"
    return "low"


def _channel_stats(image):
    raw = image.convert("RGB").tobytes()
    pixels = list(zip(raw[0::3], raw[1::3], raw[2::3]))
    if not pixels:
        return None
    channels = tuple(tuple(pixel[index] for pixel in pixels)
                     for index in range(3))
    means = tuple(sum(channel) / len(channel) for channel in channels)
    deviations = tuple(
        math.sqrt(sum((value - mean) ** 2 for value in channel) /
                   len(channel))
        for channel, mean in zip(channels, means))
    chroma = sum(max(pixel) - min(pixel) for pixel in pixels) / len(pixels)
    black_fraction = sum(max(pixel) <= 16 for pixel in pixels) / len(pixels)
    non_black_pixels = [pixel for pixel in pixels if max(pixel) > 16]
    dominant_counts = [0, 0, 0]
    for pixel in non_black_pixels:
        dominant_counts[max(range(3), key=pixel.__getitem__)] += 1
    dominant_channel_fraction = (
        max(dominant_counts) / len(non_black_pixels)
        if non_black_pixels else 1.0)
    gray_fraction = sum(
        max(pixel) - min(pixel) <= 8 for pixel in pixels) / len(pixels)
    quantized = {
        (pixel[0] // 16, pixel[1] // 16, pixel[2] // 16)
        for pixel in pixels
    }
    histogram = {}
    for pixel in pixels:
        key = (pixel[0] // 16, pixel[1] // 16, pixel[2] // 16)
        histogram[key] = histogram.get(key, 0) + 1
    color_entropy = 0.0
    for count in histogram.values():
        probability = count / len(pixels)
        color_entropy -= probability * math.log2(probability)

    width, height = image.size
    adjacent_differences = []
    for y in range(height):
        for x in range(width):
            pixel = pixels[y * width + x]
            if x + 1 < width:
                neighbor = pixels[y * width + x + 1]
                adjacent_differences.append(
                    sum(abs(pixel[index] - neighbor[index])
                        for index in range(3)) / 3)
            if y + 1 < height:
                neighbor = pixels[(y + 1) * width + x]
                adjacent_differences.append(
                    sum(abs(pixel[index] - neighbor[index])
                        for index in range(3)) / 3)

    normalized = [tuple(channel / 255.0 for channel in pixel)
                  for pixel in pixels]
    valid_xy_fraction = sum(
        (2 * red - 1) ** 2 + (2 * green - 1) ** 2 <= 1.0
        for red, green, _blue in normalized) / len(normalized)
    blue_low_fraction = sum(
        blue <= 0.05 for _red, _green, blue in normalized) / len(normalized)
    return {
        "means": means,
        "deviations": deviations,
        "chroma": chroma,
        "black_fraction": black_fraction,
        "dominant_channel_fraction": dominant_channel_fraction,
        "gray_fraction": gray_fraction,
        "quantized_occupancy": len(quantized),
        "color_entropy": color_entropy,
        "spatial_detail": (
            sum(adjacent_differences) / len(adjacent_differences)
            if adjacent_differences else 0.0),
        "valid_xy_fraction": valid_xy_fraction,
        "blue_low_fraction": blue_low_fraction,
    }


def _decoded_classification(info, image):
    stats = _channel_stats(image)
    if stats is None:
        return _unknown("unknown", "empty_image")
    means = stats["means"]
    deviations = stats["deviations"]
    chroma = stats["chroma"]
    if max(info.width, info.height) <= 4:
        return _unknown("lookup", "tiny_dimensions")
    if (sum(deviations) < 9 and chroma < 8
            or stats["black_fraction"] >= 0.75):
        return DDSClassification(
            None, "packed_mask", "low",
            ("low_variation", "low_chroma"), mask_score=0.85)

    # A packed XY normal is centered around 0.5 in R/G and has an almost
    # empty blue channel.  The unit-circle test rejects arbitrary low-blue
    # packed data; variation is intentionally not required because a valid
    # normal can be flat or only gently varying.
    normal_layout = (
        info.format in {"bc7_unorm", "rgba8", "bgra8"}
        and means[2] / 255.0 <= 0.03
        and deviations[2] / 255.0 <= 0.02
        and 0.35 <= means[0] / 255.0 <= 0.65
        and 0.35 <= means[1] / 255.0 <= 0.65
        and stats["valid_xy_fraction"] >= 0.80
        and stats["blue_low_fraction"] >= 0.98
    )
    if normal_layout:
        return DDSClassification(
            "normal_map", "packed_normal", "high",
            (f"format:{info.format}", "centered_xy", "low_blue",
             "valid_xy"), normal_score=0.95)

    if (info.format in {"bc7_unorm", "rgba8", "bgra8"}
            and means[2] / 255.0 < 0.15 and chroma > 20):
        return DDSClassification(
            None, "packed_data", "medium",
            (f"format:{info.format}", "linear_channels",
             "invalid_normal_layout"), data_score=0.85)

    is_srgb = info.format.endswith("_srgb")
    if is_srgb and info.width >= 2 and info.height >= 2:
        color_score = _color_score(stats, is_srgb)
        texture_class = "color" if color_score >= 0.55 else "effect"
        return DDSClassification(
            None, texture_class, _score_confidence(color_score),
            (f"format:{info.format}", "srgb_color_space",
             "pixel_color_evidence"), color_score=color_score)

    if chroma < 12:
        return _unknown("packed_mask", f"format:{info.format}",
                        "low_chroma")
    return _unknown("unknown", f"format:{info.format}",
                    "unclassified_pixel_evidence")


def classify_dds(path):
    """Classify a DDS using only its validated header and decoded pixels."""
    info = inspect_dds(path)
    if info is None:
        return _unknown()
    if info.format in {"bc5_unorm", "bc5_snorm"}:
        return DDSClassification(
            "normal_map", "packed_normal", "high",
            (f"format:{info.format}", "two_channel_block_format"),
            normal_score=1.0)
    if info.format in {"bc4_unorm", "bc4_snorm"}:
        return DDSClassification(
            None, "single_channel_mask", "high",
            (f"format:{info.format}",), mask_score=0.95)
    if max(info.width, info.height) <= 4:
        return _unknown("lookup", "tiny_dimensions")
    if info.format in {"bc6h_ufloat", "bc6h_float"}:
        return DDSClassification(
            None, "effect", "high", (f"format:{info.format}",),
            data_score=0.75)

    image = load_texture_image(path, max_size=128, preserve_alpha=True)
    if image is None:
        # sRGB is useful structural evidence, but without pixels it is not
        # strong enough to assign a role.
        return _unknown("color" if info.format.endswith("_srgb") else "unknown",
                        f"format:{info.format}", "decode_unavailable")
    return _decoded_classification(info, image)

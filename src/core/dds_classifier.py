"""Game-agnostic, conservative semantic hints for DDS replacement files."""

from dataclasses import dataclass
import math
import os

from .dds import inspect_dds
from .textures import load_texture_image


@dataclass(frozen=True, slots=True)
class DDSClassification:
    """The evidence available without relying on a mod or game naming scheme."""

    role: str | None
    texture_class: str
    confidence: str
    evidence: tuple[str, ...] = ()


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
    return means, deviations, chroma


def _decoded_classification(info, image):
    stats = _channel_stats(image)
    if stats is None:
        return _unknown("unknown", "empty_image")
    means, deviations, chroma = stats
    variation = sum(deviations)
    if max(info.width, info.height) <= 4:
        return _unknown("lookup", "tiny_dimensions")
    if variation < 9 and chroma < 8:
        return _unknown("packed_mask", "low_variation", "low_chroma")

    # Linear two-channel-style data is the common packed normal layout. The
    # blue channel is deliberately required to be low so arbitrary masks and
    # colorful material maps are not promoted to a render role.
    if (info.format in {"bc7_unorm", "rgba8", "bgra8"}
            and means[2] < 112 and deviations[0] > 16
            and deviations[1] > 16 and chroma > 20):
        return DDSClassification(
            "normal_map", "packed_normal", "high",
            (f"format:{info.format}", "linear_channels", "low_blue"))

    is_srgb = info.format.endswith("_srgb")
    if is_srgb and info.width >= 2 and info.height >= 2:
        if chroma >= 12 and variation >= 18:
            return DDSClassification(
                "diffuse", "color", "high",
                (f"format:{info.format}", "chroma_variation"))
        return _unknown("packed_mask", f"format:{info.format}",
                        "weak_color_evidence")

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
            (f"format:{info.format}", "two_channel_block_format"))
    if info.format in {"bc4_unorm", "bc4_snorm"}:
        return _unknown("single_channel_mask", f"format:{info.format}")
    if max(info.width, info.height) <= 4:
        return _unknown("lookup", "tiny_dimensions")
    if info.format in {"bc6h_ufloat", "bc6h_float"}:
        return _unknown("effect", f"format:{info.format}")

    image = load_texture_image(path, max_size=128, preserve_alpha=True)
    if image is None:
        # sRGB is useful structural evidence, but without pixels it is not
        # strong enough to assign a role.
        return _unknown("color" if info.format.endswith("_srgb") else "unknown",
                        f"format:{info.format}", "decode_unavailable")
    return _decoded_classification(info, image)

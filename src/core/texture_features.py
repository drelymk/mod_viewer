"""Game-agnostic texture measurements for offline corpus experiments.

This module deliberately does not assign a semantic texture role.  It only
extracts repeatable measurements from a validated DDS header and a bounded,
decoded preview.  Classifier output belongs to the corpus report as diagnostic
evidence and is never used as a label here.
"""

from __future__ import annotations

import math
from statistics import fmean

from .dds import inspect_dds
from .textures import load_texture_image


FEATURE_SCHEMA_VERSION = "wuwa-texture-features-v1.1"
FEATURE_IMAGE_SIZE = 128


def _percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _mean(values):
    return fmean(values) if values else None


def _std(values, mean=None):
    if not values:
        return None
    mean = _mean(values) if mean is None else mean
    return math.sqrt(fmean((value - mean) ** 2 for value in values))


def _correlation(first, second):
    if not first or not second or len(first) != len(second):
        return None
    first_mean = _mean(first)
    second_mean = _mean(second)
    first_dev = _std(first, first_mean)
    second_dev = _std(second, second_mean)
    if not first_dev or not second_dev:
        return 0.0
    covariance = fmean(
        (left - first_mean) * (right - second_mean)
        for left, right in zip(first, second))
    return covariance / (first_dev * second_dev)


def _entropy(histogram, total):
    if not total:
        return None
    return -sum(
        (count / total) * math.log2(count / total)
        for count in histogram.values() if count)


def _pixel_features(image):
    rgba = image.convert("RGBA")
    width, height = rgba.size
    pixels = list(rgba.getdata())
    if not pixels:
        return {"decode_status": "empty_image"}

    channels = [
        [pixel[index] / 255.0 for pixel in pixels]
        for index in range(4)]
    red, green, blue, alpha = channels
    rgb = list(zip(red, green, blue))
    chroma = [max(pixel) - min(pixel) for pixel in rgb]
    saturation = [
        (max(pixel) - min(pixel)) / max(pixel) if max(pixel) else 0.0
        for pixel in rgb]
    neutral = [value <= 8.0 / 255.0 for value in chroma]
    dark = [max(pixel) <= 16.0 / 255.0 for pixel in rgb]
    bright = [min(pixel) >= 240.0 / 255.0 for pixel in rgb]

    result = {"decode_status": "decoded", "image_width": width,
              "image_height": height}
    for name, values in zip(("r", "g", "b", "a"), channels):
        result[f"{name}_mean"] = _mean(values)
        result[f"{name}_std"] = _std(values)
        for label, fraction in (("p05", 0.05), ("p25", 0.25),
                                ("p50", 0.50), ("p75", 0.75),
                                ("p95", 0.95)):
            result[f"{name}_{label}"] = _percentile(values, fraction)

    result.update({
        "rgb_corr_rg": _correlation(red, green),
        "rgb_corr_rb": _correlation(red, blue),
        "rgb_corr_gb": _correlation(green, blue),
        "chroma_mean": _mean(chroma),
        "chroma_std": _std(chroma),
        "chroma_p05": _percentile(chroma, 0.05),
        "chroma_p50": _percentile(chroma, 0.50),
        "chroma_p95": _percentile(chroma, 0.95),
        "saturation_mean": _mean(saturation),
        "saturation_std": _std(saturation),
        "neutral_fraction": sum(neutral) / len(pixels),
        "dark_fraction": sum(dark) / len(pixels),
        "bright_fraction": sum(bright) / len(pixels),
    })

    dominant_counts = [0, 0, 0]
    dominant_margins = []
    for pixel in rgb:
        ordered = sorted(pixel, reverse=True)
        dominant_margins.append(ordered[0] - ordered[1])
        dominant_counts[max(range(3), key=pixel.__getitem__)] += 1
    for name, count in zip(("r", "g", "b"), dominant_counts):
        result[f"dominant_{name}_fraction"] = count / len(pixels)
    result.update({
        "dominant_margin_mean": _mean(dominant_margins),
        "dominant_margin_std": _std(dominant_margins),
        "dominant_margin_p10": _percentile(dominant_margins, 0.10),
        "dominant_margin_p50": _percentile(dominant_margins, 0.50),
        "dominant_margin_p90": _percentile(dominant_margins, 0.90),
    })

    quantized = {}
    for pixel in rgb:
        key = tuple(min(15, int(value * 16)) for value in pixel)
        quantized[key] = quantized.get(key, 0) + 1
    result["quantized_color_occupancy"] = len(quantized) / 4096.0
    result["quantized_color_entropy"] = _entropy(quantized, len(pixels))

    gray = [fmean(pixel) for pixel in rgb]
    horizontal = []
    vertical = []
    edge_count = 0
    edge_threshold = 20.0 / 255.0
    for y in range(height):
        for x in range(width):
            index = y * width + x
            if x + 1 < width:
                delta = abs(gray[index] - gray[index + 1])
                horizontal.append(delta)
                edge_count += delta >= edge_threshold
            if y + 1 < height:
                delta = abs(gray[index] - gray[index + width])
                vertical.append(delta)
                edge_count += delta >= edge_threshold
    result.update({
        "gradient_h_mean": _mean(horizontal),
        "gradient_h_std": _std(horizontal),
        "gradient_v_mean": _mean(vertical),
        "gradient_v_std": _std(vertical),
        "edge_density": edge_count / max(1, len(horizontal) + len(vertical)),
    })

    # Four-by-four block means provide a small spatial/layout signal while
    # remaining stable for large source textures after downsampling.
    block_means = []
    for block_y in range(4):
        y0 = block_y * height // 4
        y1 = max(y0 + 1, (block_y + 1) * height // 4)
        for block_x in range(4):
            x0 = block_x * width // 4
            x1 = max(x0 + 1, (block_x + 1) * width // 4)
            block = [gray[y * width + x]
                     for y in range(y0, min(y1, height))
                     for x in range(x0, min(x1, width))]
            block_means.append(_mean(block))
    block_mean = _mean(block_means)
    result.update({
        "block_mean_std": _std(block_means, block_mean),
        "block_mean_min": min(block_means),
        "block_mean_max": max(block_means),
        "spatial_contrast": max(block_means) - min(block_means),
    })

    normal_xy_radius = [
        math.sqrt((2 * red_value - 1.0) ** 2
                  + (2 * green_value - 1.0) ** 2)
        for red_value, green_value in zip(red, green)]
    result.update({
        "normal_xy_valid_fraction": sum(value <= 1.0
                                         for value in normal_xy_radius)
        / len(pixels),
        "normal_xy_radius_mean": _mean(normal_xy_radius),
        "normal_xy_radius_std": _std(normal_xy_radius),
        "normal_rg_center_error": math.sqrt(
            (fmean(red) - 0.5) ** 2 + (fmean(green) - 0.5) ** 2),
        "normal_blue_low_fraction": sum(value <= 0.05 for value in blue)
        / len(pixels),
        "normal_blue_mean": _mean(blue),
        "normal_blue_std": _std(blue),
        "alpha_transparent_fraction": sum(value <= 1.0 / 255.0
                                           for value in alpha) / len(pixels),
        "alpha_nearly_transparent_fraction": sum(value < 0.10
                                                  for value in alpha)
        / len(pixels),
        "alpha_partial_fraction": sum(0.10 <= value < 0.99
                                       for value in alpha) / len(pixels),
        "alpha_opaque_fraction": sum(value >= 0.99 for value in alpha)
        / len(pixels),
    })
    alpha_histogram = {}
    for value in alpha:
        bucket = min(15, int(value * 16))
        alpha_histogram[bucket] = alpha_histogram.get(bucket, 0) + 1
    result["alpha_entropy"] = _entropy(alpha_histogram, len(alpha))
    return result


def extract_texture_features(path, *, dds_info=None, image=None,
                              baseline=None, max_size=FEATURE_IMAGE_SIZE,
                              decode=True):
    """Return deterministic DDS and generic pixel features for ``path``.

    ``baseline`` is an optional diagnostic object, for example the current
    structural classifier result.  Its fields are explicitly prefixed with
    ``baseline_`` so callers can exclude them from model input.
    """
    info = dds_info if dds_info is not None else inspect_dds(path)
    result = {
        "feature_version": FEATURE_SCHEMA_VERSION,
        "dds_valid": bool(info),
        "dds_format": info.format if info else None,
        "dds_width": info.width if info else None,
        "dds_height": info.height if info else None,
        "dds_mip_count": info.mip_count if info else None,
        "dds_compressed": info.compressed if info else None,
        "dds_requires_bc": info.requires_bc if info else None,
        "dds_aspect_ratio": (info.width / info.height) if info else None,
        "dds_pixel_count": (info.width * info.height) if info else None,
        "dds_log_pixel_count": (math.log2(info.width * info.height)
                                if info else None),
    }
    if info:
        format_name = info.format.lower()
        result["format_is_srgb"] = format_name.endswith("_srgb")
        for family in ("bc1", "bc2", "bc3", "bc4", "bc5", "bc6h",
                       "bc7", "rgba8", "bgra8"):
            result[f"format_{family}"] = format_name.startswith(family)

    if image is None and info and decode:
        image = load_texture_image(path, max_size=max_size,
                                   preserve_alpha=True)
    if image is None:
        result["decode_status"] = "invalid_dds" if not info else "unavailable"
    else:
        result.update(_pixel_features(image))

    if baseline is not None:
        result.update({
            "baseline_role": getattr(baseline, "role", None),
            "baseline_texture_class": getattr(baseline, "texture_class", None),
            "baseline_confidence": getattr(baseline, "confidence", None),
            "baseline_color_score": getattr(baseline, "color_score", None),
            "baseline_normal_score": getattr(baseline, "normal_score", None),
            "baseline_mask_score": getattr(baseline, "mask_score", None),
            "baseline_data_score": getattr(baseline, "data_score", None),
            "baseline_evaluation_only": True,
        })
    return result


def model_feature_columns(columns):
    """Return columns safe to pass to a later model trainer."""
    excluded = {
        "sha256", "file_size", "example_mod_id", "example_relative_file",
        "occurrence_count", "feature_version", "decode_status",
    }
    return sorted(
        column for column in columns
        if not column.startswith("baseline_")
        and column not in excluded)

"""Shared color-adjustment validation and the CPU equivalent of the viewer shader."""

from __future__ import annotations

import math
import re


COLOR_DEFAULTS = {
    "hue": 0,
    "saturation": 1.0,
    "brightness": 1.0,
    "contrast": 1.0,
    "red": 1.0,
    "green": 1.0,
    "blue": 1.0,
    "tint": "#ffffff",
    "tint_strength": 0.0,
}

COLOR_RANGES = {
    "hue": (-180.0, 180.0),
    "saturation": (0.0, 2.0),
    "brightness": (0.0, 2.0),
    "contrast": (0.0, 2.0),
    "red": (0.0, 2.0),
    "green": (0.0, 2.0),
    "blue": (0.0, 2.0),
    "tint_strength": (0.0, 1.0),
}

_TINT_PATTERN = re.compile(r"^#[0-9a-f]{6}$", re.IGNORECASE)


def _number(value, default, *, reject_invalid):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        if reject_invalid:
            return None
        return default
    if not math.isfinite(value):
        if reject_invalid:
            return None
        return default
    return float(value)


def normalize_color_adjustment(value, *, reject_invalid=False):
    """Return the canonical color state, or ``None`` for malformed input.

    Missing fields deliberately use the neutral defaults.  This keeps the
    persistence format sparse while allowing the bake endpoint to validate a
    complete state with the same ranges used by the metadata layer.
    """
    if not isinstance(value, dict):
        return None
    result = {}
    for field, default in COLOR_DEFAULTS.items():
        raw = value.get(field, value.get("tintStrength", default)
                        if field == "tint_strength" else default)
        if field == "tint":
            if not isinstance(raw, str) or not _TINT_PATTERN.fullmatch(raw):
                if reject_invalid:
                    return None
                raw = default
            result[field] = raw.lower()
            continue
        number = _number(raw, default, reject_invalid=reject_invalid)
        if number is None:
            return None
        minimum, maximum = COLOR_RANGES[field]
        result[field] = min(maximum, max(minimum, number))
    result["hue"] = (int(result["hue"])
                      if result["hue"].is_integer() else result["hue"])
    return result


def is_neutral_color_adjustment(value):
    """Return whether *value* is the normalized neutral adjustment."""
    normalized = normalize_color_adjustment(value)
    return normalized == COLOR_DEFAULTS


def tint_rgb(value):
    """Decode a canonical ``#rrggbb`` value into raw sRGB floats."""
    normalized = value.lower() if isinstance(value, str) else COLOR_DEFAULTS["tint"]
    if not _TINT_PATTERN.fullmatch(normalized):
        normalized = COLOR_DEFAULTS["tint"]
    return tuple(int(normalized[index:index + 2], 16) / 255.0
                 for index in (1, 3, 5))


def _rgb_to_hsv(red, green, blue):
    maximum = max(red, green, blue)
    minimum = min(red, green, blue)
    delta = maximum - minimum
    safe_delta = max(delta, 0.000001)
    if maximum == red:
        hue = (green - blue) / safe_delta
        if hue < 0:
            hue += 6.0
    elif maximum == green:
        hue = (blue - red) / safe_delta + 2.0
    else:
        hue = (red - green) / safe_delta + 4.0
    return hue / 6.0, (0.0 if maximum == 0 else delta / maximum), maximum


def _hsv_to_rgb(hue, saturation, value):
    sector_value = hue * 6.0
    sector = math.floor(sector_value)
    fraction = sector_value - sector
    p = value * (1.0 - saturation)
    q = value * (1.0 - saturation * fraction)
    t = value * (1.0 - saturation * (1.0 - fraction))
    if sector == 0:
        return value, t, p
    if sector == 1:
        return q, value, p
    if sector == 2:
        return p, value, t
    if sector == 3:
        return p, q, value
    if sector == 4:
        return t, p, value
    return value, p, q


def _apply_normalized(rgb, normalized):
    """Apply the operation order to RGB floats and a validated state."""
    try:
        red, green, blue = (float(channel) for channel in rgb)
    except (TypeError, ValueError):
        raise ValueError("RGB must contain three numeric channels") from None
    hue, saturation, value = _rgb_to_hsv(red, green, blue)
    hue = (hue + normalized["hue"] / 360.0) % 1.0
    saturation = min(1.0, max(0.0, saturation * normalized["saturation"]))
    value = min(1.0, max(0.0, value * normalized["brightness"]))
    red, green, blue = _hsv_to_rgb(hue, saturation, value)
    red = (red - 0.5) * normalized["contrast"] + 0.5
    green = (green - 0.5) * normalized["contrast"] + 0.5
    blue = (blue - 0.5) * normalized["contrast"] + 0.5
    red *= normalized["red"]
    green *= normalized["green"]
    blue *= normalized["blue"]
    red = min(1.0, max(0.0, red))
    green = min(1.0, max(0.0, green))
    blue = min(1.0, max(0.0, blue))
    tint_red, tint_green, tint_blue = tint_rgb(normalized["tint"])
    strength = normalized["tint_strength"]
    red = red * (1.0 - strength) + tint_red * strength
    green = green * (1.0 - strength) + tint_green * strength
    blue = blue * (1.0 - strength) + tint_blue * strength
    return tuple(min(1.0, max(0.0, channel))
                 for channel in (red, green, blue))


def apply_color_adjustment(rgb, adjustment):
    """Apply the viewer's operation order to raw sRGB RGB floats.

    The input and output are editor-sRGB values.  No color-space conversion is
    performed here: the GPU graph converts its sampled linear value into this
    same editor space before applying these operations.
    """
    normalized = normalize_color_adjustment(adjustment, reject_invalid=True)
    if normalized is None:
        raise ValueError("invalid color adjustment")
    return _apply_normalized(rgb, normalized)


def adjust_rgba_bytes(data, width, height, adjustment, pixel_mask=None):
    """Return adjusted RGBA bytes, preserving every source alpha byte.

    When *pixel_mask* is supplied it must contain one truthy entry per pixel;
    only selected pixels receive the RGB operation.  A full adjusted candidate
    is still computed for each source pixel before the mask is composited, which
    mirrors the bake pipeline's image-to-encoder boundary.
    """
    normalized = normalize_color_adjustment(adjustment, reject_invalid=True)
    if normalized is None:
        raise ValueError("invalid color adjustment")
    try:
        width, height = int(width), int(height)
    except (TypeError, ValueError):
        raise ValueError("image dimensions are invalid") from None
    if width <= 0 or height <= 0 or len(data) != width * height * 4:
        raise ValueError("RGBA data has the wrong size")
    if pixel_mask is not None and len(pixel_mask) != width * height:
        raise ValueError("pixel mask has the wrong size")
    result = bytearray(len(data))
    for index in range(0, len(data), 4):
        red, green, blue = (data[index + channel] / 255.0
                            for channel in range(3))
        adjusted = _apply_normalized((red, green, blue), normalized)
        pixel_index = index // 4
        source = adjusted if pixel_mask is None or pixel_mask[pixel_index] else (
            red, green, blue)
        result[index:index + 3] = bytes(
            min(255, max(0, round(channel * 255.0))) for channel in source)
        result[index + 3] = data[index + 3]
    return bytes(result)


__all__ = [
    "COLOR_DEFAULTS", "COLOR_RANGES", "adjust_rgba_bytes",
    "apply_color_adjustment", "is_neutral_color_adjustment",
    "normalize_color_adjustment", "tint_rgb",
]

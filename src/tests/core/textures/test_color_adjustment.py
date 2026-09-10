"""CPU color-bake math and alpha/mask invariants."""

import json
from pathlib import Path

import pytest

from core.textures.color_adjustment import (
    COLOR_DEFAULTS,
    apply_prepared_color_adjustment, apply_prepared_color_u8,
    is_neutral_color_adjustment, normalize_color_adjustment,
    prepare_color_adjustment,
)


@pytest.fixture
def vectors():
    path = Path(__file__).parents[2] / "fixtures" / "color_adjustment_vectors.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_normalization_accepts_frontend_tint_name_and_rejects_bad_values():
    assert normalize_color_adjustment({
        "tint": "#AABBCC", "tintStrength": 0.25,
    })["tint"] == "#aabbcc"
    assert normalize_color_adjustment({
        "tint": "#ffffff", "tint_strength": 0,
    })["tint"] is None
    assert normalize_color_adjustment({"hue": True}, reject_invalid=True) is None
    assert normalize_color_adjustment({"tint": "blue"}, reject_invalid=True) is None


def test_brightness_range_allows_light_recolors_and_clamps_above_four():
    assert normalize_color_adjustment({"brightness": 0})["brightness"] == 0
    assert normalize_color_adjustment({"brightness": 1})["brightness"] == 1
    assert normalize_color_adjustment({"brightness": 2})["brightness"] == 2
    assert normalize_color_adjustment({"brightness": 3})["brightness"] == 3
    assert normalize_color_adjustment({"brightness": 4})["brightness"] == 4
    assert normalize_color_adjustment({"brightness": 5})["brightness"] == 4
    assert normalize_color_adjustment(
        {"brightness": True}, reject_invalid=True) is None


def test_neutral_state_is_identity():
    assert is_neutral_color_adjustment(COLOR_DEFAULTS)
    assert _apply((0.23, 0.45, 0.91), COLOR_DEFAULTS) == \
        pytest.approx((0.23, 0.45, 0.91))


def _apply(rgb, adjustment):
    return apply_prepared_color_adjustment(
        rgb, prepare_color_adjustment(adjustment))


def test_color_entry_points_match_shader_order_vectors(vectors):
    for vector in vectors:
        actual = _apply(vector["rgb"], vector["adjustment"])
        assert actual == pytest.approx(vector["expected"], abs=1e-7), vector["name"]
        prepared = prepare_color_adjustment(vector["adjustment"])
        actual = apply_prepared_color_adjustment(vector["rgb"], prepared)
        assert actual == pytest.approx(vector["expected"], abs=1e-7), vector["name"]
        bytes_result = apply_prepared_color_u8(
            tuple(round(channel * 255) for channel in vector["rgb"]),
            prepared)
        source_bytes = tuple(round(channel * 255)
                             for channel in vector["rgb"])
        expected_bytes = tuple(round(channel * 255) for channel in
                               _apply(
                                   tuple(channel / 255 for channel in source_bytes),
                                   vector["adjustment"]))
        assert bytes_result == expected_bytes


@pytest.mark.parametrize(
    ("rgb", "adjustment", "expected"),
    [
        ((1.0, 0.0, 0.0), {"green": 1.0}, (1.0, 0.0, 0.0)),
        ((1.0, 0.0, 0.0), {"green": 1.5}, (1.0, 0.5, 0.0)),
        ((1.0, 0.0, 0.0), {"green": 2.0}, (1.0, 1.0, 0.0)),
        ((1.0, 0.0, 0.0), {"blue": 2.0}, (1.0, 0.0, 1.0)),
        ((0.0, 1.0, 0.0), {"red": 2.0}, (1.0, 1.0, 0.0)),
        ((0.0, 1.0, 0.0), {"blue": 2.0}, (0.0, 1.0, 1.0)),
        ((0.0, 0.0, 1.0), {"red": 2.0}, (1.0, 0.0, 1.0)),
        ((0.0, 0.0, 1.0), {"green": 2.0}, (0.0, 1.0, 1.0)),
        ((0.8, 0.0, 0.0), {"green": 2.0}, (0.8, 0.8, 0.0)),
        ((0.2, 0.0, 0.0), {"green": 2.0}, (0.2, 0.2, 0.0)),
        ((0.8, 0.4, 0.2), {"green": 1.5}, (0.8, 0.6, 0.2)),
        ((0.8, 0.4, 0.2), {"green": 2.0}, (0.8, 0.8, 0.2)),
        ((0.8, 0.4, 0.2), {"red": 0.5, "blue": 0.0},
         (0.4, 0.4, 0.0)),
        ((0.5, 0.5, 0.5), {"red": 2.0}, (0.5, 0.5, 0.5)),
        ((0.5, 0.5, 0.5), {"red": 0.0}, (0.0, 0.5, 0.5)),
        ((0.8, 0.2, 0.0),
         {"red": 0.5, "green": 2.0, "blue": 2.0},
         (0.4, 0.8, 0.8)),
    ],
)
def test_rgb_channel_adjustments_fill_missing_channels_and_preserve_shading(
        rgb, adjustment, expected):
    actual = _apply(rgb, adjustment)
    prepared = apply_prepared_color_adjustment(
        rgb, prepare_color_adjustment(adjustment))

    assert actual == pytest.approx(expected, abs=1e-7)
    assert prepared == pytest.approx(expected, abs=1e-7)


def test_brightness_and_rgb_adjustments_are_applied_with_tint():
    assert _apply(
        (0.4, 0.0, 0.0),
        {"brightness": 1.5, "green": 2.0},
    ) == pytest.approx((0.6, 0.6, 0.0), abs=1e-7)
    assert _apply(
        (0.2, 0.0, 0.0),
        {"brightness": 3.0, "tint": "#ff9999"},
    ) == pytest.approx((0.6, 0.36, 0.36), abs=1e-7)


@pytest.mark.parametrize(
    ("rgb", "adjustment", "expected"),
    [
        ((0.8, 0.2, 0.1), {"tint": None}, (0.8, 0.2, 0.1)),
        ((0.8, 0.1, 0.1), {"tint": "#ff8080"},
         (0.8, 0.8 * 128 / 255, 0.8 * 128 / 255)),
        ((0.2, 0.02, 0.02), {"tint": "#ff8080"},
         (0.2, 0.2 * 128 / 255, 0.2 * 128 / 255)),
        ((0.7, 0.2, 0.1), {"tint": "#ffffff"}, (0.7, 0.7, 0.7)),
        ((0.7, 0.2, 0.1), {"tint": "#000000"}, (0.0, 0.0, 0.0)),
        ((0.8, 0.0, 0.0),
         {"green": 2.0, "tint": "#ff8080"},
         (0.8, 0.8, 0.8 * 128 / 255)),
    ],
)
def test_tint_recolors_toward_target_while_preserving_intensity(
        rgb, adjustment, expected):
    actual = _apply(rgb, adjustment)
    prepared = apply_prepared_color_adjustment(
        rgb, prepare_color_adjustment(adjustment))

    assert actual == pytest.approx(expected, abs=1e-7)
    assert prepared == pytest.approx(expected, abs=1e-7)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("hue", 120),
        ("saturation", 0),
        ("brightness", 0.5),
        ("contrast", 0.5),
        ("green", 2),
    ],
)
def test_tint_keeps_color_controls_active(field, value):
    rgb = (0.8, 0.1, 0.05)
    base_adjustment = {"tint": "#4080c0"}
    changed_adjustment = {**base_adjustment, field: value}

    base = _apply(rgb, base_adjustment)
    changed = _apply(rgb, changed_adjustment)
    prepared = apply_prepared_color_adjustment(
        rgb, prepare_color_adjustment(changed_adjustment))

    assert changed != pytest.approx(base, abs=1e-7)
    assert prepared == pytest.approx(changed, abs=1e-7)

"""CPU color-bake math and alpha/mask invariants."""

import json
from pathlib import Path

import pytest

from core.textures import color_adjustment
from core.textures.color_adjustment import (
    COLOR_DEFAULTS, adjust_rgba_bytes, apply_color_adjustment,
    apply_prepared_color_adjustment, apply_prepared_color_u8,
    is_neutral_color_adjustment, normalize_color_adjustment,
    prepare_color_adjustment,
)


@pytest.fixture
def vectors():
    path = Path(__file__).parents[2] / "fixtures" / "color_adjustment_vectors.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_shader_order_vectors(vectors):
    for vector in vectors:
        actual = apply_color_adjustment(vector["rgb"], vector["adjustment"])
        assert actual == pytest.approx(vector["expected"], abs=1e-7), vector["name"]


def test_normalization_accepts_frontend_tint_name_and_rejects_bad_values():
    assert normalize_color_adjustment({"tintStrength": 0.25})[
        "tint_strength"] == 0.25
    assert normalize_color_adjustment({"hue": True}, reject_invalid=True) is None
    assert normalize_color_adjustment({"tint": "blue"}, reject_invalid=True) is None


def test_neutral_state_is_identity():
    assert is_neutral_color_adjustment(COLOR_DEFAULTS)
    assert apply_color_adjustment((0.23, 0.45, 0.91), COLOR_DEFAULTS) == \
        pytest.approx((0.23, 0.45, 0.91))


def test_prepared_adjustment_matches_validated_transform(vectors):
    for vector in vectors:
        prepared = prepare_color_adjustment(vector["adjustment"])
        actual = apply_prepared_color_adjustment(vector["rgb"], prepared)
        assert actual == pytest.approx(vector["expected"], abs=1e-7), vector["name"]
        bytes_result = apply_prepared_color_u8(
            tuple(round(channel * 255) for channel in vector["rgb"]),
            prepared)
        source_bytes = tuple(round(channel * 255)
                             for channel in vector["rgb"])
        expected_bytes = tuple(round(channel * 255) for channel in
                               apply_color_adjustment(
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
    actual = apply_color_adjustment(rgb, adjustment)
    prepared = apply_prepared_color_adjustment(
        rgb, prepare_color_adjustment(adjustment))

    assert actual == pytest.approx(expected, abs=1e-7)
    assert prepared == pytest.approx(expected, abs=1e-7)


def test_rgb_adjustment_uses_post_brightness_value_before_tint():
    assert apply_color_adjustment(
        (0.4, 0.0, 0.0),
        {"brightness": 1.5, "green": 2.0},
    ) == pytest.approx((0.6, 0.6, 0.0), abs=1e-7)
    assert apply_color_adjustment(
        (0.8, 0.0, 0.0),
        {"green": 2.0, "tint": "#0000ff", "tint_strength": 0.5},
    ) == pytest.approx((0.4, 0.4, 0.5), abs=1e-7)


def test_adjust_rgba_preserves_alpha_and_only_changes_selected_pixels():
    source = bytes([255, 0, 0, 7, 0, 255, 0, 129])

    result = adjust_rgba_bytes(
        source, 2, 1, {"hue": 120}, pixel_mask=[True, False])

    assert result == bytes([0, 255, 0, 7, 0, 255, 0, 129])


def test_adjust_rgba_skips_transform_for_unselected_pixels(monkeypatch):
    calls = []
    real_apply = color_adjustment._apply_normalized

    def counting_apply(rgb, normalized):
        calls.append(rgb)
        return real_apply(rgb, normalized)

    monkeypatch.setattr(color_adjustment, "_apply_normalized", counting_apply)

    color_adjustment.adjust_rgba_bytes(
        bytes([10, 20, 30, 7, 40, 50, 60, 8, 70, 80, 90, 9]),
        3, 1, {"hue": 30}, pixel_mask=[True, False, False])

    assert len(calls) == 1

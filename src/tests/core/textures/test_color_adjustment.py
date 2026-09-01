"""CPU color-bake math and alpha/mask invariants."""

import json
from pathlib import Path

import pytest

from core.textures.color_adjustment import (
    COLOR_DEFAULTS, adjust_rgba_bytes, apply_color_adjustment,
    is_neutral_color_adjustment, normalize_color_adjustment,
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


def test_adjust_rgba_preserves_alpha_and_only_changes_selected_pixels():
    source = bytes([255, 0, 0, 7, 0, 255, 0, 129])

    result = adjust_rgba_bytes(
        source, 2, 1, {"hue": 120}, pixel_mask=[True, False])

    assert result == bytes([0, 255, 0, 7, 0, 255, 0, 129])

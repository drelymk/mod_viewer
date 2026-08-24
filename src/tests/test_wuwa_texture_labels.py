"""Regression tests for the shared offline WuWa label vocabulary."""

from tools.wuwa_texture_labels import MANUAL_LABELS, canonical_label


def test_manual_and_training_labels_share_negative_and_ignored_values():
    assert "not_diffuse" in MANUAL_LABELS
    assert "skip" in MANUAL_LABELS
    assert canonical_label(" NOT_DIFFUSE ") == ("not_diffuse", 0)
    assert canonical_label(" skip ") is None

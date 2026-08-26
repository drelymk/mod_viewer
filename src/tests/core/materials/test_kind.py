"""Conservative material-kind classification regressions."""

import pytest

from core.materials.kind import (MaterialKindDetection, detect_material_kind,
                                MATERIAL_KIND_UNKNOWN)


@pytest.mark.parametrize(
    "entry, expected",
    [
        ({"material_kind_evidence": {
            "kind": "face", "reliable": True,
            "reason": "explicit game binding signature",
        }}, MaterialKindDetection(
            kind="face", reliable=True,
            reason="explicit game binding signature")),
        ({"component": "Face"}, MaterialKindDetection(
            kind="face", reliable=False, reason="component-name hint")),
        ({"texture_file": "Face.png"}, MaterialKindDetection(
            kind=MATERIAL_KIND_UNKNOWN, reliable=False, reason="")),
    ],
    ids=("explicit-reliable", "component-weak-hint", "filename-ignored"),
)
def test_material_kind_evidence_precedence(entry, expected):
    assert detect_material_kind(entry) == expected

"""Conservative material-kind classification regressions."""

from core.material_kind import (MaterialKindDetection, detect_material_kind,
                                MATERIAL_KIND_UNKNOWN)


def test_explicit_structural_evidence_is_reliable():
    result = detect_material_kind({
        "material_kind_evidence": {
            "kind": "face", "reliable": True,
            "reason": "explicit game binding signature",
        },
    })

    assert result == MaterialKindDetection(
        kind="face", reliable=True,
        reason="explicit game binding signature")


def test_component_label_is_only_a_weak_hint():
    result = detect_material_kind({"component": "Face"})

    assert (result.kind, result.reliable, result.reason) == (
        "face", False, "component-name hint")


def test_filenames_do_not_classify_a_mesh():
    result = detect_material_kind({"texture_file": "Face.png"})

    assert (result.kind, result.reliable) == (MATERIAL_KIND_UNKNOWN, False)


def test_conflicting_component_hints_fall_back_to_unknown():
    result = detect_material_kind({"component": "Face Hair"})

    assert (result.kind, result.reliable) == (MATERIAL_KIND_UNKNOWN, False)
    assert result.reason == "conflicting component-kind hints"

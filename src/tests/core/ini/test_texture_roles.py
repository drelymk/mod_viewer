"""Texture evidence precedence regressions."""

from core.ini.texture_roles import _effective_role_assignments


def test_semantic_texture_evidence_subtracts_its_covered_condition():
    mode_one = [[{"var": "mode", "value": 1, "negate": False}]]
    resolved = _effective_role_assignments([
        {"res": "Semantic", "cond": mode_one, "source": "semantic"},
        {"res": "Legacy", "cond": [], "source": "legacy_slot"},
    ])

    assert resolved == [
        {"res": "Semantic", "cond": mode_one, "source": "semantic"},
        {"res": "Legacy", "source": "legacy_slot", "cond": [[
            {"var": "mode", "value": 1, "negate": True},
        ]]},
    ]

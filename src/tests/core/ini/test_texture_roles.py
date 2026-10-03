"""Texture evidence precedence regressions."""

from core.ini.texture_roles import _effective_role_assignments


def test_semantic_texture_evidence_subtracts_its_covered_condition():
    mode_one = [[{"var": "mode", "value": 1, "negate": False}]]
    resolved = _effective_role_assignments([
        {"res": "Semantic", "cond": mode_one, "source": "semantic"},
        {"res": "Legacy", "cond": [], "source": "legacy_slot"},
    ])

    assert [(item["res"], item["cond"]) for item in resolved] == [
        ("Semantic", mode_one),
        ("Legacy", [[
            {"var": "mode", "value": 1, "negate": True},
        ]]),
    ]


def test_large_texture_history_preserves_order_with_bounded_evidence_reads():
    reads = 0

    class Assignment(dict):
        def get(self, key, default=None):
            nonlocal reads
            if key == "source":
                reads += 1
            return super().get(key, default)

    assignments = [Assignment(
        res=f"ResourceVariant{index % 2}", source="semantic", cond=[],
    ) for index in range(32)]

    resolved = _effective_role_assignments(assignments)

    assert resolved == assignments
    assert reads <= len(assignments) * 8

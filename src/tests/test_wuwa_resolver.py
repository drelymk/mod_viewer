"""Regression tests for offline WuWa resolver ranking and outcomes."""

from tools import evaluate_wuwa_resolver as resolver


def _candidate(sha, rank, model, baseline=0.0, tags=()):
    return {
        "texture_sha256": sha,
        "association_rank": rank,
        "association_kind": min(
            resolver.ASSOCIATION_RANKS,
            key=lambda name: abs(resolver.ASSOCIATION_RANKS[name] - rank)),
        "association_kinds": set(),
        "association_tiers": set(),
        "filename_tags": set(tags),
        "relative_files": set(),
        "roles": set(),
        "model_score": model,
        "baseline_color_score": baseline,
    }


def test_policies_have_distinct_association_and_model_orders():
    candidates = [
        _candidate("model", 3, 0.99),
        _candidate("direct", 0, 0.70),
    ]

    assert resolver.rank_candidates(candidates, "association_first")[0][
        "texture_sha256"] == "direct"
    assert resolver.rank_candidates(candidates, "model_first")[0][
        "texture_sha256"] == "model"


def test_resolver_accepts_any_member_of_legitimate_ambiguity():
    candidates = [
        _candidate("diffuse-a", 0, 0.91),
        _candidate("diffuse-b", 0, 0.89),
    ]
    expected = {
        "kind": "diffuse",
        "texture_shas": {"diffuse-a", "diffuse-b"},
        "description": "trusted diffuse component label",
    }

    result = resolver.resolve_case(candidates, expected, "model_first")

    assert result["status"] == "correct"
    assert result["ambiguous_expected"] is True


def test_missing_filename_target_is_unavailable():
    expected = resolver._expected_for_case(
        {
            "name": "missing",
            "mod_id": "mod",
            "component": "Component0",
            "expected_filename_tag": "missing-tag",
        },
        [_candidate("sha", 1, 0.9)],
        {},
    )

    assert expected["kind"] == "unavailable"


def test_association_threshold_can_abstain_on_weak_exact_evidence():
    candidates = [_candidate("weak-exact", 1, 0.65)]
    expected = {
        "kind": "unresolved",
        "texture_shas": [],
        "description": "expected unresolved",
    }

    result = resolver.resolve_case(
        candidates, expected, "association_thresholds")

    assert result["status"] == "correct"
    assert result["predicted_texture_sha256"] is None

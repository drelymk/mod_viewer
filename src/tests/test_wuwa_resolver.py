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

    assert resolver.rank_candidates(candidates, "association_then_model")[0][
        "texture_sha256"] == "direct"
    assert resolver.rank_candidates(candidates, "model_first")[0][
        "texture_sha256"] == "model"


def test_resolver_abstains_for_legitimate_ambiguity():
    candidates = [
        _candidate("diffuse-a", 0, 0.91),
        _candidate("diffuse-b", 0, 0.90999),
    ]
    expected = {
        "kind": "ambiguous",
        "texture_shas": {"diffuse-a", "diffuse-b"},
        "description": "expected abstention for legitimate ambiguity",
    }

    result = resolver.resolve_case(
        candidates, expected, "association_then_model")

    assert result["status"] == "correct_abstention"
    assert result["predicted_texture_sha256"] is None
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

    assert result["status"] == "correct_abstention"
    assert result["predicted_texture_sha256"] is None


def test_membership_policy_compares_direct_and_exact_together():
    candidates = [
        _candidate("exact", 1, 0.99),
        _candidate("direct", 0, 0.70),
    ]

    assert resolver.rank_candidates(
        candidates, "association_then_model")[0]["texture_sha256"] == "direct"
    assert resolver.rank_candidates(
        candidates, "membership_then_model")[0]["texture_sha256"] == "exact"

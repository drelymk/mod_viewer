"""Evaluate an offline WuWa diffuse-texture resolver.

The evaluator combines the trained diffuse score with component association
evidence. It is deliberately separate from the viewer runtime and writes
benchmark artifacts only.

Usage::

    python -m tools.evaluate_wuwa_resolver <corpus> --model <model.joblib> \
        --output <directory>
"""

from __future__ import annotations

import argparse
import csv
import html
import json
from collections import defaultdict
from pathlib import Path

from tools import train_wuwa_texture_model as trainer


ASSOCIATION_RANKS = {
    "direct": 0,
    "exact": 1,
    "leading": 2,
    "contains": 3,
    "pool": 4,
    "inventory": 5,
    "unknown": 6,
}
DIRECT_SOURCES = frozenset({
    "explicit_semantic_binding",
    "mod_slot_mapping",
})
DIRECT_CONTEXTS = frozenset({
    "parser_binding",
    "parser_slot_binding",
})
FILENAME_TIERS = frozenset({"exact", "leading", "contains"})
POLICIES = (
    "current_heuristic",
    "association_first",
    "association_thresholds",
    "model_first",
    "combined",
)
ASSOCIATION_THRESHOLDS = {
    "direct": 0.50,
    "exact": 0.80,
    "leading": 0.70,
    "contains": 0.999,
    "pool": 0.80,
    "inventory": 0.90,
    "unknown": 0.90,
}
COMBINED_BONUS = {
    0: 0.25,
    1: 0.15,
    2: 0.08,
    3: 0.0,
    4: -0.05,
    5: -0.10,
    6: -0.15,
}


def _read_csv(path):
    with Path(path).open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2,
                               sort_keys=True) + "\n", encoding="utf-8")


def _as_set(value):
    if value is None:
        return set()
    if isinstance(value, (list, tuple, set)):
        return {str(item) for item in value if str(item)}
    return {str(value)} if str(value) else set()


def _number(value, default=-1.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def association_kind(row):
    """Return the strongest evidence kind represented by one occurrence."""
    if ((row.get("source") or "") in DIRECT_SOURCES
            or (row.get("context") or "") in DIRECT_CONTEXTS):
        return "direct"
    tier = (row.get("association_tier") or "").strip().lower()
    if tier in FILENAME_TIERS:
        return tier
    if (row.get("context") or "") == "parser_resource_pool":
        return "pool"
    if (row.get("context") or "") == "file_inventory":
        return "inventory"
    return "unknown"


def load_candidates(corpus_dir):
    """Aggregate occurrence evidence into one candidate per component and SHA."""
    corpus_dir = Path(corpus_dir)
    textures = {
        row.get("sha256"): row
        for row in _read_csv(corpus_dir / "textures.csv")
        if row.get("sha256")
    }
    grouped = {}
    for row in _read_csv(corpus_dir / "occurrences.csv"):
        mod_id = (row.get("mod_id") or "").strip()
        component = (row.get("component") or "").strip()
        sha = (row.get("texture_sha256") or "").strip()
        if not mod_id or not component or not sha:
            continue
        key = (mod_id, component, sha)
        candidate = grouped.setdefault(key, {
            "mod_id": mod_id,
            "component": component,
            "texture_sha256": sha,
            "relative_files": set(),
            "association_kinds": set(),
            "association_tiers": set(),
            "filename_tags": set(),
            "roles": set(),
        })
        if row.get("relative_file"):
            candidate["relative_files"].add(row["relative_file"])
        candidate["association_kinds"].add(association_kind(row))
        if row.get("association_tier"):
            candidate["association_tiers"].add(row["association_tier"])
        if row.get("filename_tag"):
            candidate["filename_tags"].add(row["filename_tag"])
        if row.get("role"):
            candidate["roles"].add(row["role"])

    candidates = defaultdict(list)
    for candidate in grouped.values():
        rank = min(ASSOCIATION_RANKS[k]
                   for k in candidate["association_kinds"])
        texture = textures.get(candidate["texture_sha256"], {})
        candidates[(candidate["mod_id"], candidate["component"])].append({
            **candidate,
            "association_rank": rank,
            "association_kind": min(
                candidate["association_kinds"],
                key=lambda kind: ASSOCIATION_RANKS[kind]),
            "model_score": None,
            "baseline_color_score": _number(
                texture.get("baseline_color_score")),
        })
    for values in candidates.values():
        values.sort(key=lambda row: row["texture_sha256"])
    return candidates


def score_candidates(corpus_dir, model_path, candidates):
    """Add trained model probabilities to every candidate with known features."""
    import joblib

    corpus_dir = Path(corpus_dir)
    model_path = Path(model_path)
    if model_path.is_dir():
        model_path = model_path / "model.joblib"
    model = joblib.load(model_path)
    feature_columns, _schema_version = trainer._load_model_features(corpus_dir)
    texture_rows = [
        {"texture_sha256": sha}
        for values in candidates.values()
        for sha in {row["texture_sha256"] for row in values}
    ]
    feature_rows = [
        {"texture_sha256": row["texture_sha256"]}
        for row in texture_rows
    ]
    matrix, _missing = trainer._load_feature_matrix(
        corpus_dir, feature_rows, feature_columns)
    probabilities = model.predict_proba(matrix)[:, 1]
    scores = {
        row["texture_sha256"]: float(probability)
        for row, probability in zip(feature_rows, probabilities)
    }
    for values in candidates.values():
        for candidate in values:
            candidate["model_score"] = scores.get(
                candidate["texture_sha256"])
    return candidates


def load_expected_labels(corpus_dir):
    expected = defaultdict(set)
    for row in _read_csv(Path(corpus_dir) / "component_labels.csv"):
        if ((row.get("label") or "").strip().lower() == "diffuse"
                and row.get("mod_id") and row.get("component")
                and row.get("texture_sha256")):
            expected[(row["mod_id"], row["component"])].add(
                row["texture_sha256"])
    return expected


def load_cases(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError("resolver cases must be a JSON list")
    for index, case in enumerate(value):
        if not isinstance(case, dict):
            raise ValueError(f"resolver case {index} is not an object")
        for field in ("name", "mod_id", "component"):
            if not case.get(field):
                raise ValueError(f"resolver case {index} lacks {field}")
    return value


def _expected_for_case(case, candidates, expected_labels):
    key = (case["mod_id"], case["component"])
    if case.get("expected_status") == "unresolved":
        if not candidates:
            return {"kind": "unavailable", "texture_shas": set(),
                    "description": "expected unresolved case not in corpus"}
        return {"kind": "unresolved", "texture_shas": set(),
                "description": "expected unresolved"}
    explicit_shas = _as_set(case.get("expected_texture_sha256"))
    if explicit_shas:
        target = explicit_shas
        description = "explicit texture SHA"
    else:
        tags = _as_set(case.get("expected_filename_tag"))
        if tags:
            target = {
                candidate["texture_sha256"]
                for candidate in candidates
                if candidate["filename_tags"] & tags
            }
            description = "filename tag " + ", ".join(sorted(tags))
            if not target:
                return {"kind": "unavailable", "texture_shas": set(),
                        "description": description + " not in corpus"}
        elif case.get("expected_label") == "diffuse":
            target = set(expected_labels.get(key, set()))
            description = "trusted diffuse component label"
            if not target:
                return {"kind": "unavailable", "texture_shas": set(),
                        "description": description + " not in corpus"}
        else:
            raise ValueError(
                f"resolver case {case['name']!r} has no expected outcome")
    available = {candidate["texture_sha256"] for candidate in candidates}
    if not target & available:
        return {"kind": "unavailable", "texture_shas": target,
                "description": description + " not among candidates"}
    return {"kind": "diffuse", "texture_shas": target,
            "description": description}


def _sort_key(candidate, policy):
    rank = candidate["association_rank"]
    model_score = (candidate["model_score"]
                   if candidate["model_score"] is not None else -1.0)
    baseline = candidate["baseline_color_score"]
    if policy == "current_heuristic":
        return (-baseline, rank, -model_score, candidate["texture_sha256"])
    if policy == "association_first":
        return (rank, -model_score, -baseline, candidate["texture_sha256"])
    if policy == "association_thresholds":
        return (rank, -model_score, -baseline, candidate["texture_sha256"])
    if policy == "model_first":
        return (-model_score, rank, -baseline, candidate["texture_sha256"])
    if policy == "combined":
        combined = model_score + COMBINED_BONUS.get(rank, 0.0)
        return (-combined, rank, -model_score, candidate["texture_sha256"])
    raise ValueError(f"unknown resolver policy: {policy}")


def rank_candidates(candidates, policy):
    """Return candidates in deterministic policy order."""
    return sorted(candidates, key=lambda row: _sort_key(row, policy))


def _candidate_report(row):
    return {
        "texture_sha256": row["texture_sha256"],
        "relative_files": sorted(row["relative_files"]),
        "association_kind": row["association_kind"],
        "association_rank": row["association_rank"],
        "association_kinds": sorted(row["association_kinds"]),
        "association_tiers": sorted(row["association_tiers"]),
        "filename_tags": sorted(row["filename_tags"]),
        "roles": sorted(row["roles"]),
        "model_score": row["model_score"],
        "baseline_color_score": row["baseline_color_score"],
    }


def resolve_case(candidates, expected, policy, min_score=0.5):
    ordered = rank_candidates(candidates, policy)
    predicted = None
    if ordered:
        top = ordered[0]
        if policy == "current_heuristic":
            predicted = top["texture_sha256"]
        else:
            threshold = min_score
            if policy == "association_thresholds":
                threshold = ASSOCIATION_THRESHOLDS.get(
                    top["association_kind"], min_score)
            if ((top["model_score"] is not None)
                    and top["model_score"] >= threshold):
                predicted = top["texture_sha256"]

    if expected["kind"] == "unavailable":
        status = "unavailable"
    elif expected["kind"] == "unresolved":
        status = "correct" if predicted is None else "wrong"
    elif predicted is None:
        status = "unresolved"
    else:
        status = ("correct" if predicted in expected["texture_shas"]
                  else "wrong")
    return {
        "status": status,
        "predicted_texture_sha256": predicted,
        "expected_texture_shas": sorted(expected["texture_shas"]),
        "expected_description": expected["description"],
        "ambiguous_expected": len(expected["texture_shas"]) > 1,
        "candidate_count": len(ordered),
        "candidates": [_candidate_report(row) for row in ordered],
    }


def _summary(results):
    counts = {status: 0 for status in
              ("correct", "wrong", "unresolved", "unavailable")}
    for result in results:
        counts[result["status"]] += 1
    automatic = counts["correct"] + counts["wrong"]
    return {
        **counts,
        "total_cases": len(results),
        "eligible_cases": len(results) - counts["unavailable"],
        "automatic_cases": automatic,
        "false_positive_rate": (
            counts["wrong"] / automatic if automatic else None),
    }


def _compare_to_baseline(results, baseline_results):
    baseline_by_name = {
        row["name"]: row for row in baseline_results
    }
    fixed = 0
    regressed = 0
    for row in results:
        previous = baseline_by_name.get(row["name"], {})
        if previous.get("status") in {"wrong", "unresolved"} \
                and row["status"] == "correct":
            fixed += 1
        if previous.get("status") == "correct" \
                and row["status"] in {"wrong", "unresolved"}:
            regressed += 1
    return {"fixed": fixed, "regressed": regressed}


def evaluate_corpus(corpus_dir, model_path, cases_path, *, min_score=0.5):
    """Run all resolver policies against the supplied case set."""
    candidates = load_candidates(corpus_dir)
    score_candidates(corpus_dir, model_path, candidates)
    expected_labels = load_expected_labels(corpus_dir)
    cases = load_cases(cases_path)
    baseline = []
    policy_results = {}
    for case in cases:
        expected = _expected_for_case(
            case, candidates.get((case["mod_id"], case["component"]), []),
            expected_labels)
        expected = {
            **expected,
            "texture_shas": sorted(expected["texture_shas"]),
        }
        row = {
            "name": case["name"],
            "mod_id": case["mod_id"],
            "component": case["component"],
            "expected": expected,
        }
        result = resolve_case(
            candidates.get((case["mod_id"], case["component"]), []),
            expected, "current_heuristic", min_score=min_score)
        baseline.append({**row, **result})
    for policy in POLICIES:
        if policy == "current_heuristic":
            results = baseline
        else:
            results = []
            for base in baseline:
                expected = {
                    "kind": base["expected"]["kind"],
                    "texture_shas": set(base["expected"]["texture_shas"]),
                    "description": base["expected"]["description"],
                }
                candidates_for_case = candidates.get(
                    (base["mod_id"], base["component"]), [])
                results.append({
                    **{key: base[key] for key in
                       ("name", "mod_id", "component", "expected")},
                    **resolve_case(candidates_for_case, expected, policy,
                                   min_score=min_score),
                })
        summary = _summary(results)
        if policy != "current_heuristic":
            summary.update(_compare_to_baseline(results, baseline))
        policy_results[policy] = {"summary": summary, "cases": results}
    return {
        "schema_version": "wuwa-resolver-evaluation-v1",
        "corpus": str(Path(corpus_dir).resolve()),
        "model": str(Path(model_path).resolve()),
        "cases_file": str(Path(cases_path).resolve()),
        "min_score": min_score,
        "association_ranks": ASSOCIATION_RANKS,
        "association_thresholds": ASSOCIATION_THRESHOLDS,
        "combined_bonus": COMBINED_BONUS,
        "policies": policy_results,
    }


def _write_html_report(path, report):
    rows = []
    for policy, value in report["policies"].items():
        summary = value["summary"]
        rows.append(
            "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td>"
            "<td>{}</td><td>{}</td><td>{}</td></tr>".format(
                html.escape(policy), summary["correct"], summary["wrong"],
                summary["unresolved"], summary["unavailable"],
                summary["false_positive_rate"],
                summary.get("fixed", "-")))
    page = """<!doctype html><meta charset='utf-8'>
<title>WuWa resolver evaluation</title>
<h1>WuWa resolver evaluation</h1>
<p>Offline benchmark only. Runtime behavior is unchanged.</p>
<pre>{summary}</pre>
<table><tr><th>Policy</th><th>Correct</th><th>Wrong</th>
<th>Unresolved</th><th>Unavailable</th><th>False-positive rate</th>
<th>Fixed</th></tr>{rows}</table>
""".format(
        summary=html.escape(json.dumps({
            "min_score": report["min_score"],
            "policies": {
                policy: value["summary"]
                for policy, value in report["policies"].items()
            },
        }, indent=2, sort_keys=True)),
        rows="".join(rows),
    )
    Path(path).write_text(page, encoding="utf-8")


def evaluate_and_write(corpus_dir, model_path, cases_path, output_dir,
                       *, min_score=0.5):
    report = evaluate_corpus(
        corpus_dir, model_path, cases_path, min_score=min_score)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "resolver_evaluation.json", report)
    _write_html_report(output_dir / "resolver_evaluation.html", report)
    return report


def _build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--cases", default=Path(__file__).with_name(
        "wuwa_resolver_regressions.json"), type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--min-score", default=0.5, type=float)
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    report = evaluate_and_write(
        args.corpus, args.model, args.cases, args.output,
        min_score=args.min_score)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Train and evaluate an offline WuWa Diffuse classifier.

This module is intentionally separate from the viewer runtime.  It consumes a
corpus produced by ``wuwa_texture_corpus`` and uses only authoritative labels
plus explicit manual labels.  Unknown examples are excluded from supervised
training.

Usage::

    python -m tools.train_wuwa_texture_model <corpus> --output <directory>
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import sys
from collections import defaultdict
from pathlib import Path


POSITIVE_LABELS = frozenset({"diffuse"})
NEGATIVE_LABELS = frozenset({
    "light_map", "material_map", "normal_map", "not_diffuse",
})
IGNORED_LABELS = frozenset({"", "skip", "unknown"})
MODEL_EXCLUDED_PREFIXES = ("baseline_",)
TRAINING_LABEL_FIELDS = [
    "texture_sha256", "label", "target", "label_sources", "source_labels",
    "group_signature", "group_count",
]


def _read_csv(path):
    with Path(path).open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _write_csv(path, rows, fields):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2,
                               sort_keys=True) + "\n", encoding="utf-8")


def _canonical_label(label):
    label = (label or "").strip().lower()
    if label in POSITIVE_LABELS:
        return "diffuse", 1
    if label in NEGATIVE_LABELS:
        return "not_diffuse", 0
    if label in IGNORED_LABELS:
        return None
    raise ValueError(f"unsupported training label: {label}")


def _signature_for_mod(row):
    signature = (row.get("character_signature") or "").strip()
    if signature:
        return signature
    return f"mod:{row.get('mod_id') or row.get('relative_path') or ''}"


def _load_group_map(corpus_dir):
    mods = _read_csv(Path(corpus_dir) / "mods.csv")
    mod_groups = {
        row.get("mod_id", ""): _signature_for_mod(row)
        for row in mods
    }
    texture_groups = defaultdict(set)
    for row in _read_csv(Path(corpus_dir) / "occurrences.csv"):
        sha = row.get("texture_sha256") or ""
        mod_id = row.get("mod_id") or ""
        if sha:
            texture_groups[sha].add(
                mod_groups.get(mod_id, f"mod:{mod_id}"))
    textures = _read_csv(Path(corpus_dir) / "textures.csv")
    for row in textures:
        sha = row.get("sha256") or ""
        if sha and not texture_groups[sha]:
            texture_groups[sha].add(
                mod_groups.get(row.get("example_mod_id", ""),
                               f"mod:{row.get('example_mod_id', '')}"))
    return mod_groups, texture_groups


def _load_labels(corpus_dir, *, include_secondary=False):
    label_sets = defaultdict(set)
    source_sets = defaultdict(set)
    original_sets = defaultdict(set)
    manual_rows = 0
    manual_unknown = 0
    secondary_rows = 0
    for row in _read_csv(Path(corpus_dir) / "trusted_labels.csv"):
        if (row.get("label_source") == "legacy_slot_mapping"
                and not include_secondary):
            secondary_rows += 1
            continue
        sha = row.get("texture_sha256") or ""
        if not sha:
            continue
        canonical = _canonical_label(row.get("label"))[0]
        if canonical is None:
            continue
        label_sets[sha].add(canonical)
        source_sets[sha].add(row.get("label_source") or "trusted")
        original_sets[sha].add(row.get("label") or "")
    for row in _read_csv(Path(corpus_dir) / "manual_labels.csv"):
        manual_rows += 1
        sha = row.get("texture_sha256") or ""
        if not sha:
            continue
        parsed = _canonical_label(row.get("label"))
        if parsed is None:
            manual_unknown += 1
            continue
        canonical, _target = parsed
        label_sets[sha].add(canonical)
        source_sets[sha].add("manual")
        original_sets[sha].add(row.get("label") or "")
    return label_sets, source_sets, original_sets, {
        "manual_rows": manual_rows,
        "manual_unknown_rows": manual_unknown,
        "secondary_rows_excluded": secondary_rows,
    }


def _group_signature(groups):
    values = sorted(groups or {"group:missing"})
    return hashlib.sha256("|".join(values).encode("utf-8")).hexdigest()[:16]


def load_training_rows(corpus_dir, *, include_secondary=False):
    """Load one conflict-free, one-texture training row per SHA."""
    corpus_dir = Path(corpus_dir)
    textures = {
        row.get("sha256"): row
        for row in _read_csv(corpus_dir / "textures.csv")
        if row.get("sha256")
    }
    _mod_groups, texture_groups = _load_group_map(corpus_dir)
    label_sets, source_sets, original_sets, label_stats = _load_labels(
        corpus_dir, include_secondary=include_secondary)
    rows = []
    conflicts = []
    missing_features = []
    shared_shas = 0
    for sha, labels in sorted(label_sets.items()):
        if len(labels) != 1:
            conflicts.append({"texture_sha256": sha,
                              "labels": sorted(labels)})
            continue
        if sha not in textures:
            missing_features.append(sha)
            continue
        groups = texture_groups.get(sha) or {"group:missing"}
        if len(groups) > 1:
            shared_shas += 1
            continue
        label = next(iter(labels))
        rows.append({
            "texture_sha256": sha,
            "label": label,
            "target": 1 if label == "diffuse" else 0,
            "label_sources": json.dumps(sorted(source_sets[sha]),
                                         separators=(",", ":")),
            "source_labels": json.dumps(sorted(original_sets[sha]),
                                         separators=(",", ":")),
            "group_signature": _group_signature(groups),
            "group_count": len(groups),
        })
    stats = {
        **label_stats,
        "candidate_label_shas": len(label_sets),
        "training_rows": len(rows),
        "positive_rows": sum(row["target"] == 1 for row in rows),
        "negative_rows": sum(row["target"] == 0 for row in rows),
        "conflict_rows_excluded": len(conflicts),
        "missing_feature_rows_excluded": len(missing_features),
        "shared_texture_shas": shared_shas,
        "shared_texture_rows_excluded": shared_shas,
        "character_groups": len({row["group_signature"] for row in rows}),
        "character_groups_in_label_map": len({
            group for groups in texture_groups.values() for group in groups
        }),
    }
    return rows, stats, conflicts, missing_features


def _load_feature_matrix(corpus_dir, rows, feature_columns):
    import numpy as np

    textures = {
        row.get("sha256"): row
        for row in _read_csv(Path(corpus_dir) / "textures.csv")
    }
    values = []
    missing = []
    for row in rows:
        source = textures.get(row["texture_sha256"], {})
        current = []
        for column in feature_columns:
            value = source.get(column, "")
            if column == "dds_format":
                current.append(value)
                continue
            if value in ("", None):
                current.append(np.nan)
                continue
            lowered = str(value).lower()
            if lowered == "true":
                current.append(1.0)
            elif lowered == "false":
                current.append(0.0)
            else:
                try:
                    current.append(float(value))
                except (TypeError, ValueError):
                    current.append(np.nan)
        if not current:
            missing.append(row["texture_sha256"])
        values.append(current)
    return np.asarray(values, dtype=object), missing


def _load_model_features(corpus_dir):
    manifest = json.loads((Path(corpus_dir) / "manifest.json").read_text(
        encoding="utf-8"))
    columns = list(manifest.get("model_feature_columns") or [])
    if not columns:
        raise ValueError("corpus manifest has no model_feature_columns")
    excluded = [column for column in columns
                if column.startswith(MODEL_EXCLUDED_PREFIXES)]
    if excluded:
        raise ValueError(
            "diagnostic baseline features entered model columns: "
            + ", ".join(excluded))
    return columns, manifest.get("feature_schema_version")


def _one_hot_encoder():
    from sklearn.preprocessing import OneHotEncoder

    try:
        return OneHotEncoder(handle_unknown="ignore", sparse_output=False)
    except TypeError:  # scikit-learn before 1.2
        return OneHotEncoder(handle_unknown="ignore", sparse=False)


def _build_pipeline(model, feature_columns):
    from sklearn.compose import ColumnTransformer
    from sklearn.impute import SimpleImputer
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    categorical = [index for index, column in enumerate(feature_columns)
                   if column == "dds_format"]
    numeric = [index for index in range(len(feature_columns))
               if index not in categorical]
    transformers = []
    if numeric:
        transformers.append((
            "numeric",
            Pipeline([
                ("impute", SimpleImputer(strategy="median")),
                ("scale", StandardScaler()),
            ]),
            numeric,
        ))
    if categorical:
        transformers.append((
            "categorical",
            Pipeline([
                ("impute", SimpleImputer(strategy="most_frequent")),
                ("onehot", _one_hot_encoder()),
            ]),
            categorical,
        ))
    return Pipeline([
        ("features", ColumnTransformer(transformers=transformers,
                                        remainder="drop")),
        ("model", model),
    ])


def _splits(y, groups, folds, seed):
    from sklearn.model_selection import GroupKFold, StratifiedGroupKFold

    group_count = len(set(groups))
    split_count = min(max(2, int(folds)), group_count)
    if split_count < 2:
        return []
    try:
        splitter = StratifiedGroupKFold(
            n_splits=split_count, shuffle=True, random_state=seed)
        return list(splitter.split(list(range(len(y))), y, groups))
    except ValueError:
        splitter = GroupKFold(n_splits=split_count)
        return list(splitter.split(list(range(len(y))), y, groups))


def _evaluate_model(model, X, y, groups, feature_columns, folds, seed):
    import numpy as np
    from sklearn.metrics import (average_precision_score, confusion_matrix,
                                 f1_score, precision_score, recall_score)

    splits = _splits(y, groups, folds, seed)
    probabilities = np.full(len(y), np.nan, dtype=float)
    fold_stats = []
    for fold, (train_indices, test_indices) in enumerate(splits, start=1):
        if len(set(y[train_indices])) < 2:
            fold_stats.append({"fold": fold, "status": "skipped_one_class"})
            continue
        pipeline = _build_pipeline(model, feature_columns)
        pipeline.fit(X[train_indices], y[train_indices])
        probabilities[test_indices] = pipeline.predict_proba(
            X[test_indices])[:, 1]
        fold_stats.append({
            "fold": fold,
            "status": "evaluated",
            "train_rows": int(len(train_indices)),
            "validation_rows": int(len(test_indices)),
            "train_groups": len(set(groups[train_indices])),
            "validation_groups": len(set(groups[test_indices])),
        })
    evaluated = ~np.isnan(probabilities)
    if not evaluated.any():
        metrics = {key: None for key in
                   ("average_precision", "precision", "recall", "f1")}
        confusion = [[0, 0], [0, 0]]
    else:
        predicted = (probabilities[evaluated] >= 0.5).astype(int)
        actual = y[evaluated]
        metrics = {
            "average_precision": float(average_precision_score(
                actual, probabilities[evaluated])),
            "precision": float(precision_score(actual, predicted,
                                                zero_division=0)),
            "recall": float(recall_score(actual, predicted, zero_division=0)),
            "f1": float(f1_score(actual, predicted, zero_division=0)),
        }
        confusion = confusion_matrix(actual, predicted, labels=[0, 1]).tolist()
    final = _build_pipeline(model, feature_columns)
    final.fit(X, y)
    return final, {
        "metrics": metrics,
        "confusion_matrix_labels": ["not_diffuse", "diffuse"],
        "confusion_matrix": confusion,
        "evaluated_rows": int((~np.isnan(probabilities)).sum()),
        "folds": fold_stats,
    }


def _model_factories(seed):
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression

    return {
        "logistic_regression": lambda: LogisticRegression(
            max_iter=2000, class_weight="balanced", random_state=seed),
        "hist_gradient_boosting": lambda: HistGradientBoostingClassifier(
            learning_rate=0.05, max_iter=200, max_leaf_nodes=15,
            random_state=seed),
    }


def _require_ml():
    try:
        import joblib  # noqa: F401
        import numpy  # noqa: F401
        import sklearn  # noqa: F401
    except ImportError as exc:
        raise SystemExit(
            "ML dependencies are not installed. Install requirements-ml.txt "
            "in an offline training environment.") from exc


def train_corpus(corpus_dir, output_dir, *, folds=5, seed=42,
                 include_secondary=False):
    """Train both planned baseline models and write an evaluation report."""
    _require_ml()
    import joblib
    import numpy as np

    rows, label_stats, conflicts, missing_features = load_training_rows(
        corpus_dir, include_secondary=include_secondary)
    if len({row["target"] for row in rows}) < 2:
        raise ValueError("training labels contain only one target class")
    feature_columns, feature_schema_version = _load_model_features(corpus_dir)
    X, feature_missing = _load_feature_matrix(corpus_dir, rows, feature_columns)
    y = np.asarray([row["target"] for row in rows], dtype=int)
    groups = np.asarray([row["group_signature"] for row in rows])
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "training_labels.csv", rows, TRAINING_LABEL_FIELDS)
    _write_json(output_dir / "feature_schema.json", {
        "feature_schema_version": feature_schema_version,
        "feature_columns": feature_columns,
        "categorical_columns": [column for column in feature_columns
                                 if column == "dds_format"],
        "diagnostic_columns_excluded": [
            "baseline_color_score", "baseline_confidence",
            "baseline_data_score", "baseline_mask_score",
            "baseline_normal_score", "baseline_role",
            "baseline_texture_class",
        ],
    })
    models_dir = output_dir / "models"
    model_reports = {}
    final_models = {}
    for name, factory in _model_factories(seed).items():
        final_model, report = _evaluate_model(
            factory(), X, y, groups, feature_columns, folds, seed)
        model_path = models_dir / f"{name}.joblib"
        model_path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(final_model, model_path)
        model_reports[name] = report
        final_models[name] = final_model
    eligible = {
        name: report["metrics"].get("average_precision")
        for name, report in model_reports.items()
        if report["metrics"].get("average_precision") is not None
    }
    selected_name = max(eligible, key=eligible.get) if eligible else None
    if selected_name:
        joblib.dump(final_models[selected_name], output_dir / "model.joblib")
    report = {
        "schema_version": "wuwa-texture-model-v1",
        "corpus": str(Path(corpus_dir).resolve()),
        "seed": seed,
        "folds_requested": folds,
        "include_secondary": include_secondary,
        "label_stats": label_stats,
        "feature_rows_missing": len(set(feature_missing)),
        "conflicts": conflicts,
        "missing_features": missing_features,
        "feature_schema_version": feature_schema_version,
        "feature_columns": feature_columns,
        "models": model_reports,
        "selected_model": selected_name,
        "leakage": {
            "split_unit": "character_signature_group",
            "duplicate_texture_unit": "texture_sha256",
            "shared_texture_shas": label_stats["shared_texture_shas"],
            "shared_texture_rows_excluded": label_stats[
                "shared_texture_rows_excluded"],
            "duplicate_validation_rows_removed": 0,
        },
    }
    _write_json(output_dir / "training_report.json", report)
    _write_html_report(output_dir / "training_report.html", report)
    return report


def _write_html_report(path, report):
    rows = []
    for name, model in report["models"].items():
        metrics = model["metrics"]
        rows.append(
            "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td>"
            "<td>{}</td></tr>".format(
                html.escape(name), metrics.get("average_precision"),
                metrics.get("precision"), metrics.get("recall"),
                metrics.get("f1")))
    page = """<!doctype html><meta charset='utf-8'>
<title>WuWa texture model report</title>
<h1>WuWa texture model report</h1>
<p>Offline evaluation only. Unknown labels are excluded; runtime behavior is unchanged.</p>
<pre>{summary}</pre>
<table><tr><th>Model</th><th>PR-AUC</th><th>Precision</th>
<th>Recall</th><th>F1</th></tr>{rows}</table>
""".format(
        summary=html.escape(json.dumps({
            "selected_model": report["selected_model"],
            "label_stats": report["label_stats"],
            "leakage": report["leakage"],
        }, indent=2, sort_keys=True)),
        rows="".join(rows),
    )
    Path(path).write_text(page, encoding="utf-8")


def _build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--folds", default=5, type=int)
    parser.add_argument("--seed", default=42, type=int)
    parser.add_argument("--include-secondary", action="store_true")
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    report = train_corpus(
        args.corpus, args.output, folds=args.folds, seed=args.seed,
        include_secondary=args.include_secondary)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

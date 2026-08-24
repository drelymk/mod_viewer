"""Regression tests for offline WuWa model-label preparation."""

import csv
import json

from tools import train_wuwa_texture_model as model


def _write_csv(path, fields, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_training_rows_exclude_unknowns_and_conflicts(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "manifest.json").write_text(json.dumps({
        "model_feature_columns": ["dds_format", "dds_width"],
        "feature_schema_version": "test",
    }), encoding="utf-8")
    _write_csv(corpus / "mods.csv", [
        "mod_id", "character_signature",
    ], [
        {"mod_id": "A", "character_signature": "char-a"},
        {"mod_id": "B", "character_signature": "char-b"},
    ])
    _write_csv(corpus / "textures.csv", [
        "sha256", "dds_format", "dds_width",
    ], [
        {"sha256": "a", "dds_format": "bc7_srgb", "dds_width": "128"},
        {"sha256": "b", "dds_format": "bc5_unorm", "dds_width": "128"},
        {"sha256": "c", "dds_format": "bc7_srgb", "dds_width": "128"},
        {"sha256": "d", "dds_format": "bc7_srgb", "dds_width": "128"},
    ])
    _write_csv(corpus / "occurrences.csv", [
        "texture_sha256", "mod_id",
    ], [
        {"texture_sha256": "a", "mod_id": "A"},
        {"texture_sha256": "a", "mod_id": "B"},
        {"texture_sha256": "b", "mod_id": "A"},
        {"texture_sha256": "c", "mod_id": "B"},
        {"texture_sha256": "d", "mod_id": "A"},
    ])
    _write_csv(corpus / "trusted_labels.csv", [
        "texture_sha256", "label", "label_source",
    ], [
        {"texture_sha256": "a", "label": "diffuse",
         "label_source": "explicit_semantic_binding"},
        {"texture_sha256": "b", "label": "normal_map",
         "label_source": "explicit_semantic_binding"},
        {"texture_sha256": "c", "label": "diffuse",
         "label_source": "explicit_semantic_binding"},
        {"texture_sha256": "c", "label": "light_map",
         "label_source": "explicit_semantic_binding"},
    ])
    _write_csv(corpus / "manual_labels.csv", [
        "texture_sha256", "label", "notes", "reviewer", "source",
    ], [
        {"texture_sha256": "d", "label": "unknown", "notes": "",
         "reviewer": "", "source": "visual_review"},
    ])

    rows, stats, conflicts, missing = model.load_training_rows(corpus)

    assert [row["texture_sha256"] for row in rows] == ["b"]
    assert [row["target"] for row in rows] == [0]
    assert stats["training_rows"] == 1
    assert stats["positive_rows"] == 0
    assert stats["negative_rows"] == 1
    assert stats["conflict_rows_excluded"] == 1
    assert stats["manual_unknown_rows"] == 1
    assert stats["shared_texture_shas"] == 1
    assert stats["shared_texture_rows_excluded"] == 1
    assert conflicts[0]["texture_sha256"] == "c"
    assert missing == []


def test_model_feature_loader_rejects_baseline_columns(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "manifest.json").write_text(json.dumps({
        "model_feature_columns": ["mean", "baseline_role"],
    }), encoding="utf-8")

    try:
        model._load_model_features(corpus)
    except ValueError as exc:
        assert "diagnostic baseline" in str(exc)
    else:
        raise AssertionError("baseline feature column was accepted")

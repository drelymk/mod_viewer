"""Regression tests for the offline WuWa corpus tooling."""

import csv
import hashlib
from types import SimpleNamespace

from tools import wuwa_texture_corpus as corpus


def _write_mod(root):
    mod = root / "Character" / "Outfit"
    mod.mkdir(parents=True)
    ini = r"""[Constants]
global $\WWMIv1\object_guid = 1

[TextureOverrideComponent0]
ib = ResourceComponent0IB
vb0 = ResourceComponent0Position
vb1 = ResourceComponent0Texcoord
Resource\WWMI\Diffuse = ref ResourceDiffuse
Resource\WWMI\NormalMap = ref ResourceNormal
Resource\WWMI\LightMap = ref ResourceLight
drawindexed = 3, 0, 0

[ResourceComponent0IB]
filename = component.ib
format = R32_UINT
[ResourceComponent0Position]
filename = component-position.buf
stride = 40
[ResourceComponent0Texcoord]
filename = component-texcoord.buf
stride = 20
[ResourceDiffuse]
filename = Components-0 t=semantic.dds
[ResourceNormal]
filename = normal.dds
[ResourceLight]
filename = light.dds
"""
    (mod / "mod.ini").write_text(ini, encoding="utf-8")
    (mod / "Components-0 t=semantic.dds").write_bytes(b"diffuse")
    (mod / "normal.dds").write_bytes(b"normal")
    (mod / "light.dds").write_bytes(b"light")
    (mod / "Components-0-1 t=filename-only.dds").write_bytes(b"filename")
    (mod / "unknown.dds").write_bytes(b"unknown")
    (mod / "duplicate.dds").write_bytes(b"unknown")
    (mod / "mod.zip").write_bytes(b"archive")
    return mod


def test_association_tiers_and_character_signature_are_diagnostic_only():
    assert corpus.filename_association((0,), 0) == "exact"
    assert corpus.filename_association((0, 1), 0) == "leading"
    assert corpus.filename_association((0, 1, 2), 1) == "contains"
    assert corpus.filename_association((1, 2), 0) is None
    assert corpus.parse_component_filename(
        r"folder\Components-0-1 t=abc123.dds") == {
            "components": (0, 1), "tag": "abc123"}
    assert "sha256" not in corpus.model_feature_columns(
        {"sha256", "dds_width", "baseline_role", "example_mod_id"})
    assert "dds_width" in corpus.model_feature_columns(
        {"sha256", "dds_width", "baseline_role", "example_mod_id"})


def test_scan_uses_parser_labels_and_keeps_unknown_separate(tmp_path, monkeypatch):
    mod = _write_mod(tmp_path / "mods")
    output = tmp_path / "corpus"

    # Make the diagnostic classifier claim every file is a diffuse candidate.
    # That must not create a trusted label: classifier output, Components
    # filenames, and the t= tag are all excluded by the label policy.
    monkeypatch.setattr(
        corpus, "classify_dds",
        lambda _path: SimpleNamespace(
            role="diffuse", texture_class="color", confidence="high",
            color_score=1.0, normal_score=0.0, mask_score=0.0,
            data_score=0.0),
    )

    summary = corpus.scan_corpus(tmp_path / "mods", output)

    assert summary["mods_in_dataset"] == 1
    assert summary["archives_found"] == summary["archives_skipped"] == 1
    assert summary["feature_extractions"] == 5
    assert summary["unknown_candidates"] >= 1

    labels = list((output / "trusted_labels.csv").read_text(
        encoding="utf-8").splitlines())
    assert any(",diffuse,explicit_semantic_binding,primary," in line
               for line in labels)
    assert any(",normal_map,explicit_semantic_binding,primary," in line
               for line in labels)
    assert any(",light_map,explicit_semantic_binding,primary," in line
               for line in labels)
    assert not any("filename_analysis" in line for line in labels)
    assert not any("filename-only" in line for line in labels)

    unknown = (output / "unknown_candidates.csv").read_text(encoding="utf-8")
    assert "filename-only" in unknown
    assert "unknown" in unknown

    occurrences = (output / "occurrences.csv").read_text(encoding="utf-8")
    assert "exact" in occurrences
    assert "leading" in occurrences

    with (output / "unknown_candidates.csv").open(
            encoding="utf-8", newline="") as stream:
        first_unknown = next(csv.DictReader(stream))["texture_sha256"]
    with (output / "manual_labels.csv").open(
            "a", encoding="utf-8", newline="") as stream:
        stream.write(
            f"{first_unknown},diffuse,reviewed color texture,,visual_review\n")

    second = corpus.scan_corpus(tmp_path / "mods", output)
    assert second["feature_extractions"] == 0
    assert second["manual_label_rows"] == 1
    manual_labels = (output / "manual_labels.csv").read_text(encoding="utf-8")
    assert f"{first_unknown},diffuse,reviewed color texture" in manual_labels
    texture_rows = (output / "textures.csv").read_text(encoding="utf-8")
    assert "Character/Outfit" in texture_rows


def test_non_wuwa_mods_are_reported_but_excluded(tmp_path):
    root = tmp_path / "mods"
    mod = root / "Foreign"
    mod.mkdir(parents=True)
    (mod / "mod.ini").write_text(
        "[TextureOverrideBody]\n"
        "ib = ResourceIB\n"
        "drawindexed = 3, 0, 0\n"
        "[ResourceIB]\nfilename = body.ib\n",
        encoding="utf-8")
    summary = corpus.scan_corpus(root, tmp_path / "corpus")
    assert summary["mods_discovered"] == 1
    assert summary["mods_in_dataset"] == 0
    assert summary["mods_by_game"] == {"unknown": 1}


def test_nested_ini_directories_are_independent_texture_boundaries(tmp_path):
    root = tmp_path / "mods"
    parent = root / "Parent"
    child = parent / "Child"
    child.mkdir(parents=True)
    (parent / "parent.ini").write_text("[Constants]\n", encoding="utf-8")
    (child / "child.ini").write_text("[Constants]\n", encoding="utf-8")
    (parent / "parent.dds").write_bytes(b"parent")
    (child / "child.dds").write_bytes(b"child")

    directories = corpus.discover_mod_directories(root)
    parent_files = list(corpus._walk_mod_textures(parent, directories))

    assert str(parent) in directories
    assert str(child) in directories
    assert str(parent / "parent.dds") in parent_files
    assert str(child / "child.dds") not in parent_files


def test_known_labels_bypass_pixel_preview_budget(tmp_path, monkeypatch):
    output = tmp_path / "corpus"
    output.mkdir()
    texture = tmp_path / "sample.dds"
    texture.write_bytes(b"sample")
    sha = hashlib.sha256(b"sample").hexdigest()
    (output / "trusted_labels.csv").write_text(
        "texture_sha256,label,label_source\n"
        f"{sha},diffuse,explicit_semantic_binding\n",
        encoding="utf-8")
    (output / "feature_cache.json").write_text(
        "{\"feature_schema_version\": \"wuwa-texture-features-v1.1\","
        " \"pixel_decode_limit\": 1, \"entries\": {"
        f"\"{sha}\": {{\"sha256\": \"{sha}\", "
        "\"decode_status\": \"skipped_budget\"}}}}",
        encoding="utf-8")

    monkeypatch.setattr(corpus, "inspect_dds", lambda _path: object())
    monkeypatch.setattr(corpus, "load_texture_image",
                        lambda _path, **_kwargs: object())
    monkeypatch.setattr(corpus, "_diagnostic_baseline",
                        lambda *_args: None)
    monkeypatch.setattr(
        corpus, "extract_texture_features",
        lambda _path, **_kwargs: {
            "feature_version": "wuwa-texture-features-v1.1",
            "decode_status": "decoded",
        },
    )

    builder = corpus.CorpusBuilder(
        tmp_path / "mods", output, pixel_limit=0)
    builder._ensure_texture(texture, "Mod", "sample.dds")

    assert builder.feature_extractions == 1
    assert builder.pixel_decoded == 1
    assert builder.feature_cache_hits == 0

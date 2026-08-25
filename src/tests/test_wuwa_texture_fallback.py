import pytest

from app.wuwa_texture_fallback import apply
from app.wuwa_texture_names import texture_component_ordinals
from core.draw_call import DrawCall, SlotTextureBinding
from core.ini_parser import TextureOverrideIndex, TextureReplacement


@pytest.mark.parametrize("filename, expected", [
    ("Components-2 t=abc.dds", frozenset({2})),
    ("folder\\Components-0-1-4 t=abc.dds", frozenset({0, 1, 4})),
    ("SomeTexture.dds", None),
    ("Components-2 t=abc.png", None),
])
def test_texture_component_ordinals(filename, expected):
    assert texture_component_ordinals(filename) == expected


def _replacement(tmp_path, original_hash, filename, *, create=True):
    if create:
        path = tmp_path / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic texture")
    return TextureReplacement(
        original_hash, f"Resource{original_hash}", (),
        "TextureOverrideGenerated", filename)


def _group(name, index=None, draws=None):
    return {
        "name": name,
        "display_name": name,
        "draws": list(draws or [DrawCall()]),
        "_texture_override_index": index,
    }


def _files(group):
    return [item["file"] for item in group["discovered_textures"]]


def test_filename_exact_association_discovers_without_mutating_draw(tmp_path):
    replacement = _replacement(
        tmp_path, "aaaaaaaa", "Components-2 t=A.dds")
    draw = DrawCall()
    group = _group("Component2", TextureOverrideIndex(
        replacements_by_hash={"aaaaaaaa": (replacement,)}), [draw])

    apply([group], str(tmp_path))

    assert _files(group) == [replacement.file]
    assert draw.texture_default("diffuse") is None
    assert draw.texture_provenance == {}


def test_filename_shared_association_is_included(tmp_path):
    replacement = _replacement(
        tmp_path, "aaaaaaaa", "Components-0-1-2-3 t=A.dds")
    group = _group("Component2", TextureOverrideIndex(
        replacements_by_hash={"aaaaaaaa": (replacement,)}))

    apply([group], str(tmp_path))

    assert _files(group) == [replacement.file]


def test_all_matching_filenames_are_retained(tmp_path):
    replacements = [
        _replacement(tmp_path, "aaaaaaaa", "Components-2 t=A.dds"),
        _replacement(tmp_path, "bbbbbbbb", "Components-2-3 t=B.dds"),
        _replacement(tmp_path, "cccccccc", "Components-0-1-2 t=C.dds"),
    ]
    group = _group("Component2", TextureOverrideIndex(
        replacements_by_hash={
            "aaaaaaaa": (replacements[0],),
            "bbbbbbbb": (replacements[1],),
            "cccccccc": (replacements[2],),
        }))

    apply([group], str(tmp_path))

    assert _files(group) == [item.file for item in replacements]


def test_nonmatching_filename_is_excluded(tmp_path):
    replacement = _replacement(
        tmp_path, "aaaaaaaa", "Components-3-4 t=A.dds")
    group = _group("Component2", TextureOverrideIndex(
        replacements_by_hash={"aaaaaaaa": (replacement,)}))

    apply([group], str(tmp_path))

    assert group["discovered_textures"] == []


def test_matching_replacement_survives_mixed_hash_family(tmp_path):
    matching = _replacement(
        tmp_path, "aaaaaaaa", "Components-2 t=A.dds")
    other = _replacement(
        tmp_path, "aaaaaaaa", "Components-5 t=B.dds")
    group = _group("Component2", TextureOverrideIndex(
        replacements_by_hash={"aaaaaaaa": (matching, other)}))

    apply([group], str(tmp_path))

    assert _files(group) == [matching.file]


def test_dds_classifier_is_not_called_by_discovery(tmp_path, monkeypatch):
    replacement = _replacement(
        tmp_path, "aaaaaaaa", "Components-2 t=A.dds")

    def classify(_path):
        raise AssertionError("classifier must not be called")

    monkeypatch.setattr("app.asset_enrichment.classify_dds", classify)
    group = _group("Component2", TextureOverrideIndex(
        replacements_by_hash={"aaaaaaaa": (replacement,)}))

    apply([group], str(tmp_path))

    assert _files(group) == [replacement.file]


def test_slot_candidates_union_effective_bindings_across_draws(tmp_path):
    for filename in ("A.dds", "B.dds", "C.dds"):
        (tmp_path / filename).write_bytes(b"synthetic texture")
    draws = [
        DrawCall(slot_textures=[
            SlotTextureBinding(0, "ResourceA", file="A.dds"),
            SlotTextureBinding(1, "ResourceB", file="B.dds"),
        ]),
        DrawCall(slot_textures=[
            SlotTextureBinding(0, "ResourceC", file="C.dds"),
            SlotTextureBinding(2, "ResourceB", file="B.dds"),
        ]),
    ]
    group = _group("Component2", draws=draws)

    apply([group], str(tmp_path))

    assert set(_files(group)) == {"A.dds", "B.dds", "C.dds"}


def test_duplicate_slot_files_are_recorded_once(tmp_path):
    (tmp_path / "A.dds").write_bytes(b"synthetic texture")
    draw = DrawCall(slot_textures=[
        SlotTextureBinding(0, "ResourceA", file="A.dds"),
        SlotTextureBinding(3, "ResourceA", file="A.dds"),
    ])
    group = _group("Component2", draws=[draw])

    apply([group], str(tmp_path))

    assert _files(group) == ["A.dds"]


def test_filename_and_slot_duplicate_is_recorded_once(tmp_path):
    replacement = _replacement(
        tmp_path, "aaaaaaaa", "Components-2 t=A.dds")
    draw = DrawCall(slot_textures=[
        SlotTextureBinding(0, "ResourceA", file=replacement.file),
    ])
    group = _group("Component2", TextureOverrideIndex(
        replacements_by_hash={"aaaaaaaa": (replacement,)}), [draw])

    apply([group], str(tmp_path))

    assert group["discovered_textures"] == [{
        "file": replacement.file,
        "source": "wuwa_ps_slot",
    }]


def test_slotfix_bindings_are_discovered_without_role_assignment(tmp_path):
    (tmp_path / "A.dds").write_bytes(b"synthetic texture")
    draw = DrawCall(slot_textures=[SlotTextureBinding(
        0, "ResourceA", file="A.dds", role_hint="diffuse",
        role_hint_source="mod_slot_mapping")])
    group = _group("Component2", draws=[draw])

    apply([group], str(tmp_path))

    assert _files(group) == ["A.dds"]
    assert draw.texture_default("diffuse") is None
    assert draw.texture_provenance == {}


def test_existing_texture_defaults_are_untouched(tmp_path):
    for filename in ("A.dds", "existing.dds", "existing-normal.dds"):
        (tmp_path / filename).write_bytes(b"synthetic texture")
    replacement = _replacement(
        tmp_path, "aaaaaaaa", "Components-2 t=A.dds", create=False)
    draw = DrawCall(
        texture_default_file="existing.dds",
        normal_map_default_file="existing-normal.dds")
    group = _group("Component2", TextureOverrideIndex(
        replacements_by_hash={"aaaaaaaa": (replacement,)}), [draw])

    apply([group], str(tmp_path))

    assert draw.texture_default("diffuse") == "existing.dds"
    assert draw.texture_default("normal_map") == "existing-normal.dds"
    assert draw.texture_default("light_map") is None
    assert draw.texture_default("material_map") is None
    assert draw.texture_provenance == {}


def test_empty_unsafe_and_missing_slot_files_are_ignored(tmp_path):
    (tmp_path / "safe.dds").write_bytes(b"synthetic texture")
    draw = DrawCall(slot_textures=[
        SlotTextureBinding(0, "ResourceSafe", file="safe.dds"),
        SlotTextureBinding(1, "ResourceNull", file=None),
        SlotTextureBinding(2, "ResourceMissing", file="missing.dds"),
        SlotTextureBinding(3, "ResourceUnsafe", file="../../outside.dds"),
        SlotTextureBinding(4, "ResourceEmpty", file=""),
    ])
    group = _group("ComponentWithoutFilename", draws=[draw])

    apply([group], str(tmp_path))

    assert _files(group) == ["safe.dds"]

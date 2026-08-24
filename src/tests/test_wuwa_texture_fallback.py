import os
from types import SimpleNamespace

from app import mod_loader
from app.asset_resolver import AssetComponentBinding
from app.wuwa_texture_fallback import apply
from core import dds_classifier
from core.draw_call import DrawCall, SlotTextureBinding
from core.ini_parser import TextureOverrideIndex, TextureReplacement


def _replacement(tmp_path, original_hash, filename, *, conditions=()):
    path = tmp_path / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"synthetic dds")
    return TextureReplacement(
        original_hash, f"Resource{original_hash}", conditions,
        "TextureOverrideGenerated", filename)


def _direct_file(tmp_path, filename):
    path = tmp_path / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"synthetic dds")
    return filename


def _group(name, index, draw=None):
    return {
        "name": name,
        "display_name": name,
        "draws": [draw or DrawCall()],
        "_texture_override_index": index,
    }


def _classify(monkeypatch, roles, calls=None):
    def classify(path):
        filename = os.path.basename(path)
        if calls is not None:
            calls.append(filename)
        role = roles[filename]
        return dds_classifier.DDSClassification(
            role, "packed_normal" if role == "normal_map" else "color",
            "high", (f"synthetic_{role}",))

    monkeypatch.setattr("app.asset_enrichment.classify_dds", classify)


def test_no_asset_component_fallback_prefers_exact_filename_candidate(
        tmp_path, monkeypatch):
    shared = _replacement(
        tmp_path, "aaaaaaaa", "Components-0-1-2-3 t=randomA.dds")
    exact = _replacement(
        tmp_path, "bbbbbbbb", "Components-2 t=randomB.dds")
    index = TextureOverrideIndex(replacements_by_hash={
        "aaaaaaaa": (shared,), "bbbbbbbb": (exact,),
    })
    _classify(monkeypatch, {
        shared.file: "diffuse", exact.file: "diffuse",
    })
    draw = DrawCall()

    apply([_group("Component2", index, draw)], str(tmp_path))

    assert draw.texture_default("diffuse") == exact.file
    assert draw.texture_provenance == {"diffuse": "wuwa_filename_analysis"}
    assert draw.asset_binding is None


def test_color_candidate_without_diffuse_role_is_contextually_selected(
        tmp_path, monkeypatch):
    candidate = _replacement(
        tmp_path, "aaaaaaaa", "Components-1-2 t=color-candidate.dds")
    _classify(monkeypatch, {candidate.file: "color"})
    draw = DrawCall()

    apply([_group("Component1", TextureOverrideIndex(
        replacements_by_hash={"aaaaaaaa": (candidate,)}), draw)],
        str(tmp_path))

    assert draw.texture_default("diffuse") == candidate.file
    assert draw.texture_provenance == {"diffuse": "wuwa_filename_analysis"}


def test_component_priority_prefers_leading_target_over_shorter_shared_list(
        tmp_path, monkeypatch):
    shared = _replacement(
        tmp_path, "aaaaaaaa", "Components-0-1-2-3 t=shared.dds")
    leading = _replacement(
        tmp_path, "bbbbbbbb", "Components-1-2-4-5-7 t=leading.dds")
    _classify(monkeypatch, {
        shared.file: "diffuse", leading.file: "diffuse",
    })
    draw = DrawCall()

    apply([_group("Component1", TextureOverrideIndex(
        replacements_by_hash={"aaaaaaaa": (shared,),
                              "bbbbbbbb": (leading,)}), draw)],
        str(tmp_path))

    assert draw.texture_default("diffuse") == leading.file


def test_direct_dds_binding_works_without_index_or_filename_convention(
        tmp_path, monkeypatch):
    filename = _direct_file(tmp_path, "Textures/completely-opaque-name.dds")
    _classify(monkeypatch, {"completely-opaque-name.dds": "diffuse"})
    draw = DrawCall(slot_textures=[SlotTextureBinding(
        0, "ResourceMystery", file=filename)])

    apply([_group("Component4", None, draw)], str(tmp_path))

    assert draw.texture_default("diffuse") == filename
    assert draw.texture_provenance == {"diffuse": "wuwa_direct_analysis"}


def test_direct_dds_bindings_are_resolved_per_draw(tmp_path, monkeypatch):
    first = _direct_file(tmp_path, "first.dds")
    second = _direct_file(tmp_path, "second.dds")
    _classify(monkeypatch, {"first.dds": "diffuse", "second.dds": "diffuse"})
    first_draw = DrawCall(slot_textures=[SlotTextureBinding(
        0, "ResourceFirst", file=first)])
    second_draw = DrawCall(slot_textures=[SlotTextureBinding(
        0, "ResourceSecond", file=second)])
    group = _group("Component4", None, first_draw)
    group["draws"] = [first_draw, second_draw]

    apply([group], str(tmp_path))

    assert first_draw.texture_default("diffuse") == first
    assert second_draw.texture_default("diffuse") == second


def test_direct_dds_binding_beats_filename_fallback(
        tmp_path, monkeypatch):
    direct = _direct_file(tmp_path, "direct.dds")
    fallback = _replacement(
        tmp_path, "aaaaaaaa", "Components-4 t=fallback.dds")
    _classify(monkeypatch, {
        "direct.dds": "diffuse", fallback.file: "diffuse",
    })
    draw = DrawCall(slot_textures=[SlotTextureBinding(
        0, "ResourceDirect", file=direct)])
    group = _group("Component4", TextureOverrideIndex(
        replacements_by_hash={"aaaaaaaa": (fallback,)}), draw)

    apply([group], str(tmp_path))

    assert draw.texture_default("diffuse") == direct
    assert draw.texture_provenance == {"diffuse": "wuwa_direct_analysis"}
    assert {item["file"] for item in group["discovered_textures"]} == {
        direct, fallback.file}


def test_filename_normal_is_ignored_even_when_direct_diffuse_exists(
        tmp_path, monkeypatch):
    direct = _direct_file(tmp_path, "direct.dds")
    normal = _replacement(
        tmp_path, "aaaaaaaa", "Components-4 t=normal.dds")
    _classify(monkeypatch, {
        "direct.dds": "diffuse", normal.file: "normal_map",
    })
    draw = DrawCall(slot_textures=[SlotTextureBinding(
        0, "ResourceDirect", file=direct)])
    group = _group("Component4", TextureOverrideIndex(
        replacements_by_hash={"aaaaaaaa": (normal,)}), draw)

    apply([group], str(tmp_path))

    assert draw.texture_default("diffuse") == direct
    assert draw.texture_default("normal_map") is None
    assert draw.texture_provenance == {"diffuse": "wuwa_direct_analysis"}
    assert [item["file"] for item in group["discovered_textures"]] == [direct]


def test_direct_strong_normal_still_resolves_and_is_discovered(
        tmp_path, monkeypatch):
    normal = _direct_file(tmp_path, "direct-normal.dds")
    _classify(monkeypatch, {"direct-normal.dds": "normal_map"})
    draw = DrawCall(slot_textures=[SlotTextureBinding(
        0, "ResourceNormal", file=normal)])
    group = _group("Component4", None, draw)

    apply([group], str(tmp_path))

    assert draw.texture_default("normal_map") == normal
    assert draw.texture_provenance == {"normal_map": "wuwa_direct_analysis"}
    assert group["discovered_textures"][0]["file"] == normal


def test_multiple_direct_files_with_one_role_are_ambiguous(
        tmp_path, monkeypatch):
    first = _direct_file(tmp_path, "first.dds")
    second = _direct_file(tmp_path, "second.dds")
    _classify(monkeypatch, {"first.dds": "diffuse", "second.dds": "diffuse"})
    draw = DrawCall(slot_textures=[
        SlotTextureBinding(0, "ResourceFirst", file=first),
        SlotTextureBinding(3, "ResourceSecond", file=second),
    ])

    apply([_group("Component4", None, draw)], str(tmp_path))

    assert draw.texture_default("diffuse") is None
    assert draw.texture_provenance == {}


def test_same_direct_file_in_multiple_slots_is_one_candidate(
        tmp_path, monkeypatch):
    filename = _direct_file(tmp_path, "shared.dds")
    _classify(monkeypatch, {"shared.dds": "diffuse"})
    draw = DrawCall(slot_textures=[
        SlotTextureBinding(0, "ResourceShared", file=filename),
        SlotTextureBinding(3, "ResourceShared", file=filename),
    ])

    apply([_group("Component4", None, draw)], str(tmp_path))

    assert draw.texture_default("diffuse") == filename


def test_unknown_direct_dds_does_not_block_filename_fallback(
        tmp_path, monkeypatch):
    unknown = _direct_file(tmp_path, "unknown.dds")
    fallback = _replacement(
        tmp_path, "aaaaaaaa", "Components-4 t=fallback.dds")

    def classify(path):
        if os.path.basename(path) == "unknown.dds":
            return dds_classifier.DDSClassification(
                None, "packed_data", "medium", ("synthetic_unknown",))
        return dds_classifier.DDSClassification(
            "diffuse", "color", "high", ("synthetic_diffuse",))

    monkeypatch.setattr("app.asset_enrichment.classify_dds", classify)
    draw = DrawCall(slot_textures=[SlotTextureBinding(
        0, "ResourceUnknown", file=unknown)])

    apply([_group("Component4", TextureOverrideIndex(
        replacements_by_hash={"aaaaaaaa": (fallback,)}), draw)],
        str(tmp_path))

    assert draw.texture_default("diffuse") == fallback.file
    assert draw.texture_provenance == {"diffuse": "wuwa_filename_analysis"}


def test_non_dds_direct_binding_is_skipped(tmp_path, monkeypatch):
    calls = []

    def classify(path):
        calls.append(path)
        raise AssertionError("non-DDS bindings must not be classified")

    monkeypatch.setattr("app.asset_enrichment.classify_dds", classify)
    draw = DrawCall(slot_textures=[SlotTextureBinding(
        2, "ResourceImage", file="image.jpg")])

    apply([_group("Component4", None, draw)], str(tmp_path))

    assert calls == []
    assert draw.texture_provenance == {}


def test_filename_tag_is_opaque_and_does_not_need_to_match_ini_hash(
        tmp_path, monkeypatch):
    first = _replacement(
        tmp_path, "aaaaaaaa", "Components-2 t=bbbbbbbb.dds")
    _classify(monkeypatch, {first.file: "diffuse"})
    first_draw = DrawCall()
    apply([_group("Component2", TextureOverrideIndex(
        replacements_by_hash={"aaaaaaaa": (first,)}), first_draw)],
        str(tmp_path))

    second = _replacement(
        tmp_path, "aaaaaaaa", "Components-2 t=completely-different-tag.dds")
    _classify(monkeypatch, {second.file: "diffuse"})
    second_draw = DrawCall()
    apply([_group("Component2", TextureOverrideIndex(
        replacements_by_hash={"aaaaaaaa": (second,)}), second_draw)],
        str(tmp_path))

    assert first_draw.texture_default("diffuse") == first.file
    assert second_draw.texture_default("diffuse") == second.file


def test_equal_specificity_distinct_hashes_are_ambiguous(tmp_path, monkeypatch):
    first = _replacement(tmp_path, "aaaaaaaa", "Components-0 t=A.dds")
    second = _replacement(tmp_path, "bbbbbbbb", "Components-0 t=B.dds")
    _classify(monkeypatch, {first.file: "diffuse", second.file: "diffuse"})
    draw = DrawCall()

    apply([_group("Component0", TextureOverrideIndex(
        replacements_by_hash={"aaaaaaaa": (first,), "bbbbbbbb": (second,)}),
        draw)], str(tmp_path))

    assert draw.texture_default("diffuse") is None


def test_ambiguous_filename_candidates_remain_discovered(tmp_path, monkeypatch):
    first = _replacement(tmp_path, "aaaaaaaa", "Components-0 t=A.dds")
    second = _replacement(tmp_path, "bbbbbbbb", "Components-0 t=B.dds")
    _classify(monkeypatch, {first.file: "diffuse", second.file: "diffuse"})
    draw = DrawCall()
    group = _group("Component0", TextureOverrideIndex(
        replacements_by_hash={"aaaaaaaa": (first,), "bbbbbbbb": (second,)}),
        draw)
    group["diffuse_pool_files"] = []

    apply([group], str(tmp_path))

    assert draw.texture_default("diffuse") is None
    assert {item["file"] for item in group["discovered_textures"]} == {
        first.file, second.file}
    assert group["diffuse_pool_files"] == []


def test_different_roles_compete_per_role(tmp_path, monkeypatch):
    normal = _replacement(tmp_path, "aaaaaaaa", "Components-0 t=normal.dds")
    diffuse = _replacement(
        tmp_path, "bbbbbbbb", "Components-0-1-2 t=diffuse.dds")
    _classify(monkeypatch, {
        normal.file: "normal_map", diffuse.file: "diffuse",
    })
    draw = DrawCall()

    apply([_group("Component0", TextureOverrideIndex(
        replacements_by_hash={"aaaaaaaa": (normal,), "bbbbbbbb": (diffuse,)}),
        draw)], str(tmp_path))

    assert draw.texture_default("normal_map") is None
    assert draw.texture_default("diffuse") == diffuse.file


def test_unknown_dds_does_not_create_a_render_role(tmp_path, monkeypatch):
    unknown = _replacement(tmp_path, "aaaaaaaa", "Components-0 t=unknown.dds")

    def classify(_path):
        return dds_classifier.DDSClassification(
            None, "packed_data", "medium", ("synthetic_unknown",))

    monkeypatch.setattr("app.asset_enrichment.classify_dds", classify)
    draw = DrawCall()
    apply([_group("Component0", TextureOverrideIndex(
        replacements_by_hash={"aaaaaaaa": (unknown,)}), draw)], str(tmp_path))

    assert draw.texture_default("diffuse") is None
    assert draw.texture_default("normal_map") is None


def test_slotfix_skips_filename_inference_and_dds_classification(
        tmp_path, monkeypatch):
    direct = _direct_file(tmp_path, "slotfix-direct.dds")
    replacement = _replacement(
        tmp_path, "aaaaaaaa", "Components-0 t=slotfix.dds")
    calls = []
    _classify(monkeypatch, {
        "slotfix-direct.dds": "diffuse", replacement.file: "diffuse",
    }, calls)
    draw = DrawCall(slot_textures=[SlotTextureBinding(
        0, "ResourceSlot", file=direct, role_hint="diffuse",
        role_hint_source="mod_slot_mapping")])

    apply([_group("Component0", TextureOverrideIndex(
        replacements_by_hash={"aaaaaaaa": (replacement,)}), draw)],
        str(tmp_path))

    assert calls == []
    assert draw.texture_default("diffuse") is None


def test_existing_role_is_preserved_while_missing_role_is_filled(
        tmp_path, monkeypatch):
    existing = "existing.dds"
    diffuse = _replacement(tmp_path, "aaaaaaaa", "Components-0 t=other.dds")
    normal = _replacement(tmp_path, "bbbbbbbb", "Components-0 t=normal.dds")
    _classify(monkeypatch, {
        diffuse.file: "diffuse", normal.file: "normal_map",
    })
    draw = DrawCall(texture_default_file=existing)

    apply([_group("Component0", TextureOverrideIndex(
        replacements_by_hash={"aaaaaaaa": (diffuse,), "bbbbbbbb": (normal,)}),
        draw)], str(tmp_path))

    assert draw.texture_default("diffuse") == existing
    assert draw.texture_default("normal_map") is None
    assert draw.texture_provenance == {}


def test_conditional_same_hash_family_keeps_all_variants(
        tmp_path, monkeypatch):
    first = _replacement(
        tmp_path, "aaaaaaaa", "Components-0 t=A.dds", conditions=(
            (("style", "0", False),),))
    second = _replacement(
        tmp_path, "aaaaaaaa", "Components-0 t=B.dds", conditions=(
            (("style", "1", False),),))
    _classify(monkeypatch, {first.file: "diffuse", second.file: "diffuse"})
    draw = DrawCall()

    apply([_group("Component0", TextureOverrideIndex(
        replacements_by_hash={"aaaaaaaa": (first, second)}), draw)],
        str(tmp_path))

    assert [item["file"] for item in draw.texture_rules("diffuse")] == [
        first.file, second.file,
    ]


def test_cross_component_variants_do_not_promote_a_hash_family(
        tmp_path, monkeypatch):
    first = _replacement(tmp_path, "aaaaaaaa", "Components-0 t=A.dds")
    second = _replacement(tmp_path, "aaaaaaaa", "Components-1 t=B.dds")
    calls = []
    _classify(monkeypatch, {first.file: "diffuse", second.file: "diffuse"},
              calls)
    draw = DrawCall()

    apply([_group("Component0", TextureOverrideIndex(
        replacements_by_hash={"aaaaaaaa": (first, second)}), draw)],
        str(tmp_path))

    assert calls == []
    assert draw.texture_default("diffuse") is None


def test_loader_runs_wuwa_fallback_without_asset_configuration(
        tmp_path, monkeypatch):
    replacement = _replacement(
        tmp_path, "aaaaaaaa", "Components-0 t=no-asset.dds")
    _classify(monkeypatch, {replacement.file: "diffuse"})
    draw = DrawCall()
    parsed = mod_loader.ParsedModAnalysis(
        groups=[_group("Component0", TextureOverrideIndex(
            replacements_by_hash={"aaaaaaaa": (replacement,)}), draw)],
        toggles={}, menu={}, defaults={}, state_rules=[], present={},
        game=SimpleNamespace(game="wuwa"),
    )
    context = mod_loader.ModLoadContext(str(tmp_path), [], {}, {})
    binding = AssetComponentBinding(status="not_found")

    mod_loader._apply_texture_enrichment(
        parsed, context, [[binding]], complete_index=False)

    assert draw.texture_default("diffuse") == replacement.file
    assert draw.texture_provenance == {"diffuse": "wuwa_filename_analysis"}


def test_wuwa_fallback_reuses_cache_across_semantic_refresh(
        tmp_path, monkeypatch):
    replacement = _replacement(
        tmp_path, "aaaaaaaa", "Components-0 t=cache.dds")
    calls = []
    _classify(monkeypatch, {replacement.file: "diffuse"}, calls)
    index = TextureOverrideIndex(
        replacements_by_hash={"aaaaaaaa": (replacement,)})
    context = mod_loader.ModLoadContext(str(tmp_path), [], {}, {})

    def parsed(draw):
        return mod_loader.ParsedModAnalysis(
            groups=[_group("Component0", index, draw)],
            toggles={}, menu={}, defaults={}, state_rules=[], present={},
            game=SimpleNamespace(game="wuwa"),
        )

    first = DrawCall()
    second = DrawCall()
    binding = [AssetComponentBinding(status="not_found")]
    mod_loader._apply_texture_enrichment(
        parsed(first), context, [binding], complete_index=False)
    mod_loader._apply_texture_enrichment(
        parsed(second), context, [binding], complete_index=False)

    assert calls == ["Components-0 t=cache.dds"]
    assert first.texture_default("diffuse") == replacement.file
    assert second.texture_default("diffuse") == replacement.file


def test_wwmi_asset_json_does_not_change_filename_texture_result(
        tmp_path, monkeypatch):
    replacement = _replacement(
        tmp_path, "aaaaaaaa", "Components-0 t=filename-authority.dds")
    _classify(monkeypatch, {replacement.file: "diffuse"})
    index = TextureOverrideIndex(
        replacements_by_hash={"aaaaaaaa": (replacement,)})

    def parsed(draw):
        return mod_loader.ParsedModAnalysis(
            groups=[_group("Component0", index, draw)],
            toggles={}, menu={}, defaults={}, state_rules=[], present={},
            game=SimpleNamespace(game="wuwa"),
        )

    without_asset = DrawCall()
    context = mod_loader.ModLoadContext(str(tmp_path), [], {}, {})
    mod_loader._apply_texture_enrichment(
        parsed(without_asset), context,
        [[AssetComponentBinding(status="not_found")]], False)

    asset_root = tmp_path / "assets"
    asset_dir = asset_root / "Alice"
    asset_dir.mkdir(parents=True)
    (asset_dir / "TextureUsage.json").write_text(
        '{"Component 1": {"ps-t0": '
        '["bbbbbbbb-vs=aaaa-ps=bbbb"]}}', encoding="utf-8")
    with_asset = DrawCall()
    binding = AssetComponentBinding(
        status="exact", component_status="exact", range_status="exact",
        asset_type="WWMI", asset="Alice", root=str(asset_root),
        component_ordinal=1, detail_metadata="Alice/TextureUsage.json")
    mod_loader._apply_texture_enrichment(
        parsed(with_asset), context, [[binding]], True)

    assert with_asset.texture_default("diffuse") == \
        without_asset.texture_default("diffuse") == replacement.file

"""Packed material interpretation regressions."""

from dataclasses import replace

import pytest

from core.materials.game_profile import GameDetection
from core.materials.profiles import (ChannelRef, MaterialInterpretation,
                                    material_profile_for)


def test_zzz_uses_light_map_g_for_metalness_and_material_map_b_for_specular():
    profile = material_profile_for("zzz", "zzmi")

    assert profile.id == "zzz:zzmi"
    assert profile.metalness == ChannelRef("light_map", "g")
    assert profile.specular == ChannelRef("material_map", "b")
    assert profile.material_id == ChannelRef("material_map", "r")
    assert profile.to_metadata()["specular_influence"] is None


def test_genshin_uses_light_map_r_response_and_g_toon_shadow():
    profile = material_profile_for("genshin", "gimi")

    assert profile.id == "genshin:gimi"
    assert profile.shadow_mask == ChannelRef("light_map", "g")
    assert profile.material_id == ChannelRef("light_map", "a")
    assert profile.material_id_decoder == "genshin_5_region"
    assert profile.metalness == ChannelRef("light_map", "r")
    assert profile.specular == ChannelRef("light_map", "r")
    assert profile.specular_area == ChannelRef("light_map", "b")
    assert profile.metalness_scale == 0.08
    assert profile.specular_scale == 1.0
    assert profile.specular_influence == 0.15
    assert (profile.toon_specular_shininess,
            profile.toon_specular_threshold_bias,
            profile.toon_specular_softness,
            profile.toon_specular_metal_cutoff) == (10.0, 1.015, 0.0, 0.90)
    assert (profile.shadow_threshold, profile.shadow_softness,
            profile.shadow_mask_strength, profile.shadow_influence) == (
                0.5, 0.08, 0.5, 1.0)


def test_material_profile_kind_falls_back_to_stable_base_profile():
    assert material_profile_for("genshin", "gimi", "unknown").id == "genshin:gimi"
    assert material_profile_for("genshin", "gimi", "body").id == "genshin:gimi"
    assert material_profile_for("genshin", "gimi", "face").id == "genshin:gimi"
    assert material_profile_for("zzz", "zzmi", "hair").id == "zzz:zzmi"
    assert material_profile_for("not-a-game", "unknown", "body").id == "none"


def test_material_profile_accepts_complete_detection_without_guessing_unknown_api():
    detection = GameDetection(
        game="zzz", runtime="zzmi", texture_api="unknown",
        confidence="high", scores={})

    profile = material_profile_for(detection)

    assert profile.id == "none"
    assert profile.game == "zzz"
    assert profile.texture_api == "unknown"


def test_wuwa_keeps_normal_data_available_for_raw_or_unknown_api():
    for texture_api in ("raw", "unknown"):
        profile = material_profile_for("wuwa", texture_api)

        assert profile.id == f"wuwa:{texture_api}"
        assert profile.normal_xy == ("r", "g")
        assert profile.normal_data_b == ChannelRef("normal_data", "b")
        assert profile.normal_data_a == ChannelRef("normal_data", "a")
        assert profile.shadow_mask is None
        assert profile.direct_shadow_model is None
        assert profile.metalness is None
        assert profile.specular is None


def test_wuwa_rabbitfx_alone_enables_validated_shadow_semantics():
    profile = material_profile_for("wuwa", "rabbitfx")

    assert profile.shadow_mask == ChannelRef("light_map", "g")
    assert profile.direct_shadow_model == "wuwa_base"


def test_wuwa_rabbitfx_body_is_the_only_first_specialized_profile():
    body = material_profile_for("wuwa", "rabbitfx", "body")

    assert body.id == "wuwa:rabbitfx:body"
    assert body.material_kind == "body"
    assert body.normal_data_b == ChannelRef("normal_data", "b")
    assert body.normal_data_a == ChannelRef("normal_data", "a")
    assert body.toon_specular_mask == ChannelRef("normal_data", "b")
    assert body.metal_route == ChannelRef("normal_data", "a")
    assert body.direct_specular_model == "wuwa_body"
    assert body.metalness is None
    assert body.specular is None
    assert (body.wuwa_specular_power,
            body.wuwa_toon_specular_cutoff,
            body.wuwa_specular_mask_cutoff) == (1.0, 0.1, 0.5)

    assert material_profile_for("wuwa", "rabbitfx", "hair").id == (
        "wuwa:rabbitfx")
    assert material_profile_for("wuwa", "rabbitfx", "face").id == (
        "wuwa:rabbitfx")
    assert material_profile_for("wuwa", "rabbitfx", "eye").id == (
        "wuwa:rabbitfx")
    assert material_profile_for("wuwa", "raw", "body").id == "wuwa:raw"
    assert material_profile_for("unknown", "unknown", "body").id == "none"




def test_wuwa_shadow_tuning_is_serialized_without_genshin_reuse():
    profile = material_profile_for("wuwa", "rabbitfx")

    assert (profile.wuwa_shadow_process,
            profile.wuwa_shadow_front_offset,
            profile.wuwa_shadow_width,
            profile.wuwa_shadow_mask_cutoff,
            profile.wuwa_shadow_mask_endpoint_tolerance,
            profile.wuwa_shadow_influence) == (
                0.55, 0.4, 0.01, 0.1, 0.01, 1.0)
    metadata = profile.to_metadata()
    assert metadata["direct_shadow_model"] == "wuwa_base"
    assert metadata["wuwa_shadow_width"] == 0.01
    assert metadata["wuwa_shadow_mask_cutoff"] == 0.1
    assert metadata["wuwa_shadow_mask_endpoint_tolerance"] == 0.01
    assert metadata["shadow_threshold"] == 0.5




def test_explicit_kind_evidence_selects_body_but_weak_hint_does_not():
    from app.mods.enrichment import _assign_material_profiles

    detection = GameDetection(
        game="wuwa", runtime="rabbitfx", texture_api="rabbitfx",
        confidence="high", scores={})
    meshes = {
        "Body-0": {"component": "Body"},
        "Override-0": {"component": "Anything", "material_kind_evidence": {
            "kind": "body", "reliable": True,
            "reason": "viewer material-kind override",
        }},
    }

    profiles = _assign_material_profiles(meshes, detection)

    assert meshes["Body-0"]["material_profile_id"] == "wuwa:rabbitfx"
    assert meshes["Override-0"]["material_profile_id"] == (
        "wuwa:rabbitfx:body")
    assert set(profiles) == {"wuwa:rabbitfx", "wuwa:rabbitfx:body"}

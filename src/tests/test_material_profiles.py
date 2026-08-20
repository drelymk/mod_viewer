"""Packed material interpretation regressions."""

from core.game_profile import GameDetection
from core.material_profiles import (ChannelRef, MaterialInterpretation,
                                    material_profile_for)


def test_zzz_uses_material_map_g_for_metalness_and_b_for_specular():
    profile = material_profile_for("zzz", "zzmi")

    assert profile.id == "zzz:zzmi"
    assert profile.metalness == ChannelRef("material_map", "g")
    assert profile.specular == ChannelRef("material_map", "b")
    assert profile.material_id == ChannelRef("material_map", "r")
    assert profile.to_metadata()["specular_influence"] is None


def test_zzz_rabbitfx_profile_is_separate_from_zzmi_but_keeps_semantics():
    profile = material_profile_for("zzz", "rabbitfx")

    assert profile.id == "zzz:rabbitfx"
    assert profile.texture_api == "rabbitfx"
    assert profile.metalness.source == "material_map"
    assert profile.specular.channel == "b"


def test_genshin_uses_light_map_r_response_and_g_toon_shadow():
    profile = material_profile_for("genshin", "gimi")

    assert profile.id == "genshin:gimi"
    assert profile.shadow_mask == ChannelRef("light_map", "g")
    assert profile.metalness == ChannelRef("light_map", "r")
    assert profile.specular == ChannelRef("light_map", "r")
    assert profile.metalness_scale == 0.08
    assert profile.specular_scale == 1.0
    assert profile.specular_influence == 0.15
    assert (profile.shadow_threshold, profile.shadow_softness,
            profile.shadow_mask_strength, profile.shadow_influence) == (
                0.5, 0.08, 0.5, 1.0)


def test_material_profile_kind_falls_back_to_stable_base_profile():
    assert material_profile_for("genshin", "gimi", "unknown").id == "genshin:gimi"
    assert material_profile_for("genshin", "gimi", "body").id == "genshin:gimi"
    assert material_profile_for("genshin", "gimi", "face").id == "genshin:gimi"
    assert material_profile_for("zzz", "zzmi", "hair").id == "zzz:zzmi"
    assert material_profile_for("not-a-game", "unknown", "body").id == "none"


def test_exact_kind_profile_beats_base_profile_without_production_specialization(
        monkeypatch):
    import core.material_profiles as profiles

    specialized = MaterialInterpretation(
        id="genshin:gimi:face", game="genshin", texture_api="gimi",
        material_kind="face")
    monkeypatch.setattr(
        profiles, "_specialized_profile_for",
        lambda game, texture_api, kind: specialized if kind == "face" else None)

    assert material_profile_for("genshin", "gimi", "face") is specialized
    assert material_profile_for("genshin", "gimi", "hair").id == "genshin:gimi"


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
        assert profile.metalness is None
        assert profile.specular is None


def test_structured_payload_exposes_material_profile_metadata():
    from app.mod_loader import _structured_payload

    detection = GameDetection(
        game="genshin", runtime="gimi", texture_api="gimi",
        confidence="high", scores={})
    payload = _structured_payload(game=detection)

    profiles = payload["metadata"]["material_profiles"]
    assert profiles["genshin:gimi"]["id"] == "genshin:gimi"
    assert profiles["genshin:gimi"]["metalness"] == {
        "source": "light_map", "channel": "r", "invert": False}


def test_mesh_profiles_are_deduplicated_and_keep_kind_identity():
    from app.mod_loader import _assign_material_profiles

    detection = GameDetection(
        game="genshin", runtime="gimi", texture_api="gimi",
        confidence="high", scores={})
    meshes = {
        name: {"component": component}
        for name, component in {
            "Body-0": "Body", "Body-1": "Body",
            "Hair-0": "Hair", "Face-0": "Face",
        }.items()
    }

    profiles = _assign_material_profiles(meshes, detection)

    assert len(profiles) == 1
    assert all(entry["material_profile_id"] == "genshin:gimi"
               for entry in meshes.values())
    assert meshes["Face-0"]["material_kind"] == "face"
    assert meshes["Face-0"]["material_kind_reliable"] is False

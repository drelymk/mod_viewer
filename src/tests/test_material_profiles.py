"""Packed material interpretation regressions."""

from core.game_profile import GameDetection
from core.material_profiles import ChannelRef, material_profile_for


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


def test_genshin_uses_conservative_light_map_r_and_b_response():
    profile = material_profile_for("genshin", "gimi")

    assert profile.id == "genshin:gimi"
    assert profile.shadow_mask == ChannelRef("light_map", "g")
    assert profile.metalness == ChannelRef("light_map", "r")
    assert profile.specular == ChannelRef("light_map", "r")
    assert profile.metalness_scale == 0.08
    assert profile.specular_scale == 1.0
    assert profile.specular_influence == 0.15


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

    assert payload["metadata"]["material_profile"]["id"] == "genshin:gimi"
    assert payload["metadata"]["material_profile"]["metalness"] == {
        "source": "light_map", "channel": "r", "invert": False}

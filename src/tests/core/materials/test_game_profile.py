"""Structural game/runtime/texture-API detection regressions."""

import pytest

from core.materials.game_profile import detect_game
from core.ini.analysis import analyze_ini
from core.ini.parser import _scan_sections_for_draws
from core.materials.profiles import material_profile_for
from core.ini.sections import parse_sections
from core.textures.profiles import texture_profile_for


def _classic_gimi_sections():
    return {
        "TextureOverrideComponent01Position": ["vb0 = ResourceComponent01Position"],
        "TextureOverrideComponent01Blend": ["vb1 = ResourceComponent01Blend"],
        "TextureOverrideComponent01Texcoord": ["vb0 = ResourceComponent01Texcoord"],
        "TextureOverrideComponent01": ["ps-t1 = ResourceComponent01Diffuse"],
        "ResourceComponent01Diffuse": ["filename = component01_diffuse.dds"],
    }


def test_wuwa_runtime_and_rabbitfx_api_are_separate():
    detection = detect_game({
        "Constants": [r"global $\WWMIv1\object_guid = 1"],
        r"Resource\RabbitFX\Diffuse": ["filename = diffuse.dds"],
    })
    assert detection.game == "wuwa"
    assert detection.runtime == "wwmi"
    assert detection.texture_api == "rabbitfx"
    assert detection.confidence == "high"


def test_zzz_draw_type_vb2_blend_and_zzmi_texture_namespace():
    detection = detect_game({
        "TextureOverrideComponent01": ["if $DRAW_TYPE == 2", "checktextureoverride = ib"],
        "TextureOverrideComponent01Blend": ["vb2 = ResourceZZMIBlend"],
        r"Resource\ZZMI\Diffuse": ["filename = diffuse.dds"],
    })
    assert (detection.game, detection.runtime, detection.texture_api) == (
        "zzz", "zzmi", "zzmi")
    assert detection.confidence == "high"


@pytest.mark.parametrize(
    ("sections", "expected_api"),
    [
        (_classic_gimi_sections(), "gimi"),
        ({
            "TextureOverrideComponent01": ["ps-t1 = ResourceComponent01Diffuse"],
            "ResourceComponent01Diffuse": ["filename = component01_diffuse.dds"],
        }, "raw"),
        ({
            **_classic_gimi_sections(),
            r"CommandList\RabbitFX\SetTextures": [
                r"Resource\RabbitFX\Diffuse = ref ResourceComponent01Diffuse",
            ],
        }, "rabbitfx"),
        ({
            **_classic_gimi_sections(),
            "TextureOverrideComponent01": ["ps-t1 = ResourceFoo"],
            "ResourceFoo": ["filename = component01_diffuse.dds"],
        }, "raw"),
        ({
            **_classic_gimi_sections(),
            "ResourceComponent01Diffuse": ["format = rgba8"],
        }, "raw"),
    ],
)
def test_classic_gimi_direct_texture_detection_is_conservative(
        sections, expected_api):
    detection = detect_game(sections)

    assert detection.texture_api == expected_api
    if expected_api == "gimi":
        assert (detection.game, detection.runtime) == ("genshin", "gimi")
        assert material_profile_for(detection).id == "genshin:gimi"




def test_rabbitfx_settextures_maps_explicit_roles_case_insensitively():
    sections = {
        "TextureOverrideComponent01": [r"run = commandlist\rabbitfx\settextures"],
        r"CommandList\RabbitFX\SetTextures": [
            r"Resource\RabbitFX\Diffuse = ref ResourceDiffuse",
            r"Resource\RabbitFX\Lightmap = ref ResourceLightmap",
            r"Resource\RabbitFX\Materialmap = ref ResourceMaterialmap",
            r"Resource\RabbitFX\Normalmap = ref ResourceNormalmap",
        ],
    }

    info = _scan_sections_for_draws(sections)["TextureOverrideComponent01"]

    assert info["diffuse"] == "ResourceDiffuse"
    assert info["aux_maps_at_end"]["light_map"]["variants"] == [{
        "res": "ResourceLightmap", "cond": [], "source": "semantic",
    }]
    assert info["aux_maps_at_end"]["normal_map"]["variants"] == [{
        "res": "ResourceNormalmap", "cond": [], "source": "semantic",
    }]
    assert info["aux_maps_at_end"]["material_map"]["variants"] == [{
        "res": "ResourceMaterialmap", "cond": [], "source": "semantic",
    }]


def test_resource_filename_alone_does_not_create_rabbitfx_semantics():
    detection = detect_game({
        "ResourceDefinitelyALightmap": ["filename = face_lightmap.dds"],
    })

    assert detection.game == "unknown"
    assert detection.texture_api == "unknown"




def test_strong_runtime_evidence_beats_conflicting_weak_namespace():
    detection = detect_game({
        "Constants": [r"global $\WWMIv1\object_guid = 1"],
        r"Resource\GIMI\Diffuse": ["filename = diffuse.dds"],
    })
    assert detection.game == "wuwa"
    assert detection.runtime == "wwmi"


def test_srmi_markers_do_not_resolve_ambiguous_draw_type_as_zzz():
    detection = detect_game({
        "Constants": [r"global $namespace = SRMIv1"],
        "TextureOverrideComponent01": [
            "if DRAW_TYPE == 1",
            "vb2 = ResourceComponent01Blend",
            r"Resource\SRMI\PositionBuffer = ref ResourcePosition",
            r"Resource\SRMI\BlendBuffer = ref ResourceBlend",
            r"$\SRMI\vertex_count = 123",
        ],
    })
    assert (detection.game, detection.runtime, detection.texture_api) == (
        "hsr", "srmi", "raw")
    assert detection.confidence == "high"
    assert not any(item.code == "zzz_draw_type_vb2_blend"
                   for item in detection.evidence)


def test_resolved_texcoord_binding_does_not_inherit_blend_parent():
    from core.materials.game_profile import _binding_is_blend

    sections = {
        "TextureOverrideComponent01Blend": [
            "vb1 = ResourceComponent01Texcoord",
            "vb2 = ResourceComponent01Blend",
        ],
        "ResourceComponent01Texcoord": ["filename = texcoord.buf"],
        "ResourceComponent01Blend": ["filename = blend.buf"],
    }
    assert not _binding_is_blend(
        "TextureOverrideComponent01Blend", "ResourceComponent01Texcoord", sections)
    assert _binding_is_blend(
        "TextureOverrideComponent01Blend", "ResourceComponent01Blend", sections)






def test_wuwa_profile_uses_intact_normal_data_for_normal_transport():
    profile = texture_profile_for("wuwa")
    assert not profile.bind_normal_map
    assert profile.normal_transport_role == "normal_data"

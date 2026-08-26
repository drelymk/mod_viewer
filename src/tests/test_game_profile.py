"""Structural game/runtime/texture-API detection regressions."""

from core.materials.game_profile import detect_game
from core.ini.analysis import analyze_ini
from core.ini.parser import _scan_sections_for_draws
from core.ini.sections import parse_sections
from core.textures.profiles import texture_profile_for


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
        "TextureOverrideBody": ["if $DRAW_TYPE == 2", "checktextureoverride = ib"],
        "TextureOverrideBodyBlend": ["vb2 = ResourceZZMIBlend"],
        r"Resource\ZZMI\Diffuse": ["filename = diffuse.dds"],
    })
    assert (detection.game, detection.runtime, detection.texture_api) == (
        "zzz", "zzmi", "zzmi")
    assert detection.confidence == "high"




def test_rabbitfx_settextures_maps_explicit_roles_case_insensitively():
    sections = {
        "TextureOverrideBody": [r"run = commandlist\rabbitfx\settextures"],
        r"CommandList\RabbitFX\SetTextures": [
            r"Resource\RabbitFX\Diffuse = ref ResourceDiffuse",
            r"Resource\RabbitFX\Lightmap = ref ResourceLightmap",
            r"Resource\RabbitFX\Materialmap = ref ResourceMaterialmap",
            r"Resource\RabbitFX\Normalmap = ref ResourceNormalmap",
        ],
    }

    info = _scan_sections_for_draws(sections)["TextureOverrideBody"]

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
        "TextureOverrideBody": [
            "if DRAW_TYPE == 1",
            "vb2 = ResourceBodyBlend",
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
        "TextureOverrideBodyBlend": [
            "vb1 = ResourceBodyTexcoord",
            "vb2 = ResourceBodyBlend",
        ],
        "ResourceBodyTexcoord": ["filename = texcoord.buf"],
        "ResourceBodyBlend": ["filename = blend.buf"],
    }
    assert not _binding_is_blend(
        "TextureOverrideBodyBlend", "ResourceBodyTexcoord", sections)
    assert _binding_is_blend(
        "TextureOverrideBodyBlend", "ResourceBodyBlend", sections)






def test_wuwa_profile_uses_intact_normal_data_for_normal_transport():
    profile = texture_profile_for("wuwa")
    assert profile.recipe_for("normal_map") == "passthrough"
    assert profile.recipe_for("normal_data") == "passthrough"
    assert not profile.bind_normal_map
    assert profile.normal_transport_role == "normal_data"

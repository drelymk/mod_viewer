"""Structural game/runtime/texture-API detection regressions."""

from core.game_profile import detect_game
from core.ini_analysis import analyze_ini
from core.ini_parser import _scan_sections_for_draws
from core.ini_sections import parse_sections
from core.texture_profiles import texture_profile_for


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


def test_classic_gimi_routing_and_settextures_detect_genshin():
    detection = detect_game({
        "TextureOverrideBodyPosition": ["vb0 = ResourcePosition"],
        "TextureOverrideBodyBlend": ["vb1 = ResourceBlend"],
        "TextureOverrideBodyTexcoord": ["vb1 = ResourceTexcoord"],
        r"CommandList\GIMI\SetTextures": ["ps-t0 = ResourceDiffuse"],
    })
    assert (detection.game, detection.runtime, detection.texture_api) == (
        "genshin", "gimi", "gimi")


def test_realistic_gimi_statements_detect_texture_api():
    detection = detect_game({
        "TextureOverrideBody": [
            r"Resource\GIMI\Diffuse = ref ResourceBodyDiffuse",
            r"run = CommandList\GIMI\SetTextures",
        ],
    })
    assert (detection.game, detection.runtime, detection.texture_api) == (
        "genshin", "gimi", "gimi")
    assert any(item.code == "gimi_resource_assignment"
               for item in detection.evidence)
    assert any(item.code == "gimi_settextures"
               for item in detection.evidence)


def test_realistic_zzmi_statements_detect_zzz():
    detection = detect_game({
        "TextureOverrideBody": [
            "if $DRAW_TYPE == 2",
            "checktextureoverride = ib",
            r"Resource\ZZMI\Diffuse = ref ResourceBodyDiffuse",
            r"run = CommandList\ZZMI\SetTextures",
        ],
        "TextureOverrideBodyBlend": ["vb2 = ResourceBodyBlend"],
    })
    assert (detection.game, detection.runtime, detection.texture_api) == (
        "zzz", "zzmi", "zzmi")


def test_realistic_rabbitfx_statements_stay_separate_from_wwmi_game():
    detection = detect_game({
        "Constants": [r"global $\WWMIv1\object_guid = 1"],
        "TextureOverrideBody": [
            r"Resource\RabbitFX\Diffuse = ref ResourceBodyDiffuse",
            r"run = CommandList\RabbitFX\SetTextures",
        ],
    })
    assert (detection.game, detection.runtime, detection.texture_api) == (
        "wuwa", "wwmi", "rabbitfx")
    assert any(item.code == "rabbitfx_resource_assignment"
               for item in detection.evidence)


def test_rabbitfx_settextures_maps_explicit_roles_case_insensitively():
    sections = {
        "TextureOverrideBody": [r"run = commandlist\rabbitfx\settextures"],
        r"CommandList\RabbitFX\SetTextures": [
            r"Resource\RabbitFX\Diffuse = ref ResourceDiffuse",
            r"Resource\RabbitFX\Lightmap = ref ResourceLightmap",
            r"Resource\RabbitFX\Normalmap = ref ResourceNormalmap",
        ],
    }

    info = _scan_sections_for_draws(sections)["TextureOverrideBody"]

    assert info["diffuse"] == "ResourceDiffuse"
    assert info["aux_maps_at_end"]["light_map"]["variants"] == [{
        "res": "ResourceLightmap", "cond": [],
    }]
    assert info["aux_maps_at_end"]["normal_map"]["variants"] == [{
        "res": "ResourceNormalmap", "cond": [],
    }]


def test_resource_filename_alone_does_not_create_rabbitfx_semantics():
    detection = detect_game({
        "ResourceDefinitelyALightmap": ["filename = face_lightmap.dds"],
    })

    assert detection.game == "unknown"
    assert detection.texture_api == "unknown"


def test_namespaces_alone_do_not_force_a_game():
    rabbitfx = detect_game({
        r"Resource\RabbitFX\Diffuse": ["filename = diffuse.dds"],
    })
    gimi = detect_game({
        r"Resource\GIMI\Diffuse": ["filename = diffuse.dds"],
    })
    assert rabbitfx.game == "unknown"
    assert rabbitfx.texture_api == "rabbitfx"
    assert gimi.game == "unknown"


def test_comments_and_filenames_are_not_detection_evidence(tmp_path):
    path = tmp_path / "Genshin_WWMI_comment.ini"
    path.write_text(
        "; required_wwmi_version = 1\n"
        "[Resource\\GIMI\\Diffuse]\n"
        "filename = required_wwmi_version_WWMI.dds\n",
        encoding="utf-8",
    )
    detection = detect_game(parse_sections(str(path)))
    assert detection.game == "unknown"


def test_conflicting_weak_namespaces_remain_unknown():
    detection = detect_game({
        r"Resource\GIMI\Diffuse": ["filename = gimi.dds"],
        r"Resource\ZZMI\Diffuse": ["filename = zzmi.dds"],
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
    from core.game_profile import _binding_is_blend

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


def test_texture_profile_for_hsr_is_conservative():
    profile = texture_profile_for("hsr")
    assert profile.recipe_for("normal_map") == "passthrough"
    assert profile.normal_y_sign == 1
    assert not profile.bind_normal_map


def test_semantic_analysis_carries_detection_evidence_in_one_pass():
    analysis = analyze_ini({
        "TextureOverrideBody": [
            "if $DRAW_TYPE == 2",
            "checktextureoverride = ib",
        ],
        "TextureOverrideBodyBlend": ["vb2 = ResourceZZMIBlend"],
        r"Resource\ZZMI\Diffuse": ["filename = diffuse.dds"],
    })
    assert any(item.code == "zzz_draw_type_vb2_blend"
               for item in analysis.game_evidence)
    assert any(item.code == "zzmi_vb2_blend_binding"
               for item in analysis.runtime_evidence)


def test_texture_profiles_keep_auxiliary_maps_packed():
    profile = texture_profile_for("zzz")
    assert profile.recipe_for("normal_map") == "normal_xy_reconstruct"
    assert profile.recipe_for("light_map") == "passthrough"
    assert profile.recipe_for("material_map") == "passthrough"
    assert not profile.bind_light_map
    assert not profile.bind_material_map
    assert profile.normal_y_sign == -1


def test_wuwa_profile_retains_raw_normal_data_for_future_material_shaders():
    profile = texture_profile_for("wuwa")
    assert profile.recipe_for("normal_map") == "normal_xy_reconstruct"
    assert profile.recipe_for("normal_data") == "passthrough"
    assert profile.retain_normal_data

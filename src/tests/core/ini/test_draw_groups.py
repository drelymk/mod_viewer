"""Resolved draw-group assembly regressions."""

import pytest

from core.ini.draw_groups import build_draw_groups
from core.ini.draw_scan import _scan_sections_for_draws
from core.ini.sections import extract_resources, parse_sections


def test_draw_scanner_keeps_geometry_and_texture_hash_evidence_separate():
    sections = parse_sections("fixture.ini", text="""[TextureOverrideBody]
hash = 0x73c8cae2
match_first_index = 43845
match_index_count = 24
Resource\\GIMI\\Diffuse = ResourceDiffuseOpaque
ps-t1 = ResourceMystery
drawindexed = 3, 0, 0

[TextureOverrideDiffuse]
hash = 11111111
this = ResourceDiffuseOpaque

[TextureOverrideMystery]
hash = 22222222
this = ResourceMystery
""")

    draw = _scan_sections_for_draws(sections)["TextureOverrideBody"][
        "draws"][0]

    assert (draw.geometry_match.hash,
            draw.geometry_match.first_index,
            draw.geometry_match.index_count) == ("73c8cae2", 43845, 24)
    assert [(item.slot, item.resource, item.texture_hashes)
            for item in draw.slot_textures] == [
                (1, "ResourceMystery", ("22222222",))]
    assert draw.diffuse_variants[0]["texture_hashes"] == ("11111111",)


def test_draw_scanner_does_not_infer_texture_hash_from_resource_name():
    sections = parse_sections("fixture.ini", text="""[TextureOverrideBody]
hash = 73c8cae2
Resource\\GIMI\\Diffuse = ResourceFoo_11111111
drawindexed = 3, 0, 0
""")

    draw = _scan_sections_for_draws(sections)["TextureOverrideBody"][
        "draws"][0]

    assert draw.slot_textures == []
    assert "texture_hashes" not in draw.diffuse_variants[0]


def test_texture_override_index_preserves_conditions_and_resolves_files():
    sections = parse_sections("fixture.ini", text="""[KeyStyle]
type = cycle
$Style = 0,1

[TextureOverrideBody]
vb0 = ResourcePosition
vb1 = ResourceTexcoord
ib = ResourceBodyIB
drawindexed = 3, 0, 0

[TextureOverrideOriginal]
hash = 11111111
if $Style == 0
this = ResourceA
else
this = ResourceB
endif

[ResourcePosition]
filename = position.buf
stride = 40

[ResourceTexcoord]
filename = texcoord.buf
stride = 20

[ResourceBodyIB]
filename = body.ib
format = DXGI_FORMAT_R32_UINT

[ResourceA]
filename = textures/a.dds

[ResourceB]
filename = textures/b.dds
""")
    scanned = _scan_sections_for_draws(sections)
    replacements = scanned.texture_override_index.replacements_by_hash[
        "11111111"]

    assert [(item.resource, item.dnf) for item in replacements] == [
        ("ResourceA", [[{
            "var": "Style", "value": "0", "negate": False}]]),
        ("ResourceB", [[{
            "var": "Style", "value": "0", "negate": True}]])]

    group = build_draw_groups(sections, extract_resources(sections))[0]
    resolved = group["_texture_override_index"].replacements_by_hash[
        "11111111"]
    assert [item.file for item in resolved] == [
        "textures/a.dds", "textures/b.dds"]


def test_animation_binding_keeps_already_resolved_qualified_reads():
    sections = parse_sections("fixture.ini", text="""[TextureOverrideBody]
if $swapvar == 1 && $\\JaneDoe_AIO\\Master\\swapvar == 1
vb0 = ResourcePosition
endif
drawindexed = 3, 0, 0
""")
    scanned = _scan_sections_for_draws(
        sections,
        var_prefix="Consumer::",
        animation_vars={"swapvar"},
        qualified_vars={
            r"\janedoe_aio\master\swapvar": "Master::swapvar",
        })

    binding = scanned["TextureOverrideBody"][
        "animation_vertex_bindings"][0]
    assert binding["conditions"] == [[{
        "var": "Master::swapvar", "value": "1", "negate": False,
    }]]
    assert binding["animation_conditions"] == [[{
        "var": "Consumer::swapvar", "value": "1", "negate": False,
    }]]


def test_texture_override_index_resolves_qualified_conditions():
    sections = parse_sections("fixture.ini", text="""[TextureOverrideOriginal]
hash = 11111111
if $\\JaneDoe_AIO\\Master\\swapvar == 1
this = ResourceA
endif

[ResourceA]
filename = textures/a.dds
""")
    scanned = _scan_sections_for_draws(
        sections,
        qualified_vars={
            r"\janedoe_aio\master\swapvar": "Master::swapvar",
        })
    replacement = scanned.texture_override_index.replacements_by_hash[
        "11111111"][0]
    assert replacement.dnf == [[{
        "var": "Master::swapvar", "value": "1", "negate": False,
    }]]


def test_draw_groups_keep_clean_display_names_with_shared_seen_labels():
    sections = parse_sections("sample.ini", text="""[TextureOverrideBody]
ib = ResourceBodyIB
vb0 = ResourceBodyPosition
vb1 = ResourceBodyTexcoord
drawindexed = 3, 0, 0

[ResourceBodyIB]
filename = body.ib
format = DXGI_FORMAT_R32_UINT

[ResourceBodyPosition]
filename = body-position.buf
stride = 12

[ResourceBodyTexcoord]
filename = body-texcoord.buf
stride = 8
""")
    resources = extract_resources(sections)
    seen = {}

    first = build_draw_groups(sections, resources, seen=seen)[0]
    second = build_draw_groups(sections, resources, seen=seen)[0]

    assert (first["name"], first["display_name"],
            second["name"], second["display_name"]) == (
        "Body", "Body", "Body_2", "Body")


def test_per_draw_ib_switch_uses_hash_geometry_when_component_is_unknown():
    sections = parse_sections("sample.ini", text="""
[TextureOverride_98ece166_Position]
vb0 = Resource98ece166Position

[TextureOverride_98ece166_Texcoord]
vb1 = Resource98ece166Texcoord

[TextureOverride_d942b3a7_Position]
vb0 = Resourced942b3a7Position

[TextureOverride_d942b3a7_Texcoord]
vb1 = Resourced942b3a7Texcoord

[TextureOverride_LOD0.98ece166_26946_0]
ib = Resource_LOD0.98ece166_26946_0_Index
drawindexed = 3, 0, 0
ib = Resource_LOD0.d942b3a7_39828_0_Index
vb0 = ResourceBodyVB_d942b3a7_0
vb1 = Resourced942b3a7Texcoord
vb2 = Resourced942b3a7Blend
drawindexed = 3, 0, 0

[Resource_LOD0.98ece166_26946_0_Index]
filename = 98ece166-index.buf
format = DXGI_FORMAT_R32_UINT

[Resource_LOD0.d942b3a7_39828_0_Index]
filename = d942b3a7-index.buf
format = DXGI_FORMAT_R32_UINT

[Resource98ece166Position]
filename = 98ece166-position.buf
stride = 40

[Resource98ece166Texcoord]
filename = 98ece166-texcoord.buf
stride = 20

[Resourced942b3a7Position]
filename = d942b3a7-position.buf
stride = 40

[Resourced942b3a7Texcoord]
filename = d942b3a7-texcoord.buf
stride = 20

[ResourceBodyVB_d942b3a7_0]
stride = 40

[Resourced942b3a7Blend]
filename = d942b3a7-blend.buf
stride = 32
""")
    groups = build_draw_groups(sections, extract_resources(sections))
    draws = groups[0]["draws"]

    assert [(draw.ib_file, draw.position_file, draw.texcoord_file)
            for draw in draws] == [
                ("98ece166-index.buf", "98ece166-position.buf",
                 "98ece166-texcoord.buf"),
                ("d942b3a7-index.buf", "d942b3a7-position.buf",
                 "d942b3a7-texcoord.buf"),
            ]


def test_per_draw_ib_switch_prefers_component_geometry_over_hash_fallback():
    sections = parse_sections("sample.ini", text="""
[TextureOverrideBody]
ib = ResourceBodyIB
drawindexed = 3, 0, 0
ib = ResourceAltBodyIB
drawindexed = 3, 0, 0

[TextureOverrideBodyPosition]
vb0 = ResourceBodyPosition

[TextureOverrideBodyTexcoord]
vb1 = ResourceBodyTexcoord

[TextureOverrideAltBodyPosition]
vb0 = ResourceAltBodyPosition

[TextureOverrideAltBodyTexcoord]
vb1 = ResourceAltBodyTexcoord

[ResourceBodyIB]
filename = body-index.buf
format = DXGI_FORMAT_R32_UINT

[ResourceAltBodyIB]
filename = alt-body-index.buf
format = DXGI_FORMAT_R32_UINT

[ResourceBodyPosition]
filename = body-position.buf
stride = 40

[ResourceBodyTexcoord]
filename = body-texcoord.buf
stride = 20

[ResourceAltBodyPosition]
filename = alt-body-position.buf
stride = 40

[ResourceAltBodyTexcoord]
filename = alt-body-texcoord.buf
stride = 20
""")
    groups = build_draw_groups(sections, extract_resources(sections))
    draws = groups[0]["draws"]

    assert [(draw.ib_file, draw.position_file, draw.texcoord_file)
            for draw in draws] == [
                ("body-index.buf", "body-position.buf", "body-texcoord.buf"),
                ("alt-body-index.buf", "alt-body-position.buf",
                 "alt-body-texcoord.buf"),
            ]


def test_draw_groups_resolve_the_scanner_snapshot_for_inline_execution():
    sections = parse_sections("sample.ini", text="""[TextureOverrideBody]
ib = ResourceBodyIB
vb0 = ResourceBodyPosition
vb1 = ResourceBodyTexcoord
hash = 0123abcd
match_first_index = 9
match_index_count = 3
if $mode == 1
run = CommandListBody
endif
drawindexed = 3, 3, 0

[CommandListBody]
vb0 = ResourceBodyPositionAlt
Resource\\GIMI\\Diffuse = ResourceBodyDiffuse
Resource\\GIMI\\LightMap = ResourceBodyLightMap
drawindexed = 3, 0, 0

[ResourceBodyIB]
filename = body.ib
format = DXGI_FORMAT_R32_UINT

[ResourceBodyPosition]
filename = body-position.buf
stride = 12

[ResourceBodyPositionAlt]
filename = body-position-alt.buf
stride = 12

[ResourceBodyTexcoord]
filename = body-texcoord.buf
stride = 8

[ResourceBodyDiffuse]
filename = body-diffuse.dds

[ResourceBodyLightMap]
filename = body-light-map.dds
""")
    scanned = _scan_sections_for_draws(sections, gating_vars={"mode"})
    authored = scanned["TextureOverrideBody"]["draws"]
    groups = build_draw_groups(
        sections, extract_resources(sections), gating_vars={"mode"})
    draws = groups[0]["draws"]

    assert [(item.start, item.vertex_resources[0]) for item in authored] == [
        (0, "ResourceBodyPositionAlt"),
        (3, "ResourceBodyPositionAlt")]
    assert [(item.start, item.position_file) for item in draws] == [
        (0, "body-position-alt.buf"),
        (3, "body-position-alt.buf")]
    assert draws[0].texture_default("diffuse") == "body-diffuse.dds"
    assert draws[0].texture_default("light_map") is None
    assert draws[0].texture_rules("light_map") == [{
        "conditions": [[{"var": "mode", "value": "1", "negate": False}]],
        "file": "body-light-map.dds",
    }]
    assert (draws[0].geometry_match.hash,
            draws[0].geometry_match.first_index,
            draws[0].geometry_match.index_count) == ("0123abcd", 9, 3)


def test_rabbitfx_glow_map_flows_through_the_draw_scanner():
    sections = parse_sections("sample.ini", text=r"""[TextureOverrideBody]
ib = ResourceBodyIB
vb0 = ResourceBodyPosition
vb1 = ResourceBodyTexcoord
Resource\RabbitFX\Diffuse = ref ResourceBodyDiffuse
Resource\RabbitFX\GlowMap = ref ResourceBodyGlow
drawindexed = 3, 0, 0

[ResourceBodyIB]
filename = body.ib
format = DXGI_FORMAT_R32_UINT

[ResourceBodyPosition]
filename = body-position.buf
stride = 12

[ResourceBodyTexcoord]
filename = body-texcoord.buf
stride = 8

[ResourceBodyDiffuse]
filename = body-diffuse.dds

[ResourceBodyGlow]
filename = body-glow.dds
""")
    groups = build_draw_groups(sections, extract_resources(sections))

    assert groups[0]["draws"][0].texture_default("emission_map") == (
        "body-glow.dds")


def test_non_rabbitfx_glow_map_is_not_recorded_as_emission():
    sections = parse_sections("sample.ini", text=r"""[TextureOverrideBody]
ib = ResourceBodyIB
vb0 = ResourceBodyPosition
vb1 = ResourceBodyTexcoord
Resource\GIMI\Diffuse = ref ResourceBodyDiffuse
Resource\GIMI\GlowMap = ref ResourceBodyGlow
drawindexed = 3, 0, 0

[ResourceBodyIB]
filename = body.ib
format = DXGI_FORMAT_R32_UINT

[ResourceBodyPosition]
filename = body-position.buf
stride = 12

[ResourceBodyTexcoord]
filename = body-texcoord.buf
stride = 8

[ResourceBodyDiffuse]
filename = body-diffuse.dds

[ResourceBodyGlow]
filename = body-glow.dds
""")
    groups = build_draw_groups(sections, extract_resources(sections))

    assert groups[0]["draws"][0].texture_default("emission_map") is None


def test_draw_groups_preserve_inline_run_snapshots_without_buffer_files():
    sections = parse_sections("sample.ini", text="""[TextureOverrideBody]
ib = ResourceMissingIB
vb0 = ResourceMissingPosition
run = CommandListBody
drawindexed = 3, 3, 0

[CommandListBody]
vb1 = ResourceMissingTexcoord
drawindexed = 3, 0, 0
""")

    body = _scan_sections_for_draws(sections)["TextureOverrideBody"]
    assert [(draw.start, draw.index_resource) for draw in body["draws"]] == [
        (0, "ResourceMissingIB"), (3, "ResourceMissingIB")]
    assert body["draws"][0].vertex_resources == {
        0: "ResourceMissingPosition", 1: "ResourceMissingTexcoord"}


def test_draw_groups_resolve_blend_from_sibling_component_snapshot():
    sections = parse_sections("sample.ini", text="""[TextureOverrideBodyPosition]
vb0 = ResourceBodyPosition

[TextureOverrideBodyTexcoord]
vb1 = ResourceBodyTexcoord

[TextureOverrideBodyBlend]
handling = skip
vb1 = ResourceBodyBlend

[TextureOverrideBody]
ib = ResourceBodyIB
drawindexed = 3, 0, 0

[ResourceBodyIB]
filename = body.ib
format = DXGI_FORMAT_R32_UINT

[ResourceBodyPosition]
filename = body-position.buf
stride = 40

[ResourceBodyBlend]
filename = body-blend.buf
stride = 32

[ResourceBodyTexcoord]
filename = body-texcoord.buf
stride = 20
""")

    groups = build_draw_groups(sections, extract_resources(sections))

    draw = groups[0]["draws"][0]
    assert draw.skinning_error is None
    assert draw.skinning_source.file == "body-blend.buf"
    assert draw.skinning_source.encoding == "gimi_f32_u32_4"


def test_draw_groups_resolve_wwmi_component_bone_offset():
    sections = parse_sections("sample.ini", text=r"""[TextureOverrideComponent3]
$\WWMIv1\vg_offset = 24
ib = ResourceBodyIB
vb0 = ResourceBodyPosition
vb1 = ResourceBodyBlend
vb2 = ResourceBodyTexcoord
cs-t35 = ref ResourceVertexVG
drawindexed = 3, 0, 0

[ResourceBodyIB]
filename = body.ib
format = DXGI_FORMAT_R32_UINT

[ResourceBodyPosition]
filename = body-position.buf
stride = 40

[ResourceBodyBlend]
filename = body-blend.buf
stride = 8
format = DXGI_FORMAT_R8_UINT

[ResourceBodyTexcoord]
filename = body-texcoord.buf
stride = 20

[ResourceVertexVG]
filename = body-vertex-vg.buf
format = DXGI_FORMAT_R16_UINT
stride = 8
""")

    draw = build_draw_groups(
        sections, extract_resources(sections))[0]["draws"][0]

    assert draw.skinning_bone_offset == 24
    assert draw.skinning_source.bone_id_offset == 24
    assert draw.skinning_source.vertex_vg_file == "body-vertex-vg.buf"


def test_draw_groups_treat_r16_wwmi_ids_as_model_wide_without_remap():
    sections = parse_sections("sample.ini", text=r"""[TextureOverrideComponent3]
ib = ResourceBodyIB
vb0 = ResourceBodyPosition
vb1 = ResourceBodyBlend
vb2 = ResourceBodyTexcoord
drawindexed = 3, 0, 0

[ResourceBodyIB]
filename = body.ib
format = DXGI_FORMAT_R32_UINT

[ResourceBodyPosition]
filename = body-position.buf
stride = 40

[ResourceBodyBlend]
filename = Meshes/Blend_R16.buf
format = DXGI_FORMAT_R16_UINT
stride = 32

[ResourceBodyTexcoord]
filename = body-texcoord.buf
stride = 20

[ResourceBlendRemapVertexVGBuffer]
filename = Meshes/BlendRemapVertexVG.buf
format = DXGI_FORMAT_R16_UINT
stride = 16
""")

    draw = build_draw_groups(
        sections, extract_resources(sections))[0]["draws"][0]

    assert draw.skinning_error is None
    assert draw.skinning_source.encoding == "wwmi_u16_8"
    assert draw.skinning_source.vertex_vg_file is None
    assert draw.skinning_source.bone_id_namespace == "model"


def test_draw_groups_associate_unbound_vertex_vg_with_each_blend_family():
    sections = parse_sections("sample.ini", text=r"""[TextureOverrideBody]
ib = ResourceBodyIB
vb0 = ResourceBodyPosition
vb1 = ResourceBodyBlend
vb2 = ResourceBodyTexcoord
drawindexed = 3, 0, 0

[TextureOverrideHair]
ib = ResourceHairIB
vb0 = ResourceHairPosition
vb1 = ResourceHairBlend
vb2 = ResourceHairTexcoord
drawindexed = 3, 0, 0

[ResourceBodyIB]
filename = body.ib
format = DXGI_FORMAT_R32_UINT

[ResourceHairIB]
filename = hair.ib
format = DXGI_FORMAT_R32_UINT

[ResourceBodyPosition]
filename = body-position.buf
stride = 40

[ResourceBodyTexcoord]
filename = body-texcoord.buf
stride = 20

[ResourceHairPosition]
filename = hair-position.buf
stride = 40

[ResourceHairTexcoord]
filename = hair-texcoord.buf
stride = 20

[ResourceBodyBlend]
filename = Meshes/BodyBlend.buf
format = DXGI_FORMAT_R8_UINT
stride = 16

[ResourceHairBlend]
filename = Meshes/HairBlend.buf
format = DXGI_FORMAT_R8_UINT
stride = 16

[ResourceBodyBlendRemapVertexVG]
filename = Meshes/BodyBlendRemapVertexVG.buf
format = DXGI_FORMAT_R16_UINT
stride = 16

[ResourceHairBlendRemapVertexVG]
filename = Meshes/HairBlendRemapVertexVG.buf
format = DXGI_FORMAT_R16_UINT
stride = 16
""")

    groups = build_draw_groups(sections, extract_resources(sections))
    sources = {
        group["name"]: group["draws"][0].skinning_source
        for group in groups
    }

    assert sources["Body"].vertex_vg_file == \
        "Meshes/BodyBlendRemapVertexVG.buf"
    assert sources["Hair"].vertex_vg_file == \
        "Meshes/HairBlendRemapVertexVG.buf"


def test_draw_groups_reject_multiple_associated_vertex_vg_candidates():
    sections = parse_sections("sample.ini", text=r"""[TextureOverrideBody]
ib = ResourceBodyIB
vb0 = ResourceBodyPosition
vb1 = ResourceBodyBlend
vb2 = ResourceBodyTexcoord
drawindexed = 3, 0, 0

[ResourceBodyIB]
filename = body.ib
format = DXGI_FORMAT_R32_UINT

[ResourceBodyPosition]
filename = body-position.buf
stride = 40

[ResourceBodyTexcoord]
filename = body-texcoord.buf
stride = 20

[ResourceBodyBlend]
filename = Meshes/BodyBlend.buf
format = DXGI_FORMAT_R8_UINT
stride = 16

[ResourceBodyBlendRemapVertexVG]
filename = Meshes/BodyBlendRemapVertexVG.buf
format = DXGI_FORMAT_R16_UINT
stride = 16

[ResourceBodyBlendRemapVertexVGAlt]
filename = Meshes/BodyBlendRemapVertexVGAlt.buf
format = DXGI_FORMAT_R16_UINT
stride = 16
""")

    draw = build_draw_groups(
        sections, extract_resources(sections))[0]["draws"][0]

    assert draw.skinning_error is None
    assert draw.skinning_source.vertex_vg_file is None
    assert draw.skinning_source.bone_id_namespace == "model"


def test_draw_groups_do_not_attach_unrelated_single_vertex_vg_resource():
    sections = parse_sections("sample.ini", text=r"""[TextureOverrideBody]
ib = ResourceBodyIB
vb0 = ResourceBodyPosition
vb1 = ResourceBodyBlend
vb2 = ResourceBodyTexcoord
drawindexed = 3, 0, 0

[ResourceBodyIB]
filename = body.ib
format = DXGI_FORMAT_R32_UINT

[ResourceBodyPosition]
filename = body-position.buf
stride = 40

[ResourceBodyTexcoord]
filename = body-texcoord.buf
stride = 20

[ResourceBodyBlend]
filename = Meshes/BodyBlend.buf
format = DXGI_FORMAT_R8_UINT
stride = 16

[ResourceAccessoryBlendRemapVertexVG]
filename = Meshes/AccessoryBlendRemapVertexVG.buf
format = DXGI_FORMAT_R16_UINT
stride = 16
""")

    draw = build_draw_groups(
        sections, extract_resources(sections))[0]["draws"][0]

    assert draw.skinning_source.vertex_vg_file is None
    assert draw.skinning_source.bone_id_namespace == "model"


@pytest.mark.parametrize(
    ("blend_slot", "expect_source"),
    [(1, False), (4, True)],
    ids=["same-slot-is-blocked", "different-slot-remains-available"],
)
def test_draw_groups_sibling_blend_fallback_respects_authored_slots(
        blend_slot, expect_source):
    sections = parse_sections("sample.ini", text=f"""[TextureOverrideBodyPosition]
vb0 = ResourceBodyPosition

[TextureOverrideBodyBlend]
handling = skip
vb{blend_slot} = ResourceBodyBlend

[TextureOverrideBody]
ib = ResourceBodyIB
vb1 = ResourceBodyTexcoord
drawindexed = 3, 0, 0

[ResourceBodyIB]
filename = body.ib
format = DXGI_FORMAT_R32_UINT

[ResourceBodyPosition]
filename = body-position.buf
stride = 40

[ResourceBodyBlend]
filename = body-blend.buf
stride = 32

[ResourceBodyTexcoord]
filename = body-texcoord.buf
stride = 20
""")

    draw = build_draw_groups(
        sections, extract_resources(sections))[0]["draws"][0]

    assert draw.skinning_error is None
    if expect_source:
        assert draw.skinning_source.file == "body-blend.buf"
    else:
        assert draw.skinning_source is None


def test_draw_groups_resolve_blend_from_position_provenance_without_hashes():
    sections = parse_sections("sample.ini", text="""
[TextureOverridePotato]
hash = 11111111
vb0 = ResourceAlpha
vb7 = ResourceAuthoredBlendStream

[TextureOverrideBanana]
hash = 22222222
ib = ResourceIndex
vb0 = ResourceAlpha
vb1 = ResourceTexcoord
drawindexed = 3, 0, 0

[ResourceIndex]
filename = index.buf
format = DXGI_FORMAT_R32_UINT

[ResourceAlpha]
filename = position.buf
stride = 40

[ResourceTexcoord]
filename = texcoord.buf
stride = 20

[ResourceAuthoredBlendStream]
filename = skin-data.bin
stride = 32
""")

    draw = build_draw_groups(
        sections, extract_resources(sections))[0]["draws"][0]

    assert draw.skinning_error is None
    assert draw.skinning_source.file == "skin-data.bin"
    assert draw.skinning_resolution["resolution_source"] == \
        "position_provenance"


def test_draw_groups_position_provenance_does_not_merge_draw_snapshots():
    sections = parse_sections("sample.ini", text="""
[TextureOverrideSource]
ib = ResourceSourceIndex
vb0 = ResourcePosition
vb1 = ResourceTexcoord
vb4 = ResourceSourceBlend
drawindexed = 3, 0, 0
vb4 = ResourceOtherBlend
drawindexed = 3, 3, 0

[TextureOverrideBody]
ib = ResourceBodyIndex
vb0 = ResourcePosition
vb1 = ResourceTexcoord
drawindexed = 3, 0, 0

[ResourceSourceIndex]
filename = source.ib
format = DXGI_FORMAT_R32_UINT

[ResourceBodyIndex]
filename = body.ib
format = DXGI_FORMAT_R32_UINT

[ResourcePosition]
filename = position.buf
stride = 40

[ResourceTexcoord]
filename = texcoord.buf
stride = 20

[ResourceSourceBlend]
filename = source-a.blend
stride = 32

[ResourceOtherBlend]
filename = source-b.blend
stride = 32
""")

    groups = build_draw_groups(
        sections, extract_resources(sections))
    body_draw = next(
        draw for group in groups if group["name"] == "Body"
        for draw in group["draws"])

    assert body_draw.skinning_source is None
    assert body_draw.skinning_error is None


def test_draw_groups_rejects_unlabeled_authored_weight_provenance():
    sections = parse_sections("sample.ini", text="""
[TextureOverridePosition]
vb0 = ResourcePosition
vb7 = ResourceColorData

[TextureOverrideBody]
ib = ResourceBodyIndex
vb0 = ResourcePosition
vb1 = ResourceTexcoord
drawindexed = 3, 0, 0

[ResourceBodyIndex]
filename = body.ib
format = DXGI_FORMAT_R32_UINT

[ResourcePosition]
filename = position.buf
stride = 40

[ResourceTexcoord]
filename = texcoord.buf
stride = 20

[ResourceColorData]
filename = color.bin
stride = 32
""")

    draw = build_draw_groups(
        sections, extract_resources(sections))[0]["draws"][0]

    assert draw.skinning_error is None
    assert draw.skinning_source is None
    assert draw.skinning_resolution["resolution_source"] is None


def test_draw_groups_position_provenance_respects_actual_draw_slots():
    sections = parse_sections("sample.ini", text="""
[TextureOverridePosition]
vb0 = ResourcePosition
vb2 = ResourceFallbackBlend

[TextureOverrideBody]
ib = ResourceBodyIndex
vb0 = ResourcePosition
vb1 = ResourceTexcoord
vb2 = ResourceOtherStream
drawindexed = 3, 0, 0

[ResourceBodyIndex]
filename = body.ib
format = DXGI_FORMAT_R32_UINT

[ResourcePosition]
filename = position.buf
stride = 40

[ResourceTexcoord]
filename = texcoord.buf
stride = 20

[ResourceFallbackBlend]
filename = fallback.blend
stride = 32

[ResourceOtherStream]
filename = color.bin
stride = 32
""")

    draw = build_draw_groups(
        sections, extract_resources(sections))[0]["draws"][0]

    assert draw.skinning_source is None
    assert draw.skinning_error is None


def test_draw_groups_position_provenance_rejects_unrelated_draw_scope():
    sections = parse_sections("sample.ini", text="""
[TextureOverrideOtherDraw]
ib = ResourceOtherIndex
vb0 = ResourcePosition
run = CommandListOther
drawindexed = 3, 0, 0

[CommandListOther]
vb4 = ResourceOtherBlend

[TextureOverrideBody]
ib = ResourceBodyIndex
vb0 = ResourcePosition
vb1 = ResourceTexcoord
drawindexed = 3, 0, 0

[ResourceOtherIndex]
filename = other.ib
format = DXGI_FORMAT_R32_UINT

[ResourceBodyIndex]
filename = body.ib
format = DXGI_FORMAT_R32_UINT

[ResourcePosition]
filename = position.buf
stride = 40

[ResourceTexcoord]
filename = texcoord.buf
stride = 20

[ResourceOtherBlend]
filename = other.blend
stride = 32
""")

    groups = build_draw_groups(
        sections, extract_resources(sections))
    body_draw = next(
        draw for group in groups if group["name"] == "Body"
        for draw in group["draws"])

    assert body_draw.skinning_source is None
    assert body_draw.skinning_error is None


def test_draw_groups_position_provenance_rejects_conditional_bindings():
    sections = parse_sections("sample.ini", text="""
[TextureOverridePosition]
vb0 = ResourcePosition
if $mode
    vb2 = ResourceBlendA
else
    vb2 = ResourceBlendB
endif

[TextureOverrideBody]
ib = ResourceBodyIndex
vb0 = ResourcePosition
vb1 = ResourceTexcoord
drawindexed = 3, 0, 0

[ResourceBodyIndex]
filename = body.ib
format = DXGI_FORMAT_R32_UINT

[ResourcePosition]
filename = position.buf
stride = 40

[ResourceTexcoord]
filename = texcoord.buf
stride = 20

[ResourceBlendA]
filename = a.blend
stride = 32

[ResourceBlendB]
filename = b.blend
stride = 32
""")

    draw = build_draw_groups(
        sections, extract_resources(sections))[0]["draws"][0]

    assert draw.skinning_source is None
    assert draw.skinning_error is None


def test_draw_groups_position_provenance_rejects_conditional_commandlist_root():
    sections = parse_sections("sample.ini", text="""
[TextureOverridePosition]
vb0 = ResourcePosition
if $mode
    run = CommandListConditional
endif

[CommandListConditional]
vb0 = ResourcePosition
vb4 = ResourceConditionalBlend

[TextureOverrideBody]
ib = ResourceBodyIndex
vb0 = ResourcePosition
vb1 = ResourceTexcoord
drawindexed = 3, 0, 0

[ResourceBodyIndex]
filename = body.ib
format = DXGI_FORMAT_R32_UINT

[ResourcePosition]
filename = position.buf
stride = 40

[ResourceTexcoord]
filename = texcoord.buf
stride = 20

[ResourceConditionalBlend]
filename = conditional.blend
stride = 32
""")

    draw = build_draw_groups(
        sections, extract_resources(sections))[0]["draws"][0]

    assert draw.skinning_source is None
    assert draw.skinning_error is None


def test_draw_groups_position_provenance_follows_reverse_explicit_copy():
    sections = parse_sections("sample.ini", text="""
[TextureOverridePosition]
vb0 = ResourcePosition
vb4 = ResourceAuthoredBlend

[TextureOverrideBody]
ib = ResourceBodyIndex
vb0 = ResourcePositionAlias
vb1 = ResourceTexcoord
drawindexed = 3, 0, 0

[Present]
ResourcePositionAlias = copy ResourcePosition

[ResourceBodyIndex]
filename = body.ib
format = DXGI_FORMAT_R32_UINT

[ResourcePosition]
filename = position.buf
stride = 40

[ResourcePositionAlias]

[ResourceTexcoord]
filename = texcoord.buf
stride = 20

[ResourceAuthoredBlend]
filename = blend.buf
stride = 32
""")

    draw = build_draw_groups(
        sections, extract_resources(sections))[0]["draws"][0]

    assert draw.skinning_error is None
    assert draw.skinning_source.file == "blend.buf"
    assert draw.skinning_resolution["resolution_source"] == \
        "position_provenance"


def test_draw_groups_position_provenance_reports_ambiguity():
    sections = parse_sections("sample.ini", text="""
[TextureOverridePositionA]
vb0 = ResourcePosition
vb2 = ResourceBlendA

[TextureOverridePositionB]
vb0 = ResourcePosition
vb2 = ResourceBlendB

[TextureOverrideBody]
ib = ResourceIndex
vb0 = ResourcePosition
vb1 = ResourceTexcoord
drawindexed = 3, 0, 0

[ResourceIndex]
filename = index.buf
format = DXGI_FORMAT_R32_UINT

[ResourcePosition]
filename = position.buf
stride = 40

[ResourceTexcoord]
filename = texcoord.buf
stride = 20

[ResourceBlendA]
filename = a.bin
stride = 32

[ResourceBlendB]
filename = b.bin
stride = 32
""")

    draw = build_draw_groups(
        sections, extract_resources(sections))[0]["draws"][0]

    assert draw.skinning_source is None
    assert draw.skinning_error == "ambiguous_skinning_source"


def test_draw_groups_direct_binding_beats_position_provenance():
    sections = parse_sections("sample.ini", text="""
[TextureOverridePosition]
vb0 = ResourcePosition
vb4 = ResourceFallbackBlend

[TextureOverrideBody]
ib = ResourceIndex
vb0 = ResourcePosition
vb1 = ResourceTexcoord
vb4 = ResourceDirectBlend
drawindexed = 3, 0, 0

[ResourceIndex]
filename = index.buf
format = DXGI_FORMAT_R32_UINT

[ResourcePosition]
filename = position.buf
stride = 40

[ResourceTexcoord]
filename = texcoord.buf
stride = 20

[ResourceFallbackBlend]
filename = fallback.bin
stride = 32

[ResourceDirectBlend]
filename = direct.bin
stride = 32
""")

    draw = build_draw_groups(
        sections, extract_resources(sections))[0]["draws"][0]

    assert draw.skinning_error is None
    assert draw.skinning_source.file == "direct.bin"
    assert draw.skinning_resolution["resolution_source"] == "direct"


def test_draw_groups_keep_draw_time_vertex_state_ordered():
    sections = parse_sections("sample.ini", text="""
[TextureOverridePosition]
vb0 = ResourcePosition
vb4 = ResourceFallbackBlend

[TextureOverrideBody]
ib = ResourceIndex
vb0 = ResourcePosition
vb1 = ResourceTexcoord
drawindexed = 3, 0, 0
vb4 = ResourceDirectBlend
drawindexed = 3, 3, 0

[ResourceIndex]
filename = index.buf
format = DXGI_FORMAT_R32_UINT

[ResourcePosition]
filename = position.buf
stride = 40

[ResourceTexcoord]
filename = texcoord.buf
stride = 20

[ResourceFallbackBlend]
filename = fallback.bin
stride = 32

[ResourceDirectBlend]
filename = direct.bin
stride = 32
""")

    draws = build_draw_groups(
        sections, extract_resources(sections))[0]["draws"]

    assert draws[0].skinning_source.file == "fallback.bin"
    assert draws[0].skinning_resolution["resolution_source"] == \
        "position_provenance"
    assert draws[1].skinning_source.file == "direct.bin"
    assert draws[1].skinning_resolution["resolution_source"] == "direct"


def test_draw_groups_command_list_bindings_are_direct_draw_state():
    sections = parse_sections("sample.ini", text="""
[TextureOverrideBody]
ib = ResourceIndex
run = CommandListShared
drawindexed = 3, 0, 0

[CommandListShared]
vb0 = ResourcePosition
vb1 = ResourceTexcoord
vb4 = ResourceBlendBuffer

[ResourceIndex]
filename = index.buf
format = DXGI_FORMAT_R32_UINT

[ResourcePosition]
filename = position.buf
stride = 40

[ResourceTexcoord]
filename = texcoord.buf
stride = 20

[ResourceBlendBuffer]
filename = blend.buf
stride = 32
""")

    draw = build_draw_groups(
        sections, extract_resources(sections))[0]["draws"][0]

    assert draw.skinning_error is None
    assert draw.skinning_source.file == "blend.buf"
    assert draw.skinning_resolution["resolution_source"] == "direct"

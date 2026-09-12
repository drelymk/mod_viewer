"""Resolved draw-group assembly regressions."""

import pytest

from core.ini.draw_groups import build_draw_groups
from core.ini.draw_scan import _scan_sections_for_draws
from core.ini.sections import extract_resources, parse_sections


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

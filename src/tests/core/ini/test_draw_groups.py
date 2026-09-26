"""Resolved draw-group assembly regressions."""

import pytest

from core.ini.draw_groups import build_draw_groups
from core.ini.draw_scan import _scan_sections_for_draws
from core.ini.sections import extract_resources, parse_sections


def test_draw_scanner_keeps_geometry_and_texture_hash_evidence_separate():
    sections = parse_sections("source-01.ini", text="""[TextureOverrideComponent01]
hash = 0x10101010
match_first_index = 12
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

    draw = _scan_sections_for_draws(sections)["TextureOverrideComponent01"][
        "draws"][0]

    assert (draw.geometry_match.hash,
            draw.geometry_match.first_index,
            draw.geometry_match.index_count) == ("10101010", 12, 24)
    assert [(item.slot, item.resource, item.texture_hashes)
            for item in draw.slot_textures] == [
                (1, "ResourceMystery", ("22222222",))]
    assert draw.diffuse_variants[0]["texture_hashes"] == ("11111111",)


def test_draw_scanner_does_not_infer_texture_hash_from_resource_name():
    sections = parse_sections("source-01.ini", text="""[TextureOverrideComponent01]
hash = 10101010
Resource\\GIMI\\Diffuse = ResourceFoo_11111111
drawindexed = 3, 0, 0
""")

    draw = _scan_sections_for_draws(sections)["TextureOverrideComponent01"][
        "draws"][0]

    assert draw.slot_textures == []
    assert "texture_hashes" not in draw.diffuse_variants[0]


def test_texture_override_index_preserves_conditions_and_resolves_files():
    sections = parse_sections("source-01.ini", text="""[KeyStyle]
type = cycle
$Style = 0,1

[TextureOverrideComponent01]
vb0 = ResourcePosition
vb1 = ResourceTexcoord
ib = ResourceComponent01IB
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

[ResourceComponent01IB]
filename = component01.ib
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


def test_draw_groups_keep_clean_display_names_with_shared_seen_labels():
    sections = parse_sections("source-01.ini", text="""[TextureOverrideComponent01]
ib = ResourceComponent01IB
vb0 = ResourceComponent01Position
vb1 = ResourceComponent01Texcoord
drawindexed = 3, 0, 0

[ResourceComponent01IB]
filename = component01.ib
format = DXGI_FORMAT_R32_UINT

[ResourceComponent01Position]
filename = component01-position.buf
stride = 12

[ResourceComponent01Texcoord]
filename = component01-texcoord.buf
stride = 8
""")
    resources = extract_resources(sections)
    seen = {}

    first = build_draw_groups(sections, resources, seen=seen)[0]
    second = build_draw_groups(sections, resources, seen=seen)[0]

    assert (first["name"], first["display_name"],
            second["name"], second["display_name"]) == (
        "Component01", "Component01", "Component01_2", "Component01")


def test_per_draw_ib_switch_uses_hash_geometry_when_component_is_unknown():
    sections = parse_sections("source-01.ini", text="""
[TextureOverride_a2a2a2a2_Position]
vb0 = Resourcea2a2a2a2Position

[TextureOverride_a2a2a2a2_Texcoord]
vb1 = Resourcea2a2a2a2Texcoord

[TextureOverride_b2b2b2b2_Position]
vb0 = Resourceb2b2b2b2Position

[TextureOverride_b2b2b2b2_Texcoord]
vb1 = Resourceb2b2b2b2Texcoord

[TextureOverride_LOD0.a2a2a2a2_6_0]
ib = Resource_LOD0.a2a2a2a2_6_0_Index
drawindexed = 3, 0, 0
ib = Resource_LOD0.b2b2b2b2_9_0_Index
vb0 = ResourceComponent01VB_b2b2b2b2_0
vb1 = Resourceb2b2b2b2Texcoord
vb2 = Resourceb2b2b2b2Blend
drawindexed = 3, 0, 0

[Resource_LOD0.a2a2a2a2_6_0_Index]
filename = a2a2a2a2-index.buf
format = DXGI_FORMAT_R32_UINT

[Resource_LOD0.b2b2b2b2_9_0_Index]
filename = b2b2b2b2-index.buf
format = DXGI_FORMAT_R32_UINT

[Resourcea2a2a2a2Position]
filename = a2a2a2a2-position.buf
stride = 40

[Resourcea2a2a2a2Texcoord]
filename = a2a2a2a2-texcoord.buf
stride = 20

[Resourceb2b2b2b2Position]
filename = b2b2b2b2-position.buf
stride = 40

[Resourceb2b2b2b2Texcoord]
filename = b2b2b2b2-texcoord.buf
stride = 20

[ResourceComponent01VB_b2b2b2b2_0]
stride = 40

[Resourceb2b2b2b2Blend]
filename = b2b2b2b2-blend.buf
stride = 32
""")
    groups = build_draw_groups(sections, extract_resources(sections))
    draws = groups[0]["draws"]

    assert [(draw.ib_file, draw.position_file, draw.texcoord_file)
            for draw in draws] == [
                ("a2a2a2a2-index.buf", "a2a2a2a2-position.buf",
                 "a2a2a2a2-texcoord.buf"),
                ("b2b2b2b2-index.buf", "b2b2b2b2-position.buf",
                 "b2b2b2b2-texcoord.buf"),
            ]


def test_per_draw_ib_switch_prefers_component_geometry_over_hash_fallback():
    sections = parse_sections("source-01.ini", text="""
[TextureOverrideComponent01]
ib = ResourceComponent01IB
drawindexed = 3, 0, 0
ib = ResourceAltComponent01IB
drawindexed = 3, 0, 0
drawindexed = 3, 3, 0

[TextureOverrideComponent01Position]
vb0 = ResourceComponent01Position

[TextureOverrideComponent01Texcoord]
vb1 = ResourceComponent01Texcoord

[TextureOverrideAltComponent01Position]
vb0 = ResourceAltComponent01Position

[TextureOverrideAltComponent01Texcoord]
vb1 = ResourceAltComponent01Texcoord

[TextureOverrideComponent01Blend]
run = CommandListComponent01Blend

[CommandListComponent01Blend]
if $frame == 0
vb0 = ResourceComponent01Frame0
elif $frame == 1
vb0 = ResourceComponent01Frame1
endif

[TextureOverrideAltComponent01Blend]
run = CommandListAltComponent01Blend

[CommandListAltComponent01Blend]
if $frame == 0
vb0 = ResourceAltComponent01Frame0
elif $frame == 1
vb0 = ResourceAltComponent01Frame1
endif

[ResourceComponent01IB]
filename = component01-index.buf
format = DXGI_FORMAT_R32_UINT

[ResourceAltComponent01IB]
filename = alt-component01-index.buf
format = DXGI_FORMAT_R32_UINT

[ResourceComponent01Position]
filename = component01-position.buf
stride = 40

[ResourceComponent01Texcoord]
filename = component01-texcoord.buf
stride = 20

[ResourceAltComponent01Position]
filename = component01-position.buf
stride = 40

[ResourceAltComponent01Texcoord]
filename = alt-component01-texcoord.buf
stride = 20

[ResourceComponent01Frame0]
filename = component01-frame-0.buf
stride = 40
[ResourceComponent01Frame1]
filename = component01-frame-1.buf
stride = 40
[ResourceAltComponent01Frame0]
filename = alt-component01-frame-0.buf
stride = 40
[ResourceAltComponent01Frame1]
filename = alt-component01-frame-1.buf
stride = 40
""")
    groups = build_draw_groups(
        sections, extract_resources(sections), animation_vars={"frame"})
    draws = groups[0]["draws"]

    assert [(draw.ib_file, draw.position_file, draw.texcoord_file)
            for draw in draws] == [
                ("component01-index.buf", "component01-position.buf", "component01-texcoord.buf"),
                ("alt-component01-index.buf", "component01-position.buf",
                 "alt-component01-texcoord.buf"),
                ("alt-component01-index.buf", "component01-position.buf",
                 "alt-component01-texcoord.buf"),
            ]
    assert [(item["ib_file"], item["position_file"], item["file"])
            for item in groups[0]["animation_vertex_bindings"]] == [
                ("component01-index.buf", "component01-position.buf", "component01-frame-0.buf"),
                ("component01-index.buf", "component01-position.buf", "component01-frame-1.buf"),
                ("alt-component01-index.buf", "component01-position.buf",
                 "alt-component01-frame-0.buf"),
                ("alt-component01-index.buf", "component01-position.buf",
                 "alt-component01-frame-1.buf"),
            ]


def test_draw_groups_resolve_the_scanner_snapshot_for_inline_execution():
    sections = parse_sections("source-01.ini", text="""[TextureOverrideComponent01]
ib = ResourceComponent01IB
vb0 = ResourceComponent01Position
vb1 = ResourceComponent01Texcoord
hash = 0123abcd
match_first_index = 9
match_index_count = 3
if $mode == 1
run = CommandListComponent01
endif
drawindexed = 3, 3, 0

[CommandListComponent01]
vb0 = ResourceComponent01PositionAlt
Resource\\GIMI\\Diffuse = ResourceComponent01Diffuse
Resource\\GIMI\\LightMap = ResourceComponent01LightMap
drawindexed = 3, 0, 0

[ResourceComponent01IB]
filename = component01.ib
format = DXGI_FORMAT_R32_UINT

[ResourceComponent01Position]
filename = component01-position.buf
stride = 12

[ResourceComponent01PositionAlt]
filename = component01-position-alt.buf
stride = 12

[ResourceComponent01Texcoord]
filename = component01-texcoord.buf
stride = 8

[ResourceComponent01Diffuse]
filename = component01-diffuse.dds

[ResourceComponent01LightMap]
filename = component01-light-map.dds
""")
    scanned = _scan_sections_for_draws(sections, gating_vars={"mode"})
    authored = scanned["TextureOverrideComponent01"]["draws"]
    groups = build_draw_groups(
        sections, extract_resources(sections), gating_vars={"mode"})
    draws = groups[0]["draws"]

    assert [(item.start, item.vertex_resources[0]) for item in authored] == [
        (0, "ResourceComponent01PositionAlt"),
        (3, "ResourceComponent01PositionAlt")]
    assert [(item.start, item.position_file) for item in draws] == [
        (0, "component01-position-alt.buf"),
        (3, "component01-position-alt.buf")]
    assert draws[0].texture_default("diffuse") == "component01-diffuse.dds"
    assert draws[0].texture_default("light_map") is None
    assert draws[0].texture_rules("light_map") == [{
        "conditions": [[{"var": "mode", "value": "1", "negate": False}]],
        "file": "component01-light-map.dds",
    }]
    assert (draws[0].geometry_match.hash,
            draws[0].geometry_match.first_index,
            draws[0].geometry_match.index_count) == ("0123abcd", 9, 3)


@pytest.mark.parametrize("namespace, expected", [("RabbitFX", "component01-glow.dds"), ("GIMI", None)])
def test_glow_map_emission_requires_supported_namespace(namespace, expected):
    sections = parse_sections("source-01.ini", text=r"""[TextureOverrideComponent01]
ib = ResourceComponent01IB
vb0 = ResourceComponent01Position
vb1 = ResourceComponent01Texcoord
Resource\RabbitFX\Diffuse = ref ResourceComponent01Diffuse
Resource\RabbitFX\GlowMap = ref ResourceComponent01Glow
drawindexed = 3, 0, 0

[ResourceComponent01IB]
filename = component01.ib
format = DXGI_FORMAT_R32_UINT

[ResourceComponent01Position]
filename = component01-position.buf
stride = 12

[ResourceComponent01Texcoord]
filename = component01-texcoord.buf
stride = 8

[ResourceComponent01Diffuse]
filename = component01-diffuse.dds

[ResourceComponent01Glow]
filename = component01-glow.dds
""".replace("RabbitFX", namespace))
    groups = build_draw_groups(sections, extract_resources(sections))

    assert groups[0]["draws"][0].texture_default("emission_map") == expected


def test_draw_groups_preserve_inline_run_snapshots_without_buffer_files():
    sections = parse_sections("source-01.ini", text="""[TextureOverrideComponent01]
ib = ResourceMissingIB
vb0 = ResourceMissingPosition
run = CommandListComponent01
drawindexed = 3, 3, 0

[CommandListComponent01]
vb1 = ResourceMissingTexcoord
drawindexed = 3, 0, 0
""")

    phase01 = _scan_sections_for_draws(sections)["TextureOverrideComponent01"]
    assert [(draw.start, draw.index_resource) for draw in phase01["draws"]] == [
        (0, "ResourceMissingIB"), (3, "ResourceMissingIB")]
    assert phase01["draws"][0].vertex_resources == {
        0: "ResourceMissingPosition", 1: "ResourceMissingTexcoord"}


def test_draw_groups_resolve_blend_from_position_provenance_without_hashes():
    sections = parse_sections("source-01.ini", text="""
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


def test_draw_groups_position_provenance_respects_actual_draw_slots():
    sections = parse_sections("source-01.ini", text="""
[TextureOverridePosition]
vb0 = ResourcePosition
vb2 = ResourceFallbackBlend

[TextureOverrideComponent01]
ib = ResourceComponent01Index
vb0 = ResourcePosition
vb1 = ResourceTexcoord
vb2 = ResourceOtherStream
drawindexed = 3, 0, 0

[ResourceComponent01Index]
filename = component01.ib
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


def test_draw_groups_position_provenance_rejects_conditional_bindings():
    sections = parse_sections("source-01.ini", text="""
[TextureOverridePosition]
vb0 = ResourcePosition
if $mode
    vb2 = ResourceBlendA
else
    vb2 = ResourceBlendB
endif

[TextureOverrideComponent01]
ib = ResourceComponent01Index
vb0 = ResourcePosition
vb1 = ResourceTexcoord
drawindexed = 3, 0, 0

[ResourceComponent01Index]
filename = component01.ib
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


def test_draw_groups_position_provenance_reports_ambiguity():
    sections = parse_sections("source-01.ini", text="""
[TextureOverridePositionA]
vb0 = ResourcePosition
vb2 = ResourceBlendA

[TextureOverridePositionB]
vb0 = ResourcePosition
vb2 = ResourceBlendB

[TextureOverrideComponent01]
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


def test_draw_groups_keep_draw_time_vertex_state_ordered():
    sections = parse_sections("source-01.ini", text="""
[TextureOverridePosition]
vb0 = ResourcePosition
vb4 = ResourceFallbackBlend

[TextureOverrideComponent01]
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

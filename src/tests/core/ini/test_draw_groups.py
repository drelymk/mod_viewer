"""Resolved draw-group assembly regressions."""

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

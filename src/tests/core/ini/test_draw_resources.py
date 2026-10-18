"""File-backed geometry-resource resolution boundaries."""

import pytest

from core.ini.draw_groups import build_draw_groups
from core.ini.draw_resources import (
    _collect_resource_copy_sources, _collect_resource_descriptor_sources,
    _ib_index_size,
    _resolve_component_buffers,
)
from core.ini.draw_scan import _scan_sections_for_draws
from core.ini.sections import ResourceTable, extract_resources, parse_sections


def test_resource_lookup_exact_spelling_and_sibling_isolation():
    records = {
        "ResourceStream": {"filename": "first.buf", "stride": 12},
        "RESOURCESTREAM": {"filename": "second.buf", "stride": 40},
        "ResourceEmpty": {},
        "RESOURCEEMPTY": {"filename": "other.buf"},
    }
    resources = ResourceTable.normalize(records)
    assert ResourceTable.normalize(resources) is resources
    assert resources.get_ci("resourceStream")["filename"] == "first.buf"
    assert resources.get_ci("ResourceEmpty") == {}
    resolved = _resolve_component_buffers({}, resources, {})
    for name, filename in [("ResourceStream", "first.buf"),
                           ("RESOURCESTREAM", "second.buf"),
                           ("resourcestream", "first.buf")]:
        assert resolved["resolve_vertex_info"](name)["filename"] == filename
    sibling = ResourceTable({"ResourceStream": {"filename": "sibling.buf"}})
    assert sibling.get_ci("resourcestream")["filename"] == "sibling.buf"
    assert resources.get_ci("resourcestream")["filename"] == "first.buf"


def test_declaring_b_suffixed_resource_does_not_resolve_base_resource():
    resources = ResourceTable({
        "ResourcePosition.B": {
            "filename": "position-rest.buf", "stride": 12},
    })
    resolved = _resolve_component_buffers({}, resources, {})

    assert resolved["resolve_vertex_info"]("ResourcePosition") == {}
    assert _ib_index_size("DXGI_FORMAT_R16_UINT") == 2
    assert _ib_index_size("DXGI_FORMAT_R32_UINT") == 4


def test_b_suffixed_resource_resolves_through_authored_copy():
    sections = parse_sections("source-01.ini", text="""
[Present]
ResourcePosition = copy ResourcePosition.B

[ResourcePosition]
[ResourcePosition.B]
filename = position-rest.buf
stride = 12
""")
    resources = extract_resources(sections)
    copy_sources = _collect_resource_copy_sources(sections, resources)
    resolved = _resolve_component_buffers({}, resources, copy_sources)

    assert resolved["resolve_vertex_info"]("ResourcePosition") == {
        "filename": "position-rest.buf", "stride": 12,
    }


@pytest.mark.parametrize("reference", ("reference", "copy reference"))
def test_resource_reference_alias_resolves_file_backed_source(reference):
    sections = parse_sections("source-01.ini", text=f"""
[Present]
ResourcePosition = {reference} ResourcePosition.B

[ResourcePosition]
[ResourcePosition.B]
filename = position-rest.buf
stride = 12
""")
    resources = extract_resources(sections)
    copy_sources = _collect_resource_copy_sources(sections, resources)
    resolved = _resolve_component_buffers({}, resources, copy_sources)

    assert copy_sources["resourceposition"] == ["ResourcePosition.B"]
    assert resolved["resolve_vertex_info"]("ResourcePosition") == {
        "filename": "position-rest.buf", "stride": 12,
    }


def test_copy_description_alias_is_collected_as_descriptor_only():
    sections = parse_sections("source-01.ini", text="""
[CommandListRemap]
ResourceBlendOverride = copy_description ResourceBlendSource

[ResourceBlendSource]
format = DXGI_FORMAT_R8_UINT
stride = 16
""")

    assert _collect_resource_descriptor_sources(sections) == {
        "resourceblendoverride": ["ResourceBlendSource"],
    }


def test_uav_resource_copy_chain_resolves_file_backed_source():
    sections = parse_sections("source-01.ini", text="""
[CustomShaderA]
cs-u5 = copy ResourcePosition.2
ResourcePosition.1 = ref cs-u5

[CustomShaderB]
cs-u5 = copy ResourcePosition.1
ResourcePosition = ref cs-u5

[ResourcePosition]
[ResourcePosition.1]
[ResourcePosition.2]
stride = 40
filename = Position.buf
""")
    resources = extract_resources(sections)
    copy_sources = _collect_resource_copy_sources(sections, resources)
    resolved = _resolve_component_buffers(
        _scan_sections_for_draws(sections), resources, copy_sources)

    assert resolved["resolve_vertex_info"]("ResourcePosition") == {
        "stride": 40, "filename": "Position.buf",
    }


def test_uav_null_clears_the_tracked_resource_source():
    sections = parse_sections("source-01.ini", text="""
[CustomShader]
cs-u5 = copy ResourceA
cs-u5 = null
ResourceB = ref cs-u5

[ResourceA]
filename = a.buf
""")

    copy_sources = _collect_resource_copy_sources(
        sections, extract_resources(sections))

    assert "resourceb" not in copy_sources


def test_uav_resource_tracking_is_isolated_between_sections():
    sections = parse_sections("source-01.ini", text="""
[CustomShaderA]
cs-u5 = copy ResourceA

[CustomShaderB]
ResourceB = ref cs-u5

[ResourceA]
filename = a.buf
""")

    copy_sources = _collect_resource_copy_sources(
        sections, extract_resources(sections))

    assert "resourceb" not in copy_sources


@pytest.mark.parametrize("prefix", ["pre ", "post "])
def test_phased_resource_copies_preserve_data_descriptor_and_uav_order(prefix):
    sections = parse_sections("source-01.ini", text=f"""
[CommandListCopy]
post cs-u5 = null
cs-u5 = copy ResourceSource
ResourceEarly = ref cs-u5
{prefix} ResourceAlias = copy ResourceSource
{prefix} ResourceAlias = copy_desc ResourceLayout
{prefix} cs-u5 = copy ResourceAlias
{prefix} ResourceOutput = ref cs-u5

[ResourceSource]
filename = position.buf
stride = 12
[ResourceLayout]
filename = descriptor.buf
stride = 40
""")
    resources = extract_resources(sections)
    copies = _collect_resource_copy_sources(sections, resources)
    assert copies == {
        "resourceearly": ["ResourceSource"],
        "resourcealias": ["ResourceSource"],
        "resourceoutput": ["ResourceAlias"],
    }
    resolved = _resolve_component_buffers({}, resources, copies, sections=sections)
    assert resolved["resolve_vertex_info"]("ResourceOutput") == {
        "filename": "position.buf", "stride": 40}


@pytest.mark.parametrize("deferred", [
    "post ResourceGeometry = copy ResourcePost",
    "post run = CommandListCopy",
])
def test_shared_copy_alias_resolves_at_each_draw_phase(deferred):
    text = f"""[TextureOverrideComponent01]
{deferred}
ResourceGeometry = copy ResourcePre
vb0 = ResourceGeometry
vb1 = ResourceTexcoord
ib = ResourceIndex
drawindexed = 3, 0, 0
post drawindexed = 3, 0, 0
[ResourcePre]
filename = pre.buf
stride = 12
[ResourcePost]
filename = post.buf
stride = 40
[ResourceTexcoord]
filename = uv.buf
stride = 8
[ResourceIndex]
filename = index.buf
format = R32_UINT
[CommandListCopy]
ResourceGeometry = copy ResourcePost
"""
    sections = parse_sections("source-01.ini", text=text)
    resources = extract_resources(sections)
    copies = _collect_resource_copy_sources(sections, resources)
    assert copies["resourcegeometry"] == ["ResourcePre", "ResourcePost"]
    draws = build_draw_groups(sections, resources)[0]["draws"]
    assert [(draw.position_file, draw.position_stride) for draw in draws] == [
        ("pre.buf", 12), ("post.buf", 40)]
    assert [draw.occurrence.ordinal for draw in draws] == [0, 1]
    assert draws[0].render_identity() != draws[1].render_identity()

    pre_only = parse_sections("source-01.ini", text="\n".join(
        line for line in text.splitlines() if not line.startswith("post ")))
    ordinary = build_draw_groups(pre_only, extract_resources(pre_only))[0]["draws"]
    assert len(ordinary) == 1
    assert ordinary[0].render_identity() == draws[0].render_identity()
    assert ordinary[0].occurrence == draws[0].occurrence


def test_conditional_copy_snapshot_retains_conservative_source_until_reset():
    sections = parse_sections("source-01.ini", text="""[TextureOverrideComponent01]
vb0 = ResourceGeometry
vb1 = ResourceTexcoord
ib = ResourceIndex
if $mode == 0
ResourceGeometry = copy ResourceA
else
ResourceGeometry = copy ResourceB
endif
drawindexed = 3, 0, 0
ResourceGeometry = copy ResourceC
drawindexed = 3, 3, 0
[ResourceA]
filename = a.buf
stride = 12
[ResourceB]
filename = b.buf
stride = 40
[ResourceC]
filename = c.buf
stride = 12
[ResourceTexcoord]
filename = uv.buf
stride = 8
[ResourceIndex]
filename = index.buf
format = R32_UINT
""")
    resources = extract_resources(sections)
    copies = _collect_resource_copy_sources(sections, resources)
    assert copies["resourcegeometry"] == ["ResourceA", "ResourceB", "ResourceC"]
    legacy = _resolve_component_buffers({}, resources, copies)["resolve_vertex_info"]
    scanned = _scan_sections_for_draws(sections, gating_vars={"mode"})
    assert "resourcegeometry" not in scanned["TextureOverrideComponent01"][
        "draws"][0].resource_copies
    draws = build_draw_groups(sections, resources, section_info=scanned)[0]["draws"]
    assert [(draw.position_file, draw.position_stride) for draw in draws] == [
        (legacy("ResourceGeometry")["filename"], 12), ("c.buf", 12)]
    assert [draw.occurrence.ordinal for draw in draws] == [0, 1]


@pytest.mark.parametrize("pre_layout,deferred", [
    ("pre", "post ResourceGeometry = copy_desc ResourceLayoutPost"),
    ("pre", "post run = CommandListLayout"),
    ("conditional", "post run = CommandListLayout"),
    ("none", "PoSt ResourceGeometry = copy_description ResourceLayoutPost"),
])
def test_copy_descriptors_resolve_at_each_draw_phase(pre_layout, deferred):
    assignment = ("ResourceGeometry = copy_desc ResourceLayoutPre"
                  if pre_layout != "none" else "")
    if pre_layout == "conditional":
        assignment = f"if $mode == 0\n{assignment}\nendif"
    sections = parse_sections("source-01.ini", text=f"""[TextureOverrideComponent01]
{deferred}
ResourceGeometry = copy ResourcePosition
{assignment}
vb0 = ResourceGeometry
vb1 = ResourceTexcoord
ib = ResourceIndex
drawindexed = 3, 0, 0
post drawindexed = 3, 0, 0
[ResourcePosition]
filename = position.buf
stride = 12
format = R32G32B32_FLOAT
[ResourceLayoutPre]
filename = layout-pre.buf
stride = 12
format = R32G32B32_FLOAT
[ResourceLayoutPost]
filename = layout-post.buf
stride = 40
format = R16G16B16A16_FLOAT
[ResourceTexcoord]
filename = uv.buf
stride = 8
[ResourceIndex]
filename = index.buf
format = R32_UINT
[CommandListLayout]
ResourceGeometry = copy_desc ResourceLayoutPost
""")
    resources = extract_resources(sections)
    scanned = _scan_sections_for_draws(sections)
    draws = build_draw_groups(sections, resources, section_info=scanned)[0]["draws"]
    assert [(draw.position_file, draw.position_stride) for draw in draws] == [
        ("position.buf", 12), ("position.buf", 40)]
    resolve = _resolve_component_buffers(
        scanned, resources, _collect_resource_copy_sources(sections, resources),
        sections=sections)["resolve_vertex_info"]
    assert [resolve("ResourceGeometry", copy_sources=authored.resource_copies,
                    descriptor_copies=authored.resource_descriptors)["format"]
            for authored in scanned["TextureOverrideComponent01"]["draws"]] == [
                "R32G32B32_FLOAT", "R16G16B16A16_FLOAT"]
    assert [draw.occurrence.ordinal for draw in draws] == [0, 1]


def test_runtime_wwmi_blend_override_uses_authored_descriptor():
    sections = parse_sections("source-01.ini", text="""
[TextureOverrideComponent01]
ib = ResourceComponent01IB
vb0 = ResourceComponent01Position
vb1 = ResourceComponent01Texcoord
vb4 = ResourceBlendBufferOverride
drawindexed = 3, 0, 0

[CommandListRemap]
cs-t35 = ref ResourceBlendRemapVertexVGBuffer
ResourceRemappedBlendBufferRW = copy ResourceBlendBufferNoStride
ResourceRemappedBlendBufferComponent = copy ResourceRemappedBlendBufferRW
ResourceRemappedBlendBufferComponent = copy_desc ResourceBlendBuffer
ResourceBlendBufferOverride = ref ResourceRemappedBlendBufferComponent

[ResourceComponent01IB]
filename = Meshes/Index.buf
format = DXGI_FORMAT_R32_UINT

[ResourceComponent01Position]
filename = Meshes/Position.buf
stride = 12

[ResourceComponent01Texcoord]
filename = Meshes/TexCoord.buf
stride = 20

[ResourceBlendBufferOverride]

[ResourceRemappedBlendBufferComponent]

[ResourceRemappedBlendBufferRW]

[ResourceBlendBufferNoStride]
filename = Meshes/BlendContent.buf

[ResourceBlendBuffer]
filename = Meshes/BlendDescriptor.buf
format = DXGI_FORMAT_R8_UINT
stride = 16

[ResourceBlendRemapVertexVGBuffer]
filename = Meshes/BlendRemapVertexVG.buf
format = DXGI_FORMAT_R16_UINT
stride = 16
""")
    resources = extract_resources(sections)
    scanned = _scan_sections_for_draws(sections)
    copy_sources = _collect_resource_copy_sources(sections, resources)
    resolved = _resolve_component_buffers(
        scanned, resources, copy_sources, sections=sections)

    assert copy_sources["resourceremappedblendbuffercomponent"] == [
        "ResourceRemappedBlendBufferRW",
    ]
    assert resolved["resolve_vertex_info"](
        "ResourceBlendBufferOverride") == {
            "filename": "Meshes/BlendContent.buf",
            "format": "DXGI_FORMAT_R8_UINT",
            "stride": 16,
        }
    assert not resolved["vertex_binding_index"]._resource_connected(
        "ResourceRemappedBlendBufferComponent", "ResourceBlendBuffer")

    group = build_draw_groups(sections, resources)[0]
    assert group["draws"][0].skinning_source.file == \
        "Meshes/BlendContent.buf"
    assert group["draws"][0].skinning_source.encoding == "wwmi_u8_8"
    assert group["draws"][0].skinning_source.vertex_vg_file == \
        "Meshes/BlendRemapVertexVG.buf"
    assert group["draws"][0].skinning_source.bone_id_namespace == \
        "wwmi_vertex_vg"


@pytest.mark.parametrize("suffix", [
    "LOD0",
    ".LOD0",
    "SomethingBlend"
])
def test_component_roles_allow_trailing_text(suffix):
    sections = parse_sections("source-01.ini", text=f"""
[TextureOverrideComponent01Blend{suffix}]
vb0 = ResourcePosition
vb1 = ResourceTexcoord

[TextureOverrideComponent01Position{suffix}]
vb0 = ResourcePosition

[TextureOverrideComponent01Texcoord{suffix}]
vb1 = ResourceTexcoord

[ResourcePosition]
filename = Meshes/Position.buf
stride = 12

[ResourceTexcoord]
filename = Meshes/Texcoord.buf
stride = 20
""")
    resolved = _resolve_component_buffers(
        _scan_sections_for_draws(sections), extract_resources(sections), {})

    assert resolved["component_buffers"] == {
        "component01": {
            "position": "ResourcePosition",
            "texcoord": "ResourceTexcoord",
        },
    }
    assert resolved["component_vertex_resources"]["component01"] == {
        0: "ResourcePosition",
        1: "ResourceTexcoord",
    }
    assert resolved["component_blend_vertex_resources"]["component01"] == {
        0: "ResourcePosition",
        1: "ResourceTexcoord",
    }


def test_component_role_words_inside_component_use_sibling_evidence():
    sections = parse_sections("source-01.ini", text="""
[TextureOverrideBlendComponent01BlendLOD0]
vb0 = ResourcePosition
vb1 = ResourceTexcoord

[TextureOverrideBlendComponent01PositionLOD0]
vb0 = ResourcePosition

[TextureOverrideBlendComponent01TexcoordLOD0]
vb1 = ResourceTexcoord

[ResourcePosition]
filename = Meshes/Position.buf
stride = 12

[ResourceTexcoord]
filename = Meshes/Texcoord.buf
stride = 20
""")
    resolved = _resolve_component_buffers(
        _scan_sections_for_draws(sections), extract_resources(sections), {})

    assert resolved["component_buffers"] == {
        "blendcomponent01": {
            "position": "ResourcePosition",
            "texcoord": "ResourceTexcoord",
        },
    }

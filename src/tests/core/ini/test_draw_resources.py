"""File-backed geometry-resource resolution boundaries."""

import pytest

from core.mod_source import DirectoryModSource
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


@pytest.mark.parametrize("declared_exists, copies, expected", [
    (True, ("ResourceBase",), "position.buf"),
    (False, ("ResourceBase",), "base.buf"),
    (False, ("ResourceAbsent", "ResourceBase"), "base.buf"),
    (False, ("ResourceAbsent",), "position.buf"),
    (False, ("ResourcePosition",), "position.buf"),
    (False, (), "position.buf"),
])
def test_missing_declared_buffer_uses_only_available_authored_copies(
        tmp_path, declared_exists, copies, expected):
    (tmp_path / "base.buf").write_bytes(b"base")
    if declared_exists:
        (tmp_path / "position.buf").write_bytes(b"position")
    resources = ResourceTable({
        "ResourcePosition": {"filename": "position.buf", "stride": 40},
        "ResourceBase": {"filename": "base.buf", "stride": 12},
        "ResourceAbsent": {"filename": "absent.buf", "stride": 12},
    })
    copy_sources = {"resourceposition": list(copies)}
    resolved = _resolve_component_buffers(
        {}, resources, copy_sources,
        resource_source=DirectoryModSource(tmp_path))

    assert resolved["resolve_vertex_info"]("ResourcePosition") == {
        "filename": expected, "stride": 40 if expected == "position.buf" else 12,
    }
    assert resources["ResourcePosition"]["filename"] == "position.buf"
    detached = _resolve_component_buffers({}, resources, copy_sources)
    assert detached["resolve_vertex_info"]("ResourcePosition")["filename"] == \
        "position.buf"


@pytest.mark.parametrize("reference", ("reference", "copy reference"))
@pytest.mark.parametrize("phase", ("", "post "))
def test_resource_reference_alias_resolves_file_backed_source(reference, phase):
    sections = parse_sections("source-01.ini", text=f"""
[Present]
{phase}ResourcePosition = {reference} ResourcePosition.B

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


@pytest.mark.parametrize("phase", ("", "post "))
def test_copy_description_alias_is_collected_as_descriptor_only(phase):
    sections = parse_sections("source-01.ini", text=f"""
[CommandListRemap]
{phase}ResourceBlendOverride = copy_description ResourceBlendSource

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

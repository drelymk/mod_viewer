"""File-backed geometry-resource resolution boundaries."""

import pytest

from core.ini.draw_groups import build_draw_groups
from core.ini.draw_resources import (
    _collect_resource_copy_sources, _ib_index_size,
    _resolve_component_buffers,
)
from core.ini.draw_scan import _scan_sections_for_draws
from core.ini.sections import ResourceTable, extract_resources, parse_sections


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
    sections = parse_sections("sample.ini", text="""
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


def test_uav_resource_copy_chain_resolves_file_backed_source():
    sections = parse_sections("sample.ini", text="""
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
    sections = parse_sections("sample.ini", text="""
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
    sections = parse_sections("sample.ini", text="""
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
    sections = parse_sections("sample.ini", text="""
[TextureOverrideBody]
ib = ResourceBodyIB
vb0 = ResourceBodyPosition
vb1 = ResourceBodyTexcoord
vb4 = ResourceBlendBufferOverride
drawindexed = 3, 0, 0

[CommandListRemap]
cs-t35 = ref ResourceBlendRemapVertexVGBuffer
ResourceRemappedBlendBufferRW = copy ResourceBlendBufferNoStride
ResourceRemappedBlendBufferComponent = copy ResourceRemappedBlendBufferRW
ResourceRemappedBlendBufferComponent = copy_desc ResourceBlendBuffer
ResourceBlendBufferOverride = ref ResourceRemappedBlendBufferComponent

[ResourceBodyIB]
filename = Meshes/Index.buf
format = DXGI_FORMAT_R32_UINT

[ResourceBodyPosition]
filename = Meshes/Position.buf
stride = 12

[ResourceBodyTexcoord]
filename = Meshes/TexCoord.buf
stride = 20

[ResourceBlendBufferOverride]

[ResourceRemappedBlendBufferComponent]

[ResourceRemappedBlendBufferRW]

[ResourceBlendBufferNoStride]
filename = Meshes/Blend.buf

[ResourceBlendBuffer]
filename = Meshes/Blend.buf
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
    resolved = _resolve_component_buffers(scanned, resources, copy_sources)

    assert resolved["resolve_vertex_info"](
        "ResourceBlendBufferOverride") == {
            "filename": "Meshes/Blend.buf",
            "format": "DXGI_FORMAT_R8_UINT",
            "stride": 16,
        }

    group = build_draw_groups(sections, resources)[0]
    assert group["draws"][0].skinning_source.file == "Meshes/Blend.buf"
    assert group["draws"][0].skinning_source.encoding == "wwmi_u8_8"
    assert group["draws"][0].skinning_source.vertex_vg_file == \
        "Meshes/BlendRemapVertexVG.buf"
    assert group["draws"][0].skinning_source.bone_id_namespace == \
        "wwmi_vertex_vg"


@pytest.mark.parametrize("suffix", ("LOD0", "-LOD0", ".LOD0", "_LOD0",
                                     "WhateverText", "SomethingBlend",
                                     "SomethingPosition", "SomethingTexcoord"))
def test_component_roles_allow_trailing_text(suffix):
    sections = parse_sections("sample.ini", text=f"""
[TextureOverrideSunnaBodyBlend{suffix}]
vb0 = ResourcePosition
vb1 = ResourceTexcoord

[TextureOverrideSunnaBodyPosition{suffix}]
vb0 = ResourcePosition

[TextureOverrideSunnaBodyTexcoord{suffix}]
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
        "sunnabody": {
            "position": "ResourcePosition",
            "texcoord": "ResourceTexcoord",
        },
    }
    assert resolved["component_vertex_resources"]["sunnabody"] == {
        0: "ResourcePosition",
        1: "ResourceTexcoord",
    }
    assert resolved["component_blend_vertex_resources"]["sunnabody"] == {
        0: "ResourcePosition",
        1: "ResourceTexcoord",
    }


def test_component_role_matching_is_case_insensitive():
    sections = parse_sections("sample.ini", text="""
[TextureOverrideSunnaBodybLeNdWhatever]
vb0 = ResourcePosition
vb1 = ResourceTexcoord

[TextureOverrideSunnaBodypOsItIoNWhatever]
vb0 = ResourcePosition

[TextureOverrideSunnaBodytExCoOrDWhatever]
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

    assert resolved["component_buffers"]["sunnabody"] == {
        "position": "ResourcePosition",
        "texcoord": "ResourceTexcoord",
    }


def test_component_role_words_in_opaque_suffix_use_sibling_evidence():
    sections = parse_sections("sample.ini", text="""
[TextureOverrideSunnaBodyBlendSomethingPositionFoo]
vb0 = ResourcePosition
vb1 = ResourceTexcoord

[TextureOverrideSunnaBodyPositionSomethingPositionFoo]
vb0 = ResourcePosition

[TextureOverrideSunnaBodyTexcoordSomethingPositionFoo]
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
        "sunnabody": {
            "position": "ResourcePosition",
            "texcoord": "ResourceTexcoord",
        },
    }


def test_component_role_words_inside_component_use_sibling_evidence():
    sections = parse_sections("sample.ini", text="""
[TextureOverrideBlendGirlBodyBlendLOD0]
vb0 = ResourcePosition
vb1 = ResourceTexcoord

[TextureOverrideBlendGirlBodyPositionLOD0]
vb0 = ResourcePosition

[TextureOverrideBlendGirlBodyTexcoordLOD0]
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
        "blendgirlbody": {
            "position": "ResourcePosition",
            "texcoord": "ResourceTexcoord",
        },
    }


def test_legacy_component_role_names_keep_existing_resolution():
    sections = parse_sections("sample.ini", text="""
[TextureOverrideBodyBlend]
vb0 = ResourcePosition
vb1 = ResourceTexcoord

[TextureOverrideBodyPosition]
vb0 = ResourcePosition

[TextureOverrideBodyTexcoord]
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
        "body": {
            "position": "ResourcePosition",
            "texcoord": "ResourceTexcoord",
        },
    }

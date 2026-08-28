"""File-backed geometry-resource resolution boundaries."""

from core.ini.draw_groups import build_draw_groups
from core.ini.draw_resources import _ib_index_size, _resolve_component_buffers
from core.ini.draw_scan import _scan_sections_for_draws
from core.ini.sections import ResourceTable, extract_resources, parse_sections


def test_runtime_vertex_resource_uses_one_b_rest_pose_fallback():
    resources = ResourceTable({
        "ResourcePosition.B": {
            "filename": "position-rest.buf", "stride": 12},
    })
    resolved = _resolve_component_buffers({}, resources, {})

    assert resolved["resolve_vertex_info"]("ResourcePosition") == {
        "filename": "position-rest.buf", "stride": 12}
    assert _ib_index_size("DXGI_FORMAT_R16_UINT") == 2
    assert _ib_index_size("DXGI_FORMAT_R32_UINT") == 4


def test_runtime_wwmi_blend_override_uses_authored_descriptor():
    sections = parse_sections("sample.ini", text="""
[TextureOverrideBody]
ib = ResourceBodyIB
vb0 = ResourceBodyPosition
vb1 = ResourceBodyTexcoord
vb4 = ResourceBlendBufferOverride
drawindexed = 3, 0, 0

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

[ResourceBlendBuffer]
filename = Meshes/Blend.buf
format = DXGI_FORMAT_R8_UINT
stride = 16
""")
    resources = extract_resources(sections)
    scanned = _scan_sections_for_draws(sections)
    resolved = _resolve_component_buffers(scanned, resources, {})

    assert resolved["resolve_vertex_info"](
        "ResourceBlendBufferOverride") == {
            "filename": "Meshes/Blend.buf",
            "format": "DXGI_FORMAT_R8_UINT",
            "stride": 16,
        }

    group = build_draw_groups(sections, resources)[0]
    assert group["draws"][0].skinning_source.file == "Meshes/Blend.buf"
    assert group["draws"][0].skinning_source.encoding == "wwmi_u8_8"

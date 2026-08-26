"""File-backed geometry-resource resolution boundaries."""

from core.ini.draw_resources import _ib_index_size, _resolve_component_buffers
from core.ini.sections import ResourceTable


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

"""Geometry-free semantic projection boundary regressions."""

from unittest.mock import patch

from core.geometry.draw_call import DrawCall
from core.geometry.mesh_builder import build_mesh_semantics


def test_mesh_semantics_does_not_read_buffers_or_publish_textures(tmp_path):
    (tmp_path / "diffuse.dds").write_bytes(b"not decoded")
    draw = DrawCall(label="Body-1", texture_default_file="diffuse.dds")
    groups = [{"draws": [draw]}]

    with patch("builtins.open", side_effect=AssertionError("buffer read")), \
            patch("core.textures.pipeline.encode_texture_data_uri",
                  side_effect=AssertionError("texture encoding")):
        result = build_mesh_semantics(groups, str(tmp_path))

    assert result["Body-1"]["tex_key"] == "diffuse::diffuse.dds"

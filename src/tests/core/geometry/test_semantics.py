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


def test_mesh_semantics_preserves_group_source_and_component_identity(tmp_path):
    draw = DrawCall(label="Body-1")
    result = build_mesh_semantics([{
        "name": "Body",
        "display_name": "Body Display",
        "source": "Root.ini",
        "draws": [draw],
    }], str(tmp_path))

    assert result["Body-1"]["source"] == "Root.ini"
    assert result["Body-1"]["component"] == "Body Display"

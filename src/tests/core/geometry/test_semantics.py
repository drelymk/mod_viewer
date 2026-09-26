"""Geometry-free semantic projection boundary regressions."""

from unittest.mock import patch

import pytest

from app.assets.resolver import AssetComponentBinding
from core.geometry.draw_call import DrawCall
from core.geometry.identity import GeometryMatch
from core.geometry.mesh_builder import build_mesh_semantics
from core.geometry.semantics import deduplicate_draws


def test_mesh_semantics_does_not_read_buffers_or_publish_textures(tmp_path):
    (tmp_path / "diffuse.dds").write_bytes(b"not decoded")
    draw = DrawCall(label="Component01-1", texture_default_file="diffuse.dds")
    groups = [{"draws": [draw]}]

    with patch("builtins.open", side_effect=AssertionError("buffer read")), \
            patch("core.textures.pipeline.encode_texture_data_uri",
                  side_effect=AssertionError("texture encoding")):
        result = build_mesh_semantics(groups, str(tmp_path))

    assert result["Component01-1"]["tex_key"] == "diffuse::diffuse.dds"


def test_mesh_semantics_preserves_group_source_and_component_identity(tmp_path):
    draw = DrawCall(label="Component01-1", count=3, start=0, base=0,
                    geometry_match=GeometryMatch("1234abcd", 0, 3))
    result = build_mesh_semantics([{
        "name": "Component01",
        "display_name": "Component01 Display",
        "source": "Root.ini",
        "draws": [draw],
    }], str(tmp_path))

    assert result["Component01-1"]["source"] == "Root.ini"
    assert result["Component01-1"]["component"] == "Component01 Display"
    assert result["Component01-1"]["identity"] == {
        "version": 5,
        "key": 'mesh:[5,"Root.ini","Component01 Display",null,'
                '["1234abcd",0,3],[3,0,0],'
                '[null,null,null,null,null,null,null]]',
        "source": "Root.ini",
        "component": "Component01 Display",
        "occurrence": None,
        "geometry": {"hash": "1234abcd", "first_index": 0,
                     "index_count": 3},
        "draw": {"count": 3, "start": 0, "base": 0},
        "geometry_state": {
            "ib_file": None, "index_size": None,
            "position_file": None, "position_stride": None,
            "texcoord_file": None, "texcoord_stride": None,
            "normal_source": None,
        },
    }


def test_mesh_identity_is_independent_of_optional_asset_binding(tmp_path):
    draw = DrawCall(
        label="Component01-1", count=3, start=0, base=0,
        geometry_match=GeometryMatch("1234abcd", 0, 3))
    group = [{
        "name": "Component01", "display_name": "Component01", "source": "Root.ini",
        "draws": [draw],
    }]

    without_asset = build_mesh_semantics(group, str(tmp_path))["Component01-1"]
    draw.asset_binding = AssetComponentBinding(
        status="exact", geometry_hash="1234abcd", first_index=0,
        index_count=3)
    with_asset = build_mesh_semantics(group, str(tmp_path))["Component01-1"]

    assert with_asset["identity"] == without_asset["identity"]
    assert "asset_binding" in with_asset
    assert with_asset["asset_binding"]["geometry_hash"] == \
        with_asset["identity"]["geometry"]["hash"]
    assert with_asset["asset_binding"]["first_index"] == \
        with_asset["identity"]["geometry"]["first_index"]
    assert with_asset["asset_binding"]["index_count"] == \
        with_asset["identity"]["geometry"]["index_count"]


@pytest.mark.parametrize("status", ["ambiguous", "not_found"])
def test_mesh_identity_survives_uncertain_asset_resolution(tmp_path, status):
    draw = DrawCall(
        label="Component01-1", count=3, start=0, base=0,
        geometry_match=GeometryMatch("1234abcd", 0, 3),
        asset_binding=AssetComponentBinding(status=status))
    result = build_mesh_semantics([{
        "name": "Component01", "display_name": "Component01", "source": "Root.ini",
        "draws": [draw],
    }], str(tmp_path))["Component01-1"]

    assert result["identity"]["key"] == (
        'mesh:[5,"Root.ini","Component01",null,["1234abcd",0,3],[3,0,0],'
        '[null,null,null,null,null,null,null]]')
    assert result["asset_binding"]["status"] == status


def test_distinct_geometry_resources_keep_distinct_mesh_identities(tmp_path):
    geometry_match = GeometryMatch("1234abcd", 0, 3)
    draws = [
        DrawCall(
            label="Component01-1", count=3, start=0, base=0,
            ib_file="component01.ib", index_size=4,
            position_file="component01-a.buf", position_stride=12,
            texcoord_file="component01-uv.buf", texcoord_stride=8,
            geometry_match=geometry_match),
        DrawCall(
            label="Component01-2", count=3, start=0, base=0,
            ib_file="component01.ib", index_size=4,
            position_file="component01-b.buf", position_stride=12,
            texcoord_file="component01-uv.buf", texcoord_stride=8,
            geometry_match=geometry_match),
    ]
    group = [{
        "name": "Component01", "display_name": "Component01", "source": "Root.ini",
        "draws": draws,
    }]

    assert len(deduplicate_draws(group[0])) == 2
    result = build_mesh_semantics(group, str(tmp_path))

    assert len(result) == 2
    assert len({entry["identity"]["key"] for entry in result.values()}) == 2


def test_texture_only_draws_keep_distinct_displayed_mesh_identities(tmp_path):
    geometry_match = GeometryMatch("1234abcd", 0, 3)
    draws = [
        DrawCall(
            label="Component01-1", count=3, start=0, base=0,
            ib_file="component01.ib", index_size=4,
            position_file="component01.buf", position_stride=12,
            texcoord_file="component01-uv.buf", texcoord_stride=8,
            texture_default_file="red.dds",
            occurrence=("TextureOverrideComponent01", 0),
            geometry_match=geometry_match),
        DrawCall(
            label="Component01-2", count=3, start=0, base=0,
            ib_file="component01.ib", index_size=4,
            position_file="component01.buf", position_stride=12,
            texcoord_file="component01-uv.buf", texcoord_stride=8,
            texture_default_file="blue.dds",
            occurrence=("TextureOverrideComponent01", 1),
            geometry_match=geometry_match),
    ]
    group = [{
        "name": "Component01", "display_name": "Component01", "source": "Root.ini",
        "draws": draws,
    }]

    assert len(deduplicate_draws(group[0])) == 2
    result = build_mesh_semantics(group, str(tmp_path))
    keys = [entry["identity"]["key"] for entry in result.values()]

    assert len(result) == 2
    assert len(keys) == len(set(keys)) == 2
    assert {entry["identity"]["occurrence"]["ordinal"]
            for entry in result.values()} == {0, 1}


def test_deduplicated_sources_keep_individual_conditions_and_occurrences(tmp_path):
    draws = [
        DrawCall(
            label="Component01-1", count=3, start=0, base=0,
            conditions=[[{"var": "swap", "value": "0", "negate": False}]],
            sources=[{
                "ini_path": str(tmp_path / "Root.ini"),
                "line_no": 10, "section": "TextureOverrideComponent01",
                "occurrence": {
                    "section": "TextureOverrideComponent01", "ordinal": 0,
                    "path": [],
                },
            }],
        ),
        DrawCall(
            label="Component01-2", count=3, start=0, base=0,
            conditions=[[{"var": "other", "value": "1", "negate": False}]],
            sources=[{
                "ini_path": str(tmp_path / "Root.ini"),
                "line_no": 20, "section": "TextureOverrideComponent01",
                "occurrence": {
                    "section": "TextureOverrideComponent01", "ordinal": 1,
                    "path": [],
                },
            }],
        ),
    ]
    result = build_mesh_semantics([{
        "name": "Component01", "display_name": "Component01", "source": "Root.ini",
        "draws": draws,
    }], str(tmp_path))

    assert len(result) == 1
    sources = result["Component01-1"]["sources"]
    assert [source["line"] for source in sources] == [10, 20]
    assert [source["conditions"][0][0]["var"] for source in sources] == [
        "swap", "other",
    ]
    assert [source["occurrence"]["ordinal"] for source in sources] == [0, 1]

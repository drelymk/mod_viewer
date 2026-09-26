"""Canonical displayed-mesh identity regressions."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from core.geometry.draw_call import DrawCall
from core.geometry.identity import (
    DrawOccurrence, GeometryMatch, make_mesh_identity,
    normalize_identity_source,
)
from core.geometry.vertex_attributes import VertexAttributeSource


@pytest.mark.parametrize("value, expected", [
    (None, None),
    (".\\variants\\sub", "variants/sub"),
    ("Variants/Component01", "Variants/Component01")
])
def test_identity_source_normalization_preserves_authored_spelling(value,
                                                                    expected):
    assert normalize_identity_source(value) == expected


def _draw(**changes):
    values = {
        "count": 120,
        "start": 4,
        "base": -2,
        "geometry_match": GeometryMatch("1234ABCD", 8, 120),
    }
    values.update(changes)
    return DrawCall(**values)


def test_mesh_identity_is_deterministic_and_projects_all_authored_fields():
    draw = _draw(
        ib_file="index.buf", index_size=2,
        position_file="position.buf", position_stride=12,
        texcoord_file="uv.buf", texcoord_stride=8,
        normal_source=VertexAttributeSource("normal.buf", 8, 4, "snorm8x3"))
    identity = make_mesh_identity(
        draw, source=".\\Root.ini", component="Component01:Main")

    assert identity.key == (
        'mesh:[5,"Root.ini","Component01:Main",null,["1234ABCD",8,120],'
        '[120,4,-2],["index.buf",2,"position.buf",12,"uv.buf",8,'
        '["normal.buf",8,4,"snorm8x3"]]]')
    assert identity.to_dict() == {
        "version": 5,
        "key": identity.key,
        "source": "Root.ini",
        "component": "Component01:Main",
        "occurrence": None,
        "geometry": {
            "hash": "1234ABCD",
            "first_index": 8,
            "index_count": 120,
        },
        "draw": {"count": 120, "start": 4, "base": -2},
        "geometry_state": {
            "ib_file": "index.buf", "index_size": 2,
            "position_file": "position.buf", "position_stride": 12,
            "texcoord_file": "uv.buf", "texcoord_stride": 8,
            "normal_source": {
                "file": "normal.buf", "stride": 8, "offset": 4,
                "encoding": "snorm8x3"},
        },
    }
    assert make_mesh_identity(
        draw, source=".\\Root.ini", component="Component01:Main").key == identity.key


@pytest.mark.parametrize("field, value", [
    ("source", "Other.ini"),
    ("occurrence", DrawOccurrence("TextureOverrideComponent01", 1)),
    ("geometry", GeometryMatch("abcdef12", 8, 120)),
    ("count", 121)
])
def test_mesh_identity_key_changes_for_structural_fields(field, value):
    identity = make_mesh_identity(
        _draw(), source="Root.ini", component="Component01")
    assert replace(identity, **{field: value}).key != identity.key


@pytest.mark.parametrize("field, value", [
    ("position_file", "other-position.buf"),
    ("normal_source", VertexAttributeSource(
        "other-normals.buf", 8, 4, "snorm8x3"))
])
def test_mesh_identity_key_changes_for_geometry_resources(field, value):
    first = make_mesh_identity(
        _draw(), source="Root.ini", component="Component01")
    changed_draw = _draw(**{field: value})

    assert make_mesh_identity(
        changed_draw, source="Root.ini", component="Component01").key != first.key


def test_mesh_identity_normalizes_geometry_resource_paths():
    first = make_mesh_identity(
        _draw(position_file="variants\\Component01.buf"),
        source="Root.ini", component="Component01")
    second = make_mesh_identity(
        _draw(position_file="variants/Component01.buf"),
        source="Root.ini", component="Component01")

    assert first.key == second.key


def test_mesh_identity_keeps_asset_binding_and_render_state_out_of_key():
    draw = _draw()
    first = make_mesh_identity(draw, source="Root.ini", component="Component01")
    draw.conditions = [[{"var": "toggle", "value": "1"}]]
    draw.asset_binding = SimpleNamespace(status="exact")
    draw.texture_default_file = "different.dds"
    second = make_mesh_identity(draw, source="Root.ini", component="Component01")

    assert second.key == first.key


def test_mesh_identity_occurrence_distinguishes_texture_only_draws():
    first = make_mesh_identity(
        _draw(texture_default_file="red.dds",
              occurrence=("TextureOverrideComponent01", 0)),
        source="Root.ini", component="Component01")
    same_occurrence = make_mesh_identity(
        _draw(texture_default_file="red-v2.dds",
              occurrence=("TextureOverrideComponent01", 0)),
        source="Root.ini", component="Component01")
    second = make_mesh_identity(
        _draw(texture_default_file="blue.dds",
              occurrence=("TextureOverrideComponent01", 1)),
        source="Root.ini", component="Component01")

    assert same_occurrence.key == first.key
    assert first.key != second.key
    assert first.to_dict()["occurrence"] == {
        "section": "TextureOverrideComponent01", "ordinal": 0}
    assert second.to_dict()["occurrence"] == {
        "section": "TextureOverrideComponent01", "ordinal": 1}


def test_draw_occurrence_is_not_render_identity():
    first = _draw(occurrence=("TextureOverrideComponent01", 0))
    second = _draw(occurrence=("TextureOverrideComponent01", 1))

    assert first.render_identity() == second.render_identity()


def test_mesh_identity_without_geometry_evidence_is_still_present():
    identity = make_mesh_identity(
        _draw(geometry_match=None), source=None, component=None)

    assert identity.to_dict() == {
        "version": 5,
        "key": 'mesh:[5,"","",null,null,[120,4,-2],'
                '[null,null,null,null,null,null,null]]',
        "source": None,
        "component": None,
        "occurrence": None,
        "geometry": None,
        "draw": {"count": 120, "start": 4, "base": -2},
        "geometry_state": {
            "ib_file": None, "index_size": None,
            "position_file": None, "position_stride": None,
            "texcoord_file": None, "texcoord_stride": None,
            "normal_source": None,
        },
    }

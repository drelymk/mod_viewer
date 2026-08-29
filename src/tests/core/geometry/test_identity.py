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
    ("", None),
    ("./Root.ini", "Root.ini"),
    (".\\variants\\sub", "variants/sub"),
    ("Variants/Body", "Variants/Body"),
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
    identity = make_mesh_identity(
        _draw(), source=".\\Root.ini", component="Body:Main")

    assert identity.key == (
        'mesh:[5,"Root.ini","Body:Main",null,["1234ABCD",8,120],'
                '[120,4,-2],[null,null,null,null,null,null,null]]')
    assert identity.to_dict() == {
        "version": 5,
        "key": identity.key,
        "source": "Root.ini",
        "component": "Body:Main",
        "occurrence": None,
        "geometry": {
            "hash": "1234ABCD",
            "first_index": 8,
            "index_count": 120,
        },
        "draw": {"count": 120, "start": 4, "base": -2},
        "geometry_state": {
            "ib_file": None, "index_size": None,
            "position_file": None, "position_stride": None,
            "texcoord_file": None, "texcoord_stride": None,
            "normal_source": None,
        },
    }
    assert make_mesh_identity(
        _draw(), source=".\\Root.ini", component="Body:Main").key == identity.key


@pytest.mark.parametrize("field, value", [
    ("source", "Other.ini"),
    ("component", "Other"),
    ("occurrence", DrawOccurrence("TextureOverrideBody", 1)),
    ("geometry", GeometryMatch("abcdef12", 8, 120)),
    ("geometry", GeometryMatch("1234abcd", 9, 120)),
    ("geometry", GeometryMatch("1234abcd", 8, 121)),
    ("count", 121),
    ("start", 5),
    ("base", -1),
])
def test_mesh_identity_key_changes_for_structural_fields(field, value):
    identity = make_mesh_identity(
        _draw(), source="Root.ini", component="Body")
    assert replace(identity, **{field: value}).key != identity.key


@pytest.mark.parametrize("field, value", [
    ("ib_file", "other.ib"),
    ("index_size", 2),
    ("position_file", "other-position.buf"),
    ("position_stride", 40),
    ("texcoord_file", "other-texcoord.buf"),
    ("texcoord_stride", 16),
    ("normal_source", VertexAttributeSource(
        "other-normals.buf", 8, 4, "snorm8x3")),
])
def test_mesh_identity_key_changes_for_geometry_resources(field, value):
    first = make_mesh_identity(
        _draw(), source="Root.ini", component="Body")
    changed_draw = _draw(**{field: value})

    assert make_mesh_identity(
        changed_draw, source="Root.ini", component="Body").key != first.key


def test_mesh_identity_normalizes_geometry_resource_paths():
    first = make_mesh_identity(
        _draw(position_file="variants\\Body.buf"),
        source="Root.ini", component="Body")
    second = make_mesh_identity(
        _draw(position_file="variants/Body.buf"),
        source="Root.ini", component="Body")

    assert first.key == second.key


def test_mesh_identity_keeps_asset_binding_and_render_state_out_of_key():
    draw = _draw()
    first = make_mesh_identity(draw, source="Root.ini", component="Body")
    draw.conditions = [[{"var": "toggle", "value": "1"}]]
    draw.asset_binding = SimpleNamespace(status="exact")
    draw.texture_default_file = "different.dds"
    second = make_mesh_identity(draw, source="Root.ini", component="Body")

    assert second.key == first.key


def test_mesh_identity_occurrence_distinguishes_texture_only_draws():
    first = make_mesh_identity(
        _draw(texture_default_file="red.dds",
              occurrence=("TextureOverrideBody", 0)),
        source="Root.ini", component="Body")
    same_occurrence = make_mesh_identity(
        _draw(texture_default_file="red-v2.dds",
              occurrence=("TextureOverrideBody", 0)),
        source="Root.ini", component="Body")
    second = make_mesh_identity(
        _draw(texture_default_file="blue.dds",
              occurrence=("TextureOverrideBody", 1)),
        source="Root.ini", component="Body")

    assert same_occurrence.key == first.key
    assert first.key != second.key
    assert first.to_dict()["occurrence"] == {
        "section": "TextureOverrideBody", "ordinal": 0}
    assert second.to_dict()["occurrence"] == {
        "section": "TextureOverrideBody", "ordinal": 1}


def test_draw_occurrence_is_not_render_identity():
    first = _draw(occurrence=("TextureOverrideBody", 0))
    second = _draw(occurrence=("TextureOverrideBody", 1))

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

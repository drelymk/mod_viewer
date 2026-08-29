"""Canonical displayed-mesh identity regressions."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from core.geometry.draw_call import DrawCall
from core.geometry.identity import (
    GeometryMatch, make_mesh_identity, normalize_identity_source,
)


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
        'mesh:[2,"Root.ini","Body:Main",["1234ABCD",8,120],'
        '[120,4,-2]]')
    assert identity.to_dict() == {
        "version": 2,
        "key": identity.key,
        "source": "Root.ini",
        "component": "Body:Main",
        "geometry": {
            "hash": "1234ABCD",
            "first_index": 8,
            "index_count": 120,
        },
        "draw": {"count": 120, "start": 4, "base": -2},
    }
    assert make_mesh_identity(
        _draw(), source=".\\Root.ini", component="Body:Main").key == identity.key


@pytest.mark.parametrize("field, value", [
    ("source", "Other.ini"),
    ("component", "Other"),
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


def test_mesh_identity_keeps_asset_binding_and_render_state_out_of_key():
    draw = _draw()
    first = make_mesh_identity(draw, source="Root.ini", component="Body")
    draw.conditions = [[{"var": "toggle", "value": "1"}]]
    draw.asset_binding = SimpleNamespace(status="exact")
    draw.texture_default_file = "different.dds"
    second = make_mesh_identity(draw, source="Root.ini", component="Body")

    assert second.key == first.key


def test_mesh_identity_without_geometry_evidence_is_still_present():
    identity = make_mesh_identity(
        _draw(geometry_match=None), source=None, component=None)

    assert identity.to_dict() == {
        "version": 2,
        "key": 'mesh:[2,"","",null,[120,4,-2]]',
        "source": None,
        "component": None,
        "geometry": None,
        "draw": {"count": 120, "start": 4, "base": -2},
    }

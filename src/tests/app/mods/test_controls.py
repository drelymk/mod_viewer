"""Control-panel projections and lightweight semantic reads."""

from types import SimpleNamespace

import pytest

from app.mods.analysis import ParsedModAnalysis
from app.mods.controls import (
    _gating_vars, _gating_vars_from_groups, load_control_state,
    load_present_state,
)
from app.mods.loader import ModLoadContext

from app.mods.controls import build_toggle_panel


def _key(name, varvals, key="", key_display="", source=None, ini_path="mod.ini"):
    return {
        "name": name, "key": key, "key_display": key_display,
        "vars": dict(varvals), "source": source,
        "ini_path": ini_path, "section": f"Key{name}",
    }


def test_gating_variable_collection_covers_draw_and_variant_conditions():
    draw = {
        "conditions": [[{"var": "draw", "value": "1", "negate": False}]],
        "texture_variants": [{"conditions": [[{
            "var": "texture", "value": "1", "negate": False,
        }]]}],
        "normal_map_variants": [{"conditions": [[{
            "var": "normal", "value": "1", "negate": False,
        }]]}],
        "normal_data_variants": [{"conditions": [[{
            "var": "normal_data", "value": "1", "negate": False,
        }]]}],
        "light_map_variants": [{"conditions": [[{
            "var": "light", "value": "1", "negate": False,
        }]]}],
        "material_map_variants": [{"conditions": [[{
            "var": "material", "value": "1", "negate": False,
        }]]}],
    }
    expected = {"draw", "texture", "normal", "normal_data", "light", "material"}

    assert _gating_vars({"Body-1": draw}) == expected
    assert _gating_vars_from_groups([{"draws": [draw]}]) == expected


def test_wired_toggle_shows_only_its_gating_vars():
    """Wired sections show gating vars but retain the full record tuple."""
    master = "\\Some\\Master\\State"
    toggle_keys = {"KeyUpper": _key("Upper", {
        "Upper": ["0", "1"], master: ["0", "1", "2"],
    })}
    panel = build_toggle_panel(toggle_keys, {}, gating_vars={"Upper"}, mod_dir=None)
    assert "KeyUpper" in panel
    entry = panel["KeyUpper"]
    assert entry["wired"] is True
    assert [v["var"] for v in entry["vars"]] == ["Upper"]
    assert [v["var"] for v in entry["cycle_vars"]] == ["Upper", master]
    assert entry["cycle_vars"][1]["values"] == ["0", "1", "2"]


@pytest.mark.parametrize(
    "ini_path, variables, pending, expected_vars",
    [
        ("mod.ini", {"Fresh": ["0", "1", "2"]},
         {"mod.ini": {"KeyNew"}}, ["Fresh"]),
        ("mod.ini", {"Fresh": ["0", "1"]}, None, None),
        ("other.ini", {"Fresh": ["0", "1"]},
         {"mod.ini": {"KeyNew"}}, None),
        ("mod.ini", {
            "Local": ["0", "1"],
            "\\Mod\\Master\\swapvar": ["0", "1", "2"],
        }, {"mod.ini": {"KeyNew"}}, ["Local"]),
    ],
    ids=("pending-same-ini", "not-pending", "pending-other-ini",
         "namespaced-vars-excluded"),
)
def test_unwired_toggle_visibility_policy(
        ini_path, variables, pending, expected_vars):
    toggle_keys = {"KeyNew": _key("New", variables, ini_path=ini_path)}
    panel = build_toggle_panel(
        toggle_keys, {}, gating_vars=set(), mod_dir=None,
        pending_new_sections=pending,
    )
    if expected_vars is None:
        assert panel == {}
    else:
        assert panel["KeyNew"]["wired"] is False
        assert [item["var"] for item in panel["KeyNew"]["vars"]] == expected_vars


def test_present_state_does_not_build_geometry(
        tmp_path, monkeypatch):
    ini_path = tmp_path / "mod.ini"
    ini_path.write_text(
        "[KeyModViewerPresent]\n"
        "key = p\n"
        "type = cycle\n"
        "$Outfit = 0,1\n",
        encoding="utf-8",
    )
    context = ModLoadContext(str(tmp_path), [str(ini_path)])

    monkeypatch.setattr(
        "app.mods.controls.build_mesh_semantics",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("PRESENT reads must not build mesh semantics")),
    )
    present = load_present_state(context)

    assert present["item"]["key_raw"] == "p"
    assert present["item"]["count"] == 2


def test_control_state_does_not_build_geometry(
        tmp_path, monkeypatch):
    parsed = ParsedModAnalysis(
        groups=[{"draws": [{"conditions": [[{
            "var": "Outfit", "value": "1", "negate": False,
        }]], "texture_variants": []}]}],
        toggles={"KeyOutfit": {
            "name": "Outfit", "key_display": "", "key": "",
            "source": None, "ini_path": str(tmp_path / "mod.ini"),
            "section": "KeyOutfit", "vars": {"Outfit": ["0", "1"]},
        }},
        menu={}, defaults={"Outfit": "0"}, state_rules=[], present={},
        game=SimpleNamespace(game="unknown"),
    )
    context = ModLoadContext(str(tmp_path), [str(tmp_path / "mod.ini")])
    monkeypatch.setattr(
        "app.mods.controls.analyze_mod_inis", lambda *args, **kwargs: parsed)

    semantic_calls = []

    def build_semantics(*args, **kwargs):
        semantic_calls.append((args, kwargs))
        return {"Body-1": {"conditions": [[{
            "var": "Outfit", "value": "1", "negate": False,
        }]]}}

    monkeypatch.setattr("app.mods.controls.build_mesh_semantics", build_semantics)

    result = load_control_state(context, active_mesh_keys={"Body-1"})

    assert semantic_calls
    assert set(result["controls"]["toggles"]) == {"KeyOutfit"}

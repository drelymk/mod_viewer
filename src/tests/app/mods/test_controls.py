"""Control-panel projections and lightweight semantic reads."""

from types import SimpleNamespace
import struct

import pytest

from app.mods.analysis import ParsedModAnalysis, build_mod_ini_snapshot
from app.mods.controls import (
    _gating_vars, _gating_vars_from_groups, load_control_state,
    load_present_state, unwired_pending_sections,
)
from app.mods.loader import load_mod, load_semantic_state
from core.ini.document import IniDocument
from tests.support_snapshot import snapshot_context

from app.mods.controls import build_toggle_panel


def test_unwired_pending_sections_uses_full_staged_snapshot(tmp_path,
                                                          monkeypatch):
    menu_path = tmp_path / "nested" / "Menu.ini"
    draw_path = tmp_path / "Component01.ini"
    menu = IniDocument.from_string(
        "namespace = Controls\n"
        "[Constants]\nglobal persist $style = 0\n"
        "[KeyStyle]\ntype = cycle\n$style = 0,1\n",
        path=str(menu_path))
    draw = IniDocument.from_string(
        "[TextureOverrideComponent01]\n"
        "ib = ResourceIB\nvb0 = ResourcePosition\n"
        "vb1 = ResourceTexcoord\n"
        "if $\\Controls\\style == 1\ndrawindexed = 3,0,0\nendif\n"
        "[ResourceIB]\nfilename = component01.ib\nformat = R32_UINT\n"
        "[ResourcePosition]\nfilename = position.buf\nstride = 12\n"
        "[ResourceTexcoord]\nfilename = texcoord.buf\nstride = 8\n",
        path=str(draw_path))
    paths = [str(menu_path), str(draw_path)]
    snapshot = build_mod_ini_snapshot(
        paths, str(tmp_path), {paths[0]: menu, paths[1]: draw},
        require_documents=True)
    from app.mods import controls
    real_analyze = controls.analyze_mod_inis
    analyzed = []

    def analyze(current):
        analyzed.append(current)
        return real_analyze(current)

    monkeypatch.setattr(controls, "analyze_mod_inis", analyze)
    assert unwired_pending_sections(snapshot, {
        "nested/Menu.ini": ["KeyStyle"]}) == {}
    assert analyzed == [snapshot]

    unwired_draw = IniDocument.from_string(
        draw.to_string().replace("$\\Controls\\style", "$other"),
        path=str(draw_path))
    changed = build_mod_ini_snapshot(
        paths, str(tmp_path), {paths[0]: menu, paths[1]: unwired_draw},
        require_documents=True)
    assert unwired_pending_sections(changed, {
        "nested/Menu.ini": ["KeyStyle"]}) == {
            "nested/Menu.ini": ["KeyStyle"]}


def test_indirect_controls_survive_load_staged_reload_and_dependency_cycles(tmp_path):
    text = """[Present]
if $Style < 2
$stage = 1
else
$stage = 0
endif
if $stage == 1
$visible = 1
else
$visible = 0
endif
if $visible == 1
$stage = 1
endif
[Constants]
global persist $Style = 0
global $stage = 0
global $visible = 0
global persist $Unused = 0
[KeyStyle]
type = cycle
$Style = 0,1,2
[KeyUnused]
type = cycle
$Unused = 0,1
[TextureOverrideComponent01]
vb0 = ResourcePosition
vb1 = ResourceTexcoord
ib = ResourceComponent01IB
if $visible == 1
drawindexed = 3,0,0
endif
[ResourcePosition]
filename = position.buf
stride = 12
[ResourceTexcoord]
filename = texcoord.buf
stride = 8
[ResourceComponent01IB]
filename = component01.ib
format = R32_UINT
"""
    path = tmp_path / "mod.ini"
    path.write_text(text, encoding="utf-8")
    original = path.read_bytes()
    (tmp_path / "position.buf").write_bytes(struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0))
    (tmp_path / "texcoord.buf").write_bytes(struct.pack("<6f", 0, 0, 1, 0, 0, 1))
    (tmp_path / "component01.ib").write_bytes(struct.pack("<3I", 0, 1, 2))
    context = snapshot_context(str(tmp_path), [str(path)])
    full = load_mod(context=context)
    assert not full.get("error")
    assert set(full["controls"]["toggles"]) == {"KeyStyle"}
    assert full["state"]["defaults"]["stage"] == "0"
    assert len(full["meshes"]) == 1
    active = set(full["meshes"])
    for state in (load_control_state(context, active_mesh_keys=active),
                  load_semantic_state(context, active_mesh_keys=active)):
        assert state["controls"] == full["controls"]
    staged = text.replace("if $stage == 1", "if $Unused == 1")
    staged_context = snapshot_context(
        str(tmp_path), [str(path)],
        {str(path): IniDocument.from_string(staged, path=str(path))})
    updated = load_control_state(staged_context, active_mesh_keys=active)
    assert set(updated["controls"]["toggles"]) == {"KeyUnused"}
    restored = load_control_state(context, active_mesh_keys=active)
    assert restored["controls"] == full["controls"]
    assert path.read_bytes() == original


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

    assert _gating_vars({"Component01-1": draw}) == expected
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
        "$Input01 = 0,1\n",
        encoding="utf-8",
    )
    context = snapshot_context(str(tmp_path), [str(ini_path)])

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
            "var": "Input01", "value": "1", "negate": False,
        }]], "texture_variants": []}]}],
        toggles={"KeyInput01": {
            "name": "Input01", "key_display": "", "key": "",
            "source": None, "ini_path": str(tmp_path / "mod.ini"),
            "section": "KeyInput01", "vars": {"Input01": ["0", "1"]},
        }},
        menu={}, defaults={"Input01": "0"}, state_rules=[], present={},
        game=SimpleNamespace(game="unknown"),
    )
    context = snapshot_context(str(tmp_path), [str(tmp_path / "mod.ini")])
    monkeypatch.setattr(
        "app.mods.controls.analyze_mod_inis", lambda *args, **kwargs: parsed)

    semantic_calls = []

    def build_semantics(*args, **kwargs):
        semantic_calls.append((args, kwargs))
        return {"Component01-1": {"conditions": [[{
            "var": "Input01", "value": "1", "negate": False,
        }]]}}

    monkeypatch.setattr("app.mods.controls.build_mesh_semantics", build_semantics)

    result = load_control_state(context, active_mesh_keys={"Component01-1"})

    assert semantic_calls
    assert set(result["controls"]["toggles"]) == {"KeyInput01"}

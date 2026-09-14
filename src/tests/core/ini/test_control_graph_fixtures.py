"""Representative INI-shape acceptance tests for the control graph."""

from pathlib import Path

import pytest

from app.mods.analysis import analyze_mod_inis
from app.mods.controls import load_control_state
from app.mods.loader import ModLoadContext


FIXTURE_ROOT = Path(__file__).parents[2] / "fixtures" / "control_graph"


def _fixture(*names):
    return [str(FIXTURE_ROOT / name) for name in names]


def _menu_by_var(parsed):
    return {
        item["var"].casefold(): item
        for item in parsed.control_projection["menu"].values()
    }


def test_mod2_fixture_preserves_direct_keys_menu_branches_and_preset():
    parsed = analyze_mod_inis(_fixture("mod2.ini"), str(FIXTURE_ROOT))
    menu = _menu_by_var(parsed)
    toggles = parsed.control_projection["toggles"]

    assert set(toggles) == {"KeyBody", "KeyHair"}
    assert {"body", "hair", "eyes"} <= set(menu)
    assert "keymodviewerpresent" not in {
        name.casefold() for name in toggles}
    assert {menu[var]["slot"] for var in ("body", "hair", "eyes")} == {
        "1", "2", "3"}
    assert {menu[var]["image_file"] for var in ("body", "hair", "eyes")} == {
        "menu/body.dds", "menu/hair.dds", "menu/eyes.dds"}
    preset = next(action for action in parsed.control_projection["actions"]
                  if action["trigger"] == "p")
    assert preset["kind"] == "compound_action"
    assert preset["user_facing"] is True
    assert set(preset["writes"]) == {"Body", "Hair"}
    assert "KeyMouse" not in toggles
    assert "HairColor" not in menu
    assert "object_detected" not in parsed.defaults
    assert "mouse_clicked" not in parsed.defaults
    modeled = parsed.control_projection["provenance"][
        "modeled_state_variables"]
    assert {name.casefold() for name in modeled} == {"body", "hair", "eyes"}
    for group in parsed.groups:
        for draw in group["draws"]:
            conditions = list(draw.conditions)
            conditions.extend(
                condition for variant in draw.texture_rules("diffuse")
                for condition in variant["conditions"])
            assert all(clause["var"].casefold() != "object_detected"
                       for branch in conditions for clause in branch)
    hair_draw = next(draw for group in parsed.groups
                     for draw in group["draws"] if draw.label == "Hair-1")
    assert hair_draw.texture_rules("diffuse")


def test_sandrone_fixture_separates_slots_and_continuous_controls():
    parsed = analyze_mod_inis(_fixture("sandrone.ini"), str(FIXTURE_ROOT))
    menu = _menu_by_var(parsed)

    assert {"hair", "hat", "boobssize", "buttsize", "thighssize"} <= set(menu)
    assert menu["hair"]["slot"] == "1"
    assert menu["hat"]["slot"] == "2"
    assert {menu[var]["image_file"] for var in ("hair", "hat")} == {
        "menu/hair.dds", "menu/hat.dds"}
    for variable in ("boobssize", "buttsize", "thighssize"):
        assert menu[variable]["domain"] == {
            "kind": "continuous", "min": 0, "max": 1,
        }
    assert "KeyMenu" not in parsed.control_projection["toggles"]
    assert "KeyClickedSlot" not in parsed.control_projection["toggles"]
    assert "active" not in parsed.defaults
    assert all(clause["var"].casefold() != "active"
               for group in parsed.groups for draw in group["draws"]
               for branch in draw.conditions for clause in branch)

    slot_actions = [action for action in parsed.control_projection["actions"]
                    if action["trigger"] == "CommandListClickedSlot"]
    assert {(action["selector"]["value"], action["writes"][0])
            for action in slot_actions} == {("1", "Hair"), ("2", "Hat")}
    assert all({"slot", "hoveredslot"}.isdisjoint(
        {alias.casefold() for alias in action["selector_aliases"]})
               for action in slot_actions)
    flow = parsed.control_projection["provenance"]["selector_flow"]
    assert {(
        edge["source"].casefold(), edge["target"].casefold())
        for edge in flow
    } >= {("slot", "hoveredslot"), ("hoveredslot", "clickedslot")}
    assert {"slot", "hoveredslot", "clickedslot"} <= {
        name.casefold() for name in menu["hair"]["_selector_names"]}


def test_lucy_fixture_keeps_present_and_internal_range_state_separate():
    parsed = analyze_mod_inis(_fixture("lucy_summer.ini"), str(FIXTURE_ROOT))
    menu = _menu_by_var(parsed)

    assert "currflat" in menu
    assert menu["currflat"]["domain"] == {
        "kind": "continuous", "min": 0, "max": 1,
    }
    assert "rangeflat" not in menu
    assert "menu" not in menu
    assert "KeyModViewerPresent" not in parsed.control_projection["toggles"]
    assert next(item for item in menu.values()
                if item["var"].casefold() == "outfit")["image_file"] == (
                    "menu/flat.dds")
    assert "active" not in parsed.defaults
    assert "first_run" not in parsed.defaults
    assert all(clause["var"].casefold() != "active"
               for group in parsed.groups for draw in group["draws"]
               for branch in draw.conditions for clause in branch)


def test_belle_fixture_keeps_slider_slots_and_direct_toggle():
    parsed = analyze_mod_inis(_fixture("belle_mod3.ini"), str(FIXTURE_ROOT))
    menu = _menu_by_var(parsed)

    assert "KeyOutfit" in parsed.control_projection["toggles"]
    for variable, slot, image in (
            ("swapvarsliderbreast", "50", "menu/breast.dds"),
            ("swapvarsliderbottom", "51", "menu/bottom.dds")):
        assert menu[variable]["slot"] == slot
        assert menu[variable]["image_file"] == image
        assert menu[variable]["domain"] == {
            "kind": "continuous", "min": 0, "max": 1,
        }
    assert "active" not in parsed.defaults
    assert {"slot", "hovered_slot", "clicked_slot"} <= {
        name.casefold() for name in menu["swapvarsliderbreast"][
            "_selector_names"]}


def test_namespace_fixture_projects_one_qualified_state_and_present_capture():
    paths = _fixture("namespace_model.ini", "namespace_menu.ini")
    parsed = analyze_mod_inis(paths, str(FIXTURE_ROOT))
    menu = _menu_by_var(parsed)

    assert set(menu) == {"fixture_shared/state"}
    assert "KeyState" not in parsed.control_projection["toggles"]
    assert "KeyModViewerPresent" not in parsed.control_projection["toggles"]
    assert parsed.control_projection["schema_version"] == 3
    roots = parsed.control_projection["provenance"]["input_roots"]
    assert any(root["kind"] == "reserved_present" for root in roots)
    assert any(root["kind"] == "present" for root in roots)
    assert any(edge["kind"] == "data"
               and edge["target"].casefold() == "fixture_shared/state"
               for edge in parsed.control_projection["provenance"][
                   "influences"])
    assert parsed.present["item"]["count"] == 3
    assert parsed.present["item"]["vars"] == [{
        "var": "namespace_menu::State",
        "values": ["0", "1", "1"],
        "default": "0",
    }]
    state = load_control_state(
        ModLoadContext(str(FIXTURE_ROOT), paths))
    assert state["controls"]["schema_version"] == 3
    assert state["controls"]["provenance"]["input_roots"]


@pytest.mark.parametrize(
    "name, expected",
    [("mod2.ini", {"body", "hair", "eyes"}),
     ("sandrone.ini", {"hair", "hat"}),
     ("lucy_summer.ini", {"outfit"}),
     ("belle_mod3.ini", {"swapvarsliderbreast", "swapvarsliderbottom"})],
)
def test_representative_fixture_controls_have_render_effects(name, expected):
    parsed = analyze_mod_inis(_fixture(name), str(FIXTURE_ROOT))
    menu = _menu_by_var(parsed)
    assert expected <= set(menu)
    assert all(info.get("_semantic_id") for info in menu.values())

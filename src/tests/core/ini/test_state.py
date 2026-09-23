"""State-rule extraction cases adjacent to, but distinct from, menus."""

from .test_menu import _by_slot, sections
from core.ini.menu import extract_menu_toggles
from core.ini.state import extract_state_rules
from core.ini.parser import gating_var_names
from core.ini.analysis import analyze_ini
from core.ini.sections import parse_sections
from core.ini.toggles import extract_variable_defaults
import pytest


@pytest.mark.parametrize("comparison", ["< 2", "<= 1", "> 1", ">= 2"])
def test_menu_numeric_conditions_share_draw_and_present_semantics(comparison):
    from .test_menu import MENU_INI
    text = MENU_INI + f"""
[Present]
if $glasses {comparison}
$piece = 1
else
$piece = 0
endif
[TextureOverrideBody]
if $glasses {comparison}
drawindexed = 3,0,0
endif
"""
    from core.ini.draw_scan import _scan_sections_for_draws
    secs = parse_sections("fixture.ini", text=text)
    analysis = analyze_ini(secs, var_prefix="Menu::")
    scan = _scan_sections_for_draws(
        secs, "Menu::", {"glasses"}, condition_aliases=analysis.condition_aliases)
    conditions = scan["TextureOverrideBody"]["draws"][0].conditions
    assert conditions
    rules = analysis.state_rules
    assert len(rules) == 2
    for value in range(3):
        def matches(groups):
            return not groups or any(all(
                (str(value) == c["value"]) != c["negate"] for c in group
            ) for group in groups)
        expected = value < 2 if comparison in ("< 2", "<= 1") else value >= 2
        assert matches(conditions) == expected
        assert [rule["value"] for rule in rules if matches(rule["conditions"])] == ["1" if expected else "0"]


def test_unknown_numeric_elif_blocks_its_later_branches():
    secs = parse_sections("fixture.ini", text="""[KeyStyle]
type = cycle
$Style = 0,1,2
[Present]
if $Style == 0
$piece = 0
elif $unknown > 2
$piece = 1
elif $Style < 2
$piece = 2
else
$piece = 3
endif
""")
    rules = extract_state_rules(secs)
    assert [rule["value"] for rule in rules] == ["0"]


@pytest.mark.parametrize("before", [
    "[Present]\nif $active == 1\n$style = 2\nendif\n",
    "[KeyPreset]\ntype = cycle\n$style = 2\n",
])
def test_constants_defaults_win_over_earlier_runtime_and_preset_assignments(before):
    secs = parse_sections("fixture.ini", text=(before +
        "[cOnStAnTs]\nglobal persist $Style = 0\n"
        "[CommandListFallback]\n$legacy = 3\n"))
    assert extract_variable_defaults(secs, var_prefix="Mod::") == {
        "Mod::Style": "0", "Mod::legacy": "3"}

def test_modulo_cycle_and_present_derived_rules():
    text = r"""
[Constants]
global persist $outfit = 0
global $piece = 0
[CommandListClickedSlot]
if $clickedSlot == 1
    $outfit = $outfit + 1
    $outfit = $outfit % 4
elif $clickedSlot == 2
    $other = 1 - $other
endif
[Present]
if $outfit == 0
    $piece = 0
elif $outfit == 1
    $piece = 1
endif
"""
    secs = sections(text)
    slots = _by_slot(extract_menu_toggles(secs))
    assert (slots[1]["values"] == ["0", "1", "2", "3"]), (f"modulo cycle exposes every value (got {slots[1]['values']})")
    rules = extract_state_rules(secs)
    piece_rules = [rule for rule in rules if rule["var"] == "piece"]
    assert (len(piece_rules) == 2 and piece_rules[1]["value"] == "1"), (f"Present-derived literal draw flags are modelled (got {piece_rules})")
    assert ("piece" in gating_var_names(secs)), ("a Present-derived flag remains available to gate draw meshes")
    assert (all(not any(a["var"] == b["var"] and a["value"] == b["value"] and
                          a["negate"] != b["negate"]
                          for i, a in enumerate(group) for b in group[i + 1:])
              for rule in rules for group in rule["conditions"])), ("impossible elif alternatives are removed from state rules")


def test_unsupported_state_conditions_are_not_replayed():
    text = r"""
[Constants]
global persist $anime_state = 0
global $anime_loop = 0
global $start_frame = 40

[Present]
if $anime_state == 0
    $start_frame = 40
endif
if $anime_auto_play == 1
    if $anime_state == 0 && $anime_loop > 50
        $anime_state = 1
    endif
endif
"""
    rules = extract_state_rules(sections(text))
    assert rules == [{
        "var": "start_frame", "value": "40",
        "conditions": [[{
            "var": "anime_state", "value": "0", "negate": False}]],
    }]


def test_state_rules_reject_unsupported_ordered_conditions_through_alias():
    secs = parse_sections("fixture.ini", text="""[Constants]
global $piece = 0
[CommandListAlias]
$allowed = ($runtime_value > 2)
[Present]
if $allowed
$piece = 1
endif
""")

    analysis = analyze_ini(secs)

    assert "allowed" in analysis.condition_aliases.unsupported
    assert analysis.state_rules == []

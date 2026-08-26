"""State-rule extraction cases adjacent to, but distinct from, menus."""

from .test_menu import _by_slot, sections
from core.ini.menu import extract_menu_toggles
from core.ini.state import extract_state_rules
from core.ini.parser import gating_var_names

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

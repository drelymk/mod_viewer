"""core/ini_menu.py + app.mod_loader.build_menu_panel: mods whose meshes are
driven by an in-game clickable menu instead of [Key...] bindings.

Such a mod has no cycle-type Key section for any of its outfit variables, so
without menu discovery every condition on them is treated as untracked (=
always satisfied) and the viewer shows every variant at once.
"""

import os, sys, tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.ini_menu import extract_menu_toggles, extract_menu_var_names
from core.ini_parser import (build_draw_groups, extract_resources,
                             extract_toggle_keys, find_inis, gating_var_names,
                             merge_sections, parse_sections)
from app.mod_loader import build_menu_panel

FAILS = []


def check(cond, msg):
    print(("PASS  " if cond else "FAIL  ") + msg)
    if not cond:
        FAILS.append(msg)


def write(tmp, name, text):
    p = os.path.join(tmp, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def sections(text):
    with tempfile.TemporaryDirectory() as tmp:
        return parse_sections(write(tmp, "mod.ini", text))


# Both mutation idioms real menu mods use, plus the mutual-exclusion rules a
# click applies alongside the cycled variable.
MENU_INI = """
[Constants]
global persist $top = 0
global persist $glasses = 0
global persist $color = 1
global $clickedSlot
global $hoveredSlot

[CommandListClickedSlot]
$clickedSlot = $hoveredSlot
if $clickedSlot == 1
	$top = 1 - $top
	if $top == 0
		$pasties = 1
		$piercing = 1
	endif
elif $clickedSlot == 2
	$glasses = $glasses + 1
	if $glasses > 2
		$glasses = 0
	endif
elif $clickedSlot == 3
	$color = $color + 1
	if $color > 3
		$color = 0
	endif
endif
"""


def _by_slot(menu):
    return {info["slot"]: info for info in menu.values()}


def test_binary_flip_idiom():
    """`$v = 1 - $v` is the two-state click."""
    slots = _by_slot(extract_menu_toggles(sections(MENU_INI)))
    check(set(slots) == {1, 2, 3}, f"every slot in the chain is found (got {sorted(slots)})")
    check(slots[1]["var"] == "top", f"slot 1 cycles $top (got {slots[1]['var']!r})")
    check(slots[1]["values"] == ["0", "1"], f"$top is binary (got {slots[1]['values']})")


def test_increment_wrap_idiom():
    """`$v = $v + 1` bounded by `if $v > N then $v = 0` is an N+1 state cycle."""
    slots = _by_slot(extract_menu_toggles(sections(MENU_INI)))
    check(slots[2]["values"] == ["0", "1", "2"],
          f"the wrap guard sets $glasses' range (got {slots[2]['values']})")
    check(slots[3]["values"] == ["0", "1", "2", "3"],
          f"a different wrap bound gives a different range (got {slots[3]['values']})")
    check(slots[2]["effects"] == [],
          f"the wrap reset is the cycle itself, not a side effect (got {slots[2]['effects']})")


def test_mutual_exclusion_effects_are_kept():
    """A click also applies the branch's other assignments, under whatever
    nested `if` guards them — the UI replays them so cycling matches the game."""
    slots = _by_slot(extract_menu_toggles(sections(MENU_INI)))
    effects = slots[1]["effects"]
    check([e["var"] for e in effects] == ["pasties", "piercing"],
          f"both guarded assignments are captured, in order (got {effects})")
    check(all(e["when"] == {"var": "top", "op": "==", "value": "0"} for e in effects),
          f"each carries the guard it sits under (got {[e['when'] for e in effects]})")
    check([e["value"] for e in effects] == ["1", "1"], "each carries its assigned value")


def test_display_name_is_the_variable_name():
    """The on-screen caption of a slot is a .dds image, so the ini offers no
    human-readable label — the variable name is the only thing to show."""
    slots = _by_slot(extract_menu_toggles(sections(MENU_INI)))
    check(slots[1]["name"] == "top", f"slot 1 is named after its var (got {slots[1]['name']!r})")


# The same cycle written inside out, plus a menu that spells a variable
# differently from its declaration.

GUARD_FIRST_INI = """
[Constants]
global persist $Hair = 0
global persist $Gloves = 0
global persist $Socks = 0

[CommandListSetButtonCondition]
if $Button_number == 2
	if $Hair < 1
		$Hair = $Hair + 1
	else
		$Hair = 0
	endif
else if $Button_number == 3
	if $Gloves < 3
		$Gloves = $Gloves + 1
	else
		$Gloves = 0
	endif
else if $Button_number == 4
	if $socks <= 1
		$socks = $socks + 1
	else
		$socks = 0
	endif
endif
"""


def test_guard_first_cycle_idiom():
    """`if $v < N / $v = $v + 1 / else / $v = 0` cycles exactly like the
    increment-then-wrap form. Its reset must not be read as a side effect —
    the UI replays those after cycling, which would undo every click."""
    slots = _by_slot(extract_menu_toggles(sections(GUARD_FIRST_INI)))
    check(set(slots) == {2, 3, 4}, f"every slot in the chain is found (got {sorted(slots)})")
    check(slots[2]["values"] == ["0", "1"],
          f"`< 1` gives a two-state cycle (got {slots[2]['values']})")
    check(slots[3]["values"] == ["0", "1", "2", "3"],
          f"`< 3` gives a four-state cycle (got {slots[3]['values']})")
    check(slots[4]["values"] == ["0", "1", "2"],
          f"`<= 1` includes the bound (got {slots[4]['values']})")
    check(all(s["effects"] == [] for s in slots.values()),
          f"no slot mistakes its own reset for a side effect "
          f"(got {[s['effects'] for s in slots.values()]})")


def test_variable_casing_follows_the_declaration():
    """3DMigoto variable names are case-insensitive, so a chain writing $socks
    drives the $Socks that [Constants] declares and the draws are gated on."""
    secs = sections(GUARD_FIRST_INI)
    slots = _by_slot(extract_menu_toggles(secs))
    check(slots[4]["var"] == "Socks",
          f"$socks resolves to the declared $Socks (got {slots[4]['var']!r})")
    check(slots[4]["name"] == "Socks",
          f"the label uses the declared spelling too (got {slots[4]['name']!r})")
    check("Socks" in extract_menu_var_names(secs),
          f"the gating-var set gets the declared spelling "
          f"(got {sorted(extract_menu_var_names(secs))})")


# A "preset" slot: both branches assign the whole wardrobe, and only the guard
# says which one a click means.

PRESET_INI = """
[Constants]
global persist $preset = 1
global persist $hat = 0
global persist $coat = 0

[CommandListSetButtonCondition]
if $Button_number == 1
	if $hat < 1
		$hat = $hat + 1
	else
		$hat = 0
	endif
else if $Button_number == 2
	if $preset < 1
		$hat = 1
		$coat = 1
		$preset = $preset + 1
	else
		$hat = 0
		$coat = 0
		$preset = 0
	endif
endif
"""


def test_else_branch_effects_are_guarded():
    """An `else` is the negation of its `if`, not an unknown. Left unguarded,
    the UI -- which replays effects after every click -- would apply both
    branches and the second would always win."""
    slots = _by_slot(extract_menu_toggles(sections(PRESET_INI)))
    effects = slots[2]["effects"]
    check(slots[2]["values"] == ["0", "1"],
          f"the preset still reads as a two-state cycle (got {slots[2]['values']})")
    check([(e["var"], e["value"]) for e in effects] ==
          [("hat", "1"), ("coat", "1"), ("hat", "0"), ("coat", "0")],
          f"both branches' assignments are kept, in source order (got {effects})")
    check([e["when"] for e in effects[:2]] ==
          [{"var": "preset", "op": "<", "value": "1"}] * 2,
          f"the if-branch keeps its own guard (got {[e['when'] for e in effects[:2]]})")
    check([e["when"] for e in effects[2:]] ==
          [{"var": "preset", "op": ">=", "value": "1"}] * 2,
          f"the else-branch gets the negated one (got {[e['when'] for e in effects[2:]]})")


# A mod that shares one ini across several outfits wraps every section in a
# swapvar guard, pushing the slot chain a level deeper.

WRAPPED_MENU_INI = r"""
[Constants]
global persist $top = 0
global persist $glasses = 0
global $clickedSlot
global $hoveredSlot

[CommandListClickedSlot]
if $\Char\Master\swapvar == 15
    $clickedSlot = $hoveredSlot
    if $clickedSlot == 1
        $top = 1 - $top
        if $top == 0
            $pasties = 1
        endif
    elif $clickedSlot == 2
        $glasses = $glasses + 1
        if $glasses > 2
            $glasses = 0
        endif
    endif
endif
"""


def test_swapvar_wrapped_chain_is_still_found():
    slots = _by_slot(extract_menu_toggles(sections(WRAPPED_MENU_INI)))
    check(set(slots) == {1, 2},
          f"the nested chain's slots are found (got {sorted(slots)})")
    check(slots[1]["values"] == ["0", "1"] and slots[2]["values"] == ["0", "1", "2"],
          f"each slot's cycle survives the extra nesting "
          f"(got {slots[1]['values']}, {slots[2]['values']})")
    check(slots[1]["effects"] == [{"when": {"var": "top", "op": "==", "value": "0"},
                                   "var": "pasties", "value": "1"}],
          f"the guarded side effect is still read out of the branch body "
          f"(got {slots[1]['effects']})")


# The slot chain that paints the menu icons looks structurally identical but
# assigns nothing back to itself.
IMAGE_CHAIN_INI = """
[CommandListSlotItemImage]
if $slot == 1
	ps-t100 = ResourceMenuItem.1
	if $top == 0
		run = CustomShaderElement
	else
		run = CustomShaderDisabledElement
	endif
elif $slot == 2
	ps-t100 = ResourceMenuItem.2
	run = CustomShaderElement
endif
"""


def test_non_mutating_slot_chain_is_not_a_menu():
    check(extract_menu_toggles(sections(IMAGE_CHAIN_INI)) == {},
          "a slot chain that cycles nothing is ignored")


SINGLE_BRANCH_INI = """
[CommandListState]
if $mode == 1
	$mode = 1 - $mode
endif
"""


def test_single_self_assigning_branch_is_not_a_menu():
    """One lone self-assignment is far likelier to be state bookkeeping than a
    clickable slot list."""
    check(extract_menu_toggles(sections(SINGLE_BRANCH_INI)) == {},
          "a one-branch chain is not treated as a menu")


def test_var_prefix_namespaces_every_variable():
    """AllInOne folders reuse variable names across sibling inis."""
    menu = extract_menu_toggles(sections(MENU_INI), var_prefix="modA::", source="modA")
    slots = _by_slot(menu)
    check(all(k.startswith("modA::") for k in menu), f"entry keys are namespaced (got {list(menu)})")
    check(slots[1]["var"] == "modA::top", f"the cycled var is namespaced (got {slots[1]['var']!r})")
    check([e["var"] for e in slots[1]["effects"]] == ["modA::pasties", "modA::piercing"],
          "effect targets are namespaced too")
    check(slots[1]["effects"][0]["when"]["var"] == "modA::top", "so is the guard's var")
    check(slots[1]["name"] == "top", "but the display name stays unprefixed")


def test_menu_var_names_covers_effects_too():
    """A variable only ever written by a mutual-exclusion rule still gates
    meshes, so it has to be tracked like any other."""
    names = extract_menu_var_names(sections(MENU_INI))
    check(names == {"top", "glasses", "color", "pasties", "piercing"},
          f"cycled and effect-written vars are both listed (got {sorted(names)})")


# A whole mod with no cycle-type [Key...] section at all: only the menu makes
# these conditions meaningful.
MENU_MOD_INI = """
[Constants]
global persist $top = 0
global $clickedSlot
global $hoveredSlot

[KeyShowMenu]
key = ]
type = cycle
$menu = 0,1

[CommandListClickedSlot]
if $clickedSlot == 1
	$top = 1 - $top
elif $clickedSlot == 2
	$socks = 1 - $socks
endif

[TextureOverrideBodyPosition]
hash = 1111aaaa
vb0 = ResourceBodyPosition

[TextureOverrideBodyTexcoord]
hash = 2222bbbb
vb1 = ResourceBodyTexcoord

[TextureOverrideBody]
hash = 3333cccc
ib = ResourceBodyIB
if $top == 1
	drawindexed = 3, 0, 0
endif

[ResourceBodyPosition]
type = Buffer
stride = 40
filename = Body.buf

[ResourceBodyTexcoord]
type = Buffer
stride = 20
filename = BodyTex.buf

[ResourceBodyIB]
type = Buffer
format = DXGI_FORMAT_R32_UINT
filename = Body.ib
"""


def test_menu_variables_gate_draws():
    secs = sections(MENU_MOD_INI)
    check("top" in gating_var_names(secs),
          "a menu-only variable counts as a gating var")
    groups = build_draw_groups(secs, extract_resources(secs))
    conds = groups[0]["draws"][0]["conditions"]
    check(conds == [[{"var": "top", "value": "1", "negate": False}]],
          f"so its condition survives normalization (got {conds})")


# One variable, four spellings -- which 3DMigoto doesn't care about at all.
MIXED_CASE_INI = MENU_MOD_INI.replace(
    "global persist $top = 0", "global persist $Top = 0").replace(
    "$top = 1 - $top", "$TOP = 1 - $TOP").replace(
    "if $top == 1", "if $tOp == 1") + """
[Key$Top]
key = t
type = cycle
$toP = 0,1
"""


def test_variable_case_is_ignored_end_to_end():
    """The declared spelling wins everywhere, so the Key section, the menu slot
    and the draw all end up pointing at one variable. Mismatched, the draw's
    clause is dropped as untracked and the mesh is left permanently visible."""
    secs = sections(MIXED_CASE_INI)
    check(gating_var_names(secs) >= {"Top"},
          f"one spelling reaches the gating set (got {sorted(gating_var_names(secs))})")
    check(list(extract_toggle_keys(secs)["Key$Top"]["vars"]) == ["Top"],
          f"the Key section drives it under the declared name "
          f"(got {list(extract_toggle_keys(secs)['Key$Top']['vars'])})")
    check(_by_slot(extract_menu_toggles(secs))[1]["var"] == "Top",
          "and so does the menu slot")
    groups = build_draw_groups(secs, extract_resources(secs))
    conds = groups[0]["draws"][0]["conditions"]
    check(conds == [[{"var": "Top", "value": "1", "negate": False}]],
          f"so the draw stays gated instead of falling through (got {conds})")


def test_menu_panel_model():
    with tempfile.TemporaryDirectory() as tmp:
        write(tmp, "mod.ini", MENU_INI)
        secs = merge_sections(find_inis(tmp))
        menu = extract_menu_toggles(secs)
        panel = build_menu_panel(menu, {"color": "1"}, mod_dir=tmp)

    entries = list(panel.values())
    check([e["slot"] for e in entries] == [1, 2, 3],
          f"entries come out in menu order (got {[e['slot'] for e in entries]})")
    check(entries[2]["default"] == "1",
          f"a declared default beats values[0] (got {entries[2]['default']!r})")
    check(entries[0]["default"] == "0",
          f"$top's declared default is used as well (got {entries[0]['default']!r})")
    check(entries[0]["ini"] == "mod.ini",
          f"the ini path is relative to the mod folder (got {entries[0]['ini']!r})")
    check(entries[0]["section"] == "CommandListClickedSlot",
          f"the originating section is kept (got {entries[0]['section']!r})")


def test_menu_panel_lists_slots_that_gate_nothing():
    """Unlike the Toggle panel, nothing is filtered out: the menu is the mod's
    own statement of what it can change."""
    secs = sections(MENU_MOD_INI)
    panel = build_menu_panel(extract_menu_toggles(secs), {})
    names = sorted(e["name"] for e in panel.values())
    check(names == ["socks", "top"],
          f"$socks is listed even though it gates no mesh (got {names})")


if __name__ == "__main__":
    for fn in (test_binary_flip_idiom,
               test_increment_wrap_idiom,
               test_mutual_exclusion_effects_are_kept,
               test_display_name_is_the_variable_name,
               test_guard_first_cycle_idiom,
               test_variable_casing_follows_the_declaration,
               test_else_branch_effects_are_guarded,
               test_swapvar_wrapped_chain_is_still_found,
               test_non_mutating_slot_chain_is_not_a_menu,
               test_single_self_assigning_branch_is_not_a_menu,
               test_var_prefix_namespaces_every_variable,
               test_menu_var_names_covers_effects_too,
               test_menu_variables_gate_draws,
               test_variable_case_is_ignored_end_to_end,
               test_menu_panel_model,
               test_menu_panel_lists_slots_that_gate_nothing):
        fn()
    print("\n" + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"))
    sys.exit(1 if FAILS else 0)

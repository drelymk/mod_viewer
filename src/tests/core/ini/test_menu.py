"""core/ini_menu.py + app.mods.loader.build_menu_panel: mods whose meshes are
driven by an in-game clickable menu instead of [Key...] bindings.

Such a mod has no cycle-type Key section for any of its outfit variables, so
without menu discovery every condition on them is treated as untracked (=
always satisfied) and the viewer shows every variant at once.
"""

import base64, io, os, tempfile


from core.ini.menu import (attach_menu_images, extract_menu_toggles,
                           extract_menu_var_names)
from core.ini.parser import (build_draw_groups, extract_resources,
                             extract_toggle_keys, find_inis, gating_var_names,
                             merge_sections, parse_sections)
from app.mods.loader import build_menu_panel
from PIL import Image


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
    assert (set(slots) == {1, 2, 3}), (f"every slot in the chain is found (got {sorted(slots)})")
    assert (slots[1]["var"] == "top"), (f"slot 1 cycles $top (got {slots[1]['var']!r})")
    assert (slots[1]["values"] == ["0", "1"]), (f"$top is binary (got {slots[1]['values']})")


def test_increment_wrap_idiom():
    """`$v = $v + 1` bounded by `if $v > N then $v = 0` is an N+1 state cycle."""
    slots = _by_slot(extract_menu_toggles(sections(MENU_INI)))
    assert (slots[2]["values"] == ["0", "1", "2"]), (f"the wrap guard sets $glasses' range (got {slots[2]['values']})")
    assert (slots[3]["values"] == ["0", "1", "2", "3"]), (f"a different wrap bound gives a different range (got {slots[3]['values']})")
    assert (slots[2]["effects"] == []), (f"the wrap reset is the cycle itself, not a side effect (got {slots[2]['effects']})")






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
    assert (set(slots) == {2, 3, 4}), (f"every slot in the chain is found (got {sorted(slots)})")
    assert (slots[2]["values"] == ["0", "1"]), (f"`< 1` gives a two-state cycle (got {slots[2]['values']})")
    assert (slots[3]["values"] == ["0", "1", "2", "3"]), (f"`< 3` gives a four-state cycle (got {slots[3]['values']})")
    assert (slots[4]["values"] == ["0", "1", "2"]), (f"`<= 1` includes the bound (got {slots[4]['values']})")
    assert (all(s["effects"] == [] for s in slots.values())), (f"no slot mistakes its own reset for a side effect "
          f"(got {[s['effects'] for s in slots.values()]})")




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
    assert (slots[2]["values"] == ["0", "1"]), (f"the preset still reads as a two-state cycle (got {slots[2]['values']})")
    assert ([(e["var"], e["value"]) for e in effects] ==
          [("hat", "1"), ("coat", "1"), ("hat", "0"), ("coat", "0")]), (f"both branches' assignments are kept, in source order (got {effects})")
    assert ([e["when"] for e in effects[:2]] ==
          [{"var": "preset", "op": "<", "value": "1"}] * 2), (f"the if-branch keeps its own guard (got {[e['when'] for e in effects[:2]]})")
    assert ([e["when"] for e in effects[2:]] ==
          [{"var": "preset", "op": ">=", "value": "1"}] * 2), (f"the else-branch gets the negated one (got {[e['when'] for e in effects[2:]]})")


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
    assert (set(slots) == {1, 2}), (f"the nested chain's slots are found (got {sorted(slots)})")
    assert (slots[1]["values"] == ["0", "1"] and slots[2]["values"] == ["0", "1", "2"]), (f"each slot's cycle survives the extra nesting "
          f"(got {slots[1]['values']}, {slots[2]['values']})")
    assert (slots[1]["effects"] == [{"when": {"var": "top", "op": "==", "value": "0"},
                                   "var": "pasties", "value": "1"}]), (f"the guarded side effect is still read out of the branch body "
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
    assert (extract_menu_toggles(sections(IMAGE_CHAIN_INI)) == {}), ("a slot chain that cycles nothing is ignored")


SINGLE_BRANCH_INI = """
[CommandListState]
if $mode == 1
	$mode = 1 - $mode
endif
"""




def test_var_prefix_namespaces_every_variable():
    """AllInOne folders reuse variable names across sibling inis."""
    menu = extract_menu_toggles(sections(MENU_INI), var_prefix="modA::", source="modA")
    slots = _by_slot(menu)
    assert (all(k.startswith("modA::") for k in menu)), (f"entry keys are namespaced (got {list(menu)})")
    assert (slots[1]["var"] == "modA::top"), (f"the cycled var is namespaced (got {slots[1]['var']!r})")
    assert ([e["var"] for e in slots[1]["effects"]] == ["modA::pasties", "modA::piercing"]), ("effect targets are namespaced too")
    assert (slots[1]["effects"][0]["when"]["var"] == "modA::top"), ("so is the guard's var")
    assert (slots[1]["name"] == "top"), ("but the display name stays unprefixed")




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




def test_menu_truthiness_expressions_gate_draws():
    text = MENU_MOD_INI.replace(
        "global persist $top = 0",
        "global persist $toy = 1\n"
        "global persist $suit = 0\n"
        "global persist $panties = 0").replace(
        "if $clickedSlot == 1\n\t$top = 1 - $top\n"
        "elif $clickedSlot == 2\n\t$socks = 1 - $socks",
        "if $clickedSlot == 1\n\t$toy = 1 - $toy\n"
        "elif $clickedSlot == 2\n\t$suit = 1 - $suit\n"
        "elif $clickedSlot == 3\n\t$panties = 1 - $panties").replace(
        "if $top == 1", "if $toy && !($suit || $panties)")
    secs = sections(text)
    groups = build_draw_groups(secs, extract_resources(secs))
    conds = groups[0]["draws"][0]["conditions"]
    assert (conds == [[
        {"var": "toy", "value": "0", "negate": True},
        {"var": "suit", "value": "0", "negate": False},
        {"var": "panties", "value": "0", "negate": False},
    ]]), (f"bare, negated and grouped menu guards survive as truthiness DNF (got {conds})")


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
    assert (gating_var_names(secs) >= {"Top"}), (f"one spelling reaches the gating set (got {sorted(gating_var_names(secs))})")
    assert (list(extract_toggle_keys(secs)["Key$Top"]["vars"]) == ["Top"]), (f"the Key section drives it under the declared name "
          f"(got {list(extract_toggle_keys(secs)['Key$Top']['vars'])})")
    assert (_by_slot(extract_menu_toggles(secs))[1]["var"] == "Top"), ("and so does the menu slot")
    groups = build_draw_groups(secs, extract_resources(secs))
    conds = groups[0]["draws"][0]["conditions"]
    assert (conds == [[{"var": "Top", "value": "1", "negate": False}]]), (f"so the draw stays gated instead of falling through (got {conds})")


def test_menu_panel_model():
    with tempfile.TemporaryDirectory() as tmp:
        write(tmp, "mod.ini", MENU_INI)
        secs = merge_sections(find_inis(tmp))
        menu = extract_menu_toggles(secs)
        panel = build_menu_panel(menu, {"color": "1"}, mod_dir=tmp)

    entries = list(panel.values())
    assert ([e["slot"] for e in entries] == [1, 2, 3]), (f"entries come out in menu order (got {[e['slot'] for e in entries]})")
    assert (entries[2]["default"] == "1"), (f"a declared default beats values[0] (got {entries[2]['default']!r})")
    assert (entries[0]["default"] == "0"), (f"$top's declared default is used as well (got {entries[0]['default']!r})")
    assert (entries[0]["ini"] == "mod.ini"), (f"the ini path is relative to the mod folder (got {entries[0]['ini']!r})")
    assert (entries[0]["section"] == "CommandListClickedSlot"), (f"the originating section is kept (got {entries[0]['section']!r})")


def test_menu_panel_lists_slots_that_gate_nothing():
    """Unlike the Toggle panel, nothing is filtered out: the menu is the mod's
    own statement of what it can change."""
    secs = sections(MENU_MOD_INI)
    panel = build_menu_panel(extract_menu_toggles(secs), {})
    names = sorted(e["name"] for e in panel.values())
    assert (names == ["socks", "top"]), (f"$socks is listed even though it gates no mesh (got {names})")




def test_nested_paged_slot_chains_are_all_discovered():
    """A navigation chain can own slots 10/11 while the actual toggles sit in
    deeper page-specific chains which reuse slots 1/2 on every page."""
    text = r"""
[CommandListClickedSlot]
if $clickedSlot == 10
    $page = 0
elif $clickedSlot == 11
    $page = 1
elif $mode == 0
    if $page == 0
        if $clickedSlot == 1
            $top = 1 - $top
        elif $clickedSlot == 2
            $hair = 1 - $hair
        endif
    elif $page == 1
        if $clickedSlot == 1
            $shoes = 1 - $shoes
        elif $clickedSlot == 2
            $socks = 1 - $socks
        endif
    endif
endif
"""
    menu = extract_menu_toggles(sections(text))
    assert (sorted(info["var"] for info in menu.values()) ==
          ["hair", "shoes", "socks", "top"]), (f"nested page chains and reused slots survive (got {menu})")
    assert (len(menu) == 4 and len(set(menu)) == 4), (f"duplicate slot numbers get unique entry keys (got {list(menu)})")
























def test_arrow_pair_menu_is_discovered_with_numbered_icons():
    """MCM-style menus can give each item separate left/right hit regions
    instead of routing every click through one integer slot dispatcher."""
    text = r"""
[Constants]
global persist $Top = 1
global persist $Hair = 1

[CommandListIcon2]
ps-t100 = resourceicon2

[CommandListButton2Left]
$Top = $Top - 1
if $Top < 0
  $Top = 4
endif

[CommandListButton2Right]
$Top = $Top + 1
if $Top > 4
  $Top = 0
endif

[CommandListIcon8]
ps-t100 = RESOURCEICON8

[CommandListButton8Left]
$Hair = $Hair - 1
if $Hair < 1
  $Hair = 5
endif

[CommandListButton8Right]
$Hair = $Hair + 1
if $Hair > 5
  $Hair = 1
endif

[ResourceIcon2]
filename = ui/top.dds

[ResourceIcon8]
filename = ui/hair.dds
"""
    secs = sections(text)
    menu = extract_menu_toggles(secs)
    attach_menu_images(menu, secs, extract_resources(secs))
    by_slot = _by_slot(menu)
    assert (sorted(by_slot) == [2, 8]), (f"both numbered arrow-pair items are found (got {sorted(by_slot)})")
    assert (by_slot[2]["var"] == "Top" and
          by_slot[2]["values"] == ["0", "1", "2", "3", "4"]), (f"Top range comes from its decrement/increment wraps (got {by_slot[2]})")
    assert (by_slot[8]["values"] == ["1", "2", "3", "4", "5"]), (f"non-zero Hair range is preserved (got {by_slot[8]['values']})")
    assert (by_slot[2].get("image_file") == "ui/top.dds" and
          by_slot[8].get("image_file") == "ui/hair.dds"), (f"IconN artwork maps to ButtonN (got {by_slot})")


def test_menu_panel_preserves_authored_transparency():
    with tempfile.TemporaryDirectory() as tmp:
        icon = Image.new("RGBA", (52, 52), (200, 100, 50, 0))
        icon.putpixel((20, 20), (10, 20, 30, 255))
        icon.save(os.path.join(tmp, "icon.png"))
        menu = {"one": {"name": "top", "slot": 1, "var": "top",
                        "values": ["0", "1"], "effects": [], "source": None,
                        "ini_path": None, "section": "CommandListMenu",
                        "image_file": "icon.png"}}
        panel = build_menu_panel(menu, {}, mod_dir=tmp)
        raw = base64.b64decode(panel["one"]["image"].split(",", 1)[1])
        decoded = Image.open(io.BytesIO(raw))
        assert (decoded.mode == "RGBA" and decoded.getpixel((0, 0))[3] == 0 and
              decoded.getpixel((20, 20))[3] == 255), (f"menu PNG keeps transparent and opaque pixels (got {decoded.mode})")

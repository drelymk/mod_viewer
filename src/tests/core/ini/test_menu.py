"""core.ini.menu + app.mods.controls.build_menu_panel: mods whose meshes are
driven by an in-game clickable menu instead of [Key...] bindings.

Such a mod has no cycle-type Key section for any of its input11 variables, so
without menu discovery every condition on them is treated as untracked (=
always satisfied) and the viewer shows every variant at once.
"""

import base64, io, os, tempfile

import pytest


from core.ini.menu import extract_menu_toggles
from core.ini.shapes import extract_shape_sliders
from core.ini.parser import build_draw_groups, extract_resources, extract_toggle_keys, gating_var_names, parse_sections
from app.mods.controls import build_menu_panel
from PIL import Image


def write(tmp, name, text):
    p = os.path.join(tmp, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def sections(text):
    with tempfile.TemporaryDirectory() as tmp:
        return parse_sections(write(tmp, "source-01.ini", text))


# Both mutation idioms real menu mods use, plus the mutual-exclusion rules a
# click applies alongside the cycled variable.
MENU_INI = """
[Constants]
global persist $input01 = 0
global persist $input05 = 0
global persist $color = 1
global $clickedSlot
global $hoveredSlot

[CommandListClickedSlot]
$clickedSlot = $hoveredSlot
if $clickedSlot == 1
	$input01 = 1 - $input01
	if $input01 == 0
		$input02 = 1
		$input03 = 1
	endif
elif $clickedSlot == 2
	$input05 = $input05 + 1
	if $input05 > 2
		$input05 = 0
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


@pytest.mark.parametrize("slot, variable, values", [
    (1, "input01", ["0", "1"]),
    (2, "input05", ["0", "1", "2"]),
], ids=["binary-flip", "increment-wrap-two"])
def test_click_mutation_idioms(slot, variable, values):
    slots = _by_slot(extract_menu_toggles(sections(MENU_INI)))
    assert set(slots) == {1, 2, 3}
    assert slots[slot]["var"] == variable
    assert slots[slot]["values"] == values
    if slot != 1:
        assert slots[slot]["effects"] == []


# The same cycle written inside out, plus a menu that spells a variable
# differently from its declaration.



# A "preset" slot: both branches assign the whole state tuple, and only the guard
# says which one a click means.

PRESET_INI = """
[Constants]
global persist $preset = 1
global persist $input09 = 0
global persist $input10 = 0

[CommandListSetButtonCondition]
if $Button_number == 1
	if $input09 < 1
		$input09 = $input09 + 1
	else
		$input09 = 0
	endif
else if $Button_number == 2
	if $preset < 1
		$input09 = 1
		$input10 = 1
		$preset = $preset + 1
	else
		$input09 = 0
		$input10 = 0
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
          [("input09", "1"), ("input10", "1"), ("input09", "0"), ("input10", "0")]), (f"both branches' assignments are kept, in source order (got {effects})")
    assert ([e["when"] for e in effects[:2]] ==
          [{"var": "preset", "op": "<", "value": "1"}] * 2), (f"the if-branch keeps its own guard (got {[e['when'] for e in effects[:2]]})")
    assert ([e["when"] for e in effects[2:]] ==
          [{"var": "preset", "op": ">=", "value": "1"}] * 2), (f"the else-branch gets the negated one (got {[e['when'] for e in effects[2:]]})")


# A mod that shares one ini across several variants wraps every section in a
# swapvar guard, pushing the slot chain a level deeper.



# The slot chain that paints the menu icons looks structurally identical but
# assigns nothing back to itself.
IMAGE_CHAIN_INI = """
[CommandListSlotItemImage]
if $slot == 1
	ps-t100 = ResourceMenuItem.1
	if $input01 == 0
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






# A whole mod with no cycle-type [Key...] section at all: only the menu makes
# these conditions meaningful.
MENU_MOD_INI = """
[Constants]
global persist $input01 = 0
global $clickedSlot
global $hoveredSlot

[KeyShowMenu]
key = ]
type = cycle
$menu = 0,1

[CommandListClickedSlot]
if $clickedSlot == 1
	$input01 = 1 - $input01
elif $clickedSlot == 2
	$input08 = 1 - $input08
endif

[TextureOverrideComponent01Position]
hash = 1111aaaa
vb0 = ResourceComponent01Position

[TextureOverrideComponent01Texcoord]
hash = 2222bbbb
vb1 = ResourceComponent01Texcoord

[TextureOverrideComponent01]
hash = 3333cccc
ib = ResourceComponent01IB
if $input01 == 1
	drawindexed = 3, 0, 0
endif

[ResourceComponent01Position]
type = Buffer
stride = 40
filename = Component01.buf

[ResourceComponent01Texcoord]
type = Buffer
stride = 20
filename = Component01Tex.buf

[ResourceComponent01IB]
type = Buffer
format = DXGI_FORMAT_R32_UINT
filename = Component01.ib
"""




# One variable, four spellings -- which 3DMigoto doesn't care about at all.
MIXED_CASE_INI = MENU_MOD_INI.replace(
    "global persist $input01 = 0", "global persist $Input01 = 0").replace(
    "$input01 = 1 - $input01", "$INPUT01 = 1 - $INPUT01").replace(
    "if $input01 == 1", "if $iNput01 == 1") + """
[Key$Input01]
key = t
type = cycle
$inPut01 = 0,1
"""


def test_variable_case_is_ignored_end_to_end():
    """The declared spelling wins everywhere, so the Key section, the menu slot
    and the draw all end up pointing at one variable. Mismatched, the draw's
    clause is dropped as untracked and the mesh is left permanently visible."""
    secs = sections(MIXED_CASE_INI)
    assert (gating_var_names(secs) >= {"Input01"}), (f"one spelling reaches the gating set (got {sorted(gating_var_names(secs))})")
    assert (list(extract_toggle_keys(secs)["Key$Input01"]["vars"]) == ["Input01"]), (f"the Key section drives it under the declared name "
          f"(got {list(extract_toggle_keys(secs)['Key$Input01']['vars'])})")
    assert (_by_slot(extract_menu_toggles(secs))[1]["var"] == "Input01"), ("and so does the menu slot")
    groups = build_draw_groups(secs, extract_resources(secs))
    conds = groups[0]["draws"][0]["conditions"]
    assert (conds == [[{"var": "Input01", "value": "1", "negate": False}]]), (f"so the draw stays gated instead of falling through (got {conds})")


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
            $input01 = 1 - $input01
        elif $clickedSlot == 2
            $input06 = 1 - $input06
        endif
    elif $page == 1
        if $clickedSlot == 1
            $input24 = 1 - $input24
        elif $clickedSlot == 2
            $input08 = 1 - $input08
        endif
    endif
endif
"""
    menu = extract_menu_toggles(sections(text))
    assert (sorted(info["var"] for info in menu.values()) ==
          ["input01", "input06", "input08", "input24"]), (f"nested page chains and reused slots survive (got {menu})")
    assert (len(menu) == 4 and len(set(menu)) == 4), (f"duplicate slot numbers get unique entry keys (got {list(menu)})")
























def test_mouse_hit_region_menu_uses_mouse_key_and_finite_actions():
    text = r"""
[Constants]
global $visible = 2
global $limit = 3
global persist $style = 0
global persist $trim = 1

[KeyPointer]
key = VK_LBUTTON
type = hold
$pressed = 1

[CommandListPointerActions]
if $visible >= 1
    if cursor_x > $left0 && cursor_x < $right0
        if $pressed
            if $style < $limit
                $style = $style + 1
            else
                $style = 0
            endif
        endif
    endif
endif
if $visible >= 3
    if cursor_x > $left1 && cursor_x < $right1
        if $pressed
            if $inactive < 2
                $inactive = $inactive + 1
            else
                $inactive = 0
            endif
        endif
    endif
endif
if $pressed && cursor_x > $left2 && cursor_x < $right2
    if $trim < 2
        $trim = $trim + 1
    else
        $trim = 0
    endif
endif
if cursor_x > $left3 && cursor_x < $right3
    if $hovered
        $hovered_style = 1 - $hovered_style
    endif
endif

[CommandListDrawButton_0]
ps-t100 = ResourceSharedFrame
ps-t100 = ResourceStyleIcon
[ResourceSharedFrame]
filename = icons/frame.png
[ResourceStyleIcon]
filename = icons/style.png
[CommandListDrawButton_2]
ps-t100 = ResourceTrimIcon
[ResourceTrimIcon]
filename = icons/trim.png
"""
    secs = sections(text)
    menu = extract_menu_toggles(secs, resources=extract_resources(secs))
    by_slot = _by_slot(menu)
    assert sorted(by_slot) == [0, 2]
    assert by_slot[0]["var"] == "style"
    assert by_slot[0]["values"] == ["0", "1", "2", "3"]
    assert by_slot[2]["var"] == "trim"
    assert by_slot[2]["values"] == ["0", "1", "2"]
    assert by_slot[0]["ini_path"].endswith("source-01.ini")
    assert by_slot[0]["image_file"] == "icons/style.png"
    assert by_slot[2]["image_file"] == "icons/trim.png"

    no_artwork = sections(text.replace("ps-t100 = ResourceTrimIcon", ""))
    plain = _by_slot(extract_menu_toggles(
        no_artwork, resources=extract_resources(no_artwork)))
    assert 2 in plain and "image_file" not in plain[2]

    no_mouse_key = text.replace("key = VK_LBUTTON", "key = k")
    assert extract_menu_toggles(sections(no_mouse_key)) == {}

    mutable_limit = text + "\n[Present]\n$limit = 4\n"
    assert extract_menu_toggles(sections(mutable_limit)) == {}

    malformed = text.replace("$trim = 0\n    endif\nendif",
                             "$trim = 0\n    endif")
    assert extract_menu_toggles(sections(malformed)) == {}


def test_shape_slider_artwork_follows_authored_run_section():
    text = """
[CommandListDrawSlider.Gauge]
x87 = $blend * x87
[CommandListPaint]
ps-t100 = ResourceOddArtwork
run = CustomShaderElement
run = CommandListDrawSlider.Gauge
[CustomShaderMorph]
x88 = $blend
cs-t50 = copy ResourceBase
cs-t51 = copy ResourceTarget
[ResourceBase]
filename = base.buf
stride = 40
[ResourceTarget]
filename = target.buf
stride = 40
[ResourceOddArtwork]
filename = item.dds
[ResourceOtherArtwork]
filename = other.dds
"""
    secs = sections(text)
    slider = extract_shape_sliders(secs, extract_resources(secs))[0]
    assert slider["ui_section"] == "CommandListDrawSlider.Gauge"
    assert slider["section"] == "CustomShaderMorph"
    assert slider["image_file"] == "item.dds"

    stale = sections(text.replace("run = CustomShaderElement", ""))
    assert "image_file" not in extract_shape_sliders(
        stale, extract_resources(stale))[0]

    ambiguous = sections(text + """
[CommandListSecondPaint]
ps-t100 = ResourceOtherArtwork
run = CustomShaderElement
run = CommandListDrawSlider.Gauge
""")
    slider = extract_shape_sliders(ambiguous, extract_resources(ambiguous))[0]
    assert "image_file" not in slider


def test_integer_slot_artwork_uses_known_slots_and_rejects_conflicts():
    text = """
[CommandListActions]
if $clicked == 1
    $first = 1 - $first
elif $clicked == 2
    $second = 1 - $second
endif
[CommandListArtwork]
if $index == 1
    ps-t100 = ResourceOne
elif $index == 2
    ps-t100 = ResourceTwo
endif
[ResourceOne]
filename = one.dds
[ResourceTwo]
filename = two.dds
[ResourceConflict]
filename = conflict.dds
"""
    secs = sections(text)
    menu = extract_menu_toggles(secs, resources=extract_resources(secs))
    assert {slot: item.get("image_file") for slot, item in _by_slot(menu).items()} == {
        1: "one.dds", 2: "two.dds"}

    conflicting = sections(text + """
[CommandListOtherArtwork]
if $other == 1
    ps-t100 = ResourceConflict
elif $other == 2
    ps-t100 = ResourceTwo
endif
""")
    menu = extract_menu_toggles(
        conflicting, resources=extract_resources(conflicting))
    assert "image_file" not in _by_slot(menu)[1]
    assert _by_slot(menu)[2]["image_file"] == "two.dds"


def test_menu_panel_preserves_authored_transparency():
    with tempfile.TemporaryDirectory() as tmp:
        icon = Image.new("RGBA", (52, 52), (200, 100, 50, 0))
        icon.putpixel((20, 20), (10, 20, 30, 255))
        icon.save(os.path.join(tmp, "icon.png"))
        menu = {"one": {"name": "input01", "slot": 1, "var": "input01",
                        "values": ["0", "1"], "effects": [], "source": None,
                        "ini_path": None, "section": "CommandListMenu",
                        "image_file": "icon.png"}}
        panel = build_menu_panel(menu, {}, mod_dir=tmp)
        raw = base64.b64decode(panel["one"]["image"].split(",", 1)[1])
        decoded = Image.open(io.BytesIO(raw))
        assert (decoded.mode == "RGBA" and decoded.getpixel((0, 0))[3] == 0 and
              decoded.getpixel((20, 20))[3] == 255), (f"menu PNG keeps transparent and opaque pixels (got {decoded.mode})")


def test_menu_control_and_panel_lifecycle():
    parsed = sections(MENU_INI)
    menu = extract_menu_toggles(parsed, var_prefix="source01::", source="source01")
    panel = list(build_menu_panel(menu, {"source01::color": "1"}).values())
    assert [item["slot"] for item in panel] == [1, 2, 3]
    assert panel[2]["default"] == "1"
    slots = _by_slot(menu)
    assert slots[1]["var"] == "source01::input01"
    assert slots[1]["effects"][0]["when"]["var"] == "source01::input01"
    assert [effect["var"] for effect in slots[1]["effects"]] == [
        "source01::input02", "source01::input03"]
    visible_panel = build_menu_panel(extract_menu_toggles(sections(MENU_MOD_INI)), {})
    assert {item["name"] for item in visible_panel.values()} == {"input01", "input08"}

"""core/ini_menu.py + app.mod_loader.build_menu_panel: mods whose meshes are
driven by an in-game clickable menu instead of [Key...] bindings.

Such a mod has no cycle-type Key section for any of its outfit variables, so
without menu discovery every condition on them is treated as untracked (=
always satisfied) and the viewer shows every variant at once.
"""

import base64, io, os, sys, tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.ini_menu import (attach_menu_images, extract_menu_toggles,
                           extract_menu_var_names)
from core.ini_shapes import extract_shape_sliders
from core.ini_state import extract_state_rules
from core.ini_parser import (build_draw_groups, extract_resources,
                             extract_toggle_keys, find_inis, gating_var_names,
                             merge_sections, parse_sections)
from app.mod_loader import build_menu_panel
from PIL import Image

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


def test_commandlist_section_name_is_case_insensitive():
    text = MENU_INI.replace("[CommandListClickedSlot]", "[commandlistClickedSlot]")
    slots = _by_slot(extract_menu_toggles(sections(text)))
    check(sorted(slots) == [1, 2, 3],
          f"lowercase CommandList section is discovered (got {sorted(slots)})")


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
    check(sorted(info["var"] for info in menu.values()) ==
          ["hair", "shoes", "socks", "top"],
          f"nested page chains and reused slots survive (got {menu})")
    check(len(menu) == 4 and len(set(menu)) == 4,
          f"duplicate slot numbers get unique entry keys (got {list(menu)})")


def test_compute_shape_slider_is_discovered_and_modelled():
    text = r"""
[Constants]
global persist $currFlat = 0.5

[CustomShaderComputeShapes]
x88 = $currFlat
cs-t50 = copy ResourceBodyPosition.Base
cs-t51 = copy ResourceBodyPosition.Flat

[ResourceBodyPosition.Base]
type = Buffer
stride = 40
filename = BodyPosition.buf

[ResourceBodyPosition.Flat]
type = Buffer
stride = 40
filename = BodyPositionFlat.buf
"""
    secs = sections(text)
    sliders = extract_shape_sliders(secs, extract_resources(secs))
    check(len(sliders) == 1, f"one conservative two-buffer shape slider is found (got {sliders})")
    slider = sliders[0]
    check((slider["var"], slider["base_file"], slider["target_file"]) ==
          ("currFlat", "BodyPosition.buf", "BodyPositionFlat.buf"),
          f"slider links its variable and buffers (got {slider})")

    panel = build_menu_panel({"shape": slider}, {"currFlat": "0.5"})
    entry = panel["shape"]
    check(entry["kind"] == "shape_slider" and entry["default"] == "0.5",
          f"menu model preserves slider kind and float default (got {entry})")


def test_compute_shape_resource_names_are_case_insensitive():
    text = r"""
[Constants]
global persist $currFlat = 0
[CustomShaderComputeShapes]
x87 = $currFlat
cs-t50 = copy resourcebodyposition.base
cs-t51 = copy RESOURCEBODYPOSITION.FLAT
[ResourceBodyPosition.Base]
stride = 40
filename = BodyPosition.buf
[ResourceBodyPosition.Flat]
stride = 40
filename = BodyPositionFlat.buf
"""
    secs = sections(text)
    sliders = extract_shape_sliders(secs, extract_resources(secs))
    check(len(sliders) == 1 and
          sliders[0].get("target_file") == "BodyPositionFlat.buf",
          f"mixed-case resource references still resolve shape buffers (got {sliders})")


def test_parenthesized_increment_modulo_menu_cycle_is_discovered():
    text = r"""
[CommandListClickedSlot]
if $clickedSlot == 1
  $headdress = ($headdress + 1) % 4
elif $clickedSlot == 2
  $cloth = ($cloth + 1) % 3
endif
"""
    menu = _by_slot(extract_menu_toggles(sections(text)))
    check(menu[1]["values"] == ["0", "1", "2", "3"] and
          menu[2]["values"] == ["0", "1", "2"],
          f"parenthesized increment/modulo cycles retain their full ranges (got {menu})")


def test_repeated_full_buffer_shape_blocks_are_discovered():
    text = r"""
[CustomShaderComputeShapes]
x88 = $BoobsSize
cs-t50 = copy ResourceBodyPosition.1
cs-t51 = copy ResourceBodyPosition.2
ResourceBodyPosition = ref cs-u5
Dispatch = 3, 1, 1
x88 = $NippleLength
cs-t50 = copy ResourceBodyPosition.1
cs-t51 = copy ResourceBodyPosition.3
ResourceBodyPosition = ref cs-u5
Dispatch = 3, 1, 1
[ResourceBodyPosition.1]
stride = 40
filename = BodyPosition.buf
[ResourceBodyPosition.2]
stride = 40
filename = BodyPosition.boobs.buf
[ResourceBodyPosition.3]
stride = 40
filename = BodyPosition.nipple.buf
"""
    sliders = extract_shape_sliders(sections(text), extract_resources(sections(text)))
    by_var = {slider["var"]: slider for slider in sliders}
    check(sorted(by_var) == ["BoobsSize", "NippleLength"] and
          by_var["BoobsSize"]["base_file"] == "BodyPosition.buf" and
          by_var["NippleLength"]["target_file"] == "BodyPosition.nipple.buf",
          f"repeated t50/t51 morph blocks share their authored base (got {sliders})")


def test_wwmi_sparse_shape_slider_is_discovered():
    text = r"""
[Constants]
global persist $BoobsSize = 0
global $shapekey_vertex_offset_batch1 = 43085

[CommandListDrawSlider.Boobs]
x87 = $BoobsSize * x87

[CommandListSetBoobsSize]
$\WWMIv1\shapekey_id = 161
$\WWMIv1\shapekey_value = $BoobsSize

[CommandListSetupShapeKeysBatch]
cs-t33 = ResourceShapeKeyOffsetBuffer
[CommandListLoadShapeKeysBatch]
cs-t0 = ResourceShapeKeyVertexIdBuffer
cs-t1 = ResourceShapeKeyVertexOffsetBuffer
[CommandListApplyShapeKeys]
cs-t6 = ResourcePositionBuffer

[ResourcePositionBuffer]
stride = 12
filename = Meshes/Position.buf
[ResourceShapeKeyOffsetBuffer]
filename = Meshes/ShapeKeyOffset.buf
[ResourceShapeKeyVertexIdBuffer]
filename = Meshes/ShapeKeyVertexId.buf
[ResourceShapeKeyVertexOffsetBuffer]
filename = Meshes/ShapeKeyVertexOffset.buf
"""
    secs = sections(text)
    sliders = extract_shape_sliders(secs, extract_resources(secs))
    check(len(sliders) == 1, f"one WWMI sparse slider is found (got {sliders})")
    slider = sliders[0]
    check(slider.get("shape_id") == 161 and slider.get("buffer_shape_id") == 162 and
          slider.get("sparse_entry_offset") == 43085 and
          slider.get("vertex_offset_file") == "Meshes/ShapeKeyVertexOffset.buf",
          f"WWMI slider aligns its key ID, batch records, and sparse buffers (got {slider})")


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
    check(slots[1]["values"] == ["0", "1", "2", "3"],
          f"modulo cycle exposes every value (got {slots[1]['values']})")
    rules = extract_state_rules(secs)
    piece_rules = [rule for rule in rules if rule["var"] == "piece"]
    check(len(piece_rules) == 2 and piece_rules[1]["value"] == "1",
          f"Present-derived literal draw flags are modelled (got {piece_rules})")
    check("piece" in gating_var_names(secs),
          "a Present-derived flag remains available to gate draw meshes")
    check(all(not any(a["var"] == b["var"] and a["value"] == b["value"] and
                          a["negate"] != b["negate"]
                          for i, a in enumerate(group) for b in group[i + 1:])
              for rule in rules for group in rule["conditions"]),
          "impossible elif alternatives are removed from state rules")


def test_zzmi_midpoint_pair_sliders_are_discovered():
    text = r"""
[Constants]
global persist $Bottom = 0
global persist $Breast = 0
[CommandListDrawSlider.Bottom]
x87 = 202 / $ww * $Bottom
[CommandListDrawSlider.Breast]
x87 = 202 / $ww * $Breast
[CommandListKeys]
cs-t50 = copy ResourceBodyBase
cs-t51 = copy ResourceBodyBigBottom
cs-t52 = copy ResourceBodySmallBottom
cs-t53 = copy ResourceBodyBigBreast
cs-t54 = copy ResourceBodySmallBreast
x88 = $Bottom
x89 = $Breast
[ResourceBodyBase]
stride = 40
filename = Body.buf
[ResourceBodyBigBottom]
stride = 40
filename = BodyBigBottom.buf
[ResourceBodySmallBottom]
stride = 40
filename = BodySmallBottom.buf
[ResourceBodyBigBreast]
stride = 40
filename = BodyBigBreast.buf
[ResourceBodySmallBreast]
stride = 40
filename = BodySmallBreast.buf
"""
    secs = sections(text)
    sliders = extract_shape_sliders(secs, extract_resources(secs))
    by_var = {slider["var"]: slider for slider in sliders}
    check(sorted(by_var) == ["Bottom", "Breast"],
          f"both multi-target sliders are found (got {sorted(by_var)})")
    check(by_var["Bottom"].get("mode") == "midpoint_pair" and
          by_var["Bottom"].get("low_file") == "BodySmallBottom.buf" and
          by_var["Bottom"].get("target_file") == "BodyBigBottom.buf",
          f"bottom slider links its smaller and bigger buffers (got {by_var['Bottom']})")


def test_zzmi_midpoint_bindings_do_not_cross_commandlists():
    """x88/x89 are generic ini registers also used by unrelated UI shaders.
    A scalar from another CommandList must never claim a five-buffer shape set."""
    text = r"""
[Constants]
global persist $ActualBottom = 0
global persist $UIAnim = 0
[CommandListDrawSlider.Bottom]
x87 = 202 / $ww * $ActualBottom
[CommandListShapeBuffers]
cs-t50 = copy ResourceBodyBase
cs-t51 = copy ResourceBodyBigBottom
cs-t52 = copy ResourceBodySmallBottom
cs-t53 = copy ResourceBodyBigBreast
cs-t54 = copy ResourceBodySmallBreast
[CommandListUnrelatedUI]
x88 = $UIAnim
x89 = $UIAnim
[ResourceBodyBase]
stride = 40
filename = Body.buf
[ResourceBodyBigBottom]
stride = 40
filename = BodyBigBottom.buf
[ResourceBodySmallBottom]
stride = 40
filename = BodySmallBottom.buf
[ResourceBodyBigBreast]
stride = 40
filename = BodyBigBreast.buf
[ResourceBodySmallBreast]
stride = 40
filename = BodySmallBreast.buf
"""
    secs = sections(text)
    sliders = extract_shape_sliders(secs, extract_resources(secs))
    check(sliders == [],
          f"unrelated register writes do not create shape sliders (got {sliders})")


def test_recognized_menu_slots_get_authored_images():
    text = r"""
[CommandListClickedSlot]
if $clickedSlot == 1
  $top = 1 - $top
elif $clickedSlot == 2
  $hair = 1 - $hair
endif
[CommandListSlotItemImage]
if $slot == 1
  ps-t100 = resourcemenuitem.top
elif $slot == 2
  ps-t100 = RESOURCEMENUITEM.HAIR
endif
[ResourceMenuItem.Top]
filename = ui/top.png
[ResourceMenuItem.Hair]
filename = ui/hair.png
"""
    secs = sections(text)
    menu = extract_menu_toggles(secs)
    attach_menu_images(menu, secs, extract_resources(secs))
    by_slot = _by_slot(menu)
    check(by_slot[1].get("image_file") == "ui/top.png" and
          by_slot[2].get("image_file") == "ui/hair.png",
          f"recognized slots retain their authored item images (got {by_slot})")


def test_generated_button_grid_gets_shared_authored_images():
    """Responsive grids dispatch icons by an arbitrary button counter; the
    same authored image is often deliberately used for every toggle."""
    text = r"""
[CommandListSetButtonCondition]
if $Button_number == 2
  $Hair = 1 - $Hair
else if $Button_number == 3
  $Eyes = $Eyes + 1
  if $Eyes > 2
    $Eyes = 0
  endif
endif
[CommandListSetButtonIcon]
if $Button_number == 1
  ps-t100 = resourceitembody
else if $Button_number == 2
  ps-t100 = resourceitembutton
else if $Button_number == 3
  ps-t100 = RESOURCEITEMBUTTON
endif
[ResourceItemBody]
filename = res/item_body.png
[ResourceItemButton]
filename = res/item_button.png
"""
    secs = sections(text)
    menu = extract_menu_toggles(secs)
    attach_menu_images(menu, secs, extract_resources(secs))
    by_slot = _by_slot(menu)
    check(by_slot[2].get("image_file") == "res/item_button.png" and
          by_slot[3].get("image_file") == "res/item_button.png",
          f"counter-dispatched shared icons activate image layout (got {by_slot})")


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
    check(sorted(by_slot) == [2, 8],
          f"both numbered arrow-pair items are found (got {sorted(by_slot)})")
    check(by_slot[2]["var"] == "Top" and
          by_slot[2]["values"] == ["0", "1", "2", "3", "4"],
          f"Top range comes from its decrement/increment wraps (got {by_slot[2]})")
    check(by_slot[8]["values"] == ["1", "2", "3", "4", "5"],
          f"non-zero Hair range is preserved (got {by_slot[8]['values']})")
    check(by_slot[2].get("image_file") == "ui/top.dds" and
          by_slot[8].get("image_file") == "ui/hair.dds",
          f"IconN artwork maps to ButtonN (got {by_slot})")


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
        check(decoded.mode == "RGBA" and decoded.getpixel((0, 0))[3] == 0 and
              decoded.getpixel((20, 20))[3] == 255,
              f"menu PNG keeps transparent and opaque pixels (got {decoded.mode})")


def test_fully_transparent_menu_placeholder_uses_fallback():
    with tempfile.TemporaryDirectory() as tmp:
        Image.new("RGBA", (52, 52), (0, 0, 0, 0)).save(
            os.path.join(tmp, "blank.png"))
        menu = {"one": {"name": "hat", "slot": 1, "var": "hat",
                        "values": ["0", "1"], "effects": [], "source": None,
                        "ini_path": None, "section": "CommandListMenu",
                        "image_file": "blank.png"}}
        entry = build_menu_panel(menu, {}, mod_dir=tmp)["one"]
        check(entry.get("image") is None and entry.get("image_slot") is True,
              f"empty artwork retains grid membership but no blank image (got {entry})")


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
               test_menu_panel_lists_slots_that_gate_nothing,
               test_commandlist_section_name_is_case_insensitive,
               test_nested_paged_slot_chains_are_all_discovered,
               test_compute_shape_slider_is_discovered_and_modelled,
               test_compute_shape_resource_names_are_case_insensitive,
               test_parenthesized_increment_modulo_menu_cycle_is_discovered,
               test_repeated_full_buffer_shape_blocks_are_discovered,
               test_wwmi_sparse_shape_slider_is_discovered,
               test_modulo_cycle_and_present_derived_rules,
               test_zzmi_midpoint_pair_sliders_are_discovered,
               test_zzmi_midpoint_bindings_do_not_cross_commandlists,
               test_recognized_menu_slots_get_authored_images,
               test_generated_button_grid_gets_shared_authored_images,
               test_arrow_pair_menu_is_discovered_with_numbered_icons,
               test_menu_panel_preserves_authored_transparency,
               test_fully_transparent_menu_placeholder_uses_fallback):
        fn()
    print("\n" + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"))
    sys.exit(1 if FAILS else 0)

"""Public menu contracts: state transitions, conservative discovery and artwork."""

import base64
import io
from textwrap import dedent

import pytest
from PIL import Image

from app.mods.controls import build_menu_panel
from core.ini.analysis import analyze_ini
from core.ini.menu import extract_controller_toggles, extract_menu_toggles, extract_menu_var_names
from core.ini.sections import extract_resources, parse_sections


def _sections(text, path="generated.ini"):
    return parse_sections(str(path), text=dedent(text))


def _menu(text, **options):
    parsed = _sections(text)
    return extract_menu_toggles(parsed, resources=extract_resources(parsed), **options)


def _dispatch(selector, items):
    return "\n".join(
        f"{'if' if index == 0 else 'elif'} ${selector} == {slot}\n{body}"
        for index, (slot, body) in enumerate(items)) + "\nendif\n"


@pytest.mark.parametrize("action,expected", [
    ("$Palette = 1 - $PALETTE", ["0", "1"]),
    ("$Palette = ($PALETTE + 1) % 4", ["0", "1", "2", "3"]),
    ("$Palette = $PALETTE + 1\n$PALETTE = $palette % 3", ["0", "1", "2"]),
    ("$Palette = 1 + $Palette\nif $Palette > 3\n$Palette = 1.00\nendif",
     ["1", "2", "3"]),
    ("if $PALETTE <= 2.0\n$Palette = $palette + 1\nelse\n$PALETTE = 0.0\nendif",
     ["0", "1", "2", "3"]),
    ("$Palette = $PALETTE + 1\nif $palette >= 3\n$PALETTE = -1.0\nendif",
     ["-1", "0", "1", "2"]),
    ("$Palette = $PALETTE + 1\nif $palette > 3\n$PALETTE = 0.25\nendif", None),
    ("$Palette = $PALETTE + 1\nif $palette > 1\n$PALETTE = 2\nendif", None),
    ("if $visible\n$Palette = $Palette + 1\nif $Palette > 2\n$Palette = 0\nendif\nendif",
     ["0", "1", "2"]),
    ("$Palette = $Palette + 1\nif $hovered\nif $Palette > 2\n$Palette = 0\nendif\nendif", None),
    ("$Palette = $Palette + 1\nif $Palette > 2\nif $hovered\n$Palette = 0\nendif\nendif", None),
    ("if $visible\n$Palette = $Palette + 1\nelse\nif $Palette > 2\n$Palette = 0\nendif\nendif", None),
    ("if $visible\nif $Palette < 2\n$Palette = $Palette + 1\nelse\n$Palette = 0\nendif\nendif",
     ["0", "1", "2"]),
    ("if $Palette < 2\n$Palette = $Palette + 1\nelse\nif $hovered\n$Palette = 0\nendif\nendif", None),
], ids=["flip", "inline-modulo", "sequential-modulo", "decimal-reset",
        "inclusive-before-step", "exclusive-after-step", "fractional", "reversed",
        "common-guard", "guarded-wrap", "guarded-reset", "sibling-branches",
        "common-guard-before-step", "guarded-before-step-reset"])
def test_slot_cycles_have_exact_values_and_case_insensitive_identity(action, expected):
    text = "[Constants]\nglobal persist $Palette = 0\n[CommandListSelect]\n"
    text += _dispatch("selection", [
        (11, action), (23, "$anchor = 1 - $anchor"), (29, "$guide = 1 - $guide")])
    controls = {entry["var"]: entry for entry in _menu(text).values()}
    assert controls["anchor"]["values"] == ["0", "1"]
    assert controls["guide"]["values"] == ["0", "1"]
    if expected is None:
        assert "Palette" not in controls
    else:
        assert controls["Palette"]["values"] == expected
        assert controls["Palette"]["effects"] == []


def test_menu_effects_identity_draw_gates_and_thumbnail_survive_projection(tmp_path):
    image = Image.new("RGBA", (3, 3), (30, 60, 90, 0))
    image.putpixel((1, 1), (90, 60, 30, 255))
    image.save(tmp_path / "badge.png")
    text = """
[Constants]
global persist $Mode = 1
global persist $Accent = 0
global $First = 0
global $Second = 1
[CommandListChoose]
if $picked == 41
    if $MODE < 1
        $FIRST = 1
        $Mode = $mode + 1
        $second = 0
    else
        $First = 0
        $MODE = 0.0
        $SECOND = 1
    endif
else if $picked == 42
    $Accent = 1 - $ACCENT
    if $accent >= 1
        $ACCENT = 0
    endif
endif
[CommandListIcon41]
ps-t100 = ResourceBadge
[CommandListIcon42]
ps-t100 = ResourceBadge
[ResourceBadge]
filename = badge.png
[TextureOverrideVisible]
vb0 = ResourceVertex
vb1 = ResourceUV
ib = ResourceIndex
if $MODE == 1
    drawindexed = 3, 0, 0
endif
[ResourceIndex]
filename = index.buf
format = DXGI_FORMAT_R32_UINT
[ResourceVertex]
filename = vertex.buf
stride = 40
[ResourceUV]
filename = uv.buf
stride = 20
"""
    path = tmp_path / "config.ini"
    parsed = _sections(text, path)
    analysis = analyze_ini(parsed, var_prefix="piece::", source="piece")
    mode, accent = analysis.menu.values()
    assert mode["var"] == "piece::Mode"
    assert mode["values"] == ["0", "1"]
    assert mode["ini_path"] == str(path)
    assert mode["section"] == "CommandListChoose"
    assert [(effect["var"], effect["value"]) for effect in mode["effects"]] == [
        ("piece::First", "1"), ("piece::Second", "0"),
        ("piece::First", "0"), ("piece::Second", "1")]
    assert [effect["when"] for effect in mode["effects"]] == [
        {"var": "piece::Mode", "op": "<", "value": "1"},
        {"var": "piece::Mode", "op": "<", "value": "1"},
        {"var": "piece::Mode", "op": ">=", "value": "1"},
        {"var": "piece::Mode", "op": ">=", "value": "1"}]
    assert accent["effects"] == [{
        "when": {"var": "piece::Accent", "op": ">=", "value": "1"},
        "var": "piece::Accent", "value": "0"}]
    variables = {"piece::Mode", "piece::Accent", "piece::First", "piece::Second"}
    assert extract_menu_var_names(parsed, menu=analysis.menu) == variables
    assert variables <= analysis.gating_vars
    assert analysis.draw_groups[0]["draws"][0].conditions == [[
        {"var": "piece::Mode", "value": "1", "negate": False}]]
    panel = build_menu_panel(analysis.menu, analysis.defaults, mod_dir=str(tmp_path))
    displayed = panel["piece::CommandListChoose#41"]
    assert displayed["default"] == "1"
    assert displayed["effects"] == mode["effects"]
    assert displayed["ini"] == "config.ini"
    decoded = Image.open(io.BytesIO(base64.b64decode(displayed["image"].split(",", 1)[1])))
    assert decoded.mode == "RGBA"
    assert decoded.getpixel((0, 0))[3] == 0
    assert decoded.getpixel((1, 1))[3] == 255


@pytest.mark.parametrize("layout", ["separate", "shared", "paged"])
def test_artwork_alignment_uses_independent_dispatch_chains(layout):
    first = _dispatch("picked", [(slot, f"$option{index} = 1 - $option{index}")
                                 for index, slot in enumerate((52, 55, 59))])
    second = _dispatch("picked", [(88, "$extraA = 1 - $extraA"),
                                  (90, "$extraB = 1 - $extraB")])
    if layout == "separate":
        actions = "[CommandListFirst]\n" + first + "[CommandListSecond]\n" + second
    elif layout == "shared":
        actions = "[CommandListActions]\n" + first + second
    else:
        actions = "[CommandListPages]\nif $page == 0\n" + first + "else\n" + second + "endif\n"
    artwork = "[CommandListBadges]\n" + _dispatch("image", [
        (2, "ps-t100 = ResourceA"), (5, "ps-t100 = ResourceB"), (9, "ps-t100 = ResourceC")])
    resources = "\n".join(f"[Resource{letter}]\nfilename = icons/{letter}.dds"
                          for letter in "ABC")
    menu = _menu(actions + artwork + resources)
    assert {entry["slot"]: entry.get("image_file") for entry in menu.values()} == {
        52: "icons/A.dds", 55: "icons/B.dds", 59: "icons/C.dds", 88: None, 90: None}


@pytest.mark.parametrize("action_groups,image_slots,expected_slots", [
    ([(72, 76, 81)], (2, 6, 10), set()),
    ([(72, 76, 81)], (2, 6), set()),
    ([(72, 76, 81), (122, 126, 131)], (2, 6, 11), set()),
    ([(2, 6, 11), (72, 76, 81)], (2, 6, 11), {2, 6, 11}),
])
def test_artwork_rejects_partial_or_ambiguous_offsets_and_prefers_exact_slots(
        action_groups, image_slots, expected_slots):
    text = "[CommandListActions]\n"
    for group_index, group in enumerate(action_groups):
        text += _dispatch("picked", [
            (slot, f"$item{group_index}_{index} = 1 - $item{group_index}_{index}")
            for index, slot in enumerate(group)])
    text += "[CommandListBadges]\n" + _dispatch("image", [
        (slot, "ps-t100 = ResourceBadge") for slot in image_slots])
    text += "[ResourceBadge]\nfilename = badge.dds\n"
    menu = _menu(text)
    assert len(menu) == sum(map(len, action_groups))
    assert {entry["slot"] for entry in menu.values() if entry.get("image_file")} == expected_slots


def test_conflicting_artwork_does_not_guess_and_reused_page_slots_remain_distinct():
    actions = _dispatch("picked", [(6, "$left = 1 - $left"), (9, "$right = 1 - $right")])
    other = _dispatch("picked", [(6, "$upper = 1 - $upper"), (9, "$lower = 1 - $lower")])
    text = "[CommandListPages]\nif $page == 0\n" + actions + "else\n" + other + "endif\n"
    text += "[CommandListBadges]\n" + _dispatch("image", [
        (6, "ps-t100 = ResourceA"), (9, "ps-t100 = ResourceB")])
    text += "[ResourceA]\nfilename = a.dds\n[ResourceB]\nfilename = b.dds\n"
    menu = _menu(text, var_prefix="piece::", source="piece")
    assert list(menu) == ["piece::CommandListPages#6", "piece::CommandListPages#9",
                          "piece::CommandListPages#6_2", "piece::CommandListPages#9_2"]
    assert [entry["var"] for entry in menu.values()] == [
        "piece::left", "piece::right", "piece::upper", "piece::lower"]
    assert [entry["image_file"] for entry in menu.values()] == ["a.dds", "b.dds"] * 2
    conflicting = text + "[ResourceAlternative]\nfilename = alternative.dds\n"
    conflicting += "[CommandListAlternative]\n" + _dispatch("image", [
        (6, "ps-t100 = ResourceAlternative"), (9, "ps-t100 = ResourceB")])
    changed = _menu(conflicting)
    assert [entry.get("image_file") for entry in changed.values()] == [None, "b.dds"] * 2


@pytest.mark.parametrize("action,expected", [
    ("$Palette = $PALETTE + 1\nif $palette > 4\n$PALETTE = 1.0\nendif", ["1", "2", "3", "4"]),
    ("$Palette = $PALETTE - 1\nif $palette <= 0\n$PALETTE = 4.0\nendif", ["1", "2", "3", "4"]),
    ("if $visible\n$Palette = $PALETTE + 1\nif $palette > 2\n$PALETTE = 0\nendif\nendif", ["0", "1", "2"]),
    ("$Palette = $PALETTE + 1\nif $palette > 2\nif $hovered\n$PALETTE = 0\nendif\nendif", None),
    ("$Palette = $PALETTE + 1\nif $hovered\nif $palette > 2\n$PALETTE = 0\nendif\nendif", None),
    ("$Palette = $PALETTE + 1\nif $palette > 2\nelse\n$PALETTE = 0\nendif", None),
    ("$Palette = $PALETTE + 1\nif $palette > 2\n$PALETTE = 0.5\nendif", None),
    ("$Palette = $PALETTE + 1\nif $palette > 2\n$PALETTE = 0", None),
    ("$frame = $frame + 1\n$Palette = $Palette + 1\nif $Palette > 3\n$Palette = 0\nendif",
     ["0", "1", "2", "3"]),
], ids=["increment", "decrement", "common-guard", "guarded-reset", "guarded-wrap",
        "else-only-reset", "fractional-reset", "malformed", "bookkeeping-before-cycle"])
def test_arrow_cycles_require_a_reset_in_the_same_execution_body(action, expected):
    text = "[Constants]\nglobal persist $Palette = 0\n[CommandListButton7Right]\n" + action
    text += """
[CommandListButton18Right]
$anchor = $anchor + 1
if $anchor > 1
$anchor = 0
endif
[CommandListButton19Right]
$guide = $guide + 1
if $guide > 1
$guide = 0
endif
[CommandListIcon7]
ps-t100 = ResourceMissing
ps-t100 = ResourceBadge
ps-t100 = ResourceFrame
[ResourceBadge]
filename = badge.dds
[ResourceFrame]
filename = frame.dds
"""
    controls = {entry["var"]: entry for entry in _menu(text).values()}
    if expected is None:
        assert "Palette" not in controls
    else:
        assert controls["Palette"]["values"] == expected
        assert controls["Palette"]["image_file"] == "badge.dds"
        assert controls["Palette"]["section"] == "CommandListButton7Right"


@pytest.mark.parametrize("action,expected", [
    ("$Palette = $PALETTE + $trigger\nif $palette > 3\n$PALETTE = 0.000\nendif",
     ["0", "1", "2", "3"]),
    ("$Palette = $PALETTE + $trigger\nif $palette > 3\n$PALETTE = 1.0\nendif",
     ["1", "2", "3"]),
    ("$Palette = $PALETTE + $trigger\nif $palette > 3\n$PALETTE = 0.5\nendif", None),
    ("if $visible\n$Palette = $Palette + $trigger\nif $Palette > 3\n$Palette = 0\nendif\nendif",
     ["0", "1", "2", "3"]),
    ("$Palette = $Palette + $trigger\nif $hovered\nif $Palette > 3\n$Palette = 0\nendif\nendif", None),
    ("$Palette = $Palette + $trigger\nif $Palette > 3\nif $hovered\n$Palette = 0\nendif\nendif", None),
    ("if $visible\n$Palette = $Palette + $trigger\nelse\nif $Palette > 3\n$Palette = 0\nendif\nendif", None),
    ("if $visible\nif $Palette > 3\n$Palette = 0\nelse\n$Palette = $Palette + $trigger\nendif\nendif",
     ["0", "1", "2", "3"]),
    ("if $Palette > 3\nif $hovered\n$Palette = 0\nendif\nelse\n$Palette = $Palette + $trigger\nendif", None),
], ids=["decimal-zero", "decimal-one", "fractional", "common-guard", "guarded-wrap",
        "guarded-reset", "sibling-branches", "common-guard-before-step", "guarded-before-step-reset"])
def test_forwarded_controllers_use_exact_cycles_and_keep_pulse_artwork(action, expected):
    parsed = _sections(f"""
[Constants]
global persist $Palette = 1
[CommandListPulse]
$trigger = 1 - $TRIGGER
[CommandListBadge]
if $trigger == 0
ps-t100 = ResourceBadge
else
ps-t100 = ResourceBadge
endif
[ResourceBadge]
filename = badge.dds
[Present]
{action}
""")
    resources = extract_resources(parsed)
    assert extract_controller_toggles(parsed, set(), resources=resources) == {}
    controls = extract_controller_toggles(parsed, {"palette"}, var_prefix="piece::",
                                          source="piece", resources=resources)
    if expected is None:
        assert controls == {}
    else:
        assert controls == {"Palette": {
            "name": "Palette", "var": "piece::Palette", "values": expected,
            "effects": [], "source": "piece", "ini_path": "generated.ini",
            "section": "Present", "_pulse_var": "trigger", "image_file": "badge.dds"}}


def test_mouse_regions_preserve_inactive_ordinals_and_require_finite_mouse_actions():
    text = """
[Constants]
global $Visible = 1
global $Limit = 2
[KeyClick]
key = VK_LBUTTON
type = hold
$pressed = 1
[CommandListRegions]
"""
    for guard, variable in (("$Visible >= 1", "first"), ("$Visible >= 2", "hidden"),
                            ("$Visible >= 1", "last")):
        text += (f"if {guard}\nif $pressed && cursor_y > $top && cursor_y < $bottom\n"
                 f"if ${variable} < $Limit\n${variable} = ${variable} + 1\n"
                 f"else\n${variable} = 0.0\nendif\nendif\nendif\n")
    text += """
[CommandListDrawButton_0]
ps-t100 = ResourceFrame
ps-t100 = ResourceBadge
[CommandListDrawButton_2]
ps-t100 = ResourceBadge
[ResourceFrame]
filename = frame.dds
[ResourceBadge]
filename = badge.dds
"""
    menu = _menu(text)
    assert [(entry["slot"], entry["var"], entry["values"], entry["image_file"])
            for entry in menu.values()] == [(0, "first", ["0", "1", "2"], "badge.dds"),
                                          (2, "last", ["0", "1", "2"], "badge.dds")]
    assert all(entry["kind"] == "mouse_region" for entry in menu.values())
    assert _menu(text.replace("VK_LBUTTON", "k")) == {}
    assert _menu(text + "[Present]\n$Limit = 4\n") == {}


@pytest.mark.parametrize("body", [
    "if $picked == 6\n$temporary = 1 - $temporary\nendif",
    "if $picked == 6\nps-t100 = ResourceBadge\nelif $picked == 9\nps-t100 = ResourceBadge\nendif",
    "if $picked == 6\n$temporary = 1\nelif $picked == 9\n$other = 0\nendif",
])
def test_bookkeeping_and_artwork_only_sections_do_not_create_controls(body):
    assert _menu("[CommandListUtility]\n" + body) == {}

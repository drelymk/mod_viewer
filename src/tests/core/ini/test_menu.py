"""Public menu contracts: state transitions, conservative discovery and artwork."""

from textwrap import dedent

import pytest
from PIL import Image

from app.mods.controls import build_menu_panel
from core.ini.analysis import analyze_ini
from core.ini.menu import extract_controller_toggles, extract_menu_toggles, extract_menu_var_names
from core.ini.sections import extract_resources, parse_sections


def _sections(text, path="generated.ini"):
    return parse_sections(str(path), text=dedent(text))


def _menu(text, *, path="generated.ini", **options):
    parsed = _sections(text, path)
    return extract_menu_toggles(parsed, resources=extract_resources(parsed), **options)


def _dispatch(selector, items):
    return "\n".join(
        f"{'if' if index == 0 else 'elif'} ${selector} == {slot}\n{body}"
        for index, (slot, body) in enumerate(items)) + "\nendif\n"


def _flow_menu(step='$Mode + $Delta', *, prepare='', upper=3, lower=0,
               resource='ResourceFirst', renderer='CustomShader Paint'):
    return f"""
[Constants]
global persist $Mode = 0
global $Delta = 7
[KeyForward]
key = VK_LBUTTON
$Direction = 1
run = CommandList Prepare
[KeyBackward]
key = VK_RBUTTON
$Direction = -1
run = CommandList Prepare
[CommandList Prepare]
$Delta = $Direction * 1
{prepare}
run = CommandList Actions Ω
[CommandList Actions Ω]
if $Picked == 11
    $Mode = {step}
    if $Mode > {upper}
        $Mode = 0
    elif $Mode < {lower}
        $Mode = 3
    endif
elif $Picked == 19
    $Aux = 1 - $Aux
elif $Picked == 27
    $Anchor = 1 - $Anchor
endif
[CommandList Paint]
$DrawID = 11
ps-t23 = {resource}
run = CommandList Tile
$DrawID = 19
ps-t23 = ResourceSecond
run = CommandList Tile
[CommandList Tile]
$AliasID = $DrawID + 0
run = CommandList Hit
run = CommandList Render
[CommandList Hit]
if $PointerX > $Left && $PointerY > $Top
    $Picked = $AliasID
endif
[CommandList Render]
run = {renderer}
[CustomShader Paint]
draw = 4, 0
[ResourceFirst]
filename = icons/first.dds
[ResourceSecond]
filename = icons/second.dds
"""


@pytest.mark.parametrize('step', [
    '$Mode + $Delta', '($DELTA + $mode)', '$Mode - (0 - $Delta)',
    '$Mode + ($Delta * 2) / 2',
])
def test_caller_values_and_shared_helpers_resolve_cycles_and_artwork(step):
    text = _flow_menu(step)
    entries = list(_menu(text, var_prefix='piece::').values())
    assert [(entry['var'], entry['values'], entry.get('image_file')) for entry in entries] == [
        ('piece::Mode', ['0', '1', '2', '3'], 'icons/first.dds'),
        ('piece::Aux', ['0', '1'], 'icons/second.dds'),
        ('piece::Anchor', ['0', '1'], None)]
    # Reanalysis of staged text must not reuse caller values or artwork from
    # another INI revision; identities are independent of helper spelling.
    changed = text.replace('$Mode > 3', '$Mode > 2').replace('$Mode = 3', '$Mode = 2')
    changed = changed.replace('icons/first.dds', 'icons/revised.dds')
    changed_entries = list(_menu(changed, var_prefix='other::').values())
    assert changed_entries[0]['var'] == 'other::Mode'
    assert changed_entries[0]['values'] == ['0', '1', '2']
    assert changed_entries[0]['image_file'] == 'icons/revised.dds'
    assert entries[0]['values'] == ['0', '1', '2', '3']


@pytest.mark.parametrize('options', [
    {'prepare': '$Delta = time'},
    {'prepare': '$Delta += time'},
    {'prepare': 'run = CommandList External'},
    {'prepare': 'run = CustomShader Unknown'},
    {'prepare': '$Delta = 2'},
    {'prepare': 'run = CommandList Prepare'},
    {'upper': 2},
    {'upper': 1000000000},
    {'lower': 1},
    {'step': '$Mode * $Mode + $Delta'},
    {'step': '$Mode + $Delta / 0'},
], ids=['runtime-value', 'unknown-write', 'external-call', 'shader-side-effects',
        'non-unit-step', 'recursive-call', 'mismatched-high', 'excessive-range', 'mismatched-low',
        'nonlinear', 'zero-division'])
def test_control_flow_refuses_unproven_steps_without_hiding_known_controls(options):
    controls = {entry['var']: entry for entry in _menu(_flow_menu(**options)).values()}
    assert 'Mode' not in controls
    assert controls['Aux']['values'] == ['0', '1']


@pytest.mark.parametrize('replacement', [
    ('run = CommandList Render', 'run = CommandList Missing'),
    ('run = CustomShader Paint', 'run = CustomShader Missing'),
    ('ps-t23 = ResourceFirst', 'ps-t23 = ResourceMissing'),
    ('$DrawID = 11', '$DrawID = time'),
    ('$Picked = $AliasID', '$OtherSelector = $AliasID'),
    ('run = CommandList Render', '$AliasID = 19\nrun = CommandList Render'),
    ('run = CommandList Render', 'ps-t23 = ResourceFirst\nrun = CommandList Render'),
    ('run = CommandList Hit', 'run = CommandList Tile'),
], ids=['unknown-helper', 'unknown-shader', 'unknown-resource', 'unknown-id',
        'different-selector', 'changed-id', 'different-binding', 'recursive-helper'])
def test_shared_artwork_requires_proven_identity_and_rendering(replacement):
    text = _flow_menu().replace(*replacement)
    controls = {entry['var']: entry for entry in _menu(text).values()}
    assert controls['Mode']['values'] == ['0', '1', '2', '3']
    assert controls['Mode'].get('image_file') is None


@pytest.mark.parametrize('alternate,expected', [
    ('ResourceFirst', 'icons/first.dds'),
    ('ResourceAlias', 'icons/first.dds'),
    ('ResourceSecond', None),
    ('ResourceMissing', None),
], ids=['same-resource', 'same-file', 'conflicting-artwork', 'missing-artwork'])
def test_shared_artwork_joins_conflicts_and_follows_resource_references(alternate, expected):
    text = _flow_menu().replace('ps-t23 = ResourceFirst',
                               'ps-t31 = ResourceFirst\nResourceHeld = ref ps-t31\nps-t23 = ref ResourceHeld\nps-t31 = null')
    text += f"""
[CommandList Alternative]
$DrawID = 11
ps-t23 = {alternate}
run = CommandList Tile
[ResourceAlias]
filename = icons/first.dds
"""
    controls = {entry['var']: entry for entry in _menu(text).values()}
    assert controls['Mode'].get('image_file') == expected
    assert controls['Aux']['image_file'] == 'icons/second.dds'


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
    run = CustomShaderRefresh
else if $picked == 42
    $Accent = 1 - $ACCENT
    if $accent >= 1
        $ACCENT = 0
    endif
    run = CustomShaderRefresh
endif
[CustomShaderRefresh]
cs = refresh.hlsl
dispatch = 1, 1, 1
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
else
    drawindexed = 3, 3, 0
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
    assert analysis.draw_groups[0]["draws"][1].conditions == [[
        {"var": "piece::Mode", "value": "1", "negate": True}]]
    panel = build_menu_panel(analysis.menu, analysis.defaults, mod_dir=str(tmp_path),
                             image_source=lambda _path: "/texture/test/0.png")
    displayed = panel["piece::CommandListChoose#41"]
    assert displayed["default"] == "1"
    assert displayed["effects"] == mode["effects"]
    assert displayed["ini"] == "config.ini"
    assert displayed["image"] == "/texture/test/0.png"



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


@pytest.mark.parametrize("right_var,right_max,side_effects,expected", [
    ("MODE", 2, False, [(7, "Mode", ["0", "1", "2"])]),
    ("Accent", 3, False, [(7, "Mode", ["0", "1", "2"]),
                          (7, "Accent", ["0", "1", "2", "3"])]),
    ("Mode", 3, False, [(7, "Mode", ["0", "1", "2"]),
                        (7, "Mode", ["0", "1", "2", "3"])]),
    ("MODE", 2, True, [(7, "Mode", ["0", "1", "2"]),
                       (7, "Mode", ["0", "1", "2"])]),
], ids=["shared-cycle", "distinct-side-actions", "different-ranges", "different-effects"])
def test_numbered_mouse_arrows_share_artwork_and_preserve_distinct_actions(
        tmp_path, right_var, right_max, side_effects, expected):
    def button(slot, side, variable, maximum):
        hit = ("cursor_x > z87 && cursor_x < z87 + x87 / 2" if side == "Left"
               else "cursor_x > z87 + x87 / 2 && cursor_x < z87 + x87")
        step, guard, reset = (("-", "< 0", maximum) if side == "Left"
                              else ("+", f"> {maximum}", 0))
        effect = (f"$Companion = {int(side == 'Right')}" if side_effects and slot == 7 else "")
        return f"""
[CommandListButton{slot}{side}]
x87 = $size * res_height/res_width
y87 = $size
z87 = $offset_x + {slot} * $spacing
w87 = $offset_y
if {hit} && cursor_y > w87 && cursor_y < w87 + y87
    if $click
        if $latch == 0
            ${variable} = ${variable} {step} 1
            if ${variable} {guard}
                ${variable} = {reset}
            endif
            {effect}
            $latch = 1
        endif
    endif
endif
"""

    text = """
[Constants]
global persist $Mode = 1
global persist $Accent = 0
global persist $Anchor = 0
global $click = 0
global $latch = 0
[KeyClick]
key = VK_RBUTTON
type = hold
$click = 1
"""
    text += button(7, "Left", "Mode", 2) + button(7, "Right", right_var, right_max)
    text += button(18, "Left", "Anchor", 1) + button(18, "Right", "Anchor", 1)
    text += """
[CommandListUtility]
if $click && cursor_x > $left && cursor_x < $left + $width
    if cursor_y > $top && cursor_y < $top + $height
        $Utility = 1 - $Utility
    endif
endif
"""
    for slot in (7, 18):
        text += (f"[CommandListIcon{slot}]\nps-t100 = ResourceBadge{slot}\n"
                 f"[ResourceBadge{slot}]\nfilename = badge-{slot}.png\n")
        Image.new("RGBA", (2, 2), (30, 60, 90, 255)).save(tmp_path / f"badge-{slot}.png")

    menu = _menu(text, path=tmp_path / 'generated.ini', var_prefix="piece::")
    entries = list(menu.values())
    expected = [*expected, (18, "Anchor", ["0", "1"])]
    assert [(entry["slot"], entry["var"], entry["values"])
            for entry in entries[:-1]] == [
                (slot, f"piece::{variable}", values) for slot, variable, values in expected]
    assert [entry["image_file"] for entry in entries[:-1]] == [
        f"badge-{slot}.png" for slot, _variable, _values in expected]
    if side_effects:
        assert [entry["section"] for entry in entries if entry["slot"] == 7] == [
            "CommandListButton7Left", "CommandListButton7Right"]
    for entry in entries[:-1]:
        effects = []
        if side_effects and entry["slot"] == 7:
            effects.append({"when": None, "var": "piece::Companion",
                            "value": str(int(entry["section"].endswith("Right")))})
        effects.append({"when": None, "var": "piece::latch", "value": "1"})
        assert entry["effects"] == effects
    assert entries[-1]["var"] == "piece::Utility"
    assert "image_file" not in entries[-1]
    panel = build_menu_panel(menu, {"piece::Mode": "1"}, mod_dir=str(tmp_path),
                             image_source=lambda _path: "/texture/test/badge.png")
    assert len(panel) == len(entries)
    assert [entry.get("image") for entry in panel.values()].count(
        "/texture/test/badge.png") == len(expected)
    assert panel["piece::CommandListButton7#7"]["default"] == "1"


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


@pytest.mark.parametrize("first,alternate,guard,conflicting,expected", [
    ("ResourceBadge", "ResourceBadge", "$mode == 0", False, "badge.dds"),
    ("ResourceBadge", "resourceAlias", "$mode == 0", False, "badge.dds"),
    ("ResourceBadge", "ResourceOther", "$mode == 0", False, "badge.dds"),
    ("ResourceBadge", "ResourceMissing", "$mode == 0", False, "badge.dds"),
    ("ResourceBadge", "ResourceBadge", "$mode == 0", True, "badge.dds"),
    ("null", "ResourceOther", "$mode == 0", False, "other.dds"),
    ("ResourceMissing", "ResourceOther", "$mode == 0", False, "other.dds"),
    ("null", "ResourceMissing", "$mode == 0", False, None),
    ("ResourceBadge", "ResourceOther", "$mode == 1 || $MODE == 2", False, "badge.dds"),
    ("ResourceBadge", "ResourceOther", "$mode == 0 && $enabled", False, None),
], ids=["same-resource", "same-file", "different-states", "missing-later", "later-artwork",
        "null-first", "missing-first", "no-artwork", "compound-state", "ambiguous-variable"])
def test_first_artwork_is_shared_by_direct_and_forwarded_controls(
        first, alternate, guard, conflicting, expected):
    text = f"""
[Constants]
global persist $Mode = 0
[CommandListChoose]
if $picked == 41
$Mode = 1 - $MODE
elif $picked == 42
$anchor = 1 - $anchor
endif
[CommandListBadge]
if {guard}
ps-t100 = {first}
else
ps-t100 = {alternate}
endif
[ResourceBadge]
filename = badge.dds
[ResourceAlias]
filename = badge.dds
[ResourceOther]
filename = other.dds
"""
    if conflicting:
        text += """
[CommandListOtherBadge]
if $MODE == 0
ps-t100 = ResourceOther
else
ps-t100 = ResourceOther
endif
"""
    parsed = _sections(text)
    resources = extract_resources(parsed)
    direct = extract_menu_toggles(parsed, resources=resources, var_prefix="piece::")
    forwarded = extract_controller_toggles(
        parsed, {"mode"}, resources=resources, var_prefix="piece::")
    for entry in (direct["piece::CommandListChoose#41"], forwarded["Mode"]):
        assert entry["var"] == "piece::Mode"
        assert entry["values"] == ["0", "1"]
        assert entry.get("image_file") == expected
        assert "_pulse_var" not in entry


@pytest.mark.parametrize("coordinates,first,expected", [
    (("width", "height", "left", "top"), "ResourceBadge", "badge.dds"),
    (("WIDTH", "Height", "LEFT", "Top"), "ResourceBadge", "badge.dds"),
    (("width", "height", "left", "top"), "null", "pressed.dds"),
    (("height", "width", "top", "left"), "ResourceBadge", None),
    (("width", "height", "left", "otherTop"), "ResourceBadge", None),
], ids=["button", "case-insensitive", "first-usable", "swapped-axes", "other-region"])
def test_button_artwork_matches_click_region_before_source_order(
        coordinates, first, expected, monkeypatch):
    assignments = "\n".join(f"{axis}87 = ${name}"
                            for axis, name in zip("xyzw", coordinates))
    parsed = _sections(f"""
[CommandListChoose]
if $picked == 41
    if cursor_x > $left && cursor_x < ($left + $width)
        if cursor_y > $top && cursor_y < ($top + $height)
            $Mode = 1 - $Mode
        endif
    endif
elif $picked == 42
    $anchor = 1 - $anchor
endif
[CommandListBackdrop]
x87 = $panelWidth
y87 = $panelHeight
z87 = $panelLeft
w87 = $panelTop
if $Mode == 0
    ps-t100 = ResourceBackdrop
endif
if $theme == 0
    ps-t100 = ResourceBackdrop
endif
[CommandListBadge]
{assignments}
if $Mode == 0
    ps-t100 = {first}
else
    ps-t100 = ResourcePressed
endif
[ResourceBackdrop]
filename = backdrop.dds
[ResourceBadge]
filename = badge.dds
[ResourcePressed]
filename = pressed.dds
""")
    resources = extract_resources(parsed)
    lookup = resources.get_ci

    def button_resource(name):
        assert name.casefold() != "resourcebackdrop"
        return lookup(name)

    monkeypatch.setattr(resources, "get_ci", button_resource)
    direct = extract_menu_toggles(parsed, resources=resources)
    forwarded = extract_controller_toggles(parsed, {"mode"}, resources=resources)
    for entry in (direct["CommandListChoose#41"], forwarded["Mode"]):
        assert entry.get("image_file") == expected


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


def _called_menu_text():
    text = r"""
[Constants]
global persist $Mode = 0
global persist $Accent = 1
global persist $Palette = 0
global $Visible = 1
[KeyMouse]
key = VK_LBUTTON
type = hold
$pressed = 1
[CommandListMode]
if $Mode == 0
$Mode = 1
else
$Mode = 0
endif
[CommandListAccent]
if $Accent == 0
$Accent = 1
else
$Accent = 0
endif
[CommandListPair]
if $Visible == 1
run = CommandListMode
run = CommandListAccent
endif
[CommandListPalette]
if $PALETTE == 0
$Palette = 2
else if $Palette == 2
$PALETTE = 1
else
$Palette = 0
endif
[CommandListAnchor]
if $anchor == 1
$anchor = 2
else
$anchor = 1
endif
"""
    text += "[Present]\nif $Visible\nif $pressed && $dragging == 0\n"

    def bounds(index):
        return (f"cursor_x > $left{index} && cursor_x < $left{index} + $width{index}"
                f" && cursor_y > $top{index} && cursor_y < $top{index} + $height{index}")

    for index in range(3):
        text += f"if {bounds(index)}\n$latch{index} = 1\nendif\n"
    text += "else\n"
    for index, target in enumerate(("Pair", "Palette", "Anchor")):
        text += (f"if $latch{index} == 1\nif {bounds(index)}\n"
                 f"run = CommandList{target}\nendif\n$latch{index} = 0\nendif\n")
    text += "endif\n"
    for index in range(3):
        text += (f"if $Mode == 0\nps-t100 = ResourceBadge{index}\nelse\n"
                 f"ps-t100 = ResourceOff{index}\nendif\nx87 = $width{index}\ny87 = $height{index}\n"
                 f"z87 = $left{index}\nif $latch{index}\nw87 = $top{index} + 0.002\n"
                 f"else\nw87 = $top{index}\nendif\nrun = CustomShaderDraw\n")
    text += "endif\n"
    for index in range(3):
        text += (f"[ResourceBadge{index}]\nfilename = icons/badge{index}.png\n"
                 f"[ResourceOff{index}]\nfilename = icons/off{index}.png\n")
    return text


def test_called_mouse_menu_preserves_paired_cycles_artwork_and_staged_state(tmp_path):
    from app.mods.analysis import analyze_mod_inis, build_mod_ini_snapshot
    from core.ini.document import IniDocument

    path = str(tmp_path / "generated.ini")
    text = _called_menu_text()
    document = IniDocument.from_string(text, path=path)
    snapshot = build_mod_ini_snapshot([path], str(tmp_path), {path: document})
    parsed = analyze_mod_inis(snapshot)
    pair, palette, anchor = parsed.menu.values()
    assert pair["cycles"] == [{"var": "Mode", "values": ["0", "1"]},
                              {"var": "Accent", "values": ["0", "1"]}]
    assert palette["values"] == ["0", "2", "1"]
    assert anchor["values"] == ["1", "2"]
    assert [entry["image_file"] for entry in parsed.menu.values()] == [
        f"icons/badge{index}.png" for index in range(3)]
    assert all(entry["section"] == "Present" and entry["ini_path"] == path
               for entry in parsed.menu.values())
    assert set(parsed.present["target_inis"][0]["vars"]) >= {"Mode", "Accent", "Palette"}
    panel = build_menu_panel(parsed.menu, parsed.defaults)
    assert next(iter(panel.values()))["cycles"] == [
        {"var": "Mode", "values": ["0", "1"], "default": "0"},
        {"var": "Accent", "values": ["0", "1"], "default": "1"}]
    analysis = analyze_ini(_sections(text), var_prefix="piece::")
    assert {"piece::Mode", "piece::Accent", "piece::Palette"} <= analysis.gating_vars
    assert "piece::Accent" in extract_menu_var_names(analysis.sections, menu=analysis.menu)
    document = IniDocument.from_string(text.replace("global $Visible = 1", "global $Visible = 0"), path=path)
    snapshot = build_mod_ini_snapshot([path], str(tmp_path), {path: document})
    assert analyze_mod_inis(snapshot).menu == {}


@pytest.mark.parametrize("original,replacement", [
    ("key = VK_LBUTTON", "key = k"),
    ("$latch1 = 1", "$latch1 = 0"),
    ("if $latch1 == 1", "if $unrelated == 1"),
    ("run = CommandListPalette", "run = CommandListMissing"),
    ("run = CommandListPalette", "run = CommandListRecursive"),
    ("run = CommandListPalette", "run = CommandListExternal\\Palette"),
    ("run = CommandListPalette", "run = CommandListDeep0"),
    ("$Palette = 2", "$other = 2"),
    ("$PALETTE = 1", "$PALETTE = 2"),
    ("$Palette = 0\nendif", "$Palette = 0\nrun = CommandListMode\nendif"),
    ("[CommandListPalette]\n", "[CommandListPalette]\n$temporary = 1\n"),
    ("[CommandListPalette]\n", "[CommandListPalette]\nrun = CommandListMode\nrun = CommandListMode\n"),
    ("[CommandListPalette]\n", "[CommandListPalette]\nif $Palette == 0\n"),
    ("if $latch1 == 1\nif cursor_x > $left1", "if $latch1 == 1\nif cursor_x > $otherLeft"),
])
def test_called_mouse_menu_rejects_unproven_actions(original, replacement):
    text = _called_menu_text().replace(original, replacement)
    text += "[CommandListRecursive]\nrun = CommandListRecursive\n"
    text += "".join(f"[CommandListDeep{index}]\nrun = CommandListDeep{index + 1}\n"
                    for index in range(18))
    text += "[CommandListDeep18]\nrun = CommandListPalette\n"
    assert "Palette" not in {entry["var"] for entry in _menu(text).values()}


def _action_menu(action, driver, helper):
    text = "[Constants]\nglobal persist $Mode = 0\nglobal persist $Accent = 1\n"
    text += "[KeyMouse]\nkey = VK_LBUTTON\ntype = hold\n$pressed = 1\n"
    if helper:
        text += "[CommandListGeneratedAction]\n" + action + "\n"
        action = "run = commandlistgeneratedaction"
    items = [(4, action), (9, "$anchor = 1 - $anchor"), (12, "$guide = 1 - $guide")]
    if driver == "slot":
        return text + "[CommandListInput]\n" + _dispatch("picked", items)
    if driver == "press-elif":
        text += "[CommandListInput]\n"
        for index, (slot, body) in enumerate(items):
            text += (f"{'if' if index == 0 else 'elif'} $pressed"
                     f" && cursor_y > $top{slot} && cursor_y < $top{slot} + $height{slot}\n"
                     + body + "\n")
        return text + "endif\n"
    text += "[Present]\nif $pressed\n"
    for slot, body in items:
        text += (f"if cursor_x > $left{slot} && cursor_x < $left{slot} + $width{slot}\n"
                 f"if cursor_y > $top{slot} && cursor_y < $top{slot} + $height{slot}\n")
        text += f"$latch{slot} = 1\n" if driver == "release" else body + "\n"
        text += "endif\nendif\n"
    if driver == "release":
        text += "else\n"
        for slot, body in items:
            text += (f"if $latch{slot} == 1\n"
                     f"if cursor_x > $left{slot} && cursor_x < $left{slot} + $width{slot}\n"
                     f"if cursor_y > $top{slot} && cursor_y < $top{slot} + $height{slot}\n"
                     + body + "\nendif\nendif\nendif\n")
    return text + "endif\n"


@pytest.mark.parametrize("driver", ["slot", "press", "press-elif", "release"])
@pytest.mark.parametrize("helper", [False, True], ids=["inline", "helper"])
@pytest.mark.parametrize("action,values,paired", [
    ("$Mode = 1 - $Mode", ["0", "1"], False),
    ("$Mode = 1 - $Mode\nrun = CustomShaderRefresh", ["0", "1"], False),
    ("$Mode = ($Mode + 1) % 3", ["0", "1", "2"], False),
    ("$Mode = $Mode + 1\nif $Mode > 2\n$Mode = 0\nendif", ["0", "1", "2"], False),
    ("if $Mode == 0\n$Mode = 2\nelif $Mode == 2\n$Mode = 1\nelse\n$Mode = 0\nendif",
     ["0", "2", "1"], False),
    ("$Mode = 1 - $Mode\n$Accent = ($Accent + 1) % 3", ["0", "1"], True),
    ("run = customshaderRefresh\n$Mode = 1 - $Mode\n$Accent = ($Accent + 1) % 3\nrun = CustomShaderRefresh",
     ["0", "1"], True),
], ids=["flip", "shader-refresh", "modulo", "wrap", "literal", "paired", "paired-shader-refresh"])
def test_menu_drivers_share_inline_and_helper_action_semantics(driver, helper, action, values, paired):
    controls = {entry["var"]: entry for entry in _menu(_action_menu(action, driver, helper)).values()}
    assert controls["anchor"]["values"] == controls["guide"]["values"] == ["0", "1"]
    mode = controls["Mode"]
    assert mode["values"] == values
    assert mode["effects"] == []
    assert mode.get("cycles") == ([{"var": "Mode", "values": ["0", "1"]},
                                   {"var": "Accent", "values": ["0", "1", "2"]}] if paired else None)


def test_menu_recognizes_literal_cycle_with_nested_shader_calls():
    text = _action_menu("""if $Mode == 0
$Mode = 1
run = CustomShaderRefresh
else
$Mode = 0
run = CustomShaderRefresh
endif""", "slot", False)
    controls = {entry["var"]: entry for entry in _menu(text).values()}
    assert set(controls) == {"Mode", "anchor", "guide"}
    assert controls["Mode"]["values"] == ["0", "1"]
    assert controls["Mode"]["effects"] == []


@pytest.mark.parametrize("driver", ["slot", "press", "press-elif", "release"])
@pytest.mark.parametrize("action", [
    "if $Mode == 0\n$Mode = 1 - $Mode\nendif",
    "$Mode = 1 - $Mode\n$Mode = $unresolved",
    "$Mode = 1 - $Mode\n$Mode = 1 - $Mode",
    "if $Mode == 0\nrun = CommandListCycle\nendif",
])
def test_all_menu_drivers_reject_partial_or_ambiguous_helpers(driver, action):
    text = _action_menu(action, driver, True)
    text += "[CommandListCycle]\n$Mode = 1 - $Mode\n"
    controls = {entry["var"]: entry for entry in _menu(text).values()}
    assert set(controls) == {"anchor", "guide"}


def test_helper_cycle_requires_a_proven_wrapper_guard():
    text = _action_menu("run = CommandListGuarded", "slot", True)
    text = text.replace("global persist $Accent = 1\n", "global persist $Accent = 1\nglobal $Enabled = 1\n")
    text += "[CommandListGuarded]\nif $Enabled == 1\n$Mode = 1 - $Mode\nendif\n"
    controls = {entry["var"]: entry for entry in _menu(text).values()}
    assert controls["Mode"]["values"] == ["0", "1"]

    unknown = text.replace("global $Enabled = 1", "global persist $Enabled = 0")
    controls = {entry["var"]: entry for entry in _menu(unknown).values()}
    assert set(controls) == {"anchor", "guide"}


@pytest.mark.parametrize("body", [
    "if $picked == 6\n$temporary = 1 - $temporary\nendif",
    "if $picked == 6\nps-t100 = ResourceBadge\nelif $picked == 9\nps-t100 = ResourceBadge\nendif",
    "if $picked == 6\n$temporary = 1\nelif $picked == 9\n$other = 0\nendif",
])
def test_bookkeeping_and_artwork_only_sections_do_not_create_controls(body, monkeypatch):
    parsed = _sections("[CommandListUtility]\n" + body
                       + "\n[ResourceBadge]\nfilename = badge.dds\n")
    resources = extract_resources(parsed)

    def unrelated_artwork(name):
        pytest.fail(f"Unrecognized controls must not request artwork: {name}")

    monkeypatch.setattr(resources, "get_ci", unrelated_artwork)
    assert extract_menu_toggles(parsed, resources=resources) == {}

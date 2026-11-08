"""Staged clickable-menu removal preserves geometry and shared slider artwork."""

import pytest

from core.editing import menu as editor
from core.editing.toggle import ToggleEditError
from core.ini.document import IniDocument
from core.ini.sections import sections_from_document, extract_resources
from core.ini.menu import extract_menu_toggles
from core.ini.shapes import extract_shape_sliders


MENU = """[Constants]
global persist $Mode = 0
global persist $Accent = 0
global $Derived = 0
global persist $currFlat = 0.5
global $Shown = 1
global $Picked = 0
global $Pressed = 0
[KeyShow]
key = m
type = cycle
$Shown = 0,1
[KeyClick]
key = VK_LBUTTON
type = hold
$Pressed = 1
run = CommandList Actions
[KeyModViewerPresent]
key = p
type = cycle
$Mode = 0,1
$Accent = 1,0
$currFlat = 0.5,0.8
[Present]
if $Shown == 1
    run = CommandList Root
endif
run = CommandListKeep
[CommandList Root]
run = CommandList Paint
run = CommandListDrawSlider.Amount
[CommandList Actions]
if $Pressed == 1
    if $Picked == 11
        $Mode = 1 - $Mode
        if $Mode == 1
            $Derived = 1
        endif
    elif $Picked == 12
        $Accent = 1 - $Accent
    endif
endif
[CommandList Paint]
if $Mode == 0
    ps-t100 = ResourceBadge
else
    ps-t100 = ResourcePressed
endif
run = CustomShader Tile
[CommandListDrawSlider.Amount]
x87 = $currFlat * x87
ps-t100 = ResourceSlider
run = CustomShader Tile
[CustomShader Tile]
ps = tile.hlsl
draw = 4,0
[CommandListKeep]
$active = 1
[TextureOverridePart]
ib = ResourceIndex
if $Mode == 0
    drawindexed = 12,0,0
elif $Mode == 1
    drawindexed = 24,12,0
else
    drawindexed = 12,0,0
endif
if $Accent == 0
    drawindexed = 36,36,0
endif
if $Derived == 1
    drawindexed = 48,72,0
endif
[ResourceIndex]
filename = part.ib
format = R32_UINT
[ResourceBadge]
filename = icons/badge.dds
[ResourcePressed]
filename = icons/pressed.dds
[ResourceSlider]
filename = icons/slider.dds
[CustomShaderComputeShapes]
x88 = $currFlat
cs-t50 = copy ResourcePosition.Base
cs-t51 = copy ResourcePosition.Flat
[ResourcePosition.Base]
type = Buffer
stride = 40
filename = position.buf
[ResourcePosition.Flat]
type = Buffer
stride = 40
filename = position-flat.buf
"""


@pytest.mark.parametrize("shared_slots", [False, True], ids=["exclusive-painter", "shared-slot-painter"])
def test_delete_menu_preserves_sliders_shared_helpers_and_lossless_geometry(shared_slots):
    text = MENU
    if shared_slots:
        text = text.replace("run = CommandList Paint\nrun = CommandListDrawSlider.Amount", """$Tile = 11
run = CommandList Tile
$Tile = 12
run = CommandList Tile
run = CommandListDrawSlider.Amount""")
        text = text.replace("""if $Mode == 0
    ps-t100 = ResourceBadge
else
    ps-t100 = ResourcePressed
endif
run = CustomShader Tile
[CommandListDrawSlider.Amount]""", """if $Tile == 11
    ps-t100 = ResourceBadge
elif $Tile == 12
    ps-t100 = ResourcePressed
endif
if $Tile == 50
    x87 = $currFlat * x87
    ps-t100 = ResourceSlider
    run = CustomShader Tile
endif
[CommandList Tile]
run = CommandList Paint
run = CustomShader Tile
[CommandListDrawSlider.Amount]""")
        text = text.replace("[CommandListDrawSlider.Amount]\nx87 = $currFlat * x87",
                            "[CommandListDrawSlider.Amount]\nx87 = $currFlat * x87\n$Tile = 50\nrun = CommandList Paint")
        text = text.replace("[CommandList Root]\n", "[CommandList Root]\n$Tile = 11\nrun = CommandList Geometry\n")
        text += "[CommandList Geometry]\nrun = CommandList GeometryBody\n[CommandList GeometryBody]\ndrawindexed = 48,72,0\n"
    text += "[CommandListExistingEmpty]\n[ResourceExistingEmpty]\n"
    document = IniDocument.from_string("\ufeff" + text + "[ResourceUnrelated]\r\nfilename = kept.dds")
    sections = sections_from_document(document)
    assert len(extract_menu_toggles(sections, resources=extract_resources(sections))) == 2
    before_sliders = extract_shape_sliders(sections, extract_resources(sections))
    assert before_sliders
    result = editor.delete_menu(document)
    assert result["controls_removed"] == 2
    assert {"mode", "accent", "derived", "picked", "pressed"} <= set(result["vars_removed"])
    assert all("$" + variable not in document.to_string().lower() for variable in ("mode", "accent", "derived", "picked", "pressed"))
    for name in ("CommandList Actions", "ResourceBadge", "ResourcePressed", "KeyClick"):
        assert document.section(name) is None
    if shared_slots:
        assert document.section("CommandList Paint") is not None
        assert document.section("CommandList Tile") is None
        assert "run = CommandList Tile" not in document.to_string()
        assert "run = CommandList Geometry" in document.to_string()
        assert [line.text for line in document.section("CommandList GeometryBody").lines if line.text] == ["drawindexed = 48,72,0"]
    else:
        assert document.section("CommandList Paint") is None
    assert "icons/badge.dds" not in document.to_string()
    assert "icons/pressed.dds" not in document.to_string()
    assert [line.text for line in document.section("TextureOverridePart").lines if line.text] == [
        "ib = ResourceIndex", "drawindexed = 12,0,0", "drawindexed = 24,12,0", "drawindexed = 36,36,0", "drawindexed = 48,72,0",
    ]
    assert document.section("CustomShader Tile") is not None
    assert document.section("ResourceSlider") is not None
    assert document.section("CommandListKeep") is not None
    assert document.section("CommandListExistingEmpty") is not None
    assert document.section("ResourceExistingEmpty") is not None
    assert all(document.lines[section.header_no - 1].kind == "blank" for section in document.sections[1:])
    assert "$currFlat = 0.5,0.8" in document.to_string()
    sections = sections_from_document(document)
    assert not extract_menu_toggles(sections, resources=extract_resources(sections))
    assert extract_shape_sliders(sections, extract_resources(sections)) == before_sliders
    assert document.to_string().startswith("\ufeff")
    assert document.to_string().endswith("[ResourceUnrelated]\r\nfilename = kept.dds")
    assert not document.structure_errors() and not document.syntax_errors()


@pytest.mark.parametrize("suffix", [
    "[TextureOverrideOther]\nx0 = $Mode\n",
    "[TextureOverrideOther]\nif $Mode ? 0\ndrawindexed = 3,0,0\nendif\n",
])
def test_menu_delete_rejects_unproven_surviving_references(suffix):
    with pytest.raises(ToggleEditError):
        editor.delete_menu(IniDocument.from_string(MENU + suffix))

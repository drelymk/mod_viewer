"""Aggregated per-mod semantic analysis regressions."""

import os
import tempfile

from app.mods.analysis import analyze_mod_inis
from app.mods.controls import build_menu_panel
from core.ini.document import IniDocument
from core.ini.sections import extract_ini_namespace


def test_nested_sibling_ini_state_stays_isolated():
    ini = """[Constants]
global persist $swapvar = 0
global persist ${0} = 0
global persist $dummy = 0
global $clickedSlot
global $hoveredSlot
[KeySwap]
key = x
type = cycle
$swapvar = 0,1
[CommandListClickedSlot]
$clickedSlot = $hoveredSlot
if $clickedSlot == 1
    ${0} = 1 - ${0}
elif $clickedSlot == 2
    $dummy = 1 - $dummy
endif
[CommandListIcon1]
ps-t100 = ResourceIcon
[ResourceIcon]
filename = {1}.dds
"""
    with tempfile.TemporaryDirectory() as root:
        nested = os.path.join(root, "nested")
        os.makedirs(nested)
        paths = []
        for stem in ("body", "hair"):
            path = os.path.join(nested, f"{stem}.ini")
            with open(path, "w", encoding="utf-8") as stream:
                stream.write(ini.format(stem, stem))
            paths.append(path)

        parsed = analyze_mod_inis(paths, root)
        assert set(parsed.toggles) == {
            "nested/body::KeySwap", "nested/hair::KeySwap",
        }
        assert set(parsed.defaults) >= {
            "nested/body::swapvar", "nested/hair::swapvar",
        }
        assert {item.get("source") for item in parsed.toggles.values()} == {
            "nested",
        }
        images = {
            os.path.basename(info["ini_path"]): info.get("image_file")
            for info in parsed.menu.values()
            if info.get("slot") == 1
        }

        assert images == {
            "body.ini": os.path.join("nested", "body.dds"),
            "hair.ini": os.path.join("nested", "hair.dds"),
        }


def test_namespace_preamble_reads_disk_text_and_staged_documents():
    text = "\n  NAMESPACE =   SomeMod  ; authored metadata\n\n[Constants]\n"
    assert extract_ini_namespace(text=text) == "SomeMod"
    document = IniDocument.from_string(text, path="staged.ini")
    assert extract_ini_namespace(document=document) == "SomeMod"
    assert extract_ini_namespace(
        text="[Constants]\nnamespace = ignored\n") is None


def _forwarded_fixture(tmp_path, controller, target_var="style",
                       target_default="1", target_namespace="Target"):
    menu_path = tmp_path / "Menu.ini"
    target_path = tmp_path / "mod(5).ini"
    menu_path.write_text(controller, encoding="utf-8")
    target_path.write_text(f"""namespace = {target_namespace}

[Constants]
global ${target_var} = {target_default}

[TextureOverrideBody]
ib = ResourceBodyIB
vb0 = ResourceBodyPosition
vb1 = ResourceBodyTexcoord
if ${target_var} == 1
    drawindexed = 3, 0, 0
endif

[ResourceBodyIB]
filename = body.ib
format = DXGI_FORMAT_R32_UINT

[ResourceBodyPosition]
filename = body-position.buf
stride = 12

[ResourceBodyTexcoord]
filename = body-texcoord.buf
stride = 8
""", encoding="utf-8")
    return analyze_mod_inis([str(menu_path), str(target_path)], str(tmp_path))


def test_direct_namespace_forwarding_exposes_target_control(tmp_path):
    parsed = _forwarded_fixture(tmp_path, """
[Constants]
global persist $value = 1

[CommandListButton]
$value = 1 - $value

[Present]
$\\Target\\style = $value
""")
    controls = list(parsed.menu.values())
    assert len(controls) == 1
    assert controls[0]["var"] == "mod(5)::style"
    assert controls[0]["values"] == ["0", "1"]
    assert controls[0]["name"] == "style"
    assert parsed.defaults["mod(5)::style"] == "1"
    assert parsed.groups[0]["draws"][0].conditions == [[{
        "var": "mod(5)::style", "value": "1", "negate": False,
    }]]


def test_forwarded_numbered_button_image_uses_source_key(tmp_path):
    parsed = _forwarded_fixture(tmp_path, r"""
[Constants]
global $key_24 = 0
global $value_24 = 0

[CommandListUpdateSliderPage3]
$value_24 = 1 - $value_24

[CommandListDrawSliderButtonPage3]
if $value_24 == 0
    ps-t100 = ResourceSliderButton24_1
else
    ps-t100 = ResourceSliderButton24_2
endif

[Present]
$\Target\key_24 = $key_24
$key_24 = $key_24 + $value_24
if $key_24 > 1
    $key_24 = 0
endif

[ResourceSliderButton24_1]
filename = ui/24.dds

[ResourceSliderButton24_2]
filename = ui/24.dds
""", target_var="key_24", target_default="0")
    control = next(iter(parsed.menu.values()))
    assert control["slot"] == 1
    assert control["var"] == "mod(5)::key_24"
    assert control["image_file"] == "ui/24.dds"


def test_nested_namespace_forwarding_exposes_target_control(tmp_path):
    parsed = _forwarded_fixture(tmp_path, """
[Constants]
global persist $value = 1

[CommandListButton]
$value = 1 - $value

[Present]
$\\Group\\Master\\style = $value
""", target_namespace="Group\\Master")
    controls = list(parsed.menu.values())
    assert len(controls) == 1
    assert controls[0]["var"] == "mod(5)::style"
    assert parsed.groups[0]["draws"][0].conditions == [[{
        "var": "mod(5)::style", "value": "1", "negate": False,
    }]]


def test_forwarded_target_can_also_be_read_by_another_ini(tmp_path):
    menu = tmp_path / "Menu.ini"
    target = tmp_path / "Target.ini"
    consumer = tmp_path / "Consumer.ini"
    menu.write_text("""[Constants]
global persist $value = 1

[CommandListButton]
$value = 1 - $value

[Present]
$\\Target\\style = $value
""", encoding="utf-8")
    target.write_text("""namespace = Target

[Constants]
global $style = 1
""" + _qualified_draw_ini("$style == 1"), encoding="utf-8")
    consumer.write_text(_qualified_draw_ini(
        r"$\Target\style == 1"), encoding="utf-8")

    parsed = analyze_mod_inis(
        [str(menu), str(target), str(consumer)], str(tmp_path))
    assert any(info["var"] == "Target::style"
               for info in parsed.menu.values())
    for ini_path in (target, consumer):
        group = next(group for group in parsed.groups
                     if group["identity_source"].endswith(ini_path.name))
        assert group["draws"][0].conditions == [[{
            "var": "Target::style", "value": "1", "negate": False,
        }]]


def _qualified_draw_ini(condition):
    return f"""[TextureOverrideBody]
ib = ResourceBodyIB
vb0 = ResourceBodyPosition
vb1 = ResourceBodyTexcoord
if {condition}
    drawindexed = 3, 0, 0
endif

[ResourceBodyIB]
filename = body.ib
format = DXGI_FORMAT_R32_UINT

[ResourceBodyPosition]
filename = body-position.buf
stride = 12

[ResourceBodyTexcoord]
filename = body-texcoord.buf
stride = 8
"""


def test_qualified_reads_resolve_owner_controls_without_colliding_with_animation(
        tmp_path):
    master = tmp_path / "MasterJaneDoe_AIO.ini"
    cv = tmp_path / "JaneCV_Toggled_X_Anim_Animation.ini"
    og = tmp_path / "JaneOG.ini"
    master.write_text("""namespace = JaneDoe_AIO\\Master

[Constants]
global persist $swapvar = 2

[KeySwap]
key = x
type = cycle
$swapvar = 1,2
""", encoding="utf-8")
    cv.write_text("""[Constants]
global $fps = 60
global $frameStart = 0
global $frameEnd = 2
global $swapvar = 0

[KeyCloth]
key = c
type = cycle
$cloth = 0,1

[Present]
if $\\JaneDoe_AIO\\Master\\swapvar == 1
    $swapvar = (time * $fps % ($frameEnd - $frameStart + 1) + $frameStart) // 1
endif

[TextureOverrideBody]
ib = ResourceBodyIB
vb0 = ResourceBodyPosition
vb1 = ResourceBodyTexcoord
if $\\JaneDoe_AIO\\Master\\swapvar == 1
    Resource\\ZZMI\\Diffuse = ResourceCVDiffuse
elif $\\JaneDoe_AIO\\Master\\swapvar == 2
    Resource\\ZZMI\\Diffuse = ResourceCVAltDiffuse
endif
if $cloth == 1 && $\\JaneDoe_AIO\\Master\\swapvar == 1
    drawindexed = 3, 0, 0
endif

[ResourceBodyIB]
filename = cv.ib
format = DXGI_FORMAT_R32_UINT

[ResourceBodyPosition]
filename = cv-position.buf
stride = 12

[ResourceBodyTexcoord]
filename = cv-texcoord.buf
stride = 8

[ResourceCVDiffuse]
filename = cv.dds

[ResourceCVAltDiffuse]
filename = cv-alt.dds
""", encoding="utf-8")
    og.write_text(_qualified_draw_ini(
        r"$\JaneDoe_AIO\Master\swapvar == 2"), encoding="utf-8")

    parsed = analyze_mod_inis(
        [str(master), str(cv), str(og)], str(tmp_path))
    master_toggle = next(
        info for info in parsed.toggles.values()
        if info["vars"].get("MasterJaneDoe_AIO::swapvar") == ["1", "2"])
    assert master_toggle["vars"] == {
        "MasterJaneDoe_AIO::swapvar": ["1", "2"],
    }

    cv_group = next(group for group in parsed.groups
                    if group["identity_source"].endswith(cv.name))
    cv_draw = cv_group["draws"][0]
    assert {(clause["var"], clause["value"])
            for clause in cv_draw.conditions[0]} == {
        ("JaneCV_Toggled_X_Anim_Animation::cloth", "1"),
        ("MasterJaneDoe_AIO::swapvar", "1"),
    }
    diffuse_rules = cv_draw.texture_rules("diffuse")
    assert len(diffuse_rules) == 2
    assert {
        (clause["var"], clause["value"])
        for clause in diffuse_rules[0]["conditions"][0]
    } == {("MasterJaneDoe_AIO::swapvar", "1")}

    og_group = next(group for group in parsed.groups
                    if group["identity_source"].endswith(og.name))
    assert og_group["draws"][0].conditions == [[{
        "var": "MasterJaneDoe_AIO::swapvar", "value": "2",
        "negate": False,
    }]]

    cv_clock = next(clock for clock in parsed.animations
                    if clock.frame_var ==
                    "JaneCV_Toggled_X_Anim_Animation::swapvar")
    assert cv_clock.conditions == [[{
        "var": "MasterJaneDoe_AIO::swapvar", "value": "1",
        "negate": False,
    }]]
    assert cv_clock.frame_var != "MasterJaneDoe_AIO::swapvar"


def test_unknown_and_ambiguous_qualified_reads_fail_open(tmp_path):
    consumer = tmp_path / "Consumer.ini"
    consumer.write_text(_qualified_draw_ini(
        r"$\DoesNotExist\Master\swapvar == 1"), encoding="utf-8")
    parsed = analyze_mod_inis([str(consumer)], str(tmp_path))
    assert parsed.groups[0]["draws"][0].conditions == []

    first = tmp_path / "First.ini"
    second = tmp_path / "Second.ini"
    first.write_text("""namespace = Same\\Master
[KeySwap]
key = x
type = cycle
$swapvar = 1,2
""", encoding="utf-8")
    second.write_text(first.read_text(encoding="utf-8"), encoding="utf-8")
    consumer.write_text(_qualified_draw_ini(
        r"$\Same\Master\swapvar == 1"), encoding="utf-8")
    parsed = analyze_mod_inis(
        [str(consumer), str(first), str(second)], str(tmp_path))
    assert parsed.groups[0]["draws"][0].conditions == []


def test_unsupported_forwarding_keeps_target_gate_fail_open(tmp_path):
    parsed = _forwarded_fixture(tmp_path, """
[Constants]
global $source = 1

[Present]
$\\Target\\style = $source
""", target_default="0")
    assert parsed.menu == {}
    assert parsed.groups[0]["draws"][0].conditions == []


def test_present_pulse_is_not_treated_as_clickable_controller(tmp_path):
    parsed = _forwarded_fixture(tmp_path, """
[Present]
$value = 1 - $value
$\\Target\\style = $value
""", target_default="0")
    assert parsed.menu == {}
    assert parsed.groups[0]["draws"][0].conditions == []


def test_commandlist_forwarding_is_not_treated_as_continuous(tmp_path):
    parsed = _forwarded_fixture(tmp_path, """
[CommandListForward]
$value = 1 - $value
$\\Target\\style = $value
""", target_default="0")
    assert parsed.menu == {}
    assert parsed.groups[0]["draws"][0].conditions == []


def test_forwarded_state_cycle_uses_controller_default_and_wrap_limit(tmp_path):
    parsed = _forwarded_fixture(tmp_path, """
[Constants]
global persist $state = 3
global $pulse = 0

[CommandListButton]
$pulse = 1 - $pulse

[Present]
if $state > 3
    $state = 0
else
    $state = $state + $pulse
endif
$pulse = 0
$\\Target\\style = $state
""")
    controls = list(parsed.menu.values())
    assert len(controls) == 1
    assert controls[0]["values"] == ["0", "1", "2", "3"]
    assert parsed.defaults["mod(5)::style"] == "3"
    panel = build_menu_panel(parsed.menu, parsed.defaults)
    assert next(iter(panel.values()))["default"] == "3"


def test_external_and_ui_only_forwarding_stays_untracked(tmp_path):
    parsed = _forwarded_fixture(tmp_path, """
[Constants]
global persist $page = 0
global persist $zoom = 0

[Present]
    $page = 1 - $page
    $zoom = 1 - $zoom
    $\\WWMIv1\\vg_offset = $page
    $\\WWMIv1\\page = $page
    $\\WWMIv1\\zoom = $zoom
""")
    assert parsed.menu == {}


def test_ambiguous_namespace_fails_open(tmp_path):
    menu = tmp_path / "Menu.ini"
    first = tmp_path / "first.ini"
    second = tmp_path / "second.ini"
    menu.write_text("""[Present]
global persist $value = 1
$value = 1 - $value
$\\Same\\x = $value
""", encoding="utf-8")
    target = """namespace = Same
[Constants]
global $x = 0
"""
    first.write_text(target, encoding="utf-8")
    second.write_text(target, encoding="utf-8")
    parsed = analyze_mod_inis(
        [str(menu), str(first), str(second)], str(tmp_path))
    assert parsed.menu == {}

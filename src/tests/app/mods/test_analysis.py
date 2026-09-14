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


def test_unsupported_forwarding_keeps_target_gate_fail_open(tmp_path):
    parsed = _forwarded_fixture(tmp_path, """
[Constants]
global $source = 1

[Present]
$\\Target\\style = $source
""", target_default="0")
    assert parsed.menu == {}
    assert parsed.groups[0]["draws"][0].conditions == []


def test_forwarded_state_cycle_uses_controller_default_and_wrap_limit(tmp_path):
    parsed = _forwarded_fixture(tmp_path, """
[Constants]
global persist $state = 3
global $pulse = 0

[Present]
$pulse = 1 - $pulse
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

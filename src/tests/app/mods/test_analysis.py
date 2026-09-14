"""Aggregated per-mod semantic analysis regressions."""

import os
import tempfile

from app.mods.analysis import analyze_mod_inis


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
[TextureOverrideBody]
if $swapvar == 1
    Resource\\ZZMI\\Diffuse = ResourceIcon
endif
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
        assert set(parsed.control_projection["toggles"]) == {
            "nested/body::KeySwap", "nested/hair::KeySwap",
        }
        assert set(parsed.defaults) >= {
            "nested/body::swapvar", "nested/hair::swapvar",
        }
        assert {item.get("source") for item in parsed.control_projection[
            "toggles"].values()} == {
            "nested",
        }
        assert parsed.toggles == {}
        assert parsed.menu == {}
        assert parsed.control_projection["menu"] == {}

"""Aggregated per-mod semantic analysis regressions."""

import os
import tempfile

from app.mods.analysis import analyze_mod_inis


def test_nested_sibling_inis_have_unique_parser_namespaces():
    ini = """[Constants]
global persist $swapvar = 0
[KeySwap]
key = x
type = cycle
$swapvar = 0,1
"""
    with tempfile.TemporaryDirectory() as root:
        nested = os.path.join(root, "nested")
        os.makedirs(nested)
        paths = []
        for name in ("body.ini", "hair.ini"):
            path = os.path.join(nested, name)
            with open(path, "w", encoding="utf-8") as stream:
                stream.write(ini)
            paths.append(path)

        parsed = analyze_mod_inis(paths, root)

        assert set(parsed.toggles) == {
            "nested/body::KeySwap", "nested/hair::KeySwap",
        }
        assert set(parsed.defaults) == {
            "nested/body::swapvar", "nested/hair::swapvar",
        }
        assert {item.get("source") for item in parsed.toggles.values()} == {
            "nested",
        }


def test_nested_sibling_menu_images_do_not_bleed():
    ini = """[Constants]
global persist ${0} = 0
global persist $dummy = 0
global $clickedSlot
global $hoveredSlot
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
        images = {
            os.path.basename(info["ini_path"]): info.get("image_file")
            for info in parsed.menu.values()
            if info.get("slot") == 1
        }

        assert images == {
            "body.ini": os.path.join("nested", "body.dds"),
            "hair.ini": os.path.join("nested", "hair.dds"),
        }

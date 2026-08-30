"""INI discovery keeps active and disabled selections separate."""

import os

import pytest

from core.mod_discovery import discover_ini_paths


_GEOMETRY_INI = "[TextureOverrideRoot]\ndrawindexed = 3, 0, 0\n"
_PLAIN_INI = "[Constants]\n$Value = 1\n"


def _write(root, relative, text=_PLAIN_INI):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _relative_paths(root, paths):
    return [os.path.relpath(path, root).replace(os.sep, "/")
            for path in paths]


def test_discovery_filters_selected_files_and_preserves_bounded_nested_rules(
        tmp_path):
    _write(tmp_path, "Active.ini", _GEOMETRY_INI)
    _write(tmp_path, "active-extra.ini")
    _write(tmp_path, "DISABLEDActive.ini", _GEOMETRY_INI)
    _write(tmp_path, "disabled-extra.ini")
    _write(tmp_path, "ignored.txt")
    _write(tmp_path, "nested/Child.ini")
    _write(tmp_path, "nested/DISABLEDChild.ini")
    _write(tmp_path, "nested/deeper/Deep.ini")
    _write(tmp_path, "nested/deeper/DISABLEDDeep.ini")
    _write(tmp_path, "nested/deeper/too-deep/TooDeep.ini")
    _write(tmp_path, "nested/deeper/too-deep/DISABLEDTooDeep.ini")

    assert _relative_paths(tmp_path, discover_ini_paths(tmp_path)) == [
        "Active.ini", "active-extra.ini", "nested/Child.ini",
        "nested/deeper/Deep.ini",
    ]
    assert _relative_paths(
        tmp_path, discover_ini_paths(tmp_path, disabled=True)) == [
        "DISABLEDActive.ini", "disabled-extra.ini", "nested/DISABLEDChild.ini",
        "nested/deeper/DISABLEDDeep.ini",
    ]

    no_disabled = tmp_path / "no-disabled"
    _write(no_disabled, "Active.ini", _GEOMETRY_INI)
    assert discover_ini_paths(no_disabled, disabled=True) == []


@pytest.mark.parametrize("disabled, prefix", [(False, ""), (True, "DISABLED")])
def test_discovery_applies_ini_count_cap_to_selected_nested_files(
        disabled, prefix, tmp_path):
    _write(tmp_path, f"{prefix}Root.ini", _GEOMETRY_INI)
    for index in range(12):
        _write(tmp_path, f"nested/{prefix}{index:02}.ini")
    _write(tmp_path, "nested/other-mode.ini" if disabled else
           "nested/DISABLEDother-mode.ini")

    paths = discover_ini_paths(tmp_path, disabled=disabled)

    assert len(paths) == 10
    assert all(
        ("DISABLED" in os.path.basename(path).upper()) == disabled
        for path in paths)

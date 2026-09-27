"""Test build-time feature resolution and frozen versus source runtime behavior."""

import os
import sys
import tempfile
import types

import pytest


import build
from app.settings import features as features
from app.settings import paths as paths


def _fixture(tmp, text, name="features.ini"):
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


# ── build.py: resolve_features() / write_baked_features() ───────────────────

_FLAG_CASES = [(False, True, False, False), (True, False, True, True)]


def _run_feature_case(export, modify_toggle, open_disabled_mod, edit_mesh, tmp):
    path = _fixture(
        tmp,
        "[features]\n"
        f"Export = {int(export)}\n"
        f"Modify_Toggle = {int(modify_toggle)}\n"
        f"Open_Disabled_Mod = {int(open_disabled_mod)}\n"
        f"Edit_Mesh = {int(edit_mesh)}\n",
    )
    result = build.resolve_features(path)
    assert result == {
        "export": export,
        "modify_toggle": modify_toggle,
        "open_disabled_mod": open_disabled_mod,
        "edit_mesh": edit_mesh,
    }, f"feature flags resolve independently (got {result})"


@pytest.mark.parametrize(
    "export, modify_toggle, open_disabled_mod, edit_mesh", _FLAG_CASES)
def test_resolve_features_flag_matrix(
        export, modify_toggle, open_disabled_mod, edit_mesh, tmp_path):
    """Every authored feature combination maps to the same booleans."""
    _run_feature_case(
        export, modify_toggle, open_disabled_mod, edit_mesh, str(tmp_path))


def test_missing_feature_config_defaults_enabled(tmp_path):
    result = build.resolve_features(str(tmp_path / "does_not_exist.ini"))
    assert result == {
        "export": True, "modify_toggle": True, "open_disabled_mod": True,
        "edit_mesh": True,
    }


def test_write_baked_features_round_trips_through_import():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "_baked_features_test.py")
        build.write_baked_features({
            "export": False, "modify_toggle": True, "open_disabled_mod": False,
            "edit_mesh": False,
        }, path=path)
        ns = {}
        with open(path, encoding="utf-8") as fh:
            exec(compile(fh.read(), path, "exec"), ns)
        assert (ns.get("EXPORT") is False
                and ns.get("MODIFY_TOGGLE") is True
                and ns.get("OPEN_DISABLED_MOD") is False
                and ns.get("EDIT_MESH") is False), (
            f"the generated module's constants match the flags passed in "
            f"(got EXPORT={ns.get('EXPORT')!r}, "
            f"MODIFY_TOGGLE={ns.get('MODIFY_TOGGLE')!r}, "
            f"OPEN_DISABLED_MOD={ns.get('OPEN_DISABLED_MOD')!r}, "
            f"EDIT_MESH={ns.get('EDIT_MESH')!r})")
        build.clean_baked_features(path=path)
    assert not os.path.isfile(path), "clean_baked_features removes the generated module"


# ── app/settings/features.py: get_features() ──────────────────────────────────────────

@pytest.mark.parametrize(
    "frozen, baked, expected",
    [
        (False, (False, False, False, False), {
            "export": True, "modify_toggle": True, "open_disabled_mod": True,
            "edit_mesh": True,
        }),
        (True, (False, True, False, False), {
            "export": False, "modify_toggle": True, "open_disabled_mod": False,
            "edit_mesh": False,
        }),
        (True, None, {
            "export": True, "modify_toggle": True, "open_disabled_mod": True,
            "edit_mesh": True,
        }),
        (True, (False, None, True, None), {
            "export": False, "modify_toggle": True, "open_disabled_mod": True,
            "edit_mesh": True,
        }),
    ],
    ids=["source-ignores-baked", "frozen-reads-baked",
         "frozen-missing-module", "frozen-missing-attribute"],
)
def test_runtime_feature_resolution(frozen, baked, expected, monkeypatch):
    monkeypatch.setattr(paths, "is_frozen", lambda: frozen)
    module_name = "app.settings._baked_features"
    if baked is None:
        monkeypatch.delitem(sys.modules, module_name, raising=False)
    else:
        module = types.ModuleType(module_name)
        if baked[0] is not None:
            module.EXPORT = baked[0]
        if baked[1] is not None:
            module.MODIFY_TOGGLE = baked[1]
        if baked[2] is not None:
            module.OPEN_DISABLED_MOD = baked[2]
        if baked[3] is not None:
            module.EDIT_MESH = baked[3]
        monkeypatch.setitem(sys.modules, module_name, module)

    assert features.get_features() == expected

"""Build-time feature flags (which optional UI actions a *built* exe
exposes), split across two seams that this file tests separately:

  - build.py's resolve_features(ini_path): reads features.ini at BUILD time
    and resolves it to {"export": bool, "modify_toggle": bool}. This is now
    the only place that ever parses the ini file. write_baked_features()/
    clean_baked_features() round-trip those booleans through a tiny
    generated module (app/settings/_baked_features.py) so PyInstaller compiles them
    into the exe like ordinary code, instead of bundling features.ini itself
    as a loose, end-user-editable file (see build.py's module docstring-ish
    comments near BAKED_FEATURES_MODULE for the full rationale).

  - app/settings/features.py's get_features(): read at RUNTIME. Always all-True in a
    source checkout (paths.is_frozen()), regardless of any baked module
    present, so flags meant for a distributed build do not hide source
    features. When frozen, it imports app.settings._baked_features and reads
    its EXPORT/MODIFY_TOGGLE constants, falling back to True for a flag if the
    module or the constant is missing (a broken/skipped bake should never
    silently hide a feature nobody deliberately disabled).

Frozen-mode runtime tests inject a fake app.settings._baked_features module straight
into sys.modules rather than invoking the packaging toolchain. Importing it
through the normal submodule path exercises the same runtime contract, and
paths.is_frozen is monkeypatched the same way test_toggle_api.py monkeypatches
record_editor.verify_recording.
"""

import os, sys, tempfile, types

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

_FLAG_CASES = [(False, True), (True, False)]


def _run_feature_case(export, modify_toggle, tmp):
    path = _fixture(
        tmp,
        "[features]\n"
        f"Export = {int(export)}\n"
        f"Modify_Toggle = {int(modify_toggle)}\n",
    )
    result = build.resolve_features(path)
    assert result == {"export": export, "modify_toggle": modify_toggle}, f"feature flags resolve independently (got {result})"


@pytest.mark.parametrize("export, modify_toggle", _FLAG_CASES)
def test_resolve_features_flag_matrix(export, modify_toggle, tmp_path):
    """Every pair of authored feature flags maps to the same booleans."""
    _run_feature_case(export, modify_toggle, str(tmp_path))


def test_missing_feature_config_defaults_enabled(tmp_path):
    result = build.resolve_features(str(tmp_path / "does_not_exist.ini"))
    assert result == {"export": True, "modify_toggle": True}


def test_write_baked_features_round_trips_through_import():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "_baked_features_test.py")
        build.write_baked_features({"export": False, "modify_toggle": True}, path=path)
        ns = {}
        with open(path, encoding="utf-8") as fh:
            exec(compile(fh.read(), path, "exec"), ns)
        assert ns.get("EXPORT") is False and ns.get("MODIFY_TOGGLE") is True, (
            f"the generated module's constants match the flags passed in "
            f"(got EXPORT={ns.get('EXPORT')!r}, MODIFY_TOGGLE={ns.get('MODIFY_TOGGLE')!r})")
        build.clean_baked_features(path=path)
    assert not os.path.isfile(path), "clean_baked_features removes the generated module"


# ── app/settings/features.py: get_features() ──────────────────────────────────────────

@pytest.mark.parametrize(
    "frozen, baked, expected",
    [
        (False, (False, False), {"export": True, "modify_toggle": True}),
        (True, (False, True), {"export": False, "modify_toggle": True}),
        (True, None, {"export": True, "modify_toggle": True}),
        (True, (False, None), {"export": False, "modify_toggle": True}),
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
        monkeypatch.setitem(sys.modules, module_name, module)

    assert features.get_features() == expected

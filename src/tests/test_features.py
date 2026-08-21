"""Build-time feature flags (which optional UI actions a *built* exe
exposes), split across two seams that this file tests separately:

  - build.py's resolve_features(ini_path): reads features.ini at BUILD time
    and resolves it to {"export": bool, "modify_toggle": bool}. This is now
    the only place that ever parses the ini file. write_baked_features()/
    clean_baked_features() round-trip those booleans through a tiny
    generated module (app/_baked_features.py) so PyInstaller compiles them
    into the exe like ordinary code, instead of bundling features.ini itself
    as a loose, end-user-editable file (see build.py's module docstring-ish
    comments near BAKED_FEATURES_MODULE for the full rationale).

  - app/features.py's get_features(): read at RUNTIME. Always all-True when
    not frozen (paths.is_frozen()), regardless of any baked module present --
    a developer's own environment is never hobbled by a flag meant for a
    distributed build. When frozen, it imports app._baked_features and reads
    its EXPORT/MODIFY_TOGGLE constants, falling back to True for a flag if the
    module or the constant is missing (a broken/skipped bake should never
    silently hide a feature nobody deliberately disabled).

Frozen-mode runtime tests inject a fake app._baked_features module straight
into sys.modules rather than actually running build.py/PyInstaller -- proven
equivalent to importing it for real via a throwaway check before this file was
written (from . import _baked_features resolves through sys.modules exactly
like any other submodule import). paths.is_frozen is monkeypatched the same
way test_toggle_api.py monkeypatches record_editor.verify_recording.
"""

import os, sys, tempfile, types

import pytest


import build
from app import features, paths


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


_INVALID_FEATURE_CASES = [
    ("missing file", None),
]


def _run_invalid_feature_case(content, tmp):
    if content is None:
        path = os.path.join(tmp, "does_not_exist.ini")
    else:
        path = _fixture(tmp, content)
    result = build.resolve_features(path)
    assert result == {"export": True, "modify_toggle": True}, f"invalid feature configuration defaults to enabled (got {result})"


@pytest.mark.parametrize(
    "_case_name, content",
    _INVALID_FEATURE_CASES,
    ids=[case[0] for case in _INVALID_FEATURE_CASES],
)
def test_invalid_feature_config_defaults_enabled(_case_name, content, tmp_path):
    _run_invalid_feature_case(content, str(tmp_path))


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


# ── app/features.py: get_features() ──────────────────────────────────────────

class _frozen:
    """Swaps paths.is_frozen for the duration of a `with` block, restoring the
    original afterwards -- same monkeypatch-and-restore shape test_toggle_api.py
    uses for record_editor.verify_recording."""

    def __init__(self, value):
        self.value = value

    def __enter__(self):
        self._real = paths.is_frozen
        paths.is_frozen = lambda: self.value
        return self

    def __exit__(self, *exc):
        paths.is_frozen = self._real


class _baked_module:
    """Injects (or removes) a fake app._baked_features module in sys.modules
    for the duration of a `with` block, so get_features()'s own
    `from . import _baked_features` resolves to exactly the module under
    test -- without ever running build.py/PyInstaller for real. Restores
    whatever was previously in sys.modules afterwards (there normally isn't
    anything, since this module only exists in a real frozen build)."""

    _KEY = "app._baked_features"

    def __init__(self, present=True, export=None, modify_toggle=None):
        self.present = present
        self.export = export
        self.modify_toggle = modify_toggle

    def __enter__(self):
        self._had_key = self._KEY in sys.modules
        self._prior = sys.modules.get(self._KEY)
        if self.present:
            mod = types.ModuleType(self._KEY)
            if self.export is not None:
                mod.EXPORT = self.export
            if self.modify_toggle is not None:
                mod.MODIFY_TOGGLE = self.modify_toggle
            sys.modules[self._KEY] = mod
        else:
            sys.modules.pop(self._KEY, None)
        return self

    def __exit__(self, *exc):
        if self._had_key:
            sys.modules[self._KEY] = self._prior
        else:
            sys.modules.pop(self._KEY, None)


def test_not_frozen_always_shows_everything_even_with_a_baked_module_present():
    with _baked_module(export=False, modify_toggle=False), _frozen(False):
        result = features.get_features()
    assert result == {"export": True, "modify_toggle": True}, (
        f"running from source shows every feature even though a baked module "
        f"on sys.modules disables both (got {result})")


def test_frozen_reads_baked_export_disabled():
    with _baked_module(export=False, modify_toggle=True), _frozen(True):
        result = features.get_features()
    assert result == {"export": False, "modify_toggle": True}, f"EXPORT=False in the baked module hides only the export flag (got {result})"


def test_frozen_missing_baked_module_defaults_to_shown():
    """A real build always bakes the module right before PyInstaller runs, so
    this should never happen -- but if it somehow did (e.g. a build that
    skipped the bake step), the app must fail safe, not crash or silently
    hide something nobody chose to hide."""
    with _baked_module(present=False), _frozen(True):
        result = features.get_features()
    assert result == {"export": True, "modify_toggle": True}, f"a missing baked module never hides anything (got {result})"


def test_frozen_baked_module_missing_attribute_defaults_to_shown():
    with _baked_module(export=False, modify_toggle=None), _frozen(True):
        result = features.get_features()
    assert result == {"export": False, "modify_toggle": True}, (
        f"a baked module missing MODIFY_TOGGLE falls back to True for just "
        f"that flag (got {result})")

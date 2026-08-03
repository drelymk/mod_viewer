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

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import build
from app import features, paths

FAILS = []


def check(cond, msg):
    print(("PASS  " if cond else "FAIL  ") + msg)
    if not cond:
        FAILS.append(msg)


def _fixture(tmp, text, name="features.ini"):
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


# ── build.py: resolve_features() / write_baked_features() ───────────────────

def test_resolve_features_normal_file():
    with tempfile.TemporaryDirectory() as tmp:
        path = _fixture(tmp, "[features]\nExport = 1\nModify_Toggle = 1\n")
        result = build.resolve_features(path)
    check(result == {"export": True, "modify_toggle": True},
          f"both flags on reads through cleanly (got {result})")


def test_resolve_features_export_disabled():
    with tempfile.TemporaryDirectory() as tmp:
        path = _fixture(tmp, "[features]\nExport = 0\nModify_Toggle = 1\n")
        result = build.resolve_features(path)
    check(result == {"export": False, "modify_toggle": True},
          f"Export = 0 resolves only the export flag to False (got {result})")


def test_resolve_features_modify_toggle_disabled():
    with tempfile.TemporaryDirectory() as tmp:
        path = _fixture(tmp, "[features]\nExport = 1\nModify_Toggle = 0\n")
        result = build.resolve_features(path)
    check(result == {"export": True, "modify_toggle": False},
          f"Modify_Toggle = 0 resolves only that flag to False (got {result})")


def test_resolve_features_both_disabled():
    with tempfile.TemporaryDirectory() as tmp:
        path = _fixture(tmp, "[features]\nExport = 0\nModify_Toggle = 0\n")
        result = build.resolve_features(path)
    check(result == {"export": False, "modify_toggle": False},
          f"both flags off at once (got {result})")


def test_resolve_features_missing_file_defaults_to_shown():
    with tempfile.TemporaryDirectory() as tmp:
        missing = os.path.join(tmp, "does_not_exist.ini")
        result = build.resolve_features(missing)
    check(result == {"export": True, "modify_toggle": True},
          f"a missing features.ini resolves to fully enabled (got {result})")


def test_resolve_features_missing_section_defaults_to_shown():
    with tempfile.TemporaryDirectory() as tmp:
        path = _fixture(tmp, "[wrong_section]\nExport = 0\n")
        result = build.resolve_features(path)
    check(result == {"export": True, "modify_toggle": True},
          f"a file with no [features] section resolves to fully enabled (got {result})")


def test_resolve_features_malformed_value_defaults_to_shown():
    with tempfile.TemporaryDirectory() as tmp:
        path = _fixture(tmp, "[features]\nExport = not_a_boolean\nModify_Toggle = 1\n")
        result = build.resolve_features(path)
    check(result == {"export": True, "modify_toggle": True},
          f"an unparseable value falls back to enabled rather than raising (got {result})")


def test_repo_features_ini_resolves_to_all_enabled():
    """Guards the actual checked-in features.ini: a fresh clone/build must
    show every feature unless someone deliberately edits the file."""
    result = build.resolve_features(build.FEATURES_FILE)
    check(result == {"export": True, "modify_toggle": True},
          f"the repo's shipped features.ini resolves to fully enabled (got {result})")


def test_write_baked_features_round_trips_through_import():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "_baked_features_test.py")
        build.write_baked_features({"export": False, "modify_toggle": True}, path=path)
        ns = {}
        with open(path, encoding="utf-8") as fh:
            exec(compile(fh.read(), path, "exec"), ns)
        check(ns.get("EXPORT") is False and ns.get("MODIFY_TOGGLE") is True,
              f"the generated module's constants match the flags passed in "
              f"(got EXPORT={ns.get('EXPORT')!r}, MODIFY_TOGGLE={ns.get('MODIFY_TOGGLE')!r})")
        build.clean_baked_features(path=path)
        check(not os.path.isfile(path), "clean_baked_features removes the generated module")


def test_clean_baked_features_is_a_noop_when_nothing_to_remove():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "never_written.py")
        build.clean_baked_features(path=path)  # must not raise
    check(True, "clean_baked_features tolerates a path with nothing to clean")


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
    check(result == {"export": True, "modify_toggle": True},
          f"running from source shows every feature even though a baked module "
          f"on sys.modules disables both (got {result})")


def test_frozen_reads_baked_export_disabled():
    with _baked_module(export=False, modify_toggle=True), _frozen(True):
        result = features.get_features()
    check(result == {"export": False, "modify_toggle": True},
          f"EXPORT=False in the baked module hides only the export flag (got {result})")


def test_frozen_reads_baked_modify_toggle_disabled():
    with _baked_module(export=True, modify_toggle=False), _frozen(True):
        result = features.get_features()
    check(result == {"export": True, "modify_toggle": False},
          f"MODIFY_TOGGLE=False in the baked module hides only that flag (got {result})")


def test_frozen_both_baked_disabled():
    with _baked_module(export=False, modify_toggle=False), _frozen(True):
        result = features.get_features()
    check(result == {"export": False, "modify_toggle": False},
          f"both flags off at once (got {result})")


def test_frozen_missing_baked_module_defaults_to_shown():
    """A real build always bakes the module right before PyInstaller runs, so
    this should never happen -- but if it somehow did (e.g. a build that
    skipped the bake step), the app must fail safe, not crash or silently
    hide something nobody chose to hide."""
    with _baked_module(present=False), _frozen(True):
        result = features.get_features()
    check(result == {"export": True, "modify_toggle": True},
          f"a missing baked module never hides anything (got {result})")


def test_frozen_baked_module_missing_attribute_defaults_to_shown():
    with _baked_module(export=False, modify_toggle=None), _frozen(True):
        result = features.get_features()
    check(result == {"export": False, "modify_toggle": True},
          f"a baked module missing MODIFY_TOGGLE falls back to True for just "
          f"that flag (got {result})")


if __name__ == "__main__":
    for fn in (test_resolve_features_normal_file,
               test_resolve_features_export_disabled,
               test_resolve_features_modify_toggle_disabled,
               test_resolve_features_both_disabled,
               test_resolve_features_missing_file_defaults_to_shown,
               test_resolve_features_missing_section_defaults_to_shown,
               test_resolve_features_malformed_value_defaults_to_shown,
               test_repo_features_ini_resolves_to_all_enabled,
               test_write_baked_features_round_trips_through_import,
               test_clean_baked_features_is_a_noop_when_nothing_to_remove,
               test_not_frozen_always_shows_everything_even_with_a_baked_module_present,
               test_frozen_reads_baked_export_disabled,
               test_frozen_reads_baked_modify_toggle_disabled,
               test_frozen_both_baked_disabled,
               test_frozen_missing_baked_module_defaults_to_shown,
               test_frozen_baked_module_missing_attribute_defaults_to_shown):
        fn()
    print("\n" + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"))
    sys.exit(1 if FAILS else 0)

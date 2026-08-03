"""app/mod_loader.py's build_toggle_panel: which [Key...] cycle sections show
up in the Toggle panel, and which of their vars.

A section that already gates a visible mesh only ever shows its gating
var(s) — unchanged, long-standing behaviour.

A section that gates nothing YET is only shown (marked "wired": False,
listing its writable/non-namespaced vars so its Record button is reachable
immediately) when it's also listed in `pending_new_sections` — meaning the
user created it via the app's Add button this session and hasn't wired it
yet (edit_session.new_sections_for). Any OTHER not-yet-gating section —
most commonly a pre-existing, on-disk utility key like $menu/$skin that was
never meant to gate anything — is dropped outright and never shown, since
the app has no business loading or touching it at all (see
.copilot/context.md's Key decisions for the full rationale).
"""

import os, sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.mod_loader import build_toggle_panel

FAILS = []


def check(cond, msg):
    print(("PASS  " if cond else "FAIL  ") + msg)
    if not cond:
        FAILS.append(msg)


def _key(name, varvals, key="", key_display="", source=None, ini_path="mod.ini"):
    return {
        "name": name, "key": key, "key_display": key_display,
        "vars": dict(varvals), "source": source,
        "ini_path": ini_path, "section": f"Key{name}",
    }


def test_wired_toggle_shows_only_its_gating_vars():
    """Long-standing behaviour, unchanged: when a section has one var that
    gates something and a sibling var that doesn't, only the gating var is
    shown — and the whole section counts as wired."""
    toggle_keys = {"KeyUpper": _key("Upper", {"Upper": ["0", "1"], "TT": ["0", "1"]})}
    panel = build_toggle_panel(toggle_keys, {}, gating_vars={"Upper"}, mod_dir=None)
    check("KeyUpper" in panel, "the section appears")
    entry = panel["KeyUpper"]
    check(entry["wired"] is True, f"marked wired (got {entry['wired']})")
    names = [v["var"] for v in entry["vars"]]
    check(names == ["Upper"], f"only the gating var is listed (got {names})")


def test_unwired_pending_toggle_shown_with_writable_vars():
    """A section that gates nothing at all is still shown -- not dropped --
    when it's flagged as this session's own pending add: marked unwired,
    listing its writable var(s) so Record has something to work with
    immediately."""
    toggle_keys = {"KeyNew": _key("New", {"Fresh": ["0", "1", "2"]})}
    panel = build_toggle_panel(toggle_keys, {}, gating_vars=set(), mod_dir=None,
                               pending_new_sections={"mod.ini": {"KeyNew"}})
    check("KeyNew" in panel, "a pending, not-yet-gating toggle still appears in the panel")
    entry = panel["KeyNew"]
    check(entry["wired"] is False, f"marked unwired (got {entry['wired']})")
    names = [v["var"] for v in entry["vars"]]
    check(names == ["Fresh"], f"its writable var is listed for Record to use (got {names})")


def test_unwired_non_pending_toggle_is_hidden():
    """The core of this feature: a not-yet-gating section that ISN'T in
    pending_new_sections -- e.g. a pre-existing, on-disk utility key like
    $menu/$skin that was never meant to gate a mesh -- is dropped outright,
    exactly like any other unrecordable section. No pending_new_sections
    argument at all (the default) behaves the same way."""
    toggle_keys = {"KeyMenu": _key("Menu", {"menu": ["0", "1"]})}
    panel = build_toggle_panel(toggle_keys, {}, gating_vars=set(), mod_dir=None)
    check("KeyMenu" not in panel, "a non-gating, non-pending section is never shown")

    panel2 = build_toggle_panel(toggle_keys, {}, gating_vars=set(), mod_dir=None,
                                pending_new_sections={"mod.ini": {"KeyOther"}})
    check("KeyMenu" not in panel2, "still hidden when pending_new_sections lists a different section")


def test_pending_new_sections_scoped_by_ini():
    """pending_new_sections is keyed by ini basename -- a section name match
    in the wrong ini must not leak visibility across files."""
    toggle_keys = {"KeyNew": _key("New", {"Fresh": ["0", "1"]}, ini_path="other.ini")}
    panel = build_toggle_panel(toggle_keys, {}, gating_vars=set(), mod_dir=None,
                               pending_new_sections={"mod.ini": {"KeyNew"}})
    check("KeyNew" not in panel, "same section name in a different ini doesn't match")


def test_unwired_toggle_excludes_namespaced_vars():
    """A namespaced var (cross-ini, read-only) never belongs in the fallback
    list even when unwired — it can't actually be recorded either way, same
    exclusion record_editor.writable_cycle_vars already applies."""
    toggle_keys = {"KeyMixed": _key("Mixed", {
        "Local": ["0", "1"], "\\Mod\\Master\\swapvar": ["0", "1", "2"],
    })}
    panel = build_toggle_panel(toggle_keys, {}, gating_vars=set(), mod_dir=None,
                               pending_new_sections={"mod.ini": {"KeyMixed"}})
    check("KeyMixed" in panel, "the section still appears (has a writable var)")
    names = [v["var"] for v in panel["KeyMixed"]["vars"]]
    check(names == ["Local"], f"only the non-namespaced var is listed (got {names})")


def test_fully_namespaced_ungated_pending_section_is_still_hidden():
    """Nothing recordable at all -- no gating var, and no writable var to
    fall back to -- so the section is dropped even if it happens to be
    listed in pending_new_sections (can't actually happen in practice, since
    add_toggle rejects creating a namespaced-only var, but the panel builder
    itself must not be fooled either way)."""
    toggle_keys = {"KeyGlobal": _key("Global", {"\\Mod\\Master\\swapvar": ["0", "1"]})}
    panel = build_toggle_panel(toggle_keys, {}, gating_vars=set(), mod_dir=None,
                               pending_new_sections={"mod.ini": {"KeyGlobal"}})
    check("KeyGlobal" not in panel, "a section with nothing recordable is still dropped")


def test_default_prefers_declared_default_over_first_cycle_value():
    """toggle_defaults (from a `global $var = X` Constants-style line) wins
    over the cycle list's own first value -- exercised through both the wired
    and the new unwired path, since both build the same "default" field the
    same way."""
    toggle_keys = {"KeyNew": _key("New", {"Fresh": ["0", "1", "2"]})}
    panel = build_toggle_panel(toggle_keys, {"Fresh": "1"}, gating_vars=set(), mod_dir=None,
                               pending_new_sections={"mod.ini": {"KeyNew"}})
    entry = panel["KeyNew"]["vars"][0]
    check(entry["default"] == "1", f"declared default wins over values[0] (got {entry['default']!r})")


def test_wired_and_unwired_can_coexist_across_sections():
    """A realistic mixed-mod payload: one already-wired toggle and one
    freshly-added, pending, not-yet-wired toggle both survive into the same
    panel, while an unrelated pre-existing non-gating key stays hidden."""
    toggle_keys = {
        "KeyUpper": _key("Upper", {"Upper": ["0", "1"]}),
        "KeyNew":   _key("New", {"Fresh": ["0", "1"]}),
        "KeyMenu":  _key("Menu", {"menu": ["0", "1"]}),
    }
    panel = build_toggle_panel(toggle_keys, {}, gating_vars={"Upper"}, mod_dir=None,
                               pending_new_sections={"mod.ini": {"KeyNew"}})
    check(panel.get("KeyUpper", {}).get("wired") is True, "the pre-existing toggle stays wired")
    check(panel.get("KeyNew", {}).get("wired") is False, "the pending new toggle is present and marked unwired")
    check("KeyMenu" not in panel, "the unrelated non-gating, non-pending key stays hidden")


if __name__ == "__main__":
    for fn in (test_wired_toggle_shows_only_its_gating_vars,
               test_unwired_pending_toggle_shown_with_writable_vars,
               test_unwired_non_pending_toggle_is_hidden,
               test_pending_new_sections_scoped_by_ini,
               test_unwired_toggle_excludes_namespaced_vars,
               test_fully_namespaced_ungated_pending_section_is_still_hidden,
               test_default_prefers_declared_default_over_first_cycle_value,
               test_wired_and_unwired_can_coexist_across_sections):
        fn()
    print("\n" + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"))
    sys.exit(1 if FAILS else 0)

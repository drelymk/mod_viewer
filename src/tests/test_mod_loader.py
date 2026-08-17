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

import os, struct, sys, tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.mod_loader import (_attach_shape_sliders, build_toggle_panel,
                            load_mod, RESERVED_KEYS, _parse_inis)

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


def test_shape_sliders_follow_mid_section_position_reassignments():
    """One override may begin with Legs and switch to Body buffers later.
    Both morph descriptions must reach the group; mesh_builder filters them
    against each draw's effective position buffer."""
    groups = [{
        "position_file": r"Meshes\LegsPosition.buf",
        "draws": [
            {"label": "Part-1"},
            {"label": "Part-2", "position_file": r"Meshes\BodyPosition.buf"},
        ],
    }]
    sliders = [
        {"var": "currFlat", "base_file": r"Meshes\LegsPosition.buf",
         "target_file": r"Meshes\LegsPositionFlat.buf"},
        {"var": "currFlat", "base_file": r"Meshes\BodyPosition.buf",
         "target_file": r"Meshes\BodyPositionFlat.buf"},
        {"var": "other", "base_file": r"Meshes\HairPosition.buf",
         "target_file": r"Meshes\HairPositionFlat.buf"},
    ]
    _attach_shape_sliders(groups, sliders)
    attached = groups[0].get("shape_sliders", [])
    check([item["target_file"] for item in attached] ==
          [r"Meshes\LegsPositionFlat.buf", r"Meshes\BodyPositionFlat.buf"],
          f"group receives its base and reassigned-draw morphs only (got {attached})")


def test_nested_ini_resources_are_relative_to_their_ini():
    ini = """[TextureOverride{0}Position]
vb0 = Resource{0}Position
[TextureOverride{0}Texcoord]
vb1 = Resource{0}Texcoord
[TextureOverride{0}]
ib = Resource{0}IB
drawindexed = 3,0,0
[Resource{0}Position]
filename = p.buf
stride = 12
[Resource{0}Texcoord]
filename = t.buf
stride = 8
[Resource{0}IB]
filename = i.buf
format = R32_UINT
"""
    with tempfile.TemporaryDirectory() as root:
        for relative, name in (("", "Root"), ("nested", "Nested")):
            folder = os.path.join(root, relative)
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, f"{name.lower()}.ini"), "w",
                      encoding="utf-8") as stream:
                stream.write(ini.format(name))
            with open(os.path.join(folder, "i.buf"), "wb") as stream:
                stream.write(struct.pack("<3I", 0, 1, 2))
            with open(os.path.join(folder, "p.buf"), "wb") as stream:
                stream.write(struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0))
            with open(os.path.join(folder, "t.buf"), "wb") as stream:
                stream.write(struct.pack("<6f", 0, 0, 1, 0, 0, 1))

        payload = load_mod(root)
        meshes = [value for key, value in payload.items()
                  if key not in RESERVED_KEYS]
        check(not payload.get("error") and len(meshes) == 2,
              "root and nested geometry both load from same-named local buffers")
        check({mesh.get("source") for mesh in meshes} == {"root", "nested"},
              "nested geometry keeps a distinct root-relative source label")
        source_inis = {src.get("ini") for mesh in meshes
                       for src in mesh.get("sources", [])}
        check("nested/nested.ini" in source_inis,
              "nested mesh provenance uses a root-relative INI path")


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

        _groups, toggles, _menu, defaults, _rules, _present = _parse_inis(
            paths, root)
        check(set(toggles) == {"nested/body::KeySwap", "nested/hair::KeySwap"},
              "nested sibling INIs keep duplicate key sections distinct")
        check(set(defaults) == {"nested/body::swapvar", "nested/hair::swapvar"},
              "nested sibling INIs keep duplicate variables distinct")
        check({item.get("source") for item in toggles.values()} == {"nested"},
              "unique parser identities retain the shared compact UI group")


if __name__ == "__main__":
    for fn in (test_wired_toggle_shows_only_its_gating_vars,
               test_unwired_pending_toggle_shown_with_writable_vars,
               test_unwired_non_pending_toggle_is_hidden,
               test_pending_new_sections_scoped_by_ini,
               test_unwired_toggle_excludes_namespaced_vars,
               test_fully_namespaced_ungated_pending_section_is_still_hidden,
               test_default_prefers_declared_default_over_first_cycle_value,
               test_wired_and_unwired_can_coexist_across_sections,
               test_shape_sliders_follow_mid_section_position_reassignments,
               test_nested_ini_resources_are_relative_to_their_ini,
               test_nested_sibling_inis_have_unique_parser_namespaces):
        fn()
    print("\n" + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"))
    sys.exit(1 if FAILS else 0)

"""app/mods/loader.py's build_toggle_panel: which [Key...] cycle sections show
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

import os, struct, tempfile


from app.mods.loader import (_attach_shape_sliders, build_toggle_panel,
                            load_mod, _parse_inis)


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
    master = "\\Some\\Master\\State"
    toggle_keys = {"KeyUpper": _key("Upper", {
        "Upper": ["0", "1"], master: ["0", "1", "2"],
    })}
    panel = build_toggle_panel(toggle_keys, {}, gating_vars={"Upper"}, mod_dir=None)
    assert ("KeyUpper" in panel), ("the section appears")
    entry = panel["KeyUpper"]
    assert (entry["wired"] is True), (f"marked wired (got {entry['wired']})")
    names = [v["var"] for v in entry["vars"]]
    assert (names == ["Upper"]), (f"only the gating var is listed (got {names})")
    cycle_names = [v["var"] for v in entry["cycle_vars"]]
    assert (cycle_names == ["Upper", master]), (
        f"Record receives the complete co-driven tuple (got {cycle_names})")
    assert entry["cycle_vars"][1]["values"] == ["0", "1", "2"]


def test_unwired_pending_toggle_shown_with_writable_vars():
    """A section that gates nothing at all is still shown -- not dropped --
    when it's flagged as this session's own pending add: marked unwired,
    listing its writable var(s) so Record has something to work with
    immediately."""
    toggle_keys = {"KeyNew": _key("New", {"Fresh": ["0", "1", "2"]})}
    panel = build_toggle_panel(toggle_keys, {}, gating_vars=set(), mod_dir=None,
                               pending_new_sections={"mod.ini": {"KeyNew"}})
    assert ("KeyNew" in panel), ("a pending, not-yet-gating toggle still appears in the panel")
    entry = panel["KeyNew"]
    assert (entry["wired"] is False), (f"marked unwired (got {entry['wired']})")
    names = [v["var"] for v in entry["vars"]]
    assert (names == ["Fresh"]), (f"its writable var is listed for Record to use (got {names})")


def test_unwired_non_pending_toggle_is_hidden():
    """The core of this feature: a not-yet-gating section that ISN'T in
    pending_new_sections -- e.g. a pre-existing, on-disk utility key like
    $menu/$skin that was never meant to gate a mesh -- is dropped outright,
    exactly like any other unrecordable section. No pending_new_sections
    argument at all (the default) behaves the same way."""
    toggle_keys = {"KeyMenu": _key("Menu", {"menu": ["0", "1"]})}
    panel = build_toggle_panel(toggle_keys, {}, gating_vars=set(), mod_dir=None)
    assert ("KeyMenu" not in panel), ("a non-gating, non-pending section is never shown")

    panel2 = build_toggle_panel(toggle_keys, {}, gating_vars=set(), mod_dir=None,
                                pending_new_sections={"mod.ini": {"KeyOther"}})
    assert ("KeyMenu" not in panel2), ("still hidden when pending_new_sections lists a different section")


def test_pending_new_sections_scoped_by_ini():
    """pending_new_sections is keyed by ini basename -- a section name match
    in the wrong ini must not leak visibility across files."""
    toggle_keys = {"KeyNew": _key("New", {"Fresh": ["0", "1"]}, ini_path="other.ini")}
    panel = build_toggle_panel(toggle_keys, {}, gating_vars=set(), mod_dir=None,
                               pending_new_sections={"mod.ini": {"KeyNew"}})
    assert ("KeyNew" not in panel), ("same section name in a different ini doesn't match")


def test_unwired_toggle_excludes_namespaced_vars():
    """A namespaced var (cross-ini, read-only) never belongs in the fallback
    list even when unwired — it can't actually be recorded either way, same
    exclusion record_editor.writable_cycle_vars already applies."""
    toggle_keys = {"KeyMixed": _key("Mixed", {
        "Local": ["0", "1"], "\\Mod\\Master\\swapvar": ["0", "1", "2"],
    })}
    panel = build_toggle_panel(toggle_keys, {}, gating_vars=set(), mod_dir=None,
                               pending_new_sections={"mod.ini": {"KeyMixed"}})
    assert ("KeyMixed" in panel), ("the section still appears (has a writable var)")
    names = [v["var"] for v in panel["KeyMixed"]["vars"]]
    assert (names == ["Local"]), (f"only the non-namespaced var is listed (got {names})")




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
        meshes = list(payload.get("meshes", {}).values())
        assert (not payload.get("error") and len(meshes) == 2), ("root and nested geometry both load from same-named local buffers")
        assert ({mesh.get("source") for mesh in meshes} == {"root", "nested"}), ("nested geometry keeps a distinct root-relative source label")
        source_inis = {src.get("ini") for mesh in meshes
                       for src in mesh.get("sources", [])}
        assert ("nested/nested.ini" in source_inis), ("nested mesh provenance uses a root-relative INI path")


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
        assert (set(toggles) == {"nested/body::KeySwap", "nested/hair::KeySwap"}), ("nested sibling INIs keep duplicate key sections distinct")
        assert (set(defaults) == {"nested/body::swapvar", "nested/hair::swapvar"}), ("nested sibling INIs keep duplicate variables distinct")
        assert ({item.get("source") for item in toggles.values()} == {"nested"}), ("unique parser identities retain the shared compact UI group")


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

        _groups, _toggles, menu, _defaults, _rules, _present = _parse_inis(
            paths, root)
        images = {os.path.basename(info["ini_path"]): info.get("image_file")
                  for info in menu.values() if info.get("slot") == 1}
        assert (images == {"body.ini": os.path.join("nested", "body.dds"),
                         "hair.ini": os.path.join("nested", "hair.dds")}), (f"nested sibling menu entries retain their own images (got {images})")

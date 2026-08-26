"""Control-panel projections and lightweight semantic reads."""

from types import SimpleNamespace

from app.mods.analysis import ParsedModAnalysis
from app.mods.controls import load_control_state, load_present_state
from app.mods.loader import ModLoadContext

from app.mods.controls import build_toggle_panel


def _key(name, varvals, key="", key_display="", source=None, ini_path="mod.ini"):
    return {
        "name": name, "key": key, "key_display": key_display,
        "vars": dict(varvals), "source": source,
        "ini_path": ini_path, "section": f"Key{name}",
    }


def test_wired_toggle_shows_only_its_gating_vars():
    """Wired sections show gating vars but retain the full record tuple."""
    master = "\\Some\\Master\\State"
    toggle_keys = {"KeyUpper": _key("Upper", {
        "Upper": ["0", "1"], master: ["0", "1", "2"],
    })}
    panel = build_toggle_panel(toggle_keys, {}, gating_vars={"Upper"}, mod_dir=None)
    assert "KeyUpper" in panel
    entry = panel["KeyUpper"]
    assert entry["wired"] is True
    assert [v["var"] for v in entry["vars"]] == ["Upper"]
    assert [v["var"] for v in entry["cycle_vars"]] == ["Upper", master]
    assert entry["cycle_vars"][1]["values"] == ["0", "1", "2"]


def test_unwired_pending_toggle_shown_with_writable_vars():
    toggle_keys = {"KeyNew": _key("New", {"Fresh": ["0", "1", "2"]})}
    panel = build_toggle_panel(
        toggle_keys, {}, gating_vars=set(), mod_dir=None,
        pending_new_sections={"mod.ini": {"KeyNew"}},
    )
    assert "KeyNew" in panel
    entry = panel["KeyNew"]
    assert entry["wired"] is False
    assert [v["var"] for v in entry["vars"]] == ["Fresh"]


def test_unwired_non_pending_toggle_is_hidden():
    toggle_keys = {"KeyMenu": _key("Menu", {"menu": ["0", "1"]})}
    panel = build_toggle_panel(toggle_keys, {}, gating_vars=set(), mod_dir=None)
    assert "KeyMenu" not in panel

    panel2 = build_toggle_panel(
        toggle_keys, {}, gating_vars=set(), mod_dir=None,
        pending_new_sections={"mod.ini": {"KeyOther"}},
    )
    assert "KeyMenu" not in panel2


def test_pending_new_sections_scoped_by_ini():
    toggle_keys = {
        "KeyNew": _key("New", {"Fresh": ["0", "1"]}, ini_path="other.ini")
    }
    panel = build_toggle_panel(
        toggle_keys, {}, gating_vars=set(), mod_dir=None,
        pending_new_sections={"mod.ini": {"KeyNew"}},
    )
    assert "KeyNew" not in panel


def test_unwired_toggle_excludes_namespaced_vars():
    toggle_keys = {"KeyMixed": _key("Mixed", {
        "Local": ["0", "1"], "\\Mod\\Master\\swapvar": ["0", "1", "2"],
    })}
    panel = build_toggle_panel(
        toggle_keys, {}, gating_vars=set(), mod_dir=None,
        pending_new_sections={"mod.ini": {"KeyMixed"}},
    )
    assert "KeyMixed" in panel
    assert [v["var"] for v in panel["KeyMixed"]["vars"]] == ["Local"]


def test_present_state_does_not_build_geometry_or_render_textures(
        tmp_path, monkeypatch):
    ini_path = tmp_path / "mod.ini"
    ini_path.write_text(
        "[KeyModViewerPresent]\n"
        "key = p\n"
        "type = cycle\n"
        "$Outfit = 0,1\n",
        encoding="utf-8",
    )
    context = ModLoadContext(str(tmp_path), [str(ini_path)])

    monkeypatch.setattr(
        "app.mods.controls.build_mesh_semantics",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("PRESENT reads must not build mesh semantics")),
    )
    monkeypatch.setattr(
        "app.mods.controls.encode_texture_data_uri",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("PRESENT reads must not render textures")),
    )

    present = load_present_state(context)

    assert present["item"]["key_raw"] == "p"
    assert present["item"]["count"] == 2


def test_control_state_does_not_build_geometry_or_render_textures(
        tmp_path, monkeypatch):
    parsed = ParsedModAnalysis(
        groups=[{"draws": [{"conditions": [[{
            "var": "Outfit", "value": "1", "negate": False,
        }]], "texture_variants": []}]}],
        toggles={"KeyOutfit": {
            "name": "Outfit", "key_display": "", "key": "",
            "source": None, "ini_path": str(tmp_path / "mod.ini"),
            "section": "KeyOutfit", "vars": {"Outfit": ["0", "1"]},
        }},
        menu={}, defaults={"Outfit": "0"}, state_rules=[], present={},
        game=SimpleNamespace(game="unknown"),
    )
    context = ModLoadContext(str(tmp_path), [str(tmp_path / "mod.ini")])
    monkeypatch.setattr(
        "app.mods.controls.analyze_mod_inis", lambda *args, **kwargs: parsed)

    monkeypatch.setattr(
        "app.mods.controls.build_mesh_semantics",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("control reads must not build mesh semantics")),
    )
    monkeypatch.setattr(
        "app.mods.controls.encode_texture_data_uri",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("control reads must not render textures")),
    )

    result = load_control_state(context)

    assert set(result["controls"]["toggles"]) == {"KeyOutfit"}

"""Global viewer preference persistence, validation and config preservation."""

import json

import pytest

from app.settings import mod_folders, viewer_preferences


def test_viewer_preferences_explicit_lifecycle_preserves_other_config(tmp_path):
    filename = tmp_path / "config.json"
    assert viewer_preferences.load_preferences(filename) == {}
    assert viewer_preferences.save_preferences({}, filename) == {}
    assert not filename.exists()

    mod_folders.save_panel_opacity(35, filename)
    mod_folders.save_language("ja", filename)
    original = json.loads(filename.read_text(encoding="utf-8"))
    original["assetFolders"] = [{"name": "Library", "enabled": True}]
    filename.write_text(json.dumps(original), encoding="utf-8")
    settings = {
        "environment": "studio", "wireframe": True, "outlines": True,
        "ambientOcclusion": 0.4, "bloom": True, "glossy": True,
        "toonShading": True, "grid": False, "smoothShading": False,
        "textureMode": "diffuse", "keyLightIntensity": 0.75,
        "navigationGizmo": False,
    }
    for name, value in settings.items():
        viewer_preferences.save_preferences({name: value}, filename)
    assert viewer_preferences.load_preferences(filename) == settings
    assert json.loads(filename.read_text(encoding="utf-8")) == {
        **original, "viewerPreferences": settings,
    }

    defaults = {
        "environment": "default", "wireframe": False, "outlines": False,
        "ambientOcclusion": 0, "bloom": False, "glossy": False,
        "toonShading": False, "grid": True, "smoothShading": True,
        "textureMode": "all", "keyLightIntensity": 1,
        "navigationGizmo": True,
    }
    assert viewer_preferences.save_preferences(defaults, filename) == defaults
    root = tmp_path / "root"
    root.mkdir()
    mod_folders.add_folder("Library", str(root), filename)
    mod_folders.save_panel_opacity(58, filename)
    saved = json.loads(filename.read_text(encoding="utf-8"))
    assert saved["viewerPreferences"] == defaults
    assert saved["panelOpacity"] == 58
    assert saved["language"] == "ja"
    assert saved["assetFolders"] == original["assetFolders"]
    assert not filename.with_suffix(".json.tmp").exists()


@pytest.mark.parametrize("changes", [
    None, [], {"orientation": 90}, {"wireframe": 1}, {"grid": "false"},
    {"environment": "unknown"}, {"textureMode": "unknown"},
    {"ambientOcclusion": True}, {"ambientOcclusion": -0.1},
    {"ambientOcclusion": 1.1}, {"keyLightIntensity": 1.6},
    {"keyLightIntensity": float("nan")}, {"keyLightIntensity": float("inf")},
    {"keyLightIntensity": 10 ** 1000},
    {"environment": "studio", "bloom": "true"},
])
def test_invalid_preference_patch_never_partially_writes(tmp_path, changes):
    filename = tmp_path / "config.json"
    viewer_preferences.save_preferences({"environment": "indoor"}, filename)
    original = filename.read_bytes()
    with pytest.raises(ValueError):
        viewer_preferences.save_preferences(changes, filename)
    assert filename.read_bytes() == original


@pytest.mark.parametrize("original", [
    "{", '{"version": 2, "modFolders": []}',
    '{"version": 1, "modFolders": [], "viewerPreferences": []}',
    '{"version": 1, "modFolders": [], "viewerPreferences": {"bloom": 1}}',
])
def test_malformed_preference_config_remains_untouched(tmp_path, original):
    filename = tmp_path / "config.json"
    filename.write_text(original, encoding="utf-8")
    with pytest.raises(ValueError):
        viewer_preferences.load_preferences(filename)
    with pytest.raises(ValueError):
        viewer_preferences.save_preferences({"grid": False}, filename)
    assert filename.read_text(encoding="utf-8") == original

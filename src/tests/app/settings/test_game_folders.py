import json

import pytest

from app.bridge.api import ModViewerAPI
from app.settings import game_folders, paths


def test_game_folders_persist_without_losing_other_config(tmp_path, monkeypatch):
    filename = tmp_path / "config.json"
    filename.write_text(json.dumps({
        "version": 1, "modFolders": [], "viewerPreferences": {"grid": False}}), encoding="utf-8")
    monkeypatch.setattr(paths, "config_path", lambda: str(filename))
    first, second = str(tmp_path / "one"), str(tmp_path / "two")

    assert game_folders.load_game_folders() == {}
    game_folders.save_game_folder("WWMI", first)
    assert game_folders.save_game_folder("GIMI", second) == {"WWMI": first, "GIMI": second}
    assert game_folders.save_game_folder("WWMI", None) == {"GIMI": second}
    stored = json.loads(filename.read_text(encoding="utf-8"))
    assert stored["viewerPreferences"] == {"grid": False} and stored["gameFolders"] == {"GIMI": second}
    game_folders.save_game_folder("GIMI", None)
    assert "gameFolders" not in json.loads(filename.read_text(encoding="utf-8"))


@pytest.mark.parametrize("kind, path", [("Other", "C:/game"), ("WWMI", "relative"), ("WWMI", 5)])
def test_game_folders_reject_unknown_kinds_and_non_absolute_paths(tmp_path, monkeypatch, kind, path):
    monkeypatch.setattr(paths, "config_path", lambda: str(tmp_path / "config.json"))
    with pytest.raises(ValueError):
        game_folders.save_game_folder(kind, path)


def test_api_selects_and_clears_game_directories(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "config_path", lambda: str(tmp_path / "config.json"))
    folder = tmp_path / "zzmi-game"
    folder.mkdir()

    class Window:
        def create_file_dialog(self, *_):
            return [str(folder)]

    api = ModViewerAPI()
    api._window = Window()
    assert api.select_game_directory("ZZMI") == {"value": {"ZZMI": str(folder.resolve())}}
    assert api.get_game_folders() == {"value": {"ZZMI": str(folder.resolve())}}
    assert "error" in api.select_game_directory("Other")
    assert api.select_game_directory("WWMI")["error"]
    assert api.get_saved_game_folder() is None
    assert api.clear_game_directory("ZZMI") == {"value": {}}

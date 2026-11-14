import json
import threading
import time
from types import SimpleNamespace

from app.assets.game_extract import game_index, pipeline, runtime
from app.assets.game_extract.runtime import SearchTruncated
from app.bridge.rig_extraction import RigExtraction
from app.settings import game_folders, paths
from .test_game_extraction import MESH, fixture


def game_folder(tmp_path):
    paks = tmp_path / "game/Client/Content/Paks"
    paks.mkdir(parents=True)
    (paks / "data.pak").write_bytes(b"fixture archive")
    return tmp_path / "game", paks


class IndexClient:
    """Role root is too broad, so the index must recurse through its folders."""

    def __init__(self, work):
        self.work = work
        self.searches = []

    def close(self):
        pass

    def search(self, query, kind, under):
        self.searches.append(under)
        if under == pipeline.ROLE_ROOT:
            raise SearchTruncated("too broad")
        return {pipeline.ROLE_ROOT + "/BodyType01": [MESH, MESH + "Other"]}.get(under, [])

    def folders(self, folder):
        return ["BodyType01"] if folder == pipeline.ROLE_ROOT else []

    def batch(self, kind, packages, destination, format=None):
        destination.mkdir(parents=True, exist_ok=True)
        result = {}
        for package in packages:
            file = destination / (package.rsplit("/", 1)[-1] + ".json")
            sections = [dict(NumVertices=3, NumTriangles=1)] if package == MESH else []
            file.write_text(json.dumps([dict(Type="SkeletalMesh", LODModels=[dict(Sections=sections)])]))
            result[package] = dict(state="completed", rootFiles=[str(file)], failures=[])
        return result


def test_index_lifecycle_storage_staleness_and_invalid_files(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "asset_index_dir", lambda: str(tmp_path / "asset-index"))
    game, paks = game_folder(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    assert game_index.status(game) == dict(state="missing", meshes=0)
    client, progress = IndexClient(work), []
    index, count = game_index.build(client, game, lambda *row: progress.append(row), threading.Event())
    assert count == 1 and index.shapes(MESH) == {(3, 3)} and MESH + "Other" not in index.meshes
    assert progress[-1] == ("index", 2, 2)
    file = game_index.index_path(game)
    assert file.parent == tmp_path / "asset-index" and file.name.startswith("game-wwmi-")
    assert game_index.status(game) == dict(state="ready", meshes=1)
    assert game_index.ensure(None, game, lambda *_: None, threading.Event()).search("model", pipeline.ROLE_ROOT) == [MESH]
    (paks / "data.pak").write_bytes(b"updated fixture archive")
    assert game_index.status(game)["state"] == "stale"
    file.write_text("{not json")
    assert game_index.status(game)["state"] == "stale"
    file.write_text(json.dumps(dict(format=1, fingerprint="x", meshes={"Outside/Path": []})))
    assert game_index.status(game)["state"] == "stale"
    game_index.remove(game)
    game_index.remove(game)
    assert game_index.status(game)["state"] == "missing"


def test_index_replaces_native_mesh_lookup_without_changing_the_result(tmp_path):
    root, _, record, client, _ = fixture(tmp_path)
    searched = []
    original = client.search
    client.search = lambda query, kind, under: searched.append(kind) or original(query, kind, under)
    index = game_index.GameIndex({MESH: [[3, 3]], MESH + "Decoy": [[9, 9]]})
    result, _ = pipeline.build(client, str(root), record, tmp_path / "output", "Unrelated Name", lambda *_: None, threading.Event(), index)
    assert result["bones"] == 2 and result["unmatched_components"] == 0
    assert "SkeletalMesh" not in searched


def test_picked_game_updates_settings_and_index_job_uses_it(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "config_path", lambda: str(tmp_path / "config.json"))
    monkeypatch.setattr(paths, "asset_index_dir", lambda: str(tmp_path / "asset-index"))
    game, _ = game_folder(tmp_path)
    service = RigExtraction(lambda _: None)
    assert "error" in service.start_index()
    assert service.index_status() == dict(state="none", meshes=0)
    service.pick_game(SimpleNamespace(create_file_dialog=lambda *_: [str(game)]))
    assert game_folders.load_game_folders()["WWMI"] == str(game.resolve())
    assert service.index_status()["state"] == "missing"
    monkeypatch.setattr("app.assets.game_extract.runtime.ensure_runtime", lambda cancel: "tool.exe")
    monkeypatch.setattr("app.assets.game_extract.runtime.Client", lambda executable, folder, work, cancel: IndexClient(work))
    grants = dict(service._games)
    job = service.start_index()["job_id"]
    deadline = time.monotonic() + 5
    while service.status(job)["status"] == "running" or service._active:
        assert time.monotonic() < deadline
        time.sleep(.01)
    assert service.status(job)["status"] == "complete" and service.status(job)["result"] == {"meshes": 1}
    assert service.index_status() == dict(state="ready", meshes=1)
    assert service._games == grants


def test_runtime_folder_listing_rejects_path_like_names(tmp_path):
    client = runtime.Client.__new__(runtime.Client)
    client.call = lambda *_arguments, **_options: dict(folders=["BodyType01", "../escape"])
    try:
        client.folders("Client/Content")
    except ValueError:
        return
    raise AssertionError("path-like folder names must be rejected")

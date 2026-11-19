"""Synthetic Genshin Impact archive, metadata index and pointer contracts."""

import json
import os
import shutil
import threading
from unittest.mock import patch

import pytest

from app.assets.game_extract import gi_index, gi_runtime
from app.settings import paths


def write_block(folder, relative, data=b"block"):
    path = folder / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def rows_for(block):
    number = int(block.rsplit("/", 1)[-1].split(".", 1)[0])
    return [
        dict(name="Controller01", type="AnimatorController", block=block, cab="CAB-01", offset=0, file_ordinal=0, path_id=number * 10 + 1),
        dict(name="Clip01", type="AnimationClip", block=block, cab="CAB-01", offset=8, file_ordinal=0, path_id=number * 10 + 2),
        dict(name="Mesh01", type="Mesh", block=block, cab="CAB-01", offset=16, file_ordinal=0, path_id=number * 10 + 3, shape=[[3, 3]]),
        dict(name="Avatar01", type="Avatar", block=block, cab="CAB-01", offset=24, file_ordinal=0, path_id=number * 10 + 4),
    ]


class Client:
    def __init__(self, folder, mutate=lambda row: row):
        self.root, self.mutate = folder.resolve(), mutate

    def scan_many(self, files):
        return [self.mutate(row) for file in files for row in rows_for(file.relative_to(self.root).as_posix())]


def test_gi_folder_validation_and_effective_block_set(tmp_path):
    missing = tmp_path / "missing"
    missing.mkdir()
    with pytest.raises(ValueError, match="Choose the Genshin Impact game folder"):
        gi_runtime.validate_folder(missing)
    (missing / "YuanShen_Data").mkdir()
    with pytest.raises(ValueError, match="global Genshin Impact client layout"):
        gi_runtime.validate_folder(missing)

    folder = tmp_path / "game"
    streaming = gi_runtime.BLOCK_DIRS[0]
    persistent = gi_runtime.BLOCK_DIRS[1]
    for relative in (streaming + "/00/1.blk", streaming + "/00/2.blk", streaming + "/01/3.blk", persistent + "/00/2.blk", persistent + "/02/4.blk"):
        write_block(folder, relative)
    write_block(folder, streaming + "/loose.blk")
    root = folder.resolve()
    files = gi_runtime.archives(folder)
    assert len(files) == 4
    relatives = {file.relative_to(root).as_posix() for file in files}
    assert relatives == {streaming + "/00/1.blk", persistent + "/00/2.blk", streaming + "/01/3.blk", persistent + "/02/4.blk"}
    assert streaming + "/loose.blk" not in relatives
    # Persistent overrides the StreamingAssets block with the same relative path.
    assert next(file for file in files if file.name == "2.blk").relative_to(root).as_posix() == persistent + "/00/2.blk"

    # A missing Persistent tree is valid and leaves only StreamingAssets archives.
    shutil.rmtree(folder / gi_runtime.DATA / "Persistent")
    assert len(gi_runtime.archives(folder)) == 3

    empty = tmp_path / "empty"
    (empty / streaming / "00").mkdir(parents=True)
    write_block(empty, streaming + "/loose.blk")
    with pytest.raises(ValueError, match="contains no block archives"):
        gi_runtime.archives(empty)


def test_gi_archives_reject_linked_block_directories(tmp_path):
    folder = tmp_path / "game"
    blocks = folder / gi_runtime.BLOCK_DIRS[0]
    target = tmp_path / "elsewhere"
    write_block(target, "1.blk")
    blocks.mkdir(parents=True)
    try:
        os.symlink(target, blocks / "00", target_is_directory=True)
    except OSError:
        # Junctions need no extra privilege on Windows, unlike symbolic links.
        try:
            import _winapi
            _winapi.CreateJunction(str(target), str(blocks / "00"))
        except (ImportError, OSError):
            pytest.skip("Links are unavailable.")
    with pytest.raises(ValueError, match="linked directories"):
        gi_runtime.archives(folder)


def test_gi_index_lifecycle_identity_and_staleness(tmp_path, monkeypatch):
    folder = tmp_path / "game"
    block = write_block(folder, gi_runtime.BLOCK_DIRS[0] + "/00/1.blk")
    monkeypatch.setattr(paths, "asset_index_dir", lambda: str(tmp_path / "index"))
    assert gi_index.index_path(folder).name.startswith("game-gimi-")
    cancel = threading.Event()
    client = Client(folder)
    assert gi_index.status(folder)["state"] == "missing"
    rows, count = gi_index.build(client, folder, lambda *_: None, cancel)
    assert count == 1 and gi_index.status(folder)["state"] == "ready"
    assert {row["type"] for row in rows} == set(gi_index.TYPES)
    with patch.object(client, "scan_many", side_effect=AssertionError("A ready index must be reused.")):
        assert gi_index.ensure(client, folder, lambda *_: None, cancel) == rows
    # A schema upgrade needs one completed rebuild; the replacement client reuses that result.
    file = gi_index.index_path(folder)
    previous = json.loads(file.read_bytes())
    previous["format"] = 0
    file.write_text(json.dumps(previous))
    assert gi_index.status(folder)["state"] == "stale"
    assert gi_index.ensure(client, folder, lambda *_: None, cancel) == rows
    replacement = Client(folder)
    with patch.object(replacement, "scan_many", side_effect=AssertionError("The rebuilt index must be reused.")):
        assert gi_index.ensure(replacement, folder, lambda *_: None, cancel) == rows
    # Failed builds must leave the previously published cache untouched.
    before = file.read_bytes()
    with pytest.raises(ValueError, match="incomplete"):
        gi_index.build(Client(folder, lambda row: {**row, "offset": -1} if row["type"] == "Mesh" else row), folder, lambda *_: None, cancel)
    assert file.read_bytes() == before
    with pytest.raises(ValueError, match="incomplete or ambiguous"):
        gi_index.build(Client(folder, lambda row: {**row, "type": "SkinnedMeshRenderer"} if row["type"] == "Mesh" else row), folder, lambda *_: None, cancel)
    assert file.read_bytes() == before
    # Changing a block, then adding a Persistent override of it, each makes the cache stale.
    block.write_bytes(b"updated block")
    assert gi_index.status(folder)["state"] == "stale"
    assert gi_index.ensure(client, folder, lambda *_: None, cancel) == rows
    assert gi_index.status(folder)["state"] == "ready"
    write_block(folder, gi_runtime.BLOCK_DIRS[1] + "/00/1.blk", b"override")
    assert gi_index.status(folder)["state"] == "stale"
    gi_index.remove(folder)
    assert gi_index.status(folder)["state"] == "missing"


def test_gi_pointer_resolution_is_case_insensitive_and_rejects_ambiguous_cabs():
    def row(block, cab, kind, path_id):
        return dict(name="", type=kind, block=block, cab=cab, offset=0, file_ordinal=0, path_id=path_id)
    rows = [row("x/1.blk", "CAB-AA", "AnimationClip", 5), row("x/1.blk", "CAB-AA", "AnimatorController", 6),
            row("x/1.blk", "CAB-BB", "AnimationClip", 7), row("x/2.blk", "CAB-BB", "AnimationClip", 7)]
    index = gi_index.by_cab(rows)
    assert gi_index.resolve_pointer(index, "cab-aa", 5) is rows[0]
    assert gi_index.resolve_pointer(index, "cab-aa", 6) is None
    assert gi_index.resolve_pointer(index, "cab-aa", 6, "AnimatorController") is rows[1]
    assert gi_index.resolve_pointer(index, "cab-cc", 5) is None
    assert gi_index.resolve_pointer(index, "cab-bb", 7) is None


def test_gi_client_command_and_folder_validation(tmp_path):
    folder = tmp_path / "game"
    write_block(folder, gi_runtime.BLOCK_DIRS[0] + "/00/1.blk")
    client = gi_runtime.Client("helper.exe", folder, tmp_path / "work", threading.Event())
    assert client.command()[1] == "--gi"
    assert client.command()[2] == str(folder.resolve())
    with pytest.raises(ValueError):
        gi_runtime.Client("helper.exe", tmp_path / "missing", tmp_path / "work", threading.Event())

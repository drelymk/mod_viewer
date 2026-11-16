"""Native distribution size, lazy materialization and archive trust boundaries."""

import hashlib
import importlib.util
import json
from pathlib import Path
import threading
import zipfile

import pytest

from app.assets.game_extract import bundle, runtime, zzz_runtime


def packer():
    script = Path(__file__).resolve().parents[3] / "tools/pack_game_extract.py"
    spec = importlib.util.spec_from_file_location("helper_packer", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def published(tmp_path):
    assets = tmp_path / "source"
    kinds = {
        "game-extract": (runtime.VERSION, runtime.PARSER_COMMIT,
            ["ModViewer.GameExtract.exe", "CUE4Parse.dll", "CUE4Parse-Conversion.dll", "CUE4Parse-Natives.dll",
             "oodle-data-shared.dll", "zlib-ng2.dll", "keys.json", "ModViewer.GameExtract.deps.json", "ModViewer.GameExtract.runtimeconfig.json"]),
        "zzz-extract": (zzz_runtime.VERSION, zzz_runtime.PARSER_COMMIT,
            ["ModViewer.ZzzExtract.exe", "UnityReader.dll", "AnimeStudio.ACL.ZZZV2.dll", "oodle-data-shared.dll",
             "ModViewer.ZzzExtract.deps.json", "ModViewer.ZzzExtract.runtimeconfig.json"]),
    }
    for kind, (version, parser, names) in kinds.items():
        root = assets / kind
        root.mkdir(parents=True)
        files = {}
        for name in [*names, "shared-runtime.dll", "symbols.pdb", "licenses/notice.txt"]:
            path = root / name
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b"shared runtime bytes" * 10000 if name == "shared-runtime.dll" else name.encode())
            files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        (root / "runtime.json").write_text(json.dumps(dict(version=version, parser=parser, files=files)))
    return assets


def test_shared_archive_preserves_runtime_validation_and_lazy_helper_lifecycle(tmp_path, monkeypatch):
    assets = published(tmp_path)
    vendor = tmp_path / "packaged"
    vendor.mkdir()
    result = packer().pack_helpers(assets, vendor / "extractors.zip")
    assert result["packed"] < result["original"]
    with zipfile.ZipFile(vendor / "extractors.zip") as archive:
        catalog = json.loads(archive.read("catalog.json"))["runtimes"]
        shared = catalog["game-extract"]["shared-runtime.dll"]
        assert catalog["zzz-extract"]["shared-runtime.dll"] == shared
        assert archive.namelist().count(shared) == 1
        assert all("symbols.pdb" not in files for files in catalog.values())
    cancel = threading.Event()
    assert not bundle._cache.get((str(vendor.resolve()), "game-extract"))
    root = bundle.runtime_directory("game-extract", cancel, vendor=vendor)
    assert (str(vendor.resolve()), "zzz-extract") not in bundle._cache
    assert runtime.ensure_runtime(cancel, root).name == "ModViewer.GameExtract.exe"
    assert not (root / "symbols.pdb").exists() and (root / "licenses/notice.txt").is_file()
    assert bundle.runtime_directory("game-extract", cancel, vendor=vendor) == root
    second = bundle.runtime_directory("zzz-extract", cancel, vendor=vendor)
    assert zzz_runtime.ensure_runtime(cancel, second).name == "ModViewer.ZzzExtract.exe"
    assert (root / "shared-runtime.dll").read_bytes() == (second / "shared-runtime.dll").read_bytes()
    (root / "shared-runtime.dll").write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        runtime.ensure_runtime(cancel, root)
    assert bundle.runtime_directory("game-extract", cancel, vendor=assets) == assets / "game-extract"
    bundle._cache.pop((str(vendor.resolve()), "game-extract")).cleanup()
    bundle._cache.pop((str(vendor.resolve()), "zzz-extract")).cleanup()


@pytest.mark.parametrize("relative", ["../outside.dll", "/outside.dll", "C:/outside.dll", "a\\outside.dll", "a/./b", "a//b", "a:stream"])
def test_helper_archive_rejects_invented_paths(tmp_path, relative):
    file = tmp_path / "extractors.zip"
    data = b"data"
    digest = hashlib.sha256(data).hexdigest()
    with zipfile.ZipFile(file, "w") as archive:
        archive.writestr("catalog.json", json.dumps(dict(format=1, runtimes={"game-extract": {relative: digest}})))
        archive.writestr(digest, data)
    with pytest.raises(ValueError, match="Unsafe"):
        bundle.runtime_directory("game-extract", threading.Event(), vendor=tmp_path)
    assert not (tmp_path / "outside.dll").exists()
    assert (str(tmp_path.resolve()), "game-extract") not in bundle._cache


@pytest.mark.parametrize("failure", ["checksum", "size", "cancel", "collision"])
def test_invalid_or_cancelled_helper_archive_never_publishes_runtime(tmp_path, monkeypatch, failure):
    digest = hashlib.sha256(b"expected").hexdigest()
    files = {"file.dll": digest}
    if failure == "collision":
        files["FILE.dll"] = digest
    with zipfile.ZipFile(tmp_path / "extractors.zip", "w") as archive:
        archive.writestr("catalog.json", json.dumps(dict(format=1, runtimes={"game-extract": files})))
        archive.writestr(digest, b"changed" if failure == "checksum" else b"expected")
    cancel = threading.Event()
    if failure == "size":
        monkeypatch.setattr(bundle, "MAX_FILE", 1)
    if failure == "cancel":
        original = runtime.check_cancelled
        calls = 0
        def checkpoint(event):
            nonlocal calls
            calls += 1
            if calls == 4:
                event.set()
            original(event)
        monkeypatch.setattr(runtime, "check_cancelled", checkpoint)
    with pytest.raises(ValueError):
        bundle.runtime_directory("game-extract", cancel, vendor=tmp_path)
    assert (str(tmp_path.resolve()), "game-extract") not in bundle._cache


def test_changed_publish_files_fail_packaging(tmp_path):
    assets = published(tmp_path)
    (assets / "game-extract/CUE4Parse.dll").write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        packer().pack_helpers(assets, tmp_path / "extractors.zip")

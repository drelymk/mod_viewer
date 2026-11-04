"""Headless capture extraction owns only configured output; inputs stay in memory."""

import copy
import json
from pathlib import Path
import struct
import os
import random
import mmap
import subprocess
import sys
import uuid

import pytest
from PIL import Image

import extract_asset as headless
from app.assets import index
from app.assets.extractor.discovery import analyze
from app.assets.extractor.export import Selection, export, extract
from app.assets.extractor.memory_capture import MemoryCapture
from app.settings import paths
from tests.support.extraction_data import make_capture
from tests.support.dds_data import uncompressed_dds


PREFIX = "Local\\ModManager.Capture.7." + "1" * 32 + "."


class Seed:
    def __init__(self, store, path=""):
        self.store, self.path = store, path

    def __truediv__(self, name):
        return Seed(self.store, self.path + "/" + name)

    def mkdir(self):
        pass

    def write_bytes(self, value):
        self.store[self.path.rsplit("/", 1)[-1]] = value

    def write_text(self, value, **_kwargs):
        self.write_bytes(value.encode())


class Block:
    def __init__(self, data, name, capacity, offset):
        self.data, self.name, self.capacity, self.offset = data, name, capacity, offset

    def read(self, offset, length):
        return self.data[offset : offset + length]

    def close(self):
        pass


def capture_data(data=None):
    if data is None:
        data = {}
        make_capture(Seed(data))
        # A minimal PNG exercises memory-backed texture copying during export.
        png = (
            b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR"
            + struct.pack(">II", 16, 16)
            + b"\x08\x02\0\0\0"
            + b"\0" * 16
        )
        data["000002-ps-t0=66666666.png"] = png
    manifest = {
        "version": 1,
        "prefix": PREFIX,
        "capture": "frameanalysis-sample",
        "names": {},
        "files": {},
    }
    blocks = {}
    for identity, (name, payload) in enumerate(data.items(), 1):
        mapping = PREFIX + str(identity) + ".0"
        blocks[mapping] = payload
        capacity = max(65536, (len(payload) + 65535) // 65536 * 65536)
        manifest["names"]["frameanalysis-sample/" + name] = identity
        manifest["files"][str(identity)] = {
            "size": len(payload),
            "mappings": [{"name": mapping, "capacity": capacity, "offset": 0}],
        }

    def factory(name, capacity, offset):
        return Block(blocks[name], name, capacity, offset)

    return manifest, factory


def test_headless_memory_capture_add_update_and_rollback(tmp_path, monkeypatch):
    root = tmp_path / "assets"
    root.mkdir()
    settings = tmp_path / "config.json"
    settings.write_text(json.dumps({"version": 1, "modFolders": []}), encoding="utf-8")
    monkeypatch.setattr(paths, "config_path", lambda: str(settings))
    monkeypatch.setattr(paths, "asset_index_dir", lambda: str(tmp_path / "index"))
    manifest, factory = capture_data()
    monkeypatch.setattr(
        headless,
        "MemoryCapture",
        lambda value: MemoryCapture(value, mapping_factory=factory),
    )
    request = {
        "version": 1,
        "folder": str(root),
        "asset_type": "ZZMI",
        "name": "Sample",
        "capture": manifest,
    }
    first = headless.install_capture(request)
    destination = Path(first["path"])
    assert (destination / "hash.json").is_file()
    assert list(destination.glob("*.png"))
    assert first["extraction"]["components"] == 1
    second = headless.install_capture(request)
    assert second["path"] == first["path"]
    before = {
        str(p.relative_to(destination)): p.read_bytes()
        for p in destination.rglob("*")
        if p.is_file()
    }

    def fail(*_args, **_kwargs):
        raise OSError("Synthetic index commit failure")

    monkeypatch.setattr(index, "save_index", fail)
    with pytest.raises(OSError, match="Synthetic index"):
        headless.install_capture(request)
    assert {
        str(p.relative_to(destination)): p.read_bytes()
        for p in destination.rglob("*")
        if p.is_file()
    } == before
    assert not list(root.glob(".asset-extract-*"))
    assert not list(tmp_path.glob("FrameAnalysis*"))


@pytest.mark.parametrize(
    "case", ["traversal", "foreign_section", "missing_extent", "another_frame"]
)
def test_memory_capture_rejects_escaped_or_unbacked_input(case):
    manifest, factory = capture_data()
    manifest = copy.deepcopy(manifest)
    if case == "traversal":
        manifest["names"]["frameanalysis-sample/../escape"] = 1
    elif case == "foreign_section":
        manifest["files"]["1"]["mappings"][0]["name"] = "OtherSection"
    elif case == "missing_extent":
        manifest["files"]["1"]["size"] = 65537
    else:
        manifest["names"]["frameanalysis-other/file"] = 1
    with pytest.raises(ValueError):
        MemoryCapture(manifest, mapping_factory=factory)


def test_existing_unindexed_folder_is_preserved(tmp_path, monkeypatch):
    root = tmp_path / "assets"
    existing = root / "Sample"
    existing.mkdir(parents=True)
    sentinel = existing / "private.txt"
    sentinel.write_bytes(b"preserve")
    settings = tmp_path / "config.json"
    settings.write_text(json.dumps({"version": 1, "modFolders": []}), encoding="utf-8")
    monkeypatch.setattr(paths, "config_path", lambda: str(settings))
    monkeypatch.setattr(paths, "asset_index_dir", lambda: str(tmp_path / "index"))
    manifest, factory = capture_data()
    monkeypatch.setattr(
        headless,
        "MemoryCapture",
        lambda value: MemoryCapture(value, mapping_factory=factory),
    )
    with pytest.raises(ValueError, match="not an indexed Asset"):
        headless.install_capture(
            {
                "version": 1,
                "folder": str(root),
                "asset_type": "ZZMI",
                "name": "Sample",
                "capture": manifest,
            }
        )
    assert sentinel.read_bytes() == b"preserve"
    assert "assetFolders" in json.loads(settings.read_text())


@pytest.mark.skipif(os.name != "nt", reason="Windows shared capture sections required")
def test_headless_process_extracts_real_shared_sections_without_raw_dump_files(
    tmp_path,
):
    manifest, factory = capture_data()
    new_prefix = "Local\\ModManager.Capture.7." + uuid.uuid4().hex + "."
    owners = []
    try:
        for file in manifest["files"].values():
            for block in file["mappings"]:
                data = factory(block["name"], block["capacity"], block["offset"]).data
                block["name"] = block["name"].replace(PREFIX, new_prefix)
                owner = mmap.mmap(-1, block["capacity"], tagname=block["name"])
                owner.write(data)
                owners.append(owner)
        manifest["prefix"] = new_prefix
        output = tmp_path / "assets"
        output.mkdir()
        settings = tmp_path / "viewer.json"
        request = {
            "version": 1,
            "folder": str(output),
            "asset_type": "ZZMI",
            "name": "SharedModel",
            "capture": manifest,
        }
        script = Path(__file__).resolve().parents[3] / "extract_asset.py"
        process = subprocess.run(
            [sys.executable, "-B", str(script), "--config", str(settings)],
            input=json.dumps(request),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert process.returncode == 0, process.stderr + process.stdout
        result = json.loads(process.stdout)
        assert result["ok"]
        assert (output / "SharedModel" / "hash.json").is_file()
        assert list((output / "SharedModel").glob("*.png"))
        assert not list(tmp_path.glob("FrameAnalysis*"))
    finally:
        for owner in owners:
            owner.close()


def test_memory_and_disk_capture_preserve_pixel_roles_geometry_and_asset_metadata(tmp_path):
    dump = make_capture(tmp_path)
    randomizer = random.Random(17)
    palette = [tuple(randomizer.randrange(256) for _ in range(3)) + (255,)
               for _ in range(64)]
    pixels = bytes(value for y in range(128) for x in range(128)
                   for value in palette[(y // 16) * 8 + x // 16])
    (dump / "000002-ps-t0=66666666.dds").write_bytes(
        uncompressed_dds(pixels, width=128, height=128, dxgi_format=29))
    Image.new("RGB", (16, 16)).save(dump / "000002-o0=12345678.png")
    log = dump / "log.txt"
    log.write_text(log.read_text(encoding="utf-8").replace(
        "000002 DrawIndexed", "000002 PSSetShaderResources(StartSlot:0, NumViews:1)\n"
        "       0: resource=0x60 hash=66666666\n000002 DrawIndexed"), encoding="utf-8")
    manifest, factory = capture_data({p.name: p.read_bytes() for p in dump.iterdir()})
    disk = analyze(dump)
    texture = disk.components[0].parts[0].textures[0]
    assert texture["role"] == "Diffuse"
    assert texture["role_confidence"] == "inferred_pixels_high"
    with MemoryCapture(manifest, mapping_factory=factory) as source:
        memory = analyze(source)
        assert memory.components == disk.components
        disk_report = export(extract(disk, Selection("Sample")), tmp_path / "disk-output")
        memory_report = export(extract(memory, Selection("Sample")), tmp_path / "memory-output")

    def asset_files(report):
        root = Path(report.output)
        return {p.relative_to(root).as_posix(): p.read_bytes()
                for p in root.rglob("*") if p.is_file()}

    assert asset_files(memory_report) == asset_files(disk_report)

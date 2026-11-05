"""Headless capture extraction owns only configured output; inputs stay in memory."""

import copy
import json
from pathlib import Path
import os
import random
import mmap
import subprocess
import sys
import uuid

import pytest
from PIL import Image

import extract_asset as headless
from app.assets import index, loader
from app.assets.extractor.discovery import analyze
from app.assets.extractor.export import Selection, export, extract
from app.assets.extractor.memory_capture import MemoryCapture
from app.settings import paths
from tests.support.extraction_data import make_capture
from tests.support.dds_data import dx10_dds, uncompressed_dds
from tests.support.memory_capture_data import PREFIX, capture_data


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
    assert (destination / "11111111" / "hash.json").is_file()
    assert list((destination / "11111111").glob("*.png"))
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
    "case", ["traversal", "foreign_section", "missing_extent", "another_frame", "case_collision"]
)
def test_memory_capture_rejects_escaped_or_unbacked_input(case):
    manifest, factory = capture_data()
    manifest = copy.deepcopy(manifest)
    if case == "traversal":
        manifest["names"]["frameanalysis-sample/../escape"] = 1
    elif case == "foreign_section":
        manifest["files"]["1"]["mappings"][0]["name"] = "OtherSection"
    elif case == "case_collision":
        name, identity = next(iter(manifest["names"].items()))
        root, relative = name.split("/", 1)
        manifest["names"][root + "/" + relative.upper()] = identity
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
        assert (output / "SharedModel" / "11111111" / "hash.json").is_file()
        assert list((output / "SharedModel" / "11111111").glob("*.png"))
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


@pytest.mark.parametrize("keep_alias", [True, False])
@pytest.mark.parametrize("hashless", [False, True])
def test_lowercased_capture_names_preserve_zzz_material_roles_and_dedup_lookup(tmp_path, keep_alias, hashless):
    dump = make_capture(tmp_path)
    diffuse = "000002-ps-t3.dds" if hashless else "000002-ps-t3=66666666.dds"
    normal = "000002-ps-t4.jpg" if hashless else "000002-ps-t4=!S!=77777777.jpg"
    (dump / diffuse).write_bytes(dx10_dds(bytes(256 * 256), dxgi_format=99,
                                         width=256, height=256))
    Image.new("RGB", (128, 128), (128, 128, 255)).save(dump / normal)
    Image.new("RGB", (16, 16)).save(dump / "000002-o0=88888888.png")
    deduped = dump / "deduped"
    deduped.mkdir()
    target = "12345678-R8G8B8A8_UNORM.jpg"
    (deduped / "87654321-BC7_UNORM_SRGB.dds").write_bytes((dump / diffuse).read_bytes())
    (deduped / target).write_bytes((dump / normal).read_bytes())
    log = dump / "log.txt"
    log.write_text(log.read_text(encoding="utf-8").replace(
        "000002 DrawIndexed", "000002 PSSetShader(pPixelShader:0x9) hash=cccccccccccccccc\n"
        "000002 PSSetShaderResources(StartSlot:3, NumViews:2)\n"
        "       3: resource=0x60 hash=66666666\n"
        "       4: resource=0x70 hash=77777777\n"
        f"000002 3DMigoto Dumping Texture2D C:\\Runtime\\{diffuse} -> C:\\Runtime\\deduped\\87654321-BC7_UNORM_SRGB.dds\n"
        f"000002 3DMigoto Dumping Texture2D C:\\Runtime\\{normal} -> C:\\Runtime\\deduped\\{target}\n"
        "000002 DrawIndexed"), encoding="utf-8")
    disk = analyze(dump)
    expected = [(t["role"], t["format"]) for t in disk.components[0].parts[0].textures]
    assert expected == [("Diffuse", "BC7_UNORM_SRGB"), ("NormalMap", "R8G8B8A8_UNORM")]
    expected_hashes = ["87654321", "12345678"] if hashless else ["66666666", "77777777"]
    assert [t["hash"] for t in disk.components[0].parts[0].textures] == expected_hashes
    assert all((t.get("hash_source") == "logged_dedup") == hashless
               for t in disk.components[0].parts[0].textures)
    data = {p.relative_to(dump).as_posix().lower(): p.read_bytes()
            for p in dump.rglob("*") if p.is_file()}
    if not keep_alias:
        data.pop(normal.lower())
    manifest, factory = capture_data(data)
    with MemoryCapture(manifest, mapping_factory=factory) as source:
        memory = analyze(source)
        actual = [(t["role"], t["format"]) for t in memory.components[0].parts[0].textures]
        assert actual == expected
        assert [t["hash"] for t in memory.components[0].parts[0].textures] == expected_hashes
        assert (source.root / "deduped" / target).read_bytes() == (dump / normal).read_bytes()
        reports = [export(extract(a, Selection("Sample")), tmp_path / kind)
                   for a, kind in [(disk, "disk-output"), (memory, "memory-output")]]
    assets = [{p.relative_to(Path(r.output)).as_posix(): p.read_bytes()
               for p in Path(r.output).rglob("*") if p.is_file()} for r in reports]
    assert assets[0] == assets[1]
    for report in reports:
        root = str(Path(report.output).parent)
        loaded = loader.load_asset_parts("ZZMI", root, index.build_index("ZZMI", root)["assets"][0])
        part, = loaded.parts
        assert not loaded.warnings
        assert Path(part.textures["diffuse"].path).read_bytes() == (dump / diffuse).read_bytes()
        assert Path(part.textures["normal_map"].path).read_bytes() == (dump / normal).read_bytes()


@pytest.mark.parametrize("limit", ["capacity", "sections"])
def test_memory_manifest_budget_counts_capacity_and_sections_before_mapping(limit):
    from app.assets.extractor.budget import CaptureBudget, ResourceLimit
    manifest, factory = capture_data({"log.txt": b"", "buffer.buf": b""})
    budget = CaptureBudget(max_source_bytes=65536 if limit == "capacity" else 16 * 1024**3,
                           max_mappings=1 if limit == "sections" else 16384)
    opened = []
    def mapping(name, capacity, offset):
        opened.append(name)
        return factory(name, capacity, offset)
    with pytest.raises(ResourceLimit, match="mapping budget"):
        MemoryCapture(manifest, mapping_factory=mapping, budget=budget)
    assert opened == []


def test_memory_mapping_failure_closes_previously_opened_sections():
    manifest, factory = capture_data({"log.txt": b"", "buffer.buf": b""})
    closed = []
    def mapping(name, capacity, offset):
        if closed:
            raise OSError("Synthetic mapping failure")
        block = factory(name, capacity, offset)
        closed.append(False)
        block.close = lambda: closed.__setitem__(0, True)
        return block
    with pytest.raises(OSError, match="Synthetic mapping failure"):
        MemoryCapture(manifest, mapping_factory=mapping)
    assert closed == [True]

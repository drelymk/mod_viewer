import hashlib
from io import BytesIO
import json
from pathlib import Path
import shutil
import struct
import threading
import time
from types import SimpleNamespace
import zipfile

import pytest

from app.assets import index
from app.assets.game_extract import pipeline, runtime
from app.assets.game_extract.native import capture, read_mesh, match_components, make_rig, humanoid_controls
from app.assets.rig import validate_rig
from app.bridge.api import ModViewerAPI
from app.bridge.rig_extraction import RigExtraction, install_package, sidecar_revision
from app.settings import paths
from .test_loader import _fmt


CHARACTER = "Client/Content/Aki/Character/Role/BodyType01/Character01"
MESH = CHARACTER + "/Skin01/Model/Model01"
SKELETON = CHARACTER + "/Skin01/Model/Skeleton01"
BASE = CHARACTER + "/CommonAnim/Clip01"
LAYER = CHARACTER + "/Skin01/RibbonAnim/Rib_Clip01"


def string(value):
    data = value.encode()
    return struct.pack("<i", len(data)) + data


def attributes(values):
    return struct.pack("<i", len(values)) + b"".join(string(key) + struct.pack("<i", len(data)) + data for key, data in values.items())


def psa(bones, rows):
    records = bytearray()
    for bone in bones:
        row = bytearray(120)
        row[:len(bone["name"])] = bone["name"].encode()
        struct.pack_into("<i4f3f", row, 72, bone["parent"], *bone["rotation"], *bone["translation"])
        records.extend(row)
    info = bytearray(168)
    struct.pack_into("<f", info, 152, 2)
    struct.pack_into("<i", info, 164, 2)
    keys = bytearray()
    for ordinal, row in enumerate(rows):
        x, y, z, qx, qy, qz, qw = row
        keys.extend(struct.pack("<8f", x, -y, z, qx, -qy, qz, -qw if ordinal % 2 == 0 else qw, 1))
    def chunk(name, size, count, data):
        return struct.pack("<20siii", name, 0, size, count) + data
    return chunk(b"BONENAMES", 120, 2, records) + chunk(b"ANIMINFO", 168, 1, info) + chunk(b"ANIMKEYS", 32, 4, keys)


def fixture(tmp_path, scale=(1, 1, 1)):
    root = tmp_path / "assets"
    asset = root / "Character01"
    geometry = asset / "11111111"
    geometry.mkdir(parents=True)
    bones = [dict(name="Root", parent=-1, translation=[0, 0, 0], rotation=[0, 0, 0, 1], scale=list(scale)),
             dict(name="Secondary01", parent=0, translation=[1, 2, 3], rotation=[0, 0, 0, 1], scale=[1, 1, 1])]
    positions = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
    _fmt(geometry / "Component 0.fmt", 28, "R16_UINT", [
        ("POSITION", 0, "R32G32B32_FLOAT", 0), ("TEXCOORD", 0, "R32G32_FLOAT", 12),
        ("BLENDINDICES", 0, "R8G8B8A8_UINT", 20), ("BLENDWEIGHTS", 0, "R8G8B8A8_UNORM", 24)])
    (geometry / "Component 0.vb").write_bytes(b"".join(struct.pack("<3f2f8B", *p, 0, 0, 0, 1, 0, 0, 128, 127, 0, 0) for p in positions))
    (geometry / "Component 0.ib").write_bytes(struct.pack("<3H", 0, 1, 2))
    (geometry / "Metadata.json").write_text(json.dumps(dict(vb0_hash="11111111", components=[dict(index_offset=0, index_count=3, vertex_count=3)])))
    record = index.build_index("WWMI", str(root))["assets"][0]
    bone_bytes = struct.pack("<i", 2) + b"".join(string(b["name"]) + struct.pack("<i3f4f3f", b["parent"], *b["translation"], *b["rotation"], *b["scale"]) for b in bones)
    level = attributes(dict(
        VERTICES=struct.pack("<i9f", 3, *(v for p in positions for v in p)),
        INDICES=struct.pack("<i3i", 3, 0, 1, 2),
        TEXCOORDS=struct.pack("<i", 1) + string("UV0") + struct.pack("<i6f", 3, *([0] * 6)),
        MATERIALS=struct.pack("<i", 1) + string("Material01") + string("Material01") + struct.pack("<ii", 0, 1),
        WEIGHTS=struct.pack("<i", 6) + b"".join(struct.pack("<Hif", bone, vertex, weight / 255) for vertex in range(3) for bone, weight in [(0, 128), (1, 127)])))
    native = b"UEFORMAT" + string("UEMODEL") + b"\x0a" + string("Model01") + string(MESH) + b"\x00" + attributes(dict(
        LODS=struct.pack("<i", 1) + string("LOD0") + level, SKELETON=attributes(dict(BONES=bone_bytes))))
    base_rows = [(0, 0, 0, 0, 0, 0, 1), (1, 2, 3, 0, 0, 0, 1), (2, 0, 0, 0, 0, 0, 1), (1, 2, 3, 0, 0, 0, 1)]
    extra_rows = [base_rows[0], base_rows[1], base_rows[2], (1, 2, 3, 0, 0, 1, 0)]
    properties = dict(Skeleton=dict(ObjectPath=SKELETON + ".0"), NumFrames=2, SequenceLength=1)
    sections = [dict(NumVertices=3, NumTriangles=1)]
    metadata = {MESH: [dict(Type="SkeletalMesh", LODModels=[dict(Sections=sections)])],
                BASE: [dict(Type="AnimSequence", Name="Clip01", Properties={**properties, "TrackToSkeletonMapTable": [dict(BoneTreeIndex=0)]})],
                LAYER: [dict(Type="AnimSequence", Name="Rib_Clip01", Properties={**properties,
                    "TrackToSkeletonMapTable": [dict(BoneTreeIndex=1)], "AdditiveAnimType": "AAT_LocalSpaceBase", "RefPoseType": "ABPT_RefPose"})]}

    class Client:
        def __init__(self):
            self.work = tmp_path / "work"
            self.work.mkdir()

        def search(self, query, kind, under):
            return [MESH] if kind == "SkeletalMesh" else [BASE, LAYER]

        def export(self, kind, package, destination, format=None):
            destination.mkdir(parents=True, exist_ok=True)
            file = destination / ("Model01.uemodel" if kind == "mesh" else "Model01.json")
            file.write_bytes(native if kind == "mesh" else json.dumps([dict(Type="SkeletalMesh", Properties=dict(Skeleton=dict(ObjectPath=SKELETON + ".0")))]).encode())
            return [file]

        def batch(self, kind, packages, destination, format=None):
            destination.mkdir(parents=True, exist_ok=True)
            result = {}
            for package in packages:
                file = destination / (package.rsplit("/", 1)[-1] + (".json" if kind == "json" else ".psa"))
                file.write_bytes(json.dumps(metadata.get(package, metadata[MESH])).encode() if kind == "json" else psa(bones, base_rows if package == BASE else extra_rows))
                result[package] = dict(state="completed", rootFiles=[str(file)], failures=[])
            return result
    return root, asset, record, Client(), base_rows


def test_mesh_name_is_tried_first_then_captured_geometry(tmp_path):
    root, _, record, client, _ = fixture(tmp_path)
    queries = []
    original = client.search
    client.search = lambda query, kind, under: queries.append((query, under)) or ([] if query else original(query, kind, under))
    result, _ = pipeline.build(client, str(root), record, tmp_path / "output", "Unrelated Name", lambda *_: None, threading.Event())
    assert result["bones"] == 2 and result["unmatched_components"] == 0
    assert queries[0] == ("Unrelated Name", pipeline.ROLE_ROOT)
    assert {under.rsplit("/", 1)[-1] for query, under in queries if not query} >= {"FemaleMS", "MaleMS"}
    queries.clear()
    client.search = lambda query, kind, under: queries.append(query) or original(query, kind, under)
    pipeline.build(client, str(root), record, tmp_path / "named", "Character01", lambda *_: None, threading.Event())
    assert queries == ["Character01", ""]  # mesh by name, then the animation search


def test_prop_geometry_in_another_mesh_does_not_block_the_body(tmp_path):
    root, _, record, client, _ = fixture(tmp_path)
    prop = root / "Character01/22222222"
    prop.mkdir()
    for name in ("Component 0.fmt", "Component 0.vb", "Component 0.ib"):
        shutil.copy(root / "Character01/11111111" / name, prop / name)
    file = prop / "Component 0.vb"
    data = bytearray(file.read_bytes())
    file.write_bytes(bytes(data) + bytes(28))
    (prop / "Component 0.ib").write_bytes(struct.pack("<6H", 0, 1, 2, 3, 3, 3))
    (prop / "Metadata.json").write_text(json.dumps(dict(vb0_hash="22222222", components=[dict(index_offset=0, index_count=6, vertex_count=4)])))
    record = index.build_index("WWMI", str(root))["assets"][0]
    result, _ = pipeline.build(client, str(root), record, tmp_path / "output", "", lambda *_: None, threading.Event())
    assert result["proven_components"] == 1 and result["unmatched_components"] == 1


def test_companion_prop_meshes_join_the_rig_only_with_an_exact_sub_skeleton(tmp_path, monkeypatch):
    def bone(name, parent, x=0):
        return dict(name=name, parent=parent, translation=[x, 0, 0], rotation=[0, 0, 0, 1], scale=[1, 1, 1])

    primary = dict(bones=[bone("Root", -1), bone("A", 0, 1), bone("B", 1, 2)])
    prop = dict(bones=[bone("Root", -1), bone("B", 0, 2)])
    assert pipeline.sub_skeleton_map(primary, dict(bones=[bone("Root", -1), bone("A", 0, 1)])) == [0, 1]
    assert pipeline.sub_skeleton_map(primary, prop) is None  # parent differs
    assert pipeline.sub_skeleton_map(primary, dict(bones=[bone("Root", -1), bone("A", 0, 5)])) is None
    assert pipeline.sub_skeleton_map(primary, dict(bones=[bone("Root", -1), bone("Z", 0)])) is None
    mesh = dict(bones=[bone("Root", -1), bone("B", 1, 2), bone("A", 0, 1)])
    mesh["bones"][1]["parent"] = 2
    mesh["bones"][2]["parent"] = 0
    assert pipeline.sub_skeleton_map(primary, mesh) == [0, 2, 1]
    work = tmp_path / "work"
    work.mkdir()
    captured = [dict(index=0), dict(index=1)]

    class Client:
        def __init__(self):
            self.work = work

        def search(self, query, kind, under):
            assert query == "Sample"
            return ["Client/Content/Aki/Character/Prop/Item/Model/Item", "Client/Content/Aki/Character/Role/FemaleMS/Sample/Own"]

        def export(self, kind, package, destination, format=None):
            return [package]

    monkeypatch.setattr(pipeline, "read_mesh", lambda package: mesh)
    monkeypatch.setattr(pipeline, "match_components",
                        lambda parts, _mesh: [dict(bone_map=[0, 1, 2])] if parts[0]["index"] == 1 else (_ for _ in ()).throw(ValueError()))
    found = pipeline.adopt_companions(Client(), captured, primary, "Client/Content/Aki/Character/Role/FemaleMS/Sample/Model/Body",
                                      {0}, "Client/Content/Aki/Character/Role/FemaleMS/Sample", threading.Event())
    assert list(found) == [1] and found[1][1]["bone_map"] == [0, 2, 1]


def test_native_proof_and_secondary_pipeline_lifecycle(tmp_path):
    root, asset, record, client, rows = fixture(tmp_path)
    phases = []
    result, fingerprints = pipeline.build(client, str(root), record, tmp_path / "output", "Character01",
        lambda phase, *_: phases.append(phase), threading.Event())
    assert result["bones"] == 2 and result["imported"] == result["secondary"] == result["clips"] == 1
    assert result["skipped"] == 0 and fingerprints
    assert phases[:3] == ["search", "rig", "animations"]
    data = (tmp_path / "output/Animation/Clip01.bin").read_bytes()
    assert list(struct.iter_unpack("<7f", data)) == [rows[0], rows[1], rows[2], (1, 2, 3, 0, 0, 1, 0)]
    rig = validate_rig(json.loads((tmp_path / "output/Rig.json").read_text()))
    captured, _ = capture(str(root), record)
    mesh = read_mesh(tmp_path / "work/meshes/Model01.uemodel")
    assert make_rig(mesh, match_components(captured, mesh), rig)["id"] == rig["id"]
    shutil.copytree(tmp_path / "output", asset, dirs_exist_ok=True)
    manifest_file = asset / "Animation/manifest.json"
    manifest = json.loads(manifest_file.read_text())
    manifest["clips"][0].update(id="custom01", name="Custom pose")
    manifest_file.write_text(json.dumps(manifest))
    clip_file = asset / "Animation/Clip01.json"
    custom = json.loads(clip_file.read_text())
    custom.pop("source")
    custom_binary = bytearray(data)
    struct.pack_into("<f", custom_binary, 0, .25)
    custom["sha256"] = hashlib.sha256(custom_binary).hexdigest()
    clip_file.write_text(json.dumps(custom))
    (asset / "Animation/Clip01.bin").write_bytes(custom_binary)
    (asset / "Animation/source.psa").write_bytes(b"user export")
    result, _ = pipeline.build(client, str(root), record, tmp_path / "updated", "Character01", lambda *_: None, threading.Event())
    assert result["clips"] == 2 and result["imported"] == 1
    assert (tmp_path / "updated/Animation/Clip01.bin").read_bytes() == custom_binary
    assert (tmp_path / "updated/Animation/source.psa").read_bytes() == b"user export"
    library = json.loads((tmp_path / "updated/Animation/manifest.json").read_text())
    assert {entry["id"] for entry in library["clips"]} >= {"custom01"}
    assert any(entry["id"].startswith("Clip01_") for entry in library["clips"])
    file = asset / "11111111/Component 0.vb"
    modified = bytearray(file.read_bytes())
    struct.pack_into("<f", modified, 0, .01)
    file.write_bytes(modified)
    with pytest.raises(ValueError, match="unique matching"):
        pipeline.build(client, str(root), record, tmp_path / "failed", "Character01", lambda *_: None, threading.Event())
    assert not (tmp_path / "failed").exists()


def test_scaled_reference_extraction_identity_errors_and_ambiguity(tmp_path, monkeypatch):
    root, _asset, record, client, _rows = fixture(tmp_path, (.75, .75, .75))
    output = tmp_path / "output"
    result, _ = pipeline.build(client, str(root), record, output, "Character01", lambda *_: None, threading.Event())
    assert result["bones"] == 2 and result["clips"] == 1
    rig = validate_rig(json.loads((output / "Rig.json").read_text()))
    assert rig["bones"][0]["scale"] == [.75] * 3
    mesh = read_mesh(client.work / "meshes/Model01.uemodel")
    captured, _ = capture(str(root), record)
    palettes = match_components(captured, mesh)
    assert make_rig(mesh, palettes, rig)["id"] == rig["id"]
    mesh["bones"][0]["scale"] = [.5] * 3
    changed = make_rig(mesh, palettes, rig)
    assert changed["id"] != rig["id"] and changed["bones"][0]["scale"] == [.5] * 3
    mesh["bones"][1]["scale"] = [-1, -1, -1]
    assert make_rig(mesh, palettes, rig)["bones"][1]["scale"] == [-1, -1, -1]
    mesh["bones"][1]["scale"] = [1, 1, 1]
    mesh["bones"][0]["scale"] = [.75, .75, 1]
    monkeypatch.setattr(pipeline, "read_mesh", lambda _: mesh)
    with pytest.raises(ValueError, match="Reference bone scales must be uniform"):
        pipeline.build(client, str(root), record, tmp_path / "invalid", "Character01", lambda *_: None, threading.Event())
    assert not (tmp_path / "invalid").exists()
    monkeypatch.setattr(client, "search", lambda *_: [MESH, MESH + "Other"])
    with pytest.raises(ValueError, match="unique matching game mesh"):
        pipeline.build(client, str(root), record, tmp_path / "ambiguous", "Character01", lambda *_: None, threading.Event())
    assert not (tmp_path / "ambiguous").exists()


def test_biped_humanoid_controls_require_exact_names_and_hierarchy():
    tree = [("Root", -1), ("Bip001", 0), ("Bip001Pelvis", 1), ("Bip001Spine2", 2), ("Bip001Neck", 3), ("Bip001Head", 4)]
    for side in "LR":
        base = len(tree)
        tree += [(f"Bip001{side}UpperArm", 3), (f"Bip001{side}Forearm", base), (f"Bip001{side}Hand", base + 1),
                 (f"Bip001{side}Thigh", 2), (f"Bip001{side}Calf", base + 3), (f"Bip001{side}Foot", base + 4)]
    bones = [dict(name=name, parent=parent, translation=[0, 0, 0], rotation=[0, 0, 0, 1], scale=[1, 1, 1])
             for name, parent in tree]
    controls = humanoid_controls(dict(bones=bones))
    assert len(controls) == 16 and controls["pelvis"] == 2 and controls["rightFoot"] == len(tree) - 1
    assert humanoid_controls(dict(bones=bones[:-1])) is None
    bones[6]["parent"] = 2
    assert humanoid_controls(dict(bones=bones)) is None


def test_library_survives_semantic_rig_fingerprint_change(tmp_path):
    root, _asset, record, client, _rows = fixture(tmp_path, (1, 1, 1))
    output = tmp_path / "output"
    pipeline.build(client, str(root), record, output, "Character01", lambda *_: None, threading.Event())
    previous = validate_rig(json.loads((output / "Rig.json").read_text()))
    changed = {**previous, "id": "f" * 64}
    kept = tmp_path / "kept"
    entries = pipeline.preserve_library(output, kept, changed, previous)
    assert entries and pipeline.preserve_library(output, tmp_path / "other", changed) == {}
    for entry in entries.values():
        assert json.loads((kept / entry["file"]).read_text())["skeleton_id"] == changed["id"]


def test_install_transaction_rolls_back_every_sidecar(tmp_path, monkeypatch):
    asset = tmp_path / "Asset01"
    asset.mkdir()
    (asset / "Rig.json").write_bytes(b"old rig")
    (asset / "Animation").mkdir()
    (asset / "Animation/manifest.json").write_bytes(b"old animation")
    (asset / "RigExtraction.json").write_bytes(b"old report")
    before = sidecar_revision(asset)
    def stage(name):
        directory = tmp_path / name
        output = directory / "output"
        output.mkdir(parents=True)
        (output / "Rig.json").write_bytes(b"new rig")
        (output / "Animation").mkdir()
        (output / "Animation/manifest.json").write_bytes(b"new animation")
        (output / "RigExtraction.json").write_bytes(b"new report")
        return directory, output
    directory, output = stage("stage01")
    def permissions(path):
        if path.name == "Animation":
            raise OSError("synthetic permission failure")
    monkeypatch.setattr("app.bridge.rig_extraction.inherit_directory_permissions", permissions)
    with pytest.raises(OSError):
        install_package(asset, output, directory)
    assert sidecar_revision(asset) == before
    directory, output = stage("stage02")
    monkeypatch.setattr("app.bridge.rig_extraction.inherit_directory_permissions", lambda _: None)
    install_package(asset, output, directory)
    assert (asset / "Rig.json").read_bytes() == b"new rig"
    assert (directory / "previous/Animation/manifest.json").read_bytes() == b"old animation"


def test_job_picker_authorization_cancellation_revision_and_save_lifecycle(tmp_path, monkeypatch):
    root, asset, record, _, _ = fixture(tmp_path)
    monkeypatch.setattr(paths, "config_path", lambda: str(tmp_path / "config.json"))
    game = tmp_path / "game/Client/Content/Paks"
    game.mkdir(parents=True)
    (game / "data.pak").write_bytes(b"fixture archive")
    selected = str(asset)
    def selection(path):
        if path != selected:
            raise PermissionError("unregistered Asset")
        return selected, dict(type="WWMI", path=str(root)), {}, record
    service = RigExtraction(selection)
    assert "error" in service.start(selected, "invented")
    picked = service.pick_game(SimpleNamespace(create_file_dialog=lambda *_: [str(game.parents[2])]))
    assert "error" in service.start(str(tmp_path / "outside"), picked["token"])
    assert "error" in service.start(selected, picked["token"], "--config=outside.json")
    entered, resume = threading.Event(), threading.Event()
    def tool(cancel):
        entered.set()
        assert resume.wait(5)
        runtime.check_cancelled(cancel)
        return Path("tool.exe")
    monkeypatch.setattr("app.bridge.rig_extraction.ensure_runtime", tool)
    monkeypatch.setattr("app.bridge.rig_extraction.Client", lambda *_: None)
    def build(_client, root, record, output, *_):
        output.mkdir()
        (output / "Rig.json").write_bytes(b"rig")
        (output / "Animation").mkdir()
        (output / "Animation/manifest.json").write_bytes(b"animation")
        (output / "RigExtraction.json").write_bytes(b"report")
        return dict(bones=2, clips=1, imported=1, secondary=1, skipped=0), capture(root, record)[1]
    monkeypatch.setattr("app.bridge.rig_extraction.pipeline.build", build)
    monkeypatch.setattr("app.bridge.rig_extraction.inherit_directory_permissions", lambda _: None)
    def finish(job):
        deadline = time.monotonic() + 5
        while service.status(job)["status"] == "running" or service._active:
            assert time.monotonic() < deadline
            time.sleep(.01)
        return service.status(job)
    job = service.start(selected, picked["token"])["job_id"]
    assert entered.wait(5)
    assert "error" in service.start(selected, picked["token"])
    assert service.cancel(job)["cancelled"]
    resume.set()
    assert finish(job)["status"] == "cancelled" and not (asset / "Rig.json").exists()
    entered.clear()
    resume.clear()
    job = service.start(selected, picked["token"])["job_id"]
    assert entered.wait(5)
    (asset / "Rig.json").write_bytes(b"concurrent edit")
    resume.set()
    assert finish(job)["status"] == "error"
    assert (asset / "Rig.json").read_bytes() == b"concurrent edit"
    job = service.start(selected, picked["token"])["job_id"]
    assert finish(job)["status"] == "complete"
    assert (asset / "Rig.json").read_bytes() == b"rig"


def test_native_game_picker_never_grants_mod_or_asset_access(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    config.write_text(json.dumps(dict(version=1, modFolders=[], assetFolders=[])))
    monkeypatch.setattr(paths, "config_path", lambda: str(config))
    game = tmp_path / "game"
    game.mkdir()
    (game / "data.pak").write_bytes(b"archive")
    api = ModViewerAPI()
    api._window = SimpleNamespace(create_file_dialog=lambda *_: [str(game)])
    picked = api.select_game_folder()
    assert picked["token"] and picked["path"] == str(game.resolve())
    assert not api._access.was_picker_selected(str(game))
    with pytest.raises(PermissionError):
        api._access.mod_folder(str(game))
    with pytest.raises(PermissionError):
        api._access.asset_folder(str(game))


def test_runtime_pin_archive_containment_and_cache_lifecycle(tmp_path, monkeypatch):
    def bundle(name="Cue4ParseMcp.exe"):
        stream = BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr(name, b"synthetic executable")
        return stream.getvalue()
    current = bundle()
    monkeypatch.setattr(runtime, "urlopen", lambda *_args, **_kwargs: BytesIO(current))
    monkeypatch.setattr(runtime, "ARCHIVE_SHA256", hashlib.sha256(current).hexdigest())
    tool = runtime.ensure_runtime(threading.Event(), tmp_path / "cache01")
    assert tool.read_bytes() == b"synthetic executable"
    assert runtime.ensure_runtime(threading.Event(), tmp_path / "cache01") == tool
    current = bundle("../escape.exe")
    monkeypatch.setattr(runtime, "ARCHIVE_SHA256", hashlib.sha256(current).hexdigest())
    with pytest.raises(ValueError, match="unsafe"):
        runtime.ensure_runtime(threading.Event(), tmp_path / "cache02")
    monkeypatch.setattr(runtime, "ARCHIVE_SHA256", "0" * 64)
    with pytest.raises(ValueError, match="checksum"):
        runtime.ensure_runtime(threading.Event(), tmp_path / "cache03")


def test_native_process_errors_are_redacted_and_cancellation_stops_process(tmp_path, monkeypatch):
    game = tmp_path / "game"
    game.mkdir()
    (game / "data.pak").write_bytes(b"archive")
    work = tmp_path / "work"
    work.mkdir()
    cancel = threading.Event()
    client = runtime.Client(tmp_path / "tool.exe", game, work, cancel)
    calls = []
    class Process:
        returncode = 1
        def __init__(self, arguments, **options):
            calls.append((arguments, options))
            options["stdout"].write(b'{"error":"synthetic-private-token"}')
        def poll(self):
            return self.returncode
    monkeypatch.setattr(runtime.subprocess, "Popen", Process)
    with pytest.raises(ValueError, match="character name"):
        client.search("--config=outside.json", "SkeletalMesh", "Client/Content")
    assert calls == []
    with pytest.raises(ValueError) as failure:
        client.call("search", "wuwa", "name with spaces")
    assert "synthetic-private-token" not in str(failure.value)
    assert calls[0][0][3] == "name with spaces"
    assert "shell" not in calls[0][1]
    killed = []
    class Pending(Process):
        returncode = None
        def poll(self):
            cancel.set()
            return self.returncode
        def kill(self):
            killed.append(True)
            self.returncode = -1
        def wait(self):
            return self.returncode
    monkeypatch.setattr(runtime.subprocess, "Popen", Pending)
    with pytest.raises(runtime.ExtractionCancelled):
        client.call("search", "wuwa", "fixture")
    assert killed == [True]

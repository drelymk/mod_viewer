import hashlib
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import struct
import threading
import time
from types import SimpleNamespace

import pytest

from app.assets import index
from app.assets.game_extract import native, pipeline, runtime
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


def psa(bones, rows, duration=1):
    records = bytearray()
    for bone in bones:
        row = bytearray(120)
        row[:len(bone["name"])] = bone["name"].encode()
        struct.pack_into("<i4f3f", row, 72, bone["parent"], *bone["rotation"], *bone["translation"])
        records.extend(row)
    info = bytearray(168)
    frames = len(rows) // len(bones)
    struct.pack_into("<f", info, 152, frames / duration)
    struct.pack_into("<i", info, 164, frames)
    keys = bytearray()
    for ordinal, row in enumerate(rows):
        x, y, z, qx, qy, qz, qw = row
        keys.extend(struct.pack("<8f", x, -y, z, qx, -qy, qz, -qw if bones[ordinal % len(bones)]["parent"] < 0 else qw, 1))
    def chunk(name, size, count, data):
        return struct.pack("<20siii", name, 0, size, count) + data
    return chunk(b"BONENAMES", 120, len(bones), records) + chunk(b"ANIMINFO", 168, 1, info) + chunk(b"ANIMKEYS", 32, len(rows), keys)


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


@pytest.mark.parametrize("kind", ["metadata", "fmt", "vb", "ib", "mesh", "base", "layer"])
def test_oversized_extraction_files_are_rejected_before_reading(tmp_path, monkeypatch, kind):
    root, asset, record, client, _ = fixture(tmp_path)
    geometry = asset / "11111111"
    target = {"metadata": geometry / "Metadata.json", "fmt": geometry / "Component 0.fmt",
              "vb": geometry / "Component 0.vb", "ib": geometry / "Component 0.ib",
              "base": client.work / "animations/Clip01.psa", "layer": client.work / "animations/Rib_Clip01.psa"}.get(kind)
    if kind == "mesh":
        target = client.export("mesh", MESH, client.work / "mesh")[0]
    before = {file: file.read_bytes() for file in asset.rglob("*") if file.is_file()}
    original_stat, original_open = Path.stat, Path.open

    def stat(file, *args, **kwargs):
        result = original_stat(file, *args, **kwargs)
        if file == target:
            fields = list(result)
            fields[6] = 1024 ** 4
            return os.stat_result(fields)
        return result

    def open_file(file, mode="r", *args, **kwargs):
        if file == target and "r" in mode:
            pytest.fail("Oversized input was opened for reading.")
        return original_open(file, mode, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "stat", stat)
        patch.setattr(Path, "open", open_file)
        if kind in ("base", "layer"):
            if kind == "base":
                with pytest.raises(ValueError, match="Native animation exceeds its size limit"):
                    pipeline.build(client, str(root), record, tmp_path / "output", "Character01", lambda *_: None, threading.Event())
            else:
                result, _ = pipeline.build(client, str(root), record, tmp_path / "output", "Character01", lambda *_: None, threading.Event())
                assert result["secondary"] == 0 and result["skipped_layers"] == 1
        else:
            with pytest.raises(ValueError, match="size limit"):
                read_mesh(target) if kind == "mesh" else capture(str(root), record)
    assert before == {file: file.read_bytes() for file in asset.rglob("*") if file.is_file()}


@pytest.mark.parametrize("attribute,count", [
    ("VERTICES", native.MAX_VERTICES + 1), ("INDICES", native.MAX_INDICES + 1),
    ("WEIGHTS", native.MAX_VERTICES * native.MAX_INFLUENCES + 1),
    ("TEXCOORDS", native.MAX_VERTICES + 1), ("TEXCOORDS", 4), ("WEIGHTS", 9),
])
def test_native_geometry_counts_are_bounded_before_decoding(tmp_path, attribute, count):
    _, _, _, client, _ = fixture(tmp_path)
    file = client.export("mesh", MESH, client.work / "mesh")[0]
    data = file.read_bytes()
    reader = native.Reader(data)
    reader.take(8)
    reader.string()
    reader.take(1)
    reader.string()
    reader.string()
    reader.take(1)
    header = data[:reader.tell()]
    outer = reader.attributes()
    levels = native.Reader(outer["LODS"])
    levels.integer()
    name = levels.string()
    level = levels.attributes()
    if attribute == "WEIGHTS" and count == 9:
        payload = b"".join(struct.pack("<Hif", 0, 0, 0.1) for _ in range(count))
    else:
        payload = b""  # Counts must fail before decoding any declared records.
    level[attribute] = ((struct.pack("<i", 1) + string("UV0")) if attribute == "TEXCOORDS" else b"") + struct.pack("<i", count) + payload
    outer["LODS"] = struct.pack("<i", 1) + string(name) + attributes(level)
    file.write_bytes(header + attributes(outer))
    with pytest.raises(ValueError, match="limit|influences"):
        read_mesh(file)


def test_native_mesh_decompression_is_bounded(tmp_path):
    file = tmp_path / "mesh.uemodel"
    file.write_bytes(b"UEFORMAT" + string("UEMODEL") + b"\x0a" + string("Model01") + string(MESH)
                     + b"\x01" + string("GZIP") + struct.pack("<i", native.MAX_MESH_BYTES + 1))
    with pytest.raises(ValueError, match="count exceeds its limit"):
        read_mesh(file)


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


@pytest.mark.parametrize("mode", ["Animation", "short", "empty", "AnimationRelative", "oversized", "malformed"])
def test_extraction_uses_verified_source_reference_with_mesh_specific_rest_rotations(tmp_path, mode):
    root, asset, record, client, _ = fixture(tmp_path)
    original_export, original_batch = client.export, client.batch
    original = read_mesh(client.export("mesh", MESH, client.work / "original")[0])["bones"]
    rotation = [0, 0, 2 ** -.5, 2 ** -.5]
    source = dict(Type="Skeleton", Properties=dict(BoneTree=[
        dict(TranslationRetargetingMode="EBoneTranslationRetargetingMode::" + ("AnimationRelative" if mode == "AnimationRelative" else "Animation")) for _ in original]),
        ReferenceSkeleton=dict(
            FinalRefBoneInfo=[dict(Name=bone["name"], ParentIndex=bone["parent"]) for bone in original],
            FinalRefBonePose=[dict(Translation=dict(zip(("X", "Y", "Z"), bone["translation"])),
                                  Rotation=dict(zip(("X", "Y", "Z", "W"), bone["rotation"])),
                                  Scale3D=dict(zip(("X", "Y", "Z"), bone["scale"]))) for bone in original]))
    tree = source["Properties"]["BoneTree"]
    if mode == "short":
        source["Properties"]["BoneTree"] = tree[:1]
    elif mode == "empty":
        source["Properties"]["BoneTree"] = []
    elif mode == "oversized":
        source["Properties"]["BoneTree"] = tree + tree
    elif mode == "malformed":
        source["Properties"]["BoneTree"] = [None]
    exports = []
    authored = False

    def export(kind, package, destination, format=None):
        if package == SKELETON:
            exports.append(package)
            destination.mkdir(parents=True, exist_ok=True)
            file = destination / "Skeleton01.json"
            file.write_text(json.dumps([source]))
            return [file]
        files = original_export(kind, package, destination, format)
        if kind == "mesh":
            bone = original[1]
            before = struct.pack("<3f4f3f", *bone["translation"], *bone["rotation"], *bone["scale"])
            after = struct.pack("<3f4f3f", *bone["translation"], *rotation, *bone["scale"])
            files[0].write_bytes(files[0].read_bytes().replace(before, after, 1))
        return files

    def batch(kind, packages, destination, format=None):
        result = original_batch(kind, packages, destination, format)
        if kind == "json" and authored:
            file = Path(result[BASE]["rootFiles"][0])
            rows = json.loads(file.read_text())
            rows[0]["Properties"]["TrackToSkeletonMapTable"].append(dict(BoneTreeIndex=1))
            file.write_text(json.dumps(rows))
        return result

    client.export, client.batch = export, batch
    client.search = lambda query, kind, under: [MESH] if kind == "SkeletalMesh" else [BASE]
    output = tmp_path / "output"
    if mode not in ("Animation", "short", "empty"):
        with pytest.raises(ValueError, match="unsupported retargeting"):
            pipeline.build(client, str(root), record, output, "Character01", lambda *_: None, threading.Event())
        assert exports == [SKELETON] and not (asset / "Rig.json").exists()
        return
    result, _ = pipeline.build(client, str(root), record, output, "Character01", lambda *_: None, threading.Event())
    assert result["imported"] == 1 and exports == [SKELETON]
    omitted = (output / "Animation/Clip01.bin").read_bytes()
    assert struct.unpack_from("<4f", omitted, 28 + 12) == pytest.approx(rotation)
    authored = True
    output = tmp_path / "authored"
    pipeline.build(client, str(root), record, output, "Character01", lambda *_: None, threading.Event())
    keys = (output / "Animation/Clip01.bin").read_bytes()
    assert struct.unpack_from("<4f", keys, 28 + 12) == (0, 0, 0, 1)
    assert keys[:28] == omitted[:28]
    assert not (asset / "Rig.json").exists()


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


@pytest.mark.parametrize("change, expected", [
    ("unchanged", [0, 2, 3]), ("quaternion_sign", [0, 2, 3]),
    ("translation", None), ("rotation", None), ("scale", None),
    ("wrong_parent", None), ("missing_name", None), ("duplicate_name", None),
    ("cycle", None), ("invalid_parent", None), ("nonfinite", None),
    ("invalid_rotation", None), ("nonuniform_scale", None),
])
def test_companion_sub_skeleton_proves_collapsed_ancestors_in_world_space(change, expected):
    def bone(name, parent, translation, rotation=None, scale=None):
        return dict(name=name, parent=parent, translation=translation,
                    rotation=rotation or [0, 0, 0, 1], scale=scale or [1, 1, 1])

    rotation = [0, 0, 2 ** -.5, 2 ** -.5]
    primary = dict(bones=[bone("Root", -1, [0, 0, 0]),
                          bone("Anchor01", 0, [1, 0, 0], rotation, [-2, -2, -2]),
                          bone("Joint01", 1, [2, 0, 0]), bone("Joint02", 2, [1, 0, 0])])
    mesh = dict(bones=[bone("Root", -1, [0, 0, 0]),
                       bone("Joint01", 0, [1, -4, 0], rotation, [-2, -2, -2]),
                       bone("Joint02", 1, [1, 0, 0])])
    joint = mesh["bones"][1]
    if change == "quaternion_sign":
        joint["rotation"] = [-value for value in rotation]
    elif change == "translation":
        joint["translation"][0] += .01
    elif change == "rotation":
        joint["rotation"] = [0, 0, 0, 1]
    elif change == "scale":
        joint["scale"] = [2, 2, 2]
    elif change == "wrong_parent":
        # World transforms alone cannot prove a hierarchy that skips a represented parent.
        mesh["bones"][2] = bone("Joint02", 0, [1, -6, 0], rotation, [-2, -2, -2])
    elif change == "missing_name":
        joint["name"] = "Unknown01"
    elif change == "duplicate_name":
        joint["name"] = "Root"
    elif change == "cycle":
        joint["parent"] = 2
    elif change == "invalid_parent":
        joint["parent"] = -2
    elif change == "nonfinite":
        joint["translation"][0] = float("nan")
    elif change == "invalid_rotation":
        joint["rotation"] = [0, 0, 0, 0]
    elif change == "nonuniform_scale":
        joint["scale"] = [1, 2, 1]
    assert pipeline.sub_skeleton_map(primary, mesh) == expected


def test_companion_prop_discovery_and_unique_sub_skeleton_adoption(tmp_path, monkeypatch):
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
    primary_package = CHARACTER + "/Model/Body01"
    accessory = "Client/Content/Aki/Character/Prop/Accessory/Character01/Model/Item01"
    own_accessory = CHARACTER + "/Model/Item01"
    packages = [primary_package, accessory]
    exports = []

    class Client:
        def __init__(self):
            self.work = work

        def search(self, query, kind, under):
            assert (query, kind, under) == ("Character01", "SkeletalMesh", "Client/Content/Aki/Character")
            return packages

        def export(self, kind, package, destination, format=None):
            exports.append(package)
            return [package]

    monkeypatch.setattr(pipeline, "read_mesh", lambda package: mesh)
    monkeypatch.setattr(pipeline, "match_components",
                        lambda parts, _mesh: [dict(bone_map=[0, 1, 2])] if parts[0]["index"] == 1 else (_ for _ in ()).throw(ValueError()))
    def discover():
        return pipeline.adopt_companions(Client(), captured, primary, primary_package, {0}, CHARACTER, threading.Event())

    found = discover()
    assert list(found) == [1] and found[1][1]["bone_map"] == [0, 2, 1]
    assert exports == [accessory]
    packages[:] = [primary_package, own_accessory, own_accessory]
    assert discover()[1][0] == own_accessory  # same-character meshes are candidates; repeated search rows are not ambiguity
    packages.append(accessory)
    assert discover() == {}  # a second proven mesh, including an unindexed accessory, is ambiguous
    packages[:] = [own_accessory] + [CHARACTER + f"/Model/Decoy{number:02}" for number in range(64)]
    exports.clear()
    assert discover() == {} and not exports  # never adopt from a silently truncated candidate set
    packages[:] = [accessory]
    monkeypatch.setattr(pipeline, "read_mesh", lambda package: prop)
    assert discover() == {}  # geometry alone cannot authorize a different hierarchy


@pytest.mark.parametrize("indexed", [False, True])
def test_companion_extraction_exposes_animation_for_every_preview_source(tmp_path, monkeypatch, indexed):
    from app.assets.game_extract.game_index import GameIndex
    from app.bridge.asset_preview import AssetPreview

    root, asset, record, client, _ = fixture(tmp_path)
    prop = asset / "22222222"
    shutil.copytree(asset / "11111111", prop)
    file = prop / "Component 0.vb"
    data = bytearray(file.read_bytes())
    for offset in range(0, len(data), 28):
        struct.pack_into("<f", data, offset, struct.unpack_from("<f", data, offset)[0] + 10)
    file.write_bytes(data)
    metadata = json.loads((prop / "Metadata.json").read_text())
    metadata["vb0_hash"] = "22222222"
    (prop / "Metadata.json").write_text(json.dumps(metadata))
    record = index.build_index("WWMI", str(root))["assets"][0]
    original_export, original_search = client.export, client.search
    body = read_mesh(original_export("mesh", MESH, client.work / "reference")[0])
    companion = deepcopy(body)
    companion["positions"] = [(x + 10, y, z) for x, y, z in body["positions"]]
    package = "Client/Content/Aki/Character/Prop/Accessory/Character01/Model/Item01"
    searches = []

    def search(query, kind, under):
        searches.append((kind, under))
        return [package] if under == "Client/Content/Aki/Character" else original_search(query, kind, under)

    client.search = search
    client.export = lambda kind, path, *args: [path] if path == package else original_export(kind, path, *args)
    original_reader = pipeline.read_mesh
    monkeypatch.setattr(pipeline, "read_mesh", lambda file: companion if file == package else original_reader(file))
    # A ready index deliberately lacks the accessory; it cannot replace the broader native search.
    cached = GameIndex({MESH: [[3, 3]]}) if indexed else None
    output = tmp_path / "output"
    result, _ = pipeline.build(client, str(root), record, output, "Character01", lambda *_: None, threading.Event(), cached)
    assert result["proven_components"] == 2 and result["unmatched_components"] == 0
    report = json.loads((output / "RigExtraction.json").read_text())
    assert report["companion_meshes"] == [package]
    assert ("SkeletalMesh", "Client/Content/Aki/Character") in searches
    shutil.copytree(output, asset, dirs_exist_ok=True)
    preview = AssetPreview(object())
    monkeypatch.setattr(preview, "_asset_selection", lambda _: (
        str(asset), dict(type="WWMI", path=str(root)), {}, record))
    try:
        payload = preview.load_asset(str(asset))
        weights = preview.get_skinning_preview(payload["metadata"]["asset"]["rig_session"])
        assert weights["status"] == "ok" and len(weights["game_rigs"]) == 1
        assert len(weights["meshes"]) == 2
        rig = next(iter(weights["game_rigs"].values()))
        assert all(mesh["game_rig_id"] == rig["id"] for mesh in weights["meshes"].values())
        assert [clip["id"] for clip in rig["animations"]] == ["Clip01"]
        assert preview.get_animation(rig["animation_session"], "Clip01")["status"] == "ok"
    finally:
        preview.clear_preview()


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


@pytest.mark.parametrize("scenario", ["hold", "trim", "rate", "reference", "mask", "skeleton", "export", "metadata", "frame_count"])
def test_secondary_alignment_preserves_body_and_reports_rejected_companions(tmp_path, scenario):
    root, asset, record, client, _ = fixture(tmp_path)
    bones = read_mesh(client.export("mesh", MESH, client.work / "reference")[0])["bones"]
    layer_frames = 10 if scenario == "trim" else 3
    layer_duration = (layer_frames - 1) * .5 if scenario != "rate" else 2
    body_rows = [(float(frame), 0, 0, 0, 0, 0, 1) if bone == 0 else (1, 2, 3, 0, 0, 0, 1)
                 for frame in range(4) for bone in range(2)]
    layer_rows = [(99, 99, 99, 0, 0, 0, 1) if bone == 0 else (frame + 1, 2, 3, 0, 0, 0, 1)
                  for frame in range(layer_frames) for bone in range(2)]
    original_batch = client.batch

    def batch(kind, packages, destination, format=None):
        result = original_batch(kind, packages, destination, format)
        for package in packages:
            if kind == "json" and package == LAYER and scenario == "metadata":
                result[package] = dict(state="failed", rootFiles=[], failures=["Export failed."])
                continue
            file = Path(result[package]["rootFiles"][0])
            if kind == "json":
                rows = json.loads(file.read_text())
                props = rows[0]["Properties"]
                props.update(NumFrames=4 if package == BASE else layer_frames,
                             SequenceLength=1.5 if package == BASE else layer_duration)
                if package == LAYER:
                    if scenario == "reference":
                        props["RefPoseType"] = "ABPT_AnimFrame"
                    elif scenario == "mask":
                        props["TrackToSkeletonMapTable"] = [dict(BoneTreeIndex=0)]
                    elif scenario == "skeleton":
                        props["Skeleton"] = dict(ObjectPath=CHARACTER + "/Model/OtherSkeleton.0")
                file.write_text(json.dumps(rows))
            elif package == BASE:
                file.write_bytes(psa(bones, body_rows, 1.5))
            elif scenario == "export":
                result[package] = dict(state="failed", rootFiles=[], failures=["Export failed."])
            else:
                frames = layer_rows if scenario != "frame_count" else layer_rows + layer_rows[-2:]
                file.write_bytes(psa(bones, frames, layer_duration))
        return result

    client.batch = batch
    output = tmp_path / "output"
    result, _ = pipeline.build(client, str(root), record, output, "Character01", lambda *_: None, threading.Event())
    raw = (output / "Animation/Clip01.bin").read_bytes()
    _, body = pipeline.convert_clip(psa(bones, body_rows, 1.5), {"bones": bones}, 1.5)
    assert result["clips"] == result["imported"] == 1
    for frame in range(4):
        assert raw[frame * 56:frame * 56 + 28] == body[frame * 56:frame * 56 + 28]
    if scenario in ("hold", "trim"):
        assert result["secondary"] == 1 and result["skipped"] == result["skipped_layers"] == 0
        metadata = json.loads((output / "Animation/Clip01.json").read_text())
        timing = metadata["reference_layer"]["timing"]
        assert timing["body_duration"] == 1.5 and timing["layer_duration"] == layer_duration
        assert timing["held_frames"] == (1 if scenario == "hold" else 0)
        assert timing["trimmed_frames"] == (6 if scenario == "trim" else 0)
        for frame in range(4):
            expected = layer_rows[min(frame, layer_frames - 1) * 2 + 1]
            assert struct.unpack_from("<7f", raw, frame * 56 + 28) == expected
    else:
        assert result["secondary"] == 0 and result["skipped"] == result["skipped_layers"] == 1
        detail, = result["skipped_details"]
        assert detail["kind"] == "layer" and detail["source"] == LAYER and detail["base"] == BASE and detail["reason"]
        assert raw == body
    assert not (asset / "Rig.json").exists()


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
    (asset / "Rig.json").write_bytes(b"existing rig")
    (asset / "Animation").mkdir()
    (asset / "Animation/manifest.json").write_bytes(b"existing animation")
    (asset / "RigExtraction.json").write_bytes(b"existing report")
    original = {file: file.read_bytes() for file in asset.rglob("*") if file.is_file()}
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
    monkeypatch.setattr("app.assets.game_extract.runtime.ensure_runtime", tool)
    monkeypatch.setattr("app.assets.game_extract.runtime.Client", lambda *_: None)
    def build(_client, root, record, output, *_):
        output.mkdir()
        (output / "Rig.json").write_bytes(b"rig")
        (output / "Animation").mkdir()
        (output / "Animation/manifest.json").write_bytes(b"animation")
        (output / "RigExtraction.json").write_bytes(b"report")
        return dict(bones=2, clips=1, imported=1, secondary=1, skipped=0), capture(root, record)[1]
    monkeypatch.setattr("app.assets.game_extract.wwmi.build", build)
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
    assert finish(job)["status"] == "cancelled"
    assert original == {file: file.read_bytes() for file in asset.rglob("*") if file.is_file()}
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


@pytest.mark.parametrize("game_kind", ["GIMI", "ZZMI"])
def test_game_adapters_scope_grants_and_reject_unavailable_jobs(tmp_path, monkeypatch, game_kind):
    from app.assets.game_extract import backends

    root, asset, record, _, _ = fixture(tmp_path)
    monkeypatch.setattr(paths, "config_path", lambda: str(tmp_path / "config.json"))
    game = tmp_path / "game"
    game.mkdir()
    (game / "data.pak").write_bytes(b"archive")
    kind = game_kind
    service = RigExtraction(lambda _: (str(asset), dict(type=kind, path=str(root)), {}, record))
    window = SimpleNamespace(create_file_dialog=lambda *_: [str(game)])
    other = service.pick_game(window, game_kind)
    assert other["kind"] == game_kind and service.saved_game(game_kind)["path"] == str(game)
    assert service.saved_game("WWMI") is None
    assert service.index_status(game_kind)["state"] == "unsupported"
    assert service.start_index(game_kind)["error_code"] == "gameExtract.unavailable"
    assert service.start(str(asset), other["token"])["error_code"] == "gameExtract.unavailable"
    assert not service._jobs and not (asset / "Rig.json").exists()
    kind = "WWMI"
    assert "error" in service.start(str(asset), other["token"])
    assert not service._jobs
    # A newly registered adapter routes through the same folder and job lifecycle.
    monkeypatch.setitem(backends._BACKENDS, game_kind, backends.backend_for("WWMI"))
    assert backends.capabilities()[game_kind] == dict(extraction=True, index=True)
    assert service.index_status(game_kind)["state"] == "missing"
    with pytest.raises(ValueError, match="Unknown"):
        service.pick_game(window, "Other")


def test_bundled_runtime_pin_integrity_and_containment(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    required = ("ModViewer.GameExtract.exe", "CUE4Parse.dll", "CUE4Parse-Conversion.dll", "CUE4Parse-Natives.dll",
                "oodle-data-shared.dll", "zlib-ng2.dll", "keys.json", "ModViewer.GameExtract.deps.json", "ModViewer.GameExtract.runtimeconfig.json")
    files = {}
    for name in required:
        (bundle / name).write_bytes(b"synthetic runtime")
        files[name] = hashlib.sha256(b"synthetic runtime").hexdigest()
    record = dict(version=runtime.VERSION, parser=runtime.PARSER_COMMIT, files=files)
    manifest = bundle / "runtime.json"
    manifest.write_text(json.dumps(record))
    assert runtime.ensure_runtime(threading.Event(), bundle) == bundle / required[0]
    (bundle / "keys.json").write_bytes(b"modified keys")
    with pytest.raises(ValueError, match="checksum"):
        runtime.ensure_runtime(threading.Event(), bundle)
    (bundle / "keys.json").write_bytes(b"synthetic runtime")
    key_digest = files.pop("keys.json")
    manifest.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="Incomplete"):
        runtime.ensure_runtime(threading.Event(), bundle)
    files["keys.json"] = key_digest
    manifest.write_text(json.dumps(record))
    (bundle / required[0]).write_bytes(b"modified")
    with pytest.raises(ValueError, match="checksum"):
        runtime.ensure_runtime(threading.Event(), bundle)
    (bundle / required[0]).write_bytes(b"synthetic runtime")
    files["../outside.dll"] = files[required[0]]
    manifest.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="Unsafe"):
        runtime.ensure_runtime(threading.Event(), bundle)
    files.pop("../outside.dll")
    record["version"] = "unsupported"
    manifest.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="version"):
        runtime.ensure_runtime(threading.Event(), bundle)


def test_native_session_reuse_redaction_and_cancellation(tmp_path, monkeypatch):
    game = tmp_path / "game"
    game.mkdir()
    (game / "data.pak").write_bytes(b"archive")
    work = tmp_path / "work"
    work.mkdir()
    cancel = threading.Event()
    client = runtime.Client(tmp_path / "tool.exe", game, work, cancel)
    calls, requests, stopped = [], [], []
    response = {"results": [{"path": MESH}]}
    class Input:
        def write(self, data):
            request = json.loads(data)
            requests.append(request)
            if response is not None:
                (work / f"command-{request['id']}.json").write_text(json.dumps(response))
        def flush(self):
            pass
        def close(self):
            stopped.append("stdin")
    class Process:
        returncode = None
        def __init__(self, arguments, **options):
            calls.append((arguments, options))
            self.stdin = Input()
        def poll(self):
            return self.returncode
        def kill(self):
            stopped.append("kill")
            self.returncode = -1
        def wait(self):
            stopped.append("wait")
            return self.returncode
    monkeypatch.setattr(runtime.subprocess, "Popen", Process)
    with pytest.raises(ValueError, match="character name"):
        client.search("--config=outside.json", "SkeletalMesh", "Client/Content")
    assert calls == []
    for _ in range(2):
        assert client.search("name with spaces", "SkeletalMesh", "Client/Content/Aki") == [MESH]
    assert len(calls) == 1 and requests[0]["query"] == "name with spaces"
    assert client.search("", "SkeletalMesh", "Client/Content/Aki", recursive=False) == [MESH]
    assert requests[-1]["recursive"] is False and len(calls) == 1
    assert calls[0][0] == [str(tmp_path / "tool.exe"), "--paks", str(game), "--work", str(work)]
    assert "shell" not in calls[0][1] and not (work / "games.json").exists()
    response = {"error": "synthetic-private-token"}
    with pytest.raises(ValueError) as failure:
        client.call("search", query="fixture")
    assert "synthetic-private-token" not in str(failure.value)
    assert stopped == ["kill", "wait", "stdin"] and client.process is None
    response = None
    with pytest.raises(ValueError, match="timed out"):
        client.call("search", query="fixture", timeout=-1)
    assert stopped == ["kill", "wait", "stdin"] * 2
    response = {"results": []}
    monkeypatch.setattr(runtime, "MAX_RESPONSE", 1)
    with pytest.raises(ValueError, match="too large"):
        client.call("search", query="fixture")
    assert stopped == ["kill", "wait", "stdin"] * 3
    monkeypatch.setattr(runtime, "MAX_RESPONSE", 32 * 1024 * 1024)
    response = None
    def pending():
        cancel.set()
        return None
    monkeypatch.setattr(Process, "poll", lambda self: pending() if self.returncode is None else self.returncode)
    with pytest.raises(runtime.ExtractionCancelled):
        client.call("search", query="fixture")
    assert stopped == ["kill", "wait", "stdin"] * 4
    assert not list(work.glob("command-*.json"))


@pytest.mark.parametrize("response", [
    {"items": []},
    {"items": [{"path": MESH + "Other", "state": "failed"}]},
    {"items": [{"path": MESH, "state": "completed", "rootFiles": ["../escape.json"]}]},
])
def test_native_batch_rejects_incomplete_or_uncontained_results(tmp_path, response):
    client = object.__new__(runtime.Client)
    client.call = lambda *_args, **_kwargs: response
    with pytest.raises(ValueError):
        client.batch("json", [MESH], tmp_path)

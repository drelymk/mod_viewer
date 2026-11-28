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

import numpy as np
import pytest

from app.assets import index
from app.assets.game_extract import native, pipeline, runtime, unity_dds, wuwa_props
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
MONTAGE = CHARACTER + "/CommonAnim/Montage01"
LAYER = CHARACTER + "/Skin01/RibbonAnim/Rib_Clip01"


def string(value):
    data = value.encode()
    return struct.pack("<i", len(data)) + data


def attributes(values):
    return struct.pack("<i", len(values)) + b"".join(string(key) + struct.pack("<i", len(data)) + data for key, data in values.items())


def psa(bones, rows, duration=1, *, scales=None):
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
    data = chunk(b"BONENAMES", 120, len(bones), records) + chunk(b"ANIMINFO", 168, 1, info) + chunk(b"ANIMKEYS", 32, len(rows), keys)
    if scales is not None:
        data += chunk(b"SCALEKEYS", 16, len(rows), b"".join(struct.pack("<4f", *scale, 0) for scale in scales))
    return data


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
            return [MESH] if kind == "SkeletalMesh" else ([BASE, LAYER] if kind == "AnimSequence" else [])

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
    client.search = lambda query, kind, under: (queries.append(query) if kind != "AnimMontage" else None) or original(query, kind, under)
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
    client.search = lambda query, kind, under: [MESH] if kind == "SkeletalMesh" else ([BASE] if kind == "AnimSequence" else [])
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
        return [package] if under == "Client/Content/Aki/Character" and kind != "AnimMontage" else original_search(query, kind, under)

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


def test_truncated_animation_discovery_preserves_direct_and_nested_clips(tmp_path):
    root, _, record, client, _ = fixture(tmp_path)
    search, batch = client.search, client.batch
    direct = CHARACTER + "/ClipDirect"
    calls = []

    def search_tree(query, kind, under, *, recursive=True):
        if kind != "AnimSequence":
            return search(query, kind, under)
        calls.append((under, recursive))
        if recursive and under in (CHARACTER, CHARACTER + "/Skin01"):
            raise runtime.SearchTruncated("too broad")
        return {(CHARACTER, False): [direct],
                (CHARACTER + "/CommonAnim", True): [BASE],
                (CHARACTER + "/Skin01/RibbonAnim", True): [LAYER]}.get((under, recursive), [])

    def folders(under):
        return {CHARACTER: ["CommonAnim", "Skin01"], CHARACTER + "/Skin01": ["RibbonAnim"]}[under]

    def export_batch(kind, packages, destination, format=None):
        result = batch(kind, packages, destination, format)
        if kind == "json" and direct in packages:
            metadata = json.loads(Path(result[BASE]["rootFiles"][0]).read_text())
            metadata[0]["Name"] = "ClipDirect"
            Path(result[direct]["rootFiles"][0]).write_text(json.dumps(metadata))
        return result

    client.search, client.folders, client.batch = search_tree, folders, export_batch
    output = tmp_path / "output"
    result, _ = pipeline.build(client, str(root), record, output, "Character01", lambda *_: None, threading.Event())
    assert result["imported"] == result["clips"] == 2 and result["secondary"] == 1
    assert {row["source"] for row in result["animations"]} == {direct, BASE}
    assert result["skipped"] == 0
    assert calls == [(CHARACTER, True), (CHARACTER, False), (CHARACTER + "/CommonAnim", True),
                     (CHARACTER + "/Skin01", True), (CHARACTER + "/Skin01", False),
                     (CHARACTER + "/Skin01/RibbonAnim", True)]


@pytest.mark.parametrize("failure", ["direct", "cancel"])
def test_incomplete_animation_discovery_never_produces_sidecars(tmp_path, failure):
    root, asset, record, client, _ = fixture(tmp_path)
    search = client.search
    cancel = threading.Event()

    def search_tree(query, kind, under, *, recursive=True):
        if kind != "AnimSequence":
            return search(query, kind, under)
        if recursive or failure == "direct":
            raise runtime.SearchTruncated("too broad")
        return [BASE]

    def folders(_under):
        cancel.set()
        return ["CommonAnim"]

    client.search, client.folders = search_tree, folders
    output = tmp_path / "output"
    expected = runtime.SearchTruncated if failure == "direct" else runtime.ExtractionCancelled
    with pytest.raises(expected, match="folder exceeds|cancelled"):
        pipeline.build(client, str(root), record, output, "Character01", lambda *_: None, cancel)
    assert not output.exists() and not (asset / "Rig.json").exists() and not (asset / "Animation").exists()


def test_native_proof_and_secondary_pipeline_lifecycle(tmp_path):
    root, asset, record, client, rows = fixture(tmp_path)
    search, batch = client.search, client.batch
    def search_montages(query, kind, under):
        return [MONTAGE] if kind == "AnimMontage" else search(query, kind, under)
    def batch_montages(kind, packages, destination, format=None):
        result = batch(kind, [package for package in packages if package != MONTAGE], destination, format)
        if MONTAGE in packages:
            # Two contiguous segments of the one converted clip, cut at 0.2 seconds.
            segments = [dict(AnimReference=dict(ObjectPath=BASE + ".0"), StartPos=0, AnimStartTime=0, AnimEndTime=0.2),
                        dict(AnimReference=dict(ObjectPath=BASE + ".0"), StartPos=0.2, AnimStartTime=0.2, AnimEndTime=1)]
            destination.mkdir(parents=True, exist_ok=True)
            file = destination / "Montage01.json"
            file.write_text(json.dumps([dict(Type="AnimMontage", Properties=dict(Skeleton=dict(ObjectPath=SKELETON + ".0"),
                SlotAnimTracks=[dict(AnimTrack=dict(AnimSegments=segments))]))]))
            result[MONTAGE] = dict(state="completed", rootFiles=[str(file)], failures=[])
        return result
    client.search, client.batch = search_montages, batch_montages
    phases = []
    result, fingerprints = pipeline.build(client, str(root), record, tmp_path / "output", "Character01",
        lambda phase, *_: phases.append(phase), threading.Event())
    assert result["bones"] == 2 and result["imported"] == result["secondary"] == result["clips"] == 1
    assert result["skipped"] == 0 and fingerprints
    assert phases[:3] == ["search", "rig", "animations"]
    assert result["sequences"] == 1 and result["skipped_sequences"] == 0 and result["sequence_error"] is None
    library = json.loads((tmp_path / "output/Animation/manifest.json").read_text())
    (sequence,) = library["sequences"]
    clip_id = library["clips"][0]["id"]
    assert [(step["clip"], step["start"], step["leave"], step["fade"]) for step in sequence["steps"]] == [
        (clip_id, 0, 0.2, 0), (clip_id, 0.2, 1, 0)]
    # The authored body track keys bone 0 and the merged reference layer adds bone 1.
    assert json.loads((tmp_path / "output/Animation/Clip01.json").read_text())["keyed_bones"] == [0, 1]
    assert isinstance(library["runtime_bones"], list)
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


@pytest.mark.parametrize("names", [["Rib_clip01"], ["Rib_clip01", "Rib_CLIP01"]])
def test_secondary_companion_matches_package_name_case_insensitively(tmp_path, names):
    # Unreal package names ignore case: a lone case variant pairs, case-only duplicates stay ambiguous.
    root, _, record, client, rows = fixture(tmp_path)
    layers = [CHARACTER + "/Skin01/RibbonAnim/" + name for name in names]
    search, batch = client.search, client.batch
    client.search = lambda query, kind, under: [p for p in search(query, kind, under) if p != LAYER] + (
        layers if kind == "AnimSequence" else [])

    def renamed(kind, packages, destination, format=None):
        result = batch(kind, [package for package in packages if package not in layers], destination, format)
        for package in packages:
            if package in layers:
                result[package] = batch(kind, [LAYER], destination / package.rsplit("/", 1)[-1], format)[LAYER]
        return result

    client.batch = renamed
    output = tmp_path / "output"
    result, _ = pipeline.build(client, str(root), record, output, "Character01", lambda *_: None, threading.Event())
    data = list(struct.iter_unpack("<7f", (output / "Animation/Clip01.bin").read_bytes()))
    if len(layers) == 1:
        assert result["secondary"] == 1 and result["skipped_layers"] == 0
        assert json.loads((output / "Animation/Clip01.json").read_text())["reference_layer"]["source"] == layers[0]
        assert data[3] == (1, 2, 3, 0, 0, 1, 0)
    else:
        assert result["secondary"] == 0 and result["skipped_layers"] == 1
        detail, = result["skipped_details"]
        assert detail["source"] in layers and detail["base"] == BASE and "letter case" in detail["reason"]
        assert data[3] == rows[3]


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


def test_section_matching_preserves_captured_ranges_and_native_morph_correspondence(tmp_path, monkeypatch):
    root, _asset, record, client, _ = fixture(tmp_path)
    captured, _ = capture(str(root), record)
    mesh = read_mesh(client.export("mesh", MESH, client.work / "reference")[0])
    mesh["positions"] = [(8, 0, 0), *mesh["positions"][1:], *mesh["positions"]]
    mesh["uv"] = [channel * 2 for channel in mesh["uv"]]
    mesh["weights"] *= 2
    mesh["indices"] = list(range(6))
    mesh["sections"] = [(0, 3), (3, 3)]
    mesh["morphs"] = {"Shape01": [(0, 0, 2, 0, .5, 0, 3)]}
    palette, = match_components(captured, mesh)
    assert palette["first_index"] == 0 and palette["native_first_index"] == 3
    assert palette["bone_map"] == [0, 1]
    monkeypatch.setattr(pipeline, "read_mesh", lambda _: mesh)
    output = tmp_path / "shifted"
    result, _ = pipeline.build(client, str(root), record, output, "Character01", lambda *_: None, threading.Event())
    assert result["proven_components"] == 1 and result["unmatched_components"] == 0
    rig = validate_rig(json.loads((output / "Rig.json").read_text()))
    assert rig["components"][0]["first_index"] == 0
    assert "native_first_index" not in rig["components"][0]
    morph_file, = (output / "Animation").glob("morphs-*.bin")
    assert struct.unpack("<I6f", morph_file.read_bytes()) == (0, 0, 0, 2, 0, .5, 0)

    mesh["indices"][:3] = mesh["indices"][3:]
    with pytest.raises(ValueError, match="one unique native mesh section"):
        match_components(captured, mesh)
    with pytest.raises(ValueError, match="unique matching game mesh"):
        pipeline.build(client, str(root), record, tmp_path / "ambiguous-sections", "Character01", lambda *_: None, threading.Event())
    assert not (tmp_path / "ambiguous-sections").exists()


@pytest.mark.parametrize("attribute", ["positions", "uv", "weights"])
def test_shifted_section_matching_requires_exact_vertex_evidence(tmp_path, attribute):
    root, _asset, record, client, _ = fixture(tmp_path)
    captured, _ = capture(str(root), record)
    mesh = read_mesh(client.export("mesh", MESH, client.work / "reference")[0])
    mesh["indices"] = [0, 0, 0, 0, 1, 2]
    mesh["sections"] = [(3, 3)]
    if attribute == "positions":
        mesh["positions"][0] = (0.00001, 0, 0)
    elif attribute == "uv":
        mesh["uv"][0][0] = (0.00001, 0)
    else:
        mesh["weights"][0] = [(0, 127), (1, 128)]
    with pytest.raises(ValueError, match="one unique native mesh section"):
        match_components(captured, mesh)


@pytest.mark.parametrize("candidates,expected", [
    ({0: {2}, 1: {2, 4}, 2: {4, 6}}, [2, 4, 6]),
    ({0: {2, 4}, 1: {2}}, [4, 2]),
    ({0: {2, 4}, 1: {2, 4}}, None),
    ({0: {2}, 1: {2}}, None),
    ({0: {2}, 1: {2, 4}, 2: {4}}, None),
    ({0: {2}, 1: set()}, None),
])
def test_bone_palette_requires_a_forced_one_to_one_mapping(candidates, expected):
    before = deepcopy(candidates)
    if expected is None:
        with pytest.raises(ValueError, match="cannot be mapped unambiguously"):
            native._unique_bone_map(candidates)
    else:
        assert native._unique_bone_map(candidates) == expected
    assert candidates == before


def test_equal_weight_palette_is_retained_in_extracted_rig_and_animation(tmp_path, monkeypatch):
    root, asset, record, client, _ = fixture(tmp_path)
    mesh = read_mesh(client.export("mesh", MESH, client.work / "reference")[0])
    mesh["weights"] = [[(0, 255)], [(0, 128), (1, 128)], [(0, 128), (1, 128)]]
    vertex_file = asset / "11111111/Component 0.vb"
    vertices = bytearray(vertex_file.read_bytes())
    for vertex, weights in enumerate(((255, 0), (128, 128), (128, 128))):
        struct.pack_into("<4B", vertices, vertex * 28 + 24, *weights, 0, 0)
    vertex_file.write_bytes(vertices)
    monkeypatch.setattr(pipeline, "read_mesh", lambda _: mesh)
    output = tmp_path / "output"
    result, _ = pipeline.build(client, str(root), record, output, "Character01", lambda *_: None, threading.Event())
    assert result["proven_components"] == result["clips"] == 1
    assert result["unmatched_components"] == 0
    rig = validate_rig(json.loads((output / "Rig.json").read_text()))
    assert rig["components"][0]["bone_map"] == [0, 1]
    assert rig["components"][0]["vertex_sha256"] == hashlib.sha256(vertices).hexdigest()
    assert json.loads((output / "Animation/manifest.json").read_text())["skeleton_id"] == rig["id"]


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
    def permissions(path, **_):
        if path.name == "Animation":
            raise OSError("synthetic permission failure")
    monkeypatch.setattr("app.bridge.rig_extraction.inherit_directory_permissions", permissions)
    with pytest.raises(OSError):
        install_package(asset, output, directory)
    assert sidecar_revision(asset) == before
    directory, output = stage("stage02")
    monkeypatch.setattr("app.bridge.rig_extraction.inherit_directory_permissions", lambda *_, **__: None)
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
    monkeypatch.setattr("app.bridge.rig_extraction.inherit_directory_permissions", lambda *_, **__: None)
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


@pytest.mark.parametrize("kind", ["ZZMI", "WWMI"])
def test_index_failure_is_reported_without_rescanning_required_indexes(tmp_path, monkeypatch, kind):
    from app.assets.game_extract import backends

    asset = tmp_path / "Asset01"
    asset.mkdir()
    (asset / "Rig.json").write_bytes(b"existing rig")
    entry = dict(type=kind, path=str(tmp_path))
    record = dict(path="Asset01")
    selected = str(asset)
    service = RigExtraction(lambda _: (selected, entry, {}, record))
    monkeypatch.setattr(paths, "config_path", lambda: str(tmp_path / "config.json"))
    monkeypatch.setattr("app.bridge.rig_extraction.inherit_directory_permissions", lambda *_, **__: None)
    calls = []
    backend = backends.require_backend(kind)
    monkeypatch.setattr(backend, "capture", lambda *_: ([], {}))
    monkeypatch.setattr(backend, "open_client", lambda *_: SimpleNamespace(close=lambda: calls.append("close")))
    def failed_index(*_):
        calls.append("index")
        raise ValueError("Synthetic index scan failed.")
    monkeypatch.setattr(backend, "ensure_index", failed_index)
    def build(_client, _root, _record, output, _query, _progress, _cancel, index):
        assert index is None
        calls.append("fallback")
        output.mkdir()
        (output / "Rig.json").write_bytes(b"new rig")
        (output / "Animation").mkdir()
        (output / "RigExtraction.json").write_bytes(b"report")
        return dict(bones=1, clips=1, imported=1, secondary=0, skipped=0), {}
    monkeypatch.setattr(backend, "build", build)
    job = service._begin()
    service._run(job, selected, entry, record, str(tmp_path / "Game01"), "Character01", backend)
    result = service.status(job)
    if kind == "ZZMI":
        assert result["status"] == "error" and result["error"] == "Synthetic index scan failed."
        assert calls == ["index", "close"]
        assert (asset / "Rig.json").read_bytes() == b"existing rig"
    else:
        assert result["status"] == "complete" and calls == ["index", "fallback", "close"]
        assert (asset / "Rig.json").read_bytes() == b"new rig"


@pytest.mark.parametrize("game_kind", ["GIMI"])
def test_game_adapters_scope_grants_and_reject_unavailable_jobs(tmp_path, monkeypatch, game_kind):
    from app.assets.game_extract import backends

    root, asset, record, _, _ = fixture(tmp_path)
    # Every shipped game has an adapter; an unregistered one keeps folder selection only.
    monkeypatch.delitem(backends._BACKENDS, game_kind)
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


@pytest.mark.parametrize("limit", ["file", "total"])
@pytest.mark.parametrize("completed", [False, True])
def test_native_export_budget_terminates_writing_and_completed_helpers(tmp_path, monkeypatch, limit, completed):
    game = tmp_path / "game"
    game.mkdir()
    (game / "data.pak").write_bytes(b"archive")
    work = tmp_path / "work"
    nested = work / "animations/nested"
    nested.mkdir(parents=True)
    growing = nested / "clip.psa"
    growing.write_bytes(b"x" * (240 if limit == "file" else 120))
    if limit == "total":
        (work / "prior.json").write_bytes(b"x" * 120)
    monkeypatch.setattr(runtime, "MAX_EXPORT_BYTES", 256)
    monkeypatch.setattr(runtime, "MAX_WORK_BYTES", 256 if limit == "total" else 4096)
    client = runtime.Client(tmp_path / "tool.exe", game, work, threading.Event())
    stopped, polls, writes = [], [], []

    class Input:
        def write(self, _data):
            pass
        def flush(self):
            pass
        def close(self):
            stopped.append("stdin")

    class Process:
        stdin = Input()
        returncode = None
        def poll(self):
            if self.returncode is None:
                polls.append(True)
            return self.returncode
        def kill(self):
            stopped.append("kill")
            self.returncode = -1
        def wait(self):
            stopped.append("wait")

    def write_more(_delay):
        writes.append(True)
        assert len(writes) == 1, "The over-budget producer was allowed to keep writing."
        with growing.open("ab") as stream:
            stream.write(b"x" * 32)
        if completed:
            (work / "command-1.json").write_text("{}")

    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *_args, **_kwargs: Process())
    monkeypatch.setattr(runtime.time, "sleep", write_more)
    with pytest.raises(ValueError, match="temporary output budget"):
        client.call("batch", kind="anim", paths=[BASE], outDir=str(nested))
    assert stopped == ["kill", "wait", "stdin"] and client.process is None
    assert polls == [True, True]  # One pending check, then the existing close path.
    assert not (work / "command-1.json").exists()
    # The existing job owns staging cleanup; the client only stops its producer.
    assert growing.exists()
    with pytest.raises(ValueError, match="temporary output budget"):
        client.call("search", query="fixture")
    assert stopped == ["kill", "wait", "stdin"]


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


@pytest.mark.parametrize("request_kind,response", [
    ("texture-tier", None),
    ("texture-absent-tier", None),
    ("texture-metadata", dict(texture=dict(width=0, height=4, format="PF_BC7", srgb=True))),
    ("texture-metadata", dict(texture=dict(width=4, height=4, format="PF_BC7"))),
])
def test_texture_exports_reject_unsafe_requests_and_incomplete_results(tmp_path, request_kind, response):
    client = object.__new__(runtime.Client)
    client.paks = tmp_path / "Paks"
    (tmp_path / "HD").mkdir()
    file = tmp_path / "Texture01.mip"
    file.write_bytes(bytes(16))
    calls = []
    client.call = lambda *args, **kwargs: calls.append(kwargs) or {"rootFiles": [str(file)], **(response or {})}
    with pytest.raises(ValueError):
        if request_kind == "texture-tier":
            client.texture(MESH, tmp_path, "LOW")
        else:
            client.texture(MESH, tmp_path, "UHD" if request_kind == "texture-absent-tier" else "HD")
    # Unknown tiers and tiers absent beside Paks never reach the native helper.
    assert bool(calls) == (request_kind == "texture-metadata")


@pytest.mark.parametrize("curve_mode", ["supported", "weighted", "interpolation"])
def test_morph_extraction_maps_native_vertices_and_upgrades_existing_clips(tmp_path, curve_mode):
    from app.assets.animations import read_clip
    from app.assets.animation_morphs import read_morphs

    root, asset, record, client, _ = fixture(tmp_path)
    geometry = asset / "11111111"
    vb = (geometry / "Component 0.vb").read_bytes()
    (geometry / "Component 0.vb").write_bytes(vb[56:] + vb[:56])
    (geometry / "Component 0.ib").write_bytes(struct.pack("<3H", 1, 2, 0))
    original_export, original_batch = client.export, client.batch
    unrelated = CHARACTER + "/CommonAnim/Other01"

    def export(kind, package, destination, format=None):
        files = original_export(kind, package, destination, format)
        if kind == "mesh":
            reader = native.Reader(files[0].read_bytes())
            reader.take(8)
            reader.string()
            reader.take(1)
            reader.string()
            reader.string()
            reader.take(1)
            header = reader.getvalue()[:reader.tell()]
            outer = reader.attributes()
            levels = native.Reader(outer["LODS"])
            levels.integer()
            name = levels.string()
            level = levels.attributes()
            level["MORPHTARGETS"] = (struct.pack("<i", 1) + string("Shape01") + struct.pack("<i6fI", 1, 0, 0, 2, 0, .5, 0, 2))
            outer["LODS"] = struct.pack("<i", 1) + string(name) + attributes(level)
            files[0].write_bytes(header + attributes(outer))
        return files

    def batch(kind, packages, destination, format=None):
        result = original_batch(kind, packages, destination, format)
        if kind == "json" and unrelated in result:
            Path(result[unrelated]["rootFiles"][0]).write_text(json.dumps([dict(
                Type="AnimSequence", Name="Other01", Properties=dict(
                    Skeleton=dict(ObjectPath=CHARACTER + "/Model/OtherSkeleton01.0"), NumFrames=2, SequenceLength=1),
                CompressedCurveNames=[dict(DisplayName="Shape01")],
                CompressedCurveData=dict(FloatCurves=[dict(FloatCurve=dict(Keys=[{}]))]))]))
        if kind == "json" and BASE in result:
            file = Path(result[BASE]["rootFiles"][0])
            rows = json.loads(file.read_text())
            rows[0]["CompressedCurveNames"] = [dict(DisplayName=name) for name in ("Shape01", "Unknown01")]
            keys = [dict(Time=time, Value=time, ArriveTangent=0, LeaveTangent=0,
                         InterpMode="RCIM_Linear", TangentWeightMode="RCTWM_WeightedNone") for time in (0, 1)]
            if curve_mode == "weighted":
                keys[0]["TangentWeightMode"] = "RCTWM_WeightedBoth"
            elif curve_mode == "interpolation":
                keys[0]["InterpMode"] = "RCIM_Unknown"
            rows[0]["CompressedCurveData"] = dict(FloatCurves=[dict(FloatCurve=dict(Keys=keys))] * 2)
            file.write_text(json.dumps(rows))
        return result

    # Upgrade a pre-existing bone-only library without changing its clip identity.
    output = tmp_path / "before"
    pipeline.build(client, str(root), record, output, "Character01", lambda *_: None, threading.Event())
    shutil.copytree(output / "Animation", asset / "Animation")
    shutil.copyfile(output / "Rig.json", asset / "Rig.json")
    client.export, client.batch = export, batch
    original_search = client.search
    client.search = lambda query, kind, under: original_search(query, kind, under) + (
        [unrelated] if kind == "AnimSequence" else [])
    output = tmp_path / "with-morphs"
    pipeline.build(client, str(root), record, output, "Character01", lambda *_: None, threading.Event())
    rig = validate_rig(json.loads((output / "Rig.json").read_text()))
    catalog = json.loads((output / "Animation/manifest.json").read_text())
    assert [entry["id"] for entry in catalog["clips"]] == ["Clip01"]
    metadata, data = read_clip(str(output / "Animation"), catalog["clips"][0], rig)
    if curve_mode != "supported":
        assert "morphs" not in metadata
        assert data == (tmp_path / "before/Animation/Clip01.bin").read_bytes()
        return
    assert metadata["morphs"]["names"] == ["Shape01"]
    assert struct.unpack("<2f", data[2 * 2 * 28:]) == (0, 1)
    morphs = read_morphs(str(output / "Animation"), metadata["morphs"])
    assert struct.unpack("<I6f", morphs) == (0, 0, 0, 2, 0, .5, 0)
    assert metadata["morphs"]["components"][0]["vertex_sha256"] == hashlib.sha256((geometry / "Component 0.vb").read_bytes()).hexdigest()


@pytest.mark.parametrize("mode,expected", [("RCIM_Linear", .5), ("RCIM_Constant", 0), ("RCIM_Cubic", .75)])
def test_morph_curves_sample_authored_interpolation(mode, expected):
    from app.assets.game_extract.morphs import sample_curves

    keys = [dict(Time=time, Value=time, ArriveTangent=0, LeaveTangent=2 if time == 0 else 0,
                 InterpMode=mode, TangentWeightMode="RCTWM_WeightedNone") for time in (0, 1)]
    sequence = dict(Properties=dict(NumFrames=3, SequenceLength=1), CompressedCurveNames=[dict(DisplayName="Shape01")],
                    CompressedCurveData=dict(FloatCurves=[dict(FloatCurve=dict(Keys=keys))]))
    result = sample_curves(sequence, {"Shape01"})
    assert struct.unpack("<3f", result["data"]) == pytest.approx((0, expected, 1))
    keys[0]["TangentWeightMode"] = "RCTWM_WeightedBoth"
    assert sample_curves(sequence, {"Shape01"}) is None
    keys[0]["TangentWeightMode"] = "RCTWM_WeightedNone"
    keys[0]["InterpMode"] = "RCIM_Unknown"
    assert sample_curves(sequence, {"Shape01"}) is None
    # Other supported targets survive an unsupported optional curve.
    sequence["CompressedCurveNames"].append(dict(DisplayName="Shape02"))
    sequence["CompressedCurveData"]["FloatCurves"].append(dict(FloatCurve=dict(Keys=[
        {**key, "InterpMode": mode} for key in keys])))
    assert sample_curves(sequence, {"Shape01", "Shape02"}) == {
        "names": ["Shape02"], "data": result["data"]}
    keys[0]["Value"] = float("nan")
    with pytest.raises(ValueError, match="Invalid morph curve"):
        sample_curves(sequence, {"Shape01"})


def test_character_ribbon_layers_pair_with_shared_clips_by_name_and_exact_timing(tmp_path):
    from .test_shared_animations import write_library

    root, _, record, client, _ = fixture(tmp_path)
    # The shared library animates only the root; the character's Secondary01 chain is its own.
    library = write_library(root, "BodyType01-BaseAnim-0123456789", label="BaseAnim",
                            bones=[dict(name="Root", parent=-1, translation=[0, 0, 0], rotation=[0, 0, 0, 1],
                                        scale=[1, 1, 1])],
                            clips={"Walk01": [0], "Run01": [0]})
    walk, run = CHARACTER + "/Skin01/RibbonAnim/Rib_Walk01", CHARACTER + "/Skin01/RibbonAnim3/Rib_Run01"
    layer = dict(Skeleton=dict(ObjectPath=SKELETON + ".0"), AdditiveAnimType="AAT_LocalSpaceBase",
                 RefPoseType="ABPT_RefPose", TrackToSkeletonMapTable=[dict(BoneTreeIndex=1)])
    extra = {walk: dict(NumFrames=2, SequenceLength=1), run: dict(NumFrames=3, SequenceLength=2)}
    bones = [dict(name="Root", parent=-1, translation=[0, 0, 0], rotation=[0, 0, 0, 1]),
             dict(name="Secondary01", parent=0, translation=[1, 2, 3], rotation=[0, 0, 0, 1])]
    rows = [(0, 0, 0, 0, 0, 0, 1), (1, 2, 3, 0, 0, 1, 0)] * 2
    search, batch = client.search, client.batch
    client.search = lambda query, kind, under: search(query, kind, under) + (sorted(extra) if kind == "AnimSequence" else [])

    def layered_batch(kind, packages, destination, format=None):
        result = batch(kind, [package for package in packages if package not in extra], destination, format)
        for package in packages:
            if package in extra:
                file = destination / (package.rsplit("/", 1)[-1] + (".json" if kind == "json" else ".psa"))
                file.write_bytes(json.dumps([dict(Type="AnimSequence", Name=package.rsplit("/", 1)[-1],
                                                  Properties={**layer, **extra[package]})]).encode()
                                 if kind == "json" else psa(bones, rows))
                result[package] = dict(state="completed", rootFiles=[str(file)], failures=[])
        return result

    client.batch = layered_batch
    output = tmp_path / "output"
    report, _ = pipeline.build(client, str(root), record, output, "", lambda *_: None, threading.Event())
    manifest = json.loads((output / "Animation/manifest.json").read_text())
    # Rib_Walk01 matches Walk01 exactly; Rib_Run01's frame count differs, so Run01 keeps physics only.
    [entry] = manifest["shared_layers"]
    assert report["shared_layers"] == 1 and entry["clip"] == "Walk01" and entry["source"] == walk
    assert entry["library"] == library.name and entry["fingerprint"] == "build-1"
    assert manifest["shared_layer_masks"][entry["mask"]] == [1] and 1 in manifest["layer_bones"]
    data = (output / "Animation" / entry["file"]).read_bytes()
    assert hashlib.sha256(data).hexdigest() == entry["sha256"]
    assert list(struct.iter_unpack("<7f", data)) == [(1, 2, 3, 0, 0, 1, 0)] * 2


@pytest.mark.parametrize("scenario", ["blueprint", "untimed", "effect", "unavailable"])
def test_wuwa_props_need_an_attachment_and_clip_timing(tmp_path, monkeypatch, scenario):
    from app.assets import animations

    root, asset, record, client, _ = fixture(tmp_path)
    weapon_root = "Client/Content/Aki/Character/Weapon/Type01/R5Weapon01/"
    weapon = weapon_root + "Model/R5Weapon01"
    prop_root = "Client/Content/Aki/Character/Weapon/Prop/Role/Prop01/"
    prop, prop_skeleton, prop_clip = prop_root + "Model/Prop01", prop_root + "Model/Prop01_Skeleton", prop_root + "BaseAnim/Clip01_Prop01"
    # An effect mesh plays an animation from a sibling folder, on a skeleton with the same bones.
    book_root = "Client/Content/Aki/Character/Weapon/Prop/Role/Prop02/"
    sibling_root = "Client/Content/Aki/Character/Weapon/Prop/Role/Prop03/"
    book, book_animation = book_root + "Model/Book01", sibling_root + "BaseAnim/Action01"
    effect_root = "Client/Content/Aki/Effect/"
    book_model = effect_root + "DataAsset/DA_Mesh_Book01"
    loose_group, loose_model = effect_root + "EffectGroup/Character01/DA_Group_Pet01", effect_root + "DataAsset/DA_Mesh_Pet01"
    claim_group, claim_model = effect_root + "EffectGroup/Character01/DA_Group_Prop01", effect_root + "DataAsset/DA_Mesh_Prop01"
    particles = effect_root + "EffectGroup/Character01/DA_Group_Sparks01"
    blueprint, base_blueprint = CHARACTER + "/BP_Character01", CHARACTER + "/BP_Base_Character01"
    body_material, prop_material = CHARACTER + "/Skin01/Model/Material01", prop_root + "Model/Material02"
    body_texture, prop_texture = CHARACTER + "/Skin01/Model/Texture01", prop_root + "Model/Texture02"
    # BC7 4x4 top mips: the captured diffuse matches the body's native texture byte for byte.
    pixels = {body_texture: bytes(range(16)), prop_texture: bytes(range(16, 32))}
    geometry = asset / "11111111"
    header = bytearray(148)
    header[:4], header[84:88] = b"DDS ", b"DX10"
    (geometry / "Component 0 t=aaaaaaaa.dds").write_bytes(bytes(header) + pixels[body_texture])
    metadata = json.loads((geometry / "Metadata.json").read_text())
    metadata["components"][0]["texture_hashes"] = [[["Diffuse", ".dds", "aaaaaaaa", "Component 0 t=aaaaaaaa.dds"]]]
    (geometry / "Metadata.json").write_text(json.dumps(metadata))

    def game(package, suffix=""):
        return "/Game/" + package.removeprefix("Client/Content/") + "." + package.rsplit("/", 1)[-1] + suffix

    def row(mesh, description, sockets=(), bp=None):
        return {"网格体_1_" + "A" * 32: dict(AssetPathName=game(mesh)), "描述_2_" + "B" * 32: description,
                "BattleSockets_3_" + "C" * 32: list(sockets), "蓝图_4_" + "D" * 32: dict(AssetPathName=game(bp, "_C") if bp else "None")}

    # The character's signature weapon: a model row that must never be exported.
    rows = {"21010016": row(weapon, "5星-Character01专武")}
    if scenario != "unavailable":
        rows["100001"] = row(MESH, "Character01", ["Secondary01", "Root"], blueprint)

    def uemodel(name, package, bones, bone):
        level = attributes(dict(
            VERTICES=struct.pack("<i9f", 3, 0, 0, 0, 1, 0, 0, 0, 0, 1),
            NORMALS=struct.pack("<i12f", 3, *[1, 0, 1, 0] * 3),
            INDICES=struct.pack("<i3i", 3, 0, 1, 2),
            TEXCOORDS=struct.pack("<i", 1) + string("UV0") + struct.pack("<i6f", 3, 0, 0, 1, 0, 0, 1),
            MATERIALS=struct.pack("<i", 1) + string("Material02") + string("Material02") + struct.pack("<ii", 0, 1),
            WEIGHTS=struct.pack("<i", 3) + b"".join(struct.pack("<Hif", bone, vertex, 1) for vertex in range(3))))
        skeleton = struct.pack("<i", len(bones)) + b"".join(
            string(item["name"]) + struct.pack("<i3f4f3f", item["parent"], *item["translation"], *item["rotation"], *item["scale"])
            for item in bones)
        return b"UEFORMAT" + string("UEMODEL") + b"\x0a" + string(name) + string(package) + b"\x00" + attributes(dict(
            LODS=struct.pack("<i", 1) + string("LOD0") + level, SKELETON=attributes(dict(BONES=skeleton))))

    identity = dict(rotation=[0, 0, 0, 1], scale=[1, 1, 1])
    weapon_bones = [dict(name="Bone01", parent=-1, translation=[0, 0, 0], **identity)]
    prop_bones = [dict(name="Root", parent=-1, translation=[0, 0, 0], **identity),
                  dict(name="Blade01", parent=0, translation=[0, 0, 1], **identity)]
    meshes = {weapon: uemodel("R5Weapon01", weapon, weapon_bones, 0), prop: uemodel("Prop01", prop, prop_bones, 1),
              book: uemodel("Book01", book, prop_bones, 1)}

    def mesh_document(material, skeleton=SKELETON):
        return [dict(Type="SkeletalMesh", Properties=dict(Skeleton=dict(ObjectPath=skeleton + ".0")),
                     SkeletalMaterials=[dict(Material=dict(ObjectPath=material + ".0"))],
                     LODModels=[dict(Sections=[dict(MaterialIndex=0, BaseIndex=0)])])]

    template = "OtherCase01_GEN_VARIABLE"
    documents = {wuwa_props.MODEL_CONFIG: [dict(Type="CompositeDataTable", Rows=rows)], MESH: mesh_document(body_material),
                 prop: mesh_document(prop_material, prop_skeleton),
                 # The base blueprint attaches the component to a socket with a float-noise offset; the derived one sets its mesh.
                 base_blueprint: [dict(Type="BlueprintGeneratedClass", Name="BP_Base_Character01_C"),
                                  dict(Type="SCS_Node", Name="SCS_Node_1", Properties=dict(
                                      InternalVariableName="OtherCase01", AttachToName="Secondary01",
                                      ParentComponentOrVariableName="CharacterMesh0")),
                                  dict(Type="SkeletalMeshComponent", Name=template, Properties=dict(
                                      bHideLogicallyOnBeginPlay=True, RelativeLocation=dict(X=-2e-5, Y=1e-5, Z=0.0),
                                      RelativeRotation=dict(Pitch=-6e-5, Yaw=6e-5, Roll=4e-5)))],
                 blueprint: [dict(Type="BlueprintGeneratedClass", Name="BP_Character01_C",
                                  SuperStruct=dict(ObjectPath=base_blueprint + ".1")),
                             dict(Type="SkeletalMeshComponent", Name=template, Properties=dict(
                                 SkeletalMesh=dict(ObjectPath=prop + ".0")))],
                 book: mesh_document(prop_material, book_root + "Model/Book01_Skeleton")}

    def effect_group(model, kind="EffectModelSkeletalMesh_C"):
        key = kind + "'" + model + "." + model.rsplit("/", 1)[-1] + "'"
        return [dict(Type="EffectModelGroup_C", Properties=dict(EffectData=[dict(Key=key, Value=0.0)], StartTime=0.0, LoopTime=1.0))]

    def effect_model(mesh, animation=book_animation):
        return [dict(Type="EffectModelSkeletalMesh_C", Properties=dict(
            SkeletalMeshRef=dict(ObjectPath=mesh + ".0"), AnimationRef=dict(ObjectPath=animation + ".1"),
            Looping=False, StartTime=0.0, LoopTime=1.0))]

    def notify(name, start, duration):
        return dict(NotifyName="AnimNotifyStateEffect_C", LinkValue=start, Duration=duration, LinkMethod="EAnimLinkMethod::Absolute",
                    NotifyStateClass=dict(ObjectName="AnimNotifyStateEffect_C'Clip01:" + name + "'", ObjectPath=BASE + ".1"))

    def effect(name, group, **settings):
        return dict(Type="AnimNotifyStateEffect_C", Name=name, Properties=dict(
            EffectDataAssetRef=dict(AssetPathName=game(group)), SyncEventTimeToEffectTime=True, **settings))

    if scenario == "effect":
        # Clip01 spawns the book's mesh model directly on a socket for its middle half, a pet
        # on no socket while its root moves, particles with an offset, and the blueprint
        # prop's own sequence on its mesh at Root through a model the viewer cannot
        # reproduce: that effect still plays the sequence instead of the component.
        documents[BASE] = [dict(Type="AnimSequence", Name="Clip01", Properties=dict(
                               Skeleton=dict(ObjectPath=SKELETON + ".0"), NumFrames=2, SequenceLength=1,
                               TrackToSkeletonMapTable=[dict(BoneTreeIndex=0)],
                               Notifies=[notify("AnimNotifyStateEffect_C_" + str(index), *timing)
                                         for index, timing in enumerate([(0.25, 0.5), (0.5, 0.5), (0, 1), (0, 1)])])),
                           effect("AnimNotifyStateEffect_C_0", book_model, SocketName="Secondary01", EffectSlotName="Slot01"),
                           effect("AnimNotifyStateEffect_C_1", loose_group),
                           effect("AnimNotifyStateEffect_C_2", particles, SocketName="Root", Location=dict(X=1.0, Y=0.0, Z=0.0)),
                           effect("AnimNotifyStateEffect_C_3", claim_group, SocketName="Root")]
        documents.update({loose_group: effect_group(loose_model),
                          claim_group: effect_group(claim_model),
                          particles: effect_group(effect_root + "DataAsset/DA_Sparks01", "EffectModelNiagara_C"),
                          book_model: effect_model(book), loose_model: effect_model(prop),
                          claim_model: [dict(effect_model(prop, prop_clip)[0], Properties={
                              **effect_model(prop, prop_clip)[0]["Properties"], "HideFrames": 2})]})
    for material, texture in ((body_material, body_texture), (prop_material, prop_texture)):
        documents[material] = [dict(Properties=dict(TextureParameterValues=[
            dict(ParameterInfo=dict(Name="MainTex"), ParameterValue=dict(ObjectPath=texture + ".0"))]))]
    original_export, original_batch, original_search, tiers = client.export, client.batch, client.search, []
    exported_meshes = []

    def write(destination, name, data):
        destination.mkdir(parents=True, exist_ok=True)
        file = destination / name
        file.write_bytes(data)
        return file

    def export(kind, package, destination, format=None):
        if kind == "mesh":
            exported_meshes.append(package)
        if kind == "mesh" and package in meshes:
            return [write(destination, package.rsplit("/", 1)[-1] + ".uemodel", meshes[package])]
        if kind == "json" and package in documents:
            return [write(destination, package.rsplit("/", 1)[-1] + ".json",
                          json.dumps(documents[package], ensure_ascii=False).encode("utf-8"))]
        return original_export(kind, package, destination, format)

    # Prop animations: the blueprint prop's clip, timed like Clip01, and the effect mesh's
    # shorter one; Blade01 moves one unit along x by their end.
    prop_animations = {prop_clip: ("Clip01_Prop01", prop_skeleton, 1),
                       book_animation: ("Action01", sibling_root + "Model/Prop03_Skeleton", 0.25)}
    motion = [(0, 0, 0, 0, 0, 0, 1), (0, 0, 1, 0, 0, 0, 1), (0, 0, 0, 0, 0, 0, 1), (1, 0, 1, 0, 0, 0, 1)]

    def batch(kind, packages, destination, format=None):
        result, rest = {}, []
        for package in packages:
            name = package.rsplit("/", 1)[-1]
            if kind == "json" and package in documents:
                data = json.dumps(documents[package], ensure_ascii=False).encode("utf-8")
            elif package in prop_animations:
                name, skeleton, length = prop_animations[package]
                data = psa(prop_bones, motion, length) if kind != "json" else json.dumps([dict(
                    Type="AnimSequence", Name=name, Properties=dict(
                        Skeleton=dict(ObjectPath=skeleton + ".0"), NumFrames=2, SequenceLength=length))]).encode()
            else:
                rest.append(package)
                continue
            file = write(destination, name + (".json" if kind == "json" else ".psa"), data)
            result[package] = dict(state="completed", rootFiles=[str(file)], failures=[])
        return {**(original_batch(kind, rest, destination, format) if rest else {}), **result}

    def search(query, kind, under, *, recursive=True):
        if under.startswith(prop_root.rstrip("/")):
            return [prop_clip] if kind == "AnimSequence" and scenario != "untimed" else []
        return original_search(query, kind, under)

    def texture(package, destination, tier):
        tiers.append(tier)
        file = write(destination, package.rsplit("/", 1)[-1] + ".mip", pixels[package] if tier == "HD" else bytes(16))
        return file, dict(width=4, height=4, format="PF_BC7", srgb=True)

    client.export, client.batch, client.search, client.texture = export, batch, search, texture
    output = tmp_path / "output"
    report, _ = pipeline.build(client, str(root), record, output, "Character01", lambda *_: None, threading.Event())
    manifest = json.loads((output / "Animation/manifest.json").read_text())
    assert [entry["id"] for entry in manifest["clips"]] == ["Clip01"]
    # The regular weapon is never read or reported, in any scenario.
    assert "weapon" not in report and weapon not in exported_meshes
    if scenario == "unavailable":
        # Props never block clips.
        assert "props" not in manifest and report["props"] == []
        assert report["prop_error"] == "No game model configuration uses the verified mesh."
        return
    if scenario == "untimed":
        # A blueprint prop without a paired clip is reported and never exported.
        assert "props" not in manifest and report["props"] == []
        assert "prop_error" not in report
        assert report["skipped_props"] == [dict(kind="blueprint", source=prop, reason="No clip has a paired animation for the prop.")]
        assert tiers == []
        return
    assert tiers == ["HD", "HD"]
    rig = validate_rig(json.loads((output / "Rig.json").read_text()))
    library = str(output / "Animation")
    props = animations._props(manifest)
    shutil.copytree(output, asset, dirs_exist_ok=True)
    published = []
    monkeypatch.setattr("app.assets.animations.server.publish_geometry",
                        lambda blob, **_: published.append(blob) or f"/geometry/{len(published)}")
    monkeypatch.setattr("app.assets.animations.server.release_geometry", lambda url: None)
    sessions = animations.AnimationSessions()
    if scenario == "effect":
        # The component loses the sequence its clip's unsupported effect plays; that effect
        # and the socketless pet (while the root moves) are reported; the particles spawn
        # no mesh and are not props.
        assert report["skipped_props"] == [
            dict(kind="blueprint", source=prop, reason="No clip has a paired animation for the prop."),
            dict(kind="effect", source=loose_model, clips=["Clip01"], reason="The clip moves its root when the effect spawns."),
            dict(kind="effect", source=claim_model, clips=["Clip01"],
                 reason="The effect mesh model has settings the viewer does not support: HideFrames.")]
        [effect_prop] = props.values()
        assert effect_prop["name"] == "Book01" and effect_prop["socket"] == 1 and effect_prop["bone_count"] == 2
        # The book's own animation plays from its window's start, which the track records.
        track = effect_prop["tracks"]["Clip01"]
        assert {key: track[key] for key in ("frame_count", "duration", "start", "end")} == dict(
            frame_count=2, duration=0.25, start=0.25, end=0.75)
        values = np.frombuffer((output / "Animation" / track["file"]).read_bytes(), "<f4").reshape(2, 2, 7)
        np.testing.assert_allclose(values[1, 1], [1, 0, 0, 0, 0, 0, 1], atol=1e-6)
        catalog = sessions.catalog(str(root), "Character01", rig, lambda: True)
        [served] = sessions.load_props(catalog["animation_session"])["props"]
        assert served["tracks"]["clips"] == {"Clip01": dict(offset=0, frame_count=2, duration=0.25, start=0.25, end=0.75)}
        sessions.clear()
        return
    assert report["skipped_props"] == []
    [blueprint_prop] = props.values()
    assert blueprint_prop["name"] == "Prop01"
    # The blueprint prop keeps its own skin, placed at its socket's rest transform.
    data = animations._read_prop(library, blueprint_prop, len(rig["bones"]))
    assert blueprint_prop["socket"] == 1 and blueprint_prop["bone_count"] == 2
    assert np.frombuffer(data, "<f4", count=24).reshape(3, 8)[:, :3].tolist() == [[1, 2, 3], [2, 2, 3], [1, 2, 4]]
    assert np.frombuffer(data, "<u2", count=12, offset=96).tolist() == [1, 0, 0, 0] * 3
    # The learned MainTex slot feeds the prop's own diffuse texture, written unchanged as sRGB BC7.
    dds = (output / "Animation" / blueprint_prop["textures"]["diffuse"]["file"]).read_bytes()
    assert dds[148:] == pixels[prop_texture] and struct.unpack_from("<I", dds, 128)[0] == 99
    # Its paired clip becomes per-frame socket-relative bone transforms.
    track = blueprint_prop["tracks"]["Clip01"]
    assert set(blueprint_prop["tracks"]) == {"Clip01"} and track["frame_count"] == 2 and track["duration"] == 1
    values = np.frombuffer((output / "Animation" / track["file"]).read_bytes(), "<f4").reshape(2, 2, 7)
    np.testing.assert_allclose(values, [[[0, 0, 0, 0, 0, 0, 1]] * 2, [[0, 0, 0, 0, 0, 0, 1], [1, 0, 0, 0, 0, 0, 1]]], atol=1e-6)
    # Serving publishes the geometry and one track blob for the session's clips.
    catalog = sessions.catalog(str(root), "Character01", rig, lambda: True)
    [served] = sessions.load_props(catalog["animation_session"])["props"]
    assert served["socket"] == 1 and served["bone_count"] == 2
    assert served["tracks"]["clips"] == {"Clip01": dict(offset=0, frame_count=2, duration=1, start=0, end=1)}
    assert published[-1] == values.tobytes() and served["tracks"]["length"] == len(values.tobytes())
    sessions.clear()


@pytest.mark.parametrize("native,expected", [
    # Byte-identical, or the same image re-encoded within a few levels, proves the role.
    ("same", {"MainTex": "diffuse"}),
    ("near", {"MainTex": "diffuse"}),
    # A different image, or another size, proves nothing.
    ("far", {}),
    ("small", {}),
    # A byte-identical parameter is not contested by a near one of the same part.
    ("both", {"MainTex": "diffuse"}),
    # Sections may name their diffuse differently: each parameter proves the role.
    ("sections", {"MainTex": "diffuse", "BaseCol": "diffuse"}),
])
def test_wuwa_texture_roles_need_matching_captured_pixels(native, expected):
    base = bytes((index * 7) % 200 + 20 for index in range(64))
    variants = {
        "same": (4, 4, base),
        "near": (4, 4, bytes(value + 3 for value in base)),
        "far": (4, 4, bytes((value + 60) % 256 for value in base[:32]) + base[32:]),
        "small": (2, 2, base[:16]),
    }

    def texture(variant):
        width, height, data = variants[variant]
        return dict(width=width, height=height, format="PF_R8G8B8A8", data=data)

    picks = {"MainTex": "same", "OtherTex": "near"} if native == "both" else         {"MainTex": "same", "BaseCol": "same"} if native == "sections" else {"MainTex": native}
    parameters = {name: f"Texture{index:02d}" for index, name in enumerate(picks, 1)}
    textures = {parameters[name]: texture(variant) for name, variant in picks.items()}
    payloads = {hashlib.sha256(base).hexdigest(): ("diffuse", unity_dds.wrap(4, 4, 28, base))}
    parts = [(payloads, {name: parameters[name]}) for name in parameters] if native == "sections"         else [(payloads, parameters)]

    class Reader:
        def read(self, tier, package):
            return textures.get(package)

    assert wuwa_props._learn(Reader(), "HD", parts) == expected


@pytest.mark.parametrize("parameters,expected", [
    # A prop material's proven parameter feeds its role; unproven parameters feed nothing.
    ({"MainTex": "Texture01", "Ramp": "Texture02"}, {"diffuse": "Texture01"}),
    # Two proven parameters naming one texture agree; naming two textures, the role is left out.
    ({"MainTex": "Texture01", "BaseCol": "Texture01"}, {"diffuse": "Texture01"}),
    ({"MainTex": "Texture01", "BaseCol": "Texture02", "LightTex": "Texture03"}, {"light_map": "Texture03"}),
])
def test_wuwa_prop_roles_are_never_guessed_between_textures(parameters, expected):
    slots = {"MainTex": "diffuse", "BaseCol": "diffuse", "LightTex": "light_map"}
    assert wuwa_props._role_textures(slots, parameters) == expected


MODEL01 = "Client/Content/Aki/Effect/DataAsset/DA_Mesh01"
ABSOLUTE = "EAnimLinkMethod::Absolute"
MESH_KEY = f"EffectModelSkeletalMesh_C'{MODEL01}.DA_Mesh01'"
REFS = dict(SkeletalMeshRef=dict(ObjectPath=MESH + ".0"), AnimationRef=dict(ObjectPath=BASE + ".1"))
def _scale(*keys, axes=("Curve", "Curve[1]", "Curve[2]"), mode="RCIM_Linear"):
    """An effect model's per-axis Scale curves with (time, value) keys of one interpolation."""
    return {axis: dict(EditorCurveData=dict(Keys=[dict(InterpMode=mode, Time=time, Value=value)
                                                   for time, value in keys])) for axis in axes}


@pytest.mark.parametrize("check,value,expected", [
    # Notify windows start inside the clip, linked absolutely.
    ("window", dict(start=0.25, duration=0.5, link=ABSOLUTE), (0.25, 0.75)),
    ("window", dict(start=0.5, duration=1, link=ABSOLUTE), (0.5, 1.5)),
    ("window", dict(start=1, duration=0.5, link=ABSOLUTE), None),
    ("window", dict(start=0, duration=0.5, link="EAnimLinkMethod::Relative"), None),
    # Groups list their mesh models with their play delays and the group's lifetime;
    # other effect assets spawn none.
    ("group", [dict(Type="EffectModelGroup_C", Properties=dict(StartTime=15.7, ContinuousLoop=True,
                                                                EffectData=[dict(Key=MESH_KEY, Value=0.5)]))],
     ([(MODEL01, 0.5)], (15.7, 0.0, 0.0), None)),
    ("group", [dict(Type="EffectModelNiagara_C", Properties={})], ([], None, None)),
    ("group", [dict(Type="EffectModelGroup_C", Properties=dict(Location=dict(bUseCurve=True), EffectData=[
        dict(Key=MESH_KEY, Value=0.0)]))],
     ([(MODEL01, 0.0)], (0.0, 0.0, 0.0), None)),
    ("group", None, None),
    # A notify may name one mesh model directly: it plays at spawn without a group level.
    ("group", [dict(Type="EffectModelSkeletalMesh_C", Properties=REFS)], ([(MODEL01, 0.0)], None, None)),
    # Mesh models play their named animation once; an unsupported model still names its
    # mesh and animation, which its clip's effect then claims.
    ("model", dict(REFS, StartTime=0.1, LoopTime=1, EndTime=1.2, ContinuousLoop=True, Looping=False),
     ((MESH, BASE), (0.1, 1.0, 1.2), None)),
    ("model", dict(REFS, Looping=True), ((MESH, BASE), None, "The effect mesh animation loops.")),
    # A mesh model's loop of exactly -1 has no time limit; other negative stages, and a group's, are rejected.
    ("model", dict(REFS, LoopTime=-1.0, EndTime=1.0), ((MESH, BASE), (0.0, -1.0, 1.0), None)),
    ("model", dict(REFS, LoopTime=-2.0), ((MESH, BASE), None, "The effect lifetime starts before its spawn.")),
    ("model", dict(REFS, StartTime=-1.0, LoopTime=-1.0), ((MESH, BASE), None, "The effect lifetime starts before its spawn.")),
    ("group", [dict(Type="EffectModelGroup_C", Properties=dict(LoopTime=-1.0, EffectData=[dict(Key=MESH_KEY, Value=0.0)]))],
     ([(MODEL01, 0.0)], None, "The effect lifetime starts before its spawn.")),
    ("model", dict(REFS, HideFrames=2),
     ((MESH, BASE), None, "The effect mesh model has settings the viewer does not support: HideFrames.")),
    # Scale curves only drive the mesh with bUseCurve, and cubic keys are not reproduced.
    ("model", dict(REFS, StartTime=40, Scale=_scale((0, 0), (0.015, 1))), ((MESH, BASE), (40.0, 0.0, 0.0), None)),
    ("model", dict(REFS, StartTime=1, Scale=dict(_scale((0, 0), (0.015, 1)), bUseCurve=True)),
     ((MESH, BASE), (1.0, 0.0, 0.0), None)),
    ("model", dict(REFS, StartTime=1, Scale=dict(_scale((0, 0), (0.015, 1), mode="RCIM_Cubic"), bUseCurve=True)),
     ((MESH, BASE), None, "The effect curve interpolation is unsupported.")),
    ("model", dict(REFS, HideFrames=0), ((MESH, BASE), (0.0, 0.0, 0.0), None)),
    ("model", dict(REFS, HideFrames=2, Scale=_scale((0, 0), (0.015, 1))),
     ((MESH, BASE), None, "The effect mesh model has settings the viewer does not support: HideFrames.")),
    ("model", dict(SkeletalMeshRef=dict(ObjectPath=MESH + ".0")),
     (None, None, "The effect mesh model names no character mesh and animation.")),
    # The mesh plays after its delay and hides when its group or model lifetime ends:
    # by itself without a loop, at the stop when the notify stops it at once, else after
    # playing out its lifetime from its time wrapped into the loop; never after the clip.
    ("visible", (dict(start=1, duration=4, faster_stop=True), 0.5, (0, 1, 0), (0, 1, 0)), (1.5, 5.0)),
    ("visible", (dict(start=1, duration=4, faster_stop=True), 0, (2, 0, 0), (0, 1, 0)), (1.0, 3.0)),
    ("visible", (dict(start=1, duration=4, faster_stop=True), 0, None, (0, 1, 0)), (1.0, 5.0)),
    ("visible", (dict(start=1, duration=4, faster_stop=True), 0, None, (2, 0, 0)), (1.0, 3.0)),
    ("visible", (dict(start=1, duration=4, faster_stop=False), 0, (0, 0.25, 0.4), (0, 1, 1)), (1.0, 5.65)),
    ("visible", (dict(start=1, duration=4.1, faster_stop=False), 0, (0, 1, 0.5), (0.5, 1, 0)), (1.0, 5.5)),
    ("visible", (dict(start=1, duration=8.5, faster_stop=False), 0, (0, 1, 2), (0, 1, 2)), (1.0, 10.0)),
    ("visible", (dict(start=1, duration=1, faster_stop=True), 1, (0, 1, 0), (0, 1, 0)), None),
    # An unbounded model loop lives until the notify stops it: at once, or after its end stage.
    ("visible", (dict(start=1, duration=4, faster_stop=True), 0, None, (0, -1, 1)), (1.0, 5.0)),
    ("visible", (dict(start=1, duration=4, faster_stop=False), 0, None, (0, -1, 1)), (1.0, 6.0)),
    ("visible", (dict(start=1, duration=8.5, faster_stop=False), 0, None, (0, -1, 2)), (1.0, 10.0)),
])
def test_effect_props_follow_the_game_effect_lifetimes(check, value, expected):
    from app.assets.game_extract import wuwa_effect_props

    def run():
        if check == "window":
            return wuwa_effect_props.window(value, 1)
        if check == "group":
            return wuwa_effect_props.mesh_models(value, MODEL01)
        if check == "model":
            return wuwa_effect_props.mesh_model([dict(Type="EffectModelSkeletalMesh_C", Properties=value)])
        notify, delay, group, model = value
        return wuwa_effect_props.visible(dict(notify, link=ABSOLUTE), 10, delay, group, model)

    if expected is None:
        with pytest.raises(ValueError):
            run()
    else:
        result = run()
        assert (pytest.approx(result) if check == "visible" else result) == expected


def _curve_keys(*keys, mode="RCIM_Linear"):
    """One transform axis curve with (time, value) keys of one interpolation."""
    return dict(EditorCurveData=dict(Keys=[dict(InterpMode=mode, Time=time, Value=value) for time, value in keys]))


@pytest.mark.parametrize("check,value,expected", [
    # Handled and ignored settings never block; blocking ones block unless neutral; any other is unverified.
    ("classify", ("notify", dict(Location=dict(X=1), exportIndex=3)), ([], [])),
    ("classify", ("mesh model", dict(bForceLod0=True, NativeClass="Cls", CurrentTimeLength=1.0)), ([], [])),
    ("classify", ("notify", dict(NotRealAttach=True, AttachLocationTime=0.5)), (["AttachLocationTime", "NotRealAttach"], [])),
    ("classify", ("notify", dict(NotRealAttach=False, AttachLocationTime=0, PlayNeedTags=[])), ([], [])),
    ("classify", ("mesh model", dict(HideFrames=0)), ([], [])),
    ("classify", ("mesh model", dict(HideFrames=2, Foo=1)), (["HideFrames"], ["Foo"])),
    ("classify", ("group", dict(Mystery=None)), ([], ["Mystery"])),
    ("classify", ("notify", {"(unreadable)": None}), (["(unreadable)"], [])),
    ("classify", ("notify", "unreadable"), (["(unreadable)"], [])),
    # Notify offsets: Unreal to asset space negates Y; Rotation is Pitch, Yaw, Roll in degrees.
    ("offset", (dict(Location=dict(X=1, Y=2, Z=3)), (0, 0, 0)), [1, -2, 3]),
    ("offset", (dict(Rotation=dict(Yaw=90)), (1, 0, 0)), [0, -1, 0]),
    ("offset", (dict(Rotation=dict(Pitch=90)), (1, 0, 0)), [0, 0, 1]),
    ("offset", (dict(Scale=dict(X=2, Y=2, Z=2)), (1, 1, 0)), [2, 2, 0]),
    ("offset", (dict(Scale=dict(X=0, Y=1, Z=1)), (1, 0, 0)), None),
    ("offset", (dict(Location=5), (0, 0, 0)), None),
    # Group and model transforms: Constant unless bUseCurve, then curves per axis (held outside their keys).
    ("transform", (dict(Location=dict(Constant=dict(X=1, Y=0, Z=0), Curve=_curve_keys((0, 5), (1, 9)))), [0.5], 3),
     [[1, 0, 0]]),
    ("transform", (dict(Location=dict(bUseCurve=True, Curve=_curve_keys((0, 0), (1, 2)))), [0.5], 3), [[1, 0, 0]]),
    ("transform", (dict(Location=dict(bUseCurve=True, Curve=_curve_keys((0, 0), (1, 2), mode="RCIM_Constant"))),
                   [0.5, 1, 2], 3), [[0, 0, 0], [2, 0, 0], [2, 0, 0]]),
    ("transform", (dict(Location=dict(bUseCurve=True, Curve=_curve_keys((0, 0), (1, 2)))), [-1, 5], 3),
     [[0, 0, 0], [2, 0, 0]]),
    # Rotation axes are Roll, Pitch and Yaw: Z 90 is the notify's Yaw 90.
    ("transform", (dict(Rotation=dict(Constant=dict(Z=90))), [0], 0), [[0, -1, 0]]),
    ("transform", (dict(Location=dict(bUseCurve=True, Curve=_curve_keys((0, 0), (1, 2), mode="RCIM_Cubic"))), [0.5], 3), None),
    ("transform", (dict(Location=dict(Constant=dict(X=0, Y=0, Z=0), Foo=1)), [0], 3), None),
    # A group's curve runs from its own start, so the mesh's delay shifts it.
    ("combined", ({}, dict(Location=dict(bUseCurve=True, Curve=_curve_keys((0, 0), (2, 2)))), {}, 1.0, [0.0]), [[1, 0, 0]]),
    ("combined", ({}, {}, {}, 0.0, [0.0]), "none"),
])
def test_effect_settings_are_classified_and_offsets_reproduced(check, value, expected):
    from app.assets.game_extract import wuwa_effect_settings as policy

    def run():
        if check == "classify":
            return policy.classify(*value)
        if check == "offset":
            properties, point = value
            return (policy.notify_offset(properties) @ [*point, 1])[:3].round(9).tolist()
        if check == "transform":
            properties, moments, column = value
            return policy.level_transform(properties)(moments)[:, :3, column].round(9).tolist()
        notify, group, model, delay, moments = value
        sampler = policy.combined(notify, group, model, delay)
        return "none" if sampler is None else sampler(moments)[:, :3, 3].round(9).tolist()

    if expected is None:
        with pytest.raises(ValueError):
            run()
    else:
        assert run() == expected


def test_prop_scale_keys_hide_and_resize_props():
    from app.assets.animation_conversion import convert_clip
    from app.assets.game_extract import wuwa_blueprint_props

    class Writer:
        def __init__(self):
            self.lengths = []

        def write(self, data, track=False):
            digest = hashlib.sha256(data).hexdigest()
            self.lengths.append(len(data))
            return f"Props/{digest[:32]}.bin", digest

    bones = [dict(name="Root", parent=-1, translation=[0, 0, 0], rotation=[0, 0, 0, 1], scale=[1, 1, 1]),
             dict(name="Bone01", parent=0, translation=[1, 0, 0], rotation=[0, 0, 0, 1], scale=[1, 1, 1])]
    rig = dict(bones=bones)
    root, bone = (0, 0, 0, 0, 0, 0, 1), (1, 0, 0, 0, 0, 0, 1)
    rows = [root, bone, root, bone]
    # Bone01 hides at scale 0 on the first frame and shows at 0.9 on the second.
    scaled_psa = psa(bones, rows, 1, scales=[(1, 1, 1), (0, 0, 0), (1, 1, 1), (0.9, 0.9, 0.9)])
    plain_psa = psa(bones, rows, 1)

    frames, scaled_binary = convert_clip(scaled_psa, rig, 1, scales=True)
    records = list(struct.iter_unpack("<10f", scaled_binary))
    assert frames == 2 and len(records) == 4
    assert records[1][7:] == pytest.approx((0, 0, 0)) and records[3][7:] == pytest.approx((0.9, 0.9, 0.9))
    _frames, plain_binary = convert_clip(plain_psa, rig, 1, scales=True)
    assert [record[7:] for record in struct.iter_unpack("<10f", plain_binary)] == [(1, 1, 1)] * 4
    # Without scales, the output keeps the old 7-float records exactly.
    assert convert_clip(scaled_psa, rig, 1, scales=False)[1] == convert_clip(plain_psa, rig, 1)[1]

    affine = np.frombuffer(wuwa_blueprint_props.bake(scaled_binary, 2, bones, np.eye(4), affine=True), "<f4")
    assert affine.size == 2 * 2 * 12
    affine = affine.reshape(2, 2, 3, 4)
    assert np.allclose(affine[0, 1, :, :3], 0)
    assert affine[1, 1, :, :3] == pytest.approx(0.9 * np.eye(3))
    rigid = wuwa_blueprint_props.bake(plain_binary, 2, bones, np.eye(4))
    assert len(rigid) == 2 * 2 * 7 * 4

    writer = Writer()
    entries, failures, extra = wuwa_blueprint_props.write_tracks(
        writer, {"A": (2, scaled_binary, 1.0, None), "B": (2, plain_binary, 1.0, None)}, bones, np.eye(4))
    assert set(entries) == {"A", "B"} and failures == {}
    assert extra == {"track_layout": "prop-track-v2"}
    assert writer.lengths == [2 * 2 * 48, 2 * 2 * 48]

    writer = Writer()
    entries, failures, extra = wuwa_blueprint_props.write_tracks(
        writer, {"B": (2, plain_binary, 1.0, None)}, bones, np.eye(4))
    assert set(entries) == {"B"} and failures == {} and extra == {}
    assert writer.lengths == [2 * 2 * 28]

    # Keys are relative to a rest scale other than 1: a key of 1 draws the built size.
    shrunk = [bones[0], dict(bones[1], scale=[0.5, 0.5, 0.5])]
    relative_psa = psa(shrunk, rows, 1, scales=[(1, 1, 1), (1, 1, 1), (1, 1, 1), (0.5, 0.5, 0.5)])
    _frames, relative = convert_clip(relative_psa, dict(bones=shrunk), 1, scales=True)
    assert [record[7:] for record in struct.iter_unpack("<10f", relative)][1::2] == [(0.5,) * 3, (0.25,) * 3]
    drawn = np.frombuffer(wuwa_blueprint_props.bake(relative, 2, shrunk, np.eye(4), affine=True), "<f4").reshape(2, 2, 3, 4)
    assert drawn[0, 1, :, :3] == pytest.approx(np.eye(3)) and drawn[1, 1, :, :3] == pytest.approx(0.5 * np.eye(3))
    # Offsets move the whole pose: a z translation of 5 shifts Bone01's delta by 5 in z; a scaled
    # offset makes its track affine.
    shift = np.eye(4)
    shift[2, 3] = 5
    moved = np.frombuffer(wuwa_blueprint_props.bake(plain_binary, 2, bones, np.eye(4), affine=True,
                                                    offsets=np.stack([shift, shift])), "<f4").reshape(2, 2, 3, 4)
    base = np.frombuffer(wuwa_blueprint_props.bake(plain_binary, 2, bones, np.eye(4), affine=True),
                         "<f4").reshape(2, 2, 3, 4)
    np.testing.assert_allclose(moved[:, 1, :, 3] - base[:, 1, :, 3], [[0, 0, 5], [0, 0, 5]], atol=1e-6)
    writer = Writer()
    scaled_offset = np.stack([np.diag([2.0, 2.0, 2.0, 1.0])] * 2)
    entries, failures, extra = wuwa_blueprint_props.write_tracks(
        writer, {"C": (2, plain_binary, 1.0, scaled_offset)}, bones, np.eye(4))
    assert set(entries) == {"C"} and failures == {} and extra == {"track_layout": "prop-track-v2"}


@pytest.mark.parametrize("socket,attached,start,settings,expected", [
    # Without a socket: the mesh's origin, while the root rests at spawn.
    (None, True, 0, {}, ("fixed", "origin")),
    (None, True, 1.5, {}, "The clip moves its root when the effect spawns."),
    # Attached: follows the socket. Placed: the socket's world at spawn, even while it moves later.
    ("Bone01", True, 0, {}, ("socket", 1)),
    ("Bone01", False, 0, {}, ("fixed", "bone")),
    ("Bone01", False, 0.5, {}, "The effect spawns while its socket moves between frames."),
    ("Bone03", True, 0, {}, "The effect socket is not a unique bone of the verified rig."),
    # A blocking notify setting refuses the placement; an ignored one places normally.
    ("Bone01", True, 0, {"NotRealAttach": True}, "The effect notify has settings the viewer does not support: NotRealAttach."),
    ("Bone01", True, 0, {"bRestartWithReplay": True}, ("socket", 1)),
])
def test_effect_placement_reproduces_the_notify_attachment(tmp_path, socket, attached, start, settings, expected):
    from app.assets.game_extract import wuwa_effect_props

    rig = dict(bones=[dict(name="Root", parent=-1, translation=[0, 0, 0], rotation=[0, 0, 0, 1], scale=[1, 1, 1]),
                      dict(name="Bone01", parent=0, translation=[0, 0, 1], rotation=[0, 0, 0, 1], scale=[1, 1, 1]),
                      dict(name="Bone02", parent=0, translation=[0, 1, 0], rotation=[0, 0, 0, 1], scale=[1, 1, 1])])
    frames = np.zeros((4, 3, 7), "<f4")
    frames[..., 6] = 1
    frames[:, 1, 2] = [1, 2, 3, 4]  # Bone01 rises; Bone02 stays.
    frames[:, 2, 1] = 1
    frames[2:, 0, 0] = 2  # The root moves from the third frame.
    (tmp_path / "Clip01.bin").write_bytes(frames.tobytes())
    (tmp_path / "Clip01.json").write_text(json.dumps(dict(layout="frame-major-tq-f32le", frame_count=4, bone_count=3,
                                                          duration=3, data_file="Clip01.bin")))
    notify = dict(socket=socket, attached=attached, start=start, settings=settings)
    use = dict(clip="Clip01", notify=notify)
    poses = wuwa_effect_props._Poses(tmp_path, rig)
    try:
        kind, where = wuwa_effect_props.placement(use, ["Root", "Bone01", "Bone02"], poses)
    except ValueError as error:
        assert str(error) == expected
        return
    if expected[1] == "origin":
        assert kind == "fixed" and np.allclose(where, np.eye(4))
    elif expected[1] == "bone":
        assert kind == "fixed" and np.allclose(where[:3, 3], [0, 0, 1])
    else:
        assert (kind, where) == expected


@pytest.mark.parametrize("sequences,expected", [
    # Exact name, or the longest clip name followed by a suffix.
    ({"A": ("Attack01", 3, 2)}, {"Attack01": "A"}),
    ({"A": ("Attack01_End_Prop", 3, 2)}, {"Attack01_End": "A"}),
    # Same frame interval and no longer than the clip: the prop plays from the clip's start.
    ({"A": ("Attack01_Prop", 2, 1)}, {"Attack01": "A"}),
    ({"A": ("Attack01_Prop", 3, 1)}, {}),
    ({"A": ("Attack01_Prop", 4, 3)}, {}),
    # Two sequences for one clip are never guessed between.
    ({"A": ("Attack01_Prop", 3, 2), "B": ("Attack01_Other", 3, 2)}, {}),
    ({"A": ("Unrelated01", 3, 2)}, {}),
])
def test_prop_sequences_pair_with_clips_by_exact_name_and_frame_interval(sequences, expected):
    from app.assets.game_extract.wuwa_blueprint_props import pairs

    clips = {"Attack01": [("Attack01", 3, 2)], "Attack01_End": [("Attack01_End", 3, 2)]}
    assert pairs(sequences, clips) == expected


@pytest.mark.parametrize("preferred,expected", [
    # Left and right sequences of one name fit: only the component's own animation blueprint breaks the tie.
    (None, {}), (set(), {}), ({"R"}, {"Clip01": "R"}), ({"L"}, {"Clip01": "L"}), ({"L", "R"}, {}), ({"Other"}, {}),
])
def test_prop_animation_blueprint_only_breaks_ties_between_fitting_sequences(preferred, expected):
    from app.assets.game_extract.wuwa_blueprint_props import pairs

    clips = {"Clip01": [("Clip01", 3, 2)], "Clip02": [("Clip02", 3, 2)]}
    sequences = {"L": ("Clip01", 3, 2), "R": ("Clip01", 3, 2), "Only": ("Clip02", 3, 2)}
    # A clip with a single fitting sequence pairs whether or not the blueprint lists it.
    assert pairs(sequences, clips, preferred) == {**expected, "Clip02": "Only"}


def test_prop_components_choose_their_own_side_through_animation_blueprint_and_notify(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app.assets.game_extract import wuwa_blueprint_props as props

    root = "Client/Content/Aki/Character/"
    base, left, right = root + "Role/BP_Base", root + "Prop/Hand/ABP_Hand", root + "Prop/Hand/ABP_Hand_R"
    mesh = root + "Prop/Hand/Model/Hand"

    def sequence(side, name="Clip01"):
        return root + "Prop/Hand/CommonAnim/" + side + "/" + name

    def template(variable, abp):
        return [dict(Type="SCS_Node", Name="Node_" + variable, Properties=dict(
                    InternalVariableName=variable, AttachToName="Socket01", ParentComponentOrVariableName="CharacterMesh0")),
                dict(Type="SkeletalMeshComponent", Name=variable + "_GEN_VARIABLE", Properties=dict(
                    SkeletalMesh=dict(ObjectPath=mesh + ".0"), AnimClass=dict(ObjectPath=abp + ".4")))]

    def entry(key, clip_sequence):
        return {"Key": key, "Value": dict(ObjectName="AnimSequence'x'", ObjectPath=clip_sequence + ".1")}

    # Both sides play Clip01; only the right side plays Dodge (as Dodge_2 too, whose sequence name differs).
    documents = {base: template("Left", left) + template("Right", right),
                 left: [dict(Type="AnimBlueprint", Properties=dict(Maps=[entry("Clip01", sequence("L"))]))],
                 right: [dict(Type="AnimBlueprint", Properties=dict(Maps=[
                     entry("Clip01", sequence("R")), entry("Dodge", sequence("R", "Dodge")), entry("Dodge_2", sequence("R", "Dodge2"))]))]}

    def export(kind, package, destination):
        if package not in documents:
            raise ValueError("missing")
        file = tmp_path / (package.rsplit("/", 1)[-1] + ".json")
        file.write_text(json.dumps(documents[package]))
        return [file]

    client = SimpleNamespace(export=export, work=tmp_path)
    cancel = threading.Event()
    anims = {}
    assert props.components(client, {base}, cancel, anims) == {"Left": {(mesh, "Socket01")}, "Right": {(mesh, "Socket01")}}
    assert anims == {"Left": {left}, "Right": {right}}
    cache = {}
    maps = {"Left": props.animation_map(client, left, cache, cancel), "Right": props.animation_map(client, right, cache, cancel)}
    assert maps["Left"] == {"Clip01": {sequence("L")}}
    assert maps["Right"]["Dodge_2"] == {sequence("R", "Dodge2")}
    # An unreadable animation blueprint, or one without keyed sequence maps, says nothing.
    assert props.animation_map(client, root + "Prop/Hand/Missing", cache, cancel) is None
    documents[root + "Prop/Hand/Empty"] = [dict(Type="AnimBlueprint", Properties={})]
    assert props.animation_map(client, root + "Prop/Hand/Empty", cache, cancel) is None
    folders = {"Left": "Hand", "Right": "Hand"}
    assert props.ownership("Left", folders, {"Left": None, "Right": maps["Right"]}) is None
    assert props.ownership("Left", folders, maps)["foreign"] == {sequence("R"), sequence("R", "Dodge"), sequence("R", "Dodge2")}
    assert props.ownership("Right", folders, maps)["foreign"] == {sequence("L")}
    # A sibling in another folder owns nothing of this one.
    assert props.ownership("Left", {"Left": "Hand", "Right": "Other"}, maps)["foreign"] == set()

    sequences = {sequence("L"): ("Clip01", 3, 2), sequence("R"): ("Clip01", 3, 2),
                 sequence("R", "Dodge"): ("Dodge", 3, 2), sequence("R", "Dodge2"): ("Dodge2", 3, 2)}
    monkeypatch.setattr(props, "list_packages", lambda *_: list(sequences))
    monkeypatch.setattr(props, "sequence_items", lambda *_: {package: dict(Properties=dict(
        Skeleton=dict(ObjectPath=root + "Prop/Hand/Model/Skeleton01.0"), NumFrames=3, SequenceLength=2)) for package in sequences})
    monkeypatch.setattr(props, "prepare", lambda *args: (3, b"", 2.0))
    monkeypatch.setattr(props, "write_tracks", lambda writer, prepared, *_: (
        {package: dict(file=package, sha256="", frame_count=3, duration=2.0) for package in prepared}, {}, {}))
    writer = SimpleNamespace(skipped=[])
    clips = {name: [(name, 3, 2)] for name in ("Clip01", "Dodge", "Dodge_2")}

    def tracks(component, shown, siblings=maps):
        found, _ = props._tracks(client, dict(bones=[]), root + "Prop/Hand/Model/Skeleton01", "Folder", clips, None, writer, cancel,
                                 owner=props.ownership(component, folders, siblings), component=component, shown=shown)
        return {clip: track["file"] for clip, track in found.items()}

    dodges = {"Dodge": sequence("R", "Dodge"), "Dodge_2": sequence("R", "Dodge2")}
    # Each component takes its own side of a tie, and only for the clips whose notifies show it.
    assert tracks("Right", {"Clip01": {"Right"}}) == {"Clip01": sequence("R"), **dodges}
    assert tracks("Left", {"Clip01": {"Left"}}) == {"Clip01": sequence("L")}
    assert tracks("Left", {"Clip01": {"Right"}}) == {}
    # No notify at all: the tie stays unresolved, while sequences only this side plays pair by name or exact key.
    assert tracks("Right", {}) == dodges
    # The right side's sequences are never the left component's, even when they pair by name.
    assert tracks("Left", {}) == {}
    # A clip a sibling also lists by key needs the notify to name this component.
    rivals = {"Left": {**maps["Left"], "Dodge_2": frozenset({sequence("L")})}, "Right": maps["Right"]}
    assert tracks("Right", {}, rivals) == {"Dodge": sequence("R", "Dodge")}
    assert tracks("Right", {"Dodge_2": {"Right"}}, rivals) == dodges
    # Without an animation blueprint a component pairs by name alone, as before.
    assert tracks("Right", {}, {"Left": None, "Right": None}) == {"Dodge": sequence("R", "Dodge")}
    assert writer.skipped == []


def test_submesh_notifies_show_components_only_with_visible_flag():
    from app.assets.game_extract.pipeline import SUBMESH_VISIBLE, shown_components

    def notify(name):
        return dict(NotifyStateClass=dict(ObjectName="TsAnimNotifyStateSubMeshControl_C'Clip01:" + name + "'"))

    def state(name, mesh, visible):
        return dict(Type="TsAnimNotifyStateSubMeshControl_C", Name=name, Properties={"MeshName": mesh, SUBMESH_VISIBLE: visible})

    rows = [state("N_1", "OtherCase02", True), state("N_2", "OtherCase01", False), state("N_3", "OtherCase03", None)]
    row = dict(Properties=dict(Notifies=[notify("N_1"), notify("N_2"), notify("N_3"), notify("N_4"),
                                         dict(NotifyStateClass=dict(ObjectName="AnimNotifyStateEffect_C'Clip01:N_1'"))]))
    assert shown_components(row, rows) == ["OtherCase02"]
    assert shown_components(dict(Properties={}), rows) == []
    # A name exported twice is never guessed between.
    assert shown_components(row, rows + [state("N_1", "OtherCase04", True)]) == []

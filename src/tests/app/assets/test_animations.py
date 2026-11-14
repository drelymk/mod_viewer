import hashlib
import importlib.util
import json
from pathlib import Path
import struct
from copy import deepcopy

import pytest

from app.assets.animations import AnimationSessions
from app.assets.animation_conversion import reference_layer_timing
from app.assets.skinning import capture_skinning, decode_captured_skinning
from core.geometry.migoto_format import MigotoBinaryLayout, MigotoElement
from app.assets.index import build_index
from app.bridge.asset_preview import AssetPreview
from .test_loader import _fmt, _wwmi_triangle


def converter(name="convert_clip"):
    path = Path(__file__).resolve().parents[4] / "tools" / "convert_asset_animations.py"
    spec = importlib.util.spec_from_file_location("animation_converter", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, name)


def sample():
    bones = [dict(name="Root", parent=-1, translation=[0, 0, 0], rotation=[0, 0, 0, 1]),
             dict(name="Joint01", parent=0, translation=[1, 2, 3], rotation=[0, 0, 0, 1])]
    records = bytearray()
    for bone in bones:
        row = bytearray(120)
        row[:len(bone["name"])] = bone["name"].encode()
        struct.pack_into("<i", row, 72, bone["parent"])
        struct.pack_into("<4f", row, 76, *bone["rotation"])
        struct.pack_into("<3f", row, 92, *bone["translation"])
        records.extend(row)
    info = bytearray(168)
    struct.pack_into("<f", info, 152, 2)
    struct.pack_into("<i", info, 164, 2)
    keys = b"".join(struct.pack("<8f", frame, -2, 3, 0, 0, 0, -1 if bone == 0 else 1, 1)
                    for frame in range(2) for bone in range(2))
    def chunk(name, size, count, data):
        return struct.pack("<20siii", name, 0, size, count) + data
    data = (chunk(b"BONENAMES", 120, 2, records) + chunk(b"ANIMINFO", 168, 1, info)
            + chunk(b"ANIMKEYS", 32, 4, keys))
    return dict(id="a" * 64, bones=bones), data


def test_conversion_coordinates_timing_and_skeleton_contract():
    rig, source = sample()
    convert = converter()
    frames, data = convert(source, rig, 1)
    assert frames == 2
    assert list(struct.iter_unpack("<7f", data)) == [
        (0, 2, 3, 0, 0, 0, 1), (0, 2, 3, 0, 0, 0, 1),
        (1, 2, 3, 0, 0, 0, 1), (1, 2, 3, 0, 0, 0, 1)]
    with pytest.raises(ValueError, match="timing"):
        convert(source, rig, 2)
    rig["bones"][1]["translation"][0] = 10
    with pytest.raises(ValueError, match="reference skeleton"):
        convert(source, rig, 1)


@pytest.mark.parametrize("difference", ["rotation", "translation", "both"])
def test_verified_source_reference_preserves_authored_keys_and_uses_mesh_rest_for_omitted_tracks(difference):
    rig, source = sample()
    # Omitted native tracks contain their source reference transform in every frame.
    source = bytearray(source)
    for frame in range(2):
        struct.pack_into("<3f", source, len(source) - 128 + (frame * 2 + 1) * 32, 1, -2, 3)
    reference = deepcopy(rig)
    if difference in ("rotation", "both"):
        rig["bones"][1]["rotation"] = [0, 0, 2 ** -.5, 2 ** -.5]
    if difference in ("translation", "both"):
        rig["bones"][1]["translation"] = [4, 5, 6]
    convert = converter()
    with pytest.raises(ValueError, match="reference skeleton"):
        convert(source, rig, 1)
    _, original = convert(source, reference, 1)
    _, authored = convert(source, rig, 1, reference=reference, authored_bones=[0, 1])
    assert authored == original
    _, omitted = convert(source, rig, 1, reference=reference, authored_bones=[0])
    for frame in range(2):
        offset = (frame * 2 + 1) * 28
        assert omitted[offset - 28:offset] == original[offset - 28:offset]
        assert struct.unpack_from("<7f", omitted, offset) == pytest.approx(rig["bones"][1]["translation"] + rig["bones"][1]["rotation"])
    # A declared omission cannot conceal a non-reference animated rotation.
    moving = bytearray(source)
    struct.pack_into("<4f", moving, len(source) - 20, 0, 0, 1, 0)
    with pytest.raises(ValueError, match="undeclared"):
        convert(moving, rig, 1, reference=reference, authored_bones=[0])


@pytest.mark.parametrize("change", ["name", "parent", "translation", "scale", "nonfinite", "rotation", "header", "track_map"])
def test_source_reference_cannot_bypass_skeleton_validation(change):
    rig, source = sample()
    reference = deepcopy(rig)
    authored = [0, 1]
    bone = reference["bones"][1]
    if change == "name":
        bone["name"] = "Other01"
    elif change == "parent":
        bone["parent"] = -1
    elif change == "translation":
        bone["translation"][0] += 1
    elif change == "scale":
        bone["scale"] = [2, 2, 2]
    elif change == "nonfinite":
        bone["scale"] = [float("nan")] * 3
    elif change == "rotation":
        bone["rotation"] = [0, 0, 0, 0]
    elif change == "header":
        bone["rotation"] = [0, 0, 1, 0]
    elif change == "track_map":
        authored = [0, 2]
    with pytest.raises(ValueError):
        converter()(source, rig, 1, reference=reference, authored_bones=authored)


def test_reference_layer_preserves_body_and_refuses_existing_motion():
    rig, _ = sample()
    rows = [(0, 0, 0, 0, 0, 0, 1), (1, 2, 3, 0, 0, 0, 1),
            (2, 0, 0, 0, 0, 0, 1), (1, 2, 3, 0, 0, 0, 1)]
    base = b"".join(struct.pack("<7f", *row) for row in rows)
    layer_rows = [(99, 99, 99, 0, 0, 0, 1), (1, 2, 3, 0, 0, 0, 1),
                  (99, 99, 99, 0, 0, 0, 1), (1, 2, 3, 0, 0, 1, 0)]
    layer = b"".join(struct.pack("<7f", *row) for row in layer_rows)
    merge = converter("merge_reference_layer")
    result = list(struct.iter_unpack("<7f", merge(base, layer, rig, [1])))
    assert result == [rows[0], layer_rows[1], rows[2], layer_rows[3]]
    with pytest.raises(ValueError, match="already has base motion"):
        merge(base, layer, rig, [0])
    with pytest.raises(ValueError, match="tracks"):
        merge(base, layer[:-1], rig, [1])
    with pytest.raises(ValueError, match="tracks"):
        merge(base, layer, rig, [1, 1])
    subtle_motion = bytearray(base)
    struct.pack_into("<f", subtle_motion, 3 * 28, 1.00001)
    with pytest.raises(ValueError, match="already has base motion"):
        merge(subtle_motion, layer, rig, [1])


@pytest.mark.parametrize("layer_frames, mode", [(7, "exact"), (6, "hold-last"), (13, "trim-end")])
def test_reference_layer_aligns_endpoints_without_resampling_body_or_layer(layer_frames, mode):
    rig, _ = sample()
    frames, duration = 7, .2
    layer_duration = (layer_frames - 1) / 30
    body_rows = [(float(frame), 0, 0, 0, 0, 0, 1) if bone == 0 else (1, 2, 3, 0, 0, 0, 1)
                 for frame in range(frames) for bone in range(2)]
    layer_rows = [(99, 99, 99, 0, 0, 0, 1) if bone == 0 else (frame + 1, 2, 3, 0, 0, 0, 1)
                  for frame in range(layer_frames) for bone in range(2)]
    body = b"".join(struct.pack("<7f", *row) for row in body_rows)
    layer = b"".join(struct.pack("<7f", *row) for row in layer_rows)
    timing = reference_layer_timing(frames, duration, layer_frames, layer_duration)
    assert timing["mode"] == mode
    result = converter("merge_reference_layer")(body, layer, rig, [1], duration=duration, layer_duration=layer_duration)
    assert len(result) == len(body)
    for frame in range(frames):
        offset = frame * 56
        source_offset = min(frame, layer_frames - 1) * 56 + 28
        assert result[offset:offset + 28] == body[offset:offset + 28]
        assert result[offset + 28:offset + 56] == layer[source_offset:source_offset + 28]


@pytest.mark.parametrize("frames, duration, layer_frames, layer_duration", [
    (7, .2, 6, .2), (7, .2, 13, .5), (7, .2, 1, .1), (True, .2, 7, .2),
    (7, 0, 7, .2), (7, float("nan"), 7, .2), (7, .2, 7, float("inf")), (7, 6e-10, 7, 3e-9),
])
def test_reference_layer_alignment_rejects_incompatible_or_invalid_timing(frames, duration, layer_frames, layer_duration):
    with pytest.raises(ValueError, match="sample spacing"):
        reference_layer_timing(frames, duration, layer_frames, layer_duration)


def test_layer_metadata_timing_mask_and_reference_contract(tmp_path):
    rig, psa = sample()
    (tmp_path / "Layer01.psa").write_bytes(psa)
    entry = dict(file="Layer01.psa", metadata_file="Layer01.json",
                 sha256=hashlib.sha256(psa).hexdigest(), bone_ids=[1])
    properties = dict(AdditiveAnimType="AAT_LocalSpaceBase", RefPoseType="ABPT_RefPose",
                      NumFrames=2, SequenceLength=1, TrackToSkeletonMapTable=[dict(BoneTreeIndex=1)])
    def write_metadata():
        (tmp_path / "Layer01.json").write_text(json.dumps([dict(Type="AnimSequence", Properties=properties)]))
    write_metadata()
    base = b"".join(struct.pack("<7f", *bone["translation"], *bone["rotation"])
                    for _ in range(2) for bone in rig["bones"])
    apply = converter("apply_layer")
    assert len(apply(base, rig, 2, 1, entry, tmp_path)) == len(base)
    for key, value in [("AdditiveAnimType", "AAT_RotationOffsetMeshSpace"),
                       ("RefPoseType", "ABPT_AnimFrame"), ("NumFrames", 3),
                       ("SequenceLength", 2), ("SequenceLength", float("nan"))]:
        original = properties[key]
        properties[key] = value
        write_metadata()
        with pytest.raises(ValueError, match="incompatible"):
            apply(base, rig, 2, 1, entry, tmp_path)
        properties[key] = original
    write_metadata()
    entry["bone_ids"] = [0]
    with pytest.raises(ValueError, match="mask"):
        apply(base, rig, 2, 1, entry, tmp_path)


def test_animation_library_containment_integrity_and_session_lifecycle(tmp_path, monkeypatch):
    rig, source = sample()
    _, data = converter()(source, rig, 1)
    directory = tmp_path / "Asset01" / "Animation"
    directory.mkdir(parents=True)
    metadata = dict(format="mod-viewer-animation", version=1, coordinate_space="asset",
                    layout="frame-major-tq-f32le", skeleton_id=rig["id"], frame_count=2,
                    bone_count=2, duration=1, data_file="Clip01.bin", sha256=hashlib.sha256(data).hexdigest())
    (directory / "Clip01.bin").write_bytes(data)
    def write_metadata():
        (directory / "Clip01.json").write_text(json.dumps(metadata))
    write_metadata()
    manifest = dict(format="mod-viewer-animations", version=1, skeleton_id=rig["id"],
                    clips=[dict(id="clip01", name="Clip 01", file="Clip01.json", duration=1)])
    (directory / "manifest.json").write_text(json.dumps(manifest))
    published, released = [], []
    monkeypatch.setattr("app.assets.animations.server.publish_geometry",
                        lambda blob, **_: published.append(blob) or f"/geometry/{len(published)}")
    monkeypatch.setattr("app.assets.animations.server.release_geometry", released.append)
    current = True
    sessions = AnimationSessions()
    catalog = sessions.catalog(str(tmp_path), "Asset01", rig, lambda: current)
    token = catalog["animation_session"]
    assert len(catalog["animations"]) == 1 and not published
    assert sessions.load("invented", "clip01")["status"] == "error"
    assert sessions.load(token, "../Clip01")["status"] == "error"
    assert sessions.load(token, "clip01")["clip"]["data"]["length"] == len(data)
    metadata["data_file"] = "../../outside.bin"
    write_metadata()
    assert sessions.load(token, "clip01")["status"] == "error"
    metadata["data_file"] = "Clip01.bin"
    write_metadata()
    (directory / "Clip01.bin").write_bytes(b"x" * len(data))
    assert sessions.load(token, "clip01")["status"] == "error"
    current = False
    assert sessions.load(token, "clip01")["status"] == "error"
    sessions.clear()
    assert released == ["/geometry/1"]
    manifest["skeleton_id"] = "b" * 64
    (directory / "manifest.json").write_text(json.dumps(manifest))
    assert sessions.catalog(str(tmp_path), "Asset01", rig, lambda: True)["animations"] == []


def test_captured_weights_follow_geometry_vertex_compaction(tmp_path):
    file = tmp_path / "Component.vb"
    file.write_bytes(bytes([0, 1, 2, 3, 255, 0, 0, 0,
                           4, 5, 6, 7, 128, 127, 0, 0]))
    layout = MigotoBinaryLayout(8, "trianglelist", "R32_UINT", (
        MigotoElement("BLENDINDICES", 0, "R8G8B8A8_UINT", 0),
        MigotoElement("BLENDWEIGHTS", 0, "R8G8B8A8_UNORM", 4)))
    manifest = capture_skinning(str(tmp_path), str(file), layout, [1, 0])
    source, decoded = decode_captured_skinning(str(tmp_path), manifest)
    assert source.influence_count == 4
    assert decoded.bone_ids == (0, 4, 5)
    assert list(struct.unpack("<8I", decoded.indices)) == [4, 5, 6, 7, 0, 1, 2, 3]


def test_direct_asset_weight_rig_preview_and_stale_token(tmp_path, monkeypatch):
    geometry = tmp_path / "Asset01" / "11111111"
    geometry.mkdir(parents=True)
    _wwmi_triangle(geometry)
    vertex = geometry / "Component 0.vb"
    original = vertex.read_bytes()
    vertex.write_bytes(b"".join(original[i:i + 32] + bytes([0, 1, 0, 0, 128, 127, 0, 0])
                                for i in range(0, len(original), 32)))
    _fmt(geometry / "Component 0.fmt", 40, "R16_UINT", [
        ("POSITION", 0, "R32G32B32_FLOAT", 0), ("TEXCOORD", 0, "R32G32_FLOAT", 12),
        ("NORMAL", 0, "R32G32B32_FLOAT", 20), ("BLENDINDICES", 0, "R8G8B8A8_UINT", 32),
        ("BLENDWEIGHTS", 0, "R8G8B8A8_UNORM", 36)])
    raw, _ = sample()
    raw.update(format="mod-viewer-rig", version=1, coordinate_space="asset", components=[
        dict(geometry_hash="11111111", component_ordinal=0, first_index=0, index_count=3,
             bone_map=[0, 1], vertex_sha256=hashlib.sha256(vertex.read_bytes()).hexdigest(),
             index_sha256=hashlib.sha256((geometry / "Component 0.ib").read_bytes()).hexdigest())])
    (geometry.parent / "Rig.json").write_text(json.dumps(raw))
    record = build_index("WWMI", str(tmp_path))["assets"][0]
    preview = AssetPreview(object())
    monkeypatch.setattr(preview, "_asset_selection", lambda _: (
        str(geometry.parent), dict(type="WWMI", path=str(tmp_path)), {}, record))
    payload = preview.load_asset("Asset01")
    assert all(mesh["skinning_available"] for mesh in payload["meshes"].values())
    token = payload["metadata"]["asset"]["rig_session"]
    assert preview.get_skinning_preview("invented")["status"] == "error"
    weights = preview.get_skinning_preview(token)
    assert weights["status"] == "ok" and len(weights["game_rigs"]) == 1
    assert set(weights["meshes"]) == set(payload["meshes"])
    assert all(mesh["game_bone_map"] == {"0": 0, "1": 1} for mesh in weights["meshes"].values())
    assert next(iter(weights["game_rigs"].values()))["animations"] == []
    preview.clear_preview()
    assert preview.get_skinning_preview(token)["status"] == "error"

import hashlib
import importlib.util
import json
from pathlib import Path
import struct
from copy import deepcopy

import pytest

from app.assets import animation_keys, animations
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


def _valid_sequence():
    """A two-step sequence that reuses the single fixture clip for both steps."""
    return dict(id="seq-0001", name="Sequence 01", cyclic=True, restart=0.25,
                steps=[dict(clip="clip01", start=0, leave=0.5, fade=0.25),
                       dict(clip="clip01", start=0, leave=0.5, fade=0)])


def _mutated_sequences(mutate):
    if mutate is None:
        return None
    sequences = [_valid_sequence()]
    mutate(sequences)
    return sequences


@pytest.mark.parametrize("mutate", [
    None,
    lambda s: s[0]["steps"][0].update(clip="clip99"),
    lambda s: s.append(_valid_sequence()),
    lambda s: s[0].update(id="clip01"),
    lambda s: s[0]["steps"][0].update(start=0.5),
    lambda s: s[0]["steps"][0].update(leave=1.5),
    lambda s: s[0]["steps"][0].update(fade=0.6),
    lambda s: s[0].update(steps=s[0]["steps"][:1]),
    lambda s: s[0].update(cyclic=1),
    lambda s: s[0]["steps"][0].update(start=float("nan")),
    lambda s: s[0].update(restart=True),
    lambda s: s[0].update(restart=1.0),
    # The fade must end before the following step's own leave (its start to leave span).
    lambda s: s[0]["steps"][1].update(start=0.3),
    lambda s: s[0].update(restart=0.5),
    lambda s: s[0].update(by_name=1),
], ids=[
    "absent-key", "unknown-clip", "duplicate-id", "id-equals-clip-id", "start-not-before-leave",
    "leave-beyond-duration", "fade-beyond-remaining", "one-step", "non-bool-cyclic",
    "nan-number", "bool-number", "restart-not-before-head-duration",
    "fade-beyond-next-step-span", "restart-not-before-head-leave", "non-bool-by-name",
])
def test_invalid_animation_sequences_drop_all_sequences_and_keep_clips(tmp_path, mutate):
    rig, source = sample()
    _, data = converter()(source, rig, 1)
    directory = tmp_path / "Asset01" / "Animation"
    directory.mkdir(parents=True)
    metadata = dict(format="mod-viewer-animation", version=1, coordinate_space="asset",
                    layout="frame-major-tq-f32le", skeleton_id=rig["id"], frame_count=2,
                    bone_count=2, duration=1, data_file="Clip01.bin", sha256=hashlib.sha256(data).hexdigest())
    (directory / "Clip01.bin").write_bytes(data)
    (directory / "Clip01.json").write_text(json.dumps(metadata))
    manifest = dict(format="mod-viewer-animations", version=1, skeleton_id=rig["id"],
                    clips=[dict(id="clip01", name="Clip 01", file="Clip01.json", duration=1)])
    sequences = _mutated_sequences(mutate)
    if sequences is not None:
        manifest["sequences"] = sequences
    (directory / "manifest.json").write_text(json.dumps(manifest))
    catalog = AnimationSessions().catalog(str(tmp_path), "Asset01", rig, lambda: True)
    assert len(catalog["animations"]) == 1
    assert catalog["animation_sequences"] == []


@pytest.mark.parametrize("changes, expected", [
    (dict(by_name=True), dict(name="Sequence 01", by_name=True)),
    # Libraries extracted before the flag named their previews; the viewer marks them the same way.
    (dict(name="Sequence 01 (by name)"), dict(name="Sequence 01", by_name=True)),
    (dict(name="Sequence 01 (by name)", by_name=True), dict(name="Sequence 01 (by name)", by_name=True)),
    (dict(), dict(name="Sequence 01")),
])
def test_name_paired_sequences_carry_a_flag_instead_of_a_label(changes, expected):
    [sequence] = animations._sequences([{**_valid_sequence(), **changes}], {"clip01": dict(duration=1)})
    assert {key: sequence[key] for key in ("name", "by_name") if key in sequence} == expected


@pytest.mark.parametrize("changes, valid", [
    (dict(), True),
    (dict(track_layout="prop-track-v2"), True),
    (dict(track_layout="prop-track-v3"), False),
])
def test_prop_track_layouts_are_validated(changes, valid):
    track = dict(file="Props/" + "1" * 32 + ".bin", sha256="2" * 64, frame_count=2, duration=1, start=0, end=1)
    prop = dict(id="prop-01", name="Prop01", file="Props/" + "0" * 32 + ".bin", layout=animations.PROP_LAYOUT,
                vertex_count=3, index_count=3, sha256="3" * 64, socket=None, bone_count=2, tracks={"clip01": track},
                **changes)
    props = animations._props(dict(props=[prop]))
    assert bool(props) is valid
    if valid:
        assert props["prop-01"]["track_layout"] == changes.get("track_layout", "prop-track-v1")
    # A rigid prop has no track layout.
    rigid = {key: value for key, value in prop.items() if key not in ("socket", "bone_count", "tracks")}
    assert bool(animations._props(dict(props=[rigid]))) is ("track_layout" not in changes)


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
                    clips=[dict(id="clip01", name="Clip 01", file="Clip01.json", duration=1)],
                    sequences=[_valid_sequence()])
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
    assert catalog["animation_sequences"] == [
        dict(id="seq-0001", name="Sequence 01", cyclic=True, restart=0.25,
             steps=[dict(clip="clip01", start=0.0, leave=0.5, fade=0.25),
                    dict(clip="clip01", start=0.0, leave=0.5, fade=0.0)])]
    assert sessions.load("invented", "clip01")["status"] == "error"
    assert sessions.load(token, "../Clip01")["status"] == "error"
    assert sessions.load(token, "clip01")["clip"]["data"]["length"] == len(data)
    # Playback reads fresh bounded bytes without a clip cache or integrity rescan.
    replacement = bytearray(data)
    struct.pack_into("<f", replacement, 0, 3)
    (directory / "Clip01.bin").write_bytes(replacement)
    assert sessions.load(token, "clip01")["status"] == "ok"
    assert published[-1] == replacement
    from app.assets.animations import read_clip

    with pytest.raises(ValueError, match="incomplete or has changed"):
        read_clip(str(directory), manifest["clips"][0], rig)
    metadata["data_file"] = "../../outside.bin"
    write_metadata()
    assert sessions.load(token, "clip01")["status"] == "error"
    metadata["data_file"] = "Clip01.bin"
    write_metadata()
    (directory / "Clip01.bin").write_bytes(data[:-4])
    assert sessions.load(token, "clip01")["status"] == "error"
    current = False
    assert sessions.load(token, "clip01")["status"] == "error"
    sessions.clear()
    assert sorted(released) == ["/geometry/1", "/geometry/2"]
    manifest["skeleton_id"] = "b" * 64
    (directory / "manifest.json").write_text(json.dumps(manifest))
    assert sessions.catalog(str(tmp_path), "Asset01", rig, lambda: True)["animations"] == []


# Root, Spine, Arm, Twist, a Chain (with a child) and an unskinned helper.
_RUNTIME_CORE = {0, 1, 2, 3}
_RUNTIME_SKINNED = {0, 1, 2, 3, 4, 5}


def _runtime_rig(skinned):
    names, parents = ["Root", "Spine", "Arm", "Twist", "Chain01", "Chain02", "Helper"], [-1, 0, 1, 2, 1, 4, 1]
    return dict(id="c" * 64, bones=[dict(name=name, parent=parent) for name, parent in zip(names, parents)],
                components=[dict(bone_map=sorted(skinned))])


@pytest.mark.parametrize("keyed_sets, skinned, expected", [
    # The twist is keyed in every clip, so it stays authored; the unkeyed chain and its child run.
    pytest.param([_RUNTIME_CORE] * 10, _RUNTIME_SKINNED, [4, 5], id="never-keyed-chain-and-child"),
    pytest.param([_RUNTIME_CORE] * 29 + [_RUNTIME_CORE | {4}], _RUNTIME_SKINNED, [4, 5], id="chain-keyed-in-one-of-thirty"),
    pytest.param([_RUNTIME_CORE | {4}] * 5 + [_RUNTIME_CORE] * 5, _RUNTIME_SKINNED, [5], id="chain-keyed-in-half"),
    pytest.param([_RUNTIME_CORE] * 10, {0, 1, 2, 3}, [], id="unskinned-chain-dropped"),
    pytest.param([], _RUNTIME_SKINNED, [], id="no-keyed-clips"),
])
def test_runtime_bones_follow_keyed_evidence_and_skinning(keyed_sets, skinned, expected):
    clips = [dict(keyed_bones=sorted(keyed)) for keyed in keyed_sets] + [dict(keyed_bones=[True])]
    assert animation_keys.runtime_bones(_runtime_rig(skinned), clips) == expected


@pytest.mark.parametrize("value, expected", [
    ([1, 0], [0, 1]),
    ([], []),
    (None, None),
    ("01", None),
    ((0,), None),
    ({0: 0}, None),
    ([0, 1, 2], None),
    ([1, 1], None),
    ([2], None),
    ([-1], None),
    ([True], None),
    ([0.0], None),
    (["0"], None),
])
def test_bone_list_accepts_only_unique_in_range_int_lists(value, expected):
    assert animation_keys.bone_list(value, 2) == expected


def test_runtime_and_keyed_evidence_gate_secondary_bones_per_clip(tmp_path, monkeypatch):
    rig, source = sample()
    _, data = converter()(source, rig, 1)
    directory = tmp_path / "Asset01" / "Animation"
    directory.mkdir(parents=True)
    (directory / "Clip01.bin").write_bytes(data)
    metadata = dict(format="mod-viewer-animation", version=1, coordinate_space="asset",
                    layout="frame-major-tq-f32le", skeleton_id=rig["id"], frame_count=2,
                    bone_count=2, duration=1, data_file="Clip01.bin", sha256=hashlib.sha256(data).hexdigest(),
                    keyed_bones=[0])
    manifest = dict(format="mod-viewer-animations", version=1, skeleton_id=rig["id"],
                    clips=[dict(id="clip01", name="Clip 01", file="Clip01.json", duration=1)],
                    runtime_bones=[0, 1])

    def write():
        (directory / "Clip01.json").write_text(json.dumps(metadata))
        (directory / "manifest.json").write_text(json.dumps(manifest))

    write()
    monkeypatch.setattr("app.assets.animations.server.publish_geometry",
                        lambda blob, **_: f"/geometry/{len(blob)}")
    monkeypatch.setattr("app.assets.animations.server.release_geometry", lambda url: None)
    sessions = AnimationSessions()

    def load_clip():
        catalog = sessions.catalog(str(tmp_path), "Asset01", rig, lambda: True)
        result = sessions.load(catalog["animation_session"], "clip01")
        assert result["status"] == "ok"
        return result["clip"]

    # Runtime bones the clip does not key are simulated.
    assert load_clip()["secondary_bones"] == [1]
    # An invalid runtime list is ignored without failing the clip.
    manifest["runtime_bones"] = [5]
    write()
    assert "secondary_bones" not in load_clip()
    # So is a clip without keyed evidence.
    manifest["runtime_bones"] = [0, 1]
    del metadata["keyed_bones"]
    write()
    assert "secondary_bones" not in load_clip()


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


@pytest.mark.parametrize("change", ["valid", "path", "checksum", "index", "nonfinite", "overlap", "duplicate", "weight", "names", "publication"])
def test_optional_morph_extraction_validation_and_runtime_publication(tmp_path, monkeypatch, change):
    rig, source = sample()
    frames, bone_data = converter()(source, rig, 1)
    directory = tmp_path / "Asset01/Animation"
    directory.mkdir(parents=True)
    target_data = struct.pack("<I6f", 1, 0, 0, 2, 0, 1, 0)
    target = dict(name="Shape01", offset=0, count=1)
    morphs = dict(names=["Shape01"], file="Morph01.bin", sha256=hashlib.sha256(target_data).hexdigest(),
                  components=[dict(vertex_sha256="b" * 64, index_sha256="c" * 64, vertex_count=3, targets=[target])])
    data = bone_data + struct.pack("<2f", 0, 1)
    if change == "path":
        morphs["file"] = "../../outside.bin"
    elif change == "checksum":
        morphs["sha256"] = "0" * 64
    elif change in ("index", "nonfinite"):
        target_data = struct.pack("<I6f", 3 if change == "index" else 1, 0, 0,
                                  float("nan") if change == "nonfinite" else 2, 0, 1, 0)
        morphs["sha256"] = hashlib.sha256(target_data).hexdigest()
    elif change == "overlap":
        target["offset"] = 4
    elif change == "duplicate":
        morphs["components"].append(deepcopy(morphs["components"][0]))
    elif change == "weight":
        data = bone_data + struct.pack("<2f", 0, float("nan"))
    elif change == "names":
        morphs["names"] = ["Shape01", "Shape01"]
    metadata = dict(format="mod-viewer-animation", version=1, coordinate_space="asset", layout="frame-major-tq-f32le",
                    skeleton_id=rig["id"], frame_count=frames, bone_count=2, duration=1, data_file="Clip01.bin",
                    sha256=hashlib.sha256(data).hexdigest(), morphs=morphs)
    (directory / "Clip01.bin").write_bytes(data)
    (directory / "Morph01.bin").write_bytes(target_data)
    (directory / "Clip01.json").write_text(json.dumps(metadata))
    (directory / "manifest.json").write_text(json.dumps(dict(format="mod-viewer-animations", version=1,
        skeleton_id=rig["id"], clips=[dict(id="clip01", name="Clip 01", file="Clip01.json", duration=1)])))
    published, released = [], []
    def publish(data, **_):
        if change == "publication" and published:
            raise ValueError("Fixture publication failed.")
        published.append(data)
        return f"/geometry/{len(published)}"
    monkeypatch.setattr("app.assets.animations.server.publish_geometry", publish)
    monkeypatch.setattr("app.assets.animations.server.release_geometry", released.append)
    current = True
    sessions = AnimationSessions()
    catalog = sessions.catalog(str(tmp_path), "Asset01", rig, lambda: current)
    token = catalog["animation_session"]
    from unittest.mock import patch
    from app.assets.animation_morphs import read_morphs
    from app.assets.animations import read_clip

    if change not in ("valid", "publication"):
        with pytest.raises(ValueError):
            read_clip(str(directory), dict(file="Clip01.json", duration=1), rig)
        if change in ("checksum", "index", "nonfinite", "weight"):
            return

    with patch("app.assets.animations.read_morphs", wraps=read_morphs) as reader:
        if change == "valid":
            with patch("app.assets.animations.hashlib.sha256", side_effect=AssertionError("Unexpected checksum scan")), \
                    patch("app.assets.animations._valid_transforms", side_effect=AssertionError("Unexpected sample scan")), \
                    patch("app.assets.animations.bounded", side_effect=AssertionError("Unexpected sample scan")):
                response = sessions.load(token, "clip01")
        else:
            response = sessions.load(token, "clip01")
    if change == "valid":
        assert reader.call_count == 1
    if change == "publication":
        assert response["status"] == "error" and published == [data] and released == ["/geometry/1"]
        return
    if change != "valid":
        assert response["status"] == "error" and not published
        return
    assert response["clip"]["morphs"]["data"]["length"] == 28
    assert published == [data, target_data]
    current = False
    assert sessions.load(token, "clip01")["status"] == "error"
    sessions.clear()
    assert set(released) == {"/geometry/1", "/geometry/2"}


def test_extraction_validation_cache_reuses_only_unchanged_morph_sidecars(tmp_path):
    from unittest.mock import patch
    from app.assets.animations import read_clip

    rig, source = sample()
    frames, bone_data = converter()(source, rig, 1)
    target_data = struct.pack("<I6f", 1, 0, 0, 2, 0, 1, 0)
    morphs = dict(names=["Shape01"], file="Morph01.bin", sha256=hashlib.sha256(target_data).hexdigest(),
                  components=[dict(vertex_sha256="b" * 64, index_sha256="c" * 64, vertex_count=3,
                                   targets=[dict(name="Shape01", offset=0, count=1)])])
    data = bone_data + struct.pack("<2f", 0, 1)
    metadata = dict(format="mod-viewer-animation", version=1, coordinate_space="asset", layout="frame-major-tq-f32le",
                    skeleton_id=rig["id"], frame_count=frames, bone_count=2, duration=1, data_file="Clip01.bin",
                    sha256=hashlib.sha256(data).hexdigest(), morphs=morphs)
    target = tmp_path / "Morph01.bin"
    target.write_bytes(target_data)
    clip = tmp_path / "Clip01.json"
    clip.write_text(json.dumps(metadata))
    (tmp_path / "Clip02.json").write_text(json.dumps(metadata))
    binary = tmp_path / "Clip01.bin"
    binary.write_bytes(data)
    validated = set()

    def read(file="Clip01.json"):
        return read_clip(str(tmp_path), dict(file=file, duration=1), rig, validated_morphs=validated)

    with patch("app.assets.animation_morphs.open", wraps=open, create=True) as reader:
        assert read()[1] == data
        assert read("Clip02.json")[1] == data
        assert reader.call_count == 1
        # A fresh operation and publication still perform a fresh read.
        read_clip(str(tmp_path), dict(file="Clip01.json", duration=1), rig)
        assert reader.call_count == 2
        replacement = tmp_path / "Replacement.bin"
        replacement.write_bytes(target_data)
        replacement.replace(target)
        assert read()[1] == data
        assert reader.call_count == 3
        morphs["components"][0]["targets"][0]["offset"] = 4
        clip.write_text(json.dumps(metadata))
        with pytest.raises(ValueError, match="morph target range"):
            read()
        morphs["components"][0]["targets"][0]["offset"] = 0
        invalid = bone_data + struct.pack("<2f", 0, float("nan"))
        metadata["sha256"] = hashlib.sha256(invalid).hexdigest()
        binary.write_bytes(invalid)
        clip.write_text(json.dumps(metadata))
        with pytest.raises(ValueError, match="invalid morph weights"):
            read()
        metadata["sha256"] = hashlib.sha256(data).hexdigest()
        binary.write_bytes(data)
        clip.write_text(json.dumps(metadata))
        # An in-place change must invalidate the marker even at equal byte length.
        target.write_bytes(bytes(len(target_data)))
        with pytest.raises(ValueError, match="incomplete or has changed"):
            read()


@pytest.mark.parametrize("names, expected", [
    (["E_Close", "E_Close_L", "E_Close_R", "M_Close", "Smile"], ["E_Close"]),
    (["eyeBlink", "mouthClose"], ["eyeBlink"]),
    (["Blink_L", "Blink_R", "M_Close"], ["Blink_L", "Blink_R"]),
    (["left_eye_blink", "right_eye_blink"], ["left_eye_blink", "right_eye_blink"]),
    (["Blink_L"], []),
    (["M_Close", "Close", "Smile"], []),
])
def test_blink_targets_prefer_whole_face_shape_and_never_wink(names, expected):
    from app.assets.animation_morphs import blink_targets

    components = [dict(targets=[dict(name=name) for name in names[:2]]), dict(targets=[dict(name=name) for name in names[2:]])]
    assert blink_targets(components) == expected
    assert blink_targets(None) == [] and blink_targets([dict(targets=None), "x"]) == []


EYE, MOUTH = "0000000a:", "0000000b:"


@pytest.mark.parametrize("names, rest, expected", [
    # The captured default eye pose crossfades into the closed WinkB pair.
    ([EYE + "00000001:Eye_Default", EYE + "00000002:Eye_WinkB_L", EYE + "00000003:Eye_WinkB_R",
      EYE + "00000004:Eye_WinkA_L", MOUTH + "00000005:Mouth_Default"],
     {EYE + "00000001:Eye_Default": 1.0, MOUTH + "00000005:Mouth_Default": 1.0},
     ([EYE + "00000002:Eye_WinkB_L", EYE + "00000003:Eye_WinkB_R", EYE + "00000001:Eye_Default"], [1, 1, -1])),
    # Eyes captured open: the closed pair alone, less any closure already shown.
    ([EYE + "00000002:Eye_WinkB_L", EYE + "00000003:Eye_WinkB_R"], {EYE + "00000002:Eye_WinkB_L": .25},
     ([EYE + "00000002:Eye_WinkB_L", EYE + "00000003:Eye_WinkB_R"], [.75, 1])),
    # Never one eye, never a pair split across renderers, never a generic name.
    ([EYE + "00000002:Eye_WinkB_L"], {}, None),
    ([EYE + "00000002:Eye_WinkB_L", MOUTH + "00000003:Eye_WinkB_R"], {}, None),
    ([EYE + "00000002:E_Close"], {}, None),
    # Without captured weights the library keeps the plain named-shape rule.
    (["E_Close", "Smile"], None, (["E_Close"], None)),
])
def test_idle_blink_crossfades_genshin_eyes_from_their_captured_pose(names, rest, expected):
    from app.assets.animation_morphs import idle_blink

    morphs = dict(components=[dict(targets=[dict(name=name) for name in names])])
    if rest is not None:
        morphs["rest_weights"] = rest
    result = idle_blink(morphs)
    assert result == (None if expected is None else (expected[0], pytest.approx(expected[1]) if expected[1] else None))
    with pytest.raises(ValueError, match="captured morph weights"):
        idle_blink({**morphs, "rest_weights": {"name": float("nan")}})


def test_clips_without_facial_curves_share_the_library_blink_targets(tmp_path, monkeypatch):
    rig, source = sample()
    frames, bone_data = converter()(source, rig, 1)
    directory = tmp_path / "Asset01/Animation"
    directory.mkdir(parents=True)
    target_data = struct.pack("<I6f", 1, 0, 0, 2, 0, 1, 0) * 2
    targets = [dict(name="Smile", offset=0, count=1), dict(name="E_Close", offset=28, count=1)]
    components = [dict(vertex_sha256="b" * 64, index_sha256="c" * 64, vertex_count=3, targets=targets)]
    morphs = dict(names=["Smile"], file="Morph01.bin", sha256=hashlib.sha256(target_data).hexdigest(),
                  components=components)

    def write(name, data, morphs=None):
        base = dict(format="mod-viewer-animation", version=1, coordinate_space="asset", layout="frame-major-tq-f32le",
                    skeleton_id=rig["id"], frame_count=frames, bone_count=2, duration=1, data_file=name + ".bin",
                    sha256=hashlib.sha256(data).hexdigest())
        (directory / (name + ".bin")).write_bytes(data)
        (directory / (name + ".json")).write_text(json.dumps({**base, **({"morphs": morphs} if morphs else {})}))

    write("Plain", bone_data)
    write("Facial", bone_data + struct.pack("<2f", 0, 1), morphs)
    (directory / "Morph01.bin").write_bytes(target_data)
    clips = [dict(id=name.lower(), name=name, file=name + ".json", duration=1) for name in ("Plain", "Facial")]
    (directory / "manifest.json").write_text(json.dumps(dict(
        format="mod-viewer-animations", version=1, skeleton_id=rig["id"], clips=clips)))
    published, released = [], []
    monkeypatch.setattr("app.assets.animations.server.publish_geometry",
                        lambda data, **_: published.append(data) or f"/geometry/{len(published)}")
    monkeypatch.setattr("app.assets.animations.server.release_geometry", released.append)
    sessions = AnimationSessions()
    token = sessions.catalog(str(tmp_path), "Asset01", rig, lambda: True)["animation_session"]

    plain = sessions.load(token, "plain")["clip"]
    assert "morphs" not in plain and "scales" not in plain["idle_morphs"]
    assert plain["idle_morphs"]["names"] == ["E_Close"] and plain["idle_morphs"]["components"] == components
    assert plain["idle_morphs"]["data"] == {"url": "/geometry/2", "length": len(target_data)}
    assert published == [bone_data, target_data]

    facial = sessions.load(token, "facial")["clip"]
    assert "idle_morphs" not in facial and facial["morphs"]["names"] == ["Smile"]
    assert sorted(released) == ["/geometry/1", "/geometry/2"]

    # A library with no eye-closing shape plays plain clips without an idle face.
    targets[1]["name"] = "Frown"
    (directory / "Facial.json").write_text(json.dumps({**json.loads((directory / "Facial.json").read_text()),
                                                       "morphs": {**morphs, "components": components}}))
    other = AnimationSessions()
    token = other.catalog(str(tmp_path), "Asset01", rig, lambda: True)["animation_session"]
    assert "idle_morphs" not in other.load(token, "plain")["clip"]

    # Genshin libraries record captured weights; the blink then carries per-shape scales.
    targets[0]["name"], targets[1]["name"] = EYE + "00000002:Eye_WinkB_L", EYE + "00000003:Eye_WinkB_R"
    (directory / "Facial.json").write_text(json.dumps({**json.loads((directory / "Facial.json").read_text()), "morphs": {
        **morphs, "names": [targets[0]["name"]], "components": components, "rest_weights": {}}}))
    genshin = AnimationSessions()
    token = genshin.catalog(str(tmp_path), "Asset01", rig, lambda: True)["animation_session"]
    idle = genshin.load(token, "plain")["clip"]["idle_morphs"]
    assert idle["names"] == [targets[0]["name"], targets[1]["name"]] and idle["scales"] == [1, 1]

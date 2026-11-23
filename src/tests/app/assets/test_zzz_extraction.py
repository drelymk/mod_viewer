"""Synthetic ZZZ extraction, admission and clip contracts."""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import threading
import time
from unittest.mock import patch

import numpy as np
import pytest

from app.assets import index
from app.assets.animations import AnimationSessions, read_clip
from app.assets.animation_morphs import read_morphs
from app.assets.game_extract import (zzz_animation, zzz_index, zzz_morphs, zzz_native, zzz_pipeline,
                                     unity_dds, zzz_prop_textures, zzz_props, zzz_runtime)
from app.assets.game_extract.pipeline import rest_worlds
from app.assets.game_extract.runtime import ExtractionCancelled
from app.assets.loader.hash_asset import load_hash_asset
from app.assets.rig import resolve_game_rig, validate_rig
from app.assets.resolver import AssetComponentBinding
from app.assets.skinning import decode_captured_skinning
from app.settings import paths


def fixture(tmp_path):
    root = tmp_path / "assets"
    asset = root / "Character01"
    asset.mkdir(parents=True)
    vertices = [[.2, .3, .4], [1.2, .4, .8], [.4, 1.2, .7]]
    uv = [[.1, .2], [.3, .4], [.5, .6]]
    vb = asset / "mesh-vb0=11111111.txt"
    lines = ["stride: 52", "vertex count: 3",
             "element[0]: POSITION0, format: R32G32B32_FLOAT",
             "element[1]: TEXCOORD0, format: R32G32_FLOAT",
             "element[2]: BLENDWEIGHTS0, format: R32G32B32A32_FLOAT",
             "element[3]: BLENDINDICES0, format: R32G32B32A32_UINT"]
    for number, (position, texcoord) in enumerate(zip(vertices, uv)):
        for name, values in (("POSITION", position), ("TEXCOORD", texcoord),
                             ("BLENDWEIGHTS", [.75, .25, 0, 0]), ("BLENDINDICES", [8, 12, 0, 0])):
            lines.append(f"vb0[{number}]+000 {name}: " + ", ".join(map(str, values)))
    vb.write_text("\n".join(lines) + "\n")
    ib = asset / "mesh-ib=22222222.txt"
    ib.write_text("first index: 0\nindex count: 3\ntopology: trianglelist\n0 1 2\n")
    (asset / "hash.json").write_text(json.dumps([dict(component_name="Body01", position_vb="11111111",
        ib="22222222", object_indexes=[0], object_indexes_count=[3], object_classifications=["A"],
        vb_file=vb.name, ib_files=[ib.name])]))
    record = index.build_index("ZZMI", str(root))["assets"][0]
    avatar = dict(name="Avatar_Type01_Character01_ModelAvatar", bones=[
        dict(path="", hash=0, parent=-1, translation=[0, 0, 0], rotation=[0, 0, 0, 1], scale=[1, 1, 1]),
        dict(path="Root/Joint01", hash=100, parent=0, translation=[0, 0, 0], rotation=[0, 0, 0, 1], scale=[1, 2, 1])])
    mesh = dict(name="SeparateMesh_Avatar_Type01_Character01_Model_Body", vertices=np.array(vertices).ravel().tolist(),
        uv=np.array(uv).ravel().tolist(), indices=[0, 1, 2], weights=[[.75, .25, 0, 0]] * 3,
        bone_indices=[[0, 1, 0, 0]] * 3, bone_hashes=[0, 100],
        bind_poses=[matrix.ravel(order="C").tolist() for matrix in (np.eye(4), np.diag([1, .5, 1, 1]))],
        sections=[dict(first=0, count=3, vertex_first=0, vertex_count=3)])
    rows = [dict(name=value["name"], type=kind, block="ZenlessZoneZero_Data/StreamingAssets/Blocks/1.blk",
                 cab="CAB-01", offset=0, file_ordinal=0, path_id=number)
            for number, (kind, value) in enumerate((("Avatar", avatar), ("Mesh", mesh),
                ("AnimationClip", dict(name="Avatar_Type01_Character01_Ani_Clip01"))))]
    class Client:
        def __init__(self):
            self.work = tmp_path / "work"
            self.work.mkdir(exist_ok=True)
        def read(self, row):
            if row["type"] == "Avatar":
                return deepcopy(avatar)
            if row["type"] == "Mesh":
                return deepcopy(mesh)
            values = np.array([[0, 0, 0, 0, 0, 0, 1, 1, 2, 1, 0],
                               [1, 0, 0, 0, 0, 0, -1, 2, 3, 1, 1]], dtype="<f4")
            (self.work / "samples-1.bin").write_bytes(values.tobytes())
            return dict(name=row["name"], start=0, stop=1, file="samples-1.bin", times=[0, 1], acl_count=11,
                streamed_count=0, streamed=[], dense=dict(count=0), constant=[], bindings=[
                    dict(path=100, attribute=attribute, type="Transform", custom=0, pointer=0) for attribute in (1, 2, 3)]
                    + [dict(path=0, attribute=0, type="Animator", custom=1, pointer=0)])
    return str(root), asset, record, avatar, mesh, rows, Client()


def facial_fixture(tmp_path):
    root, asset, record, avatar, mesh, rows, client = fixture(tmp_path)
    mesh["blend_shapes"] = dict(channels=[dict(name="Shape01", hash=123, first=0, count=1)],
        weights=[100], frames=[dict(first=0, count=1, normals=True)],
        vertices=[dict(index=0, position=[0, 0, 2], normal=[0, 1, 0])])
    rows.append({**rows[0], "type": "SkinnedMeshRenderer", "path_id": 99, "name": "",
        "renderer": dict(path="", mesh={key: rows[1][key] for key in ("cab", "path_id", "offset", "file_ordinal")},
                         avatar={key: rows[0][key] for key in ("cab", "path_id", "offset", "file_ordinal")})})
    read = client.read
    def read_facial(row):
        metadata = read(row)
        if row["type"] == "AnimationClip":
            file = client.work / metadata["file"]
            values = np.frombuffer(file.read_bytes(), dtype="<f4").reshape(2, 11)
            file.write_bytes(np.column_stack((values, [0, 100])).astype("<f4").tobytes())
            metadata["acl_count"] = 12
            metadata["blend_shape_clamp"] = True
            metadata["bindings"].append(dict(path=0, attribute=123, type="SkinnedMeshRenderer", custom=20, pointer=0))
        return metadata
    client.read = read_facial
    return root, asset, record, avatar, mesh, rows, client


def test_stock_zzz_mod_loads_captured_rig_and_clips_with_edited_blend_stream(tmp_path, monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace
    from app.bridge.mod_preview import ModPreview
    from tests.support.model_data import standard_component_resources

    root, asset, record, _avatar, mesh, rows, client = facial_fixture(tmp_path)
    metadata = json.loads((asset / "hash.json").read_text())
    metadata[0].update(blend_vb="33333333", object_index_counts=[3])
    (asset / "hash.json").write_text(json.dumps(metadata))
    snapshot = index.build_index("ZZMI", root)
    record = snapshot["assets"][0]
    output = tmp_path / "output"
    zzz_pipeline.build(client, root, record, output, "Character01", lambda *_: None, threading.Event(), rows)
    shutil.copytree(output, asset, dirs_exist_ok=True)
    # A second captured component is restored by Asset Fill rather than the mod.
    shutil.copyfile(next(asset.glob("*-ib=*.txt")), asset / "part02-ib=88888888.txt")
    metadata.append({**metadata[0], "ib": "88888888", "component_name": "Part02",
                     "vb_file": next(asset.glob("*-vb0=*.txt")).name,
                     "ib_files": ["part02-ib=88888888.txt"]})
    (asset / "hash.json").write_text(json.dumps(metadata))
    raw_rig = json.loads((asset / "Rig.json").read_text())
    for palette in raw_rig["components"]:
        palette["metadata_sha256"] = hashlib.sha256((asset / "hash.json").read_bytes()).hexdigest()
    raw_rig["components"].append({**raw_rig["components"][0], "geometry_hash": "88888888", "metadata_entry": 1,
                                   "index_file": "part02-ib=88888888.txt"})
    (asset / "Rig.json").write_text(json.dumps(raw_rig))
    snapshot = index.build_index("ZZMI", root)
    mod = tmp_path / "mod"
    mod.mkdir()
    # An added vertex changes the whole-stream fingerprint without changing its palette.
    positions = np.asarray(mesh["vertices"]).reshape(-1, 3).tolist()
    positions.append(positions[0])
    (mod / "position.buf").write_bytes(b"".join(struct.pack("<10f", *p, 0, 0, 1, 1, 0, 0, 1) for p in positions))
    (mod / "texcoord.buf").write_bytes(struct.pack("<8f", .1, .2, .3, .4, .5, .6, .1, .2))
    (mod / "blend.buf").write_bytes(struct.pack("<4f4I", .75, .25, 0, 0, 8, 12, 0, 0) * 4)
    (mod / "mesh.ib").write_bytes(struct.pack("<6I", 0, 1, 2, 3, 1, 2))
    (mod / "mod.ini").write_text("""[TextureOverrideComponent01Blend]
hash = 33333333
handling = skip
if DRAW_TYPE == 2 || DRAW_TYPE == 4
vb1 = ResourceComponent01Texcoord
vb2 = ResourceComponent01Blend
checktextureoverride = ib
elif DRAW_TYPE == 1
vb0 = ResourceComponent01Position
vb2 = ResourceComponent01Blend
draw = 4, 0
endif
[TextureOverrideComponent01A]
hash = 22222222
match_first_index = 0
match_index_count = 3
ib = ResourceComponent01IB
drawindexed = 6, 0, 0
[ResourceComponent01Blend]
type = Buffer
stride = 32
filename = blend.buf
""" + standard_component_resources(position_file="position.buf", texcoord_file="texcoord.buf",
                                    ib_file="mesh.ib", position_stride=40))
    monkeypatch.setattr(index, "load_index", lambda *_: snapshot)
    monkeypatch.setattr(ModPreview, "_load_asset_folders", staticmethod(
        lambda: [dict(type="ZZMI", path=root, enabled=True)]))
    preview = ModPreview(SimpleNamespace(mod_folder=lambda _: str(mod)))
    loaded = preview.load_mod(str(mod))
    assert not loaded.get("error")
    retained = next(iter(preview._skinning_manifests[str(mod)].values()))
    assert retained.asset_binding.component_ordinal is None
    assert retained.skinning_source.native_palette_hashes == ("33333333",)
    # Weight access uses the retained proof, not another INI/resource scan.
    monkeypatch.setattr("core.ini.draw_groups.build_draw_groups", lambda *_args, **_kwargs: pytest.fail("Unexpected draw scan"))
    weights = preview.get_model_skinning_preview(str(mod))
    assert weights["status"] == "ok" and len(weights["game_rigs"]) == 1
    skin = next(iter(weights["meshes"].values()))
    rig = weights["game_rigs"][skin["game_rig_id"]]
    assert skin["bone_ids"] == [8, 12] and skin["game_bone_map"] == {"8": 1, "12": 2}
    assert len(rig["animations"]) == 1
    clip = preview.get_animation(rig["animation_session"], rig["animations"][0]["id"])
    assert clip["status"] == "ok" and clip["clip"]["morphs"]["names"]
    from app.bridge.asset_preview import AssetPreview

    originals = AssetPreview(SimpleNamespace(mod_folder=lambda _: str(mod)))
    context = preview.authoritative_context(str(mod))[2]
    fill = originals.load_missing_asset_parts(str(mod), context)
    assert fill["status"] == "loaded", fill
    fill_entry = next(iter(fill["payload"]["meshes"].values()))
    assert not fill_entry["skinning_available"] and fill_entry["animation_skinning_available"]
    fill_token = fill["payload"]["metadata"]["asset"]["rig_session"]
    captured_weights = originals.get_skinning_preview(fill_token)
    assert captured_weights["status"] == "ok" and len(captured_weights["meshes"]) == 1
    assert next(iter(captured_weights["meshes"])).startswith("asset-fill::")
    captured_rig = captured_weights["game_rigs"][rig["id"]]
    captured_clip = originals.get_animation(captured_rig["animation_session"], captured_rig["animations"][0]["id"])
    assert captured_clip["status"] == "ok" and captured_clip["clip"]["morphs"]["names"]
    originals.remove_missing_asset_parts(str(mod), fill["fill_id"])
    assert originals.get_skinning_preview(fill_token)["status"] == "error"
    assert originals.get_animation(captured_rig["animation_session"], captured_rig["animations"][0]["id"])["status"] == "error"
    manifest = preview._skinning_manifests[str(mod)]
    key = next(iter(manifest))
    manifest[key] = replace(retained, skinning_source=replace(retained.skinning_source, native_palette_hashes=("33333333", "44444444")))
    assert preview.get_model_skinning_preview(str(mod))["game_rigs"] == {}
    manifest[key] = retained
    # Missing ordinals may resolve only when the captured hash/range is unique.
    rig_file = asset / "Rig.json"
    original = rig_file.read_bytes()
    ambiguous = json.loads(original)
    ambiguous["components"].append({**ambiguous["components"][0], "component_ordinal": 1})
    rig_file.write_text(json.dumps(ambiguous))
    assert preview.get_model_skinning_preview(str(mod))["game_rigs"] == {}
    rig_file.write_bytes(original)
    # A positively weighted ID the proven palette lacks still adopts it; that ID stays at rest.
    (mod / "blend.buf").write_bytes(struct.pack("<4f4I", .75, .25, 0, 0, 8, 99, 0, 0) * 4)
    partial = preview.get_model_skinning_preview(str(mod))
    assert partial["status"] == "ok" and list(partial["game_rigs"]) == [rig["id"]]
    assert [entry["game_unmapped_bone_ids"] for entry in partial["meshes"].values()] == [[99]]
    # Changed captured metadata must invalidate even an otherwise matching stock program.
    (mod / "blend.buf").write_bytes(struct.pack("<4f4I", .75, .25, 0, 0, 8, 12, 0, 0) * 4)
    (asset / "hash.json").write_text(json.dumps(metadata) + "\n")
    assert preview.get_model_skinning_preview(str(mod))["game_rigs"] == {}
    preview.clear_loaded_model()


@pytest.mark.parametrize("short_names", [False, True])
def test_zzz_package_reimport_and_exact_capture_admission_lifecycle(tmp_path, short_names):
    root, asset, record, avatar, mesh, rows, client = fixture(tmp_path)
    if short_names:
        mesh["name"] = rows[1]["name"] = "Character01_Body"
        rows[1]["shape"] = [[3, 3]]
    cancel = threading.Event()
    output = tmp_path / "output"
    query = "UnmatchedQuery" if short_names else "Character01"
    report, fingerprints = zzz_pipeline.build(client, root, record, output, query, lambda *_: None, cancel, rows)
    assert report["proven_components"] == 1 and report["bones"] == 3 and report["imported"] == 1
    rig = validate_rig(json.loads((output / "Rig.json").read_text()))
    assert rig["components"][0]["bone_map"] == {"8": 1, "12": 2}
    entry = json.loads((output / "Animation/manifest.json").read_text())["clips"][0]
    metadata, data = read_clip(str(output / "Animation"), entry, rig)
    samples = np.frombuffer(data, dtype="<f4").reshape(61, 3, 10)
    assert metadata["version"] == 2 and metadata["omitted_non_transform_channels"] == 1
    assert samples[30, 2, :3] == pytest.approx([.5, 0, 0])
    assert samples[30, 2, 7:] == pytest.approx([1.5, 2.5, 1])
    assert abs(samples[30, 2, 6]) == pytest.approx(1)
    assert samples[0, 2, 7:] == pytest.approx(avatar["bones"][1]["scale"])
    assert all(hashlib.sha256(Path(file).read_bytes()).hexdigest() == digest for file, digest in fingerprints.items())
    for file in output.iterdir():
        if file.is_dir():
            shutil.copytree(file, asset / file.name)
        else:
            shutil.copyfile(file, asset / file.name)
    (asset / "Animation/user.txt").write_text("preserve")
    # A subsequent native read may differ only by serialized reference/bind noise.
    avatar["bones"][1]["translation"][0] = 2e-7
    for pose in mesh["bind_poses"]:
        pose[3] = 2e-7
    output2 = tmp_path / "output2"
    report2, _ = zzz_pipeline.build(client, root, record, output2, "Character01", lambda *_: None, cancel, rows)
    assert report2["clips"] == 1 and (output2 / "Animation/user.txt").read_text() == "preserve"
    assert json.loads((output2 / "Animation/manifest.json").read_text())["clips"][0]["id"] == entry["id"]
    assert validate_rig(json.loads((output2 / "Rig.json").read_text()))["id"] == rig["id"]
    # An actual reference change must not adopt the previous pose or overwrite
    # its clips, even when updated inverse binds prove the new native geometry.
    original_catalog = (asset / "Animation/manifest.json").read_bytes()
    avatar["bones"][1]["translation"][0] = .01
    mesh["bind_poses"][1][3] = -.01 + 2e-7
    with pytest.raises(ValueError, match="conflict with preserved"):
        zzz_pipeline.build(client, root, record, tmp_path / "changed", "Character01", lambda *_: None, cancel, rows)
    assert (asset / "Animation/manifest.json").read_bytes() == original_catalog
    part = load_hash_asset("ZZMI", root, record).parts[0]
    source, decoded = decode_captured_skinning(root, part.skinning)
    binding = AssetComponentBinding(status="exact", range_status="exact", asset_type="ZZMI", root=root,
        asset=record["path"], metadata=part.metadata_path, geometry_hash=part.geometry_hash,
        component_ordinal=part.component_ordinal, first_index=part.first_index, index_count=part.index_count)
    def resolve(**kwargs):
        return resolve_game_rig(binding, source, decoded.bone_ids, {os.path.normcase(root)}, {}, **kwargs)
    assert resolve(captured_palette=True)["bone_map"] == {"8": 1, "12": 2}
    digest = rig["skin_bindings"][0]["sha256"]
    assert resolve(blend_sha256=digest)["version"] == 2
    assert resolve(blend_sha256="0" * 64) is None
    assert resolve_game_rig(binding, source, [99], {root}, {}, captured_palette=True) is None
    vb = Path(root) / part.skinning.file
    vb.write_text(vb.read_text().replace("0.75", "0.7"))
    assert resolve(captured_palette=True) is None


def test_zzz_serialized_inverse_binds_preserve_affine_placement(tmp_path):
    root, _asset, record, avatar, mesh, rows, client = fixture(tmp_path)
    half = float(np.sqrt(.5))
    avatar["bones"][0].update(translation=[3, -2, 1], rotation=[0, 0, half, half], scale=[1, 2, 1])
    avatar["bones"][1].update(translation=[1, 2, -1], rotation=[half, 0, 0, half], scale=[2, 1, 3])
    root_world = np.array([[0, -2, 0, 3], [1, 0, 0, -2], [0, 0, 1, 1], [0, 0, 0, 1]])
    child_local = np.array([[2, 0, 0, 1], [0, 0, -3, 2], [0, 1, 0, -1], [0, 0, 0, 1]])
    placement = np.array([[0, 0, 1, 4], [0, 1, 0, -3], [-1, 0, 0, 2], [0, 0, 0, 1]])
    binds = [np.linalg.inv(world) @ placement for world in (root_world, root_world @ child_local)]
    mesh["bind_poses"] = [matrix.ravel(order="C").tolist() for matrix in binds]
    mapping, actual = zzz_native.mesh_placement(mesh, avatar)
    assert mapping == [0, 1]
    np.testing.assert_allclose(actual, placement, atol=1e-12)
    captured, _ = zzz_native.capture(root, record)
    chosen = zzz_pipeline.verify_candidates(client, captured, rows, "Character01", threading.Event())
    worlds = rest_worlds(chosen["rig"]["bones"], affine=True)
    np.testing.assert_allclose(worlds[0], np.linalg.inv(placement), atol=1e-12)
    np.testing.assert_allclose(worlds[2], np.linalg.inv(placement) @ root_world @ child_local, atol=1e-12)
    changed = deepcopy(mesh)
    changed["bind_poses"] = [matrix.ravel(order="F").tolist() for matrix in binds]
    assert zzz_native.mesh_placement(changed, avatar) is None
    changed = deepcopy(mesh)
    changed["bind_poses"][1][3] += .01
    assert zzz_native.mesh_placement(changed, avatar) is None


@pytest.mark.parametrize("weight,drift,accepted", [
    (.005, .008, True),
    (.005, .03, False),
    (.05, .008, False),
    (.005, float("nan"), False),
])
def test_zzz_placement_tolerates_small_inverse_bind_drift_only_on_lightly_weighted_bones(tmp_path, weight, drift, accepted):
    _root, _asset, _record, avatar, mesh, _rows, _client = fixture(tmp_path)
    avatar["bones"].append(dict(path="Root/Joint02", hash=200, parent=0,
        translation=[0, 0, 0], rotation=[0, 0, 0, 1], scale=[1, 1, 1]))
    bind = np.eye(4)
    bind[0, 3] = drift
    mesh["bone_hashes"].append(200)
    mesh["bind_poses"].append(bind.ravel(order="C").tolist())
    mesh["weights"][0] = [.75 - weight, .25, weight, 0]
    mesh["bone_indices"][0] = [0, 1, 2, 0]
    placed = zzz_native.mesh_placement(mesh, avatar)
    assert (placed is not None) == accepted
    if accepted:
        # The heavily weighted bones define the placement, never the drifting one.
        np.testing.assert_allclose(placed[1], np.eye(4), atol=1e-12)


def test_zzz_native_proof_rejects_wrong_rest_weights_topology_and_ambiguous_palettes(tmp_path):
    root, _asset, record, avatar, mesh, _rows, _client = fixture(tmp_path)
    part = zzz_native.capture(root, record)[0][0]
    placement = zzz_native.mesh_placement(mesh, avatar)
    assert placement is not None
    assert zzz_native.match_component(part, mesh)[0] == {"8": 0, "12": 1}
    reordered = deepcopy(mesh)
    order = [2, 0, 1]
    reordered["vertices"] = np.asarray(mesh["vertices"]).reshape(-1, 3)[order].ravel().tolist()
    reordered["uv"] = np.asarray(mesh["uv"]).reshape(-1, 2)[order].ravel().tolist()
    reordered["weights"] = [mesh["weights"][i] for i in order]
    reordered["bone_indices"] = [mesh["bone_indices"][i] for i in order]
    reordered["indices"] = [order.index(i) for i in mesh["indices"]]
    assert zzz_native.match_component(part, reordered)[0] == {"8": 0, "12": 1}
    repeated = deepcopy(part)
    repeated["blends"][0] = (.5, .5, 0, 0, 8, 8, 0, 0)
    changed = deepcopy(mesh)
    changed["weights"][0] = [.5, .5, 0, 0]
    assert zzz_native.match_component(repeated, changed) is None
    changed = deepcopy(mesh)
    changed["bind_poses"][1][0] = 2
    assert zzz_native.mesh_placement(changed, avatar) is None
    changed = deepcopy(mesh)
    changed["weights"][0] = [.5, .5, 0, 0]
    assert zzz_native.match_component(part, changed) is None
    changed["weights"] = [[.5, .5, 0, 0]] * 3
    part["blends"] = [(.5, .5, 0, 0, 8, 12, 0, 0)] * 3
    assert zzz_native.match_component(part, changed) is None
    changed["indices"] = [0, 0, 1]
    assert zzz_native.match_component(part, changed) is None


@pytest.mark.parametrize("uv,accepted", [
    (None, False), ([], False), (0, False), ([0, 0, 0], False),
    ([.1, .2, .3, .4, .5], False),
    ([float("nan"), .2, .3, .4, .5, .6], False),
    ([.1, .2, .3, .4, .5, .6], True),
    ([.1, .2, 0, .3, .4, 0, .5, .6, 0], True),
    ([.1, .2, 0, 0, .3, .4, 0, 0, .5, .6, 0, 0], True),
])
def test_zzz_native_uv_layout_must_prove_two_coordinates_per_vertex(tmp_path, uv, accepted):
    root, _asset, record, _avatar, mesh, _rows, _client = fixture(tmp_path)
    part = zzz_native.capture(root, record)[0][0]
    mesh["uv"] = uv
    assert bool(zzz_native.match_component_candidates(part, mesh)) == accepted


def test_zzz_extraction_adds_textured_props_driven_only_by_uncaptured_bones(tmp_path, monkeypatch):
    import zlib

    root, asset, record, avatar, mesh, rows, client = fixture(tmp_path)
    # Renderers route by CRC32 bone paths, as the game hashes them.
    joint, prop_path = zlib.crc32(b"Root/Joint01"), zlib.crc32(b"Root/Prop01")
    avatar["bones"][1]["hash"] = joint
    mesh["bone_hashes"] = [0, joint]
    avatar["bones"].append(dict(path="Root/Prop01", hash=prop_path, parent=0,
        translation=[0, 0, 1], rotation=[0, 0, 0, 1], scale=[1, 1, 1]))
    # The prop renderer's GameObject path is "Prop01"; a bone of that path makes it a verified route.
    avatar["bones"].append(dict(path="Prop01", hash=zlib.crc32(b"Prop01"), parent=0,
        translation=[0, 0, 0], rotation=[0, 0, 0, 1], scale=[1, 1, 1]))
    bind = np.eye(4)
    bind[2, 3] = -1
    prop = dict(name="SeparateMesh_Avatar_Type01_Character01_Model_Prop01", vertices=[0, 0, 1, 1, 0, 1, 0, 1, 1],
        normals=[0, 0, 1] * 3, uv=[0, 0, 1, 0, 0, 1], indices=[0, 1, 2], weights=[[1, 0, 0, 0]] * 3,
        bone_indices=[[0, 0, 0, 0]] * 3, bone_hashes=[prop_path], bind_poses=[bind.ravel(order="C").tolist()],
        sections=[dict(first=0, count=3, vertex_first=0, vertex_count=3)])
    # An uncaptured mesh driven by a captured bone (like a hair shadow) is not a prop,
    # and a static effect mesh has no skin to follow the rig.
    driven = {**deepcopy(prop), "name": prop["name"] + "_Shadow", "bone_hashes": [joint],
              "bind_poses": [np.diag([1, .5, 1, 1]).ravel().tolist()]}
    static = {**deepcopy(prop), "name": prop["name"] + "_Glow", "weights": None, "bone_indices": None,
              "bind_poses": None, "bone_hashes": []}
    # Another outfit's copy of the prop: other vertices, the same skin, bone paths and renderers.
    alternate = {**deepcopy(prop), "name": "SeparateMesh_Avatar_Type01_Character01_Model_Alternate_Prop01",
                 "vertices": [0, 0, 1, 1, 0, 1, 0, 1.5, 1]}
    extra = {}
    for number, value in enumerate((prop, driven, static, alternate), start=10):
        rows.append({**rows[1], "name": value["name"], "path_id": number})
        extra[number] = value
    # The captured body diffuse is a BC1 block; the body material's _MainTex holds the same
    # bytes, which proves that slot's role. _OtherTex matches nothing and stays unused.
    body_block, prop_block, other_block = bytes(range(8)), bytes(range(8, 16)), bytes(8)
    metadata = json.loads((asset / "hash.json").read_text())
    metadata[0]["texture_hashes"] = [[["Diffuse", ".dds", "33333333", "body-diffuse.dds"]]]
    (asset / "hash.json").write_text(json.dumps(metadata))
    (asset / "body-diffuse.dds").write_bytes(zzz_prop_textures.unity_dds.encode(4, 4, 10, body_block, srgb=True))
    record = index.build_index("ZZMI", root)["assets"][0]
    textures = {20: body_block, 21: prop_block, 22: other_block}

    def material(texture):
        return dict(pointer=dict(cab="CAB-01", path_id=40 + texture, file_ordinal=0, offset=0), name="Material01",
                    textures=[dict(name="_MainTex", texture=dict(cab="CAB-Textures", path_id=texture),
                                   scale=[1, 1], offset=[0, 0]),
                              dict(name="_OtherTex", texture=dict(cab="CAB-Textures", path_id=22),
                                   scale=[1, 1], offset=[0, 0])],
                    floats=[], colors=[])
    for number, (bones, texture) in enumerate(((["", "Root/Joint01"], 20), (["Root/Prop01"], 21)), start=30):
        rows.append({**rows[0], "type": "SkinnedMeshRenderer", "name": "", "path_id": number, "material": material(texture),
                     "renderer": dict(path="Prop01" if bones == ["Root/Prop01"] else "", bone_paths=bones,
                                      avatar=dict(cab="CAB-01", path_id=rows[0]["path_id"]))})
    files = {"cab-textures": [(rows[0]["block"], 4096, 0, "CAB-Textures")]}
    read = client.read

    def read_row(row):
        if row["path_id"] in extra:
            return deepcopy(extra[row["path_id"]])
        if row.get("type") == "SkinnedMeshRenderer":
            return dict(mesh=dict(cab="CAB-01", path_id=0), materials=[row["material"]])
        if row["cab"] == "CAB-Textures":
            (client.work / "texture-1.bin").write_bytes(textures[row["path_id"]])
            return dict(name="Texture", width=4, height=4, format=10, format_name="DXT1", mip_count=1,
                        file="texture-1.bin", size=8)
        value = read(row)
        if row.get("type") == "AnimationClip":
            # The clip also keys the prop bone, lifted off its rest translation, before the Animator column.
            samples = np.frombuffer((client.work / "samples-1.bin").read_bytes(), "<f4").reshape(2, 11)
            prop_columns = np.tile(np.array([0, 0, 1.5, 0, 0, 0, 1, 1, 1, 1], dtype="<f4"), (2, 1))
            joined = np.concatenate([samples[:, :10], prop_columns, samples[:, 10:]], axis=1)
            (client.work / "samples-1.bin").write_bytes(joined.astype("<f4").tobytes())
            bindings = value["bindings"]
            value["bindings"] = bindings[:3] + [dict(path=prop_path, attribute=attribute, type="Transform", custom=0,
                                                     pointer=0) for attribute in (1, 2, 3)] + bindings[3:]
            value["acl_count"] = 21
        for binding in value.get("bindings", []):
            binding["path"] = joint if binding["path"] == 100 else binding["path"]
        return value
    client.read = read_row
    output = tmp_path / "output"
    report, _ = zzz_pipeline.build(client, root, record, output, "Character01", lambda *_: None, threading.Event(),
                                   zzz_index.IndexRows(rows, files))
    assert report["props"] == [dict(id=report["props"][0]["id"], name=prop["name"],
                                    source=zzz_index.identity(rows[3]), textures=["diffuse"])]
    # The alternate copy routes to the same renderers, which name only the captured outfit's prop.
    assert report["skipped_props"] == [dict(name=alternate["name"], source=zzz_index.identity(rows[6]),
                                            reason="Another outfit's mesh: the renderers it routes to are named for a different mesh.")]
    manifest = json.loads((output / "Animation/manifest.json").read_text())
    entry = manifest["props"][0]
    # The clip places the prop above the floor for its whole length, so one window covers it.
    [clip] = manifest["clips"]
    expected_visible = {clip["id"]: [[0.0, clip["duration"]]]}
    assert entry["visible"] == expected_visible
    data = (output / "Animation" / entry["file"]).read_bytes()
    assert entry["vertex_count"] == 3 and entry["index_count"] == 3 and hashlib.sha256(data).hexdigest() == entry["sha256"]
    diffuse = (output / "Animation" / entry["textures"]["diffuse"]["file"]).read_bytes()
    # The prop's own texture, wrapped as a single-mip sRGB BC1 DDS.
    assert set(entry["textures"]) == {"diffuse"} and diffuse[-8:] == prop_block
    assert struct.unpack_from("<I", diffuse, 128)[0] == 72
    rig = validate_rig(json.loads((output / "Rig.json").read_text()))
    records = np.frombuffer(data, "<f4", count=24).reshape(3, 8)
    # Asset space equals the native rest geometry here; UVs flip like captured ones.
    np.testing.assert_allclose(records[:, :3], np.reshape(prop["vertices"], (3, 3)), atol=1e-6)
    np.testing.assert_allclose(records[:, 6:], [[0, 1], [1, 1], [0, 0]], atol=1e-6)
    joints = np.frombuffer(data, "<u2", count=12, offset=96).reshape(3, 4)
    assert rig["bones"][joints[0, 0]]["name"].endswith("Prop01")
    assert np.frombuffer(data, "<u4", count=3, offset=168).tolist() == [0, 1, 2]
    shutil.copytree(output, asset, dirs_exist_ok=True)
    published, released = [], []
    monkeypatch.setattr("app.assets.animations.server.publish_geometry",
                        lambda blob, **_: published.append(blob) or f"/geometry/{len(published)}")
    monkeypatch.setattr("app.assets.animations.server.release_geometry", released.append)
    sessions = AnimationSessions()
    catalog = sessions.catalog(root, "Character01", rig, lambda: True)
    assert catalog["animation_props"] == [dict(id=entry["id"], name=prop["name"])]
    token = catalog["animation_session"]
    first = sessions.load_props(token)["props"][0]
    assert first["visible"] == expected_visible
    assert first["data"] == {"url": "/geometry/1", "length": len(data)} and set(first["textures"]) == {"diffuse"}
    assert first["textures"]["diffuse"].endswith(".dds")
    # Geometry URLs are single-use, so every load publishes fresh ones and releases the last.
    second = sessions.load_props(token)["props"][0]
    assert second["data"]["url"] == "/geometry/2" and released == ["/geometry/1"]
    assert second["textures"] == first["textures"] and published.count(data) == 2
    sessions.clear()
    assert "/geometry/2" in released
    tampered = bytearray(data)
    tampered[0] ^= 1
    (asset / "Animation" / entry["file"]).write_bytes(tampered)
    token = sessions.catalog(root, "Character01", rig, lambda: True)["animation_session"]
    assert sessions.load_props(token)["status"] == "error"
    assert sessions.load_props("invented")["status"] == "error"


@pytest.mark.parametrize("bone, translations, layout, expected", [
    # Prop bone 2 lifted off its rest translation, above the floor, in either clip layout.
    (2, [[0, 0, 1.5]] * 4, "frame-major-tqs-f32le", {"clip01": [[0.0, 0.3]]}),
    (2, [[0, 0, 1.5]] * 4, "frame-major-tq-f32le", {"clip01": [[0.0, 0.3]]}),
    # The prop bone rests on every frame, so the clip does not place the prop.
    (2, [[0, 0, 1]] * 4, "frame-major-tqs-f32le", {}),
    # Placed above the floor for two frames, then parked far below it.
    (2, [[0, 0, 1.5]] * 2 + [[0, -5, 1]] * 2, "frame-major-tqs-f32le", {"clip01": [[0.0, 0.15]]}),
    # A body-only part has no own bones, so the floor alone decides and the clip shows it whole.
    (1, [[0, 0, 1]] * 4, "frame-major-tqs-f32le", {"clip01": [[0.0, 0.3]]}),
])
def test_clip_windows_show_a_rig_prop_only_where_clips_place_it_above_the_floor(bone, translations, layout, expected):
    rig = dict(bones=[
        dict(name="mesh-space", parent=-1, translation=[0, 0, 0], rotation=[0, 0, 0, 1], scale=[1, 1, 1]),
        dict(name="Body", parent=0, translation=[0, 0, 0], rotation=[0, 0, 0, 1], scale=[1, 1, 1]),
        dict(name="Prop", parent=1, translation=[0, 0, 1], rotation=[0, 0, 0, 1], scale=[1, 1, 1])])
    identity = [0, 0, 0, 0, 0, 0, 1, 1, 1, 1]
    frames = np.array([[identity, identity, [*translation, 0, 0, 0, 1, 1, 1, 1]] for translation in translations],
                      dtype="<f4")
    if layout == "frame-major-tq-f32le":
        frames = frames[..., :7]
    metadata = dict(frame_count=len(translations), duration=0.3, bone_count=3, layout=layout)
    positions = np.array([[0, 2, 1], [1, 2, 1], [0, 2.5, 1]], dtype=float)
    joints = np.array([[bone, 0, 0, 0]] * 3, dtype=np.int64)
    weights = np.array([[1, 0, 0, 0]] * 3, dtype=float)
    windows = zzz_props.clip_windows(rig, [("clip01", metadata, frames.tobytes())], [(positions, joints, weights)], {1})
    assert windows == [expected]


def _legacy_dds(fourcc, width, height, data):
    header = struct.pack("<7I44x", 124, 0x1007, height, width, 0, 0, 1)
    pixel_format = struct.pack("<II4s5I", 32, 0x4, fourcc, 0, 0, 0, 0, 0)
    return b"DDS " + header + pixel_format + bytes(20) + data


@pytest.mark.parametrize("raw, expected", [
    # A single-mip BC7 capture keeps every byte after its header.
    (unity_dds.wrap(8, 8, 98, bytes(range(64))), bytes(range(64))),
    # A capture saved with its mip chain yields only the top mip compared with game textures.
    (unity_dds.wrap(8, 8, 98, bytes(range(64)) + bytes(32)), bytes(range(64))),
    (_legacy_dds(b"DXT1", 8, 8, bytes(range(32)) + bytes(8)), bytes(range(32))),
    # A format whose level size is unknown keeps every byte; an incomplete top mip is unusable.
    (unity_dds.wrap(8, 8, 1, bytes(10)), bytes(10)),
    (unity_dds.wrap(8, 8, 98, bytes(40)), None),
])
def test_captured_dds_payloads_compare_only_their_top_mip(raw, expected):
    assert unity_dds.top_mip(raw) == expected


_RENDERER_REASON = "Another outfit's mesh: the renderers it routes to are named for a different mesh."


@pytest.mark.parametrize("paths, routes, expected", [
    # Two candidates share one renderer; only the first is named for it, so the other is dropped.
    ({1: "Weapon"}, {"Mesh_Model_Weapon": [1], "Mesh_Model_Princess_Weapon": [1]},
     {"Mesh_Model_Princess_Weapon": _RENDERER_REASON}),
    # Neither is named for the shared renderer, so nothing is dropped.
    ({1: "Weapon"}, {"Mesh_Model_Alpha": [1], "Mesh_Model_Beta": [1]}, {}),
    # Different renderers do not compete, so each stays.
    ({1: "Weapon", 2: "Other"}, {"Mesh_Model_Weapon": [1], "Mesh_Model_Beta": [2]}, {}),
    # One renderer binds one mesh: the legacy exporter copy yields to the exact-name copy.
    ({1: "Weapon"}, {"Weapon": [1], "Mesh_Model_Weapon": [1]},
     {"Mesh_Model_Weapon": "Another copy of the mesh its renderer is named for is kept."}),
    # Two legacy copies match equally, so both stay.
    ({1: "Weapon"}, {"Alpha_Model_Weapon": [1], "Beta_Model_Weapon": [1]}, {}),
    # No verified renderer draws a candidate with its own bone list, whatever its name.
    ({1: "Weapon"}, {"Mesh_Model_Weapon": [1], "Mesh_Model_Princess_Weapon": []},
     {"Mesh_Model_Princess_Weapon": "The captured outfit's renderers never draw the mesh."}),
])
def test_alternates_drop_unnamed_competitors_of_a_shared_renderer(paths, routes, expected):
    rows = {number: dict(block="B", offset=0, cab="CAB", file_ordinal=0, path_id=number, type="SkinnedMeshRenderer",
                         renderer=dict(path=path)) for number, path in paths.items()}

    def route(chosen, mesh, source):
        return [rows[number] for number in routes[mesh["name"]]]

    candidates = [(name, dict(name=name), name) for name in routes]
    reader = zzz_prop_textures.Reader(None, (), threading.Event(), route)
    assert zzz_prop_textures.alternates(reader, None, candidates) == expected


@pytest.mark.parametrize("drawn, played, windows, expected", [
    # A clip played only by a context that draws no props is removed; the other clip stays.
    ({"ctx_a": False, "ctx_b": True}, {"ctx_a": {"clip01"}, "ctx_b": {"clip02"}},
     {"clip01": [[0, 1]], "clip02": [[0, 1]]}, {"clip02": [[0, 1]]}),
    # A clip that a drawing context also plays stays.
    ({"ctx_a": False, "ctx_b": True}, {"ctx_a": {"clip01"}, "ctx_b": {"clip01"}},
     {"clip01": [[0, 1]]}, {"clip01": [[0, 1]]}),
    # A clip no known context plays keeps its windows.
    ({"ctx_a": False}, {"ctx_a": {"clip01"}}, {"clip02": [[0, 1]]}, {"clip02": [[0, 1]]}),
    # Without known contexts nothing changes.
    ({}, {}, {"clip01": [[0, 1]]}, {"clip01": [[0, 1]]}),
])
def test_filter_contexts_removes_clips_only_non_prop_drawing_contexts_play(drawn, played, windows, expected):
    assert zzz_props.filter_contexts([windows], drawn, played) == [expected]


def _animator(path_id):
    return dict(animator=dict(name="Animator", controller=dict(cab="CAB-01", path_id=path_id)))


class _ReadClient:
    def __init__(self, reads):
        self.reads = reads

    def read(self, row):
        value = self.reads[row["path_id"]]
        if isinstance(value, Exception):
            raise value
        return value


@pytest.mark.parametrize("reads, expected", [
    ({1: dict(visible=True, **_animator(7))}, {("cab-01", 7): True}),
    # Only renderers that are not drawn by default (False or unknown) leave the context hidden.
    ({1: dict(visible=False, **_animator(7)), 2: dict(visible=None, **_animator(7))}, {("cab-01", 7): False}),
    # A null controller (path_id 0) or a missing animator is ignored.
    ({1: dict(visible=True, animator=dict(controller=dict(cab="CAB-01", path_id=0)))}, {}),
    ({1: dict(visible=True)}, {}),
    # A read that raises is skipped; other rows still count.
    ({1: ValueError("unreadable"), 2: dict(visible=True, **_animator(7))}, {("cab-01", 7): True}),
    ({1: ValueError("unreadable")}, {}),
])
def test_contexts_report_whether_a_controller_draws_props_by_default(reads, expected):
    rows = {number: dict(block="B", offset=0, cab="CAB", file_ordinal=0, path_id=number,
                         type="SkinnedMeshRenderer", renderer=dict(path="Weapon")) for number in reads}
    reader = zzz_prop_textures.Reader(_ReadClient(reads), (), threading.Event(),
                                      lambda chosen, mesh, source: [rows[number] for number in reads])
    found = zzz_prop_textures.contexts(reader, None, [(dict(name="Mesh"), "source")])
    assert found == expected


def test_controller_clips_follow_every_leaf_of_one_read_per_indexed_controller():
    def row(kind, cab, path_id, block="B1"):
        return dict(name="Item", type=kind, block=block, cab=cab, offset=0, file_ordinal=0, path_id=path_id)
    clip_rows = [row("AnimationClip", "CAB-Clips", number) for number in (1, 2, 3)]
    converted = {zzz_index.identity(clip): (f"clip{clip['path_id']}", 1.0) for clip in clip_rows}
    # Two index copies of one controller, plus one that cannot be read.
    controllers = [row("AnimatorController", "CAB-Ctrl", 10), row("AnimatorController", "CAB-Ctrl", 10, "B2"),
                   row("AnimatorController", "CAB-Ctrl", 11)]
    def leaf(number):
        return dict(cab="cab-clips", path_id=number)
    reads = []

    class Client:
        def read(self, item):
            reads.append(item["path_id"])
            if item["path_id"] == 11:
                raise ValueError("unreadable")
            # A blend motion's every leaf counts; a pointer to an unconverted clip plays nothing.
            return dict(states=[dict(motions=[dict(leaves=[leaf(1), leaf(2)])]), dict(motions=[dict(leaves=[leaf(9)])])])
    keys = [("cab-ctrl", 10), ("cab-ctrl", 11), ("cab-ctrl", 12)]
    played = zzz_pipeline._controller_clips(Client(), clip_rows + controllers, keys, clip_rows, converted,
                                            threading.Event())
    assert played == {("cab-ctrl", 10): {"clip1", "clip2"}} and sorted(reads) == [10, 11]


def test_zzz_extraction_rejects_uvless_candidate_and_keeps_verified_rig_and_clips(tmp_path):
    root, asset, record, _avatar, _mesh, rows, client = fixture(tmp_path)
    helper = {**rows[1], "name": rows[1]["name"] + "_Helper", "path_id": 99}
    # A shape-compatible static mesh exports without skin data and must be skipped.
    static = {**rows[1], "name": rows[1]["name"] + "_Static", "path_id": 98}
    rows[1:1] = [helper, static]
    read = client.read
    visited = []
    def read_candidate(row):
        data = read(row)
        visited.append(zzz_index.identity(row))
        if row is helper:
            data["uv"] = None
        elif row is static:
            data.update(weights=None, bone_indices=None, bind_poses=None, bone_hashes=[])
        return data
    client.read = read_candidate
    before = {file.name: file.read_bytes() for file in asset.iterdir()}
    output = tmp_path / "output"
    report, _ = zzz_pipeline.build(client, root, record, output, "Character01", lambda *_: None, threading.Event(), rows)
    assert {zzz_index.identity(helper), zzz_index.identity(static)} <= set(visited)
    assert report["meshes"] == [zzz_index.identity(rows[3])]
    assert report["proven_components"] == report["imported"] == report["clips"] == 1
    rig = validate_rig(json.loads((output / "Rig.json").read_text()))
    entry = json.loads((output / "Animation/manifest.json").read_text())["clips"][0]
    metadata, data = read_clip(str(output / "Animation"), entry, rig)
    assert metadata["frame_count"] == 61 and data
    assert isinstance(metadata["keyed_bones"], list) and metadata["keyed_bones"]
    assert isinstance(json.loads((output / "Animation/manifest.json").read_text())["runtime_bones"], list)
    assert before == {file.name: file.read_bytes() for file in asset.iterdir()}
    # Without a UV-verifiable mesh, fail with the ordinary proof diagnostic.
    captured, _ = zzz_native.capture(root, record)
    with pytest.raises(ValueError, match="No ZZZ mesh could be verified"):
        zzz_pipeline.verify_candidates(client, captured, [rows[0], helper], "Character01", threading.Event())


def test_zzz_symmetric_component_requires_a_unique_model_wide_placement(tmp_path):
    root, _asset, record, _avatar, mesh, rows, client = fixture(tmp_path)
    anchor = zzz_native.capture(root, record)[0][0]
    symmetric = deepcopy(anchor)
    symmetric["positions"] = np.array([[-1, .3, .4], [1, .3, .4], [0, 1.2, .7]], dtype=np.float32)
    symmetric["uv"] = np.tile([.1, .8], (3, 1))
    symmetric_mesh = deepcopy(mesh)
    symmetric_mesh["vertices"] = symmetric["positions"].ravel().tolist()
    symmetric_mesh["uv"] = np.tile([.1, .2], (3, 1)).ravel().tolist()
    original_read = client.read
    def read(row):
        return deepcopy(symmetric_mesh) if row["path_id"] == rows[1]["path_id"] else original_read(row)
    client.read = read
    cancel = threading.Event()
    assert len(zzz_native.match_component_candidates(symmetric, symmetric_mesh)) == 2
    with pytest.raises(ValueError, match="ambiguous"):
        zzz_pipeline.verify_candidates(client, [symmetric], rows, "Character01", cancel)
    # The asymmetric component proves which reflected hypothesis belongs to the
    # shared Avatar placement, without discarding the symmetric component.
    anchor["component_ordinal"] = 1
    candidates = [*rows, {**rows[1], "path_id": 50}]
    chosen = zzz_pipeline.verify_candidates(client, [symmetric, anchor], candidates, "Character01", cancel)
    reordered = zzz_pipeline.verify_candidates(client, [symmetric, anchor], list(reversed(candidates)), "Character01", cancel)
    assert len(chosen["matched"]) == 2 and chosen["rig"]["id"] == reordered["rig"]["id"]
    assert chosen["rig"]["bones"][0]["scale"] == pytest.approx([1, 1, 1])


def test_zzz_discovery_skips_unrelated_variants_before_native_avatar_reads(tmp_path):
    root, _asset, record, _avatar, _mesh, rows, client = fixture(tmp_path)
    captured, _files = zzz_native.capture(root, record)
    alternate = {**rows[0], "name": "Avatar_Type01_Character01_Alternate_ModelAvatar", "path_id": 50}
    alternate_mesh = {**rows[1], "name": "SeparateMesh_Avatar_Type01_Character01_Alternate_Model_Body", "shape": [[50, 60]], "path_id": 51}
    no_mesh = {**rows[0], "name": "Avatar_Type01_Character01_Extra_ModelAvatar", "path_id": 52}
    reads = []
    original_read = client.read
    def read(row):
        reads.append(row["path_id"])
        if row["path_id"] in (50, 51, 52):
            raise ValueError("Synthetic unsupported candidate metadata.")
        return original_read(row)
    client.read = read
    candidates = [*rows, alternate, alternate_mesh, no_mesh]
    chosen = zzz_pipeline.verify_candidates(client, captured, candidates, "Character01", threading.Event())
    assert chosen["family"] == "Avatar_Type01_Character01" and len(chosen["matched"]) == 1
    assert not {50, 51, 52} & set(reads)
    # A shape-compatible unreadable candidate must still fail closed rather than
    # disappearing from the uniqueness check for the chosen native skeleton.
    alternate_mesh["shape"] = [[3, 3]]
    with pytest.raises(ValueError, match="unsupported candidate"):
        zzz_pipeline.verify_candidates(client, captured, candidates, "Character01", threading.Event())


def test_zzz_shape_fallback_keeps_body_avatar_when_a_prop_has_an_extra_legacy_mesh_hint(tmp_path):
    root, _asset, record, avatar, mesh, rows, client = fixture(tmp_path)
    mesh["name"] = rows[1]["name"] = "Character01_Body"
    rows[1]["shape"] = [[3, 3]]
    avatar["bones"].append(dict(path="Root/Joint02", hash=200, parent=0,
        translation=[0, 0, 0], rotation=[0, 0, 0, 1], scale=[1, 1, 1]))
    # This prop has a standalone legacy-named mesh, but its skeleton proves only
    # that prop; the body Avatar proves both parts and owns the animation family.
    prop_avatar = deepcopy(avatar)
    prop_avatar["bones"] = [prop_avatar["bones"][0], prop_avatar["bones"][2]]
    prop_avatar["name"] = "Avatar_Type01_Character01_Prop_ModelAvatar"
    prop_mesh = deepcopy(mesh)
    prop_mesh["name"] = "SeparateMesh_Avatar_Type01_Character01_Prop_Model_Prop"
    prop_mesh["bone_hashes"] = [0, 200]
    prop_mesh["bind_poses"] = [np.eye(4).ravel(order="C").tolist()] * 2
    prop_mesh["vertices"] = (np.asarray(mesh["vertices"]).reshape(-1, 3) + [0, 0, 2]).ravel().tolist()
    prop_rows = [{**rows[0], "name": prop_avatar["name"], "path_id": 50},
                 {**rows[1], "name": prop_mesh["name"], "path_id": 51}]
    captured, _files = zzz_native.capture(root, record)
    prop_part = deepcopy(captured[0])
    prop_part["positions"] = prop_part["positions"] + [0, 0, 2]
    prop_part["component_ordinal"] = 1
    # A different triangle count makes the prop add to the shape discovery score.
    prop_part["indices"] = np.concatenate([prop_part["indices"], prop_part["indices"]])
    prop_part["index_count"] = 6
    prop_mesh["indices"] *= 2
    prop_mesh["sections"][0]["count"] = 6
    prop_rows[1]["shape"] = [[3, 6]]
    original_read = client.read
    def read(row):
        return deepcopy({50: prop_avatar, 51: prop_mesh}[row["path_id"]]) if row["path_id"] in (50, 51) else original_read(row)
    client.read = read
    chosen = zzz_pipeline.verify_candidates(client, [*captured, prop_part], [*rows, *prop_rows], "UnmatchedQuery", threading.Event())
    assert chosen["family"] == "Avatar_Type01_Character01"
    assert len(chosen["matched"]) == 2 and len(chosen["rig"]["bones"]) == 4
    assert chosen["matched"][1]["bone_map"] == {"8": 1, "12": 3}


@pytest.mark.parametrize("difference", ["roundoff", "rest", "family", "model_helper", "model_hierarchy"])
def test_zzz_equivalent_native_avatar_copies_and_genuine_ambiguity(tmp_path, difference):
    root, _asset, record, avatar, mesh, rows, client = fixture(tmp_path)
    mesh["name"] = rows[1]["name"] = "Character01_Body"
    rows[1]["shape"] = [[3, 3]]
    if difference in ("rest", "model_helper", "model_hierarchy"):
        avatar["bones"].append(dict(path="Root/Unused01", hash=200, parent=0,
            translation=[0, 0, 0], rotation=[0, 0, 0, 1], scale=[1, 1, 1]))
    duplicate = deepcopy(avatar)
    duplicate_row = {**rows[0], "path_id": 50}
    if difference == "roundoff":
        duplicate["bones"][1]["translation"][0] = 2e-7
    elif difference in ("rest", "model_helper"):
        duplicate["bones"][2]["translation"][0] = .01
    elif difference == "model_hierarchy":
        duplicate["bones"][2]["parent"] = 1
    else:
        duplicate["name"] = duplicate_row["name"] = "Avatar_Type01_Character01_Variant_ModelAvatar"
    if difference.startswith("model_"):
        # A larger animation Avatar of the same family drives the model Avatar's mesh.
        duplicate["name"] = duplicate_row["name"] = "Avatar_Type01_Character01_Avatar"
        duplicate["bones"].append(dict(path="Root/Extra01", hash=300, parent=0,
            translation=[0, 0, 0], rotation=[0, 0, 0, 1], scale=[1, 1, 1]))
    original_read = client.read
    def read(row):
        return deepcopy(duplicate) if row["path_id"] == 50 else original_read(row)
    client.read = read
    captured, _files = zzz_native.capture(root, record)
    candidates = [*rows, duplicate_row]
    if difference == "roundoff":
        chosen = zzz_pipeline.verify_candidates(client, captured, candidates, "Character01", threading.Event())
        reordered = zzz_pipeline.verify_candidates(client, captured, list(reversed(candidates)), "Character01", threading.Event())
        assert len(chosen["matched"]) == 1 and chosen["rig"]["id"] == reordered["rig"]["id"]
        assert chosen["source"] == min(zzz_index.identity(row) for row in (rows[0], duplicate_row))
    elif difference == "model_helper":
        # Only an unskinned helper's rest differs, so the animation Avatar's rig is adopted.
        for ordered in (candidates, list(reversed(candidates))):
            chosen = zzz_pipeline.verify_candidates(client, captured, ordered, "Character01", threading.Event())
            assert chosen["source"] == zzz_index.identity(duplicate_row)
            assert chosen["avatar_sources"] == {zzz_index.identity(row) for row in (rows[0], duplicate_row)}
            assert chosen["rig"]["bones"][3]["translation"][0] == pytest.approx(.01, abs=1e-6)
    else:
        with pytest.raises(ValueError, match="ambiguous"):
            zzz_pipeline.verify_candidates(client, captured, candidates, "Character01", threading.Event())


@pytest.mark.parametrize("case", ["copy_newer", "original_newer", "tied", "missing_block", "different_object"])
def test_zzz_stale_block_version_of_one_avatar_yields_to_the_newest_block(tmp_path, case):
    root, _asset, record, avatar, mesh, rows, client = fixture(tmp_path)
    mesh["name"] = rows[1]["name"] = "Character01_Body"
    rows[1]["shape"] = [[3, 3]]
    avatar["bones"].append(dict(path="Root/Unused01", hash=200, parent=0,
        translation=[0, 0, 0], rotation=[0, 0, 0, 1], scale=[1, 1, 1]))
    version = deepcopy(avatar)
    version["bones"][-1]["translation"][0] = .01
    other_block = "ZenlessZoneZero_Data/StreamingAssets/Blocks/2.blk"
    version_row = {**rows[0], "block": other_block, **({"path_id": 50} if case == "different_object" else {})}
    original_read = client.read
    def read(row):
        return deepcopy(version) if row["block"] == other_block else original_read(row)
    client.read = read
    game = tmp_path / "game"
    client.root = game
    for row, stamp in ((rows[0], 1_000), (version_row, 1_000 if case == "tied" else 2_000 if case == "copy_newer" else 500)):
        file = game / row["block"]
        if case == "missing_block" and row is version_row:
            continue
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b"block")
        os.utime(file, ns=(stamp * 10**9, stamp * 10**9))
    captured, _files = zzz_native.capture(root, record)
    candidates = [*rows, version_row]
    if case in ("copy_newer", "original_newer"):
        newest = version_row if case == "copy_newer" else rows[0]
        for ordered in (candidates, list(reversed(candidates))):
            chosen = zzz_pipeline.verify_candidates(client, captured, ordered, "Character01", threading.Event())
            assert chosen["source"] == zzz_index.identity(newest)
            assert chosen["avatar_sources"] == {zzz_index.identity(newest)}
            assert chosen["rig"]["bones"][-1]["translation"][0] == pytest.approx(.01 if case == "copy_newer" else 0, abs=1e-6)
    else:
        with pytest.raises(ValueError, match="ambiguous"):
            zzz_pipeline.verify_candidates(client, captured, candidates, "Character01", threading.Event())


def test_zzz_case_only_clip_names_keep_distinct_files_and_stable_reimport_ids(tmp_path):
    root, asset, record, _avatar, _mesh, rows, client = fixture(tmp_path)
    rows[-1]["name"] = "Avatar_Type01_Character01_Ani_Sequence"
    rows.append({**rows[-1], "path_id": 50, "name": "Avatar_Type01_Character01_Ani_sequence"})
    # A two-state controller chains the two converted clips: Any State enters state 0, which
    # leaves for state 1 after one input press and a frame window.
    def leaf(path_id):
        return dict(motions=[dict(leaves=[dict(cab="CAB-01", path_id=path_id)])])
    def edge(dest, conditions, fade=0.0):
        return dict(dest=dest, duration=fade, offset=0.0, exit_time=1.0, has_exit_time=False, fixed_duration=True,
                    conditions=[dict(param=param, mode=mode, threshold=value) for param, mode, value in conditions])
    controller = dict(states=[
        dict(name="Idle", path="Base Layer.Idle", transitions=[edge(1, [("Input01", 1, 0), ("FrameCount", 9, 6)], fade=0.1)], **leaf(2)),
        dict(name="Attack", path="Base Layer.Attack", transitions=[], **leaf(50)),
    ], selectors=[], any_state=[edge(0, [("Input01", 1, 0)])])
    # Controller names drop the family's internal underscores; an index copy of the same
    # object in another block is one controller, and another variant's controller is ignored.
    named = {**rows[0], "name": "Avatar_Type01Character01_Controller", "type": "AnimatorController", "path_id": 60}
    rows += [named, {**named, "block": "ZenlessZoneZero_Data/StreamingAssets/Blocks/2.blk"},
             {**named, "name": "Avatar_Type01_Character01Extra_Controller", "path_id": 61}]
    read, controllers = client.read, []
    def read_controller(row):
        if row["type"] != "AnimatorController":
            return read(row)
        controllers.append(row["name"])
        return deepcopy(controller)
    client.read = read_controller
    output = tmp_path / "output"
    report, _ = zzz_pipeline.build(client, root, record, output, "Character01", lambda *_: None, threading.Event(), rows)
    assert report["clips"] == 2 and report["imported"] == 2 and report["sequences"] == 1
    assert controllers == ["Avatar_Type01Character01_Controller"]
    manifest = json.loads((output / "Animation/manifest.json").read_text())
    entries = manifest["clips"]
    assert len({entry["file"].casefold() for entry in entries}) == 2
    sources = [json.loads((output / "Animation" / entry["file"]).read_text())["source"] for entry in entries]
    assert len(set(sources)) == 2
    (sequence,) = manifest["sequences"]
    assert [step["clip"] for step in sequence["steps"]] == [entries[0]["id"], entries[1]["id"]]
    assert sequence["steps"][1]["leave"] == entries[1]["duration"] and sequence["steps"][1]["fade"] == 0
    assert sequence["steps"][0]["start"] == 0 and sequence["steps"][0]["leave"] == pytest.approx(0.1)
    assert sequence["steps"][0]["fade"] == pytest.approx(0.1)
    shutil.copyfile(output / "Rig.json", asset / "Rig.json")
    shutil.copytree(output / "Animation", asset / "Animation")
    second = tmp_path / "second"
    zzz_pipeline.build(client, root, record, second, "Character01", lambda *_: None, threading.Event(), rows)
    assert json.loads((second / "Animation/manifest.json").read_text())["clips"] == entries
    assert json.loads((second / "Animation/manifest.json").read_text())["sequences"] == manifest["sequences"]


@pytest.mark.parametrize("case", ["complete", "failure", "cancelled", "serial"])
def test_parallel_index_scan_keeps_archive_order_and_stops_every_helper(tmp_path, monkeypatch, case):
    monkeypatch.setattr(zzz_index.os, "cpu_count", lambda: 8)
    files = [f"block-{number:03}" for number in range(100)]
    failing = files[3 * zzz_index.SCAN_BATCH] if case == "failure" else None
    helpers, consumed = [], []
    cancel = threading.Event()

    class Helper:
        def __init__(self, work, stop):
            self.stop, self.closed = stop, False

        def scan_many(self, batch):
            if batch[0] == failing:
                raise ValueError("Synthetic scan failure.")
            # Later batches finish first so completion order differs from archive order.
            for _ in range((len(files) - files.index(batch[0])) // 10):
                if self.stop.is_set():
                    raise ExtractionCancelled("Stopped.")
                time.sleep(.001)
            return [dict(block=name) for name in batch]

        def close(self):
            self.closed = True

    class Client(Helper):
        work = tmp_path / "work"

        def clone(self, work, stop):
            assert work.parent == self.work and work.is_dir()
            helpers.append(Helper(work, stop))
            return helpers[-1]

    Client.work.mkdir()
    client = Helper(None, cancel) if case == "serial" else Client(None, cancel)
    def progress(_phase, current, _total):
        if case == "cancelled" and current:
            cancel.set()
    if case in ("complete", "serial"):
        zzz_index.scan_batches(client, files, progress, cancel, consumed.append)
        assert [row["block"] for batch in consumed for row in batch] == files
        assert len(helpers) == (0 if case == "serial" else zzz_index.MAX_SCAN_WORKERS)
    else:
        expected = (ValueError, "Synthetic scan failure") if case == "failure" else (ExtractionCancelled, "cancelled")
        with pytest.raises(expected[0], match=expected[1]):
            zzz_index.scan_batches(client, files, progress, cancel, consumed.append)
        # Earlier batches were consumed in order; every helper was told to stop.
        assert len(consumed) == (3 if case == "failure" else 1)
        assert all(helper.stop.is_set() for helper in helpers)
    assert all(helper.closed for helper in helpers)


def test_zzz_index_composite_identity_staleness_and_failed_build_preserve_cache(tmp_path, monkeypatch):
    folder = tmp_path / "game"
    blocks = folder / zzz_runtime.BLOCK_DIRS[0]
    blocks.mkdir(parents=True)
    (blocks / "1.blk").write_bytes(b"block")
    monkeypatch.setattr(paths, "asset_index_dir", lambda: str(tmp_path / "index"))
    row = dict(name="Mesh01", type="Mesh", block="", cab="CAB-01", offset=0, file_ordinal=0, path_id=1, shape=[[3, 3]])
    class Client:
        def scan_many(self, files):
            return [{**row, "block": file.relative_to(folder).as_posix()} for file in files]
    cancel = threading.Event()
    client = Client()
    assert zzz_index.status(folder)["state"] == "missing"
    rows, count = zzz_index.build(client, folder, lambda *_: None, cancel)
    assert count == 1 and zzz_index.status(folder)["state"] == "ready"
    with patch.object(client, "scan_many", side_effect=AssertionError("A ready index must be reused.")):
        assert zzz_index.ensure(client, folder, lambda *_: None, cancel) == rows
        monkeypatch.setattr(zzz_runtime, "VERSION", "synthetic-clip-runtime-update")
        assert zzz_index.ensure(client, folder, lambda *_: None, cancel) == rows
    # A schema upgrade needs one completed rebuild; subsequent extractions use
    # that persisted result, including after the indexing client is replaced.
    file = zzz_index.index_path(folder)
    previous = json.loads(file.read_bytes())
    previous["format"] = 0
    file.write_text(json.dumps(previous))
    assert zzz_index.status(folder)["state"] == "stale"
    assert zzz_index.ensure(client, folder, lambda *_: None, cancel) == rows
    replacement = Client()
    with patch.object(replacement, "scan_many", side_effect=AssertionError("The rebuilt index must be reused.")):
        assert zzz_index.ensure(replacement, folder, lambda *_: None, cancel) == rows
    assert zzz_index.identity(rows[0]) != zzz_index.identity({**rows[0], "offset": 4})
    assert zzz_index.identity(rows[0]) != zzz_index.identity({**rows[0], "file_ordinal": 1})
    before = zzz_index.index_path(folder).read_bytes()
    row["offset"] = -1
    with pytest.raises(ValueError, match="incomplete"):
        zzz_index.build(Client(), folder, lambda *_: None, cancel)
    assert zzz_index.index_path(folder).read_bytes() == before
    (blocks / "1.blk").write_bytes(b"updated block")
    assert zzz_index.status(folder)["state"] == "stale"
    zzz_index.remove(folder)
    assert zzz_index.status(folder)["state"] == "missing"


def test_zzz_clip_rejects_unknown_transform_and_uncontained_native_samples(tmp_path):
    _root, _asset, _record, avatar, _mesh, rows, client = fixture(tmp_path)
    metadata = client.read(rows[-1])
    rig = dict(bones=[dict(translation=[0, 0, 0], rotation=[0, 0, 0, 1], scale=[1, 1, 1])] * 3)
    metadata["bindings"][0]["path"] = 999
    with pytest.raises(ValueError, match="unsupported transform"):
        zzz_animation.convert(metadata, client.work, rig, avatar)
    metadata["file"] = "../samples.bin"
    with pytest.raises(ValueError, match="incomplete"):
        zzz_animation.convert(metadata, client.work, rig, avatar)


def test_zzz_index_pools_exact_renderer_paths_and_reuses_complete_cache(tmp_path, monkeypatch):
    folder = tmp_path / "game"
    blocks = folder / zzz_runtime.BLOCK_DIRS[0]
    blocks.mkdir(parents=True)
    (blocks / "1.blk").write_bytes(b"block")
    monkeypatch.setattr(paths, "asset_index_dir", lambda: str(tmp_path / "index"))
    bones = ["Root/" + "Joint01/" * 64 + f"Joint{i:02d}" for i in range(32)] + [None, "", "Root/Joint\u00e9"]
    rows = [dict(name="", type="SkinnedMeshRenderer", block=f"{zzz_runtime.BLOCK_DIRS[0]}/1.blk", cab="CAB-01",
                 offset=0, file_ordinal=0, path_id=i, renderer=dict(path=f"Root/Part{i:03d}", bone_paths=list(bones),
                    mesh=dict(cab="CAB-01", path_id=0), avatar=dict(cab="CAB-01", path_id=500), blend_shape_count=0))
            for i in range(128)]
    expected = deepcopy(rows)
    cancel = threading.Event()
    client = type("Client", (), {"scan_many": lambda self, files: deepcopy(rows)})()
    monkeypatch.setattr(zzz_index, "MAX_BYTES", 96 * 1024)
    assert len(json.dumps(rows).encode()) > zzz_index.MAX_BYTES * 10
    built, count = zzz_index.build(client, folder, lambda *_: None, cancel)
    assert built == expected and rows == expected and count == 0
    file = zzz_index.index_path(folder)
    saved = json.loads(file.read_bytes())
    assert file.stat().st_size < zzz_index.MAX_BYTES and saved["bone_paths"] == bones
    assert all("bone_paths" not in row["renderer"] for row in saved["rows"])
    with patch.object(client, "scan_many", side_effect=AssertionError("A complete cache must be reused.")):
        loaded = zzz_index.ensure(client, folder, lambda *_: None, cancel)
    assert loaded == expected
    assert loaded[0]["renderer"]["bone_paths"][0] is loaded[-1]["renderer"]["bone_paths"][0]
    before = file.read_bytes()
    monkeypatch.setattr(zzz_index, "MAX_BYTES", 16)
    with pytest.raises(ValueError, match="size limit"):
        zzz_index.build(client, folder, lambda *_: None, cancel)
    assert file.read_bytes() == before
    # Indexes from before the material/texture file table need one rescan.
    monkeypatch.setattr(zzz_index, "MAX_BYTES", 4 * 1024 * 1024)
    saved.pop("bone_paths")
    saved["format"] = 3
    saved["rows"] = expected
    file.write_text(json.dumps(saved))
    assert zzz_index.read(folder)[0] == "stale"


@pytest.mark.parametrize("case", ["missing", "table", "entry", "negative", "range", "boolean", "mixed", "budget"])
def test_zzz_index_rejects_invalid_pooled_bone_paths(tmp_path, monkeypatch, case):
    folder = tmp_path / "game"
    blocks = folder / zzz_runtime.BLOCK_DIRS[0]
    blocks.mkdir(parents=True)
    (blocks / "1.blk").write_bytes(b"block")
    monkeypatch.setattr(paths, "asset_index_dir", lambda: str(tmp_path / "index"))
    renderer = dict(path="Root/Part01", bone_path_ids=[0])
    value = dict(format=zzz_index.FORMAT, fingerprint=zzz_index.fingerprint(folder), bone_paths=["Root/Joint01"], rows=[
        dict(name="", type="SkinnedMeshRenderer", block="1.blk", cab="CAB-01", offset=0, file_ordinal=0, path_id=1, renderer=renderer)])
    if case == "missing":
        del value["bone_paths"]
    elif case == "table":
        value["bone_paths"] = {}
    elif case == "entry":
        value["bone_paths"] = [1]
    elif case in ("negative", "range", "boolean"):
        renderer["bone_path_ids"] = [{"negative": -1, "range": 1, "boolean": True}[case]]
    elif case == "mixed":
        renderer["bone_paths"] = ["Root/Joint01"]
    else:
        monkeypatch.setattr(zzz_index, "MAX_BONE_REFERENCES", 0)
    file = zzz_index.index_path(folder)
    file.parent.mkdir()
    file.write_text(json.dumps(value))
    assert zzz_index.read(folder) == ("stale", None, 0)


@pytest.mark.parametrize("step,translation", [(False, .15625), (True, 0)])
def test_zzz_mixed_streamed_dense_constant_tracks_without_acl(tmp_path, step, translation):
    _root, _asset, _record, avatar, _mesh, rows, client = fixture(tmp_path)
    metadata = client.read(rows[-1])
    (client.work / metadata["file"]).write_bytes(b"")
    metadata.update(acl_count=0, times=[], streamed_count=3,
        dense=dict(count=4, frames=2, rate=1, start=0, values=[0, 0, 0, 1, 0, 0, 1, 0]),
        constant=[1, 2, 1], bindings=metadata["bindings"][:3], streamed=[dict(time=time, keys=[
            dict(index=i, value=time if i == 0 else 0, incoming=0, outgoing="Infinity" if step else 0)
            for i in range(3)]) for time in (0, 1)])
    rig = dict(bones=[dict(translation=[0, 0, 0], rotation=[0, 0, 0, 1], scale=[1, 1, 1])] * 3)
    data, frames, duration, omitted, morph, keyed, root_motion = zzz_animation.convert(metadata, client.work, rig, avatar)
    samples = np.frombuffer(data, dtype="<f4").reshape(frames, 3, 10)
    assert duration == 1 and omitted == 0 and morph is None and root_motion is None
    # The only transform track targets avatar bone hash 100, the rig's bone 2.
    assert keyed == [2]
    assert samples[15, 2, 0] == pytest.approx(translation)
    assert samples[15, 2, 3:7] == pytest.approx([0, 0, np.sin(np.pi / 8), np.cos(np.pi / 8)])
    assert samples[-1, 2, 0] == 1 and list(samples[-1, 2, 7:]) == [1, 2, 1]


@pytest.mark.parametrize("attributes", [tuple(range(7)), tuple(range(6))])
def test_zzz_root_motion_track_conversion_validation_and_publication(tmp_path, monkeypatch, attributes):
    from app.assets.animations import AnimationSessions, read_clip

    _root, _asset, _record, avatar, _mesh, rows, client = fixture(tmp_path)
    metadata = client.read(rows[-1])
    # MotionT.xyz then an unnormalized identity MotionQ.xyzw, as constant Animator muscles.
    motion = [.5, 0, -2, 0, 0, 0, 2]
    metadata["constant"] = [motion[attribute] for attribute in attributes]
    metadata["bindings"] += [dict(path=0, attribute=attribute, type="Animator", custom=8, pointer=0)
                             for attribute in attributes]
    rig = dict(id="a" * 64, bones=[dict(name=f"Joint{i:02}", parent=i - 1, translation=[0, 0, 0],
                                        rotation=[0, 0, 0, 1], scale=[1, 1, 1]) for i in range(3)])
    data, frames, _duration, omitted, _morph, _keyed, root_motion = zzz_animation.convert(
        metadata, client.work, rig, avatar)
    bone_bytes = frames * 3 * 10 * 4
    if len(attributes) < 7:
        # An incomplete muscle set is an omitted channel family, never a guessed track.
        assert root_motion is None and omitted == 1 + len(attributes) and len(data) == bone_bytes
        return
    # The track lives in the Animator's space: the Avatar root after the mesh-space root.
    assert root_motion == {"bone": 1} and omitted == 1
    track = np.frombuffer(data, "<f4", offset=bone_bytes).reshape(frames, 7)
    assert track == pytest.approx(np.tile([.5, 0, -2, 0, 0, 0, 1], (frames, 1)))
    library = tmp_path / "Asset01" / "Animation"
    library.mkdir(parents=True)
    raw = dict(format="mod-viewer-animation", version=2, coordinate_space="asset", layout="frame-major-tqs-f32le",
               skeleton_id=rig["id"], frame_count=frames, bone_count=3, duration=1, data_file="Clip01.bin",
               sha256=hashlib.sha256(data).hexdigest(), root_motion=root_motion)
    entry = dict(id="clip01", name="Clip 01", file="Clip01.json", duration=1)
    (library / "Clip01.bin").write_bytes(data)
    (library / "manifest.json").write_text(json.dumps(dict(
        format="mod-viewer-animations", version=1, skeleton_id=rig["id"], clips=[entry])))

    def write(**changes):
        (library / "Clip01.json").write_text(json.dumps({**raw, **changes}))

    write()
    assert read_clip(str(library), entry, rig)[1] == data
    monkeypatch.setattr("app.assets.animations.server.publish_geometry", lambda *_args, **_kwargs: "/geometry/01")
    sessions = AnimationSessions()
    token = sessions.catalog(str(tmp_path), "Asset01", rig, lambda: True)["animation_session"]
    assert sessions.load(token, "clip01")["clip"]["root_motion"] == {"bone": 1}
    for change in (dict(root_motion={"bone": 3}), dict(root_motion={"bone": 1, "space": 0}), dict(root_motion=[1])):
        write(**change)
        with pytest.raises(ValueError, match="root motion does not match"):
            read_clip(str(library), entry, rig)
    # The track's quaternions must be unit length like every other rotation.
    corrupt = bytearray(data)
    struct.pack_into("<f", corrupt, bone_bytes + 6 * 4, 2)
    (library / "Clip01.bin").write_bytes(corrupt)
    write(sha256=hashlib.sha256(corrupt).hexdigest())
    with pytest.raises(ValueError, match="invalid root-motion track"):
        read_clip(str(library), entry, rig)
    write(root_motion=None)
    with pytest.raises(ValueError, match="incomplete or has changed"):
        read_clip(str(library), entry, rig)

@pytest.mark.parametrize("triangles,sections,accepted", [
    ([0, 1, 2, 0, 1, 3, 0, 1, 3], [(0, 9)], True),
    ([0, 1, 2, 0, 1, 3, 0, 2, 3], [(0, 9)], False),
    ([0, 1, 2, 0, 1, 3, 0, 1, 3], [(0, 3), (3, 6)], False),
    ([0, 2, 3, 0, 1, 2, 0, 1, 3, 0, 1, 3], [(0, 3), (3, 9)], True),
])
def test_zzz_geometry_proof_preserves_complete_section_triangle_multiplicity(triangles, sections, accepted):
    positions = np.array([[.2, .3, .4], [1.2, .4, .8], [.4, 1.2, .7], [.9, .8, .6]])
    uv = np.array([[.1, .2], [.3, .4], [.5, .6], [.7, .8]])
    part = dict(positions=positions, uv=uv, source_vertices=[0, 1, 2, 3],
        indices=np.array([[0, 1, 2], [0, 1, 3], [0, 1, 3]]), blends=[(.75, .25, 0, 0, 8, 12, 0, 0)] * 4)
    mesh = dict(vertices=positions.ravel().tolist(), uv=(uv * [1, -1] + [0, 1]).ravel().tolist(),
        indices=triangles, weights=[[.75, .25, 0, 0]] * 4, bone_indices=[[0, 1, 0, 0]] * 4,
        sections=[dict(first=first, count=count) for first, count in sections])
    assert (zzz_native.match_component(part, mesh) is not None) == accepted
    # Reordered vertices take the geometric correspondence path and need the
    # same multiset proof as the direct source-index fast path.
    part["source_vertices"] = [3, 2, 1, 0]
    assert (zzz_native.match_component(part, mesh) is not None) == accepted


def test_zzz_scalar_sampling_budget_rejects_before_native_read_or_allocation(tmp_path):
    _root, _asset, _record, avatar, _mesh, rows, client = fixture(tmp_path)
    metadata = client.read(rows[-1])
    (client.work / metadata["file"]).write_bytes(b"")
    metadata.update(stop=999, acl_count=0, times=[], constant=[1] * 65536, bindings=[])
    rig = dict(bones=[dict(translation=[0, 0, 0], rotation=[0, 0, 0, 1], scale=[1, 1, 1])] * 3)
    with patch.object(zzz_animation.np, "empty") as allocate, patch.object(zzz_animation.np, "fromfile") as read:
        with pytest.raises(ValueError, match="sampling memory budget"):
            zzz_animation.convert(metadata, client.work, rig, avatar)
    allocate.assert_not_called()
    read.assert_not_called()


@pytest.mark.parametrize("policy", [True, False, None, 1])
def test_zzz_facial_weights_follow_verified_player_policy(tmp_path, policy):
    _root, _asset, _record, avatar, _mesh, rows, client = facial_fixture(tmp_path)
    metadata = client.read(rows[2])
    metadata["blend_shape_clamp"] = policy
    file = client.work / metadata["file"]
    values = np.frombuffer(file.read_bytes(), dtype="<f4").reshape(2, 12).copy()
    values[:, -1] = [-25, 150]
    file.write_bytes(values.tobytes())
    rig = dict(bones=[dict(translation=[0, 0, 0], rotation=[0, 0, 0, 1], scale=[1, 1, 1])] * 3)
    if type(policy) is not bool:
        with pytest.raises(ValueError, match="weight policy is unavailable"):
            zzz_animation.convert(metadata, client.work, rig, avatar, {(0, 123): ("Shape01", 100)})
        return
    raw, frames, _duration, _omitted, morph, keyed, _motion = zzz_animation.convert(
        metadata, client.work, rig, avatar, {(0, 123): ("Shape01", 100)})
    assert keyed == [2]
    weights = np.frombuffer(raw, dtype="<f4")[-frames:]
    assert weights[[0, -1]] == pytest.approx([0, 1] if policy else [-.25, 1.5])
    assert weights[30] == pytest.approx(.625)
    assert np.array_equal(weights, morph["weights"][:, 0]) and morph["clamp"] is policy


def test_zzz_facial_glb_extraction_publication_reimport_and_mod_mapping_lifecycle(tmp_path, monkeypatch):
    root, asset, record, _avatar, _mesh, rows, client = facial_fixture(tmp_path)
    packed, strings = zzz_index._pack_rows(rows)
    restored = zzz_index._unpack_rows(json.loads(json.dumps(dict(rows=packed, bone_paths=strings))))
    assert restored == rows
    rows = restored
    output = tmp_path / "output"
    report, _ = zzz_pipeline.build(client, root, record, output, "Character01", lambda *_: None, threading.Event(), rows)
    rig = validate_rig(json.loads((output / "Rig.json").read_text()))
    entry = json.loads((output / "Animation/manifest.json").read_text())["clips"][0]
    metadata, data = read_clip(str(output / "Animation"), entry, rig)
    morph = metadata["morphs"]
    assert morph["format"] == "glb" and morph["file"].endswith(".glb")
    assert report["animations"][0]["facial_glb"] == morph["file"]
    assert metadata["omitted_non_transform_channels"] == 1
    assert metadata["blend_shape_clamp"] is True
    assert np.frombuffer(data, dtype="<f4")[-61:][30] == pytest.approx(.5)
    targets = read_morphs(str(output / "Animation"), morph)
    assert list(struct.iter_unpack("<I6f", targets)) == [(0, 0, 0, 2, 0, 1, 0)]
    glb = (output / "Animation" / morph["file"]).read_bytes()
    assert struct.unpack_from("<3I", glb) == (0x46546C67, 2, len(glb))
    document = json.loads(glb[20:20 + struct.unpack_from("<I", glb, 12)[0]])
    assert len(document["animations"][0]["channels"]) == len(document["meshes"]) == 1
    assert document["meshes"][0]["extras"]["targetNames"] == morph["names"]
    shutil.copytree(output / "Animation", asset / "Animation")
    shutil.copyfile(output / "Rig.json", asset / "Rig.json")
    published = []
    monkeypatch.setattr("app.assets.animations.server.publish_geometry", lambda data, **_: published.append(data) or f"/geometry/{len(published)}")
    monkeypatch.setattr("app.assets.animations.server.release_geometry", lambda _: None)
    sessions = AnimationSessions()
    catalog = sessions.catalog(root, record["path"], rig, lambda: True)
    response = sessions.load(catalog["animation_session"], entry["id"])
    assert response["status"] == "ok" and published == [data, targets]
    part = load_hash_asset("ZZMI", root, record).parts[0]
    source, decoded = decode_captured_skinning(root, part.skinning)
    binding = AssetComponentBinding(status="exact", range_status="exact", asset_type="ZZMI", root=root,
        asset=record["path"], metadata=part.metadata_path, geometry_hash=part.geometry_hash,
        component_ordinal=part.component_ordinal, first_index=part.first_index, index_count=part.index_count)
    resolved = resolve_game_rig(binding, source, decoded.bone_ids, {os.path.normcase(root)}, {},
        blend_sha256=rig["skin_bindings"][0]["sha256"], positions=part.positions,
        morph_topology=(part.indices, part.uvs))
    assert resolved["morph_binding"]["vertices"] == [0, 1, 2]
    second = tmp_path / "second"
    zzz_pipeline.build(client, root, record, second, "Character01", lambda *_: None, threading.Event(), rows)
    assert json.loads((second / "Animation/manifest.json").read_text())["clips"] == [entry]
    assert (second / "Animation" / morph["file"]).read_bytes() == glb
    (asset / "Animation" / morph["file"]).write_bytes(b"changed")
    assert sessions.load(catalog["animation_session"], entry["id"])["status"] == "error"
    assert len(published) == 2
    sessions.clear()


@pytest.mark.parametrize("route", ["direct", "streamed", "empty_defaults", "equivalent_avatar", "copy_noise",
                                   "wrong_bones", "wrong_count", "wrong_avatar", "ambiguous_cab", "copy_differs"])
def test_zzz_facial_renderer_routes_require_native_pointer_or_unique_palette_proof(tmp_path, route):
    root, _asset, record, _avatar, _mesh, rows, client = facial_fixture(tmp_path)
    renderer = rows[-1]["renderer"]
    if route in ("streamed", "empty_defaults", "wrong_bones", "wrong_count", "copy_noise", "copy_differs"):
        renderer["mesh"]["path_id"] = 0
        renderer.update(bone_paths=["", "Root/Joint01"], blend_shape_count=1)
        if route == "wrong_bones":
            renderer["bone_paths"].reverse()
        elif route == "wrong_count":
            renderer["blend_shape_count"] = 2
        elif route == "empty_defaults":
            renderer["blend_shape_count"] = 0
        elif route in ("copy_noise", "copy_differs"):
            # A second proven export of the streamed mesh shares its palette, lists
            # its two shapes in another order and has float export noise or a
            # genuinely different facial target.
            rows.append({**rows[1], "name": "Character01_Body", "path_id": 50})
            renderer["blend_shape_count"] = 2
        # The synthetic Avatar hashes must match its actual path CRCs.
        import zlib
        original = client.read
        def read(row):
            data = original(row)
            if row["type"] == "Avatar":
                data["bones"][1]["hash"] = zlib.crc32(b"Root/Joint01")
            if row["type"] == "Mesh":
                data["bone_hashes"][1] = zlib.crc32(b"Root/Joint01")
                shapes = data["blend_shapes"]
                if route.startswith("copy_"):
                    shapes["channels"].append(dict(name="Shape02", hash=124, first=1, count=1))
                    shapes["weights"].append(100)
                    shapes["frames"].append(dict(first=1, count=1, normals=True))
                    shapes["vertices"].append(dict(index=1, position=[0, 1, 0], normal=[0, 0, 0]))
                if row["path_id"] == 50:
                    data["name"] = row["name"]
                    data["bind_poses"][0][3] = 1e-9
                    shapes["channels"].reverse()
                    shapes["vertices"][0]["position"][2] += 3e-9 if route == "copy_noise" else .01
            return data
        client.read = read
    elif route == "wrong_avatar":
        renderer["avatar"]["path_id"] = 1000
    elif route == "equivalent_avatar":
        renderer["avatar"] = {key: renderer["avatar"][key] for key in ("cab", "path_id")}
        rows.append({**rows[0], "block": "ZenlessZoneZero_Data/StreamingAssets/Blocks/2.blk"})
    elif route == "ambiguous_cab":
        renderer["mesh"] = {key: renderer["mesh"][key] for key in ("cab", "path_id")}
        rows.append({**rows[1], "name": "UnrelatedMesh01", "block": "ZenlessZoneZero_Data/StreamingAssets/Blocks/2.blk"})
    captured, _ = zzz_native.capture(root, record)
    chosen = zzz_pipeline.verify_candidates(client, captured, rows, "Character01", threading.Event())
    if route.startswith("copy_"):
        assert len({zzz_index.identity(item["row"]) for items in chosen["native"].values() for item in items}) == 2
    prepared = zzz_morphs.prepare(chosen, captured, rows)
    assert bool(prepared["bindings"]) == bool(prepared["components"]) == (
        route in ("direct", "streamed", "empty_defaults", "equivalent_avatar", "copy_noise"))
    if prepared["components"]:
        target = next(value for name, value in prepared["components"][0]["targets"].items() if name.endswith(":Shape01"))
        assert target.keys() == {0} and target[0] == pytest.approx([0, 0, 2, 0, 1, 0], abs=1e-6)


@pytest.mark.parametrize("change", ["header", "range", "descriptor", "external"])
def test_zzz_facial_glb_rejects_invalid_embedded_runtime_data(tmp_path, change):
    root, _asset, record, _avatar, _mesh, rows, client = facial_fixture(tmp_path)
    output = tmp_path / "output"
    zzz_pipeline.build(client, root, record, output, "Character01", lambda *_: None, threading.Event(), rows)
    metadata = json.loads(next(file for file in (output / "Animation").glob("*.json") if file.name != "manifest.json").read_text())
    morph = metadata["morphs"]
    file = output / "Animation" / morph["file"]
    data = file.read_bytes()
    if change == "header":
        data = b"BAD!" + data[4:]
    else:
        size = struct.unpack_from("<I", data, 12)[0]
        document = json.loads(data[20:20 + size])
        runtime = document["extras"]["mod_viewer_morphs"]
        if change == "range":
            document["bufferViews"][runtime["buffer_view"]]["byteLength"] += 28
        elif change == "descriptor":
            runtime["names"] = ["OtherShape01"]
        else:
            document["buffers"][0]["uri"] = "../outside.bin"
        raw = json.dumps(document, separators=(",", ":")).encode()
        raw += b" " * (-len(raw) % 4)
        data = struct.pack("<5I", 0x46546C67, 2, len(data) - size + len(raw), len(raw), 0x4E4F534A) + raw + data[20 + size:]
    file.write_bytes(data)
    morph["sha256"] = hashlib.sha256(data).hexdigest()
    with pytest.raises(ValueError, match="facial GLB|Facial GLB"):
        read_morphs(str(file.parent), morph)


def test_zzz_runtime_manifest_integrity_containment_and_cancellation(tmp_path):
    bundle = tmp_path / "runtime"
    bundle.mkdir()
    names = ("ModViewer.ZzzExtract.exe", "UnityReader.dll", "AnimeStudio.ACL.ZZZV2.dll", "AnimeStudio.ACL.DB.dll",
             "oodle-data-shared.dll", "ModViewer.ZzzExtract.deps.json", "ModViewer.ZzzExtract.runtimeconfig.json")
    files = {}
    for name in names:
        (bundle / name).write_bytes(b"synthetic runtime")
        files[name] = hashlib.sha256(b"synthetic runtime").hexdigest()
    record = dict(version=zzz_runtime.VERSION, parser=zzz_runtime.PARSER_COMMIT, files=files)
    manifest = bundle / "runtime.json"
    manifest.write_text(json.dumps(record))
    cancel = threading.Event()
    assert zzz_runtime.ensure_runtime(cancel, bundle) == bundle / names[0]
    (bundle / names[1]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        zzz_runtime.ensure_runtime(cancel, bundle)
    record["files"]["../outside.dll"] = "0" * 64
    manifest.write_text(json.dumps(record))
    (bundle / names[1]).write_bytes(b"synthetic runtime")
    with pytest.raises(ValueError, match="Unsafe"):
        zzz_runtime.ensure_runtime(cancel, bundle)
    cancel.set()
    with pytest.raises(ExtractionCancelled):
        zzz_runtime.ensure_runtime(cancel, bundle)

"""Synthetic Genshin Impact archive, metadata index and pointer contracts."""

from collections import Counter
from copy import deepcopy
import json
import os
import shutil
import threading
from unittest.mock import patch

import numpy as np
import pytest

from app.assets import index
from app.assets.game_extract import gi_index, gi_pipeline, gi_props, gi_runtime, zzz_index, zzz_native, zzz_prop_textures
from app.settings import paths


def write_block(folder, relative, data=b"block"):
    path = folder / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def rows_for(block):
    number = int(block.rsplit("/", 1)[-1].split(".", 1)[0])

    def row(name, kind, cab, offset, path_id, **extra):
        return dict(name=name, type=kind, block=block, cab=cab, offset=offset, file_ordinal=0, path_id=path_id, **extra)

    return [
        row("Avatar_Type01_Name01", "AnimatorController", "CAB-04", 0, number * 10 + 1),
        row("Clip01", "AnimationClip", "CAB-02", 8, number * 10 + 2),
        row("Mesh01", "Mesh", "CAB-01", 16, number * 10 + 3, shape=[[3, 3]]),
        row("Avatar_Type01_Name01_ModelAvatar", "Avatar", "CAB-01", 16, number * 10 + 4),
        # Facial routes keep only path and pointers; null-mesh renderers are not indexed.
        row("", "SkinnedMeshRenderer", "CAB-01", 16, number * 10 + 5, materials=["CAB-05"],
            renderer=dict(path="Part01", mesh=dict(cab="cab-01", path_id=number * 10 + 3),
                          avatar=dict(cab="cab-01", path_id=number * 10 + 4), bone_paths=["Root"], blend_shape_count=2)),
        row("", "SkinnedMeshRenderer", "CAB-01", 16, number * 10 + 6, materials=["CAB-08"],
            renderer=dict(path="Part02", mesh=dict(cab="cab-01", path_id=0),
                          avatar=dict(cab="cab-01", path_id=number * 10 + 4), bone_paths=["Root"], blend_shape_count=0)),
        # Material and texture files are kept only where character renderers lead.
        row("", "File", "CAB-05", 40, 0), row("", "File", "CAB-06", 48, 0), row("", "File", "CAB-07", 56, 0),
        row("", "File", "CAB-08", 64, 0),
        row("", "Material", "CAB-05", 40, number * 10 + 7, textures=["CAB-06"]),
        row("", "Material", "CAB-05", 40, number * 10 + 8, textures=None),
        row("", "Material", "CAB-07", 56, number * 10 + 9, textures=["CAB-07"]),
        # World meshes, their renderers, other controllers and unreferenced clips are dropped.
        row("Rock01", "Mesh", "CAB-09", 72, number * 10 + 10, shape=[[3, 3]]),
        row("", "SkinnedMeshRenderer", "CAB-09", 72, number * 10 + 11, materials=["CAB-07"],
            renderer=dict(path="Rock", mesh=dict(cab="cab-09", path_id=number * 10 + 10),
                          avatar=dict(cab="cab-09", path_id=number * 10 + 12), bone_paths=["Root"], blend_shape_count=0)),
        row("Monster01_ModelAvatar", "Avatar", "CAB-09", 72, number * 10 + 12),
        row("Monster01", "AnimatorController", "CAB-09", 72, number * 10 + 13),
        row("Clip02", "AnimationClip", "CAB-03", 80, number * 10 + 14),
        # Skill objects named for the family token stay for the name-paired preview; others do not.
        row("SkillObj_Name01_Chair01_ModelAvatar", "Avatar", "CAB-10", 88, number * 10 + 15),
        row("Chair01_Model", "Mesh", "CAB-10", 88, number * 10 + 16, shape=[[3, 3]]),
        row("Ani_SkillObj_Name01_Chair01_Idle01", "AnimationClip", "CAB-11", 96, number * 10 + 17),
        row("SkillObj_Other01_Cup01_ModelAvatar", "Avatar", "CAB-12", 104, number * 10 + 18),
        row("Cup01_Model", "Mesh", "CAB-12", 104, number * 10 + 19, shape=[[3, 3]]),
        row("Ani_SkillObj_Other01_Cup01_Idle01", "AnimationClip", "CAB-13", 112, number * 10 + 20),
    ]


class Client:
    def __init__(self, folder, mutate=lambda row: row):
        self.root, self.mutate = folder.resolve(), mutate

    def scan_many(self, files):
        return [self.mutate(row) for file in files for row in rows_for(file.relative_to(self.root).as_posix())]

    def read(self, row):
        assert row["type"] == "AnimatorController" and row["name"] == "Avatar_Type01_Name01"
        leaf = dict(cab="cab-02", path_id=12, node_index=0, threshold=None)
        return dict(states=[dict(name="Idle", motions=[dict(blend_type=0, leaves=[leaf]), None])])


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
    assert count == 2 and gi_index.status(folder)["state"] == "ready"
    # Only character data stays: every Avatar, the character bundle's mesh, its renderer,
    # the family controller and the clip it points to.
    assert sorted((row["type"], row["name"]) for row in rows) == [
        ("AnimationClip", "Ani_SkillObj_Name01_Chair01_Idle01"), ("AnimationClip", "Clip01"),
        ("AnimatorController", "Avatar_Type01_Name01"),
        ("Avatar", "Avatar_Type01_Name01_ModelAvatar"), ("Avatar", "Monster01_ModelAvatar"),
        ("Avatar", "SkillObj_Name01_Chair01_ModelAvatar"), ("Avatar", "SkillObj_Other01_Cup01_ModelAvatar"),
        ("Mesh", "Chair01_Model"), ("Mesh", "Mesh01"), ("SkinnedMeshRenderer", "")]
    (renderer,) = [row for row in rows if row["type"] == "SkinnedMeshRenderer"]
    assert "materials" not in renderer
    assert renderer["renderer"] == dict(path="Part01", mesh=dict(cab="cab-01", path_id=13),
                                        avatar=dict(cab="cab-01", path_id=14), blend_shape_count=2)
    # The renderer's material file and that file's texture files; unreadable materials add nothing.
    assert {cab: [copy[1:] for copy in copies] for cab, copies in rows.files.items()} == {
        "cab-05": [(40, 0, "CAB-05")], "cab-06": [(48, 0, "CAB-06")]}
    assert gi_index.read(folder)[1].files == rows.files
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
        gi_index.build(Client(folder, lambda row: {**row, "offset": -1} if row["type"] == "Avatar" else row), folder, lambda *_: None, cancel)
    assert file.read_bytes() == before
    with pytest.raises(ValueError, match="incomplete or ambiguous"):
        gi_index.build(Client(folder, lambda row: {**row, "type": "Light"} if row["type"] == "Mesh" else row), folder, lambda *_: None, cancel)
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


GI_BLOCK = "GenshinImpact_Data/StreamingAssets/AssetBundles/blocks/00/1.blk"
GI_OTHER_BLOCK = "GenshinImpact_Data/StreamingAssets/AssetBundles/blocks/00/2.blk"


def gi_row(name, kind, path_id, block=GI_BLOCK, offset=0, shape=None):
    row = dict(name=name, type=kind, block=block, cab="CAB-01", offset=offset, file_ordinal=0, path_id=path_id)
    if shape is not None:
        row["shape"] = shape
    return row


def gimi_fixture(tmp_path):
    root = tmp_path / "assets"
    asset = root / "Character01"
    asset.mkdir(parents=True)
    vertices = [[.2, .3, .4], [1.2, .4, .8], [.4, 1.2, .7]]
    uv = [[.1, .2], [.3, .4], [.5, .6]]
    vb = asset / "mesh-vb0=11111111.txt"
    # Real GIMI dumps declare signed integer BLENDINDICES; the parser reads them as plain values.
    lines = ["stride: 52", "vertex count: 3",
             "element[0]: POSITION0, format: R32G32B32_FLOAT",
             "element[1]: TEXCOORD0, format: R32G32_FLOAT",
             "element[2]: BLENDWEIGHTS0, format: R32G32B32A32_FLOAT",
             "element[3]: BLENDINDICES0, format: R32G32B32A32_SINT"]
    for number, (position, texcoord) in enumerate(zip(vertices, uv)):
        for name, values in (("POSITION", position), ("TEXCOORD", texcoord),
                             ("BLENDWEIGHTS", [.75, .25, 0, 0]), ("BLENDINDICES", [8, 12, 0, 0])):
            lines.append(f"vb0[{number}]+000 {name}: " + ", ".join(map(str, values)))
    vb.write_text("\n".join(lines) + "\n")
    ib = asset / "mesh-ib=22222222.txt"
    ib.write_text("first index: 0\nindex count: 3\ntopology: trianglelist\n0 1 2\n")
    (asset / "hash.json").write_text(json.dumps([dict(component_name="Body", position_vb="11111111",
        ib="22222222", object_indexes=[0], object_indexes_count=[3], object_classifications=["A"],
        vb_file=vb.name, ib_files=[ib.name])]))
    record = index.build_index("GIMI", str(root))["assets"][0]
    avatar = dict(name="Avatar_Type01_Name01_ModelAvatar", human=dict(synthetic=True), bones=[
        dict(path="", hash=0, parent=-1, translation=[0, 0, 0], rotation=[0, 0, 0, 1], scale=[1, 1, 1]),
        dict(path="Root/Joint01", hash=100, parent=0, translation=[0, 0, 0], rotation=[0, 0, 0, 1], scale=[1, 2, 1])])
    mesh = dict(name="Body", vertices=np.array(vertices).ravel().tolist(),
        uv=np.array(uv).ravel().tolist(), indices=[0, 1, 2], weights=[[.75, .25, 0, 0]] * 3,
        bone_indices=[[0, 1, 0, 0]] * 3, bone_hashes=[0, 100],
        bind_poses=[matrix.ravel(order="C").tolist() for matrix in (np.eye(4), np.diag([1, .5, 1, 1]))],
        sections=[dict(first=0, count=3, vertex_first=0, vertex_count=3)])
    shape = [[3, 3]]
    rows = [gi_row(avatar["name"], "Avatar", 1),
            gi_row("Body", "Mesh", 2, shape=shape),
            # Same name and shape in another bundle must never be read for this Avatar.
            gi_row("Body", "Mesh", 3, offset=64, shape=shape),
            gi_row("Body_LOD1", "Mesh", 4, shape=shape)]
    documents = {1: avatar, 2: mesh, 3: deepcopy(mesh), 4: deepcopy(mesh)}

    class Client:
        def __init__(self):
            self.documents, self.reads = documents, Counter()
        def read(self, row):
            self.reads[(row["type"], row["path_id"])] += 1
            return deepcopy(self.documents[row["path_id"]])
    return str(root), asset, record, rows, Client()


def test_gi_rig_proof_uses_colocated_meshes_and_skips_other_bundles(tmp_path):
    root, asset, record, rows, client = gimi_fixture(tmp_path)
    chosen, rig, previous, captured, _fingerprints = gi_pipeline.prove_rig(
        client, root, record, None, lambda *_: None, threading.Event(), rows)
    assert client.reads == Counter({("Avatar", 1): 1, ("Mesh", 2): 1})
    assert chosen["source"] == zzz_index.identity(rows[0])
    assert chosen["meshes"] == [zzz_index.identity(rows[1])]
    assert list(chosen["matched"]) == [0]
    assert len(captured[0]["blends"]) == 3
    assert previous is None
    assert rig["bones"][0]["name"] == "mesh-space"
    assert len(rig["bones"]) == len(client.documents[1]["bones"]) + 1
    assert rig["skin_bindings"][0]["encoding"] == "gimi_f32_u32_4"


def test_gi_rig_proof_rejects_wrong_weights_and_reports_label(tmp_path):
    root, asset, record, rows, client = gimi_fixture(tmp_path)
    client.documents[2]["weights"] = [[.5, .5, 0, 0]] * 3
    with pytest.raises(ValueError, match="No Genshin Impact mesh could be verified"):
        gi_pipeline.prove_rig(client, root, record, None, lambda *_: None, threading.Event(), rows)


def test_gi_equivalent_avatar_copies_ignore_humanoid_data(tmp_path):
    root, asset, record, rows, client = gimi_fixture(tmp_path)
    copy = gi_row("Avatar_Type01_Name01_ModelAvatar", "Avatar", 5, block=GI_OTHER_BLOCK)
    copy_mesh = gi_row("Body", "Mesh", 6, block=GI_OTHER_BLOCK, shape=[[3, 3]])
    rows += [copy, copy_mesh]
    # Only the Humanoid payload differs; skeleton and palette identity are equal.
    client.documents[5] = {**deepcopy(client.documents[1]), "human": {"synthetic": False}}
    client.documents[6] = deepcopy(client.documents[2])
    chosen, *_ = gi_pipeline.prove_rig(client, root, record, None, lambda *_: None, threading.Event(), rows)
    assert chosen["avatar_sources"] == {zzz_index.identity(rows[0]), zzz_index.identity(copy)}
    # Equivalent copies are proven once; the other copy's native mesh is not read.
    assert client.reads[("Mesh", 2)] + client.reads[("Mesh", 6)] == 1


def test_gi_placement_tolerates_bind_noise_that_zzz_rejects(tmp_path):
    root, asset, record, rows, client = gimi_fixture(tmp_path)
    noisy = np.asarray(client.documents[2]["bind_poses"][1]).reshape(4, 4)
    noisy[0, 3] += 5e-4  # Sub-millimetre inverse-bind noise on one joint.
    client.documents[2]["bind_poses"][1] = noisy.ravel().tolist()
    avatar = client.documents[1]
    assert zzz_native.mesh_placement(client.documents[2], avatar) is None
    assert zzz_native.mesh_placement(client.documents[2], avatar, gi_pipeline.PLACEMENT_TOLERANCE) is not None
    chosen, *_ = gi_pipeline.prove_rig(client, root, record, None, lambda *_: None, threading.Event(), rows)
    assert list(chosen["matched"]) == [0]
    noisy[0, 3] += 5e-3  # A real placement disagreement still fails closed.
    client.documents[2]["bind_poses"][1] = noisy.ravel().tolist()
    with pytest.raises(ValueError, match="No Genshin Impact mesh could be verified"):
        gi_pipeline.prove_rig(client, root, record, None, lambda *_: None, threading.Event(), rows)


def test_gi_unproven_name_match_falls_back_to_geometry_but_not_on_cancel(tmp_path):
    from app.assets.game_extract.runtime import ExtractionCancelled

    root, asset, record, rows, client = gimi_fixture(tmp_path)
    # A different character whose name contains the query, with its own unrelated mesh.
    rows += [gi_row("Avatar_Type01_Name01Shadow_ModelAvatar", "Avatar", 7, block=GI_OTHER_BLOCK),
             gi_row("Body", "Mesh", 8, block=GI_OTHER_BLOCK, shape=[[3, 3]])]
    client.documents[7] = deepcopy(client.documents[1])
    client.documents[8] = {**deepcopy(client.documents[2]), "weights": [[.5, .5, 0, 0]] * 3}
    chosen, *_ = gi_pipeline.prove_rig(client, root, record, "Shadow", lambda *_: None, threading.Event(), rows)
    assert chosen["source"] == zzz_index.identity(rows[0])
    assert client.reads[("Avatar", 7)] >= 1  # The name attempt read the wrong character first.
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ExtractionCancelled):
        gi_pipeline.prove_rig(client, root, record, "Shadow", lambda *_: None, cancel, rows)


def test_gi_shape_prefilter_compares_index_counts_of_overlapping_sections(tmp_path):
    from app.assets.game_extract import zzz_pipeline

    root, asset, record, rows, client = gimi_fixture(tmp_path)
    # Overlapping submesh vertex ranges report more vertices than the component references.
    for row in rows:
        if row["type"] == "Mesh":
            row["shape"] = [[9, 3]]
    chosen, *_ = gi_pipeline.prove_rig(client, root, record, None, lambda *_: None, threading.Event(), rows)
    assert list(chosen["matched"]) == [0]
    captured, _files = zzz_native.capture(root, record, asset_type="GIMI")
    with pytest.raises(ValueError, match="Enter the character's game name"):
        zzz_pipeline.verify_candidates(client, captured, rows, None, threading.Event(),
                                       mesh_hint=gi_pipeline.colocated, shape_key=zzz_pipeline.exact_shape)


SHAPE_HASH = 7


def add_attachment_component(root, asset, rows, client):
    """A weightless capture of one of a native two-bone mesh's triangles, shown with a blend shape at full weight."""
    vertices = np.array([[2.1, .3, .5], [2.6, .4, .9], [2.3, 1.1, .6], [2.8, 1.3, .2]])
    shape = {1: [0, .01, 0], 3: [0, .02, .01]}
    captured = vertices.copy()
    for index_, delta in shape.items():
        captured[index_] += delta
    uv = [[.6, .1], [.7, .2], [.8, .3], [.9, .4]]
    vb = asset / "part-vb0=33333333.txt"
    lines = ["stride: 20", "vertex count: 4",
             "element[0]: POSITION0, format: R32G32B32_FLOAT",
             "element[1]: TEXCOORD0, format: R32G32_FLOAT"]
    for number, (position, texcoord) in enumerate(zip(captured.tolist(), uv)):
        for name, values in (("POSITION", position), ("TEXCOORD", texcoord)):
            lines.append(f"vb0[{number}]+000 {name}: " + ", ".join(map(str, values)))
    vb.write_text("\n".join(lines) + "\n")
    ib = asset / "part-ib=44444444.txt"
    ib.write_text("first index: 0\nindex count: 3\ntopology: trianglelist\n1 3 2\n")
    entries = json.loads((asset / "hash.json").read_text())
    entries.append(dict(component_name="Part02", position_vb="33333333", ib="44444444", object_indexes=[0],
                        object_indexes_count=[3], object_classifications=["A"], vb_file=vb.name, ib_files=[ib.name]))
    (asset / "hash.json").write_text(json.dumps(entries))
    shapes = dict(channels=[dict(name="Shape01", hash=SHAPE_HASH, first=0, count=1)], weights=[100.0],
                  frames=[dict(first=0, count=2, normals=False)],
                  vertices=[dict(index=index_, position=delta, normal=[0, 0, 0]) for index_, delta in shape.items()])
    mesh = dict(name="Part02", vertices=vertices.ravel().tolist(), uv=np.array(uv).ravel().tolist(),
                indices=[0, 1, 2, 1, 2, 3], weights=[[.75, .25, 0, 0]] * 4, bone_indices=[[1, 0, 0, 0]] * 4,
                bone_hashes=[0, 100], bind_poses=deepcopy(client.documents[2]["bind_poses"]), blend_shapes=shapes,
                sections=[dict(first=0, count=6, vertex_first=0, vertex_count=4)])
    rows.append(gi_row("Part02", "Mesh", 9, shape=[[4, 6]]))
    client.documents[9] = mesh
    return index.build_index("GIMI", str(root))["assets"][0]


def test_gi_weightless_part_adopts_native_weights_and_its_captured_expression(tmp_path):
    root, asset, record, rows, client = gimi_fixture(tmp_path)
    record = add_attachment_component(root, asset, rows, client)
    chosen, rig, _previous, captured, _fingerprints = gi_pipeline.prove_rig(
        client, root, record, None, lambda *_: None, threading.Event(), rows)
    assert [part.get("weightless", False) for part in captured] == [False, True]
    assert sorted(chosen["matched"]) == [0, 1]
    attached = next(component for component in rig["components"] if "attachment" in component)
    assert attached["geometry_hash"] == "44444444"
    # Native slots map to the root and Joint01 after the mesh-space root.
    assert attached["bone_map"] == {"0": 1, "1": 2}
    attachment = attached["attachment"]
    assert attachment["weights"] == pytest.approx([.75, .25, 0, 0] * 3)
    assert attachment["slots"] == [1, 0, 0, 0] * 3
    # The capture shows the native shape at full weight.
    assert attachment["expression"] == {str(SHAPE_HASH): pytest.approx(1, abs=1e-4)}
    # Only the skinned part's captured Blend stream is reusable by mods.
    assert len(rig["skin_bindings"]) == 1


def test_gi_attachment_joins_asset_weights_only_with_its_proven_rig(tmp_path, monkeypatch):
    from app.assets.game_extract.pipeline import write_document
    from app.bridge.asset_preview import AssetPreview

    root, asset, record, rows, client = gimi_fixture(tmp_path)
    record = add_attachment_component(root, asset, rows, client)
    _chosen, rig, *_ = gi_pipeline.prove_rig(client, root, record, None, lambda *_: None, threading.Event(), rows)
    preview = AssetPreview(object())
    monkeypatch.setattr(preview, "_asset_selection", lambda _: (str(asset), dict(type="GIMI", path=root), {}, record))

    def weights():
        payload = preview.load_asset(str(asset))
        flags = {mesh["component"]: (mesh["skinning_available"], mesh["rig_attachment_available"])
                 for mesh in payload["meshes"].values()}
        assert flags == {"Body": (True, False), "Part02": (False, True)}
        result = preview.get_skinning_preview(payload["metadata"]["asset"]["rig_session"])
        assert result["status"] == "ok"
        return {payload["meshes"][key]["component"]: entry for key, entry in result["meshes"].items()}, result

    try:
        # Without a proven rig the weightless part publishes no weights.
        unproven, _result = weights()
        assert set(unproven) == {"Body"}
        write_document(asset / "Rig.json", {key: value for key, value in rig.items() if key != "id"}, 2 * 1024 * 1024)
        proven, result = weights()
        assert set(proven) == {"Body", "Part02"}
        part = proven["Part02"]
        assert part["encoding"] == "gimi_f32_u32_4" and part["bone_ids"] == [0, 1] and part["vertex_count"] == 3
        assert part["game_bone_map"] == {"0": 1, "1": 2}
        assert part["game_morph_binding"]["vertices"] == [1, 3, 2]
        assert part["game_rig_id"] == proven["Body"]["game_rig_id"] in result["game_rigs"]
    finally:
        preview.clear_preview()


@pytest.mark.parametrize("change", ["unexplained", "excess_expression", "partial_weight", "foreign_triangle"])
def test_gi_attachment_proof_fails_closed(tmp_path, change):
    root, asset, record, rows, client = gimi_fixture(tmp_path)
    record = add_attachment_component(root, asset, rows, client)
    mesh = client.documents[9]
    if change == "unexplained":
        mesh["vertices"][7] += .01  # Vertex 2 moves in no native shape.
    elif change == "excess_expression":
        for row in mesh["blend_shapes"]["vertices"]:
            row["position"] = [value * .4 for value in row["position"]]  # Would need weight 2.5.
    elif change == "partial_weight":
        mesh["weights"] = [[.6, .3, 0, 0]] * 4
    else:
        mesh["indices"] = [0, 1, 2, 0, 2, 3]
    chosen, rig, *_ = gi_pipeline.prove_rig(client, root, record, None, lambda *_: None, threading.Event(), rows)
    assert list(chosen["matched"]) == [0]
    assert not any("attachment" in component for component in rig["components"])


FAMILY = "Avatar_Type01_Name01"
HAIR, ACCESSORY = 100, 101


def clip(name, start, stop, bindings, values):
    """Helper-shaped clip metadata; ``values`` are ACL frames in binding order."""
    rows = [dict(path=path, attribute=attribute, type=kind, custom=custom, pointer=0) for kind, custom, path, attribute in bindings]
    return dict(name=name, start=start, stop=stop, times=[start, stop], acl_count=len(values[0]), streamed_count=0, streamed=[],
                dense=dict(count=0), constant=[], mirror=False, bindings=rows, values=values)


def animated_fixture(tmp_path):
    from tests.support.humanoid_data import humanoid

    root, asset, record, rows, client = gimi_fixture(tmp_path)
    human = humanoid()
    count = len(human["pose"])
    human["skeleton_index"] = list(range(count))
    bones = [dict(path="" if node == 0 else f"Root/Node{node:02d}", hash=0 if node == 0 else 1000 + node, parent=parent,
                  translation=pose["translation"], rotation=pose["rotation"], scale=[1, 1, 1])
             for node, (parent, pose) in enumerate(zip(human["skeleton"]["parent"], human["pose"]))]
    # The mesh's second palette joint, animated only by the streamer layer.
    bones.append(dict(path="Root/Joint01", hash=HAIR, parent=0, translation=[0, 0, 0], rotation=[0, 0, 0, 1], scale=[1, 2, 1]))
    bones.append(dict(path="Root/Joint02", hash=ACCESSORY, parent=0, translation=[0, 0, 0], rotation=[0, 0, 0, 1], scale=[1, 1, 1]))
    client.documents[1] = dict(name=FAMILY + "_ModelAvatar", human=human, bones=bones)
    hips = 1000 + human["bone_index"][0]
    root_curves = [("Animator", 8, 0, attribute) for attribute in range(14)]
    identity_root = [0, 0, 0, 0, 0, 0, 1, 0, .9, 0, 0, 0, 0, 1]
    body_bindings = root_curves + [("Transform", 0, hips, 2), ("Transform", 0, 999, 1), ("Animator", 8, 0, 150), ("Animator", 5, 0, 0)]
    body_frame = identity_root + [0, 0, 0, 1, 0, 0, 0, 0, 0]
    client.documents[20] = clip("Body01", 0.0, 1.0, body_bindings, [body_frame, body_frame])
    # The same body with a hand IK goal ten metres away cannot be a correct Humanoid conversion.
    goal_bindings = body_bindings + [("Animator", 8, 0, 28 + axis) for axis in range(3)]
    client.documents[23] = clip("Body02", 0.0, 1.0, goal_bindings, [body_frame + [10, 0, 0], body_frame + [10, 0, 0]])
    client.documents[24] = clip("Empty01", 0.0, 1.0, [], [[], []])
    streamer_bindings = [("Transform", 0, HAIR, 1), ("Transform", 0, hips, 1), ("Transform", 0, ACCESSORY, 1), ("Animator", 8, 0, 0)]
    client.documents[21] = clip("Layer01", 0.0, 2.0, streamer_bindings,
                                [[0, 0, 0, 0, 0, 0, 0, 0, 0, 0], [2, 0, 0, 5, 5, 5, 3, 3, 3, 0]])

    def leaf(path_id, cab="CAB-02"):
        return dict(cab=cab.lower(), path_id=path_id, node_index=0, threshold=None)

    def motion(*leaves):
        return dict(blend_type=0, leaves=list(leaves))

    def state(name, motions, mirror=False):
        return dict(name=name, speed=1.0, mirror=mirror, mirror_param=0, ik_on_feet=False, motions=motions)

    client.documents[30] = dict(name=FAMILY, layers=[
        dict(name="Base Layer", state_machine=0, motion_set=0, blend_mode=0, weight=0.0, synced_affects_timing=False, skeleton_mask=[]),
        dict(name="StreamerSyncLayer", state_machine=0, motion_set=1, blend_mode=0, weight=1.0, synced_affects_timing=False,
             skeleton_mask=[dict(path_hash=HAIR, weight=1.0), dict(path_hash=hips, weight=1.0)])],
        states=[state("Tree", [motion(leaf(20), leaf(23)), motion(leaf(21), leaf(21))]),
                state("Idle", [motion(leaf(20)), motion(leaf(21))]),
                state("Mirrored", [motion(leaf(20)), motion(leaf(21))], mirror=True),
                state("Uneven", [motion(leaf(20), leaf(23)), motion(leaf(21))]),
                state("Missing", [motion(leaf(20, "CAB-99")), motion(leaf(21))]),
                state("Empty", [None, motion(leaf(21))]),
                state("Silent", [motion(leaf(24)), motion(leaf(21))])])
    rows += [gi_row(FAMILY, "AnimatorController", 30),
             *({**gi_row(name, "AnimationClip", path_id), "cab": "CAB-02"}
               for name, path_id in (("Body01", 20), ("Layer01", 21), ("Body02", 23), ("Empty01", 24)))]
    client.work = tmp_path / "work"
    client.work.mkdir()
    read = client.read

    def read_with_samples(row):
        document = read(row)
        if row["type"] == "AnimationClip":
            values = np.asarray(document.pop("values"), dtype="<f4")
            document["file"] = "samples-%d.bin" % row["path_id"]
            (client.work / document["file"]).write_bytes(values.tobytes())
        return document
    client.read = read_with_samples
    return root, asset, record, rows, client


def validate_rig_document(file):
    from app.assets.rig import validate_rig

    return validate_rig(json.loads(file.read_text()))


def test_gi_build_layers_controller_states_and_keeps_clip_identity(tmp_path):
    from app.assets.animations import read_clip

    root, asset, record, rows, client = animated_fixture(tmp_path)
    # A body-only state chained with Idle by authored exit-time edges, entered from Any State.
    controller = client.documents[30]
    controller["states"].append(dict(controller["states"][1], name="Combo", motions=[controller["states"][1]["motions"][0]]))
    controller["states"][1]["transitions"] = [sequence_edge(7, exit_time=0.5, has_exit=True, fade=0.1)]
    controller["states"][7]["transitions"] = [sequence_edge(1, exit_time=1.0, has_exit=True, fade=0.1)]
    controller.update(any_state=[sequence_edge(1, ("Input01", 1, 0))], selectors=[])
    output = tmp_path / "output"
    report, _ = gi_pipeline.build(client, root, record, output, None, lambda *_: None, threading.Event(), rows)
    assert report["game"] == "gi" and report["imported"] == 2 and report["sequences"] == 1
    # A plain state's name wins over a blend-tree leaf that shares the same clips.
    assert [item["state"] for item in report["animations"]] == ["Idle", "Combo"]
    reasons = {item["state"]: item["reason"] for item in report["skipped_details"]}
    assert set(reasons) == {"Tree 2", "Mirrored", "Uneven", "Missing", "Empty", "Silent"}
    assert "no animation curves" in reasons["Silent"]
    assert "IK goals" in reasons["Tree 2"] and "mirrored" in reasons["Mirrored"]
    assert "differ" in reasons["Uneven"] and "pointer" in reasons["Missing"] and "no body motion" in reasons["Empty"]
    rig = validate_rig_document(output / "Rig.json")
    manifest = json.loads((output / "Animation/manifest.json").read_text())
    entry = manifest["clips"][0]
    idle, combo = (item["id"] for item in report["animations"])
    [sequence] = manifest["sequences"]
    assert (sequence["cyclic"], sequence["restart"]) == (True, 0.0)
    assert [(step["clip"], step["start"], step["leave"], step["fade"]) for step in sequence["steps"]] == [
        (idle, 0.0, 0.5, 0.1), (combo, 0.0, 1.0, 0.0)]
    metadata, data = read_clip(str(output / "Animation"), entry, rig)
    layers = metadata["layers"]
    assert layers["omitted"] == dict(non_transform=1, translation_dof=1, humanoid_owned=2, unbound=1, masked=1)
    assert layers["body_duration"] == 1.0 and layers["streamer_duration"] == 2.0
    frames = np.frombuffer(data, dtype="<f4").reshape(metadata["frame_count"], len(rig["bones"]), 10)
    names = [bone["name"] for bone in rig["bones"]]
    hair, accessory = names.index("Root/Joint01"), names.index("Root/Joint02")
    assert isinstance(metadata["keyed_bones"], list) and hair in metadata["keyed_bones"]
    assert isinstance(manifest["runtime_bones"], list)
    # The 2 s streamer follows the 1 s body's normalized time.
    middle = metadata["frame_count"] // 2
    assert np.allclose(frames[[0, middle, -1], hair, 0], [0, 1, 2], atol=1e-5)
    assert np.allclose(frames[:, accessory, :3], 0)
    # The Humanoid owns the hips: neither layer's Transform curve reaches them.
    hips = names.index("Root/Node01")
    assert not np.allclose(frames[-1, hips, :3], 5)
    assert np.allclose(np.linalg.norm(frames[:, hips, 3:7], axis=1), 1, atol=1e-5)
    # Reimporting over the installed library keeps the same clip identity.
    shutil.copytree(output, asset, dirs_exist_ok=True)
    again, _ = gi_pipeline.build(client, root, record, tmp_path / "again", None, lambda *_: None, threading.Event(), rows)
    assert [item["id"] for item in again["animations"]] == [idle, combo] and entry["id"] == idle
    assert json.loads((tmp_path / "again/Animation/manifest.json").read_text())["sequences"] == [sequence]


def sequence_edge(dest, *conditions, exit_time=1.0, has_exit=False, fade=0.0, fixed=False, offset=0.0):
    return dict(dest=dest, duration=fade, offset=offset, exit_time=exit_time, has_exit_time=has_exit,
                fixed_duration=fixed, conditions=[dict(param=param, mode=mode, threshold=value)
                                                  for param, mode, value in conditions])


def sequence_graph(transitions, any_state=(), selectors=()):
    names = ["StateA", "StateB", "StateC", "StateD"]
    return dict(states=[dict(name=names[index], path=f"Base Layer.Root.Group01.{names[index]}", transitions=edges)
                        for index, edges in enumerate(transitions)],
                any_state=list(any_state), selectors=list(selectors))


TRIGGER = (("Input01", 1, 0), ("Choice01", 6, 1))


def window(lower):
    return ("NormalizedTime", 3, lower), ("NormalizedTime", 4, 0.9)


@pytest.mark.parametrize("controller, clips, expected", [
    # A combo repeats its entry input inside each window; other inputs and NormalizedTime
    # misuse never continue it, fades convert to seconds and the closing offset restarts it.
    (sequence_graph([
        [sequence_edge(3, ("Move01", 6, 2), *window(0.05)), sequence_edge(2, ("Input01", 1, 0), ("Choice01", 6, 6), *window(0.1)),
         sequence_edge(1, *TRIGGER, *window(0.25), fade=0.1, offset=0.1)],
        [sequence_edge(2, *TRIGGER, ("NormalizedTime", 6, 0.2)), sequence_edge(2, *TRIGGER, *window(0.5), fade=0.3, fixed=True)],
        [sequence_edge(0, *TRIGGER, *window(0.75), fade=0.1, offset=0.05)],
        []], any_state=[sequence_edge(0, *TRIGGER, ("Gate01", 1, 0))]),
     {0: ("clip-a", 2.0), 1: ("clip-b", 2.0), 2: ("clip-c", 2.0), 3: ("clip-d", 1.0)},
     [("Group01: StateA – StateC", True, 0.1, [("clip-a", 0, 0.5, 0.2), ("clip-b", 0.2, 1.0, 0.3), ("clip-c", 0, 1.5, 0.2)])]),
    # An automatic chain starts where nothing leads; its fade is clamped to the remaining clip.
    (sequence_graph([[sequence_edge(1, exit_time=0.8, has_exit=True, fade=0.25, fixed=True)], []]),
     {0: ("clip-a", 1.0), 1: ("clip-b", 2.0)},
     [("Group01: StateA – StateB", False, 0, [("clip-a", 0, 0.8, 0.2), ("clip-b", 0, 2.0, 0)])]),
    # Reaching a non-head state again stops the chain; that loop is its own cycle, and
    # runs already inside another chain are not repeated.
    (sequence_graph([[sequence_edge(1, has_exit=True)], [sequence_edge(2, has_exit=True)], [sequence_edge(1, has_exit=True)]]),
     {0: ("clip-a", 1.0), 1: ("clip-b", 1.0), 2: ("clip-c", 1.0)},
     [("Group01: StateA – StateC", False, 0, [("clip-a", 0, 1.0, 0), ("clip-b", 0, 1.0, 0), ("clip-c", 0, 1.0, 0)]),
      ("Group01: StateB – StateC", True, 0, [("clip-b", 0, 1.0, 0), ("clip-c", 0, 1.0, 0)])]),
    # An unconverted destination cuts the chain.
    (sequence_graph([[sequence_edge(1, has_exit=True)], [sequence_edge(2, has_exit=True)], []]),
     {0: ("clip-a", 1.0), 1: ("clip-b", 1.0)},
     [("Group01: StateA – StateB", False, 0, [("clip-a", 0, 1.0, 0), ("clip-b", 0, 1.0, 0)])]),
    # A window that closes before the entry offset, an unnamed input and a missing graph yield nothing.
    (sequence_graph([[sequence_edge(1, *TRIGGER, *window(0.5))], []], any_state=[sequence_edge(0, *TRIGGER, offset=0.6)]),
     {0: ("clip-a", 1.0), 1: ("clip-b", 1.0)}, []),
    (sequence_graph([[sequence_edge(1, (None, 1, 0), *window(0.5))], []], any_state=[sequence_edge(0, (None, 1, 0))]),
     {0: ("clip-a", 1.0), 1: ("clip-b", 1.0)}, []),
    ({"states": [dict(name="StateA", transitions=[sequence_edge(1, has_exit=True)]), dict(name="StateB")]},
     {0: ("clip-a", 1.0), 1: ("clip-b", 1.0)}, []),
])
def test_gi_sequences_follow_authored_continuations(controller, clips, expected):
    from app.assets.game_extract import animator_sequences

    result = animator_sequences.derive(controller, clips, animator_sequences.NORMALIZED_TIME)
    # Times are stored rounded to 1e-6, so they compare exactly.
    assert [(item["name"], item["cyclic"], item["restart"],
             [(step["clip"], step["start"], step["leave"], step["fade"]) for step in item["steps"]])
            for item in result] == expected
    assert all(item["id"].startswith("seq-") for item in result) and len({item["id"] for item in result}) == len(result)


def add_facial_route(rows, client, values, clamp=True):
    """Route the attachment's native mesh through a renderer and key its shape in the body clip."""
    import zlib

    path = zlib.crc32(b"Part02")
    client.documents[1]["bones"].append(dict(path="Part02", hash=path, parent=0, translation=[0, 0, 0],
                                             rotation=[0, 0, 0, 1], scale=[1, 1, 1]))
    # External pointers may spell the CAB in lower case.
    rows.append({**gi_row("", "SkinnedMeshRenderer", 40, block=GI_OTHER_BLOCK), "renderer": dict(
        path="Part02", mesh=dict(cab="cab-01", path_id=9), avatar=dict(cab="cab-01", path_id=1), blend_shape_count=1)})
    body = client.documents[20]
    body["bindings"].append(dict(path=path, attribute=SHAPE_HASH, type="SkinnedMeshRenderer", custom=20, pointer=0))
    body["values"] = [frame + [value] for frame, value in zip(body["values"], values)]
    body["acl_count"] += 1
    body["blend_shape_clamp"] = clamp
    return f"{path:08x}:{SHAPE_HASH:08x}:Shape01"


def test_gi_facial_curves_play_relative_to_the_captured_expression(tmp_path):
    from app.assets.animations import read_clip

    root, asset, record, rows, client = animated_fixture(tmp_path)
    record = add_attachment_component(root, asset, rows, client)
    name = add_facial_route(rows, client, [150, 50])
    output = tmp_path / "output"
    report, _ = gi_pipeline.build(client, root, record, output, None, lambda *_: None, threading.Event(), rows)
    assert report["attachments"] == 1 and report["facial_targets"] == 1 and report["facial_error"] is None
    rig = validate_rig_document(output / "Rig.json")
    entry = json.loads((output / "Animation/manifest.json").read_text())["clips"][0]
    metadata, data = read_clip(str(output / "Animation"), entry, rig)
    assert metadata["morphs"]["names"] == [name] and metadata["blend_shape_clamp"] is True
    # The captured weight is recorded for the procedural blink's crossfade.
    assert metadata["morphs"]["rest_weights"] == {name: pytest.approx(1, abs=1e-4)}
    assert metadata["layers"]["facial_targets"] == 1
    # The routed curve is not an omitted channel.
    assert metadata["layers"]["omitted"]["non_transform"] == 1
    frames = metadata["frame_count"]
    weights = np.frombuffer(data, dtype="<f4")[frames * len(rig["bones"]) * 10:]
    # 150% clamps to full weight; the capture already shows full weight, so the clip
    # moves from no change to half of the shape removed.
    assert weights[[0, -1]] == pytest.approx([0, -.5], abs=1e-3)
    (component,) = metadata["morphs"]["components"]
    assert (output / "Animation" / metadata["morphs"]["file"]).is_file()
    assert component["vertex_sha256"] == next(c for c in rig["components"] if "attachment" in c)["vertex_sha256"]


@pytest.mark.parametrize("policies, expected", [
    ([True], [1, 0]), ([False], [2, 0]), ([None], None), ([True, False], None)])
def test_gi_facial_weights_follow_one_known_game_policy(policies, expected):
    from app.assets.game_extract import gi_animation

    facial = dict(offsets={"target": .5})
    if expected is None:
        with pytest.raises(ValueError, match="weight policy"):
            gi_animation._morphs({"target": np.array([2.0, 0.0])}, set(policies), facial)
        return
    morph = gi_animation._morphs({"target": np.array([2.0, 0.0])}, set(policies), facial)
    # Clamping (or not) precedes removing the captured expression.
    assert morph["weights"][:, 0] == pytest.approx(np.array(expected) - .5)
    assert morph["clamp"] is policies[0]


def _remove_controller(rows, client):
    rows[:] = [row for row in rows if row["type"] != "AnimatorController"]


def _duplicate_controller(rows, client):
    controller = next(row for row in rows if row["type"] == "AnimatorController")
    rows.append({**controller, "block": GI_OTHER_BLOCK})


def _break_humanoid_map(rows, client):
    human = client.documents[1]["human"]
    human["skeleton_index"] = [0, *reversed(human["skeleton_index"][1:])]


@pytest.mark.parametrize("change, match", [
    (_remove_controller, "controller is missing or ambiguous"),
    (_duplicate_controller, "controller is missing or ambiguous"),
    (_break_humanoid_map, "does not match the Avatar hierarchy"),
])
def test_gi_build_requires_one_controller_and_a_consistent_humanoid(tmp_path, change, match):
    root, asset, record, rows, client = animated_fixture(tmp_path)
    change(rows, client)
    with pytest.raises(ValueError, match=match):
        gi_pipeline.build(client, root, record, tmp_path / "output", None, lambda *_: None, threading.Event(), rows)


def test_gi_streamer_layer_follows_its_own_motion_set_after_an_additive_layer(tmp_path):
    root, asset, record, rows, client = animated_fixture(tmp_path)
    controller = client.documents[30]
    base, streamer = controller["layers"]
    additive = dict(name="AdjustLayer", state_machine=0, motion_set=1, blend_mode=1, weight=1.0,
                    synced_affects_timing=False, skeleton_mask=[])
    controller["layers"] = [base, additive, {**streamer, "motion_set": 2}]
    for state in controller["states"]:
        # Motion set 1 now belongs to the additive layer, which is not applied.
        state["motions"] = [state["motions"][0], None, *state["motions"][1:]]
    report, _ = gi_pipeline.build(client, root, record, tmp_path / "output", None, lambda *_: None, threading.Event(), rows)
    assert report["streamer_layer"] == "StreamerSyncLayer" and report["unapplied_layers"] == ["AdjustLayer"]
    assert [item["layers"]["streamer_name"] for item in report["animations"]] == ["Layer01"]


def _drift_fixture(drift, *, outliers=0, noise=0.0, swapped=0, native_noise=None):
    """Captured Blend rows of one part and the native mesh they should prove against."""
    blends, weights, slots = [], [], []
    for number in range(60):
        if number < swapped:
            # Near-equal neighbours trade order between the two streams.
            native, bones, captured, ids = (.509, .491), (7, 6), (.505, .495), (1, 2)
        elif number % 2:
            native, bones, ids = (.6, .4), (6, 7), (1, 2)
            captured = (native[0] + drift, native[1] - drift)
        else:
            native, bones, ids = (.7, .3), (5, 6), (0, 1)
            captured = (native[0] + drift, native[1] - drift)
        if number >= 60 - outliers:
            captured = (native[0] + .3, native[1] - .3)
        blends.append([*captured, noise, 0, *ids, 3, 0])
        # ``native_noise`` names the native bone that carries the same light influence.
        weights.append([*native, noise if native_noise is not None else 0, 0])
        slots.append([*bones, native_noise or 0, 0])
    return dict(blends=blends), dict(weights=weights, bone_indices=slots), {number: number for number in range(60)}


@pytest.mark.parametrize("case, expected", [
    ("exact", {"0": 5, "1": 6, "2": 7}), ("drifting", {"0": 5, "1": 6, "2": 7}),
    ("runtime_noise", {"0": 5, "1": 6, "2": 7, "3": 8}), ("swapped_neighbours", {"0": 5, "1": 6, "2": 7}),
    # The viewer maps every weighted id, so a light id with no native counterpart rejects the palette.
    ("unpaired_noise", None), ("too_many_outliers", None), ("beyond_drift", None),
])
def test_gi_weight_drift_proves_palettes_only_by_clear_majorities(case, expected):
    options = dict(exact=dict(drift=0), drifting=dict(drift=.008),
                   runtime_noise=dict(drift=.004, noise=.001, native_noise=8), unpaired_noise=dict(drift=.004, noise=.001),
                   swapped_neighbours=dict(drift=.004, swapped=4), too_many_outliers=dict(drift=.004, outliers=6),
                   beyond_drift=dict(drift=.05))[case]
    part, mesh, mapping = _drift_fixture(**options)
    assert zzz_native._drifted_weight_palette(part, mesh, mapping, gi_pipeline.WEIGHT_DRIFT) == expected
    # Exact proof stays the first choice and never accepts drifting weights on its own.
    if case in ("drifting", "runtime_noise", "unpaired_noise", "too_many_outliers", "beyond_drift"):
        assert zzz_native._weight_palette(part, mesh, mapping) is None


def test_gi_build_exports_only_attached_props_every_verified_prefab_draws(tmp_path):
    import zlib

    root, asset, record, rows, client = animated_fixture(tmp_path)
    bones = client.documents[1]["bones"]
    renderer_path = "Root/Prop01"
    bones.append(dict(path=renderer_path, hash=zlib.crc32(renderer_path.encode()), parent=0,
                      translation=[0, 0, 0], rotation=[0, 0, 0, 1], scale=[1, 1, 1]))
    prop = dict(name="Prop01", vertices=[0, 0, 1, 1, 0, 1, 0, 1, 1], normals=[0, 0, 1] * 3, uv=[0, 0, 1, 0, 0, 1],
                indices=[0, 1, 2], weights=[[1, 0, 0, 0]] * 3, bone_indices=[[0, 0, 0, 0]] * 3,
                bone_hashes=[ACCESSORY], bind_poses=[np.eye(4).ravel().tolist()],
                sections=[dict(first=0, count=3, vertex_first=0, vertex_count=3)])
    # Skill objects (hidden by a prefab), unrouted and unprovable meshes are reported; a mesh
    # driven by a captured bone is not a prop, and names never decide.
    # A two-material prop (sections sharing an edge) becomes one prop per material section.
    split = {**deepcopy(prop), "name": "Prop02", "vertices": [0, 0, 2, 1, 0, 2, 0, 1, 2, 1, 1, 2], "normals": [0, 0, 1] * 4,
             "uv": [0, 0, 1, 0, 0, 1, 1, 1], "indices": [0, 1, 2, 1, 3, 2], "weights": [[1, 0, 0, 0]] * 4,
             "bone_indices": [[0, 0, 0, 0]] * 4, "sections": [dict(first=0, count=3, vertex_first=0, vertex_count=3),
                                                             dict(first=3, count=3, vertex_first=1, vertex_count=3)]}
    meshes = {50: prop, 51: {**deepcopy(prop), "name": "SkillObj01"}, 52: {**deepcopy(prop), "name": "Unrouted01"},
              53: {**deepcopy(prop), "name": "Unknown01"}, 54: {**deepcopy(prop), "name": "Driven01", "bone_hashes": [HAIR]},
              55: split}
    other_avatar = gi_row("Avatar_Other01", "Avatar", 60, block=GI_OTHER_BLOCK)
    client.documents[60] = deepcopy(client.documents[1])
    rows.append(other_avatar)
    for path_id, mesh in meshes.items():
        client.documents[path_id] = mesh
        rows.append(gi_row(mesh["name"], "Mesh", path_id, shape=[[3, 3]]))

    # The captured body diffuse (found by the GIMI component naming) is a BC1 block; the body
    # material's _MainTex holds the same bytes, which proves that slot's role, so the prop's
    # own _MainTex becomes its diffuse. _OtherTex matches nothing and stays unused.
    body_block, prop_block, other_block, second_block = bytes(range(8)), bytes(range(8, 16)), bytes(8), bytes(range(16, 24))
    metadata = json.loads((asset / "hash.json").read_text())
    metadata[0]["texture_hashes"] = [[["Diffuse", ".dds", "33333333"]]]
    (asset / "hash.json").write_text(json.dumps(metadata))
    (asset / "BodyADiffuse.dds").write_bytes(zzz_prop_textures.unity_dds.encode(4, 4, 10, body_block, srgb=True))
    record = index.build_index("GIMI", root)["assets"][0]
    textures = {80: body_block, 81: prop_block, 82: other_block, 83: second_block}

    def material(texture):
        return dict(pointer=dict(cab="CAB-01", path_id=90 + texture, file_ordinal=0, offset=0), name="Material01",
                    textures=[dict(name="_MainTex", texture=dict(cab="cab-textures", path_id=texture)),
                              dict(name="_OtherTex", texture=dict(cab="cab-textures", path_id=82))])

    def renderer(path_id, mesh, visible, avatar=1, texture=(81,), path=renderer_path):
        client.documents[path_id] = dict(materials=[material(number) for number in texture], visible=visible)
        rows.append({**gi_row("", "SkinnedMeshRenderer", path_id, block=GI_OTHER_BLOCK), "renderer": dict(
            path=path, mesh=dict(cab="cab-01", path_id=mesh), avatar=dict(cab="cab-01", path_id=avatar),
            blend_shape_count=0)})

    read = client.read

    def read_texture(row):
        if row["cab"] != "CAB-Textures":
            return read(row)
        (client.work / "texture-1.bin").write_bytes(textures[row["path_id"]])
        return dict(width=4, height=4, format=10, file="texture-1.bin")
    client.read = read_texture
    # The body's own prefab renderer routes through its explicit mesh pointer.
    renderer(69, 2, True, texture=(80,), path="")
    renderer(70, 50, True)
    renderer(71, 50, True)
    # A prefab of a different Avatar does not decide this character's props.
    renderer(72, 50, False, avatar=60)
    renderer(73, 51, True)
    renderer(74, 51, False)
    renderer(75, 53, None)
    renderer(76, 54, True)
    renderer(77, 55, True, texture=(81, 83))
    output = tmp_path / "output"
    files = {"cab-textures": [(GI_OTHER_BLOCK, 4096, 0, "CAB-Textures")]}
    report, _ = gi_pipeline.build(client, root, record, output, None, lambda *_: None, threading.Event(),
                                  zzz_index.IndexRows(rows, files))
    assert [(item["name"], item["textures"], item.get("texture_error")) for item in report["props"]] == [
        ("Prop01", ["diffuse"], None), ("Prop02 1", ["diffuse"], None), ("Prop02 2", ["diffuse"], None)]
    assert "prop_error" not in report
    reasons = {item["name"]: item["reason"] for item in report["skipped_props"]}
    assert set(reasons) == {"SkillObj01", "Unrouted01", "Unknown01"}
    assert "hides the prop" in reasons["SkillObj01"] and "No prefab" in reasons["Unrouted01"]
    assert "could not be proven" in reasons["Unknown01"]
    entry, first, second = json.loads((output / "Animation/manifest.json").read_text())["props"]
    assert entry["name"] == "Prop01" and "texture_error" not in entry
    # The prop's own texture, wrapped as a single-mip sRGB BC1 DDS.
    diffuse = (output / "Animation" / entry["textures"]["diffuse"]["file"]).read_bytes()
    assert set(entry["textures"]) == {"diffuse"} and diffuse[-8:] == prop_block
    records = np.frombuffer((output / "Animation" / entry["file"]).read_bytes()[:3 * 32], dtype="<f4").reshape(3, 8)
    np.testing.assert_allclose(records[:, :3], np.reshape(prop["vertices"], (3, 3)), atol=1e-6)
    assert client.reads[("SkinnedMeshRenderer", 72)] == 0
    # Each section keeps only its own vertices and its own material's texture.
    for part, block, positions, triangles in ((first, prop_block, [[0, 0, 2], [1, 0, 2], [0, 1, 2]], [0, 1, 2]),
                                              (second, second_block, [[1, 0, 2], [0, 1, 2], [1, 1, 2]], [0, 2, 1])):
        data = (output / "Animation" / part["file"]).read_bytes()
        assert (part["vertex_count"], part["index_count"], "section" in part) == (3, 3, False)
        np.testing.assert_allclose(np.frombuffer(data[:96], dtype="<f4").reshape(3, 8)[:, :3], positions, atol=1e-6)
        assert np.frombuffer(data[168:], dtype="<u4").tolist() == triangles
        assert (output / "Animation" / part["textures"]["diffuse"]["file"]).read_bytes()[-8:] == block


def test_gi_prop_failures_leave_clips_unaffected(tmp_path):
    root, asset, record, rows, client = animated_fixture(tmp_path)
    with patch.object(gi_props, "extract", side_effect=ValueError("Synthetic prop failure.")):
        report, _ = gi_pipeline.build(client, root, record, tmp_path / "output", None, lambda *_: None,
                                      threading.Event(), rows)
    assert report["imported"] == 1 and report["props"] == [] and report["prop_error"] == "Synthetic prop failure."
    assert "props" not in json.loads((tmp_path / "output/Animation/manifest.json").read_text())


MOTION = [("Animator", 8, 0, attribute) for attribute in range(7)]
STILL = [0, 0, 0, 0, 0, 0, 1]


def add_skill_object(rows, client, thing, path_id, clips):
    """A family skill object (own Avatar, skinned mesh) and its clips, in another bundle."""
    avatar = dict(name=f"SkillObj_Name01_{thing}_ModelAvatar", bones=[
        dict(path="", hash=0, parent=-1, translation=[0, 0, 0], rotation=[0, 0, 0, 1], scale=[1, 1, 1]),
        dict(path="Bone01", hash=501, parent=0, translation=[0, 1, 0], rotation=[0, 0, 0, 1], scale=[1, 1, 1])])
    bind = np.eye(4)
    bind[1, 3] = -1
    mesh = dict(name=f"{thing}_Model", vertices=[0, 1, 0, 1, 1, 0, 0, 2, 0], normals=[0, 0, 1] * 3, uv=[0, 0, 1, 0, 0, 1],
                indices=[0, 1, 2], weights=[[1, 0, 0, 0]] * 3, bone_indices=[[0, 0, 0, 0]] * 3, bone_hashes=[501],
                bind_poses=[bind.ravel().tolist()], sections=[dict(first=0, count=3, vertex_first=0, vertex_count=3)])
    offset = path_id * 8
    rows += [gi_row(avatar["name"], "Avatar", path_id, block=GI_OTHER_BLOCK, offset=offset),
             gi_row(mesh["name"], "Mesh", path_id + 1, block=GI_OTHER_BLOCK, offset=offset, shape=[[3, 3]]),
             # Lower-detail copies are never props.
             gi_row(mesh["name"] + "_LOD1", "Mesh", path_id + 2, block=GI_OTHER_BLOCK, offset=offset, shape=[[3, 3]])]
    client.documents.update({path_id: avatar, path_id + 1: mesh, path_id + 2: deepcopy(mesh)})
    for number, (suffix, document) in enumerate(clips.items(), start=path_id + 3):
        rows.append(gi_row(f"Ani_SkillObj_Name01_{thing}_{suffix}", "AnimationClip", number, block=GI_OTHER_BLOCK))
        client.documents[number] = document


def test_gi_skill_objects_pair_by_name_and_keep_their_own_scale_and_placement(tmp_path, monkeypatch):
    from app.assets import animations
    from app.assets.rig import validate_rig

    root, asset, record, rows, client = animated_fixture(tmp_path)
    # The Idle state's body clip carries the family token, as Genshin names its clips.
    next(row for row in rows if row["path_id"] == 20)["name"] = "Ani_Avatar_Type01_Name01_Idle01"
    growing = [("Transform", 0, 501, 1), ("Transform", 0, 501, 3), *MOTION]
    # The chair grows in from nothing while it slides one unit forward.
    add_skill_object(rows, client, "Chair01", 200, {
        "Idle01": clip("Chair01", 0.0, 1.0, growing, [[0, 1, 0, 0, 0, 0, *STILL], [0, 1, 1, 1, 1, 1, *STILL]]),
        "Other01": clip("Chair02", 0.0, 1.0, growing, [[0, 1, 0, 1, 1, 1, *STILL]] * 2)})
    # Equal names are not enough: lengths must match, and objects never move by root motion.
    add_skill_object(rows, client, "Cup01", 220, {
        "Idle01": clip("Cup01", 0.0, 2.0, growing, [[0, 1, 0, 1, 1, 1, *STILL]] * 2)})
    add_skill_object(rows, client, "Bot01", 240, {
        "Idle01": clip("Bot01", 0.0, 1.0, growing, [[0, 1, 0, 1, 1, 1, *STILL], [0, 1, 0, 1, 1, 1, 1, 0, 0, 0, 0, 0, 1]])})
    output = tmp_path / "output"
    report, _ = gi_pipeline.build(client, root, record, output, None, lambda *_: None, threading.Event(), rows)
    assert "skill_object_error" not in report and "prop_error" not in report
    assert [(item["name"], item.get("texture_error") is not None) for item in report["props"]] == [("Chair01_Model", True)]
    reasons = {(item["name"], item["source"]): item["reason"] for item in report["skipped_props"]}
    assert reasons == {
        ("Chair01", "Ani_SkillObj_Name01_Chair01_Other01"): "No single character clip has this clip's name.",
        ("Cup01", "Ani_SkillObj_Name01_Cup01_Idle01"): "The paired character clip lasts a different time.",
        ("Bot01", "Ani_SkillObj_Name01_Bot01_Idle01"): "The object moves by root motion, which its paired clip does not."}
    manifest = json.loads((output / "Animation/manifest.json").read_text())
    [entry] = manifest["props"]
    [idle] = [clip_entry["id"] for clip_entry in manifest["clips"] if clip_entry["name"] == "Idle"]
    assert entry["socket"] is None and entry["bone_count"] == 2
    assert entry["track_layout"] == "prop-track-v2" and set(entry["tracks"]) == {idle}
    track = entry["tracks"][idle]
    assert (track["start"], track["end"]) == (0.0, track["duration"])
    deltas = np.frombuffer((output / "Animation" / track["file"]).read_bytes(), "<f4").reshape(track["frame_count"], 2, 3, 4)
    # Placed at the character's origin: scaled to nothing at the start, then moved one unit.
    np.testing.assert_allclose(deltas[0, 1], [[0, 0, 0, 0], [0, 0, 0, 1], [0, 0, 0, 0]], atol=1e-6)
    np.testing.assert_allclose(deltas[-1, 1], [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 1]], atol=1e-6)
    np.testing.assert_allclose(deltas[:, 0], np.broadcast_to(np.eye(4)[:3], (track["frame_count"], 3, 4)), atol=1e-6)
    records = np.frombuffer((output / "Animation" / entry["file"]).read_bytes()[:3 * 32], "<f4").reshape(3, 8)
    np.testing.assert_allclose(records[:, :3], [[0, 1, 0], [1, 1, 0], [0, 2, 0]], atol=1e-6)
    # Serving keeps the affine track layout.
    shutil.copytree(output, asset, dirs_exist_ok=True)
    published = []
    monkeypatch.setattr("app.assets.animations.server.publish_geometry",
                        lambda blob, **_: published.append(blob) or f"/geometry/{len(published)}")
    monkeypatch.setattr("app.assets.animations.server.release_geometry", lambda url: None)
    sessions = animations.AnimationSessions()
    rig = validate_rig(json.loads((output / "Rig.json").read_text()))
    catalog = sessions.catalog(root, record["path"], rig, lambda: True)
    assert catalog["animation_props"] == [dict(id=entry["id"], name="Chair01_Model")]
    [served] = sessions.load_props(catalog["animation_session"])["props"]
    assert served["tracks"]["layout"] == "prop-track-v2"
    assert published[-1] == deltas.tobytes()
    sessions.clear()

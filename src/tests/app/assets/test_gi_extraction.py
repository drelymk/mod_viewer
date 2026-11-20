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
from app.assets.game_extract import gi_index, gi_pipeline, gi_runtime, zzz_index, zzz_native
from app.settings import paths


def write_block(folder, relative, data=b"block"):
    path = folder / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def rows_for(block):
    number = int(block.rsplit("/", 1)[-1].split(".", 1)[0])
    return [
        dict(name="Controller01", type="AnimatorController", block=block, cab="CAB-01", offset=0, file_ordinal=0, path_id=number * 10 + 1),
        dict(name="Clip01", type="AnimationClip", block=block, cab="CAB-01", offset=8, file_ordinal=0, path_id=number * 10 + 2),
        dict(name="Mesh01", type="Mesh", block=block, cab="CAB-01", offset=16, file_ordinal=0, path_id=number * 10 + 3, shape=[[3, 3]]),
        dict(name="Avatar01", type="Avatar", block=block, cab="CAB-01", offset=24, file_ordinal=0, path_id=number * 10 + 4),
        # Facial routes keep only path and pointers; null-mesh renderers are not indexed.
        dict(name="", type="SkinnedMeshRenderer", block=block, cab="CAB-01", offset=32, file_ordinal=0, path_id=number * 10 + 5,
             renderer=dict(path="Part01", mesh=dict(cab="cab-01", path_id=number * 10 + 3),
                           avatar=dict(cab="cab-01", path_id=number * 10 + 4), bone_paths=["Root"], blend_shape_count=2)),
        dict(name="", type="SkinnedMeshRenderer", block=block, cab="CAB-01", offset=32, file_ordinal=0, path_id=number * 10 + 6,
             renderer=dict(path="Part02", mesh=dict(cab="cab-01", path_id=0),
                           avatar=dict(cab="cab-01", path_id=number * 10 + 4), bone_paths=["Root"], blend_shape_count=0)),
    ]


class Client:
    def __init__(self, folder, mutate=lambda row: row):
        self.root, self.mutate = folder.resolve(), mutate

    def scan_many(self, files):
        return [self.mutate(row) for file in files for row in rows_for(file.relative_to(self.root).as_posix())]


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
    assert count == 1 and gi_index.status(folder)["state"] == "ready"
    assert {row["type"] for row in rows} == set(gi_index.TYPES)
    (renderer,) = [row["renderer"] for row in rows if row["type"] == "SkinnedMeshRenderer"]
    assert renderer == dict(path="Part01", mesh=dict(cab="cab-01", path_id=13), avatar=dict(cab="cab-01", path_id=14),
                            blend_shape_count=2)
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
        gi_index.build(Client(folder, lambda row: {**row, "offset": -1} if row["type"] == "Mesh" else row), folder, lambda *_: None, cancel)
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
    output = tmp_path / "output"
    report, _ = gi_pipeline.build(client, root, record, output, None, lambda *_: None, threading.Event(), rows)
    assert report["game"] == "gi" and report["imported"] == 1
    # A plain state's name wins over a blend-tree leaf that shares the same clips.
    assert [item["state"] for item in report["animations"]] == ["Idle"]
    reasons = {item["state"]: item["reason"] for item in report["skipped_details"]}
    assert set(reasons) == {"Tree 2", "Mirrored", "Uneven", "Missing", "Empty", "Silent"}
    assert "no animation curves" in reasons["Silent"]
    assert "IK goals" in reasons["Tree 2"] and "mirrored" in reasons["Mirrored"]
    assert "differ" in reasons["Uneven"] and "pointer" in reasons["Missing"] and "no body motion" in reasons["Empty"]
    rig = validate_rig_document(output / "Rig.json")
    manifest = json.loads((output / "Animation/manifest.json").read_text())
    entry = manifest["clips"][0]
    metadata, data = read_clip(str(output / "Animation"), entry, rig)
    layers = metadata["layers"]
    assert layers["omitted"] == dict(non_transform=1, translation_dof=1, humanoid_owned=2, unbound=1, masked=1)
    assert layers["body_duration"] == 1.0 and layers["streamer_duration"] == 2.0
    frames = np.frombuffer(data, dtype="<f4").reshape(metadata["frame_count"], len(rig["bones"]), 10)
    names = [bone["name"] for bone in rig["bones"]]
    hair, accessory = names.index("Root/Joint01"), names.index("Root/Joint02")
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
    assert [item["id"] for item in again["animations"]] == [entry["id"]]


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


def _drift_fixture(drift, *, outliers=0, noise=0.0, swapped=0):
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
        weights.append([*native, 0, 0])
        slots.append([*bones, 0, 0])
    return dict(blends=blends), dict(weights=weights, bone_indices=slots), {number: number for number in range(60)}


@pytest.mark.parametrize("case, expected", [
    ("exact", {"0": 5, "1": 6, "2": 7}), ("drifting", {"0": 5, "1": 6, "2": 7}),
    ("runtime_noise", {"0": 5, "1": 6, "2": 7}), ("swapped_neighbours", {"0": 5, "1": 6, "2": 7}),
    ("too_many_outliers", None), ("beyond_drift", None),
])
def test_gi_weight_drift_proves_palettes_only_by_clear_majorities(case, expected):
    options = dict(exact=dict(drift=0), drifting=dict(drift=.008), runtime_noise=dict(drift=.004, noise=.001),
                   swapped_neighbours=dict(drift=.004, swapped=4), too_many_outliers=dict(drift=.004, outliers=6),
                   beyond_drift=dict(drift=.05))[case]
    part, mesh, mapping = _drift_fixture(**options)
    assert zzz_native._drifted_weight_palette(part, mesh, mapping, gi_pipeline.WEIGHT_DRIFT) == expected
    # Exact proof stays the first choice and never accepts drifting weights on its own.
    if case in ("drifting", "runtime_noise", "too_many_outliers", "beyond_drift"):
        assert zzz_native._weight_palette(part, mesh, mapping) is None

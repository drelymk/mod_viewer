import os
import struct

from app import metadata
from core.mesh_builder import GeometryBlob, build_mesh_result


def _write_geometry(root):
    (root / "p.buf").write_bytes(struct.pack(
        "<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0))
    (root / "t.buf").write_bytes(struct.pack(
        "<6f", 0, 0, 1, 0, 0, 1))
    (root / "i.buf").write_bytes(struct.pack("<3I", 0, 1, 2))


def _group(root, *, discovered=("B.dds", "normal.dds")):
    for filename in ("A.dds", *discovered):
        (root / filename).write_bytes(b"synthetic dds")
    return [{
        "name": "Component4", "display_name": "Component4",
        "position_file": "p.buf", "position_stride": 12,
        "texcoord_file": "t.buf", "texcoord_stride": 8,
        "ib_file": "i.buf", "index_size": 4,
        "diffuse_pool_files": [{"res": "ResourceA", "file": "A.dds"}],
        "discovered_textures": [
            {"file": "A.dds", "source": "wuwa_ps_slot"},
            *[{"file": filename, "source": "wuwa_filename"}
              for filename in discovered],
        ],
        "draws": [{"label": "Component4-1", "count": 3,
                   "start": 0, "base": 0}],
    }]


def _build(root, *, discovered=("B.dds", "normal.dds")):
    _write_geometry(root)

    def register(path, role, transform=None):
        return f"/texture/{role}/{os.path.basename(path)}"

    return build_mesh_result(
        _group(root, discovered=discovered), str(root), geometry=GeometryBlob(),
        texture_source=register, game_profile="wuwa")


def test_manage_texture_pool_deduplicates_parser_and_discovered_files(
        tmp_path):
    built = _build(tmp_path)
    entry = built.meshes["Component4-1"]

    assert [item["file"] for item in entry["texture_options"]] == [
        "A.dds", "B.dds", "normal.dds"]
    assert all("normal_map" not in item for item in entry["texture_options"])
    assert all("normal_data" not in item for item in entry["texture_options"])

    payload = {"meshes": built.meshes, "textures": {}}
    metadata.hydrate_textures(
        str(tmp_path), payload, texture_profile="wuwa")

    pool = payload["texture_pools"]["p0"]
    assert [item["file"] for item in pool] == [
        "A.dds", "B.dds", "normal.dds"]
    assert all("normal_map" not in item for item in pool)
    assert all("normal_data" not in item for item in pool)


def test_manage_texture_pool_keeps_all_candidates_independent(tmp_path):
    built = _build(tmp_path, discovered=(
        "B.dds", "normal-a.dds", "normal-b.dds"))
    options = built.meshes["Component4-1"]["texture_options"]

    assert [item["file"] for item in options] == [
        "A.dds", "B.dds", "normal-a.dds", "normal-b.dds"]
    assert all("normal_map" not in item for item in options)
    assert all("normal_data" not in item for item in options)

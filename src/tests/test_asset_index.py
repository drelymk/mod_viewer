import builtins
import json
import os

import pytest

from app import asset_folders, paths
from app.api import ModViewerAPI
from app.asset_index import (
    IndividualAssetError,
    build_index,
    index_path,
    index_status,
    load_index,
    lookup_geometry,
    normalize_geometry_hash,
)


def _config(tmp_path, asset_entries=None):
    filename = str(tmp_path / "config.json")
    with open(filename, "w", encoding="utf-8") as stream:
        json.dump({"version": 1, "modFolders": [],
                   "assetFolders": asset_entries or []}, stream)
    return filename


def _write_json(filename, value):
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    with open(filename, "w", encoding="utf-8") as stream:
        json.dump(value, stream)


def _gimi_root(tmp_path, name="gimi"):
    root = tmp_path / name
    (root / "Character").mkdir(parents=True)
    _write_json(str(root / "Character" / "hash.json"), [
        {"ib": "0x3D7B9C89", "object_indexes": [0, 20],
         "object_index_counts": [10, 5]},
        {"ib": "3d7b9c89", "object_indexes": [40]},
    ])
    return root


def _wwmi_root(tmp_path, name="wwmi"):
    root = tmp_path / name
    character = root / "Character"
    character.mkdir(parents=True)
    _write_json(str(character / "Metadata.json"), {
        "vb0_hash": "0XABCDEF12",
        "components": [
            {"index_offset": 0, "index_count": 12},
            {"index_offset": 12, "index_count": 8},
        ],
    })
    (character / "TextureUsage.json").write_text("{}", encoding="utf-8")
    return root


def test_geometry_hash_normalization():
    assert normalize_geometry_hash("3D7B9C89") == "3d7b9c89"
    assert normalize_geometry_hash("0x3d7b9c89") == "3d7b9c89"
    assert normalize_geometry_hash("not-a-hash") is None
    assert normalize_geometry_hash("123") is None


def test_invalid_cache_is_reported_without_rebuilding(tmp_path, monkeypatch):
    root = _gimi_root(tmp_path)
    config = _config(tmp_path)
    monkeypatch.setattr(paths, "config_path", lambda: config)
    filename = index_path("GIMI", str(root))
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    _write_json(filename, {"version": 1, "type": "GIMI",
                           "root": asset_folders.normalize_path(str(root))})

    assert index_status("GIMI", str(root)) == {"status": "invalid"}


def test_gimi_index_merges_ranges_and_builds_reverse_lookup(tmp_path, monkeypatch):
    root = _gimi_root(tmp_path)
    (root / "Notes").mkdir()
    broken = root / "Broken"
    broken.mkdir()
    (broken / "hash.json").write_text("{", encoding="utf-8")

    index = build_index("GIMI", str(root))

    assert index["stats"] == {
        "assetCount": 1,
        "geometryRecordCount": 1,
        "geometryHashCount": 1,
        "skippedCount": 2,
    }
    geometry = index["assets"][0]["geometry"][0]
    assert geometry["hash"] == "3d7b9c89"
    assert geometry["ranges"] == [
        {"firstIndex": 0, "indexCount": 10},
        {"firstIndex": 20, "indexCount": 5},
        {"firstIndex": 40, "indexCount": None},
    ]
    assert lookup_geometry(index, "0X3D7B9C89") == [{"asset": 0, "geometry": 0}]
    assert index["warnings"][0]["path"] == "Broken"

    config = _config(tmp_path)
    monkeypatch.setattr(paths, "config_path", lambda: config)
    from app.asset_index import save_index
    save_index(index)
    assert load_index("GIMI", str(root))["stats"] == index["stats"]
    assert index_status("GIMI", str(root))["status"] == "ready"


def test_zzmi_uses_optional_index_counts(tmp_path):
    root = tmp_path / "zzmi"
    character = root / "Character"
    character.mkdir(parents=True)
    _write_json(str(character / "hash.json"), [{
        "ib": "11223344", "object_indexes": [0, 16],
    }])

    index = build_index("ZZMI", str(root))

    assert index["stats"]["assetCount"] == 1
    assert [item["indexCount"] for item in
            index["assets"][0]["geometry"][0]["ranges"]] == [None, None]


def test_wwmi_index_reads_metadata_and_records_detail_path(tmp_path):
    root = _wwmi_root(tmp_path)

    index = build_index("WWMI", str(root))

    geometry = index["assets"][0]["geometry"][0]
    assert geometry["hash"] == "abcdef12"
    assert geometry["ranges"] == [
        {"firstIndex": 0, "indexCount": 12},
        {"firstIndex": 12, "indexCount": 8},
    ]
    assert geometry["detailMetadata"] == "Character/TextureUsage.json"


def test_wwmi_object_layout_is_supported(tmp_path):
    root = tmp_path / "wwmi-objects"
    object_dir = root / "Character" / "3d7b9c89"
    object_dir.mkdir(parents=True)
    _write_json(str(object_dir / "Metadata.json"), {
        "vb0_hash": "3D7B9C89",
        "components": [{"index_offset": 4, "index_count": 20}],
    })

    index = build_index("WWMI", str(root))

    assert index["assets"][0]["path"] == "Character"
    assert index["assets"][0]["geometry"][0]["metadata"] == \
        "Character/3d7b9c89/Metadata.json"


@pytest.mark.parametrize("asset_type, filename, payload", [
    ("GIMI", "hash.json", [{"ib": "3d7b9c89", "object_indexes": [0]}]),
    ("WWMI", "Metadata.json", {
        "vb0_hash": "3d7b9c89", "components": [{"index_offset": 0}]}),
])
def test_individual_character_folder_gets_specific_error(
        tmp_path, asset_type, filename, payload):
    selected = tmp_path / "Character"
    selected.mkdir()
    _write_json(str(selected / filename), payload)

    with pytest.raises(IndividualAssetError, match="individual character asset"):
        build_index(asset_type, str(selected))


def test_index_builder_does_not_open_heavy_asset_files(tmp_path, monkeypatch):
    root = _gimi_root(tmp_path)
    for suffix in ("dds", "buf", "vb", "ib", "fmt"):
        (root / "Character" / f"payload.{suffix}").write_bytes(b"payload")
    real_open = builtins.open

    def guarded_open(filename, *args, **kwargs):
        if os.fspath(filename).lower().endswith(
                (".dds", ".buf", ".vb", ".ib", ".fmt")):
            raise AssertionError("indexing opened a heavy asset file")
        return real_open(filename, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
    assert build_index("GIMI", str(root))["stats"]["assetCount"] == 1


def test_api_add_rebuild_and_delete_manage_index_transactionally(
        tmp_path, monkeypatch):
    config = _config(tmp_path)
    root = _gimi_root(tmp_path)
    monkeypatch.setattr(paths, "config_path", lambda: config)
    api = ModViewerAPI()
    api._picker_authorized_folders.add(asset_folders.normalize_path(str(root)))

    added = api.add_asset_folder("GIMI", str(root))
    assert added["folders"][0]["index"]["status"] == "ready"
    cache = index_path("GIMI", str(root))
    original_bytes = open(cache, "rb").read()

    assert api.set_asset_folder_enabled(str(root), False)["folders"][0]["enabled"] is False
    (root / "Character" / "hash.json").write_text("{", encoding="utf-8")
    failed = api.rebuild_asset_index(str(root))
    assert failed["indexPreserved"] is True
    assert open(cache, "rb").read() == original_bytes
    assert api.get_asset_folders()["folders"][0]["index"]["status"] == "ready"

    (root / "Character" / "hash.json").write_text(
        json.dumps([{"ib": "3d7b9c89", "object_indexes": [0]}]),
        encoding="utf-8")
    rebuilt = api.rebuild_asset_index(str(root))
    assert rebuilt["folders"][0]["enabled"] is False
    assert rebuilt["folders"][0]["index"]["status"] == "ready"
    assert api.delete_asset_folder(str(root))["folders"] == []
    assert not os.path.exists(cache)


def test_api_add_failure_leaves_config_and_index_unchanged(tmp_path, monkeypatch):
    config = _config(tmp_path)
    root = tmp_path / "invalid"
    root.mkdir()
    monkeypatch.setattr(paths, "config_path", lambda: config)
    api = ModViewerAPI()
    api._picker_authorized_folders.add(asset_folders.normalize_path(str(root)))

    result = api.add_asset_folder("GIMI", str(root))

    assert "error" in result
    assert json.loads(open(config, encoding="utf-8").read())["assetFolders"] == []
    assert not os.path.exists(index_path("GIMI", str(root)))


def test_api_config_failure_restores_new_index(tmp_path, monkeypatch):
    config = _config(tmp_path)
    root = _gimi_root(tmp_path)
    monkeypatch.setattr(paths, "config_path", lambda: config)
    api = ModViewerAPI()
    normalized = asset_folders.normalize_path(str(root))
    api._picker_authorized_folders.add(normalized)

    def fail_write(_entries):
        raise asset_folders.AssetFolderError("config write failed")

    monkeypatch.setattr(asset_folders, "write_entries", fail_write)
    result = api.add_asset_folder("GIMI", str(root))

    assert result["error"] == "config write failed"
    assert json.loads(open(config, encoding="utf-8").read())["assetFolders"] == []
    assert not os.path.exists(index_path("GIMI", str(root)))


def test_api_edit_reindexes_same_root_without_changing_enabled_state(
        tmp_path, monkeypatch):
    config = _config(tmp_path)
    root = _gimi_root(tmp_path)
    monkeypatch.setattr(paths, "config_path", lambda: config)
    api = ModViewerAPI()
    normalized = asset_folders.normalize_path(str(root))
    api._picker_authorized_folders.add(normalized)

    assert api.add_asset_folder("GIMI", str(root))["folders"]
    assert api.set_asset_folder_enabled(str(root), False)["folders"][0]["enabled"] is False
    edited = api.edit_asset_folder(str(root), "GIMI", str(root))

    assert edited["folders"][0]["enabled"] is False
    assert edited["folders"][0]["index"]["status"] == "ready"

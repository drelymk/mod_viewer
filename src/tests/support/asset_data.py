"""Fresh synthetic asset/index inputs shared by asset tests."""

import json


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def gimi_asset_root(tmp_path, name="gimi"):
    root = tmp_path / name
    asset = root / "Asset01"
    asset.mkdir(parents=True)
    write_json(asset / "hash.json", [
        {"ib": "0xA1A1A1A1", "object_indexes": [0, 20],
         "object_index_counts": [10, 5]},
        {"ib": "a1a1a1a1", "object_indexes": [40]},
    ])
    return root


def wwmi_asset_root(tmp_path, name="wwmi"):
    root = tmp_path / name
    asset = root / "Asset01"
    asset.mkdir(parents=True)
    write_json(asset / "Metadata.json", {
        "vb0_hash": "0XABCDEF12",
        "components": [
            {"index_offset": 0, "index_count": 12},
            {"index_offset": 12, "index_count": 8},
        ],
    })
    (asset / "TextureUsage.json").write_text("{}", encoding="utf-8")
    return root


def standard_asset_index(root, *, asset_type="GIMI", asset="Asset01",
                         metadata=None, first_index=12, classification="B"):
    return {
        "version": 1, "type": asset_type, "root": root,
        "builtAt": "2026-01-01T00:00:00Z",
        "stats": {"assetCount": 1, "geometryRecordCount": 1,
                   "geometryHashCount": 1, "skippedCount": 0},
        "assets": [{"path": asset, "geometry": [{
            "hash": "10101010", "ranges": [{
                "firstIndex": first_index, "indexCount": None,
                "classification": classification, "componentOrdinal": 1,
            }], "metadata": metadata or f"{asset}/hash.json",
            "componentName": "Body",
        }]}],
        "byGeometryHash": {"10101010": [{"asset": 0, "geometry": 0}]},
    }

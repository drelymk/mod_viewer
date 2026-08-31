import json

import pytest

from app.mods import metadata


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        (None, []),
        ("45", []),
        ({"45": True}, []),
        ([{"source": "Hair\\HairBlend.buf", "bone_id_offset": 0,
           "bone_ids": [49, 45, True, -1, 47.0, 45, 53]}], [
             {"source": "Hair/HairBlend.buf", "bone_id_offset": 0,
              "bone_ids": [45, 49, 53]},
         ]),
        ([
            {"source": "Hair/HairBlend.buf", "bone_id_offset": 0,
             "bone_ids": [49]},
            {"source": "hair/./HairBlend.buf", "bone_id_offset": 0,
             "bone_ids": [45]},
            {"source": "Hair/HairBlend.buf", "bone_id_offset": 24,
             "bone_ids": [1]},
            {"source": "HairBlend.buf", "bone_id_offset": 0,
             "bone_ids": [99]},
        ], [
            {"source": "Hair/HairBlend.buf", "bone_id_offset": 0,
             "bone_ids": [45, 49]},
            {"source": "Hair/HairBlend.buf", "bone_id_offset": 24,
             "bone_ids": [1]},
            {"source": "HairBlend.buf", "bone_id_offset": 0,
             "bone_ids": [99]},
        ]),
    ],
)
def test_weight_selected_bones_validates_persisted_values(stored, expected):
    assert metadata.weight_selected_bones(data={
        "weight": {"selected_bones": stored},
    }) == expected


def test_save_weight_selected_bones_preserves_unrelated_metadata(tmp_path):
    path = tmp_path / metadata.METADATA_NAME
    original = {
        "mesh_names": {"mesh": "Body"},
        "weight": {"future_option": "preserve"},
    }
    path.write_text(json.dumps(original), encoding="utf-8")

    result = metadata.save_weight_selected_bones(str(tmp_path), [
        {"source": "Hair\\HairBlend.buf", "bone_id_offset": 0,
         "bone_ids": [53, True, 45, -1, 53, 49]},
    ])

    assert result["saved"] is True
    assert result["selected_bones"] == [{
        "source": "Hair/HairBlend.buf", "bone_id_offset": 0,
        "bone_ids": [45, 49, 53],
    }]
    assert json.loads(path.read_text(encoding="utf-8")) == {
        **original,
        "weight": {
            "future_option": "preserve",
            "selected_bones": [{
                "source": "Hair/HairBlend.buf", "bone_id_offset": 0,
                "bone_ids": [45, 49, 53],
            }],
        },
    }


def test_save_weight_selected_bones_rejects_non_list(tmp_path):
    assert metadata.save_weight_selected_bones(
        str(tmp_path), "45") == {"saved": False, "selected_bones": []}
    assert not (tmp_path / metadata.METADATA_NAME).exists()


def test_mesh_color_adjustments_normalize_and_preserve_unrelated_metadata(
        tmp_path):
    path = tmp_path / metadata.METADATA_NAME
    original = {"mesh_names": {"mesh": "Body"}, "weight": {"future": True}}
    path.write_text(json.dumps(original), encoding="utf-8")

    result = metadata.save_mesh_color_adjustment(str(tmp_path), "mesh-key", {
        "hue": 240,
        "saturation": 1.15,
        "brightness": 1.0,
        "contrast": 0.5,
        "red": 0.25,
        "green": 1.0,
        "blue": 2.5,
        "tint": "#AABBCC",
        "tint_strength": 0.4,
    })

    assert result["saved"] is True
    assert json.loads(path.read_text(encoding="utf-8")) == {
        **original,
        "mesh_color_adjustments": {
            "mesh-key": {
                "hue": 180,
                "saturation": 1.15,
                "brightness": 1.0,
                "contrast": 0.5,
                "red": 0.25,
                "green": 1.0,
                "blue": 2.0,
                "tint": "#aabbcc",
                "tint_strength": 0.4,
            },
        },
    }

    assert metadata.save_mesh_color_adjustment(
        str(tmp_path), "mesh-key", {
            "hue": 0, "saturation": 1, "brightness": 1, "contrast": 1,
            "red": 1, "green": 1, "blue": 1,
            "tint": "#ffffff", "tint_strength": 0,
        })["saved"] is True
    assert json.loads(path.read_text(encoding="utf-8")) == original


@pytest.mark.parametrize("invalid", [
    None,
    {"hue": True},
    {"brightness": float("nan")},
    {"contrast": float("inf")},
    {"tint": "white"},
    [],
])
def test_save_mesh_color_adjustment_rejects_malformed_values(tmp_path, invalid):
    assert metadata.save_mesh_color_adjustment(
        str(tmp_path), "mesh-key", invalid)["saved"] is False
    assert not (tmp_path / metadata.METADATA_NAME).exists()


def test_hydrate_mesh_color_adjustments_uses_canonical_and_safe_legacy_keys():
    canonical = "mesh:[5,\"A.ini\",\"Body\",null,null,[3,0,0],[]]"
    payload = {"meshes": {
        "Body-0": {
            "component": "Body", "drawindexed": [3, 0, 0],
            "identity": {"key": canonical},
        },
        "Body-1": {
            "component": "Body", "drawindexed": [6, 0, 0],
        },
    }}
    adjustment = {
        "hue": 35, "saturation": 1, "brightness": 1, "contrast": 1,
        "red": 1, "green": 1, "blue": 1,
        "tint": "#ffffff", "tint_strength": 0.25,
    }
    hydrated = metadata.hydrate_mesh_color_adjustments(payload, {
        "mesh_color_adjustments": {
            canonical: adjustment,
            "Body::6,0,0": adjustment,
        },
    })

    assert hydrated == {
        canonical: adjustment,
        "Body::6,0,0": adjustment,
    }

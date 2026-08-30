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

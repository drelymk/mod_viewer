import json

import pytest

from app.mods import metadata


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        (None, []),
        ("45", []),
        ({"45": True}, []),
        ([49, 45, True, -1, 47.0, 45, 53], [45, 49, 53]),
    ],
)
def test_weight_selected_bone_ids_validates_persisted_values(stored, expected):
    assert metadata.weight_selected_bone_ids(data={
        "weight": {"selected_bone_ids": stored},
    }) == expected


def test_save_weight_selected_bone_ids_preserves_unrelated_metadata(tmp_path):
    path = tmp_path / metadata.METADATA_NAME
    original = {
        "mesh_names": {"mesh": "Body"},
        "weight": {"future_option": "preserve"},
    }
    path.write_text(json.dumps(original), encoding="utf-8")

    result = metadata.save_weight_selected_bone_ids(
        str(tmp_path), [53, True, 45, -1, 53, 49])

    assert result["saved"] is True
    assert result["selected_bone_ids"] == [45, 49, 53]
    assert json.loads(path.read_text(encoding="utf-8")) == {
        **original,
        "weight": {
            "future_option": "preserve",
            "selected_bone_ids": [45, 49, 53],
        },
    }


def test_save_weight_selected_bone_ids_rejects_non_list(tmp_path):
    assert metadata.save_weight_selected_bone_ids(
        str(tmp_path), "45") == {"saved": False, "selected_bone_ids": []}
    assert not (tmp_path / metadata.METADATA_NAME).exists()

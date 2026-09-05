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


def test_rig_pose_preset_lifecycle_preserves_unrelated_metadata(tmp_path):
    preset = {
        "id": "pose-1",
        "name": "  Look Left ",
        "roots": [{"joint_signature": '["body#bone=7"]'}],
        "joints": [{"joint_signature": '["body#bone=8"]',
                    "rotation": [0, 0, 2, 0]}],
    }
    result = metadata.save_rig_pose_preset(str(tmp_path), preset)
    assert result["saved"] is True
    assert result["preset"] == {
        "id": "pose-1", "name": "Look Left",
        "roots": [{"joint_signature": '["body#bone=7"]'}],
        "joints": [{"joint_signature": '["body#bone=8"]',
                    "rotation": [0, 0, 2, 0]}],
    }

    path = tmp_path / metadata.METADATA_NAME
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["future"] = {"keep": True}
    path.write_text(json.dumps(saved), encoding="utf-8")

    renamed = metadata.rename_rig_pose_preset(
        str(tmp_path), "pose-1", "  Turned  ")
    assert renamed["saved"] is True
    assert renamed["preset"]["id"] == "pose-1"
    assert renamed["preset"]["name"] == "Turned"
    deleted = metadata.delete_rig_pose_preset(str(tmp_path), "pose-1")
    assert deleted["saved"] is True
    final = json.loads(path.read_text(encoding="utf-8"))
    assert final["future"] == {"keep": True}
    assert final["rig"] == {"version": 1, "presets": []}


def test_rig_pose_preset_metadata_reports_malformed_section_without_load_failure():
    result = metadata.rig_pose_presets(data={
        "rig": {"version": 2, "presets": []},
    })
    assert result == {
        "version": 1, "presets": [],
        "error": "Pose presets could not be loaded.",
    }


def test_rig_pose_preset_metadata_preserves_malformed_entries_for_frontend():
    stored = {
        "rig": {"version": 1, "presets": [{
            "id": "pose-1", "name": "Partial",
            "roots": [None, {"joint_signature": "not-json"}],
            "joints": [{"joint_signature": "[]", "rotation": [1, 2]}],
        }]},
    }

    result = metadata.rig_pose_presets(data=stored)

    assert result["error"] is None
    assert result["presets"] == stored["rig"]["presets"]


@pytest.mark.parametrize("operation", [
    lambda path: metadata.save_rig_pose_preset(path, {
        "id": "pose-2", "name": "New", "roots": [], "joints": [],
    }),
    lambda path: metadata.rename_rig_pose_preset(path, "pose-1", "Renamed"),
    lambda path: metadata.delete_rig_pose_preset(path, "pose-1"),
])
def test_rig_pose_preset_writes_reject_unsupported_metadata_version(
        tmp_path, operation):
    path = tmp_path / metadata.METADATA_NAME
    path.write_text(json.dumps({
        "rig": {"version": 2, "presets": []}, "future": {"keep": True},
    }), encoding="utf-8")

    result = operation(str(tmp_path))

    assert result == {
        "saved": False,
        "error": "Pose preset metadata uses an unsupported version.",
    }
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "rig": {"version": 2, "presets": []}, "future": {"keep": True},
    }


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


def test_clear_mesh_color_adjustments_is_atomic_and_preserves_other_metadata(
        tmp_path):
    path = tmp_path / metadata.METADATA_NAME
    path.write_text(json.dumps({
        "mesh_names": {"mesh": "Body"},
        "mesh_color_adjustments": {
            "body": {"hue": 30},
            "face": {"hue": 45},
        },
    }), encoding="utf-8")

    result = metadata.clear_mesh_color_adjustments(
        str(tmp_path), ["body", "missing"])

    assert result["saved"] is True
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["mesh_names"] == {"mesh": "Body"}
    assert saved["mesh_color_adjustments"]["face"]["hue"] == 45
    assert "body" not in saved["mesh_color_adjustments"]


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

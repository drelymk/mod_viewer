import json

import pytest

from app.mods import metadata


def test_save_weight_selected_bones_preserves_unrelated_metadata(tmp_path):
    path = tmp_path / metadata.METADATA_NAME
    original = {
        "mesh_names": {"mesh": "Component01"},
        "weight": {"future_option": "preserve"},
    }
    path.write_text(json.dumps(original), encoding="utf-8")

    result = metadata.save_weight_selected_bones(str(tmp_path), [
        {"source": "Component02\\Component02Blend.buf", "bone_id_offset": 0,
         "bone_ids": [53, True, 45, -1, 53, 49]},
    ])

    assert result["saved"] is True
    assert metadata.weight_selected_bones(str(tmp_path)) == result["selected_bones"]
    saved_bytes = path.read_bytes()
    assert metadata.save_weight_selected_bones(str(tmp_path), "invalid")["saved"] is False
    assert path.read_bytes() == saved_bytes
    assert result["selected_bones"] == [{
        "source": "Component02/Component02Blend.buf", "bone_id_offset": 0,
        "source_key": "component02/component02blend.buf|offset=0",
        "bone_ids": [45, 49, 53],
    }]
    assert json.loads(path.read_text(encoding="utf-8")) == {
        **original,
        "weight": {
            "future_option": "preserve",
            "selected_bones": [{
                "source": "Component02/Component02Blend.buf", "bone_id_offset": 0,
                "source_key": "component02/component02blend.buf|offset=0",
                "bone_ids": [45, 49, 53],
            }],
        },
    }


def test_rig_pose_preset_lifecycle_preserves_unrelated_metadata(tmp_path):
    preset = {
        "id": "pose-1",
        "name": "  Look Left ",
        "roots": [{"joint_signature": '["component01#bone=7"]'}],
        "joints": [{"joint_signature": '["component01#bone=8"]',
                    "rotation": [0, 0, 2, 0]}],
    }
    result = metadata.save_rig_pose_preset(str(tmp_path), preset)
    assert result["saved"] is True
    assert result["preset"] == {
        "id": "pose-1", "name": "Look Left",
        "roots": [{"joint_signature": '["component01#bone=7"]'}],
        "joints": [{"joint_signature": '["component01#bone=8"]',
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


def test_humanoid_control_rig_lifecycle_preserves_presets_and_metadata(tmp_path):
    value = {
        "version": 2,
        "model_rig_builder_version": 1,
        "controls": {
            "leftShoulder": {
                "semantic": {"sideN": -0.18, "height01": 0.7,
                              "depthN": 0.01},
                "joint_id": 17,
            },
        },
    }
    metadata.save_rig_pose_preset(str(tmp_path), {
        "id": "pose-1", "name": "Pose", "roots": [], "joints": [],
    })
    path = tmp_path / metadata.METADATA_NAME
    data = json.loads(path.read_text(encoding="utf-8"))
    data["future"] = {"keep": True}
    data["rig"]["limb_mappings"] = {"legacy": "discard"}
    data["rig"]["future_option"] = {"keep": True}
    path.write_text(json.dumps(data), encoding="utf-8")

    semantic = {"version": 2, "controls": {"leftShoulder": {
        "semantic": value["controls"]["leftShoulder"]["semantic"]}}}
    assert metadata.save_humanoid_control_rig(str(tmp_path), semantic)["saved"] is True
    before = path.read_bytes()
    assert metadata.save_humanoid_control_rig(str(tmp_path), {
        "version": 2, "controls": value["controls"],
    })["saved"] is False
    assert path.read_bytes() == before
    assert metadata.humanoid_control_rig(str(tmp_path)) == semantic
    result = metadata.save_humanoid_control_rig(str(tmp_path), value)

    assert result["saved"] is True
    assert metadata.humanoid_control_rig(str(tmp_path)) == value
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["rig"]["presets"] == data["rig"]["presets"]
    assert "limb_mappings" not in saved["rig"]
    assert saved["rig"]["future_option"] == {"keep": True}
    assert saved["future"] == {"keep": True}

    cleared = metadata.clear_humanoid_control_rig(str(tmp_path))
    assert cleared["saved"] is True
    final = json.loads(path.read_text(encoding="utf-8"))
    assert "humanoid_control_rig" not in final["rig"]
    assert final["rig"]["presets"] == data["rig"]["presets"]


@pytest.mark.parametrize("invalid", [
    {"version": 3, "controls": {}},
    {"version": 1, "controls": {"unknown": {
        "semantic": {"sideN": 0, "height01": 0, "depthN": 0}}}},
    {"version": 1, "controls": {"chest": {
        "semantic": {"sideN": True, "height01": 0, "depthN": 0}}}},
    {"version": 1, "controls": {"chest": {
        "semantic": {"sideN": 0, "height01": 0, "depthN": 0},
        "joint_id": -1}}},
])
def test_save_humanoid_control_rig_rejects_malformed_values(tmp_path, invalid):
    result = metadata.save_humanoid_control_rig(str(tmp_path), invalid)
    assert result["saved"] is False
    assert not (tmp_path / metadata.METADATA_NAME).exists()


def test_malformed_humanoid_rig_does_not_hide_valid_pose_presets():
    result = metadata.rig_pose_presets(data={
        "rig": {
            "version": 1,
            "presets": [{"id": "pose-1", "name": "Pose",
                         "roots": [], "joints": []}],
            "humanoid_control_rig": {"version": 9, "controls": {}},
        },
    })
    assert result["presets"][0]["id"] == "pose-1"


def test_model_rig_sidecar_round_trip_is_compact_and_lossless(tmp_path):
    value = {
        "version": 1,
        "builder_version": 1,
        "model_reference_radius": 1.25,
        "source_table": ["component01|offset=0"],
        "joints": [{
            "joint_id": 0,
            "members": [[0, 7]],
            "representative_member_index": 0,
            "parent_id": None,
            "rest_center": [0, 1, 0],
            "rest_pivot": [0, 1, 0],
            "rest_frame": [0, 0, 0, 1],
        }],
        "edges": [],
    }
    saved = metadata.save_model_rig(str(tmp_path), value)
    assert saved == {"saved": True,
                     "path": str(tmp_path / metadata.MODEL_RIG_METADATA_NAME)}
    assert metadata.load_model_rig(str(tmp_path)) == value
    sidecar_text = (tmp_path / metadata.MODEL_RIG_METADATA_NAME).read_text(
        encoding="utf-8")
    assert "\n" not in sidecar_text
    assert json.loads(sidecar_text) == value
    assert metadata.save_model_rig(str(tmp_path), {
        **value, "joints": [{**value["joints"][0],
                              "members": [[0, 7], [0, 8]],
                              "representative_member_index": 1}],
    })["saved"] is True


def test_model_rig_sidecar_rejects_impossible_topology(tmp_path):
    def joint(number, parent):
        return {"joint_id": number, "members": [[0, number + 7]],
                "representative_member_index": 0, "parent_id": parent,
                "rest_center": [0, number, 0], "rest_pivot": [0, number, 0],
                "rest_frame": [0, 0, 0, 1]}
    value = {"version": 1, "builder_version": 1,
             "model_reference_radius": 1, "source_table": ["component01|offset=0"],
             "joints": [joint(0, 1), joint(1, 0)], "edges": [{
                 "joint_a": 0, "joint_b": 1, "relationship_type": "source",
                 "edge_strength": 1, "edge_pivot": [0, 0, 0]}]}
    assert metadata.save_model_rig(str(tmp_path), value)["saved"] is False
    assert not (tmp_path / metadata.MODEL_RIG_METADATA_NAME).exists()



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
    original = {"mesh_names": {"mesh": "Component01"}, "weight": {"future": True}}
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
                },
        },
    }

    before = path.read_bytes()
    assert metadata.save_mesh_color_adjustment(
        str(tmp_path), "mesh-key", {"brightness": float("nan")})["saved"] is False
    assert path.read_bytes() == before
    assert metadata.save_mesh_color_adjustment(
        str(tmp_path), "mesh-key", {
            "hue": 0, "saturation": 1, "brightness": 1, "contrast": 1,
            "red": 1, "green": 1, "blue": 1,
            "tint": "#ffffff", "tint_strength": 0,
        })["saved"] is True
    assert json.loads(path.read_text(encoding="utf-8")) == original


def test_clear_mesh_color_adjustments_if_unchanged_mixes_and_handles_absent(
        tmp_path):
    path = tmp_path / metadata.METADATA_NAME
    path.write_text(json.dumps({
        "future": {"setting": 7},
        "mesh_color_adjustments": {
            "matching": {"hue": 30},
            "newer": {"hue": 60},
            "malformed": {"hue": "invalid"},
        },
    }), encoding="utf-8")

    result = metadata.clear_mesh_color_adjustments_if_unchanged(
        str(tmp_path), {
            "matching": {"hue": 30},
            "newer": {"hue": 30},
            "absent": {"hue": 30},
            "malformed": {"hue": 30},
        })

    assert result["cleared"] == ["absent", "matching"]
    assert result["preserved"] == ["newer", "malformed"]
    assert result["failed"] == []
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["mesh_color_adjustments"] == {
        "newer": {"hue": 60}, "malformed": {"hue": "invalid"}}
    assert saved["future"] == {"setting": 7}


def test_clear_mesh_color_adjustments_if_unchanged_reports_save_failure(
        tmp_path, monkeypatch):
    path = tmp_path / metadata.METADATA_NAME
    original = {
        "mesh_color_adjustments": {
            "matching": {"hue": 30},
            "newer": {"hue": 60},
        },
    }
    path.write_text(json.dumps(original), encoding="utf-8")

    def fail_save(*_args, **_kwargs):
        raise OSError("metadata write failed")

    monkeypatch.setattr(metadata, "_save", fail_save)
    result = metadata.clear_mesh_color_adjustments_if_unchanged(
        str(tmp_path), {
            "matching": {"hue": 30},
            "newer": {"hue": 30},
        })

    assert result["cleared"] == []
    assert result["preserved"] == ["newer"]
    assert result["failed"] == ["matching"]
    assert json.loads(path.read_text(encoding="utf-8")) == original


def test_hydrate_mesh_color_adjustments_uses_canonical_and_safe_legacy_keys():
    canonical = "mesh:[5,\"A.ini\",\"Component01\",null,null,[3,0,0],[]]"
    payload = {"meshes": {
        "Component01-0": {
            "component": "Component01", "drawindexed": [3, 0, 0],
            "identity": {"key": canonical},
        },
        "Component01-1": {
            "component": "Component01", "drawindexed": [6, 0, 0],
        },
    }}
    adjustment = {
        "hue": 35, "saturation": 1, "brightness": 1, "contrast": 1,
        "red": 1, "green": 1, "blue": 1,
        "tint": "#ffffff",
    }
    hydrated = metadata.hydrate_mesh_color_adjustments(payload, {
        "mesh_color_adjustments": {
            canonical: adjustment,
            "Component01::6,0,0": adjustment,
        },
    })

    assert hydrated == {
        canonical: {**adjustment},
        "Component01::6,0,0": {**adjustment},
    }

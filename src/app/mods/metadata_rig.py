"""Rig presets and humanoid overrides in the shared viewer metadata file."""

import math
from copy import deepcopy

from .metadata_storage import (
    _LOCK,
    load,
    _save,
)


RIG_METADATA_KEY = "rig"
RIG_METADATA_VERSION = 1
RIG_PRESET_NAME_MAX_LENGTH = 80
RIG_SIGNATURE_MAX_LENGTH = 1024
HUMANOID_CONTROL_RIG_VERSION = 2
MODEL_RIG_BUILDER_VERSION = 4
HUMANOID_CONTROL_KEYS = (
    "chest", "pelvis", "neck", "head", "leftShoulder", "leftElbow", "leftHand",
    "rightShoulder", "rightElbow", "rightHand", "leftHip", "leftKnee",
    "leftFoot", "rightHip", "rightKnee", "rightFoot",
)
HUMANOID_SEMANTIC_KEYS = ("sideN", "height01", "depthN")


def _normalized_rig_entry(value):
    """Keep entry-level data lossless; the browser resolves and normalizes it."""
    return deepcopy(value)


def _normalized_rig_preset(value):
    if not isinstance(value, dict):
        return None
    preset_id = value.get("id")
    name = value.get("name")
    if (not isinstance(preset_id, str) or not preset_id.strip()
            or not isinstance(name, str)):
        return None
    name = name.strip()
    if not name or len(name) > RIG_PRESET_NAME_MAX_LENGTH:
        return None
    roots = value.get("roots")
    joints = value.get("joints")
    if not isinstance(roots, list) or not isinstance(joints, list):
        return None
    normalized_roots = [_normalized_rig_entry(entry) for entry in roots]
    normalized_joints = [_normalized_rig_entry(entry) for entry in joints]
    return {
        "id": preset_id.strip(), "name": name,
        "roots": normalized_roots, "joints": normalized_joints,
    }


def _normalized_humanoid_entry(value):
    if not isinstance(value, dict):
        return None
    semantic = value.get("semantic")
    if not isinstance(semantic, dict):
        return None
    normalized_semantic = {}
    for key in HUMANOID_SEMANTIC_KEYS:
        number = semantic.get(key)
        if (isinstance(number, bool) or not isinstance(number, (int, float))
                or not math.isfinite(number)):
            return None
        normalized_semantic[key] = float(number)
    has_joint_id = "joint_id" in value
    joint_id = value.get("joint_id")
    if (joint_id is not None
            and (isinstance(joint_id, bool) or not isinstance(joint_id, int)
                 or joint_id < 0)):
        return None
    result = {"semantic": normalized_semantic}
    if has_joint_id:
        result["joint_id"] = joint_id
    return result


def _normalized_humanoid_control_rig(value, *, strict=False):
    if value is None:
        return None
    if (not isinstance(value, dict)
            or value.get("version") != HUMANOID_CONTROL_RIG_VERSION
            or not isinstance(value.get("controls"), dict)):
        if strict:
            return None
        return {"version": HUMANOID_CONTROL_RIG_VERSION, "controls": {},
                "error": "Humanoid control-rig metadata could not be loaded."}
    controls = {}
    malformed = False
    has_builder_version = "model_rig_builder_version" in value
    builder_version = value.get("model_rig_builder_version")
    if has_builder_version and (
            isinstance(builder_version, bool)
            or not isinstance(builder_version, int)):
        if strict:
            return None
        malformed = True
    if strict and has_builder_version \
            and builder_version != MODEL_RIG_BUILDER_VERSION:
        return None
    has_explicit_joint_mapping = False
    for key, raw in value["controls"].items():
        if key not in HUMANOID_CONTROL_KEYS:
            malformed = True
            if strict:
                return None
            continue
        entry = _normalized_humanoid_entry(raw)
        if entry is None:
            malformed = True
            if strict:
                return None
            if isinstance(raw, dict) and "joint_id" in raw:
                semantic_only = dict(raw)
                semantic_only.pop("joint_id", None)
                entry = _normalized_humanoid_entry(semantic_only)
                if entry is not None:
                    # Preserve the fact that this was an explicit mapping so
                    # the frontend can reject it instead of auto-mapping it.
                    entry["joint_id"] = None
                    controls[key] = entry
            continue
        controls[key] = entry
        if "joint_id" in entry:
            has_explicit_joint_mapping = True
    if (strict and has_explicit_joint_mapping
            and (not has_builder_version
                 or builder_version != MODEL_RIG_BUILDER_VERSION)):
        return None
    if strict and malformed:
        return None
    result = {"version": HUMANOID_CONTROL_RIG_VERSION, "controls": controls}
    if strict and has_builder_version:
        result["model_rig_builder_version"] = builder_version
    elif has_builder_version and isinstance(builder_version, int):
        result["model_rig_builder_version"] = builder_version
    if malformed:
        result["error"] = "Some humanoid control-rig overrides were ignored."
    return result


def humanoid_control_rig(folder_path=None, data=None):
    """Return normalized semantic HumanoidControlRig overrides."""
    data = (load(folder_path) if data is None and folder_path is not None
            else ({} if data is None else data))
    rig = data.get(RIG_METADATA_KEY) if isinstance(data, dict) else None
    raw = rig.get("humanoid_control_rig") if isinstance(rig, dict) else None
    return _normalized_humanoid_control_rig(raw)


def _updated_rig_envelope(raw_rig, presets):
    """Return the shared versioned Rig envelope for metadata updates."""
    rig = deepcopy(raw_rig) if isinstance(raw_rig, dict) else {}
    rig.update({"version": RIG_METADATA_VERSION,
                "presets": deepcopy(presets)})
    # Limb mappings belonged to the removed manual IK bridge. Do not preserve
    # or migrate them when the current preset metadata is rewritten.
    rig.pop("limb_mappings", None)
    return rig


def _rig_data_for_humanoid_update(data):
    raw_rig = data.get(RIG_METADATA_KEY) if isinstance(data, dict) else None
    if raw_rig is not None and not isinstance(raw_rig, dict):
        return None, "Rig metadata uses an unsupported version."
    current = rig_pose_presets(data=data)
    if current["error"]:
        return None, "Pose preset metadata could not be updated."
    return _updated_rig_envelope(raw_rig, current["presets"]), None


def save_humanoid_control_rig(folder_path, value):
    """Atomically save semantic humanoid overrides beside a mod."""
    normalized = _normalized_humanoid_control_rig(value, strict=True)
    if normalized is None:
        return {"saved": False, "error": "Invalid humanoid control rig."}
    with _LOCK:
        data = load(folder_path)
        rig, error = _rig_data_for_humanoid_update(data)
        if error:
            return {"saved": False, "error": error}
        rig["humanoid_control_rig"] = normalized
        data[RIG_METADATA_KEY] = rig
        return {**_save(folder_path, data),
                "humanoid_control_rig": normalized}


def clear_humanoid_control_rig(folder_path):
    """Remove only HumanoidControlRig overrides and preserve other metadata."""
    with _LOCK:
        data = load(folder_path)
        raw_rig = data.get(RIG_METADATA_KEY)
        if not isinstance(raw_rig, dict) or "humanoid_control_rig" not in raw_rig:
            return {"saved": False}
        rig = deepcopy(raw_rig)
        rig.pop("humanoid_control_rig", None)
        if rig.get("presets") is not None or len(rig) > 0:
            data[RIG_METADATA_KEY] = rig
        else:
            data.pop(RIG_METADATA_KEY, None)
        return _save(folder_path, data)


def rig_pose_presets(folder_path=None, data=None):
    """Return the versioned saved Rig presets without exposing runtime IDs."""
    data = (load(folder_path) if data is None and folder_path is not None
            else ({} if data is None else data))
    rig = data.get(RIG_METADATA_KEY) if isinstance(data, dict) else None
    if rig is None:
        return {"version": RIG_METADATA_VERSION, "presets": [], "error": None}
    if (not isinstance(rig, dict)
            or rig.get("version") != RIG_METADATA_VERSION
            or not isinstance(rig.get("presets"), list)):
        return {"version": RIG_METADATA_VERSION, "presets": [],
                "error": "Pose presets could not be loaded."}
    presets = []
    seen_ids = set()
    malformed = False
    for raw in rig["presets"]:
        preset = _normalized_rig_preset(raw)
        if preset is None or preset["id"] in seen_ids:
            malformed = True
            continue
        seen_ids.add(preset["id"])
        presets.append(preset)
    result = {
        "version": RIG_METADATA_VERSION, "presets": presets,
        "error": "Pose presets could not be loaded."
        if malformed else None,
    }
    return result


def _rig_data_for_update(data):
    raw_rig = data.get(RIG_METADATA_KEY) if isinstance(data, dict) else None
    if raw_rig is not None and not isinstance(raw_rig, dict):
        return None, "Pose preset metadata uses an unsupported version."
    if isinstance(raw_rig, dict):
        if raw_rig.get("version") != RIG_METADATA_VERSION:
            return None, "Pose preset metadata uses an unsupported version."
        if not isinstance(raw_rig.get("presets"), list):
            return None, "Pose preset metadata could not be updated."
    current = rig_pose_presets(data=data)
    if current["error"]:
        return None, "Pose preset metadata could not be updated."
    raw_rig = data.get(RIG_METADATA_KEY)
    return _updated_rig_envelope(raw_rig, current["presets"]), None


def save_rig_pose_preset(folder_path, preset):
    """Atomically add one validated preset while preserving other metadata."""
    normalized = _normalized_rig_preset(preset)
    if normalized is None:
        return {"saved": False, "error": "Invalid pose preset."}
    with _LOCK:
        data = load(folder_path)
        rig, error = _rig_data_for_update(data)
        if error:
            return {"saved": False, "error": error}
        if any(item["id"] == normalized["id"] for item in rig["presets"]):
            return {"saved": False, "error": "A pose with this ID already exists."}
        if any(item["name"].casefold() == normalized["name"].casefold()
               for item in rig["presets"]):
            return {"saved": False, "error": "A pose with this name already exists."}
        rig["presets"].append(normalized)
        data[RIG_METADATA_KEY] = rig
        return {**_save(folder_path, data), "preset": normalized,
                "presets": rig["presets"]}


def rename_rig_pose_preset(folder_path, preset_id, name):
    """Rename a preset by stable ID without changing its pose data."""
    if not isinstance(preset_id, str) or not preset_id.strip():
        return {"saved": False, "error": "Invalid pose preset ID."}
    if not isinstance(name, str) or not name.strip() \
            or len(name.strip()) > RIG_PRESET_NAME_MAX_LENGTH:
        return {"saved": False, "error": "Invalid pose preset name."}
    normalized_name = name.strip()
    with _LOCK:
        data = load(folder_path)
        rig, error = _rig_data_for_update(data)
        if error:
            return {"saved": False, "error": error}
        target = next((item for item in rig["presets"]
                       if item["id"] == preset_id.strip()), None)
        if target is None:
            return {"saved": False, "error": "Pose preset was not found."}
        if any(item["id"] != target["id"]
               and item["name"].casefold() == normalized_name.casefold()
               for item in rig["presets"]):
            return {"saved": False, "error": "A pose with this name already exists."}
        target["name"] = normalized_name
        data[RIG_METADATA_KEY] = rig
        return {**_save(folder_path, data), "preset": target,
                "presets": rig["presets"]}


def delete_rig_pose_preset(folder_path, preset_id):
    """Delete one preset by stable ID and retain an empty rig section."""
    if not isinstance(preset_id, str) or not preset_id.strip():
        return {"saved": False, "error": "Invalid pose preset ID."}
    with _LOCK:
        data = load(folder_path)
        rig, error = _rig_data_for_update(data)
        if error:
            return {"saved": False, "error": error}
        before = len(rig["presets"])
        rig["presets"] = [item for item in rig["presets"]
                          if item["id"] != preset_id.strip()]
        if len(rig["presets"]) == before:
            return {"saved": False, "error": "Pose preset was not found."}
        data[RIG_METADATA_KEY] = rig
        return {**_save(folder_path, data), "deleted": True,
                "presets": rig["presets"]}

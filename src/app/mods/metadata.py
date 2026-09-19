"""Viewer-only mesh labels and texture choices stored beside a mod."""
from collections import Counter
import json
import math
import os
import threading
from copy import deepcopy

from core.textures import (encode_texture_key, split_texture_key,
                           texture_key_for_role)
from core.materials.kind import normalize_material_kind
from core.geometry.skinning import (
    normalize_skinning_source_file, skinning_source_key,
)
from core.textures.profiles import texture_profile_for
from core.textures.color_adjustment import (
    normalize_color_adjustment as _normalize_mesh_color_adjustment,
    is_neutral_color_adjustment as _is_neutral_mesh_color_adjustment,
)
from core.mod_source import is_archive_path

METADATA_NAME = ".mod_viewer.json"
MODEL_RIG_METADATA_NAME = ".mod_viewer.rig.json"
PRESENT_NAMES_KEY = "__all__"
_LOCK = threading.RLock()

MESH_COLOR_ADJUSTMENTS_KEY = "mesh_color_adjustments"
RIG_METADATA_KEY = "rig"
RIG_METADATA_VERSION = 1
RIG_PRESET_NAME_MAX_LENGTH = 80
RIG_SIGNATURE_MAX_LENGTH = 1024
HUMANOID_CONTROL_RIG_VERSION = 2
MODEL_RIG_VERSION = 1
MODEL_RIG_BUILDER_VERSION = 1
MODEL_RIG_SOURCE_MAX_LENGTH = 1024
MODEL_RIG_MAX_JOINTS = 250000
MODEL_RIG_MAX_MEMBERS_PER_JOINT = 256
MODEL_RIG_MAX_EDGES = 250000
MODEL_RIG_MAX_BYTES = 64 * 1024 * 1024
HUMANOID_CONTROL_KEYS = (
    "chest", "pelvis", "neck", "head", "leftShoulder", "leftElbow", "leftHand",
    "rightShoulder", "rightElbow", "rightHand", "leftHip", "leftKnee",
    "leftFoot", "rightHip", "rightKnee", "rightFoot",
)
HUMANOID_SEMANTIC_KEYS = ("sideN", "height01", "depthN")


def _legacy_mesh_key(name, entry):
    component = entry.get("component")
    if not component:
        component = name.rsplit("-", 1)[0] if name.rsplit("-", 1)[-1].isdigit() else name
    draw = entry.get("drawindexed")
    draw_key = ",".join(str(value) for value in draw) if draw else "whole"
    return f"{component}::{draw_key}"


def _canonical_mesh_key(name, entry):
    """Return the key used to expose state for one displayed mesh."""
    identity = entry.get("identity") if isinstance(entry, dict) else None
    canonical = identity.get("key") if isinstance(identity, dict) else None
    if not isinstance(canonical, str) or not canonical:
        return _legacy_mesh_key(name, entry)
    return canonical


def _legacy_mesh_key_counts(meshes):
    """Count legacy-key ownership among the current displayed meshes."""
    counts = Counter()
    for name, entry in meshes.items():
        if not isinstance(entry, dict) or entry.get("error"):
            continue
        counts[_legacy_mesh_key(name, entry)] += 1
    return counts


def _mesh_metadata_keys(name, entry, legacy_key_counts=None):
    """Return safe canonical and legacy read keys for one mesh."""
    canonical = _canonical_mesh_key(name, entry)
    identity = entry.get("identity") if isinstance(entry, dict) else None
    has_canonical = isinstance(identity, dict) and isinstance(
        identity.get("key"), str) and bool(identity.get("key"))
    legacy = _legacy_mesh_key(name, entry)
    if has_canonical:
        if canonical == legacy:
            return (canonical,)
        if (legacy_key_counts is None
                or legacy_key_counts.get(legacy, 0) == 1):
            return canonical, legacy
        return (canonical,)
    if (legacy_key_counts is not None
            and legacy_key_counts.get(legacy, 0) != 1):
        return ()
    return (legacy,)


def load(folder_path, source=None):
    try:
        if source is not None:
            data = json.loads(source.read_text(
                source.resolve_resource(METADATA_NAME)))
        elif is_archive_path(folder_path):
            return {}
        else:
            with open(os.path.join(folder_path, METADATA_NAME), encoding="utf-8") as fh:
                data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError, UnicodeError):
        return {}


def _save(folder_path, data):
    if is_archive_path(folder_path):
        return {"saved": False,
                "error": "Viewer metadata cannot be saved for compressed mods."}
    path = os.path.join(folder_path, METADATA_NAME)
    temp_path = path + ".tmp"
    with open(temp_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(temp_path, path)
    return {"saved": True, "path": path}


def _model_rig_text(value, maximum=MODEL_RIG_SOURCE_MAX_LENGTH):
    return (isinstance(value, str) and bool(value) and len(value) <= maximum
            and "\x00" not in value)


def _model_rig_vector(value, length):
    if not isinstance(value, list) or len(value) != length:
        return None
    if any(isinstance(item, bool) or not isinstance(item, (int, float))
           or not math.isfinite(item) for item in value):
        return None
    return [float(item) for item in value]


def _normalized_model_rig(value, *, include_text=False):
    if not isinstance(value, dict):
        return None
    if value.get("version") != MODEL_RIG_VERSION \
            or value.get("builder_version") != MODEL_RIG_BUILDER_VERSION:
        return None
    source_table = value.get("source_table")
    joints = value.get("joints")
    edges = value.get("edges")
    model_reference_radius = value.get("model_reference_radius")
    if (not isinstance(source_table, list)
            or not isinstance(joints, list)
            or not isinstance(edges, list)
            or len(joints) == 0 or len(joints) > MODEL_RIG_MAX_JOINTS
            or len(edges) > MODEL_RIG_MAX_EDGES
            or isinstance(model_reference_radius, bool)
            or not isinstance(model_reference_radius, (int, float))
            or not math.isfinite(model_reference_radius)
            or model_reference_radius <= 0):
        return None

    sources = []
    source_keys = set()
    for source in source_table:
        if not _model_rig_text(source):
            return None
        if source in source_keys:
            return None
        source_keys.add(source)
        sources.append(source)

    normalized_joints = []
    for index, joint in enumerate(joints):
        if (not isinstance(joint, dict)
                or isinstance(joint.get("joint_id"), bool)
                or joint.get("joint_id") != index):
            return None
        members = joint.get("members")
        representative_member_index = joint.get("representative_member_index")
        parent_id = joint.get("parent_id")
        if (not isinstance(members, list)
                or len(members) > MODEL_RIG_MAX_MEMBERS_PER_JOINT
                or len(members) == 0
                or (isinstance(representative_member_index, bool)
                    or not isinstance(representative_member_index, int)
                    or representative_member_index < 0
                    or representative_member_index >= len(members))
                or (parent_id is not None
                    and (isinstance(parent_id, bool)
                         or not isinstance(parent_id, int)
                         or parent_id < 0 or parent_id >= len(joints)
                         or parent_id == index))):
            return None
        normalized_members = []
        for member in members:
            if (not isinstance(member, list) or len(member) != 2
                    or isinstance(member[0], bool)
                    or not isinstance(member[0], int) or member[0] < 0
                    or member[0] >= len(source_table)
                    or isinstance(member[1], bool)
                    or not isinstance(member[1], int) or member[1] < 0):
                return None
            bone_id = member[1]
            normalized_members.append([member[0], bone_id])
        vectors = {
            key: _model_rig_vector(joint.get(key), length)
            for key, length in (("rest_center", 3), ("rest_pivot", 3),
                                ("rest_frame", 4))}
        if any(vector is None for vector in vectors.values()):
            return None
        normalized_joints.append({
            "joint_id": index,
            "members": normalized_members,
            "representative_member_index": representative_member_index,
            "parent_id": parent_id,
            **vectors,
        })
    visit_state = [0] * len(normalized_joints)
    for start in range(len(normalized_joints)):
        if visit_state[start] == 2:
            continue
        path = []
        current = start
        while current is not None and visit_state[current] == 0:
            visit_state[current] = 1
            path.append(current)
            current = normalized_joints[current]["parent_id"]
        if current is not None and visit_state[current] == 1:
            return None
        for index in path:
            visit_state[index] = 2

    normalized_edges = []
    edge_keys = set()
    for edge in edges:
        if not isinstance(edge, dict):
            return None
        joint_a = edge.get("joint_a")
        joint_b = edge.get("joint_b")
        relationship_type = edge.get("relationship_type")
        edge_strength = edge.get("edge_strength")
        edge_pivot = _model_rig_vector(edge.get("edge_pivot"), 3)
        if (isinstance(joint_a, bool) or not isinstance(joint_a, int)
                or isinstance(joint_b, bool) or not isinstance(joint_b, int)
                or joint_a < 0 or joint_b < 0 or joint_a >= len(joints)
                or joint_b >= len(joints) or joint_a == joint_b
                or relationship_type not in ("source", "attachment")
                or isinstance(edge_strength, bool)
                or not isinstance(edge_strength, (int, float))
                or not math.isfinite(edge_strength) or edge_strength < 0
                or edge_pivot is None):
            return None
        left, right = sorted((joint_a, joint_b))
        edge_key = (left, right)
        if edge_key in edge_keys:
            return None
        edge_keys.add(edge_key)
        normalized_edges.append({
            "joint_a": left,
            "joint_b": right,
            "relationship_type": relationship_type,
            "edge_strength": float(edge_strength),
            "edge_pivot": edge_pivot,
        })
    expected_edge_keys = {
        tuple(sorted((joint["parent_id"], joint["joint_id"])))
        for joint in normalized_joints if joint["parent_id"] is not None
    }
    if edge_keys != expected_edge_keys:
        return None

    normalized = {
        "version": MODEL_RIG_VERSION,
        "builder_version": MODEL_RIG_BUILDER_VERSION,
        "model_reference_radius": float(model_reference_radius),
        "source_table": sources,
        "joints": normalized_joints,
        "edges": normalized_edges,
    }
    try:
        text = json.dumps(normalized, separators=(",", ":"),
                          ensure_ascii=False)
        if len(text.encode("utf-8")) > MODEL_RIG_MAX_BYTES:
            return None
    except (TypeError, ValueError, UnicodeError):
        return None
    return (normalized, text) if include_text else normalized


def load_model_rig(folder_path, source=None):
    """Load a validated cached ModelRig sidecar, if one is present."""
    try:
        if source is not None:
            path = source.resolve_resource(MODEL_RIG_METADATA_NAME)
            if not path or not source.is_file(path) \
                    or source.size(path) > MODEL_RIG_MAX_BYTES:
                return None
            return _normalized_model_rig(json.loads(source.read_text(path)))
        if is_archive_path(folder_path):
            return None
        path = os.path.join(folder_path, MODEL_RIG_METADATA_NAME)
        if os.path.getsize(path) > MODEL_RIG_MAX_BYTES:
            return None
        with open(path, encoding="utf-8") as fh:
            return _normalized_model_rig(json.load(fh))
    except (OSError, ValueError, TypeError):
        return None


def save_model_rig(folder_path, model_rig):
    """Atomically replace the cached ModelRig sidecar."""
    normalized_result = _normalized_model_rig(model_rig, include_text=True)
    if normalized_result is None:
        return {"saved": False, "error": "Invalid ModelRig metadata."}
    if is_archive_path(folder_path):
        return {"saved": False,
                "error": "Viewer metadata cannot be saved for compressed mods."}
    normalized, text = normalized_result
    with _LOCK:
        path = os.path.join(folder_path, MODEL_RIG_METADATA_NAME)
        temp_path = path + ".tmp"
        try:
            with open(temp_path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            os.replace(temp_path, path)
        except (OSError, TypeError, ValueError) as error:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            return {"saved": False, "error": str(error)}
    return {"saved": True, "path": path}


def mesh_color_adjustments(folder_path=None, data=None):
    """Return validated non-neutral per-mesh color states."""
    data = (load(folder_path) if data is None and folder_path is not None
            else ({} if data is None else data))
    saved = data.get(MESH_COLOR_ADJUSTMENTS_KEY) \
        if isinstance(data, dict) else None
    if not isinstance(saved, dict):
        return {}
    result = {}
    for mesh_key, value in saved.items():
        if not isinstance(mesh_key, str) or not mesh_key:
            continue
        normalized = _normalize_mesh_color_adjustment(value)
        if normalized is not None and not _is_neutral_mesh_color_adjustment(
                normalized):
            result[mesh_key] = normalized
    return result


def save_mesh_color_adjustment(folder_path, mesh_key, adjustment):
    """Persist one viewer-only color state without touching source assets."""
    if not isinstance(mesh_key, str) or not mesh_key:
        return {"saved": False, "error": "Invalid mesh metadata key."}
    normalized = _normalize_mesh_color_adjustment(
        adjustment, reject_invalid=True)
    if normalized is None:
        return {"saved": False, "error": "Invalid mesh color adjustment."}
    with _LOCK:
        data = load(folder_path)
        raw_adjustments = data.get(MESH_COLOR_ADJUSTMENTS_KEY)
        raw_has_entry = (isinstance(raw_adjustments, dict)
                         and mesh_key in raw_adjustments)
        adjustments = mesh_color_adjustments(data=data)
        if _is_neutral_mesh_color_adjustment(normalized):
            existed = mesh_key in adjustments or raw_has_entry
            adjustments.pop(mesh_key, None)
        else:
            existed = adjustments.get(mesh_key) == normalized
            adjustments[mesh_key] = normalized
        if adjustments:
            data[MESH_COLOR_ADJUSTMENTS_KEY] = adjustments
        else:
            data.pop(MESH_COLOR_ADJUSTMENTS_KEY, None)
        if not adjustments and not data and not existed:
            return {"saved": False}
        if existed and not _is_neutral_mesh_color_adjustment(normalized):
            return {"saved": False}
        if not existed and _is_neutral_mesh_color_adjustment(normalized):
            return {"saved": False}
        return _save(folder_path, data)


def clear_mesh_color_adjustments_if_unchanged(
        folder_path, expected_adjustments):
    """Remove only color states that still match the completed save request."""
    result = {"cleared": [], "preserved": [], "failed": [], "saved": False}
    if not isinstance(expected_adjustments, dict):
        result["error"] = "Invalid expected mesh color adjustments."
        return result

    expected = {}
    invalid_input = False
    for mesh_key, adjustment in expected_adjustments.items():
        if not isinstance(mesh_key, str) or not mesh_key:
            invalid_input = True
            continue
        normalized = _normalize_mesh_color_adjustment(
            adjustment, reject_invalid=True)
        if normalized is None:
            result["failed"].append(mesh_key)
            invalid_input = True
        else:
            expected[mesh_key] = normalized
    if invalid_input:
        result["failed"] = [
            key for key in expected_adjustments
            if isinstance(key, str) and key]
        result["error"] = "Invalid expected mesh color adjustment."
        return result

    with _LOCK:
        data = load(folder_path)
        raw_adjustments = data.get(MESH_COLOR_ADJUSTMENTS_KEY)
        if not isinstance(raw_adjustments, dict):
            result["cleared"].extend(expected)
            return result

        removable = []
        for mesh_key, expected_adjustment in expected.items():
            if mesh_key not in raw_adjustments:
                result["cleared"].append(mesh_key)
                continue
            current = _normalize_mesh_color_adjustment(
                raw_adjustments[mesh_key])
            if current is None or current != expected_adjustment:
                result["preserved"].append(mesh_key)
                continue
            removable.append(mesh_key)

        if not removable:
            return result

        updated_adjustments = dict(raw_adjustments)
        for mesh_key in removable:
            updated_adjustments.pop(mesh_key, None)
        updated_data = dict(data)
        if updated_adjustments:
            updated_data[MESH_COLOR_ADJUSTMENTS_KEY] = updated_adjustments
        else:
            updated_data.pop(MESH_COLOR_ADJUSTMENTS_KEY, None)
        try:
            saved = _save(folder_path, updated_data)
        except Exception as exc:
            result["failed"].extend(removable)
            result["error"] = str(exc)
            return result

        result["cleared"].extend(removable)
        result["saved"] = (bool(saved.get("saved"))
                           if isinstance(saved, dict) else True)
        return result


def hydrate_mesh_color_adjustments(payload, data=None):
    """Project saved color state through canonical/legacy mesh identities."""
    saved = mesh_color_adjustments(data=data)
    meshes = payload.get("meshes", {}) if isinstance(payload, dict) else {}
    if not isinstance(meshes, dict):
        return {}
    legacy_key_counts = _legacy_mesh_key_counts(meshes)
    hydrated = {}
    for name, entry in meshes.items():
        if not isinstance(entry, dict) or entry.get("error"):
            continue
        keys = _mesh_metadata_keys(name, entry, legacy_key_counts)
        if not keys:
            continue
        value = next((saved[key] for key in keys if key in saved), None)
        if value is not None:
            hydrated[_canonical_mesh_key(name, entry)] = value.copy()
    return hydrated


def save_mesh_names(folder_path, names):
    with _LOCK:
        data = load(folder_path)
        data["mesh_names"] = names if isinstance(names, dict) else {}
        return _save(folder_path, data)


def save_textures(folder_path, textures):
    with _LOCK:
        data = load(folder_path)
        data["textures"] = textures if isinstance(textures, dict) else {}
        return _save(folder_path, data)


def _normalized_weight_bones(value):
    """Normalize source-scoped selection entries and merge duplicate sources."""
    if not isinstance(value, list):
        return []
    merged = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        source = normalize_skinning_source_file(item.get("source"))
        offset = item.get("bone_id_offset")
        source_key = item.get("source_key")
        if (source is None or isinstance(offset, bool)
                or not isinstance(offset, int) or offset < 0):
            continue
        if not isinstance(source_key, str) or not source_key.strip():
            source_key = skinning_source_key(source, offset)
        else:
            source_key = source_key.strip().replace("\\", "/").casefold()
        key = source_key
        if key is None:
            continue
        bone_ids = {
            bone_id for bone_id in (item.get("bone_ids") or [])
            if isinstance(bone_id, int) and not isinstance(bone_id, bool)
            and bone_id >= 0
        }
        if not bone_ids:
            continue
        entry = merged.setdefault(key, {
            "source": source,
            "source_key": source_key,
            "bone_id_offset": offset,
            "bone_ids": set(),
        })
        entry["bone_ids"].update(bone_ids)
    return [
        {
            "source": entry["source"],
            "source_key": entry["source_key"],
            "bone_id_offset": entry["bone_id_offset"],
            "bone_ids": sorted(entry["bone_ids"]),
        }
        for _key, entry in sorted(merged.items())
    ]


def weight_selected_bones(folder_path=None, data=None):
    """Return the validated viewer-saved source-scoped Bone selection."""
    data = (load(folder_path) if data is None and folder_path is not None
            else ({} if data is None else data))
    weight = data.get("weight") if isinstance(data, dict) else None
    return _normalized_weight_bones(
        weight.get("selected_bones") if isinstance(weight, dict) else None)


def save_weight_selected_bones(folder_path, bones):
    """Persist only the normalized source-scoped Bone selection."""
    if not isinstance(bones, list):
        return {"saved": False, "selected_bones": []}
    normalized = _normalized_weight_bones(bones)
    with _LOCK:
        data = load(folder_path)
        weight = data.get("weight")
        if not isinstance(weight, dict):
            weight = {}
        weight["selected_bones"] = normalized
        data["weight"] = weight
        return {
            **_save(folder_path, data),
            "selected_bones": normalized,
        }


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


def _valid_rig_signature(value):
    if (not isinstance(value, str) or not value.strip()
            or len(value) > RIG_SIGNATURE_MAX_LENGTH):
        return False
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return False
    return (isinstance(parsed, list) and bool(parsed)
            and all(isinstance(item, str) and item for item in parsed))


def _normalized_humanoid_entry(value, *, strict=False):
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
        entry = _normalized_humanoid_entry(raw, strict=strict)
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


def hydrate_mesh_names(payload, data=None):
    """Project saved mesh names onto current canonical metadata keys."""
    data = data if isinstance(data, dict) else {}
    saved = data.get("mesh_names")
    if not isinstance(saved, dict):
        return {}
    meshes = payload.get("meshes", {}) if isinstance(payload, dict) else {}
    legacy_key_counts = _legacy_mesh_key_counts(meshes)
    hydrated = {}
    for name, entry in meshes.items():
        if not isinstance(entry, dict) or entry.get("error"):
            continue
        keys = _mesh_metadata_keys(name, entry, legacy_key_counts)
        if not keys:
            continue
        value = next((saved[key] for key in keys
                      if isinstance(saved.get(key), str)
                      and saved[key].strip()), None)
        if value is not None:
            hydrated[keys[0]] = value
    return hydrated


def _source_key(value):
    return str(value or "")


def component_material_kinds(folder_path, data=None):
    """Return supported overrides keyed by source and canonical component."""
    data = (load(folder_path) if data is None and folder_path is not None
            else ({} if data is None else data))
    saved = data.get("component_material_kinds") if isinstance(data, dict) else None
    if not isinstance(saved, dict):
        return {}
    result = {}
    for source, components in saved.items():
        if not isinstance(source, str) or not isinstance(components, dict):
            continue
        clean = {}
        for component, value in components.items():
            if not isinstance(component, str) or not component:
                continue
            kind = normalize_material_kind(value, overrides_only=True)
            if kind is not None:
                clean[component] = kind
        if clean:
            result[source] = clean
    return result


def save_component_material_kind(folder_path, source, component, material_kind):
    """Save/remove one source-qualified component material-kind override."""
    if not isinstance(source, str):
        source = _source_key(source)
    if not isinstance(component, str) or not component:
        return {"saved": False}
    normalized = str(material_kind or "").strip().lower()
    kind = normalize_material_kind(normalized, overrides_only=True)
    if normalized not in ("", "auto", "unknown") and kind is None:
        return {"saved": False}
    with _LOCK:
        data = load(folder_path)
        overrides = component_material_kinds(folder_path, data)
        source_overrides = overrides.setdefault(source, {})
        if kind is not None:
            source_overrides[component] = kind
        else:
            source_overrides.pop(component, None)
            if not source_overrides:
                overrides.pop(source, None)
        if overrides:
            data["component_material_kinds"] = overrides
        else:
            data.pop("component_material_kinds", None)
        return _save(folder_path, data)


def present_names(folder_path, ini_rel, data=None):
    data = (load(folder_path) if data is None else data).get("present_names", {})
    names = data.get(ini_rel, {}) if isinstance(data, dict) else {}
    if not isinstance(names, dict):
        return {}
    return {str(index): name for index, name in names.items()
            if str(index).isdigit() and isinstance(name, str) and name.strip()}


def all_present_names(folder_path, source=None):
    """Return a detached snapshot suitable for edit-session rollback."""
    names = load(folder_path, source=source).get("present_names")
    return deepcopy(names) if isinstance(names, dict) else None


def restore_present_names(folder_path, names):
    """Restore only PRESENT metadata, preserving unrelated viewer settings."""
    with _LOCK:
        data = load(folder_path)
        if isinstance(names, dict) and names:
            data["present_names"] = deepcopy(names)
        else:
            data.pop("present_names", None)
        return _save(folder_path, data)


def apply_present_name(data, ini_rel, position, name):
    """Apply one sparse PRESENT name to an in-memory metadata mapping."""
    position = int(position)
    name = str(name or "").strip()
    if not name:
        raise ValueError("a present name is required")
    default = f"Present {position + 1}"
    all_names = data.get("present_names")
    if not isinstance(all_names, dict):
        all_names = {}
    names = all_names.get(ini_rel)
    if not isinstance(names, dict):
        names = {}
    if name == default:
        if str(position) not in names:
            return False
        names.pop(str(position), None)
    else:
        if names.get(str(position)) == name:
            return False
        names[str(position)] = name
    if names:
        all_names[ini_rel] = names
    else:
        all_names.pop(ini_rel, None)
    if all_names:
        data["present_names"] = all_names
    else:
        data.pop("present_names", None)
    return True


def save_present_name(folder_path, ini_rel, position, name):
    """Persist only names that differ from their implicit ``Present N``."""
    with _LOCK:
        data = load(folder_path)
        if not apply_present_name(data, ini_rel, position, name):
            return {"saved": False}
        return _save(folder_path, data)


def apply_clear_present_names(data, ini_rel):
    """Remove one PRESENT name mapping from an in-memory metadata mapping."""
    all_names = data.get("present_names")
    if not isinstance(all_names, dict) or ini_rel not in all_names:
        return False
    all_names.pop(ini_rel, None)
    if all_names:
        data["present_names"] = all_names
    else:
        data.pop("present_names", None)
    return True


def clear_present_names(folder_path, ini_rel):
    with _LOCK:
        data = load(folder_path)
        if not apply_clear_present_names(data, ini_rel):
            return {"saved": False}
        return _save(folder_path, data)


def apply_delete_present_name(data, ini_rel, position, old_count):
    """Shift sparse PRESENT names after deleting one position in memory."""
    position = int(position)
    old_count = int(old_count)
    all_names = data.get("present_names")
    if not isinstance(all_names, dict):
        return False
    names = all_names.get(ini_rel)
    if not isinstance(names, dict):
        return False
    shifted = {}
    for old_index in range(old_count):
        if old_index == position:
            continue
        old_name = names.get(str(old_index))
        if not old_name:
            continue
        new_index = old_index if old_index < position else old_index - 1
        if old_name != f"Present {new_index + 1}":
            shifted[str(new_index)] = old_name
    if shifted:
        all_names[ini_rel] = shifted
    else:
        all_names.pop(ini_rel, None)
    if all_names:
        data["present_names"] = all_names
    else:
        data.pop("present_names", None)
    return True


def delete_present_name(folder_path, ini_rel, position, old_count):
    """Remove one name and shift sparse overrides with their value positions."""
    with _LOCK:
        data = load(folder_path)
        if not apply_delete_present_name(data, ini_rel, position, old_count):
            return {"saved": False}
        return _save(folder_path, data)


def hydrate_present(folder_path, present, data=None):
    item = present.get("item") if isinstance(present, dict) else None
    if not isinstance(item, dict):
        return
    count = int(item.get("count") or 0)
    data = load(folder_path) if data is None else data
    custom = present_names(folder_path, PRESENT_NAMES_KEY, data)
    if not custom:
        for ini in item.get("inis", []):
            custom = present_names(folder_path, ini, data)
            if custom:
                break
    item["names"] = [custom.get(str(index), f"Present {index + 1}")
                     for index in range(count)]


def hydrate_component_material_kinds(meshes, data=None):
    """Apply one saved component override to every draw in that component."""
    if not isinstance(meshes, dict):
        return {}
    saved = component_material_kinds(None, data)
    hydrated = {}
    for name, entry in meshes.items():
        if not isinstance(entry, dict) or entry.get("error"):
            continue
        source = _source_key(entry.get("source"))
        component = entry.get("component")
        source_overrides = saved.get(source, {})
        override = (source_overrides.get(component)
                    if isinstance(component, str) else None)
        if override is None:
            entry["material_kind_override"] = None
            continue
        entry["material_kind_override"] = override
        entry["material_kind_evidence"] = {
            "kind": override,
            "reliable": True,
            "reason": "viewer component material-kind override",
        }
        hydrated.setdefault(source, {})[component] = override
    return hydrated


def hydrate_textures(folder_path, payload, data=None, texture_source=None,
                     texture_profile=None, source=None):
    """Restore sparse highlighted boundaries, then rebuild component pools.

    ``payload`` is the structured application payload; only its ``meshes``
    and texture registry fields are mutated here.
    """
    data = load(folder_path) if data is None else data
    meshes = payload.setdefault("meshes", {})
    textures = payload.setdefault("textures", {})
    saved = data.get("textures")
    if not isinstance(saved, dict):
        saved = {}

    highlighted = {}
    packed_normal_transport = (
        texture_profile_for(texture_profile).normal_transport_role
        == "normal_data")

    def _role_key(value, role):
        if not value:
            return None
        return texture_key_for_role(value, role)

    def _pool_option(value):
        if not isinstance(value, dict):
            return None
        key = texture_key_for_role(value.get("tex_key"), "diffuse")
        if not key:
            return None
        _role, relative_path = split_texture_key(key)
        option = dict(value)
        option["tex_key"] = key
        option["file"] = relative_path
        for field in ("normal_map", "normal_data", "light_map",
                      "material_map", "emission_map"):
            if field in option:
                option[field] = _role_key(option[field], field)
        return option

    for name, state in saved.items():
        if not isinstance(state, dict):
            continue
        key, label, manual = (texture_key_for_role(
                                  state.get("tex_key"), "diffuse"),
                              state.get("label"), state.get("manual"))
        if (not isinstance(key, str) or not key
                or not isinstance(label, str) or not label
                or not isinstance(manual, bool)):
            continue
        _role, relative_path = split_texture_key(key)
        item = {"tex_key": key, "file": relative_path,
                "label": label, "manual": manual}
        fields = ("light_map", "material_map", "emission_map")
        if not packed_normal_transport:
            fields = ("normal_map", "normal_data", *fields)
        for field in fields:
            value = _role_key(state.get(field), field)
            if value:
                item[field] = value
            manual = state.get(f"{field}_manual")
            if isinstance(manual, bool) and manual:
                item[f"{field}_manual"] = True
        if packed_normal_transport:
            packed = _role_key(state.get("normal_data"), "normal_data")
            legacy = _role_key(state.get("normal_map"), "normal_map")
            if packed:
                item["normal_data"] = packed
            elif legacy:
                # Older WuWa metadata stored the same authored file under
                # normal_map.  Migrate only the in-memory representation; the
                # next legitimate save converges it on normal_data.
                _legacy_role, legacy_path = split_texture_key(legacy)
                item["normal_data"] = texture_key_for_role(
                    legacy_path, "normal_data")
            if (state.get("normal_data_manual") is True
                    or state.get("normal_map_manual") is True):
                item["normal_data_manual"] = True
        highlighted[name] = item

    restored = {}
    legacy_key_counts = _legacy_mesh_key_counts(meshes)
    for name, entry in meshes.items():
        if not isinstance(entry, dict) or entry.get("error"):
            continue
        keys = _mesh_metadata_keys(name, entry, legacy_key_counts)
        mesh_key = _canonical_mesh_key(name, entry)
        state = next((highlighted[key] for key in keys
                      if key in highlighted), None)
        if state:
            restored[mesh_key] = state
            if state["manual"]:
                entry["saved_texture_override"] = state["tex_key"]

    # Rebuild each shared component pool from ini options plus saved boundary
    # textures. The pool no longer needs to be duplicated for every draw in JSON.
    pools = {}
    for name, entry in meshes.items():
        if not isinstance(entry, dict) or entry.get("error"):
            continue
        group = (entry.get("source"), entry.get("component"))
        pool = pools.setdefault(group, [])
        candidates = list(entry.get("texture_options") or [])
        mesh_key = _canonical_mesh_key(name, entry)
        if mesh_key in restored:
            state = restored[mesh_key]
            candidates.append({key: value for key, value in state.items()
                               if key != "manual"})
        for raw_opt in candidates:
            opt = _pool_option(raw_opt)
            if opt is None:
                continue
            old = next((item for item in pool
                        if item["tex_key"] == opt["tex_key"]), None)
            if old is None:
                pool.append(opt)
            else:
                for field in ("normal_map", "normal_data", "light_map",
                              "material_map", "emission_map"):
                    manual_key = f"{field}_manual"
                    if opt.get(manual_key):
                        old[manual_key] = True
                        # A saved manual flag without a value is an explicit
                        # tombstone. Remove the fresh INI-derived value
                        # instead of merely winning future writes.
                        if field in opt:
                            if opt[field]:
                                old[field] = opt[field]
                            else:
                                old.pop(field, None)
                        else:
                            old.pop(field, None)
                    elif opt.get(field):
                        old[field] = opt[field]

    texture_pools = {}
    pool_ids = {}
    for name, entry in meshes.items():
        if not isinstance(entry, dict) or entry.get("error"):
            continue
        group = (entry.get("source"), entry.get("component"))
        pool_id = pool_ids.get(group)
        if pool_id is None:
            pool_id = f"p{len(pool_ids)}"
            pool_ids[group] = pool_id
            texture_pools[pool_id] = pools.get(group, [])
        entry["texture_pool_id"] = pool_id
        entry.pop("texture_options", None)

    role_fields = (
        ("tex_key", "diffuse"),
        ("normal_map", "normal_map"),
        ("normal_data", "normal_data"),
        ("light_map", "light_map"),
        ("material_map", "material_map"),
        ("emission_map", "emission_map"),
    )
    for pool in texture_pools.values():
        for option in pool:
            for field, role in role_fields:
                key = option.get(field)
                if not key or key in textures:
                    continue
                encoded = encode_texture_key(
                    folder_path, key, role, texture_source=texture_source,
                    texture_profile=texture_profile, source=source)
                if encoded and not encoded.get("error"):
                    textures[encoded["tex_key"]] = encoded["uri"]

    payload["texture_pools"] = texture_pools

    return restored

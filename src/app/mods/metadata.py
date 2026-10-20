"""Viewer metadata public interface, mesh appearance and texture choices."""

from collections import Counter

from core.textures import encode_texture_key, split_texture_key, texture_key_for_role
from core.materials.kind import normalize_material_kind
from core.geometry.skinning import (
    normalize_skinning_source_file, skinning_source_key,
)
from core.textures.profiles import texture_profile_for
from core.textures.color_adjustment import (
    normalize_color_adjustment as _normalize_mesh_color_adjustment,
    is_neutral_color_adjustment as _is_neutral_mesh_color_adjustment,
)

from .metadata_storage import (
    METADATA_NAME as METADATA_NAME,
    _LOCK,
    load as load,
    _save,
)
from .metadata_rig import (
    RIG_METADATA_KEY as RIG_METADATA_KEY,
    RIG_METADATA_VERSION as RIG_METADATA_VERSION,
    RIG_PRESET_NAME_MAX_LENGTH as RIG_PRESET_NAME_MAX_LENGTH,
    RIG_SIGNATURE_MAX_LENGTH as RIG_SIGNATURE_MAX_LENGTH,
    HUMANOID_CONTROL_RIG_VERSION as HUMANOID_CONTROL_RIG_VERSION,
    MODEL_RIG_BUILDER_VERSION as MODEL_RIG_BUILDER_VERSION,
    HUMANOID_CONTROL_KEYS as HUMANOID_CONTROL_KEYS,
    HUMANOID_SEMANTIC_KEYS as HUMANOID_SEMANTIC_KEYS,
    humanoid_control_rig as humanoid_control_rig,
    save_humanoid_control_rig as save_humanoid_control_rig,
    clear_humanoid_control_rig as clear_humanoid_control_rig,
    rig_pose_presets as rig_pose_presets,
    save_rig_pose_preset as save_rig_pose_preset,
    rename_rig_pose_preset as rename_rig_pose_preset,
    delete_rig_pose_preset as delete_rig_pose_preset,
)
from .metadata_present import (
    PRESENT_NAMES_KEY as PRESENT_NAMES_KEY,
    present_names as present_names,
    all_present_names as all_present_names,
    restore_present_names as restore_present_names,
    apply_present_name as apply_present_name,
    save_present_name as save_present_name,
    apply_clear_present_names as apply_clear_present_names,
    clear_present_names as clear_present_names,
    apply_delete_present_name as apply_delete_present_name,
    delete_present_name as delete_present_name,
    hydrate_present as hydrate_present,
)


MESH_COLOR_ADJUSTMENTS_KEY = "mesh_color_adjustments"


def _legacy_mesh_key(name, entry):
    component = entry.get("component")
    if not component:
        component = name.rsplit("-", 1)[0] if name.rsplit("-", 1)[-1].isdigit() else name
    draw = entry.get("drawindexed")
    draw_key = ",".join(str(value) for value in draw) if draw else "whole"
    return f"{component}::{draw_key}"


def _canonical_mesh_key(entry):
    """Return the key used to expose state for one displayed mesh."""
    identity = entry.get("identity") if isinstance(entry, dict) else None
    canonical = identity.get("key") if isinstance(identity, dict) else None
    if not isinstance(canonical, str) or not canonical:
        raise ValueError("Mesh payload is missing a canonical identity.")
    return canonical


def _legacy_mesh_key_counts(meshes):
    """Count legacy-key ownership among the current displayed meshes."""
    counts = Counter()
    for name, entry in meshes.items():
        if not isinstance(entry, dict) or entry.get("error"):
            continue
        counts[_legacy_mesh_key(name, entry)] += 1
    return counts


def _mesh_metadata_keys(name, entry, legacy_key_counts):
    """Return safe canonical and legacy read keys for one mesh."""
    canonical = _canonical_mesh_key(entry)
    legacy = _legacy_mesh_key(name, entry)
    if canonical != legacy and legacy_key_counts.get(legacy, 0) == 1:
        return canonical, legacy
    return (canonical,)


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
        value = next((saved[key] for key in keys if key in saved), None)
        if value is not None:
            hydrated[keys[0]] = value.copy()
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
        mesh_key = keys[0]
        state = next((highlighted[key] for key in keys
                      if key in highlighted), None)
        if state:
            restored[mesh_key] = state
            if state["manual"]:
                entry["saved_texture_override"] = state["tex_key"]

    # Rebuild each shared component pool from ini options plus saved boundary
    # textures. The pool no longer needs to be duplicated for every draw in JSON.
    pools = {}
    pool_options = {}
    for name, entry in meshes.items():
        if not isinstance(entry, dict) or entry.get("error"):
            continue
        group = (entry.get("source"), entry.get("component"))
        pool = pools.setdefault(group, [])
        options_by_key = pool_options.setdefault(group, {})
        candidates = list(entry.get("texture_options") or [])
        mesh_key = _canonical_mesh_key(entry)
        if mesh_key in restored:
            state = restored[mesh_key]
            candidates.append({key: value for key, value in state.items()
                               if key != "manual"})
        for raw_opt in candidates:
            opt = _pool_option(raw_opt)
            if opt is None:
                continue
            old = options_by_key.get(opt["tex_key"])
            if old is None:
                pool.append(opt)
                options_by_key[opt["tex_key"]] = opt
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
                    source=source)
                if encoded and not encoded.get("error"):
                    textures[encoded["tex_key"]] = encoded["uri"]

    if texture_source is not None:
        # Reuse publication eligibility; detached analysis has no image URLs.
        for pool in texture_pools.values():
            pool[:] = [option for option in pool
                       if textures.get(option["tex_key"])]
            for option in pool:
                for field, _role in role_fields[1:]:
                    if option.get(field) and not textures.get(option[field]):
                        option.pop(field)

    payload["texture_pools"] = texture_pools

    return restored

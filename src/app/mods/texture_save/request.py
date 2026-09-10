"""Request, texture ownership, and authored-role validation for Save to Texture."""

import os

from core.geometry.semantics import (
    authored_texture_keys_for_draw, deduplicate_draws,
)
from core.resource_paths import _canonical, safe_resource_path
from core.textures import TEXTURE_ROLES, split_texture_key
from core.textures.dds import inspect_dds

from app.assets.textures import is_asset_texture_key
from app.mods.analysis import resolved_draws

from .errors import TextureSaveError


TEXTURE_USAGE_ROLES = tuple(TEXTURE_ROLES)
TEXTURE_ROLE_LABELS = {
    "normal_map": "Normal Map",
    "normal_data": "Normal Data",
    "light_map": "Light Map",
    "material_map": "Material Map",
    "emission_map": "Emission Map",
}


def canonical_mod_path(mod_dir, relative_path):
    """Resolve a file strictly inside the mod root, not its escape ceiling."""
    path = safe_resource_path(mod_dir, relative_path)
    if not path or not os.path.isfile(path):
        return None
    root = _canonical(mod_dir)
    canonical = _canonical(path)
    try:
        if os.path.commonpath((canonical, root)) != root:
            return None
    except ValueError:
        return None
    return path


def physical_identity(path):
    return os.path.normcase(os.path.realpath(os.path.abspath(path))).casefold()


def texture_path(mod_dir, key, *, selected=False):
    if not key:
        if selected:
            raise TextureSaveError(
                "no_diffuse", "The selected mesh has no diffuse texture.")
        return None
    role, relative_path = split_texture_key(key)
    if selected and role != "diffuse":
        raise TextureSaveError(
            "not_diffuse_texture", "Save to Texture requires a diffuse texture.")
    if role != "diffuse" or not relative_path:
        return None
    if is_asset_texture_key(key):
        if selected:
            raise TextureSaveError(
                "asset_texture_read_only",
                "Asset textures cannot be modified.", "unsupported")
        return None
    if selected and not relative_path.lower().endswith(".dds"):
        raise TextureSaveError(
            "unsupported_texture_type",
            "Save to Texture currently requires a DDS source.", "unsupported")
    path = canonical_mod_path(mod_dir, relative_path)
    if path is None:
        if selected:
            raise TextureSaveError(
                "texture_not_found", "The selected diffuse texture was not found.")
        return None
    return path


def usage_texture_path(mod_dir, key, expected_role):
    """Resolve one submitted role assignment with the shared mod sandbox."""
    if not key:
        return None
    role, relative_path = split_texture_key(key, expected_role)
    if role != expected_role or not relative_path:
        return None
    if is_asset_texture_key(key):
        return None
    return canonical_mod_path(mod_dir, relative_path)


def inspect_save_texture(path):
    if not path.lower().endswith(".dds"):
        raise TextureSaveError(
            "unsupported_texture_type",
            "Save to Texture currently requires a DDS source.", "unsupported")
    info = inspect_dds(path)
    if info is None:
        raise TextureSaveError(
            "invalid_dds", "The selected texture is not a valid supported DDS.")
    if info.format not in {"bc7_unorm", "bc7_srgb"}:
        raise TextureSaveError(
            "unsupported_texture_format",
            f"DDS format {info.format} is not supported by Save to Texture.",
            "unsupported")
    return info


def validate_usage(active_mesh_keys, texture_usage):
    if not isinstance(texture_usage, list):
        raise TextureSaveError(
            "stale_mesh_state",
            "The model changed before the texture save started.")
    entries = []
    keys = []
    for item in texture_usage:
        if not isinstance(item, dict) or "tex_key" in item:
            raise TextureSaveError(
                "stale_mesh_state",
                "The model changed before the texture save started.")
        key = item.get("semantic_key")
        if not isinstance(key, str) or not key or key in keys:
            raise TextureSaveError(
                "stale_mesh_state",
                "The model changed before the texture save started.")
        role_keys = item.get("texture_keys")
        if (not isinstance(role_keys, dict)
                or set(role_keys) != set(TEXTURE_USAGE_ROLES)):
            raise TextureSaveError(
                "stale_mesh_state",
                "The model changed before the texture save started.")
        for role in TEXTURE_USAGE_ROLES:
            texture_key = role_keys[role]
            if texture_key is not None and not isinstance(texture_key, str):
                raise TextureSaveError(
                    "stale_mesh_state",
                    "The model changed before the texture save started.")
            if texture_key is not None:
                parsed_role, relative_path = split_texture_key(
                    texture_key, role)
                if parsed_role != role or not relative_path:
                    raise TextureSaveError(
                        "stale_mesh_state",
                        "The model changed before the texture save started.")
        keys.append(key)
        entries.append({
            "semantic_key": key,
            "texture_keys": dict(role_keys),
        })
    expected = set(active_mesh_keys or ())
    if set(keys) != expected or len(keys) != len(expected):
        raise TextureSaveError(
            "stale_mesh_state",
            "The model changed before the texture save started.")
    return tuple(entries)


def resolve_save_request(context, overrides, active_mesh_keys,
                         selected_texture_key, texture_usage):
    entries = validate_usage(active_mesh_keys, texture_usage)
    if not isinstance(selected_texture_key, str):
        raise TextureSaveError(
            "stale_mesh_state",
            "The model changed before the texture save started.")
    selected_path = texture_path(
        context.mod_dir, selected_texture_key, selected=True)
    info = inspect_save_texture(selected_path)
    parsed, draws = resolved_draws(context, overrides)
    selected_identity = physical_identity(selected_path)
    has_active_selected_diffuse = False
    for entry in entries:
        diffuse_path = texture_path(
            context.mod_dir, entry["texture_keys"]["diffuse"])
        if (diffuse_path
                and physical_identity(diffuse_path) == selected_identity):
            has_active_selected_diffuse = True
            break
    if not has_active_selected_diffuse:
        raise TextureSaveError(
            "stale_mesh_state",
            "The model changed before the texture save started.")
    cross_role_usage = []
    cross_role_seen = set()
    for entry in entries:
        for role in TEXTURE_USAGE_ROLES:
            if role == "diffuse":
                continue
            texture_key = entry["texture_keys"][role]
            path = usage_texture_path(context.mod_dir, texture_key, role)
            if path and physical_identity(path) == selected_identity:
                value = (entry["semantic_key"], role)
                if value not in cross_role_seen:
                    cross_role_seen.add(value)
                    cross_role_usage.append(value)

    # The live viewer snapshot only describes active bindings. Parse authored
    # defaults and conditional variants for cross-role ownership too, since an
    # inactive branch can still own the same physical DDS.
    for group in getattr(parsed, "groups", ()):
        for draw in deduplicate_draws(group):
            owned = authored_texture_keys_for_draw(
                draw, context.mod_dir, parsed.game.game)
            for role in TEXTURE_USAGE_ROLES:
                if role == "diffuse":
                    continue
                for texture_key in owned.get(role, ()):
                    path = usage_texture_path(
                        context.mod_dir, texture_key, role)
                    if not path or physical_identity(path) != selected_identity:
                        continue
                    value = (draw.label, role)
                    if value not in cross_role_seen:
                        cross_role_seen.add(value)
                        cross_role_usage.append(value)
    if cross_role_usage:
        uses = []
        for semantic_key, role in cross_role_usage:
            uses.append(
                f"as a {TEXTURE_ROLE_LABELS[role]} by {semantic_key}")
        if len(uses) == 1:
            message = f"This DDS is also used {uses[0]}."
        else:
            message = "This DDS is also used " + ", ".join(uses[:-1])
            message += f", and {uses[-1]}."
        raise TextureSaveError(
            "cross_role_texture_usage", message, "unsupported")
    return entries, selected_path, info, parsed, draws


def affected_texture_keys(context, prepared):
    """Resolve every active usage key for the physically changed source."""
    selected_identity = physical_identity(prepared.selected_path)
    affected = []
    seen_keys = set()
    for entry in prepared.entries:
        role_keys = entry.get("texture_keys")
        for role in TEXTURE_USAGE_ROLES:
            key = role_keys.get(role)
            path = usage_texture_path(
                context.mod_dir, key, role) if key else None
            if (not key or not path or key in seen_keys
                    or physical_identity(path) != selected_identity):
                continue
            seen_keys.add(key)
            affected.append(key)
    return affected

"""Texture identity, lazy source publication, and draw binding assembly."""

import os

from ..resource_paths import safe_resource_path
from ..textures.pipeline import (
    _begin_texture_cache, _texture_source_uri, encode_texture_data_uri,
    normalize_texture_role, normalize_texture_transform, texture_key,
)


class TextureRegistry:
    """Build-scoped role-aware texture registry."""

    def __init__(self, mod_dir, profile, texture_source=None):
        _begin_texture_cache(mod_dir)
        self.mod_dir = mod_dir
        self.profile = profile
        self.texture_source = texture_source
        self._sources = {}
        self._keys = {}

    def key(self, path, role=None, *, identity=None, transform=None):
        if not path or not os.path.exists(path):
            return None
        role = normalize_texture_role(role)
        if transform is None:
            transform = self.profile.recipe_for(role)
        transform = normalize_texture_transform(transform)
        cache_key = (path, role, transform, identity)
        if cache_key not in self._keys:
            relative_path = identity or os.path.relpath(
                path, self.mod_dir).replace(os.sep, "/")
            self._keys[cache_key] = texture_key(relative_path, role)
        return self._keys[cache_key]

    def ensure(self, path, role=None, *, identity=None):
        role = normalize_texture_role(role)
        transform = self.profile.recipe_for(role)
        key = self.key(path, role, identity=identity, transform=transform)
        if key and key not in self._sources:
            if self.texture_source is None:
                value = encode_texture_data_uri(
                    path, texture_role=role, texture_transform=transform)
            else:
                value = _texture_source_uri(
                    self.texture_source, path, role, transform)
            self._sources[key] = value or ""
        return key

    @property
    def sources(self):
        return {key: value for key, value in self._sources.items() if value}


def build_texture_options(group, registry):
    """Build the lazy diffuse picker pool for one component/group."""
    texture_options = []
    texture_option_keys = set()

    def append_texture_option(key, filename, label, **metadata):
        if not key or key in texture_option_keys:
            return
        texture_option_keys.add(key)
        option = {"tex_key": key, "file": filename, "label": label}
        option.update(metadata)
        texture_options.append(option)

    for pool_entry in group.get("diffuse_pool_files") or []:
        path = safe_resource_path(registry.mod_dir, pool_entry["file"])
        key = registry.key(path)
        if key:
            res_name = pool_entry["res"]
            label = res_name[8:] if res_name.startswith("Resource") else res_name
            append_texture_option(key, pool_entry["file"], label)

    for candidate in group.get("discovered_textures") or []:
        filename = candidate.get("file")
        path = safe_resource_path(registry.mod_dir, filename)
        if path is None:
            continue
        key = registry.key(path)
        label = os.path.splitext(
            str(filename).replace("\\", "/").rsplit("/", 1)[-1]
        )[0]
        append_texture_option(
            key, filename, label, candidate_source=candidate.get("source"))
    return texture_options


def apply_draw_texture_bindings(entry, draw, texture_options, *, registry):
    """Apply default and conditional role-aware texture bindings to an entry."""
    profile = registry.profile
    mod_dir = registry.mod_dir

    asset_default = draw.asset_texture_defaults.get("diffuse") or {}
    default_key = registry.ensure(
        asset_default.get("path") or safe_resource_path(
            mod_dir, draw.texture_default("diffuse")),
        "diffuse", identity=asset_default.get("key"))
    entry["tex_key"] = default_key
    entry["normal_map_y_sign"] = profile.normal_y_sign
    entry["normal_map_enabled"] = profile.bind_normal_map

    # NormalMap is a user-facing authored role, but its transport is
    # profile-owned. WuWa publishes the intact packed source as normal_data;
    # Genshin/ZZZ retain the derived normal_map path.
    asset_normal = draw.asset_texture_defaults.get("normal_map") or {}
    normal_path = asset_normal.get("path") or safe_resource_path(
        mod_dir, draw.texture_default("normal_map"))
    normal_role = profile.normal_transport_role
    normal_key = registry.ensure(
        normal_path, normal_role, identity=asset_normal.get("key"))
    if normal_key:
        entry[f"{normal_role}_key"] = normal_key
    for channel in ("light_map", "material_map"):
        asset_default = draw.asset_texture_defaults.get(channel) or {}
        key = registry.ensure(
            asset_default.get("path") or safe_resource_path(
                mod_dir, draw.texture_default(channel)),
            channel, identity=asset_default.get("key"))
        if key:
            entry[f"{channel}_key"] = key

    # The manager presents one row per diffuse. Seed that row with auxiliary
    # maps resolved alongside this draw so authored maps can be inspected or
    # replaced with the same controls as manual ones.
    def seed_option_maps(diffuse_key):
        if not diffuse_key:
            return
        option = next((item for item in texture_options
                       if item["tex_key"] == diffuse_key), None)
        if option:
            for channel in ("normal_map", "light_map", "normal_data",
                            "material_map"):
                key = entry.get(f"{channel}_key")
                if key and not option.get(channel):
                    option[channel] = key

    seed_option_maps(default_key)
    texture_rules = draw.texture_rules("diffuse")
    if texture_rules:
        variants = []
        for variant in texture_rules:
            key = registry.ensure(
                safe_resource_path(mod_dir, variant["file"]))
            if key:
                # Auxiliary assignments after a conditional diffuse branch
                # belong to every branch reaching this draw.
                seed_option_maps(key)
                variants.append({
                    "conditions": variant["conditions"], "tex_key": key,
                })
        if len(variants) > 1:
            entry["texture_variants"] = variants

    for channel in ("light_map", "material_map"):
        rules = draw.texture_rules(channel)
        variants = []
        for variant in rules:
            key = registry.ensure(
                safe_resource_path(mod_dir, variant["file"]), channel)
            if key:
                variants.append({
                    "conditions": variant["conditions"], "tex_key": key,
                })
        if variants:
            entry[f"{channel}_variants"] = variants

    normal_variants = []
    for variant in draw.texture_rules("normal_map"):
        key = registry.ensure(
            safe_resource_path(mod_dir, variant["file"]), normal_role)
        if key:
            normal_variants.append({
                "conditions": variant["conditions"], "tex_key": key,
            })
    if normal_variants:
        entry[f"{normal_role}_variants"] = normal_variants


__all__ = [
    "TextureRegistry", "build_texture_options", "apply_draw_texture_bindings",
]

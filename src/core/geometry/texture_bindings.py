"""Texture identity, lazy source publication, and draw binding assembly."""

import os

from .draw_call import DrawCall
from ..resource_paths import safe_resource_path
from ..textures.pipeline import (
    normalize_texture_role, texture_key,
)


class TextureRegistry:
    """Build-scoped role-aware texture registry."""

    def __init__(self, mod_dir, profile, texture_source=None, source=None):
        self.mod_dir = mod_dir
        self.profile = profile
        self.texture_source = texture_source
        self.source = source
        self._sources = {}
        self._keys = {}
        self._paths = {}

    def key(self, path, role=None, *, identity=None):
        if not path:
            return None
        role = normalize_texture_role(role)
        cache_key = (path, role, identity)
        if cache_key in self._keys:
            return self._keys[cache_key]
        exists = (self.source.is_file(path)
                  if self.source is not None
                  and self.source.is_resource_reference(path)
                  else os.path.exists(path))
        if not exists:
            return None
        relative_path = identity or (
            self.source.logical_path(path)
            if self.source is not None
            and self.source.is_resource_reference(path)
            else os.path.relpath(path, self.mod_dir).replace(os.sep, "/"))
        self._keys[cache_key] = texture_key(relative_path, role)
        return self._keys[cache_key]

    def ensure(self, path, role=None, *, identity=None):
        role = normalize_texture_role(role)
        key = self.key(path, role, identity=identity)
        if key and key not in self._sources:
            value = self.texture_source(path, role) if self.texture_source else None
            self._sources[key] = value or ""
        return key

    @property
    def sources(self):
        return {key: value for key, value in self._sources.items() if value}

    def resolve(self, filename):
        if isinstance(filename, str) and filename in self._paths:
            return self._paths[filename]
        path = (self.source.resolve_resource(filename)
                if self.source is not None
                else safe_resource_path(self.mod_dir, filename))
        if isinstance(filename, str):
            self._paths[filename] = path
        return path


def finalize_texture_candidates(group, *, draws=None):
    """Fold final draw defaults and alternatives into the component's one pool."""
    candidates = {}

    def add(candidate):
        filename = candidate.get("identity") or candidate.get("file")
        if not filename:
            return None
        identity = os.path.normpath(filename.replace("\\", "/")).replace("\\", "/")
        return candidates.setdefault(os.path.normcase(identity), dict(candidate))

    for candidate in group.get("texture_candidates", ()):
        add({key: value for key, value in candidate.items() if key != "maps"})
    for raw_draw in draws if draws is not None else group.get("draws", ()):
        draw = DrawCall.from_mapping(raw_draw, group)
        maps = {}
        for role in ("normal_map", "light_map", "material_map", "emission_map"):
            asset = draw.asset_texture_defaults.get(role) or {}
            filename = asset.get("key") or draw.texture_default(role)
            if filename:
                maps[role] = {"file": filename, "path": asset.get("path"),
                              "identity": asset.get("key")}
        for role in draw._TEXTURE_PREFIX:
            asset = draw.asset_texture_defaults.get(role) or {}
            defaults = [{"file": asset.get("key") or draw.texture_default(role),
                         "path": asset.get("path"), "identity": asset.get("key"),
                         "source": "asset" if asset else "mod"}]
            defaults.extend({"file": rule.get("file"), "source": "mod"}
                            for rule in draw.texture_rules(role))
            for candidate in defaults:
                option = add(candidate)
                if option is not None and role == "diffuse":
                    for channel, value in maps.items():
                        option.setdefault("maps", {}).setdefault(channel, value)
    group["texture_candidates"] = list(candidates.values())


def build_texture_options(group, registry):
    """Publish picker identities from the single finalized component pool."""
    texture_options = []
    seen = set()
    for candidate in group.get("texture_candidates", ()):
        filename = candidate.get("file")
        identity = candidate.get("identity")
        path = candidate.get("path") or registry.resolve(filename)
        if path is None:
            continue
        # External candidates must publish their source before hydration,
        # which resolves ordinary candidate keys relative to the mod.
        external = candidate.get("source") == "asset"
        key = (registry.ensure(path, identity=identity) if external
               else registry.key(path))
        if not key or key in seen:
            continue
        seen.add(key)
        res_name = candidate.get("res") or ""
        label = candidate.get("label") or os.path.splitext(
            str(filename).replace("\\", "/").rsplit("/", 1)[-1]
        )[0]
        if res_name:
            label = res_name.removeprefix("Resource")
        if external and not label.endswith(" (Asset)"):
            label += " (Asset)"
        option = {"tex_key": key, "file": filename, "label": label,
                  "candidate_source": candidate.get("source", "mod")}
        for role, value in candidate.get("maps", {}).items():
            transport_role = (registry.profile.normal_transport_role
                              if role == "normal_map" else role)
            map_path = value.get("path") or registry.resolve(value["file"])
            map_key = registry.key(map_path, transport_role, identity=value.get("identity"))
            if map_key:
                option[transport_role] = map_key
        texture_options.append(option)
    return texture_options


def apply_draw_texture_bindings(entry, draw, *, registry):
    """Apply default and conditional role-aware texture bindings to an entry."""
    profile = registry.profile

    asset_default = draw.asset_texture_defaults.get("diffuse") or {}
    default_key = registry.ensure(
        asset_default.get("path") or registry.resolve(
            draw.texture_default("diffuse")),
        "diffuse", identity=asset_default.get("key"))
    entry["tex_key"] = default_key
    entry["normal_map_y_sign"] = profile.normal_y_sign
    entry["normal_map_enabled"] = profile.bind_normal_map

    # NormalMap is a user-facing authored role, but its transport is
    # profile-owned. Genshin/ZZZ keep the authored source under normal_map;
    # WuWa exposes it as normal_data.
    asset_normal = draw.asset_texture_defaults.get("normal_map") or {}
    normal_path = asset_normal.get("path") or registry.resolve(
        draw.texture_default("normal_map"))
    normal_role = profile.normal_transport_role
    normal_key = registry.ensure(
        normal_path, normal_role, identity=asset_normal.get("key"))
    if normal_key:
        entry[f"{normal_role}_key"] = normal_key
    for channel in ("light_map", "material_map", "emission_map"):
        asset_default = draw.asset_texture_defaults.get(channel) or {}
        key = registry.ensure(
            asset_default.get("path") or registry.resolve(
                draw.texture_default(channel)),
            channel, identity=asset_default.get("key"))
        if key:
            entry[f"{channel}_key"] = key

    texture_rules = draw.texture_rules("diffuse")
    if texture_rules:
        variants = []
        for variant in texture_rules:
            key = registry.ensure(
                registry.resolve(variant["file"]))
            if key:
                variants.append({
                    "conditions": variant["conditions"], "tex_key": key,
                })
        if len(variants) > 1 or (variants and variants[0]["conditions"]):
            entry["texture_variants"] = variants

    for channel in ("light_map", "material_map", "emission_map"):
        rules = draw.texture_rules(channel)
        variants = []
        for variant in rules:
            key = registry.ensure(
                registry.resolve(variant["file"]), channel)
            if key:
                variants.append({
                    "conditions": variant["conditions"], "tex_key": key,
                })
        if variants:
            entry[f"{channel}_variants"] = variants

    normal_variants = []
    for variant in draw.texture_rules("normal_map"):
        key = registry.ensure(
            registry.resolve(variant["file"]), normal_role)
        if key:
            normal_variants.append({
                "conditions": variant["conditions"], "tex_key": key,
            })
    if normal_variants:
        entry[f"{normal_role}_variants"] = normal_variants


__all__ = [
    "TextureRegistry", "build_texture_options", "apply_draw_texture_bindings",
    "finalize_texture_candidates",
]

"""Geometry-free draw projection used by staged semantic refreshes."""

import os

from .draw_call import DrawCall
from .identity import make_mesh_identity
from ..resource_paths import safe_resource_path
from ..textures.pipeline import normalize_texture_role, texture_key


_MAX_DRAWS = 10_000


def _rel_source(src, mod_dir):
    """Project absolute INI provenance to a browser-safe relative path."""
    path = src.get("ini_path")
    if path:
        try:
            path = os.path.relpath(path, mod_dir).replace(os.sep, "/")
        except ValueError:
            path = os.path.basename(path)
    return {"ini": path, "line": src.get("line_no"), "section": src.get("section")}


def deduplicate_draws(group, max_draws=0):
    """Merge repeated draw regions while preserving every gate and source."""
    merged = {}
    order = []
    for raw_draw in group["draws"]:
        draw = DrawCall.from_mapping(raw_draw, group)
        key = draw.render_identity()
        if key not in merged:
            merged[key] = {"draw": draw, "alts": [], "sources": []}
            order.append(key)
        entry = merged[key]
        for source in draw.sources:
            if source not in entry["sources"]:
                entry["sources"].append(source)
        cond_groups = draw.conditions
        if not cond_groups:
            if [] not in entry["alts"]:
                entry["alts"].append([])
        else:
            for condition_group in cond_groups:
                if condition_group not in entry["alts"]:
                    entry["alts"].append(condition_group)

    unique = []
    for key in order:
        entry = merged[key]
        draw, alternatives = entry["draw"], entry["alts"]
        draw.conditions = (
            [] if any(not group for group in alternatives) else alternatives)
        draw.sources = entry["sources"]
        unique.append(draw)
    return unique[:max_draws] if max_draws else unique


# Existing integration fixtures import this private helper from mesh_builder.
_deduplicate_draws = deduplicate_draws


def _semantic_texture_key(mod_dir, authored_path, role):
    """Resolve a texture identity without opening or decoding the source."""
    path = safe_resource_path(mod_dir, authored_path)
    if not path or not os.path.exists(path):
        return None
    relative_path = os.path.relpath(path, mod_dir).replace(os.sep, "/")
    return texture_key(relative_path, normalize_texture_role(role))


def _semantic_asset_key(draw, role, transport_role=None):
    item = draw.asset_texture_defaults.get(role) or {}
    key = item.get("key") if isinstance(item, dict) else None
    if not key:
        return None
    if transport_role and transport_role != role:
        return texture_key(key, transport_role)
    return texture_key(key, role)


def _semantic_texture_variants(draw, mod_dir, authored_role, transport_role=None):
    role = transport_role or authored_role
    variants = []
    for variant in draw.texture_rules(authored_role):
        key = _semantic_texture_key(mod_dir, variant.get("file"), role)
        if key:
            variants.append({"conditions": variant["conditions"], "tex_key": key})
    return variants


def validate_draw_count(groups):
    draw_total = sum(len(group.get("draws", [])) for group in groups)
    if draw_total > _MAX_DRAWS:
        raise ValueError(
            f"Mod has too many draws ({draw_total:,}; limit {_MAX_DRAWS:,}).")


def build_mesh_semantics(groups, mod_dir, max_draws=0, game_profile=None,
                         active_mesh_keys=None):
    """Return draw visibility semantics without resolving any geometry."""
    validate_draw_count(groups)
    from ..textures.profiles import texture_profile_for

    texture_profile = texture_profile_for(game_profile)
    normal_role = texture_profile.normal_transport_role
    result = {}
    for group in groups:
        for draw in deduplicate_draws(group, max_draws=max_draws):
            if active_mesh_keys is not None and draw.label not in active_mesh_keys:
                continue
            entry = {
                "conditions": draw.conditions or [],
                "tex_key": (_semantic_asset_key(draw, "diffuse") or
                            _semantic_texture_key(
                                mod_dir, draw.texture_default("diffuse"),
                                "diffuse")),
                "normal_map_key": None,
                "normal_data_key": None,
                "light_map_key": (_semantic_asset_key(draw, "light_map") or
                                  _semantic_texture_key(
                                      mod_dir, draw.texture_default("light_map"),
                                      "light_map")),
                "material_map_key": (_semantic_asset_key(
                    draw, "material_map") or _semantic_texture_key(
                        mod_dir, draw.texture_default("material_map"),
                        "material_map")),
                "emission_map_key": (_semantic_asset_key(
                    draw, "emission_map") or _semantic_texture_key(
                        mod_dir, draw.texture_default("emission_map"),
                        "emission_map")),
            }
            source = group.get("source")
            identity_source = group.get("identity_source") or source
            component = group.get("display_name") or group.get("name")
            if source:
                entry["source"] = source
            if component:
                entry["component"] = component
            entry["identity"] = make_mesh_identity(
                draw, source=identity_source, component=component).to_dict()
            normal_key = _semantic_texture_key(
                mod_dir, draw.texture_default("normal_map"), normal_role)
            normal_key = (_semantic_asset_key(
                draw, "normal_map", normal_role) or normal_key)
            entry[f"{normal_role}_key"] = normal_key
            if draw.sources:
                entry["sources"] = [_rel_source(source, mod_dir)
                                     for source in draw.sources]

            texture_variants = _semantic_texture_variants(
                draw, mod_dir, "diffuse")
            if len(texture_variants) > 1:
                entry["texture_variants"] = texture_variants
            for channel in ("light_map", "material_map", "emission_map"):
                variants = _semantic_texture_variants(draw, mod_dir, channel)
                if variants:
                    entry[f"{channel}_variants"] = variants
            normal_variants = _semantic_texture_variants(
                draw, mod_dir, "normal_map", normal_role)
            if normal_variants:
                entry[f"{normal_role}_variants"] = normal_variants
            binding = draw.asset_binding
            if binding is not None and hasattr(binding, "to_dict"):
                entry["asset_binding"] = binding.to_dict()
            if draw.texture_provenance:
                entry["texture_resolution"] = dict(draw.texture_provenance)
            if draw.asset_slot_evidence:
                entry["asset_slot_evidence"] = list(draw.asset_slot_evidence)
            result[draw.label] = entry
    return result


__all__ = [
    "build_mesh_semantics", "deduplicate_draws", "validate_draw_count",
    "_deduplicate_draws", "_rel_source",
]

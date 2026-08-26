"""Apply application-level Asset and material semantics to parsed mods."""

from core.materials.kind import detect_material_kind
from core.materials.profiles import material_profile_for

from app.assets import enrichment as asset_enrichment
from app.assets import resolver as asset_resolver
from app.assets import wuwa_texture_fallback


def enrich_mod_analysis(parsed, context):
    """Resolve Asset/material texture semantics shared by both load paths."""
    availability = {}
    bindings = asset_resolver.resolve_groups(
        parsed.groups, parsed.game, context.asset_folders,
        availability=availability)
    complete_index = (
        availability.get("configured_roots", 0) > 0
        and availability.get("ready_roots", 0)
        == availability.get("configured_roots", 0))
    _apply_texture_enrichment(parsed, context, bindings, complete_index)
    asset_resolution = asset_resolver.summarize_groups(
        parsed.groups, bindings, availability).to_dict()
    return bindings, asset_resolution


def _register_material_profile(table, profile):
    """Register one immutable profile, rejecting same-ID contradictions."""
    metadata = profile.to_metadata()
    existing = table.get(profile.id)
    if existing is not None and existing != metadata:
        raise RuntimeError(f"Material profile ID collision: {profile.id}")
    table[profile.id] = metadata


def _apply_texture_enrichment(parsed, context, bindings, complete_index):
    """Run all semantic texture enrichment in the shared load order."""
    asset_enrichment.apply(
        parsed.groups, bindings, include_not_found=complete_index,
        mod_dir=context.mod_dir,
        dds_classification_cache=context.dds_classification_cache)
    if str(getattr(parsed.game, "game", "")).casefold() == "wuwa":
        wuwa_texture_fallback.apply(
            parsed.groups, context.mod_dir)


def _assign_material_profiles(meshes, game):
    """Attach per-mesh kind/profile identity and return a shared profile table."""
    profiles = {}
    for entry in (meshes or {}).values():
        if not isinstance(entry, dict):
            continue
        detection = detect_material_kind(entry)
        selected_kind = detection.kind if detection.reliable else "unknown"
        profile = material_profile_for(game, material_kind=selected_kind)
        entry["material_kind"] = detection.kind
        entry["material_kind_reliable"] = detection.reliable
        entry["material_kind_reason"] = detection.reason
        entry["material_profile_id"] = profile.id
        _register_material_profile(profiles, profile)
    return profiles

"""Orchestrate the application-facing mod loading pipeline.

The public payload keeps meshes, textures, controls, state, geometry and
metadata in separate named fields. This module coordinates the focused
analysis, control, enrichment and mesh-building stages without owning their
implementation details.
"""

import traceback
from dataclasses import dataclass, field, replace

from core.geometry.mesh_builder import build_mesh_result, build_mesh_semantics
from core.ini.health import analyze_mod
from core.materials.profiles import material_profile_for
from core.mod_discovery import discover_ini_paths
from core.mod_source import (
    ModSourceError, mod_source_for_path,
)

from app.mods.analysis import (ParsedModAnalysis, analyze_mod_inis,
                               build_mod_ini_snapshot)
from core.ini.snapshot import ModIniSnapshot
from app.mods.controls import (
    _control_semantic_projection,
    _gating_vars,
    _gating_vars_from_mesh_semantics,
    build_menu_panel,
    build_toggle_panel,
    load_control_state,
    load_present_state,
    unwired_pending_sections,
)
from app.mods.enrichment import (
    _assign_material_profiles,
    _register_material_profile,
    enrich_mod_analysis,
)


# Kept for scripts that still inspect the low-level mesh-builder result. These
# keys are no longer emitted by load_mod's public application payload.
RESERVED_KEYS = ("__textures__", "__toggles__", "__menu__", "__mesh_names__",
                 "__geometry__", "__state_rules__", "__state_defaults__",
                 "__health__", "__present__")

# Kept for callers that imported the old helper from this module. The loader
# itself only consumes an explicit path list or ModLoadContext.
find_inis = discover_ini_paths


@dataclass
class ModLoadContext:
    """Inputs shared by one open/reload of a mod."""

    mod_dir: str
    ini: ModIniSnapshot
    metadata: dict = field(default_factory=dict)
    asset_folders: list = field(default_factory=list)
    dds_classification_cache: dict = field(default_factory=dict)
    skinning_manifest: dict = field(default_factory=dict)
    buffer_overrides: dict = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.ini, ModIniSnapshot):
            raise TypeError("ModLoadContext requires a ModIniSnapshot")

    @property
    def source(self):
        return self.ini.source

    @property
    def ini_paths(self):
        return [record.path for record in self.ini.records]

    @property
    def docs(self):
        return {record.path: record.document for record in self.ini.records}


def _resolve_context(folder_path, ini_paths=None, documents=None, context=None,
                     overrides=None):
    if context is not None:
        if overrides:
            snapshot = build_mod_ini_snapshot(
                context.ini_paths, context.mod_dir, context.docs,
                overrides, context.source)
            return replace(context, ini=snapshot)
        return context
    if folder_path is None:
        raise ValueError("folder_path is required")
    if ini_paths is None:
        # Direct callers retain the convenience form. The application passes
        # an explicit context so the normal open path never rediscovers INIs.
        source = mod_source_for_path(folder_path)
        ini_paths = find_inis(folder_path, source=source)
    else:
        source = (getattr(ini_paths[0], "source", None)
                  if ini_paths else None) or mod_source_for_path(folder_path)
    if getattr(source, "virtual", False):
        ini_paths = [source.document_path(source.logical_path(path))
                     for path in ini_paths]
        documents = {source.document_path(source.logical_path(path)): document
                     for path, document in (documents or {}).items()}
        overrides = {source.document_path(source.logical_path(path)): text
                     for path, text in (overrides or {}).items()}
    snapshot = build_mod_ini_snapshot(
        ini_paths, folder_path, documents, overrides, source)
    return ModLoadContext(folder_path, snapshot)


def _failure_health(context):
    """Best-effort diagnostics reserved for a failed model load."""
    try:
        return analyze_mod(
            context.mod_dir, ini_paths=context.ini_paths,
            documents=context.docs, source=context.source)
    except Exception:
        traceback.print_exc()
        return {
            "summary": {"errors": 0, "warnings": 1, "issues": 1,
                        "unused_files": 0, "unused_resources": 0},
            "files": {"unreferenced": 0, "inactive_only": 0,
                      "viewer_only": 0, "referenced": 0},
            "issues": [{
                "code": "health_check_failed", "severity": "warning",
                "category": "ini",
                "message": "The INI diagnostics could not be completed.",
            }],
        }


def _structured_payload(meshes=None, textures=None, toggles=None, menu=None,
                        present=None, state_rules=None, state_defaults=None,
                        health=None, error=None, game=None,
                        material_profiles=None, asset_resolution=None,
                        source=None, animations=None):
    """Create the stable application-to-frontend payload shape."""
    profile_table = dict(material_profiles or {})
    if game is not None:
        _register_material_profile(profile_table, material_profile_for(game))
    payload = {
        "meshes": meshes or {},
        "textures": textures or {},
        "texture_pools": {},
        "controls": {
            "toggles": toggles or {},
            "menu": menu or {},
            "present": present or {"target_inis": [], "item": None},
        },
        "state": {
            "rules": state_rules or [],
            "defaults": state_defaults or {},
        },
        "geometry": None,
        "metadata": {
            "mesh_names": {},
            "mesh_color_adjustments": {},
            "material_profiles": profile_table,
        },
        "health": health,
        "asset_resolution": asset_resolution,
    }
    if game is not None:
        payload["metadata"]["game"] = game.to_metadata()
    if animations:
        payload["animations"] = animations
    if source is not None:
        payload["metadata"].update({
            "source_kind": source.kind,
            "source_read_only": bool(source.read_only),
        })
    if error:
        payload["error"] = error
    return payload


def _mesh_semantic_projection(parsed, context, active_mesh_keys=None):
    """Build the mesh projection shared by narrow and combined reads."""
    _bindings, asset_resolution = enrich_mod_analysis(parsed, context)
    mesh_payload = build_mesh_semantics(
        parsed.groups, context.mod_dir, game_profile=parsed.game.game,
        active_mesh_keys=active_mesh_keys, source=context.source)
    # Keep semantic refresh on the same authoritative material-resolution
    # chain as a full model load. Viewer-only choices affect evidence here but
    # never edit the source INI.
    from .metadata import hydrate_component_material_kinds
    hydrate_component_material_kinds(mesh_payload, context.metadata)
    material_profiles = _assign_material_profiles(mesh_payload, parsed.game)
    return mesh_payload, material_profiles, asset_resolution


def load_mesh_semantics(context, active_mesh_keys=None):
    """Read draw and material semantics without building geometry."""
    parsed = analyze_mod_inis(context.ini)
    mesh_payload, material_profiles, asset_resolution = \
        _mesh_semantic_projection(parsed, context, active_mesh_keys)
    return {
        "meshes": mesh_payload,
        "material_profiles": material_profiles,
        "asset_resolution": asset_resolution,
    }


def load_semantic_state(context, pending_new_sections=None,
                        active_mesh_keys=None, *, menu_image_source=None):
    """Read mesh and control projections from one authoritative analysis."""
    parsed = analyze_mod_inis(context.ini)
    mesh_payload, material_profiles, asset_resolution = \
        _mesh_semantic_projection(parsed, context, active_mesh_keys)
    gating_vars = _gating_vars_from_mesh_semantics(
        mesh_payload, active_mesh_keys)
    return {
        "meshes": mesh_payload,
        "material_profiles": material_profiles,
        "asset_resolution": asset_resolution,
        **_control_semantic_projection(
            parsed, context, pending_new_sections, gating_vars=gating_vars,
            menu_image_source=menu_image_source),
    }


def load_mod(folder_path=None, overrides=None, pending_new_sections=None, *,
             ini_paths=None, documents=None, geometry=None, context=None,
             texture_source=None, menu_image_source=None):
    """Parse a mod folder and return the structured application payload.

    Errors are returned as ``{"error": ...}`` rather than raised, since this
    function is called across the JS bridge where an exception is opaque.
    """
    try:
        context = _resolve_context(
            folder_path, ini_paths=ini_paths, documents=documents,
            context=context, overrides=overrides)
    except ModSourceError as error:
        return _structured_payload(error=str(error))
    context.skinning_manifest = {}
    if not context.ini_paths:
        health = _failure_health(context)
        return _structured_payload(
            health=health,
            error="No active .ini files found in this folder.",
            source=context.source)

    try:
        parsed = analyze_mod_inis(context.ini)
        if not parsed.groups:
            health = _failure_health(context)
            return _structured_payload(
                health=health,
                error=(f"No mesh geometry found across "
                       f"{len(context.ini_paths)} ini file(s)."),
                game=parsed.game, source=context.source)

        _bindings, asset_resolution = enrich_mod_analysis(parsed, context)
        built = build_mesh_result(
            parsed.groups, context.mod_dir, geometry=geometry,
            texture_source=texture_source,
            game_profile=parsed.game.game, source=context.source,
            animations=parsed.animations,
            buffer_overrides=getattr(context, "buffer_overrides", None))
        mesh_payload = built.meshes
        if not mesh_payload:
            context.skinning_manifest = {}
            health = _failure_health(context)
            return _structured_payload(
                health=health,
                error="No mesh data could be extracted (buffer files missing?).",
                game=parsed.game, source=context.source)

        context.skinning_manifest = getattr(
            built, "skinning_manifest", None) or {}

        # Viewer-only material choices are hydrated into the same evidence
        # field used by the classifier. This never edits the source INI.
        from .metadata import hydrate_component_material_kinds
        hydrate_component_material_kinds(mesh_payload, context.metadata)
        material_profiles = _assign_material_profiles(mesh_payload, parsed.game)
        toggles = build_toggle_panel(
            parsed.toggles, parsed.defaults,
            _gating_vars(mesh_payload)
            | set(getattr(parsed, "animation_control_vars", ()) or ()),
            context.mod_dir, pending_new_sections,
            state_rules=parsed.state_rules)
        menu = build_menu_panel(
            parsed.menu, parsed.defaults, context.mod_dir,
            source=context.source, image_source=menu_image_source)
        return _structured_payload(
            meshes=mesh_payload, textures=built.textures, toggles=toggles,
            menu=menu, present=parsed.present,
            state_rules=parsed.state_rules, state_defaults=parsed.defaults,
            game=parsed.game, material_profiles=material_profiles,
            asset_resolution=asset_resolution, source=context.source,
            animations=getattr(built, "animations", None))
    except ModSourceError as error:
        context.skinning_manifest = {}
        return _structured_payload(
            health=_failure_health(context),
            error=str(error), source=context.source)
    except Exception:
        context.skinning_manifest = {}
        traceback.print_exc()
        health = _failure_health(context)
        return _structured_payload(
            health=health,
            error="Unexpected backend error. See the application log for details.",
            source=context.source)

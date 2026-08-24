"""Turn a 3DMigoto mod folder into the structured payload the UI renders.

Deliberately free of any GUI imports so it can be exercised from tests and
scripts without pywebview or a display.

The public payload keeps meshes, textures, controls, state, geometry and
metadata in separate named fields.  ``build_mesh_payload`` still exposes its
older flat builder result for direct core fixtures; this module is the
application boundary that turns it into the public schema.
"""

import os
import traceback
from dataclasses import dataclass, field

from core.ini_condition import is_namespaced
from core.ini_sections import extract_resources, merge_sections
from core.ini_analysis import analyze_ini
from core.mod_discovery import discover_ini_paths
from core.mesh_builder import build_mesh_result, build_mesh_semantics
from core.resource_paths import safe_resource_path
from core.textures import encode_texture_data_uri
from core.ini_menu import attach_menu_images
from core.ini_health import analyze_mod
from core.present_editor import SECTION_NAME as PRESENT_SECTION
from core.game_profile import GameDetection, resolve_game_detection
from core.material_kind import detect_material_kind
from core.material_profiles import material_profile_for

from . import asset_enrichment, asset_resolver, wuwa_texture_fallback

# Kept for scripts that still inspect the low-level mesh-builder result.  These
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
    ini_paths: list[str]
    docs: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    asset_folders: list = field(default_factory=list)
    dds_classification_cache: dict = field(default_factory=dict)


@dataclass
class ParsedModAnalysis:
    """Named result of the shared per-INI semantic analysis pass."""

    groups: list
    toggles: dict
    menu: dict
    defaults: dict
    state_rules: list
    present: dict
    game: GameDetection

    def __iter__(self):
        """Keep old six-value helper callers source-compatible."""
        yield self.groups
        yield self.toggles
        yield self.menu
        yield self.defaults
        yield self.state_rules
        yield self.present


def _attach_shape_sliders(groups, shape_sliders):
    """Attach morphs to groups that use their base buffer on any draw.

    Some generated mods switch `ib`/`vb0` halfway through one override, so a
    group can contain draws backed by several position buffers. mesh_builder
    performs the final per-draw filter; it needs every matching morph here.
    """
    def path_key(path):
        return os.path.normcase(os.path.normpath(path)) if path else None

    for group in groups:
        position_files = {path_key(group.get("position_file"))}
        position_files.update(path_key(draw.get("position_file"))
                              for draw in group.get("draws", []))
        position_files.discard(None)
        matches = [slider for slider in shape_sliders
                   if path_key(slider.get("base_file")) in position_files]
        if matches:
            group["shape_sliders"] = matches


def _ini_scope(ini_path, folder_path, multi):
    """Namespace an ini's variables so sibling inis can't collide.

    Multiple inis in one folder (an "AllInOne" mod) routinely reuse the same
    [Key...] section and variable names for different things. Prefixing by
    subfolder (or filename) keeps their toggle vars and conditions apart.

    Returns (var_prefix, source); both None for a single-ini folder. `source`
    is the bare tag the UI groups same-named keys under.
    """
    if not multi:
        return None, None
    parent_dir = os.path.dirname(ini_path)
    if os.path.normpath(parent_dir) != os.path.normpath(folder_path):
        source = os.path.relpath(parent_dir, folder_path).replace(os.sep, "/")
    else:
        source = os.path.splitext(os.path.basename(ini_path))[0]
    # `source` is deliberately a compact UI grouping label, so all INIs in a
    # nested component folder share it.  It cannot also be the parser identity:
    # sibling files commonly reuse [Key...] names and plain variables.
    identity = os.path.splitext(_ini_rel(ini_path, folder_path))[0]
    return f"{identity}::", source


def _ini_rel(ini_path, folder_path):
    return os.path.relpath(ini_path, folder_path).replace(os.sep, "/")


def _rebase_resources(resources, ini_path, folder_path):
    """Make filenames authored relative to a nested INI relative to the root."""
    rel_dir = os.path.relpath(os.path.dirname(ini_path), folder_path)
    if rel_dir == os.curdir:
        return resources
    for info in resources.values():
        filename = info.get("filename")
        if filename:
            info["filename"] = os.path.normpath(os.path.join(rel_dir, filename))
    return resources


def _parse_inis(ini_paths, folder_path, overrides=None, documents=None):
    """Parse each ini independently, so one mod's resource definitions never
    overwrite another's. Returns (draw groups, toggle keys, menu slots,
    variable defaults).

    `overrides`, if given, is {ini_path: text} — see merge_sections; used to
    preview pending, not-yet-exported edits instead of stale on-disk content.
    """
    groups = []
    toggle_keys, menu_slots, toggle_defaults, state_rules = {}, {}, {}, []
    present_infos, present_sources = [], []
    game_evidence = []
    runtime_evidence = []
    texture_api_evidence = []
    multi = len(ini_paths) > 1

    # Shared across every ini (not reset per file): two sibling inis reusing
    # the same generic component name get disambiguated ("Component0_2")
    # instead of one silently overwriting the other's mesh entry.
    seen_labels = {}

    for ini_path in ini_paths:
        secs = merge_sections([ini_path], overrides=overrides,
                              documents=documents)
        var_prefix, source = _ini_scope(ini_path, folder_path, multi)

        resources = _rebase_resources(
            extract_resources(secs), ini_path, folder_path)
        analysis = analyze_ini(
            secs, resources=resources, var_prefix=var_prefix, source=source,
            seen=seen_labels)
        ini_groups = analysis.draw_groups
        shape_sliders = analysis.shapes
        state_rules.extend(analysis.state_rules)
        game_evidence.extend(analysis.game_evidence)
        runtime_evidence.extend(analysis.runtime_evidence)
        texture_api_evidence.extend(analysis.texture_api_evidence)
        _attach_shape_sliders(ini_groups, shape_sliders)
        groups.extend(ini_groups)
        ini_toggles = analysis.toggles
        ini_menu = analysis.menu
        own_menu = dict(ini_menu)
        ini_present = None
        for key, info in ini_toggles.items():
            if info.get("section", "").lower() == PRESENT_SECTION.lower():
                ini_present = info
                present_infos.append(info)
                continue
            toggle_keys[key] = info
        menu_slots.update(ini_menu)
        has_controls = (any(info.get("section", "").lower() != PRESENT_SECTION.lower()
                            for info in ini_toggles.values()) or bool(ini_menu)
                        or bool(shape_sliders))
        capture_vars = []
        if has_controls:
            rel = _ini_rel(ini_path, folder_path)
            for info in ini_toggles.values():
                if info.get("section", "").lower() == PRESENT_SECTION.lower():
                    continue
                capture_vars.extend(info.get("vars", {}))
            capture_vars.extend(info.get("var") for info in ini_menu.values())
            capture_vars.extend(info.get("var") for info in shape_sliders)
            capture_vars = list(dict.fromkeys(var for var in capture_vars if var))
            present_sources.append({
                "value": rel, "label": rel, "vars": capture_vars,
                "has_present": ini_present is not None,
            })
            if ini_present is not None:
                ini_present["capture_vars"] = capture_vars
        seen_slider_vars = set()
        for index, slider in enumerate(shape_sliders, 1):
            if slider["var"].lower() in seen_slider_vars:
                continue
            seen_slider_vars.add(slider["var"].lower())
            key = f"{var_prefix or ''}{slider['section']}#shape{index}"
            menu_slots[key] = slider
            own_menu[key] = slider
        attach_menu_images(own_menu, secs, resources)
        for var, val in analysis.defaults.items():
            toggle_defaults.setdefault(var, val)

    present_items = []
    for present_info in present_infos:
        variables = [{"var": var, "values": values,
                      "default": toggle_defaults.get(var, values[0])}
                     for var, values in present_info["vars"].items()]
        lengths = {len(var["values"]) for var in variables}
        present_items.append({
            "ini": _ini_rel(present_info["ini_path"], folder_path),
            "source": present_info.get("source"),
            "section": present_info["section"],
            "key": present_info["key_display"] or present_info["key"],
            "key_raw": present_info["key"],
            "back": present_info.get("back", ""),
            "vars": variables,
            "capture_vars": present_info.get("capture_vars", []),
            "count": max((len(var["values"]) for var in variables), default=0),
            "aligned": len(lengths) == 1,
        })
    present_item = None
    if present_items:
        first = present_items[0]
        counts = {item["count"] for item in present_items}
        missing_inis = [source["value"] for source in present_sources
                        if not source["has_present"]]
        aligned = all(item["aligned"] for item in present_items)
        sync_error = None
        if not aligned:
            sync_error = ("A PRESENT key has variable lists with different "
                          "position counts. Edit the INI so its cycle lists align.")
        elif len(counts) != 1:
            sync_error = ("PRESENT keys have different position counts. "
                          "Edit the INIs so their cycle lists align.")
        present_item = {
            "inis": [item["ini"] for item in present_items],
            "target_inis": present_sources,
            "key": first["key"], "key_raw": first["key_raw"],
            "back": first["back"],
            "vars": [var for item in present_items for var in item["vars"]],
            "capture_vars": [var for source in present_sources
                             for var in source["vars"]],
            "count": first["count"] if sync_error is None else 0,
            "missing_inis": missing_inis,
            "sync_error": sync_error,
        }
    present = {"target_inis": present_sources, "item": present_item}
    return ParsedModAnalysis(
        groups=groups,
        toggles=toggle_keys,
        menu=menu_slots,
        defaults=toggle_defaults,
        state_rules=state_rules,
        present=present,
        game=resolve_game_detection(
            game_evidence, runtime_evidence, texture_api_evidence),
    )


def _gating_vars(payload):
    """Variables that actually decide some mesh's visibility OR its texture.

    `conditions`/`texture_variants[].conditions` are each a list of OR'd
    AND-groups: [[{var,value,negate}, ...], ...]. A var that only ever swaps
    a diffuse (e.g. Key$SuitCL) never appears in `conditions`, but must still
    count as "wired" here or build_toggle_panel drops it from the Toggle
    panel entirely even though it visibly does something.
    """
    found = set()
    for entry in payload.values():
        if not isinstance(entry, dict):
            continue
        for group in entry.get("conditions", []):
            for cond in group:
                found.add(cond["var"])
        for field in ("texture_variants", "normal_map_variants",
                      "normal_data_variants", "light_map_variants",
                      "material_map_variants"):
            for variant in entry.get(field, []):
                for group in variant.get("conditions", []):
                    for cond in group:
                        found.add(cond["var"])
    return found


def build_toggle_panel(toggle_keys, toggle_defaults, gating_vars, mod_dir=None,
                       pending_new_sections=None):
    """Model for the Toggle panel, keyed by [Key...] section rather than by
    variable, since one section can cycle several variables at once.

    A section that already gates something visible shows only its gating
    var(s) ("wired": True). A section that gates nothing yet is only shown
    ("wired": False, listing its writable/non-namespaced vars instead) if
    it's in `pending_new_sections` — {ini basename: {section name, ...}}
    from edit_session.new_sections_for() — meaning the user added it THIS
    session and hasn't Recorded it yet, so its Record button still needs to
    be reachable. Any other not-yet-gating section (e.g. a long-standing
    $menu/$skin utility key) is never shown at all: there's no reliable way
    to tell "about to be wired" apart from "will never gate anything".
    """
    pending_new_sections = pending_new_sections or {}
    panel = {}
    for section, info in toggle_keys.items():
        gated = {v: vals for v, vals in info["vars"].items() if v in gating_vars}
        wired = bool(gated)
        if wired:
            shown_vars = gated
        else:
            ini_name = (_ini_rel(info["ini_path"], mod_dir) if mod_dir
                        else os.path.basename(info["ini_path"])) if info.get("ini_path") else None
            is_pending_new = bool(ini_name) and info.get("section") in pending_new_sections.get(ini_name, ())
            if not is_pending_new:
                continue
            shown_vars = {v: vals for v, vals in info["vars"].items() if not is_namespaced(v)}
        if not shown_vars:
            continue
        panel[section] = {
            "name": info["name"],
            "key": info["key_display"] or info["key"],
            "source": info["source"],
            "ini": (_ini_rel(info["ini_path"], mod_dir)
                    if info.get("ini_path") and mod_dir else info.get("ini_path")),
            "section": info.get("section"),
            "wired": wired,
            "vars": [
                {
                    "var": var,
                    "values": values,
                    "default": toggle_defaults.get(var, values[0]),
                }
                for var, values in shown_vars.items()
            ],
            # Keep the normal Toggle UI focused on variables that visibly
            # affect the model, but retain the Key section's complete aligned
            # tuple for Record-mode position evaluation.
            "cycle_vars": [
                {
                    "var": var,
                    "values": values,
                    "default": toggle_defaults.get(var, values[0]),
                }
                for var, values in info["vars"].items()
            ],
        }
    return panel


def build_menu_panel(menu_slots, toggle_defaults, mod_dir=None):
    """Model for the Menu panel: the slots of a mod's in-game clickable menu
    (see core/ini_menu.py), ordered as they appear in the menu.

    Read-only, and deliberately unfiltered — unlike the Toggle panel, a slot
    that gates no mesh is still shown, since the menu is the mod's own
    documentation of what it can change and hiding entries would misrepresent
    it. `effects` are the mutual-exclusion assignments a real click also
    applies, replayed by the UI so cycling here behaves like the game does.
    """
    panel = {}
    for key in sorted(menu_slots, key=lambda k: (
            menu_slots[k]["source"] or "",
            1 if menu_slots[k].get("kind") == "shape_slider" else 0,
            menu_slots[k].get("slot", 0))):
        info = menu_slots[key]
        if info.get("kind") == "shape_slider":
            panel[key] = {
                "kind": "shape_slider",
                "name": info["name"],
                "source": info["source"],
                "ini": (_ini_rel(info["ini_path"], mod_dir)
                        if info.get("ini_path") and mod_dir else info.get("ini_path")),
                "section": info["section"],
                "var": info["var"],
                "min": info["min"],
                "max": info["max"],
                "step": info["step"],
                "default": toggle_defaults.get(info["var"], "0"),
            }
            image_path = safe_resource_path(mod_dir, info.get("image_file"))
            if image_path and os.path.isfile(image_path):
                panel[key]["image_slot"] = True
                panel[key]["image"] = encode_texture_data_uri(
                    image_path, max_size=256, preserve_alpha=True)
            continue
        panel[key] = {
            "name": info["name"],
            "slot": info["slot"],
            "source": info["source"],
            "ini": (_ini_rel(info["ini_path"], mod_dir)
                    if info.get("ini_path") and mod_dir else info.get("ini_path")),
            "section": info["section"],
            "var": info["var"],
            "values": info["values"],
            "default": toggle_defaults.get(info["var"], info["values"][0]),
            "effects": info["effects"],
        }
        image_path = safe_resource_path(mod_dir, info.get("image_file"))
        if image_path and os.path.isfile(image_path):
            panel[key]["image_slot"] = True
            panel[key]["image"] = encode_texture_data_uri(
                image_path, max_size=256, preserve_alpha=True)
    return panel


def _gating_vars_from_groups(groups, mod_dir=None, game_profile=None,
                             active_mesh_keys=None):
    """Same notion as _gating_vars, computed directly from build_draw_groups'
    draws instead of the (buffer-file-dependent) mesh payload — matches
    _gating_vars(payload) but never needs buffer files to exist on disk.
    Used by unwired_pending_sections, which must stay cheap and reliable
    even when mesh geometry can't be resolved.
    """
    if active_mesh_keys is not None:
        semantics = build_mesh_semantics(
            groups, mod_dir, game_profile=game_profile)
        draws = (entry for label, entry in semantics.items()
                 if label in active_mesh_keys)
        found = set()
        for draw in draws:
            for cond_group in draw.get("conditions", []):
                for cond in cond_group:
                    found.add(cond["var"])
            for field in ("texture_variants", "normal_map_variants",
                          "normal_data_variants", "light_map_variants",
                          "material_map_variants"):
                for variant in draw.get(field, []):
                    for cond_group in variant.get("conditions", []):
                        for cond in cond_group:
                            found.add(cond["var"])
        return found

    found = set()
    for group in groups:
        for draw in group.get("draws", []):
            for cond_group in draw.get("conditions", []):
                for cond in cond_group:
                    found.add(cond["var"])
            for field in ("texture_variants", "normal_map_variants",
                          "normal_data_variants", "light_map_variants",
                          "material_map_variants"):
                for variant in draw.get(field, []):
                    for cond_group in variant.get("conditions", []):
                        for cond in cond_group:
                            found.add(cond["var"])
    return found


def load_present_state(context, overrides=None):
    """Read only the logical PRESENT projection from authoritative INIs."""
    return _parse_inis(
        context.ini_paths, context.mod_dir, overrides, context.docs).present


def load_control_state(context, overrides=None, pending_new_sections=None,
                       active_mesh_keys=None):
    """Read control semantics without constructing mesh or texture payloads."""
    parsed = _parse_inis(
        context.ini_paths, context.mod_dir, overrides, context.docs)
    gating_vars = _gating_vars_from_groups(
        parsed.groups, context.mod_dir, parsed.game.game, active_mesh_keys)
    return {
        "controls": {
            "toggles": build_toggle_panel(
                parsed.toggles, parsed.defaults, gating_vars,
                context.mod_dir, pending_new_sections),
            "menu": build_menu_panel(
                parsed.menu, parsed.defaults, context.mod_dir),
            "present": parsed.present,
        },
        "state": {
            "rules": parsed.state_rules,
            "defaults": parsed.defaults,
        },
    }


def load_mesh_semantics(context, overrides=None, active_mesh_keys=None):
    """Read draw visibility semantics without building geometry."""
    parsed = _parse_inis(
        context.ini_paths, context.mod_dir, overrides, context.docs)
    availability = {}
    bindings = asset_resolver.resolve_groups(
        parsed.groups, parsed.game, context.asset_folders,
        availability=availability)
    complete_index = (
        availability.get("configured_roots", 0) > 0
        and availability.get("ready_roots", 0)
        == availability.get("configured_roots", 0))
    _apply_texture_enrichment(parsed, context, bindings, complete_index)
    return {
        "meshes": build_mesh_semantics(
            parsed.groups, context.mod_dir, game_profile=parsed.game.game,
            active_mesh_keys=active_mesh_keys),
        "asset_resolution": asset_resolver.summarize_groups(
            parsed.groups, bindings, availability).to_dict(),
    }


def _register_material_profile(table, profile):
    """Register one immutable profile, rejecting same-ID contradictions."""
    metadata = profile.to_metadata()
    existing = table.get(profile.id)
    if existing is not None and existing != metadata:
        raise RuntimeError(f"Material profile ID collision: {profile.id}")
    table[profile.id] = metadata


def _apply_texture_enrichment(parsed, context, bindings, complete_index):
    """Run all semantic texture enrichment in the shared load order."""
    if str(getattr(parsed.game, "game", "")).casefold() == "wuwa":
        wuwa_texture_fallback.apply(
            parsed.groups, context.mod_dir,
            dds_classification_cache=context.dds_classification_cache)
    asset_enrichment.apply(
        parsed.groups, bindings, include_not_found=complete_index,
        mod_dir=context.mod_dir,
        dds_classification_cache=context.dds_classification_cache)


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


def unwired_pending_sections(folder_path, overrides, pending_new_sections,
                             ini_paths=None, documents=None):
    """{ini basename: [section name, ...]} — the subset of
    `pending_new_sections` (toggles added this session, not yet exported)
    that still don't gate any mesh. {} once every freshly-added toggle is
    wired or nothing is pending.

    Reuses only the cheap, buffer-file-free half of the load pipeline
    (_parse_inis, _gating_vars_from_groups) rather than the full
    load_mod/build_mesh_payload, since toggle_api.export_changes calls this
    on every export attempt and shouldn't pay for full geometry resolution.
    Each pending ini is parsed on its own, so names are never
    namespace-prefixed here, matching how edit_session/toggle_api track them.
    """
    if not pending_new_sections:
        return {}
    ini_paths = (list(ini_paths) if ini_paths is not None
                 else discover_ini_paths(folder_path))
    by_name = {_ini_rel(p, folder_path): p for p in ini_paths}

    result = {}
    for ini_name, sections in pending_new_sections.items():
        if not sections:
            continue
        ini_path = by_name.get(ini_name)
        if ini_path is None:
            continue
        groups, toggle_keys, _menu, _, _rules, _present = _parse_inis(
            [ini_path], folder_path, overrides, documents)
        gating = _gating_vars_from_groups(groups)
        still_unwired = [
            section for section in sections
            if not any(v in gating for v in toggle_keys.get(section, {}).get("vars", {}))
        ]
        if still_unwired:
            result[ini_name] = still_unwired
    return result


def _failure_health(context, overrides):
    """Best-effort diagnostics reserved for a failed model load."""
    try:
        return analyze_mod(
            context.mod_dir, ini_paths=context.ini_paths, overrides=overrides,
            documents=context.docs)
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
                        material_profiles=None, asset_resolution=None):
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
        "metadata": {"mesh_names": {}, "material_profiles": profile_table},
        "health": health,
        "asset_resolution": asset_resolution,
    }
    if game is not None:
        payload["metadata"]["game"] = game.to_metadata()
    if error:
        payload["error"] = error
    return payload


def load_mod(folder_path=None, overrides=None, pending_new_sections=None, *,
             ini_paths=None, documents=None, geometry=None, context=None,
             texture_source=None):
    """Parse a mod folder and return the structured application payload.

    `overrides`, if given, is {ini_path: text} — read that ini from this
    in-memory text instead of disk, to preview a pending edit (see
    app/edit_session.py) without writing anything. Defaults to reading
    everything from disk.

    `pending_new_sections`, if given, is {ini basename: {section name, ...}}
    (edit_session.new_sections_for), passed through to build_toggle_panel so
    a freshly-added, not-yet-wired toggle stays visible.

    `texture_source`, if supplied, receives each resolved model texture path,
    role and explicit transform and returns its application-facing URI.
    Legacy two-argument callbacks remain supported. Without it, direct callers
    retain the eager data-URI compatibility path.

    Errors are returned as {"error": ...} rather than raised, since this is
    called across the JS bridge where an exception surfaces as an opaque
    rejection.
    """
    if context is None:
        if folder_path is None:
            raise ValueError("folder_path is required")
        if ini_paths is None:
            # Direct core/test callers retain the old convenience form. The
            # application supplies paths through ModLoadContext before this
            # function is called, so the normal open path never rediscovers.
            ini_paths = find_inis(folder_path)
        context = ModLoadContext(
            folder_path, list(ini_paths), documents or {}, {})
    else:
        folder_path = context.mod_dir
        ini_paths = list(context.ini_paths)
        documents = context.docs
    overrides = overrides or {}
    health = None
    if not ini_paths:
        health = _failure_health(context, overrides)
        return _structured_payload(
            health=health,
            error="No active .ini files found in this folder.")

    try:
        parsed = _parse_inis(ini_paths, folder_path, overrides, documents)
        groups = parsed.groups
        toggle_keys = parsed.toggles
        menu_slots = parsed.menu
        toggle_defaults = parsed.defaults
        state_rules = parsed.state_rules
        present = parsed.present
        if not groups:
            health = _failure_health(context, overrides)
            return _structured_payload(
                health=health,
                error=f"No mesh geometry found across {len(ini_paths)} ini file(s).",
                game=parsed.game)

        availability = {}
        bindings = asset_resolver.resolve_groups(
            groups, parsed.game, context.asset_folders,
            availability=availability)
        complete_index = (
            availability.get("configured_roots", 0) > 0
            and availability.get("ready_roots", 0)
            == availability.get("configured_roots", 0))
        _apply_texture_enrichment(parsed, context, bindings, complete_index)
        asset_resolution = asset_resolver.summarize_groups(
            groups, bindings, availability).to_dict()

        built = build_mesh_result(
            groups, folder_path, geometry=geometry,
            texture_source=texture_source,
            game_profile=parsed.game.game)
        mesh_payload = built.meshes
        textures = built.textures
        if not mesh_payload:
            health = _failure_health(context, overrides)
            return _structured_payload(
                health=health,
                error="No mesh data could be extracted (buffer files missing?).",
                game=parsed.game)

        # Viewer-only material choices are hydrated into the same evidence
        # field used by the classifier.  This keeps reliable override handling
        # in one profile-selection path and never edits the source INI.
        from .metadata import hydrate_component_material_kinds
        hydrate_component_material_kinds(mesh_payload, context.metadata)
        material_profiles = _assign_material_profiles(mesh_payload, parsed.game)

        toggles = build_toggle_panel(
            toggle_keys, toggle_defaults, _gating_vars(mesh_payload), folder_path,
            pending_new_sections)
        menu = build_menu_panel(menu_slots, toggle_defaults, folder_path)
        return _structured_payload(
            meshes=mesh_payload, textures=textures, toggles=toggles, menu=menu,
            present=present, state_rules=state_rules,
            state_defaults=toggle_defaults, game=parsed.game,
            material_profiles=material_profiles,
            asset_resolution=asset_resolution)
    except Exception:
        traceback.print_exc()
        health = _failure_health(context, overrides)
        return _structured_payload(
            health=health,
            error="Unexpected backend error. See the application log for details.")

"""Project analyzed mod semantics into application control state."""

import os

from core.geometry.mesh_builder import build_mesh_semantics
from core.ini.condition import is_namespaced
from core.mod_discovery import discover_ini_paths
from core.resource_paths import safe_resource_path
from core.textures import encode_texture_data_uri

from app.mods.analysis import _ini_rel, analyze_mod_inis


_VARIANT_FIELDS = (
    "texture_variants", "normal_map_variants", "normal_data_variants",
    "light_map_variants", "material_map_variants", "emission_map_variants",
)


def _gating_vars_from_entries(entries):
    """Collect variables that decide entries' visibility or textures."""
    found = set()
    for entry in entries:
        for group in entry.get("conditions", []):
            for cond in group:
                found.add(cond["var"])
        for field in _VARIANT_FIELDS:
            for variant in entry.get(field, []):
                for group in variant.get("conditions", []):
                    for cond in group:
                        found.add(cond["var"])
    return found


def _gating_vars(payload):
    """Variables that decide some mesh's visibility or its texture."""
    return _gating_vars_from_entries(
        entry for entry in payload.values() if isinstance(entry, dict))


def build_toggle_panel(toggle_keys, toggle_defaults, gating_vars, mod_dir=None,
                       pending_new_sections=None):
    """Build the Toggle panel projection from analyzed key sections."""
    pending_new_sections = pending_new_sections or {}
    panel = {}
    for section, info in toggle_keys.items():
        gated = {v: vals for v, vals in info["vars"].items()
                 if v in gating_vars}
        wired = bool(gated)
        if wired:
            shown_vars = gated
        else:
            ini_name = (
                _ini_rel(info["ini_path"], mod_dir) if mod_dir
                else os.path.basename(info["ini_path"])
            ) if info.get("ini_path") else None
            is_pending_new = (
                bool(ini_name)
                and info.get("section") in pending_new_sections.get(ini_name, ())
            )
            if not is_pending_new:
                continue
            shown_vars = {
                v: vals for v, vals in info["vars"].items()
                if not is_namespaced(v)
            }
        if not shown_vars:
            continue
        panel[section] = {
            "name": info["name"],
            "key": info["key_display"] or info["key"],
            "source": info["source"],
            "ini": (
                _ini_rel(info["ini_path"], mod_dir)
                if info.get("ini_path") and mod_dir else info.get("ini_path")
            ),
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
            # Preserve the complete aligned tuple for Record-mode evaluation.
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


def project_toggle_panel(toggle_keys, toggle_defaults, mod_dir=None):
    """Project the graph's already-classified direct and pending controls.

    The unified graph owns the wired decision.  This projection intentionally
    does not rediscover mesh gates or inspect legacy toggle detectors.
    """
    panel = {}
    for section, info in toggle_keys.items():
        wired = bool(info.get("wired"))
        if wired:
            shown_vars = dict(info.get("vars", {}))
        elif info.get("pending"):
            kinds = info.get("variable_kinds", {})
            shown_vars = {
                var: values for var, values in info.get("vars", {}).items()
                if kinds.get(var) != "namespace"
            }
        else:
            continue
        if not shown_vars:
            continue
        ini_path = info.get("ini_path")
        panel[section] = {
            "name": info.get("name", section),
            "key": info.get("key_display") or info.get("key", ""),
            "source": info.get("source"),
            "ini": (_ini_rel(ini_path, mod_dir)
                    if ini_path and mod_dir else ini_path),
            "section": info.get("section"),
            "wired": wired,
            "vars": [
                {"var": var, "values": values,
                 "default": toggle_defaults.get(var, values[0])}
                for var, values in shown_vars.items()
            ],
            "cycle_vars": [
                {"var": var, "values": values,
                 "default": toggle_defaults.get(var, values[0])}
                for var, values in info.get("vars", {}).items()
            ],
        }
    return panel


def _filter_active_projection(projection, graph, active_mesh_keys):
    """Keep the graph projection aligned with the currently displayed meshes."""
    if graph is None or active_mesh_keys is None:
        return projection
    active_mesh_keys = {str(key) for key in active_mesh_keys}
    active_controls = {
        control.id for control in graph.controls.values()
        if any(str(effect.target) in active_mesh_keys
               for effect in control.effects)
    }
    toggles = {
        key: info for key, info in projection.get("toggles", {}).items()
        if info.get("pending")
        or active_controls.intersection(info.get("_semantic_ids", ()))
    }
    menu = {
        key: info for key, info in projection.get("menu", {}).items()
        if info.get("_semantic_id") in active_controls
    }
    return {**projection, "toggles": toggles, "menu": menu}


def build_menu_panel(menu_slots, toggle_defaults, mod_dir=None):
    """Build the read-only projection of unified interactive controls."""
    panel = {}
    for key in sorted(menu_slots, key=lambda k: (
            menu_slots[k]["source"] or "",
            1 if menu_slots[k].get("domain", {}).get("kind") == "continuous"
            else 0,
            menu_slots[k].get("slot", 0))):
        info = menu_slots[key]
        domain = info.get("domain") or {}
        if domain.get("kind") == "continuous":
            minimum = domain.get("min", info.get("min", 0.0))
            maximum = domain.get("max", info.get("max", 1.0))
            panel[key] = {
                "kind": "continuous",
                "domain": {"kind": "continuous", "min": minimum,
                            "max": maximum},
                "name": info["name"],
                "source": info["source"],
                "ini": (
                    _ini_rel(info["ini_path"], mod_dir)
                    if info.get("ini_path") and mod_dir else info.get("ini_path")
                ),
                "section": info["section"],
                "var": info["var"],
                "min": minimum,
                "max": maximum,
                "step": info.get("step", 0.01),
                "default": toggle_defaults.get(info["var"], "0"),
            }
            image_path = safe_resource_path(mod_dir, info.get("image_file"))
            if image_path and os.path.isfile(image_path):
                panel[key]["image_slot"] = True
                panel[key]["image"] = encode_texture_data_uri(
                    image_path, max_size=256, preserve_alpha=True)
            continue
        values = info.get("values", domain.get("values", []))
        panel[key] = {
            "kind": "discrete",
            "domain": {"kind": "discrete", "values": values},
            "name": info["name"],
            "slot": info.get("slot"),
            "source": info["source"],
            "ini": (
                _ini_rel(info["ini_path"], mod_dir)
                if info.get("ini_path") and mod_dir else info.get("ini_path")
            ),
            "section": info["section"],
            "var": info["var"],
            "values": values,
            "default": toggle_defaults.get(info["var"], values[0] if values else "0"),
            "effects": info.get("effects", []),
        }
        image_path = safe_resource_path(mod_dir, info.get("image_file"))
        if image_path and os.path.isfile(image_path):
            panel[key]["image_slot"] = True
            panel[key]["image"] = encode_texture_data_uri(
                image_path, max_size=256, preserve_alpha=True)
    return panel


def _gating_vars_from_groups(groups, mod_dir=None, game_profile=None,
                             active_mesh_keys=None):
    """Collect gating variables without requiring geometry files."""
    if active_mesh_keys is not None:
        semantics = build_mesh_semantics(
            groups, mod_dir, game_profile=game_profile)
        draws = (entry for label, entry in semantics.items()
                 if label in active_mesh_keys)
        return _gating_vars_from_entries(draws)

    return _gating_vars_from_entries(
        draw for group in groups for draw in group.get("draws", []))


def load_present_state(context, overrides=None):
    """Read only the logical PRESENT projection from authoritative INIs."""
    return analyze_mod_inis(
        context.ini_paths, context.mod_dir, overrides, context.docs).present


def load_control_state(context, overrides=None, pending_new_sections=None,
                       active_mesh_keys=None):
    """Read control semantics without constructing mesh geometry."""
    parsed = analyze_mod_inis(
        context.ini_paths, context.mod_dir, overrides, context.docs,
        pending_new_sections=pending_new_sections)
    graph_projection = parsed.control_projection
    graph_projection = _filter_active_projection(
        graph_projection, parsed.control_graph, active_mesh_keys)
    projected_toggles = graph_projection.get("toggles", {})
    projected_menu = graph_projection.get("menu", {})
    return {
        "controls": {
            "toggles": project_toggle_panel(
                projected_toggles, parsed.defaults, context.mod_dir),
            "menu": build_menu_panel(
                projected_menu, parsed.defaults, context.mod_dir),
            "actions": graph_projection.get("actions", []),
            "present": parsed.present,
        },
        "state": {
            "rules": parsed.state_rules,
            "defaults": parsed.defaults,
        },
    }


def unwired_pending_sections(folder_path, overrides, pending_new_sections,
                             ini_paths=None, documents=None):
    """Find newly-added Key sections not connected to a render effect."""
    if not pending_new_sections:
        return {}
    ini_paths = (list(ini_paths) if ini_paths is not None
                 else discover_ini_paths(folder_path))
    parsed = analyze_mod_inis(
        ini_paths, folder_path, overrides, documents,
        pending_new_sections=pending_new_sections)
    graph = parsed.control_graph
    facts_by_ini = {
        _ini_rel(fact.source.path, folder_path): fact
        for fact in (graph.facts if graph is not None else ())
    }
    wired = (graph.control_variables if graph is not None else set())
    result = {}
    for ini_name, sections in pending_new_sections.items():
        if not sections:
            continue
        fact = facts_by_ini.get(ini_name)
        if fact is None:
            continue
        key_writes = {
            key.section: set(key.writes) for key in fact.key_inputs
        }
        still_unwired = [
            section for section in sections
            if not (key_writes.get(section, set()) & wired)
        ]
        if still_unwired:
            result[ini_name] = still_unwired
    return result

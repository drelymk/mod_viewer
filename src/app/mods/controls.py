"""Project analyzed mod semantics into application control state."""

import os

from core.geometry.mesh_builder import build_mesh_semantics
from core.ini.condition import is_namespaced
from core.mod_discovery import discover_ini_paths
from core.resource_paths import safe_resource_path
from core.textures import encode_texture_data_uri

from app.mods.analysis import _ini_rel, analyze_mod_inis


def _gating_vars(payload):
    """Variables that decide some mesh's visibility or its texture."""
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


def build_menu_panel(menu_slots, toggle_defaults, mod_dir=None):
    """Build the read-only projection of a mod's clickable menu."""
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
                "ini": (
                    _ini_rel(info["ini_path"], mod_dir)
                    if info.get("ini_path") and mod_dir else info.get("ini_path")
                ),
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
            "ini": (
                _ini_rel(info["ini_path"], mod_dir)
                if info.get("ini_path") and mod_dir else info.get("ini_path")
            ),
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
    """Collect gating variables without requiring geometry files."""
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
    return analyze_mod_inis(
        context.ini_paths, context.mod_dir, overrides, context.docs).present


def load_control_state(context, overrides=None, pending_new_sections=None,
                       active_mesh_keys=None):
    """Read control semantics without constructing mesh or texture payloads."""
    parsed = analyze_mod_inis(
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


def unwired_pending_sections(folder_path, overrides, pending_new_sections,
                             ini_paths=None, documents=None):
    """Find newly-added toggle sections that still gate no mesh."""
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
        parsed = analyze_mod_inis(
            [ini_path], folder_path, overrides, documents)
        gating = _gating_vars_from_groups(parsed.groups)
        still_unwired = [
            section for section in sections
            if not any(v in gating for v in parsed.toggles.get(section, {}).get("vars", {}))
        ]
        if still_unwired:
            result[ini_name] = still_unwired
    return result

"""Aggregate the semantic analysis of all INIs in one selected mod."""

import os
from dataclasses import dataclass

from core.editing.present import SECTION_NAME as PRESENT_SECTION
from core.ini.analysis import analyze_ini
from core.ini.menu import attach_menu_images
from core.ini.sections import extract_resources, merge_sections
from core.materials.game_profile import GameDetection, resolve_game_detection


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

    Some generated mods switch ``ib``/``vb0`` halfway through one override,
    so a group can contain draws backed by several position buffers.
    ``mesh_builder`` performs the final per-draw filter; it needs every
    matching morph here.
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
    """Namespace an INI's variables so sibling INIs cannot collide."""
    if not multi:
        return None, None
    parent_dir = os.path.dirname(ini_path)
    if os.path.normpath(parent_dir) != os.path.normpath(folder_path):
        source = os.path.relpath(parent_dir, folder_path).replace(os.sep, "/")
    else:
        source = os.path.splitext(os.path.basename(ini_path))[0]
    # ``source`` is a compact UI grouping label, not the parser identity.
    identity = os.path.splitext(_ini_rel(ini_path, folder_path))[0]
    return f"{identity}::", source


def _ini_rel(ini_path, folder_path):
    return os.path.relpath(ini_path, folder_path).replace(os.sep, "/")


def _rebase_resources(resources, ini_path, folder_path):
    """Make filenames authored relative to a nested INI root-relative."""
    rel_dir = os.path.relpath(os.path.dirname(ini_path), folder_path)
    if rel_dir == os.curdir:
        return resources
    for info in resources.values():
        filename = info.get("filename")
        if filename:
            info["filename"] = os.path.normpath(os.path.join(rel_dir, filename))
    return resources


def analyze_mod_inis(ini_paths, folder_path, overrides=None, documents=None):
    """Aggregate independent INI analyses into one mod semantic model.

    Each INI is parsed separately so resource definitions from sibling files
    cannot overwrite one another. ``overrides`` and ``documents`` are passed
    through to ``merge_sections`` so staged edits remain authoritative.
    """
    groups = []
    toggle_keys, menu_slots, toggle_defaults, state_rules = {}, {}, {}, []
    present_infos, present_sources = [], []
    game_evidence = []
    runtime_evidence = []
    texture_api_evidence = []
    multi = len(ini_paths) > 1

    # Shared across every INI: duplicate generic component names are
    # disambiguated instead of one silently overwriting another.
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
        has_controls = (
            any(info.get("section", "").lower() != PRESENT_SECTION.lower()
                for info in ini_toggles.values())
            or bool(ini_menu)
            or bool(shape_sliders)
        )
        capture_vars = []
        if has_controls:
            rel = _ini_rel(ini_path, folder_path)
            for info in ini_toggles.values():
                if info.get("section", "").lower() == PRESENT_SECTION.lower():
                    continue
                capture_vars.extend(info.get("vars", {}))
            capture_vars.extend(info.get("var") for info in ini_menu.values())
            capture_vars.extend(info.get("var") for info in shape_sliders)
            capture_vars = list(dict.fromkeys(
                var for var in capture_vars if var))
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
        variables = [{
            "var": var,
            "values": values,
            "default": toggle_defaults.get(var, values[0]),
        } for var, values in present_info["vars"].items()]
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
            sync_error = (
                "A PRESENT key has variable lists with different position "
                "counts. Edit the INI so its cycle lists align.")
        elif len(counts) != 1:
            sync_error = (
                "PRESENT keys have different position counts. Edit the INIs "
                "so their cycle lists align.")
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

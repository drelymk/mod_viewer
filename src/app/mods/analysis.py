"""Aggregate the semantic analysis of all INIs in one selected mod."""

import os
import re
from dataclasses import dataclass, field

from core.geometry.semantics import deduplicate_draws
from core.editing.present import SECTION_NAME as PRESENT_SECTION
from core.ini.analysis import analyze_ini
from core.ini.document import IniDocument
from core.ini.draw_scan import gating_var_names
from core.ini.menu import attach_menu_images, extract_controller_toggles
from core.ini.sections import (canonical_var_names, extract_ini_namespace,
                               extract_resources, merge_sections)
from core.ini.animations import (
    compute_animation_control_vars, discover_compute_animations,
)
from core.materials.game_profile import GameDetection, resolve_game_detection


_DIRECT_FORWARD_RE = re.compile(
    r'^\$\\(?P<namespace>[^\\\s]+(?:\\[^\\\s]+)*)\\(?P<target>\w+)\s*=\s*\$(?P<source>\w+)$',
    re.I)
_VARIANT_FIELDS = (
    "texture_variants", "normal_map_variants", "normal_data_variants",
    "light_map_variants", "material_map_variants", "emission_map_variants",
)


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
    animations: list = field(default_factory=list)
    animation_control_vars: set = field(default_factory=set)
    resource_files: list = field(default_factory=list)
    texture_override_indexes: list = field(default_factory=list)

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


def _ini_scope(ini_path, folder_path, multi, source=None):
    """Namespace an INI's variables so sibling INIs cannot collide."""
    if not multi:
        return None, None
    ini_rel = _ini_rel(ini_path, folder_path, source=source)
    rel_dir = os.path.dirname(ini_rel)
    if rel_dir not in ("", "."):
        source_name = rel_dir.replace("\\", "/")
    else:
        source_name = os.path.splitext(os.path.basename(ini_rel))[0]
    # ``source`` is a compact UI grouping label, not the parser identity.
    identity = os.path.splitext(ini_rel)[0]
    return f"{identity}::", source_name


def _ini_rel(ini_path, folder_path, source=None):
    if source is None:
        source = getattr(ini_path, "source", None)
    if source is not None and source.is_resource_reference(ini_path):
        return source.logical_path(ini_path)
    return os.path.relpath(ini_path, folder_path).replace(os.sep, "/")


def _rebase_resources(resources, ini_path, folder_path, source=None):
    """Make filenames authored relative to a nested INI root-relative."""
    if source is None:
        source = getattr(ini_path, "source", None)
    rel_dir = (os.path.dirname(_ini_rel(ini_path, folder_path, source=source))
               if source is not None
               else os.path.relpath(os.path.dirname(ini_path), folder_path))
    if rel_dir == os.curdir:
        return resources
    for info in resources.values():
        filename = info.get("filename")
        if filename:
            info["filename"] = os.path.normpath(os.path.join(rel_dir, filename))
    return resources


def _mapped_value(mapping, path):
    """Read a staged mapping using either its authored or normalized path key."""
    if not mapping:
        return None
    value = mapping.get(path)
    if value is not None:
        return value
    normalized = os.path.normcase(os.path.abspath(path))
    for candidate, value in mapping.items():
        if os.path.normcase(os.path.abspath(candidate)) == normalized:
            return value
    return None


def _extract_namespace_forwarding(record, namespace_targets):
    """Resolve direct qualified-variable assignments to known namespaces."""
    result = []
    seen = set()
    canonical = record["canonical_vars"]
    present_sections = (
        lines for section, lines in record["sections"].items()
        if str(section).casefold() == "present")
    for lines in present_sections:
        for raw in lines:
            line = str(raw).split(";", 1)[0].strip()
            match = _DIRECT_FORWARD_RE.fullmatch(line)
            if not match:
                continue
            target_record = namespace_targets.get(
                match.group("namespace").casefold())
            if target_record is None:
                continue
            source_local = canonical.get(
                match.group("source").casefold(), match.group("source"))
            target_local = target_record["canonical_vars"].get(
                match.group("target").casefold(), match.group("target"))
            source_var = f"{record['var_prefix'] or ''}{source_local}"
            target_var = f"{target_record['var_prefix'] or ''}{target_local}"
            key = (source_var.casefold(), target_var.casefold())
            if key in seen:
                continue
            seen.add(key)
            result.append({
                "source_var": source_var,
                "source_local": source_local,
                "destination": target_var,
                "destination_local": target_local,
                "namespace": match.group("namespace"),
                "source_ini": record["ini_path"],
                "target_ini": target_record["ini_path"],
            })
    return result


def _gating_vars_from_groups(groups):
    """Collect variables used by draw visibility and texture variants."""
    found = set()
    for group in groups:
        for entry in group.get("draws", []):
            for clauses in entry.get("conditions", []):
                found.update(clause["var"] for clause in clauses)
            for field in _VARIANT_FIELDS:
                for variant in entry.get(field, []):
                    for clauses in variant.get("conditions", []):
                        found.update(clause["var"] for clause in clauses)
    return found


def _qualified_vars_from_targets(namespace_targets):
    """Map qualified reads to gated variables owned by unique namespaces."""
    result = {}
    for record in namespace_targets.values():
        namespace = record.get("namespace")
        if not namespace:
            continue
        local_vars = set(gating_var_names(record["sections"]))
        local_vars.update(record.get("extra_gating_vars", ()))
        for local_var in local_vars:
            canonical = record["canonical_vars"].get(
                str(local_var).casefold(), str(local_var))
            result[f"\\{namespace}\\{canonical}".casefold()] = (
                f"{record['var_prefix'] or ''}{canonical}")
    return result


def analyze_mod_inis(ini_paths, folder_path, overrides=None, documents=None,
                     source=None):
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
    animations = []
    animation_control_vars = set()
    resource_files = []
    texture_override_indexes = []
    multi = len(ini_paths) > 1
    if source is None and ini_paths:
        source = getattr(ini_paths[0], "source", None)
    if source is not None and getattr(source, "virtual", False):
        documents = dict(documents or {})
        for ini_path in ini_paths:
            if source.is_resource_reference(ini_path) \
                    and ini_path not in documents:
                documents[ini_path] = IniDocument.from_string(
                    source.read_text(ini_path), path=ini_path)

    # Parse each INI once up front so file-level namespaces and direct
    # forwarding assignments can be resolved before semantic analysis. The
    # section projection remains per-INI; only the namespace registry is shared.
    ini_records = []
    for ini_path in ini_paths:
        secs = merge_sections([ini_path], overrides=overrides,
                              documents=documents)
        var_prefix, source_name = _ini_scope(
            ini_path, folder_path, multi, source=source)
        document = _mapped_value(documents, ini_path)
        text = _mapped_value(overrides, ini_path)
        ini_records.append({
            "ini_path": ini_path,
            "sections": secs,
            "var_prefix": var_prefix,
            "source": source_name,
            "canonical_vars": canonical_var_names(secs),
            "namespace": extract_ini_namespace(
                ini_path, text=text, document=document),
            "extra_gating_vars": set(),
        })

    namespace_candidates = {}
    for record in ini_records:
        namespace = record["namespace"]
        if namespace:
            namespace_candidates.setdefault(namespace.casefold(), []).append(
                record)
    namespace_targets = {
        namespace: records[0]
        for namespace, records in namespace_candidates.items()
        if len(records) == 1
    }
    records_by_path = {record["ini_path"]: record for record in ini_records}
    for record in ini_records:
        record["forwardings"] = _extract_namespace_forwarding(
            record, namespace_targets)
        record["controllers"] = extract_controller_toggles(
            record["sections"],
            {item["source_local"] for item in record["forwardings"]},
            var_prefix=record["var_prefix"], source=record["source"],
            canonical_vars=record["canonical_vars"])

        # Do not make a forwarded target a tracked draw gate until its source
        # has a controller shape that the viewer can actually expose. A
        # source with multiple destinations is also left fail-open because no
        # single menu entry can represent that fan-out safely.
        by_source = {}
        for forwarding in record["forwardings"]:
            by_source.setdefault(
                forwarding["source_local"].casefold(), []).append(forwarding)
        for local in record["controllers"]:
            destinations = {
                item["destination"].casefold(): item
                for item in by_source.get(local.casefold(), [])
            }
            if len(destinations) != 1:
                continue
            forwarding = next(iter(destinations.values()))
            target = records_by_path.get(forwarding["target_ini"])
            if target is not None:
                target["extra_gating_vars"].add(
                    forwarding["destination_local"])

    qualified_vars = _qualified_vars_from_targets(namespace_targets)

    # Shared across every INI: duplicate generic component names are
    # disambiguated instead of one silently overwriting another.
    seen_labels = {}

    for record in ini_records:
        ini_path = record["ini_path"]
        secs = record["sections"]
        var_prefix = record["var_prefix"]
        source_name = record["source"]

        resources = _rebase_resources(
            extract_resources(secs), ini_path, folder_path, source=source)
        analysis = analyze_ini(
            secs, resources=resources, var_prefix=var_prefix, source=source_name,
            seen=seen_labels,
            extra_gating_vars=record["extra_gating_vars"],
            qualified_vars=qualified_vars,
            canonical_vars=record["canonical_vars"])
        compute = discover_compute_animations(
            secs, resources, mod_dir=folder_path, ini_path=ini_path,
            source=source, var_prefix=var_prefix,
            canonical_vars=record["canonical_vars"],
            qualified_vars=qualified_vars)
        animation_control_vars.update(
            compute_animation_control_vars(compute, analysis.state_rules))
        record["analysis"] = analysis
        resource_files.extend(
            info["filename"] for info in analysis.resources.values()
            if info.get("filename"))
        texture_override_indexes.append(analysis.texture_override_index)
        ini_groups = analysis.draw_groups
        identity_source = _ini_rel(ini_path, folder_path, source=source)
        for group in ini_groups:
            # ``source`` is intentionally a compact UI grouping label. Keep
            # the complete relative INI path separately for mesh identity.
            group["identity_source"] = identity_source
        compute_by_position = {}
        for item in compute:
            key = str(item.get("position_resource", "")).casefold()
            if key:
                compute_by_position.setdefault(key, []).append(item)
        for group in ini_groups:
            matches = compute_by_position.get(
                str(group.get("position_resource", "")).casefold(), ())
            if len(matches) == 1:
                # Keep the descriptor on the group that came from this INI.
                # Mesh construction must not match resources across siblings.
                group["_compute_animation"] = matches[0]
        shape_sliders = analysis.shapes
        state_rules.extend(analysis.state_rules)
        game_evidence.extend(analysis.game_evidence)
        runtime_evidence.extend(analysis.runtime_evidence)
        texture_api_evidence.extend(analysis.texture_api_evidence)
        animations.extend(analysis.animations)
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
            rel = _ini_rel(ini_path, folder_path, source=source)
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

    # Namespace controllers are discovered independently from the existing
    # clickable-slot parser. Only a resolved destination that the shared draw
    # analysis actually retained as a visibility/texture gate becomes a
    # viewer menu entry.
    model_gating_vars = _gating_vars_from_groups(groups)
    model_gating_keys = {value.casefold() for value in model_gating_vars}
    existing_slots = [
        int(info["slot"])
        for info in menu_slots.values()
        if str(info.get("slot", "")).lstrip("-").isdigit()
    ]
    next_controller_slot = max(existing_slots, default=0) + 1
    for record in ini_records:
        by_source = {}
        for forwarding in record["forwardings"]:
            by_source.setdefault(
                forwarding["source_local"].casefold(), []).append(forwarding)
        for local, info in record["controllers"].items():
            forwardings = by_source.get(local.casefold(), [])
            destinations = {
                item["destination"].casefold(): item
                for item in forwardings
            }
            if len(destinations) != 1:
                continue
            forwarding = next(iter(destinations.values()))
            destination = forwarding["destination"]
            if destination.casefold() not in model_gating_keys:
                continue

            controller = dict(info)
            controller.update({
                "name": forwarding["destination_local"],
                "var": destination,
                "slot": next_controller_slot,
                "source": record["source"],
                "ini_path": record["ini_path"],
            })
            next_controller_slot += 1
            base_key = (
                f"{record['var_prefix'] or ''}{info['section']}"
                f"#forwarded#{local}")
            key = base_key
            suffix = 2
            while key in menu_slots:
                key = f"{base_key}_{suffix}"
                suffix += 1
            menu_slots[key] = controller

            source_default = record["analysis"].defaults.get(info["var"])
            if source_default is not None:
                toggle_defaults[destination] = source_default

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
        animations=animations,
        animation_control_vars=animation_control_vars,
        resource_files=list(dict.fromkeys(resource_files)),
        texture_override_indexes=texture_override_indexes,
    )


def resolved_draws(context, overrides=None):
    """Resolve the current staged draw map once for analysis consumers."""
    parsed = analyze_mod_inis(
        context.ini_paths, context.mod_dir, overrides, context.docs,
        source=context.source)
    draws = {}
    for group in parsed.groups:
        for draw in deduplicate_draws(group):
            draws.setdefault(draw.label, (draw, group))
    return parsed, draws

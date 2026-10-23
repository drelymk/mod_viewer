"""Assembly of resolved ``DrawCall`` records into component draw groups."""

import re
from dataclasses import replace

from ..geometry.buffers import DEFAULT_UV_OFFSET, POSITION_STRIDE, _res_get
from ..geometry.draw_call import AuthoredDrawCall, DrawCall
from ..geometry.identity import DrawOccurrence
from ..geometry.skinning import resolve_skinning_source
from ..mod_source import DirectoryModSource
from .draw_resources import (
    _collect_resource_copy_sources, _extract_hash, _ib_index_size,
    _ib_res_to_component, _resolve_component_buffers, _resolve_normal_source,
    _hash_wide_overrides, _select_draw_sections,
)
from .draw_scan import _buffer_resource_at_draw, _scan_sections_for_draws
from .sections import ResourceTable
from .texture_roles import (TextureOverrideIndex, _condition_group_is_consistent,
                            _condition_difference)


def _lookup_component_value(mapping, component):
    candidates = [
        component,
        re.sub(r"[A-Za-z]+$", "", component),
        re.sub(r"(?<=.)[A-Z][a-z]+$", "", component),
    ]
    for candidate in candidates:
        if candidate:
            value = mapping.get(candidate.lower())
            if value:
                return value
    component_low = component.lower()
    prefix = max(
        (key for key in mapping if component_low.startswith(key)),
        key=len, default=None)
    return mapping.get(prefix) if prefix else None


def _resolved_texture_assignments(assignments, resolve_file, draw_conditions=None):
    resolved = []
    for assignment in assignments:
        # A binding from a disjoint branch cannot affect this draw. Keep the
        # surviving history in authored order, including repeated resources.
        if draw_conditions and assignment["cond"] and not any(
                _condition_group_is_consistent([*draw_group, *binding_group])
                for draw_group in draw_conditions
                for binding_group in assignment["cond"]):
            continue
        file = resolve_file(assignment["res"])
        if not file and str(assignment["res"]).casefold() != "null":
            continue
        item = {"conditions": assignment["cond"], "file": file}
        if assignment.get("texture_hashes"):
            item["texture_hashes"] = tuple(assignment["texture_hashes"])
        resolved.append(item)
    return resolved


def _apply_diffuse_state(draw, authored, resolve_file):
    history = _resolved_texture_assignments(
        authored.diffuse_history, resolve_file, draw.conditions)
    variants = _resolved_texture_assignments(
        authored.diffuse_variants, resolve_file, draw.conditions) or history
    if variants:
        if (any(not item["conditions"] for item in history)
                or _condition_difference(draw.conditions, variants[0]["conditions"]) is None):
            last_default = next((item for item in reversed(history) if not item["conditions"]), None)
            draw.set_texture_default("diffuse", None if last_default and last_default["file"] is None
                                     else variants[0]["file"])
        draw.texture_hashes["diffuse"] = list(dict.fromkeys(
            texture_hash
            for item in variants
            for texture_hash in item.get("texture_hashes", ())))
    if len(variants) > 1 or (variants and (
            variants[0]["file"] is None or (variants[0]["conditions"]
            and _condition_difference(draw.conditions, variants[0]["conditions"]) is not None))):
        draw.set_texture_variants("diffuse", variants)

    variant_variables = {
        clause["var"] for item in variants
        for group in item["conditions"] for clause in group
    }
    history_variables = {
        clause["var"] for item in history
        for group in item["conditions"] for clause in group
    }
    if (len(history) > 1 and
            (history_variables - variant_variables or len(history) > len(variants))):
        draw.texture_assignments = history


def _apply_auxiliary_map_state(draw, authored, resolve_file):
    for channel, state in authored.auxiliary_maps.items():
        assignments = state.get("history") or state.get("variants") or []
        resolved = _resolved_texture_assignments(
            assignments, resolve_file, draw.conditions)
        default_file = None
        for item in resolved:
            if not item["conditions"]:
                default_file = item["file"]
        if default_file:
            draw.set_texture_default(channel, default_file)
        hashes = list(dict.fromkeys(
            texture_hash
            for item in resolved
            for texture_hash in item.get("texture_hashes", ())))
        if hashes:
            draw.texture_hashes[channel] = hashes
        if len(resolved) > 1 or (resolved and (resolved[0]["conditions"] or resolved[0]["file"] is None)):
            draw.set_texture_variants(channel, resolved)


def _resolve_slot_texture_files(authored, resolve_file):
    return [
        replace(item, file=(resolve_file(item.resource) if item.resource else None) or item.file)
        for item in authored.slot_textures
    ]


def _auto_families(section_info):
    """Identify one authored shared auto and its same-INI/hash components."""
    families = {}
    for name, info in section_info.items():
        match = info.get("geometry_match_at_end")
        if name.lower().startswith("textureoverride") and match:
            key = ((info.get("src") or {}).get("ini_path"), match.hash)
            families.setdefault(key, []).append((name, info))
    result = {}
    for key, family in families.items():
        owners = [(name, info) for name, info in family
                  if info["geometry_match_at_end"].first_index is None
                  and info["drawindexed_auto"] and not info["ib"]]
        if len(owners) != 1:
            continue
        _name, owner = owners[0]
        members = [(label, info) for label, info in family
                   if info is not owner and info["ib"]]
        if (len(owner["auto_sources"]) != 1
                or len(owner["auto_guards"]) != 1 or not members
                or owner["auto_sources"][0]["occurrence"]["path"]):
            continue
        if all(not info["draws"] and not info["auto_draws"]
               and not info.get("unresolved_draws")
               for _label, info in members):
            result[key] = tuple(label for label, _ in members)
    return result


def build_draw_groups(sections, resources, var_prefix=None, source=None, seen=None,
                      gating_vars=None, animation_vars=None,
                      qualified_vars=None, *, section_info=None, resource_source=None):
    """Build resolved component groups while preserving authored draw snapshots."""
    resources = ResourceTable.normalize(resources)
    resource_source = resource_source or DirectoryModSource(".")
    if seen is None:
        seen = {}
    if section_info is None:
        section_info = _scan_sections_for_draws(
            sections, var_prefix, gating_vars, animation_vars, qualified_vars,
            resources=resources)
    resource_copy_sources = _collect_resource_copy_sources(sections, resources)
    resolved_buffers = _resolve_component_buffers(
        section_info, resources, resource_copy_sources, sections=sections)
    resolve_vertex_info = resolved_buffers["resolve_vertex_info"]
    component_buffers = resolved_buffers["component_buffers"]
    component_positions = resolved_buffers["component_positions"]
    component_texcoords = resolved_buffers["component_texcoords"]
    component_vertex_resources = resolved_buffers["component_vertex_resources"]
    component_blend_vertex_resources = resolved_buffers[
        "component_blend_vertex_resources"]
    component_animation_vertex_bindings = resolved_buffers[
        "component_animation_vertex_bindings"]
    # Include static owners so an absent animation cannot fall through to a
    # shorter component prefix belonging to a different position stream.
    component_animation_owners = {
        component: {
            "position": resource,
            "bindings": component_animation_vertex_bindings.get(component, []),
        }
        for component, resource in component_positions.items()
    }
    component_vertex_binding_history = resolved_buffers["component_vertex_binding_history"]
    hash_buffers = resolved_buffers["hash_buffers"]
    global_ib = resolved_buffers["global_ib"]
    global_position = resolved_buffers["global_position"]
    global_texcoord = resolved_buffers["global_texcoord"]
    draw_sections = _select_draw_sections(section_info, global_ib)
    texture_override_index = getattr(
        section_info, "texture_override_index", TextureOverrideIndex())
    global_compute_resources = dict(
        getattr(section_info, "global_compute_resources", {}) or {})
    if not draw_sections:
        return []

    # Hash-wide references are manual choices for each same-INI/hash part.
    family_references = {}
    family_auto_sources = {}
    family_auto_guards = {}
    family_auto_draws = {}
    auto_families = _auto_families(section_info)
    for provider in _hash_wide_overrides(section_info):
        match = provider["geometry_match_at_end"]
        family = ((provider.get("src") or {}).get("ini_path"), match.hash)
        family_references.setdefault(family, {}).update(
            provider["referenced_textures"])
        family_auto_sources.setdefault(family, []).extend(
            provider.get("auto_sources", []))
        if family in auto_families and provider["drawindexed_auto"]:
            family_auto_guards[family] = provider["auto_guards"][0]
            family_auto_draws[family] = provider["auto_draws"][0]

    ib_file_cache = {}

    def resolve_ib_file(ib_name):
        if ib_name not in ib_file_cache:
            ib_file_cache[ib_name] = _res_get(resources, ib_name).get("filename")
        return ib_file_cache[ib_name]

    texture_file_cache = {}

    def resolve_texture_file(resource_name):
        if resource_name not in texture_file_cache:
            texture_file_cache[resource_name] = _res_get(
                resources, resource_name).get("filename")
        return texture_file_cache[resource_name]

    def resolve_vertex_resource(resource_name):
        resource_info = resolve_vertex_info(resource_name)
        return resource_info.get("filename"), resource_info.get("stride")

    def lookup_component_buffers(component):
        return _lookup_component_value(component_buffers, component)

    def lookup_hash_buffers(value):
        return hash_buffers.get(_extract_hash(value)) if value else None

    def lookup_component_vertex_resources(component):
        return _lookup_component_value(component_vertex_resources, component) or {}

    def lookup_component_blend_vertex_resources(component):
        return _lookup_component_value(
            component_blend_vertex_resources, component) or {}

    def lookup_component_animation_vertex_bindings(component, position_resource):
        owner = _lookup_component_value(component_animation_owners, component)
        if not owner or not owner["bindings"] or not position_resource:
            return []
        owner_file, owner_stride = resolve_vertex_resource(owner["position"])
        position_file, position_stride = resolve_vertex_resource(position_resource)
        if (not owner_file or not position_file
                or not resource_source.same_reference(
                    resource_source.resolve_resource(owner_file),
                    resource_source.resolve_resource(position_file))
                or (owner_stride or POSITION_STRIDE)
                != (position_stride or POSITION_STRIDE)):
            return []
        return owner["bindings"]

    groups = []
    for section_name, info in draw_sections:
        if info.get("unresolved_draws") and not info["draws"]:
            continue
        display_name = section_name[len("TextureOverride"):] or section_name
        seen[display_name] = seen.get(display_name, 0) + 1
        label = (display_name if seen[display_name] == 1
                 else f"{display_name}_{seen[display_name]}")

        ib_resource = info["ib"] or global_ib
        component = _ib_res_to_component(ib_resource)
        group_vertex_resources = {
            slot: resource
            for slot, resource in (
                info.get("vertex_resources_at_end") or {}).items()
            if resource
        }
        # Group defaults prefer paired component declarations, direct/inferred
        # slots, hash families, then command-list globals. The authored state
        # at each draw overrides these defaults below, including explicit nulls.
        buffers = lookup_component_buffers(component)
        if not buffers:
            position = (info["vb0"] or _lookup_component_value(
                component_positions, component))
            vb2_stride = (_res_get(resources, info["vb2"]).get("stride", 0)
                          if info["vb2"] else 0)
            texcoord = ((info["vb2"] if info["vb2"] and vb2_stride != 32
                         else None) or info["vb1"] or _lookup_component_value(
                             component_texcoords, component))
            if (position and texcoord
                    and resolve_vertex_info(position).get("filename")):
                buffers = {"position": position, "texcoord": texcoord}
        if not buffers:
            buffers = (lookup_hash_buffers(section_name)
                       or lookup_hash_buffers(ib_resource))
        if not buffers and global_position and global_texcoord:
            buffers = {"position": global_position, "texcoord": global_texcoord}
        if not buffers:
            continue

        position_info = resolve_vertex_info(buffers["position"])
        texcoord_info = _res_get(resources, buffers["texcoord"])
        ib_info = _res_get(resources, ib_resource)
        diffuse_info = (_res_get(resources, info["diffuse"])
                        if info["diffuse"] else {})
        position_file = position_info.get("filename")
        texcoord_file = texcoord_info.get("filename")
        ib_file = ib_info.get("filename")
        if not (position_file and texcoord_file and ib_file):
            continue

        texcoord_stride = texcoord_info.get("stride", 20)
        position_stride = position_info.get("stride", POSITION_STRIDE)
        index_size = _ib_index_size(ib_info.get("format"))
        group_normal_source = _resolve_normal_source(
            group_vertex_resources, resources, position_file, position_stride,
            resolve_vertex_info)
        local = info["auto_draws"]
        local_binding = (len(local) == 1 and local[0].index_resource
                         and local[0].index_resource.casefold() != "null")
        match = info.get("geometry_match_at_end")
        family = (((info.get("src") or {}).get("ini_path"), match.hash)
                  if match else None)
        shared_auto = family_auto_draws.get(family)
        # An eligible local auto uses its authored execution snapshot. Shared
        # auto needs the component's end-state fallback below.
        authored_draws = list(info["draws"]) or (local if local_binding else []) or [AuthoredDrawCall(
            count=None, start=0, base=0, source=info["src"],
            conditions=shared_auto.conditions if shared_auto else [],
            animation_conditions=shared_auto.animation_conditions if shared_auto else [],
            binding_conditions=shared_auto.binding_conditions if shared_auto else [],
            index_resource=(_buffer_resource_at_draw(
                info["index_binding_history"], shared_auto.binding_conditions, None)
                if shared_auto else None),
            occurrence=DrawOccurrence(section_name, None),
            diffuse_variants=info.get("diffuse_variants_at_end") or [],
            diffuse_history=info.get("diffuse_history_at_end") or [],
            auxiliary_maps=info.get("aux_maps_at_end") or {},
            texture_provenance=(
                info.get("texture_provenance_at_end") or {}),
            geometry_match=info.get("geometry_match_at_end"),
            vertex_resources=info.get("vertex_resources_at_end") or {},
            slot_textures=info.get("slot_textures_at_end") or [],
            texture_checks=info.get("texture_checks_at_end"),
        )]
        auto_sources = (info.get("auto_sources") or
                        family_auto_sources.get(family, []))
        draws = []
        animation_bindings = []
        def resolve_geometry(authored):
            draw = DrawCall(
                count=authored.count, start=authored.start, base=authored.base,
                ib_file=ib_file, index_size=index_size,
                position_file=position_file, position_stride=position_stride,
                texcoord_file=texcoord_file, texcoord_stride=texcoord_stride,
                normal_source=group_normal_source)
            effective_ib = authored.index_resource or ib_resource
            if effective_ib != ib_resource:
                resolved_ib = resolve_ib_file(effective_ib)
                if resolved_ib:
                    draw.ib_file = resolved_ib
                    draw.index_size = _ib_index_size(
                        _res_get(resources, effective_ib).get("format"))

            draw_buffers = lookup_component_buffers(
                _ib_res_to_component(effective_ib))
            if not draw_buffers:
                draw_buffers = lookup_hash_buffers(effective_ib)
            effective_position_resource = buffers["position"]
            if draw_buffers and draw_buffers != buffers:
                position, stride = resolve_vertex_resource(
                    draw_buffers["position"])
                texcoord, texcoord_stride_for_draw = resolve_vertex_resource(
                    draw_buffers["texcoord"])
                if position:
                    draw.position_file = position
                    draw.position_stride = stride or POSITION_STRIDE
                    effective_position_resource = draw_buffers["position"]
                if texcoord:
                    draw.texcoord_file = texcoord
                    draw.texcoord_stride = texcoord_stride_for_draw or 20

            histories = _lookup_component_value(
                component_vertex_binding_history, _ib_res_to_component(effective_ib)
            ) or _lookup_component_value(component_vertex_binding_history, component) or {}
            # Select resources only when the authored guard proves one binding.
            # Layout interpretation and direct/null overrides stay below.
            for role, fallback in (("position", effective_position_resource),
                                   ("texcoord", (draw_buffers or buffers)["texcoord"])):
                history = histories.get(role, [])
                if role == "position" and not history:
                    history = histories.get("blend", [])
                slots = {item["slot"] for item in history
                         if str(item["resource"]).casefold() == str(fallback).casefold()}
                if len(slots) != 1:
                    continue
                slot = next(iter(slots))
                resource = _buffer_resource_at_draw(
                    [item for item in history if item["slot"] == slot],
                    authored.binding_conditions, fallback)
                if resource != fallback:
                    file, stride = resolve_vertex_resource(resource)
                    if file or resource is None:
                        setattr(draw, f"{role}_file", file)
                        default_stride = POSITION_STRIDE if role == "position" else 20
                        setattr(draw, f"{role}_stride", (stride or default_stride) if file else None)
                        if role == "position":
                            effective_position_resource = resource

            vertex_resources = authored.vertex_resources
            if 0 in vertex_resources:
                position_resource = vertex_resources[0]
                if position_resource is None:
                    draw.position_file = None
                    draw.position_stride = None
                    effective_position_resource = None
                else:
                    position, stride = resolve_vertex_resource(position_resource)
                    if position:
                        draw.position_file = position
                        draw.position_stride = stride or POSITION_STRIDE
                        effective_position_resource = position_resource

            authored_texcoords = {
                slot: vertex_resources[slot]
                for slot in (1, 2) if slot in vertex_resources
            }
            resolved_texcoord = None
            for slot in (2, 1):
                resource_name = authored_texcoords.get(slot)
                if not resource_name:
                    continue
                texcoord, stride = resolve_vertex_resource(resource_name)
                if texcoord and (stride or 0) != 32:
                    resolved_texcoord = (texcoord, stride or 20)
                    break
            if resolved_texcoord:
                draw.texcoord_file, draw.texcoord_stride = resolved_texcoord
            elif (authored_texcoords and any(resource_name is None
                                              for resource_name in authored_texcoords.values())):
                draw.texcoord_file = None
                draw.texcoord_stride = None

            effective_vertex_resources = dict(
                lookup_component_vertex_resources(
                    _ib_res_to_component(effective_ib)))
            effective_vertex_resources.update(group_vertex_resources)
            effective_vertex_resources.update(vertex_resources)
            draw.normal_source = _resolve_normal_source(
                effective_vertex_resources, resources, draw.position_file,
                draw.position_stride, resolve_vertex_info)
            return draw, effective_position_resource, histories

        for number, authored in enumerate(authored_draws, 1):
            # None with authored IB assignments is unresolved, not the group's
            # first replacement. Keep it in the scan without inventing geometry.
            if (info.get("index_binding_history") and authored.index_resource is None
                    and authored.count is not None):
                continue
            draw, effective_position_resource, histories = resolve_geometry(authored)
            draw = replace(draw,
                label=f"{label}-{number}", count=authored.count,
                start=authored.start, base=authored.base,
                conditions=authored.conditions,
                animation_conditions=authored.animation_conditions,
                sources=[authored.source] if authored.source else [],
                occurrence=authored.occurrence,
                geometry_match=authored.geometry_match,
                skinning_bone_offset=authored.skinning_bone_offset,
                texture_provenance=dict(authored.texture_provenance),
                slot_textures=_resolve_slot_texture_files(
                    authored, resolve_texture_file),
                texture_checks=authored.texture_checks,
            )
            effective_ib = authored.index_resource or ib_resource
            effective_ib_info = _res_get(resources, effective_ib)
            if authored.count is None:
                if not local_binding:
                    draw.sources = list(auto_sources)
                if (effective_ib_info.get("index_editable") and
                        (local_binding or (not local and family in auto_families))):
                    draw.auto_draw = {
                        "component": section_name, "resource": effective_ib,
                        "format": effective_ib_info.get("format"),
                        "family": () if local else auto_families[family],
                        "guard": () if local else family_auto_guards[family],
                    }
            vertex_resources = authored.vertex_resources
            bindings = authored.animation_bindings
            for binding in bindings:
                frame_draw, _, _ = resolve_geometry(replace(authored, **binding))
                draw.animation_bindings.append({
                    "animation_conditions": binding["animation_conditions"],
                    **{field: getattr(frame_draw, field) for field in (
                        "ib_file", "index_size", "position_file", "position_stride",
                        "texcoord_file", "texcoord_stride", "normal_source")},
                })
            if draw.animation_bindings:
                for field, value in draw.animation_bindings[0].items():
                    if field != "animation_conditions":
                        setattr(draw, field, value)
            # AuthoredDrawCall.vertex_resources is the complete effective
            # state captured at this draw, including CommandList bindings and
            # explicit nulls.  Do not merge a section-end snapshot here: a
            # later vbN assignment must not leak backward to an earlier draw.
            direct_skinning_resources = {
                slot: resource
                for slot, resource in vertex_resources.items()
                if resource
            }
            remap_resources = dict(global_compute_resources)
            remap_resources.update(authored.skinning_remap_resources)
            skinning_resolution = {
                "direct_candidate_count": sum(
                    1 for resource in direct_skinning_resources.values()
                    if resource),
                "position_resource": effective_position_resource,
                "legacy_component_candidate_count": 0,
                "resolution_source": None,
            }
            skinning_source, skinning_error = resolve_skinning_source(
                direct_skinning_resources, resolve_vertex_info,
                bone_id_offset=authored.skinning_bone_offset,
                remap_resources=remap_resources)
            if skinning_source is not None:
                skinning_resolution["resolution_source"] = "direct"
            if skinning_source is None and skinning_error is None:
                occupied_slots = set(vertex_resources)
                blend_fallback = {
                    slot: _buffer_resource_at_draw(
                        [item for item in histories.get("blend", []) if item["slot"] == slot],
                        authored.binding_conditions, resource)
                    for slot, resource in lookup_component_blend_vertex_resources(
                        _ib_res_to_component(effective_ib)).items()
                    if slot not in occupied_slots
                }
                skinning_resolution[
                    "legacy_component_candidate_count"] = len(blend_fallback)
                skinning_source, skinning_error = resolve_skinning_source(
                    blend_fallback, resolve_vertex_info,
                    bone_id_offset=authored.skinning_bone_offset,
                    remap_resources=remap_resources)
                if skinning_source is not None:
                    skinning_resolution["resolution_source"] = \
                        "legacy_component"
            draw.skinning_source = skinning_source
            draw.skinning_error = skinning_error
            draw.skinning_resolution = skinning_resolution
            _apply_diffuse_state(draw, authored, resolve_texture_file)
            _apply_auxiliary_map_state(draw, authored, resolve_texture_file)
            draw.texture_provenance = {
                role: provenance
                for role, provenance in draw.texture_provenance.items()
                if draw.texture_default(role) or draw.texture_rules(role)
            }
            for binding in lookup_component_animation_vertex_bindings(
                    _ib_res_to_component(effective_ib), effective_position_resource):
                item = {**binding, "position_file": draw.position_file,
                        "ib_file": draw.ib_file}
                if item not in animation_bindings:
                    animation_bindings.append(item)
            draws.append(draw)

        pool_files = []
        seen_pool_files = set()
        for resource_name in info["diffuse_pool"]:
            file = resolve_texture_file(resource_name)
            if file and file not in seen_pool_files:
                seen_pool_files.add(file)
                pool_files.append({"res": resource_name, "file": file})
        match = info.get("geometry_match_at_end")
        family = (((info.get("src") or {}).get("ini_path"), match.hash)
                  if match else None)
        references = dict(family_references.get(family, {}))
        references.update(info["referenced_textures"])
        referenced_files = list(dict.fromkeys(
            file for resource_name in references.values()
            if (file := resolve_texture_file(resource_name))))
        groups.append({
            "name": label,
            "display_name": display_name,
            "source": source,
            "position_resource": buffers["position"],
            "position_file": position_file,
            "texcoord_file": texcoord_file,
            "position_stride": position_stride,
            "texcoord_stride": texcoord_stride,
            "texcoord_uv_off": DEFAULT_UV_OFFSET,
            "normal_source": group_normal_source,
            "ib_file": ib_file,
            "diffuse_file": diffuse_info.get("filename"),
            "texture_candidates": pool_files,
            "referenced_texture_files": referenced_files,
            "index_size": index_size,
            "geometry_match": info.get("geometry_match_at_end"),
            "draws": draws,
            "animation_vertex_bindings": animation_bindings,
            "_texture_override_index": texture_override_index,
            "_texture_resources": resources,
        })
    return groups


__all__ = ["build_draw_groups"]

"""Assembly of resolved ``DrawCall`` records into component draw groups."""

import re

from ..geometry.buffers import DEFAULT_UV_OFFSET, POSITION_STRIDE, _res_get
from ..geometry.draw_call import AuthoredDrawCall, DrawCall, SlotTextureBinding
from ..geometry.skinning import resolve_skinning_source
from .draw_resources import (
    _collect_resource_copy_sources, _extract_hash, _ib_index_size,
    _ib_res_to_component, _resolve_component_buffers, _resolve_normal_source,
    _select_draw_sections,
)
from .draw_scan import _scan_sections_for_draws
from .texture_roles import TextureOverrideIndex


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


def _resolved_texture_assignments(assignments, resolve_file):
    resolved = []
    for assignment in assignments:
        file = resolve_file(assignment["res"])
        if not file:
            continue
        item = {"conditions": assignment["cond"], "file": file}
        if assignment.get("texture_hashes"):
            item["texture_hashes"] = tuple(assignment["texture_hashes"])
        resolved.append(item)
    return resolved


def _apply_diffuse_state(draw, authored, resolve_file):
    variants = _resolved_texture_assignments(
        authored.diffuse_variants, resolve_file)
    if variants:
        draw.set_texture_default("diffuse", variants[0]["file"])
        draw.texture_hashes["diffuse"] = list(dict.fromkeys(
            texture_hash
            for item in variants
            for texture_hash in item.get("texture_hashes", ())))
    if len(variants) > 1:
        draw.set_texture_variants("diffuse", variants)

    history = _resolved_texture_assignments(authored.diffuse_history, resolve_file)
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
        resolved = _resolved_texture_assignments(assignments, resolve_file)
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
        if len(resolved) > 1 or (resolved and resolved[0]["conditions"]):
            draw.set_texture_variants(channel, resolved)


def _resolve_slot_texture_files(authored, resolve_file):
    return [
        SlotTextureBinding(
            slot=item.slot,
            resource=item.resource,
            file=resolve_file(item.resource) or item.file,
            texture_hashes=item.texture_hashes,
            role_hint=item.role_hint,
            role_hint_source=item.role_hint_source,
        )
        for item in authored.slot_textures
    ]


def build_draw_groups(sections, resources, var_prefix=None, source=None, seen=None,
                      gating_vars=None):
    """Build resolved component groups while preserving authored draw snapshots."""
    if seen is None:
        seen = {}
    section_info = _scan_sections_for_draws(sections, var_prefix, gating_vars)
    resource_copy_sources = _collect_resource_copy_sources(sections, resources)
    resolved_buffers = _resolve_component_buffers(
        section_info, resources, resource_copy_sources)
    resolve_vertex_info = resolved_buffers["resolve_vertex_info"]
    component_buffers = resolved_buffers["component_buffers"]
    component_positions = resolved_buffers["component_positions"]
    component_texcoords = resolved_buffers["component_texcoords"]
    component_vertex_resources = resolved_buffers["component_vertex_resources"]
    component_blend_vertex_resources = resolved_buffers[
        "component_blend_vertex_resources"]
    hash_positions = resolved_buffers["hash_positions"]
    hash_texcoords = resolved_buffers["hash_texcoords"]
    global_ib = resolved_buffers["global_ib"]
    global_position = resolved_buffers["global_position"]
    global_texcoord = resolved_buffers["global_texcoord"]
    draw_sections = _select_draw_sections(section_info, global_ib)
    texture_override_index = getattr(
        section_info, "texture_override_index", TextureOverrideIndex())
    texture_override_index = texture_override_index.with_resource_files(resources)
    if not draw_sections:
        return []

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

    def lookup_component_vertex_resources(component):
        return _lookup_component_value(component_vertex_resources, component) or {}

    def lookup_component_blend_vertex_resources(component):
        return _lookup_component_value(
            component_blend_vertex_resources, component) or {}

    groups = []
    for section_name, info in draw_sections:
        display_name = section_name[len("TextureOverride"):] or section_name
        seen[display_name] = seen.get(display_name, 0) + 1
        label = (display_name if seen[display_name] == 1
                 else f"{display_name}_{seen[display_name]}")

        ib_resource = info["ib"] or global_ib
        component = _ib_res_to_component(ib_resource)
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
            texture_hash = _extract_hash(section_name) or _extract_hash(ib_resource)
            if texture_hash and texture_hash in hash_positions and texture_hash in hash_texcoords:
                buffers = {
                    "position": hash_positions[texture_hash],
                    "texcoord": hash_texcoords[texture_hash],
                }
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
        group_vertex_resources = {
            slot: info[f"vb{slot}"]
            for slot in (0, 1, 2) if info[f"vb{slot}"]
        }
        group_normal_source = _resolve_normal_source(
            group_vertex_resources, resources, position_file, position_stride,
            resolve_vertex_info)
        authored_draws = list(info["draws"]) or [AuthoredDrawCall(
            count=None, start=0, base=0, source=info["src"],
            diffuse_variants=info.get("diffuse_variants_at_end") or [],
            diffuse_history=info.get("diffuse_history_at_end") or [],
            auxiliary_maps=info.get("aux_maps_at_end") or {},
            texture_provenance=(
                info.get("texture_provenance_at_end") or {}),
            geometry_match=info.get("geometry_match_at_end"),
            vertex_resources=info.get("vertex_resources_at_end") or {},
            slot_textures=info.get("slot_textures_at_end") or [],
        )]
        draws = []
        for number, authored in enumerate(authored_draws, 1):
            draw = DrawCall(
                label=f"{label}-{number}", count=authored.count,
                start=authored.start, base=authored.base,
                conditions=authored.conditions,
                sources=[authored.source] if authored.source else [],
                ib_file=ib_file, index_size=index_size,
                position_file=position_file, position_stride=position_stride,
                texcoord_file=texcoord_file, texcoord_stride=texcoord_stride,
                normal_source=group_normal_source,
                geometry_match=authored.geometry_match,
                texture_provenance=dict(authored.texture_provenance),
                slot_textures=_resolve_slot_texture_files(
                    authored, resolve_texture_file),
            )
            effective_ib = authored.index_resource or ib_resource
            if effective_ib != ib_resource:
                resolved_ib = resolve_ib_file(effective_ib)
                if resolved_ib:
                    draw.ib_file = resolved_ib
                    draw.index_size = _ib_index_size(
                        _res_get(resources, effective_ib).get("format"))

            draw_buffers = lookup_component_buffers(
                _ib_res_to_component(effective_ib))
            if draw_buffers and draw_buffers != buffers:
                position, stride = resolve_vertex_resource(
                    draw_buffers["position"])
                texcoord, texcoord_stride_for_draw = resolve_vertex_resource(
                    draw_buffers["texcoord"])
                if position:
                    draw.position_file = position
                    draw.position_stride = stride or POSITION_STRIDE
                if texcoord:
                    draw.texcoord_file = texcoord
                    draw.texcoord_stride = texcoord_stride_for_draw or 20

            vertex_resources = authored.vertex_resources
            if 0 in vertex_resources:
                position_resource = vertex_resources[0]
                if position_resource is None:
                    draw.position_file = None
                    draw.position_stride = None
                else:
                    position, stride = resolve_vertex_resource(position_resource)
                    if position:
                        draw.position_file = position
                        draw.position_stride = stride or POSITION_STRIDE

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
            direct_skinning_resources = dict(group_vertex_resources)
            direct_skinning_resources.update(vertex_resources)
            skinning_source, skinning_error = resolve_skinning_source(
                direct_skinning_resources, resolve_vertex_info)
            if skinning_source is None and skinning_error is None:
                occupied_slots = (set(group_vertex_resources)
                                  | set(vertex_resources))
                blend_fallback = {
                    slot: resource
                    for slot, resource in lookup_component_blend_vertex_resources(
                        _ib_res_to_component(effective_ib)).items()
                    if slot not in occupied_slots
                }
                skinning_source, skinning_error = resolve_skinning_source(
                    blend_fallback, resolve_vertex_info)
            draw.skinning_source = skinning_source
            draw.skinning_error = skinning_error
            _apply_diffuse_state(draw, authored, resolve_texture_file)
            _apply_auxiliary_map_state(draw, authored, resolve_texture_file)
            draw.texture_provenance = {
                role: provenance
                for role, provenance in draw.texture_provenance.items()
                if draw.texture_default(role) or draw.texture_rules(role)
            }
            draws.append(draw)

        pool_files = []
        seen_pool_files = set()
        for resource_name in info["diffuse_pool"]:
            file = resolve_texture_file(resource_name)
            if file and file not in seen_pool_files:
                seen_pool_files.add(file)
                pool_files.append({"res": resource_name, "file": file})
        groups.append({
            "name": label,
            "display_name": display_name,
            "source": source,
            "position_file": position_file,
            "texcoord_file": texcoord_file,
            "position_stride": position_stride,
            "texcoord_stride": texcoord_stride,
            "texcoord_uv_off": DEFAULT_UV_OFFSET,
            "normal_source": group_normal_source,
            "ib_file": ib_file,
            "diffuse_file": diffuse_info.get("filename"),
            "diffuse_pool_files": pool_files,
            "index_size": index_size,
            "geometry_match": info.get("geometry_match_at_end"),
            "draws": draws,
            "_texture_override_index": texture_override_index,
        })
    return groups


__all__ = ["build_draw_groups"]

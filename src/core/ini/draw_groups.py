"""Assembly of resolved ``DrawCall`` records into component draw groups."""

import re

from ..geometry.buffers import DEFAULT_UV_OFFSET, POSITION_STRIDE, _res_get
from ..geometry.draw_call import AuthoredDrawCall, DrawCall, SlotTextureBinding
from ..geometry.identity import DrawOccurrence
from ..geometry.skinning import resolve_skinning_source
from .draw_resources import (
    _collect_resource_copy_sources, _extract_hash, _ib_index_size,
    _ib_res_to_component, _resolve_component_buffers, _resolve_normal_source,
    _select_draw_sections,
)
from .draw_scan import _scan_sections_for_draws
from .geometry_resolution import GeometryResolution, GeometryResolver
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


def _resource_filename_stem(value):
    normalized = str(value or "").strip().replace("\\", "/")
    filename = normalized.rsplit("/", 1)[-1]
    return filename.rsplit(".", 1)[0].casefold()


def _declared_vertex_vg_resources_for_blend(
        resources, blend_resource_name, blend_filename):
    """Return declared remaps strongly associated with one Blend resource."""
    blend_name = str(blend_resource_name or "").strip().casefold()
    blend_stem = _resource_filename_stem(blend_filename)
    evidence_marker = "blendremapvertexvg"
    remap_marker = "remapvertexvg"
    candidates = []
    def matches_association(candidate, anchor):
        stripped = candidate.replace(remap_marker, "", 1)
        return (stripped == anchor
                or candidate.startswith(anchor + remap_marker))

    for name, info in (resources or {}).items():
        if not isinstance(info, dict) or not info.get("filename"):
            continue
        resource_name = str(name).strip().casefold()
        filename_stem = _resource_filename_stem(info["filename"])
        if (evidence_marker not in resource_name
                and evidence_marker not in filename_stem):
            continue
        is_associated = (
            matches_association(resource_name, blend_name)
            or matches_association(filename_stem, blend_stem))
        if is_associated:
            candidates.append(name)
    return sorted(set(candidates), key=lambda value: str(value).casefold())


def build_draw_groups(sections, resources, var_prefix=None, source=None, seen=None,
                      gating_vars=None, mod_dir=None):
    """Build resolved component groups while preserving authored draw snapshots."""
    if seen is None:
        seen = {}
    def declared_vertex_vg_resources_for_blend(blend_resource_name,
                                                blend_filename):
        return _declared_vertex_vg_resources_for_blend(
            resources, blend_resource_name, blend_filename)
    section_info = _scan_sections_for_draws(sections, var_prefix, gating_vars)
    resource_copy_sources = _collect_resource_copy_sources(sections, resources)
    resolved_buffers = _resolve_component_buffers(
        section_info, resources, resource_copy_sources, sections=sections)
    resolve_vertex_info = resolved_buffers["resolve_vertex_info"]
    vertex_binding_index = resolved_buffers["vertex_binding_index"]
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
    geometry_resolver = GeometryResolver(
        section_info, resources, resource_copy_sources, vertex_binding_index,
        resolve_vertex_info, mod_dir=mod_dir,
        global_position=global_position, global_texcoord=global_texcoord)
    draw_sections = _select_draw_sections(section_info, global_ib)
    texture_override_index = getattr(
        section_info, "texture_override_index", TextureOverrideIndex())
    texture_override_index = texture_override_index.with_resource_files(resources)
    global_compute_resources = dict(
        getattr(section_info, "global_compute_resources", {}) or {})
    if not draw_sections:
        return []

    texture_file_cache = {}

    def resolve_texture_file(resource_name):
        if resource_name not in texture_file_cache:
            texture_file_cache[resource_name] = _res_get(
                resources, resource_name).get("filename")
        return texture_file_cache[resource_name]

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
        group_vertex_resources = {
            slot: resource
            for slot, resource in (
                info.get("vertex_resources_at_end") or {}).items()
            if resource
        }
        ib_info = _res_get(resources, ib_resource)
        diffuse_info = (_res_get(resources, info["diffuse"])
                        if info["diffuse"] else {})
        if not ib_resource or not ib_info.get("filename"):
            continue
        authored_draws = list(info["draws"]) or [AuthoredDrawCall(
            count=None, start=0, base=0, source=info["src"],
            occurrence=DrawOccurrence(section_name, None),
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
        resolved_for_group = []
        for number, authored in enumerate(authored_draws, 1):
            effective_ib = authored.index_resource or ib_resource
            effective_component = _ib_res_to_component(effective_ib)
            component_pair = lookup_component_buffers(effective_component) or {}
            texture_hash = (_extract_hash(section_name) or
                            _extract_hash(effective_ib))
            resolution = geometry_resolver.resolve(
                section_name, authored, effective_ib,
                legacy_position=component_pair.get("position"),
                legacy_texcoord=component_pair.get("texcoord"),
                component_position=_lookup_component_value(
                    component_positions, effective_component),
                component_texcoord=_lookup_component_value(
                    component_texcoords, effective_component),
                hash_position=hash_positions.get(texture_hash),
                hash_texcoord=hash_texcoords.get(texture_hash),
            )
            if resolution.ib_file and resolution.position_file and \
                    resolution.texcoord_file:
                resolved_for_group.append(resolution)
            draw = DrawCall(
                label=f"{label}-{number}", count=authored.count,
                start=authored.start, base=authored.base,
                conditions=authored.conditions,
                sources=[authored.source] if authored.source else [],
                occurrence=authored.occurrence,
                ib_file=resolution.ib_file,
                index_size=resolution.index_size,
                position_file=resolution.position_file,
                position_stride=resolution.position_stride,
                texcoord_file=resolution.texcoord_file,
                texcoord_stride=resolution.texcoord_stride,
                geometry_match=authored.geometry_match,
                skinning_bone_offset=authored.skinning_bone_offset,
                texture_provenance=dict(authored.texture_provenance),
                slot_textures=_resolve_slot_texture_files(
                    authored, resolve_texture_file),
            )
            vertex_resources = authored.vertex_resources
            effective_position_resource = resolution.position_resource
            effective_vertex_resources = dict(
                lookup_component_vertex_resources(
                    _ib_res_to_component(effective_ib)))
            effective_vertex_resources.update(group_vertex_resources)
            effective_vertex_resources.update(vertex_resources)
            draw.normal_source = _resolve_normal_source(
                effective_vertex_resources, resources, draw.position_file,
                draw.position_stride, resolve_vertex_info)
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
                "provenance_section_count": 0,
                "provenance_candidate_count": 0,
                "legacy_component_candidate_count": 0,
                "resolution_source": None,
            }
            skinning_source, skinning_error = resolve_skinning_source(
                direct_skinning_resources, resolve_vertex_info,
                bone_id_offset=authored.skinning_bone_offset,
                remap_resources=remap_resources,
                declared_vertex_vg_resources_for_blend=(
                    declared_vertex_vg_resources_for_blend))
            if skinning_source is not None:
                skinning_resolution["resolution_source"] = "direct"
            if skinning_source is None and skinning_error is None:
                provenance_bindings, provenance_sections = \
                    vertex_binding_index.provenance_bindings_for_position(
                        effective_position_resource,
                        root_section=section_name,
                        current_bindings=vertex_resources)
                provenance_bindings = [
                    (slot, resource)
                    for slot, resource in provenance_bindings
                    if slot not in vertex_resources
                ]
                provenance_candidates = provenance_bindings
                provenance_resources = {
                    resource for _slot, resource in provenance_bindings
                }
                skinning_resolution.update({
                    "provenance_section_count": len(provenance_sections),
                    "provenance_candidate_count": len(provenance_resources),
                })
                provenance_bindings = {}
                for _slot, resource in provenance_candidates:
                    synthetic_slot = len(provenance_bindings) + 2
                    provenance_bindings[synthetic_slot] = resource
                skinning_source, skinning_error = resolve_skinning_source(
                    provenance_bindings, resolve_vertex_info,
                    bone_id_offset=authored.skinning_bone_offset,
                    remap_resources=remap_resources,
                    declared_vertex_vg_resources_for_blend=(
                        declared_vertex_vg_resources_for_blend))
                if skinning_source is not None:
                    skinning_resolution["resolution_source"] = \
                        "position_provenance"
            if skinning_source is None and skinning_error is None:
                occupied_slots = set(vertex_resources)
                blend_fallback = {
                    slot: resource
                    for slot, resource in lookup_component_blend_vertex_resources(
                        _ib_res_to_component(effective_ib)).items()
                    if slot not in occupied_slots
                }
                skinning_resolution[
                    "legacy_component_candidate_count"] = len(blend_fallback)
                skinning_source, skinning_error = resolve_skinning_source(
                    blend_fallback, resolve_vertex_info,
                    bone_id_offset=authored.skinning_bone_offset,
                    remap_resources=remap_resources,
                    declared_vertex_vg_resources_for_blend=(
                        declared_vertex_vg_resources_for_blend))
                if skinning_source is not None:
                    skinning_resolution["resolution_source"] = \
                        "legacy_component"
            draw.skinning_source = skinning_source
            draw.skinning_error = skinning_error
            draw.skinning_resolution = skinning_resolution
            draw.geometry_resolution = {
                "source": resolution.source,
                "ib_resource": resolution.ib_resource,
                "position_resource": resolution.position_resource,
                "texcoord_resource": resolution.texcoord_resource,
                "position_candidate_count": resolution.evidence.get(
                    "position_candidate_count", 0),
                "texcoord_candidate_count": resolution.evidence.get(
                    "texcoord_candidate_count", 0),
                "coherence_selected": resolution.evidence.get(
                    "coherence_selected", False),
                "used_name_tiebreaker": resolution.evidence.get(
                    "used_name_tiebreaker", False),
                "error": resolution.error,
            }
            _apply_diffuse_state(draw, authored, resolve_texture_file)
            _apply_auxiliary_map_state(draw, authored, resolve_texture_file)
            draw.texture_provenance = {
                role: provenance
                for role, provenance in draw.texture_provenance.items()
                if draw.texture_default(role) or draw.texture_rules(role)
            }
            draws.append(draw)

        if not draws:
            continue
        group_resolution = (resolved_for_group[0]
                            if resolved_for_group else GeometryResolution(
                                ib_resource=ib_resource,
                                ib_file=ib_info.get("filename"),
                                index_size=_ib_index_size(
                                    ib_info.get("format"))))
        position_file = group_resolution.position_file
        texcoord_file = group_resolution.texcoord_file
        position_stride = group_resolution.position_stride
        texcoord_stride = group_resolution.texcoord_stride
        ib_file = group_resolution.ib_file
        index_size = group_resolution.index_size
        group_normal_source = _resolve_normal_source(
            group_vertex_resources, resources, position_file, position_stride,
            resolve_vertex_info)

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

"""Resolution of authored geometry resources into file-backed buffers."""

import re

from ..geometry.buffers import POSITION_STRIDE, _res_get
from ..geometry.vertex_attributes import VertexAttributeSource


def _ib_res_to_component(ib_res):
    value = re.sub(r"^Resource", "", ib_res or "", flags=re.I)
    value = re.sub(r"IB$", "", value, flags=re.I)
    return re.sub(r"[A-Z]$", "", value)


def _ib_index_size(fmt):
    """Bytes per index -- 3DMigoto index buffers are R16_UINT or R32_UINT."""
    return 2 if "R16" in (fmt or "").upper() else 4


def _extract_hash(name):
    """Return an 8-hex hash in a resource or section name, if present."""
    match = re.search(r"_([0-9a-f]{8})_", name, re.I)
    if match:
        return match.group(1).lower()
    match = re.search(r"[0-9a-f]{8}", name, re.I)
    return match.group(0).lower() if match else None


def _collect_resource_copy_sources(sections, resources):
    """Resolve explicit/rest-pose resource copy edges before group building."""
    resource_copy_sources = {}
    copy_re = re.compile(
        r"^\s*(Resource\S+)\s*=\s*copy(?:\s+ref)?\s+(Resource\S+)\s*$",
        re.I)
    for lines in sections.values():
        for raw in lines:
            line = raw.split(";", 1)[0].strip()
            match = copy_re.match(line)
            if not match:
                continue
            destination, copy_source = match.groups()
            if destination.lower() == copy_source.lower():
                continue
            sources = resource_copy_sources.setdefault(destination.lower(), [])
            if all(existing.lower() != copy_source.lower()
                   for existing in sources):
                sources.append(copy_source)

    cs_read_re = re.compile(
        r"^\s*cs-t([12])\s*=\s*(?:ref\s+)?(\S+)\s*$", re.I)
    cs_write_re = re.compile(
        r"^\s*cs-u0\s*=\s*(?:ref\s+)?(\S+)\s*$", re.I)
    for lines in sections.values():
        cs_inputs = {}
        for raw in lines:
            line = raw.split(";", 1)[0].strip()
            match = cs_read_re.match(line)
            if match:
                slot, resource_name = match.groups()
                if resource_name.lower() == "null":
                    cs_inputs.pop(slot, None)
                else:
                    cs_inputs[slot] = resource_name
                continue
            match = cs_write_re.match(line)
            if not match or match.group(1).lower() == "null":
                continue
            output = match.group(1)
            position = cs_inputs.get("1")
            blend = cs_inputs.get("2")
            if (position and blend
                    and _res_get(resources, position).get("filename")
                    and _res_get(resources, blend).get("stride") == 32):
                sources = resource_copy_sources.setdefault(output.lower(), [])
                if all(existing.lower() != position.lower()
                       for existing in sources):
                    sources.append(position)
    return resource_copy_sources


def _resolve_normal_source(effective_vertex_resources, resources,
                           position_file, position_stride,
                           resolve_vertex_info=None):
    """Recognize a supported authored-normal layout from effective bindings."""
    effective_vertex_resources = effective_vertex_resources or {}
    vector_resource = effective_vertex_resources.get(1)
    if vector_resource:
        vector_info = (resolve_vertex_info(vector_resource)
                       if resolve_vertex_info is not None
                       else _res_get(resources, vector_resource))
        vector_format = str(vector_info.get("format") or "").upper()
        if (vector_info.get("filename")
                and vector_info.get("stride") == 8
                and vector_format == "DXGI_FORMAT_R8G8B8A8_SNORM"):
            return VertexAttributeSource(
                file=vector_info["filename"], stride=8, offset=4,
                encoding="snorm8x3")

    if position_file and position_stride == POSITION_STRIDE:
        return VertexAttributeSource(
            file=position_file, stride=40, offset=12, encoding="f32x3")
    return None


def _select_draw_sections(section_info, global_ib):
    """Select TextureOverride sections that can produce viewer geometry."""
    return [(name, info) for name, info in section_info.items()
            if name.lower().startswith("textureoverride")
            and (info["ib"] or global_ib)
            and (info["draws"] or (info["ib"] and not info["handling_skip"]))]


def _resolve_component_buffers(section_info, resources, resource_copy_sources):
    """Resolve component, hash, and WWMI global buffer bindings."""
    vertex_info_cache = {}

    def resolve_vertex_info(resource_name, visiting=None):
        if not resource_name:
            return {}
        cache_key = resource_name.lower()
        if cache_key in vertex_info_cache:
            return vertex_info_cache[cache_key]

        resource_info = _res_get(resources, resource_name)
        if resource_info.get("filename"):
            vertex_info_cache[cache_key] = resource_info
            return resource_info

        visiting = set(visiting or ())
        if cache_key in visiting:
            return {}
        visiting.add(cache_key)
        candidates = list(resource_copy_sources.get(cache_key, ()))
        # WWMI binds the remapped blend buffer through a reusable runtime
        # resource named ``ResourceBlendBufferOverride``.  The resource is
        # intentionally empty in the INI because the command list fills it
        # with a runtime copy, while the source descriptor remains the
        # authored ``ResourceBlendBuffer``.  Keep this fallback limited to
        # blend resources so unrelated override resources are not guessed.
        if (cache_key.endswith("blendbufferoverride")
                and not candidates):
            candidates.append(resource_name[:-len("Override")])
        if not cache_key.endswith(".b"):
            candidates.append(resource_name + ".B")
        for candidate in candidates:
            resolved = resolve_vertex_info(candidate, visiting)
            if resolved.get("filename"):
                vertex_info_cache[cache_key] = resolved
                return resolved

        vertex_info_cache[cache_key] = {}
        return {}

    component_positions, component_texcoords = {}, {}
    component_vertex_resources = {}
    component_blend_vertex_resources = {}
    hash_positions, hash_texcoords = {}, {}

    for name, info in section_info.items():
        if not name.lower().startswith("textureoverride"):
            continue
        base = name[len("TextureOverride"):]
        component_name = None
        component_suffix = None
        for suffix in ("Blend", "Position", "Texcoord"):
            if base.lower().endswith(suffix.lower()):
                component_name = base[:-len(suffix)]
                component_suffix = suffix
                break
        if component_name is not None:
            resources_for_component = component_vertex_resources.setdefault(
                component_name.lower(), {})
            for slot, resource in (
                    info.get("vertex_resources_at_end") or {}).items():
                if resource is not None:
                    resources_for_component.setdefault(slot, resource)
            if component_suffix == "Blend":
                blend_resources = component_blend_vertex_resources.setdefault(
                    component_name.lower(), {})
                for slot, resource in (
                        info.get("vertex_resources_at_end") or {}).items():
                    if resource is not None:
                        blend_resources.setdefault(slot, resource)
        if base.lower().endswith("texcoord"):
            component = base[:-len("Texcoord")]
            if info["vb1"]:
                component_texcoords[component.lower()] = info["vb1"]

    for name, info in section_info.items():
        if not name.lower().startswith("textureoverride"):
            continue
        base = name[len("TextureOverride"):]
        if base.lower().endswith("blend"):
            component = base[:-len("Blend")]
            component_key = component.lower()
            if info["vb0"] and component_key not in component_positions:
                component_positions[component_key] = info["vb0"]
            if (info["vb1"] and component_key not in component_texcoords
                    and _res_get(resources, info["vb1"]).get("stride", 0) != 32):
                component_texcoords[component_key] = info["vb1"]
        elif base.lower().endswith("position"):
            component = base[:-len("Position")]
            component_key = component.lower()
            if info["vb0"] and component_key not in component_positions:
                component_positions[component_key] = info["vb0"]

        texture_hash = _extract_hash(name)
        if texture_hash:
            if info["vb0"] and texture_hash not in hash_positions:
                hash_positions[texture_hash] = info["vb0"]
            vb2_stride = (_res_get(resources, info["vb2"]).get("stride", 0)
                          if info["vb2"] else 0)
            texcoord = ((info["vb2"] if info["vb2"] and vb2_stride != 32
                         else None) or info["vb1"])
            if texcoord and texture_hash not in hash_texcoords:
                hash_texcoords[texture_hash] = texcoord

    component_buffers = {
        component: {
            "position": component_positions[component],
            "texcoord": component_texcoords[component],
        }
        for component in component_positions if component in component_texcoords
    }

    global_ib, global_position, global_texcoord = None, None, None
    for name, info in section_info.items():
        if not name.lower().startswith("commandlist"):
            continue
        if info["ib"] and not global_ib:
            global_ib = info["ib"]
        if info["vb0"] and not global_position:
            global_position = info["vb0"]
        texcoord = info["vb2"] or info["vb1"]
        if texcoord and not global_texcoord:
            global_texcoord = texcoord

    if global_position and not _res_get(resources, global_position).get("filename"):
        for resource_name, resource_info in resources.items():
            fmt = resource_info.get("format", "")
            if (resource_info.get("filename")
                    and "R32G32B32" in fmt):
                global_position = resource_name
                break

    return {
        "resolve_vertex_info": resolve_vertex_info,
        "component_buffers": component_buffers,
        "component_positions": component_positions,
        "component_texcoords": component_texcoords,
        "component_vertex_resources": component_vertex_resources,
        "component_blend_vertex_resources": component_blend_vertex_resources,
        "hash_positions": hash_positions,
        "hash_texcoords": hash_texcoords,
        "global_ib": global_ib,
        "global_position": global_position,
        "global_texcoord": global_texcoord,
    }


__all__ = [
    "_ib_res_to_component", "_ib_index_size", "_extract_hash",
    "_collect_resource_copy_sources", "_resolve_normal_source",
    "_resolve_component_buffers", "_select_draw_sections",
]

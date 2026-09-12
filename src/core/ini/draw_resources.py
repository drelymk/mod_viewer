"""Resolution of authored geometry resources into file-backed buffers."""

from dataclasses import dataclass, field
import re

from ..geometry.buffers import POSITION_STRIDE, _res_get
from ..geometry.vertex_attributes import VertexAttributeSource
from .draw_scan import (_reachable_execution_sections, _run_target_name)


@dataclass
class VertexBindingIndex:
    """Reverse references between authored vertex bindings and INI scopes.

    This is deliberately smaller than an execution graph.  The draw scanner
    already captures effective per-draw state; this index only retains enough
    authored scope information to answer which vertex resources were bound
    alongside a resolved Position resource.
    """

    sections: dict
    section_lookup: dict
    section_bindings: dict
    section_binding_conditionals: dict
    section_draw_bindings: dict
    section_runs: dict
    resource_consumers: dict
    resource_copy_sources: dict
    resource_copy_neighbors: dict
    _position_roots_cache: dict = field(default_factory=dict, init=False,
                                        repr=False)
    _scope_sections_cache: dict = field(default_factory=dict, init=False,
                                        repr=False)

    def _resource_connected(self, start, target):
        """Return whether explicit authored resource edges connect two names."""
        if not start or not target:
            return False
        target_key = str(target).casefold()
        pending = [str(start)]
        visited = set()
        while pending:
            current = pending.pop()
            current_key = current.casefold()
            if current_key in visited:
                continue
            visited.add(current_key)
            if current_key == target_key:
                return True
            pending.extend(self.resource_copy_neighbors.get(current_key, ()))
        return False

    def _scope_sections(self, root):
        cached = self._scope_sections_cache.get(root)
        if cached is not None:
            return cached
        if root not in self.sections:
            result = (root,)
        else:
            result = _reachable_execution_sections(
                self.sections, root, self.section_lookup)
        self._scope_sections_cache[root] = result
        return result

    def _position_roots(self, position_resource):
        key = str(position_resource).casefold()
        cached = self._position_roots_cache.get(key)
        if cached is not None:
            return cached
        roots = set()
        for bound_resource, consumers in self.resource_consumers.items():
            if self._resource_connected(bound_resource, position_resource):
                roots.update(
                    consumer for consumer in consumers
                    if str(consumer).lower().startswith("textureoverride"))
        self._position_roots_cache[key] = roots
        return roots

    def provenance_for_position(self, position_resource, *, root_section=None,
                                current_bindings=None):
        """Return co-bound resources and their authored provenance sections."""
        bindings, provenance_sections = self.provenance_bindings_for_position(
            position_resource, root_section=root_section,
            current_bindings=current_bindings)
        return {resource for _slot, resource in bindings}, provenance_sections

    def provenance_bindings_for_position(
            self, position_resource, *, root_section=None,
            current_bindings=None):
        """Return co-bound ``(slot, resource)`` pairs with provenance."""
        if not position_resource:
            return [], set()

        roots = self._position_roots(position_resource)

        candidates = []
        provenance_sections = set()
        for root in roots:
            if self.section_binding_conditionals.get(root, False):
                # A conditional binding anywhere in a TextureOverride's
                # execution closure makes the complete root unsafe as static
                # provenance evidence.
                continue
            if root != root_section and root in self.section_draw_bindings:
                # A different draw root has its own execution state. Its
                # command-list closure must not leak into this draw's
                # provenance candidates.
                continue
            for section_name in self._scope_sections(root):
                # A draw-producing root contains multiple execution snapshots.
                # Unless it is the exact target draw, those snapshots are not
                # safe provenance evidence because their conditions and order
                # may not match the target draw.
                if (section_name != root_section
                        and section_name in self.section_draw_bindings):
                    continue
                if self.section_binding_conditionals.get(section_name, False):
                    continue
                if (section_name == root
                        and section_name in self.section_draw_bindings):
                    if section_name == root_section and current_bindings is not None:
                        scopes = (current_bindings,)
                    else:
                        scopes = self.section_draw_bindings[section_name]
                else:
                    scopes = (self.section_bindings.get(section_name, {}),)
                for bindings in scopes:
                    for slot, resource in bindings.items():
                        if not resource:
                            continue
                        if self._resource_connected(resource, position_resource):
                            continue
                        candidates.append((slot, resource))
                        provenance_sections.add(section_name)
        return candidates, provenance_sections


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


def _build_vertex_binding_index(section_info, sections,
                                resource_copy_sources):
    """Build the small reverse index used by skinning provenance lookup."""
    section_lookup = {str(name).lower(): name for name in sections}
    section_bindings = {}
    section_binding_conditionals = {}
    section_draw_bindings = {}
    section_runs = {}
    resource_consumers = {}
    resource_copy_neighbors = {}
    for destination, sources in resource_copy_sources.items():
        destination_key = str(destination).casefold()
        for source in sources:
            source_key = str(source).casefold()
            resource_copy_neighbors.setdefault(destination_key, set()).add(
                source)
            resource_copy_neighbors.setdefault(source_key, set()).add(
                destination)

    for name, info in section_info.items():
        bindings = dict(info.get("vertex_resources_at_end") or {})
        section_bindings[name] = bindings
        section_binding_conditionals[name] = bool(
            info.get("vertex_bindings_conditional"))
        draw_bindings = [
            dict(draw.vertex_resources)
            for draw in info.get("draws", ())
        ]
        if draw_bindings:
            section_draw_bindings[name] = draw_bindings
        for resource in bindings.values():
            if resource:
                resource_consumers.setdefault(
                    str(resource).casefold(), set()).add(name)
        for snapshot in draw_bindings:
            for resource in snapshot.values():
                if resource:
                    resource_consumers.setdefault(
                        str(resource).casefold(), set()).add(name)

        runs = []
        for raw in sections.get(name, ()):
            target_name = _run_target_name(raw, section_lookup)
            if target_name and target_name not in runs:
                runs.append(target_name)
        section_runs[name] = tuple(runs)

    return VertexBindingIndex(
        sections=sections,
        section_lookup=section_lookup,
        section_bindings=section_bindings,
        section_binding_conditionals=section_binding_conditionals,
        section_draw_bindings=section_draw_bindings,
        section_runs=section_runs,
        resource_consumers=resource_consumers,
        resource_copy_sources=resource_copy_sources,
        resource_copy_neighbors=resource_copy_neighbors,
    )


def _select_draw_sections(section_info, global_ib):
    """Select TextureOverride sections that can produce viewer geometry."""
    return [(name, info) for name, info in section_info.items()
            if name.lower().startswith("textureoverride")
            and (info["ib"] or global_ib)
            and (info["draws"] or (info["ib"] and not info["handling_skip"]))]


def _resolve_component_buffers(section_info, resources, resource_copy_sources,
                               sections=None):
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
        "vertex_binding_index": _build_vertex_binding_index(
            section_info, sections or {},
            resource_copy_sources),
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
    "VertexBindingIndex",
]

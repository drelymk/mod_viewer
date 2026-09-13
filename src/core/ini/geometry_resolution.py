"""Evidence-based association of indexed geometry and vertex streams.

The INI format does not assign semantic meaning to resource or section names.
This module therefore treats names as compatibility evidence only.  Effective
draw state and explicit resource relationships are considered before structural
and file-backed evidence, and an unresolved association is left unresolved
instead of borrowing another component's buffers.
"""

from dataclasses import dataclass, field
import math
import os
import re
import struct

from ..geometry.buffers import POSITION_STRIDE, _detect_uv_best, _res_get
from ..resource_paths import safe_resource_path


@dataclass(frozen=True)
class GeometryResourceEvidence:
    resource: str
    file: str | None
    stride: int | None
    format: str | None
    bound_slots: tuple[int, ...] = ()
    bound_sections: tuple[str, ...] = ()
    copy_neighbors: tuple[str, ...] = ()
    record_count: int | None = None
    declared_counts: tuple[int, ...] = ()


@dataclass
class GeometryResolution:
    ib_resource: str | None = None
    position_resource: str | None = None
    texcoord_resource: str | None = None
    ib_file: str | None = None
    position_file: str | None = None
    texcoord_file: str | None = None
    index_size: int | None = None
    position_stride: int | None = None
    texcoord_stride: int | None = None
    source: str | None = None
    error: str | None = None
    evidence: dict = field(default_factory=dict)

    @property
    def resolved(self):
        return bool(self.ib_file and self.position_file and
                    self.texcoord_file)


@dataclass
class _Candidate:
    resource: str
    info: GeometryResourceEvidence
    score: int = 0
    structural: bool = False
    name_tiebreaker: bool = False
    provenance: bool = False
    exact_count: bool = False
    uv_plausible: bool = True
    coherence: float | None = None


def _lookup_component_value(mapping, component):
    mapping = mapping or {}
    component = str(component or "")
    candidates = (
        component,
        # Keep the historical component matching as compatibility evidence,
        # but never require it for a candidate to participate.
        re.sub(r"[A-Za-z]+$", "", component),
        re.sub(r"(?<=.)[A-Z][a-z]+$", "", component),
    )
    for candidate in candidates:
        if candidate and mapping.get(candidate.lower()):
            return mapping[candidate.lower()]
    component_low = component.lower()
    prefix = max(
        (key for key in mapping if component_low.startswith(key)),
        key=len, default=None)
    return mapping.get(prefix) if prefix else None


class GeometryResolver:
    """Resolve one authored draw from progressively weaker evidence."""

    def __init__(self, section_info, resources, resource_copy_sources,
                 vertex_binding_index, resolve_vertex_info, *, mod_dir=None,
                 global_position=None, global_texcoord=None):
        self.section_info = section_info
        self.resources = resources
        self.resource_copy_sources = resource_copy_sources or {}
        self.vertex_binding_index = vertex_binding_index
        self.resolve_vertex_info = resolve_vertex_info
        self.mod_dir = mod_dir
        self.global_position = global_position
        self.global_texcoord = global_texcoord
        self._catalog = {}
        self._resource_cache = {}
        self._record_count_cache = {}
        self._index_bounds_cache = {}
        self._scope_bounds_cache = {}
        self._coherence_cache = {}
        self._uv_cache = {}
        self._build_catalog()

    def _build_catalog(self):
        records = {}

        def add(resource, slot, section, counts=()):
            if not resource:
                return
            key = str(resource).casefold()
            item = records.setdefault(key, {
                "resource": resource, "slots": set(), "sections": set(),
                "counts": set(),
            })
            if slot is not None:
                item["slots"].add(int(slot))
            if section:
                item["sections"].add(section)
            item["counts"].update(int(value) for value in counts or ()
                                  if value is not None)

        for section, info in self.section_info.items():
            counts = info.get("vertex_count_evidence") or ()
            for slot, resource in (info.get("vertex_resources_at_end") or
                                   {}).items():
                add(resource, slot, section, counts)
            for authored in info.get("draws", ()):
                for slot, resource in authored.vertex_resources.items():
                    add(resource, slot, section, counts)

        for item in records.values():
            resource = item["resource"]
            resolved = self._resolved_info(resource)
            self._catalog[item["resource"].casefold()] = {
                "resource": resource,
                "slots": frozenset(item["slots"]),
                "sections": frozenset(item["sections"]),
                "counts": tuple(sorted(item["counts"])),
                "info": resolved,
            }

    def _resolved_info(self, resource):
        if not resource:
            return {}
        key = str(resource).casefold()
        if key not in self._resource_cache:
            self._resource_cache[key] = dict(
                self.resolve_vertex_info(resource) or {})
        return self._resource_cache[key]

    def _record_count(self, filename, stride):
        if not self.mod_dir or not filename or not stride or stride <= 0:
            return None
        path = safe_resource_path(self.mod_dir, filename)
        if not path or not os.path.isfile(path):
            return None
        key = (os.path.normcase(path), int(stride))
        if key not in self._record_count_cache:
            try:
                self._record_count_cache[key] = os.path.getsize(path) // stride
            except OSError:
                self._record_count_cache[key] = None
        return self._record_count_cache[key]

    def _evidence(self, resource):
        if not resource:
            return GeometryResourceEvidence(resource="", file=None,
                                            stride=None, format=None)
        item = self._catalog.get(str(resource).casefold())
        info = self._resolved_info(resource)
        if item is None:
            item = {"slots": frozenset(), "sections": frozenset(),
                    "counts": ()}
        filename = info.get("filename")
        stride = info.get("stride")
        return GeometryResourceEvidence(
            resource=resource,
            file=filename,
            stride=stride,
            format=info.get("format"),
            bound_slots=tuple(sorted(item["slots"])),
            bound_sections=tuple(sorted(item["sections"], key=str.casefold)),
            copy_neighbors=tuple(sorted(
                self.vertex_binding_index.resource_copy_neighbors.get(
                    str(resource).casefold(), ()), key=str.casefold)),
            record_count=self._record_count(filename, stride),
            declared_counts=tuple(item["counts"]),
        )

    def _index_bounds(self, filename, start, count, base, index_size):
        """Read only the requested index range and return effective bounds."""
        if not self.mod_dir or not filename:
            return None
        path = safe_resource_path(self.mod_dir, filename)
        if not path or not os.path.isfile(path) or index_size not in (2, 4):
            return None
        key = (os.path.normcase(path), start, count, base, index_size)
        if key in self._index_bounds_cache:
            return self._index_bounds_cache[key]
        if start < 0:
            result = (-1, -1, False)
            self._index_bounds_cache[key] = result
            return result
        try:
            total = os.path.getsize(path) // index_size
            requested = total - start if count is None else max(0, count)
            available = max(0, min(requested, total - start))
            complete = available == requested
            if available <= 0:
                result = (-1, -1, complete)
            else:
                fmt = "H" if index_size == 2 else "I"
                with open(path, "rb") as handle:
                    handle.seek(start * index_size)
                    data = handle.read(available * index_size)
                values = struct.unpack(
                    f"<{available}{fmt}", data[:available * index_size])
                effective = [value + base for value in values]
                result = (min(effective), max(effective), complete)
        except (OSError, struct.error):
            result = None
        self._index_bounds_cache[key] = result
        return result

    def _draw_scope_bounds(self, section_name, ib_resource, filename,
                           index_size):
        """Return bounds across all authored draws using one effective IB.

        A split-resource mod can contain a small first submesh whose indices
        are also valid against an unrelated, smaller vertex stream.  When the
        same indexed replacement contains later submeshes, resolving the
        stream against the complete draw scope prevents that legal-looking
        first range from selecting the wrong family.
        """
        key = (section_name, str(ib_resource or '').casefold(), index_size)
        if key in self._scope_bounds_cache:
            return self._scope_bounds_cache[key]
        info = self.section_info.get(section_name) or {}
        draws = info.get("draws") or ()
        matching = []
        target = str(ib_resource or '').casefold()
        for authored in draws:
            effective = str(authored.index_resource or '').casefold()
            if effective != target:
                continue
            matching.append(authored)
        if len(matching) < 2:
            self._scope_bounds_cache[key] = None
            return None
        bounds = []
        for authored in matching:
            item = self._index_bounds(
                filename, authored.start, authored.count, authored.base,
                index_size)
            if item is None or not item[2]:
                self._scope_bounds_cache[key] = None
                return None
            bounds.append(item)
        result = (
            min(item[0] for item in bounds),
            max(item[1] for item in bounds),
            True,
        )
        self._scope_bounds_cache[key] = result
        return result

    def _position_coherence(self, evidence, geometry):
        """Return a scale-normalized sampled edge-coherence score."""
        if not self.mod_dir or not evidence.file or not evidence.stride:
            return None
        ib_file, start, count, base, index_size = geometry
        if not ib_file or evidence.stride < 12 or index_size not in (2, 4):
            return None
        ib_path = safe_resource_path(self.mod_dir, ib_file)
        position_path = safe_resource_path(self.mod_dir, evidence.file)
        if (not ib_path or not position_path or
                not os.path.isfile(ib_path) or
                not os.path.isfile(position_path)):
            return None
        key = (os.path.normcase(ib_path), start, count, base, index_size,
               os.path.normcase(position_path), int(evidence.stride))
        if key in self._coherence_cache:
            return self._coherence_cache[key]
        try:
            total_indices = os.path.getsize(ib_path) // index_size
            if start < 0 or start >= total_indices:
                result = None
            else:
                requested = (total_indices - start
                             if count is None else max(0, count))
                available = min(requested, total_indices - start)
                triangle_count = available // 3
                if triangle_count <= 0:
                    result = None
                else:
                    sample_count = min(256, triangle_count)
                    fmt = "<3H" if index_size == 2 else "<3I"
                    triangles = []
                    with open(ib_path, "rb") as ib_handle:
                        for sample in range(sample_count):
                            triangle = (sample * (triangle_count - 1) /
                                        max(1, sample_count - 1))
                            offset = start + int(round(triangle)) * 3
                            ib_handle.seek(offset * index_size)
                            data = ib_handle.read(3 * index_size)
                            if len(data) != 3 * index_size:
                                continue
                            triangles.append(tuple(value + base for value in
                                                   struct.unpack(fmt, data)))
                    edges = []
                    with open(position_path, "rb") as position_handle:
                        for triangle in triangles:
                            points = []
                            valid = True
                            for vertex in triangle:
                                if vertex < 0:
                                    valid = False
                                    break
                                position_handle.seek(vertex * evidence.stride)
                                data = position_handle.read(12)
                                if len(data) != 12:
                                    valid = False
                                    break
                                point = struct.unpack("<3f", data)
                                if not all(math.isfinite(value)
                                           for value in point):
                                    valid = False
                                    break
                                points.append(point)
                            if not valid:
                                continue
                            edges.extend((
                                math.dist(points[0], points[1]),
                                math.dist(points[1], points[2]),
                                math.dist(points[2], points[0]),
                            ))
                    if not edges:
                        result = None
                    else:
                        ordered = sorted(edges)
                        median = ordered[len(ordered) // 2]
                        if not math.isfinite(median) or median <= 1e-8:
                            result = None
                        else:
                            p95 = ordered[min(
                                len(ordered) - 1,
                                int(math.ceil(len(ordered) * 0.95)) - 1)]
                            outlier_ratio = sum(
                                edge > median * 4 for edge in edges) / len(edges)
                            result = p95 / median + outlier_ratio * 10
        except (OSError, struct.error, ValueError):
            result = None
        self._coherence_cache[key] = result
        return result

    def _uv_plausible(self, evidence):
        if evidence.stride == 32:
            return False
        if not evidence.stride:
            return True
        if not self.mod_dir or not evidence.file:
            return True
        path = safe_resource_path(self.mod_dir, evidence.file)
        if not path or not os.path.isfile(path):
            return True
        key = (os.path.normcase(path), evidence.stride)
        if key in self._uv_cache:
            return self._uv_cache[key]
        try:
            with open(path, "rb") as handle:
                data = handle.read(256 * 1024)
            if not data or not evidence.stride:
                result = False
            else:
                offset, fmt = _detect_uv_best(
                    path, evidence.stride, data=data)
                size = struct.calcsize(fmt)
                total = len(data) // evidence.stride
                sampled = 0
                in_range = 0
                for index in range(0, total, max(1, total // 256)):
                    location = index * evidence.stride + offset
                    if location + size > len(data):
                        break
                    u, v = struct.unpack_from(fmt, data, location)
                    sampled += 1
                    if (-0.01 <= u <= 2.0 and -0.01 <= v <= 2.0):
                        in_range += 1
                result = bool(sampled and in_range / sampled >= 0.95)
        except (OSError, ValueError, struct.error):
            result = True
        self._uv_cache[key] = result
        return result

    def _candidate(self, resource, *, structural=False,
                   name_tiebreaker=False, provenance=False):
        if not resource:
            return None
        evidence = self._evidence(resource)
        if (not evidence.file or
                (evidence.stride is not None and evidence.stride <= 0)):
            return None
        return _Candidate(
            resource=evidence.resource, info=evidence,
            structural=structural, name_tiebreaker=name_tiebreaker,
            provenance=provenance)

    @staticmethod
    def _unique_resources(values):
        result = {}
        for value in values:
            if value:
                result.setdefault(str(value).casefold(), value)
        return list(result.values())

    def _is_connected_to_current(self, resource, current_bindings):
        for current in (current_bindings or {}).values():
            if (current and str(current).casefold() !=
                    str(resource).casefold() and
                    self.vertex_binding_index._resource_connected(
                    resource, current)):
                return True
        return False

    def _position_candidates(self, authored, *, legacy_position=None,
                             hash_position=None, component_position=None):
        explicit = authored.vertex_resources
        if 0 in explicit:
            if explicit[0] is None:
                return [], True
            candidate = self._candidate(
                explicit[0],
                provenance=self._is_connected_to_current(
                    explicit[0], explicit))
            # Runtime-produced vertex resources can be declared without a
            # file.  In that case retain the safe legacy structural candidates
            # rather than turning an otherwise resolvable draw into a hole.
            if candidate is not None:
                return [candidate], True

        resources = []
        for item in self._catalog.values():
            if 0 in item["slots"]:
                resources.append(item["resource"])
        resources.extend((legacy_position, component_position, hash_position))
        # A runtime-created shared resource can be observed at vb0 without a
        # file.  In that case the component catalog contains only a
        # non-resolvable placeholder, while _resolve_component_buffers may
        # already have found the one file-backed structural position stream.
        # Use that source only when no file-backed candidate is present; an
        # unrelated file-backed candidate must not be hidden by a global one.
        if (self.global_position and not any(
                self._resolved_info(resource).get("filename")
                for resource in resources if resource)):
            resources.append(self.global_position)
        candidates = []
        for resource in self._unique_resources(resources):
            candidates.append(self._candidate(
                resource,
                structural=(resource.casefold() == str(hash_position or "").casefold()),
                name_tiebreaker=(resource.casefold() == str(
                    legacy_position or component_position or "").casefold()),
                provenance=self._is_connected_to_current(
                    resource, explicit)))
        return [item for item in candidates if item is not None], False

    def _texcoord_candidates(self, authored, *, legacy_texcoord=None,
                             hash_texcoord=None, component_texcoord=None,
                             selected_position=None):
        explicit = authored.vertex_resources
        explicit_slots = [slot for slot in (2, 1) if slot in explicit]
        other_explicit_slots = [slot for slot in sorted(explicit)
                               if slot > 2 and explicit[slot] is not None]
        if explicit_slots:
            resources = []
            for slot in explicit_slots:
                resource = explicit[slot]
                if resource is None:
                    continue
                candidate = self._candidate(resource)
                if candidate is not None and candidate.info.stride != 32:
                    # Higher slots are preferred, but do not let an
                    # unsupported blend-like stream hide a valid lower slot.
                    resources = [resource]
                    break
                resources.append(resource)
            if not resources:
                return [], True
        elif other_explicit_slots:
            # Some generated layouts bind UVs outside the conventional 1/2
            # slots.  Effective draw state still outranks inferred catalog
            # candidates when the resource metadata identifies a file.
            resources = [explicit[slot] for slot in other_explicit_slots]
        else:
            resources = [
                item["resource"] for item in self._catalog.values()
                if item["slots"] and not (
                    selected_position and item["resource"].casefold() ==
                    selected_position.resource.casefold())
            ]
            resources.extend((legacy_texcoord, component_texcoord,
                              hash_texcoord))
            if not resources and self.global_texcoord:
                resources.append(self.global_texcoord)
        candidates = []
        for resource in self._unique_resources(resources):
            candidate = self._candidate(
                resource,
                structural=(resource.casefold() == str(
                    hash_texcoord or "").casefold()),
                name_tiebreaker=(resource.casefold() == str(
                    legacy_texcoord or component_texcoord or "").casefold()),
                provenance=self._is_connected_to_current(
                    resource, explicit))
            if candidate is None:
                continue
            candidate.uv_plausible = self._uv_plausible(candidate.info)
            if not candidate.uv_plausible:
                continue
            if (selected_position and candidate.info.record_count is not None
                    and selected_position.info.record_count is not None
                    and candidate.info.record_count !=
                    selected_position.info.record_count):
                # Keep the candidate as weak evidence, but prefer matching
                # domains strongly below instead of dropping valid variants.
                pass
            candidates.append(candidate)
        return candidates, bool(explicit_slots or other_explicit_slots)

    def _choose(self, candidates, *, bounds, role, selected_position=None,
                geometry=None):
        compatible = []
        for candidate in candidates:
            if role == "texcoord" and not candidate.uv_plausible:
                continue
            if role == "position" and candidate.info.stride == 32:
                # A stride-32 stream is the known GIMI blend layout.  It can
                # still be selected when explicitly bound at vb0; inferred
                # position candidates should not steal it from geometry.
                if 0 not in candidate.info.bound_slots:
                    continue
            if role == "position" and bounds is not None:
                _minimum, maximum, complete = bounds
                if not complete or maximum < 0:
                    continue
                if (candidate.info.record_count is not None and
                        maximum >= candidate.info.record_count):
                    continue
            candidate.score = 0
            if candidate.provenance:
                candidate.score += 500
            if candidate.structural:
                candidate.score += 300
            if candidate.exact_count:
                candidate.score += 400
            if role == "texcoord" and selected_position:
                if (candidate.info.record_count is not None and
                        selected_position.info.record_count is not None and
                        candidate.info.record_count ==
                        selected_position.info.record_count):
                    candidate.score += 250
            if role == "texcoord":
                if 2 in candidate.info.bound_slots:
                    candidate.score += 100
                elif 1 in candidate.info.bound_slots:
                    candidate.score += 80
            if 0 in candidate.info.bound_slots and role == "position":
                candidate.score += 100
            if candidate.name_tiebreaker:
                candidate.score += 10
            compatible.append(candidate)

        if not compatible:
            return (None, "invalid_geometry_binding" if bounds is not None
                    else "unresolved_geometry", False)

        coherence_candidates = []
        if role == "position" and geometry and len(compatible) > 1:
            for candidate in compatible:
                candidate.coherence = self._position_coherence(
                    candidate.info, geometry)
                if candidate.coherence is not None:
                    coherence_candidates.append(candidate)
            if len(coherence_candidates) >= 2:
                best_coherence = min(
                    candidate.coherence for candidate in coherence_candidates)
                best = [candidate for candidate in coherence_candidates
                        if candidate.coherence == best_coherence]
                if len(best) == 1:
                    return best[0], None, True
        # A declared count is useful only within the smallest compatible
        # vertex domain.  Split-resource INIs commonly declare the vertex
        # count for several unrelated components, so rewarding a candidate
        # merely because its own declaration matches its file would let an
        # unrelated larger stream outrank the smallest indexed-valid domain.
        recorded = [candidate for candidate in compatible
                    if candidate.info.record_count is not None]
        smallest_count = min(
            (candidate.info.record_count for candidate in recorded),
            default=None)
        declared_matches = [
            candidate for candidate in compatible
            if role == "position"
            and candidate.info.record_count == smallest_count
            and any(
                count == candidate.info.record_count
                for count in candidate.info.declared_counts)
        ]
        if len(declared_matches) == 1:
            declared_matches[0].exact_count = True
            declared_matches[0].score += 400
        best_score = max(item.score for item in compatible)
        best = [item for item in compatible if item.score == best_score]
        if len(best) > 1:
            with_counts = [item for item in best
                           if item.info.record_count is not None]
            if with_counts:
                smallest = min(item.info.record_count for item in with_counts)
                smallest_items = [item for item in with_counts
                                  if item.info.record_count == smallest]
                if len(smallest_items) == 1:
                    return smallest_items[0], None, False
            return None, f"ambiguous_{role}", False
        return best[0], None, False

    def resolve(self, section_name, authored, ib_resource, *,
                legacy_position=None, legacy_texcoord=None,
                component_position=None, component_texcoord=None,
                hash_position=None, hash_texcoord=None):
        result = GeometryResolution(ib_resource=ib_resource)
        ib_info = _res_get(self.resources, ib_resource)
        result.ib_file = ib_info.get("filename")
        result.index_size = 2 if "R16" in str(
            ib_info.get("format") or "").upper() else 4
        if not ib_resource or not result.ib_file:
            result.error = "invalid_geometry_binding"
            return result

        bounds = self._index_bounds(
            result.ib_file, authored.start, authored.count,
            authored.base, result.index_size)
        scope_bounds = self._draw_scope_bounds(
            section_name, ib_resource, result.ib_file, result.index_size)
        selection_bounds = scope_bounds or bounds
        positions, explicit_position = self._position_candidates(
            authored, legacy_position=legacy_position,
            component_position=component_position,
            hash_position=hash_position)
        result.evidence["position_candidate_count"] = len(positions)
        if explicit_position and authored.vertex_resources.get(0) is None:
            result.error = "invalid_geometry_binding"
            return result
        position, error, coherence_selected = self._choose(
            positions, bounds=selection_bounds, role="position",
            geometry=(result.ib_file, authored.start, authored.count,
                      authored.base, result.index_size))
        if position is None:
            result.error = error
            return result

        texcoords, explicit_texcoord = self._texcoord_candidates(
            authored, legacy_texcoord=legacy_texcoord,
            component_texcoord=component_texcoord,
            hash_texcoord=hash_texcoord, selected_position=position)
        result.evidence["texcoord_candidate_count"] = len(texcoords)
        texcoord, tc_error, _ = self._choose(
            texcoords, bounds=None, role="texcoord",
            selected_position=position)
        if texcoord is None:
            result.error = tc_error
            return result

        result.position_resource = position.resource
        result.position_file = position.info.file
        result.position_stride = position.info.stride or POSITION_STRIDE
        result.texcoord_resource = texcoord.resource
        result.texcoord_file = texcoord.info.file
        result.texcoord_stride = texcoord.info.stride or 20
        result.evidence.update({
            "ib_resource": ib_resource,
            "position_resource": position.resource,
            "texcoord_resource": texcoord.resource,
            "coherence_selected": coherence_selected,
            "used_name_tiebreaker": bool(
                not coherence_selected and
                (position.name_tiebreaker or texcoord.name_tiebreaker)),
            "position_record_count": position.info.record_count,
            "texcoord_record_count": texcoord.info.record_count,
        })
        if explicit_position and explicit_texcoord:
            result.source = "direct"
        elif position.provenance or texcoord.provenance:
            result.source = "copy_provenance"
        elif not coherence_selected and (
                position.name_tiebreaker or texcoord.name_tiebreaker):
            result.source = "name_tiebreaker"
        else:
            result.source = "buffer_compatibility"
        return result


__all__ = [
    "GeometryResourceEvidence", "GeometryResolution", "GeometryResolver",
]

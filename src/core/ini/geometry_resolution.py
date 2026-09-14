"""Target-aware association of indexed geometry and vertex streams.

3DMigoto's original geometry hash is the useful identity boundary for a
replacement family. Resource names, section names, index coverage and
vertex-file shape remain evidence, but they are not allowed to discover a
family across unrelated geometry targets.
"""

from dataclasses import dataclass, field
import math
import os
import re
import struct

from ..geometry.buffers import POSITION_STRIDE, _detect_uv_best, _res_get
from ..geometry.draw_call import VertexBindingEvidence
from ..resource_paths import safe_resource_path
from .draw_scan import _reachable_execution_sections


@dataclass(frozen=True, slots=True)
class GeometryTargetKey:
    """Identity of one original geometry target within an INI analysis."""

    source: str | None
    hash: str
    match_priority: int | None = None

    def to_dict(self):
        return {
            "source": self.source,
            "hash": self.hash,
            "match_priority": self.match_priority,
        }


@dataclass
class GeometryTarget:
    """All authored replacement evidence belonging to one target hash."""

    key: GeometryTargetKey
    sections: set[str] = field(default_factory=set)
    indexed_sections: set[str] = field(default_factory=set)
    vertex_binding_sections: set[str] = field(default_factory=set)
    ib_resources: set[str] = field(default_factory=set)
    position_resources: set[str] = field(default_factory=set)
    texcoord_resources: set[str] = field(default_factory=set)
    vertex_bindings: list[VertexBindingEvidence] = field(default_factory=list)
    declared_counts: set[int] = field(default_factory=set)
    captured_sources: dict[str, set[int]] = field(default_factory=dict)


class GeometryTargetIndex:
    """INI-wide index whose candidate collections remain target-local."""

    def __init__(self, section_info, sections, *, source=None):
        self.section_info = section_info
        self.sections = sections or {}
        self.source = source
        self.targets = {}
        self.section_targets = {}
        self._build()

    def _key_for_info(self, info):
        geometry_hash = info.get("_geometry_hash")
        if not geometry_hash:
            match = info.get("geometry_match_at_end")
            geometry_hash = getattr(match, "hash", None)
        if not geometry_hash:
            return None
        return GeometryTargetKey(
            source=self.source,
            hash=str(geometry_hash).casefold(),
            match_priority=info.get("match_priority"),
        )

    @staticmethod
    def _event_key(event):
        source = event.source or {}
        return (
            event.section, event.slot, event.resource, event.target_hash,
            event.match_first_index, event.match_index_count,
            event.conditions, event.execution_path, event.order,
            source.get("ini_path"), source.get("line_no"),
        )

    def _build(self):
        for section, info in self.section_info.items():
            if not str(section).casefold().startswith("textureoverride"):
                continue
            key = self._key_for_info(info)
            if key is None:
                continue
            target = self.targets.setdefault(key, GeometryTarget(key))
            self.section_targets[section] = key
            target.sections.add(section)
            target.declared_counts.update(
                int(value) for value in info.get("vertex_count_evidence") or ()
                if value is not None)
            if info.get("ib") or info.get("draws"):
                target.indexed_sections.add(section)
            for draw in info.get("draws") or ():
                if draw.index_resource:
                    target.ib_resources.add(draw.index_resource)
            if info.get("ib"):
                target.ib_resources.add(info["ib"])

            seen_events = {self._event_key(event)
                           for event in target.vertex_bindings}
            for event in info.get("vertex_binding_events") or ():
                if self._event_key(event) in seen_events:
                    continue
                seen_events.add(self._event_key(event))
                target.vertex_bindings.append(event)
                target.vertex_binding_sections.add(event.section)
                if not event.resource:
                    continue
                if event.slot == 0:
                    target.position_resources.add(event.resource)
                else:
                    target.texcoord_resources.add(event.resource)

        section_lookup = {str(name).casefold(): name
                          for name in self.sections}
        for root, key in self.section_targets.items():
            reachable = _reachable_execution_sections(
                self.sections, root, section_lookup)
            target = self.targets[key]
            for section in reachable:
                for raw in self.sections.get(section, ()):
                    line = raw.split(";", 1)[0].strip()
                    match = re.match(
                        r"^\s*(Resource\S+)\s*=\s*copy\s+vb(\d+)\s*$",
                        line, re.I)
                    if not match:
                        continue
                    resource, slot = match.groups()
                    target.captured_sources.setdefault(
                        resource.casefold(), set()).add(int(slot))

    def target_for_section(self, section):
        return self.section_targets.get(section)

    def get(self, key):
        return self.targets.get(key)

    def capture_slots(self, key, resource):
        target = self.targets.get(key)
        if target is None or not resource:
            return frozenset()
        return frozenset(target.captured_sources.get(
            str(resource).casefold(), ()))

    def diagnostics(self, key):
        target = self.targets.get(key)
        if target is None:
            return {"target": None}
        return {
            "target": target.key.to_dict(),
            "target_sections": sorted(target.sections, key=str.casefold),
            "target_ib_count": len(target.ib_resources),
            "target_position_resource_count": len(target.position_resources),
            "target_texcoord_resource_count": len(target.texcoord_resources),
        }


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
    target_local: bool = False
    name_tiebreaker: bool = False
    provenance: bool = False
    exact_count: bool = False
    uv_plausible: bool = True
    coherence: float | None = None
    source_kind: str | None = None
    declared_counts: tuple[int, ...] = ()


class GeometryResolver:
    """Resolve a draw from direct state, target evidence, and safe fallback."""

    def __init__(self, section_info, resources, resource_copy_sources,
                 vertex_binding_index, resolve_vertex_info, *, mod_dir=None,
                 target_index=None, resource_lineage_kinds=None,
                 global_position=None, global_texcoord=None):
        self.section_info = section_info
        self.resources = resources
        self.resource_copy_sources = resource_copy_sources or {}
        self.vertex_binding_index = vertex_binding_index
        self.resolve_vertex_info = resolve_vertex_info
        self.mod_dir = mod_dir
        self.target_index = target_index or GeometryTargetIndex(
            section_info, {}, source=None)
        self.resource_lineage_kinds = resource_lineage_kinds or {}
        # Kept as constructor compatibility for callers that still provide
        # these values. Runtime resources are never resolved from them.
        self.global_position = global_position
        self.global_texcoord = global_texcoord
        self._catalog = {}
        self._resource_cache = {}
        self._record_count_cache = {}
        self._index_bounds_cache = {}
        self._coherence_cache = {}
        self._uv_cache = {}
        self._target_geometry_cache = {}
        self._scope_bounds_cache = {}
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
            for event in info.get("vertex_binding_events") or ():
                add(event.resource, event.slot, section, counts)
            for authored in info.get("draws", ()):
                for slot, resource in authored.vertex_resources.items():
                    add(resource, slot, section, counts)

        for item in records.values():
            resource = item["resource"]
            self._catalog[item["resource"].casefold()] = {
                "resource": resource,
                "slots": frozenset(item["slots"]),
                "sections": frozenset(item["sections"]),
                "counts": tuple(sorted(item["counts"])),
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
                not os.path.isfile(ib_path) or not os.path.isfile(position_path)):
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
        format_name = str(evidence.format or "").upper()
        if (("UINT" in format_name or "SINT" in format_name or
             "SNORM" in format_name) and "FLOAT" not in format_name):
            return False
        if ("B8A8" in format_name or "B16A16" in format_name or
                "B32A32" in format_name):
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
            if not data:
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

    def _candidate(self, resource, *, structural=False, target_local=False,
                   name_tiebreaker=False, provenance=False,
                   declared_counts=None, source_kind=None):
        if not resource:
            return None
        evidence = self._evidence(resource)
        if (not evidence.file or
                (evidence.stride is not None and evidence.stride <= 0)):
            return None
        raw_info = _res_get(self.resources, resource)
        lineage_kind = self.resource_lineage_kinds.get(
            str(resource).casefold())
        if lineage_kind is None and not raw_info.get("filename"):
            lineage_kind = "exact" if evidence.file else None
        if lineage_kind:
            provenance = True
            if source_kind in (None, "direct"):
                source_kind = lineage_kind
        return _Candidate(
            resource=evidence.resource, info=evidence,
            structural=structural, target_local=target_local,
            name_tiebreaker=name_tiebreaker, provenance=provenance,
            source_kind=source_kind,
            declared_counts=tuple(
                evidence.declared_counts if declared_counts is None
                else declared_counts))

    @staticmethod
    def _unique_resources(values):
        result = {}
        for value in values:
            if value:
                result.setdefault(str(value).casefold(), value)
        return list(result.values())

    @staticmethod
    def _dedupe_candidates(candidates):
        """Collapse aliases that resolve to the same physical vertex family."""
        result = {}
        for candidate in candidates:
            key = (
                str(candidate.info.file).casefold(), candidate.info.stride,
                str(candidate.info.format or "").casefold())
            previous = result.get(key)
            if previous is None:
                result[key] = candidate
                continue
            previous_rank = (
                bool(previous.target_local), bool(previous.provenance),
                not bool(previous.source_kind))
            current_rank = (
                bool(candidate.target_local), bool(candidate.provenance),
                not bool(candidate.source_kind))
            if current_rank > previous_rank:
                result[key] = candidate
        return list(result.values())

    def _is_connected_to_current(self, resource, current_bindings):
        for current in (current_bindings or {}).values():
            if (current and str(current).casefold() !=
                    str(resource).casefold() and
                    self.vertex_binding_index._resource_connected(
                        resource, current)):
                return True
        return False

    def _target_candidates(self, section_name, role):
        key = self.target_index.target_for_section(section_name)
        if key is None:
            return []
        cached = self._target_geometry_cache.setdefault(key, {})
        if role in cached:
            return list(cached[role])
        target = self.target_index.get(key)
        resources = (target.position_resources if role == "position"
                     else target.texcoord_resources)
        candidates = []
        for resource in self._unique_resources(sorted(
                resources, key=lambda value: str(value).casefold())):
            candidate = self._candidate(
                resource, structural=True, target_local=True,
                declared_counts=tuple(sorted(target.declared_counts)),
                source_kind="target_binding")
            if candidate is None:
                continue
            if role == "texcoord":
                candidate.uv_plausible = self._uv_plausible(candidate.info)
                if not candidate.uv_plausible:
                    continue
            candidates.append(candidate)
        candidates = self._dedupe_candidates(candidates)
        cached[role] = tuple(candidates)
        return list(candidates)

    def _scope_bounds(self, section_name, ib_resource, ib_file, index_size):
        """Return complete bounds for one section's authored IB scope."""
        key = (section_name, str(ib_resource or "").casefold(), index_size)
        if key in self._scope_bounds_cache:
            return self._scope_bounds_cache[key]
        info = self.section_info.get(section_name) or {}
        draws = [draw for draw in info.get("draws") or ()
                 if str(draw.index_resource or "").casefold() ==
                 str(ib_resource or "").casefold()]
        if not draws:
            self._scope_bounds_cache[key] = None
            return None
        bounds = []
        for draw in draws:
            item = self._index_bounds(
                ib_file, draw.start, draw.count, draw.base, index_size)
            if item is None or not item[2] or item[1] < 0:
                self._scope_bounds_cache[key] = None
                return None
            bounds.append(item)
        result = (min(item[0] for item in bounds),
                  max(item[1] for item in bounds), True)
        self._scope_bounds_cache[key] = result
        return result

    def _cross_target_candidates(self, role):
        """Collect target families for the narrowly-proven fallback path."""
        candidates = []
        for target in self.target_index.targets.values():
            resources = (target.position_resources if role == "position"
                         else target.texcoord_resources)
            for resource in self._unique_resources(sorted(
                    resources, key=lambda value: str(value).casefold())):
                candidate = self._candidate(
                    resource, structural=False, target_local=False,
                    declared_counts=(),
                    source_kind="cross_target")
                if candidate is None:
                    continue
                if role == "texcoord":
                    candidate.uv_plausible = self._uv_plausible(candidate.info)
                    if not candidate.uv_plausible:
                        continue
                candidates.append(candidate)
        return self._dedupe_candidates(candidates)

    def _legacy_candidates(self, resources, *, role, name_resources=()):
        candidates = []
        name_keys = {str(item).casefold() for item in name_resources if item}
        for resource in self._unique_resources(resources):
            candidate = self._candidate(
                resource, name_tiebreaker=(str(resource).casefold() in
                                           name_keys),
                source_kind="legacy_compatibility")
            if candidate is None:
                continue
            if role == "texcoord":
                candidate.uv_plausible = self._uv_plausible(candidate.info)
                if not candidate.uv_plausible:
                    continue
            candidates.append(candidate)
        return self._dedupe_candidates(candidates)

    def _unscoped_unique_candidates(self, role):
        """Return a unique legacy family only when no target exists."""
        candidates = []
        for item in self._catalog.values():
            resource = item["resource"]
            candidate = self._candidate(
                resource, source_kind="legacy_unique")
            if candidate is None:
                continue
            if role == "position":
                if candidate.info.stride == 32 or 0 not in candidate.info.bound_slots:
                    continue
            else:
                if not candidate.info.bound_slots:
                    continue
                candidate.uv_plausible = self._uv_plausible(candidate.info)
                if not candidate.uv_plausible:
                    continue
            candidates.append(candidate)
        return self._dedupe_candidates(candidates)

    def _position_candidates(self, section_name, authored, *,
                             legacy_position=None, hash_position=None,
                             component_position=None):
        explicit = authored.vertex_resources
        if 0 in explicit:
            resource = explicit[0]
            if resource is None:
                return [], "explicit_null"
            candidate = self._candidate(
                resource,
                provenance=self._is_connected_to_current(resource, explicit),
                source_kind="direct")
            if candidate is not None:
                return [candidate], "direct"
            # A runtime resource without a file may still be the effective
            # target binding. It is only filled from target-local evidence.
            candidates = self._target_candidates(section_name, "position")
            target_key = self.target_index.target_for_section(section_name)
            if self.target_index.capture_slots(target_key, resource) & {0}:
                for item in candidates:
                    item.provenance = True
                    item.source_kind = "captured_slot"
            return candidates, "runtime"

        candidates = self._target_candidates(section_name, "position")
        if candidates:
            return candidates, "target_binding"
        if self.target_index.target_for_section(section_name) is not None:
            return [], "target_binding"
        return self._legacy_candidates(
            (legacy_position, component_position, hash_position),
            role="position", name_resources=(legacy_position,
                                               component_position)), "legacy"

    def _texcoord_candidates(self, section_name, authored, *,
                             legacy_texcoord=None, hash_texcoord=None,
                             component_texcoord=None, selected_position=None):
        explicit = authored.vertex_resources
        explicit_slots = [slot for slot in (2, 1) if slot in explicit]
        other_explicit_slots = [slot for slot in sorted(explicit)
                                if slot > 2]
        if explicit_slots or other_explicit_slots:
            resources = []
            for slot in explicit_slots + other_explicit_slots:
                resource = explicit[slot]
                if resource is not None:
                    resources.append(resource)
            candidates = []
            for resource in self._unique_resources(resources):
                candidate = self._candidate(resource, source_kind="direct")
                if candidate is None:
                    continue
                candidate.uv_plausible = self._uv_plausible(candidate.info)
                if candidate.uv_plausible:
                    candidates.append(candidate)
            if candidates:
                return self._dedupe_candidates(candidates), "direct"
            # Explicit runtime slots do not authorize a global search.
            target_candidates = self._target_candidates(
                section_name, "texcoord")
            target_key = self.target_index.target_for_section(section_name)
            for slot in explicit_slots + other_explicit_slots:
                resource = explicit[slot]
                if resource is None:
                    continue
                if self.target_index.capture_slots(target_key, resource) & {
                        1, 2}:
                    for item in target_candidates:
                        item.provenance = True
                        item.source_kind = "captured_slot"
            return target_candidates, "runtime"

        candidates = self._target_candidates(section_name, "texcoord")
        if candidates:
            return candidates, "target_binding"
        if self.target_index.target_for_section(section_name) is not None:
            return [], "target_binding"
        return self._legacy_candidates(
            (legacy_texcoord, component_texcoord, hash_texcoord),
            role="texcoord", name_resources=(legacy_texcoord,
                                              component_texcoord)), "legacy"

    def _choose(self, candidates, *, bounds, role, selected_position=None,
                geometry=None):
        compatible = []
        for candidate in candidates:
            if role == "texcoord" and not candidate.uv_plausible:
                continue
            if role == "position" and candidate.info.stride == 32:
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
            if candidate.target_local:
                candidate.score += 300
            if candidate.structural:
                candidate.score += 200
            if role == "texcoord" and selected_position:
                if (candidate.info.record_count is not None and
                        selected_position.info.record_count is not None and
                        candidate.info.record_count ==
                        selected_position.info.record_count):
                    candidate.score += 120
            if role == "position" and 0 in candidate.info.bound_slots:
                candidate.score += 30
            if candidate.name_tiebreaker:
                candidate.score += 10
            compatible.append(candidate)

        if not compatible:
            return (None, "invalid_geometry_binding"
                    if bounds is not None and candidates
                    else f"unresolved_{role}", False)

        declared_matches = [
            candidate for candidate in compatible
            if candidate.info.record_count is not None and any(
                count == candidate.info.record_count
                for count in candidate.declared_counts)
        ]
        if len(declared_matches) == 1:
            declared_matches[0].exact_count = True
            declared_matches[0].score += 100

        best_score = max(item.score for item in compatible)
        best = [item for item in compatible if item.score == best_score]
        if len(best) > 1 and role == "position" and geometry:
            measured = []
            for candidate in best:
                candidate.coherence = self._position_coherence(
                    candidate.info, geometry)
                if candidate.coherence is not None:
                    measured.append(candidate)
            if measured:
                best_coherence = min(item.coherence for item in measured)
                coherent = [item for item in measured
                            if item.coherence == best_coherence]
                if len(coherent) == 1:
                    return coherent[0], None, True
        if len(best) != 1:
            return None, f"ambiguous_{role}", False
        return best[0], None, False

    def _legacy_fallback(self, resources, *, role, bounds, geometry,
                         selected_position=None):
        candidates = self._legacy_candidates(resources, role=role)
        if not candidates:
            return None, None, False
        return self._choose(
            candidates, bounds=bounds, role=role,
            selected_position=selected_position, geometry=geometry)

    def resolve(self, section_name, authored, ib_resource, *,
                legacy_position=None, legacy_texcoord=None,
                component_position=None, component_texcoord=None,
                hash_position=None, hash_texcoord=None):
        result = GeometryResolution(ib_resource=ib_resource)
        target_key = self.target_index.target_for_section(section_name)
        result.evidence.update(self.target_index.diagnostics(target_key))
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
        geometry = (result.ib_file, authored.start, authored.count,
                    authored.base, result.index_size)
        positions, position_mode = self._position_candidates(
            section_name, authored, legacy_position=legacy_position,
            component_position=component_position,
            hash_position=hash_position)
        if not positions and position_mode == "legacy":
            positions = self._unscoped_unique_candidates("position")
            if positions:
                position_mode = "legacy_unique"
        result.evidence["position_candidate_count"] = len(positions)
        if position_mode == "explicit_null":
            result.error = "invalid_geometry_binding"
            return result
        position, error, coherence_selected = self._choose(
            positions, bounds=bounds, role="position", geometry=geometry)
        if position is None and position_mode in ("target_binding", "runtime"):
            fallback_position, fallback_error, fallback_coherence = \
                self._legacy_fallback(
                (legacy_position, component_position, hash_position),
                role="position", bounds=bounds, geometry=geometry)
            if fallback_position is not None:
                position = fallback_position
                error = fallback_error
                coherence_selected = fallback_coherence
                position_mode = "legacy"
        if position is None and position_mode == "target_binding":
            scope_bounds = self._scope_bounds(
                section_name, ib_resource, result.ib_file, result.index_size)
            if scope_bounds is not None:
                cross_positions = self._cross_target_candidates("position")
                cross_position, cross_error, cross_coherence = self._choose(
                    cross_positions, bounds=scope_bounds, role="position",
                    geometry=geometry)
                result.evidence["cross_target_candidate_count"] = len(
                    cross_positions)
                if cross_position is not None:
                    position = cross_position
                    positions = [cross_position]
                    position_mode = "cross_target"
                    coherence_selected = cross_coherence
                else:
                    error = cross_error or error
        if position is None:
            result.error = ("unresolved_runtime_position"
                            if position_mode == "runtime" else error)
            result.evidence["position_resolution"] = {
                "source": position_mode,
                "candidate_count": len(positions),
            }
            return result

        texcoords, texcoord_mode = self._texcoord_candidates(
            section_name, authored, legacy_texcoord=legacy_texcoord,
            component_texcoord=component_texcoord,
            hash_texcoord=hash_texcoord, selected_position=position)
        if not texcoords and texcoord_mode == "legacy":
            texcoords = self._unscoped_unique_candidates("texcoord")
            if texcoords:
                texcoord_mode = "legacy_unique"
        result.evidence["texcoord_candidate_count"] = len(texcoords)
        texcoord, tc_error, _ = self._choose(
            texcoords, bounds=None, role="texcoord",
            selected_position=position)
        if texcoord is None and texcoord_mode in ("target_binding", "runtime"):
            fallback_texcoord, fallback_error, _ = self._legacy_fallback(
                (legacy_texcoord, component_texcoord, hash_texcoord),
                role="texcoord", bounds=None, geometry=None,
                selected_position=position)
            if fallback_texcoord is not None:
                texcoord = fallback_texcoord
                tc_error = fallback_error
                texcoord_mode = "legacy"
        if texcoord is None and texcoord_mode == "target_binding":
            cross_texcoords = self._cross_target_candidates("texcoord")
            if cross_texcoords:
                texcoords = cross_texcoords
                texcoord_mode = "cross_target"
                result.evidence["texcoord_candidate_count"] = len(
                    cross_texcoords)
                texcoord, tc_error, _ = self._choose(
                    texcoords, bounds=None, role="texcoord",
                    selected_position=position)
        if texcoord is None:
            result.error = tc_error
            result.evidence["texcoord_resolution"] = {
                "source": texcoord_mode,
                "candidate_count": len(texcoords),
            }
            return result

        explicit_position_resource = authored.vertex_resources.get(0)
        explicit_texcoord_resource = next(
            (authored.vertex_resources[slot]
             for slot in (2, 1)
             if slot in authored.vertex_resources
             and authored.vertex_resources[slot] is not None), None)
        result.position_resource = (
            explicit_position_resource
            if position_mode == "runtime" and explicit_position_resource
            else position.resource)
        result.position_file = position.info.file
        result.position_stride = position.info.stride or POSITION_STRIDE
        result.texcoord_resource = (
            explicit_texcoord_resource
            if texcoord_mode == "runtime" and explicit_texcoord_resource
            else texcoord.resource)
        result.texcoord_file = texcoord.info.file
        result.texcoord_stride = texcoord.info.stride or 20
        result.evidence.update({
            "ib_resource": ib_resource,
            "position_resource": result.position_resource,
            "texcoord_resource": result.texcoord_resource,
            "position_resolution": {
                "source": position.source_kind or position_mode,
                "candidate_count": len(positions),
            },
            "texcoord_resolution": {
                "source": texcoord.source_kind or texcoord_mode,
                "candidate_count": len(texcoords),
            },
            "coherence_selected": coherence_selected,
            "used_name_tiebreaker": bool(
                not coherence_selected and
                (position.name_tiebreaker or texcoord.name_tiebreaker)),
            "position_record_count": position.info.record_count,
            "texcoord_record_count": texcoord.info.record_count,
        })
        if position.source_kind == "captured_slot" or \
                texcoord.source_kind == "captured_slot":
            result.source = "captured_slot"
        elif position.source_kind in ("transformed", "transform_lineage") or \
                texcoord.source_kind in ("transformed", "transform_lineage"):
            result.source = "transform_lineage"
        elif position_mode == "direct" and texcoord_mode == "direct":
            result.source = "direct"
        elif position_mode == "cross_target" or texcoord_mode == "cross_target":
            result.source = "cross_target"
        elif position_mode == "target_binding" or texcoord_mode == "target_binding":
            result.source = "target_binding"
        elif position_mode in ("legacy", "legacy_unique") or \
                texcoord_mode in ("legacy", "legacy_unique"):
            result.source = "legacy_compatibility"
        elif coherence_selected:
            result.source = "target_binding_coherence"
        else:
            result.source = "buffer_compatibility"
        return result


__all__ = [
    "GeometryTargetKey", "GeometryTarget", "GeometryTargetIndex",
    "GeometryResourceEvidence", "GeometryResolution", "GeometryResolver",
]

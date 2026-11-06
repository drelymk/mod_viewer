"""Recover GIMI face placement from captured object and skinning matrices."""

from dataclasses import asdict
import hashlib
import math
import struct

from ..budget import ResourceLimit
from ..core import Unsupported
from ..geometry import codec, stream_layout


_IDENTITY = ((1., 0., 0., 0.), (0., 1., 0., 0.),
             (0., 0., 1., 0.), (0., 0., 0., 1.))
# Observed head-local axis convention; translation must come from the capture.
_FACE_BASIS = ((0., 0., -1.), (-1., 0., 0.), (0., 1., 0.))
_PLACEMENT_TOLERANCE = 1e-4


def _multiply(a, b):
    return tuple(tuple(sum(a[i][k] * b[k][j] for k in range(4))
                       for j in range(4)) for i in range(4))


def _difference(a, b):
    return max(abs(x - y) for left, right in zip(a, b) for x, y in zip(left, right))


def _inverse(matrix):
    rows = [list(row) + list(identity) for row, identity in zip(matrix, _IDENTITY)]
    for column in range(4):
        pivot = max(range(column, 4), key=lambda i: abs(rows[i][column]))
        if abs(rows[pivot][column]) < 1e-10:
            raise Unsupported("Singular captured matrix")
        rows[column], rows[pivot] = rows[pivot], rows[column]
        divisor = rows[column][column]
        rows[column] = [value / divisor for value in rows[column]]
        for i in range(4):
            if i != column:
                factor = rows[i][column]
                rows[i] = [a - factor * b for a, b in zip(rows[i], rows[column])]
    return tuple(tuple(row[4:]) for row in rows)


def _point(matrix, point):
    return tuple(sum(row[a] * point[a] for a in range(3)) + row[3]
                 for row in matrix[:3])


def _basis_error(matrix):
    return _difference([row[:3] for row in matrix[:3]], _FACE_BASIS)


class _Capture:
    def __init__(self, inv, calls, context):
        self.inv, self.context = inv, context
        self.calls = {call.id: call for call in calls if call.kind.startswith("Draw")}
        self.objects = {}

    def read(self, name, sources):
        data = self.inv.read_bytes(name)
        sources[name] = dict(file=name, sha256=hashlib.sha256(data).hexdigest())
        return data

    def object(self, call):
        if call.id in self.objects:
            return self.objects[call.id]
        candidates, sources = [], {}
        for slot, resource in call.bindings.get("vs-cb", {}).items():
            try:
                name = self.inv.find(call.id, f"vs-cb{slot}", resource)
                if self.inv.path(name).stat().st_size != 160:
                    continue
                self.context.budget.allocate(4096)
                values = struct.unpack("<40f", self.read(name, sources))
                if not all(math.isfinite(v) for v in values):
                    continue
                matrix = tuple(tuple(values[j * 4 + i] for j in range(4)) for i in range(4))
                inverse = tuple(tuple(values[16 + j * 4 + i] for j in range(4)) for i in range(4))
                if matrix[3] != _IDENTITY[3] or inverse[3] != _IDENTITY[3]:
                    continue
                error = max(_difference(_multiply(matrix, inverse), _IDENTITY),
                            _difference(_multiply(inverse, matrix), _IDENTITY))
                if error > .001:
                    continue
                _inverse(matrix)
                candidates.append((matrix, dict(draw=call.id, slot=slot, shader=call.shaders.get("vs", ""),
                                                resource=asdict(resource), file=name, inverse_error=error)))
            except ResourceLimit:
                raise
            except (ValueError, OSError, KeyError, struct.error):
                continue
        unique = []
        for candidate in candidates:
            if not any(_difference(candidate[0], other[0]) < 1e-7 for other in unique):
                unique.append(candidate)
        result = (unique[0] if len(unique) == 1 else None, sources)
        self.objects[call.id] = result
        return result

    def skinned_output(self, draw, count, sources):
        """Read one optional output snapshot without discarding later draws."""
        try:
            resource = draw.bindings.get("vb", {}).get(0)
            if resource is None:
                return None
            name = self.inv.find(draw.id, "vb0", resource)
            self.read(name.replace(".buf", ".txt"), sources)
            header, fields = self.inv.header(name.replace(".buf", ".txt"))
            stride = int(header.get("stride", 0))
            declared = [f for f in fields if f.get("SemanticName") == "POSITION"
                        and f.get("SemanticIndex") == "0" and f.get("InputSlot") == "0"]
            if (stride < 12 or len(declared) != 1
                    or declared[0].get("AlignedByteOffset") != "0"
                    or declared[0].get("Format", "").removeprefix("DXGI_FORMAT_") != "R32G32B32_FLOAT"):
                return None
            data = self.read(name, sources)
            # Allocation tails are capacity, not additional written vertices.
            if (len(data) < count * stride or len(data) % stride
                    or int(header.get("first vertex", 0)) != 0
                    or not 0 <= int(header.get("vertex count", -1)) <= len(data) // stride):
                return None
            return resource, name, stride, data
        except ResourceLimit:
            raise
        except (ValueError, OSError, KeyError, struct.error):
            return None

    def native(self, component):
        """Validate palette semantics against the actual stream-output positions."""
        producer = self.calls.get(component.provenance.get("producer"))
        if producer is None or 0 not in producer.bindings.get("vs-t", {}):
            return None
        sources = {}
        resource = producer.bindings["vs-t"][0]
        palette_name = self.inv.find(producer.id, "vs-t0", resource)
        size = self.inv.path(palette_name).stat().st_size
        if not size or size % 48 or size > 4096 * 48:
            return None
        data = self.read(palette_name, sources)
        self.context.budget.allocate(size * 16)
        values = tuple(struct.iter_unpack("<12f", data))
        if not all(math.isfinite(v) for row in values for v in row):
            return None
        palette = tuple(tuple(tuple(row[i:i + 4]) for i in (0, 4, 8)) + (_IDENTITY[3],)
                        for row in values)
        position, blend = (component.streams[role] for role in ("position", "blend"))
        for stream in (position, blend):
            self.read(stream["file"].replace(".buf", ".txt"), sources)
        stride, _, _, _ = stream_layout(self.inv, position["file"], position["slot"], "position")
        blend_stride, fields, _, _ = stream_layout(self.inv, blend["file"], blend["slot"], "blend")
        source = self.read(position["file"], sources)
        weights = self.read(blend["file"], sources)
        count = component.vertex_count
        if (len(source) != stride * count or len(weights) != blend_stride * count
                or int(producer.args.get("VertexCount", -1)) != count
                or int(producer.args.get("StartVertexLocation", 0)) != 0):
            return None
        decoders = [(field, codec(field.format)[1]) for field in fields]
        self.context.budget.allocate(count * 512)
        influences, used = [], set()
        for i in range(count):
            row = {field.semantic: decode(weights, i * blend_stride + field.offset)
                   for field, decode in decoders}
            ids = row["BLENDINDICES"]
            amounts = row.get("BLENDWEIGHTS", (1.,))
            pairs = [(bone, amount) for bone, amount in zip(ids, amounts) if amount > 0]
            if (len(ids) != len(amounts) or any(bone < 0 or bone >= len(palette)
                                              for bone, _ in pairs)):
                return None
            influences.append(pairs)
            used.update(bone for bone, _ in pairs)
        draws = [self.calls[d] for d in sorted({d for p in component.parts for d in p.draws})
                 if d in self.calls]
        verified = None
        for draw in draws:
            snapshot = self.skinned_output(draw, count, sources)
            if snapshot is None:
                continue
            target_resource, target_name, target_stride, target = snapshot
            error = 0.
            for i, pairs in enumerate(influences):
                if i % 1024 == 0:
                    self.context.tick("Validating captured face placement", i, count)
                point = struct.unpack_from("<3f", source, i * stride)
                actual = struct.unpack_from("<3f", target, i * target_stride)
                predicted = tuple(sum(amount * _point(palette[bone], point)[a]
                                      for bone, amount in pairs) for a in range(3))
                difference = max(abs(a - b) for a, b in zip(actual, predicted))
                if not all(math.isfinite(v) for v in (*actual, *predicted)) or difference > 1e-5:
                    break
                error = max(error, difference)
            else:
                verified = dict(draw=draw.id, file=target_name, resource=asdict(target_resource),
                                vertex_count=count, max_error=error)
                break
        if verified is None:
            return None
        inverses = []
        for bone in sorted(used):
            try:
                inverses.append((bone, _inverse(palette[bone])))
            except Unsupported:
                continue
        objects = []
        for draw in draws:
            candidate, dependencies = self.object(draw)
            sources.update(dependencies)
            if candidate and not any(_difference(candidate[0], other[0]) < 1e-7 for other in objects):
                objects.append(candidate)
        return dict(component=component.id, inverses=inverses, objects=objects, sources=sources,
                    palette=dict(draw=producer.id, slot=0, file=palette_name, resource=asdict(resource)),
                    replay=verified)


def placement_resolver(inv, calls, components, context):
    """Share native replay validation across candidate selection and final families."""
    capture = _Capture(inv, calls, context)
    natives = None

    def resolve(members):
        nonlocal natives
        if natives is None:
            natives = []
            for component in components:
                if component.status != "included" or not component.provenance.get("producer"):
                    continue
                try:
                    native = capture.native(component)
                    if native:
                        natives.append(native)
                except ResourceLimit:
                    raise
                except (ValueError, OSError, KeyError, struct.error):
                    continue
        matches, sources, failure = [], {}, "Missing or ambiguous captured face placement"
        draws = sorted({d for c in members for p in c.parts for d in p.draws})
        for draw_id in draws:
            call = capture.calls.get(draw_id)
            face, dependencies = capture.object(call) if call else (None, {})
            sources.update(dependencies)
            candidates = []
            if face:
                for native in natives:
                    for body in native["objects"]:
                        relative = _multiply(_inverse(body[0]), face[0])
                        for bone, inverse in native["inverses"]:
                            matrix = _multiply(inverse, relative)
                            if _basis_error(matrix) <= _PLACEMENT_TOLERANCE:
                                sources.update(native["sources"])
                                context.budget.allocate(2048)
                                candidates.append(dict(matrix=matrix, bone=bone, component=native["component"],
                                                       face=face[1], body=body[1], palette=native["palette"],
                                                       replay=native["replay"], basis_error=_basis_error(matrix)))
            if not candidates:
                break
            matches.extend(candidates)
            if any(_difference(match["matrix"], matches[0]["matrix"]) > _PLACEMENT_TOLERANCE
                   for match in matches):
                failure = "Captured matrices yield conflicting face placements"
                break
        else:
            failure = "" if matches else failure
        placement = dict(version=1, method="captured_matrices", status="unresolved", reason=failure)
        if not failure:
            best = min(matches, key=lambda match: match["basis_error"])
            placement.update(status="proven", matrix=best["matrix"], matches=matches,
                             sources=list(sources.values()))
            placement.pop("reason")
        return placement

    return resolve


def record_placements(inv, calls, components, context, *, resolve=None):
    """Require one placement across every captured draw of a proven face family."""
    families = {}
    for component in components:
        family = component.provenance.get("gimi_face_family")
        if family and component.provenance.get("geometry_kind") == "direct":
            families.setdefault(family, []).append(component)
    if not families:
        return
    resolve = resolve or placement_resolver(inv, calls, components, context)
    for members in families.values():
        placement = resolve(members)
        for component in members:
            component.provenance["gimi_face_placement"] = placement


def apply_placement(mesh, inv, context, *, validated_sources=None):
    """Bake a recorded placement once, preserving captured source buffers."""
    component = mesh.component
    if not (component.provenance.get("gimi_face_family")
            and component.provenance.get("geometry_kind") == "direct"):
        return
    placement = component.provenance.setdefault("gimi_face_placement", dict(
        version=1, method="captured_matrices", status="unresolved",
        reason="Capture analysis does not contain proven face placement"))
    if placement["status"] != "proven":
        component.diagnostics.append("Face placement unavailable: " + placement["reason"] + ". Captured coordinates retained.")
        return
    validated = set() if validated_sources is None else validated_sources
    for source in placement["sources"]:
        identity = source["file"], source["sha256"]
        if identity in validated:
            continue
        if hashlib.sha256(inv.read_bytes(source["file"])).hexdigest() != source["sha256"]:
            raise Unsupported("Face placement capture changed since analysis; analyze again")
        validated.add(identity)
    matrix = placement["matrix"]
    inverse = _inverse(matrix)
    context.budget.allocate(len(mesh.vertices) * 128)
    for i, row in enumerate(mesh.vertices):
        if i % 512 == 0:
            context.tick("Placing " + component.name, i, len(mesh.vertices))
        for j, element in enumerate(mesh.elements):
            value = row[j]
            if element.semantic == "POSITION":
                row[j] = _point(matrix, value)
            elif (element.semantic in {"NORMAL", "TANGENT"} and len(value) >= 3
                  and element.format.endswith("_FLOAT")):
                direction = (tuple(sum(inverse[b][a] * value[b] for b in range(3)) for a in range(3))
                             if element.semantic == "NORMAL" else
                             tuple(sum(matrix[a][b] * value[b] for b in range(3)) for a in range(3)))
                length = math.sqrt(sum(v * v for v in direction))
                row[j] = tuple(v / length for v in direction) + value[3:] if length else value
    placement["status"] = "baked"
    points = [row[0] for row in mesh.vertices]
    component.bounds = [[fn(point[a] for point in points) for a in range(3)] for fn in (min, max)]

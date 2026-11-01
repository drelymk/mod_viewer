"""Recognize GIMI head-local families from material and eye topology evidence."""

import copy
import hashlib
import math
import struct

from app.assets.loader.gimi_face_alignment import (
    AlignmentMesh, _boundary_components, find_two_eye_centers, solve,
)


def _alignment_mesh(mesh):
    return AlignmentMesh(mesh.component.id, tuple(v[0] for v in mesh.vertices), tuple(
        index + part.base_vertex for part, indices in zip(mesh.component.parts, mesh.indices)
        for index in indices))


def _eye_reference(mesh):
    points = tuple(v[0] for v in mesh.vertices)
    if not 16 <= len(points) <= 4096:
        return False
    centers = find_two_eye_centers(points)
    if centers is None:
        return False
    separation = math.dist(*centers)
    groups = [[], []]
    for point in points:
        groups[min(range(2), key=lambda i: math.dist(point, centers[i]))].append(point)
    if not separation or min(map(len, groups)) < 0.9 * max(map(len, groups)):
        return False
    spans = [max(p[a] for p in points) - min(p[a] for p in points) for a in range(3)]
    return (min(spans) <= 0.25 * max(spans) and all(
        max(math.dist(p, centers[i]) for p in group) <= 0.45 * separation
        for i, group in enumerate(groups)))


def _material(mesh):
    signatures = []
    for part in mesh.component.parts:
        selected = [t for t in part.textures if not t.get("auxiliary") and t["role"] in {"Diffuse", "LightMap"}]
        maps = {t["role"]: (t["hash"], t["sha256"]) for t in selected}
        if len(selected) != 2 or set(maps) != {"Diffuse", "LightMap"}:
            return None
        signatures.append(tuple(sorted(maps.items())))
    return signatures[0] if signatures and all(s == signatures[0] for s in signatures) else None


def _signature(mesh):
    digest = hashlib.sha256()
    for column, element in enumerate(mesh.elements):
        if element.semantic not in {"POSITION", "NORMAL", "TEXCOORD", "COLOR"}:
            continue
        digest.update(element.label.encode())
        for vertex in mesh.vertices:
            digest.update(struct.pack("<" + "f" * len(vertex[column]), *vertex[column]))
    c = mesh.component
    return (c.ib, digest.hexdigest(), tuple(
        (p.first, p.count, p.base_vertex, p.index_offset, p.sha256) for p in c.parts),
        tuple(tuple(sorted((t["role"], t["hash"], t["sha256"]) for t in p.textures
                           if not t.get("auxiliary") and not t["role"].startswith("TextureSlot")))
              for p in c.parts),
        tuple((role, s["hash"], s["resource"]["address"], s["resource"]["version"])
              for role, s in sorted(c.streams.items())))


def _near_anchor(mesh, anchor, distance):
    gap = []
    for axis in range(3):
        first = [v[0][axis] for v in mesh.vertices]
        second = [v[0][axis] for v in anchor.vertices]
        gap.append(max(0, min(first) - max(second), min(second) - max(first)))
    return sum(value * value for value in gap) <= distance * distance


def _separate_overlay(mesh):
    """Retain an independently UV-tagged duplicate face shell as an opt-in layer."""
    c = mesh.component
    if len(c.parts) != 1:
        return None
    channels = {e.index: i for i, e in enumerate(mesh.elements) if e.semantic == "TEXCOORD"}
    if not {0, 1, 2} <= channels.keys():
        return None
    tagged = []
    for vertex in mesh.vertices:
        uv, alternate, mask = (vertex[channels[i]] for i in range(3))
        if any(len(value) != 2 for value in (uv, alternate, mask)):
            return None
        active = any(abs(v) > 1e-6 for v in mask)
        if active and (uv != alternate or uv != mask):
            return None
        tagged.append(active)
    indices = mesh.indices[0]
    flags = []
    for offset in range(0, len(indices), 3):
        values = {tagged[i + c.parts[0].base_vertex] for i in indices[offset:offset + 3]}
        if len(values) != 1:
            return None
        flags.append(values.pop())
    if not flags or flags[0] or not flags[-1]:
        return None
    split = flags.index(True) * 3
    if not all(flags[split // 3:]):
        return None
    positions = tuple(v[0] for v in mesh.vertices)
    boundaries, bounds = [], []
    for selected in (indices[:split], indices[split:]):
        absolute = tuple(i + c.parts[0].base_vertex for i in selected)
        loops = _boundary_components(AlignmentMesh(c.name, positions, absolute))
        if not loops:
            return None
        largest = max(loops, key=len)
        boundaries.append(largest)
        bounds.append(tuple((min(positions[i][a] for i in absolute),
                             max(positions[i][a] for i in absolute)) for a in range(3)))
    # Separate shells can have a small authored offset; never snap their vertices.
    tolerance = math.dist(tuple(b[0] for b in bounds[0]), tuple(b[1] for b in bounds[0])) * 1e-4
    if (not tolerance or len(boundaries[0]) != len(boundaries[1]) or any(
        abs(a - b) > tolerance for first, second in zip(bounds[0], bounds[1])
        for a, b in zip(first, second)
    )):
        return None
    matches = [min(boundaries[1], key=lambda j: math.dist(positions[i], positions[j]))
               for i in boundaries[0]]
    rim_error = max(math.dist(positions[i], positions[j]) for i, j in zip(boundaries[0], matches))
    uv_error = max(math.dist(mesh.vertices[i][channels[channel]], mesh.vertices[j][channels[channel]])
                   for i, j in zip(boundaries[0], matches) for channel in (0, 1))
    # The UV tag alone is insufficient: require one-to-one rim geometry and UVs.
    if len(set(matches)) != len(matches) or rim_error > tolerance or uv_error > 1e-4:
        return None
    overlay = copy.deepcopy(c)
    overlay.id = hashlib.sha256((c.id + ":face-overlay").encode()).hexdigest()[:12]
    overlay.name += "Overlay"
    overlay.parts[0].first += split
    overlay.parts[0].count -= split
    overlay.triangle_count = overlay.parts[0].count // 3
    overlay.provenance.update(auxiliary_face_layer=True, source_component=c.id,
                              suggested_name=c.provenance["suggested_name"] + "Overlay")
    note = "Separate UV-tagged face overlay shares the outer face boundary; excluded by default to preserve eye openings without its shader masking."
    overlay.diagnostics.append(note)
    c.provenance["separated_face_overlay"] = dict(component=overlay.id, first=overlay.parts[0].first,
                                                   count=overlay.parts[0].count, boundary_max_error=rim_error,
                                                   boundary_tolerance=tolerance, boundary_uv_max_error=uv_error)
    c.parts[0].count = split
    c.triangle_count = split // 3
    mesh.indices[0] = indices[:split]
    return overlay


def classify_faces(meshes, context):
    """Classify only unique eye references and families with matching eye loops."""
    references = [m for m in meshes.values() if m.component.status == "included"
                  and m.component.provenance.get("geometry_kind") != "direct" and _eye_reference(m)]
    if len(references) != 1:
        return []
    reference = _alignment_mesh(references[0])
    families = {}
    for mesh in meshes.values():
        if mesh.component.status != "included" or mesh.component.provenance.get("geometry_kind") != "direct":
            continue
        material = _material(mesh)
        if material is not None:
            families.setdefault(material, {}).setdefault(_signature(mesh), []).append(mesh)
    proven = []
    for material, groups in families.items():
        context.tick("Recognizing GIMI facial geometry")
        anchors = []
        for equivalent in groups.values():
            candidate = equivalent[0]
            anchor = _alignment_mesh(candidate)
            # Paired brow strips alone also fit two centers, but do not establish
            # an eye-bearing face surface with additional closed boundaries.
            if len(_boundary_components(anchor)) > 2 and solve(reference, anchor, refine=False) is not None:
                anchors.append(candidate)
        if len(anchors) == 1:
            proven.append((material, groups, anchors[0]))
    if len(proven) != 1:
        return []
    _material_key, groups, anchor = proven[0]
    eye_separation = math.dist(*find_two_eye_centers(reference.positions))
    references[0].component.provenance["suggested_name"] = "Eyes"
    names, overlays = {}, []
    for equivalent in groups.values():
        primary = equivalent[0]
        if not _near_anchor(primary, anchor, eye_separation):
            continue
        loops = _boundary_components(_alignment_mesh(primary))
        kind = "FaceEye" if primary is anchor else "FaceEyebrow" if len(loops) == 2 and _eye_reference(primary) else "Face"
        names[kind] = names.get(kind, 0) + 1
        name = kind if names[kind] == 1 else ("FacePart" if kind == "Face" else kind) + str(names[kind])
        for ordinal, mesh in enumerate(equivalent):
            provenance = mesh.component.provenance
            provenance.update(gimi_face_family=anchor.component.id,
                              suggested_name=name if not ordinal else ("FacePart" if name == "Face" else name) + "Pass" + str(ordinal + 1),
                              face_group_evidence="Matching diffuse/light payloads and a unique eye-loop fit to a bilateral native Eyes mesh")
            if ordinal:
                provenance["duplicate_capture_pass"] = primary.component.id
                mesh.component.diagnostics.append("Equivalent captured facial geometry and material payloads; alternate pass excluded by default.")
            if primary is anchor:
                overlay = _separate_overlay(mesh)
                if overlay is not None:
                    overlays.append(overlay)
    return overlays

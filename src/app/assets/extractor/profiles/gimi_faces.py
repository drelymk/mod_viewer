"""Recognize GIMI head-local families from material and eye topology evidence."""

import copy
import hashlib
import math
import struct

from app.assets.loader.gimi_face_alignment import (
    AlignmentMesh, _area_weighted_normal, _boundary_components, find_two_eye_centers, solve,
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


def _open_face(mesh):
    """Prove paired open eye holes in one connected, enclosing face surface."""
    geometry = _alignment_mesh(mesh)
    positions = geometry.positions
    parents = {}

    def root(index):
        parents.setdefault(index, index)
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    triangles = [geometry.indices[i:i + 3] for i in range(0, len(geometry.indices), 3)]
    for a, b, c in triangles:
        parents[root(b)] = parents[root(a)]
        parents[root(c)] = parents[root(a)]
    surfaces = {}
    for triangle in triangles:
        surfaces.setdefault(root(triangle[0]), []).extend(triangle)
    matches = []
    for indices in surfaces.values():
        surface = AlignmentMesh(geometry.name, positions, tuple(indices))
        loops = _boundary_components(surface)
        if len(loops) < 3:
            continue
        # The enclosing contour is larger than either eye opening.
        loops = sorted(loops, key=lambda loop: math.dist(
            tuple(min(positions[i][a] for i in loop) for a in range(3)),
            tuple(max(positions[i][a] for i in loop) for a in range(3))), reverse=True)
        outer, left, right, *extra = loops
        spans = [math.dist(tuple(min(positions[i][a] for i in loop) for a in range(3)),
                           tuple(max(positions[i][a] for i in loop) for a in range(3)))
                 for loop in (left, right, *extra)]
        if any(span > 0.5 * min(spans[:2]) for span in spans[2:]):
            continue
        if min(len(left), len(right)) < 0.95 * max(len(left), len(right)):
            continue
        centers = [tuple(sum(positions[i][a] for i in loop) / len(loop) for a in range(3))
                   for loop in (left, right)]
        separation = math.dist(*centers)
        normal = _area_weighted_normal(surface)
        if not separation or normal is None:
            continue
        horizontal = tuple((centers[1][a] - centers[0][a]) / separation for a in range(3))
        if abs(sum(a * b for a, b in zip(horizontal, normal))) > 0.15:
            continue
        vertical = (normal[1] * horizontal[2] - normal[2] * horizontal[1],
                    normal[2] * horizontal[0] - normal[0] * horizontal[2],
                    normal[0] * horizontal[1] - normal[1] * horizontal[0])
        length = math.sqrt(sum(v * v for v in vertical))
        vertical = tuple(v / length for v in vertical)
        midpoint = tuple((a + b) / 2 for a, b in zip(*centers))

        def bounds(loop):
            projected = [tuple(sum((positions[i][a] - midpoint[a]) * axis[a] for a in range(3))
                               / separation for axis in (horizontal, vertical)) for i in loop]
            return tuple((min(p[a] for p in projected), max(p[a] for p in projected)) for a in range(2))

        outline, *eyes = [bounds(loop) for loop in (outer, left, right)]
        widths = [b[0][1] - b[0][0] for b in eyes]
        heights = [b[1][1] - b[1][0] for b in eyes]
        if (not all(0.2 <= width <= 0.85 for width in widths)
                or not all(0.08 <= height <= 0.65 for height in heights)
                or min(widths) < 0.8 * max(widths) or min(heights) < 0.8 * max(heights)
                or not 1.5 <= outline[0][1] - outline[0][0] <= 3.5
                or outline[1][1] - outline[1][0] < 1.0
                or abs(outline[0][0] + outline[0][1]) > 0.2
                or any(not outline[a][0] < eye[a][0] < eye[a][1] < outline[a][1]
                       for eye in eyes for a in range(2))
                or max(abs(value) for value in outline[1]) < 0.65):
            continue
        if any(not all(outline[a][0] < bound[a][0] < bound[a][1] < outline[a][1]
                       for a in range(2)) for bound in map(bounds, extra)):
            continue
        matches.append(dict(eye_midpoint=midpoint, eye_separation=separation,
                            extra_boundary_count=len(extra),
                            surface_triangle_fraction=len(indices) / len(geometry.indices)))
    if not matches:
        return None
    best = max(matches, key=lambda match: match["surface_triangle_fraction"])
    midpoint, separation = best["eye_midpoint"], best["eye_separation"]
    # Multiple authored layers may cover the same face, but separate faces are ambiguous.
    if any(math.dist(midpoint, match["eye_midpoint"]) > 0.15 * separation
           or abs(match["eye_separation"] / separation - 1) > 0.15 for match in matches):
        return None
    return dict(best, evidence="Paired open eye boundaries inside one connected enclosing face surface")


def _separate_overlay(mesh):
    """Retain an independently UV-tagged duplicate face shell as an opt-in layer."""
    c = mesh.component
    if len(c.parts) != 1:
        return None
    channels = {e.index: i for i, e in enumerate(mesh.elements) if e.semantic == "TEXCOORD"}
    if not {0, 1, 2} <= channels.keys():
        return None
    paired_mask = 3 in channels and all(
        len(vertex[channels[3]]) == 2 and vertex[channels[2]] == vertex[channels[3]]
        for vertex in mesh.vertices)
    tagged = []
    alternate_mask = False
    for vertex in mesh.vertices:
        uv, alternate, mask = (vertex[channels[i]] for i in range(3))
        if any(len(value) != 2 for value in (uv, alternate, mask)):
            return None
        active = any(abs(v) > 1e-6 for v in mask)
        if active:
            if uv != alternate or (uv != mask and not paired_mask):
                return None
            alternate_mask |= uv != mask
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
    boundaries, bounds, loop_counts = [], [], []
    for selected in (indices[:split], indices[split:]):
        absolute = tuple(i + c.parts[0].base_vertex for i in selected)
        loops = _boundary_components(AlignmentMesh(c.name, positions, absolute))
        if not loops:
            return None
        loop_counts.append(len(loops))
        largest = max(loops, key=len)
        boundaries.append(largest)
        bounds.append(tuple((min(positions[i][a] for i in absolute),
                             max(positions[i][a] for i in absolute)) for a in range(3)))
    # A separately mapped paired mask must cover openings in the retained shell.
    if alternate_mask and loop_counts[0] <= loop_counts[1]:
        return None
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
                                                   boundary_tolerance=tolerance, boundary_uv_max_error=uv_error,
                                                   uv_tag_layout="paired_secondary" if alternate_mask else "shared_primary")
    c.parts[0].count = split
    c.triangle_count = split // 3
    mesh.indices[0] = indices[:split]
    return overlay


def classify_faces(meshes, context, *, resolve_placement=None):
    """Recognize an open-eye face, retaining native-eye proof for legacy layouts."""
    references = [m for m in meshes.values() if m.component.status == "included"
                  and m.component.provenance.get("geometry_kind") != "direct" and _eye_reference(m)]
    families = {}
    for mesh in meshes.values():
        if mesh.component.status != "included" or mesh.component.provenance.get("geometry_kind") != "direct":
            continue
        material = _material(mesh)
        if material is not None:
            families.setdefault(material, {}).setdefault(_signature(mesh), []).append(mesh)
    proven, candidates, open_candidates = [], [], False
    for material, groups in families.items():
        context.tick("Recognizing GIMI facial geometry")
        anchors = [(equivalent[0], proof, signature) for signature, equivalent in groups.items()
                   if (proof := _open_face(equivalent[0])) is not None]
        open_candidates |= bool(anchors)
        candidates.extend((material, groups, *anchor) for anchor in anchors)
        if len(anchors) == 1:
            proven.append(candidates[-1])
    if not open_candidates and len(references) == 1:
        reference = _alignment_mesh(references[0])
        for material, groups in families.items():
            anchors = []
            for signature, equivalent in groups.items():
                candidate = equivalent[0]
                anchor = _alignment_mesh(candidate)
                # Legacy split eye rings need an independently proven native reference.
                if len(_boundary_components(anchor)) > 2 and solve(reference, anchor, refine=False) is not None:
                    anchors.append((candidate, signature))
            for anchor, signature in anchors:
                candidates.append((material, groups, anchor, dict(
                    eye_separation=math.dist(*find_two_eye_centers(reference.positions)),
                    evidence="Eye-loop fit to a bilateral native Eyes mesh"), signature))
            if len(anchors) == 1:
                proven.append(candidates[-1])
    if len(proven) != 1:
        proven = candidates
    if not proven:
        return []
    fallback = len(proven) > 1
    eligible = proven
    if fallback and resolve_placement is not None:
        placed = [candidate for candidate in proven if resolve_placement(
            [mesh.component for mesh in candidate[1][candidate[4]]])["status"] == "proven"]
        eligible = placed or proven
    # Prefer complete surfaces with fewer auxiliary openings, then compare content
    # before captured resource identity to keep equivalent quality choices stable.
    selected = min(eligible, key=lambda candidate: (
        -candidate[3].get("surface_triangle_fraction", 0),
        candidate[3].get("extra_boundary_count", 0),
        candidate[4])) if fallback else proven[0]
    _material_key, groups, anchor, proof, _signature_key = selected
    eye_separation = proof["eye_separation"]
    if len(references) == 1 and solve(
        _alignment_mesh(references[0]), _alignment_mesh(anchor), refine=False
    ) is not None:
        references[0].component.provenance["suggested_name"] = "Eyes"
    names, overlays = {}, []
    for equivalent in groups.values():
        primary = equivalent[0]
        if fallback and primary is not anchor:
            continue
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
                              face_group_evidence="Matching diffuse/light payloads; " + proof["evidence"],
                              face_anchor_proof=proof)
            if fallback:
                provenance["gimi_face_selection"] = dict(status="best_effort", candidates=len(proven))
                mesh.component.diagnostics.append(
                    "Ambiguous facial candidates: retained one best-effort face using validated eye topology "
                    "and matching diffuse/light payloads; competing candidates remain excluded.")
            if ordinal:
                provenance["duplicate_capture_pass"] = primary.component.id
                mesh.component.diagnostics.append("Equivalent captured facial geometry and material payloads; alternate pass excluded by default.")
            if primary is anchor:
                overlay = _separate_overlay(mesh)
                if overlay is not None:
                    overlays.append(overlay)
    return overlays

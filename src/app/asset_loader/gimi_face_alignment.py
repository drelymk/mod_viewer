"""Geometry-only alignment for GIMI face parts.

GIMI exports can contain face parts in a head-local frame while the exported
Eyes mesh is already in character space.  This module deliberately knows
nothing about Asset records or rendering; it only derives and applies a rigid
transform from the two pieces of geometry.
"""

from dataclasses import dataclass
import math
import struct


Vec3 = tuple[float, float, float]
Matrix4 = tuple[
    tuple[float, float, float, float],
    tuple[float, float, float, float],
    tuple[float, float, float, float],
    tuple[float, float, float, float],
]

_EPSILON = 1e-8
_MIN_LOOP_VERTICES = 8
_MAX_SEPARATION_ERROR = 0.25
_MIN_AXIS_DOMINANCE = 0.90


@dataclass(frozen=True, slots=True)
class AlignmentMesh:
    name: str
    positions: tuple[Vec3, ...]
    indices: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class GimiFaceAlignment:
    matrix: Matrix4
    diagnostics: dict


def _finite_vec(value):
    try:
        return (len(value) == 3 and
                all(isinstance(item, (int, float)) and math.isfinite(item)
                    for item in value))
    except TypeError:
        return False


def _sub(left, right):
    return (left[0] - right[0], left[1] - right[1], left[2] - right[2])


def _add(left, right):
    return (left[0] + right[0], left[1] + right[1], left[2] + right[2])


def _scale(value, factor):
    return (value[0] * factor, value[1] * factor, value[2] * factor)


def _dot(left, right):
    return left[0] * right[0] + left[1] * right[1] + left[2] * right[2]


def _cross(left, right):
    return (left[1] * right[2] - left[2] * right[1],
            left[2] * right[0] - left[0] * right[2],
            left[0] * right[1] - left[1] * right[0])


def _length(value):
    return math.sqrt(_dot(value, value))


def _normalize(value):
    length = _length(value)
    if length <= _EPSILON or not math.isfinite(length):
        return None
    return _scale(value, 1.0 / length)


def _mean(points):
    if not points:
        return None
    total = (0.0, 0.0, 0.0)
    for point in points:
        total = _add(total, point)
    return _scale(total, 1.0 / len(points))


def _clean_points(positions):
    result = []
    for point in positions or ():
        if _finite_vec(point):
            result.append(tuple(float(item) for item in point))
    return result


def _initial_eye_centers(points):
    """Choose deterministic far-apart initial centers without O(n squared)."""
    candidates = []
    for axis in range(3):
        low = min(range(len(points)), key=lambda index: points[index][axis])
        high = max(range(len(points)), key=lambda index: points[index][axis])
        candidates.extend((low, high))
    pair = max(
        ((left, right) for left in set(candidates)
         for right in set(candidates) if left != right),
        key=lambda item: _length(_sub(points[item[0]], points[item[1]])),
        default=None,
    )
    if pair is None:
        return None
    return points[pair[0]], points[pair[1]]


def _cluster_two_means(positions):
    """Return ordered two-means centers and their point groups."""
    points = _clean_points(positions)
    if len(points) < 4:
        return None, None
    centers = _initial_eye_centers(points)
    if centers is None:
        return None, None
    centers = [centers[0], centers[1]]
    groups = ((), ())
    for _ in range(32):
        grouped = [[], []]
        for point in points:
            distances = [_dot(_sub(point, center), _sub(point, center))
                         for center in centers]
            grouped[0 if distances[0] <= distances[1] else 1].append(point)
        if not all(grouped):
            return None, None
        updated = [_mean(group) for group in grouped]
        if updated == centers:
            groups = (tuple(grouped[0]), tuple(grouped[1]))
            break
        centers = updated
        groups = (tuple(grouped[0]), tuple(grouped[1]))
    else:
        groups = (tuple(grouped[0]), tuple(grouped[1]))
    if min(len(group) for group in groups) < 2:
        return None, None
    separation = _sub(centers[1], centers[0])
    if _length(separation) <= _EPSILON:
        return None, None
    axis = max(range(3), key=lambda index: abs(separation[index]))
    order = sorted(range(2), key=lambda index: centers[index][axis])
    return (tuple(centers[index] for index in order),
            tuple(groups[index] for index in order))


def find_two_eye_centers(positions):
    """Return the consistently ordered centers of two point clusters.

    The largest geometric span seeds a deterministic two-means fit.  Centers
    are ordered along their dominant separation axis, which gives callers a
    stable left-to-right bilateral axis without relying on vertex order.
    """
    centers, _groups = _cluster_two_means(positions)
    return centers


def _boundary_components(mesh):
    """Return closed triangle-boundary components as vertex-index tuples."""
    positions = mesh.positions
    edge_counts = {}
    for offset in range(0, len(mesh.indices) - 2, 3):
        triangle = mesh.indices[offset:offset + 3]
        if (len(triangle) != 3 or any(
                not isinstance(item, int) or item < 0 or item >= len(positions)
                for item in triangle) or len(set(triangle)) != 3):
            continue
        for left, right in ((triangle[0], triangle[1]),
                            (triangle[1], triangle[2]),
                            (triangle[2], triangle[0])):
            edge = (min(left, right), max(left, right))
            edge_counts[edge] = edge_counts.get(edge, 0) + 1
    adjacency = {}
    for (left, right), count in edge_counts.items():
        if count != 1:
            continue
        adjacency.setdefault(left, set()).add(right)
        adjacency.setdefault(right, set()).add(left)
    components = []
    remaining = set(adjacency)
    while remaining:
        start = min(remaining)
        stack = [start]
        component = set()
        while stack:
            current = stack.pop()
            if current in component:
                continue
            component.add(current)
            remaining.discard(current)
            stack.extend(adjacency.get(current, ()) - component)
        if (len(component) >= _MIN_LOOP_VERTICES and
                all(len(adjacency.get(item, ())) == 2 for item in component)):
            components.append(tuple(sorted(component)))
    return tuple(components)


def _component_centroid(mesh, component):
    return _mean([mesh.positions[index] for index in component])


def _dominant_axis(value):
    length = _length(value)
    if length <= _EPSILON:
        return None, 0.0
    axis = max(range(3), key=lambda index: abs(value[index]))
    return axis, abs(value[axis]) / length


def _select_eye_loop_pair(mesh, body_separation=None):
    components = _boundary_components(mesh)
    centers = [(component, _component_centroid(mesh, component))
               for component in components]
    candidates = []
    for left_index, (left_component, left_center) in enumerate(centers):
        for right_component, right_center in centers[left_index + 1:]:
            separation = _sub(right_center, left_center)
            distance = _length(separation)
            axis, dominance = _dominant_axis(separation)
            if axis is None or dominance < _MIN_AXIS_DOMINANCE:
                continue
            error = (abs(distance - body_separation) / body_separation
                     if body_separation and body_separation > _EPSILON else 0.0)
            if body_separation and error > _MAX_SEPARATION_ERROR:
                continue
            topology_penalty = (abs(len(left_component) - len(right_component))
                                / max(len(left_component),
                                      len(right_component)))
            if topology_penalty > 0.05:
                continue
            candidates.append((error + topology_penalty, topology_penalty,
                               -distance, left_component,
                               right_component, left_center, right_center))
    if not candidates:
        return None
    _, _, _, first_component, second_component, first_center, second_center = min(
        candidates, key=lambda item: item[:3])
    axis, _ = _dominant_axis(_sub(second_center, first_center))
    if first_center[axis] <= second_center[axis]:
        return ((first_component, first_center),
                (second_component, second_center))
    return ((second_component, second_center),
            (first_component, first_center))


def _area_weighted_normal(mesh):
    total = (0.0, 0.0, 0.0)
    valid = 0
    for offset in range(0, len(mesh.indices) - 2, 3):
        triangle = mesh.indices[offset:offset + 3]
        if (len(triangle) != 3 or any(
                not isinstance(item, int) or item < 0 or item >= len(mesh.positions)
                for item in triangle)):
            continue
        first, second, third = (mesh.positions[item] for item in triangle)
        cross = _cross(_sub(second, first), _sub(third, first))
        if _length(cross) <= _EPSILON:
            continue
        total = _add(total, cross)
        valid += 1
    return _normalize(total) if valid else None


def _basis_rotation(source_basis, target_basis):
    """Return R = target_basis @ transpose(source_basis)."""
    rotation = tuple(
        tuple(sum(target_basis[basis][row] * source_basis[basis][column]
                  for basis in range(3)) for column in range(3))
        for row in range(3))
    return rotation


def _determinant(rotation):
    a, b, c = rotation
    return (a[0] * (b[1] * c[2] - b[2] * c[1])
            - a[1] * (b[0] * c[2] - b[2] * c[0])
            + a[2] * (b[0] * c[1] - b[1] * c[0]))


def _rotation_point(rotation, point):
    return tuple(sum(rotation[row][column] * point[column]
                     for column in range(3)) for row in range(3))


def _matrix(rotation, translation):
    return (
        (rotation[0][0], rotation[0][1], rotation[0][2], translation[0]),
        (rotation[1][0], rotation[1][1], rotation[1][2], translation[1]),
        (rotation[2][0], rotation[2][1], rotation[2][2], translation[2]),
        (0.0, 0.0, 0.0, 1.0),
    )


def transform_point(matrix, point):
    """Apply a row-major homogeneous matrix to one point."""
    return (
        matrix[0][0] * point[0] + matrix[0][1] * point[1]
        + matrix[0][2] * point[2] + matrix[0][3],
        matrix[1][0] * point[0] + matrix[1][1] * point[1]
        + matrix[1][2] * point[2] + matrix[1][3],
        matrix[2][0] * point[0] + matrix[2][1] * point[1]
        + matrix[2][2] * point[2] + matrix[2][3],
    )


def _with_translation(matrix, translation):
    return (matrix[0][:3] + (translation[0],),
            matrix[1][:3] + (translation[1],),
            matrix[2][:3] + (translation[2],),
            matrix[3])


def _sample_points(points, limit=256):
    if len(points) <= limit:
        return tuple(points)
    return tuple(points[round(index * (len(points) - 1) / (limit - 1))]
                 for index in range(limit))


def _projected_half_span(points, center, axis):
    values = [_dot(_sub(point, center), axis) for point in points]
    return ((max(values) - min(values)) * 0.5) if values else 0.0


def _trimmed_surface_error(face_groups, body_groups, matrix):
    distances = []
    for face_points, body_points in zip(face_groups, body_groups):
        for point in face_points:
            transformed = transform_point(matrix, point)
            nearest = min((_length(_sub(transformed, candidate))
                           for candidate in body_points), default=None)
            if nearest is not None and math.isfinite(nearest):
                distances.append(nearest * nearest)
    if not distances:
        return None
    distances.sort()
    keep = max(1, int(math.ceil(len(distances) * 0.7)))
    return sum(distances[:keep]) / keep


def _fit_eye_surface_offset(face_points, body_points, matrix,
                            target_h, target_v, target_f, separation, *,
                            body_centers=None, body_groups=None):
    """Fit corresponding eye surfaces with bounded up/forward translations."""
    if (not face_points or len(body_points) < 2
            or separation <= _EPSILON):
        return matrix, 0.0
    if body_centers is None or body_groups is None:
        return matrix, 0.0
    scales = []
    for center, group in zip(body_centers, body_groups):
        if not group:
            return matrix, 0.0
        half_h = _projected_half_span(group, center, target_h)
        half_v = _projected_half_span(group, center, target_v)
        scales.append((half_h + half_v) * 0.5)
    if not scales:
        return matrix, 0.0
    eye_scale = sum(scales) / len(scales)
    if eye_scale <= _EPSILON:
        return matrix, 0.0

    selection_radius = eye_scale * 2.0
    selected = [[], []]
    for point in face_points:
        aligned = transform_point(matrix, point)
        distances = [_length(_sub(aligned, center))
                     for center in body_centers]
        side = 0 if distances[0] <= distances[1] else 1
        if distances[side] <= selection_radius:
            selected[side].append(point)
    if min(len(group) for group in selected) < 24:
        return matrix, 0.0

    face_groups = tuple(_sample_points(group) for group in selected)
    reference_groups = tuple(_sample_points(group) for group in body_groups)
    baseline = _trimmed_surface_error(face_groups, reference_groups, matrix)
    if baseline is None or baseline <= _EPSILON:
        return matrix, 0.0
    up_range = 1.5 * eye_scale
    forward_range = 0.75 * eye_scale
    base_translation = (matrix[0][3], matrix[1][3], matrix[2][3])
    best_error = baseline
    best_matrix = matrix
    best_up = 0.0
    best_forward = 0.0

    def evaluate(up, forward):
        nonlocal best_error, best_matrix, best_up, best_forward
        offset = _add(_scale(target_v, up), _scale(target_f, forward))
        candidate = _with_translation(matrix, _add(base_translation, offset))
        error = _trimmed_surface_error(face_groups, reference_groups, candidate)
        if error is not None and error < best_error:
            best_error = error
            best_matrix = candidate
            best_up = up
            best_forward = forward

    for up_step in range(9):
        up = (up_step / 4.0 - 1.0) * up_range
        for forward_step in range(9):
            forward = (forward_step / 4.0 - 1.0) * forward_range
            evaluate(up, forward)
    local_up_range = up_range / 4.0
    local_forward_range = forward_range / 4.0
    for _ in range(4):
        local_up_range /= 2.0
        local_forward_range /= 2.0
        for up_step in range(5):
            up = best_up + (up_step / 2.0 - 1.0) * local_up_range
            for forward_step in range(5):
                forward = (best_forward
                           + (forward_step / 2.0 - 1.0) * local_forward_range)
                evaluate(up, forward)
    improvement = (baseline - best_error) / baseline
    return (best_matrix if improvement >= 0.02 else matrix), improvement


def solve(body_eyes, face_anchor, landmark=None, *, landmark_kind=None,
          refine=True):
    """Derive one conservative rigid face-to-character alignment.

    ``body_eyes`` supplies the target eye centers and ``face_anchor`` supplies
    the source eye boundary loops and authored winding.  ``landmark`` is
    preferably Brow/Eyebrow and otherwise Mouth; it is only used to resolve
    the source frame's 180-degree roll ambiguity.
    """
    body_points = _clean_points(body_eyes.positions if body_eyes else ())
    if not body_points or not face_anchor:
        return None
    if not all(_finite_vec(point) for point in face_anchor.positions):
        return None
    body_centers, body_groups = _cluster_two_means(body_points)
    if body_centers is None:
        return None
    body_separation_vector = _sub(body_centers[1], body_centers[0])
    body_separation = _length(body_separation_vector)
    if body_separation <= _EPSILON:
        return None
    pair = _select_eye_loop_pair(face_anchor, body_separation)
    if pair is None:
        return None
    (left_loop, left_center), (right_loop, right_center) = pair
    source_h = _normalize(_sub(right_center, left_center))
    if source_h is None:
        return None
    source_f = _area_weighted_normal(face_anchor)
    if source_f is None:
        return None
    source_f = _normalize(_sub(source_f, _scale(source_h,
                                                _dot(source_f, source_h))))
    if source_f is None:
        return None
    source_v = _normalize(_cross(source_f, source_h))
    if source_v is None:
        return None

    landmark_points = _clean_points(landmark.positions if landmark else ())
    eye_midpoint = _scale(_add(left_center, right_center), 0.5)
    if landmark_points:
        landmark_relative = _sub(_mean(landmark_points), eye_midpoint)
        landmark_height = _dot(landmark_relative, source_v)
        is_brow = (landmark_kind == "brow" or
                   (landmark_kind is None and
                    "brow" in str(getattr(landmark, "name", "")).casefold()))
        if ((is_brow and landmark_height < -_EPSILON)
                or (not is_brow and landmark_height > _EPSILON)):
            source_h = _scale(source_h, -1.0)
            source_v = _scale(source_v, -1.0)

    target_v = (0.0, 1.0, 0.0)
    target_h = _normalize(body_separation_vector)
    if target_h is None:
        return None
    target_h = _normalize(_sub(
        target_h, _scale(target_v, _dot(target_h, target_v))))
    if target_h is None:
        return None
    target_f = _normalize(_cross(target_h, target_v))
    if target_f is None:
        return None
    source_basis = (source_h, source_v, source_f)
    target_basis = (target_h, target_v, target_f)
    rotation = _basis_rotation(source_basis, target_basis)
    determinant = _determinant(rotation)
    if not math.isfinite(determinant) or abs(determinant - 1.0) > 1e-4:
        return None
    rotated_midpoint = _rotation_point(rotation, eye_midpoint)
    body_midpoint = _scale(_add(body_centers[0], body_centers[1]), 0.5)
    matrix = _matrix(rotation, _sub(body_midpoint, rotated_midpoint))

    refinement = 0.0
    face_points = _clean_points(face_anchor.positions)
    if refine:
        matrix, refinement = _fit_eye_surface_offset(
            face_points, body_points, matrix, target_h, target_v, target_f,
            body_separation, body_centers=body_centers,
            body_groups=body_groups)
    return GimiFaceAlignment(matrix, {
        "body_eye_separation": body_separation,
        "face_eye_separation": _length(_sub(right_center, left_center)),
        "rotation_determinant": determinant,
        "surface_fit_improvement": refinement,
    })


def solve_face_alignment(body_eyes, face_anchor, landmark=None, *,
                         landmark_kind=None, refine=True):
    """Descriptive alias for callers outside the geometry module."""
    return solve(body_eyes, face_anchor, landmark,
                 landmark_kind=landmark_kind, refine=refine)


def unpack_f32x3(data):
    """Unpack canonical packed Float32 XYZ data."""
    if data is None or len(data) % 12:
        raise ValueError("Packed XYZ data must contain complete Float32 triples.")
    points = tuple(item for item in struct.iter_unpack("<3f", bytes(data)))
    if any(not _finite_vec(point) for point in points):
        raise ValueError("Packed XYZ data contains a non-finite value.")
    return points


def _transform_vectors(data, matrix, *, normalize=False):
    if data is None:
        return None
    points = unpack_f32x3(data)
    result = bytearray(len(points) * 12)
    for index, point in enumerate(points):
        transformed = _rotation_point((
            matrix[0][:3], matrix[1][:3], matrix[2][:3]), point)
        if normalize:
            length = _length(transformed)
            if length > _EPSILON:
                transformed = _scale(transformed, 1.0 / length)
        struct.pack_into("<3f", result, index * 12, *transformed)
    return bytes(result)


def transform_position_bytes(data, matrix):
    """Transform packed canonical positions, including translation."""
    if data is None:
        return None
    points = unpack_f32x3(data)
    result = bytearray(len(points) * 12)
    for index, point in enumerate(points):
        struct.pack_into("<3f", result, index * 12,
                         *transform_point(matrix, point))
    return bytes(result)


def transform_normal_bytes(data, matrix):
    """Rotate packed canonical authored normals without applying translation."""
    return _transform_vectors(data, matrix, normalize=True)


__all__ = [
    "AlignmentMesh", "GimiFaceAlignment", "Matrix4", "Vec3",
    "find_two_eye_centers", "solve", "solve_face_alignment",
    "transform_normal_bytes", "transform_point", "transform_position_bytes",
    "unpack_f32x3",
]

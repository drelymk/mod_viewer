"""Conservative source-texture coverage for packed mesh UV triangles."""

from dataclasses import dataclass
import math
import struct


_UV_EPSILON = 1e-6
_AREA_EPSILON = 1e-12


class UVCoverageError(ValueError):
    """A stable analysis failure raised by the pure UV coverage layer."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class UVCoverage:
    """Compact edit-unit mask and diagnostics for one UV set."""

    grid_width: int
    grid_height: int
    mask: bytearray
    count: int
    bounds: tuple | None
    triangle_count: int
    degenerate_triangle_count: int


def _indices(values):
    if isinstance(values, (bytes, bytearray, memoryview)):
        raw = bytes(values)
        if len(raw) % 4:
            raise UVCoverageError(
                "invalid_geometry", "Packed indices are not complete u32 values.")
        return list(struct.unpack(f"<{len(raw) // 4}I", raw))
    try:
        return [int(value) for value in values]
    except (TypeError, ValueError):
        raise UVCoverageError(
            "invalid_geometry", "Mesh indices could not be read.") from None


def _texcoords(values):
    if values is None:
        raise UVCoverageError("mesh_has_no_uv", "The mesh has no UV coordinates.")
    if isinstance(values, (bytes, bytearray, memoryview)):
        raw = bytes(values)
        if len(raw) % 8:
            raise UVCoverageError(
                "invalid_geometry", "Packed UVs are not complete Float32 pairs.")
        flat = struct.unpack(f"<{len(raw) // 4}f", raw)
        return list(zip(flat[::2], flat[1::2]))
    try:
        values = list(values)
    except TypeError:
        raise UVCoverageError(
            "mesh_has_no_uv", "The mesh has no UV coordinates.") from None
    if not values:
        return []
    if isinstance(values[0], (list, tuple)):
        try:
            return [(float(item[0]), float(item[1])) for item in values]
        except (IndexError, TypeError, ValueError):
            raise UVCoverageError(
                "invalid_geometry", "Mesh UV coordinates could not be read.") from None
    if len(values) % 2:
        raise UVCoverageError(
            "invalid_geometry", "Mesh UV coordinates are not complete pairs.")
    try:
        return [(float(u), float(v)) for u, v in zip(values[::2], values[1::2])]
    except (TypeError, ValueError):
        raise UVCoverageError(
            "invalid_geometry", "Mesh UV coordinates could not be read.") from None


def _clamp_uv(value):
    value = float(value)
    if not math.isfinite(value):
        raise UVCoverageError("invalid_geometry", "Mesh UVs contain non-finite values.")
    if value < -_UV_EPSILON or value > 1.0 + _UV_EPSILON:
        raise UVCoverageError(
            "tiled_uv_unsupported",
            "The mesh uses UVs outside the 0-1 texture range.")
    return min(1.0, max(0.0, value))


def _cell_index(value, size):
    return min(size - 1, max(0, math.floor(value)))


def _mark_point(mask, grid_width, grid_height, x, y):
    """Mark the cells touched by a point, including grid-line neighbors."""
    x = min(float(grid_width), max(0.0, x))
    y = min(float(grid_height), max(0.0, y))
    x_cell = _cell_index(x, grid_width)
    y_cell = _cell_index(y, grid_height)
    x_cells = {x_cell}
    y_cells = {y_cell}
    if (abs(x - round(x)) <= _UV_EPSILON
            and x > 0
            and x < grid_width - _UV_EPSILON):
        x_cells.add(max(0, x_cell - 1))
    if (abs(y - round(y)) <= _UV_EPSILON
            and y > 0
            and y < grid_height - _UV_EPSILON):
        y_cells.add(max(0, y_cell - 1))
    for cell_y in y_cells:
        for cell_x in x_cells:
            mask[cell_y * grid_width + cell_x] = 1


def _mark_cell(mask, grid_width, grid_height, cell_x, cell_y):
    if 0 <= cell_x < grid_width and 0 <= cell_y < grid_height:
        mask[cell_y * grid_width + cell_x] = 1


def _supercover_segment(mask, grid_width, grid_height, start, end):
    """Mark every grid cell crossed by a segment using a DDA supercover."""
    x0, y0 = start
    x1, y1 = end
    dx, dy = x1 - x0, y1 - y0
    if abs(dx) <= _AREA_EPSILON and abs(dy) <= _AREA_EPSILON:
        _mark_point(mask, grid_width, grid_height, x0, y0)
        return

    def initial(value, delta, size):
        cell = math.floor(value)
        if delta < 0 and abs(value - round(value)) <= _UV_EPSILON:
            cell -= 1
        return min(size - 1, max(0, cell))

    cell_x = initial(x0, dx, grid_width)
    cell_y = initial(y0, dy, grid_height)
    horizontal_boundary = (
        abs(dy) <= _AREA_EPSILON
        and abs(y0 - round(y0)) <= _UV_EPSILON
        and y0 > _UV_EPSILON
        and y0 < grid_height - _UV_EPSILON)
    vertical_boundary = (
        abs(dx) <= _AREA_EPSILON
        and abs(x0 - round(x0)) <= _UV_EPSILON
        and x0 > _UV_EPSILON
        and x0 < grid_width - _UV_EPSILON)

    def mark_traversed(cell_x, cell_y):
        _mark_cell(mask, grid_width, grid_height, cell_x, cell_y)
        if horizontal_boundary:
            _mark_cell(mask, grid_width, grid_height, cell_x, cell_y - 1)
        if vertical_boundary:
            _mark_cell(mask, grid_width, grid_height, cell_x - 1, cell_y)

    mark_traversed(cell_x, cell_y)
    _mark_point(mask, grid_width, grid_height, x0, y0)
    _mark_point(mask, grid_width, grid_height, x1, y1)

    step_x = 1 if dx > 0 else -1
    step_y = 1 if dy > 0 else -1
    delta_x = abs(1.0 / dx) if dx else math.inf
    delta_y = abs(1.0 / dy) if dy else math.inf
    next_x = ((cell_x + 1 - x0) / dx if dx > 0
              else (cell_x - x0) / dx if dx < 0 else math.inf)
    next_y = ((cell_y + 1 - y0) / dy if dy > 0
              else (cell_y - y0) / dy if dy < 0 else math.inf)
    while min(next_x, next_y) <= 1.0 + _UV_EPSILON:
        if next_x < next_y - _UV_EPSILON:
            cell_x += step_x
            mark_traversed(cell_x, cell_y)
            next_x += delta_x
        elif next_y < next_x - _UV_EPSILON:
            cell_y += step_y
            mark_traversed(cell_x, cell_y)
            next_y += delta_y
        else:
            crossing_x = x0 + dx * next_x
            crossing_y = y0 + dy * next_y
            _mark_point(mask, grid_width, grid_height,
                        crossing_x, crossing_y)
            cell_x += step_x
            mark_traversed(cell_x, cell_y)
            cell_y += step_y
            mark_traversed(cell_x, cell_y)
            next_x += delta_x
            next_y += delta_y
        if not (0 <= cell_x < grid_width and 0 <= cell_y < grid_height):
            break


def _inside(px, py, triangle):
    (ax, ay), (bx, by), (cx, cy) = triangle
    ab = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
    bc = (cx - bx) * (py - by) - (cy - by) * (px - bx)
    ca = (ax - cx) * (py - cy) - (ay - cy) * (px - cx)
    return (ab >= -_AREA_EPSILON and bc >= -_AREA_EPSILON
            and ca >= -_AREA_EPSILON) or (
                ab <= _AREA_EPSILON and bc <= _AREA_EPSILON
                and ca <= _AREA_EPSILON)


def _rasterize_triangle(mask, grid_width, grid_height, triangle):
    for point in triangle:
        _mark_point(mask, grid_width, grid_height, *point)
    for start, end in zip(triangle, triangle[1:] + triangle[:1]):
        _supercover_segment(mask, grid_width, grid_height, start, end)

    min_x = max(0, math.floor(min(point[0] for point in triangle)))
    max_x = min(grid_width - 1,
                math.ceil(max(point[0] for point in triangle)) - 1)
    min_y = max(0, math.floor(min(point[1] for point in triangle)))
    max_y = min(grid_height - 1,
                math.ceil(max(point[1] for point in triangle)) - 1)
    for cell_y in range(min_y, max_y + 1):
        for cell_x in range(min_x, max_x + 1):
            if _inside(cell_x + 0.5, cell_y + 0.5, triangle):
                _mark_cell(mask, grid_width, grid_height, cell_x, cell_y)


def rasterize_uv_coverage(
        indices, texcoords, texture_width, texture_height, *,
        unit_width=1, unit_height=1):
    """Rasterize source-space UVs into a compact edit-unit mask.

    ``texcoords`` are expected in source-texture orientation. Callers that
    receive viewer-packed UVs must invert V before calling this function.
    """
    try:
        texture_width = int(texture_width)
        texture_height = int(texture_height)
        unit_width = int(unit_width)
        unit_height = int(unit_height)
    except (TypeError, ValueError):
        raise UVCoverageError(
            "invalid_texture", "Texture dimensions are invalid.") from None
    if min(texture_width, texture_height, unit_width, unit_height) <= 0:
        raise UVCoverageError(
            "invalid_texture", "Texture dimensions are invalid.")

    indices = _indices(indices)
    texcoords = _texcoords(texcoords)
    if not texcoords:
        raise UVCoverageError("mesh_has_no_uv", "The mesh has no UV coordinates.")
    if len(indices) % 3:
        raise UVCoverageError(
            "invalid_geometry", "Mesh indices do not form complete triangles.")
    if any(index < 0 or index >= len(texcoords) for index in indices):
        raise UVCoverageError(
            "invalid_geometry", "Mesh indices reference missing UV coordinates.")

    grid_width = math.ceil(texture_width / unit_width)
    grid_height = math.ceil(texture_height / unit_height)
    mask = bytearray(grid_width * grid_height)
    triangle_count = len(indices) // 3
    degenerate = 0
    for offset in range(0, len(indices), 3):
        points = []
        for index in indices[offset:offset + 3]:
            u = _clamp_uv(texcoords[index][0])
            v = _clamp_uv(texcoords[index][1])
            points.append((u * texture_width / unit_width,
                           v * texture_height / unit_height))
        area = ((points[1][0] - points[0][0])
                * (points[2][1] - points[0][1])
                - (points[1][1] - points[0][1])
                * (points[2][0] - points[0][0]))
        if abs(area) <= _AREA_EPSILON:
            degenerate += 1
            continue
        _rasterize_triangle(mask, grid_width, grid_height, points)

    count = sum(mask)
    if not count:
        raise UVCoverageError(
            "no_uv_coverage", "The mesh has no non-degenerate UV coverage.")
    used = [index for index, value in enumerate(mask) if value]
    bounds = (
        min(index % grid_width for index in used),
        min(index // grid_width for index in used),
        max(index % grid_width for index in used),
        max(index // grid_width for index in used),
    )
    return UVCoverage(
        grid_width, grid_height, mask, count, bounds,
        triangle_count, degenerate)


__all__ = ["UVCoverage", "UVCoverageError", "rasterize_uv_coverage"]

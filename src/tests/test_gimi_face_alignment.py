import math
import struct

import pytest

from app.asset_loader.gimi_face_alignment import (
    AlignmentMesh, solve, transform_normal_bytes, transform_point,
    transform_position_bytes)


def _face_mesh(centers, *, name="FaceEye", h=(0, 0, 1), v=(0, 1, 0),
               radius=0.15):
    positions = []
    indices = []
    for center in centers:
        center_index = len(positions)
        positions.append(center)
        ring = []
        for ordinal in range(8):
            angle = 2 * math.pi * ordinal / 8
            ring.append(len(positions))
            positions.append(tuple(
                center[axis] + radius * (
                    h[axis] * math.cos(angle) + v[axis] * math.sin(angle))
                for axis in range(3)))
        for ordinal, first in enumerate(ring):
            indices.extend((center_index, first, ring[(ordinal + 1) % 8]))
    return AlignmentMesh(name, tuple(positions), tuple(indices))


def _body_eyes(left, right):
    offsets = ((-0.04, 0, 0), (0.04, 0, 0),
               (0, -0.04, 0), (0, 0.04, 0))
    return AlignmentMesh(
        "EyesA", tuple(tuple(center[axis] + offset[axis]
                              for axis in range(3))
                       for center in (left, right) for offset in offsets), ())


def test_solver_recovers_rotation_translation_and_positive_determinant():
    body = _body_eyes((-0.75, 2, 3), (0.75, 2, 3))
    source_mid = (4, -2, 7)
    face = _face_mesh(
        (tuple(source_mid[axis] - (0, 0, 0.75)[axis]
                for axis in range(3)),
         tuple(source_mid[axis] + (0, 0, 0.75)[axis]
               for axis in range(3))))

    alignment = solve(body, face, refine=False)

    assert alignment is not None
    assert alignment.diagnostics["rotation_determinant"] == pytest.approx(1)
    mapped = [transform_point(alignment.matrix, point)
              for point in (face.positions[0], face.positions[9])]
    assert mapped[0] == pytest.approx((-0.75, 2, 3), abs=1e-5)
    assert mapped[1] == pytest.approx((0.75, 2, 3), abs=1e-5)


def test_brow_and_mouth_landmarks_resolve_roll_without_mirroring():
    body = _body_eyes((-0.75, 2, 3), (0.75, 2, 3))
    face = _face_mesh(
        ((4, -2, 6.25), (4, -2, 7.75)), v=(0, -1, 0))

    brow = AlignmentMesh("Brow", ((4, -1.5, 7),), ())
    brow_alignment = solve(body, face, brow, refine=False)
    brow_mapped = transform_point(brow_alignment.matrix, brow.positions[0])
    assert brow_mapped[1] > 2

    mouth = AlignmentMesh("Mouth", ((4, -2.5, 7),), ())
    mouth_alignment = solve(body, face, mouth, refine=False)
    mouth_mapped = transform_point(mouth_alignment.matrix, mouth.positions[0])
    assert mouth_mapped[1] < 2
    assert mouth_alignment.diagnostics["rotation_determinant"] == pytest.approx(1)


def test_already_aligned_face_stays_aligned_and_bad_spacing_is_rejected():
    body = _body_eyes((-0.75, 2, 3), (0.75, 2, 3))
    aligned = _face_mesh(((-0.75, 2, 3), (0.75, 2, 3)), h=(1, 0, 0),
                         v=(0, 1, 0))
    result = solve(body, aligned, refine=False)
    assert result is not None
    assert result.matrix[0][0] == pytest.approx(1)
    assert result.matrix[1][1] == pytest.approx(1)
    assert result.matrix[2][2] == pytest.approx(1)
    assert result.matrix[0][3] == pytest.approx(0)

    too_wide = _face_mesh(((-1.25, 2, 3), (1.25, 2, 3)), h=(1, 0, 0),
                          v=(0, 1, 0))
    assert solve(body, too_wide, refine=False) is None


def test_packed_helpers_transform_positions_and_authored_normals_only():
    matrix = ((0, -1, 0, 4), (1, 0, 0, 5), (0, 0, 1, 6),
              (0, 0, 0, 1))
    positions = struct.pack("<3f", 1, 2, 3)
    normals = struct.pack("<3f", 1, 0, 0)

    assert struct.unpack("<3f", transform_position_bytes(positions, matrix)) == \
        pytest.approx((2, 6, 9))
    assert struct.unpack("<3f", transform_normal_bytes(normals, matrix)) == \
        pytest.approx((0, 1, 0))


def test_malformed_boundary_geometry_returns_no_alignment():
    body = _body_eyes((-0.75, 2, 3), (0.75, 2, 3))
    malformed = AlignmentMesh(
        "FaceEye", ((-0.75, 2, 3), (0.75, 2, 3), (0, 2, 3)),
        (0, 1, 2))

    assert solve(body, malformed, refine=False) is None

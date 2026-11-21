"""Uncaptured ZZZ props (weapons) skinned to the verified rig, shown only during clip playback.

The capture never draws a character's weapon, but the game's model meshes skin it to
bones of the same Avatar. A prop is a native model mesh of the verified family that no
captured component came from, whose inverse binds prove a placement against the
verified Avatar, and whose positive skin weight lies entirely on bones that no captured
palette uses. Names only discover candidates; outfit variants, hair shadows and other
meshes driven by captured bones never qualify.
"""

import hashlib
import re
import shutil

import numpy as np

from app.assets.animations import MAX_PROP_INDICES, MAX_PROP_VERTICES, MAX_PROPS, PROP_LAYOUT
from . import zzz_index, zzz_prop_textures
from .pipeline import rest_worlds
from .runtime import ExtractionCancelled, check_cancelled
from .zzz_native import mesh_placement

MAX_TOTAL_BYTES = 64 * 1024 * 1024


def _components(values, count, widths):
    array = np.asarray(values if values is not None else [], dtype=float)
    width = next((width for width in widths if array.size == count * width), None)
    if width is None or not np.isfinite(array).all():
        return None
    return array.reshape(count, width)


def _encode(mesh, placed, root, captured_bones, reference):
    """Asset-space ``PROP_LAYOUT`` geometry, or (None, reason); (None, None) when not a prop.

    Blocks in order: per-vertex f32 records (position xyz, normal xyz, uv), per-vertex
    u16x4 rig joints, per-vertex f32x4 weights, then u32 triangle indices.
    """
    bone_map, placement = placed
    vertices = _components(mesh.get("vertices"), len(mesh.get("vertices") or []) // 3, (3,))
    if vertices is None or not 0 < len(vertices) <= MAX_PROP_VERTICES:
        return None, "Unsupported prop geometry."
    count = len(vertices)
    indices = np.asarray(mesh.get("indices") or [], dtype=np.int64)
    if not 0 < indices.size <= MAX_PROP_INDICES or indices.size % 3 or indices.min() < 0 or indices.max() >= count:
        return None, "Unsupported prop triangles."
    joints = np.asarray(mesh.get("bone_indices") or [], dtype=np.int64)
    weights = np.asarray(mesh.get("weights") or [], dtype=float)
    if joints.shape != (count, 4) or weights.shape != (count, 4) or not np.isfinite(weights).all():
        return None, "Unsupported prop skin layout."
    positive = weights > 0
    if np.any(joints[positive] < 0) or np.any(joints[positive] >= len(bone_map)):
        return None, "The prop skin references unknown bones."
    rig_joints = np.where(positive, np.asarray(bone_map)[np.where(positive, joints, 0)] + 1, 0)
    if captured_bones.intersection(rig_joints[positive].tolist()):
        return None, None
    normals = _components(mesh.get("normals"), count, (3, 4))
    uv = _components(mesh.get("uv"), count, (2, 3, 4))
    if normals is None or uv is None:
        return None, "The prop has no usable normals or UVs."
    totals = weights.clip(min=0).sum(axis=1)
    if np.any(totals <= 0):
        return None, "The prop has unweighted vertices."
    weights = np.where(positive, weights, 0) / totals[:, None]
    # The rig root holds the mesh-to-Avatar basis, so root @ placement maps native
    # vertices into the captured (asset) space every clip transform acts on.
    conversion = root @ placement
    linear = conversion[:3, :3]
    positions = vertices @ linear.T + conversion[:3, 3]
    normals = normals[:, :3] @ np.linalg.inv(linear)
    lengths = np.linalg.norm(normals, axis=1)
    normals = np.divide(normals, lengths[:, None], out=np.zeros_like(normals), where=lengths[:, None] > 1e-12)
    texcoords = uv[:, :2].copy()
    texcoords[:, 1] = 1 - texcoords[:, 1]
    triangles = indices.reshape(-1, 3)
    # Captured parts keep native triangle order under the body's axis reflection; a
    # prop whose conversion has the opposite handedness reverses its winding.
    if np.sign(np.linalg.det(linear)) != reference:
        triangles = triangles[:, ::-1]
    if not np.isfinite(positions).all() or not np.isfinite(normals).all():
        return None, "Unsupported prop geometry."
    payload = b"".join([
        np.hstack([positions, normals, texcoords]).astype("<f4").tobytes(),
        rig_joints.astype("<u2").tobytes(),
        weights.astype("<f4").tobytes(),
        triangles.astype("<u4").tobytes(),
    ])
    return (payload, count, int(indices.size)), None


def extract(client, chosen, rig, index, library, cancel, mesh_hint, captured_parts, asset):
    """Write verified props under ``library/Props``; returns (entries, skipped).

    Each entry may carry ``textures`` (viewer role -> DDS file) whose material slots
    were proven against ``captured_parts`` of ``asset``; otherwise ``texture_error``
    says why it stays untextured.
    """
    family, avatar = chosen["family"], chosen["avatar"]
    captured = set(chosen["meshes"])
    captured_bones = {bone for palette in chosen["matched"].values() for bone in palette["bone_map"].values()}
    root = rest_worlds(rig["bones"], affine=True)[0]
    signs = next(iter(next(iter(chosen["native"].values()))))["signs"]
    reference = np.sign(np.prod(signs))
    rows = sorted((row for row in index if row["type"] == "Mesh" and mesh_hint(family, row)
                   and not re.search(r"_LOD[1-9]", row["name"], re.I)
                   and zzz_index.identity(row) not in captured), key=zzz_index.identity)
    if len(rows) > 256:
        raise ValueError("The character has too many candidate prop meshes to verify.")
    folder = library / "Props"
    if folder.exists():
        shutil.rmtree(folder)
    found, skipped = {}, []
    for row in rows:
        check_cancelled(cancel)
        try:
            mesh = client.read(row)
        except ExtractionCancelled:
            raise
        except (ValueError, KeyError, TypeError):
            continue
        # Static effect meshes and helpers without a proven placement are not rig props.
        if not mesh.get("weights") or not mesh.get("bone_hashes"):
            continue
        placed = mesh_placement(mesh, avatar)
        if placed is None:
            continue
        encoded, reason = _encode(mesh, placed, root, captured_bones, reference)
        if encoded is None:
            if reason:
                skipped.append(dict(source=zzz_index.identity(row), name=row["name"], reason=reason))
            continue
        found.setdefault(row["name"], {})[hashlib.sha256(encoded[0]).hexdigest()] = (row, encoded, mesh)
    entries, meshes, total = [], [], 0
    for name in sorted(found):
        copies = found[name]
        if len(copies) != 1:
            # Different native versions under one name are never guessed between.
            skipped.append(dict(name=name, reason="Several different native copies exist."))
            continue
        digest, (row, (payload, vertices, indices), mesh) = next(iter(copies.items()))
        if any(entry["sha256"] == digest for entry in entries):
            continue
        if len(entries) == MAX_PROPS or total + len(payload) > MAX_TOTAL_BYTES:
            skipped.append(dict(name=name, reason="The prop budget is exhausted."))
            continue
        folder.mkdir(exist_ok=True)
        file = f"Props/{digest[:32]}.bin"
        (library / file).write_bytes(payload)
        total += len(payload)
        entries.append(dict(id=f"prop-{digest[:12]}", name=name[:128], file=file, layout=PROP_LAYOUT, sha256=digest,
                            vertex_count=vertices, index_count=indices, source=zzz_index.identity(row)))
        meshes.append(mesh)
    if entries:
        zzz_prop_textures.attach(entries, meshes, client, chosen, index, library, cancel, captured_parts, asset)
    return entries, skipped

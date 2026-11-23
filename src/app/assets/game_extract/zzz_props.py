"""Uncaptured ZZZ props (weapons) skinned to the verified rig, shown only during clip playback.

The capture never draws a character's weapon, but the game's model meshes skin it to
bones of the same Avatar. A prop is a native model mesh of the verified family that no
captured component came from, whose inverse binds prove a placement against the
verified Avatar, and whose positive skin weight lies entirely on bones that no captured
palette uses. Names only discover candidates; outfit variants, hair shadows and other
meshes driven by captured bones never qualify.

A prop the clips never place or never lift above the floor is shown only inside the clip
windows where it is: :func:`clip_windows` samples each clip's skinned geometry at extraction time.
Another outfit's mesh that routes to the same renderers as the captured outfit's is told apart by the
renderers' GameObject names.
"""

import hashlib
import json
import re
import shutil

import numpy as np

from app.assets.animations import (MAX_PROP_INDICES, MAX_PROP_VERTICES, MAX_PROP_WINDOWS, MAX_PROPS, PROP_LAYOUT,
                                   pack_prop)
from . import zzz_index, zzz_prop_textures
from .pipeline import rest_worlds
from .runtime import ExtractionCancelled, check_cancelled
from .zzz_native import mesh_placement

MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_VISIBLE_BYTES = 256 * 1024
# Clip-window sampling: pose tolerance for "at rest", and bounded frame chunks.
_REST_TOLERANCE = 1e-3
# A posed prop whose centre sits within this height of the floor is parked on it: float
# noise there would otherwise flicker the prop on and off every few frames.
_GROUND_TOLERANCE = 1e-3
# Native copies of one mesh in different blocks can differ by float serialization noise
# (about 1e-7 in bind poses); copies within this tolerance are the same prop.
_COPY_TOLERANCE = 1e-5
_CHUNK_VERTEX_FRAMES = 4_000_000
_FRAME_WIDTHS = {"frame-major-tqs-f32le": 10, "frame-major-tq-f32le": 7}


def _components(values, count, widths):
    array = np.asarray(values if values is not None else [], dtype=float)
    width = next((width for width in widths if array.size == count * width), None)
    if width is None or not np.isfinite(array).all():
        return None
    return array.reshape(count, width)


def encode(mesh, joint_map, conversion, reference, excluded=frozenset(), section=None):
    """Asset-space ``PROP_LAYOUT`` geometry, or (None, reason); (None, None) when not a prop.

    ``joint_map`` maps each mesh bone to its output joint, and ``conversion`` maps native
    vertices into asset space; weight on an ``excluded`` joint means the mesh is no prop.
    A ``section`` index keeps only that material section's triangles and the vertices
    they use. Blocks in order: per-vertex f32 records (position xyz, normal xyz, uv),
    per-vertex u16x4 joints, per-vertex f32x4 weights, then u32 triangle indices.
    """
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
    if np.any(joints[positive] < 0) or np.any(joints[positive] >= len(joint_map)):
        return None, "The prop skin references unknown bones."
    rig_joints = np.where(positive, np.asarray(joint_map)[np.where(positive, joints, 0)], 0)
    if excluded.intersection(rig_joints[positive].tolist()):
        return None, None
    normals = _components(mesh.get("normals"), count, (3, 4))
    uv = _components(mesh.get("uv"), count, (2, 3, 4))
    if normals is None or uv is None:
        return None, "The prop has no usable normals or UVs."
    totals = weights.clip(min=0).sum(axis=1)
    if np.any(totals <= 0):
        return None, "The prop has unweighted vertices."
    weights = np.where(positive, weights, 0) / totals[:, None]
    linear = conversion[:3, :3]
    positions = vertices @ linear.T + conversion[:3, 3]
    normals = normals[:, :3] @ np.linalg.inv(linear)
    lengths = np.linalg.norm(normals, axis=1)
    normals = np.divide(normals, lengths[:, None], out=np.zeros_like(normals), where=lengths[:, None] > 1e-12)
    texcoords = uv[:, :2].copy()
    texcoords[:, 1] = 1 - texcoords[:, 1]
    if section is not None:
        part = (mesh.get("sections") or [])[section]
        first, size = part["first"], part["count"]
        if type(first) is not int or type(size) is not int or first < 0 or not 0 < size <= indices.size - first or size % 3:
            return None, "Unsupported prop section."
        # Sections may share vertices; each part keeps only the ones its triangles use.
        used, indices = np.unique(indices[first:first + size], return_inverse=True)
        positions, normals, texcoords, rig_joints, weights = (
            values[used] for values in (positions, normals, texcoords, rig_joints, weights))
        count = len(used)
    triangles = indices.reshape(-1, 3)
    # Captured parts keep native triangle order under the body's axis reflection; a
    # prop whose conversion has the opposite handedness reverses its winding.
    if np.sign(np.linalg.det(linear)) != reference:
        triangles = triangles[:, ::-1]
    if not np.isfinite(positions).all() or not np.isfinite(normals).all():
        return None, "Unsupported prop geometry."
    payload = pack_prop(positions, normals, texcoords, rig_joints, weights, triangles)
    return (payload, count, int(indices.size)), None


def encode_parts(mesh, joint_map, conversion, reference, excluded=frozenset()):
    """[encoded geometry] of the whole mesh, or one per section of a multi-material mesh
    (the viewer draws a prop with one material); or (None, reason) as :func:`encode`."""
    whole, reason = encode(mesh, joint_map, conversion, reference, excluded)
    sections = mesh.get("sections") or []
    if whole is None or len(sections) < 2:
        return ([whole] if whole else None), reason
    parts = []
    for section in range(len(sections)):
        part, reason = encode(mesh, joint_map, conversion, reference, excluded, section)
        if part is None:
            return None, reason
        parts.append(part)
    return parts, None


def equivalent_copies(first, second):
    """Whether two encoded part lists hold the same geometry up to float serialization noise.

    Counts, joints and triangles must match exactly; positions, normals, UVs and weights may
    differ by ``_COPY_TOLERANCE``."""
    if len(first) != len(second):
        return False
    for (payload, count, indices), (other, other_count, other_indices) in zip(first, second):
        if (count, indices, len(payload)) != (other_count, other_indices, len(other)):
            return False
        # Layout: f32 records, u16 joints, f32 weights, then u32 triangles (see ``pack_prop``).
        records, joints, weights = count * 32, count * 40, count * 56
        if payload[records:joints] != other[records:joints] or payload[weights:] != other[weights:]:
            return False
        for block in (slice(0, records), slice(joints, weights)):
            if not np.allclose(np.frombuffer(payload[block], "<f4"), np.frombuffer(other[block], "<f4"),
                               rtol=0, atol=_COPY_TOLERANCE):
                return False
    return True


def filter_contexts(windows_by_prop, drawn, played):
    """Each prop's clip windows without the clips that only non-prop-drawing contexts play.

    ``drawn`` maps each known controller context to whether it draws props, and ``played`` maps
    contexts to the clip ids their controllers play. A clip that some known context plays, and
    that no context playing it draws props under, is removed from every prop. Other clips stay.
    """
    hidden = set()
    for clip in set().union(*(played.get(key, set()) for key in drawn)):
        playing = [key for key in drawn if clip in played.get(key, set())]
        if not any(drawn[key] for key in playing):
            hidden.add(clip)
    return [{clip: windows for clip, windows in item.items() if clip not in hidden} for item in windows_by_prop]


def _decode(payload, count):
    """Positions (V,3), rig joints (V,4) and weights (V,4) of an encoded ``PROP_LAYOUT`` payload."""
    records = np.frombuffer(payload, "<f4", count=count * 8).reshape(count, 8)
    joints = np.frombuffer(payload, "<u2", count=count * 4, offset=count * 32).reshape(count, 4)
    weights = np.frombuffer(payload, "<f4", count=count * 4, offset=count * 40).reshape(count, 4)
    return records[:, :3].astype(float), joints.astype(np.int64), weights.astype(float)


def _ancestry(parents, bones):
    """The ``bones`` with all their ancestors (inclusive), as a set."""
    result = set()
    for bone in bones:
        while bone >= 0 and bone not in result:
            result.add(bone)
            bone = parents[bone]
    return result


def _depth(parents, bone):
    depth = 0
    while parents[bone] >= 0:
        bone, depth = parents[bone], depth + 1
    return depth


def _local_matrices(tqs):
    """4x4 affine matrices (..., 4, 4) of translation, quaternion (xyzw) and scale rows (..., 10)."""
    x, y, z, w = (tqs[..., 3 + index] for index in range(4))
    rotation = np.stack([
        np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)], axis=-1),
        np.stack([2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)], axis=-1),
        np.stack([2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)], axis=-1)], axis=-2)
    matrices = np.zeros(tqs.shape[:-1] + (4, 4))
    matrices[..., :3, :3] = rotation * tqs[..., None, 7:10]
    matrices[..., :3, 3] = tqs[..., 0:3]
    matrices[..., 3, 3] = 1
    return matrices


def _at_rest(local, reference):
    """Whether every (frame, bone) transform ``local`` (F,O,10) equals its rest ``reference`` (O,10)."""
    translation = np.abs(local[..., 0:3] - reference[:, 0:3]).max(axis=-1) <= _REST_TOLERANCE
    scale = np.abs(local[..., 7:10] - reference[:, 7:10]).max(axis=-1) <= _REST_TOLERANCE
    dot = np.abs(np.sum(local[..., 3:7] * reference[:, 3:7], axis=-1))
    angle = 2 * np.arccos(np.minimum(1.0, dot))
    return bool((translation & scale & (angle <= _REST_TOLERANCE)).all())


def _windows(mask, duration):
    """Clip windows of the visible frames (frame i at i * dt); None when none or too many."""
    step = duration / (len(mask) - 1)
    edges = np.diff(np.concatenate([[0], mask.astype(np.int64), [0]]))
    starts, ends = np.flatnonzero(edges == 1).tolist(), (np.flatnonzero(edges == -1) - 1).tolist()
    if not starts or len(starts) > MAX_PROP_WINDOWS:
        return None
    return [[round(float(max(0.0, first * step - step / 2)), 6), round(float(min(duration, last * step + step / 2)), 6)]
            for first, last in zip(starts, ends)]


def clip_windows(rig, clips, parts, captured_bones):
    """One {clip_id: [[start, end], ...]} per part: the windows in which each clip shows the prop.

    ``clips`` yields (clip_id, metadata, data) and is consumed once; ``parts`` holds
    (positions, joints, weights) arrays. A part's own bones are the ancestors of its skin
    joints that stop at the body (the captured bones and their ancestors). When every own
    bone and frame sits at its rig rest pose, the clip does not place the prop and shows
    nothing. Otherwise a frame shows the prop while the centre height of its posed vertices
    is at least ``_GROUND_TOLERANCE`` above 0 (the Avatar's floor in asset space). A clip with more than
    ``MAX_PROP_WINDOWS`` windows, or none, is omitted.
    """
    if not parts:
        return []
    bones = rig["bones"]
    parents = [int(bone["parent"]) for bone in bones]
    body = _ancestry(parents, captured_bones)
    rest = np.array([[*bone["translation"], *bone["rotation"], *bone["scale"]] for bone in bones], dtype=float)
    rest_world = np.asarray(rest_worlds(bones, affine=True), dtype=float)
    inverse = np.linalg.inv(rest_world)
    root = rest_world[0]
    origin = root[:3, 3]
    normal = np.linalg.inv(root[:3, :3]).T @ np.array([0.0, 1.0, 0.0])
    normal = normal / np.linalg.norm(normal)
    plans, skinned = [], set()
    for positions, joints, weights in parts:
        own = set()
        for joint in np.unique(joints[weights > 0]).tolist():
            bone = joint
            while bone >= 0 and bone not in body and bone not in own:
                own.add(bone)
                bone = parents[bone]
        unique, inverse_index = np.unique(joints, return_inverse=True)
        skinned.update(unique.tolist())
        # Influence slots no vertex weights are skipped when sampling heights.
        slots = [slot for slot in range(4) if np.any(weights[:, slot] > 0)]
        plans.append((positions, weights, unique, inverse_index.reshape(joints.shape), sorted(own), slots))
    # Only the skin joints and their ancestors are composed, parents first.
    order = sorted(_ancestry(parents, skinned), key=lambda bone: _depth(parents, bone))
    column = {bone: number for number, bone in enumerate(order)}
    plans = [(positions, weights, [column[bone] for bone in unique.tolist()], inverse_index,
              [column[bone] for bone in own], slots)
             for positions, weights, unique, inverse_index, own, slots in plans]
    largest = max(len(plan[0]) for plan in plans)
    result = [{} for _ in plans]
    for clip_id, metadata, data in clips:
        frames, count = int(metadata["frame_count"]), int(metadata["bone_count"])
        width = _FRAME_WIDTHS[metadata["layout"]]
        duration = float(metadata["duration"])
        values = np.frombuffer(data, "<f4", count=frames * count * width).reshape(frames, count, width)
        step = max(1, min(frames, _CHUNK_VERTEX_FRAMES // largest))
        masks = [np.zeros(frames, dtype=bool) for _ in plans]
        placed = [False] * len(plans)
        for start in range(0, frames, step):
            stop = min(frames, start + step)
            tqs = np.array(values[start:stop, order], dtype=float)
            if width == 7:
                tqs = np.concatenate([tqs, np.broadcast_to(rest[order, 7:10], (stop - start, len(order), 3))], axis=-1)
            locals_ = _local_matrices(tqs)
            world = np.empty_like(locals_)
            for number, bone in enumerate(order):
                parent = parents[bone]
                world[:, number] = locals_[:, number] if parent < 0 else world[:, column[parent]] @ locals_[:, number]
            posed = world @ inverse[order]
            for number, (positions, weights, columns, inverse_index, own, slots) in enumerate(plans):
                if own and not placed[number]:
                    placed[number] = not _at_rest(tqs[:, own], rest[order][own])
                selected = posed[:, columns]
                linear_a = np.einsum("i,fuij->fuj", normal, selected[..., :3, :3])
                constant = np.einsum("fui,i->fu", selected[..., :3, 3], normal) - normal @ origin
                heights = np.zeros((stop - start, len(positions)))
                for slot in slots:
                    index = inverse_index[:, slot]
                    heights += weights[:, slot] * (np.einsum("fvd,vd->fv", linear_a[:, index], positions)
                                                   + constant[:, index])
                centre = (heights.max(axis=1) + heights.min(axis=1)) / 2
                masks[number][start:stop] = centre >= _GROUND_TOLERANCE
        for number, (_positions, _weights, _unique, _index, own, _slots) in enumerate(plans):
            # A clip that leaves the own bones at rest does not place this prop.
            if own and not placed[number]:
                continue
            windows = _windows(masks[number], duration)
            if windows:
                result[number][clip_id] = windows
    return result


def _part_windows(reader, chosen, rig, clips, pending, captured_bones, controller_clips):
    """{part digest: clip windows} for the pending parts, without the clips that only
    contexts drawing no props play (when ``controller_clips`` is given)."""
    parts = {}
    for _name, row, _parts, mesh, _number, payload, vertices, _indices, digest in pending:
        parts.setdefault(digest, (_decode(payload, vertices), mesh, zzz_index.identity(row)))
    digests = list(parts)
    windows = clip_windows(rig, clips, [parts[digest][0] for digest in digests], captured_bones)
    if controller_clips is not None:
        shown = [parts[digest][1:] for digest, item in zip(digests, windows) if item]
        drawn = zzz_prop_textures.contexts(reader, chosen, shown) if shown else {}
        if drawn:
            windows = filter_contexts(windows, drawn, controller_clips(list(drawn)))
    return dict(zip(digests, windows))


def extract(client, chosen, rig, index, library, cancel, mesh_hint, captured_parts, asset, *,
            placement_tolerance=2e-4, admit=None, textures=None, clips=None, alternates=False,
            controller_clips=None, timed=None):
    """Write verified props under ``library/Props``; returns (entries, skipped).

    Each entry may carry ``textures`` (viewer role -> DDS file) whose material slots
    were proven against ``captured_parts`` of ``asset``; otherwise ``texture_error``
    says why it stays untextured. ``admit(row, mesh)`` may add a game's own evidence
    to the attachment proof, returning why a candidate is not a prop (or None).
    ``textures`` holds a game's ``zzz_prop_textures.attach`` routing and payload options.
    ``clips`` (clip_id, metadata, data) triples give each written prop its ``visible``
    clip windows (see :func:`clip_windows`); a prop no clip shows is not written.
    ``alternates`` drops another outfit's meshes that route to the same renderers
    (see ``zzz_prop_textures.alternates``). ``controller_clips(keys)`` gives the clip ids each
    renderer context's controller plays; clips played only under contexts that draw no props
    lose their windows (see ``zzz_prop_textures.contexts`` and :func:`filter_contexts`).
    ``timed(row)`` limits the clip windows to the props it accepts (default: every prop);
    the others are written without ``visible`` and shown in every clip.
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
        placed = mesh_placement(mesh, avatar, placement_tolerance)
        if placed is None:
            continue
        # The rig root holds the mesh-to-Avatar basis, so root @ placement maps native
        # vertices into the captured (asset) space every clip transform acts on.
        bone_map, placement = placed
        encoded, reason = encode_parts(mesh, np.asarray(bone_map) + 1, root @ placement, reference, captured_bones)
        if encoded is None:
            if reason:
                skipped.append(dict(source=zzz_index.identity(row), name=row["name"], reason=reason))
            continue
        reason = admit(row, mesh) if admit else None
        if reason:
            skipped.append(dict(source=zzz_index.identity(row), name=row["name"], reason=reason))
            continue
        copies = found.setdefault(row["name"], [])
        # Copies are read in identity order, so the first of equivalent copies is the one kept.
        if not any(equivalent_copies(known[1], encoded) for known in copies):
            copies.append((row, encoded, mesh))
    shared = zzz_prop_textures.Reader(client, index, cancel, (textures or {}).get("route"))
    single = {name: copies[0] for name, copies in found.items() if len(copies) == 1}
    dropped = zzz_prop_textures.alternates(
        shared, chosen, [(name, mesh, zzz_index.identity(row)) for name, (row, _parts, mesh) in sorted(single.items())]
    ) if alternates else {}
    # Parts in write order: (name, row, parts, mesh, section number, payload, vertices, indices, digest).
    pending = []
    for name in sorted(found):
        if name not in single:
            # Different native versions under one name are never guessed between.
            skipped.append(dict(name=name, reason="Several different native copies exist."))
            continue
        row, parts, mesh = single[name]
        if name in dropped:
            skipped.append(dict(name=name, source=zzz_index.identity(row), reason=dropped[name]))
            continue
        for number, (payload, vertices, indices) in enumerate(parts):
            pending.append((name, row, parts, mesh, number, payload, vertices, indices, hashlib.sha256(payload).hexdigest()))
    clocked = [clips is not None and (timed is None or timed(item[1])) for item in pending]
    windows = _part_windows(shared, chosen, rig, clips, [item for item, on in zip(pending, clocked) if on],
                            captured_bones, controller_clips) if any(clocked) else {}
    entries, meshes, total, visible_total, exhausted = [], [], 0, 0, None
    for (name, row, parts, mesh, number, payload, vertices, indices, digest), clock in zip(pending, clocked):
        if name == exhausted or any(entry["sha256"] == digest for entry in entries):
            continue
        # A multi-material mesh is one prop per material section, each textured from its own.
        label = name if len(parts) == 1 else f"{name} {number + 1}"
        visible = windows.get(digest) if clock else None
        if clock and not visible:
            skipped.append(dict(name=label, source=zzz_index.identity(row), reason="No clip shows the prop."))
            continue
        if len(entries) == MAX_PROPS or total + len(payload) > MAX_TOTAL_BYTES:
            skipped.append(dict(name=name, reason="The prop budget is exhausted."))
            exhausted = name
            continue
        size = 0 if visible is None else len(json.dumps(visible, separators=(",", ":")))
        if visible_total + size > MAX_VISIBLE_BYTES:
            skipped.append(dict(name=name, reason="The prop budget is exhausted."))
            continue
        visible_total += size
        folder.mkdir(exist_ok=True)
        file = f"Props/{digest[:32]}.bin"
        (library / file).write_bytes(payload)
        total += len(payload)
        entries.append(dict(id=f"prop-{digest[:12]}", name=label[:128], file=file, layout=PROP_LAYOUT, sha256=digest,
                            vertex_count=vertices, index_count=indices, source=zzz_index.identity(row),
                            **({"section": number} if len(parts) > 1 else {}),
                            **({"visible": visible} if visible is not None else {})))
        meshes.append(mesh)
    if entries:
        zzz_prop_textures.attach(entries, meshes, client, chosen, index, library, cancel, captured_parts, asset,
                                 **(textures or {}), reader=shared)
    return entries, skipped

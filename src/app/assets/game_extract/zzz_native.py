"""Prove Unity meshes against captured ZZZ triangles, UVs and float skin weights."""

import hashlib
import itertools
import math
from collections import Counter
from pathlib import Path
import struct

import numpy as np

from app.assets import paths
from app.assets.loader.hash_asset import load_hash_asset
from core.geometry.migoto_dump import parse_vertex_dump
from .native import read_limited
from .pipeline import rest_worlds

_LABELS = {"ZZMI": "ZZZ", "GIMI": "Genshin Impact"}


def capture(root, record, asset_type="ZZMI"):
    label = _LABELS[asset_type]
    result = load_hash_asset(asset_type, root, record)
    asset = Path(paths.safe_asset_dir(root, record["path"]) or "")
    components, weightless, files, dumps = [], [], {}, {}
    for part in result.parts:
        manifest = part.skinning or part.attachment
        if manifest is None:
            continue
        vb = paths.safe_asset_path(root, manifest.file)
        ib = paths.safe_asset_path(root, manifest.index_file)
        metadata = paths.safe_asset_path(root, part.metadata_path)
        if not all((vb, ib, metadata)):
            raise ValueError(f"The captured {label} geometry is unavailable.")
        for file in (vb, ib, metadata):
            if file not in files:
                files[file] = hashlib.sha256(read_limited(file, 512 * 1024 * 1024, "Captured geometry")).hexdigest()
        if vb not in dumps:
            dumps[vb] = parse_vertex_dump(vb, include_skinning=True)
        dump = dumps[vb]
        component = dict(geometry_hash=part.geometry_hash, component_ordinal=part.component_ordinal,
            first_index=part.first_index, index_count=part.index_count,
            vertex_sha256=files[vb], index_sha256=files[ib], metadata_sha256=files[metadata],
            vertex_file=Path(vb).relative_to(asset).as_posix(), index_file=Path(ib).relative_to(asset).as_posix(),
            metadata_file=Path(metadata).relative_to(asset).as_posix(), metadata_entry=part.metadata_entry,
            source_vertex_count=dump.layout.vertex_count,
            source_vertices=manifest.vertices,
            positions=np.frombuffer(part.positions, dtype="<f4").reshape(-1, 3),
            normals=np.frombuffer(part.normals, dtype="<f4").reshape(-1, 3) if part.normals else None,
            uv=np.frombuffer(part.uvs, dtype="<f4").reshape(-1, 2) if part.uvs else None,
            indices=np.frombuffer(part.indices, dtype="<u4").reshape(-1, 3))
        if part.skinning is None:
            # Weightless captures (GIMI faces) are proven separately as attachments
            # that take a native mesh's own weights; they never carry a Blend palette.
            weightless.append({**component, "weightless": True})
            continue
        if not dump.blend_data:
            continue
        blends = [struct.unpack_from("<4f4I", dump.blend_data, vertex * 32) for vertex in manifest.vertices]
        components.append({**component, "blend_sha256": hashlib.sha256(dump.blend_data).hexdigest(), "blends": blends})
    if not components:
        raise ValueError(f"{label} rig extraction requires captured vertex weights and index dumps.")
    return components + weightless, files


def avatar_bones(avatar):
    rows = avatar["bones"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= 4096:
        raise ValueError("The native Avatar skeleton exceeds its limits.")
    paths_seen, hashes = set(), set()
    bones = []
    for index, row in enumerate(rows):
        path = row["path"]
        if (not isinstance(path, str) or len(path) > 4096 or path in paths_seen or row["hash"] in hashes
                or type(row["parent"]) is not int or not -1 <= row["parent"] < index):
            raise ValueError("The native Avatar hierarchy is ambiguous.")
        paths_seen.add(path)
        hashes.add(row["hash"])
        # Names are stable whole-path identities, bounded independently from Unity's paths.
        name = path if 0 < len(path) <= 256 else "root" if not path else "path-" + hashlib.sha256(path.encode()).hexdigest()
        bones.append({key: row[key] for key in ("parent", "translation", "rotation", "scale")} | {"name": name})
    rest_worlds(bones, affine=True)
    return bones


# Game data can leave a lightly weighted bone's inverse bind slightly off its Avatar
# rest. Such bones may disagree with the placement only within these bounds.
DRIFT_WEIGHT_SHARE = .01
DRIFT_LIMIT = .02


def mesh_placement(mesh, avatar, tolerance=2e-4):
    """Require the used inverse binds to prove one common mesh-to-Avatar placement.

    Bones carrying at least 99% of the positive skin weight must agree within
    ``tolerance``; the remaining used bones may drift from it by at most DRIFT_LIMIT.
    """
    bones = avatar_bones(avatar)
    worlds = rest_worlds(bones, affine=True)
    by_hash = {bone["hash"]: index for index, bone in enumerate(avatar["bones"])}
    hashes, poses = mesh["bone_hashes"], mesh["bind_poses"]
    if len(hashes) != len(poses) or not hashes or len(hashes) > 4096:
        raise ValueError("The mesh bind-pose table is invalid.")
    mapping = [by_hash.get(value) for value in hashes]
    if any(value is None for value in mapping) or len(set(mapping)) != len(mapping):
        return None
    load = Counter()
    for indices, weights in zip(mesh["bone_indices"], mesh["weights"]):
        for bone, weight in zip(indices, weights):
            if weight > 0:
                load[bone] += weight
    if not load or any(type(bone) is not int or not 0 <= bone < len(mapping) for bone in load):
        return None
    used = sorted(load, key=lambda bone: (-load[bone], bone))
    # ReadMatrix and the native indexer preserve ZZZ's serialized row-major
    # float order despite Matrix4x4's column-major field names.
    matrices = np.stack([worlds[mapping[bone]] @ np.asarray(poses[bone]).reshape(4, 4) for bone in used])
    shares = np.array([load[bone] for bone in used], dtype=float)
    shares /= shares.sum()
    rejected = 0.0
    # Try anchors from the heaviest bone down; rejected anchors are drifting bones,
    # so the search stops once they exceed the share drift may carry.
    for anchor, share in enumerate(shares):
        if rejected > DRIFT_WEIGHT_SHARE:
            break
        deviation = np.abs(matrices - matrices[anchor]).reshape(len(used), -1).max(axis=1)
        # Negated comparisons count a non-finite bind as disagreement.
        if shares[~(deviation <= tolerance)].sum() <= DRIFT_WEIGHT_SHARE and np.all(deviation <= DRIFT_LIMIT):
            return mapping, matrices[anchor]
        rejected += share
    return None


def match_component(part, mesh):
    """Find a unique complete triangle correspondence and one positive-weight palette."""
    matches = match_component_candidates(part, mesh)
    return matches[0] if len(matches) == 1 else None


def _native_geometry(part, mesh):
    """Bounded native (positions, viewer-space UVs, triangles) for a UV-carrying capture, or None."""
    native = np.asarray(mesh["vertices"], dtype=float).reshape(-1, 3)
    if part["uv"] is None or not 0 < len(native) <= 250000 or not np.isfinite(native).all():
        return None
    # Captures contain mesh-local coordinates. The verified inverse bind placement
    # is applied to the Avatar root when building the rig, never to capture data.
    uv = np.asarray(mesh["uv"], dtype=float)
    # UV-less helper meshes are valid native candidates, but cannot establish
    # captured position/UV correspondence. Reject them before reshaping.
    if uv.ndim != 1 or uv.size not in (len(native) * 2, len(native) * 3, len(native) * 4):
        return None
    uv = uv.reshape(len(native), -1)[:, :2].copy()
    if not np.isfinite(uv).all():
        return None
    uv[:, 1] = 1 - uv[:, 1]
    indices = np.asarray(mesh["indices"], dtype=int).reshape(-1, 3)
    if not 0 < len(indices) <= 500000 or np.min(indices) < 0 or np.max(indices) >= len(native):
        return None
    return native, uv, indices


def match_component_candidates(part, mesh, weight_drift=0.0):
    """Retain coordinate hypotheses for the model-wide placement proof.

    ``weight_drift`` is the largest per-influence difference a game may show between its
    captured Blend stream and the native mesh; zero demands exact weights.
    """
    geometry = _native_geometry(part, mesh)
    if geometry is None:
        return []
    native, uv, indices = geometry
    # Unity and captured coordinates can differ by one axis reflection. Every
    # triangle and weight must prove the same conversion; never fit arbitrary geometry.
    matches = []
    for signs in ((1, 1, 1), (-1, 1, 1), (1, -1, 1), (1, 1, -1)):
        positions = native * signs
        original = np.asarray(part["source_vertices"], dtype=int)
        mapping = {}
        if (len(original) == len(part["positions"]) and np.max(original) < len(native)
                and np.allclose(part["positions"], positions[original], rtol=0, atol=1e-4)
                and np.allclose(part["uv"], uv[original], rtol=0, atol=1e-4)):
            if _section_triangles(part, mesh, indices, dict(enumerate(original))):
                mapping = dict(enumerate(original))
        if not mapping:
            mapping = _triangle_mapping(part, positions, uv, indices)
        if mapping is None or not _section_triangles(part, mesh, indices, mapping):
            continue
        palette = _weight_palette(part, mesh, mapping) or (
            _drifted_weight_palette(part, mesh, mapping, weight_drift) if weight_drift else None)
        if palette:
            matches.append((palette, signs, mapping))
    return matches


ATTACHMENT_TOLERANCE = 1e-4
# Captured expressions are clamped game weights; allow only least-squares noise outside 0..1.
EXPRESSION_SLACK = 0.05


def _native_influences(mesh, count):
    """Native (weights, slots) arrays, each vertex fully weighted, or None."""
    weights = np.asarray(mesh.get("weights") or [], dtype=float)
    slots = np.asarray(mesh.get("bone_indices") or [])
    bones = len(mesh.get("bone_hashes") or [])
    if (weights.shape != (count, 4) or slots.shape != (count, 4) or slots.dtype.kind not in "iu"
            or not np.isfinite(weights).all() or np.any(weights < 0) or np.any(slots < 0)
            or np.any(slots[weights > 0] >= bones) or np.any(np.abs(weights.sum(axis=1) - 1) > 1e-3)):
        return None
    return weights, slots.astype(int)


def _shape_deltas(mesh, count):
    """Single-frame native blend shapes as (hashes, full-weight deltas[K, N, 3])."""
    raw = mesh.get("blend_shapes") or {}
    channels, frames, weights, vertices = (raw.get(key) or [] for key in ("channels", "frames", "weights", "vertices"))
    hashes, deltas = [], []
    for channel in channels[:256]:
        first, frame_count = channel.get("first"), channel.get("count")
        if (frame_count != 1 or type(first) is not int or not 0 <= first < min(len(frames), len(weights))
                or type(channel.get("hash")) is not int or channel["hash"] in hashes):
            continue
        frame, full = frames[first], weights[first]
        begin, size = frame.get("first"), frame.get("count")
        if (type(full) not in (int, float) or not math.isfinite(full) or full <= 0
                or type(begin) is not int or type(size) is not int or begin < 0 or size < 0 or begin + size > len(vertices)):
            continue
        delta = np.zeros((count, 3))
        for row in vertices[begin:begin + size]:
            index = row.get("index")
            if type(index) is not int or not 0 <= index < count:
                break
            delta[index] = row["position"]
        else:
            if np.isfinite(delta).all():
                hashes.append(channel["hash"])
                deltas.append(delta)
    return hashes, (np.stack(deltas) if deltas else np.zeros((0, count, 3)))


def _uv_cells(uv):
    cells = {}
    for index, key in enumerate(map(tuple, np.floor(uv / ATTACHMENT_TOLERANCE).astype(np.int64))):
        cells.setdefault(key, []).append(index)
    return cells


def _uv_candidates(cells, uv, texcoord):
    key = np.floor(np.asarray(texcoord) / ATTACHMENT_TOLERANCE).astype(np.int64)
    found = [index for du in (-1, 0, 1) for dv in (-1, 0, 1)
             for index in cells.get((int(key[0] + du), int(key[1] + dv)), ())]
    return [index for index in found if np.all(np.abs(uv[index] - texcoord) <= ATTACHMENT_TOLERANCE)]


def _explained_mapping(part, positions, uv, deltas, indices):
    """Map each captured vertex to its unique native vertex at native rest plus one shared expression.

    Every captured vertex must have a same-UV native vertex whose rest position,
    displaced by the solved blend-shape weights, matches within tolerance, and no
    other same-UV vertex may. Returns (mapping, weights) or None.
    """
    cells = _uv_cells(uv)
    candidates = [_uv_candidates(cells, uv, texcoord) for texcoord in part["uv"]]
    if any(not items for items in candidates) or len(candidates) > 250000:
        return None
    captured = np.asarray(part["positions"], dtype=float)
    weights = np.zeros(len(deltas))
    chosen = None
    for _round in range(4):
        posed = positions + np.tensordot(weights, deltas, axes=1) if len(deltas) else positions
        chosen = np.array([min(items, key=lambda index: np.linalg.norm(posed[index] - point))
                           for items, point in zip(candidates, captured)])
        if not len(deltas):
            break
        system = deltas[:, chosen, :].reshape(len(deltas), -1).T
        solved, *_ = np.linalg.lstsq(system, (captured - positions[chosen]).ravel(), rcond=None)
        if np.allclose(solved, weights, rtol=0, atol=1e-7):
            break
        weights = solved
    if np.any(weights < -EXPRESSION_SLACK) or np.any(weights > 1 + EXPRESSION_SLACK):
        return None
    posed = positions + np.tensordot(weights, deltas, axes=1) if len(deltas) else positions
    options = [{index for index in items if np.all(np.abs(posed[index] - point) <= ATTACHMENT_TOLERANCE)}
               for items, point in zip(candidates, captured)]
    mapping = _triangle_assignment(part["indices"], options, indices)
    return None if mapping is None else (mapping, weights)


def _triangle_assignment(captured, options, indices):
    """Resolve each captured vertex's explained candidates through shared native triangles.

    Seam duplicates share position and UV; only their triangles tell them apart.
    Every captured triangle must keep a native triangle consistent with its
    vertices' options, and every vertex must end with exactly one distinct native vertex.
    """
    if any(not option for option in options):
        return None
    by_vertex = {}
    for number, row in enumerate(indices):
        for vertex in set(row.tolist()):
            by_vertex.setdefault(vertex, []).append(number)
    rows = [tuple(int(v) for v in row) for row in captured]
    for _pass in range(8):
        changed = False
        for row in rows:
            if len(set(row)) != 3:
                return None
            found = [set(), set(), set()]
            for number in {t for v in options[row[0]] for t in by_vertex.get(v, ())}:
                for order in itertools.permutations(indices[number].tolist()):
                    if all(order[k] in options[row[k]] for k in range(3)):
                        for k in range(3):
                            found[k].add(order[k])
            for k, vertex in enumerate(row):
                narrowed = options[vertex] & found[k]
                if not narrowed:
                    return None
                if narrowed != options[vertex]:
                    options[vertex], changed = narrowed, True
        if not changed:
            break
    if any(len(option) != 1 for option in options):
        return None
    mapping = {compact: next(iter(option)) for compact, option in enumerate(options)}
    return mapping if len(set(mapping.values())) == len(mapping) else None


def match_attachment_candidates(part, mesh):
    """Prove a weightless capture against a native skinned mesh and adopt its own weights.

    The capture may show the mesh in an expression: its positions must equal the
    native rest pose plus one solved blend-shape combination, every vertex matched
    uniquely by UV and explained position, and its triangles must form a
    sub-multiset of one native section. The drawn subset then binds through the
    native per-vertex influences. Returns (palette, signs, mapping, attachment).
    """
    geometry = _native_geometry(part, mesh)
    influences = _native_influences(mesh, len(geometry[0])) if geometry else None
    if influences is None:
        return []
    native, uv, indices = geometry
    hashes, deltas = _shape_deltas(mesh, len(native))
    matches = []
    for signs in ((1, 1, 1), (-1, 1, 1), (1, -1, 1), (1, 1, -1)):
        found = _explained_mapping(part, native * signs, uv, deltas * signs, indices)
        if found is None or not _section_triangles(part, mesh, indices, found[0], complete=False):
            continue
        mapping, expression = found
        order = [mapping[compact] for compact in range(len(mapping))]
        weights, slots = influences[0][order], influences[1][order]
        slots[weights <= 0] = 0
        used = sorted({int(slot) for slot in slots[weights > 0]})
        attachment = dict(weights=weights.ravel().tolist(), slots=slots.ravel().tolist(),
                          expression={str(key): float(value) for key, value in zip(hashes, expression)})
        matches.append(({str(slot): slot for slot in used}, signs, mapping, attachment))
    return matches


def _section_triangles(part, mesh, indices, mapping, complete=True):
    """Prove the captured triangle multiset (repeats included) against one native section.

    ``complete`` requires the whole section; otherwise the capture may be a
    sub-multiset of it (weightless attachments draw parts of a section).
    """
    if len(mapping) != len(part["positions"]):
        return False
    captured = Counter(tuple(sorted(mapping[int(vertex)] for vertex in row)) for row in part["indices"])
    size = len(part["indices"]) * 3
    for section in mesh.get("sections", [dict(first=0, count=len(indices) * 3)]):
        first, count = section["first"], section["count"]
        if (type(first) is not int or type(count) is not int or first < 0 or first % 3 or count % 3
                or (count != size if complete else count < size) or first + count > len(indices) * 3):
            continue
        native = Counter(tuple(sorted(row)) for row in indices[first // 3:(first + count) // 3])
        if captured == native if complete else all(native[row] >= number for row, number in captured.items()):
            return True
    return False


def _triangle_mapping(part, positions, uv, indices):
    signatures = {}
    def key(point, texcoord):
        # Native JSON reads as float64 while captured attributes are float32.
        # Hash both in the authored float32 domain, with integer grid keys.
        return tuple(round(float(value) * 10000) for value in np.asarray((*point, *texcoord), dtype=np.float32))
    native_keys = [key(p, t) for p, t in zip(positions, uv)]
    captured_keys = [key(p, t) for p, t in zip(part["positions"], part["uv"])]
    # A triangle match necessarily contains every referenced position/UV key.
    # Reject impossible coordinate candidates before building all triangle rotations.
    if not set(captured_keys).issubset(set(native_keys)):
        return None
    for triangle in indices:
        for shift in range(3):
            row = tuple(np.roll(triangle, shift))
            signature = tuple(native_keys[i] for i in row)
            signatures.setdefault(signature, []).append(row)
            reverse = row[0], row[2], row[1]
            signatures.setdefault(tuple(native_keys[i] for i in reverse), []).append(reverse)
    mapping = {}
    valid = True
    for triangle in part["indices"]:
        candidates = signatures.get(tuple(captured_keys[i] for i in triangle), [])
        candidates = {candidate for candidate in candidates if all(
            np.allclose(part["positions"][a], positions[b], rtol=0, atol=1e-4)
            and np.allclose(part["uv"][a], uv[b], rtol=0, atol=1e-4)
            for a, b in zip(triangle, candidate))}
        if len(candidates) != 1:
            valid = False
            break
        for captured, source in zip(triangle, candidates.pop()):
            previous = mapping.setdefault(int(captured), int(source))
            if previous != source:
                valid = False
                break
    return mapping if valid and len(mapping) == len(part["positions"]) else None


def _weight_palette(part, mesh, mapping):
    domains = {}
    for captured, source in mapping.items():
        blend = part["blends"][captured]
        left = sorted((float(w), int(b)) for w, b in zip(blend[:4], blend[4:]) if w > 0)
        right = sorted((float(w), int(b)) for w, b in zip(mesh["weights"][source], mesh["bone_indices"][source]) if w > 0)
        if len(left) != len(right):
            return None
        for wa, a in left:
            possible = {b for wb, b in right if abs(wa - wb) <= 2e-5}
            domains[a] = domains.get(a, possible) & possible
            if not domains[a]:
                return None
    palette = {}
    while domains:
        singles = {a: next(iter(values)) for a, values in domains.items() if len(values) == 1}
        if not singles or len(set(singles.values())) != len(singles):
            return None
        palette.update(singles)
        domains = {a: values - set(singles.values()) for a, values in domains.items() if a not in singles}
        if any(not values for values in domains.values()):
            return None
    # Domain membership alone cannot prove repeated influences on one vertex.
    for captured, source in mapping.items():
        blend = part["blends"][captured]
        left = sorted((palette[int(b)], float(w)) for w, b in zip(blend[:4], blend[4:]) if w > 0)
        right = sorted((int(b), float(w)) for w, b in zip(mesh["weights"][source], mesh["bone_indices"][source]) if w > 0)
        if any(a != b or abs(wa - wb) > 2e-5 for (a, wa), (b, wb) in zip(left, right)):
            return None
    return {str(a): b for a, b in sorted(palette.items())} if palette else None


# A captured influence this light is runtime noise, not a skinning decision.
_NOISE_WEIGHT = 0.0025
# Share of vertices that may disagree with a proven palette (seam and noise outliers).
_DRIFT_AGREEMENT = 0.99


def _drifted_weight_palette(part, mesh, mapping, drift):
    """Palette for geometry proven exactly whose captured weights drift slightly from native.

    Only vertices whose influences pair unambiguously by weight order vote, so a captured
    id must map to one native bone by a clear majority, distinct ids to distinct bones, and
    nearly every vertex must then agree with the native influences within ``drift``. Every
    captured id with any weight must be mapped, or the palette could never bind.
    """
    def influences(weights, bones):
        return sorted(((float(w), int(b)) for w, b in zip(weights, bones) if w > _NOISE_WEIGHT), reverse=True)

    votes, rows = {}, []
    for captured, source in mapping.items():
        blend = part["blends"][captured]
        left = influences(blend[:4], blend[4:])
        right = influences(mesh["weights"][source], mesh["bone_indices"][source])
        rows.append((left, right))
        if (not left or len(left) != len(right)
                or any(abs(a - b) > drift for (a, _), (b, _) in zip(left, right))
                # Neighbours closer than twice the drift can swap order between streams.
                or any(a - b <= 2 * drift for (a, _), (b, _) in zip(left, left[1:]))
                or any(a - b <= 2 * drift for (a, _), (b, _) in zip(right, right[1:]))):
            continue
        for (_, a), (_, b) in zip(left, right):
            votes.setdefault(a, Counter())[b] += 1
    palette = _majority_palette(votes)
    if palette is None:
        return None
    # The viewer must map every captured id with any weight, including ids seen only as
    # noise. Those pair a vertex's one remaining light influence with its one remaining
    # light native influence, by the same clear majority; an unpaired id rejects the palette.
    used, light_votes = set(), {}
    for captured, source in mapping.items():
        blend = part["blends"][captured]
        used.update(int(b) for w, b in zip(blend[:4], blend[4:]) if w > 0)
        light = [int(b) for w, b in zip(blend[:4], blend[4:]) if 0 < w <= _NOISE_WEIGHT and int(b) not in palette]
        native = [int(b) for w, b in zip(mesh["weights"][source], mesh["bone_indices"][source])
                  if 0 < w <= _NOISE_WEIGHT and int(b) not in palette.values()]
        if len(light) == len(native) == 1:
            light_votes.setdefault(light[0], Counter())[native[0]] += 1
    light_palette = _majority_palette(light_votes)
    if light_palette is None:
        return None
    palette.update(light_palette)
    if len(set(palette.values())) != len(palette) or not used <= palette.keys():
        return None
    disagree = 0
    for left, right in rows:
        native = {bone: weight for weight, bone in right}
        if (any(local not in palette for _, local in left)
                or {palette[local]: weight for weight, local in left}.keys() != native.keys()
                or any(abs(weight - native[palette[local]]) > drift for weight, local in left)):
            disagree += 1
    if not palette or disagree > (1 - _DRIFT_AGREEMENT) * len(rows):
        return None
    return {str(a): b for a, b in sorted(palette.items())}


def _majority_palette(votes):
    """Each captured id's native bone by a clear majority of its votes, or None."""
    palette = {}
    for local, counter in votes.items():
        best, count = counter.most_common(1)[0]
        if count < _DRIFT_AGREEMENT * sum(counter.values()):
            return None
        palette[local] = best
    return palette

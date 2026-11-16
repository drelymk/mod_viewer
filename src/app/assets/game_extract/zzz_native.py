"""Prove Unity meshes against captured ZZZ triangles, UVs and float skin weights."""

import hashlib
from collections import Counter
from pathlib import Path
import struct

import numpy as np

from app.assets import paths
from app.assets.loader.hash_asset import load_hash_asset
from core.geometry.migoto_dump import parse_vertex_dump
from .native import read_limited
from .pipeline import rest_worlds


def capture(root, record):
    result = load_hash_asset("ZZMI", root, record)
    asset = Path(paths.safe_asset_dir(root, record["path"]) or "")
    components, files, dumps = [], {}, {}
    for part in result.parts:
        manifest = part.skinning
        if manifest is None:
            continue
        vb = paths.safe_asset_path(root, manifest.file)
        ib = paths.safe_asset_path(root, manifest.index_file)
        metadata = paths.safe_asset_path(root, part.metadata_path)
        if not all((vb, ib, metadata)):
            raise ValueError("The captured ZZZ geometry is unavailable.")
        for file in (vb, ib, metadata):
            if file not in files:
                files[file] = hashlib.sha256(read_limited(file, 512 * 1024 * 1024, "Captured geometry")).hexdigest()
        if vb not in dumps:
            dumps[vb] = parse_vertex_dump(vb, include_skinning=True)
        dump = dumps[vb]
        if not dump.blend_data:
            continue
        blends = [struct.unpack_from("<4f4I", dump.blend_data, vertex * 32) for vertex in manifest.vertices]
        components.append(dict(geometry_hash=part.geometry_hash, component_ordinal=part.component_ordinal,
            first_index=part.first_index, index_count=part.index_count,
            vertex_sha256=files[vb], index_sha256=files[ib], metadata_sha256=files[metadata],
            vertex_file=Path(vb).relative_to(asset).as_posix(), index_file=Path(ib).relative_to(asset).as_posix(),
            metadata_file=Path(metadata).relative_to(asset).as_posix(), metadata_entry=part.metadata_entry,
            blend_sha256=hashlib.sha256(dump.blend_data).hexdigest(),
            source_vertex_count=dump.layout.vertex_count,
            source_vertices=manifest.vertices,
            positions=np.frombuffer(part.positions, dtype="<f4").reshape(-1, 3),
            normals=np.frombuffer(part.normals, dtype="<f4").reshape(-1, 3) if part.normals else None,
            uv=np.frombuffer(part.uvs, dtype="<f4").reshape(-1, 2) if part.uvs else None,
            indices=np.frombuffer(part.indices, dtype="<u4").reshape(-1, 3), blends=blends))
    if not components:
        raise ValueError("ZZZ rig extraction requires captured vertex weights and index dumps.")
    return components, files


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


def mesh_placement(mesh, avatar):
    """Require every used inverse bind to prove one common mesh-to-Avatar placement."""
    bones = avatar_bones(avatar)
    worlds = rest_worlds(bones, affine=True)
    by_hash = {bone["hash"]: index for index, bone in enumerate(avatar["bones"])}
    hashes, poses = mesh["bone_hashes"], mesh["bind_poses"]
    if len(hashes) != len(poses) or not hashes or len(hashes) > 4096:
        raise ValueError("The mesh bind-pose table is invalid.")
    mapping = [by_hash.get(value) for value in hashes]
    if any(value is None for value in mapping) or len(set(mapping)) != len(mapping):
        return None
    used = {bone for indices, weights in zip(mesh["bone_indices"], mesh["weights"])
            for bone, weight in zip(indices, weights) if weight > 0}
    if not used or any(type(bone) is not int or not 0 <= bone < len(mapping) for bone in used):
        return None
    # ReadMatrix and the native indexer preserve ZZZ's serialized row-major
    # float order despite Matrix4x4's column-major field names.
    matrices = [worlds[mapping[bone]] @ np.asarray(poses[bone]).reshape(4, 4) for bone in used]
    if not all(np.allclose(matrix, matrices[0], rtol=0, atol=2e-4) for matrix in matrices):
        return None
    return mapping, matrices[0]


def match_component(part, mesh):
    """Find a unique complete triangle correspondence and one positive-weight palette."""
    matches = match_component_candidates(part, mesh)
    return matches[0] if len(matches) == 1 else None


def match_component_candidates(part, mesh):
    """Retain coordinate hypotheses for the model-wide placement proof."""
    native = np.asarray(mesh["vertices"], dtype=float).reshape(-1, 3)
    if len(native) > 250000 or not np.isfinite(native).all():
        return []
    # Captures contain mesh-local coordinates. The verified inverse bind placement
    # is applied to the Avatar root when building the rig, never to capture data.
    uv = np.asarray(mesh["uv"], dtype=float).reshape(len(native), -1)[:, :2].copy()
    if not np.isfinite(uv).all():
        return []
    uv[:, 1] = 1 - uv[:, 1]
    indices = np.asarray(mesh["indices"], dtype=int).reshape(-1, 3)
    if part["uv"] is None or len(indices) > 500000 or np.min(indices) < 0 or np.max(indices) >= len(native):
        return []
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
            if _same_triangles(part, mesh, indices, dict(enumerate(original))):
                mapping = dict(enumerate(original))
        if not mapping:
            mapping = _triangle_mapping(part, positions, uv, indices)
        if mapping is None or not _same_triangles(part, mesh, indices, mapping):
            continue
        palette = _weight_palette(part, mesh, mapping)
        if palette:
            matches.append((palette, signs, mapping))
    return matches


def _same_triangles(part, mesh, indices, mapping):
    """Prove one complete native section, preserving repeated triangle counts."""
    if len(mapping) != len(part["positions"]):
        return False
    captured = Counter(tuple(sorted(mapping[int(vertex)] for vertex in row)) for row in part["indices"])
    sections = mesh.get("sections", [dict(first=0, count=len(indices) * 3)])
    for section in sections:
        first, count = section["first"], section["count"]
        if (type(first) is not int or type(count) is not int or first < 0 or first % 3
                or count != len(part["indices"]) * 3 or first + count > len(indices) * 3):
            continue
        if captured == Counter(tuple(sorted(row)) for row in indices[first // 3:(first + count) // 3]):
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

"""Inspect selected rest-surface edges without constructing or posing a Rig."""

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys

import numpy as np


ROOT = Path(__file__).resolve().parents[1]


def components(
    triangles, count, positions=None, edge_seams=False, vectors=None, seam_links=None
):
    """Indexed connectivity, optionally joined at exactly coincident positions."""
    parents = list(range(count))

    def find(vertex):
        while parents[vertex] != vertex:
            parents[vertex] = parents[parents[vertex]]
            vertex = parents[vertex]
        return vertex

    def join(a, b):
        a, b = find(a), find(b)
        parents[max(a, b)] = min(a, b)

    used = np.unique(triangles)
    for a, b, c in triangles.tolist():
        join(a, b)
        join(b, c)
    if positions is not None and edge_seams:
        first_edge = {}
        for triangle in triangles.tolist():
            for corner in range(3):
                a, b = triangle[corner], triangle[(corner + 1) % 3]
                ends = [(tuple(positions[v]), v) for v in (a, b)]
                ends.sort()
                if ends[0][0] == ends[1][0]:
                    continue
                key = tuple(
                    (
                        point,
                        tuple(sorted(vectors[v].items()))
                        if vectors is not None
                        else (),
                    )
                    for point, v in ends
                )
                if key in first_edge:
                    for (_, vertex), previous in zip(ends, first_edge[key]):
                        join(vertex, previous)
                        if seam_links is not None:
                            seam_links.append((vertex, previous))
                else:
                    first_edge[key] = [v for _, v in ends]
    elif positions is not None:
        first = {}
        for vertex in used.tolist():
            key = tuple(positions[vertex])
            if key in first:
                join(vertex, first[key])
            else:
                first[key] = vertex
    groups = defaultdict(list)
    for vertex in used.tolist():
        groups[find(vertex)].append(vertex)
    return {key: np.asarray(vertices) for key, vertices in groups.items()}


def authored_vectors(indices, weights, normalize=False):
    """Detached source-local vectors; normalization is only for overlap metrics."""
    vectors = []
    for ids, values in zip(indices, weights):
        vector = defaultdict(float)
        for bone, weight in zip(ids, values):
            if np.isfinite(weight) and weight > 0:
                vector[int(bone)] += float(weight)
        total = sum(vector.values())
        vectors.append(
            {
                bone: value / total if normalize else value
                for bone, value in vector.items()
            }
        )
    return vectors


def overlap(a, b):
    return sum(min(weight, b.get(bone, 0)) for bone, weight in a.items())


def inspect_mesh(positions, triangles, indices, weights, probes, uv=None):
    count = len(positions)
    if triangles.size == 0 or triangles.min() < 0 or triangles.max() >= count:
        raise ValueError("Invalid triangle indices.")
    if indices.shape != weights.shape or len(indices) != count:
        raise ValueError("Skin data does not match geometry.")
    if not np.isfinite(positions).all():
        raise ValueError("Invalid rest positions.")
    raw = components(triangles, count)
    welded = components(triangles, count, positions)
    owner = {int(v): key for key, vertices in raw.items() for v in vertices}
    vectors = authored_vectors(indices, weights)
    normalized = authored_vectors(indices, weights, normalize=True)
    edge_welded = components(triangles, count, positions, edge_seams=True)
    compatible = components(
        triangles, count, positions, edge_seams=True, vectors=vectors
    )
    compatible_owner = {
        int(v): key for key, vertices in compatible.items() for v in vertices
    }
    area = np.zeros(count)
    points = positions[triangles]
    triangle_area = (
        np.linalg.norm(
            np.cross(points[:, 1] - points[:, 0], points[:, 2] - points[:, 0]), axis=1
        )
        / 2
    )
    for corner in range(3):
        np.add.at(area, triangles[:, corner], triangle_area / 3)
    support = defaultdict(dict)
    compatible_support = defaultdict(lambda: defaultdict(float))
    triangle_counts = Counter(owner[int(t[0])] for t in triangles)
    summaries = {}
    for key, vertices in raw.items():
        mass = defaultdict(float)
        for vertex in vertices.tolist():
            for bone, weight in vectors[vertex].items():
                mass[bone] += area[vertex] * weight
                if area[vertex] > 0:
                    compatible_support[bone][compatible_owner[vertex]] += (
                        area[vertex] * weight
                    )
        for bone, value in mass.items():
            if value > 0:
                support[bone][key] = float(value)
        summaries[key] = {
            "vertices": len(vertices),
            "triangles": triangle_counts[key],
            "authored_influences": len(mass),
        }
    edges = {
        tuple(sorted((int(t[i]), int(t[(i + 1) % 3]))))
        for t in triangles
        for i in range(3)
    }
    seams = defaultdict(list)
    for vertex in sorted(owner):
        seams[tuple(positions[vertex])].append(vertex)
    duplicate_groups = [group for group in seams.values() if len(group) > 1]
    seam_pairs = [(group[0], v) for group in duplicate_groups for v in group[1:]]
    inspected = []
    for a, b in probes:
        if a not in owner or b not in owner:
            raise ValueError("Probe vertices must belong to selected triangles.")
        bones = sorted(vectors[a].keys() | vectors[b].keys())
        inspected.append(
            {
                "vertices": [a, b],
                "indexed_edge": tuple(sorted((a, b))) in edges,
                "same_indexed_island": owner[a] == owner[b],
                "same_compatible_edge_island": compatible_owner[a]
                == compatible_owner[b],
                "islands": [owner[a], owner[b]],
                "island_summary": summaries[owner[a]],
                "compatible_edge_island_summary": {
                    "vertices": len(compatible[compatible_owner[a]]),
                    "indexed_islands_merged": len(
                        {owner[int(v)] for v in compatible[compatible_owner[a]]}
                    ),
                },
                "authored_overlap": overlap(normalized[a], normalized[b]),
                "authored": [vectors[a], vectors[b]],
                "authored_weight_sums": [
                    sum(vectors[a].values()),
                    sum(vectors[b].values()),
                ],
                "influence_island_support": {
                    bone: {
                        "islands": len(support[bone]),
                        "compatible_edge_islands": len(compatible_support[bone]),
                        "compatible_probe_area_fraction": (
                            compatible_support[bone].get(compatible_owner[a], 0)
                            / sum(compatible_support[bone].values())
                            if support[bone]
                            else None
                        ),
                        "probe_island_area_fraction": (
                            support[bone].get(owner[a], 0) / sum(support[bone].values())
                            if support[bone]
                            else None
                        ),
                    }
                    for bone in bones
                },
            }
        )
    return {
        "vertices": len(owner),
        "triangles": len(triangles),
        "indexed_islands": len(raw),
        "exact_position_islands": len(welded),
        "exact_edge_islands": len(edge_welded),
        "authored_compatible_edge_islands": len(compatible),
        "duplicate_position_triangles": len(triangles)
        - len({tuple(sorted(tuple(positions[int(v)]) for v in t)) for t in triangles}),
        "duplicate_position_groups": len(duplicate_groups),
        "duplicate_position_pairs": len(seam_pairs),
        "different_uv_pairs": sum(
            not np.array_equal(uv[a], uv[b]) for a, b in seam_pairs
        )
        if uv is not None
        else None,
        "different_authored_pairs": sum(
            vectors[a] != vectors[b] for a, b in seam_pairs
        ),
        "minimum_duplicate_authored_overlap": min(
            (overlap(normalized[a], normalized[b]) for a, b in seam_pairs), default=None
        ),
        "probes": inspected,
    }


def read_surface(path, mesh_key):
    """Read one resolved draw for detached inspection or the offline probe."""
    sys.path.insert(0, str(ROOT / "src"))
    from app.mods.analysis import build_mod_ini_snapshot
    from app.mods.loader import ModLoadContext, load_mod
    from core.geometry.buffers import BufferStore
    from core.geometry.skinning import decode_skinning, skinning_source_descriptor
    from core.geometry.transport import GeometryBlob
    from core.mod_discovery import discover_ini_paths
    from core.mod_source import mod_source_for_path

    source = mod_source_for_path(str(path))
    snapshot = build_mod_ini_snapshot(
        discover_ini_paths(source.root, source=source), source.root, source=source
    )
    context = ModLoadContext(source.root, ini=snapshot)
    blob = GeometryBlob()
    payload = load_mod(context=context, geometry=blob)
    if payload.get("error"):
        raise ValueError(payload["error"])
    if mesh_key not in payload["meshes"] or mesh_key not in context.skinning_manifest:
        raise ValueError("Select a resolved mesh with authored skinning.")
    entry = payload["meshes"][mesh_key]
    manifest = context.skinning_manifest[mesh_key]
    skin_source = manifest.skinning_source
    buffers = BufferStore(source=source)
    decoded = decode_skinning(
        skin_source,
        buffers.raw(source.resolve_resource(skin_source.file)),
        manifest.used_vertices,
        buffers.raw(source.resolve_resource(skin_source.vertex_vg_file))
        if skin_source.vertex_vg_file
        else None,
    )
    if any(
        decoded.diagnostics.get(name)
        for name in ("truncated_vertices", "vertex_vg_truncated_vertices")
    ):
        raise ValueError("Authored skin data is truncated.")

    def array(field, dtype, width):
        ref = entry[field]
        return np.frombuffer(
            blob.data, dtype, ref["length"] // 4, ref["offset"]
        ).reshape(-1, width)

    positions = array("pos", "<f4", 3)
    triangles = array("idx", "<u4", 3)
    shape = (decoded.vertex_count, decoded.influence_count)
    return {
        "positions": positions,
        "triangles": triangles,
        "indices": np.frombuffer(decoded.indices, "<u4").reshape(shape),
        "weights": np.frombuffer(decoded.weights, "<f4").reshape(shape),
        "uv": array("uv", "<f4", 2) if "uv" in entry else None,
        "geometry_sha256": hashlib.sha256(
            positions.tobytes() + triangles.tobytes()
        ).hexdigest(),
        "skinning_sha256": hashlib.sha256(
            decoded.indices + decoded.weights
        ).hexdigest(),
        "source": skinning_source_descriptor(skin_source),
        "used_vertices": manifest.used_vertices,
    }


def load_inspection(path, mesh_key, probes):
    surface = read_surface(path, mesh_key)
    result = inspect_mesh(
        surface["positions"],
        surface["triangles"],
        surface["indices"],
        surface["weights"],
        probes,
        surface["uv"],
    )
    return {
        "scope": "One resolved rest draw; no visibility or pose inference.",
        "mesh": mesh_key,
        **{
            key: surface[key]
            for key in ("geometry_sha256", "skinning_sha256", "source")
        },
        "authored_vertices": [
            [surface["used_vertices"][v] for v in pair] for pair in probes
        ],
        **result,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mod", type=Path)
    parser.add_argument("--mesh", required=True)
    parser.add_argument("--edge", type=int, nargs=2, action="append", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = load_inspection(args.mod, args.mesh, args.edge)
    text = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")


if __name__ == "__main__":
    main()

"""Bounded offline feasibility probe; never imported by the viewer runtime."""

from pathlib import Path
import runpy

import numpy as np


INSPECTOR = runpy.run_path(
    str(Path(__file__).resolve().parents[3] / "tools" / "inspect_surface_continuity.py")
)


def propagate(
    positions, triangles, indices, authored_weights, labels, max_iterations=2000
):
    """Return three driver weights and diagnostics; unlabelled components stay NaN."""
    count = len(positions)
    if triangles.size == 0 or triangles.min() < 0 or triangles.max() >= count:
        raise ValueError("Invalid surface indices.")
    if not np.isfinite(positions).all():
        raise ValueError("Invalid rest positions.")
    if indices.shape != authored_weights.shape or len(indices) != count:
        raise ValueError("Authored weights do not match the surface.")
    if any(
        not isinstance(v, (int, np.integer))
        or isinstance(v, (bool, np.bool_))
        or isinstance(v, (bool, np.bool_))
        or v < 0
        or v >= count
        or not isinstance(driver, (int, np.integer))
        or isinstance(driver, (bool, np.bool_))
        or not isinstance(driver, (int, np.integer))
        or isinstance(driver, (bool, np.bool_))
        or driver not in (0, 1, 2)
        for v, driver in labels.items()
    ):
        raise ValueError("Invalid experimental boundary label.")
    original = INSPECTOR["authored_vectors"](indices, authored_weights)
    normalized = INSPECTOR["authored_vectors"](
        indices, authored_weights, normalize=True
    )
    seams = []
    INSPECTOR["components"](
        triangles, count, positions, edge_seams=True, vectors=original, seam_links=seams
    )
    pairs = {
        tuple(sorted((int(t[i]), int(t[(i + 1) % 3]))))
        for t in triangles
        for i in range(3)
    }
    pairs.update(tuple(sorted(pair)) for pair in seams)
    weighted = [
        (u, v, INSPECTOR["overlap"](normalized[u], normalized[v]))
        for u, v in sorted(pairs)
        if u != v
    ]
    edges = np.array([edge for edge in weighted if edge[2] > 0], dtype=float).reshape(
        -1, 3
    )
    u, v = edges[:, :2].astype(int).T
    conductance = edges[:, 2]
    vertices = np.arange(count)
    groups = INSPECTOR["components"](
        np.vstack([np.column_stack([u, v, v]), np.column_stack([vertices] * 3)]), count
    )
    covered = np.zeros(count, dtype=bool)
    for members in groups.values():
        if any(int(vertex) in labels for vertex in members):
            covered[members] = True
    free = covered.copy()
    free[list(labels)] = False
    degree = np.bincount(u, conductance, minlength=count) + np.bincount(
        v, conductance, minlength=count
    )
    result = np.full((count, 3), np.nan)
    result[covered] = 0
    for vertex, driver in labels.items():
        result[vertex, driver] = 1
    boundary = np.zeros((count, 3))
    boundary[list(labels)] = result[list(labels)]

    def multiply(x):
        full = np.zeros(count)
        full[free] = x
        return (
            degree * full
            - np.bincount(u, conductance * full[v], minlength=count)
            - np.bincount(v, conductance * full[u], minlength=count)
        )[free]

    residuals, iterations = [], []
    for driver in range(3):
        rhs = (
            np.bincount(u, conductance * boundary[v, driver], minlength=count)
            + np.bincount(v, conductance * boundary[u, driver], minlength=count)
        )[free]
        x = np.zeros(free.sum())
        r = rhs.copy()
        z = r / degree[free]
        direction = z.copy()
        previous = r @ z
        step = 0
        for step in range(max_iterations):
            if np.max(np.abs(r), initial=0) <= 1e-10:
                break
            product = multiply(direction)
            alpha = previous / (direction @ product)
            x += alpha * direction
            r -= alpha * product
            z = r / degree[free]
            current = r @ z
            direction = z + (current / previous) * direction
            previous = current
        residual = float(np.max(np.abs(multiply(x) - rhs), initial=0))
        if not np.isfinite(x).all() or residual > 1e-9:
            raise RuntimeError("Experimental harmonic solve did not converge.")
        result[free, driver] = x
        residuals.append(residual)
        iterations.append(step)
    return result, {
        "components": len(groups),
        "constraints": len(labels),
        "covered_vertices": int(covered.sum()),
        "unresolved_vertices": int((~covered).sum()),
        "zero_conductance_edges": sum(edge[2] == 0 for edge in weighted),
        "iterations": iterations,
        "residual": residuals,
        "unseeded_component_sizes": sorted(
            [len(members) for members in groups.values() if not covered[members[0]]],
            reverse=True,
        ),
    }

"""Retain all supported GIMI direct face candidates for manual cleanup."""

import copy
import dataclasses
import hashlib

from ..core import Component
from ..geometry import Mesh

# Mirrors the captured face VS collapse test: TEXCOORD2.x above this is collapsed unless a shader switch selects it.
_SHELL_SELECTOR = 0.05


def _slice(part, lo, hi):
    return dataclasses.replace(part, first=part.first + 3 * lo, count=3 * (hi - lo),
                               draws=list(part.draws), textures=copy.deepcopy(part.textures))


def _gated_split(mesh):
    """Return the base/shell part split for one direct mesh, or None when the evidence does not match."""
    column = next((k for k, e in enumerate(mesh.elements) if e.semantic == "TEXCOORD" and e.index == 2), None)
    if column is None:
        return None
    gated = [vertex[column][0] > _SHELL_SELECTOR for vertex in mesh.vertices]
    if not any(gated):
        return None
    if any(not flag and any(value != 0 for value in vertex[column])
           for flag, vertex in zip(gated, mesh.vertices)):
        return None
    base_parts, base_indices, shell_parts, shell_indices = [], [], [], []
    for part, indices in zip(mesh.component.parts, mesh.indices):
        if len(indices) % 3:
            return None
        flags = []
        for j in range(0, len(indices), 3):
            corners = [gated[i + part.base_vertex] for i in indices[j:j + 3]]
            if any(corners) and not all(corners):
                return None
            flags.append(all(corners))
        runs = [j for j, flag in enumerate(flags) if flag]
        if not runs:
            base_parts.append(part)
            base_indices.append(indices)
            continue
        a, b = runs[0], runs[-1] + 1
        if len(runs) != b - a:
            return None
        shell_parts.append(_slice(part, a, b))
        shell_indices.append(indices[3 * a:3 * b])
        for lo, hi in ((0, a), (b, len(flags))):
            if hi > lo:
                base_parts.append(_slice(part, lo, hi))
                base_indices.append(indices[3 * lo:3 * hi])
    if not shell_parts or not base_parts:
        return None
    return sum(gated), base_parts, base_indices, shell_parts, shell_indices


def split_gated_shells(components, meshes):
    """Move a TEXCOORD2-gated alternate face shell into its own candidate excluded by default."""
    for mesh in list(meshes.values()):
        component = mesh.component
        if component.status != "included" or component.provenance.get("geometry_kind") != "direct":
            continue
        split = _gated_split(mesh)
        if split is None:
            continue
        gated_count, base_parts, base_indices, shell_parts, shell_indices = split
        shell_triangles = sum(len(x) // 3 for x in shell_indices)
        base_triangles = sum(len(x) // 3 for x in base_indices)
        shell = Component(
            hashlib.sha256((component.id + ":texcoord2-gated-shell").encode()).hexdigest()[:12],
            component.name + "Shell", component.ib, "included", "Shader-gated alternate face shell",
            copy.deepcopy(component.streams), shell_parts, copy.deepcopy(component.provenance),
            vertex_count=component.vertex_count, triangle_count=shell_triangles,
            diagnostics=["Alternate face shell selected by TEXCOORD2.x > 0.05 is collapsed by the captured "
                         "face shader; excluded by default and available for explicit selection."],
            morph=copy.deepcopy(component.morph), bounds=copy.deepcopy(component.bounds))
        shell.provenance["gimi_gated_shell"] = dict(base=component.id, selector="TEXCOORD2.x > 0.05",
                                                    vertices=gated_count, triangles=shell_triangles)
        component.parts = base_parts
        component.triangle_count = base_triangles
        component.provenance["gimi_gated_shell_component"] = shell.id
        mesh.indices = base_indices
        components.insert(components.index(component) + 1, shell)
        meshes[shell.id] = Mesh(shell, mesh.elements, mesh.vertices, shell_indices,
                                mesh.source_position, mesh.morph_status)


def _material(mesh):
    signatures = []
    for part in mesh.component.parts:
        selected = [t for t in part.textures if not t.get("auxiliary") and t["role"] in {"Diffuse", "LightMap"}]
        maps = {t["role"]: (t["hash"], t["sha256"]) for t in selected}
        if len(selected) != 2 or set(maps) != {"Diffuse", "LightMap"}:
            return None
        signatures.append(tuple(sorted(maps.items())))
    return signatures[0] if signatures and all(s == signatures[0] for s in signatures) else None


def classify_faces(meshes, context):
    """Group placement evidence without selecting expressions or passes.

    A gated alternate shell is separated beforehand by split_gated_shells; other
    overlay shells are retained. Direct GIMI streams are retained as candidates rather than claiming that
    their topology proves an open eye or membership in one character. Matching
    captured materials share placement validation; missing materials remain
    independent candidates. Each source keeps its ordinary hash output folder.
    """
    families = {}
    ordinal = 0
    for mesh in meshes.values():
        component = mesh.component
        if component.status != "included" or component.provenance.get("geometry_kind") != "direct":
            continue
        context.tick("Collecting GIMI face candidates")
        ordinal += 1
        material = _material(mesh)
        key = ("material", material) if material is not None else ("component", component.id)
        families.setdefault(key, []).append(component)
        component.provenance.update(gimi_face_candidate=True, suggested_name=f"Face{ordinal:03d}")
        component.diagnostics.append(
            "Captured GIMI direct face candidate retained for manual removal; "
            "expression, alternate passes and complete topology are preserved.")
    for members in families.values():
        identity = min(component.id for component in members)
        for component in members:
            component.provenance["gimi_face_family"] = identity

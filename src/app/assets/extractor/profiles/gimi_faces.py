"""Retain all supported GIMI direct face candidates for manual cleanup."""


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
    """Group placement evidence without selecting expressions, passes or shells.

    Direct GIMI streams are retained as candidates rather than claiming that
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

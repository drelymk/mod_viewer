"""ZZZ adapter for the existing staged Asset rig/animation extraction workflow."""

import hashlib
import json
from pathlib import Path
import re

import numpy as np

from app.assets.animations import read_clip
from app.assets.rig import validate_rig
from . import zzz_animation, zzz_index, zzz_morphs
from .native import humanoid_controls
from .pipeline import clip_id, document, preserve_library, reusable_clip_key, sub_skeleton_map, write_document
from .runtime import ExtractionCancelled, check_cancelled
from .zzz_native import avatar_bones, capture, match_attachment_candidates, match_component_candidates, mesh_placement


def _search_name(value):
    # Search tolerance is only candidate discovery. Geometry and inverse binds
    # establish identity before any skeleton or animation is adopted.
    return re.sub(r"[^a-z0-9]", "", value.lower()).replace("y", "i")


def _family(name):
    for suffix in ("_ModelAvatar", "_Avatar"):
        if name.startswith("Avatar_") and name.endswith(suffix):
            return name[:-len(suffix)]
    return ""


def _mesh_family_hint(family, name):
    """Discover both exported Unity mesh naming forms; this is never binding proof."""
    if family + "_Model_" in name:
        return True
    character = re.sub(r"^Avatar_(?:(?:Female|Male)_)?(?:Size\d+|Type\d+)_", "", family)
    if character == family:
        return False
    stem = character.split("_", 1)[0]
    legacy = "SeparateMesh_" + family + "_"
    # A body Avatar may also own meshes named for its child prop/variant family.
    # Name hints must not exclude those meshes before native ownership proof.
    return name.casefold().startswith((stem.casefold() + "_", legacy.casefold()))


def _root_transform(matrix):
    scales = np.linalg.norm(matrix[:3, :3], axis=0)
    if np.linalg.det(matrix[:3, :3]) < 0:
        scales[0] *= -1
    rotation = matrix[:3, :3] / scales
    if (not np.isfinite(matrix).all() or np.min(np.abs(scales)) < 1e-6
            or not np.allclose(rotation.T @ rotation, np.eye(3), rtol=0, atol=2e-4)):
        raise ValueError("The mesh-to-Avatar basis contains unsupported shear.")
    # Stable matrix -> quaternion conversion without a separate math dependency.
    candidates = [1 + rotation[0, 0] - rotation[1, 1] - rotation[2, 2],
                  1 - rotation[0, 0] + rotation[1, 1] - rotation[2, 2],
                  1 - rotation[0, 0] - rotation[1, 1] + rotation[2, 2], 1 + np.trace(rotation)]
    axis = int(np.argmax(candidates))
    q = np.zeros(4)
    q[axis] = np.sqrt(max(0, candidates[axis])) / 2
    d = 4 * q[axis]
    if axis == 3:
        q[:3] = [rotation[2, 1] - rotation[1, 2], rotation[0, 2] - rotation[2, 0], rotation[1, 0] - rotation[0, 1]]
        q[:3] /= d
    else:
        b, c = (axis + 1) % 3, (axis + 2) % 3
        q[b] = (rotation[axis, b] + rotation[b, axis]) / d
        q[c] = (rotation[axis, c] + rotation[c, axis]) / d
        q[3] = (rotation[c, b] - rotation[b, c]) / d
    q /= np.linalg.norm(q)
    return dict(name="mesh-space", parent=-1, translation=matrix[:3, 3].tolist(), rotation=q.tolist(), scale=scales.tolist())


def _rig(avatar, basis, palettes):
    bones = [_root_transform(basis)]
    bones += [{**bone, "parent": bone["parent"] + 1} for bone in avatar_bones(avatar)]
    raw = dict(format="mod-viewer-rig", version=2, coordinate_space="asset", bones=bones,
               components=palettes, skin_bindings=[])
    # Reuse the hierarchy-checked humanoid mapping after adapting Biped spelling.
    semantic_bones = [{**bone, "name": bone["name"].rsplit("/", 1)[-1].replace(" ", "")} for bone in bones]
    controls = humanoid_controls({"bones": semantic_bones})
    if controls:
        raw["humanoid_controls"] = controls
    return validate_rig(raw)


def exact_shape(shape):
    """ZZZ submesh vertex counts equal the captured component's referenced vertices."""
    return tuple(shape)


def index_shape(shape):
    """Overlapping submesh vertex ranges leave only the index count comparable."""
    return None, shape[1]


def verify_candidates(client, captured, rows, query, cancel, *,
                      mesh_hint=lambda avatar_row, family, mesh_row: _mesh_family_hint(family, mesh_row["name"]), label="ZZZ",
                      placement_tolerance=2e-4, shape_key=exact_shape, weight_drift=0.0):
    skinned = [part for part in captured if not part.get("weightless")]
    weightless_counts = [part["index_count"] for part in captured if part.get("weightless")]
    # Shapes only narrow candidates; every adopted part still needs proof.
    wanted = {shape_key((len(part["positions"]), part["index_count"])) for part in skinned}
    avatars = [row for row in rows if row["type"] == "Avatar" and _family(row["name"])]
    named = [row for row in avatars if _search_name(query) in _search_name(row["name"])] if query else []
    if not named:
        meshes = [row for row in rows if row["type"] == "Mesh" and any(shape_key(s) in wanted for s in row.get("shape") or [])]
        scores = {}
        for avatar in avatars:
            family = _family(avatar["name"])
            shapes = {shape_key(shape) for mesh in meshes if mesh_hint(avatar, family, mesh) for shape in mesh.get("shape") or []}
            scores[family] = sum(count for vertices, count in wanted & shapes)
        best = max(scores.values(), default=0)
        named = [avatar for avatar in avatars if best and scores[_family(avatar["name"])] == best]
    if not 1 <= len(named) <= 32:
        raise ValueError(f"Enter the character's game name to narrow the {label} Avatar search.")
    candidates = []
    seen_avatars = {}

    def shape_wanted(shape):
        # A weightless attachment may draw only part of its native section.
        return shape_key(shape) in wanted or any(shape[1] >= count for count in weightless_counts)

    for avatar_row in sorted(named, key=zzz_index.identity):
        check_cancelled(cancel)
        family = _family(avatar_row["name"])
        mesh_rows = [row for row in rows if row["type"] == "Mesh" and mesh_hint(avatar_row, family, row)
                     and not re.search(r"_LOD[1-9]", row["name"], re.I)
                     and (row.get("shape") is None or any(shape_wanted(shape) for shape in row["shape"]))]
        if not mesh_rows:
            continue
        if len(mesh_rows) > 128:
            raise ValueError("The character has too many native mesh variants to verify uniquely.")
        avatar = client.read(avatar_row)
        # Humanoid data is not part of skeleton/palette identity.
        avatar_content = json.dumps({key: value for key, value in avatar.items() if key != "human"},
                                    sort_keys=True, separators=(",", ":"))
        avatar_key = family, avatar_content
        if avatar_key in seen_avatars:
            seen_avatars[avatar_key].add(zzz_index.identity(avatar_row))
            continue
        seen_avatars[avatar_key] = {zzz_index.identity(avatar_row)}
        placements = []
        for mesh_row in sorted(mesh_rows, key=zzz_index.identity):
            check_cancelled(cancel)
            mesh = client.read(mesh_row)
            if not mesh.get("bone_hashes"):
                continue
            placed = mesh_placement(mesh, avatar, placement_tolerance)
            if placed is None:
                continue
            bone_map, placement = placed
            for number, part in enumerate(captured):
                if part.get("weightless"):
                    found = match_attachment_candidates(part, mesh)
                elif any(section["count"] == part["index_count"] for section in mesh["sections"]):
                    found = match_component_candidates(part, mesh, weight_drift)
                else:
                    continue
                for palette, signs, vertices, *attachment in found:
                    conversion = np.diag([*signs, 1]) @ np.linalg.inv(placement)
                    group = next((item for item in placements if np.allclose(item["basis"], conversion, rtol=0, atol=placement_tolerance)), None)
                    if group is None:
                        group = dict(basis=conversion, matches={}, ambiguous=set(), sources=set(), native={})
                        placements.append(group)
                    result = {key: value for key, value in part.items() if key not in
                              ("positions", "normals", "uv", "indices", "blends", "source_vertices", "source_vertex_count",
                               "blend_sha256", "weightless")}
                    result["bone_map"] = {key: bone_map[value] + 1 for key, value in palette.items()}
                    if attachment:
                        result["attachment"] = attachment[0]
                    if number in group["matches"] and group["matches"][number] != result:
                        group["ambiguous"].add(number)
                    group["matches"][number] = result
                    group["sources"].add(zzz_index.identity(mesh_row))
                    group["native"].setdefault(number, []).append(dict(row=mesh_row, mesh=mesh, signs=signs, vertices=vertices))
        for group in placements:
            matches = {number: value for number, value in group["matches"].items() if number not in group["ambiguous"]}
            # Weightless attachments follow a placement that a skinned palette has proven.
            if any("attachment" not in value for value in matches.values()):
                rig = _rig(avatar, group["basis"], [matches[i] for i in sorted(matches)])
                score = sum(captured[i]["index_count"] for i in matches)
                candidates.append(dict(rig=rig, avatar=avatar, family=family, score=score, matched=matches,
                                       source=zzz_index.identity(avatar_row), meshes=sorted(group["sources"]),
                                       avatar_copies=seen_avatars[avatar_key],
                                       native={i: group["native"][i] for i in matches}))
    if not candidates:
        raise ValueError(f"No {label} mesh could be verified against the captured triangles, UVs, weights and Avatar inverse binds.")
    best = max(item["score"] for item in candidates)
    best_candidates = [item for item in candidates if item["score"] == best]
    largest = min(best_candidates, key=lambda item: (-len(item["rig"]["bones"]), item["source"]))
    compatible = True
    for item in best_candidates:
        mapping = sub_skeleton_map(largest["rig"], item["rig"], affine=True)
        if mapping is None:
            compatible = False
            break
        primary = largest["matched"]
        if set(item["matched"]) != set(primary) or any(
                {key: mapping[value] for key, value in palette["bone_map"].items()} != primary[number]["bone_map"]
                for number, palette in item["matched"].items()):
            compatible = False
            break
    # Equal native copies can differ by float serialization noise. The existing
    # hierarchy/world/palette proof establishes equivalence; exact JSON equality
    # would reject equivalent copies. Animation family ownership must still agree.
    if not compatible or len({item["family"] for item in best_candidates}) != 1:
        raise ValueError(f"The matching {label} skeleton is ambiguous. Existing Asset files were preserved.")
    largest["avatar_sources"] = set().union(*(item["avatar_copies"] for item in best_candidates))
    return largest


def adopt_rig(asset, chosen, captured, label="ZZZ"):
    """Adopt the proven rig against the saved Asset Rig.json without changing its identity."""
    rig = chosen["rig"]
    previous = None
    if (asset / "Rig.json").is_file():
        try:
            previous = validate_rig(document(asset / "Rig.json"))
        except (ValueError, KeyError, TypeError):
            pass
    # Equivalent native copies and bind calculations can introduce float noise.
    # Keep the saved reference only when every joint retains its index, induced
    # hierarchy and proven reference world, so compatible clips retain identity.
    if previous and len(previous["bones"]) == len(rig["bones"]):
        mapping = sub_skeleton_map(previous, rig, affine=True)
        if mapping == list(range(len(rig["bones"]))):
            rig = validate_rig({**rig, "bones": previous["bones"]})
    # Exact captured float Blend fingerprints allow unchanged mod weight streams
    # to reuse the proven palette. Preserve compatible explicitly authored profiles.
    profiles = {}
    for number, palette in chosen["matched"].items():
        if "attachment" in palette:
            continue  # A weightless attachment has no Blend stream for mods to reuse.
        digest = captured[number]["blend_sha256"]
        mapping = profiles.setdefault(digest, {})
        for key, bone in palette["bone_map"].items():
            if key in mapping and mapping[key] != bone:
                raise ValueError(f"Captured {label} palettes disagree about the same Blend stream.")
            mapping[key] = bone
    raw = {**rig, "skin_bindings": [dict(sha256=digest, encoding="gimi_f32_u32_4", bone_map=mapping)
                                  for digest, mapping in profiles.items()]}
    if previous and previous["bones"] == rig["bones"]:
        if previous.get("humanoid_controls"):
            raw["humanoid_controls"] = previous["humanoid_controls"]
        identities = {(p["sha256"], p["encoding"]) for p in raw["skin_bindings"]}
        raw["skin_bindings"] += [p for p in previous["skin_bindings"] if (p["sha256"], p["encoding"]) not in identities]
    rig = validate_rig(raw)
    return rig, previous


def build(client, root, record, output, query, progress, cancel, index=None):
    captured, fingerprints = capture(root, record)
    if index is None:
        index = zzz_index.scan(client, client.root, progress, cancel)
    progress("search", 0, 0)
    chosen = verify_candidates(client, captured, index, query, cancel)
    avatar = chosen["avatar"]
    progress("rig", 1, 1)
    asset, output = Path(root) / record["path"], Path(output)
    library = output / "Animation"
    library.mkdir(parents=True)
    rig, previous = adopt_rig(asset, chosen, captured)
    validated_morphs = set()
    entries = preserve_library(asset, library, rig, previous, validated_morphs=validated_morphs)
    existing = {key: read_clip(str(library), entry, rig, validated_morphs=validated_morphs)[0] for key, entry in entries.items()}
    facial = zzz_morphs.prepare(chosen, captured, index)
    clips = [row for row in index if row["type"] == "AnimationClip" and row["name"].startswith(chosen["family"] + "_Ani_")]
    if not clips or len(clips) > 4096:
        raise ValueError("No bounded character animation set was found for the verified ZZZ Avatar.")
    imported, skipped, provenance, seen = 0, [], [], set()
    for number, row in enumerate(clips):
        progress("convert", number, len(clips))
        check_cancelled(cancel)
        source = zzz_index.identity(row)
        try:
            metadata = client.read(row)
            binary, frames, duration, unsupported, morph = zzz_animation.convert(
                metadata, client.work, rig, avatar, facial["bindings"])
        except ExtractionCancelled:
            raise
        except (ValueError, KeyError, TypeError) as error:
            reason = str(error) if isinstance(error, ValueError) else "Unsupported or incompatible native clip."
            skipped.append(dict(source=source, name=row["name"], reason=reason))
            continue
        finally:
            # Sample buffers belong to a single clip; bounded jobs must not keep
            # accumulating native exports after conversion.
            for file in client.work.glob("samples-*.bin"):
                file.unlink(missing_ok=True)
        digest = hashlib.sha256(binary).hexdigest()
        duplicate = row["name"], digest
        if duplicate in seen:
            continue
        seen.add(duplicate)
        key = reusable_clip_key(entries, existing, source, row["name"], {digest})
        if key is None:
            key = clip_id(row["name"], source, set(entries))
            if (library / (key + ".json")).exists() or (library / (key + ".bin")).exists():
                raise ValueError("Extracted clips conflict with preserved user files.")
        metadata = dict(format="mod-viewer-animation", version=2, coordinate_space="asset", layout="frame-major-tqs-f32le",
                        skeleton_id=rig["id"], frame_count=frames, bone_count=len(rig["bones"]), duration=duration,
                        data_file=key + ".bin", sha256=digest, source=source, omitted_non_transform_channels=unsupported)
        if morph:
            metadata["morphs"] = zzz_morphs.export_glb(facial, morph, frames, duration, library)
            metadata["blend_shape_clamp"] = morph["clamp"]
        (library / (key + ".bin")).write_bytes(binary)
        write_document(library / (key + ".json"), metadata, 1024 * 1024)
        entry = dict(id=key, name=row["name"][:128], file=key + ".json", duration=duration)
        read_clip(str(library), entry, rig, validated_morphs=validated_morphs)
        entries[key], existing[key] = entry, metadata
        provenance.append(dict(id=key, source=source, omitted_non_transform_channels=unsupported,
                               facial_glb=metadata.get("morphs", {}).get("file")))
        imported += 1
        if len(entries) > 1024 or sum(file.stat().st_size for file in library.rglob("*") if file.is_file()) > 2 * 1024 ** 3:
            raise ValueError("The extracted animation library exceeds its size limit.")
    if not imported:
        raise ValueError("No compatible ZZZ animation clips could be converted. Existing Asset files were preserved.")
    write_document(library / "manifest.json", dict(format="mod-viewer-animations", version=1, skeleton_id=rig["id"], clips=list(entries.values())), 1024 * 1024)
    write_document(output / "Rig.json", {key: value for key, value in rig.items() if key != "id"}, 2 * 1024 * 1024)
    report = dict(format="mod-viewer-rig-extraction", version=1, game="zzz", skeleton_id=rig["id"], skeleton=chosen["source"],
                  meshes=chosen["meshes"], proven_components=len(chosen["matched"]), unmatched_components=len(captured) - len(chosen["matched"]),
                  bones=len(rig["bones"]), clips=len(entries), imported=imported, secondary=0, skipped=len(skipped),
                  skipped_details=skipped, animations=provenance)
    write_document(output / "RigExtraction.json", report, 4 * 1024 * 1024)
    return report, fingerprints

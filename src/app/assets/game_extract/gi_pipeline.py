"""Genshin Impact Avatar discovery and rig proof, sharing the ZZZ native proof."""

import hashlib
from pathlib import Path

from app.assets.animations import read_clip
from . import gi_animation, gi_index, zzz_morphs, zzz_native, zzz_pipeline
from .mecanim import HumanSolver
from .pipeline import clip_id, preserve_library, reusable_clip_key, write_document
from .runtime import ExtractionCancelled, check_cancelled

LABEL = "Genshin Impact"
# Long Genshin face chains carry float noise just above ZZZ's 2e-4 inverse-bind
# agreement (about 0.25 mm); 1e-3 stays sub-millimetre and about 0.06 degrees.
PLACEMENT_TOLERANCE = 1e-3


def colocated(avatar_row, _family, mesh_row):
    """A character's meshes share its Avatar's bundle; generic mesh names are not evidence."""
    return mesh_row["block"] == avatar_row["block"] and mesh_row["offset"] == avatar_row["offset"]


def _verify(client, captured, index, query, cancel):
    # Genshin submeshes share overlapping vertex ranges, so SubMesh.vertexCount is
    # not the captured component's vertex count; only index counts narrow meshes.
    return zzz_pipeline.verify_candidates(client, captured, index, query, cancel, mesh_hint=colocated, label=LABEL,
                                          placement_tolerance=PLACEMENT_TOLERANCE, shape_key=zzz_pipeline.index_shape)


def prove_rig(client, root, record, query, progress, cancel, index):
    captured, fingerprints = zzz_native.capture(root, record, asset_type="GIMI")
    progress("search", 0, 0)
    try:
        chosen = _verify(client, captured, index, query, cancel)
    except ExtractionCancelled:
        raise
    except ValueError:
        if not query:
            raise
        # Asset names differ from internal ones; a name that cannot be proven falls
        # back to geometry, which still requires the complete native proof.
        chosen = _verify(client, captured, index, None, cancel)
    progress("rig", 1, 1)
    rig, previous = zzz_pipeline.adopt_rig(Path(root) / record["path"], chosen, captured, label=LABEL)
    return chosen, rig, previous, captured, fingerprints


def _facial(chosen, captured, index):
    """Renderer-proven facial targets, or (None, reason); facial curves never block body motion."""
    expressions = {number: palette["attachment"]["expression"]
                   for number, palette in chosen["matched"].items() if "attachment" in palette}
    try:
        facial = zzz_morphs.prepare(chosen, captured, index, expressions)
    except ValueError as error:
        return None, str(error)
    return (facial, None) if facial["bindings"] and facial["components"] else (None, None)


def _clear_samples(work):
    # Sample buffers belong to one state; bounded jobs must not keep accumulating them.
    for file in work.glob("samples-*.bin"):
        file.unlink(missing_ok=True)


def build(client, root, record, output, query, progress, cancel, index=None):
    if index is None:
        index = gi_index.scan(client, client.root, progress, cancel)
    chosen, rig, previous, captured, fingerprints = prove_rig(client, root, record, query, progress, cancel, index)
    avatar = chosen["avatar"]
    solver = HumanSolver(avatar.get("human"))
    gi_animation.human_mapping(avatar, solver)
    asset, output = Path(root) / record["path"], Path(output)
    library = output / "Animation"
    library.mkdir(parents=True)
    validated_morphs = set()
    entries = preserve_library(asset, library, rig, previous, validated_morphs=validated_morphs)
    existing = {key: read_clip(str(library), entry, rig, validated_morphs=validated_morphs)[0] for key, entry in entries.items()}
    controller_row = gi_animation.controller_row(index, chosen["family"])
    controller = client.read(controller_row)
    _base, streamer_layer = gi_animation.layers(controller)
    mask = gi_animation.streamer_mask(streamer_layer)
    pairs, skipped = gi_animation.state_pairs(controller, index)
    facial, facial_error = _facial(chosen, captured, index)
    imported, provenance, seen = 0, [], set()
    for number, pair in enumerate(pairs):
        progress("convert", number, len(pairs))
        check_cancelled(cancel)
        source = gi_animation.pair_source(pair)
        try:
            body = client.read(pair["body"])
            streamer = client.read(pair["streamer"]) if pair["streamer"] else None
            if streamer is not None and not gi_animation.has_curves(streamer):
                streamer = None  # An empty streamer clip leaves the body motion unchanged.
            binary, frames, duration, omitted, goals, morph = gi_animation.convert_state(
                body, streamer, client.work, rig, avatar, solver, mask, facial)
        except ExtractionCancelled:
            raise
        except (ValueError, KeyError, TypeError) as error:
            reason = str(error) if isinstance(error, ValueError) else "Unsupported or incompatible native clip."
            skipped.append(dict(state=pair["state"], source=source, reason=reason))
            continue
        finally:
            _clear_samples(client.work)
        digest = hashlib.sha256(binary).hexdigest()
        if (pair["state"], digest) in seen:
            continue
        seen.add((pair["state"], digest))
        key = reusable_clip_key(entries, existing, source, pair["state"], {digest})
        if key is None:
            key = clip_id(pair["state"], source, set(entries))
            if (library / (key + ".json")).exists() or (library / (key + ".bin")).exists():
                raise ValueError("Extracted clips conflict with preserved user files.")
        layers = dict(state=pair["state"], body_source=gi_index.identity(pair["body"]), body_name=pair["body"]["name"],
                      body_duration=duration, streamer_source=gi_index.identity(pair["streamer"]) if streamer else None,
                      streamer_name=pair["streamer"]["name"] if streamer else None,
                      streamer_duration=streamer["stop"] - streamer["start"] if streamer else None,
                      mask_bones=len(mask), omitted=omitted, humanoid_goal_error=goals,
                      facial_targets=len(morph["names"]) if morph else 0)
        metadata = dict(format="mod-viewer-animation", version=2, coordinate_space="asset", layout="frame-major-tqs-f32le",
                        skeleton_id=rig["id"], frame_count=frames, bone_count=len(rig["bones"]), duration=duration,
                        data_file=key + ".bin", sha256=digest, source=source,
                        omitted_non_transform_channels=omitted["non_transform"] + omitted["translation_dof"], layers=layers)
        if morph:
            metadata["morphs"] = zzz_morphs.export_glb(facial, morph, frames, duration, library)
            # The captured weight of each target, so a procedural blink can crossfade from it.
            metadata["morphs"]["rest_weights"] = {
                name: round(facial["offsets"][name], 6) for name in morph["names"]
                if abs(facial["offsets"].get(name, 0.0)) > 1e-3}
            metadata["blend_shape_clamp"] = morph["clamp"]
        (library / (key + ".bin")).write_bytes(binary)
        write_document(library / (key + ".json"), metadata, 1024 * 1024)
        entry = dict(id=key, name=pair["state"][:128], file=key + ".json", duration=duration)
        read_clip(str(library), entry, rig, validated_morphs=validated_morphs)
        entries[key], existing[key] = entry, metadata
        provenance.append(dict(id=key, source=source, state=pair["state"], layers=layers))
        imported += 1
        if len(entries) > 1024 or sum(file.stat().st_size for file in library.rglob("*") if file.is_file()) > 2 * 1024 ** 3:
            raise ValueError("The extracted animation library exceeds its size limit.")
    if not imported:
        raise ValueError("No compatible Genshin Impact animation clips could be converted. Existing Asset files were preserved.")
    write_document(library / "manifest.json", dict(format="mod-viewer-animations", version=1, skeleton_id=rig["id"],
                                                   clips=list(entries.values())), 1024 * 1024)
    write_document(output / "Rig.json", {key: value for key, value in rig.items() if key != "id"}, 2 * 1024 * 1024)
    report = dict(format="mod-viewer-rig-extraction", version=1, game="gi", skeleton_id=rig["id"], skeleton=chosen["source"],
                  meshes=chosen["meshes"], proven_components=len(chosen["matched"]),
                  attachments=sum(1 for palette in chosen["matched"].values() if "attachment" in palette),
                  unmatched_components=len(captured) - len(chosen["matched"]), bones=len(rig["bones"]),
                  controller=gi_index.identity(controller_row), states=len(controller.get("states") or []),
                  streamer_layer=streamer_layer.get("name") if streamer_layer else None,
                  unapplied_layers=[layer.get("name") for layer in gi_animation.synced_layers(controller)
                                    if layer is not streamer_layer],
                  clips=len(entries), imported=imported, secondary=0, skipped=len(skipped), skipped_details=skipped,
                  facial_targets=len(facial["bindings"]) if facial else 0, facial_error=facial_error,
                  animations=provenance)
    write_document(output / "RigExtraction.json", report, 4 * 1024 * 1024)
    return report, fingerprints

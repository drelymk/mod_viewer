"""Genshin Impact Avatar discovery and rig proof, sharing the ZZZ native proof."""

import hashlib
from pathlib import Path

import numpy as np

from app.assets import animation_keys
from app.assets.animation_morphs import idle_blink
from app.assets.animations import read_clip
from . import animator_sequences, gi_animation, gi_index, gi_props, gi_skill_objects, zzz_index, zzz_morphs, zzz_native, zzz_pipeline
from .mecanim import HumanSolver
from .pipeline import (clip_id, document, manifest_props, preserve_library, report_props, reusable_clip_key,
                       write_document)
from .runtime import ExtractionCancelled, check_cancelled, clear_samples

LABEL = "Genshin Impact"
# Long Genshin face chains carry float noise just above ZZZ's 2e-4 inverse-bind
# agreement (about 0.25 mm), and eye bones sit about 0.0015 off their mesh's common
# bind placement; 2e-3 stays about 2 mm and 0.11 degrees.
PLACEMENT_TOLERANCE = 2e-3
# Baked face placement carries that same inverse-bind noise (about 0.09 mm seen), so a
# weightless face capture may sit one shared translation within it off the native rest.
# Some Genshin Blend streams drift from the native weights by up to this much per influence.
WEIGHT_DRIFT = 0.02


def colocated(avatar_row, _family, mesh_row):
    """A character's meshes share its Avatar's bundle; generic mesh names are not evidence."""
    return mesh_row["block"] == avatar_row["block"] and mesh_row["offset"] == avatar_row["offset"]


def _verify(client, captured, index, query, cancel):
    # Genshin submeshes share overlapping vertex ranges, so SubMesh.vertexCount is
    # not the captured component's vertex count; only index counts narrow meshes.
    return zzz_pipeline.verify_candidates(client, captured, index, query, cancel, mesh_hint=colocated, label=LABEL,
                                          placement_tolerance=PLACEMENT_TOLERANCE, shape_key=zzz_pipeline.index_shape,
                                          weight_drift=WEIGHT_DRIFT, attachment_offset=PLACEMENT_TOLERANCE)


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


def _rest_weights(facial, names):
    """The weight the capture already shows for each named target, so a procedural blink can crossfade from it."""
    return {name: round(facial["offsets"][name], 6) for name in names if abs(facial["offsets"].get(name, 0.0)) > 1e-3}


def _idle_face(facial, library):
    """The library's one shared blink face (a clip ``morphs`` document), or None without a unique closing pair.

    Only the face's sparse targets are used; the zero-weight animation inside the GLB is not."""
    if not facial:
        return None
    shown = [name for component in facial["components"] for name in component["targets"]]
    probe = dict(components=[dict(targets=[dict(name=name) for name in shown])], rest_weights=_rest_weights(facial, shown))
    blink = idle_blink(probe)
    if not blink:
        return None
    names = blink[0]
    try:
        morphs = zzz_morphs.export_glb(facial, dict(names=names, weights=np.zeros((2, len(names)), "<f4")), 2, 1 / 60, library)
    except ValueError:
        return None
    return {**morphs, "rest_weights": _rest_weights(facial, names)}


def _props(client, chosen, rig, index, library, cancel, captured, root, record, body_clips, sequences, entries):
    """Props the capture never drew: default-visible attached props, then name-paired skill
    objects; returns (props, skipped, prop_error, object_error). Failures leave the clips,
    and object failures the props, unaffected."""
    avatar_row = next((row for row in index if zzz_index.identity(row) == chosen["source"]), None)
    if avatar_row is None:
        return [], [], "The verified Avatar is missing from the index.", None
    routed = gi_props.routes(index, chosen["avatar_sources"], chosen["avatar"])
    payloads = gi_props.captured_payloads(root, record)
    props, skipped, prop_error, object_error = [], [], None, None
    try:
        # A prop a prefab hides is timed by the clips already written and validated.
        written = ((key, *read_clip(str(library), entry, rig, full_validation=False)) for key, entry in entries.items())
        props, skipped = gi_props.extract(client, chosen, rig, index, library, cancel,
                                          lambda family, row: colocated(avatar_row, family, row), PLACEMENT_TOLERANCE,
                                          captured, routed, payloads, written)
    except ExtractionCancelled:
        raise
    except (ValueError, KeyError, TypeError, OSError) as error:
        prop_error = str(error) if isinstance(error, ValueError) else "The props could not be extracted."
    try:
        objects, skipped_objects = gi_skill_objects.extract(client, chosen, rig, index, library, body_clips, cancel,
                                                            PLACEMENT_TOLERANCE, captured, payloads, props, routed,
                                                            sequences)
        props, skipped = props + objects, skipped + skipped_objects
    except ExtractionCancelled:
        raise
    except (ValueError, KeyError, TypeError, OSError) as error:
        object_error = str(error) if isinstance(error, ValueError) else "The skill objects could not be extracted."
    return props, skipped, prop_error, object_error


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
    existing = {key: document(library / entry["file"]) for key, entry in entries.items()}
    controller_row = gi_animation.controller_row(index, chosen["family"])
    controller = client.read(controller_row)
    _base, streamer_layer = gi_animation.layers(controller)
    mask = gi_animation.streamer_mask(streamer_layer)
    pairs, skipped = gi_animation.state_pairs(controller, index)
    facial, facial_error = _facial(chosen, captured, index)
    points = gi_skill_objects.point_hashes(index, chosen)
    imported, provenance, seen, state_clips, body_clips = 0, [], set(), {}, {}
    for number, pair in enumerate(pairs):
        progress("convert", number, len(pairs))
        check_cancelled(cancel)
        source = gi_animation.pair_source(pair)
        try:
            body = client.read(pair["body"])
            streamer = client.read(pair["streamer"]) if pair["streamer"] else None
            if streamer is not None and not gi_animation.has_curves(streamer):
                streamer = None  # An empty streamer clip leaves the body motion unchanged.
            binary, frames, duration, omitted, goals, morph, keyed, point_tracks = gi_animation.convert_state(
                body, streamer, client.work, rig, avatar, solver, mask, facial, points)
        except ExtractionCancelled:
            raise
        except (ValueError, KeyError, TypeError) as error:
            reason = str(error) if isinstance(error, ValueError) else "Unsupported or incompatible native clip."
            skipped.append(dict(state=pair["state"], source=source, reason=reason))
            continue
        finally:
            clear_samples(client.work)
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
                        omitted_non_transform_channels=omitted["non_transform"] + omitted["translation_dof"], layers=layers,
                        keyed_bones=keyed)
        if morph:
            metadata["morphs"] = zzz_morphs.export_glb(facial, morph, frames, duration, library)
            metadata["morphs"]["rest_weights"] = _rest_weights(facial, morph["names"])
            metadata["blend_shape_clamp"] = morph["clamp"]
        (library / (key + ".bin")).write_bytes(binary)
        write_document(library / (key + ".json"), metadata, 1024 * 1024)
        entry = dict(id=key, name=pair["state"][:128], file=key + ".json", duration=duration)
        read_clip(str(library), entry, rig, validated_morphs=validated_morphs)
        entries[key], existing[key] = entry, metadata
        state_clips.update((index, (key, duration)) for index in pair["state_indices"])
        body = body_clips.setdefault(gi_index.identity(pair["body"]), dict(
            name=pair["body"]["name"], duration=duration, frames=frames, clips=[], points={}))
        body["clips"].append(key)
        body["points"][key] = point_tracks
        provenance.append(dict(id=key, source=source, state=pair["state"], layers=layers))
        imported += 1
        if len(entries) > 1024 or sum(file.stat().st_size for file in library.rglob("*") if file.is_file()) > 2 * 1024 ** 3:
            raise ValueError("The extracted animation library exceeds its size limit.")
    if not imported:
        raise ValueError("No compatible Genshin Impact animation clips could be converted. Existing Asset files were preserved.")
    sequences = animator_sequences.derive(controller, state_clips, animator_sequences.NORMALIZED_TIME)
    props, skipped_props, prop_error, object_error = _props(client, chosen, rig, index, library, cancel, captured,
                                                            root, record, body_clips, sequences, entries)
    manifest = dict(format="mod-viewer-animations", version=1, skeleton_id=rig["id"], clips=list(entries.values()),
                    sequences=sequences, runtime_bones=animation_keys.runtime_bones(rig, existing.values()))
    if props:
        manifest["props"] = manifest_props(props)
    # Clips without facial curves borrow one shared blink face; none is needed when any clip carries one.
    idle_face = None if any(metadata.get("morphs") for metadata in existing.values()) else _idle_face(facial, library)
    if idle_face:
        manifest["idle_face"] = idle_face
    write_document(library / "manifest.json", manifest, 1024 * 1024)
    write_document(output / "Rig.json", {key: value for key, value in rig.items() if key != "id"}, 2 * 1024 * 1024)
    report = dict(format="mod-viewer-rig-extraction", version=1, game="gi", skeleton_id=rig["id"], skeleton=chosen["source"],
                  meshes=chosen["meshes"], proven_components=len(chosen["matched"]),
                  attachments=sum(1 for palette in chosen["matched"].values() if "attachment" in palette),
                  unmatched_components=len(captured) - len(chosen["matched"]), bones=len(rig["bones"]),
                  controller=gi_index.identity(controller_row), states=len(controller.get("states") or []),
                  streamer_layer=streamer_layer.get("name") if streamer_layer else None,
                  unapplied_layers=[layer.get("name") for layer in gi_animation.synced_layers(controller)
                                    if layer is not streamer_layer],
                  clips=len(entries), imported=imported, secondary=0, sequences=len(sequences),
                  skipped=len(skipped), skipped_details=skipped,
                  facial_targets=len(facial["bindings"]) if facial else 0, facial_error=facial_error,
                  idle_face=bool(idle_face),
                  animations=provenance,
                  props=report_props(props),
                  skipped_props=skipped_props, **({"prop_error": prop_error} if prop_error else {}),
                  **({"skill_object_error": object_error} if object_error else {}))
    write_document(output / "RigExtraction.json", report, 4 * 1024 * 1024)
    return report, fingerprints

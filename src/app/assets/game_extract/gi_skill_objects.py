"""Genshin skill objects paired with the character's clips by name.

Skill objects (``SkillObj_<token>_<object>_ModelAvatar``, where the token is the last
part of the character family's name) are separate models with their own skeleton and
clips; gameplay configuration the viewer cannot read spawns and times them. Nothing in
the game data the viewer reads ties them to the character, so they are paired by name:

- An object clip ``Ani_SkillObj_<token>_<object>_<suffix>`` pairs with the character's
  converted clips whose body clip is the only one named ``…_<token>_<suffix>``, when both
  last the same time. A name several clips share is never chosen between.
- No position is guessed beyond one rule: the object's origin is the character's origin,
  facing the same way. The object clip's own curves place it from there.
- The object must not move by root motion, as the character's clips play in place.

Geometry keeps the object's own skin (its Avatar's bones, at most 256), placed in asset
space through the rig root; each pair is baked into a ``prop-track-v2`` affine track
shown for the whole clip, keeping the scale curves objects use to grow in or shrink
away. Textures use the character's learned material slots, routed through the object's
own renderers.
"""

import re

import numpy as np

from app.assets.animations import MAX_PROP_BONES, PROP_AFFINE_TRACK_LAYOUT
from . import gi_index, gi_props, zzz_index, zzz_prop_textures, zzz_props
from .pipeline import rest_worlds
from .runtime import ExtractionCancelled, check_cancelled
from .wuwa_blueprint_props import poses
from .wuwa_props import Writer
from .zzz_animation import dimension, sample_channels
from .zzz_native import mesh_placement

MAX_OBJECTS = 16
MAX_OBJECT_CLIPS = 256
DURATION_TOLERANCE = 1e-3
# Unity's Animator motion curves (MotionT xyz, MotionQ xyzw); the RootT/RootQ curves after
# them repeat the root bone, whose own Transform curves are used instead.
MOTION_ATTRIBUTES = range(7)


def _suffixes(token, body_clips):
    """Character clip suffix (case-folded) -> converted clips, only for names nothing else shares."""
    marker = f"_{token}_"
    names = {}
    for identity, item in body_clips.items():
        name = item["name"]
        if name.startswith("Ani_Avatar_") and name.count(marker) == 1:
            names.setdefault(name.split(marker, 1)[1].casefold(), set()).add(identity)
    return {suffix: body_clips[next(iter(found))] for suffix, found in names.items() if len(found) == 1}


def _objects(index, token):
    """(object name, Avatar rows) of the family's skill objects."""
    found = {}
    for row in index:
        match = gi_index.SKILL_OBJECT.fullmatch(row["name"]) if row["type"] == "Avatar" else None
        if match and match["token"] == token:
            found.setdefault(match["thing"], []).append(row)
    return sorted(found.items())


def _object_clips(index, token, things):
    """Object name -> {suffix (case-folded): [clip rows]}; the longest object name prefix wins."""
    prefix = f"Ani_SkillObj_{token}_"
    result = {thing: {} for thing in things}
    for row in index:
        if row["type"] != "AnimationClip" or not row["name"].startswith(prefix):
            continue
        rest = row["name"][len(prefix):]
        owners = [thing for thing in things if rest.startswith(thing + "_") and len(rest) > len(thing) + 1]
        if owners:
            thing = max(owners, key=len)
            result[thing].setdefault(rest[len(thing) + 1:].casefold(), []).append(row)
    return result


def _locals(clip, work, bones, frames):
    """Frame-major local (xyz, xyzw, scale xyz) transforms of the object's bones, or (None, reason)."""
    sampled = sample_channels(clip, work, np.linspace(clip["start"], clip["stop"], frames), 0)
    by_hash = {bone["hash"]: number for number, bone in enumerate(bones)}
    rest = np.array([[*bone["translation"], *bone["rotation"], *bone["scale"]] for bone in bones], dtype=float)
    output = np.repeat(rest[None], frames, axis=0)
    written, cursor = set(), 0
    for binding in clip["bindings"]:
        width = dimension(binding)
        channel = sampled[:, cursor:cursor + width]
        cursor += width
        if binding["type"] == "Animator":
            if binding["attribute"] in MOTION_ATTRIBUTES and np.ptp(channel) > 1e-4:
                return None, "The object moves by root motion, which its paired clip does not."
            continue
        if binding["type"] != "Transform":
            continue
        bone = by_hash.get(binding["path"])
        attribute = binding["attribute"]
        if bone is None or attribute not in (1, 2, 3) or (bone, attribute) in written:
            return None, "The object clip animates bones its skeleton does not have."
        written.add((bone, attribute))
        if attribute == 2:
            lengths = np.linalg.norm(channel, axis=1)
            if np.any(lengths < 1e-6):
                return None, "The object clip contains a zero rotation."
            channel = channel / lengths[:, None]
        offset = {1: 0, 2: 3, 3: 7}[attribute]
        output[:, bone, offset:offset + width] = channel
    if cursor != sampled.shape[1]:
        raise ValueError("The native curve bindings are incomplete.")
    return output, None


def _track(locals_, bones, rest, anchor):
    """``prop-track-v2`` bytes: per frame and bone, the asset-space affine delta (3x4 rows)
    from the bone's rest skin to its pose, relative to the placement ``anchor``."""
    delta = anchor @ poses(locals_, bones) @ np.linalg.inv(rest) @ np.linalg.inv(anchor)
    if not np.isfinite(delta).all() or np.abs(delta).max() > 1e6:
        raise ValueError("The object animation is invalid.")
    return delta[..., :3, :].astype("<f4").tobytes()


def _tracks(client, thing, clips, suffixes, bones, rest, anchor, cancel, skip):
    """[(track bytes, paired character clips)] of the object's clips that pair by name."""
    found = []
    for suffix, rows in sorted(clips.items())[:MAX_OBJECT_CLIPS]:
        check_cancelled(cancel)
        name = rows[0]["name"]
        partner = suffixes.get(suffix)
        if len(rows) != 1:
            skip(thing, "Several object clips share one name.", name)
            continue
        if partner is None:
            skip(thing, "No single character clip has this clip's name.", name)
            continue
        try:
            clip = client.read(rows[0])
            if abs((clip["stop"] - clip["start"]) - partner["duration"]) > DURATION_TOLERANCE:
                skip(thing, "The paired character clip lasts a different time.", name)
                continue
            locals_, reason = _locals(clip, client.work, bones, partner["frames"])
            if locals_ is None:
                skip(thing, reason, name)
                continue
            found.append((_track(locals_, bones, rest, anchor), partner))
        except ExtractionCancelled:
            raise
        except (ValueError, KeyError, TypeError) as error:
            skip(thing, str(error) if isinstance(error, ValueError) else "Unsupported object clip.", name)
        finally:
            for file in client.work.glob("samples-*.bin"):
                file.unlink(missing_ok=True)
    return found


def _geometry(client, index, thing, avatar_row, avatar, anchor, reference, tolerance, cancel, skip):
    """[(mesh row, mesh, encoded parts)] of the object's meshes proven on its own skeleton."""
    rows = sorted((row for row in index if row["type"] == "Mesh" and row["block"] == avatar_row["block"]
                   and row["offset"] == avatar_row["offset"] and not re.search(r"_LOD[1-9]", row["name"], re.I)),
                  key=zzz_index.identity)
    found = []
    for row in rows:
        check_cancelled(cancel)
        try:
            mesh = client.read(row)
        except ExtractionCancelled:
            raise
        except (ValueError, KeyError, TypeError):
            continue
        if not mesh.get("weights") or not mesh.get("bone_hashes"):
            continue
        placed = mesh_placement(mesh, avatar, tolerance)
        encoded, reason = zzz_props.encode_parts(mesh, placed[0], anchor @ placed[1], reference) if placed else (None, None)
        if encoded is None:
            skip(thing, reason or "The object mesh has no proven placement on its own skeleton.", row["name"])
            continue
        found.append((row, mesh, encoded))
    return found


def extract(client, chosen, rig, index, library, body_clips, cancel, tolerance, captured, payloads, existing, routed):
    """Add the family's name-paired skill objects to ``library/Props``; returns (entries, skipped).

    ``body_clips`` maps a converted body clip's row identity to its name, duration, frame
    count and converted clip keys; ``existing`` are props already written there and
    ``routed`` the character's own renderer routes, which teach the texture slots.
    """
    token = chosen["family"].rsplit("_", 1)[-1]
    objects = _objects(index, token)[:MAX_OBJECTS]
    if not objects:
        return [], []
    writer = Writer(library, existing)
    skipped = writer.skipped

    def skip(thing, reason, source=None):
        skipped.append(dict(kind="skill_object", name=thing, **({"source": source} if source else {}), reason=reason))

    suffixes = _suffixes(token, body_clips)
    clips = _object_clips(index, token, [thing for thing, _rows in objects])
    anchor = rest_worlds(rig["bones"], affine=True)[0]
    reference = np.sign(np.prod(next(iter(next(iter(chosen["native"].values()))))["signs"]))
    entries, meshes, routes = [], [], dict(routed)
    for thing, rows in objects:
        if len(rows) != 1:
            skip(thing, "Several skill object models share this name.")
            continue
        avatar = client.read(rows[0])
        bones = avatar.get("bones")
        if not isinstance(bones, list) or not 0 < len(bones) <= MAX_PROP_BONES:
            skip(thing, "The object skeleton exceeds the prop bone limit.")
            continue
        rest = np.asarray(rest_worlds(bones, affine=True))
        tracks = _tracks(client, thing, clips[thing], suffixes, bones, rest, anchor, cancel, skip)
        geometry = _geometry(client, index, thing, rows[0], avatar, anchor, reference, tolerance, cancel, skip) if tracks else []
        if not geometry:
            continue
        # Tracks are written only once a mesh shows them.
        written = {}
        for data, partner in tracks:
            file, digest = writer.write(data, track=True)
            written.update((key, dict(file=file, sha256=digest, frame_count=partner["frames"], duration=partner["duration"],
                                      start=0.0, end=partner["duration"])) for key in partner["clips"])
        for row, mesh, parts in geometry:
            # A multi-material mesh is one prop per material section (the writer's part material).
            before = len(writer.materials)
            writer.add(row["name"], [(number if len(parts) > 1 else None, part) for number, part in enumerate(parts)],
                       socket=None, bone_count=len(bones), tracks=written, track_layout=PROP_AFFINE_TRACK_LAYOUT)
            for entry, section in writer.materials[before:]:
                entry["source"] = zzz_index.identity(row)
                if section is not None:
                    entry["section"] = section
                entries.append(entry)
                meshes.append(mesh)
        routes.update(gi_props.routes(index, {zzz_index.identity(rows[0])}, avatar))
    if entries:
        zzz_prop_textures.attach(entries, meshes, client, chosen, index, library, cancel, captured, None,
                                 route=lambda _chosen, _mesh, source: sorted(routes.get(source, ()), key=zzz_index.identity),
                                 payloads=payloads)
    return entries, skipped

"""Wuthering Waves meshes a clip's own effect notifies spawn, shown while the game shows them.

A character sequence can carry an effect notify state (``AnimNotifyStateEffect_C``)
whose effect group (``EffectModelGroup_C``) lists effect models. An
``EffectModelSkeletalMesh_C`` model names a skeletal mesh and the animation it plays,
such as a book a character reads during an idle action. Everything is read from the game
data and follows the game's own effect scripts; nothing is paired by name, and an
animation a clip's effect plays belongs to that effect: blueprint props never claim it
for that clip, even when the viewer cannot reproduce the effect.

Placement (``AnimNotifyStateEffect``): an attached effect (the default) follows its
socket; one with ``Attached`` off is placed once, at spawn, at its socket's transform and
stays there; one without a socket is placed at the character mesh's origin, which needs
the clip's root at rest at spawn so that origin does not depend on whether the game
moves the character by root motion. Offsets, follow rules (``NotRealAttach``) and
tag-gated notifies are reported, never approximated.

Timing (``EffectModelGroupSpec``, ``EffectSpec``, ``EffectLifeTime``): the effect spawns
at the notify's start; a group plays each model after its delay (``Value``), and the
mesh model then plays its animation once. Each level (group, model) has a three-stage
lifetime (``StartTime``/``LoopTime``/``EndTime``): without a loop it ends by itself
after their sum; with one it lives until the notify ends, then plays out its lifetime
(``Start + Loop + End`` minus its time wrapped into the loop) unless the notify stops
it at once (``FasterStop``, the default). The mesh hides when either level ends, and
never later than the clip. ``ContinuousLoop`` only concerns particle groups.

An animation whose skeleton converts onto the mesh's own bones is baked into a
``prop-track-v1`` track per clip, with the clip window it shows in; a shorter animation
holds its last frame, as a non-looping animation does.
"""

from pathlib import Path

import numpy as np

from .native import read_limited
from .pipeline import document, exported_file, package_name, rest_worlds, sequence
from .runtime import ExtractionCancelled, check_cancelled
from .wuwa_blueprint_props import MAX_PROP_BONES, _matrices, bake, convert
from .wuwa_props import CHARACTER_ROOT, mesh_parts

GROUP = "EffectModelGroup_C"
MESH_MODEL = "EffectModelSkeletalMesh_C"
# Effect model settings whose meaning for a mesh the viewer reproduces.
MESH_SETTINGS = frozenset(("SkeletalMeshRef", "AnimationRef", "Looping", "StartTime", "LoopTime", "EndTime",
                           "ContinuousLoop", "NativeClass"))
GROUP_SETTINGS = frozenset(("EffectData", "StartTime", "LoopTime", "EndTime", "ContinuousLoop", "NativeClass"))
ABSOLUTE = "EAnimLinkMethod::Absolute"
MAX_EFFECTS = 512
MAX_EFFECT_PROPS = 32
BATCH = 250
MAX_CLIP_BYTES = 160 * 1024 * 1024
# EffectLifeTime treats loops this short as no loop at all.
NEAR_ZERO = 0.001
_STILL = 1e-3


def _exports(client, packages, folder, cancel):
    """Package -> exported JSON document (None when unavailable), batched."""
    found, directory = {}, client.work / folder
    packages = sorted(packages)
    for start in range(0, len(packages), BATCH):
        chunk = packages[start:start + BATCH]
        files = client.batch("json", chunk, directory)
        for package in chunk:
            check_cancelled(cancel)
            file = exported_file(files.get(package), directory, ".json")
            try:
                found[package] = document(file, 16 * 1024 * 1024) if file else None
            except (ValueError, OSError):
                found[package] = None
    return found


def _one(exports, kind):
    """The properties of the single export of ``kind``; None otherwise."""
    items = [item for item in exports if isinstance(item, dict) and item.get("Type") == kind] \
        if isinstance(exports, list) else []
    properties = items[0].get("Properties") if len(items) == 1 else None
    return properties if isinstance(properties, dict) else None


def _number(value, default=0.0):
    if value is None:
        return default
    if type(value) not in (int, float) or not np.isfinite(value):
        raise ValueError("The effect timing is unreadable.")
    return float(value)


def lifetime(properties):
    """(StartTime, LoopTime, EndTime) of a group or model, or a ValueError."""
    stages = tuple(_number(properties.get(key)) for key in ("StartTime", "LoopTime", "EndTime"))
    if stages[0] < 0 or stages[1] < 0 or stages[2] < 0:
        raise ValueError("The effect lifetime starts before its spawn.")
    return stages


def mesh_models(exports):
    """(mesh models as [(package, delay seconds)], group lifetime, reason it cannot be
    exported or None) of one effect.

    Other effect assets (no effect group) spawn no mesh; an unreadable effect raises.
    """
    if not isinstance(exports, list):
        raise ValueError("The effect is unreadable.")
    if not any(isinstance(item, dict) and item.get("Type") == GROUP for item in exports):
        return [], None, None
    properties = _one(exports, GROUP)
    data = properties.get("EffectData") if properties else None
    if not isinstance(data, list):
        raise ValueError("The effect group is unreadable.")
    rows = [row for row in data if isinstance(row, dict) and isinstance(row.get("Key"), str)
            and row["Key"].startswith(MESH_MODEL + "'")]
    models = [package_name(row["Key"]) for row in rows]
    if "" in models:
        raise ValueError("The effect group names an unreadable mesh model.")
    reason = stages = None
    try:
        stages = lifetime(properties)
        delays = [_number(row.get("Value")) for row in rows]
        if any(delay < 0 for delay in delays):
            raise ValueError("The effect group delays a mesh by a negative time.")
    except ValueError as error:
        delays, reason = [0.0] * len(models), str(error)
    if not reason and set(properties) - GROUP_SETTINGS:
        reason = ("The effect group has settings the viewer does not support: "
                  + ", ".join(sorted(set(properties) - GROUP_SETTINGS)) + ".")
    return list(zip(models, delays)), stages, reason


def mesh_model(exports):
    """((mesh package, animation package) or None, model lifetime or None, reason it
    cannot be exported or None) of one skeletal mesh effect model.

    The mesh and animation are read whenever named, so an effect the viewer cannot
    reproduce still claims its animation.
    """
    properties = _one(exports, MESH_MODEL)
    if properties is None:
        return None, None, "The effect mesh model is unreadable."
    mesh, animation = properties.get("SkeletalMeshRef"), properties.get("AnimationRef")
    mesh = package_name(mesh.get("ObjectPath")) if isinstance(mesh, dict) else ""
    animation = package_name(animation.get("ObjectPath")) if isinstance(animation, dict) else ""
    refs = (mesh, animation) if mesh and animation else None
    if set(properties) - MESH_SETTINGS:
        return refs, None, ("The effect mesh model has settings the viewer does not support: "
                            + ", ".join(sorted(set(properties) - MESH_SETTINGS)) + ".")
    if properties.get("Looping") not in (None, False):
        return refs, None, "The effect mesh animation loops."
    try:
        stages = lifetime(properties)
    except ValueError as error:
        return refs, None, str(error)
    if refs is None or not mesh.startswith(CHARACTER_ROOT):
        return refs, None, "The effect mesh model names no character mesh and animation."
    return refs, stages, None


def window(notify, length):
    """(start, end) seconds of an effect notify in a clip of ``length``, or a ValueError.

    The end may lie past the clip's end; playback shows nothing after it anyway.
    """
    start, duration = notify.get("start"), notify.get("duration")
    if (notify.get("link") != ABSOLUTE or type(start) not in (int, float) or type(duration) not in (int, float)
            or not 0 <= start < length or not 0 < duration <= 3600):
        raise ValueError("The effect notify window does not start inside the clip.")
    return float(start), float(start + duration)


def finish(stages, begin, stop, immediately):
    """When one effect level ends, as ``EffectLifeTime`` runs it: it began at ``begin``
    and its notify stops it at ``stop`` (at once when ``immediately``)."""
    start, loop, end = stages
    stamp = start + loop + end
    if loop <= 0:
        # No loop: the lifetime ends by itself, or at the stop when it has none.
        ends = begin + stamp if stamp > 0 else stop
        return min(ends, stop) if immediately else ends
    if immediately:
        return stop
    elapsed = stop - begin
    if elapsed >= start + loop:
        elapsed = start if loop <= NEAR_ZERO else start + np.fmod(elapsed - start, loop)
    return stop + max(0.0, stamp - elapsed)


def visible(notify, length, delay, group, model):
    """(play, hide) clip seconds of one mesh model, or a ValueError.

    The mesh plays its animation after its group's delay and hides when its group or
    model ends, at the clip's end at the latest.
    """
    spawn, stop = window(notify, length)
    play = spawn + delay
    if play >= stop:
        raise ValueError("The effect stops before its mesh plays.")
    immediately = notify.get("faster_stop", True)
    hide = min(finish(group, spawn, stop, immediately), finish(model, play, stop, immediately), length)
    if not hide > play:
        raise ValueError("The effect mesh ends before it shows.")
    return play, hide


def resolve(client, effects, cancel):
    """The effect meshes the Asset's own clips spawn, before any is checked for export.

    ``effects`` maps clip ids to (clip length, effect notify facts from ``pipeline.sequence``).
    Returns (uses, skipped): ``uses`` lists dicts of clip, length, notify, model, delay,
    refs ((mesh, animation) or None), group and model lifetimes, and the reason it
    cannot be exported or None; ``skipped`` maps (source, reason) to clips.
    """
    notified = [(clip, length, notify) for clip, (length, notifies) in sorted(effects.items())
                for notify in notifies if isinstance(notify, dict) and notify.get("effect")][:MAX_EFFECTS]
    skipped, uses = {}, []
    if not notified:
        return uses, skipped
    groups = _exports(client, {notify["effect"] for _clip, _length, notify in notified}, "effect-groups", cancel)
    models = {}
    for clip, length, notify in notified:
        try:
            found, group, reason = mesh_models(groups.get(notify["effect"]))
        except ValueError as error:
            skipped.setdefault((notify["effect"], str(error)), set()).add(clip)
            continue
        # Groups without meshes are particle and other effects, not props.
        for model, delay in found:
            uses.append(dict(clip=clip, length=length, notify=notify, model=model, delay=delay, group=group,
                             reason=reason))
            models[model] = None
    if uses:
        for model, exports in _exports(client, models, "effect-models", cancel).items():
            models[model] = mesh_model(exports)
    for use in uses:
        refs, stages, reason = models[use["model"]]
        use.update(refs=refs, stages=stages, reason=use["reason"] or reason)
    return uses, skipped


def claimed(uses):
    """(clip, animation) pairs the clips' effects play, whether or not the viewer can
    reproduce the effect: no other prop plays them in those clips."""
    return {(use["clip"], use["refs"][1]) for use in uses if use["refs"]}


class _Poses:
    """Bone worlds of the Asset's own converted clips, read lazily."""

    def __init__(self, library, rig):
        self.library, self.rig, self.cache = Path(library), rig, {}
        self.rest = np.asarray(rest_worlds(rig["bones"], affine=True))

    def _clip(self, clip):
        if clip not in self.cache:
            value = None
            try:
                metadata = document(self.library / (clip + ".json"), 1024 * 1024)
                frames, count, duration = metadata["frame_count"], metadata["bone_count"], metadata["duration"]
                if (metadata.get("layout") == "frame-major-tq-f32le" and count == len(self.rig["bones"])
                        and duration > 0 and frames > 1):
                    data = np.frombuffer(read_limited(self.library / metadata["data_file"], MAX_CLIP_BYTES, "Clip"),
                                         "<f4", count=frames * count * 7).reshape(frames, count, 7).astype(float)
                    value = data, duration
            except (OSError, ValueError, KeyError, TypeError):
                value = None
            self.cache[clip] = value
        return self.cache[clip]

    def frames(self, clip, start, end):
        """(locals, frame indices) of the clip's frames from ``start`` to ``end`` seconds."""
        found = self._clip(clip)
        if found is None:
            raise ValueError("The clip pose is unreadable.")
        data, duration = found
        count = len(data)
        first = min(count - 1, max(0, int(np.floor(start / duration * (count - 1) + 1e-6))))
        last = min(count - 1, max(first, int(np.ceil(end / duration * (count - 1) - 1e-6))))
        return data, range(first, last + 1)

    def worlds(self, data, frame):
        bones = self.rig["bones"]
        scale = np.asarray([bone["scale"] for bone in bones], dtype=float)
        local = _matrices(data[frame, :, :3], data[frame, :, 3:7], scale)
        world = np.zeros_like(local)
        for index, bone in enumerate(bones):
            world[index] = local[index] if bone["parent"] < 0 else world[bone["parent"]] @ local[index]
        return world

    def root_at_rest(self, clip, time):
        """Whether every root bone is at its rest transform on the frames around ``time``."""
        data, frames = self.frames(clip, time, time)
        for index, bone in enumerate(self.rig["bones"]):
            if bone["parent"] >= 0:
                continue
            values = data[list(frames), index]
            if (np.abs(values[:, :3] - np.asarray(bone["translation"], dtype=float)).max() > _STILL
                    or (np.abs(values[:, 3:7] @ np.asarray(bone["rotation"], dtype=float)) < 1 - 1e-6).any()):
                return False
        return True

    def socket(self, clip, index, start, end):
        """The socket's world at ``start`` and whether it stays there until ``end``."""
        data, frames = self.frames(clip, start, end)
        worlds = [self.worlds(data, frame)[index] for frame in frames]
        still = all(np.abs(world - worlds[0]).max() <= _STILL for world in worlds)
        return worlds[0], still


def placement(use, names, poses):
    """("socket", bone index) for a prop following its socket, or ("fixed", asset-space
    world) for one placed once at spawn; a ValueError when the viewer cannot reproduce it."""
    notify = use["notify"]
    if notify["settings"]:
        raise ValueError("The effect notify has settings the viewer does not support: "
                         + ", ".join(notify["settings"]) + ".")
    spawn = float(notify["start"])
    socket = notify["socket"]
    if socket is None:
        # Placed at the mesh's origin at spawn, which root motion would move.
        if not poses.root_at_rest(use["clip"], spawn):
            raise ValueError("The clip moves its root when the effect spawns.")
        return "fixed", np.eye(4)
    if names.count(socket) != 1:
        raise ValueError("The effect socket is not a unique bone of the verified rig.")
    index = names.index(socket)
    if not notify["attached"]:
        # A socket's world at spawn is the same whether the game moves the character by
        # root motion or by its root bone; between two frames it must not move.
        world, still = poses.socket(use["clip"], index, spawn, spawn)
        if not still:
            raise ValueError("The effect spawns while its socket moves between frames.")
        return "fixed", world
    return "socket", index


def extract(client, resolved, rig, library, writer, cancel):
    """Add the effect meshes of ``resolve`` that extraction can reproduce to ``writer``."""
    uses, skipped = resolved
    names = [bone["name"] for bone in rig["bones"]]
    worlds = rest_worlds(rig["bones"], affine=True)
    poses = _Poses(library, rig)

    def skip(source, clip, reason):
        skipped.setdefault((source, reason), set()).add(clip)

    props = {}
    for use in uses:
        try:
            if use["reason"]:
                raise ValueError(use["reason"])
            play, hide = visible(use["notify"], use["length"], use["delay"], use["group"], use["stages"])
            kind, where = placement(use, names, poses)
        except ValueError as error:
            skip(use["model"], use["clip"], str(error))
            continue
        mesh, animation = use["refs"]
        key = (mesh, kind, where if kind == "socket" else tuple(np.round(where, 4).ravel()))
        props.setdefault(key, dict(where=where, clips={}))["clips"].setdefault(use["clip"], []).append(
            (animation, play, hide))
    # A clip spawning one mesh at one place several times shows several instances: the
    # n-th spawn of each clip (by animation, then window) is the n-th instance.
    instances = {}
    for (mesh, kind, where), prop in props.items():
        for clip, spawns in prop["clips"].items():
            for number, spawn in enumerate(sorted(set(spawns))):
                instances.setdefault((mesh, kind, where, number), dict(where=prop["where"], clips={}))["clips"][clip] = spawn
    ordered = sorted(instances.items(), key=lambda item: (item[0][0], item[0][1], str(item[0][2]), item[0][3]))
    for (mesh, _kind, _where, _number), prop in ordered[MAX_EFFECT_PROPS:]:
        for clip in prop["clips"]:
            skip(mesh, clip, "The clips spawn too many effect props.")
    animations = {}
    for (mesh, kind, _where, _number), prop in ordered[:MAX_EFFECT_PROPS]:
        check_cancelled(cancel)
        anchor = worlds[prop["where"]] if kind == "socket" else prop["where"]
        try:
            native, _document, parts = mesh_parts(client, mesh, anchor)
            if not 0 < len(native["bones"]) <= MAX_PROP_BONES:
                raise ValueError("The prop skeleton is unsupported.")
        except ExtractionCancelled:
            raise
        except (ValueError, KeyError, TypeError, IndexError, OSError) as error:
            for clip in prop["clips"]:
                skip(mesh, clip, str(error) if isinstance(error, ValueError) else "The prop could not be read.")
            continue
        tracks, baked, references = {}, {}, {}
        for clip, (animation, play, hide) in sorted(prop["clips"].items()):
            check_cancelled(cancel)
            try:
                if animation not in animations:
                    animations[animation] = _exports_metadata(client, animation)
                item = animations[animation]
                if item is None:
                    raise ValueError("The effect mesh animation is unreadable.")
                if animation not in baked:
                    skeleton = package_name(item["Properties"].get("Skeleton", {}).get("ObjectPath"))
                    frames, binary = convert(client, animation, item, native["bones"], skeleton, references)
                    baked[animation] = (frames, float(item["Properties"]["SequenceLength"]),
                                        writer.write(bake(binary, frames, native["bones"], anchor), track=True))
                frames, duration, (path, digest) = baked[animation]
            except ExtractionCancelled:
                raise
            except (ValueError, KeyError, TypeError) as error:
                skip(animation, clip, str(error) if isinstance(error, ValueError) else "The effect mesh animation is incomplete.")
                continue
            tracks[clip] = dict(file=path, sha256=digest, frame_count=frames, duration=duration, start=play, end=hide)
        if tracks:
            writer.add(mesh, parts, socket=prop["where"] if kind == "socket" else None,
                       bone_count=len(native["bones"]), tracks=tracks)
    for (source, reason), clips in sorted(skipped.items()):
        writer.skipped.append(dict(kind="effect", source=source, clips=sorted(clips), reason=reason))


def _exports_metadata(client, animation):
    """Slim metadata of one effect mesh animation, or None when unusable."""
    directory = client.work / "effect-animations"
    file = exported_file(client.batch("json", [animation], directory).get(animation), directory, ".json")
    item = sequence(file) if file else None
    properties = item.get("Properties", {}) if item else {}
    if not item or type(properties.get("NumFrames")) is not int or type(properties.get("SequenceLength")) not in (int, float):
        return None
    return item

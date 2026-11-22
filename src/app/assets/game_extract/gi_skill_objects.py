"""Genshin skill objects paired with the character's clips by name.

Skill objects (``SkillObj_<token>_<object>_ModelAvatar``, where the token is the last
part of the character family's name) are separate models with their own skeleton and
clips; gameplay configuration the viewer cannot read spawns and times them. Nothing in
the game data the viewer reads ties them to the character, so they are paired by name:

- An object clip ``Ani_SkillObj_<token>_<object>_<suffix>`` pairs with the character's
  converted clips whose body clip is the only one named ``…_<token>_<suffix>``, when both
  last the same time. A name several clips share is never chosen between. Failing that,
  names compare ignoring case, separators and a ``Bone`` marker (``Show01`` and
  ``Show_01_Bone_01_BS`` against ``Show_01``/``Show_01_BS``), still needing one clip, one
  length and no other object clip of the same object claiming that clip.
- An object's own controller plays runs: from each plain state, the states it reaches without
  gameplay input (each state's only exit-time transition without conditions). A run is named
  by its first state, by the controller's name after the object's plus that state, by its
  first clip's name after the object's and, from the controller's entry state, by the
  controller's name alone (``…_Chess_Show_01`` plays ``Take 001``). It pairs with the one
  character clip whose length it fills (ending up to one 60 Hz frame early and holding its
  last pose) and whose words a name has exactly, or else whose action word a name starts
  with and whose other words it keeps in order. It plays with the controller's timing and
  crossfades; its clips then pair only through it. Short generic states (``BS``, ``Loop``)
  or unrelated names pair through nothing else, as length alone is often coincidental.
- A run no single clip fits may play through a character sequence's leading steps (one state
  of the object for ``ElementalBurst``, two more for ``ElementalBurst_AS``): every step must end
  where a state of the run hands over or the run ends (within one frame), the run must fill the
  last step, and a name must relate to the first step's clip either way round. Each step's clip
  then shows the part of the run it plays; several such sequences leave the run unpaired.
- Placement comes from the game's effect prefabs that hold the object's Animator (by its
  Avatar or, for generic rigs without one, by playing one of the object's controllers): the
  prefab root's Follow component (``MoleMole.MonoEffectPluginFollow``) names the character
  point it follows, empty for the character itself, and the prefab's own transforms place
  the object below that root. Prefabs whose Animator plays the pair's own controller decide
  first; otherwise prefabs that agree decide, or else the most specific one whose label
  begins the pair's name (``Eff_<family>_<label>[_<object>][_SkillObj]``, ``Show_01``
  naming ``Show_01_BS``) or else the generic ``Eff_<family>_<object>``; prefabs that still
  disagree leave the clip skipped. A point is the character bone with that name or, outside
  the Avatar, the character clip's own ``Root/<point>`` track (``Root`` itself identity unless
  the clip animates it); the mount follows the point's position and rotation, not its scale.
  An object no Follow prefab holds keeps the character's origin, facing the same way. The
  object clip's own curves place it from there.
- The object must not move by root motion, as the character's clips play in place.

Geometry keeps the object's own skin (its Avatar's bones, at most 256), placed in asset
space through the rig root; each pair is baked per converted clip into a ``prop-track-v2``
affine track shown for the whole clip, keeping the scale curves objects use to grow in or
shrink away. Textures use the character's learned material slots, routed through the
object's own renderers.
"""

from collections import Counter
import re
from typing import NamedTuple
import zlib

import numpy as np

from app.assets.animations import MAX_PROP_BONES, PROP_AFFINE_TRACK_LAYOUT, read_clip
from . import gi_animation, gi_index, gi_props, zzz_index, zzz_prop_textures, zzz_props
from .animator_sequences import _fade, _playable
from .pipeline import rest_worlds
from .runtime import ExtractionCancelled, check_cancelled, clear_samples
from .wuwa_blueprint_props import poses
from .wuwa_props import Writer
from .zzz_animation import dimension, sample_channels
from .zzz_native import mesh_placement

MAX_OBJECTS = 16
MAX_OBJECT_CLIPS = 256
DURATION_TOLERANCE = 1e-3
# A chained run may end up to one 60 Hz frame before its character clip and hold its last pose.
RUN_FRAME = 1 / 60
# Unity's Animator motion curves (MotionT xyz, MotionQ xyzw); the RootT/RootQ curves after
# them repeat the root bone, whose own Transform curves are used instead.
MOTION_ATTRIBUTES = range(7)
# Character points outside the Avatar live under this child of the character's root.
POINT_ROOT = "Root"
# The mount keeps the point's rotation; a point scaled this close to nothing has none.
MIN_POINT_SCALE = 1e-6
# A ``Bone`` marker (with its number) in clip and prefab names distinguishes nothing.
BONE_MARKER = re.compile(r"_?bone(?:_?\d+)?")


class State(NamedTuple):
    """One state of a run, in run seconds: where it starts, the clip time it enters at, the
    crossfade into it, its clip's length and whether it loops."""
    start: float
    offset: float
    fade: float
    length: float
    loop: bool


def _hash(path):
    return zlib.crc32(path.encode("utf-8"))


def _tokens(name):
    """A clip or prefab label's letter and number runs, case-folded, without a ``Bone`` marker
    (``Show02``, ``Show_02`` and ``Show_02_Bone_01`` all give show, 02)."""
    return re.findall(r"[a-z]+|\d+", BONE_MARKER.sub("", name.casefold()))


def _normalized(suffix):
    """A clip suffix without case, separators or a ``Bone`` marker."""
    return "".join(_tokens(suffix))


def _owner(rest, things):
    """The object whose name begins ``rest`` (the longest), or None."""
    owners = [thing for thing in things if rest == thing or rest.startswith(thing + "_")]
    return max(owners, key=len) if owners else None


def _suffixes(token, body_clips):
    """(exact, loose, characters) character clip lookups for suffixes nothing else shares.

    ``exact`` maps a case-folded suffix to its converted clips and ``loose`` a normalized
    suffix; a normalized name that two different suffixes reach is left out. ``characters``
    maps each uniquely named clip's ``id`` to (its suffix, the suffix's tokens).
    """
    marker = f"_{token}_"
    names = {}
    for identity, item in body_clips.items():
        name = item["name"]
        if name.startswith("Ani_Avatar_") and name.count(marker) == 1:
            names.setdefault(name.split(marker, 1)[1].casefold(), set()).add(identity)
    exact = {suffix: body_clips[next(iter(found))] for suffix, found in names.items() if len(found) == 1}
    loose = {}
    for suffix, found in names.items():
        loose.setdefault(_normalized(suffix), set()).update(found)
    characters = {id(partner): (suffix, _tokens(suffix)) for suffix, partner in exact.items()}
    return exact, {key: body_clips[next(iter(found))] for key, found in loose.items() if len(found) == 1}, characters


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
        thing = _owner(rest, [thing for thing in things if len(rest) > len(thing) + 1])
        if thing is not None:
            result[thing].setdefault(rest[len(thing) + 1:].casefold(), []).append(row)
    return result


def _label(name, family, thing):
    """The clip part of an effect prefab's name, ``Eff_<family>_<label>[_<thing>][_SkillObj]``
    ("" for the generic ``Eff_<family>_<thing>``), or None for prefabs named otherwise."""
    prefix = f"Eff_{family}_"
    if not name.startswith(prefix):
        return None
    rest = name[len(prefix):]
    if rest.casefold().endswith("_skillobj"):
        rest = rest[:-len("_SkillObj")]
    if rest.casefold() == thing.casefold():
        return ""
    if rest.casefold().endswith("_" + thing.casefold()):
        return rest[:-len(thing) - 1]
    return rest


def _held(cabs, item):
    """(Avatar row, controller row) a Follow row's Animator points to, each None when absent
    or not resolved unambiguously."""
    return tuple(gi_index.resolve_pointer(cabs, item[key]["cab"], item[key]["path_id"], kind) if item[key] else None
                 for key, kind in (("avatar", "Avatar"), ("controller", "AnimatorController")))


def _prefabs(follows, cabs, family, thing, avatar_row, controllers):
    """[(label, placement, controller)] of the effect prefabs holding the object's Animator, by
    its Avatar or, without one, by playing a controller of ``controllers`` (row identities). A
    placement is (point, transform chain from the prefab root's child down to the Animator),
    or None when the prefab does not prove one; ``controller`` is the one its Animator plays."""
    found = []
    for row in follows:
        held = []
        for item in row["animators"]:
            avatar, target = _held(cabs, item)
            controller = zzz_index.identity(target) if target is not None else None
            if avatar is avatar_row or (avatar is None and controller in controllers):
                held.append((item, controller if controller in controllers else None))
        if not held:
            continue
        placement = None
        if len(held) == 1 and row["enabled"] and row["follow"] is not None:
            chain = tuple((tuple(node["translation"]), tuple(node["rotation"]), tuple(node["scale"]))
                          for node in held[0][0]["chain"])
            placement = (row["follow"], chain)
        found.append((_label(row["name"], family, thing), placement, held[0][1] if len(held) == 1 else None))
    return found


def _placement(prefabs, *names, controller=None):
    """(placement, None) for a pair named by ``names`` whose clips play in ``controller``, or
    (None, reason)."""
    playing = {placement for _label, placement, own in prefabs if controller is not None and own == controller}
    if len(playing) > 1:
        return None, "The effect prefabs that play the object's controller place it differently."
    distinct = playing or {placement for _label, placement, _own in prefabs}
    if len(distinct) > 1:
        tokens = [_tokens(name) for name in names]
        named = [(len(words), placement) for label, placement, _own in prefabs
                 if label and (words := _tokens(label)) and any(words == own[:len(words)] for own in tokens)]
        longest = max((length for length, _placement in named), default=0)
        named = {placement for length, placement in named if length == longest}
        if not named:
            named = {placement for label, placement, _own in prefabs if label == ""}
        if len(named) != 1:
            return None, "The object's effect prefabs place it differently and none is named for this clip."
        distinct = named
    placement = next(iter(distinct))
    if placement is None:
        return None, "The object's effect prefab does not prove where it is mounted."
    return placement, None


def point_hashes(index, chosen):
    """Path hashes of the character points the family's skill objects may follow outside the
    Avatar (``Root/<point>`` and ``Root``), kept while the character's clips convert."""
    token = chosen["family"].rsplit("_", 1)[-1]
    # The family's skill-object Avatars and controllers (generic-rig Animators have only the latter).
    owned = {zzz_index.identity(row) for _thing, rows in _objects(index, token) for row in rows}
    owned |= {zzz_index.identity(row) for row in index if row["type"] == "AnimatorController"
              and row["name"].startswith(f"Ani_SkillObj_{token}_")}
    cabs = gi_index.by_cab(index)
    names = {row["follow"] for row in index if row["type"] == "Follow" and row["follow"] and any(
        target is not None and zzz_index.identity(target) in owned
        for item in row["animators"] for target in _held(cabs, item))}
    return frozenset({_hash(f"{POINT_ROOT}/{name}") for name in names} | ({_hash(POINT_ROOT)} if names else set()))


def _rigid(worlds, frame):
    """``worlds`` keeping only position and rotation relative to the rig root ``frame``, or
    None when a point collapses to nothing."""
    local = np.linalg.inv(frame) @ worlds
    u, singular, vt = np.linalg.svd(local[..., :3, :3])
    if singular.min() < MIN_POINT_SCALE:
        return None
    local[..., :3, :3] = u @ vt
    return frame @ local


def _bone_worlds(library, rig, key, duration, bone):
    """Frame-major asset-space worlds of one rig bone in a converted clip."""
    raw, data = read_clip(str(library), dict(id=key, name=key, file=key + ".json", duration=duration), rig)
    frames, count = raw["frame_count"], raw["bone_count"]
    chain = []
    while bone >= 0:
        chain.append(bone)
        bone = rig["bones"][bone]["parent"]
    chain.reverse()
    table = np.frombuffer(data, "<f4", count=frames * count * 10).reshape(frames, count, 10)[:, chain].astype(float)
    return poses(table, [dict(parent=number - 1) for number in range(len(chain))])[:, -1]


def _local(track):
    """Frame-major matrices of (xyz, xyzw) local transforms."""
    return poses(np.hstack([track, np.ones((len(track), 3))])[:, None], [dict(parent=-1)])[:, 0]


def _mounts(placement, keys, library, rig, avatar, anchor, partner, cancel, worlds_of):
    """{converted clip key: (frame-major asset-space mount worlds, None) or (None, reason)} of
    ``keys``; ``worlds_of`` caches converted bone worlds by (clip key, bone)."""
    point = placement[0] if placement is not None else ""
    if not point:
        return {key: (np.broadcast_to(anchor, (partner["frames"], 4, 4)), None) for key in keys}
    named = [number for number, bone in enumerate(avatar["bones"]) if number and bone["path"].rsplit("/", 1)[-1] == point]
    if len(named) > 1:
        return {key: (None, "Several character bones share the point the effect prefab follows.") for key in keys}

    def bone_worlds(key, bone):
        if (key, bone) not in worlds_of:
            worlds_of[key, bone] = _bone_worlds(library, rig, key, partner["duration"], bone)
        return worlds_of[key, bone]
    result = {}
    for key in keys:
        check_cancelled(cancel)
        if named:
            # Avatar bone n is rig bone n + 1 (the rig root holds the mesh-to-Avatar basis).
            worlds = bone_worlds(key, named[0] + 1)
        else:
            tracks = partner["points"].get(key, {})
            track = tracks.get(_hash(f"{POINT_ROOT}/{point}"))
            if track is None:
                result[key] = None, "The paired character clip does not animate the point the effect prefab follows."
                continue
            local = _local(track)
            if (root := tracks.get(_hash(POINT_ROOT))) is not None:
                local = _local(root) @ local
            # Points outside the Avatar sit below the character's root, the Avatar's root bone.
            worlds = bone_worlds(key, 1) @ local
        worlds = _rigid(worlds, anchor)
        result[key] = (worlds, None) if worlds is not None else (None, "The point the effect prefab follows collapses to nothing.")
    return result


def _runs(client, index, cabs, token, things, cancel):
    """{object: [(names, [(clip row, state, transition into it or None)], controller identity)]}
    from each plain state of the objects' own controllers (see the module docstring)."""
    prefix = f"Ani_SkillObj_{token}_"
    result = {thing: [] for thing in things}
    controllers = [row for row in index if row["type"] == "AnimatorController" and row["name"].startswith(prefix)]
    for row in sorted(controllers, key=zzz_index.identity):
        rest = row["name"][len(prefix):]
        thing = _owner(rest, things)
        if thing is None:
            continue
        label = rest[len(thing) + 1:]
        check_cancelled(cancel)
        try:
            document = client.read(row)
            states, entry = document.get("states"), document.get("default_state")
        except ExtractionCancelled:
            raise
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
        if not isinstance(states, list) or len(states) > gi_animation.MAX_STATES:
            continue
        clips = {}
        for number, state in enumerate(states):
            motions = state.get("motions") if isinstance(state, dict) else None
            leaves = motions[0].get("leaves") if isinstance(motions, list) and len(motions) == 1 and motions[0] else None
            # Only plain, unmirrored states at normal speed play one clip as authored.
            if (isinstance(leaves, list) and len(leaves) == 1 and _playable(states, number)
                    and not state.get("mirror") and not state.get("mirror_param")):
                target = gi_index.resolve_pointer(cabs, leaves[0]["cab"], leaves[0]["path_id"])
                if target is not None:
                    clips[number] = target
        clip_prefix = f"{prefix}{thing}_"
        for first in sorted(clips):
            chain, current = [(clips[first], states[first], None)], first
            while True:
                automatic = [edge for edge in states[current].get("transitions") or []
                             if edge.get("has_exit_time") and not edge.get("conditions")]
                if len(automatic) != 1:
                    break
                following = automatic[0].get("dest")
                if following not in clips or any(state is states[following] for _row, state, _edge in chain):
                    chain = None
                    break
                chain.append((clips[following], states[following], automatic[0]))
                current = following
            name = states[first].get("name")
            if chain and isinstance(name, str) and name:
                names = [name, *([f"{label}_{name}"] if label else []),
                         *([clips[first]["name"][len(clip_prefix):]] if clips[first]["name"].startswith(clip_prefix) else []),
                         *([label] if label and first == entry else [])]
                result[thing].append((names, chain, zzz_index.identity(row)))
    return result


def _contains(words, within):
    """Whether ``words`` begin with ``within``'s first word (the action, such as ``Show`` or
    ``ElementalArt``) and all appear in order in it, phase words such as ``AS`` aside."""
    remaining = iter(within)
    return bool(words) and bool(within) and words[0] == within[0] and all(word in remaining for word in words)


def _fits(end, timeline, duration):
    """Whether a run (its end, or None when its last state loops) fills a character clip of ``duration``."""
    if end is None:
        return timeline[-1].start < duration
    return duration - RUN_FRAME - DURATION_TOLERANCE <= end <= duration + DURATION_TOLERANCE


def _run_partner(names, timeline, end, characters, partners):
    """The one character clip of ``partners`` a run fills whose words a name has exactly, or
    else whose action word and other words in order a name has; None otherwise."""
    fitting = [partner for partner in partners if _fits(end, timeline, partner["duration"])]
    for match in (lambda words, within: words == within, _contains):
        found = {id(partner): partner for name in names for partner in fitting
                 if match(_tokens(name), characters[id(partner)][1])}
        if found:
            return next(iter(found.values())) if len(found) == 1 else None
    return None


def _timeline(chain, lengths):
    """The chained states (``lengths`` are their clips' lengths) as :class:`State`, then the
    run's end (None when the last state loops). Unity's exit time and offset are normalized
    state times."""
    timeline, start, offset = [], 0.0, 0.0
    for (_row, state, transition), length in zip(chain, lengths):
        length, fade = float(length), 0.0
        if transition is not None:
            previous = timeline[-1]
            exit_time, duration = float(transition.get("exit_time", 0)), float(transition.get("duration", 0))
            if not (0 < exit_time <= 1e3 and 0 <= duration <= 60):
                raise ValueError("The object controller has an unsupported transition.")
            start = previous.start + exit_time * previous.length - previous.offset
            fade = _fade(transition, previous.length)
            offset = float(transition.get("offset", 0)) * length
        if not 0 < length <= 3600 or not np.isfinite(start):
            raise ValueError("The object controller has unsupported clip timing.")
        timeline.append(State(start, offset, fade, length, bool(state and state.get("loop"))))
    last = timeline[-1]
    return timeline, (None if last.loop else last.start + last.length - last.offset)


def _blend(source, target, weight):
    """Per-frame blend of local transforms (translation and scale linear, rotation normalized)."""
    weight = weight[:, None, None]
    out = source + (target - source) * weight
    q_source, q_target = source[..., 3:7], target[..., 3:7]
    q_target = np.where(np.sum(q_source * q_target, -1, keepdims=True) < 0, -q_target, q_target)
    rotation = q_source + (q_target - q_source) * weight
    out[..., 3:7] = rotation / np.linalg.norm(rotation, axis=-1, keepdims=True)
    return out


def _spans(names, timeline, end, sequences, keyed, characters):
    """[[(character clip, converted clip key, run seconds at the clip's start)]]: the character
    sequences whose leading steps one run plays through (see the module docstring)."""
    handovers = [state.start for state in timeline[1:]] + ([end] if end is not None else [])
    found = {}
    for sequence in sequences:
        steps, elapsed = [], 0.0
        for step in sequence.get("steps") or []:
            partner = keyed.get(step.get("clip"))
            if partner is None or not 0 <= step["start"] < step["leave"]:
                break
            steps.append((partner, step["clip"], elapsed - step["start"]))
            elapsed += step["leave"] - step["start"]
            if not any(abs(elapsed - handover) <= RUN_FRAME + DURATION_TOLERANCE for handover in handovers):
                break
            if len(steps) > 1 and _fits(end, timeline, elapsed):
                head = characters[id(steps[0][0])][1]
                if any((words := _tokens(name)) == head or _contains(words, head) or _contains(head, words)
                       for name in names):
                    found[tuple(key for _partner, key, _offset in steps)] = list(steps)
    return list(found.values())


def _sequence(documents, timeline, work, bones, times, root):
    """Frame-major locals of a run at ``times`` (run seconds; each state from its start,
    crossfading into the next for the transition's duration; a last non-looping state holds
    its end), or (None, reason)."""
    output = None
    for document, state in zip(documents, timeline):
        local = np.maximum(times - state.start + state.offset, 0)
        # A looping state wraps past its end; a state ending exactly there keeps its last frame.
        local = (np.where(local > state.length, np.mod(local, state.length), local) if state.loop
                 else np.minimum(local, state.length))
        locals_, reason = _locals(document, work, bones, document["start"] + local, root)
        if locals_ is None:
            return None, reason
        if output is None:
            output = locals_
            continue
        weight = (np.clip((times - state.start) / state.fade, 0, 1) if state.fade > 0 else (times >= state.start).astype(float))
        output = _blend(output, locals_, weight)
    return output, None


def _locals(clip, work, bones, times, root=None):
    """Frame-major local (xyz, xyzw, scale xyz) transforms of the object's bones at ``times``
    (clip seconds), or (None, reason). ``root`` replaces the Avatar root's rest with the effect
    prefab's own transform of the Animator, which the clip may still animate."""
    sampled = sample_channels(clip, work, times, 0)
    by_hash = {bone["hash"]: number for number, bone in enumerate(bones)}
    rest = np.array([[*bone["translation"], *bone["rotation"], *bone["scale"]] for bone in bones], dtype=float)
    if root is not None:
        rest[0] = root
    output = np.repeat(rest[None], len(times), axis=0)
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


def _track(locals_, bones, rest, anchor, mount):
    """``prop-track-v2`` bytes: per frame and bone, the asset-space affine delta (3x4 rows)
    from the bone's rest skin to its pose, relative to the placement ``anchor``; ``mount`` is
    the frame-major asset-space world the object's root sits in."""
    delta = mount[:, None] @ poses(locals_, bones) @ np.linalg.inv(rest) @ np.linalg.inv(anchor)
    if not np.isfinite(delta).all() or np.abs(delta).max() > 1e6:
        raise ValueError("The object animation is invalid.")
    return delta[..., :3, :].astype("<f4").tobytes()


def _place(placement):
    """(parent chain matrix, Animator rest) of an effect prefab placement (identity, None without one)."""
    if placement is None:
        return np.eye(4), None
    nodes = [dict(translation=translation, rotation=rotation, scale=scale, parent=number - 1)
             for number, (translation, rotation, scale) in enumerate(placement[1])]
    parent = rest_worlds(nodes[:-1], affine=True)[-1] if len(nodes) > 1 else np.eye(4)
    # The Animator's own transform becomes the object root's rest.
    translation, rotation, scale = placement[1][-1]
    return parent, [*translation, *rotation, *scale]


def _tracks(client, thing, clips, runs, suffixes, sequences, bones, rest, anchor, cancel, skip, prefabs, mounting):
    """[(track bytes, paired character clip, converted clip key)] of the object's clips and
    controller runs that pair by name, and of runs that play through a character sequence's
    leading steps; ``mounting(placement, partner, keys)`` gives the mounts (:func:`_mounts`)."""
    exact, loose, characters = suffixes
    partners = list(exact.values())
    keyed = {key: partner for partner in partners for key in partner["clips"]}
    documents = {}

    def document(row):
        # Each clip is read once per object; its sample buffer lives until the object is done.
        key = zzz_index.identity(row)
        if key not in documents:
            documents[key] = client.read(row)
        return documents[key]

    # Jobs: (chain, steps [(character clip, converted clip keys, run seconds at its start)], loosely,
    # placement names, controller, timeline or None for a clip paired on its own).
    jobs, spans, chained = [], [], set()
    try:
        # The controllers whose states play each object clip.
        playing = {}
        for _names, chain, controller in runs:
            for row, _state, _edge in chain:
                playing.setdefault(zzz_index.identity(row), set()).add(controller)
        # A controller run pairs by its names and its length, or else spans a character sequence;
        # its clips then pair only through it.
        for names, chain, controller in runs[:MAX_OBJECT_CLIPS]:
            check_cancelled(cancel)
            try:
                timeline, end = _timeline(chain, [document(row)["stop"] - document(row)["start"] for row, _state, _edge in chain])
            except ExtractionCancelled:
                raise
            except (ValueError, KeyError, TypeError):
                continue
            if (partner := _run_partner(names, timeline, end, characters, partners)) is not None:
                jobs.append((chain, [(partner, partner["clips"], 0.0)], True, names[:1], controller, timeline))
            elif len(found := _spans(names, timeline, end, sequences, keyed, characters)) == 1:
                steps = [(partner, [key], offset) for partner, key, offset in found[0]]
                spans.append((chain, steps, False, names[:1], controller, timeline))
            else:
                continue
            chained.update(zzz_index.identity(row) for row, _state, _edge in chain)
        for suffix, rows in sorted(clips.items())[:MAX_OBJECT_CLIPS]:
            name = rows[0]["name"]
            if any(zzz_index.identity(row) in chained for row in rows):
                continue
            if len(rows) != 1:
                skip(thing, "Several object clips share one name.", name)
            elif (partner := exact.get(suffix) or loose.get(_normalized(suffix))) is not None:
                # A clip one controller plays is placed like that controller's runs.
                owners = playing.get(zzz_index.identity(rows[0]), set())
                jobs.append(([(rows[0], None, None)], [(partner, partner["clips"], 0.0)], exact.get(suffix) is None,
                             [suffix], next(iter(owners)) if len(owners) == 1 else None, None))
            else:
                skip(thing, "No single character clip has this clip's name.", name)
        claims = Counter(id(steps[0][0]) for _chain, steps, *_rest in jobs)
        # Clips that pair on their own keep their tracks over sequence spans.
        return _emit(jobs + spans, claims, document, client.work, thing, bones, rest, anchor, cancel, skip, prefabs,
                     characters, mounting)
    finally:
        clear_samples(client.work)


def _emit(jobs, claims, document, work, thing, bones, rest, anchor, cancel, skip, prefabs, characters, mounting):
    """The tracks of paired jobs (see :func:`_tracks`), first claim of each converted clip winning."""
    found, taken = [], set()
    for chain, steps, loosely, names, controller, timeline in jobs:
        check_cancelled(cancel)
        name = chain[0][0]["name"]
        if loosely and claims[id(steps[0][0])] > 1:
            skip(thing, "Several object clips pair with one character clip.", name)
            continue
        try:
            head = steps[0][0]
            if timeline is None and abs(document(chain[0][0])["stop"] - document(chain[0][0])["start"]
                                        - head["duration"]) > DURATION_TOLERANCE:
                skip(thing, "The paired character clip lasts a different time.", name)
                continue
            # Without an effect prefab the object sits at the character's origin, facing the same way.
            placement, reason = (_placement(prefabs, characters[id(head)][0], *names, controller=controller) if prefabs
                                 else (None, None))
            if reason:
                skip(thing, reason, name)
                continue
            parent, root = _place(placement)
            reasons = set()
            for partner, keys, offset in steps:
                if timeline is None:
                    clip = document(chain[0][0])
                    locals_, reason = _locals(clip, work, bones, np.linspace(clip["start"], clip["stop"], partner["frames"]), root)
                else:
                    locals_, reason = _sequence([document(row) for row, _state, _edge in chain], timeline, work, bones,
                                                offset + np.linspace(0, partner["duration"], partner["frames"]), root)
                if locals_ is None:
                    reasons.add(reason)
                    continue
                for key, (mount, reason) in mounting(placement, partner, keys).items():
                    if mount is None:
                        reasons.add(reason)
                    elif key not in taken:
                        taken.add(key)
                        found.append((_track(locals_, bones, rest, anchor, mount @ parent), partner, key))
            for reason in sorted(reasons):
                skip(thing, reason, name)
        except ExtractionCancelled:
            raise
        except (ValueError, KeyError, TypeError) as error:
            skip(thing, str(error) if isinstance(error, ValueError) else "Unsupported object clip.", name)
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


def extract(client, chosen, rig, index, library, body_clips, cancel, tolerance, captured, payloads, existing, routed, sequences):
    """Add the family's name-paired skill objects to ``library/Props``; returns (entries, skipped).

    ``body_clips`` maps a converted body clip's row identity to its name, duration, frame
    count, converted clip keys and, per key, the point tracks :func:`point_hashes` asked
    for; ``existing`` are props already written there, ``routed`` the character's own
    renderer routes, which teach the texture slots, and ``sequences`` the character's.
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
    cabs = gi_index.by_cab(index)
    follows = [row for row in index if row["type"] == "Follow"]
    runs = _runs(client, index, cabs, token, [thing for thing, _rows in objects], cancel)
    anchor = rest_worlds(rig["bones"], affine=True)[0]
    reference = np.sign(np.prod(next(iter(next(iter(chosen["native"].values()))))["signs"]))
    entries, meshes, routes, worlds_of = [], [], dict(routed), {}

    def mounting(placement, partner, keys):
        return _mounts(placement, keys, library, rig, chosen["avatar"], anchor, partner, cancel, worlds_of)
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
        prefabs = _prefabs(follows, cabs, chosen["family"], thing, rows[0], {controller for *_run, controller in runs[thing]})
        tracks = _tracks(client, thing, clips[thing], runs[thing], suffixes, sequences, bones, rest, anchor, cancel, skip,
                         prefabs, mounting)
        geometry = _geometry(client, index, thing, rows[0], avatar, anchor, reference, tolerance, cancel, skip) if tracks else []
        if not geometry:
            continue
        # Tracks are written only once a mesh shows them.
        written = {}
        for data, partner, key in tracks:
            file, digest = writer.write(data, track=True)
            written[key] = dict(file=file, sha256=digest, frame_count=partner["frames"], duration=partner["duration"],
                                start=0.0, end=partner["duration"])
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

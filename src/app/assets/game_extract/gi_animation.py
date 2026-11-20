"""Pair Genshin Impact controller states and convert them to layered TQS clips.

A playable character's AnimatorController plays two synchronized layers per
state: the Base Layer (motion set 0) carries Humanoid body motion, usually in
clips shared by a body type, and a synced streamer layer (motion set 1) carries
the character's hair/cloth Transform curves. Pairing comes only from the
controller's states, never from clip names.
"""

import hashlib
import json
import math

import numpy as np

from . import gi_index
from .mecanim import LAST, TDOF
from .zzz_animation import dimension, sample_channels

MAX_STATES = 4096
# Hands more than a tenth of the human scale from the clip's own IK goals mean the
# Humanoid conversion is wrong for this clip; retargeting differences stay far below.
MAX_HAND_GOAL_ERROR = 0.1
# Dense Genshin rotation keys are stored about 0.1% off unit length; Unity normalizes
# them when sampling. Anything further from a rotation is still rejected.
QUATERNION_TOLERANCE = 0.01
MAX_SEQUENCES = 256
MAX_SEQUENCE_STEPS = 64
MAX_SEQUENCE_TOTAL_STEPS = 4096
MAX_SEQUENCE_CANDIDATES = 1024
# The game sets this parameter to the playing state's normalized time.
NORMALIZED_TIME = "NormalizedTime"
IF, IF_NOT, GREATER, LESS = 1, 2, 3, 4


def controller_row(rows, family):
    matches = [row for row in rows if row["type"] == "AnimatorController" and row["name"] == family]
    if len(matches) != 1:
        raise ValueError("The Genshin Impact animation controller is missing or ambiguous.")
    return matches[0]


def layers(controller):
    """Return the Humanoid base layer and the optional synced override streamer layer.

    Synced layers reuse the base state machine with their own motion set; the
    streamer is the one that overrides (newer controllers add an additive
    adjustment layer before it, which is not applied).
    """
    entries = controller.get("layers")
    if not isinstance(entries, list) or not entries:
        raise ValueError("The Genshin Impact animation controller has no layers.")
    base = entries[0]
    if base.get("state_machine") != 0 or base.get("motion_set") != 0:
        raise ValueError("The Genshin Impact animation controller has no base body layer.")
    streamers = [layer for layer in synced_layers(controller) if layer.get("blend_mode") == 0]
    if len(streamers) > 1:
        raise ValueError("The Genshin Impact animation controller has ambiguous streamer layers.")
    return base, streamers[0] if streamers else None


def synced_layers(controller):
    """Layers that play the base states with another motion set."""
    return [layer for layer in controller["layers"][1:] if layer.get("state_machine") == 0
            and type(layer.get("motion_set")) is int and layer["motion_set"] > 0]


def state_pairs(controller, rows):
    """Resolve each state's body and streamer clips; return ``(pairs, skipped)``."""
    _base, streamer = layers(controller)
    states = controller.get("states")
    if not isinstance(states, list) or len(states) > MAX_STATES:
        raise ValueError("The Genshin Impact animation controller has too many states.")
    cabs = gi_index.by_cab(rows)
    pairs, skipped, seen = [], [], {}
    for number, state in enumerate(states):
        name = state.get("name") if isinstance(state.get("name"), str) and state.get("name") else f"State {number}"
        motions = state.get("motions") or []
        body = motions[0] if motions else None
        layered = motions[streamer["motion_set"]] if streamer is not None and len(motions) > streamer["motion_set"] else None
        reason = None
        if state.get("mirror") or state.get("mirror_param"):
            reason = "Runtime-mirrored states are not converted."
        elif not body or not body.get("leaves"):
            reason = "The state has no body motion."
        elif layered and layered.get("leaves") and len(layered["leaves"]) != len(body["leaves"]):
            reason = "The state's body and streamer blend trees differ."
        if reason:
            skipped.append(dict(state=name, reason=reason))
            continue
        leaves = body["leaves"]
        others = layered["leaves"] if layered and layered.get("leaves") else [None] * len(leaves)
        for index, (leaf, other) in enumerate(zip(leaves, others)):
            label = name if len(leaves) == 1 else f"{name} {index + 1}"
            body_row = gi_index.resolve_pointer(cabs, leaf["cab"], leaf["path_id"])
            streamer_row = gi_index.resolve_pointer(cabs, other["cab"], other["path_id"]) if other else None
            if body_row is None or (other and streamer_row is None):
                skipped.append(dict(state=label, reason="A clip pointer is missing or ambiguous in the index."))
                continue
            key = gi_index.identity(body_row), gi_index.identity(streamer_row) if streamer_row else None
            if key in seen:
                # States sharing a clip pair keep one clip; a plain state's name is
                # clearer than a blend-tree leaf label, so it takes precedence.
                if len(leaves) == 1 and not seen[key]["plain"]:
                    seen[key].update(state=label, plain=True)
                if len(leaves) == 1:
                    seen[key]["state_indices"].append(number)
                continue
            seen[key] = dict(state=label, body=body_row, streamer=streamer_row, plain=len(leaves) == 1,
                             state_indices=[number] if len(leaves) == 1 else [])
            pairs.append(seen[key])
    return [{key: value for key, value in pair.items() if key != "plain"} for pair in pairs], skipped


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _conditions(transition, strict):
    """Return ``(signature, lower, upper)`` of a transition's conditions, or None.

    The signature holds every condition except ``NormalizedTime`` bounds, which
    form the window. Strict parsing (continuations) rejects any other use of
    ``NormalizedTime``; head parsing ignores it.
    """
    conditions = transition.get("conditions")
    if not isinstance(conditions, list):
        return None
    signature, lower, upper = set(), 0.0, None
    for condition in conditions:
        if not isinstance(condition, dict) or type(condition.get("mode")) is not int or not _finite(condition.get("threshold")):
            return None
        param, mode, threshold = condition.get("param"), condition["mode"], float(condition["threshold"])
        if param is not None and not isinstance(param, str):
            return None
        if param == NORMALIZED_TIME and mode in (GREATER, LESS):
            if mode == GREATER:
                lower = max(lower, threshold)
            else:
                upper = threshold if upper is None else min(upper, threshold)
        elif param == NORMALIZED_TIME:
            if strict:
                return None
        else:
            # If/IfNot ignore their threshold.
            signature.add((param, mode, 0.0 if mode in (IF, IF_NOT) else threshold))
    return frozenset(signature), lower, upper


def _continuation(state, head_signature, clips, start):
    """The chain's next edge from ``state``: ``(leave fraction, transition)`` or None."""
    candidates = []
    transitions = state.get("transitions")
    for order, transition in enumerate(transitions if isinstance(transitions, list) else []):
        if not isinstance(transition, dict):
            continue
        destination, offset, fade = transition.get("dest"), transition.get("offset"), transition.get("duration")
        if (type(destination) is not int or destination not in clips or not _finite(offset) or not 0 <= offset < 1
                or not _finite(fade) or fade < 0):
            continue
        parsed = _conditions(transition, strict=True)
        if parsed is None:
            continue
        signature, lower, upper = parsed
        exit_time = transition.get("exit_time")
        if not transition["conditions"]:
            # An automatic chain leaves at its authored exit time.
            if transition.get("has_exit_time") is not True or not _finite(exit_time) or not 0 < exit_time <= 1:
                continue
            leave = float(exit_time)
        elif (transition.get("has_exit_time") is False and signature and signature <= head_signature
              and all(isinstance(param, str) for param, _mode, _threshold in signature)
              and 0 <= lower < 1 and (upper is None or upper > lower)):
            # Repeating the chain's own input continues it at the window's earliest point.
            leave = lower
        else:
            continue
        if leave * state["_duration"] > start:
            candidates.append((leave, order, transition))
    if not candidates:
        return None
    leave, _order, transition = min(candidates, key=lambda item: item[:2])
    return leave, transition


def _fade(transition, duration):
    return float(transition["duration"]) * (1.0 if transition.get("fixed_duration") is True else duration)


def _heads(controller, states, clips):
    """Chain starts in priority order: Any State, entry selectors, then states nothing leads to."""
    heads, incoming = [], set()
    for transition in controller["any_state"]:
        if isinstance(transition, dict):
            incoming.add(transition.get("dest"))
            parsed = _conditions(transition, strict=False)
            offset = transition.get("offset")
            if transition.get("dest") in clips and parsed and _finite(offset) and 0 <= offset < 1:
                heads.append((transition["dest"], parsed[0], float(offset)))
    for selector in controller["selectors"]:
        transitions = selector.get("transitions") if isinstance(selector, dict) else None
        for transition in transitions if isinstance(transitions, list) else []:
            if not isinstance(transition, dict):
                continue
            incoming.add(transition.get("dest"))
            parsed = _conditions(transition, strict=False)
            if selector.get("entry") is True and transition.get("dest") in clips and parsed:
                heads.append((transition["dest"], parsed[0], 0.0))
    targets = []
    for state in states:
        transitions = state.get("transitions")
        for transition in transitions if isinstance(transitions, list) else []:
            if not isinstance(transition, dict):
                continue
            incoming.add(transition.get("dest"))
            parsed = _conditions(transition, strict=False)
            offset = transition.get("offset")
            if transition.get("dest") in clips and parsed and _finite(offset) and 0 <= offset < 1:
                targets.append((transition["dest"], parsed[0], float(offset)))
    heads += [(index, frozenset(), 0.0) for index in sorted(clips) if index not in incoming]
    # A state reached by another state's edge starts a chain under that edge's input.
    return heads + targets


def _pieces(order, cyclic):
    """Coverage keys of a chain: its contiguous runs (wrapping for a cycle)."""
    count = len(order)
    loop = order + order if cyclic else order
    pieces = {("path", loop[start:start + length]) for start in range(count if cyclic else count)
              for length in range(1, count + 1) if start + length <= len(loop)}
    if cyclic:
        pieces.add(("cycle", min(order[shift:] + order[:shift] for shift in range(count))))
    return pieces


def _group(state, number):
    name = state.get("name") if isinstance(state.get("name"), str) and state.get("name") else f"State {number}"
    path = state.get("path")
    parts = path.split(".") if isinstance(path, str) else []
    return (parts[-2] if len(parts) >= 2 and parts[-2] else name), name


def sequences(controller, clips):
    """Derive the deterministic authored chains of the base state machine.

    ``clips`` maps a converted plain state's index to ``(clip id, duration)``.
    A chain follows automatic exit-time edges and edges repeating the input that
    started it (continuous input); locomotion is driven by game code and has no
    such edges. Returns manifest sequences with per-step times in seconds.
    """
    states = controller.get("states")
    if (not isinstance(states, list) or not isinstance(controller.get("any_state"), list)
            or not isinstance(controller.get("selectors"), list) or not clips):
        return []
    states = [dict(state, _duration=clips[index][1]) if index in clips else state for index, state in enumerate(states)]
    clip_ids = {clip_id for clip_id, _duration in clips.values()}
    candidates, keys = [], set()
    for head, signature, head_offset in _heads(controller, states, clips):
        if len(candidates) == MAX_SEQUENCE_CANDIDATES:
            break
        chain, edges, closing = [head], [], None
        starts = [head_offset * clips[head][1]]
        current = head
        while True:
            found = _continuation(states[current], signature, clips, starts[-1])
            if found is None:
                break
            leave, transition = found
            destination = transition["dest"]
            if destination in chain:
                closing = found if destination == head else None
                break
            if len(chain) == MAX_SEQUENCE_STEPS:
                break
            edges.append(found)
            chain.append(destination)
            starts.append(float(transition["offset"]) * clips[destination][1])
            current = destination
        if len(chain) < 2:
            continue
        restart = float(closing[1]["offset"]) * clips[head][1] if closing else 0.0
        leaves = [leave * clips[index][1] for index, (leave, _transition) in zip(chain, edges)]
        if closing and restart >= leaves[0]:
            closing, restart = None, 0.0
        steps = []
        for number, index in enumerate(chain):
            clip_id, duration = clips[index]
            if number < len(edges) or closing:
                leave_fraction, transition = edges[number] if number < len(edges) else closing
                leave = leave_fraction * duration
                following = chain[number + 1] if number + 1 < len(chain) else head
                following_start = starts[number + 1] if number + 1 < len(chain) else restart
                fade = min(max(_fade(transition, duration), 0.0), duration - leave,
                           clips[following][1] - following_start)
            else:
                leave, fade = duration, 0.0
            steps.append(dict(clip=clip_id, start=round(starts[number], 6), leave=round(leave, 6), fade=round(max(fade, 0.0), 6)))
        if any(step["start"] >= step["leave"] for step in steps):
            continue
        order = tuple(step["clip"] for step in steps)
        key = ("cycle", min(order[shift:] + order[:shift] for shift in range(len(order)))) if closing else ("path", order)
        if key in keys:
            continue
        keys.add(key)
        group, first = _group(states[head], head)
        _group_name, last = _group(states[chain[-1]], chain[-1])
        candidates.append(dict(order=order, key=key, cyclic=bool(closing), restart=round(restart, 6), steps=steps,
                               name=f"{group}: {first} – {last}"[:128]))
    # Longer chains first: a run already contained in another chain is not repeated;
    # among equals the earlier head wins. Output keeps head order.
    covered, accepted = set(), set()
    for number in sorted(range(len(candidates)), key=lambda number: -len(candidates[number]["order"])):
        candidate = candidates[number]
        if candidate["key"] in covered:
            continue
        covered |= _pieces(candidate["order"], candidate["cyclic"])
        accepted.add(number)
    result, total = [], 0
    for number, candidate in enumerate(candidates):
        steps = candidate["steps"]
        if number not in accepted or total + len(steps) > MAX_SEQUENCE_TOTAL_STEPS:
            continue
        timing = [[step["clip"], step["start"], step["leave"], step["fade"]] for step in steps]
        identifier = "seq-" + hashlib.sha256(json.dumps([timing, candidate["cyclic"], candidate["restart"]],
                                                         separators=(",", ":")).encode()).hexdigest()[:24]
        if identifier in clip_ids:
            continue
        total += len(steps)
        result.append(dict(id=identifier, name=candidate["name"], cyclic=candidate["cyclic"],
                           restart=candidate["restart"], steps=steps))
        if len(result) == MAX_SEQUENCES:
            break
    return result


def pair_source(pair):
    """Stable clip identity: the state with its exact body and streamer clips."""
    parts = [pair["state"], gi_index.identity(pair["body"]), gi_index.identity(pair["streamer"]) if pair["streamer"] else ""]
    return hashlib.sha256(json.dumps(parts, separators=(",", ":")).encode()).hexdigest()


def human_mapping(avatar, solver):
    """Map human skeleton nodes to Avatar bones, requiring the same induced hierarchy."""
    index = avatar["human"].get("skeleton_index")
    parents = [bone["parent"] for bone in avatar["bones"]]
    if not isinstance(index, list) or len(index) != solver.count or index[0] != 0:
        raise ValueError("The Humanoid skeleton does not match the Avatar hierarchy.")
    for node in range(1, solver.count):
        target = index[node]
        if type(target) is not int or not 0 < target < len(parents) or parents[target] != index[solver.parents[node]]:
            raise ValueError("The Humanoid skeleton does not match the Avatar hierarchy.")
    if len(set(index)) != len(index):
        raise ValueError("The Humanoid skeleton does not match the Avatar hierarchy.")
    return index


def has_curves(metadata):
    return (metadata["acl_count"] + metadata["streamed_count"] + metadata["dense"].get("count", 0)
            + len(metadata["constant"])) > 0


def _timing(metadata):
    start, stop = metadata["start"], metadata["stop"]
    if (not all(isinstance(v, (int, float)) and np.isfinite(v) for v in (start, stop))
            or not 0 < stop - start <= 3600):
        raise ValueError("Native clip timing is unsupported.")
    return float(start), float(stop)


def _bindings(metadata, sampled):
    cursor = 0
    for binding in metadata["bindings"]:
        width = dimension(binding)
        if cursor + width > sampled.shape[1]:
            raise ValueError("The native curve bindings are incomplete.")
        yield binding, sampled[:, cursor:cursor + width]
        cursor += width
    if cursor != sampled.shape[1]:
        raise ValueError("The native curve bindings are incomplete.")


def _write(output, written, bone, attribute, channel):
    if (bone, attribute) in written:
        raise ValueError("The native clip has duplicate transform tracks.")
    written.add((bone, attribute))
    if attribute == 2:
        lengths = np.linalg.norm(channel, axis=1)
        if np.any(lengths < 1e-6):
            raise ValueError("The native clip contains a zero quaternion.")
        channel = channel / lengths[:, None]
    offset = {1: 0, 2: 3, 3: 7}[attribute]
    output[:, bone, offset:offset + channel.shape[1]] = channel


def _morph_column(binding, channel, facial):
    """A routed facial curve as (target name, normalized weights), else None."""
    target = facial["bindings"].get((binding["path"], binding["attribute"])) if facial else None
    if target is None or binding["type"] != "SkinnedMeshRenderer" or binding["custom"] != 20 or binding["pointer"]:
        return None
    name, full_weight = target
    return name, channel[:, 0] / full_weight


def _morphs(columns, policies, facial):
    """Clamp per the contributing clips' one game policy, then remove the expression the capture already shows."""
    if not columns:
        return None
    if len(policies) != 1 or type(next(iter(policies))) is not bool:
        raise ValueError("The native blend-shape weight policy is unavailable.")
    clamp = next(iter(policies))
    names = list(columns)
    weights = np.column_stack([columns[name] for name in names])
    if not np.isfinite(weights).all() or np.any(np.abs(weights) > 1e6):
        raise ValueError("The native clip has invalid morph weights.")
    if clamp:
        np.clip(weights, 0, 1, out=weights)
    # Captured geometry already shows each channel's default weight; only animated
    # channels change, so they move by their difference from that weight.
    weights = weights - np.asarray([facial["offsets"].get(name, 0.0) for name in names])
    return dict(names=names, weights=weights.astype("<f4"), clamp=clamp)


def convert_state(body, streamer, work, rig, avatar, solver, mask, facial=None):
    """Convert one state's body (and optional streamer) clips to version-2 TQS data.

    ``facial`` (from ``zzz_morphs.prepare``) routes blend-shape curves to proven
    captured targets. Returns ``(binary, frames, duration, omitted, goal_errors, morph)``.
    """
    start, stop = _timing(body)
    duration = stop - start
    frames = max(2, round(duration * 60) + 1)
    bones = len(rig["bones"])
    if frames > 60000 or frames * bones * 40 > 128 * 1024 * 1024:
        raise ValueError("Native animation exceeds playback limits.")
    if body.get("mirror") or (streamer and streamer.get("mirror")):
        raise ValueError("Runtime-mirrored clips are not converted.")
    if not has_curves(body):
        raise ValueError("The state's body clip has no animation curves.")
    if streamer is not None and not has_curves(streamer):
        streamer = None  # An empty streamer clip leaves the body motion unchanged.
    targets = np.linspace(start, stop, frames)
    reserved = frames * bones * 40
    mapping = human_mapping(avatar, solver)
    human = {avatar_node: node for node, avatar_node in enumerate(mapping) if node}
    to_bone = {bone["hash"]: number + 1 for number, bone in enumerate(avatar["bones"])}
    poses = np.array([[*b["translation"], *b["rotation"], *b["scale"]] for b in rig["bones"]], dtype=float)
    output = np.broadcast_to(poses, (frames, *poses.shape)).copy()
    omitted = dict(non_transform=0, translation_dof=0, humanoid_owned=0, unbound=0, masked=0)
    written, channels, morph_columns, policies = set(), {}, {}, set()
    for binding, channel in _bindings(body, sample_channels(body, work, targets, reserved, QUATERNION_TOLERANCE)):
        morph = _morph_column(binding, channel, facial)
        if morph is not None:
            if morph[0] in morph_columns:
                raise ValueError("The native clip has duplicate morph tracks.")
            morph_columns[morph[0]] = morph[1]
            policies.add(body.get("blend_shape_clamp"))
            continue
        if binding["type"] == "Animator" and binding["custom"] == 8 and not binding["pointer"]:
            attribute = binding["attribute"]
            if not 0 <= attribute < LAST:
                omitted["non_transform"] += 1
            elif attribute >= TDOF:
                omitted["translation_dof"] += 1
            elif attribute in channels:
                raise ValueError("The native clip has duplicate Humanoid curves.")
            else:
                channels[attribute] = channel[:, 0]
            continue
        if binding["type"] != "Transform":
            omitted["non_transform"] += 1
            continue
        if binding["pointer"] or binding["attribute"] not in (1, 2, 3):
            raise ValueError("The native clip has unsupported transform bindings.")
        bone = to_bone.get(binding["path"])
        if bone is None:
            omitted["unbound"] += 1
        elif bone - 1 in human:
            omitted["humanoid_owned"] += 1
        else:
            _write(output, written, bone, binding["attribute"], channel)
    local_t, local_q, errors = solver.solve(channels, frames, in_place=True)
    hand_error = max((float(errors[name].max()) for name in ("LeftHand", "RightHand") if errors and name in errors), default=None)
    if hand_error is not None and hand_error > MAX_HAND_GOAL_ERROR:
        raise ValueError("The Humanoid pose disagrees with the clip's IK goals.")
    for avatar_node, node in human.items():
        output[:, avatar_node + 1, 0:3] = local_t[:, node]
        output[:, avatar_node + 1, 3:7] = local_q[:, node]
    if streamer is not None:
        s_start, s_stop = _timing(streamer)
        # Synced layers follow the base state's normalized time.
        layer_targets = s_start + (targets - start) / duration * (s_stop - s_start)
        layer_written, layer_morphs = set(), set()
        for binding, channel in _bindings(streamer, sample_channels(streamer, work, layer_targets, reserved, QUATERNION_TOLERANCE)):
            morph = _morph_column(binding, channel, facial)
            if morph is not None:
                # The synced override layer replaces the body's value for curves it animates.
                if morph[0] in layer_morphs:
                    raise ValueError("The native streamer clip has duplicate morph tracks.")
                layer_morphs.add(morph[0])
                morph_columns[morph[0]] = morph[1]
                policies.add(streamer.get("blend_shape_clamp"))
                continue
            if binding["type"] != "Transform":
                continue  # Streamer root motion and scalar curves are not part of the body pose.
            if binding["pointer"] or binding["attribute"] not in (1, 2, 3):
                raise ValueError("The native streamer clip has unsupported transform bindings.")
            bone = to_bone.get(binding["path"])
            if bone is None:
                omitted["unbound"] += 1
            elif bone - 1 in human:
                omitted["humanoid_owned"] += 1
            elif binding["path"] not in mask:
                omitted["masked"] += 1
            else:
                _write(output, layer_written, bone, binding["attribute"], channel)
    if not np.isfinite(output).all() or np.any(np.abs(output) > 1e6):
        raise ValueError("The native animation transform table is invalid.")
    binary = output.astype("<f4").tobytes()
    morph = _morphs(morph_columns, policies, facial)
    if morph is not None:
        binary += morph["weights"].tobytes()
    if len(binary) > 128 * 1024 * 1024:
        raise ValueError("Native animation exceeds playback limits.")
    goal_errors = None
    if errors:
        goal_errors = dict(hand=hand_error, foot=max((float(errors[name].max()) for name in ("LeftFoot", "RightFoot") if name in errors), default=None))
    return binary, frames, duration, omitted, goal_errors, morph


def streamer_mask(layer):
    """Path hashes the streamer layer may write (positive skeleton-mask weight)."""
    if layer is None:
        return frozenset()
    return frozenset(entry["path_hash"] for entry in layer.get("skeleton_mask") or [] if entry.get("weight", 0) > 0)

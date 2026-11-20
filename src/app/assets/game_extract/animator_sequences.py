"""Derive authored preview sequences from Unity AnimatorController graphs.

Genshin Impact and Zenless Zone Zero chain base-layer states through authored
transitions: automatic exit-time edges, and edges that repeat the input which
entered the chain inside a game-driven time window (a combo under continuous
input). Locomotion is driven by game code and has no such edges. Selector
(entry/exit) nodes are resolved the way Unity evaluates them: the first
transition whose whole input holds.
"""

import hashlib
import json
import math
from collections import namedtuple

MAX_SEQUENCES = 256
MAX_SEQUENCE_STEPS = 64
MAX_SEQUENCE_TOTAL_STEPS = 4096
MAX_SEQUENCE_CANDIDATES = 1024
MAX_HEADS = 16384
MAX_ROUTE = 64
# A destination of 30000 + i is selector i of the same state machine.
SELECTOR_BASE = 30000
IF, IF_NOT, GREATER, LESS, GREATER_EQUAL, LESS_EQUAL = 1, 2, 3, 4, 9, 10

# ``rate`` is None for a normalized-time parameter, else the parameter's frames per second.
Window = namedtuple("Window", "param rate")
# Genshin sets this parameter to the playing state's normalized time.
NORMALIZED_TIME = Window("NormalizedTime", None)
# ZZZ counts the playing state's frames at 60 Hz: authored exit times equal
# FrameCount divided by the clip's 60 Hz frame count.
FRAME_COUNT = Window("FrameCount", 60.0)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def sequence_id(steps, cyclic, restart):
    """Content identity of a sequence; never collides with ``_ID`` clip ids, which never start with ``seq-``."""
    timing = [[step["clip"], step["start"], step["leave"], step["fade"]] for step in steps]
    return "seq-" + hashlib.sha256(json.dumps([timing, cyclic, restart], separators=(",", ":")).encode()).hexdigest()[:24]


def _conditions(transition, window, strict):
    """Return ``(signature, lower, uppers)`` of a transition's conditions, or None.

    The signature holds every condition except the window parameter's bounds,
    which give the window's lower bound and upper limits in window units.
    Strict parsing (continuations, selectors) rejects any other use of the
    window parameter; head parsing ignores it.
    """
    conditions = transition.get("conditions")
    if not isinstance(conditions, list):
        return None
    signature, lower, uppers = set(), 0.0, []
    for condition in conditions:
        if not isinstance(condition, dict) or type(condition.get("mode")) is not int or not finite(condition.get("threshold")):
            return None
        param, mode, threshold = condition.get("param"), condition["mode"], float(condition["threshold"])
        if param is not None and not isinstance(param, str):
            return None
        if param == window.param:
            if mode in (GREATER, GREATER_EQUAL):
                if window.rate is not None:
                    # Frame counts are whole: "greater than n" first holds at n + 1.
                    threshold = math.floor(threshold) + 1 if mode == GREATER else math.ceil(threshold)
                lower = max(lower, threshold)
            elif mode in (LESS, LESS_EQUAL):
                uppers.append((mode, threshold))
            elif strict:
                return None
        else:
            # If/IfNot ignore their threshold.
            signature.add((param, mode, 0.0 if mode in (IF, IF_NOT) else threshold))
    return frozenset(signature), lower, tuple(uppers)


def _named(signature):
    return all(isinstance(param, str) for param, _mode, _threshold in signature)


def _within(value, uppers):
    return all(value < limit if mode == LESS else value <= limit for mode, limit in uppers)


def _route(selectors, destination, signature, window, count):
    """Follow selector nodes to a state index under ``signature``, or None."""
    seen = set()
    while type(destination) is int and destination >= SELECTOR_BASE:
        index = destination - SELECTOR_BASE
        if index in seen or index >= len(selectors) or len(seen) == MAX_ROUTE:
            return None
        seen.add(index)
        selector = selectors[index]
        transitions = selector.get("transitions") if isinstance(selector, dict) else None
        for transition in transitions if isinstance(transitions, list) else []:
            parsed = _conditions(transition, window, strict=True) if isinstance(transition, dict) else None
            if parsed is None or not _named(parsed[0]):
                # An input that cannot be evaluated is never guessed past.
                return None
            if parsed[0] <= signature and not parsed[1] and not parsed[2]:
                destination = transition.get("dest")
                break
        else:
            return None
    return destination if type(destination) is int and 0 <= destination < count else None


def _fade(transition, duration):
    return float(transition["duration"]) * (1.0 if transition.get("fixed_duration") is True else duration)


def _continuation(controller, current, signature, clips, start, earliest, window):
    """The chain's next edge from ``current``: ``(leave seconds, transition, destination)`` or None.

    ``start`` is where the step's clip entered; ``earliest`` is when its incoming
    transition completes, before which Unity (without interruption) evaluates none
    of the state's own transitions.
    """
    states, duration = controller["states"], clips[current][1]
    transitions = states[current].get("transitions")
    candidates = []
    for order, transition in enumerate(transitions if isinstance(transitions, list) else []):
        if not isinstance(transition, dict):
            continue
        offset, fade, exit_time = transition.get("offset"), transition.get("duration"), transition.get("exit_time")
        if not finite(offset) or not 0 <= offset < 1 or not finite(fade) or fade < 0:
            continue
        has_exit = transition.get("has_exit_time") is True
        if has_exit and (not finite(exit_time) or not 0 < exit_time <= 1):
            continue
        parsed = _conditions(transition, window, strict=True)
        if parsed is None:
            continue
        edge_signature, lower, uppers = parsed
        if transition["conditions"]:
            # An edge with input continues the chain only on a repeat of the chain's own input,
            # and only when its own exit time or window times it (an untimed input edge fires
            # the moment input arrives, which is not an authored step).
            if (not edge_signature or not edge_signature <= signature or not _named(edge_signature)
                    or (not has_exit and lower <= 0)):
                continue
        elif not has_exit:
            continue
        # The edge passes once its exit time (if any) and its window have both opened;
        # continuous input takes the earliest such point.
        opens = lower * duration if window.rate is None else start + lower / window.rate
        leave = max(float(exit_time) * duration if has_exit else 0.0, opens, earliest)
        progress = leave / duration if window.rate is None else (leave - start) * window.rate
        if leave <= start or leave > duration or (not has_exit and leave >= duration) or not _within(progress, uppers):
            continue
        destination = _route(controller["selectors"], transition.get("dest"), signature, window, len(states))
        if destination in clips:
            candidates.append((leave, order, transition, destination))
    if not candidates:
        return None
    leave, _order, transition, destination = min(candidates, key=lambda item: item[:2])
    return leave, transition, destination


def _heads(controller, clips, window):
    """Chain starts in priority order: Any State, entry selectors, states nothing leads to, other edge targets."""
    states, selectors = controller["states"], controller["selectors"]
    heads, targets, incoming = [], [], set()

    def head(transition, offset):
        destination = transition.get("dest")
        if type(destination) is int:
            incoming.add(destination)
        parsed = _conditions(transition, window, strict=False)
        state = _route(selectors, destination, parsed[0], window, len(states)) if parsed else None
        if state in clips and finite(offset) and 0 <= offset < 1:
            return state, parsed[0], float(offset)
        return None

    for transition in controller["any_state"]:
        if isinstance(transition, dict) and (found := head(transition, transition.get("offset"))):
            heads.append(found)
    for selector in selectors:
        transitions = selector.get("transitions") if isinstance(selector, dict) else None
        for transition in transitions if isinstance(transitions, list) else []:
            if isinstance(transition, dict) and (found := head(transition, 0.0)) and selector.get("entry") is True:
                heads.append(found)
    for state in states:
        transitions = state.get("transitions") if isinstance(state, dict) else None
        for transition in transitions if isinstance(transitions, list) else []:
            # A state reached by another state's edge starts a chain under that edge's input.
            if isinstance(transition, dict) and (found := head(transition, transition.get("offset"))):
                targets.append(found)
    heads += [(index, frozenset(), 0.0) for index in sorted(clips) if index not in incoming]
    unique, seen = [], set()
    for item in heads + targets:
        if item not in seen and len(unique) < MAX_HEADS:
            seen.add(item)
            unique.append(item)
    return unique


def _pieces(order, cyclic):
    """Coverage keys of a chain: its contiguous runs (wrapping for a cycle)."""
    count = len(order)
    loop = order + order if cyclic else order
    pieces = {("path", loop[start:start + length]) for start in range(count)
              for length in range(1, count + 1) if start + length <= len(loop)}
    if cyclic:
        pieces.add(("cycle", min(order[shift:] + order[:shift] for shift in range(count))))
    return pieces


def _group(state, number):
    name = state.get("name") if isinstance(state.get("name"), str) and state.get("name") else f"State {number}"
    path = state.get("path")
    parts = path.split(".") if isinstance(path, str) else []
    return (parts[-2] if len(parts) >= 2 and parts[-2] else name), name


def _playable(states, index):
    """Steps play their clip at its own speed, so a state with another playback speed never joins a chain."""
    if type(index) is not int or not 0 <= index < len(states) or not isinstance(states[index], dict):
        return False
    speed = states[index].get("speed", 1.0)
    return finite(speed) and abs(speed - 1.0) <= 1e-6


def derive(controller, clips, window=NORMALIZED_TIME, prefix=""):
    """Derive the deterministic authored chains of a controller's base state machine.

    ``clips`` maps a converted plain state's index to ``(clip id, duration)``.
    Returns manifest sequences with per-step times in seconds of each step's clip;
    ``prefix`` starts every name (to tell several controllers apart).
    """
    states = controller.get("states") if isinstance(controller, dict) else None
    if (not isinstance(states, list) or not isinstance(controller.get("any_state"), list)
            or not isinstance(controller.get("selectors"), list) or not clips):
        return []
    clips = {index: value for index, value in clips.items() if _playable(states, index)}
    clip_ids = {clip_id for clip_id, _duration in clips.values()}
    candidates, keys = [], set()
    for head, signature, head_offset in _heads(controller, clips, window):
        if len(candidates) == MAX_SEQUENCE_CANDIDATES:
            break
        chain, edges, closing = [head], [], None
        starts = [head_offset * clips[head][1]]
        earliest = starts[0]
        while True:
            found = _continuation(controller, chain[-1], signature, clips, starts[-1], earliest, window)
            if found is None:
                break
            leave, transition, destination = found
            if destination in chain:
                closing = (leave, transition) if destination == head else None
                break
            if len(chain) == MAX_SEQUENCE_STEPS:
                break
            edges.append((leave, transition))
            # The authored transition length (before clamping to the clips) holds the new state's edges.
            fading = _fade(transition, clips[chain[-1]][1])
            chain.append(destination)
            starts.append(float(transition["offset"]) * clips[destination][1])
            earliest = starts[-1] + fading
        if len(chain) < 2:
            continue
        restart = float(closing[1]["offset"]) * clips[head][1] if closing else 0.0
        # Repeats enter the head through the closing transition, whose completion must
        # still precede the head's leave.
        if closing and restart + _fade(closing[1], clips[chain[-1]][1]) > edges[0][0]:
            closing, restart = None, 0.0
        # The last step leaves on the closing edge, or plays to its end.
        exits = edges + [closing if closing else (clips[chain[-1]][1], None)]
        steps = []
        for number, index in enumerate(chain):
            clip_id, duration = clips[index]
            leave, transition = exits[number]
            fade = 0.0
            if transition is not None:
                following = number + 1 if number + 1 < len(chain) else 0
                following_start = starts[following] if following else restart
                # A crossfade stays within the outgoing clip and ends before the incoming step leaves.
                fade = min(_fade(transition, duration), duration - leave, exits[following][0] - following_start)
            steps.append(dict(clip=clip_id, start=round(starts[number], 6), leave=round(leave, 6),
                              fade=round(max(fade, 0.0), 6)))
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
                               name=f"{prefix}{group}: {first} – {last}"[:128]))
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
        identifier = sequence_id(steps, candidate["cyclic"], candidate["restart"])
        if identifier in clip_ids:
            continue
        total += len(steps)
        result.append(dict(id=identifier, name=candidate["name"], cyclic=candidate["cyclic"],
                           restart=candidate["restart"], steps=steps))
        if len(result) == MAX_SEQUENCES:
            break
    return result

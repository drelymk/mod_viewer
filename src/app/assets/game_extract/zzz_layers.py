"""Pair ZZZ controller states with the masked skin-layer clips played over them.

A character AnimatorController plays two layers per state: the Base Layer (motion set 0)
carries the body clips, which an outfit family usually shares with its base outfit, and a
synced override "skin" layer (state machine 0, motion set above 0) carries only the outfit's
own bones, limited by a skeleton mask. An outfit family's own clips are therefore often only
the skin layer. Pairing comes from the controller's states and clip pointers, never from
clip names.
"""

import hashlib
import json

from . import zzz_index

MAX_STATES = 4096


def clip_lookup(rows):
    """``(CAB casefold, PathID)`` -> the AnimationClip rows (all block copies, in index order) at that pointer."""
    lookup = {}
    for row in rows:
        if row["type"] == "AnimationClip":
            lookup.setdefault((row["cab"].casefold(), row["path_id"]), []).append(row)
    return lookup


def skin_layer(controller):
    """The one full-weight override layer over the base state machine, or None without one.

    Controllers without layer data (and additive or separate-state-machine layers) are body-only;
    layers that cannot be applied faithfully raise ``ValueError``.
    """
    layers = controller.get("layers")
    if not isinstance(layers, list) or not layers:
        return None
    base = layers[0]
    if not isinstance(base, dict) or base.get("state_machine") != 0 or base.get("motion_set") != 0:
        raise ValueError("The animation controller has no base body layer.")
    synced = [layer for layer in layers[1:] if isinstance(layer, dict) and layer.get("state_machine") == 0
              and type(layer.get("motion_set")) is int and layer["motion_set"] > 0 and layer.get("blend_mode") == 0]
    if len(synced) > 1:
        raise ValueError("The animation controller has ambiguous skin layers.")
    if synced and synced[0].get("weight") != 1:
        raise ValueError("The animation controller's skin layer is only partly weighted.")
    return synced[0] if synced else None


def mask_hashes(layer):
    """Path hashes the layer may write (positive skeleton-mask weight)."""
    if layer is None:
        return frozenset()
    return frozenset(entry["path_hash"] for entry in layer.get("skeleton_mask") or []
                     if isinstance(entry, dict) and type(entry.get("path_hash")) is int and entry.get("weight", 0) > 0)


def _leaves(motion):
    leaves = motion.get("leaves") if isinstance(motion, dict) else None
    if not isinstance(leaves, list) or not all(
            isinstance(leaf, dict) and isinstance(leaf.get("cab"), str) and type(leaf.get("path_id")) is int
            for leaf in leaves):
        return None
    return leaves


def state_pairs(controller, lookup, layer):
    """Resolve each state's body and skin clips; return ``(pairs, skipped)``.

    A pair is ``dict(body, skin, state_indices)``: ``body``/``skin`` are the block copies
    of the clip (``skin`` None without a skin leaf) and ``state_indices`` the plain single-clip
    states playing exactly that pair. Pairs repeating across states are kept once.
    """
    states = controller.get("states")
    if not isinstance(states, list) or len(states) > MAX_STATES:
        raise ValueError("The animation controller has too many states.")
    pairs, skipped, seen = [], [], {}
    for number, state in enumerate(states):
        if not isinstance(state, dict):
            continue
        name = state.get("name") if isinstance(state.get("name"), str) and state["name"] else f"State {number}"
        motions = state.get("motions") if isinstance(state.get("motions"), list) else []
        body = _leaves(motions[0]) if motions else None
        skin = None
        if layer is not None and len(motions) > layer["motion_set"]:
            skin = _leaves(motions[layer["motion_set"]])
        if not body:
            continue
        reason = None
        if state.get("mirror") or state.get("mirror_param"):
            reason = "Runtime-mirrored states are not converted."
        elif skin and len(skin) != len(body):
            reason = "The state's body and skin blend trees differ."
        if reason:
            skipped.append(dict(name=name, reason=reason))
            continue
        for index, leaf in enumerate(body):
            label = name if len(body) == 1 else f"{name} {index + 1}"
            other = skin[index] if skin else None
            body_rows = lookup.get((leaf["cab"].casefold(), leaf["path_id"]), [])
            skin_rows = lookup.get((other["cab"].casefold(), other["path_id"]), []) if other else None
            if not body_rows or (other and not skin_rows):
                skipped.append(dict(name=label, reason="A clip pointer is missing from the index."))
                continue
            key = tuple(zzz_index.identity(row) for row in body_rows), tuple(zzz_index.identity(row) for row in skin_rows or ())
            pair = seen.get(key)
            if pair is None:
                pair = seen[key] = dict(body=body_rows, skin=skin_rows, state_indices=[])
                pairs.append(pair)
            if len(body) == 1:
                pair["state_indices"].append(number)
    return pairs, skipped


def pair_source(job):
    """Stable clip identity of a skin-layered job (the exact body and skin clip rows and mask)."""
    parts = [zzz_index.identity(job["body"]), zzz_index.identity(job["skin"]), sorted(job["mask"])]
    return hashlib.sha256(json.dumps(parts, separators=(",", ":")).encode()).hexdigest()

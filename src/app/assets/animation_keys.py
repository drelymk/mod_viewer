"""Bones a game drives at runtime, from which bones its clips key."""

RUNTIME_KEY_SHARE = 0.05


def bone_list(value, bone_count):
    """Return a sorted list of unique int bone indices in [0, bone_count), or None if invalid."""
    if (not isinstance(value, list) or len(value) > bone_count or len(set(value)) != len(value)
            or any(type(item) is not int or not 0 <= item < bone_count for item in value)):
        return None
    return sorted(value)


def runtime_bones(rig, clips):
    """Bones the clips leave to the game runtime (physics chains).

    ``clips`` are clip metadata dicts; those without a valid ``keyed_bones``
    list record no evidence. A bone is runtime-driven when its nearest
    non-runtime ancestor (the anchor) is keyed by at least one clip and the bone
    itself is keyed by at most RUNTIME_KEY_SHARE of the clips that key the
    anchor. Only chains that deform skin are kept: a runtime bone survives if
    it, or a runtime descendant, appears in a rig component or skin binding.
    """
    bones = rig["bones"]
    count = len(bones)
    counts = [0] * count
    for clip in clips:
        for bone in bone_list(clip.get("keyed_bones"), count) or ():
            counts[bone] += 1
    runtime = [False] * count
    anchor = [-1] * count
    for index, bone in enumerate(bones):
        parent = bone["parent"]
        if parent < 0:
            continue
        anchor[index] = anchor[parent] if runtime[parent] else parent
        runtime[index] = counts[anchor[index]] > 0 and counts[index] <= RUNTIME_KEY_SHARE * counts[anchor[index]]
    skinned = set()
    for palette in [*rig.get("components", []), *rig.get("skin_bindings", [])]:
        mapping = palette.get("bone_map")
        skinned.update(value for value in (mapping.values() if isinstance(mapping, dict) else mapping or ())
                       if type(value) is int)
    keep = [False] * count
    for index in reversed(range(count)):
        keep[index] = keep[index] or (runtime[index] and index in skinned)
        parent = bones[index]["parent"]
        if keep[index] and parent >= 0 and runtime[parent]:
            keep[parent] = True
    return [index for index in range(count) if keep[index]]

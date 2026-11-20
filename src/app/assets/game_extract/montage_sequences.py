"""Authored Unreal AnimMontage timelines as preview sequences (Wuthering Waves).

A montage plays its slot track's segments back to back (hard cuts, no
crossfade) and moves between composite sections only through their explicit
``NextSectionName`` links; a section without one ends the montage. Combo order
between montages lives in gameplay code, so only a single montage's own
timeline becomes a sequence.
"""

from collections import Counter

from .animator_sequences import MAX_SEQUENCE_STEPS, MAX_SEQUENCE_TOTAL_STEPS, MAX_SEQUENCES, finite, sequence_id

MAX_MONTAGES = 1024
# Serialized montage times are float32 seconds.
TOLERANCE = 1e-3


def reference_package(reference):
    """Package path of a serialized ``ObjectPath`` (``<package>.<export index>``), or None."""
    path = reference.get("ObjectPath") if isinstance(reference, dict) else None
    return path.rsplit(".", 1)[0] if isinstance(path, str) and "." in path else None


def _number(value, default=None):
    value = default if value is None else value
    if not finite(value):
        raise ValueError("Montage timing is invalid.")
    return float(value)


def _pieces(properties, clips):
    """Segments expanded by their loop count: ``(clip, start, leave, montage begin)``."""
    tracks = properties.get("SlotAnimTracks")
    if not isinstance(tracks, list) or len(tracks) != 1 or not isinstance(tracks[0], dict):
        raise ValueError("Only single-slot montages are supported.")
    track = tracks[0].get("AnimTrack")
    segments = track.get("AnimSegments") if isinstance(track, dict) else None
    if not isinstance(segments, list) or not 1 <= len(segments) <= MAX_SEQUENCE_STEPS:
        raise ValueError("The montage has no bounded segment list.")
    pieces, position = [], None
    for segment in segments:
        if not isinstance(segment, dict):
            raise ValueError("Montage timing is invalid.")
        source = reference_package(segment.get("AnimReference"))
        if source not in clips:
            raise ValueError("A montage segment's clip was not converted.")
        clip_id, duration = clips[source]
        begin, first = _number(segment.get("StartPos"), 0.0), _number(segment.get("AnimStartTime"), 0.0)
        last, rate = _number(segment.get("AnimEndTime")), _number(segment.get("AnimPlayRate"), 1.0)
        loops = segment.get("LoopingCount", 1)
        if abs(rate - 1.0) > 1e-6:
            raise ValueError("Montage play rates other than 1 are not supported.")
        if type(loops) is not int or not 1 <= loops <= MAX_SEQUENCE_STEPS or len(pieces) + loops > MAX_SEQUENCE_STEPS:
            raise ValueError("The montage loops too often.")
        if not 0 <= first < last <= duration + TOLERANCE:
            raise ValueError("A montage segment exceeds its clip.")
        last = min(last, duration)
        if position is not None and abs(begin - position) > TOLERANCE:
            raise ValueError("Montage segments are not contiguous.")
        for _loop in range(loops):
            pieces.append((clip_id, first, last, begin))
            begin += last - first
        position = begin
    return pieces, position


def _sections(properties, start, end):
    """Composite sections in play order as ``[(begin, end)]`` and whether the last links back to the first."""
    raw = properties.get("CompositeSections")
    if raw is None or raw == []:
        return [(start, end)], False
    if not isinstance(raw, list) or len(raw) > MAX_SEQUENCE_STEPS:
        raise ValueError("The montage has no bounded section list.")
    sections = []
    for section in raw:
        name = section.get("SectionName") if isinstance(section, dict) else None
        following = section.get("NextSectionName") if isinstance(section, dict) else None
        if not isinstance(name, str) or not name or (following is not None and not isinstance(following, str)):
            raise ValueError("Montage sections are invalid.")
        sections.append((_number(section.get("SegmentBeginTime"), 0.0), name, following))
    sections.sort(key=lambda item: item[0])
    names = [name for _begin, name, _following in sections]
    if len(set(names)) != len(names):
        raise ValueError("Montage section names are not unique.")
    ranges = {name: (begin, sections[number + 1][0] if number + 1 < len(sections) else end)
              for number, (begin, name, _following) in enumerate(sections)}
    links = {name: following for _begin, name, following in sections}
    order, current, cyclic = [], names[0], False
    while True:
        order.append(current)
        following = links[current]
        if following in (None, "", "None") or following not in ranges:
            break
        if following in order:
            # Only a loop back to the first section repeats the whole timeline.
            cyclic = following == names[0]
            break
        current = following
    return [ranges[name] for name in order], cyclic


def timeline(properties, clips):
    """Steps of one montage's authored timeline: ``(steps, cyclic, restart)``, or None for a single clip.

    ``clips`` maps an AnimSequence package path to ``(clip id, duration)``.
    Raises ValueError with the reason a montage cannot be previewed.
    """
    if not isinstance(properties, dict):
        raise ValueError("The montage has no properties.")
    pieces, end = _pieces(properties, clips)
    ranges, cyclic = _sections(properties, pieces[0][3], end)
    steps = []
    for low, high in ranges:
        for clip_id, first, last, begin in pieces:
            stop = begin + (last - first)
            if min(high, stop) - max(low, begin) > TOLERANCE:
                # Whole segment ends keep their authored clip times; only a section boundary cuts inside one.
                start = first + (low - begin) if low > begin + TOLERANCE else first
                leave = first + (high - begin) if high < stop - TOLERANCE else last
                steps.append(dict(clip=clip_id, start=round(start, 6), leave=round(leave, 6), fade=0.0))
    if len(steps) > MAX_SEQUENCE_STEPS:
        raise ValueError("The montage has too many steps.")
    # A clip looped on its own is not a sequence.
    if len({(step["clip"], round(step["start"], 3), round(step["leave"], 3)) for step in steps}) < 2:
        return None
    if any(step["start"] >= step["leave"] for step in steps):
        raise ValueError("Montage timing is invalid.")
    return steps, cyclic, steps[0]["start"] if cyclic else 0.0


def sequences(montages, clips, root):
    """Sequences of ``montages`` (``[(package, properties)]``) under character folder ``root``.

    Returns ``(sequences, skipped)``; each skipped montage records its reason.
    Names are montage names, with the folder added when several share one.
    """
    montages = sorted(montages, key=lambda item: item[0])[:MAX_MONTAGES]
    names = Counter(package.rsplit("/", 1)[-1] for package, _properties in montages)
    clip_ids = {clip_id for clip_id, _duration in clips.values()}
    result, skipped, seen, total = [], [], set(), 0
    for package, properties in montages:
        try:
            found = timeline(properties, clips)
        except ValueError as error:
            skipped.append(dict(source=package, reason=str(error)))
            continue
        if found is None:
            continue
        steps, cyclic, restart = found
        identifier = sequence_id(steps, cyclic, restart)
        if identifier in seen or identifier in clip_ids or total + len(steps) > MAX_SEQUENCE_TOTAL_STEPS:
            continue
        name = package.rsplit("/", 1)[-1]
        if names[name] > 1:
            name += " (" + package.removeprefix(root + "/").rsplit("/", 1)[0] + ")"
        seen.add(identifier)
        total += len(steps)
        result.append(dict(id=identifier, name=name[:128], cyclic=cyclic, restart=restart, steps=steps))
        if len(result) == MAX_SEQUENCES:
            break
    return result, skipped

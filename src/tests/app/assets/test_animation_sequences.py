"""Authored sequence derivation shared by the Unity (ZZZ) and Unreal (WuWa) extractors."""

import pytest

from app.assets.game_extract import animator_sequences, montage_sequences


def edge(dest, *conditions, exit_time=1.0, has_exit=False, fade=0.0, fixed=True, offset=0.0):
    return dict(dest=dest, duration=fade, offset=offset, exit_time=exit_time, has_exit_time=has_exit,
                fixed_duration=fixed, conditions=[dict(param=param, mode=mode, threshold=value)
                                                  for param, mode, value in conditions])


def graph(states, selectors=(), any_state=()):
    return dict(states=[dict(name=name, path=f"Base Layer.Group01.{name}", transitions=edges, **extra)
                        for name, edges, extra in states],
                selectors=[dict(entry=entry, transitions=transitions) for entry, transitions in selectors],
                any_state=list(any_state))


PRESS_A, PRESS_B = ("Input01", 1, 0), ("Input02", 1, 0)
SELECTOR = animator_sequences.SELECTOR_BASE


def frames(mode, value):
    return ("FrameCount", mode, value)


def combo(speed=1.0):
    """Idle routes Input01 through an entry selector to Attack01; frame windows chain the combo."""
    return graph([
        ("Idle", [edge(SELECTOR + 1, PRESS_A, fade=0.1)], {}),
        ("Attack01", [edge(3, PRESS_B, frames(9, 6)),
                      edge(2, PRESS_A, frames(9, 18), frames(10, 40), fade=0.5)], {}),
        ("Attack02", [edge(3, PRESS_A, frames(3, 11), fade=0.1)], dict(speed=speed)),
        # Leaving through the exit selector returns to Idle when no input remains.
        ("Attack02_End", [edge(SELECTOR + 2, exit_time=0.8, has_exit=True, fade=0.25, fixed=False)], {}),
    ], selectors=[
        (True, [edge(0)]),
        (True, [edge(SELECTOR + 2, PRESS_B), edge(1, PRESS_A), edge(2)]),
        (False, [edge(0)]),
    ])


CLIPS = {0: ("clip-idle", 2.0), 1: ("clip-attack01", 1.0), 2: ("clip-attack02", 1.25), 3: ("clip-end", 0.5)}


@pytest.mark.parametrize("controller, clips, expected", [
    # FrameCount windows count 60 Hz frames from the step start (> n first holds at n + 1),
    # but a state's edges wait for its incoming transition (Attack02's window opens at
    # 0.2 s, its 0.5 s fade ends later); selectors route by the first passing input.
    (combo(), CLIPS,
     [("Group01: Attack01 – Idle", False, 0, [("clip-attack01", 0, 0.3, 0.5), ("clip-attack02", 0, 0.5, 0.1),
                                             ("clip-end", 0, 0.4, 0.1), ("clip-idle", 0, 2.0, 0)])]),
    # A state with another playback speed never joins a chain.
    (combo(speed=1.5), CLIPS,
     [("Group01: Attack02_End – Idle", False, 0, [("clip-end", 0, 0.4, 0.1), ("clip-idle", 0, 2.0, 0)])]),
    # An exit-time edge with the chain's input leaves at the later of its exit time and
    # window; a window closed by its upper limit never passes; a crossfade ends before
    # the incoming step leaves.
    (graph([("StateA", [edge(1, PRESS_A, frames(9, 6), exit_time=0.5, has_exit=True, fade=0.1)], {}),
            ("StateB", [edge(0, PRESS_A, frames(9, 30), frames(4, 20))], {})],
           any_state=[edge(0, PRESS_A)]),
     {0: ("clip-a", 1.0), 1: ("clip-b", 0.05)},
     [("Group01: StateA – StateB", False, 0, [("clip-a", 0, 0.5, 0.05), ("clip-b", 0, 0.05, 0)])]),
    # Malformed destinations are ignored rather than failing the extraction.
    (graph([("StateA", [edge([1], has_exit=True), edge(1, has_exit=True)], {}), ("StateB", [], {})],
           any_state=[edge({"index": 0}, PRESS_A)]),
     {0: ("clip-a", 1.0), 1: ("clip-b", 1.0)},
     [("Group01: StateA – StateB", False, 0, [("clip-a", 0, 1.0, 0), ("clip-b", 0, 1.0, 0)])]),
])
def test_frame_window_sequences_route_selectors_and_clamp_fades(controller, clips, expected):
    result = animator_sequences.derive(controller, clips, animator_sequences.FRAME_COUNT)
    assert [(item["name"], item["cyclic"], item["restart"],
             [(step["clip"], step["start"], step["leave"], step["fade"]) for step in item["steps"]])
            for item in result] == expected
    named = animator_sequences.derive(controller, clips, animator_sequences.FRAME_COUNT, prefix="UI · ")
    assert [item["name"] for item in named] == ["UI · " + item[0] for item in expected]


ROOT = "Client/Content/Role/Character01"
STAND, ACTION, LOOP = (f"{ROOT}/CommonAnim/{name}" for name in ("Stand01", "Action01", "Loop01"))
MONTAGE_CLIPS = {STAND: ("clip-stand", 2.0), ACTION: ("clip-action", 5.0), LOOP: ("clip-loop", 1.0)}


def segment(package, begin, first, last, rate=1.0, loops=1):
    return dict(AnimReference=dict(ObjectPath=package + ".0"), StartPos=begin, AnimStartTime=first,
                AnimEndTime=last, AnimPlayRate=rate, LoopingCount=loops)


def montage(segments, sections=()):
    return dict(SlotAnimTracks=[dict(SlotName="DefaultSlot", AnimTrack=dict(AnimSegments=list(segments)))],
                CompositeSections=[dict(SectionName=name, SegmentBeginTime=begin, NextSectionName=following)
                                   for name, begin, following in sections])


@pytest.mark.parametrize("properties, expected, reason", [
    # Segments play back to back with hard cuts; one unlinked default section ends the montage.
    (montage([segment(STAND, 0, 0, 0.3), segment(ACTION, 0.3, 0, 5.0)], [("Default", 0, "None")]),
     (False, 0, [("clip-stand", 0, 0.3), ("clip-action", 0, 5.0)]), None),
    # Looping segments expand, and a link back to the first section repeats the timeline.
    (montage([segment(STAND, 0, 0.2, 0.7), segment(LOOP, 0.5, 0, 1, loops=2)],
             [("Start", 0, "Loop"), ("Loop", 0.5, "Start")]),
     (True, 0.2, [("clip-stand", 0.2, 0.7), ("clip-loop", 0, 1), ("clip-loop", 0, 1)]), None),
    # A later section looping on itself plays once; an unlinked first section ends the montage.
    (montage([segment(STAND, 0, 0, 0.5), segment(LOOP, 0.5, 0, 1)], [("Start", 0, "Loop"), ("Loop", 0.5, "Loop")]),
     (False, 0, [("clip-stand", 0, 0.5), ("clip-loop", 0, 1)]), None),
    (montage([segment(STAND, 0, 0, 0.5), segment(LOOP, 0.5, 0, 1)], [("Start", 0, None), ("End", 0.5, None)]),
     None, None),
    # Sections split a segment at their boundary without changing its clip time.
    (montage([segment(ACTION, 0, 1, 3)], [("Start", 0, "Rest"), ("Rest", 0.5, None)]),
     (False, 0, [("clip-action", 1, 1.5), ("clip-action", 1.5, 3)]), None),
    (montage([segment(STAND, 0, 0, 2)]), None, None),
    (montage([segment(STAND, 0, 0, 0.3), segment(ACTION, 0.3, 0, 1, rate=0.5)]), None,
     "Montage play rates other than 1 are not supported."),
    (montage([segment(STAND, 0, 0, 0.3), segment(f"{ROOT}/CommonAnim/Other01", 0.3, 0, 1)]), None,
     "A montage segment's clip was not converted."),
    (montage([segment(STAND, 0, 0, 0.3), segment(ACTION, 0.5, 0, 1)]), None, "Montage segments are not contiguous."),
    (montage([segment(STAND, 0, 0, 2.5), segment(ACTION, 2.5, 0, 1)]), None, "A montage segment exceeds its clip."),
])
def test_montage_timelines_follow_segments_and_section_links(properties, expected, reason):
    result, skipped = montage_sequences.sequences([(f"{ROOT}/CommonAnim/AM_Test01", properties)], MONTAGE_CLIPS, ROOT)
    assert [item["reason"] for item in skipped] == ([reason] if reason else [])
    assert [(item["cyclic"], item["restart"], [(step["clip"], step["start"], step["leave"]) for step in item["steps"]])
            for item in result] == ([expected] if expected else [])
    assert all(item["name"] == "AM_Test01" and item["id"].startswith("seq-")
               and all(step["fade"] == 0 for step in item["steps"]) for item in result)


def test_montage_sequences_name_shared_montage_names_by_folder_and_drop_duplicates():
    properties = montage([segment(STAND, 0, 0, 0.3), segment(ACTION, 0.3, 0, 5.0)])
    other = montage([segment(STAND, 0, 0, 0.4), segment(ACTION, 0.4, 0, 5.0)])
    result, skipped = montage_sequences.sequences(
        [(f"{ROOT}/CommonAnim/Extra/AM_Test01", other), (f"{ROOT}/CommonAnim/AM_Test01", properties),
         (f"{ROOT}/CommonAnim/AM_Copy01", properties)], MONTAGE_CLIPS, ROOT)
    assert skipped == []
    # Sorted by package; an identical timeline is listed once.
    assert [item["name"] for item in result] == ["AM_Copy01", "AM_Test01 (CommonAnim/Extra)"]

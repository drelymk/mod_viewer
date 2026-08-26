"""Tests for record mode: rewrite if/elif/endif gates from recorded
per-position visibility (see core.editing.record's module docstring for the exact
safe-pattern rules these tests exercise).

Every refusal case here is a *designed* boundary of the conservative
approach, not a bug: record_editor deliberately regenerates a whole
if/elif/endif chain only when it can prove the chain is "clean" (single var,
no mixing, no nesting, complete recorded data for every drawindexed line in
it) and otherwise reports exactly what's blocking it rather than guessing â€”
because guessing wrong here would silently change what a real mod shows.

The synthetic fixtures above prove each rule in isolation; the corpus dry run
at the bottom (test_real_mods_record_toggle) exercises the real distribution
of real ini shapes to get honest safe-vs-refused numbers before the UI is
built on top of this.
"""

import os

import pytest


from tests.support.corpus import sample_mods
from core.ini.sections import sections_from_document
from core.ini.document import IniDocument, IF, ENDIF, DRAW
from core.ini import condition as ic
from core.editing import toggle as te
from core.editing import record as re_


def doc(text):
    return IniDocument.from_string(text, path="<mem>")


def dline(d, needle):
    """The one Line whose raw text contains `needle` â€” avoids hand-counting
    line numbers in the fixtures below."""
    hits = [l for l in d.lines if needle in l.raw]
    assert len(hits) == 1, f"expected exactly one line containing {needle!r}, found {len(hits)}"
    return hits[0]


def fails(fn, msg):
    try:
        fn()
        assert (False), (msg + " (no error raised)")
    except te.ToggleEditError:
        assert (True), (msg)


# â”€â”€ fixtures â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

BARE = """[Constants]
global persist $swap = 0

[KeySwap]
key = 1
type = cycle
$swap = 0,1,2

[TextureOverrideBody]
drawindexed = 500,0,0
"""

SINGLE_IF = """[Constants]
global persist $swap = 0

[KeySwap]
key = 1
type = cycle
$swap = 0,1

[TextureOverrideBody]
if $swap == 0
drawindexed = 100,0,0
endif
"""

CHAIN3 = """[Constants]
global persist $swap = 0

[KeySwap]
key = 1
type = cycle
$swap = 0,1,2

[TextureOverrideBody2]
if $swap == 0
drawindexed = 600,0,0
elif $swap == 1
drawindexed = 700,0,0
elif $swap == 2
drawindexed = 800,0,0
endif
"""

FOURVAL = """[Constants]
global persist $stage = 0

[KeyStage]
key = 1
type = cycle
$stage = 0,1,2,3

[TextureOverrideBody3]
drawindexed = 900,0,0
"""

ELSE_CHAIN = """[Constants]
global persist $swap = 0

[KeySwap]
key = 1
type = cycle
$swap = 0,1

[TextureOverrideBody]
if $swap == 0
drawindexed = 100,0,0
else
drawindexed = 200,0,0
endif
"""

MIXED = """[Constants]
global persist $swap = 0

[KeySwap]
key = 1
type = cycle
$swap = 0,1

[TextureOverrideBody]
if $swap == 0 && $DRAW_TYPE == 1
drawindexed = 100,0,0
elif $swap == 1
drawindexed = 200,0,0
endif
"""

OUTER_REF = """[Constants]
global persist $swap = 0
global persist $other = 0

[KeySwap]
key = 1
type = cycle
$swap = 0,1

[TextureOverrideBody]
if $swap == 0
if $other == 1
drawindexed = 100,0,0
endif
endif
"""

NESTED_IN_CHAIN = """[Constants]
global persist $swap = 0
global persist $other = 0

[KeySwap]
key = 1
type = cycle
$swap = 0,1

[TextureOverrideBody]
if $swap == 0
if $other == 1
drawindexed = 100,0,0
endif
drawindexed = 150,0,0
elif $swap == 1
drawindexed = 200,0,0
endif
"""

ASSIGN_IN_CHAIN = """[Constants]
global persist $swap = 0

[KeySwap]
key = 1
type = cycle
$swap = 0,1

[TextureOverrideBody]
if $swap == 0
ps-t0 = ResourceSomething
drawindexed = 100,0,0
elif $swap == 1
drawindexed = 200,0,0
endif
"""

MULTISRC = """[Constants]
global persist $swap = 0

[KeySwap]
key = 1
type = cycle
$swap = 0,1

[TextureOverrideBody]
if $swap == 0
drawindexed = 100,0,0
elif $swap == 1
drawindexed = 200,0,0
endif
"""

UNSAFE = """[Constants]
global persist $swap = 0

[KeySwap]
key = 1
type = cycle
$swap = 0,1

[TextureOverrideBody]
if $swap == 0
drawindexed = 100,0,0
endif
endif
"""

TWO_VAR_BARE = """[Constants]
global persist $upper = 0
global persist $tt = 0

[KeyMulti]
key = 1
type = cycle
$upper = 0,1
$tt = 0,1

[TextureOverrideBody]
drawindexed = 999,0,0
"""

MISMATCHED_LENGTHS = """[Constants]
global persist $short = 0
global persist $long = 0

[KeyMulti]
key = 1
type = cycle
$short = 0,1
$long = 0,1,2

[TextureOverrideBody]
drawindexed = 999,0,-4
"""

OVERLAP = """[Constants]
global persist $upper = 0
global persist $tt = 0

[KeyMulti]
key = 1
type = cycle
$upper = 0,1
$tt = 0,1

[TextureOverrideBody]
if $upper == 0
drawindexed = 100,0,0
elif $upper == 1
drawindexed = 200,0,0
endif
"""


# â”€â”€ validation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_record_toggle_validates_section_and_positions():
    d = doc(BARE)
    line = dline(d, "500,0,0")
    fails(lambda: re_.record_toggle(d, "KeyNope", {0: [], 1: [], 2: []}),
          "recording a nonexistent section raises")
    fails(lambda: re_.record_toggle(d, "KeySwap", {0: [], 1: [line.no + 1]}),
          "a position map missing a position raises")
    fails(lambda: re_.record_toggle(d, "KeySwap", {0: [], 1: [], 2: [], 3: []}),
          "a position map with an extra position raises")


def test_record_toggle_accepts_string_position_keys():
    d = doc(BARE)
    line = dline(d, "500,0,0")
    report = re_.record_toggle(d, "KeySwap", {"0": [], "1": [line.no + 1], "2": []})
    assert (report["wraps_added"] == 1), (f"string position keys (as pywebview/JSON would deliver) are accepted ({report})")




# â”€â”€ bare (previously unconditional) lines â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_bare_line_wrapped_when_partially_visible():
    d = doc(BARE)
    line = dline(d, "500,0,0")
    report = re_.record_toggle(d, "KeySwap", {0: [], 1: [line.no + 1], 2: []})
    assert (report["vars_updated"] == ["swap"] and report["chains_rewritten"] == 0
          and report["wraps_added"] == 1 and report["skipped"] == []), (f"clean private wrap, no refusals ({report})")
    gate = d.lines[dline(d, "500,0,0").no - 1]
    assert (gate.kind == IF and gate.text == "if $swap == 1"), (f"new private if wraps the line at its one visible position ({gate.text})")
    assert (d.lines[dline(d, "500,0,0").no + 1].kind == ENDIF), ("endif follows immediately")
    reparsed = doc(d.to_string())
    assert (reparsed.section("TextureOverrideBody") is not None), ("the rewritten document round-trips through from_string")






def test_two_variables_claiming_same_bare_line_refused():
    d = doc(TWO_VAR_BARE)
    before = d.to_string()
    line = dline(d, "999,0,0")
    report = re_.record_toggle(d, "KeyMulti", {0: [], 1: [line.no + 1]})
    assert (report["wraps_added"] == 0), (f"neither variable claims a line both could equally explain ({report})")
    assert (len(report["skipped"]) == 1
          and "more than one variable" in report["skipped"][0]["reason"]), (f"the ambiguity is reported ({report['skipped']})")
    assert (d.to_string() == before), ("document left completely untouched")


def test_mismatched_cycle_lengths_hold_last_value_and_refuse_ambiguity():
    d = doc(MISMATCHED_LENGTHS)
    before = d.to_string()
    line = dline(d, "999,0,-4")
    report = re_.record_toggle(d, "KeyMulti", {
        0: [], 1: [], 2: [line.no + 1],
    })
    # $short is 1 at both positions 1 and 2, so it cannot encode visibility
    # at position 2 alone. $long can, and should own the safe wrapper.
    assert any(item["var"] == "short" and "same value" in item["reason"]
               for item in report["skipped"])
    assert report["wraps_added"] == 1
    assert "if $long == 2" in d.to_string()
    assert "if $short" not in d.to_string()
    assert d.to_string() != before
    assert re_.verify_recording("<mem>", report, document=d) == []


# â”€â”€ chain regeneration â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€



def test_elif_chain_value_reshuffle_matches_intent():
    """The core hazard the whole conservative design exists to avoid: elif
    branches are mutually exclusive and evaluated in order, so surgically
    OR-ing a value into one branch could silently do nothing (or silently
    steal a value from a later sibling). Verify the *actual* gating â€” via
    ini_condition.reduce, not just the regenerated text â€” matches the
    intended swap exactly."""
    d = doc(CHAIN3)
    l600, l700, l800 = dline(d, "600,0,0"), dline(d, "700,0,0"), dline(d, "800,0,0")
    report = re_.record_toggle(d, "KeySwap", {
        0: [l700.no + 1],
        1: [l600.no + 1],
        2: [l800.no + 1],
    })
    assert (report["chains_rewritten"] == 1 and report["skipped"] == []), (f"whole chain regenerated as one unit ({report})")

    sec = d.section("TextureOverrideBody2")
    visible = {"0": [], "1": [], "2": []}
    cond = None
    for line in sec.lines:
        if line.kind == IF:
            cond = ic.parse(line.text.split(None, 1)[1])
        elif line.kind == ENDIF:
            cond = None
        elif line.kind == DRAW:
            count = line.text.split("=", 1)[1].split(",")[0].strip()
            for val in visible:
                if cond is None or ic.reduce(cond, {"swap": val}) is ic.TRUE:
                    visible[val].append(count)
    assert (visible == {"0": ["700"], "1": ["600"], "2": ["800"]}), (f"gating matches the intended swap exactly, not just the text ({visible})")
    reparsed = doc(d.to_string())
    assert (reparsed.section("TextureOverrideBody2") is not None), ("round-trips through from_string")


def test_bare_wrap_overlapping_another_vars_chain_refused():
    d = doc(OVERLAP)
    l100, l200 = dline(d, "100,0,0"), dline(d, "200,0,0")
    # $upper's chain genuinely needs reshuffling; $tt's recorded data (same
    # shared position->line map) makes both of its lines look like bare
    # candidates for $tt too, but both sit inside the span $upper's chain
    # edit is about to replace â€” must be refused, not silently dropped or
    # (worse) spliced into a stale line range.
    report = re_.record_toggle(d, "KeyMulti", {0: [l200.no + 1], 1: [l100.no + 1]})
    assert (report["chains_rewritten"] == 1), (f"upper's chain is regenerated ({report})")
    assert (report["wraps_added"] == 0), (f"tt's bare-wrap candidates inside upper's chain are refused, not applied ({report})")
    overlap_skips = [s for s in report["skipped"] if s["var"] == "tt"]
    assert (len(overlap_skips) == 2 and all("same save" in s["reason"] for s in overlap_skips)), (f"both of tt's candidate lines are refused with the overlap reason ({overlap_skips})")
    assert ("$tt ==" not in d.to_string()), ("no $tt gate was actually written")


# â”€â”€ refusals â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _swap_recording(d, first, second):
    return {
        0: [dline(d, second).no + 1],
        1: [dline(d, first).no + 1],
    }


_REFUSAL_CASES = [
    ("else-branch", ELSE_CHAIN,
     lambda d: _swap_recording(d, "100,0,0", "200,0,0"), "else"),
    ("mixed-condition", MIXED,
     lambda d: _swap_recording(d, "100,0,0", "200,0,0"), "mixes"),
]


def _assert_refusal_case(case):
    name, fixture, positions_fn, reason = case
    d = doc(fixture)
    if name == "ambiguous-nesting":
        assert (d.structure_errors() != []), ("fixture really is structurally ambiguous")
    before = d.to_string()
    report = re_.record_toggle(d, "KeySwap", positions_fn(d))
    assert (report["chains_rewritten"] == 0), (f"{name}: unsafe shape is never auto-rewritten ({report})")
    assert (report["wraps_added"] == 0), (f"{name}: unsafe shape does not add a partial wrapper ({report})")
    assert (any(reason in skipped["reason"] for skipped in report["skipped"])), (f"{name}: refusal explains the unsafe shape ({report['skipped']})")
    assert (d.to_string() == before), (f"{name}: document remains completely untouched")


@pytest.mark.parametrize(
    "case",
    _REFUSAL_CASES,
    ids=[case[0] for case in _REFUSAL_CASES],
)
def test_record_refuses_unsafe_shape(case):
    _assert_refusal_case(case)


# â”€â”€ post-save self-check (report["verify"] / verify_recording) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#
# record_toggle proves its rewrite correct only in-memory; verify_recording
# is the runtime safety net that re-derives visibility from a fresh,
# independent re-parse of whatever actually landed on disk (app/
# toggle_api.record_toggle calls it after every real save). These tests
# cover both halves: that record_toggle's own report["verify"] field names
# exactly the draws it actually rewrote (never a refused one, from either
# _analyze_var or the later bare-claim pass), and that verify_recording
# correctly confirms a genuine match and correctly flags a genuine mismatch.
#
# "verify" identifies each draw by its own (section, count, start, base)
# triple rather than by line number: regenerating a chain with 2+ distinct
# desired position-sets emits one standalone if/endif block per group, which
# shifts the line numbers of every draw after the first shifted one â€” but a
# draw's own drawindexed args never change, so that's what both this report
# and verify_recording's fresh re-parse key on instead.

def _draws_by_key(draws):
    """{(count, start, base): positions} from a report["verify"][var]["draws"]
    list â€” order-independent, so tests don't have to assume insertion order."""
    return {(d["count"], d["start"], d["base"]): d["positions"] for d in draws}


def test_verify_report_reflects_chain_rewrites():
    d = doc(CHAIN3)
    l600, l700, l800 = dline(d, "600,0,0"), dline(d, "700,0,0"), dline(d, "800,0,0")
    report = re_.record_toggle(d, "KeySwap", {
        0: [l700.no + 1], 1: [l600.no + 1], 2: [l800.no + 1],
    })
    verify = report["verify"]
    assert (set(verify) == {"swap"}), (f"verify is keyed by the rewritten var ({verify})")
    assert (verify["swap"]["values"] == ["0", "1", "2"]), (f"the var's own values list is included ({verify['swap']})")
    draws = verify["swap"]["draws"]
    assert (all(dr["section"] == "TextureOverrideBody2" for dr in draws)), (f"every draw carries its own section name ({draws})")
    assert (_draws_by_key(draws) == {(600, 0, 0): [1], (700, 0, 0): [0], (800, 0, 0): [2]}), (f"every rewritten chain draw's exact recorded position set is present, keyed "
          f"by its own (count, start, base) identity rather than a line number that "
          f"chain regeneration can shift ({draws})")




def test_verify_report_excludes_lines_refused_for_any_reason():
    cases = [
        (ELSE_CHAIN, "KeySwap",
         lambda d: {0: [dline(d, "200,0,0").no + 1], 1: [dline(d, "100,0,0").no + 1]}),
        (MIXED, "KeySwap",
         lambda d: {0: [dline(d, "200,0,0").no + 1], 1: [dline(d, "100,0,0").no + 1]}),
        (TWO_VAR_BARE, "KeyMulti",
         lambda d: {0: [], 1: [dline(d, "999,0,0").no + 1]}),
    ]
    for text, section, positions_fn in cases:
        d = doc(text)
        report = re_.record_toggle(d, section, positions_fn(d))
        assert (report["verify"] == {}), (f"a fully-refused recording leaves verify empty ({section}: {report['verify']})")

    # OVERLAP: $upper's chain genuinely succeeds (and must stay verified)
    # while $tt's bare-wrap on the very same two lines is refused for
    # overlapping it (and must be excluded) â€” both at once.
    d = doc(OVERLAP)
    l100, l200 = dline(d, "100,0,0"), dline(d, "200,0,0")
    report = re_.record_toggle(d, "KeyMulti", {0: [l200.no + 1], 1: [l100.no + 1]})
    assert (set(report["verify"]) == {"upper"}), (f"only upper's successful chain is verified; tt's refused, overlapping "
          f"wrap is excluded, not just silently omitted from a wrong var ({report['verify']})")
    assert (_draws_by_key(report["verify"]["upper"]["draws"]) == {(100, 0, 0): [1], (200, 0, 0): [0]}), (f"upper's own rewritten draws are both still verified despite tt's "
          f"refusal on the same lines ({report['verify']})")


def test_verify_recording_confirms_a_genuine_match():
    import tempfile
    d = doc(CHAIN3)
    l600, l700, l800 = dline(d, "600,0,0"), dline(d, "700,0,0"), dline(d, "800,0,0")
    report = re_.record_toggle(d, "KeySwap", {
        0: [l700.no + 1], 1: [l600.no + 1], 2: [l800.no + 1],
    })
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".ini", delete=False) as fh:
        fh.write(d.to_string().encode("utf-8"))
        path = fh.name
    try:
        mismatches = re_.verify_recording(path, report)
    finally:
        os.remove(path)
    assert (mismatches == []), (f"a genuine, correctly-saved rewrite verifies clean ({mismatches})")


def test_verify_recording_detects_a_genuine_mismatch():
    """Simulates the failure verify_recording exists to catch: `report` (the
    "what we meant to write") disagrees with what's actually on disk. Rather
    than trying to engineer a real record_toggle bug, hand-craft a `report`
    that falsely claims an *extra* position for one draw beyond what CHAIN3's
    real rewrite actually produced â€” proving the check has teeth, not just
    always returning []."""
    import tempfile
    d = doc(CHAIN3)
    l600, l700, l800 = dline(d, "600,0,0"), dline(d, "700,0,0"), dline(d, "800,0,0")
    re_.record_toggle(d, "KeySwap", {
        0: [l700.no + 1], 1: [l600.no + 1], 2: [l800.no + 1],
    })
    wrong_report = {"verify": {"swap": {"values": ["0", "1", "2"], "draws": [
        {"section": "TextureOverrideBody2", "count": 600, "start": 0, "base": 0,
         "positions": [0, 1]},  # falsely also claims position 0; really only 1
        {"section": "TextureOverrideBody2", "count": 700, "start": 0, "base": 0,
         "positions": [0]},     # actually correct
        {"section": "TextureOverrideBody2", "count": 800, "start": 0, "base": 0,
         "positions": [2]},     # actually correct
    ]}}}
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".ini", delete=False) as fh:
        fh.write(d.to_string().encode("utf-8"))
        path = fh.name
    try:
        mismatches = re_.verify_recording(path, wrong_report)
    finally:
        os.remove(path)
    assert (len(mismatches) == 1 and mismatches[0]["draw"] == [600, 0, 0]
          and mismatches[0]["position"] == 0 and mismatches[0]["expected"] is True), (f"a deliberately-wrong recorded position is caught, not silently accepted "
          f"({mismatches})")




# â”€â”€ real-mod corpus dry run â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Real recorded per-position visibility only exists once a live recording
# session produces it â€” so this reconstructs a plausible stand-in from each
# real toggle's *existing* gating: for every position, every other toggle var
# is pinned at its declared default (as if only this one toggle were being
# cycled, matching a real recording session) and each drawindexed line's
# current DNF condition (via core.ini.parser._scan_sections_for_draws â€” the same
# condition-tracking pass build_draw_groups itself uses, proven across the
# whole corpus by tests.core.ini.test_condition.test_corpus) decides whether it counts
# as visible there. Feeding that back into record_toggle exercises the real
# distribution of real ini shapes, not just the fixtures above.

def _dnf_visible(conds, bindings):
    """True if a _scan_sections_for_draws DNF (conds) is satisfied given
    `bindings` ({var: value string}); [] means unconditional. Every var that
    can appear in a toggle-var clause is always bound here (its own
    section's position, or every other toggle var's own default), so there
    is no "unknown var" case to worry about, unlike build_draw_groups' own
    fail-open default."""
    if conds == []:
        return True
    return any(all((bindings.get(c["var"]) == c["value"]) != c["negate"] for c in group)
               for group in conds)


_REASON_BUCKETS = [
    ("this section's if/elif/endif nesting is ambiguous", "ambiguous section nesting"),
    ("gated by this variable at an outer nesting level", "gated at outer nesting level only"),
    ("chain has an else branch", "else branch present"),
    ("condition mixes $", "mixed condition (other vars/literals present)"),
    ("non-drawindexed content inside a $", "non-drawindexed content inside chain"),
    ("no recorded data for this draw", "no recorded data (multi-source or uncovered)"),
    ("not a simple if/elif condition", "not a simple if/elif condition"),
    ("condition does not parse", "condition failed to parse"),
    ("elif/else with no matching if", "elif/else with no matching if"),
    ("chain never closes", "chain never closes (unmatched if)"),
    ("targeted by more than one variable", "ambiguous: 2+ vars claim same bare line"),
    ("sits inside another variable's gate", "bare-wrap overlaps another var's chain edit"),
    ("not a drawindexed line", "stale/invalid line reference"),
]


def _bucket_reason(reason):
    for needle, label in _REASON_BUCKETS:
        if needle in reason:
            return label
    return f"other: {reason}"

"""Tests for record mode: rewrite if/elif/endif gates from recorded
per-position visibility (see record_editor's module docstring for the exact
safe-pattern rules these tests exercise).

Every refusal case here is a *designed* boundary of the conservative
approach, not a bug: record_editor deliberately regenerates a whole
if/elif/endif chain only when it can prove the chain is "clean" (single var,
no mixing, no nesting, complete recorded data for every drawindexed line in
it) and otherwise reports exactly what's blocking it rather than guessing —
because guessing wrong here would silently change what a real mod shows.

The synthetic fixtures above prove each rule in isolation; the corpus dry run
at the bottom (test_real_mods_record_toggle) exercises the real distribution
of real ini shapes to get honest safe-vs-refused numbers before the UI is
built on top of this.
"""

import os, sys, random

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _corpus import corpus_roots
from core.ini_document import IniDocument, IF, ENDIF, DRAW
from core import ini_condition as ic
from core import toggle_editor as te
from core import record_editor as re_

FAILS = []


def check(cond, msg):
    print(("PASS  " if cond else "FAIL  ") + msg)
    if not cond:
        FAILS.append(msg)


def doc(text):
    return IniDocument.from_string(text, path="<mem>")


def dline(d, needle):
    """The one Line whose raw text contains `needle` — avoids hand-counting
    line numbers in the fixtures below."""
    hits = [l for l in d.lines if needle in l.raw]
    assert len(hits) == 1, f"expected exactly one line containing {needle!r}, found {len(hits)}"
    return hits[0]


def fails(fn, msg):
    try:
        fn()
        check(False, msg + " (no error raised)")
    except te.ToggleEditError:
        check(True, msg)


# ── fixtures ─────────────────────────────────────────────────────────────────

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


# ── validation ───────────────────────────────────────────────────────────────

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
    check(report["wraps_added"] == 1,
          f"string position keys (as pywebview/JSON would deliver) are accepted ({report})")


def test_position_referencing_non_draw_line_is_reported_and_ignored():
    d = doc(BARE)
    key_line = dline(d, "type = cycle")
    line = dline(d, "500,0,0")
    report = re_.record_toggle(d, "KeySwap", {0: [key_line.no + 1], 1: [line.no + 1], 2: []})
    check(any(s["var"] is None and "not a drawindexed line" in s["reason"]
              for s in report["skipped"]),
          f"a non-drawindexed line number is reported, not silently trusted ({report['skipped']})")
    check(report["wraps_added"] == 1,
          f"the genuine draw line is still recorded normally ({report})")


# ── bare (previously unconditional) lines ───────────────────────────────────

def test_bare_line_wrapped_when_partially_visible():
    d = doc(BARE)
    line = dline(d, "500,0,0")
    report = re_.record_toggle(d, "KeySwap", {0: [], 1: [line.no + 1], 2: []})
    check(report["vars_updated"] == ["swap"] and report["chains_rewritten"] == 0
          and report["wraps_added"] == 1 and report["skipped"] == [],
          f"clean private wrap, no refusals ({report})")
    gate = d.lines[dline(d, "500,0,0").no - 1]
    check(gate.kind == IF and gate.text == "if $swap == 1",
          f"new private if wraps the line at its one visible position ({gate.text})")
    check(d.lines[dline(d, "500,0,0").no + 1].kind == ENDIF, "endif follows immediately")
    reparsed = doc(d.to_string())
    check(reparsed.section("TextureOverrideBody") is not None,
          "the rewritten document round-trips through from_string")


def test_bare_line_untouched_when_visible_everywhere():
    d = doc(BARE)
    line = dline(d, "500,0,0")
    before = d.to_string()
    report = re_.record_toggle(
        d, "KeySwap", {0: [line.no + 1], 1: [line.no + 1], 2: [line.no + 1]})
    check(report["wraps_added"] == 0 and report["chains_rewritten"] == 0
          and report["skipped"] == [],
          f"nothing to do when already visible at every position ({report})")
    check(d.to_string() == before, "document is byte-identical (true no-op)")


def test_or_expression_for_multi_position_subset():
    d = doc(FOURVAL)
    line = dline(d, "900,0,0")
    report = re_.record_toggle(
        d, "KeyStage", {0: [line.no + 1], 1: [], 2: [line.no + 1], 3: []})
    check(report["wraps_added"] == 1 and report["skipped"] == [],
          f"clean OR-wrap across a 2-of-4 position subset ({report})")
    gate = d.lines[dline(d, "900,0,0").no - 1]
    check(gate.text == "if $stage == 0 || $stage == 2",
          f"OR-expression lists exactly the recorded-visible positions ({gate.text})")


def test_two_variables_claiming_same_bare_line_refused():
    d = doc(TWO_VAR_BARE)
    before = d.to_string()
    line = dline(d, "999,0,0")
    report = re_.record_toggle(d, "KeyMulti", {0: [], 1: [line.no + 1]})
    check(report["wraps_added"] == 0,
          f"neither variable claims a line both could equally explain ({report})")
    check(len(report["skipped"]) == 1
          and "more than one variable" in report["skipped"][0]["reason"],
          f"the ambiguity is reported ({report['skipped']})")
    check(d.to_string() == before, "document left completely untouched")


# ── chain regeneration ───────────────────────────────────────────────────────

def test_single_branch_chain_value_reassigned():
    d = doc(SINGLE_IF)
    line = dline(d, "100,0,0")
    report = re_.record_toggle(d, "KeySwap", {0: [], 1: [line.no + 1]})
    check(report["chains_rewritten"] == 1 and report["wraps_added"] == 0
          and report["skipped"] == [],
          f"single-if (no elif) chain regenerated cleanly ({report})")
    gate = d.lines[dline(d, "100,0,0").no - 1]
    check(gate.text == "if $swap == 1",
          f"condition now selects the new value ({gate.text})")
    reparsed = doc(d.to_string())
    check(reparsed.section("TextureOverrideBody") is not None, "round-trips through from_string")


def test_elif_chain_value_reshuffle_matches_intent():
    """The core hazard the whole conservative design exists to avoid: elif
    branches are mutually exclusive and evaluated in order, so surgically
    OR-ing a value into one branch could silently do nothing (or silently
    steal a value from a later sibling). Verify the *actual* gating — via
    ini_condition.reduce, not just the regenerated text — matches the
    intended swap exactly."""
    d = doc(CHAIN3)
    l600, l700, l800 = dline(d, "600,0,0"), dline(d, "700,0,0"), dline(d, "800,0,0")
    report = re_.record_toggle(d, "KeySwap", {
        0: [l700.no + 1],
        1: [l600.no + 1],
        2: [l800.no + 1],
    })
    check(report["chains_rewritten"] == 1 and report["skipped"] == [],
          f"whole chain regenerated as one unit ({report})")

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
    check(visible == {"0": ["700"], "1": ["600"], "2": ["800"]},
          f"gating matches the intended swap exactly, not just the text ({visible})")
    reparsed = doc(d.to_string())
    check(reparsed.section("TextureOverrideBody2") is not None, "round-trips through from_string")


def test_bare_wrap_overlapping_another_vars_chain_refused():
    d = doc(OVERLAP)
    l100, l200 = dline(d, "100,0,0"), dline(d, "200,0,0")
    # $upper's chain genuinely needs reshuffling; $tt's recorded data (same
    # shared position->line map) makes both of its lines look like bare
    # candidates for $tt too, but both sit inside the span $upper's chain
    # edit is about to replace — must be refused, not silently dropped or
    # (worse) spliced into a stale line range.
    report = re_.record_toggle(d, "KeyMulti", {0: [l200.no + 1], 1: [l100.no + 1]})
    check(report["chains_rewritten"] == 1, f"upper's chain is regenerated ({report})")
    check(report["wraps_added"] == 0,
          f"tt's bare-wrap candidates inside upper's chain are refused, not applied ({report})")
    overlap_skips = [s for s in report["skipped"] if s["var"] == "tt"]
    check(len(overlap_skips) == 2 and all("same save" in s["reason"] for s in overlap_skips),
          f"both of tt's candidate lines are refused with the overlap reason ({overlap_skips})")
    check("$tt ==" not in d.to_string(), "no $tt gate was actually written")


# ── refusals ─────────────────────────────────────────────────────────────────

def test_else_branch_chain_refused():
    d = doc(ELSE_CHAIN)
    before = d.to_string()
    l100, l200 = dline(d, "100,0,0"), dline(d, "200,0,0")
    report = re_.record_toggle(d, "KeySwap", {0: [l200.no + 1], 1: [l100.no + 1]})
    check(report["chains_rewritten"] == 0, "an else-branch chain is never auto-rewritten")
    check(len(report["skipped"]) == 1 and "else" in report["skipped"][0]["reason"],
          f"refusal explains why ({report['skipped']})")
    check(d.to_string() == before, "document left completely untouched")


def test_mixed_condition_chain_refused():
    d = doc(MIXED)
    before = d.to_string()
    l100, l200 = dline(d, "100,0,0"), dline(d, "200,0,0")
    report = re_.record_toggle(d, "KeySwap", {0: [l200.no + 1], 1: [l100.no + 1]})
    check(report["chains_rewritten"] == 0, "a mixed condition prevents auto-rewrite")
    check(any("mixes" in s["reason"] for s in report["skipped"]),
          f"refusal names the mixed condition ({report['skipped']})")
    check(d.to_string() == before, "document left completely untouched")


def test_outer_ancestor_reference_refused():
    """`var` is referenced only by an outer (non-immediate) ancestor — an
    unusual nesting the design deliberately declines to guess at."""
    d = doc(OUTER_REF)
    before = d.to_string()
    line = dline(d, "100,0,0")
    report = re_.record_toggle(d, "KeySwap", {0: [], 1: [line.no + 1]})
    check(report["chains_rewritten"] == 0 and report["wraps_added"] == 0,
          "a var referenced only by an outer ancestor is never auto-rewritten")
    check(any("outer nesting level" in s["reason"] for s in report["skipped"]),
          f"refusal names the outer-nesting shape ({report['skipped']})")
    check(d.to_string() == before, "document left completely untouched")


def test_nested_if_inside_chain_body_refused():
    d = doc(NESTED_IN_CHAIN)
    before = d.to_string()
    l150, l200 = dline(d, "150,0,0"), dline(d, "200,0,0")
    report = re_.record_toggle(d, "KeySwap", {0: [l200.no + 1], 1: [l150.no + 1]})
    check(report["chains_rewritten"] == 0,
          "a nested if inside the chain body prevents auto-rewrite")
    check(any("non-drawindexed" in s["reason"] for s in report["skipped"]),
          f"refusal names the offending content ({report['skipped']})")
    check(d.to_string() == before, "document left completely untouched")


def test_non_draw_content_line_refused():
    d = doc(ASSIGN_IN_CHAIN)
    before = d.to_string()
    l100, l200 = dline(d, "100,0,0"), dline(d, "200,0,0")
    report = re_.record_toggle(d, "KeySwap", {0: [l200.no + 1], 1: [l100.no + 1]})
    check(report["chains_rewritten"] == 0,
          "a non-drawindexed content line prevents auto-rewrite")
    check(any("non-drawindexed" in s["reason"] for s in report["skipped"]),
          f"refusal names the offending content ({report['skipped']})")
    check(d.to_string() == before, "document left completely untouched")


def test_line_with_no_recorded_data_refused():
    """Stands in for the real-world case this guards: a mesh merged from more
    than one drawindexed line, which the frontend is responsible for
    excluding from what it sends — the backend's only signal is exactly this,
    a chain-body line absent from every position's list."""
    d = doc(MULTISRC)
    before = d.to_string()
    l100 = dline(d, "100,0,0")
    report = re_.record_toggle(d, "KeySwap", {0: [l100.no + 1], 1: []})
    check(report["chains_rewritten"] == 0,
          "a chain with an unrecorded line prevents auto-rewrite")
    check(any("no recorded data" in s["reason"] for s in report["skipped"]),
          f"refusal explains the missing data ({report['skipped']})")
    check(d.to_string() == before, "document left completely untouched")


def test_ambiguous_section_nesting_refused():
    d = doc(UNSAFE)
    check(d.structure_errors() != [], "fixture really is structurally ambiguous")
    before = d.to_string()
    line = dline(d, "100,0,0")
    report = re_.record_toggle(d, "KeySwap", {0: [], 1: [line.no + 1]})
    check(report["chains_rewritten"] == 0 and report["wraps_added"] == 0,
          "an ambiguously-nested section is never auto-rewritten")
    check(any("ambiguous" in s["reason"] for s in report["skipped"]),
          f"refusal explains why ({report['skipped']})")
    check(d.to_string() == before, "document left completely untouched")


# ── post-save self-check (report["verify"] / verify_recording) ─────────────
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
# shifts the line numbers of every draw after the first shifted one — but a
# draw's own drawindexed args never change, so that's what both this report
# and verify_recording's fresh re-parse key on instead.

def _draws_by_key(draws):
    """{(count, start, base): positions} from a report["verify"][var]["draws"]
    list — order-independent, so tests don't have to assume insertion order."""
    return {(d["count"], d["start"], d["base"]): d["positions"] for d in draws}


def test_verify_report_reflects_chain_rewrites():
    d = doc(CHAIN3)
    l600, l700, l800 = dline(d, "600,0,0"), dline(d, "700,0,0"), dline(d, "800,0,0")
    report = re_.record_toggle(d, "KeySwap", {
        0: [l700.no + 1], 1: [l600.no + 1], 2: [l800.no + 1],
    })
    verify = report["verify"]
    check(set(verify) == {"swap"}, f"verify is keyed by the rewritten var ({verify})")
    check(verify["swap"]["values"] == ["0", "1", "2"],
          f"the var's own values list is included ({verify['swap']})")
    draws = verify["swap"]["draws"]
    check(all(dr["section"] == "TextureOverrideBody2" for dr in draws),
          f"every draw carries its own section name ({draws})")
    check(_draws_by_key(draws) == {(600, 0, 0): [1], (700, 0, 0): [0], (800, 0, 0): [2]},
          f"every rewritten chain draw's exact recorded position set is present, keyed "
          f"by its own (count, start, base) identity rather than a line number that "
          f"chain regeneration can shift ({draws})")


def test_verify_report_includes_bare_wraps_and_untouched_bare_lines():
    d = doc(BARE)
    line = dline(d, "500,0,0")
    report = re_.record_toggle(d, "KeySwap", {0: [], 1: [line.no + 1], 2: []})
    check(report["verify"] == {"swap": {"values": ["0", "1", "2"], "draws": [
        {"section": "TextureOverrideBody", "count": 500, "start": 0, "base": 0,
         "positions": [1]}]}},
          f"a wrapped bare line is recorded in verify with its own draw identity and "
          f"recorded positions ({report['verify']})")

    d2 = doc(BARE)
    line2 = dline(d2, "500,0,0")
    report2 = re_.record_toggle(
        d2, "KeySwap", {0: [line2.no + 1], 1: [line2.no + 1], 2: [line2.no + 1]})
    check(report2["verify"] == {"swap": {"values": ["0", "1", "2"], "draws": [
        {"section": "TextureOverrideBody", "count": 500, "start": 0, "base": 0,
         "positions": [0, 1, 2]}]}},
          f"a bare line left untouched (already visible everywhere) is still "
          f"verifiable ({report2['verify']})")


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
        check(report["verify"] == {},
              f"a fully-refused recording leaves verify empty ({section}: {report['verify']})")

    # OVERLAP: $upper's chain genuinely succeeds (and must stay verified)
    # while $tt's bare-wrap on the very same two lines is refused for
    # overlapping it (and must be excluded) — both at once.
    d = doc(OVERLAP)
    l100, l200 = dline(d, "100,0,0"), dline(d, "200,0,0")
    report = re_.record_toggle(d, "KeyMulti", {0: [l200.no + 1], 1: [l100.no + 1]})
    check(set(report["verify"]) == {"upper"},
          f"only upper's successful chain is verified; tt's refused, overlapping "
          f"wrap is excluded, not just silently omitted from a wrong var ({report['verify']})")
    check(_draws_by_key(report["verify"]["upper"]["draws"]) == {(100, 0, 0): [1], (200, 0, 0): [0]},
          f"upper's own rewritten draws are both still verified despite tt's "
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
    check(mismatches == [], f"a genuine, correctly-saved rewrite verifies clean ({mismatches})")


def test_verify_recording_detects_a_genuine_mismatch():
    """Simulates the failure verify_recording exists to catch: `report` (the
    "what we meant to write") disagrees with what's actually on disk. Rather
    than trying to engineer a real record_toggle bug, hand-craft a `report`
    that falsely claims an *extra* position for one draw beyond what CHAIN3's
    real rewrite actually produced — proving the check has teeth, not just
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
    check(len(mismatches) == 1 and mismatches[0]["draw"] == [600, 0, 0]
          and mismatches[0]["position"] == 0 and mismatches[0]["expected"] is True,
          f"a deliberately-wrong recorded position is caught, not silently accepted "
          f"({mismatches})")


def test_verify_recording_is_noop_without_a_verify_field():
    check(re_.verify_recording("<does not exist>.ini", {}) == [],
          "a report with no verify field (or an empty one) trivially verifies clean, "
          "without even touching the filesystem")


# ── real-mod corpus dry run ─────────────────────────────────────────────────
# Real recorded per-position visibility only exists once a live recording
# session produces it — so this reconstructs a plausible stand-in from each
# real toggle's *existing* gating: for every position, every other toggle var
# is pinned at its declared default (as if only this one toggle were being
# cycled, matching a real recording session) and each drawindexed line's
# current DNF condition (via ini_parser._scan_sections_for_draws — the same
# condition-tracking pass build_draw_groups itself uses, proven across the
# whole corpus by test_ini_condition.test_corpus) decides whether it counts
# as visible there. Feeding that back into record_toggle exercises the real
# distribution of real ini shapes, not just the fixtures above.

MOD_ROOTS = corpus_roots()


def _find_mods(limit, seed=11):
    mods = []
    for root in MOD_ROOTS:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.upper().startswith("DISABLED")]
            if any(f.lower().endswith(".ini") and not f.upper().startswith("DISABLED")
                   for f in filenames):
                mods.append(dirpath)
    random.Random(seed).shuffle(mods)
    return mods[:limit]


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


def _parse_sections_from_text(text, fake_path):
    """ini_parser.parse_sections's exact rules, minus the file read — lets the
    dry run re-analyze record_editor's in-memory output through the same DNF
    machinery the app already trusts, without writing a temp file."""
    from core.ini_parser import SrcLine
    sections, current = {}, None
    for line_no, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        lhs = line.split("=", 1)[0].strip().lower()
        if lhs not in ("key", "back"):
            line = line.split(";")[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip()
            sections.setdefault(current, [])
        elif current is not None:
            sections[current].append(SrcLine(line, fake_path, line_no, current))
    return sections


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


def test_real_mods_record_toggle():
    import tempfile
    from core.ini_parser import (parse_sections, _scan_sections_for_draws,
                             extract_variable_defaults, find_inis)

    mods = _find_mods(300)
    if not mods:
        print("SKIP  no local mod libraries found")
        return

    total_inis = total_sections = total_vars = 0
    exceptions = []
    reparse_failures = []
    content_lost = []
    mismatches = []
    verify_mismatches = []
    chains_rewritten = wraps_added = 0
    reason_counts = {}
    ambiguous_dup_names = []

    for mod in mods:
        for path in find_inis(mod):
            total_inis += 1
            try:
                base_text = open(path, "rb").read().decode("utf-8")
                sections = parse_sections(path)
                draw_info = _scan_sections_for_draws(sections)
                defaults = extract_variable_defaults(sections)
                base_doc = IniDocument.from_string(base_text, path=path)
            except Exception:
                continue

            toggles = te.list_cycle_toggles(base_doc)
            if not toggles:
                continue

            # (section, count, start, base) -> conds, scoped to this one ini
            # (draw_info only ever describes the file we just parsed).
            before_draws = {
                (sec_name, count, start, base): conds
                for sec_name, info in draw_info.items()
                for (count, start, base, conds, src, _ib, _dv, _vb) in info["draws"]
            }

            # A file can legitimately repeat a `[Key...]` header (3DMigoto
            # merges same-named sections; IniDocument deliberately does not —
            # see IniDocument.section's own docstring). record_toggle (like
            # add/edit/delete_toggle before it) is keyed by name and can only
            # ever resolve to the *first* same-named section, so a duplicate
            # is a pre-existing name-based-lookup ambiguity, not something
            # this corpus check can exercise per-occurrence — count and skip
            # rather than misreport it as a record_toggle crash.
            name_counts = {}
            for n, _ in toggles:
                name_counts[n.lower()] = name_counts.get(n.lower(), 0) + 1

            for section_name, cvars in toggles:
                total_sections += 1

                if name_counts[section_name.lower()] > 1:
                    ambiguous_dup_names.append((path, section_name))
                    continue

                if not cvars:
                    # A cycle-typed Key section with no $var=... line at all —
                    # record_toggle should refuse cleanly, not crash, since
                    # there's nothing to record positions for.
                    d0 = IniDocument.from_string(base_text, path=path)
                    try:
                        re_.record_toggle(d0, section_name, {})
                        exceptions.append((path, section_name,
                                            "expected ToggleEditError for a var-less cycle section"))
                    except te.ToggleEditError:
                        pass
                    except Exception as e:
                        exceptions.append((path, section_name, repr(e)))
                    continue

                # record_toggle only ever rewrites the WRITABLE (non-namespaced)
                # vars in a section — namespaced/master vars are read-only, so
                # its own position count (and validation of position_lines'
                # keys) is scoped to writable.values() only, never the whole
                # section. A section can legitimately mix a local writable var
                # with a longer-cycling namespaced master var in the same
                # [Key...] block; that master var's own value isn't rewritten
                # here, so it's held at its declared global default (or its own
                # first cycle value, lacking one) rather than advanced — this
                # session is only "recording" the writable var(s)' positions.
                writable_cvars = {v: vals for v, vals in cvars.items()
                                   if not ic.is_namespaced(v)}
                if not writable_cvars:
                    # Every $var in this section is namespaced — record_toggle
                    # refuses cleanly (nothing writable to record), not a crash.
                    d0 = IniDocument.from_string(base_text, path=path)
                    try:
                        re_.record_toggle(d0, section_name, {0: []})
                        exceptions.append((path, section_name,
                                            "expected ToggleEditError for an all-namespaced cycle section"))
                    except te.ToggleEditError:
                        pass
                    except Exception as e:
                        exceptions.append((path, section_name, repr(e)))
                    continue

                total_vars += len(writable_cvars)
                max_positions = max(len(v) for v in writable_cvars.values())

                position_lines = {p: [] for p in range(max_positions)}
                for p in range(max_positions):
                    bindings = dict(defaults)
                    for var, values in writable_cvars.items():
                        bindings[var] = values[p % len(values)]
                    for var, values in cvars.items():
                        if var not in writable_cvars:
                            bindings[var] = defaults.get(var, values[0])
                    for info in draw_info.values():
                        for (count, start, base, conds, src, _ib, _dv, _vb) in info["draws"]:
                            if src is not None and _dnf_visible(conds, bindings):
                                position_lines[p].append(src["line_no"])

                d = IniDocument.from_string(base_text, path=path)
                try:
                    report = re_.record_toggle(d, section_name, position_lines)
                except Exception as e:
                    exceptions.append((path, section_name, repr(e)))
                    continue

                chains_rewritten += report["chains_rewritten"]
                wraps_added += report["wraps_added"]
                for s in report["skipped"]:
                    label = _bucket_reason(s["reason"])
                    reason_counts[label] = reason_counts.get(label, 0) + 1

                if report["chains_rewritten"] == 0 and report["wraps_added"] == 0:
                    continue    # nothing changed; before == after trivially

                after_text = d.to_string()
                try:
                    IniDocument.from_string(after_text, path=path)
                except Exception as e:
                    reparse_failures.append((path, section_name, repr(e)))
                    continue

                try:
                    after_sections = _parse_sections_from_text(after_text, path)
                    after_draw_info = _scan_sections_for_draws(after_sections)
                except Exception as e:
                    exceptions.append((path, section_name, "after-parse: " + repr(e)))
                    continue

                after_draws = {
                    (sec_name, count, start, base): conds
                    for sec_name, info in after_draw_info.items()
                    for (count, start, base, conds, src, _ib, _dv, _vb) in info["draws"]
                }

                if sorted(before_draws) != sorted(after_draws):
                    content_lost.append((path, section_name,
                                          sorted(set(before_draws) ^ set(after_draws))[:3]))
                    continue

                for p in range(max_positions):
                    bindings = dict(defaults)
                    for var, values in writable_cvars.items():
                        bindings[var] = values[p % len(values)]
                    for var, values in cvars.items():
                        if var not in writable_cvars:
                            bindings[var] = defaults.get(var, values[0])
                    for triple, before_conds in before_draws.items():
                        expected = _dnf_visible(before_conds, bindings)
                        actual = _dnf_visible(after_draws[triple], bindings)
                        if expected != actual:
                            mismatches.append((path, section_name, p, triple,
                                                expected, actual))

                # Exercise the new runtime self-check itself against this same
                # real rewrite: write after_text to a real file (verify_recording
                # needs an actual path — it re-parses via ini_parser.parse_sections,
                # the same trusted read path used everywhere else) and confirm it
                # reports zero mismatches for a rewrite this test has *also* just
                # independently proven correct above — i.e. verify_recording itself
                # must never false-positive across the whole real-mod corpus.
                with tempfile.NamedTemporaryFile(mode="wb", suffix=".ini", delete=False) as fh:
                    fh.write(after_text.encode("utf-8"))
                    tmp_path = fh.name
                try:
                    vm = re_.verify_recording(tmp_path, report)
                finally:
                    os.remove(tmp_path)
                if vm:
                    verify_mismatches.append((path, section_name, vm[:3]))

    print(f"      {len(mods)} mods, {total_inis} ini files, "
          f"{total_sections} cycle sections ({total_vars} vars) recorded")
    print(f"      aggregate: chains_rewritten={chains_rewritten} wraps_added={wraps_added}")
    if ambiguous_dup_names:
        print(f"      skipped {len(ambiguous_dup_names)} section(s) with a same-named "
              f"sibling in the same file (pre-existing name lookup ambiguity, shared by "
              f"add/edit/delete_toggle too): {ambiguous_dup_names[:3]}")
    for label, count in sorted(reason_counts.items(), key=lambda kv: -kv[1]):
        print(f"        refused[{count:>5}]  {label}")

    check(total_sections > 0, "real mods produced cycle toggles to test")
    check(exceptions == [],
          f"record_toggle never raises or hangs on a real file (first: {exceptions[:3]})")
    check(reparse_failures == [],
          f"every rewritten document still round-trips through from_string "
          f"(first: {reparse_failures[:3]})")
    check(content_lost == [],
          f"every drawindexed line survives a rewrite, none lost or duplicated "
          f"(first: {content_lost[:3]})")
    check(mismatches == [],
          f"a rewritten chain's actual gating always matches the recorded intent "
          f"(first: {mismatches[:3]})")
    check(verify_mismatches == [],
          f"verify_recording (the runtime post-save self-check) reports zero "
          f"false-positive mismatches across the whole real-mod corpus "
          f"(first: {verify_mismatches[:3]})")


if __name__ == "__main__":
    for fn in (test_record_toggle_validates_section_and_positions,
               test_record_toggle_accepts_string_position_keys,
               test_position_referencing_non_draw_line_is_reported_and_ignored,
               test_bare_line_wrapped_when_partially_visible,
               test_bare_line_untouched_when_visible_everywhere,
               test_or_expression_for_multi_position_subset,
               test_two_variables_claiming_same_bare_line_refused,
               test_single_branch_chain_value_reassigned,
               test_elif_chain_value_reshuffle_matches_intent,
               test_bare_wrap_overlapping_another_vars_chain_refused,
               test_else_branch_chain_refused,
               test_mixed_condition_chain_refused,
               test_outer_ancestor_reference_refused,
               test_nested_if_inside_chain_body_refused,
               test_non_draw_content_line_refused,
               test_line_with_no_recorded_data_refused,
               test_ambiguous_section_nesting_refused,
               test_verify_report_reflects_chain_rewrites,
               test_verify_report_includes_bare_wraps_and_untouched_bare_lines,
               test_verify_report_excludes_lines_refused_for_any_reason,
               test_verify_recording_confirms_a_genuine_match,
               test_verify_recording_detects_a_genuine_mismatch,
               test_verify_recording_is_noop_without_a_verify_field,
               test_real_mods_record_toggle):
        fn()
    print("\n" + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"))
    sys.exit(1 if FAILS else 0)

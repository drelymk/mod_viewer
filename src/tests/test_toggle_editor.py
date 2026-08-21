"""Tests for toggle CRUD: add/edit/delete a cycle toggle in place.

A "toggle" is three coupled pieces (see toggle_editor's module docstring): the
[KeyFoo] cycle section, its $var declaration in [Constants], and every
if/elif gate elsewhere that reads $var. These tests check that add/edit/
delete keep the three in lockstep, that every ToggleEditError leaves the
document completely untouched (so a caller never has to guess whether a
rejected edit did anything), and â€” via a corpus-wide dry run â€” that
delete_toggle never corrupts a real mod's condition syntax, drops a live gate
silently, or hangs.

That last property used to fail for real files: eliminate() only recognised a
dead variable as a bare `$var` operand on either side of a comparison, so a
condition like `cursor_x < $img_x + $norm_width` (a dead var buried inside
arithmetic) was invisible to it. references() still (correctly) said the line
depended on the dead var, so _strip_vars_from_gates kept re-selecting the same
untouched line as its next rewrite target forever. eliminate() now folds dead
vars out of arithmetic too; test_real_mods_delete_toggle is what caught it.
"""

import os

import pytest


from _corpus import sample_mods

from core.ini_document import IniDocument, IF, ELIF
from core import ini_condition as ic
from core import toggle_editor as te


def doc(text):
    return IniDocument.from_string(text, path="<mem>")


BASIC = """[Constants]
global persist $swapvar = 0

[KeySwap]
key = 1
type = cycle
$swapvar = 0,1,2

[TextureOverrideBody]
hash = abc123
if $swapvar == 0
drawindexed = 100,0,0
elif $swapvar == 1
drawindexed = 200,0,0
elif $swapvar == 2
drawindexed = 300,0,0
endif
"""


# â”€â”€ shared helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€



# â”€â”€ add_toggle â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_add_happy_path():
    d = doc(BASIC)
    name = te.add_toggle(d, "Extra", "2", "extravar", ["0", "1"])
    assert (name == "KeyExtra"), (f"add_toggle returns the new section name ({name})")
    sec = d.section("KeyExtra")
    assert (sec is not None), ("new section exists")
    assert (te.cycle_vars(sec) == {"extravar": ["0", "1"]}), ("new section declares the cycle var/values")
    const_line = te._constant_line(d, "extravar")
    assert (const_line is not None and const_line.text == "global persist $extravar = 0"), (f"Constants gets a declaration defaulting to the first value "
          f"({const_line and const_line.text})")
    reparsed = IniDocument.from_string(d.to_string())
    assert (reparsed.section("KeyExtra") is not None), ("the new section round-trips through from_string")




def test_add_creates_constants_section_when_missing():
    d = doc("""[KeySwap]
key = 1
type = cycle
$swapvar = 0,1
""")
    te.add_toggle(d, "Extra", "2", "extravar", ["0", "1"])
    assert (d.section("Constants") is not None), ("Constants section created from scratch")
    assert (te._constant_line(d, "extravar") is not None), ("the new var is declared in the freshly-created Constants section")


_ADD_INVALID_CASES = [
    ("duplicate section", ("Swap", "2", "newvar", ["0", "1"])),
    ("namespaced variable", ("Extra", "2", r"Master\swapvar", ["0", "1"])),
]


@pytest.mark.parametrize(
    "case_name, args",
    _ADD_INVALID_CASES,
    ids=[case[0] for case in _ADD_INVALID_CASES],
)
def test_add_rejects_invalid_toggle(case_name, args):
    d = doc(BASIC)
    before = d.to_string()
    with pytest.raises(te.ToggleEditError):
        te.add_toggle(d, *args)
    assert (d.to_string() == before), (f"{case_name}: rejected add leaves the document untouched")




# â”€â”€ add_toggle: condition line / on-screen-detection plumbing â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_add_condition_builds_active_plumbing_from_scratch():
    d = doc(BASIC)
    te.add_toggle(d, "Extra", "2", "extravar", ["0", "1"])

    sec = d.section("KeyExtra")
    assert (sec.lines[0].text == "condition = $active == 1"), (f"condition line is the new section's first line ({sec.lines[0].text!r})")

    const_line = te._constant_line(d, "active")
    assert (const_line is not None and const_line.text == "global $active = 0"), (f"$active declared without persist ({const_line and const_line.text!r})")

    present = d.section("Present")
    assert (present is not None), ("[Present] section created from scratch")
    non_blank = [l.text for l in present.lines if l.text]
    assert (non_blank == ["post $active = 0"]), (f"post $active = 0 is the new [Present] section's only content line ({non_blank})")

    body_sec = d.section("TextureOverrideBody")
    texts = [l.text for l in body_sec.lines]
    assert (texts[0] == "hash = abc123" and texts[1] == "$active = 1"), (f"$active = 1 planted right after the leading hash line, before the "
          f"if-block ({texts[:2]})")

    reparsed = IniDocument.from_string(d.to_string())
    assert (reparsed.section("Present") is not None and reparsed.section("KeyExtra") is not None), ("the new plumbing round-trips through from_string")


def test_add_condition_reuses_existing_object_detected():
    d = doc("""[Constants]
global $object_detected = 0

[KeySwap]
key = 1
type = cycle
$swapvar = 0,1,2

[TextureOverrideBody]
hash = abc123
drawindexed = 100,0,0
""")
    te.add_toggle(d, "Extra", "2", "extravar", ["0", "1"])
    sec = d.section("KeyExtra")
    assert (sec.lines[0].text == "condition = $object_detected == 1"), (f"reuses the existing $object_detected var ({sec.lines[0].text!r})")
    assert (d.section("Present") is None), ("no [Present] section is created when a detection var already exists")
    assert (te._constant_line(d, "active") is None), ("no $active is declared when $object_detected already exists")
    body_sec = d.section("TextureOverrideBody")
    non_blank = [l.text for l in body_sec.lines if l.text]
    assert (non_blank == ["hash = abc123", "drawindexed = 100,0,0"]), (f"the TextureOverride section is left untouched ({non_blank})")






def test_add_condition_marks_first_and_second_override_only():
    d = doc("""[Constants]
global persist $swapvar = 0

[KeySwap]
key = 1
type = cycle
$swapvar = 0,1,2

[TextureOverrideFirst]
hash = 111

[TextureOverrideSecond]
hash = 222

[TextureOverrideThird]
hash = 333
""")
    te.add_toggle(d, "Extra", "2", "extravar", ["0", "1"])
    first = [l.text for l in d.section("TextureOverrideFirst").lines if l.text]
    second = [l.text for l in d.section("TextureOverrideSecond").lines if l.text]
    third = [l.text for l in d.section("TextureOverrideThird").lines if l.text]
    assert (first == ["hash = 111", "$active = 1"]), (f"first override marked ({first})")
    assert (second == ["hash = 222", "$active = 1"]), (f"second override marked ({second})")
    assert (third == ["hash = 333"]), (f"third override left untouched ({third})")


def test_add_condition_idempotent_across_two_calls():
    d = doc(BASIC)
    te.add_toggle(d, "Extra", "2", "extravar", ["0", "1"])
    te.add_toggle(d, "Extra2", "3", "extravar2", ["0", "1"])

    actives = [l for l in d.section("Constants").lines if l.text == "global $active = 0"]
    assert (len(actives) == 1), ("still only one $active declaration after two add_toggle calls")

    present = d.section("Present")
    resets = [l for l in present.lines if l.text == "post $active = 0"]
    assert (len(resets) == 1), ("still only one post $active = 0 line after two calls")

    body_sec = d.section("TextureOverrideBody")
    marks = [l for l in body_sec.lines if l.text == "$active = 1"]
    assert (len(marks) == 1), ("TextureOverrideBody only marked once, not once per add_toggle call")

    sec2 = d.section("KeyExtra2")
    assert (sec2.lines[0].text == "condition = $active == 1"), ("the second toggle reuses the $active plumbing the first call created")






def test_add_condition_appends_into_existing_present_section():
    d = doc(BASIC + "\n[Present]\nrun = CommandListUnrelated\n")
    te.add_toggle(d, "Extra", "2", "extravar", ["0", "1"])
    present_secs = [s for s in d.sections if s.name == "Present"]
    assert (len(present_secs) == 1), ("no duplicate [Present] section created")
    texts = [l.text for l in present_secs[0].lines if l.text]
    assert (texts == ["run = CommandListUnrelated", "post $active = 0"]), (f"post $active = 0 appended after the existing content ({texts})")


# â”€â”€ edit_toggle â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€











def test_edit_var_values_conflict_raises_then_can_be_overridden():
    d = doc(BASIC)
    before = d.to_string()
    try:
        te.edit_toggle(d, "KeySwap", var_values={"swapvar": ["0", "1"]})
        assert (False), ("shrinking away a live gated value should raise")
    except te.ToggleEditError as e:
        assert ("2" in str(e)), (f"conflict error names the orphaned value ({e})")
    assert (d.to_string() == before), ("doc unchanged after the rejected shrink")

    te.edit_toggle(d, "KeySwap", var_values={"swapvar": ["0", "1"]},
                   allow_value_conflicts=True)
    assert (te.cycle_vars(d.section("KeySwap")) == {"swapvar": ["0", "1"]}), ("override allows the shrink to go through")


def test_edit_var_values_fixes_up_stale_default():
    """Regression: replacing a cycle's values must not leave the Constants
    declaration defaulting to a value the cycle no longer has."""
    removed_default = doc("""[Constants]
global persist $swapvar = 2

[KeySwap]
key = 1
type = cycle
$swapvar = 0,1,2

[TextureOverrideBody]
hash = abc
if $swapvar == 0
drawindexed = 100,0,0
elif $swapvar == 1
drawindexed = 200,0,0
elif $swapvar == 2
drawindexed = 300,0,0
endif
""")
    te.edit_toggle(removed_default, "KeySwap", var_values={"swapvar": ["0", "1"]},
                   allow_value_conflicts=True)
    assert (te._constant_line(removed_default, "swapvar").text == "global persist $swapvar = 0"), (f"default falls back to the new first value when its old default "
          f"was removed ({te._constant_line(removed_default, 'swapvar').text})")

    kept_default = doc("""[Constants]
global persist $swapvar = 2

[KeySwap]
key = 1
type = cycle
$swapvar = 0,1,2
""")
    te.edit_toggle(kept_default, "KeySwap", var_values={"swapvar": ["2", "3", "4"]})
    assert (te._constant_line(kept_default, "swapvar").text == "global persist $swapvar = 2"), ("default left untouched when it's still a valid value after the edit")


def test_edit_combined_rename_and_invalid_var_leaves_doc_untouched():
    """Regression: all fields must validate before any mutation â€” a failing
    var_values check must not leave a rename half-applied."""
    d = doc(BASIC)
    before = d.to_string()
    try:
        te.edit_toggle(d, "KeySwap", new_name="Renamed",
                       var_values={"nope": ["1", "2"]})
        assert (False), ("combined edit with an invalid var should raise")
    except te.ToggleEditError:
        assert (True), ("combined edit with an invalid var raises")
    assert (d.to_string() == before), ("doc fully unchanged: the rename was not partially applied")
    assert (d.section("KeySwap") is not None), ("section still has its original name")
    assert (d.section("Renamed") is None), ("renamed section was not created")




# â”€â”€ delete_toggle â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_delete_removes_section_and_constant_and_rewrites_gates():
    d = doc(BASIC)
    report = te.delete_toggle(d, "KeySwap")
    assert (report["section"] == "KeySwap"), ("report names the deleted section")
    assert (report["vars_removed"] == ["swapvar"]), ("report names the removed var")
    assert (d.section("KeySwap") is None), ("Key section removed")
    assert (te._constant_line(d, "swapvar") is None), ("Constants declaration removed")
    assert ("swapvar" not in d.to_string()), ("no reference to the deleted var remains anywhere")
    assert (report["gates_rewritten"] == 3), (f"all three gates rewritten ({report})")
    assert (report["always_false_gates"] == []), ("no gate was negated to permanently false")
    assert (report["unsafe_gates"] == []), ("no unsafe gates reported")


def test_delete_multi_var_section_shared_var_untouched():
    d = doc("""[Constants]
global persist $swapvar = 0
global persist $othervar = 0

[KeyMulti]
key = 1
type = cycle
$swapvar = 0,1
$othervar = 0,1

[KeyOther]
key = 2
type = cycle
$othervar = 0,1

[TextureOverrideBody]
hash = abc
if $swapvar == 0 && $othervar == 0
drawindexed = 100,0,0
endif
""")
    report = te.delete_toggle(d, "KeyMulti")
    assert (report["vars_removed"] == ["swapvar"]), (f"only swapvar removed, othervar still driven by KeyOther "
          f"({report['vars_removed']})")
    assert (te._constant_line(d, "othervar") is not None), ("othervar's Constants line survives")
    assert (d.section("KeyOther") is not None), ("KeyOther section untouched")
    gate = next(l for l in d.lines if l.kind in (IF, ELIF))
    assert (gate.text == "if $othervar == 0"), (f"gate keeps the surviving var and drops the dead one ({gate.text})")


def test_delete_namespaced_var_untouched():
    d = doc(r"""[Constants]
global persist $swapvar = 0

[KeySwap]
key = 1
type = cycle
$swapvar = 0,1

[TextureOverrideBody]
hash = abc
if $\Master\swapvar == 0 && $swapvar == 0
drawindexed = 100,0,0
endif
""")
    te.delete_toggle(d, "KeySwap")
    gate = next(l for l in d.lines if l.kind in (IF, ELIF))
    assert (gate.text == r"if $\Master\swapvar == 0"), (f"the namespaced var survives, only the local var's clause is "
          f"eliminated ({gate.text})")






def test_delete_reports_unsafe_gate():
    """A stray extra endif makes the section's nesting ambiguous; delete_toggle
    must not guess, and must report it instead of guessing."""
    d = doc("""[Constants]
global persist $swapvar = 0

[KeySwap]
key = 1
type = cycle
$swapvar = 0,1

[TextureOverrideBody]
hash = abc
if $swapvar == 0
drawindexed = 100,0,0
endif
endif
""")
    assert (d.structure_errors() != []), ("fixture really is structurally ambiguous")
    report = te.delete_toggle(d, "KeySwap")
    assert (len(report["unsafe_gates"]) == 1), (f"the ambiguous gate is reported as unsafe, not rewritten ({report})")
    assert (report["gates_rewritten"] == 0), ("the unsafe gate was not counted as rewritten")
    gate = next(l for l in d.lines if l.kind == IF)
    assert ("swapvar" in gate.text), ("the unsafe gate is left completely untouched")




# â”€â”€ corpus-wide dry run â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#
# Simulates delete_toggle against every real cycle toggle in a sample of real
# mod inis, never writing to disk. This is what caught the eliminate() /
# arithmetic bug described in the module docstring: a synthetic fixture would
# never have produced `cursor_x < $img_x + $norm_width`, but the real corpus
# did on the very first large sample.

def _line_parses(line):
    split = te._split_condition_line(line)
    if not split:
        return False
    try:
        ic.parse(split[1])
        return True
    except ic.ConditionError:
        return False

"""Tests for toggle CRUD: add/edit/delete a cycle toggle in place.

A "toggle" is three coupled pieces (see toggle_editor's module docstring): the
[KeyFoo] cycle section, its $var declaration in [Constants], and every
if/elif gate elsewhere that reads $var. These tests check that add/edit/
delete keep the three in lockstep, that every ToggleEditError leaves the
document completely untouched (so a caller never has to guess whether a
rejected edit did anything), and — via a corpus-wide dry run — that
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

import os, sys, random

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _corpus import corpus_roots

from core.ini_document import IniDocument, IF, ELIF
from core import ini_condition as ic
from core import toggle_editor as te

FAILS = []


def check(cond, msg):
    print(("PASS  " if cond else "FAIL  ") + msg)
    if not cond:
        FAILS.append(msg)


def doc(text):
    return IniDocument.from_string(text, path="<mem>")


def fails(fn, msg):
    try:
        fn()
        check(False, msg + " (no error raised)")
    except te.ToggleEditError:
        check(True, msg)


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


# ── shared helpers ───────────────────────────────────────────────────────────

def test_helpers():
    d = doc(BASIC)
    sec = d.section("KeySwap")
    check(te.is_cycle_section(sec), "KeySwap recognized as a cycle section")
    check(te.cycle_vars(sec) == {"swapvar": ["0", "1", "2"]},
          f"cycle_vars reads the value list ({te.cycle_vars(sec)})")
    check(te._norm_section_name("Swap") == "KeySwap",
          "_norm_section_name adds the Key prefix")
    check(te._norm_section_name("KeySwap") == "KeySwap",
          "_norm_section_name leaves an existing Key prefix alone")
    toggles = te.list_cycle_toggles(d)
    check(toggles == [("KeySwap", {"swapvar": ["0", "1", "2"]})],
          f"list_cycle_toggles finds the one cycle section ({toggles})")


# ── add_toggle ───────────────────────────────────────────────────────────────

def test_add_happy_path():
    d = doc(BASIC)
    name = te.add_toggle(d, "Extra", "2", "extravar", ["0", "1"])
    check(name == "KeyExtra", f"add_toggle returns the new section name ({name})")
    sec = d.section("KeyExtra")
    check(sec is not None, "new section exists")
    check(te.cycle_vars(sec) == {"extravar": ["0", "1"]},
          "new section declares the cycle var/values")
    const_line = te._constant_line(d, "extravar")
    check(const_line is not None and const_line.text == "global persist $extravar = 0",
          f"Constants gets a declaration defaulting to the first value "
          f"({const_line and const_line.text})")
    reparsed = IniDocument.from_string(d.to_string())
    check(reparsed.section("KeyExtra") is not None,
          "the new section round-trips through from_string")


def test_add_with_explicit_default_and_back():
    d = doc(BASIC)
    te.add_toggle(d, "Extra", "2", "extravar", ["0", "1", "2"],
                  default="1", back_combo="3")
    const_line = te._constant_line(d, "extravar")
    check(const_line.text == "global persist $extravar = 1",
          f"explicit default is used instead of values[0] ({const_line.text})")
    sec = d.section("KeyExtra")
    backs = [l.text for l in sec.lines if l.text.lower().startswith("back")]
    check(backs == ["back = 3"], f"back combo written ({backs})")


def test_add_creates_constants_section_when_missing():
    d = doc("""[KeySwap]
key = 1
type = cycle
$swapvar = 0,1
""")
    te.add_toggle(d, "Extra", "2", "extravar", ["0", "1"])
    check(d.section("Constants") is not None,
          "Constants section created from scratch")
    check(te._constant_line(d, "extravar") is not None,
          "the new var is declared in the freshly-created Constants section")


def test_add_validation_errors():
    d = doc(BASIC)
    fails(lambda: te.add_toggle(d, "Swap", "2", "newvar", ["0", "1"]),
          "duplicate section name rejected")
    fails(lambda: te.add_toggle(d, "Extra", "2", "swapvar", ["0", "1"]),
          "duplicate/existing var name rejected")
    fails(lambda: te.add_toggle(d, "Extra", "2", "extravar", ["0"]),
          "fewer than two values rejected")
    fails(lambda: te.add_toggle(d, "Extra", "2", "extravar", ["0", ""]),
          "blank value rejected")
    fails(lambda: te.add_toggle(d, "Extra", "2", "extravar", ["0", "0"]),
          "duplicate values rejected")
    fails(lambda: te.add_toggle(d, "Extra", "", "extravar", ["0", "1"]),
          "missing key binding rejected")
    fails(lambda: te.add_toggle(d, "Extra", "2", r"Master\swapvar", ["0", "1"]),
          "namespaced var rejected")
    check(d.section("KeyExtra") is None,
          "none of the failed add_toggle calls left a partial section behind")
    check("KeyExtra" not in d.to_string(),
          "no partial trace of the rejected adds leaked into the document")


def test_add_insertion_point_after_trailing_blank_lines():
    """Regression: inserting must land after the section's last non-blank
    line, not after trailing blanks (which would butt against the next
    section header with no separator)."""
    d = doc("""[Constants]
global persist $swapvar = 0


[KeySwap]
key = 1
type = cycle
$swapvar = 0,1
""")
    te.add_toggle(d, "Extra", "2", "extravar", ["0", "1"])
    lines = d.to_string().splitlines()
    idx = next(i for i, l in enumerate(lines) if "extravar" in l)
    check(lines[idx - 1].strip() == "global persist $swapvar = 0",
          f"new declaration is inserted right after the last real Constants "
          f"line, not after the trailing blanks ({lines[idx - 1]!r})")


# ── add_toggle: condition line / on-screen-detection plumbing ──────────────

def test_add_condition_builds_active_plumbing_from_scratch():
    d = doc(BASIC)
    te.add_toggle(d, "Extra", "2", "extravar", ["0", "1"])

    sec = d.section("KeyExtra")
    check(sec.lines[0].text == "condition = $active == 1",
          f"condition line is the new section's first line ({sec.lines[0].text!r})")

    const_line = te._constant_line(d, "active")
    check(const_line is not None and const_line.text == "global $active = 0",
          f"$active declared without persist ({const_line and const_line.text!r})")

    present = d.section("Present")
    check(present is not None, "[Present] section created from scratch")
    non_blank = [l.text for l in present.lines if l.text]
    check(non_blank == ["post $active = 0"],
          f"post $active = 0 is the new [Present] section's only content line ({non_blank})")

    body_sec = d.section("TextureOverrideBody")
    texts = [l.text for l in body_sec.lines]
    check(texts[0] == "hash = abc123" and texts[1] == "$active = 1",
          f"$active = 1 planted right after the leading hash line, before the "
          f"if-block ({texts[:2]})")

    reparsed = IniDocument.from_string(d.to_string())
    check(reparsed.section("Present") is not None and reparsed.section("KeyExtra") is not None,
          "the new plumbing round-trips through from_string")


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
    check(sec.lines[0].text == "condition = $object_detected == 1",
          f"reuses the existing $object_detected var ({sec.lines[0].text!r})")
    check(d.section("Present") is None,
          "no [Present] section is created when a detection var already exists")
    check(te._constant_line(d, "active") is None,
          "no $active is declared when $object_detected already exists")
    body_sec = d.section("TextureOverrideBody")
    non_blank = [l.text for l in body_sec.lines if l.text]
    check(non_blank == ["hash = abc123", "drawindexed = 100,0,0"],
          f"the TextureOverride section is left untouched ({non_blank})")


def test_add_condition_reuses_existing_active():
    fixture = BASIC.replace("global persist $swapvar = 0",
                             "global persist $swapvar = 0\nglobal $active = 0")
    d = doc(fixture)
    te.add_toggle(d, "Extra", "2", "extravar", ["0", "1"])
    sec = d.section("KeyExtra")
    check(sec.lines[0].text == "condition = $active == 1",
          f"reuses the existing $active var ({sec.lines[0].text!r})")
    check(d.section("Present") is None,
          "no [Present] section is created when $active is already declared")
    actives = [l for l in d.section("Constants").lines if "active" in l.text]
    check(len(actives) == 1, "no duplicate $active declaration added")


def test_add_condition_prefers_object_detected_over_active():
    d = doc("""[Constants]
global $active = 0
global $object_detected = 0

[KeySwap]
key = 1
type = cycle
$swapvar = 0,1,2
""")
    te.add_toggle(d, "Extra", "2", "extravar", ["0", "1"])
    sec = d.section("KeyExtra")
    check(sec.lines[0].text == "condition = $object_detected == 1",
          f"$object_detected wins over $active when both exist ({sec.lines[0].text!r})")


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
    check(first == ["hash = 111", "$active = 1"], f"first override marked ({first})")
    check(second == ["hash = 222", "$active = 1"], f"second override marked ({second})")
    check(third == ["hash = 333"], f"third override left untouched ({third})")


def test_add_condition_idempotent_across_two_calls():
    d = doc(BASIC)
    te.add_toggle(d, "Extra", "2", "extravar", ["0", "1"])
    te.add_toggle(d, "Extra2", "3", "extravar2", ["0", "1"])

    actives = [l for l in d.section("Constants").lines if l.text == "global $active = 0"]
    check(len(actives) == 1, "still only one $active declaration after two add_toggle calls")

    present = d.section("Present")
    resets = [l for l in present.lines if l.text == "post $active = 0"]
    check(len(resets) == 1, "still only one post $active = 0 line after two calls")

    body_sec = d.section("TextureOverrideBody")
    marks = [l for l in body_sec.lines if l.text == "$active = 1"]
    check(len(marks) == 1, "TextureOverrideBody only marked once, not once per add_toggle call")

    sec2 = d.section("KeyExtra2")
    check(sec2.lines[0].text == "condition = $active == 1",
          "the second toggle reuses the $active plumbing the first call created")


def test_add_rejects_reserved_detection_var_names():
    d = doc(BASIC)
    before = d.to_string()
    fails(lambda: te.add_toggle(d, "Extra", "2", "active", ["0", "1"]),
          "'active' rejected as a toggle's own variable name")
    fails(lambda: te.add_toggle(d, "Extra", "2", "object_detected", ["0", "1"]),
          "'object_detected' rejected as a toggle's own variable name")
    check(d.to_string() == before,
          "doc unchanged after the rejected reserved-name adds")


def test_add_condition_appends_into_existing_present_section():
    d = doc(BASIC + "\n[Present]\nrun = CommandListUnrelated\n")
    te.add_toggle(d, "Extra", "2", "extravar", ["0", "1"])
    present_secs = [s for s in d.sections if s.name == "Present"]
    check(len(present_secs) == 1, "no duplicate [Present] section created")
    texts = [l.text for l in present_secs[0].lines if l.text]
    check(texts == ["run = CommandListUnrelated", "post $active = 0"],
          f"post $active = 0 appended after the existing content ({texts})")


# ── edit_toggle ──────────────────────────────────────────────────────────────

def test_edit_rename():
    d = doc(BASIC)
    name = te.edit_toggle(d, "KeySwap", new_name="Renamed")
    check(name == "KeyRenamed", f"edit_toggle returns the new name ({name})")
    check(d.section("KeySwap") is None, "old section name is gone")
    check(d.section("KeyRenamed") is not None, "new section name exists")


def test_edit_rename_collision():
    d = doc(BASIC)
    te.add_toggle(d, "Other", "3", "othervar", ["0", "1"])
    before = d.to_string()
    try:
        te.edit_toggle(d, "KeySwap", new_name="Other")
        check(False, "renaming onto an existing section should raise")
    except te.ToggleEditError:
        check(True, "renaming onto an existing section raises")
    check(d.to_string() == before, "doc unchanged after the rejected rename")


def test_edit_rebind_key_last_wins():
    d = doc("""[Constants]
global persist $swapvar = 0

[KeySwap]
key = 1
key = 2
type = cycle
$swapvar = 0,1
""")
    te.edit_toggle(d, "KeySwap", key_combo="9")
    sec = d.section("KeySwap")
    keys = [l.text for l in sec.lines if l.text.lower().startswith("key")]
    check(keys == ["key = 1", "key = 9"],
          f"only the last key= line is rewritten ({keys})")


def test_edit_add_back_and_reject_empty_key():
    d = doc(BASIC)
    te.edit_toggle(d, "KeySwap", back_combo="9")
    sec = d.section("KeySwap")
    check(any(l.text == "back = 9" for l in sec.lines), "back combo added")

    before = d.to_string()
    try:
        te.edit_toggle(d, "KeySwap", key_combo="   ")
        check(False, "blank key_combo should raise")
    except te.ToggleEditError:
        check(True, "blank key_combo raises")
    check(d.to_string() == before,
          "doc unchanged after the rejected key edit (validated before mutation)")


def test_edit_var_values_no_conflict():
    d = doc(BASIC)
    te.edit_toggle(d, "KeySwap", var_values={"swapvar": ["0", "1", "2", "3"]})
    check(te.cycle_vars(d.section("KeySwap")) == {"swapvar": ["0", "1", "2", "3"]},
          "cycle values updated")


def test_edit_var_values_conflict_raises_then_can_be_overridden():
    d = doc(BASIC)
    before = d.to_string()
    try:
        te.edit_toggle(d, "KeySwap", var_values={"swapvar": ["0", "1"]})
        check(False, "shrinking away a live gated value should raise")
    except te.ToggleEditError as e:
        check("2" in str(e), f"conflict error names the orphaned value ({e})")
    check(d.to_string() == before, "doc unchanged after the rejected shrink")

    te.edit_toggle(d, "KeySwap", var_values={"swapvar": ["0", "1"]},
                   allow_value_conflicts=True)
    check(te.cycle_vars(d.section("KeySwap")) == {"swapvar": ["0", "1"]},
          "override allows the shrink to go through")


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
    check(te._constant_line(removed_default, "swapvar").text == "global persist $swapvar = 0",
          f"default falls back to the new first value when its old default "
          f"was removed ({te._constant_line(removed_default, 'swapvar').text})")

    kept_default = doc("""[Constants]
global persist $swapvar = 2

[KeySwap]
key = 1
type = cycle
$swapvar = 0,1,2
""")
    te.edit_toggle(kept_default, "KeySwap", var_values={"swapvar": ["2", "3", "4"]})
    check(te._constant_line(kept_default, "swapvar").text == "global persist $swapvar = 2",
          "default left untouched when it's still a valid value after the edit")


def test_edit_combined_rename_and_invalid_var_leaves_doc_untouched():
    """Regression: all fields must validate before any mutation — a failing
    var_values check must not leave a rename half-applied."""
    d = doc(BASIC)
    before = d.to_string()
    try:
        te.edit_toggle(d, "KeySwap", new_name="Renamed",
                       var_values={"nope": ["1", "2"]})
        check(False, "combined edit with an invalid var should raise")
    except te.ToggleEditError:
        check(True, "combined edit with an invalid var raises")
    check(d.to_string() == before,
          "doc fully unchanged: the rename was not partially applied")
    check(d.section("KeySwap") is not None, "section still has its original name")
    check(d.section("Renamed") is None, "renamed section was not created")


def test_edit_nonexistent_section_or_var_raises():
    d = doc(BASIC)
    try:
        te.edit_toggle(d, "KeyNope", key_combo="1")
        check(False, "editing a nonexistent section should raise")
    except te.ToggleEditError:
        check(True, "editing a nonexistent section raises")
    try:
        te.edit_toggle(d, "KeySwap", var_values={"nope": ["0", "1"]})
        check(False, "editing a var the section doesn't cycle should raise")
    except te.ToggleEditError:
        check(True, "editing a nonexistent var raises")


# ── delete_toggle ────────────────────────────────────────────────────────────

def test_delete_removes_section_and_constant_and_rewrites_gates():
    d = doc(BASIC)
    report = te.delete_toggle(d, "KeySwap")
    check(report["section"] == "KeySwap", "report names the deleted section")
    check(report["vars_removed"] == ["swapvar"], "report names the removed var")
    check(d.section("KeySwap") is None, "Key section removed")
    check(te._constant_line(d, "swapvar") is None, "Constants declaration removed")
    check("swapvar" not in d.to_string(),
          "no reference to the deleted var remains anywhere")
    check(report["gates_rewritten"] == 3, f"all three gates rewritten ({report})")
    check(report["always_false_gates"] == [],
          "no gate was negated to permanently false")
    check(report["unsafe_gates"] == [], "no unsafe gates reported")


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
    check(report["vars_removed"] == ["swapvar"],
          f"only swapvar removed, othervar still driven by KeyOther "
          f"({report['vars_removed']})")
    check(te._constant_line(d, "othervar") is not None,
          "othervar's Constants line survives")
    check(d.section("KeyOther") is not None, "KeyOther section untouched")
    gate = next(l for l in d.lines if l.kind in (IF, ELIF))
    check(gate.text == "if $othervar == 0",
          f"gate keeps the surviving var and drops the dead one ({gate.text})")


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
    check(gate.text == r"if $\Master\swapvar == 0",
          f"the namespaced var survives, only the local var's clause is "
          f"eliminated ({gate.text})")


def test_delete_reports_always_false_gate():
    d = doc("""[Constants]
global persist $swapvar = 0

[KeySwap]
key = 1
type = cycle
$swapvar = 0,1

[TextureOverrideBody]
hash = abc
if !$swapvar
drawindexed = 100,0,0
else
drawindexed = 200,0,0
endif
""")
    report = te.delete_toggle(d, "KeySwap")
    check(len(report["always_false_gates"]) == 1,
          f"the negated gate is flagged as permanently false ({report})")
    gate = next(l for l in d.lines if l.kind == IF)
    check(gate.text == "if 0", f"negated gate rewritten to the literal 0 ({gate.text})")


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
    check(d.structure_errors() != [], "fixture really is structurally ambiguous")
    report = te.delete_toggle(d, "KeySwap")
    check(len(report["unsafe_gates"]) == 1,
          f"the ambiguous gate is reported as unsafe, not rewritten ({report})")
    check(report["gates_rewritten"] == 0, "the unsafe gate was not counted as rewritten")
    gate = next(l for l in d.lines if l.kind == IF)
    check("swapvar" in gate.text, "the unsafe gate is left completely untouched")


def test_delete_nonexistent_section_raises():
    d = doc(BASIC)
    try:
        te.delete_toggle(d, "KeyNope")
        check(False, "deleting a nonexistent section should raise")
    except te.ToggleEditError:
        check(True, "deleting a nonexistent section raises")


# ── corpus-wide dry run ──────────────────────────────────────────────────────
#
# Simulates delete_toggle against every real cycle toggle in a sample of real
# mod inis, never writing to disk. This is what caught the eliminate() /
# arithmetic bug described in the module docstring: a synthetic fixture would
# never have produced `cursor_x < $img_x + $norm_width`, but the real corpus
# did on the very first large sample.

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


def _line_parses(line):
    split = te._split_condition_line(line)
    if not split:
        return False
    try:
        ic.parse(split[1])
        return True
    except ic.ConditionError:
        return False


def test_real_mods_delete_toggle():
    from core.ini_parser import find_inis

    mods = _find_mods(300)
    if not mods:
        print("SKIP  no local mod libraries found")
        return

    total_inis = total_toggles = 0
    exceptions = []
    bad_leftover_ref = []
    newly_unparseable = []
    agg_rewritten = agg_false = agg_unsafe = 0

    for mod in mods:
        for path in find_inis(mod):
            total_inis += 1
            try:
                base_text = open(path, "rb").read().decode("utf-8")
                base_doc = IniDocument.from_string(base_text, path=path)
            except Exception:
                continue

            toggles = te.list_cycle_toggles(base_doc)
            if not toggles:
                continue

            for section_name, cvars in toggles:
                total_toggles += 1
                sec_before = te.find_cycle_section(base_doc, section_name)
                # Parseability of every if/elif line *before* any edit that
                # isn't inside the Key section itself (which is about to be
                # deleted wholesale, so it can't regress). delete_toggle only
                # ever rewrites an if/elif line 1-for-1 in place or deletes
                # whole lines elsewhere (Key section, Constants declarations)
                # — never inserts/removes a *gate* line — so this list and
                # the post-delete list below correspond 1:1 in file order,
                # even though absolute line numbers shift. Comparing by
                # position (not raw line number) is what lets a regression
                # (parsed before, doesn't after) be told apart from
                # pre-existing, unrelated corpus malformations (documented at
                # ~10-in-223,973 conditions in test_ini_condition; not
                # something delete_toggle can be blamed for or fix).
                before_gates = [(l.no, l.text, _line_parses(l))
                                for l in base_doc.lines if l.kind in (IF, ELIF)
                                and not (sec_before.start <= l.no < sec_before.end)]

                d = IniDocument.from_string(base_text, path=path)
                try:
                    report = te.delete_toggle(d, section_name)
                except Exception as e:
                    exceptions.append((path, section_name, repr(e)))
                    continue

                agg_rewritten += report["gates_rewritten"]
                agg_false += len(report["always_false_gates"])
                agg_unsafe += len(report["unsafe_gates"])

                try:
                    d2 = IniDocument.from_string(d.to_string(), path=path)
                except Exception as e:
                    exceptions.append((path, section_name, "reparse: " + repr(e)))
                    continue

                after_gates = [(l.no, l.text) for l in d2.lines
                               if l.kind in (IF, ELIF)]
                if len(before_gates) != len(after_gates):
                    exceptions.append((path, section_name,
                                       f"gate count changed: "
                                       f"{len(before_gates)} -> {len(after_gates)}"))
                    continue
                for (_, before_text, was_ok), (after_no, after_text) in \
                        zip(before_gates, after_gates):
                    if was_ok and not _line_parses(d2.lines[after_no]):
                        newly_unparseable.append(
                            (path, section_name, after_no, before_text, after_text))

                # is_safe_to_rewrite (and therefore unsafe_gates) operates on
                # the whole section by name, not a single line — so a gate is
                # only exempt from the leftover-reference check if its whole
                # section was reported unsafe, regardless of exactly which
                # line number that ended up at after later deletions shifted
                # everything past the removed Constants line(s).
                unsafe_sections = {name.lower() for name, _ in report["unsafe_gates"]
                                   if name}
                for line in d2.lines:
                    if line.kind not in (IF, ELIF):
                        continue
                    sec_name = line.section.name if line.section else None
                    if sec_name and sec_name.lower() in unsafe_sections:
                        continue
                    split = te._split_condition_line(line)
                    if not split:
                        continue
                    expr = split[1]
                    for var in report["vars_removed"]:
                        if ic.references(expr, var):
                            bad_leftover_ref.append(
                                (path, section_name, var, line.no, line.text))

    print(f"      {len(mods)} mods, {total_inis} ini files, "
          f"{total_toggles} cycle toggles deleted")
    print(f"      aggregate: rewritten={agg_rewritten} "
          f"always_false={agg_false} unsafe={agg_unsafe}")
    check(total_toggles > 0, "real mods produced cycle toggles to test")
    check(exceptions == [],
          f"delete_toggle never raises or hangs on a real file "
          f"(first: {exceptions[:3]})")
    check(bad_leftover_ref == [],
          f"no removed var survives in a rewritten gate outside "
          f"unsafe_gates (first: {bad_leftover_ref[:3]})")
    check(newly_unparseable == [],
          f"no condition that parsed before deleting stops parsing after "
          f"(first: {newly_unparseable[:3]})")


if __name__ == "__main__":
    for fn in (test_helpers,
               test_add_happy_path, test_add_with_explicit_default_and_back,
               test_add_creates_constants_section_when_missing,
               test_add_validation_errors,
               test_add_insertion_point_after_trailing_blank_lines,
               test_add_condition_builds_active_plumbing_from_scratch,
               test_add_condition_reuses_existing_object_detected,
               test_add_condition_reuses_existing_active,
               test_add_condition_prefers_object_detected_over_active,
               test_add_condition_marks_first_and_second_override_only,
               test_add_condition_idempotent_across_two_calls,
               test_add_rejects_reserved_detection_var_names,
               test_add_condition_appends_into_existing_present_section,
               test_edit_rename, test_edit_rename_collision,
               test_edit_rebind_key_last_wins,
               test_edit_add_back_and_reject_empty_key,
               test_edit_var_values_no_conflict,
               test_edit_var_values_conflict_raises_then_can_be_overridden,
               test_edit_var_values_fixes_up_stale_default,
               test_edit_combined_rename_and_invalid_var_leaves_doc_untouched,
               test_edit_nonexistent_section_or_var_raises,
               test_delete_removes_section_and_constant_and_rewrites_gates,
               test_delete_multi_var_section_shared_var_untouched,
               test_delete_namespaced_var_untouched,
               test_delete_reports_always_false_gate,
               test_delete_reports_unsafe_gate,
               test_delete_nonexistent_section_raises,
               test_real_mods_delete_toggle):
        fn()
    print("\n" + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"))
    sys.exit(1 if FAILS else 0)

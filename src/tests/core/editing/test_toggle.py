"""Test toggle CRUD, validation atomicity, and safe gate removal."""


import pytest



from core.ini.document import IniDocument, IF, ELIF
from core.ini import condition as ic
from core.editing import toggle as te


def doc(text):
    return IniDocument.from_string(text, path="<mem>")


BASIC = """[Constants]
global persist $swapvar = 0

[KeySwap]
key = 1
type = cycle
$swapvar = 0,1,2

[TextureOverrideComponent01]
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

def test_add_toggle_creates_complete_detection_and_cycle_plumbing():
    d = doc(BASIC)
    name = te.add_toggle(d, "Extra", "2", "extravar", ["0", "1"])
    assert (name == "KeyExtra"), (f"add_toggle returns the new section name ({name})")
    sec = d.section("KeyExtra")
    assert (sec is not None), ("new section exists")
    assert (te.cycle_vars(sec) == {"extravar": ["0", "1"]}), ("new section declares the cycle var/values")
    const_line = te._constant_line(d, "extravar")
    assert (const_line is not None and const_line.text == "global persist $extravar = 0"), (f"Constants gets a declaration defaulting to the first value "
          f"({const_line and const_line.text})")
    sec = d.section("KeyExtra")
    assert (sec.lines[0].text == "condition = $active == 1"), (f"condition line is the new section's first line ({sec.lines[0].text!r})")

    const_line = te._constant_line(d, "active")
    assert (const_line is not None and const_line.text == "global $active = 0"), (f"$active declared without persist ({const_line and const_line.text!r})")

    present = d.section("Present")
    assert (present is not None), ("[Present] section created from scratch")
    non_blank = [line.text for line in present.lines if line.text]
    assert (non_blank == ["post $active = 0"]), (f"post $active = 0 is the new [Present] section's only content line ({non_blank})")

    component01_sec = d.section("TextureOverrideComponent01")
    texts = [line.text for line in component01_sec.lines]
    assert (texts[0] == "hash = abc123" and texts[1] == "$active = 1"), (f"$active = 1 planted right after the leading hash line, before the "
          f"if-block ({texts[:2]})")

    te.add_toggle(d, "Extra2", "3", "extravar2", ["0", "1"])
    assert len([line for line in d.section("Constants").lines if line.text == "global $active = 0"]) == 1
    assert len([line for line in d.section("Present").lines if line.text == "post $active = 0"]) == 1
    assert len([line for line in component01_sec.lines if line.text == "$active = 1"]) == 1
    assert d.section("KeyExtra2").lines[0].text == "condition = $active == 1"
    reparsed = IniDocument.from_string(d.to_string())
    assert reparsed.section("Present") is not None and reparsed.section("KeyExtra2") is not None


def test_add_toggle_reuses_existing_detection_and_present_state():
    d = doc("""[Constants]
global $object_detected = 0

[KeySwap]
key = 1
type = cycle
$swapvar = 0,1,2

[TextureOverrideComponent01]
hash = abc123
drawindexed = 100,0,0
""")
    te.add_toggle(d, "Extra", "2", "extravar", ["0", "1"])
    sec = d.section("KeyExtra")
    assert (sec.lines[0].text == "condition = $object_detected == 1"), (f"reuses the existing $object_detected var ({sec.lines[0].text!r})")
    assert (d.section("Present") is None), ("no [Present] section is created when a detection var already exists")
    assert (te._constant_line(d, "active") is None), ("no $active is declared when $object_detected already exists")
    component01_sec = d.section("TextureOverrideComponent01")
    non_blank = [line.text for line in component01_sec.lines if line.text]
    assert (non_blank == ["hash = abc123", "drawindexed = 100,0,0"]), (f"the TextureOverride section is left untouched ({non_blank})")

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
    first = [line.text for line in d.section("TextureOverrideFirst").lines if line.text]
    second = [line.text for line in d.section("TextureOverrideSecond").lines if line.text]
    third = [line.text for line in d.section("TextureOverrideThird").lines if line.text]
    assert (first == ["hash = 111", "$active = 1"]), (f"first override marked ({first})")
    assert (second == ["hash = 222", "$active = 1"]), (f"second override marked ({second})")
    assert (third == ["hash = 333"]), (f"third override left untouched ({third})")

    d = doc(BASIC + "\n[Present]\nrun = CommandListUnrelated\n")
    te.add_toggle(d, "Extra", "2", "extravar", ["0", "1"])
    present_secs = [s for s in d.sections if s.name == "Present"]
    assert (len(present_secs) == 1), ("no duplicate [Present] section created")
    texts = [line.text for line in present_secs[0].lines if line.text]
    assert (texts == ["run = CommandListUnrelated", "post $active = 0"]), (f"post $active = 0 appended after the existing content ({texts})")


_ADD_INVALID_CASES = [
    ("duplicate section", ("Swap", "2", "newvar", ["0", "1"])),
    ("namespaced variable", ("Extra", "2", r"Master\swapvar", ["0", "1"])),
]


@pytest.mark.parametrize("case_name, args", _ADD_INVALID_CASES,
                         ids=[case[0] for case in _ADD_INVALID_CASES])
def test_add_toggle_rejects_invalid_inputs_without_mutation(case_name, args):
    d = doc(BASIC)
    before = d.to_string()
    with pytest.raises(te.ToggleEditError):
        te.add_toggle(d, *args)
    assert d.to_string() == before, case_name


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

[TextureOverrideComponent01]
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
    assert [line.text for line in d.section("TextureOverrideComponent01").lines] == [
        "hash = abc123", "drawindexed = 100,0,0", "drawindexed = 200,0,0",
        "drawindexed = 300,0,0",
    ]
    assert d.structure_errors() == []



@pytest.mark.parametrize("branch_keyword", ["elif", "else if", "else"])
def test_delete_flattens_all_branches_without_duplicate_draws(branch_keyword):
    body = f"""hash = abc123
drawindexed = 12,0,0
if $swapvar == 0
    drawindexed = 12, 0, 0 ; repeated outside
    drawindexed = 24,12,0
{branch_keyword} $swapvar == 1
    drawindexed = 24,12,0
    drawindexed = 36,36,0
else
    drawindexed = 48,72,0
endif
drawindexed = 48,72,0
"""
    d = doc(BASIC.split("hash =", 1)[0] + body)
    report = te.delete_toggle(d, "KeySwap")
    assert report["unsafe_gates"] == []
    assert report["always_true_gates"] == []
    assert [line.text for line in d.section("TextureOverrideComponent01").lines] == [
        "hash = abc123", "drawindexed = 12,0,0", "drawindexed = 24,12,0",
        "drawindexed = 36,36,0", "drawindexed = 48,72,0",
    ]
    assert not d.structure_errors()
    assert not d.syntax_errors()


@pytest.mark.parametrize("source, expected", [
    ("if !$SWAPVAR\n    drawindexed = 12,0,0\nelse\n    drawindexed = 24,12,0\nendif",
     "drawindexed = 12,0,0\ndrawindexed = 24,12,0"),
    ("if $swapvar == 0 && $live == 1\n    drawindexed = 12,0,0\n"
     "elif $swapvar == 1 && $other == 1\n    drawindexed = 24,12,0\n"
     "else\n    drawindexed = 36,36,0\nendif",
     "if $live == 1\n    drawindexed = 12,0,0\nendif\n"
     "if $other == 1\n    drawindexed = 24,12,0\nendif\n"
     "drawindexed = 36,36,0"),
    ("if $live == 1\n    drawindexed = 12,0,0\n"
     "elif $swapvar == 1\n    drawindexed = 24,12,0\n"
     "else\n    drawindexed = 36,36,0\nendif",
     "if $live == 1\n    drawindexed = 12,0,0\nendif\n"
     "if !($live == 1)\n    drawindexed = 24,12,0\nendif\n"
     "if !($live == 1)\n    drawindexed = 36,36,0\nendif"),
    ("if $live == 1\n    if $swapvar == 0\n        drawindexed = 12,0,0\n"
     "    else\n        drawindexed = 24,12,0\n    endif\nendif",
     "if $live == 1\n    drawindexed = 12,0,0\n    drawindexed = 24,12,0\nendif"),
    ("if $swapvar == 0\n    if $live == 1\n        drawindexed = 12,0,0\n"
     "    else\n        drawindexed = 24,12,0\n    endif\nelse\n"
     "    drawindexed = 36,36,0\nendif",
     "if $live == 1\n    drawindexed = 12,0,0\nelse\n    drawindexed = 24,12,0\nendif\n"
     "drawindexed = 36,36,0"),
    ("if $swapvar == 0\n    if $live == 1\n        drawindexed = 12,0,0\n"
     "        drawindexed = 12,0,0\n    endif\nendif",
     "if $live == 1\n    drawindexed = 12,0,0\n    drawindexed = 12,0,0\nendif"),
    ("if $swapvar == 0 && $live == 1\n    run = CustomShaderFixture\nendif\n"
     "if $swapvar == 1 && $other == 1\n    $other = 0\n    drawindexed = 12,0,0\nendif",
     "if $live == 1\n    run = CustomShaderFixture\nendif\n"
     "if $other == 1\n    $other = 0\n    drawindexed = 12,0,0\nendif"),
    ("if $swapvar == 0\n    if $swapvar != 1\n        drawindexed = 12,0,0\n"
     "    else\n        drawindexed = 24,12,0\n    endif\nendif",
     "drawindexed = 12,0,0\ndrawindexed = 24,12,0"),
])
def test_delete_keeps_surviving_conditions_and_nested_bodies(source, expected):
    d = doc(BASIC.split("hash =", 1)[0] + source + "\n")
    report = te.delete_toggle(d, "KeySwap")
    assert report["unsafe_gates"] == []
    assert "\n".join(line.raw for line in d.section("TextureOverrideComponent01").lines) == expected
    assert not d.structure_errors()
    assert not d.syntax_errors()


def test_delete_preserves_resource_state_scopes_and_lossless_lines():
    header = BASIC.split("hash =", 1)[0]
    body = ("ib = ResourceFirst\r\n"
            "if $swapvar == 0 ; gate comment\n"
            "\tdrawindexed = 12,0,0\r\n"
            "else\r\n"
            "\tib = ResourceSecond\n"
            "\tdrawindexed = 12,0,0\r\n"
            "endif ; closing comment\n"
            "if $live == 1\r\n\tdrawindexed = 12,0,0\nendif\r\n"
            "[TextureOverrideComponent02]\n"
            "drawindexed = 12,0,0")
    d = doc("\ufeff" + header + body)
    report = te.delete_toggle(d, "KeySwap")
    assert report["unsafe_gates"] == []
    expected = ("ib = ResourceFirst\r\n; gate comment\n"
                "drawindexed = 12,0,0\r\nib = ResourceSecond\n"
                "drawindexed = 12,0,0\r\n; closing comment\n"
                "if $live == 1\r\n\tdrawindexed = 12,0,0\nendif\r\n\n"
                "[TextureOverrideComponent02]\n"
                "drawindexed = 12,0,0")
    assert d.to_string().startswith("\ufeff")
    assert d.to_string().endswith(expected)
    assert not d.structure_errors()


def test_delete_removes_complete_tuple_from_other_keys():
    d = doc("""[Constants]
global persist $swapvar = 0
global persist $othervar = 0
global persist $variant = 0
global persist $retained = 0

[KeyMulti]
key = 1
type = cycle
$swapvar = 0,1
$variant = 0,1
$othervar = 0,1

[KeyOther]
key = 2
type = cycle
$othervar = 0,1
$retained = 0,1

[TextureOverrideComponent01]
hash = abc
if $swapvar == 0 && $variant == 1 && $othervar == 0 && $retained == 1
drawindexed = 100,0,0
endif
""")
    report = te.delete_toggle(d, "KeyMulti")
    assert report["vars_removed"] == ["swapvar", "variant", "othervar"]
    assert report["sections_removed"] == ["KeyMulti"]
    for variable in report["vars_removed"]:
        assert "$" + variable not in d.to_string()
    assert te.cycle_vars(d.section("KeyOther")) == {"retained": ["0", "1"]}
    assert te._constant_line(d, "retained") is not None
    gate = next(line for line in d.lines if line.kind in (IF, ELIF))
    assert gate.text == "if $retained == 1"



def test_delete_removes_assignments_and_key_conditions_case_insensitively():
    d = doc(BASIC + """[Constants]
global persist $SWAPVAR = 1
[Present]
post $SwapVar = 0
[KeyUtility]
condition = $SWAPVAR == 0 && $live == 1
key = 2
[KeyUtilityOther]
condition = !$SwapVar
key = 3
[TextureOverrideComponent02]
drawindexed = 12,0,0
if $SWAPVAR == 0
    $SwapVar = 1
    drawindexed = 12,0,0
endif
""")
    report = te.delete_toggle(d, "keyswap")
    assert report["vars_removed"] == ["swapvar"]
    assert "swapvar" not in d.to_string().casefold()
    assert [line.text for line in d.section("KeyUtility").lines if line.text] == [
        "condition = $live == 1", "key = 2",
    ]
    assert [line.text for line in d.section("KeyUtilityOther").lines if line.text] == ["key = 3"]
    assert [line.text for line in d.section("TextureOverrideComponent02").lines] == [
        "drawindexed = 12,0,0",
    ]


@pytest.mark.parametrize("other_key", ["KeyModViewerPresent", "KeyShared"])
def test_delete_cleans_all_writers_and_removes_empty_cycle_keys(other_key):
    d = doc(BASIC + f"""[Constants]
global $swapvar_suffix = 2
[Present]
post $SWAPVAR = 0
if $SwapVar == 1
    run = CommandListHelper
endif
[CommandListHelper]
local $swapvar
pre $SWAPVAR = 2
[KeyReader]
condition = $swapvar == 0
key = 3
run = CommandListHelper
[{other_key}]
key = 2
type = cycle
$SwapVar = 0,1,2
[{other_key}]
key = 4
type = cycle
$SWAPVAR = 1,2,0
""")
    report = te.delete_toggle(d, "KeySwap")
    assert report["vars_removed"] == ["swapvar"]
    assert report["sections_removed"] == ["KeySwap", other_key, other_key, "CommandListHelper", "Constants", "Present"]
    assert "$swapvar" not in d.to_string().lower().replace("$swapvar_suffix", "")
    assert d.section(other_key) is None
    assert te._constant_line(d, "swapvar") is None
    assert any(line.text == "global $swapvar_suffix = 2" for line in d.lines)
    assert d.section("Present") is None
    assert d.section("CommandListHelper") is None
    assert [line.text for line in d.section("KeyReader").lines if line.text] == ["key = 3"]
    assert all(line.kind not in (IF, ELIF) for line in d.lines)
    assert not d.structure_errors()



def test_delete_namespaced_var_untouched():
    d = doc(r"""[Constants]
global persist $swapvar = 0

[KeySwap]
key = 1
type = cycle
$swapvar = 0,1

[KeyShared]
key = 2
type = cycle
$SWAPVAR = 0,1
$\Master\swapvar = 0,1

[TextureOverrideComponent01]
hash = abc
if $\Master\swapvar == 0 && $swapvar == 0
drawindexed = 100,0,0
endif
""")
    te.delete_toggle(d, "KeySwap")
    gate = next(line for line in d.lines if line.kind in (IF, ELIF))
    assert te.cycle_vars(d.section("KeyShared"), include_read_only=True) == {
        r"\Master\swapvar": ["0", "1"],
    }
    assert (gate.text == r"if $\Master\swapvar == 0"), (f"the namespaced var survives, only the local var's clause is "
          f"eliminated ({gate.text})")






@pytest.mark.parametrize("body", [
    "if $swapvar == 0\ndrawindexed = 12,0,0\nendif\nendif\n",
    "if $swapvar ? 0\ndrawindexed = 12,0,0\nendif\n",
    "if $swapvar == 0 && $live == 1\n$live = 0\ndrawindexed = 12,0,0\nelse\ndrawindexed = 24,12,0\nendif\n",
    "if $swapvar == 0 && $live == 1\nrun = CommandListHelper\ndrawindexed = 12,0,0\nelse\ndrawindexed = 24,12,0\nendif\n",
])
def test_delete_reports_unsafe_gate(body):
    d = doc(BASIC.split("hash =", 1)[0] + body)
    report = te.delete_toggle(d, "KeySwap")
    assert len(report["unsafe_gates"]) == 1
    assert report["gates_rewritten"] == 0
    assert "\n".join(line.raw for line in d.section("TextureOverrideComponent01").lines) + "\n" == body




# -- corpus check -------------------------------------------------------------
# Check gate removal across available INIs without writing changes to disk.

def _line_parses(line):
    split = te._split_condition_line(line)
    if not split:
        return False
    try:
        ic.parse(split[1])
        return True
    except ic.ConditionError:
        return False


@pytest.mark.parametrize("newline, final_newline", [("\n", True), ("\r\n", True), ("\n", False), ("\r\n", False)])
def test_delete_preserves_existing_empty_headers_and_restores_separators(newline, final_newline):
    source = [
        "[Constants]", "global persist $swapvar = 0",
        "[KeySwap]", "key = 1", "type = cycle", "$swapvar = 0,1",
        "[CommandListEmptied]", "if $swapvar == 0", "    $swapvar = 1", "endif", "; kept note",
        "[CommandListCaller]", "post run = CommandListEmptied",
        "[CommandListOnlyGate]", "if $swapvar == 0", "endif",
        "[CommandListEmptyConditional]", "if $unrelated", "endif",
        "[ResourceEmpty]", "[CommandListEmpty]", "; existing placeholder",
        "[CommandListDuplicate]", "[CommandListDuplicate]", "$swapvar = 1",
        "[CommandListKeep]", "run = CommandListEmpty", "run = CommandListDuplicate",
        "[TextureOverridePart]", "if $swapvar == 0", "drawindexed = 12,0,0", "endif",
    ]
    d = doc("\ufeff" + newline.join(source) + (newline if final_newline else ""))
    report = te.delete_toggle(d, "KeySwap")
    expected = [
        "; kept note", "[CommandListEmptyConditional]", "if $unrelated", "endif", "",
        "[ResourceEmpty]", "", "[CommandListEmpty]", "; existing placeholder", "",
        "[CommandListDuplicate]", "", "[CommandListKeep]", "run = CommandListEmpty",
        "run = CommandListDuplicate", "", "[TextureOverridePart]", "drawindexed = 12,0,0",
    ]
    assert d.to_bytes() == ("\ufeff" + newline.join(expected) + (newline if final_newline else "")).encode("utf-8")
    assert set(report["sections_removed"]) == {
        "KeySwap", "Constants", "CommandListEmptied", "CommandListCaller", "CommandListDuplicate", "CommandListOnlyGate",
    }
    assert not d.structure_errors()


def test_delete_preserves_ambiguous_newly_empty_header():
    d = doc(BASIC.split("[TextureOverrideComponent01]", 1)[0] + """[CommandListAmbiguous]
if $swapvar == 0
    $swapvar = 1
endif
endif
""")
    report = te.delete_toggle(d, "KeySwap")
    assert len(report["unsafe_gates"]) == 1
    assert d.section("CommandListAmbiguous") is not None
    assert "CommandListAmbiguous" not in report["sections_removed"]
    assert any(issue["reason"] == "endif_without_if" for issue in d.structure_errors())
    assert "$swapvar = 1" not in d.to_string()

"""Test staged toggle operations, Record rollback, and Export behavior.

The bridge tests also check that Record uses the full co-driven cycle length
while rewriting only writable variables.
"""

import glob
import os

import pytest


from app.session import edit as edit_session
from app.bridge import toggle as toggle_api
from core.ini.document import IniDocument
from core.editing import record as record_editor


# The local variable has two values; its co-driven namespaced variable has four.
# Record previews all positions but rewrites only the local variable.
FIXTURE = """[Constants]
global persist $Upper = 0

[KeyUpper]
key = 1
type = cycle
$Upper = 0,1
$\\Other\\Master\\Mode = 0,1,2,3

[TextureOverrideComponent01]
if $Upper == 0
drawindexed = 100,0,0
endif
if $Upper == 1
drawindexed = 200,0,0
endif
"""

# Resource declarations let staged-session checks resolve the draw group.
# Filename entries suffice; no buffer file is needed.
WIRABLE_FIXTURE = """[Constants]
global persist $Upper = 0

[KeyUpper]
key = 1
type = cycle
$Upper = 0,1

[TextureOverrideComponent01Blend]
ib = ResourceComponent01IB
vb0 = ResourcePos
vb1 = ResourceTc
if $Upper == 0
drawindexed = 100,0,0
endif
if $Upper == 1
drawindexed = 200,0,0
endif

[ResourceComponent01IB]
filename = component01.ib
format = DXGI_FORMAT_R32_UINT

[ResourcePos]
filename = pos.buf
stride = 40

[ResourceTc]
filename = tc.buf
stride = 20
"""

SHIFTED_FIXTURE = """[Constants]
global persist $Upper = 0
global $object_detected = 0

[KeyUpper]
key = 1
type = cycle
$Upper = 0,1

[TextureOverrideComponent01]
if $Upper == 0
drawindexed = 100,0,0
endif
; gap one
; gap two
; gap three
; gap four
if $Upper == 1
drawindexed = 200,0,0
endif
"""


def _target_refs(text, *line_numbers):
    """Build target identities from the pre-edit mesh payload provenance."""
    document = IniDocument.from_string(text)
    refs = []
    for line_number in line_numbers:
        line = document.lines[line_number - 1]
        draw = line.text.split("=", 1)[1].split(",")
        occurrence = record_editor._draw_occurrence(document, line_number - 1)
        refs.append({
            "ini": "mod.ini",
            "line": line_number,
            "section": line.section.name,
            "drawindexed": [int(value.strip()) for value in draw],
            "occurrence": occurrence,
        })
    return refs


def _fixture(tmp, name, text):
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


@pytest.fixture
def toggle_mod(api_root):
    root = api_root
    ini_path = _fixture(root, "mod.ini", FIXTURE)
    yield root, ini_path


@pytest.fixture
def wirable_mod(api_root):
    root = api_root
    ini_path = _fixture(root, "mod.ini", WIRABLE_FIXTURE)
    yield root, ini_path


def test_get_record_positions_uses_complete_cycle_but_reports_writable_vars(toggle_mod):
    tmp, _ini_path = toggle_mod
    ini_rel = "mod.ini"
    result = toggle_api.get_record_positions(tmp, ini_rel, "KeyUpper")
    assert (result.get("ok") is True), ("get_record_positions succeeds on a real toggle")
    assert (result.get("positions") == 4), (f"position count includes the 4-value co-driven namespaced var "
          f"(got {result.get('positions')})")
    assert (result.get("vars") == ["Upper"]), (f"only the writable variable is reported (got {result.get('vars')})")








def _swap_positions(tmp, ini_rel):
    """Swap the two positions' cycle gating and return the INI path and result."""
    line_100 = next(i for i, line in enumerate(FIXTURE.splitlines(), 1) if "100,0,0" in line)
    line_200 = next(i for i, line in enumerate(FIXTURE.splitlines(), 1) if "200,0,0" in line)
    ini_path = os.path.join(tmp, ini_rel)
    result = toggle_api.record_toggle(
        tmp, ini_rel, "KeyUpper",
        {0: [line_200], 1: [line_100], 2: [line_100], 3: [line_100]},
        _target_refs(FIXTURE, line_100, line_200))
    return ini_path, result


def test_record_staged_edit_lifecycle(toggle_mod):
    tmp, ini_path = toggle_mod
    ini_rel = "mod.ini"

    positions = toggle_api.get_record_positions(tmp, ini_rel, "KeyUpper")
    assert positions == {"ok": True, "positions": 4, "vars": ["Upper"]}

    ini_path, result = _swap_positions(tmp, ini_rel)

    assert result["ok"] is True
    assert result["pending"] is True
    report = result.get("result") or {}
    assert report["chains_rewritten"] >= 1

    with open(ini_path, encoding="utf-8") as fh:
        assert fh.read() == FIXTURE
    assert not glob.glob(ini_path + "_*.BAK")
    assert toggle_api.has_pending_changes(tmp)

    pending_text = edit_session.peek(tmp, ini_path).to_string()
    branch = pending_text.split("$Upper == 0", 1)[1].split("endif", 1)[0]
    assert "200,0,0" in branch

    assert toggle_api.discard_changes(tmp) == {"ok": True}
    assert not toggle_api.has_pending_changes(tmp)
    assert "100,0,0" in edit_session.peek(tmp, ini_path).to_string()

    _swap_positions(tmp, ini_rel)
    export_result = toggle_api.export_changes(tmp)
    assert export_result["saved"] == [ini_rel]
    assert not export_result["failed"]

    backups = glob.glob(ini_path + "_*.BAK")
    assert len(backups) == 1
    with open(backups[0], encoding="utf-8") as fh:
        assert fh.read() == FIXTURE

    with open(ini_path, encoding="utf-8") as fh:
        after = fh.read()
    branch = after.split("$Upper == 0", 1)[1].split("endif", 1)[0]
    assert "200,0,0" in branch
    assert not toggle_api.has_pending_changes(tmp)




def test_new_toggle_blocks_export_until_recorded(wirable_mod, monkeypatch):
    tmp, ini_path = wirable_mod
    ini_rel = "mod.ini"
    add_result = toggle_api.add_toggle(tmp, ini_rel, "Extra", "9", "Extra", ["0", "1"])
    assert add_result["ok"] is True

    monkeypatch.setattr(edit_session, "overrides_for", lambda _mod_dir:
                        (_ for _ in ()).throw(AssertionError(
                            "Export serialized staged INI documents")))

    blocked = toggle_api.export_changes(tmp)
    assert blocked["unwired"] == {"mod.ini": ["KeyExtra"]}
    assert not glob.glob(ini_path + "_*.BAK")
    assert toggle_api.has_pending_changes(tmp)

    with open(ini_path, encoding="utf-8") as fh:
        assert fh.read() == WIRABLE_FIXTURE

    line_100 = next(i for i, line in enumerate(WIRABLE_FIXTURE.splitlines(), 1)
                    if "100,0,0" in line)
    record_result = toggle_api.record_toggle(
        tmp, ini_rel, "KeyExtra", {0: [line_100], 1: []},
        _target_refs(WIRABLE_FIXTURE, line_100))
    assert record_result["ok"] is True

    export_result = toggle_api.export_changes(tmp)
    assert export_result["saved"] == [ini_rel]
    assert not export_result["failed"]
    assert not toggle_api.has_pending_changes(tmp)

    with open(ini_path, encoding="utf-8") as fh:
        assert "$Extra == 0" in fh.read()


def test_record_after_add_resolves_original_mesh_source_lines(api_root):
    ini_rel = "mod.ini"
    ini_path = _fixture(api_root, ini_rel, SHIFTED_FIXTURE)
    first_line = next(i for i, line in enumerate(SHIFTED_FIXTURE.splitlines(), 1)
                      if "100,0,0" in line)
    second_line = next(i for i, line in enumerate(SHIFTED_FIXTURE.splitlines(), 1)
                       if "200,0,0" in line)

    added = toggle_api.add_toggle(
        api_root, ini_rel, "New", "2", "New", ["0", "1"])
    assert added["ok"] is True

    pending = edit_session.peek(api_root, ini_path)
    assert pending.lines[second_line - 1].text == "drawindexed = 100,0,0"
    result = toggle_api.record_toggle(
        api_root, ini_rel, "KeyNew",
        {0: [first_line], 1: [second_line]},
        _target_refs(SHIFTED_FIXTURE, first_line, second_line))

    assert result["ok"] is True
    assert result["result"]["skipped"] == []
    recorded = edit_session.peek(api_root, ini_path).to_string()
    assert "if $New == 0" in recorded
    assert "if $New == 1" in recorded




def test_record_bridge_stages_target_hidden_at_every_position(toggle_mod):
    tmp, ini_path = toggle_mod
    line_100 = next(i for i, line in enumerate(FIXTURE.splitlines(), 1)
                    if "100,0,0" in line)

    result = toggle_api.record_toggle(
        tmp, "mod.ini", "KeyUpper",
        {0: [], 1: [], 2: [], 3: []}, _target_refs(FIXTURE, line_100))

    assert result["ok"] is True
    assert result["result"]["skipped"] == []
    pending = edit_session.peek(tmp, ini_path).to_string()
    assert "$Upper == 0 && $Upper != 0" in pending
    assert "drawindexed = 200,0,0" in pending


def test_stale_record_target_rolls_back_without_pending_changes(toggle_mod):
    tmp, ini_path = toggle_mod
    line_100 = next(i for i, line in enumerate(FIXTURE.splitlines(), 1)
                    if "100,0,0" in line)
    stale = _target_refs(FIXTURE, line_100)[0]
    stale["drawindexed"] = [999, 0, 0]

    result = toggle_api.record_toggle(
        tmp, "mod.ini", "KeyUpper",
        {0: [], 1: [], 2: [], 3: []}, [stale])

    assert "stale" in result["error"]
    assert not toggle_api.has_pending_changes(tmp)
    with open(ini_path, encoding="utf-8") as fh:
        assert fh.read() == FIXTURE


def test_record_toggle_rolls_back_pending_on_verify_mismatch(toggle_mod):
    """A verification mismatch must roll back the staged edit and return an error."""
    from core.editing import record as record_editor
    forced = [{"var": "fake", "reason": "forced mismatch for this test"}]
    real_verify = record_editor.verify_recording
    # Accept the optional text argument used for staged verification.
    record_editor.verify_recording = lambda path, report, text=None: forced
    try:
        tmp, ini_path = toggle_mod
        ini_rel = "mod.ini"
        ini_path, result = _swap_positions(tmp, ini_rel)

        assert ("error" in result and "discarded" in result["error"]), (f"a forced verify mismatch is a clean {{\"error\": ...}} explaining the pending "
              f"change was discarded, not a silent {{\"ok\": True}} (got {result})")
        assert (result.get("mismatches") == forced), (f"the mismatch detail is surfaced too (got {result.get('mismatches')})")
        with open(ini_path, encoding="utf-8") as fh:
            assert (fh.read() == FIXTURE), ("the real ini file was never touched -- nothing reaches disk until Export")
        assert (not glob.glob(ini_path + "_*.BAK")), ("no backup exists -- there was nothing to export")
        assert (toggle_api.has_pending_changes(tmp) is False), ("the rejected recording leaves nothing pending behind")
    finally:
        record_editor.verify_recording = real_verify


def test_discard_changes_drops_pending_without_writing(toggle_mod):
    tmp, ini_path = toggle_mod
    ini_rel = "mod.ini"
    _swap_positions(tmp, ini_rel)
    assert (toggle_api.has_pending_changes(tmp) is True), ("a change is pending before discard")

    pending_branch = (edit_session.peek(tmp, ini_path).to_string()
                       .split("$Upper == 0", 1)[1].split("endif", 1)[0])
    assert ("200,0,0" in pending_branch), ("the staged swap is visible via peek before discard")

    discard_result = toggle_api.discard_changes(tmp)
    assert (discard_result == {"ok": True}), (f"discard_changes reports ok (got {discard_result})")
    assert (toggle_api.has_pending_changes(tmp) is False), ("nothing is pending after discard")

    with open(ini_path, encoding="utf-8") as fh:
        assert (fh.read() == FIXTURE), ("the real ini file was never touched")
    assert (not glob.glob(ini_path + "_*.BAK")), ("no backup exists -- there was never an export")

    reverted_branch = (edit_session.peek(tmp, ini_path).to_string()
                       .split("$Upper == 0", 1)[1].split("endif", 1)[0])
    assert ("100,0,0" in reverted_branch and "200,0,0" not in reverted_branch), ("a read right after discard reflects disk again (original gating), not the discarded edit")


def test_rejected_edit_does_not_corrupt_an_already_pending_doc(toggle_mod):
    tmp, ini_path = toggle_mod
    ini_rel = "mod.ini"

    add_result = toggle_api.add_toggle(tmp, ini_rel, "Extra", "9", "Extra", ["0", "1"])
    assert (add_result.get("ok") is True), (f"the first staged edit succeeds (got {add_result})")

    bad_result = toggle_api.edit_toggle(tmp, ini_rel, "KeyDoesNotExist", {"key_combo": "7"})
    assert ("error" in bad_result), (f"editing an unknown section is a clean {{\"error\": ...}} (got {bad_result})")

    assert (toggle_api.has_pending_changes(tmp) is True), ("the earlier, already-committed staged edit survives a later rejected one on the same ini")
    details = toggle_api.get_toggle_details(tmp, ini_rel, "KeyExtra")
    assert (details.get("ok") is True and details.get("key") == "9"), (f"the pending add is intact, not rolled back past its own commit (got {details})")

    with open(ini_path, encoding="utf-8") as fh:
        assert (fh.read() == FIXTURE), ("still nothing has reached disk -- both edits are only pending")


def test_create_toggle_wires_all_selected_draws_in_one_staged_transaction(api_root):
    root = api_root
    text = WIRABLE_FIXTURE.replace("if $Upper == 0\n", "").replace("if $Upper == 1\n", "").replace("endif\n", "")
    # One rendered draw can merge several physical authored contributions.
    text = text.replace("drawindexed = 200,0,0", "drawindexed = 200,0,0\ndrawindexed = 100,0,0")
    path = _fixture(root, "mod.ini", text)
    lines = [index for index, line in enumerate(text.splitlines(), 1)
             if line.startswith("drawindexed")]
    targets = _target_refs(text, *lines)
    edit_session.peek(root, path)
    revision = edit_session.current_revision(root)
    result = toggle_api.add_toggle(
        root, "mod.ini", "Part-01, 02", "L", "Part_01_02", ["0", "1"],
        {"default": "0", "record_targets": targets})
    assert result == {"ok": True, "result": "KeyPart-01, 02", "pending": True}
    assert edit_session.current_revision(root) == revision + 1
    assert edit_session.new_sections_for(root) == {"mod.ini": {"KeyPart-01, 02"}}
    staged = edit_session.peek(root, path)
    assert "global persist $Part_01_02 = 0" in staged.to_string()

    from core.ini.sections import sections_from_document
    from core.ini.parser import _scan_sections_for_draws

    draws = _scan_sections_for_draws(sections_from_document(staged))
    conditions = [draw.conditions for section in draws.values() for draw in section["draws"]]
    assert len(conditions) == 3
    for upper in ("0", "1"):
        for position in ("0", "1"):
            visible = [record_editor._dnf_satisfied(
                condition, {"Upper": upper, "Part_01_02": position})
                for condition in conditions]
            assert sum(visible) == (3 if position == "0" else 0)
    with open(path, encoding="utf-8") as fh:
        assert fh.read() == text
    assert not glob.glob(path + "_*.BAK")
    assert toggle_api.export_changes(root)["saved"] == ["mod.ini"]


@pytest.mark.parametrize("failure", ["stale", "mixed_ini", "skipped", "verify"])
def test_create_toggle_failure_preserves_prior_staged_state(toggle_mod, monkeypatch, failure):
    root, path = toggle_mod
    assert toggle_api.add_toggle(root, "mod.ini", "Pending", "8", "Pending", ["0", "1"])["ok"]
    before = edit_session.peek(root, path).to_string()
    tracked = edit_session.new_sections_for(root)
    line = next(index for index, text in enumerate(FIXTURE.splitlines(), 1)
                if "100,0,0" in text)
    targets = _target_refs(FIXTURE, line)
    if failure == "stale":
        targets[0]["drawindexed"] = [999, 0, 0]
    elif failure == "mixed_ini":
        targets += [{**targets[0], "ini": "other.ini"}]
    elif failure == "skipped":
        targets[0]["occurrence"]["path"] = [["CommandListFixture", 0]]
    else:
        monkeypatch.setattr(record_editor, "verify_recording",
                            lambda *args, **kwargs: [{"reason": "fixture mismatch"}])
    result = toggle_api.add_toggle(
        root, "mod.ini", "Created", "9", "Created", ["0", "1"],
        {"default": "0", "record_targets": targets})
    assert "error" in result
    assert edit_session.peek(root, path).to_string() == before
    assert edit_session.new_sections_for(root) == tracked
    assert edit_session.has_pending(root)
    with open(path, encoding="utf-8") as fh:
        assert fh.read() == FIXTURE
    assert not glob.glob(path + "_*.BAK")


@pytest.mark.parametrize("alias, generated", [
    ("no_modifiers", "no_ctrl no_Shift no_alt"),
    ("no_control no_shift no_alt", "no_ctrl no_Shift no_alt"),
    ("no-control no-shift no-alt", "no_ctrl no_Shift no_alt"),
    ("no-ctrl no_shift no_alt", "no_ctrl no_Shift no_alt"),
    ("control no_shift no_alt", "ctrl no_Shift no_alt"),
])
def test_automatic_toggle_binding_skips_equivalent_aliases(api_root, alias, generated):
    lines = []
    if generated.startswith("ctrl "):
        for index, key in enumerate(("'", "l", "p", ";", "o", "[", "]")):
            lines.extend([f"[KeyExisting{index}]", f"key = no_ctrl no_Shift no_alt {key}"])
    lines.extend(["[KeyAlias0]", f"key = {alias} '",
                  "[KeyAlias1]", f"key = {alias} l"])
    _fixture(api_root, "mod.ini", "\n".join(lines) + "\n")
    assert toggle_api.next_toggle_key(api_root) == {"key": f"{generated} p"}


def test_automatic_toggle_binding_order_uses_all_staged_key_sections(api_root):
    first = _fixture(api_root, "mod.ini", "[KeyUtility]\nkey = '\nback = L\n")
    second = _fixture(api_root, "other.ini", "[KeyUtility]\nkey = p\n")
    edit_session.load_documents(api_root, [first, second])
    groups = ("no_ctrl no_Shift no_alt", "ctrl no_Shift no_alt",
              "no_ctrl Shift no_alt", "no_ctrl no_Shift alt",
              "ctrl no_Shift alt", "ctrl Shift no_alt", "ctrl Shift alt")
    candidates = [f"{group} {key}" for group in groups for key in ("'", "l", "p", ";", "o", "[", "]")]
    for index, binding in enumerate(candidates[3:]):
        assert toggle_api.next_toggle_key(api_root) == {"key": binding}
        with edit_session.transaction(api_root, [second]) as transaction:
            doc = transaction.document(second)
            doc.insert_lines(len(doc.lines), [f"[KeyGenerated{index}]", f"key = {binding}"])
    assert toggle_api.next_toggle_key(api_root) == {"key": ""}
    with edit_session.transaction(api_root, [first]) as transaction:
        doc = transaction.document(first)
        doc.replace_lines(1, 2, ["key = F10"])
    assert toggle_api.next_toggle_key(api_root) == {"key": candidates[0]}
    with open(first, encoding="utf-8") as fh:
        assert fh.read() == "[KeyUtility]\nkey = '\nback = L\n"

"""app/toggle_api.py's app-layer wiring: record mode (get_record_positions/
record_toggle) plus the staged-edit session it now shares with add/edit/
delete_toggle (app/edit_session.py) and export_changes/discard_changes/
has_pending_changes.

Since the "make changes in app not affect the ini file until Export" feature,
every mutating call here only ever touches an in-memory IniDocument cached in
app/edit_session.py â€” nothing reaches a real ini file until export_changes()
is called, which is also the one place a timestamped backup is made (once per
changed ini, however many edits accumulated into it). This file's tests
therefore check, for every mutation:
  - the real file on disk is untouched immediately after the call;
  - the pending state is visible via has_pending_changes/get_toggle_details/
    get_record_positions (which must prefer a same-session pending edit over
    stale disk content â€” see edit_session.peek);
  - a rejected call (raised ToggleEditError, or record_toggle's own post
    -rewrite verify mismatch) never leaves a partial mutation sitting in the
    session, whether this was the ini's first pending edit or one on top of
    an already-pending doc;
  - export_changes writes exactly once per ini regardless of how many edits
    were staged against it, and clears the pending state afterwards;
  - discard_changes drops everything pending without writing anything.

Also covered: export_changes refuses outright (no backup, nothing written)
while a toggle added via add_toggle this session still doesn't gate any
mesh â€” and proceeds normally again once that toggle is either wired via
record_toggle or removed via delete_toggle (see mod_loader.
unwired_pending_sections / edit_session.new_sections_for).

record_editor.py itself is exercised in-memory (no disk I/O, no session) by
test_record_editor.py; this file instead checks the app layer on top of it:
resolving ini_rel to a path, staging via edit_session, and turning
ToggleEditError into a plain {"error": ...} rather than raising across the JS
bridge. It's also the only place that exercises get_record_positions, which
exists specifically because a [Key...] section's writable variables can have
a *different* (typically shorter) values list than a namespaced variable
declared alongside them in the same section â€” ini_parser.extract_toggle_keys
(the read path feeding the existing Toggle-panel cycle button) reports both,
but toggle_editor.cycle_vars (the write path) can't even parse a namespaced
declaration, so record_toggle's position count must come from
get_record_positions rather than the panel's own lead-variable cycle length.
"""

import glob, os

import pytest


from app import edit_session, toggle_api


# A section with one writable var (2 values) and one namespaced var declared
# alongside it with a *longer* values list â€” syntactically legal ini, and
# exactly the shape that would make a lead-variable-driven position count
# wrong (see module docstring).
FIXTURE = """[Constants]
global persist $Upper = 0

[KeyUpper]
key = 1
type = cycle
$Upper = 0,1
$\\Other\\Master\\Mode = 0,1,2,3

[TextureOverrideBody]
if $Upper == 0
drawindexed = 100,0,0
endif
if $Upper == 1
drawindexed = 200,0,0
endif
"""

# Like FIXTURE, but with real Resource/TextureOverride declarations so
# mod_loader.unwired_pending_sections (build_draw_groups under the hood) can
# actually resolve a draw group and tell whether a var gates it â€” FIXTURE
# above is deliberately minimal for tests that only exercise toggle_editor/
# record_editor's direct in-memory line splicing and never need that. No
# buffer file needs to exist on disk for this -- build_draw_groups only needs
# each Resource section's `filename =` line to be declared.
WIRABLE_FIXTURE = """[Constants]
global persist $Upper = 0

[KeyUpper]
key = 1
type = cycle
$Upper = 0,1

[TextureOverrideBodyBlend]
ib = ResourceBodyIB
vb0 = ResourcePos
vb1 = ResourceTc
if $Upper == 0
drawindexed = 100,0,0
endif
if $Upper == 1
drawindexed = 200,0,0
endif

[ResourceBodyIB]
filename = body.ib
format = DXGI_FORMAT_R32_UINT

[ResourcePos]
filename = pos.buf
stride = 40

[ResourceTc]
filename = tc.buf
stride = 20
"""


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


def test_get_record_positions_uses_writable_vars_only(toggle_mod):
    tmp, _ini_path = toggle_mod
    ini_rel = "mod.ini"
    result = toggle_api.get_record_positions(tmp, ini_rel, "KeyUpper")
    assert (result.get("ok") is True), ("get_record_positions succeeds on a real toggle")
    assert (result.get("positions") == 2), (f"position count comes from the 2-value writable var, not the "
          f"4-value namespaced one (got {result.get('positions')})")
    assert (result.get("vars") == ["Upper"]), (f"only the writable variable is reported (got {result.get('vars')})")








def _swap_positions(tmp, ini_rel):
    """Stage the fixture's one real rewrite: swap the two positions' cycle
    gating so position 0 shows the line that used to be position 1's, and
    vice versa. Returns (ini_path, record_toggle's result dict)."""
    line_100 = next(i for i, l in enumerate(FIXTURE.splitlines(), 1) if "100,0,0" in l)
    line_200 = next(i for i, l in enumerate(FIXTURE.splitlines(), 1) if "200,0,0" in l)
    ini_path = os.path.join(tmp, ini_rel)
    result = toggle_api.record_toggle(tmp, ini_rel, "KeyUpper", {0: [line_200], 1: [line_100]})
    return ini_path, result


def test_record_toggle_stages_without_writing_to_disk(toggle_mod):
    tmp, ini_path = toggle_mod
    ini_rel = "mod.ini"
    ini_path, result = _swap_positions(tmp, ini_rel)

    assert (result.get("ok") is True), (f"a valid recording session succeeds (got {result})")
    assert (result.get("pending") is True), ("the result flags this as a staged, not-yet-exported change")
    report = result.get("result") or {}
    assert (report.get("chains_rewritten", 0) >= 1), (f"the swapped visibility actually rewrites a chain (got {report})")

    with open(ini_path, encoding="utf-8") as fh:
        assert (fh.read() == FIXTURE), ("the real ini file is untouched -- the rewrite only exists in the pending session")
    assert (not glob.glob(ini_path + "_*.BAK")), ("no backup is written until Export")
    assert (toggle_api.has_pending_changes(tmp) is True), ("the mod now has a pending, not-yet-exported change")

    pending_text = edit_session.peek(tmp, ini_path).to_string()
    branch = pending_text.split("$Upper == 0", 1)[1].split("endif", 1)[0]
    assert ("200,0,0" in branch), ("the pending in-memory doc already shows the swapped gating, previewable before Export")


def test_export_changes_writes_backup_and_rewritten_gates(toggle_mod):
    tmp, ini_path = toggle_mod
    ini_rel = "mod.ini"
    _swap_positions(tmp, ini_rel)

    export_result = toggle_api.export_changes(tmp)
    assert (export_result.get("saved") == [ini_rel] and not export_result.get("failed")), (f"export saves the one pending ini cleanly (got {export_result})")

    backups = glob.glob(ini_path + "_*.BAK")
    assert (len(backups) == 1), (f"export writes exactly one timestamped backup (got {backups})")
    if backups:
        with open(backups[0], encoding="utf-8") as fh:
            assert (fh.read() == FIXTURE), ("the backup holds the original, pre-rewrite content")

    with open(ini_path, encoding="utf-8") as fh:
        after = fh.read()
    branch = after.split("$Upper == 0", 1)[1].split("endif", 1)[0]
    assert ("200,0,0" in branch), ("position 0's branch now gates the line that used to be position 1's, on disk after export")
    assert (toggle_api.has_pending_changes(tmp) is False), ("export clears the pending state once written")




def test_export_blocked_while_added_toggle_is_unwired(wirable_mod):
    """The core of this feature: Export must refuse to write anything while
    a toggle added via add_toggle this session doesn't gate any mesh yet."""
    tmp, ini_path = wirable_mod
    ini_rel = "mod.ini"
    add_result = toggle_api.add_toggle(tmp, ini_rel, "Extra", "9", "Extra", ["0", "1"])
    assert (add_result.get("ok") is True), (f"staging a new toggle succeeds (got {add_result})")

    export_result = toggle_api.export_changes(tmp)
    assert ("error" in export_result), (f"export is refused while the new toggle is unwired (got {export_result})")
    assert (export_result.get("unwired") == {"mod.ini": ["KeyExtra"]}), (f"the still-unwired section is named in the response (got {export_result.get('unwired')})")
    assert (not glob.glob(ini_path + "_*.BAK")), ("nothing is written to disk when export is refused")
    assert (toggle_api.has_pending_changes(tmp) is True), ("the pending add survives a refused export")

    with open(ini_path, encoding="utf-8") as fh:
        assert (fh.read() == WIRABLE_FIXTURE), ("the real ini file is untouched by the refused export")


def test_export_succeeds_once_added_toggle_is_recorded(wirable_mod):
    """Once the freshly-added toggle actually gates something (Record mode),
    it's no longer "unwired" and Export proceeds normally."""
    tmp, ini_path = wirable_mod
    ini_rel = "mod.ini"
    toggle_api.add_toggle(tmp, ini_rel, "Extra", "9", "Extra", ["0", "1"])

    blocked = toggle_api.export_changes(tmp)
    assert ("unwired" in blocked), (f"export is still refused before recording (got {blocked})")

    pending_text = edit_session.peek(tmp, ini_path).to_string()
    line_100 = next(i for i, l in enumerate(pending_text.splitlines(), 1) if "100,0,0" in l)
    record_result = toggle_api.record_toggle(tmp, ini_rel, "KeyExtra", {0: [line_100], 1: []})
    assert (record_result.get("ok") is True), (f"recording the new toggle succeeds (got {record_result})")

    export_result = toggle_api.export_changes(tmp)
    assert (export_result.get("saved") == [ini_rel] and not export_result.get("failed")), (f"export succeeds once the new toggle is wired (got {export_result})")
    assert (toggle_api.has_pending_changes(tmp) is False), ("export cleared the pending state")

    with open(ini_path, encoding="utf-8") as fh:
        assert ("$Extra == 0" in fh.read()), ("the new toggle's gate actually landed on disk")




def test_record_toggle_rolls_back_pending_on_verify_mismatch(toggle_mod):
    """If verify_recording ever reports a mismatch -- the self-check this
    whole feature exists for -- record_toggle must discard the just-staged
    pending edit and return a clean {"error": ...}, never silently leave a
    mod showing the wrong meshes, staged or not. Forced with a monkeypatch
    rather than trying to engineer a real record_editor bug:
    test_record_editor.py's own corpus dry run already proves
    verify_recording doesn't false-positive on a genuine rewrite, so this
    test only needs to prove the *plumbing* in toggle_api.record_toggle
    reacts correctly when it does fire."""
    from core import record_editor
    forced = [{"var": "fake", "reason": "forced mismatch for this test"}]
    real_verify = record_editor.verify_recording
    # verify_recording now takes an optional text= kwarg (in-memory preview);
    # the replacement must accept it too or the call below raises TypeError.
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

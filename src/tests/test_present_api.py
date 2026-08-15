"""One logical PRESENT cycle staged atomically across eligible INIs."""

import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import edit_session, metadata, present_api
from app.mod_loader import _parse_inis
from core import present_editor
from core.present_editor import MAX_PRESENTS, SECTION_NAME


FAILS = []


def check(condition, message):
    print(("PASS  " if condition else "FAIL  ") + message)
    if not condition:
        FAILS.append(message)


INI = """[Constants]
global persist $Hat = 0
global persist $Coat = 1

[KeyHat]
condition = ($object_detected) && $mode == 2
key = h
type = cycle
$Hat = 0,1

[KeyCoat]
condition = ($object_detected) && $mode == 2
key = c
type = cycle
$Coat = 0,1,2

[TextureOverrideBodyPosition]
vb0 = ResourceBodyPosition
[TextureOverrideBodyTexcoord]
vb1 = ResourceBodyTexcoord
[TextureOverrideBody]
ib = ResourceBodyIB
drawindexed = 3,0,0
[ResourceBodyPosition]
filename = p.buf
stride = 12
[ResourceBodyTexcoord]
filename = t.buf
stride = 8
[ResourceBodyIB]
filename = i.buf
format = R32_UINT
"""


def snapshots(a_hat="0", a_coat="0", b_hat="1", b_coat="1"):
    return {
        "a.ini": {"a::Hat": a_hat, "a::Coat": a_coat},
        "b.ini": {"b::Hat": b_hat, "b::Coat": b_coat},
    }


def test_present_lifecycle():
    with tempfile.TemporaryDirectory() as folder:
        paths = []
        for name in ("a.ini", "b.ini"):
            path = os.path.join(folder, name)
            with open(path, "w", encoding="utf-8") as stream:
                stream.write(INI)
            paths.append(path)
        edit_session.discard(folder)
        edit_session.load_documents(folder, paths)

        _groups, _toggles, _menu, _defaults, _rules, before = _parse_inis(
            paths, folder, edit_session.overrides_for(folder))
        check(before.get("item") is None and
              [target["value"] for target in before["target_inis"]] == ["a.ini", "b.ini"],
              "one Add action targets every eligible INI")

        added = present_api.add_present(folder, "p", "shift p", snapshots())
        check(added.get("ok") and added["result"] == {"count": 1, "files": 2},
              "Add creates one synchronized PRESENT position across both INIs")
        a_text = edit_session.peek(folder, paths[0]).to_string()
        b_text = edit_session.peek(folder, paths[1]).to_string()
        check(f"[{SECTION_NAME}]" in a_text and f"[{SECTION_NAME}]" in b_text,
              "each eligible INI receives its own reserved key section")
        check("$Hat = 0" in a_text and "$Coat = 0" in a_text and
              "$Hat = 1" in b_text and "$Coat = 1" in b_text,
              "each section captures only the values supplied for its own INI")
        check(all(open(path, encoding="utf-8").read() == INI for path in paths),
              "batch Add remains memory-only before Export")

        _groups, toggles, _menu, _defaults, _rules, present = _parse_inis(
            paths, folder, edit_session.overrides_for(folder))
        item = present.get("item")
        check(item and item["inis"] == ["a.ini", "b.ini"] and
              len(item["vars"]) == 4 and len(item["capture_vars"]) == 4,
              "the loader combines per-INI sections into one logical PRESENT control")
        check(all(info.get("section") != SECTION_NAME for info in toggles.values()),
              "reserved sections remain excluded from KEY TOGGLE")

        appended = present_api.capture_present(
            folder, snapshots("1", "2", "0", "2"), "Present 2")
        check(appended.get("ok") and appended["result"] == {"count": 2, "files": 2},
              "New appends the same logical position across every section")
        duplicate = present_api.capture_present(folder, snapshots(), "Present 3")
        check(duplicate.get("duplicate_positions") == [0],
              "duplicate warnings compare the complete tuple across all INIs")

        edited = present_api.capture_present(
            folder, snapshots("1", "0", "1", "0"), "Casual", 1)
        check(edited.get("ok"), "Edit replaces the selected position in every INI")
        check(metadata.present_names(folder, metadata.PRESENT_NAMES_KEY) == {"1": "Casual"},
              "one sparse name list describes the logical cross-INI presents")
        model = {"item": {"inis": ["a.ini", "b.ini"], "count": 2}}
        metadata.hydrate_present(folder, model)
        check(model["item"]["names"] == ["Present 1", "Casual"],
              "logical names hydrate independently of the participating files")

        binding = present_api.edit_present(folder, "ctrl p", "")
        check(binding.get("ok") and all(
            "key = ctrl p" in edit_session.peek(folder, path).to_string()
            for path in paths),
              "binding edits fan out to every PRESENT key")

        removed = present_api.delete_present_position(folder, 0)
        check(removed.get("ok") and removed["result"] == {"count": 1, "files": 2},
              "Delete removes one aligned position from every INI")
        check(metadata.present_names(folder, metadata.PRESENT_NAMES_KEY) == {"0": "Casual"},
              "custom names shift with a deleted logical position")
        check("error" in present_api.delete_present_position(folder, 0),
              "the only remaining position cannot be deleted")

        for index in range(1, MAX_PRESENTS):
            result = present_api.capture_present(
                folder, snapshots(str(index % 2), str(index % 3),
                                  str((index + 1) % 2), str((index + 1) % 3)),
                f"Present {index + 1}", allow_duplicate=True)
            check(result.get("ok"), f"logical present {index + 1} is accepted")
        blocked = present_api.capture_present(
            folder, snapshots(), "Present 11", allow_duplicate=True)
        check("at most 10" in blocked.get("error", ""),
              "the ten-present limit is enforced across the batch")

        exported = edit_session.export(folder)
        check(exported.get("saved") == ["a.ini", "b.ini"] and all(
            f"[{SECTION_NAME}]" in open(path, encoding="utf-8").read()
            for path in paths),
              "Export writes every participating staged INI")
        deleted = present_api.delete_present(folder)
        check(deleted.get("ok") and all(
            f"[{SECTION_NAME}]" not in edit_session.peek(folder, path).to_string()
            for path in paths),
              "section Delete stages removal from every participating INI")
        edit_session.discard(folder)


def test_add_is_atomic_when_one_snapshot_is_missing():
    with tempfile.TemporaryDirectory() as folder:
        paths = []
        for name in ("a.ini", "b.ini"):
            path = os.path.join(folder, name)
            with open(path, "w", encoding="utf-8") as stream:
                stream.write(INI)
            paths.append(path)
        edit_session.discard(folder)
        edit_session.load_documents(folder, paths)
        failed = present_api.add_present(
            folder, "p", "", {"a.ini": {"a::Hat": "0", "a::Coat": "0"}})
        check("error" in failed and all(
            f"[{SECTION_NAME}]" not in edit_session.peek(folder, path).to_string()
            for path in paths),
              "a failed multi-INI Add rolls every staged document back")
        edit_session.discard(folder)


def test_partial_present_is_completed_and_mismatches_are_reported():
    with tempfile.TemporaryDirectory() as folder:
        paths = []
        for name in ("a.ini", "b.ini"):
            path = os.path.join(folder, name)
            with open(path, "w", encoding="utf-8") as stream:
                stream.write(INI)
            paths.append(path)
        edit_session.discard(folder)
        edit_session.load_documents(folder, paths)

        sess, key, doc, _was_pending, _snapshot = edit_session.begin(folder, paths[0])
        present_editor.add(doc, "p", "", {"Hat": "0", "Coat": "0"})
        present_editor.capture(
            doc, {"Hat": "1", "Coat": "2"}, allow_duplicate=True)
        edit_session.commit(sess, key, doc)
        metadata.save_present_name(
            folder, metadata.PRESENT_NAMES_KEY, 1, "Alternate")

        _groups, _toggles, _menu, _defaults, _rules, partial = _parse_inis(
            paths, folder, edit_session.overrides_for(folder))
        check(partial["item"]["missing_inis"] == ["b.ini"],
              "a partial logical PRESENT identifies the eligible INIs still missing it")

        completed = present_api.add_present(folder, "ctrl p", "", snapshots())
        details = [present_editor.details(edit_session.peek(folder, path))
                   for path in paths]
        check(completed.get("ok") and all(info["count"] == 2 for info in details),
              "Complete adds the missing section with an aligned position count")
        check(all(info["key"] == "ctrl p" for info in details) and
              details[1]["vars"] == {"Hat": ["1", "1"], "Coat": ["1", "1"]},
              "Complete shares the binding and repeats the missing INI's current snapshot")
        check(metadata.present_names(folder, metadata.PRESENT_NAMES_KEY) ==
              {"1": "Alternate"},
              "completing a partial PRESENT preserves its logical names")

        sess, key, doc, _was_pending, _snapshot = edit_session.begin(folder, paths[0])
        present_editor.capture(
            doc, {"Hat": "0", "Coat": "1"}, allow_duplicate=True)
        edit_session.commit(sess, key, doc)
        _groups, _toggles, _menu, _defaults, _rules, mismatched = _parse_inis(
            paths, folder, edit_session.overrides_for(folder))
        check(mismatched["item"]["count"] == 0 and
              "different position counts" in mismatched["item"]["sync_error"],
              "mismatched existing PRESENT sections load as an explicit error state")
        edit_session.discard(folder)


if __name__ == "__main__":
    test_present_lifecycle()
    test_add_is_atomic_when_one_snapshot_is_missing()
    test_partial_present_is_completed_and_mismatches_are_reported()
    print("\n" + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"))
    raise SystemExit(1 if FAILS else 0)

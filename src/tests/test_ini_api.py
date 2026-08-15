"""Authoritative in-memory INI text API and its Export-only write boundary."""

import glob
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import edit_session, ini_api, toggle_api

FAILS = []


def check(condition, message):
    print(("PASS  " if condition else "FAIL  ") + message)
    if not condition:
        FAILS.append(message)


INI = """[Constants]
global persist $Mode = 0

[KeyMode]
key = 1
type = cycle
$Mode = 0,1
"""


def exercise_session():
    with tempfile.TemporaryDirectory() as folder:
        first = os.path.join(folder, "a.ini")
        second = os.path.join(folder, "b.ini")
        with open(first, "w", encoding="utf-8", newline="") as stream:
            stream.write(INI.replace("\n", "\r\n"))
        with open(second, "w", encoding="utf-8", newline="") as stream:
            stream.write("[ResourceThing]\nfilename = thing.buf\n")

        edit_session.discard(folder)
        edit_session.load_documents(folder, [first, second])
        listed = ini_api.list_inis(folder)
        check([item["value"] for item in listed] == ["a.ini", "b.ini"],
              "all active INIs are listed in stable order")

        original_disk = open(first, "rb").read()
        unchanged = ini_api.update_text(folder, "a.ini", INI)
        check(unchanged.get("changed") is False and not unchanged.get("pending"),
              "applying normalized text is a no-op for a CRLF file")

        edited = INI.replace("key = 1", "key = 7")
        result = ini_api.update_text(folder, "a.ini", edited)
        check(result.get("ok") and result.get("pending"),
              "editor changes become pending in memory")
        check(open(first, "rb").read() == original_disk,
              "editor Apply does not alter the physical INI")

        with open(first, "w", encoding="utf-8", newline="") as stream:
            stream.write(INI.replace("key = 1", "key = 9"))
        edit_session.load_documents(folder, [first, second])
        memory = ini_api.get_text(folder, "a.ini")
        check("key = 7" in memory.get("text", "") and "key = 9" not in memory.get("text", ""),
              "same-mod reload keeps the authoritative memory version")

        toggle = toggle_api.edit_toggle(folder, "a.ini", "KeyMode", {"key_combo": "8"})
        memory = ini_api.get_text(folder, "a.ini")
        check(toggle.get("ok") and "key = 8" in memory.get("text", ""),
              "toggle editing mutates the same memory version")

        check("error" in ini_api.get_text(folder, "..\\a.ini"),
              "browser-supplied paths outside the active list are rejected")

        second_before = open(second, "rb").read()
        exported = edit_session.export(folder)
        check(exported == {"saved": ["a.ini"], "failed": []},
              "Export writes only the dirty in-memory document")
        check(b"key = 8" in open(first, "rb").read(),
              "Export copies final shared memory content to disk")
        check(open(second, "rb").read() == second_before and not glob.glob(second + "_*.BAK"),
              "an unchanged INI is neither written nor backed up")
        check(ini_api.get_text(folder, "a.ini").get("dirty") is False and
              "a.ini" in [item["value"] for item in ini_api.list_inis(folder)],
              "export keeps the memory document loaded and marks it clean")
        edit_session.discard(folder)


def exercise_nested_duplicate_names():
    with tempfile.TemporaryDirectory() as folder:
        first = os.path.join(folder, "one", "mod.ini")
        second = os.path.join(folder, "two", "mod.ini")
        os.makedirs(os.path.dirname(first))
        os.makedirs(os.path.dirname(second))
        with open(first, "w", encoding="utf-8") as stream:
            stream.write(INI.replace("key = 1", "key = 3"))
        with open(second, "w", encoding="utf-8") as stream:
            stream.write(INI.replace("key = 1", "key = 4"))

        edit_session.discard(folder)
        edit_session.load_documents(folder, [first, second])
        listed = [item["value"] for item in ini_api.list_inis(folder)]
        check(listed == ["one/mod.ini", "two/mod.ini"],
              "nested duplicate basenames keep distinct relative identities")
        changed = ini_api.update_text(
            folder, "two/mod.ini", INI.replace("key = 1", "key = 8"))
        check(changed.get("ok") and "key = 3" in ini_api.get_text(
            folder, "one/mod.ini").get("text", ""),
              "editing one nested duplicate does not target the other")
        exported = edit_session.export(folder)
        check(exported == {"saved": ["two/mod.ini"], "failed": []},
              "nested export reports and saves the relative INI identity")
        edit_session.discard(folder)


if __name__ == "__main__":
    exercise_session()
    exercise_nested_duplicate_names()
    if FAILS:
        print(f"\n{len(FAILS)} FAILURE(S)")
        raise SystemExit(1)
    print("\nALL PASS")

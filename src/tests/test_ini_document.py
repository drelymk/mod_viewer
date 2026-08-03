"""Tests for the lossless ini document model.

The round-trip test is the important one: it asserts that loading and saving
5,000+ real mod inis reproduces every file byte-for-byte. That is the guarantee
the whole write-back feature rests on.

    py -3 tests\test_ini_document.py
"""
import glob
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _corpus import corpus_roots
from core.ini_document import (ASSIGN, BLANK, COMMENT, DRAW, ELIF, ELSE, ENDIF, IF,
                          SECTION, IniDocument)

failures = []


def check(name, got, want):
    if got == want:
        print(f"PASS  {name}")
    else:
        print(f"FAIL  {name}\n        got  {got!r}\n        want {want!r}")
        failures.append(name)


def test_roundtrip_string():
    """Every byte survives a load/serialise cycle, including odd endings."""
    cases = {
        "crlf": "[A]\r\nx = 1\r\n",
        "lf": "[A]\nx = 1\n",
        "mixed": "[A]\r\nx = 1\ny = 2\r\n",
        "no trailing newline": "[A]\r\nx = 1",
        "bom": "\ufeff[A]\r\nx = 1\r\n",
        "blank lines + comments": "; header\r\n\r\n[A]\r\n; note\r\nx = 1\r\n\r\n",
        "indentation": "[A]\r\n    if $x == 1\r\n        drawindexed = 1,0,0\r\n    endif\r\n",
        "empty file": "",
        "only newline": "\r\n",
        "semicolon key binding": "[KeyA]\r\nkey = no_ctrl no_Shift no_alt ;\r\n",
    }
    for name, text in cases.items():
        doc = IniDocument.from_string(text)
        check(f"roundtrip {name}", doc.to_string(), text)


def test_line_kinds():
    text = ("; comment\r\n"
            "\r\n"
            "[TextureOverrideBody]\r\n"
            "hash = aabb\r\n"
            "if $x == 0\r\n"
            "drawindexed = 1,0,0\r\n"
            "elif $x == 1\r\n"
            "else if $x == 2\r\n"
            "else\r\n"
            "endif\r\n")
    doc = IniDocument.from_string(text)
    check("line kinds",
          [ln.kind for ln in doc.lines],
          [COMMENT, BLANK, SECTION, ASSIGN, IF, DRAW, ELIF, ELIF, ELSE, ENDIF])


def test_depth():
    text = ("[A]\r\n"
            "if $x == 0\r\n"
            "if $y == 1\r\n"
            "drawindexed = 1,0,0\r\n"
            "endif\r\n"
            "else\r\n"
            "drawindexed = 2,0,0\r\n"
            "endif\r\n")
    doc = IniDocument.from_string(text)
    check("nesting depth",
          [ln.depth for ln in doc.lines],
          [0, 0, 1, 2, 1, 0, 1, 0])


def test_sections():
    text = "[A]\r\nx = 1\r\n\r\n[B]\r\ny = 2\r\n"
    doc = IniDocument.from_string(text)
    check("section names", [s.name for s in doc.sections], ["A", "B"])
    check("section A span", (doc.sections[0].start, doc.sections[0].end), (0, 3))
    check("section B span", (doc.sections[1].start, doc.sections[1].end), (3, 5))
    check("lookup is case-insensitive", doc.section("a").name, "A")
    check("missing section", doc.section("nope"), None)


def test_inline_comment_stripping():
    doc = IniDocument.from_string(
        "[KeyA]\r\nkey = no_ctrl no_Shift no_alt ;\r\nhash = aabb ; trailing\r\n")
    check("';' key binding kept", doc.lines[1].text, "key = no_ctrl no_Shift no_alt ;")
    check("inline comment stripped", doc.lines[2].text, "hash = aabb")
    check("raw untouched by stripping", doc.lines[2].raw, "hash = aabb ; trailing")


def test_edits():
    text = "[A]\r\nx = 1\r\ny = 2\r\nz = 3\r\n"

    doc = IniDocument.from_string(text)
    doc.replace_lines(2, 3, ["y = 99"])
    check("replace same count", doc.to_string(), "[A]\r\nx = 1\r\ny = 99\r\nz = 3\r\n")

    doc = IniDocument.from_string(text)
    doc.insert_lines(2, ["w = 0"])
    check("insert", doc.to_string(), "[A]\r\nx = 1\r\nw = 0\r\ny = 2\r\nz = 3\r\n")

    doc = IniDocument.from_string(text)
    doc.delete_lines(1, 2)
    check("delete", doc.to_string(), "[A]\r\ny = 2\r\nz = 3\r\n")

    doc = IniDocument.from_string(text)
    doc.replace_lines(1, 2, ["a = 1", "b = 2", "c = 3"])
    check("expand", doc.to_string(), "[A]\r\na = 1\r\nb = 2\r\nc = 3\r\ny = 2\r\nz = 3\r\n")

    # An LF file must not acquire CRLF from inserted lines.
    doc = IniDocument.from_string("[A]\nx = 1\n")
    doc.insert_lines(2, ["y = 2"])
    check("inserted line adopts LF", doc.to_string(), "[A]\nx = 1\ny = 2\n")

    # Appending after a terminator-less last line must not fuse the two.
    doc = IniDocument.from_string("[A]\r\nx = 1")
    doc.insert_lines(2, ["y = 2"])
    check("append after bare last line", doc.to_string(), "[A]\r\nx = 1\r\ny = 2")

    doc = IniDocument.from_string(text)
    doc.replace_lines(1, 2, ["if $x == 1", "drawindexed = 1,0,0", "endif"])
    check("edits reindex kinds",
          [ln.kind for ln in doc.lines[1:4]], [IF, DRAW, ENDIF])
    check("edits reindex depth", [ln.depth for ln in doc.lines[1:4]], [0, 1, 0])

    doc = IniDocument.from_string(text)
    for bad in [(-1, 2), (0, 99), (3, 1)]:
        try:
            doc.replace_lines(bad[0], bad[1], [])
            check(f"rejects range {bad}", "no error", "IndexError")
        except IndexError:
            check(f"rejects range {bad}", "IndexError", "IndexError")


def test_save_atomic_and_backup():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "mod.ini")
    original = "[A]\r\nx = 1\r\n"
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(original)

    doc = IniDocument.load(path)
    doc.replace_lines(1, 2, ["x = 2"])
    backup = doc.save()

    with open(path, encoding="utf-8", newline="") as fh:
        check("saved content", fh.read(), "[A]\r\nx = 2\r\n")
    with open(backup, encoding="utf-8", newline="") as fh:
        check("backup holds original", fh.read(), original)
    check("backup named *.BAK", backup.endswith(".BAK"), True)
    check("backup keeps .ini in name", ".ini_" in os.path.basename(backup), True)
    check("no temp file left", os.path.exists(path + ".tmp"), False)

    # A backup must never be picked up as a loadable mod ini.
    from core.ini_parser import find_inis
    check("find_inis ignores backups", find_inis(d), [path])


def test_roundtrip_corpus():
    """The real guarantee: every mod ini on disk survives byte-for-byte."""
    files = []
    for root in corpus_roots():
        if os.path.isdir(root):
            files += glob.glob(os.path.join(root, "**", "*.ini"), recursive=True)
    if not files:
        print("SKIP  corpus roundtrip (no mod libraries found)")
        return

    mismatched, errored = [], []
    for path in files:
        try:
            with open(path, "rb") as fh:
                original = fh.read()
            if IniDocument.load(path).to_bytes() != original:
                mismatched.append(path)
        except Exception as exc:
            errored.append(f"{path}: {type(exc).__name__}: {exc}")

    check(f"corpus roundtrip byte-identical ({len(files)} files)",
          (len(mismatched), len(errored)), (0, 0))
    for p in mismatched[:5]:
        print(f"        mismatch: {p}")
    for e in errored[:5]:
        print(f"        error: {e}")


def test_structure_errors():
    ok = IniDocument.from_string(
        "[A]\r\nif $x == 1\r\ndrawindexed = 1,0,0\r\nelse\r\nendif\r\n")
    check("balanced section has no errors", ok.structure_errors(), [])
    check("balanced section is safe", ok.is_safe_to_rewrite("A"), True)

    # Real pattern from MasterCorinV1.ini: endif closes the block, then an
    # `else if` appears with nothing open.
    orphan = IniDocument.from_string(
        "[A]\r\nif $x == 1\r\nendif\r\nelse if $x == 2\r\nendif\r\n")
    problems = [p["problem"] for p in orphan.structure_errors()]
    check("orphan else-if reported", problems,
          ["elif without an open if", "endif without a matching if"])
    check("orphan section not safe", orphan.is_safe_to_rewrite("A"), False)

    unclosed = IniDocument.from_string("[A]\r\nif $x == 1\r\ndrawindexed = 1,0,0\r\n")
    check("unclosed if reported",
          [p["problem"] for p in unclosed.structure_errors()], ["1 unclosed if"])

    extra = IniDocument.from_string("[A]\r\nif $x == 1\r\nendif\r\nendif\r\n")
    check("extra endif reported",
          [p["problem"] for p in extra.structure_errors()],
          ["endif without a matching if"])

    # A malformed section must not taint a healthy one in the same file.
    mixed = IniDocument.from_string(
        "[Bad]\r\nendif\r\n[Good]\r\nif $x == 1\r\nendif\r\n")
    check("errors are per-section", mixed.is_safe_to_rewrite("Good"), True)
    check("bad section still flagged", mixed.is_safe_to_rewrite("Bad"), False)


def main():
    test_roundtrip_string()
    test_line_kinds()
    test_depth()
    test_sections()
    test_inline_comment_stripping()
    test_edits()
    test_structure_errors()
    test_save_atomic_and_backup()
    test_roundtrip_corpus()
    print(f"\n{'ALL PASS' if not failures else str(len(failures)) + ' FAILURE(S)'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

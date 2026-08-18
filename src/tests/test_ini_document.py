"""Tests for the lossless ini document model.

The round-trip test is the important one: it asserts that loading and saving
5,000+ real mod inis reproduces every file byte-for-byte. That is the guarantee
the whole write-back feature rests on.

    py -3 tests\test_ini_document.py
"""
import os
import tempfile


from _corpus import active_ini_files
from core.ini_document import (ASSIGN, BLANK, COMMENT, DRAW, ELIF, ELSE, ENDIF, IF,
                          SECTION, IniDocument)


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
    assert (doc.to_string()) == (text), (f"roundtrip {name}")


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
    assert ([ln.kind for ln in doc.lines]) == ([COMMENT, BLANK, SECTION, ASSIGN, IF, DRAW, ELIF, ELIF, ELSE, ENDIF]), ("line kinds")


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
    assert ([ln.depth for ln in doc.lines]) == ([0, 0, 1, 2, 1, 0, 1, 0]), ("nesting depth")


def test_sections():
    text = "[A]\r\nx = 1\r\n\r\n[B]\r\ny = 2\r\n"
    doc = IniDocument.from_string(text)
    assert ([s.name for s in doc.sections]) == (["A", "B"]), ("section names")
    assert ((doc.sections[0].start, doc.sections[0].end)) == ((0, 3)), ("section A span")
    assert ((doc.sections[1].start, doc.sections[1].end)) == ((3, 5)), ("section B span")
    assert (doc.section("a").name) == ("A"), ("lookup is case-insensitive")
    assert (doc.section("nope")) == (None), ("missing section")


def test_inline_comment_stripping():
    doc = IniDocument.from_string(
        "[KeyA]\r\nkey = no_ctrl no_Shift no_alt ;\r\nhash = aabb ; trailing\r\n")
    assert (doc.lines[1].text) == ("key = no_ctrl no_Shift no_alt ;"), ("';' key binding kept")
    assert (doc.lines[2].text) == ("hash = aabb"), ("inline comment stripped")
    assert (doc.lines[2].raw) == ("hash = aabb ; trailing"), ("raw untouched by stripping")


def test_edits():
    text = "[A]\r\nx = 1\r\ny = 2\r\nz = 3\r\n"

    doc = IniDocument.from_string(text)
    doc.replace_lines(2, 3, ["y = 99"])
    assert (doc.to_string()) == ("[A]\r\nx = 1\r\ny = 99\r\nz = 3\r\n"), ("replace same count")

    doc = IniDocument.from_string(text)
    doc.insert_lines(2, ["w = 0"])
    assert (doc.to_string()) == ("[A]\r\nx = 1\r\nw = 0\r\ny = 2\r\nz = 3\r\n"), ("insert")

    doc = IniDocument.from_string(text)
    doc.delete_lines(1, 2)
    assert (doc.to_string()) == ("[A]\r\ny = 2\r\nz = 3\r\n"), ("delete")

    doc = IniDocument.from_string(text)
    doc.replace_lines(1, 2, ["a = 1", "b = 2", "c = 3"])
    assert (doc.to_string()) == ("[A]\r\na = 1\r\nb = 2\r\nc = 3\r\ny = 2\r\nz = 3\r\n"), ("expand")

    # An LF file must not acquire CRLF from inserted lines.
    doc = IniDocument.from_string("[A]\nx = 1\n")
    doc.insert_lines(2, ["y = 2"])
    assert (doc.to_string()) == ("[A]\nx = 1\ny = 2\n"), ("inserted line adopts LF")

    # Appending after a terminator-less last line must not fuse the two.
    doc = IniDocument.from_string("[A]\r\nx = 1")
    doc.insert_lines(2, ["y = 2"])
    assert (doc.to_string()) == ("[A]\r\nx = 1\r\ny = 2"), ("append after bare last line")

    doc = IniDocument.from_string(text)
    doc.replace_lines(1, 2, ["if $x == 1", "drawindexed = 1,0,0", "endif"])
    assert ([ln.kind for ln in doc.lines[1:4]]) == ([IF, DRAW, ENDIF]), ("edits reindex kinds")
    assert ([ln.depth for ln in doc.lines[1:4]]) == ([0, 1, 0]), ("edits reindex depth")

    doc = IniDocument.from_string(text)
    for bad in [(-1, 2), (0, 99), (3, 1)]:
        try:
            doc.replace_lines(bad[0], bad[1], [])
            assert ("no error") == ("IndexError"), (f"rejects range {bad}")
        except IndexError:
            assert ("IndexError") == ("IndexError"), (f"rejects range {bad}")


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
        assert (fh.read()) == ("[A]\r\nx = 2\r\n"), ("saved content")
    with open(backup, encoding="utf-8", newline="") as fh:
        assert (fh.read()) == (original), ("backup holds original")
    assert (backup.endswith(".BAK")) == (True), ("backup named *.BAK")
    assert (".ini_" in os.path.basename(backup)) == (True), ("backup keeps .ini in name")
    assert (os.path.exists(path + ".tmp")) == (False), ("no temp file left")

    # A backup must never be picked up as a loadable mod ini.
    from core.ini_parser import find_inis
    assert (find_inis(d)) == ([path]), ("find_inis ignores backups")


def test_find_inis_bounded_recursion():
    from core.ini_parser import find_inis

    geometry = (
        "[TextureOverrideBodyPosition]\n"
        "vb0 = ResourceBodyPosition\n"
        "[TextureOverrideBodyTexcoord]\n"
        "vb1 = ResourceBodyTexcoord\n"
        "[TextureOverrideBody]\n"
        "ib = ResourceBodyIB\n"
        "drawindexed = 3,0,0\n"
        "[ResourceBodyPosition]\nfilename = p.buf\nstride = 12\n"
        "[ResourceBodyTexcoord]\nfilename = t.buf\nstride = 8\n"
        "[ResourceBodyIB]\nfilename = i.buf\nformat = R32_UINT\n"
    )

    d = tempfile.mkdtemp()
    root_ini = os.path.join(d, "root.ini")
    with open(root_ini, "w", encoding="utf-8") as fh:
        fh.write(geometry)
    for index in range(12):
        folder = os.path.join(d, "nested", f"part{index:02d}")
        os.makedirs(folder)
        with open(os.path.join(folder, f"part{index:02d}.ini"), "w", encoding="utf-8") as fh:
            fh.write("[Constants]\nglobal $x = 0\n")
    too_deep = os.path.join(d, "nested", "part00", "deeper")
    os.makedirs(too_deep)
    with open(os.path.join(too_deep, "ignored.ini"), "w", encoding="utf-8") as fh:
        fh.write(geometry)

    found = find_inis(d)
    assert (len(found)) == (10), ("recursive find_inis is capped at ten")
    assert (found[0]) == (root_ini), ("recursive find_inis retains the root anchor")
    assert (any(os.path.basename(path) == "ignored.ini" for path in found)) == (False), ("recursive find_inis stops below depth two")

    library = tempfile.mkdtemp()
    direct = os.path.join(library, "notes.ini")
    with open(direct, "w", encoding="utf-8") as fh:
        fh.write("[Constants]\nglobal $x = 0\n")
    nested = os.path.join(library, "some_mod")
    os.makedirs(nested)
    with open(os.path.join(nested, "mod.ini"), "w", encoding="utf-8") as fh:
        fh.write(geometry)
    assert (find_inis(library)) == ([direct]), ("geometry-free root does not recurse")

    flat = tempfile.mkdtemp()
    flat_paths = []
    for index in range(11):
        path = os.path.join(flat, f"{index + 1:02d}.ini")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(geometry if index == 0 else
                     "[Constants]\nglobal $x = 0\n")
        flat_paths.append(path)
    assert (find_inis(flat)) == (flat_paths), ("direct find_inis never truncates a valid flat mod")


def test_roundtrip_corpus():
    """The real guarantee: every mod ini on disk survives byte-for-byte."""
    files = active_ini_files()
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

    assert ((len(mismatched), len(errored))) == ((0, 0)), (f"corpus roundtrip byte-identical ({len(files)} files)")
    for p in mismatched[:5]:
        print(f"        mismatch: {p}")
    for e in errored[:5]:
        print(f"        error: {e}")


def test_structure_errors():
    ok = IniDocument.from_string(
        "[A]\r\nif $x == 1\r\ndrawindexed = 1,0,0\r\nelse\r\nendif\r\n")
    assert (ok.structure_errors()) == ([]), ("balanced section has no errors")
    assert (ok.is_safe_to_rewrite("A")) == (True), ("balanced section is safe")

    # Real pattern from MasterCorinV1.ini: endif closes the block, then an
    # `else if` appears with nothing open.
    orphan = IniDocument.from_string(
        "[A]\r\nif $x == 1\r\nendif\r\nelse if $x == 2\r\nendif\r\n")
    problems = [p["problem"] for p in orphan.structure_errors()]
    assert (problems) == (["elif without an open if", "endif without a matching if"]), ("orphan else-if reported")
    assert (orphan.is_safe_to_rewrite("A")) == (False), ("orphan section not safe")

    unclosed = IniDocument.from_string("[A]\r\nif $x == 1\r\ndrawindexed = 1,0,0\r\n")
    assert ([p["problem"] for p in unclosed.structure_errors()]) == (["1 unclosed if"]), ("unclosed if reported")
    assert (unclosed.structure_errors()[0]["line"]) == (1), ("unclosed if points to its opening line")

    extra = IniDocument.from_string("[A]\r\nif $x == 1\r\nendif\r\nendif\r\n")
    assert ([p["problem"] for p in extra.structure_errors()]) == (["endif without a matching if"]), ("extra endif reported")

    # A malformed section must not taint a healthy one in the same file.
    mixed = IniDocument.from_string(
        "[Bad]\r\nendif\r\n[Good]\r\nif $x == 1\r\nendif\r\n")
    assert (mixed.is_safe_to_rewrite("Good")) == (True), ("errors are per-section")
    assert (mixed.is_safe_to_rewrite("Bad")) == (False), ("bad section still flagged")

    branch_order = IniDocument.from_string(
        "[A]\r\nif $x == 1\r\nelse\r\nelse\r\nelif $x == 2\r\nendif\r\n")
    assert ([p["problem"] for p in branch_order.structure_errors()]) == (["duplicate else", "elif after else"]), ("duplicate else and elif-after-else reported")
    assert (branch_order.is_safe_to_rewrite("A")) == (False), ("invalid branch order is unsafe to rewrite")


def test_syntax_errors():
    doc = IniDocument.from_string(
        "[Good]\r\n"
        "if\r\n"
        "elif\r\n"
        "else if\r\n"
        "elseif $x == 1\r\n"
        "if($x == 1)\r\n"
        "else if($x == 2)\r\n"
        "else unexpected\r\n"
        "endif unexpected\r\n"
        "if ($x == 1\r\n"
        "elif $x == 2)\r\n"
        "[]\r\n"
        "[Missing\r\n"
        "[Trailing] garbage\r\n")
    errors = doc.syntax_errors()
    commented_header = IniDocument.from_string("[Good] ; allowed header comment\r\n")
    assert (commented_header.syntax_errors()) == ([]), ("valid header comment is accepted")
    assert (sum(p["code"] == "malformed_condition_syntax" for p in errors)) == (8), ("malformed conditional forms reported")
    assert (sum(p["code"] == "unbalanced_condition_parentheses" for p in errors)) == (2), ("unbalanced condition parentheses reported")
    assert (sum(p["code"] == "malformed_section_header" for p in errors)) == (3), ("malformed section headers reported")
    assert (doc.is_safe_to_rewrite("Good")) == (False), ("condition syntax makes section unsafe")

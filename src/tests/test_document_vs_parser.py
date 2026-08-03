"""Cross-check the write model (ini_document) against the read model (ini_parser).

The two views must agree about what a file contains, or an edit made through
one would be validated against the other's different idea of the same file.
"""
import glob
import os
import random
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _corpus import corpus_roots
from core import ini_parser
from core.ini_document import BLANK, COMMENT, ENDIF, IF, SECTION, IniDocument

SAMPLE = 400


def corpus():
    files = []
    for root in corpus_roots():
        if os.path.isdir(root):
            files += glob.glob(os.path.join(root, "**", "*.ini"), recursive=True)
    return files


def main():
    files = [f for f in corpus() if not os.path.basename(f).upper().startswith("DISABLED")]
    if not files:
        print("SKIP (no mod libraries found)")
        return 0
    random.seed(7)
    sample = random.sample(files, min(SAMPLE, len(files)))

    bad_sections = bad_lines = unbalanced = 0
    checked = 0

    for path in sample:
        try:
            doc = IniDocument.load(path)
            parsed = ini_parser.parse_sections(path)
        except Exception as exc:
            print(f"  ERROR {path}: {type(exc).__name__}: {exc}")
            bad_sections += 1
            continue
        checked += 1

        # Same section names, in the same order (parse_sections dedups repeats,
        # so compare as sets).
        doc_names = {s.name for s in doc.sections}
        if doc_names != set(parsed):
            bad_sections += 1
            if bad_sections <= 3:
                print(f"  section mismatch {os.path.basename(path)}")
                print(f"     only in doc:    {sorted(doc_names - set(parsed))[:5]}")
                print(f"     only in parser: {sorted(set(parsed) - doc_names)[:5]}")
            continue

        # Same significant content per section: parse_sections keeps exactly the
        # lines that are neither blank, comment, nor header.
        for sec in doc.sections:
            want = parsed[sec.name]
            got = [ln.text for ln in sec.lines
                   if ln.kind not in (BLANK, COMMENT, SECTION)]
            # A repeated section name concatenates in parse_sections; only
            # compare when the name is unique in this file.
            if sum(1 for s in doc.sections if s.name == sec.name) > 1:
                continue
            if got != want:
                bad_lines += 1
                if bad_lines <= 3:
                    print(f"  line mismatch {os.path.basename(path)} [{sec.name}]")
                    for a, b in zip(got, want):
                        if a != b:
                            print(f"     doc:    {a!r}")
                            print(f"     parser: {b!r}")
                            break
                    else:
                        print(f"     count {len(got)} vs {len(want)}")
                break

        # if/endif must balance, or depth tracking is wrong.
        for sec in doc.sections:
            opens = sum(1 for ln in sec.lines if ln.kind == IF)
            closes = sum(1 for ln in sec.lines if ln.kind == ENDIF)
            if opens != closes:
                unbalanced += 1
                if unbalanced <= 3:
                    print(f"  unbalanced if/endif {os.path.basename(path)} "
                          f"[{sec.name}] {opens} if / {closes} endif")
                break

    print(f"\nchecked {checked} files")
    print(f"  section mismatches: {bad_sections}")
    print(f"  content mismatches: {bad_lines}")
    print(f"  unbalanced if/endif: {unbalanced}")
    ok = not (bad_sections or bad_lines)
    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

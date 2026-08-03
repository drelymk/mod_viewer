"""Tests for the condition syntax tree (ini_condition.py).

Deleting or editing a toggle has to rewrite `if` lines that also mention
variables this app never models ($DRAW_TYPE, master swap vars, cursor maths).
The tree must therefore round-trip *any* real condition unchanged, and partial
evaluation must only fold away the parts it was explicitly given values for.
"""

import os, re, sys, collections

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _corpus import corpus_roots
from core import ini_condition as ic
from core.ini_condition import ConditionError, TRUE, FALSE
from core.ini_document import IniDocument, IF, ELIF

FAILS = []


def check(cond, msg):
    print(("PASS  " if cond else "FAIL  ") + msg)
    if not cond:
        FAILS.append(msg)


def norm(s):
    return re.sub(r"\s+", "", s)


# ── round-trip ───────────────────────────────────────────────────────────────

ROUND_TRIP = [
    "$swapvar == 1",
    "$swapvar != 0",
    "$a == 1 && $b == 2",
    "$a == 1 || $b == 2",
    "$a == 1 && ($b == 2 || $c == 3)",
    "($a == 1 || $b == 2) && $c == 3",
    "!$a",
    "!($a == 1 && $b == 2)",
    "$DRAW_TYPE == 1",
    "vs-cb3 == 3381.7777",
    "ResourceMergedSkeleton !== null",
    "ResourceBlendBufferOverride === null",
    "$x == -1",
    "$Skirt % 4 < 2",
    "($toerings // 2) % 2 == 1",
    "$cpx > $mx + 0.0125",
    "$slot == $selectedSlot // 1",
    "$a",
    r"$\Remielle\Master\swapvar == 2",
]


def test_round_trip():
    bad = 0
    for src in ROUND_TRIP:
        try:
            rendered = ic.parse(src).render()
        except ConditionError as e:
            check(False, f"parse failed: {src!r} ({e})")
            bad += 1
            continue
        if norm(rendered) != norm(src):
            check(False, f"render changed {src!r} -> {rendered!r}")
            bad += 1
    check(bad == 0, f"all {len(ROUND_TRIP)} sample conditions round-trip verbatim")


def test_rejects_malformed():
    for src in ["$Cloth = 1", "$", "$cap ==", "$color == 0=", "", "$a &&"]:
        try:
            ic.parse(src)
            check(False, f"should have rejected {src!r}")
        except ConditionError:
            pass
    check(True, "malformed conditions raise ConditionError")


def test_variables():
    check(ic.parse("$a == 1 && $b == 2").variables() == {"a", "b"},
          "variables() finds both operands")
    check(ic.parse("$DRAW_TYPE == 1").variables() == {"DRAW_TYPE"},
          "variables() finds an untracked var")
    check(ic.parse("vs-cb3 == 1").variables() == set(),
          "bare words are not variables")
    check(ic.references("$a == 1 && $b == 2", "b"), "references() true for a used var")
    check(not ic.references("$a == 1", "b"), "references() false for an absent var")
    check(not ic.references("$Cloth = 1", "Cloth"),
          "references() is false on an unparseable condition")


# ── partial evaluation ───────────────────────────────────────────────────────

def red(src, bindings):
    return ic.render(ic.reduce(ic.parse(src), bindings))


def test_reduce_decides():
    check(red("$v == 1", {"v": "1"}) == TRUE, "matching == folds to TRUE")
    check(red("$v == 1", {"v": "0"}) == FALSE, "non-matching == folds to FALSE")
    check(red("$v != 1", {"v": "0"}) == TRUE, "!= folds")
    check(red("$v == 1", {"v": "1.0"}) == TRUE, "1 and 1.0 compare numerically")
    check(red("$v", {"v": "0"}) == FALSE, "bare $v is false at 0")
    check(red("$v", {"v": "3"}) == TRUE, "bare $v is true when non-zero")
    check(red("!$v", {"v": "0"}) == TRUE, "! inverts")


def test_reduce_preserves_unknowns():
    """The reason this module exists: untouched variables survive verbatim."""
    check(red("$v == 1 && $DRAW_TYPE == 1", {"v": "1"}) == "$DRAW_TYPE == 1",
          "true conjunct drops out, unknown survives")
    check(red("$v == 1 && $DRAW_TYPE == 1", {"v": "0"}) == FALSE,
          "false conjunct kills the whole AND")
    check(red("$v == 1 || $DRAW_TYPE == 1", {"v": "0"}) == "$DRAW_TYPE == 1",
          "false disjunct drops out")
    check(red("$v == 1 || $DRAW_TYPE == 1", {"v": "1"}) == TRUE,
          "true disjunct satisfies the whole OR")
    check(red("$other == 1", {"v": "1"}) == "$other == 1",
          "an unbound condition is returned untouched")
    check(red("$a == 1 && $b == 2 && $c == 3", {"b": "2"}) == "$a == 1 && $c == 3",
          "middle conjunct removed, order preserved")
    check(red("$v == 1 && ($a == 1 || $b == 2)", {"v": "1"}) == "($a == 1 || $b == 2)",
          "parentheses preserved when the other side folds")
    check(red("$cpx > $mx + 0.0125", {"v": "1"}) == "$cpx > $mx + 0.0125",
          "arithmetic is carried through untouched")
    check(red("$v == 1 && vs-cb3 == 3381.7777", {"v": "1"}) == "vs-cb3 == 3381.7777",
          "slot-name operands survive")
    check(red("vs-cb3 == 3381.7777", {}) == "vs-cb3 == 3381.7777",
          "a bare word is a runtime value, never folded as a string")
    check(red("ResourceMergedSkeleton !== null", {}) == "ResourceMergedSkeleton !== null",
          "resource-vs-null comparison survives")


def test_reduce_nested():
    check(red("($v == 1 || $v == 2) && $DRAW_TYPE == 1", {"v": "2"}) == "$DRAW_TYPE == 1",
          "OR inside parens collapses to TRUE and drops out")
    check(red("($v == 1 || $v == 2) && $DRAW_TYPE == 1", {"v": "3"}) == FALSE,
          "OR inside parens collapses to FALSE and kills the AND")
    check(red("!($v == 1)", {"v": "1"}) == FALSE, "NOT of a folded true")
    check(red("!($v == 1 && $a == 2)", {"v": "1"}) == "!($a == 2)",
          "NOT keeps its parens around a surviving AND")


def test_namespaced():
    check(ic.is_namespaced(r"\Remielle\Master\swapvar"), "backslash var is namespaced")
    check(not ic.is_namespaced("swapvar"), "plain var is not namespaced")
    check(not ic.is_namespaced(None), "None is not namespaced")


# ── eliminate() — used by toggle_editor's delete path ───────────────────────

def elim(src, dead_vars):
    return ic.render(ic.eliminate(ic.parse(src), dead_vars))


def test_eliminate_decides():
    check(elim("$v == 1", ["v"]) == TRUE, "bare comparison on a dead var folds to TRUE")
    check(elim("$v != 1", ["v"]) == TRUE, "!= on a dead var also folds to TRUE (never FALSE)")
    check(elim("$v", ["v"]) == TRUE, "bare truthiness check on a dead var folds to TRUE")
    check(elim("$v < 3", ["v"]) == TRUE, "ordering comparison on a dead var folds to TRUE")
    check(elim("1 == $v", ["v"]) == TRUE, "dead var on the right-hand side also folds")


def test_eliminate_preserves_survivors():
    check(elim("$v == 1 && $DRAW_TYPE == 1", ["v"]) == "$DRAW_TYPE == 1",
          "ANDed survivor kept when the dead var drops out")
    check(elim("$v == 1 || $DRAW_TYPE == 1", ["v"]) == TRUE,
          "OR containing a dead var folds the whole OR to TRUE")
    check(elim("$a == 1 && $b == 2 && $c == 3", ["b"]) == "$a == 1 && $c == 3",
          "middle conjunct removed, order preserved")
    check(elim("$v == 1 && ($a == 1 || $b == 2)", ["v"]) == "($a == 1 || $b == 2)",
          "parentheses preserved when the dead var's side folds")
    check(elim("($v == 1 || $a == 2) && $DRAW_TYPE == 1", ["v"]) == "$DRAW_TYPE == 1",
          "nested parens: dead var anywhere inside an OR folds the whole OR to TRUE")
    check(elim("!($v == 1 && $a == 2)", ["v"]) == "!($a == 2)",
          "NOT keeps its parens around a surviving AND, same as reduce()")


def test_eliminate_multiple_vars_at_once():
    check(elim("$v == 1 && $w == 2", ["v", "w"]) == TRUE,
          "both dead vars in one AND fold the whole thing to TRUE")
    check(elim("$v == 1 && $w == 2 && $DRAW_TYPE == 1", ["v", "w"]) == "$DRAW_TYPE == 1",
          "two dead vars removed at once, survivor kept")
    check(elim("$v == 1 || $w == 2", ["v", "w"]) == TRUE,
          "both dead vars in one OR fold the whole thing to TRUE")


def test_eliminate_never_produces_false_from_a_bare_dead_var():
    """A *leaf* referencing a dead var only ever folds to TRUE, never FALSE —
    so as long as it isn't wrapped in a NOT, the whole condition can't end up
    FALSE either. (A negated reference is a different story: see below.)"""
    samples = ["$v == 1", "$v != 1", "$v", "$v < 3", "$v >= 9",
               "$v == 1 && $a == 2", "$v == 1 || $a == 2",
               "($v == 1)", "$a == 1 && ($v == 2 || $b == 3)"]
    bad = [s for s in samples if elim(s, ["v"]) is FALSE]
    check(bad == [], f"no bare (non-negated) dead-var condition folds to FALSE ({bad})")


def test_eliminate_negation_can_legitimately_produce_false():
    """Not a bug: eliminate() substitutes TRUE for a dead var's clauses and
    then applies ordinary boolean algebra, same as reduce() — so a `!` wholly
    wrapping an all-dead-vars subtree inverts that TRUE to FALSE, exactly like
    negating any other known-true condition would. `if !$v` becoming `if 0`
    once `$v` is deleted is the expected, deterministic outcome, not an
    unreachable corner case (delete_toggle() surfaces these as
    `always_false_gates` precisely because it's a real, common shape)."""
    check(elim("!$v", ["v"]) == FALSE, "bare negated dead var folds to FALSE")
    check(elim("!($v == 1)", ["v"]) == FALSE, "negated == comparison on a dead var folds to FALSE")
    check(elim("!($v == 1 && $w == 2)", ["v", "w"]) == FALSE,
          "negated AND of two dead vars folds to FALSE")
    check(elim("!($v == 1 || $a == 2)", ["v"]) == FALSE,
          "negated OR short-circuits to TRUE internally, then NOT inverts to FALSE")


def test_eliminate_dead_var_inside_arithmetic():
    """Regression: a dead var buried inside arithmetic on a Cmp operand (or as
    a bare arithmetic condition) used to be invisible to eliminate() — it only
    checked for a bare `Operand` on either side of a Cmp, so `$img_x` inside
    `cursor_x < $img_x + $norm_width` was never substituted away. references()
    still says the line depends on the dead var (it recurses through Arith),
    so _strip_vars_from_gates kept re-selecting the same untouched line as its
    next rewrite target forever — a real, corpus-confirmed infinite loop
    (found via a mouse-drag [Present] script section in a real WuWa mod),
    not a hypothetical one."""
    check(elim("$v + 1 == 2", ["v"]) == TRUE,
          "dead var inside arithmetic on the left of == folds to TRUE")
    check(elim("2 == $v + 1", ["v"]) == TRUE,
          "dead var inside arithmetic on the right of == folds to TRUE")
    check(elim("$other == $v + 1", ["v"]) == TRUE,
          "a live var mixed with a dead one in arithmetic still folds "
          "(the dead var makes the whole expression unknowable)")
    check(elim("$other == $another + 1", ["v"]) == "$other == $another + 1",
          "arithmetic naming only live vars is left completely untouched")
    check(elim("cursor_x < $img_x + $norm_width", ["img_x", "img_y"]) == TRUE,
          "the exact real-world mouse-drag condition that used to hang now folds")
    check(elim("$a == 1 && $v + 1 == 2", ["v"]) == "$a == 1",
          "survivor kept when the dead-arithmetic conjunct drops out")
    check(elim("$v + 1", ["v"]) == TRUE,
          "a bare arithmetic condition (no comparison at all) with a dead "
          "var also folds to TRUE")
    check(elim("$a + 1", ["v"]) == "$a + 1",
          "a bare arithmetic condition naming only live vars is untouched")
    check(elim("($v + 1) == 2", ["v"]) == TRUE,
          "dead var inside parenthesised arithmetic still folds")


def test_find_comparisons():
    node = ic.parse("$v == 1 && $DRAW_TYPE == 1")
    check(ic.find_comparisons(node, "v") == [("==", "1")],
          "finds a simple == comparison on the tracked var")
    check(ic.find_comparisons(node, "DRAW_TYPE") == [("==", "1")],
          "finds the comparison for a different var in the same tree")
    check(ic.find_comparisons(node, "nope") == [],
          "no comparisons found for a var not in the tree")

    multi = ic.parse("$v == 1 || $v == 2 || $v == 3")
    check(ic.find_comparisons(multi, "v") == [("==", "1"), ("==", "2"), ("==", "3")],
          "finds every comparison across an OR chain")

    mirrored = ic.parse("1 == $v")
    check(ic.find_comparisons(mirrored, "v") == [("==", "1")],
          "finds a comparison with the var on the right-hand side")

    nested = ic.parse("$a == 1 && ($v == 2 || $v == 3)")
    check(ic.find_comparisons(nested, "v") == [("==", "2"), ("==", "3")],
          "finds comparisons nested inside parens")

    var_vs_var = ic.parse("$v == $other")
    check(ic.find_comparisons(var_vs_var, "v") == [],
          "a var-vs-var comparison is not a literal comparison, so it's skipped")


# ── the whole corpus ─────────────────────────────────────────────────────────

MOD_ROOTS = corpus_roots()
_RE_COND = re.compile(r"^(?:if|else\s+if|elif)\s+(.*)$", re.I)


def test_corpus():
    files = []
    for root in MOD_ROOTS:
        if not os.path.isdir(root):
            continue
        for dp, dn, fn in os.walk(root):
            for f in fn:
                if f.lower().endswith(".ini") and not f.upper().startswith("DISABLED"):
                    files.append(os.path.join(dp, f))
    if not files:
        print("SKIP  no local mod libraries found")
        return

    tot = ok = drift = 0
    fail = collections.Counter()
    for path in files:
        try:
            doc = IniDocument.load(path)
        except Exception:
            continue
        for line in doc.lines:
            if line.kind not in (IF, ELIF):
                continue
            m = _RE_COND.match(line.text)
            if not m:
                continue
            expr = m.group(1).strip()
            tot += 1
            try:
                node = ic.parse(expr)
            except ConditionError as e:
                fail[str(e)[:40]] += 1
                continue
            ok += 1
            # Re-rendering must reproduce the source exactly bar whitespace;
            # anything else means the tree invented or lost structure, and a
            # rewrite built on it would corrupt the mod.
            if norm(node.render()) != norm(expr):
                drift += 1

    print(f"      {len(files)} files, {tot} conditions, {tot-ok} unparseable")
    check(tot > 100000, f"corpus is substantial ({tot} conditions)")
    check(drift == 0, f"every parsed condition re-renders identically (drift={drift})")
    # The handful that fail are genuinely malformed ini ($Cloth = 1, $color == 0=).
    check(tot - ok <= 20, f"at most a handful are unparseable (got {tot-ok})")


if __name__ == "__main__":
    for fn in (test_round_trip, test_rejects_malformed, test_variables,
               test_reduce_decides, test_reduce_preserves_unknowns,
               test_reduce_nested, test_namespaced,
               test_eliminate_decides, test_eliminate_preserves_survivors,
               test_eliminate_multiple_vars_at_once,
               test_eliminate_never_produces_false_from_a_bare_dead_var,
               test_eliminate_negation_can_legitimately_produce_false,
               test_eliminate_dead_var_inside_arithmetic,
               test_find_comparisons, test_corpus):
        fn()
    print("\n" + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"))
    sys.exit(1 if FAILS else 0)

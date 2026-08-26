"""Tests for the condition syntax tree (core.ini.condition.py).

Deleting or editing a toggle has to rewrite `if` lines that also mention
variables this app never models ($DRAW_TYPE, master swap vars, cursor maths).
The tree must therefore round-trip *any* real condition unchanged, and partial
evaluation must only fold away the parts it was explicitly given values for.
"""

import re


from core.ini import condition as ic
from core.ini.condition import ConditionError, TRUE, FALSE


def norm(s):
    return re.sub(r"\s+", "", s)


# â”€â”€ round-trip â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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
            assert (False), (f"parse failed: {src!r} ({e})")
            bad += 1
            continue
        if norm(rendered) != norm(src):
            assert (False), (f"render changed {src!r} -> {rendered!r}")
            bad += 1
    assert (bad == 0), (f"all {len(ROUND_TRIP)} sample conditions round-trip verbatim")


def test_rejects_malformed():
    for src in ["$Cloth = 1", "$", "$cap ==", "$color == 0=", "", "$a &&"]:
        try:
            ic.parse(src)
            assert (False), (f"should have rejected {src!r}")
        except ConditionError:
            pass
    assert (True), ("malformed conditions raise ConditionError")


# â”€â”€ partial evaluation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def red(src, bindings):
    return ic.render(ic.reduce(ic.parse(src), bindings))


def test_reduce_decides():
    assert (red("$v == 1", {"v": "1"}) == TRUE), ("matching == folds to TRUE")
    assert (red("$v == 1", {"v": "0"}) == FALSE), ("non-matching == folds to FALSE")
    assert (red("$v != 1", {"v": "0"}) == TRUE), ("!= folds")
    assert (red("$v == 1", {"v": "1.0"}) == TRUE), ("1 and 1.0 compare numerically")
    assert (red("$v", {"v": "0"}) == FALSE), ("bare $v is false at 0")
    assert (red("$v", {"v": "3"}) == TRUE), ("bare $v is true when non-zero")
    assert (red("!$v", {"v": "0"}) == TRUE), ("! inverts")


def test_reduce_preserves_unknowns():
    """The reason this module exists: untouched variables survive verbatim."""
    assert (red("$v == 1 && $DRAW_TYPE == 1", {"v": "1"}) == "$DRAW_TYPE == 1"), ("true conjunct drops out, unknown survives")
    assert (red("$v == 1 && $DRAW_TYPE == 1", {"v": "0"}) == FALSE), ("false conjunct kills the whole AND")
    assert (red("$v == 1 || $DRAW_TYPE == 1", {"v": "0"}) == "$DRAW_TYPE == 1"), ("false disjunct drops out")
    assert (red("$v == 1 || $DRAW_TYPE == 1", {"v": "1"}) == TRUE), ("true disjunct satisfies the whole OR")
    assert (red("$other == 1", {"v": "1"}) == "$other == 1"), ("an unbound condition is returned untouched")
    assert (red("$a == 1 && $b == 2 && $c == 3", {"b": "2"}) == "$a == 1 && $c == 3"), ("middle conjunct removed, order preserved")
    assert (red("$v == 1 && ($a == 1 || $b == 2)", {"v": "1"}) == "($a == 1 || $b == 2)"), ("parentheses preserved when the other side folds")
    assert (red("$cpx > $mx + 0.0125", {"v": "1"}) == "$cpx > $mx + 0.0125"), ("arithmetic is carried through untouched")
    assert (red("$v == 1 && vs-cb3 == 3381.7777", {"v": "1"}) == "vs-cb3 == 3381.7777"), ("slot-name operands survive")
    assert (red("vs-cb3 == 3381.7777", {}) == "vs-cb3 == 3381.7777"), ("a bare word is a runtime value, never folded as a string")
    assert (red("ResourceMergedSkeleton !== null", {}) == "ResourceMergedSkeleton !== null"), ("resource-vs-null comparison survives")




# â”€â”€ eliminate() â€” used by toggle_editor's delete path â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def elim(src, dead_vars):
    return ic.render(ic.eliminate(ic.parse(src), dead_vars))


def test_eliminate_decides():
    assert (elim("$v == 1", ["v"]) == TRUE), ("bare comparison on a dead var folds to TRUE")
    assert (elim("$v != 1", ["v"]) == TRUE), ("!= on a dead var also folds to TRUE (never FALSE)")
    assert (elim("$v", ["v"]) == TRUE), ("bare truthiness check on a dead var folds to TRUE")
    assert (elim("$v < 3", ["v"]) == TRUE), ("ordering comparison on a dead var folds to TRUE")
    assert (elim("1 == $v", ["v"]) == TRUE), ("dead var on the right-hand side also folds")


def test_eliminate_preserves_survivors():
    assert (elim("$v == 1 && $DRAW_TYPE == 1", ["v"]) == "$DRAW_TYPE == 1"), ("ANDed survivor kept when the dead var drops out")
    assert (elim("$v == 1 || $DRAW_TYPE == 1", ["v"]) == TRUE), ("OR containing a dead var folds the whole OR to TRUE")
    assert (elim("$a == 1 && $b == 2 && $c == 3", ["b"]) == "$a == 1 && $c == 3"), ("middle conjunct removed, order preserved")
    assert (elim("$v == 1 && ($a == 1 || $b == 2)", ["v"]) == "($a == 1 || $b == 2)"), ("parentheses preserved when the dead var's side folds")
    assert (elim("($v == 1 || $a == 2) && $DRAW_TYPE == 1", ["v"]) == "$DRAW_TYPE == 1"), ("nested parens: dead var anywhere inside an OR folds the whole OR to TRUE")
    assert (elim("!($v == 1 && $a == 2)", ["v"]) == "!($a == 2)"), ("NOT keeps its parens around a surviving AND, same as reduce()")




def test_eliminate_negation_can_legitimately_produce_false():
    """Not a bug: eliminate() substitutes TRUE for a dead var's clauses and
    then applies ordinary boolean algebra, same as reduce() â€” so a `!` wholly
    wrapping an all-dead-vars subtree inverts that TRUE to FALSE, exactly like
    negating any other known-true condition would. `if !$v` becoming `if 0`
    once `$v` is deleted is the expected, deterministic outcome, not an
    unreachable corner case (delete_toggle() surfaces these as
    `always_false_gates` precisely because it's a real, common shape)."""
    assert (elim("!$v", ["v"]) == FALSE), ("bare negated dead var folds to FALSE")
    assert (elim("!($v == 1)", ["v"]) == FALSE), ("negated == comparison on a dead var folds to FALSE")
    assert (elim("!($v == 1 && $w == 2)", ["v", "w"]) == FALSE), ("negated AND of two dead vars folds to FALSE")
    assert (elim("!($v == 1 || $a == 2)", ["v"]) == FALSE), ("negated OR short-circuits to TRUE internally, then NOT inverts to FALSE")


def test_eliminate_dead_var_inside_arithmetic():
    """Regression: a dead var buried inside arithmetic on a Cmp operand (or as
    a bare arithmetic condition) used to be invisible to eliminate() â€” it only
    checked for a bare `Operand` on either side of a Cmp, so `$img_x` inside
    `cursor_x < $img_x + $norm_width` was never substituted away. references()
    still says the line depends on the dead var (it recurses through Arith),
    so _strip_vars_from_gates kept re-selecting the same untouched line as its
    next rewrite target forever â€” a real, corpus-confirmed infinite loop
    (found via a mouse-drag [Present] script section in a real WuWa mod),
    not a hypothetical one."""
    assert (elim("$v + 1 == 2", ["v"]) == TRUE), ("dead var inside arithmetic on the left of == folds to TRUE")
    assert (elim("2 == $v + 1", ["v"]) == TRUE), ("dead var inside arithmetic on the right of == folds to TRUE")
    assert (elim("$other == $v + 1", ["v"]) == TRUE), ("a live var mixed with a dead one in arithmetic still folds "
          "(the dead var makes the whole expression unknowable)")
    assert (elim("$other == $another + 1", ["v"]) == "$other == $another + 1"), ("arithmetic naming only live vars is left completely untouched")
    assert (elim("cursor_x < $img_x + $norm_width", ["img_x", "img_y"]) == TRUE), ("the exact real-world mouse-drag condition that used to hang now folds")
    assert (elim("$a == 1 && $v + 1 == 2", ["v"]) == "$a == 1"), ("survivor kept when the dead-arithmetic conjunct drops out")
    assert (elim("$v + 1", ["v"]) == TRUE), ("a bare arithmetic condition (no comparison at all) with a dead "
          "var also folds to TRUE")
    assert (elim("$a + 1", ["v"]) == "$a + 1"), ("a bare arithmetic condition naming only live vars is untouched")
    assert (elim("($v + 1) == 2", ["v"]) == TRUE), ("dead var inside parenthesised arithmetic still folds")




# â”€â”€ the whole corpus â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

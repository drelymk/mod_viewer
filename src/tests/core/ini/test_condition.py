"""Tests for the condition syntax tree (core.ini.condition.py).

Deleting or editing a toggle has to rewrite `if` lines that also mention
variables this app never models ($DRAW_TYPE, master swap vars, cursor maths).
The tree must therefore round-trip *any* real condition unchanged, and partial
evaluation must only fold away the parts it was explicitly given values for.
"""

import re

import pytest


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


@pytest.mark.parametrize("source", ROUND_TRIP)
def test_condition_round_trips(source):
    assert norm(ic.parse(source).render()) == norm(source)


@pytest.mark.parametrize(
    "source", ["$Cloth = 1", "$", "$cap ==", "$color == 0=", "", "$a &&"],
)
def test_condition_rejects_malformed(source):
    with pytest.raises(ConditionError):
        ic.parse(source)


# â”€â”€ partial evaluation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def red(src, bindings):
    return ic.render(ic.reduce(ic.parse(src), bindings))


@pytest.mark.parametrize("source, bindings, expected", [
    ("$v == 1", {"v": "1"}, TRUE),
    ("$v == 1", {"v": "0"}, FALSE),
    ("$v != 1", {"v": "0"}, TRUE),
    ("$v == 1", {"v": "1.0"}, TRUE),
    ("$v", {"v": "0"}, FALSE),
    ("$v", {"v": "3"}, TRUE),
    ("!$v", {"v": "0"}, TRUE),
    ("$v == 1 && $DRAW_TYPE == 1", {"v": "1"}, "$DRAW_TYPE == 1"),
    ("$v == 1 && $DRAW_TYPE == 1", {"v": "0"}, FALSE),
    ("$v == 1 || $DRAW_TYPE == 1", {"v": "0"}, "$DRAW_TYPE == 1"),
    ("$v == 1 || $DRAW_TYPE == 1", {"v": "1"}, TRUE),
    ("$other == 1", {"v": "1"}, "$other == 1"),
    ("$a == 1 && $b == 2 && $c == 3", {"b": "2"}, "$a == 1 && $c == 3"),
    ("$v == 1 && ($a == 1 || $b == 2)", {"v": "1"}, "($a == 1 || $b == 2)"),
    ("$cpx > $mx + 0.0125", {"v": "1"}, "$cpx > $mx + 0.0125"),
    ("$v == 1 && vs-cb3 == 3381.7777", {"v": "1"}, "vs-cb3 == 3381.7777"),
    ("vs-cb3 == 3381.7777", {}, "vs-cb3 == 3381.7777"),
    ("ResourceMergedSkeleton !== null", {}, "ResourceMergedSkeleton !== null"),
], ids=["equal", "not-equal", "not-equal-operator", "numeric", "bare-false",
        "bare-true", "negation", "and-unknown", "and-false", "or-unknown",
        "or-true", "unbound", "middle-conjunct", "parentheses", "arithmetic",
        "slot-operand", "runtime-value", "resource-null"])
def test_condition_reduce(source, bindings, expected):
    assert red(source, bindings) == expected




# â”€â”€ eliminate() â€” used by toggle_editor's delete path â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def elim(src, dead_vars):
    return ic.render(ic.eliminate(ic.parse(src), dead_vars))


@pytest.mark.parametrize("source, dead_vars, expected", [
    ("$v == 1", ["v"], TRUE), ("$v != 1", ["v"], TRUE),
    ("$v", ["v"], TRUE), ("$v < 3", ["v"], TRUE),
    ("1 == $v", ["v"], TRUE),
    ("$v == 1 && $DRAW_TYPE == 1", ["v"], "$DRAW_TYPE == 1"),
    ("$v == 1 || $DRAW_TYPE == 1", ["v"], TRUE),
    ("$a == 1 && $b == 2 && $c == 3", ["b"], "$a == 1 && $c == 3"),
    ("$v == 1 && ($a == 1 || $b == 2)", ["v"], "($a == 1 || $b == 2)"),
    ("($v == 1 || $a == 2) && $DRAW_TYPE == 1", ["v"], "$DRAW_TYPE == 1"),
    ("!($v == 1 && $a == 2)", ["v"], "!($a == 2)"),
    ("!$v", ["v"], FALSE), ("!($v == 1)", ["v"], FALSE),
    ("!($v == 1 && $w == 2)", ["v", "w"], FALSE),
    ("!($v == 1 || $a == 2)", ["v"], FALSE),
], ids=["comparison", "not-equal", "bare", "ordering", "right-hand",
        "and-survivor", "or-short-circuit", "middle-survivor", "parentheses",
        "nested-or", "not-survivor", "negated-bare", "negated-comparison",
        "negated-all-dead", "negated-or"])
def test_condition_eliminate(source, dead_vars, expected):
    assert elim(source, dead_vars) == expected


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

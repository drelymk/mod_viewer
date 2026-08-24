"""Shared labels for the offline WuWa texture corpus and trainer."""

from __future__ import annotations


POSITIVE_LABELS = frozenset({"diffuse"})
NEGATIVE_LABELS = frozenset({
    "light_map", "material_map", "normal_map", "not_diffuse",
})
IGNORED_LABELS = frozenset({"", "skip", "unknown"})
MANUAL_LABELS = frozenset({
    *POSITIVE_LABELS, *NEGATIVE_LABELS, *IGNORED_LABELS,
})


def canonical_label(label):
    """Return ``(canonical_label, target)`` or ``None`` when ignored."""
    normalized = (label or "").strip().lower()
    if normalized in POSITIVE_LABELS:
        return "diffuse", 1
    if normalized in NEGATIVE_LABELS:
        return "not_diffuse", 0
    if normalized in IGNORED_LABELS:
        return None
    raise ValueError(f"unsupported texture label: {normalized}")

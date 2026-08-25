"""Shared WuWa texture filename association rules."""

import re


_COMPONENT_TEXTURE_RE = re.compile(
    r"^Components-(?P<components>\d+(?:-\d+)*)"
    r"\s+t=(?P<tag>.+?)\.dds$", re.I)


def texture_component_ordinals(filename: str) -> frozenset[int] | None:
    """Return component ordinals encoded by a WuWa texture filename."""
    basename = str(filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    match = _COMPONENT_TEXTURE_RE.fullmatch(basename)
    if not match:
        return None
    return frozenset(
        int(value) for value in match.group("components").split("-"))


__all__ = ["texture_component_ordinals"]

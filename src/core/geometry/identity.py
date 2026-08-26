"""Shared, conservative geometry identity values."""

from dataclasses import dataclass
import re


_GEOMETRY_HASH = re.compile(r"^[0-9a-fA-F]{8}$")


@dataclass(frozen=True, slots=True)
class GeometryMatch:
    """The authored geometry evidence used to bind a draw to an Asset."""

    hash: str
    first_index: int | None = None
    index_count: int | None = None


def normalize_geometry_hash(value):
    """Return a canonical eight-digit geometry hash, or ``None``."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if value.lower().startswith("0x"):
        value = value[2:]
    if not _GEOMETRY_HASH.fullmatch(value):
        return None
    return value.lower()


def make_geometry_match(value, first_index=None, index_count=None):
    """Build a validated match record, returning ``None`` for bad hashes."""
    geometry_hash = normalize_geometry_hash(value)
    if geometry_hash is None:
        return None
    return GeometryMatch(geometry_hash, first_index, index_count)

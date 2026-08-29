"""Shared, conservative geometry and displayed-mesh identity values."""

from dataclasses import dataclass
import json
import re


_GEOMETRY_HASH = re.compile(r"^[0-9a-fA-F]{8}$")


@dataclass(frozen=True, slots=True)
class GeometryMatch:
    """The authored geometry evidence used to bind a draw to an Asset."""

    hash: str
    first_index: int | None = None
    index_count: int | None = None


def normalize_identity_source(value):
    """Normalize an authored source label without changing its spelling."""
    if value is None:
        return None
    source = str(value).replace("\\", "/")
    while source.startswith("./"):
        source = source[2:]
    return source or None


@dataclass(frozen=True, slots=True)
class MeshIdentity:
    """Stable identity for one displayed, authored mod draw."""

    source: str | None
    component: str | None
    geometry: GeometryMatch | None
    count: int | None
    start: int
    base: int

    @property
    def version(self):
        return 2

    @property
    def key(self):
        geometry = None
        if self.geometry is not None:
            geometry = [
                self.geometry.hash,
                self.geometry.first_index,
                self.geometry.index_count,
            ]
        value = [
            self.version,
            self.source or "",
            self.component or "",
            geometry,
            [self.count, self.start, self.base],
        ]
        return "mesh:" + json.dumps(
            value, ensure_ascii=False, separators=(",", ":"))

    def to_dict(self):
        """Return the stable frontend/persistence projection."""
        geometry = None
        if self.geometry is not None:
            geometry = {
                "hash": self.geometry.hash,
                "first_index": self.geometry.first_index,
                "index_count": self.geometry.index_count,
            }
        return {
            "version": self.version,
            "key": self.key,
            "source": self.source,
            "component": self.component,
            "geometry": geometry,
            "draw": {
                "count": self.count,
                "start": self.start,
                "base": self.base,
            },
        }


def make_mesh_identity(draw, source=None, component=None):
    """Build the canonical identity from an already-resolved draw record."""
    return MeshIdentity(
        source=normalize_identity_source(source),
        component=component or None,
        geometry=getattr(draw, "geometry_match", None),
        count=draw.count,
        start=draw.start,
        base=draw.base,
    )


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

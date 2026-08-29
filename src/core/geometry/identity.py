"""Shared, conservative geometry and displayed-mesh identity values."""

import json
import re
from dataclasses import dataclass

from .vertex_attributes import VertexAttributeSource


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
class MeshGeometryIdentity:
    """Effective geometry resources that distinguish rendered draws."""

    ib_file: str | None
    index_size: int | None
    position_file: str | None
    position_stride: int | None
    texcoord_file: str | None
    texcoord_stride: int | None
    normal_source: VertexAttributeSource | None = None

    def key_value(self):
        normal = None
        if self.normal_source is not None:
            normal = [
                normalize_identity_source(self.normal_source.file),
                self.normal_source.stride,
                self.normal_source.offset,
                self.normal_source.encoding,
            ]
        return [
            normalize_identity_source(self.ib_file),
            self.index_size,
            normalize_identity_source(self.position_file),
            self.position_stride,
            normalize_identity_source(self.texcoord_file),
            self.texcoord_stride,
            normal,
        ]

    def to_dict(self):
        """Return the diagnostic projection of the effective resources."""
        normal = None
        if self.normal_source is not None:
            normal = {
                "file": normalize_identity_source(self.normal_source.file),
                "stride": self.normal_source.stride,
                "offset": self.normal_source.offset,
                "encoding": self.normal_source.encoding,
            }
        return {
            "ib_file": normalize_identity_source(self.ib_file),
            "index_size": self.index_size,
            "position_file": normalize_identity_source(self.position_file),
            "position_stride": self.position_stride,
            "texcoord_file": normalize_identity_source(self.texcoord_file),
            "texcoord_stride": self.texcoord_stride,
            "normal_source": normal,
        }


def make_mesh_geometry_identity(draw):
    """Project only effective geometry state from a resolved draw."""
    return MeshGeometryIdentity(
        ib_file=draw.ib_file,
        index_size=draw.index_size,
        position_file=draw.position_file,
        position_stride=draw.position_stride,
        texcoord_file=draw.texcoord_file,
        texcoord_stride=draw.texcoord_stride,
        normal_source=draw.normal_source,
    )


@dataclass(frozen=True, slots=True)
class MeshIdentity:
    """Stable identity for one displayed, authored mod draw."""

    source: str | None
    component: str | None
    geometry: GeometryMatch | None
    count: int | None
    start: int
    base: int
    geometry_state: MeshGeometryIdentity | None = None

    @property
    def version(self):
        return 3

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
            (self.geometry_state.key_value()
             if self.geometry_state is not None else None),
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
            "geometry_state": (self.geometry_state.to_dict()
                                if self.geometry_state is not None else None),
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
        geometry_state=make_mesh_geometry_identity(draw),
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

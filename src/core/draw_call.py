"""Typed draw-call records shared by INI discovery and mesh construction.

The parser first captures an :class:`AuthoredDrawCall`, whose resources still
use INI names. Resource resolution then produces :class:`DrawCall`. The latter
retains a small mapping compatibility surface because ``build_draw_groups`` is
also used by low-level fixtures and external scripts, but its schema and render
identity are explicit: adding a field requires deciding whether it changes
rendered output instead of silently changing deduplication behavior.
"""

from collections.abc import Iterator, Mapping, MutableMapping
from dataclasses import dataclass, field, fields, replace
from typing import ClassVar


def _freeze(value):
    """Return a deterministic, hashable projection of nested IR state."""
    if isinstance(value, Mapping):
        return tuple(sorted((key, _freeze(item))
                            for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass
class AuthoredDrawCall:
    """One parsed ``drawindexed`` row before resource-name resolution."""

    count: int | None
    start: int
    base: int
    conditions: list = field(default_factory=list)
    source: dict | None = None
    index_resource: str | None = None
    diffuse_variants: list = field(default_factory=list)
    diffuse_history: list = field(default_factory=list)
    vertex_resources: tuple[str | None, str | None, str | None] = (
        None, None, None)
    auxiliary_maps: dict = field(default_factory=dict)


@dataclass
class DrawCall(MutableMapping):
    """Resolved draw geometry, material state, visibility and provenance.

    Buffer fields may initially be ``None`` to inherit their draw group's
    binding.  :meth:`resolved` applies those defaults before geometry packing
    and deduplication, so :meth:`render_identity` describes effective state,
    not the incidental difference between explicit and inherited bindings.
    """

    label: str = ""
    count: int | None = None
    start: int = 0
    base: int = 0
    conditions: list = field(default_factory=list)
    sources: list = field(default_factory=list)

    ib_file: str | None = None
    index_size: int | None = None
    position_file: str | None = None
    texcoord_file: str | None = None
    position_stride: int | None = None
    texcoord_stride: int | None = None

    texture_default_file: str | None = None
    texture_variants: list = field(default_factory=list)
    texture_assignments: list = field(default_factory=list)
    normal_map_default_file: str | None = None
    normal_map_variants: list = field(default_factory=list)
    light_map_default_file: str | None = None
    light_map_variants: list = field(default_factory=list)
    material_map_default_file: str | None = None
    material_map_variants: list = field(default_factory=list)

    _ALWAYS_PRESENT: ClassVar[frozenset[str]] = frozenset({
        "count", "start", "base", "conditions", "sources",
    })
    _TEXTURE_PREFIX: ClassVar[dict[str, str]] = {
        "diffuse": "texture",
        "normal_map": "normal_map",
        "light_map": "light_map",
        "material_map": "material_map",
    }
    _NON_RENDER_FIELDS: ClassVar[frozenset[str]] = frozenset({
        "label", "conditions", "sources",
    })
    _RENDER_IDENTITY_FIELDS: ClassVar[tuple[str, ...]] = (
        "count", "start", "base",
        "ib_file", "index_size",
        "position_file", "position_stride",
        "texcoord_file", "texcoord_stride",
        "texture_default_file", "texture_variants", "texture_assignments",
        "normal_map_default_file", "normal_map_variants",
        "light_map_default_file", "light_map_variants",
        "material_map_default_file", "material_map_variants",
    )

    @classmethod
    def field_names(cls):
        return tuple(item.name for item in fields(cls))

    @classmethod
    def from_mapping(cls, value):
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise TypeError(
                f"draw call must be a mapping, got {type(value).__name__}")
        unknown = set(value) - set(cls.field_names())
        if unknown:
            names = ", ".join(sorted(unknown))
            raise TypeError(f"unsupported DrawCall field(s): {names}")
        return cls(**dict(value))

    def resolved(self, group):
        """Return a copy with effective group-level buffer state applied."""
        def inherited(value, key):
            return group.get(key) if value is None else value

        return replace(
            self,
            ib_file=inherited(self.ib_file, "ib_file"),
            index_size=inherited(self.index_size, "index_size"),
            position_file=inherited(self.position_file, "position_file"),
            texcoord_file=inherited(self.texcoord_file, "texcoord_file"),
            position_stride=inherited(
                self.position_stride, "position_stride"),
            texcoord_stride=inherited(
                self.texcoord_stride, "texcoord_stride"),
        )

    def render_identity(self):
        """Stable identity for geometry/material deduplication.

        Visibility alternatives, provenance and the generated label are
        deliberately excluded.  Every field below has been reviewed as state
        that can change the rendered result.
        """
        classified = (set(self._RENDER_IDENTITY_FIELDS)
                      | set(self._NON_RENDER_FIELDS))
        schema = set(self.field_names())
        if classified != schema:
            missing = ", ".join(sorted(schema - classified)) or "none"
            stale = ", ".join(sorted(classified - schema)) or "none"
            raise TypeError(
                "DrawCall identity classification is incomplete "
                f"(unclassified: {missing}; stale: {stale})")
        return _freeze(tuple(
            getattr(self, name) for name in self._RENDER_IDENTITY_FIELDS))

    def set_texture_default(self, role, path):
        prefix = self._TEXTURE_PREFIX[role]
        setattr(self, f"{prefix}_default_file", path)

    def texture_default(self, role):
        prefix = self._TEXTURE_PREFIX[role]
        return getattr(self, f"{prefix}_default_file")

    def set_texture_variants(self, role, variants):
        prefix = self._TEXTURE_PREFIX[role]
        setattr(self, f"{prefix}_variants", variants)

    def texture_rules(self, role):
        if role == "diffuse" and self.texture_assignments:
            return self.texture_assignments
        prefix = self._TEXTURE_PREFIX[role]
        return getattr(self, f"{prefix}_variants")

    # MutableMapping compatibility for existing low-level consumers.
    def _present(self, key):
        value = getattr(self, key)
        if key in self._ALWAYS_PRESENT:
            return True
        if isinstance(value, (list, dict)):
            return bool(value)
        return value is not None and value != ""

    def __getitem__(self, key):
        if key not in self.field_names() or not self._present(key):
            raise KeyError(key)
        return getattr(self, key)

    def __setitem__(self, key, value):
        if key not in self.field_names():
            raise KeyError(f"unsupported DrawCall field: {key}")
        setattr(self, key, value)

    def __delitem__(self, key):
        if key not in self.field_names():
            raise KeyError(key)
        if key in self._ALWAYS_PRESENT:
            raise KeyError(f"cannot delete required DrawCall field: {key}")
        if key in {"texture_variants", "texture_assignments",
                   "normal_map_variants", "light_map_variants",
                   "material_map_variants"}:
            setattr(self, key, [])
        elif key == "label":
            setattr(self, key, "")
        else:
            setattr(self, key, None)

    def __iter__(self) -> Iterator[str]:
        return (name for name in self.field_names() if self._present(name))

    def __len__(self):
        return sum(1 for _ in self)

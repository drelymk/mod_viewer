"""Typed, bounded decoders for the geometry layouts the viewer understands.

The INI parser identifies semantic position/texcoord resources.  This module
owns the smaller question of how bytes in those resources are decoded.  Keep
the supported format table explicit: an unknown layout is not an invitation to
read past a stride or manufacture zero-valued vertices.
"""

from dataclasses import dataclass
import struct


class LayoutError(ValueError):
    """A buffer or draw range does not satisfy its declared layout."""


_FORMATS = {
    "float16x2": ("<ee", 2),
    "float16x4": ("<eeee", 4),
    "float32x2": ("<ff", 2),
    "float32x3": ("<fff", 3),
    "float32x4": ("<ffff", 4),
}

_DXGI_FORMATS = {
    "R16G16_FLOAT": "float16x2",
    "R16G16B16A16_FLOAT": "float16x4",
    "R32G32_FLOAT": "float32x2",
    "R32G32B32_FLOAT": "float32x3",
    "R32G32B32A32_FLOAT": "float32x4",
}


def _normalize_dxgi_format(value):
    normalized = (value or "").strip().upper()
    if normalized.startswith("DXGI_FORMAT_"):
        normalized = normalized[len("DXGI_FORMAT_"):]
    return normalized


@dataclass(frozen=True, slots=True)
class VertexAttributeLayout:
    """One semantic attribute within a strided vertex-buffer binding."""

    semantic: str
    stride: int
    offset: int
    format: str
    slot: int | None = None

    def __post_init__(self):
        if self.format not in _FORMATS:
            raise LayoutError(f"unsupported {self.semantic} format: {self.format}")
        if self.stride <= 0:
            raise LayoutError(f"invalid {self.semantic} stride: {self.stride}")
        if self.offset < 0 or self.offset + self.byte_width > self.stride:
            raise LayoutError(
                f"{self.semantic} attribute does not fit its stride")

    @property
    def struct_format(self):
        return _FORMATS[self.format][0]

    @property
    def component_count(self):
        return _FORMATS[self.format][1]

    @property
    def byte_width(self):
        return struct.calcsize(self.struct_format)

    def vertex_count(self, data):
        if len(data) % self.stride:
            raise LayoutError(
                f"{self.semantic} buffer size is not aligned to stride "
                f"{self.stride}")
        return len(data) // self.stride

    def read(self, data, vertex_index):
        count = self.vertex_count(data)
        if vertex_index < 0 or vertex_index >= count:
            raise LayoutError(
                f"{self.semantic} vertex {vertex_index} is outside 0..{count - 1}")
        offset = vertex_index * self.stride + self.offset
        return struct.unpack_from(self.struct_format, data, offset)


@dataclass(frozen=True, slots=True)
class IndexLayout:
    """A bounded R16/R32 index-buffer decoder."""

    size: int

    def __post_init__(self):
        if self.size not in (2, 4):
            raise LayoutError(f"unsupported index size: {self.size}")

    def index_count(self, data):
        if len(data) % self.size:
            raise LayoutError(
                f"index buffer size is not aligned to {self.size}-byte indices")
        return len(data) // self.size

    def read(self, data, start=0, count=None):
        total = self.index_count(data)
        if start < 0 or start > total:
            raise LayoutError(f"index start {start} is outside 0..{total}")
        if count is None:
            count = total - start
        if count < 0 or start + count > total:
            raise LayoutError(
                f"index range {start}..{start + count} exceeds {total} indices")
        if not count:
            return []
        code = "H" if self.size == 2 else "I"
        return list(struct.unpack_from(
            f"<{count}{code}", data, start * self.size))


def index_layout(resource_format):
    """Return the explicitly supported unsigned index layout, if any."""
    normalized = _normalize_dxgi_format(resource_format)
    if normalized == "R16_UINT":
        return IndexLayout(2)
    if normalized == "R32_UINT":
        return IndexLayout(4)
    return None


@dataclass(frozen=True, slots=True)
class GeometryLayout:
    """The supported semantic layout for one effective draw."""

    position: VertexAttributeLayout
    texcoord: VertexAttributeLayout | None = None

    def validate_vertex_range(self, start, count, position_data,
                              texcoord_data=None):
        """Validate a non-indexed range before callers materialize it."""
        if start < 0:
            raise LayoutError(f"vertex start {start} is negative")
        if count <= 0:
            raise LayoutError(f"vertex count {count} is not positive")
        end = start + count
        position_count = self.position.vertex_count(position_data)
        if end > position_count:
            raise LayoutError(
                f"vertex range {start}..{end} exceeds {position_count} "
                "positions")
        if self.texcoord is not None:
            if texcoord_data is None:
                raise LayoutError("draw has a texcoord layout but no buffer")
            texcoord_count = self.texcoord.vertex_count(texcoord_data)
            if end > texcoord_count:
                raise LayoutError(
                    f"vertex range {start}..{end} exceeds {texcoord_count} "
                    "texcoords")

    def validate_indices(self, indices, position_data, texcoord_data=None):
        if not indices:
            raise LayoutError("draw references no vertices")
        highest = max(indices)
        position_count = self.position.vertex_count(position_data)
        if highest >= position_count:
            raise LayoutError(
                f"draw references position vertex {highest}, but the buffer "
                f"contains {position_count}")
        if self.texcoord is not None:
            if texcoord_data is None:
                raise LayoutError("draw has a texcoord layout but no buffer")
            texcoord_count = self.texcoord.vertex_count(texcoord_data)
            if highest >= texcoord_count:
                raise LayoutError(
                    f"draw references texcoord vertex {highest}, but the buffer "
                    f"contains {texcoord_count}")


def position_layout(stride, resource_format=None, slot=None, offset=0):
    """Return a conservative position descriptor, or ``None`` if unsupported."""
    normalized = _normalize_dxgi_format(resource_format)
    if normalized:
        value_format = _DXGI_FORMATS.get(normalized)
        if value_format not in {"float16x4", "float32x3", "float32x4"}:
            return None
    else:
        # Existing XXMI buffer dumps omit `format` and store float32 XYZ at 0.
        value_format = "float32x3"
    try:
        return VertexAttributeLayout(
            "position", int(stride), int(offset or 0), value_format, slot)
    except (LayoutError, TypeError, ValueError):
        return None


def texcoord_layout(data, stride, resource_format=None, slot=None, offset=None,
                    sample_limit=4096):
    """Resolve an explicit UV descriptor or conservatively detect one.

    Legacy layouts search offsets 0/4.  Additional four-byte-aligned offsets
    are considered only when both UV axes have real spread, keeping broader
    layout support from turning arbitrary blend data into plausible UVs.
    """
    try:
        stride = int(stride)
    except (TypeError, ValueError):
        return None
    normalized = _normalize_dxgi_format(resource_format)
    if normalized:
        value_format = _DXGI_FORMATS.get(normalized)
        if value_format not in {
                "float16x2", "float16x4", "float32x2", "float32x4"}:
            return None
        try:
            return VertexAttributeLayout(
                "texcoord", stride, int(offset or 0), value_format, slot)
        except (LayoutError, TypeError, ValueError):
            return None

    if stride <= 0 or len(data) % stride:
        return None
    total = len(data) // stride
    if not total:
        return None
    step = max(1, total // sample_limit)
    offsets = list(range(0, stride, 4))
    scored = []
    for candidate_offset in offsets:
        for value_format in ("float16x2", "float32x2"):
            try:
                layout = VertexAttributeLayout(
                    "texcoord", stride, candidate_offset, value_format, slot)
            except LayoutError:
                continue
            us, vs, sampled = [], [], 0
            for vertex_index in range(0, total, step):
                values = layout.read(data, vertex_index)
                sampled += 1
                u, v = values[:2]
                if -0.01 <= u <= 2.0 and -0.01 <= v <= 2.0:
                    us.append(u)
                    vs.append(v)
            if not sampled or not us or len(us) / sampled < 0.95:
                continue
            du, dv = max(us) - min(us), max(vs) - min(vs)
            both_live = du >= 1e-4 and dv >= 1e-4
            if candidate_offset not in (0, 4) and not both_live:
                continue
            scored.append((
                both_live, round(len(us) / sampled, 3),
                round(du + dv, 3), candidate_offset,
                value_format == "float32x2", layout,
            ))
    if not scored:
        return None
    # Offsets 0/4 cover the established XXMI layouts. Wider layouts can hold
    # several later float pairs (secondary UVs, tangents or other attributes)
    # that are just as clean and live as the primary UVs. In that ambiguous
    # case, preferring the greatest offset silently remaps an otherwise-correct
    # texture. A broader offset may override a credible legacy candidate only
    # when it has materially better in-range evidence, or when the legacy pair
    # is degenerate.
    legacy = [item for item in scored if item[3] in (0, 4)]
    broader = [item for item in scored if item[3] not in (0, 4)]
    best_legacy = max(legacy, key=lambda item: item[:-1], default=None)
    best_broader = max(broader, key=lambda item: item[:-1], default=None)
    if best_legacy is None:
        return best_broader[-1]
    if best_broader is None:
        return best_legacy[-1]
    materially_cleaner = best_broader[1] >= best_legacy[1] + 0.02
    if best_broader[0] and (not best_legacy[0] or materially_cleaner):
        return best_broader[-1]
    return best_legacy[-1]

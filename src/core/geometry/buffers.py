"""Bounded binary buffer access and vertex-stream decoding helpers."""

import struct
from dataclasses import dataclass
import os


POSITION_STRIDE = 40
POSITION_OFFSET = 0
DEFAULT_UV_OFFSET = 4
INDEX_SIZE = 4

_MAX_BUFFER_FILE_BYTES = 512 * 1024 * 1024
_MAX_TOTAL_BUFFER_BYTES = 2 * 1024 * 1024 * 1024


def _res_get(resources, name):
    """Use the per-INI resource index shared by all resolvers."""
    return resources.get_ci(name)


def read_positions(buf_path, stride=POSITION_STRIDE):
    positions = []
    with open(buf_path, "rb") as file:
        data = file.read()
    for offset in range(0, len(data) - 11, stride):
        positions.append(struct.unpack_from("<fff", data, offset + POSITION_OFFSET))
    return positions


_MIN_AXIS_SPREAD = 1e-4
_MIN_IN_RANGE = 0.95


def _uv_candidate_score(pairs, sampled, uv_off, fmt, *, signed=False):
    """Rank plausible samples without letting isolated signed outliers win."""
    if not pairs or len(pairs) / sampled < _MIN_IN_RANGE:
        return None
    spreads = []
    for axis in (0, 1):
        values = [pair[axis] for pair in pairs]
        if signed:
            values.sort()
            trim = len(values) // 20
            spread = values[-1 - trim] - values[trim]
        else:
            spread = max(values) - min(values)
        spreads.append(spread)
    both_live = min(spreads) >= _MIN_AXIS_SPREAD
    in_range = round(len(pairs) / sampled, 3)
    if signed:
        if not both_live:
            return None
        # Reinterpreted half-floats can have a tiny U range and a large V range.
        # Require distributed variation and prefer evidence on the weaker axis.
        return (in_range, min(spreads), sum(spreads), uv_off, fmt)
    return (both_live, in_range, round(sum(spreads), 3), uv_off, fmt)


def _detect_uv_best(tc_path, stride, n=4096, data=None):
    """Detect half/float UV pairs at offset 0 or 4 from distributed samples.

    Preserve credible nonnegative layouts. When none varies on both axes, try
    signed coordinates with robust axis spreads before retaining the original
    degenerate or stride-fitting fallback. Detection never changes UV values.
    """
    if data is None:
        with open(tc_path, "rb") as file:
            data = file.read()
    size = len(data)
    total = size // stride if stride else 0
    if not total:
        return (DEFAULT_UV_OFFSET, "<ee")
    step = max(1, total // n)
    scored = []
    candidates = []
    for uv_off in (0, 4):
        for fmt in ("<ee", "<ff"):
            fmtsize = struct.calcsize(fmt)
            if uv_off + fmtsize > stride:
                continue
            pairs, sampled = [], 0
            for index in range(0, total, step):
                offset = index * stride + uv_off
                if offset + fmtsize > size:
                    break
                u, v = struct.unpack_from(fmt, data, offset)
                sampled += 1
                # Bounded positive comparisons also exclude NaN and infinity.
                if -2.0 <= u <= 2.0 and -2.0 <= v <= 2.0:
                    pairs.append((u, v))
            candidates.append((pairs, sampled, uv_off, fmt))
            nonnegative = [(u, v) for u, v in pairs
                           if u >= -0.01 and v >= -0.01]
            score = _uv_candidate_score(nonnegative, sampled, uv_off, fmt)
            if score is not None:
                scored.append(score)
    best = max(scored) if scored else None
    if best is not None and best[0]:
        return best[-2:]
    signed_scores = []
    for pairs, sampled, uv_off, fmt in candidates:
        score = _uv_candidate_score(pairs, sampled, uv_off, fmt, signed=True)
        if score is not None:
            signed_scores.append(score)
    if signed_scores:
        return max(signed_scores)[-2:]
    if best is not None:
        return best[-2:]
    for uv_off in (DEFAULT_UV_OFFSET, 0):
        if uv_off + 4 <= stride:
            return (uv_off, "<ee")
    return (0, "<ee")


def read_texcoords(buf_path, stride, uv_off=DEFAULT_UV_OFFSET, uv_fmt="<ee",
                   data=None):
    """Read one UV pair per vertex, dropping a truncated final vertex."""
    uvs = []
    fmtsize = struct.calcsize(uv_fmt)
    if data is None:
        with open(buf_path, "rb") as file:
            data = file.read()
    for offset in range(0, len(data) - uv_off - fmtsize + 1, stride):
        uvs.append(struct.unpack_from(uv_fmt, data, offset + uv_off))
    return uvs


def read_indices(ib_data, start_index=0, count=None, index_size=INDEX_SIZE):
    total = len(ib_data) // index_size
    if count is None:
        count = total - start_index
    end = min(start_index + count, total)
    if end <= start_index:
        return []
    fmt = "H" if index_size == 2 else "I"
    return list(struct.unpack_from(f"<{end - start_index}{fmt}", ib_data,
                                   start_index * index_size))


@dataclass(frozen=True)
class VertexStreams:
    """Resolved position/UV streams and the selected UV representation."""

    position_data: bytes
    position_stride: int
    texcoord_data: bytes
    texcoord_stride: int
    uv_offset: int
    uv_format: str


class BufferStore:
    """Build-scoped raw-buffer cache with the existing safety limits."""

    def __init__(self, source=None, overrides=None):
        self._raw = {}
        self._streams = {}
        self._total_bytes = 0
        self.source = source
        self.overrides = dict(overrides or {})

    def raw(self, path):
        if path not in self._raw:
            data = self._read(path, cached=True)
            self._raw[path] = data
            self._total_bytes += len(data)
        return self._raw[path]

    def transient(self, path):
        """Read one frame buffer without retaining it in the build cache."""
        return self._read(path, cached=False)

    def _read(self, path, *, cached):
        override = self._override_for(path)
        if override is not None:
            data = bytes(override)
            size = len(data)
            if size > _MAX_BUFFER_FILE_BYTES:
                raise ValueError(
                    f"Buffer file is too large ({size / 1048576:.1f} MiB).")
            if cached and self._total_bytes + size > _MAX_TOTAL_BUFFER_BYTES:
                raise ValueError("Mod buffer data exceeds the 2 GiB safety limit.")
            return data
        source_backed = (self.source is not None
                         and getattr(self.source, "virtual", False)
                         and self.source.is_resource_reference(path))
        size = (self.source.size(path)
                if source_backed else os.path.getsize(path))
        if size > _MAX_BUFFER_FILE_BYTES:
            raise ValueError(
                f"Buffer file is too large ({size / 1048576:.1f} MiB).")
        if cached and self._total_bytes + size > _MAX_TOTAL_BUFFER_BYTES:
            raise ValueError("Mod buffer data exceeds the 2 GiB safety limit.")
        if source_backed:
            data = self.source.read_bytes(path)
        else:
            with open(path, "rb") as stream:
                data = stream.read()
        return data

    def _override_for(self, path):
        if not self.overrides:
            return None
        candidates = [path]
        if isinstance(path, str):
            candidates.extend((os.path.normcase(os.path.abspath(path)),
                               path.replace("\\", "/")))
        for candidate in candidates:
            if candidate in self.overrides:
                return self.overrides[candidate]
        return None

    def vertex_streams(
        self,
        position_path,
        position_stride,
        texcoord_path,
        texcoord_stride,
    ):
        key = (position_path, position_stride, texcoord_path, texcoord_stride)
        if key not in self._streams:
            position_data = self.raw(position_path)
            texcoord_data = self.raw(texcoord_path)
            uv_offset, uv_format = _detect_uv_best(
                texcoord_path, texcoord_stride, data=texcoord_data)
            self._streams[key] = VertexStreams(
                position_data, position_stride,
                texcoord_data, texcoord_stride,
                uv_offset, uv_format)
        return self._streams[key]

    def indices(self, path, start, count, index_size=INDEX_SIZE):
        return read_indices(self.raw(path), start, count, index_size)


__all__ = [
    "POSITION_STRIDE", "POSITION_OFFSET", "DEFAULT_UV_OFFSET", "INDEX_SIZE",
    "BufferStore", "VertexStreams", "read_positions", "read_texcoords",
    "read_indices", "_MAX_BUFFER_FILE_BYTES", "_MAX_TOTAL_BUFFER_BYTES",
    "_detect_uv_best", "_res_get",
]

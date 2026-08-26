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
    """Case-insensitive resource lookup (handles WWMI naming inconsistencies)."""
    if not name:
        return {}
    lookup = getattr(resources, "get_ci", None)
    if lookup is not None:
        return lookup(name)
    if name in resources:
        return resources[name]
    nl = name.lower()
    for key, value in resources.items():
        if key.lower() == nl:
            return value
    return {}


def read_positions(buf_path, stride=POSITION_STRIDE):
    positions = []
    with open(buf_path, "rb") as file:
        data = file.read()
    for offset in range(0, len(data) - 11, stride):
        positions.append(struct.unpack_from("<fff", data, offset + POSITION_OFFSET))
    return positions


_MIN_AXIS_SPREAD = 1e-4   # below this an axis is constant, i.e. not a real UV set
_MIN_IN_RANGE = 0.95       # fraction of sampled UVs that must land in [0, 2]


def _detect_uv_best(tc_path, stride, n=4096, data=None):
    """Try (offset 0 or 4) x (float16 or float32) and return the (uv_off, fmt)
    with the largest UV spread where all values are within [0, 2].

    Candidates are sampled evenly across the whole buffer. A candidate whose U
    or V axis is constant is rejected in favor of a real two-dimensional UV
    set, while a small number of outliers is tolerated.
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
    for uv_off in (0, 4):
        for fmt in ("<ee", "<ff"):
            fmtsize = struct.calcsize(fmt)
            if uv_off + fmtsize > stride:
                continue
            us, vs, sampled = [], [], 0
            for index in range(0, total, step):
                offset = index * stride + uv_off
                if offset + fmtsize > size:
                    break
                u, v = struct.unpack_from(fmt, data, offset)
                sampled += 1
                if -0.01 <= u <= 2.0 and -0.01 <= v <= 2.0:
                    us.append(u)
                    vs.append(v)
            if not sampled or not us:
                continue
            in_range = len(us) / sampled
            if in_range < _MIN_IN_RANGE:
                continue
            du, dv = max(us) - min(us), max(vs) - min(vs)
            both_live = du >= _MIN_AXIS_SPREAD and dv >= _MIN_AXIS_SPREAD
            scored.append((both_live, round(in_range, 3), round(du + dv, 3),
                           uv_off, fmt))
    if scored:
        scored.sort(reverse=True)
        return (scored[0][3], scored[0][4])
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

    def __init__(self):
        self._raw = {}
        self._streams = {}
        self._total_bytes = 0

    def raw(self, path):
        if path not in self._raw:
            size = os.path.getsize(path)
            if size > _MAX_BUFFER_FILE_BYTES:
                raise ValueError(
                    f"Buffer file is too large ({size / 1048576:.1f} MiB).")
            if self._total_bytes + size > _MAX_TOTAL_BUFFER_BYTES:
                raise ValueError("Mod buffer data exceeds the 2 GiB safety limit.")
            with open(path, "rb") as file:
                data = file.read()
            self._raw[path] = data
            self._total_bytes += len(data)
        return self._raw[path]

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

"""Share complete loose parts of imported variants while retaining their attributes."""

from dataclasses import dataclass
import math
import struct

from .loose_parts import find_loose_part_groups


_MAX_PART_RUNS = 8000
_MAX_PART_TRIANGLES = 2_000_000


@dataclass(frozen=True)
class ImportedRange:
    count: int
    start: int


@dataclass
class ImportedIndices:
    data: bytes
    variants: list
    unique_ranges: int
    partitioned: bool


def _positions(data, stride):
    identities = {}
    result = []
    for offset in range(0, len(data), stride):
        point = struct.unpack_from("<fff", data, offset)
        if not all(math.isfinite(value) for value in point):
            return None
        if point not in identities:
            identities[point] = len(identities)
        result.append(identities[point])
    return result


def _loose_parts(data, positions):
    """Collect complete islands in original per-part triangle order."""
    triangles = list(struct.iter_unpack("<III", data))
    groups = find_loose_part_groups(triangles, positions) or [list(range(len(triangles)))]
    result = []
    for ordinals in groups:
        part_data = b"".join(data[ordinal * 12:(ordinal + 1) * 12] for ordinal in ordinals)
        result.append(part_data)
    return result, len(groups)


def consolidate_index_ranges(variants, position_data, position_stride, *, split_parts=True,
                             max_runs=None):
    """Compare complete loose parts, retaining per-part order and multiplicity."""
    whole = [[data] if data is not None else [] for data in variants]
    runs = whole
    partitioned = False
    multiple = (split_parts and len({data for data in variants if data is not None}) > 1
                and sum(len(data) // 12 for data in variants if data is not None) <= _MAX_PART_TRIANGLES)
    positions = _positions(position_data, position_stride) if multiple else None
    if positions is not None:
        candidate, part_counts = [], []
        for data in variants:
            items, count = _loose_parts(data, positions) if data is not None else ([], 0)
            candidate.append(items)
            part_counts.append(count)
        limit = _MAX_PART_RUNS if max_runs is None else min(_MAX_PART_RUNS, max_runs)
        if max(part_counts, default=0) > 1 and sum(map(len, candidate)) <= limit:
            runs = candidate
            partitioned = True

    combined = bytearray()
    ranges = {}
    result = []
    for variant in runs:
        selected = []
        for data in variant:
            if data not in ranges:
                ranges[data] = (len(data) // 4, len(combined) // 4)
                combined.extend(data)
            count, start = ranges[data]
            selected.append(ImportedRange(count, start))
        result.append(selected)
    return ImportedIndices(bytes(combined), result, len(ranges), partitioned)

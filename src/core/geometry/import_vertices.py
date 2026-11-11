"""Consolidate imported records without treating export rounding as new geometry."""

import math
import struct


NORMAL_TANGENT_TOLERANCE = 1e-6
_MAX_VECTOR_COMPARISONS = 2_000_000


class ImportVertices:
    """Keep positions, UVs, colors and weights exact; bound normal/tangent noise."""

    def __init__(self):
        self.streams = [bytearray(), bytearray(), bytearray()]
        self._exact = {}
        self._candidates = {}
        self._comparisons_left = _MAX_VECTOR_COMPARISONS
        self.count = 0
        self.canonicalized = 0

    def add(self, parts, position_stride):
        record = b"".join(parts)
        existing = self._exact.get(record)
        if existing is not None:
            return existing
        protected = values = None
        if position_stride == 40 and self._comparisons_left:
            values = struct.unpack_from("<6f", parts[0], 12)
            # Restrict rounding to unit-vector export noise, well away from
            # the normal decoder's fallback/validity boundaries.
            near_unit = all(0.99 ** 2 <= sum(value * value for value in values[start:start + 3]) <= 1.01 ** 2
                            for start in (0, 3))
            if all(math.isfinite(value) for value in values) and near_unit:
                protected = parts[0][:12] + parts[0][36:] + parts[1] + parts[2]
                for candidate, reference in self._candidates.get(protected, ()):
                    self._comparisons_left -= 1
                    matches = all(abs(left - right) <= NORMAL_TANGENT_TOLERANCE
                                  for left, right in zip(values, reference))
                    if not self._comparisons_left:
                        # Exact sharing remains available after exhausting the
                        # bounded approximation work; retain every other record.
                        self._candidates.clear()
                        protected = None
                    if matches:
                        self._exact[record] = candidate
                        self.canonicalized += 1
                        return candidate
                    if not self._comparisons_left:
                        break
        index = self.count
        self.count += 1
        self._exact[record] = index
        for stream, data in zip(self.streams, parts):
            stream.extend(data)
        if protected is not None and self._comparisons_left:
            self._candidates.setdefault(protected, []).append((index, values))
        return index

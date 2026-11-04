"""One capture-wide budget for source extents, mappings and decoded work."""

from dataclasses import dataclass, field


class ResourceLimit(ValueError):
    pass


@dataclass
class CaptureBudget:
    # Source bytes retain the Manager ceiling. Decoded work includes repeated
    # analysis/export and Python containers, rather than just packed DXGI bytes.
    max_source_bytes: int = 16 * 1024**3
    max_file_bytes: int = 512 * 1024**2
    max_decoded_bytes: int = 2 * 1024**3
    max_files: int = 100000
    max_mappings: int = 16384
    source_bytes: int = 0
    mapped_bytes: int = 0
    mappings: int = 0
    decoded_bytes: int = 0
    _sources: dict = field(default_factory=dict)

    def source(self, identity, size):
        previous = self._sources.get(identity, 0)
        total = self.source_bytes + max(0, size - previous)
        if (size < 0 or size > self.max_file_bytes or total > self.max_source_bytes
                or (identity not in self._sources and len(self._sources) >= self.max_files)):
            raise ResourceLimit("Capture exceeds source budget")
        self._sources[identity] = max(previous, size)
        self.source_bytes = total

    def mapping(self, capacity):
        if (self.mapped_bytes + capacity > self.max_source_bytes
                or self.mappings >= self.max_mappings):
            raise ResourceLimit("Capture exceeds mapping budget")
        self.mapped_bytes += capacity
        self.mappings += 1

    def allocate(self, size):
        if size < 0 or self.decoded_bytes + size > self.max_decoded_bytes:
            raise ResourceLimit("Capture exceeds decoded geometry budget")
        self.decoded_bytes += size

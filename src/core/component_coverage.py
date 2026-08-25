"""Authored geometry ownership extracted independently from render draws."""

from dataclasses import dataclass
import re

from .geometry_identity import normalize_geometry_hash


@dataclass(frozen=True, slots=True)
class ComponentCoverageKey:
    """The smallest identity needed to compare authored and Asset ranges."""

    geometry_hash: str
    first_index: int | None = None
    index_count: int | None = None

    def __post_init__(self):
        normalized = normalize_geometry_hash(self.geometry_hash)
        if normalized is None:
            raise ValueError("Component coverage requires a valid geometry hash.")
        object.__setattr__(self, "geometry_hash", normalized)


@dataclass(frozen=True, slots=True)
class AuthoredComponentOverride:
    """One authored TextureOverride claim, including its source section."""

    geometry_hash: str
    first_index: int | None
    index_count: int | None
    ini: str
    section: str
    handling_skip: bool

    @property
    def key(self):
        return ComponentCoverageKey(
            self.geometry_hash, self.first_index, self.index_count)


_HASH_RE = re.compile(r"^hash\s*=\s*(\S+)$", re.I)
_FIRST_RE = re.compile(r"^match_first_index\s*=\s*(\d+)$", re.I)
_COUNT_RE = re.compile(r"^match_index_count\s*=\s*(\d+)$", re.I)
_SKIP_RE = re.compile(r"^handling\s*=\s*skip\b", re.I)


def collect_component_overrides(sections, ini_path):
    """Return geometry ownership declared by TextureOverride sections.

    This extractor intentionally does not inspect draw rows or conditions.
    A hash-only or skipped override is still an authored claim, and the
    composition planner later decides whether the hash belongs to the
    resolved Asset.
    """
    result = []
    for section, lines in (sections or {}).items():
        if not str(section).casefold().startswith("textureoverride"):
            continue
        hashes = []
        first_index = None
        index_count = None
        handling_skip = False
        for raw in lines or ():
            line = str(raw).split(";", 1)[0].strip()
            if not line:
                continue
            match = _HASH_RE.match(line)
            if match:
                geometry_hash = normalize_geometry_hash(match.group(1))
                if geometry_hash:
                    hashes.append(geometry_hash)
                continue
            match = _FIRST_RE.match(line)
            if match:
                first_index = int(match.group(1))
                continue
            match = _COUNT_RE.match(line)
            if match:
                index_count = int(match.group(1))
                continue
            if _SKIP_RE.match(line):
                handling_skip = True
        for geometry_hash in dict.fromkeys(hashes):
            result.append(AuthoredComponentOverride(
                geometry_hash=geometry_hash,
                first_index=first_index,
                index_count=index_count,
                ini=ini_path,
                section=str(section),
                handling_skip=handling_skip,
            ))
    return tuple(result)


__all__ = [
    "AuthoredComponentOverride", "ComponentCoverageKey",
    "collect_component_overrides",
]

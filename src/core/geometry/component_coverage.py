"""Authored geometry ownership extracted independently from render draws."""

from dataclasses import dataclass
import re

from .identity import normalize_geometry_hash
from ..ini.parser import (_reachable_execution_sections,
                          _scan_sections_for_draws)


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
    """One authored TextureOverride claim, including geometry evidence."""

    geometry_hash: str
    first_index: int | None
    index_count: int | None
    ini: str
    section: str
    handling_skip: bool
    geometry_evidence: bool = False
    asset_identity_evidence: bool = True

    @property
    def key(self):
        return ComponentCoverageKey(
            self.geometry_hash, self.first_index, self.index_count)


_HASH_RE = re.compile(r"^hash\s*=\s*(\S+)$", re.I)
_FIRST_RE = re.compile(r"^match_first_index\s*=\s*(\d+)$", re.I)
_COUNT_RE = re.compile(r"^match_index_count\s*=\s*(\d+)$", re.I)
_SKIP_RE = re.compile(r"^handling\s*=\s*skip\b", re.I)
_GEOMETRY_RE = re.compile(
    r"^(?:drawindexed|draw|ib|vb\d+)\s*=\s*(?!null\b)\S+", re.I)
_IB_RE = re.compile(r"^ib\s*=\s*(?!null\b)\S+", re.I)
_DRAW_INDEXED_RE = re.compile(r"^drawindexed\s*=\s*\S+", re.I)
_AUXILIARY_RE = re.compile(
    r"^(?:draw|vb\d+|override_vertex_count|override_byte_stride)\s*=\s*\S+",
    re.I,
)


def collect_component_overrides(sections, ini_path):
    """Return geometry ownership declared by TextureOverride sections.

    Hashes remain available as Asset identity evidence even when a section
    only binds textures. The composition planner uses ``geometry_evidence``
    to avoid treating those texture-only bindings as rendered geometry.
    Explicit ``handling = skip`` remains a coverage claim because it suppresses
    the corresponding original draw. Execution follows the same command-list
    closure as the normal INI scanner so declarations in nested ``run``
    sections retain their component coverage semantics.
    """
    sections = sections or {}
    section_lookup = {str(name).casefold(): name for name in sections}
    scanned = _scan_sections_for_draws(sections)
    result = []
    for section in sections:
        if not str(section).casefold().startswith("textureoverride"):
            continue
        scope_sections = _reachable_execution_sections(
            sections, section, section_lookup)
        scope_lines = [raw for name in scope_sections
                       for raw in sections[name]]
        hashes = []
        handling_skip = False
        geometry_evidence = False
        explicit_asset_identity = False
        auxiliary_geometry = False
        last_first_index = None
        last_index_count = None
        for raw in scope_lines:
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
                last_first_index = int(match.group(1))
                continue
            match = _COUNT_RE.match(line)
            if match:
                last_index_count = int(match.group(1))
                continue
            if _SKIP_RE.match(line):
                handling_skip = True
            if _IB_RE.match(line) or _DRAW_INDEXED_RE.match(line):
                explicit_asset_identity = True
            if _AUXILIARY_RE.match(line):
                auxiliary_geometry = True
            if _GEOMETRY_RE.match(line):
                geometry_evidence = True
        info = scanned.get(section, {})
        draw_matches = [
            item.geometry_match
            for item in info.get("draws", ())
            if item.geometry_match is not None
        ]
        end_match = info.get("geometry_match_at_end")
        if end_match is not None and end_match.first_index is not None:
            # A few generated INIs place match_first_index after their
            # drawindexed line. The scanner records the draw's state before
            # that assignment; the section's final match is the authoritative
            # range for the coverage declaration in that shape.
            draw_matches = [
                match for match in draw_matches
                if not (match.hash == end_match.hash
                        and match.first_index is None)
            ]

        declarations = []
        seen_declarations = set()
        for match in draw_matches:
            key = (match.hash, match.first_index, match.index_count)
            if key in seen_declarations:
                continue
            seen_declarations.add(key)
            declarations.append((match.hash, match.first_index,
                                 match.index_count, True, True))

        if end_match is not None:
            key = (end_match.hash, end_match.first_index,
                   end_match.index_count)
            if key not in seen_declarations:
                declarations.append((
                    end_match.hash, end_match.first_index,
                    end_match.index_count, geometry_evidence,
                    explicit_asset_identity or not auxiliary_geometry))
                seen_declarations.add(key)

        fallback_first = (end_match.first_index if end_match is not None
                          else last_first_index)
        fallback_count = (end_match.index_count if end_match is not None
                          else last_index_count)
        explicit_scope_geometry = explicit_asset_identity
        for geometry_hash in dict.fromkeys(hashes):
            if any(item[0] == geometry_hash for item in declarations):
                continue
            declarations.append((
                geometry_hash, fallback_first, fallback_count,
                geometry_evidence, explicit_scope_geometry
                or not auxiliary_geometry))

        for (geometry_hash, first_index, index_count, item_geometry,
             item_identity) in declarations:
            result.append(AuthoredComponentOverride(
                geometry_hash=geometry_hash,
                first_index=first_index,
                index_count=index_count,
                ini=ini_path,
                section=str(section),
                handling_skip=handling_skip,
                geometry_evidence=item_geometry,
                asset_identity_evidence=item_identity,
            ))
    return tuple(result)


__all__ = [
    "AuthoredComponentOverride", "ComponentCoverageKey",
    "collect_component_overrides",
]

"""Contextual WuWa texture analysis and candidate discovery."""

from dataclasses import dataclass
import os
import re

from core.dds_classifier import DDSClassification, is_color_candidate
from core.ini_parser import TextureOverrideIndex
from core.resource_paths import safe_resource_path

from .asset_enrichment import (
    TextureSemanticEvidence,
    _apply_hash_replacements,
    _cached_dds_classification,
    _has_mod_texture,
)


_COMPONENT_RE = re.compile(
    r"^Component(?P<ordinal>\d+)(?:_\d+)?$", re.I)
_WUWA_COMPONENT_TEXTURE_RE = re.compile(
    r"^Components-(?P<components>\d+(?:-\d+)*)"
    r"\s+t=(?P<tag>.+?)\.dds$",
    re.I,
)
_INFERRED_ROLES = frozenset(("diffuse", "normal_map"))
_DIRECT_SOURCE = "wuwa_direct_analysis"
_FILENAME_SOURCE = "wuwa_filename_analysis"


@dataclass(frozen=True, slots=True)
class _FilenameReplacement:
    replacement: object
    components: tuple[int, ...]
    filename_tag: str


@dataclass(frozen=True, slots=True)
class _Candidate:
    original_hash: str | None
    role: str
    priority: tuple[int, ...]
    classification: DDSClassification
    file: str | None = None
    association_source: str = _FILENAME_SOURCE


def _component_ordinal(group):
    name = group.get("display_name") or group.get("name")
    match = _COMPONENT_RE.fullmatch(str(name or ""))
    return int(match.group("ordinal")) if match else None


def _uses_slotfix(group):
    return any(
        item.role_hint_source == "mod_slot_mapping"
        for draw in group.get("draws", [])
        for item in draw.slot_textures
    )


def _basename(filename):
    # Resource paths are normally POSIX-like even on Windows. Splitting both
    # separators keeps the filename parser independent of the host platform.
    return str(filename).replace("\\", "/").rsplit("/", 1)[-1]


def _file_key(path):
    return os.path.normcase(os.path.normpath(path))


def _is_strong_normal_candidate(classification):
    return (classification.role == "normal_map"
            and classification.confidence == "high")


def _direct_analysis_roles(classification):
    """Map direct draw evidence to roles the contextual resolver may use."""
    roles = []
    if _is_strong_normal_candidate(classification):
        roles.append("normal_map")
    if is_color_candidate(classification):
        roles.append("diffuse")
    return tuple(roles)


def _filename_analysis_roles(classification):
    """Filename association is allowed to produce Diffuse candidates only."""
    return ("diffuse",) if is_color_candidate(classification) else ()


def _direct_dds_candidates(draw, mod_dir, cache):
    """Collect viable direct candidates for one effective draw state."""
    candidates = {}
    for binding in draw.slot_textures:
        if binding.role_hint is not None:
            continue
        filename = binding.file
        if (not isinstance(filename, str)
                or not filename.casefold().endswith(".dds")):
            continue
        path = safe_resource_path(mod_dir, filename)
        if path is None:
            continue
        classification = _cached_dds_classification(path, cache)
        for role in _direct_analysis_roles(classification):
            key = (role, _file_key(path))
            candidates.setdefault(key, _Candidate(
                original_hash=None,
                role=role,
                priority=(0, 0, 0),
                classification=classification,
                file=filename,
                association_source=_DIRECT_SOURCE,
            ))
    return list(candidates.values())


def _parse_filename_replacement(replacement):
    filename = replacement.file
    if not isinstance(filename, str):
        return None
    match = _WUWA_COMPONENT_TEXTURE_RE.fullmatch(_basename(filename))
    if not match:
        return None
    components = tuple(
        int(value) for value in match.group("components").split("-"))
    if not components:
        return None
    return _FilenameReplacement(
        replacement=replacement,
        components=components,
        # This value is deliberately opaque. It is diagnostic metadata only;
        # it is never compared with the INI original hash or normalized.
        filename_tag=match.group("tag"),
    )


def _component_priority(components, ordinal):
    if ordinal not in components:
        return None
    if components == (ordinal,):
        tier = 0
    elif components[0] == ordinal:
        tier = 1
    else:
        tier = 2
    return tier, len(components)


def _filename_priority(components, ordinal):
    priority = _component_priority(components, ordinal)
    return (1, *priority) if priority is not None else None


def _index_metadata(texture_index):
    """Parse replacement filename metadata once for one INI index."""
    result = {}
    for original_hash, replacements in (
            texture_index.replacements_by_hash or {}).items():
        items = []
        for replacement in replacements:
            if not replacement.file:
                continue
            items.append(_parse_filename_replacement(replacement))
        result[original_hash] = tuple(items)
    return result


def _classify_items(items, ordinal, mod_dir, cache):
    """Classify one hash family after verifying component membership."""
    if not items or any(item is None for item in items):
        return None
    if any(_component_priority(item.components, ordinal) is None
           for item in items):
        return None
    classified = []
    for item in items:
        path = safe_resource_path(mod_dir, item.replacement.file)
        if path is None:
            return None
        classified.append(_cached_dds_classification(path, cache))
    return list(zip(items, classified))


def _classify_candidate_family(original_hash, items, ordinal, mod_dir, cache):
    """Return one automatic candidate for an unambiguous hash family."""
    classified = _classify_items(items, ordinal, mod_dir, cache)
    if not classified:
        return None
    role_items = [
        (item, classification, role)
        for item, classification in classified
        for role in _filename_analysis_roles(classification)
    ]
    roles = {role for _item, _classification, role in role_items}
    if len(roles) != 1:
        return None
    role = next(iter(roles))
    role_items = [item for item in role_items if item[2] == role]
    _strongest_item, strongest_classification, _role = min(
        role_items,
        key=lambda item: _filename_priority(item[0].components, ordinal),
    )
    return _Candidate(
        # The filename tag and replacement object cannot define identity.
        original_hash=original_hash,
        role=role,
        priority=min(
            _filename_priority(item.components, ordinal)
            for item, _classification, _role in role_items),
        classification=strongest_classification,
        association_source=_FILENAME_SOURCE,
    )


def _filename_inventory(ordinal, mod_dir, cache, parsed_index):
    """Return every viable filename-associated candidate for the pool."""
    result = []
    for original_hash, items in parsed_index.items():
        classified = _classify_items(items, ordinal, mod_dir, cache)
        if not classified:
            continue
        for item, classification in classified:
            priority = _filename_priority(item.components, ordinal)
            for role in _filename_analysis_roles(classification):
                result.append(_Candidate(
                    original_hash=original_hash,
                    role=role,
                    priority=priority,
                    classification=classification,
                    file=item.replacement.file,
                    association_source=_FILENAME_SOURCE,
                ))
    return result


def _candidate_identity(candidate, mod_dir):
    if candidate.file:
        path = safe_resource_path(mod_dir, candidate.file)
        if path is not None:
            return ("file", _file_key(path))
        return ("file", candidate.file.casefold())
    return ("hash", candidate.original_hash)


def _select_role_candidate(candidates, role, mod_dir):
    role_candidates = [item for item in candidates if item.role == role]
    if not role_candidates:
        return None
    priority = min(item.priority for item in role_candidates)
    strongest = [
        item for item in role_candidates if item.priority == priority
    ]
    identities = {
        _candidate_identity(item, mod_dir) for item in strongest
    }
    if len(identities) != 1:
        # Equal-strength direct files or hash families are ambiguous. A
        # weaker candidate must not silently resolve that ambiguity.
        return None
    return strongest[0]


def _select_candidates(ordinal, mod_dir, cache, parsed_index):
    candidates = []
    for original_hash, items in parsed_index.items():
        candidate = _classify_candidate_family(
            original_hash, items, ordinal, mod_dir, cache)
        if candidate is not None:
            candidates.append(candidate)
    return {
        role: selected
        for role in _INFERRED_ROLES
        if (selected := _select_role_candidate(candidates, role, mod_dir))
    }


def _record_discovered(group, candidates, mod_dir):
    """Store viable candidates as enrichment-owned, temporary group state."""
    discovered = {}
    for candidate in candidates:
        if not candidate.file:
            continue
        path = safe_resource_path(mod_dir, candidate.file)
        if path is None:
            continue
        key = (candidate.role, _file_key(path))
        record = {
            "file": candidate.file,
            "role": candidate.role,
            "semantic_candidate": candidate.role,
            "source": candidate.association_source,
            "priority": candidate.priority,
            "texture_class": candidate.classification.texture_class,
            "confidence": candidate.classification.confidence,
            "evidence": candidate.classification.evidence,
        }
        existing = discovered.get(key)
        if existing is None or record["priority"] < existing["priority"]:
            discovered[key] = record
    group["discovered_textures"] = list(discovered.values())


def _apply_candidate(draw, candidate, texture_index):
    if _has_mod_texture(draw, candidate.role):
        return
    if candidate.file:
        draw.set_texture_default(candidate.role, candidate.file)
        draw.texture_provenance.setdefault(
            candidate.role, candidate.association_source)
        return
    draw.texture_provenance.setdefault(
        candidate.role, candidate.association_source)
    _apply_hash_replacements(
        draw,
        [TextureSemanticEvidence(
            role=candidate.role,
            texture_hash=candidate.original_hash,
            source=candidate.association_source,
            texture_class=candidate.classification.texture_class,
            confidence=candidate.classification.confidence,
            evidence=candidate.classification.evidence,
        )],
        texture_index,
    )


def apply(groups, mod_dir, *, dds_classification_cache=None):
    """Resolve WuWa roles and expose viable candidates to texture pools.

    Direct bindings are stronger than generated filename associations. The
    parser-owned diffuse pool is not mutated; discovered candidates are kept
    in enrichment-owned group state for the mesh builder to merge later.
    """
    cache = (dds_classification_cache
             if dds_classification_cache is not None else {})
    parsed_indexes = {}
    for group in groups or ():
        ordinal = _component_ordinal(group)
        if ordinal is None or _uses_slotfix(group):
            continue

        direct_by_draw = []
        all_candidates = []
        for draw in group.get("draws", []):
            direct = _direct_dds_candidates(draw, mod_dir, cache)
            direct_by_draw.append(direct)
            all_candidates.extend(direct)

        texture_index = group.get("_texture_override_index")
        filename_selected = {}
        filename_inventory = []
        if isinstance(texture_index, TextureOverrideIndex):
            index_key = id(texture_index)
            parsed_index = parsed_indexes.get(index_key)
            if parsed_index is None:
                parsed_index = _index_metadata(texture_index)
                parsed_indexes[index_key] = parsed_index
            filename_selected = _select_candidates(
                ordinal, mod_dir, cache, parsed_index)
            filename_inventory = _filename_inventory(
                ordinal, mod_dir, cache, parsed_index)
            all_candidates.extend(filename_inventory)

        _record_discovered(group, all_candidates, mod_dir)

        for draw, direct_candidates in zip(
                group.get("draws", []), direct_by_draw):
            candidates = direct_candidates + list(filename_selected.values())
            for role in _INFERRED_ROLES:
                selected = _select_role_candidate(candidates, role, mod_dir)
                if selected is not None:
                    _apply_candidate(draw, selected, texture_index)

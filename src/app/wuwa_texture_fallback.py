"""Conservative WuWa texture roles from generated replacement filenames."""

from dataclasses import dataclass
import re

from core.dds_classifier import DDSClassification
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


@dataclass(frozen=True, slots=True)
class _FilenameReplacement:
    replacement: object
    components: frozenset[int]
    specificity: int
    filename_tag: str


@dataclass(frozen=True, slots=True)
class _Candidate:
    original_hash: str
    role: str
    specificity: int
    classification: DDSClassification


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


def _parse_filename_replacement(replacement):
    filename = replacement.file
    if not isinstance(filename, str):
        return None
    match = _WUWA_COMPONENT_TEXTURE_RE.fullmatch(_basename(filename))
    if not match:
        return None
    components = frozenset(
        int(value) for value in match.group("components").split("-"))
    if not components:
        return None
    return _FilenameReplacement(
        replacement=replacement,
        components=components,
        specificity=len(components),
        # This value is deliberately opaque. It is diagnostic metadata only;
        # it is never compared with the INI original hash or normalized.
        filename_tag=match.group("tag"),
    )


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


def _classify_candidate_family(original_hash, items, ordinal, mod_dir, cache):
    """Return one role candidate when a hash family is unambiguous."""
    if not items or any(item is None for item in items):
        return None
    if any(ordinal not in item.components for item in items):
        return None

    classified = []
    for item in items:
        path = safe_resource_path(mod_dir, item.replacement.file)
        if path is None:
            return None
        classified.append(_cached_dds_classification(path, cache))

    role_items = [
        (item, classification)
        for item, classification in zip(items, classified)
        if (classification.role in _INFERRED_ROLES
            and classification.confidence == "high")
    ]
    roles = {classification.role for _item, classification in role_items}
    if len(roles) != 1:
        return None
    role = next(iter(roles))
    role_items = [
        (item, classification)
        for item, classification in role_items
        if classification.role == role
    ]
    _strongest_item, strongest_classification = min(
        role_items, key=lambda pair: pair[0].specificity)
    return _Candidate(
        # Keep the index key as the TextureOverride identity. The filename's
        # opaque tag and replacement object cannot define that identity.
        original_hash=original_hash,
        role=role,
        specificity=min(item.specificity for item, _ in role_items),
        classification=strongest_classification,
    )


def _select_candidates(ordinal, mod_dir, cache, parsed_index):
    candidates = []
    for original_hash, items in parsed_index.items():
        candidate = _classify_candidate_family(
            original_hash, items, ordinal, mod_dir, cache)
        if candidate is not None:
            candidates.append(candidate)

    selected = {}
    for role in _INFERRED_ROLES:
        role_candidates = [item for item in candidates if item.role == role]
        if not role_candidates:
            continue
        specificity = min(item.specificity for item in role_candidates)
        strongest = [
            item for item in role_candidates
            if item.specificity == specificity
        ]
        hashes = {item.original_hash for item in strongest}
        if len(hashes) != 1:
            # Equal-strength distinct hash families are ambiguous. The
            # fallback must never make a first/lexicographic choice.
            continue
        selected[role] = strongest[0]
    return selected


def apply(groups, mod_dir, *, dds_classification_cache=None):
    """Apply conservative WuWa filename/DDS semantic texture evidence.

    The pass is intentionally independent of Asset resolution. It only reads
    replacement files already referenced by each INI's own texture index and
    delegates all conditional texture mutation to the shared enrichment path.
    """
    cache = (dds_classification_cache
             if dds_classification_cache is not None else {})
    parsed_indexes = {}
    for group in groups or ():
        ordinal = _component_ordinal(group)
        if ordinal is None or _uses_slotfix(group):
            continue
        texture_index = group.get("_texture_override_index")
        if not isinstance(texture_index, TextureOverrideIndex):
            continue
        index_key = id(texture_index)
        parsed_index = parsed_indexes.get(index_key)
        if parsed_index is None:
            parsed_index = _index_metadata(texture_index)
            parsed_indexes[index_key] = parsed_index
        selected = _select_candidates(
            ordinal, mod_dir, cache, parsed_index)
        if not selected:
            continue
        for draw in group.get("draws", []):
            evidence = []
            for candidate in selected.values():
                if _has_mod_texture(draw, candidate.role):
                    continue
                draw.texture_provenance.setdefault(
                    candidate.role, "wuwa_filename_dds")
                evidence.append(TextureSemanticEvidence(
                    role=candidate.role,
                    texture_hash=candidate.original_hash,
                    source="wuwa_filename_dds",
                    texture_class=candidate.classification.texture_class,
                    confidence=candidate.classification.confidence,
                    evidence=candidate.classification.evidence,
                ))
            if evidence:
                _apply_hash_replacements(draw, evidence, texture_index)

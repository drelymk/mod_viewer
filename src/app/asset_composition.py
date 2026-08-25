"""Plan explicit, session-only filling of missing original Asset parts."""

from dataclasses import dataclass
import os

from core.component_coverage import (
    AuthoredComponentOverride, ComponentCoverageKey,
    collect_component_overrides,
)
from core.game_profile import collect_game_evidence, resolve_game_detection
from core.ini_sections import extract_resources, merge_sections

from . import asset_folders, asset_index


_GAME_ASSET_TYPES = {
    "genshin": "GIMI",
    "zzz": "ZZMI",
    "wuwa": "WWMI",
}


@dataclass(frozen=True, slots=True)
class AssetCoveragePart:
    """One metadata-only original Asset geometry range."""

    asset_type: str
    root: str
    asset_path: str
    geometry_hash: str
    first_index: int | None
    index_count: int | None
    component_name: str | None = None
    classification: str | None = None
    component_ordinal: int | None = None
    metadata: str | None = None

    @property
    def key(self):
        return ComponentCoverageKey(
            self.geometry_hash, self.first_index, self.index_count)

    def to_dict(self):
        return {
            "geometry_hash": self.geometry_hash,
            "first_index": self.first_index,
            "index_count": self.index_count,
            "component_name": self.component_name,
            "classification": self.classification,
            "component_ordinal": self.component_ordinal,
            "metadata": self.metadata,
        }


@dataclass(frozen=True, slots=True)
class AssetFillPlan:
    """Decision output kept separate from the expensive Asset load."""

    status: str
    asset_type: str | None = None
    root: str | None = None
    asset: dict | None = None
    asset_parts: tuple[AssetCoveragePart, ...] = ()
    covered_parts: tuple[AssetCoveragePart, ...] = ()
    missing_parts: tuple[AssetCoveragePart, ...] = ()
    evidence: tuple[AuthoredComponentOverride, ...] = ()
    index_status: str = "not_configured"

    @property
    def skipped_parts(self):
        skipped_evidence = tuple(
            item for item in self.evidence if item.handling_skip)
        range_counts = {}
        for part in self.asset_parts:
            range_counts[part.geometry_hash] = (
                range_counts.get(part.geometry_hash, 0) + 1)
        return tuple(
            part for part in self.covered_parts
            if any(_part_is_covered(
                part, item, range_counts.get(part.geometry_hash, 0))
                for item in skipped_evidence))

    def to_dict(self):
        asset = None
        if self.asset is not None:
            asset = {"type": self.asset_type, "path": self.asset.get("path")}
        return {
            "status": self.status,
            "asset": asset,
            "coverage": {
                "asset_parts": len(self.asset_parts),
                "handled_parts": len(self.covered_parts),
                "missing_parts": len(self.missing_parts),
                "skipped_parts": len(self.skipped_parts),
            },
            "index_status": self.index_status,
        }


def _asset_type(game):
    value = getattr(game, "game", game)
    return _GAME_ASSET_TYPES.get(value.casefold()) if isinstance(value, str) else None


def _asset_parts(asset_type, root, asset):
    result = []
    for geometry in asset.get("geometry", ()):
        if not isinstance(geometry, dict):
            continue
        geometry_hash = geometry.get("hash")
        if not geometry_hash:
            continue
        ranges = geometry.get("ranges")
        if not isinstance(ranges, list) or not ranges:
            ranges = [{}]
        for item in ranges:
            if not isinstance(item, dict):
                continue
            result.append(AssetCoveragePart(
                asset_type=asset_type,
                root=root,
                asset_path=asset.get("path", ""),
                geometry_hash=geometry_hash,
                first_index=item.get("firstIndex"),
                index_count=item.get("indexCount"),
                component_name=geometry.get("componentName"),
                classification=item.get("classification"),
                component_ordinal=item.get(
                    "componentOrdinal", geometry.get("componentOrdinal")),
                metadata=geometry.get("metadata"),
            ))
    return tuple(result)


def _part_is_covered(part, override, hash_range_count):
    if part.geometry_hash != override.geometry_hash:
        return False
    if not override.handling_skip and not override.geometry_evidence:
        return False
    if hash_range_count <= 1:
        return True
    if override.first_index is None:
        # A hash-only override is intentionally conservative, including when
        # it also contains a count but no match_first_index.
        return True
    if part.first_index != override.first_index:
        return False
    return (override.index_count is None
            or part.index_count is None
            or part.index_count == override.index_count)


def _coverage_for_asset(parts, evidence):
    range_counts = {}
    for part in parts:
        range_counts[part.geometry_hash] = (
            range_counts.get(part.geometry_hash, 0) + 1)
    covered = tuple(
        part for part in parts
        if any(_part_is_covered(
            part, item, range_counts.get(part.geometry_hash, 0))
            for item in evidence))
    covered_keys = {part.key for part in covered}
    missing = tuple(part for part in parts if part.key not in covered_keys)
    return covered, missing


def _candidate_assets(asset_type, evidence, asset_entries):
    candidates = {}
    relevant = []
    for item in evidence:
        matches = {}
        for entry in asset_folders.enabled_entries_for_type(
                asset_entries, asset_type):
            try:
                index = asset_index.load_index(asset_type, entry["path"])
            except asset_index.AssetIndexError:
                continue
            for lookup in asset_index.lookup_geometry(index, item.geometry_hash):
                try:
                    asset = index["assets"][lookup["asset"]]
                except (KeyError, IndexError, TypeError):
                    continue
                identity = (asset_type, entry["path"], asset.get("path"))
                matches[identity] = (entry["path"], index, asset)
        if matches:
            relevant.append(set(matches))
            candidates.update(matches)
    if not relevant:
        return (), candidates
    common = set.intersection(*relevant)
    return tuple(sorted(common, key=lambda value: (
        value[1].casefold(), value[1], value[2].casefold(), value[2]))), candidates


def _mod_sections(context, overrides):
    result = []
    for ini_path in context.ini_paths:
        sections = merge_sections(
            [ini_path], overrides=overrides, documents=context.docs)
        result.append((ini_path, sections))
    return result


def plan_missing_asset_parts(context, overrides=None):
    """Resolve one Asset and subtract all authored component coverage."""
    overrides = overrides or {}
    sections_by_ini = _mod_sections(context, overrides)
    game_evidence = []
    runtime_evidence = []
    texture_api_evidence = []
    authored = []
    for ini_path, sections in sections_by_ini:
        resources = extract_resources(sections)
        game, runtime, texture_api = collect_game_evidence(sections, resources)
        game_evidence.extend(game)
        runtime_evidence.extend(runtime)
        texture_api_evidence.extend(texture_api)
        authored.extend(collect_component_overrides(sections, ini_path))
    detection = resolve_game_detection(
        game_evidence, runtime_evidence, texture_api_evidence)
    asset_type = _asset_type(detection)
    if asset_type is None:
        return AssetFillPlan("asset_not_found", evidence=tuple(authored))

    roots = asset_folders.enabled_entries_for_type(
        context.asset_folders, asset_type)
    if not roots:
        return AssetFillPlan(
            "asset_not_found", asset_type=asset_type,
            evidence=tuple(authored), index_status="not_configured")
    identities, candidates = _candidate_assets(
        asset_type, authored, context.asset_folders)
    if not identities:
        return AssetFillPlan(
            "asset_not_found", asset_type=asset_type,
            evidence=tuple(authored), index_status="ready")
    if len(identities) != 1:
        return AssetFillPlan(
            "asset_ambiguous", asset_type=asset_type,
            evidence=tuple(authored), index_status="ready")

    identity = identities[0]
    root, _index, asset = candidates[identity]
    parts = _asset_parts(asset_type, root, asset)
    covered, missing = _coverage_for_asset(parts, authored)
    status = "nothing_missing" if not missing else "ready"
    return AssetFillPlan(
        status=status, asset_type=asset_type, root=root, asset=asset,
        asset_parts=parts, covered_parts=covered, missing_parts=missing,
        evidence=tuple(authored), index_status="ready")


__all__ = [
    "AssetCoveragePart", "AssetFillPlan", "plan_missing_asset_parts",
]

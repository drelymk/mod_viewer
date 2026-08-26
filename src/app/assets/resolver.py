"""Conservative component binding against enabled Asset indexes."""

from dataclasses import dataclass
import logging

from core.geometry_identity import GeometryMatch

from . import folders as asset_folders
from . import index as asset_index


_LOGGER = logging.getLogger(__name__)


_GAME_ASSET_TYPES = {
    "genshin": "GIMI",
    "zzz": "ZZMI",
    "wuwa": "WWMI",
}


@dataclass(frozen=True, slots=True)
class AssetComponentBinding:
    status: str
    component_status: str | None = None
    range_status: str | None = None
    asset_type: str | None = None
    asset: str | None = None
    root: str | None = None
    geometry_hash: str | None = None
    component_name: str | None = None
    classification: str | None = None
    component_ordinal: int | None = None
    first_index: int | None = None
    index_count: int | None = None
    metadata: str | None = None
    detail_metadata: str | None = None

    def to_dict(self):
        value = {"status": self.status}
        for key, field in (
            ("component_status", "component_status"),
            ("range_status", "range_status"),
            ("asset_type", "asset_type"),
            ("asset", "asset"),
            ("geometry_hash", "geometry_hash"),
            ("component_name", "component_name"),
            ("classification", "classification"),
            ("component_ordinal", "component_ordinal"),
            ("first_index", "first_index"),
            ("index_count", "index_count"),
            ("metadata", "metadata"),
            ("detail_metadata", "detail_metadata"),
        ):
            if getattr(self, field) is not None:
                value[key] = getattr(self, field)
        return value


@dataclass(frozen=True, slots=True)
class AssetResolutionSummary:
    """Read-only aggregate diagnostics for one mod load.

    The summary is deliberately separate from draw identity.  It describes
    what the resolver learned without changing which meshes or textures the
    viewer treats as operational state.
    """

    total_draws: int
    exact_draws: int = 0
    partial_draws: int = 0
    ambiguous_draws: int = 0
    unmatched_draws: int = 0
    index_unavailable_draws: int = 0
    index_status: str = "not_configured"
    configured_roots: int = 0
    ready_roots: int = 0
    unavailable_roots: int = 0
    assets: tuple[str, ...] = ()
    components: tuple[dict, ...] = ()

    def to_dict(self):
        return {
            "total_draws": self.total_draws,
            "exact_draws": self.exact_draws,
            "partial_draws": self.partial_draws,
            "ambiguous_draws": self.ambiguous_draws,
            "unmatched_draws": self.unmatched_draws,
            "index_unavailable_draws": self.index_unavailable_draws,
            "index_status": self.index_status,
            "configured_roots": self.configured_roots,
            "ready_roots": self.ready_roots,
            "unavailable_roots": self.unavailable_roots,
            "assets": list(self.assets),
            "components": [dict(item) for item in self.components],
        }


def _game_id(game):
    value = getattr(game, "game", game)
    return value.casefold() if isinstance(value, str) else value


def _asset_type(game):
    return _GAME_ASSET_TYPES.get(_game_id(game))


def _range_matches(item, geometry_match):
    if not isinstance(item, dict) or geometry_match.first_index is None:
        return False
    if item.get("firstIndex") != geometry_match.first_index:
        return False
    asset_count = item.get("indexCount")
    if (geometry_match.index_count is not None and
            asset_count is not None and
            asset_count != geometry_match.index_count):
        return False
    return True


def _binding(root, index, geometry_match, lookup, geometry, item=None,
             *, range_status="unknown"):
    asset = index.get("assets", [])[lookup["asset"]]
    return AssetComponentBinding(
        status="exact",
        component_status="exact",
        range_status=range_status,
        asset_type=index.get("type"),
        asset=asset.get("path"),
        root=root,
        geometry_hash=geometry_match.hash,
        component_name=geometry.get("componentName"),
        classification=(item.get("classification") if item else None),
        component_ordinal=(
            item.get("componentOrdinal")
            if item and item.get("componentOrdinal") is not None
            else None),
        first_index=(item.get("firstIndex")
                     if item else geometry_match.first_index),
        index_count=(item.get("indexCount")
                     if item else geometry_match.index_count),
        metadata=geometry.get("metadata"),
        detail_metadata=geometry.get("detailMetadata"),
    )


def _load_enabled_indexes(asset_type, asset_entries, *, availability=None,
                          accumulate=False):
    """Load each enabled root's validated index at most once."""
    indexes = []
    entries = asset_folders.enabled_entries_for_type(
        asset_entries or [], asset_type)
    unavailable = 0
    for entry in entries:
        try:
            index = asset_index.load_index(asset_type, entry["path"])
        except asset_index.AssetIndexError:
            unavailable += 1
            continue
        if not index:
            unavailable += 1
            continue
        indexes.append((entry["path"], index))
    if availability is not None:
        if accumulate:
            availability["configured_roots"] = (
                availability.get("configured_roots", 0) + len(entries))
            availability["ready_roots"] = (
                availability.get("ready_roots", 0) + len(indexes))
            availability["unavailable_roots"] = (
                availability.get("unavailable_roots", 0) + unavailable)
        else:
            availability["configured_roots"] = len(entries)
            availability["ready_roots"] = len(indexes)
            availability["unavailable_roots"] = unavailable
    return indexes


def _not_found(asset_type=None):
    return AssetComponentBinding(
        status="not_found", component_status="not_found",
        range_status="unknown", asset_type=asset_type)


def _ambiguous(geometry_match, asset_type, *, component_status="ambiguous",
               range_status="unknown"):
    return AssetComponentBinding(
        status="ambiguous", component_status=component_status,
        range_status=range_status, asset_type=asset_type,
        geometry_hash=geometry_match.hash,
        first_index=geometry_match.first_index,
        index_count=geometry_match.index_count)


def _range_identity(candidate, item):
    """Return the canonical identity of one Asset component range."""
    root, index, lookup, geometry, _ranges = candidate
    try:
        asset = index["assets"][lookup["asset"]]
    except (IndexError, KeyError, TypeError):
        return None
    return (
        root,
        asset.get("path"),
        geometry.get("componentName"),
        geometry.get("componentOrdinal"),
        geometry.get("metadata"),
        geometry.get("detailMetadata"),
        item.get("firstIndex"),
        item.get("indexCount"),
        item.get("classification"),
        item.get("componentOrdinal"),
    )


def _equivalent_matches(range_matches):
    """Return a canonical match when indexed fingerprints agree."""
    if not range_matches:
        return None
    roots = {candidate[0] for candidate, _item in range_matches}
    if len(roots) != 1:
        return None

    fingerprints = []
    known_counts = set()
    for candidate, item in range_matches:
        fingerprint = candidate[3].get("componentFingerprint")
        if not isinstance(fingerprint, str) or not fingerprint:
            return None
        fingerprints.append((fingerprint, item))
    range_shapes = {
        (item.get("firstIndex"), item.get("classification"),
         item.get("componentOrdinal"))
        for _fingerprint, item in fingerprints
    }
    if len(range_shapes) != 1:
        return None
    for _fingerprint, item in fingerprints:
        if item.get("indexCount") is not None:
            known_counts.add(item["indexCount"])
    if len(set(fingerprint for fingerprint, _item in fingerprints)) != 1:
        return None
    if len(known_counts) > 1:
        return None

    return min(
        range_matches,
        key=lambda match: (
            match[0][1]["assets"][match[0][2]["asset"]].get(
                "path", "").casefold(),
            match[0][1]["assets"][match[0][2]["asset"]].get(
                "path", ""),
            match[0][3].get("metadata", ""),
        ),
    )


def _resolve_component_from_indexes(geometry_match, asset_type, indexes,
                                    *, require_range=False,
                                    asset_identity=None):
    if not isinstance(geometry_match, GeometryMatch):
        return _not_found(asset_type)
    if asset_type is None and not indexes:
        return _not_found()
    candidates = []
    seen = set()
    for root, index in indexes:
        for lookup in asset_index.lookup_geometry(index, geometry_match.hash):
            candidate_key = (
                root, lookup.get("asset"),
                lookup.get("geometry"))
            if candidate_key in seen:
                continue
            try:
                asset = index["assets"][lookup["asset"]]
                geometry = asset["geometry"][lookup["geometry"]]
                ranges = geometry.get("ranges", [])
                if not isinstance(ranges, list):
                    continue
            except (IndexError, KeyError, TypeError):
                continue
            if asset_identity is not None:
                candidate_identity = (
                    index.get("type"), root, asset.get("path"))
                if candidate_identity != asset_identity:
                    continue
            seen.add(candidate_key)
            candidates.append((root, index, lookup, geometry, ranges))

    if not candidates:
        return _not_found(asset_type)

    range_matches = [
        (candidate, item)
        for candidate in candidates
        for item in candidate[4]
        if _range_matches(item, geometry_match)
    ]
    unique_matches = {}
    for candidate, item in range_matches:
        identity = _range_identity(candidate, item)
        if identity is not None:
            unique_matches.setdefault(identity, (candidate, item))
    range_matches = list(unique_matches.values())
    if len(range_matches) == 1:
        (root, index, lookup, geometry, _ranges), item = range_matches[0]
        try:
            return _binding(
                root, index, geometry_match, lookup, geometry, item,
                range_status="exact")
        except (IndexError, KeyError, TypeError):
            return _not_found(asset_type)

    if len(range_matches) > 1:
        equivalent = _equivalent_matches(range_matches)
        if equivalent is not None:
            (root, index, lookup, geometry, _ranges), item = equivalent
            try:
                return _binding(
                    root, index, geometry_match, lookup, geometry, item,
                    range_status="exact")
            except (IndexError, KeyError, TypeError):
                return _not_found(asset_type)
        matched_candidates = {
            (candidate[0], candidate[2].get("asset"),
             candidate[2].get("geometry"))
            for candidate, _item in range_matches
        }
        return _ambiguous(
            geometry_match, asset_type,
            component_status=("ambiguous" if len(matched_candidates) > 1
                              else "exact"),
            range_status="ambiguous")

    if require_range:
        return _not_found(asset_type)

    if len(candidates) != 1:
        return _ambiguous(geometry_match, asset_type)

    root, index, lookup, geometry, _ranges = candidates[0]
    try:
        return _binding(
            root, index, geometry_match, lookup, geometry,
            range_status="unknown")
    except (IndexError, KeyError, TypeError):
        return _not_found(asset_type)


def _indexes_for_game(game, asset_entries, *, availability=None):
    """Return the allowed index set while keeping game detection separate.

    A detected game narrows matching to its corresponding Asset type.  An
    unknown game has no trustworthy type evidence, so it may use every
    enabled type, but only exact range matches are eligible for binding.
    """
    asset_type = _asset_type(game)
    if asset_type is not None:
        return asset_type, _load_enabled_indexes(
            asset_type, asset_entries, availability=availability)

    indexes = []
    for candidate_type in dict.fromkeys(_GAME_ASSET_TYPES.values()):
        indexes.extend(_load_enabled_indexes(
            candidate_type, asset_entries, availability=availability,
            accumulate=True))
    return None, indexes


def _infer_asset_type(groups, indexes):
    """Infer an unknown mod's Asset type from unique exact draw evidence.

    Unknown mods may contain enough geometry to identify one configured Asset
    type even when their INI has no runtime namespace marker. Keep ties
    ambiguous; a single draw must not make two matching ecosystems look safe.
    """
    indexes_by_type = {}
    for root, index in indexes:
        indexes_by_type.setdefault(index.get("type"), []).append((root, index))
    scores = {}
    for group in groups or []:
        for draw in group.get("draws", []):
            geometry_match = (draw if isinstance(draw, GeometryMatch)
                              else getattr(draw, "geometry_match", None))
            for asset_type, typed_indexes in indexes_by_type.items():
                binding = _resolve_component_from_indexes(
                    geometry_match, asset_type, typed_indexes,
                    require_range=True)
                if (binding.status == "exact"
                        and binding.component_status == "exact"
                        and binding.range_status == "exact"):
                    scores[asset_type] = scores.get(asset_type, 0) + 1
    if not scores:
        return None
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    winner, score = ordered[0]
    runner = ordered[1][1] if len(ordered) > 1 else 0
    return winner if score > 0 and runner == 0 else None


def _indexes_of_type(indexes, asset_type):
    return [(root, index) for root, index in indexes
            if index.get("type") == asset_type]


def _exact_asset_identity(binding):
    if not isinstance(binding, AssetComponentBinding):
        return None
    if not (binding.status == "exact"
            and binding.component_status == "exact"
            and binding.range_status == "exact"
            and binding.asset_type and binding.root and binding.asset):
        return None
    return binding.asset_type, binding.root, binding.asset


def _group_resolution_scope(group, group_index):
    ini_paths = set()
    for draw in group.get("draws", []):
        for source in getattr(draw, "sources", []) or []:
            if not isinstance(source, dict):
                continue
            ini_path = source.get("ini_path")
            if isinstance(ini_path, str) and ini_path:
                ini_paths.add(ini_path.casefold())
    if ini_paths:
        return "ini", frozenset(ini_paths)
    return "group", group_index


def _scopes_overlap(left, right):
    if left[0] != "ini" or right[0] != "ini":
        return left == right
    return bool(left[1] & right[1])


def resolve_component(geometry_match, game, asset_entries):
    """Resolve one draw's geometry evidence without using texture evidence."""
    asset_type, indexes = _indexes_for_game(game, asset_entries)
    if asset_type is None:
        inferred = _infer_asset_type(
            [{"draws": [geometry_match]}], indexes)
        if inferred is not None:
            asset_type = inferred
            indexes = _indexes_of_type(indexes, inferred)
    return _resolve_component_from_indexes(
        geometry_match, asset_type, indexes,
        require_range=asset_type is None)


def resolve_groups(groups, game, asset_entries, *, availability=None):
    """Return per-draw bindings with conservative shared-Asset narrowing."""
    groups = list(groups or [])
    if availability is not None:
        availability.clear()
    detected_asset_type = _asset_type(game)
    asset_type, indexes = _indexes_for_game(
        game, asset_entries, availability=availability)
    if asset_type is None:
        inferred = _infer_asset_type(groups, indexes)
        if inferred is not None:
            asset_type = inferred
            indexes = _indexes_of_type(indexes, inferred)
    if availability is not None:
        availability["asset_type"] = asset_type
    if availability is not None and asset_type is not None:
        # Keep the normal return shape stable while allowing the caller that
        # owns the aggregate report to distinguish missing/invalid indexes.
        availability.setdefault("unavailable_roots", 0)
    resolved = []
    for group in groups:
        group_bindings = []
        for draw in group.get("draws", []):
            geometry_match = (
                draw if isinstance(draw, GeometryMatch)
                else getattr(draw, "geometry_match", None))
            group_bindings.append(_resolve_component_from_indexes(
                geometry_match, asset_type, indexes,
                require_range=asset_type is None))
        resolved.append(group_bindings)
    scopes = [
        _group_resolution_scope(group, group_index)
        for group_index, group in enumerate(groups)
    ]
    for group_index, (group, group_bindings) in enumerate(
            zip(groups, resolved)):
        exact_identities = set()
        for evidence_index, evidence_bindings in enumerate(resolved):
            if not _scopes_overlap(scopes[group_index], scopes[evidence_index]):
                continue
            for binding in evidence_bindings:
                identity = _exact_asset_identity(binding)
                if identity is not None:
                    exact_identities.add(identity)
        if detected_asset_type is None or len(exact_identities) != 1:
            continue
        preferred_identity = next(iter(exact_identities))
        for draw_index, draw in enumerate(group.get("draws", [])):
            if group_bindings[draw_index].status != "ambiguous":
                continue
            geometry_match = (
                draw if isinstance(draw, GeometryMatch)
                else getattr(draw, "geometry_match", None))
            narrowed = _resolve_component_from_indexes(
                geometry_match, asset_type, indexes,
                require_range=asset_type is None,
                asset_identity=preferred_identity)
            if _exact_asset_identity(narrowed) == preferred_identity:
                group_bindings[draw_index] = narrowed
    return resolved


def _binding_kind(binding):
    if binding.status == "ambiguous":
        return "ambiguous"
    if binding.status == "exact":
        if (binding.component_status == "exact"
                and binding.range_status == "exact"):
            return "exact"
        return "partial"
    if binding.status == "not_found":
        return "unmatched"
    return "partial"


def _component_identity(binding):
    if not isinstance(binding, AssetComponentBinding):
        return None
    component = binding.component_name
    if component is None and binding.component_ordinal is not None:
        component = f"Component {binding.component_ordinal}"
    if binding.asset is None and component is None:
        return None
    return binding.asset, component


def summarize_groups(groups, bindings, availability=None):
    """Aggregate per-draw bindings for health/debug/UI diagnostics."""
    if availability is None:
        availability = {}
    total_draws = sum(len(group.get("draws", [])) for group in groups)
    configured = int(availability.get("configured_roots", 0) or 0)
    ready = int(availability.get("ready_roots", 0) or 0)
    unavailable_roots = int(availability.get("unavailable_roots", 0) or 0)
    if not configured:
        index_status = "not_configured"
    elif ready == configured:
        index_status = "ready"
    elif ready:
        index_status = "partial"
    else:
        index_status = "unavailable"

    counts = {key: 0 for key in (
        "exact", "partial", "ambiguous", "unmatched")}
    assets = set()
    component_summaries = []
    for group, group_bindings in zip(groups, bindings):
        draws = group.get("draws", [])
        kinds = []
        identities = set()
        ranges = set()
        for draw, binding in zip(draws, group_bindings):
            if not isinstance(binding, AssetComponentBinding):
                continue
            kind = _binding_kind(binding)
            kinds.append(kind)
            counts[kind] += 1
            if binding.asset:
                assets.add(binding.asset)
            identity = _component_identity(binding)
            if identity is not None:
                identities.add(identity)
            if binding.range_status == "exact":
                ranges.add((binding.classification, binding.component_ordinal,
                            binding.first_index, binding.index_count))

        if len(identities) > 1:
            status = "mixed"
        elif "ambiguous" in kinds:
            status = "ambiguous"
        elif any(kind in kinds for kind in ("partial", "unmatched")):
            status = "partial"
        elif "exact" in kinds:
            status = "exact"
        else:
            status = "unmatched"
        identity = next(iter(identities), (None, None))
        component_summaries.append({
            "mod_component": (group.get("display_name") or group.get("name")
                              or "Component"),
            "status": status,
            "asset": identity[0],
            "component": identity[1],
            "draws": len(draws),
            "exact_draws": kinds.count("exact"),
            "partial_draws": kinds.count("partial"),
            "ambiguous_draws": kinds.count("ambiguous"),
            "unmatched_draws": kinds.count("unmatched"),
            "ranges_vary": len(ranges) > 1,
        })
        _LOGGER.debug(
            "Asset resolution component %s -> %s/%s; exact=%d partial=%d "
            "ambiguous=%d unmatched=%d",
            component_summaries[-1]["mod_component"], identity[0],
            identity[1], kinds.count("exact"), kinds.count("partial"),
            kinds.count("ambiguous"), kinds.count("unmatched"))

    index_unavailable_draws = (
        total_draws if index_status == "unavailable"
        else counts["unmatched"] if index_status == "partial" else 0)
    return AssetResolutionSummary(
        total_draws=total_draws,
        exact_draws=counts["exact"],
        partial_draws=counts["partial"],
        ambiguous_draws=counts["ambiguous"],
        unmatched_draws=(0 if index_status in ("partial", "unavailable")
                         else counts["unmatched"]),
        index_unavailable_draws=index_unavailable_draws,
        index_status=index_status,
        configured_roots=configured,
        ready_roots=ready,
        unavailable_roots=unavailable_roots,
        assets=tuple(sorted(assets, key=lambda value: (value.casefold(), value))),
        components=tuple(component_summaries),
    )

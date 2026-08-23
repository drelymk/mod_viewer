"""Conservative component binding against enabled Asset indexes."""

from dataclasses import dataclass
import logging

from core.geometry_identity import GeometryMatch

from . import asset_folders, asset_index


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


def _load_enabled_indexes(asset_type, asset_entries, *, availability=None):
    """Load each enabled root's validated index at most once."""
    indexes = []
    entries = asset_folders.enabled_entries_for_type(
        asset_entries or [], asset_type)
    if availability is not None:
        availability["configured_roots"] = len(entries)
    for entry in entries:
        try:
            index = asset_index.load_index(asset_type, entry["path"])
        except asset_index.AssetIndexError:
            if availability is not None:
                availability["unavailable_roots"] = (
                    availability.get("unavailable_roots", 0) + 1)
            continue
        if index:
            indexes.append((entry["path"], index))
    if availability is not None:
        availability["ready_roots"] = len(indexes)
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


def _resolve_component_from_indexes(geometry_match, asset_type, indexes):
    if not isinstance(geometry_match, GeometryMatch):
        return _not_found(asset_type)
    if asset_type is None:
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
                geometry = index["assets"][lookup["asset"]]["geometry"][
                    lookup["geometry"]]
                ranges = geometry.get("ranges", [])
                if not isinstance(ranges, list):
                    continue
            except (IndexError, KeyError, TypeError):
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
    if len(range_matches) == 1:
        (root, index, lookup, geometry, _ranges), item = range_matches[0]
        try:
            return _binding(
                root, index, geometry_match, lookup, geometry, item,
                range_status="exact")
        except (IndexError, KeyError, TypeError):
            return _not_found(asset_type)

    if len(range_matches) > 1:
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

    if len(candidates) != 1:
        return _ambiguous(geometry_match, asset_type)

    root, index, lookup, geometry, _ranges = candidates[0]
    try:
        return _binding(
            root, index, geometry_match, lookup, geometry,
            range_status="unknown")
    except (IndexError, KeyError, TypeError):
        return _not_found(asset_type)


def resolve_component(geometry_match, game, asset_entries):
    """Resolve one draw's geometry evidence without using texture evidence."""
    asset_type = _asset_type(game)
    if asset_type is None:
        return _not_found()
    return _resolve_component_from_indexes(
        geometry_match, asset_type,
        _load_enabled_indexes(asset_type, asset_entries))


def resolve_groups(groups, game, asset_entries, *, availability=None):
    """Return independent per-draw bindings in group order."""
    asset_type = _asset_type(game)
    if availability is not None:
        availability.clear()
        availability["asset_type"] = asset_type
    indexes = (_load_enabled_indexes(
        asset_type, asset_entries, availability=availability)
               if asset_type is not None else [])
    if availability is not None and asset_type is not None:
        # Keep the normal return shape stable while allowing the caller that
        # owns the aggregate report to distinguish missing/invalid indexes.
        availability.setdefault("unavailable_roots", 0)
    return [
        [_resolve_component_from_indexes(
            getattr(draw, "geometry_match", None), asset_type, indexes)
         for draw in group.get("draws", [])]
        for group in groups
    ]


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
    elif ready:
        index_status = "ready"
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

    index_unavailable_draws = total_draws if index_status == "unavailable" else 0
    return AssetResolutionSummary(
        total_draws=total_draws,
        exact_draws=counts["exact"],
        partial_draws=counts["partial"],
        ambiguous_draws=counts["ambiguous"],
        unmatched_draws=(0 if index_status == "unavailable"
                         else counts["unmatched"]),
        index_unavailable_draws=index_unavailable_draws,
        index_status=index_status,
        configured_roots=configured,
        ready_roots=ready,
        unavailable_roots=unavailable_roots,
        assets=tuple(sorted(assets, key=lambda value: (value.casefold(), value))),
        components=tuple(component_summaries),
    )

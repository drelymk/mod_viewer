"""Conservative component binding against enabled Asset indexes."""

from dataclasses import dataclass

from core.geometry_identity import GeometryMatch

from . import asset_folders, asset_index


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


def _load_enabled_indexes(asset_type, asset_entries):
    """Load each enabled root's validated index at most once."""
    indexes = []
    entries = asset_folders.enabled_entries_for_type(
        asset_entries or [], asset_type)
    for entry in entries:
        try:
            index = asset_index.load_index(asset_type, entry["path"])
        except asset_index.AssetIndexError:
            continue
        if index:
            indexes.append((entry["path"], index))
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


def resolve_groups(groups, game, asset_entries):
    """Return independent per-draw bindings in group order."""
    asset_type = _asset_type(game)
    indexes = (_load_enabled_indexes(asset_type, asset_entries)
               if asset_type is not None else [])
    return [
        [_resolve_component_from_indexes(
            getattr(draw, "geometry_match", None), asset_type, indexes)
         for draw in group.get("draws", [])]
        for group in groups
    ]

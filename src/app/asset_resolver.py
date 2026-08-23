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
    return getattr(game, "game", game)


def _range_matches(item, geometry_match):
    if item.get("firstIndex") != geometry_match.first_index:
        return False
    asset_count = item.get("indexCount")
    if (geometry_match.index_count is not None and
            asset_count is not None and
            asset_count != geometry_match.index_count):
        return False
    return True


def _binding(root, index, geometry_match, lookup, geometry, item,
             range_ordinal=None):
    asset = index.get("assets", [])[lookup["asset"]]
    return AssetComponentBinding(
        status="exact",
        asset_type=index.get("type"),
        asset=asset.get("path"),
        root=root,
        geometry_hash=geometry_match.hash,
        component_name=geometry.get("componentName"),
        classification=item.get("classification"),
        component_ordinal=(item.get("componentOrdinal")
                           if item.get("componentOrdinal") is not None
                           else (range_ordinal
                                 if index.get("type") == "WWMI" else None)),
        first_index=item.get("firstIndex"),
        index_count=item.get("indexCount"),
        metadata=geometry.get("metadata"),
        detail_metadata=geometry.get("detailMetadata"),
    )


def resolve_component(geometry_match, game, asset_entries):
    """Resolve one draw's geometry evidence without using texture evidence."""
    if not isinstance(geometry_match, GeometryMatch):
        return AssetComponentBinding(status="not_found")
    asset_type = _GAME_ASSET_TYPES.get(_game_id(game))
    if asset_type is None:
        return AssetComponentBinding(status="not_found")

    matches = []
    entries = asset_folders.enabled_entries_for_type(asset_entries or [], asset_type)
    for entry in entries:
        try:
            index = asset_index.load_index(asset_type, entry["path"])
        except asset_index.AssetIndexError:
            continue
        if not index:
            continue
        for lookup in asset_index.lookup_geometry(index, geometry_match.hash):
            try:
                geometry = index["assets"][lookup["asset"]]["geometry"][
                    lookup["geometry"]]
            except (IndexError, KeyError, TypeError):
                continue
            for range_ordinal, item in enumerate(geometry.get("ranges", [])):
                if _range_matches(item, geometry_match):
                    try:
                        matches.append(_binding(
                            entry["path"], index, geometry_match, lookup,
                            geometry, item, range_ordinal))
                    except (IndexError, KeyError, TypeError):
                        continue

    if not matches:
        return AssetComponentBinding(status="not_found", asset_type=asset_type)
    if len(matches) != 1:
        return AssetComponentBinding(
            status="ambiguous", asset_type=asset_type,
            geometry_hash=geometry_match.hash,
            first_index=geometry_match.first_index,
            index_count=geometry_match.index_count)
    return matches[0]


def resolve_groups(groups, game, asset_entries):
    """Return independent per-draw bindings in group order."""
    return [
        [resolve_component(getattr(draw, "geometry_match", None), game,
                           asset_entries)
         for draw in group.get("draws", [])]
        for group in groups
    ]

"""Direct loading of already-indexed extracted Assets."""

from .models import (AssetAdapterResult, AssetLoadError, AssetLoadResult,
                     AssetMeshPart, AssetTexture)
import logging
import time


_LOGGER = logging.getLogger(__name__)


def load_asset(asset_type, root, record, *, geometry, texture_source=None,
               part_filter=None):
    """Load one exact index record into the application's normalized payload."""
    started = time.perf_counter()
    if asset_type in ("GIMI", "ZZMI"):
        from .hash_asset import load_hash_asset
        adapted = load_hash_asset(
            asset_type, root, record, texture_source=texture_source,
            part_filter=part_filter)
    elif asset_type == "WWMI":
        from .wwmi import load_wwmi_asset
        adapted = load_wwmi_asset(
            root, record, texture_source=texture_source,
            part_filter=part_filter)
    else:
        raise AssetLoadError(f"Unsupported Asset type: {asset_type}.")
    if not isinstance(adapted, AssetAdapterResult):
        adapted = AssetAdapterResult(tuple(adapted))
    if not adapted.parts:
        raise AssetLoadError("Asset contains no renderable geometry parts.")
    result = AssetLoadResult.from_parts(
        asset_type, root, record, adapted.parts, geometry=geometry,
        warnings=adapted.warnings)
    _LOGGER.info(
        "Asset type: %s; Asset: %s; Mesh parts: %d; Textures resolved: %d; "
        "Load time: %.3fs",
        asset_type, record.get("path"), len(adapted.parts),
        sum(len(part.textures) for part in adapted.parts),
        time.perf_counter() - started)
    return result


__all__ = [
    "AssetAdapterResult", "AssetLoadError", "AssetLoadResult",
    "AssetMeshPart", "AssetTexture", "load_asset",
]

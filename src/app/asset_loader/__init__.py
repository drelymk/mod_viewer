"""Direct loading of already-indexed extracted Assets."""

from .models import AssetLoadError, AssetLoadResult, AssetMeshPart, AssetTexture
import logging
import time


_LOGGER = logging.getLogger(__name__)


def load_asset(asset_type, root, record, *, geometry, texture_source=None):
    """Load one exact index record into the application's normalized payload."""
    started = time.perf_counter()
    if asset_type in ("GIMI", "ZZMI"):
        from .hash_asset import load_hash_asset
        parts = load_hash_asset(
            asset_type, root, record, texture_source=texture_source)
    elif asset_type == "WWMI":
        from .wwmi import load_wwmi_asset
        parts = load_wwmi_asset(
            root, record, texture_source=texture_source)
    else:
        raise AssetLoadError(f"Unsupported Asset type: {asset_type}.")
    if not parts:
        raise AssetLoadError("Asset contains no renderable geometry parts.")
    result = AssetLoadResult.from_parts(
        asset_type, root, record, parts, geometry=geometry)
    _LOGGER.info(
        "Asset type: %s; Asset: %s; Mesh parts: %d; Textures resolved: %d; "
        "Load time: %.3fs",
        asset_type, record.get("path"), len(parts),
        sum(len(part.textures) for part in parts),
        time.perf_counter() - started)
    return result


__all__ = [
    "AssetLoadError", "AssetLoadResult", "AssetMeshPart", "AssetTexture",
    "load_asset",
]

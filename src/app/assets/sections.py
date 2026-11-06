"""Preview-owned Asset sections and recoverable folder removal at Export."""

import os

from . import index as asset_index
from . import paths as asset_paths
from . import texture_assignments


def capture(root, record, parts):
    """Authorize only exact metadata folders represented by loaded parts."""
    result = {}
    asset = asset_paths.safe_asset_dir(root, record["path"])
    for part in parts:
        source = part.metadata_path
        if not source or source in result:
            continue
        metadata = asset_paths.safe_asset_path(root, source)
        directory = os.path.dirname(metadata) if metadata else None
        if (not directory or not asset
                or asset_paths.relative_asset_path(asset, directory) is None):
            continue
        lexical = os.path.abspath(os.path.join(root, os.path.dirname(source)))
        if os.path.normcase(lexical) != os.path.normcase(directory):
            continue
        stat = os.stat(directory, follow_symlinks=False)
        result[source] = {"directory": directory, "identity": (stat.st_dev, stat.st_ino)}
    return result


def remove(asset_type, root, record, section, revision):
    """Disable the owning folder and commit its index, rolling back on failure."""
    if texture_assignments.capture(root, record) != revision:
        raise ValueError("Asset metadata changed. Reopen the Asset before exporting.")
    directory = section["directory"]
    relative = asset_paths.relative_asset_path(root, directory)
    if not relative or relative == "." or asset_paths.safe_asset_dir(root, relative) != directory:
        raise ValueError("Asset section folder is missing or changed.")
    stat = os.stat(directory, follow_symlinks=False)
    if (stat.st_dev, stat.st_ino) != section["identity"]:
        raise ValueError("Asset section folder changed. Reopen the Asset before exporting.")
    disabled = os.path.join(os.path.dirname(directory), "DISABLED_" + os.path.basename(directory))
    if os.path.lexists(disabled):
        raise ValueError("A disabled folder with that name already exists.")
    previous = asset_index.snapshot_index(asset_type, root)
    os.rename(directory, disabled)
    try:
        rebuilt = asset_index.build_index(asset_type, root, allow_empty=True)
        asset_index.save_index(rebuilt)
    except BaseException:
        os.rename(disabled, directory)
        asset_index.restore_index(asset_type, root, previous)
        raise
    return rebuilt

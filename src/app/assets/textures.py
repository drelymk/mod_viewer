"""Root-scoped logical identities for Asset texture sources."""

import hashlib
import os

from core.textures import texture_key

from . import folders as asset_folders


def asset_root_id(root):
    return hashlib.sha256(
        asset_folders.normalize_path(root).encode("utf-8")).hexdigest()[:16]


def asset_logical_key(root, filename):
    relative = os.path.relpath(filename, root).replace(os.sep, "/")
    return f"asset/{asset_root_id(root)}/{relative}"


def asset_texture_key(root, filename, role="diffuse"):
    return texture_key(asset_logical_key(root, filename), role)


__all__ = ["asset_logical_key", "asset_root_id", "asset_texture_key"]

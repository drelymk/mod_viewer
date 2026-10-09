"""Lightweight texture inventory, filename associations and source identities."""

import hashlib
import os
import re

from core.mod_source import DirectoryModSource
from core.textures import split_texture_key, texture_key

from . import folders as asset_folders
from . import paths as asset_paths
from .wuwa_texture_names import texture_component_ordinals


IMAGE_EXTENSIONS = (".dds", ".png", ".jpg", ".jpeg")


def component_texture_matches(filename, components, *, wwmi=False):
    """Associate names conservatively; an association supplies no role."""
    if wwmi:
        ordinals = texture_component_ordinals(filename) or ()
        return {key for key, ordinal in components.items() if ordinal in ordinals}
    stem = os.path.splitext(str(filename).replace("\\", "/").rsplit("/", 1)[-1])[0].casefold()
    matches = {str(name).casefold() for name in components.values()
               if name and str(name).casefold() in stem}
    specific = {name for name in matches
                if not any(name != other and name in other for other in matches)}
    if len(specific) != 1:
        return set()
    return {key for key, name in components.items()
            if str(name).casefold() in specific}


def component_texture_name(group, *, wwmi=False):
    name = group.get("display_name") or group.get("name") or ""
    if not wwmi:
        return name
    match = re.fullmatch(r"Component(\d+)(?:_\d+)?", str(name), re.I)
    return int(match.group(1)) if match else None


def exact_texture_binding(binding):
    return (binding is not None and binding.status == "exact"
            and binding.component_status == "exact"
            and binding.range_status == "exact")


def texture_asset_directories(binding):
    """Restrict collection to directories belonging to an exact matched Asset."""
    if not exact_texture_binding(binding) or not binding.root:
        return ()
    directories = []
    metadata_directories = [
            os.path.dirname(value.replace("\\", "/")) or "."
            for value in (binding.metadata, binding.detail_metadata) if value]
    relatives = (metadata_directories if binding.asset_type == "WWMI"
                 else [binding.asset, *metadata_directories])
    for relative in relatives:
        directory = asset_paths.safe_asset_dir(binding.root, relative)
        if directory and directory not in directories:
            directories.append(directory)
    return tuple(directories)


def texture_directory_files(directory, *, recursive):
    """Enumerate supported images at the selected directory depth."""
    if recursive:
        files = DirectoryModSource(directory).list_files()
    else:
        try:
            with os.scandir(directory) as entries:
                files = [entry.name for entry in entries
                         if entry.is_file(follow_symlinks=False)]
        except OSError:
            files = []
    return sorted((filename for filename in files
                   if filename.casefold().endswith(IMAGE_EXTENSIONS)),
                  key=lambda value: (value.casefold(), value))


def collect_texture_inventory(mod_dir, bindings=(), *, source=None, resource_files=()):
    """Enumerate image identities once without reading or publishing images."""
    inventory = {}
    source = source or (DirectoryModSource(mod_dir) if mod_dir else None)
    if source is not None:
        for filename in dict.fromkeys([*source.list_files(), *resource_files]):
            if not isinstance(filename, str):
                continue
            if not filename.casefold().endswith(IMAGE_EXTENSIONS):
                continue
            path = source.resolve_resource(filename)
            if path and source.is_file(path):
                identity = source.logical_path(path)
                inventory.setdefault(os.path.normcase(identity), {
                    "file": identity, "path": path, "identity": identity,
                    "source": "mod",
                })
    scanned = []
    for group_bindings in bindings:
        for binding in group_bindings:
            for directory in texture_asset_directories(binding):
                cache_key = (os.path.normcase(binding.root), os.path.normcase(directory))
                if any(root == cache_key[0] and (
                        parent == cache_key[1] or
                        (recursive and os.path.commonpath((parent, cache_key[1])) == parent))
                       for root, parent, recursive in scanned):
                    continue
                # A metadata file at the registered root permits immediate
                # siblings only, never a traversal of the global library.
                # WWMI ordinals restart in each metadata object.
                recursive = (binding.asset_type != "WWMI" and
                             os.path.normcase(os.path.realpath(binding.root)) != os.path.normcase(directory))
                scanned.append((*cache_key, recursive))
                for filename in texture_directory_files(directory, recursive=recursive):
                    relative = os.path.relpath(os.path.join(directory, filename), binding.root)
                    path = asset_paths.safe_asset_path(binding.root, relative)
                    if not path:
                        continue
                    identity = asset_logical_key(binding.root, path)
                    inventory.setdefault(os.path.normcase(identity), {
                        "file": identity, "path": path, "identity": identity,
                        "source": "asset",
                        "label": f"{os.path.splitext(os.path.basename(path))[0]} (Asset)",
                    })
    return inventory


def asset_root_id(root):
    return hashlib.sha256(
        asset_folders.normalize_path(root).encode("utf-8")).hexdigest()[:16]


def asset_logical_key(root, filename):
    relative = os.path.relpath(filename, root).replace(os.sep, "/")
    return f"asset/{asset_root_id(root)}/{relative}"


def asset_texture_key(root, filename, role="diffuse"):
    return texture_key(asset_logical_key(root, filename), role)


def is_asset_texture_key(key):
    """Return whether a role-aware texture key names the reserved Asset space."""
    _role, relative_path = split_texture_key(key)
    return bool(relative_path and relative_path.startswith("asset/"))


__all__ = [
    "asset_logical_key", "asset_root_id", "asset_texture_key",
    "is_asset_texture_key",
]

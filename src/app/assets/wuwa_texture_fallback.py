"""Three-source WuWa component texture candidate discovery."""

import os
import re

from core.geometry.identity import normalize_geometry_hash
from core.resource_paths import safe_resource_path
from . import paths as asset_paths
from .textures import asset_logical_key
from .wuwa_texture_names import texture_component_ordinals


_COMPONENT_RE = re.compile(
    r"^Component(?P<ordinal>\d+)(?:_\d+)?$", re.I)
_IMAGE_EXTENSIONS = (".dds", ".png", ".jpg", ".jpeg", ".tga")


def _component_ordinal(group):
    name = group.get("display_name") or group.get("name")
    match = _COMPONENT_RE.fullmatch(str(name or ""))
    return int(match.group("ordinal")) if match else None


def _is_texture(filename):
    return isinstance(filename, str) and filename.casefold().endswith(
        _IMAGE_EXTENSIONS)


def _component_hashes(group):
    """Reuse TextureUsage evidence already read for exact WWMI bindings."""
    hashes = set()
    locations = {}
    for draw in group.get("draws", ()):
        binding = draw.asset_binding
        if (binding is None or binding.asset_type != "WWMI"
                or binding.status != "exact"
                or binding.component_status != "exact"
                or binding.range_status != "exact"):
            continue
        draw_hashes = {
            value for item in draw.asset_slot_evidence
            if (value := normalize_geometry_hash(item.get("texture_hash")))
        }
        hashes.update(draw_hashes)
        metadata = binding.detail_metadata or binding.metadata
        if metadata and binding.root and draw_hashes:
            relative_dir = os.path.dirname(metadata.replace("\\", "/")) or "."
            locations.setdefault((binding.root, relative_dir), set()).update(
                draw_hashes)
    return sorted(hashes), locations


def _asset_files(root, relative_dir, cache):
    directory = asset_paths.safe_asset_dir(root, relative_dir)
    if directory is None:
        return ()
    cache_key = os.path.normcase(directory)
    if cache_key not in cache:
        files = []
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if not _is_texture(entry.name):
                        continue
                    try:
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        path = asset_paths.safe_asset_path(
                            root, os.path.join(relative_dir, entry.name))
                        if path:
                            files.append(path)
                    except OSError:
                        continue
        except OSError:
            pass
        cache[cache_key] = sorted(files, key=lambda path: (path.casefold(), path))
    return cache[cache_key]


def apply(groups, mod_dir, source=None, *, resource_files=(), texture_indexes=()):
    """Collect declared names, mod hash replacements, then Asset hash images.

    Resources and indexes are aggregated only after each INI has resolved its
    own resource names. No draw binding or semantic role is changed here.
    """
    named_files = {}
    for filename in resource_files:
        if _is_texture(filename):
            for ordinal in texture_component_ordinals(filename) or ():
                named_files.setdefault(ordinal, []).append(filename)

    asset_cache = {}
    for group in groups or ():
        discovered = {}

        def add_mod_file(filename, candidate_source):
            if not _is_texture(filename):
                return
            path = (source.resolve_resource(filename) if source is not None
                    else safe_resource_path(mod_dir, filename))
            exists = source.is_file if source is not None else os.path.isfile
            if path is None or not exists(path):
                return
            key = (source.logical_path(path).casefold() if source is not None
                   else os.path.normcase(os.path.realpath(path)))
            discovered.setdefault(("mod", key), {
                "file": filename, "source": candidate_source,
            })

        # Source 1: declared Resource filenames identify the component.
        for filename in named_files.get(_component_ordinal(group), ()):
            add_mod_file(filename, "wuwa_filename")

        # Source 2: component hashes select all conditional replacements.
        hashes, locations = _component_hashes(group)
        for texture_hash in hashes:
            for index in texture_indexes:
                for replacement in index.replacements_by_hash.get(
                        texture_hash, ()):
                    add_mod_file(replacement.file, "wuwa_hash")

        # Source 3: images in the exact matched Asset metadata directory.
        for (root, relative_dir), location_hashes in locations.items():
            for path in _asset_files(root, relative_dir, asset_cache):
                name = os.path.basename(path)
                if not any(value in name.casefold() for value in location_hashes):
                    continue
                identity = asset_logical_key(root, path)
                discovered.setdefault(("asset", identity.casefold()), {
                    "file": identity, "path": path, "identity": identity,
                    "label": f"{os.path.splitext(name)[0]} (Asset)",
                    "source": "wuwa_asset_hash",
                })

        group["discovered_textures"] = list(discovered.values())

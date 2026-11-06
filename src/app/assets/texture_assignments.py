"""Commit Asset texture roles into their existing native JSON metadata."""

import json
import os
import tempfile

from . import index as asset_index
from . import paths as asset_paths
from .metadata import entries, texture_role, ROLE_LABELS, authored_roles
from core.textures import TEXTURE_ROLES


ROLES = TEXTURE_ROLES
_MAX_BYTES = 16 * 1024 * 1024


def _path(root, record, relative):
    directory = asset_paths.safe_asset_dir(root, record["path"])
    path = asset_paths.safe_asset_path(root, relative)
    if (not directory or not path or asset_paths.relative_asset_path(directory, path) is None
            or os.path.basename(path).casefold() not in ("hash.json", "metadata.json")):
        raise ValueError("Asset texture metadata is missing or escapes the open Asset.")
    return path


def capture(root, record):
    """Retain exact metadata revisions for the indexed Asset, never browser paths."""
    files = {}
    for geometry in record.get("geometry", ()):
        candidates = geometry.get("metadataPaths", [])
        if not isinstance(candidates, list):
            candidates = []
        for relative in [*candidates, geometry.get("metadata")]:
            if not relative or relative in files:
                continue
            with open(_path(root, record, relative), "rb") as stream:
                data = stream.read(_MAX_BYTES + 1)
            if len(data) > _MAX_BYTES:
                raise ValueError("Asset texture metadata exceeds the size limit.")
            files[relative] = data
    if not files:
        raise ValueError("Asset has no indexed texture metadata.")
    return files


def _row(entry, position):
    rows = entry.get("texture_hashes", entry.get("textureHashes", []))
    if not isinstance(rows, list):
        raise ValueError("Asset texture records must be a list.")
    rows = list(rows)
    while len(rows) <= position:
        rows.append([])
    if not isinstance(rows[position], list):
        raise ValueError("Asset texture range records must be a list.")
    field = "textureHashes" if "texture_hashes" not in entry and "textureHashes" in entry else "texture_hashes"
    entry[field] = rows
    return rows


def _rewrite(part, entry, assignments, root, record):
    rows = _row(entry, part.texture_range)
    original = rows[part.texture_range]
    metadata_dir = os.path.dirname(_path(root, record, part.metadata_path))
    directory = asset_paths.safe_asset_dir(root, record["path"])
    hashes_by_file, legacy_hashes = {}, {}
    def wire_role(label):
        role = texture_role(label)
        return "normal_data" if role == "normal_map" and part.asset_type == "WWMI" else role
    for item in original:
        if not isinstance(item, list) or len(item) < 3:
            continue
        role = wire_role(item[0])
        native_hash = asset_index.normalize_geometry_hash(item[2])
        if len(item) == 3 and role and native_hash:
            legacy_hashes.setdefault(role, set()).add(native_hash)
        if len(item) > 3 and isinstance(item[3], str) and native_hash:
            path = asset_paths.safe_asset_path(root, os.path.relpath(os.path.join(metadata_dir, item[3]), root))
            if path:
                hashes_by_file.setdefault(os.path.normcase(path), set()).add(native_hash)
    # A legacy hash belongs only to the source file the loader actually resolved.
    for role, hashes in legacy_hashes.items():
        source = part.textures.get(role)
        if source and len(hashes) == 1:
            hashes_by_file.setdefault(os.path.normcase(source.path), set()).update(hashes)
    selected = []
    authored = set(authored_roles(entry, part.texture_range))
    for role, relative in assignments.items():
        label = "NormalMap" if role == "normal_data" and part.asset_type == "WWMI" else ROLE_LABELS[role]
        authored.add(texture_role(label))
        if relative is None:
            # Explicit clears prevent legacy filename fallback without claiming a hash.
            selected.append([label, "", "", None])
            continue
        path = asset_paths.safe_asset_path(directory, relative)
        if not path:
            raise ValueError("An assigned Asset texture is no longer available.")
        candidates = hashes_by_file.get(os.path.normcase(path), set())
        native_hash = next(iter(candidates)) if len(candidates) == 1 else ""
        selected.append([label, os.path.splitext(path)[1], native_hash,
                         os.path.relpath(path, metadata_dir).replace(os.sep, "/")])
    # Retain unedited records exactly; only edited roles lose their old labels.
    for item in original:
        if not isinstance(item, list) or len(item) < 3:
            selected.append(item)
            continue
        candidate = list(item)
        role = wire_role(candidate[0])
        if role in assignments:
            if not candidate[2] and (len(candidate) < 4 or not candidate[3]):
                continue
            if len(candidate) == 3 and part.textures.get(role):
                candidate.append(os.path.relpath(part.textures[role].path, metadata_dir).replace(os.sep, "/"))
            candidate[0] = "TextureCandidate"
        if candidate not in selected:
            selected.append(candidate)
    rows[part.texture_range] = selected
    ranges = entry.get("texture_assignment_roles", {})
    if not isinstance(ranges, dict):
        raise ValueError("Invalid authored Asset texture ranges.")
    entry["texture_assignment_roles"] = {**ranges, str(part.texture_range): sorted(authored)}


def _temporary(path, data):
    descriptor, temporary = tempfile.mkstemp(prefix=".asset-textures-", suffix=".tmp", dir=os.path.dirname(path))
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        return temporary
    except BaseException:
        os.remove(temporary)
        raise


def save(root, record, parts, assignments, revision):
    """Commit native metadata and its index together, rolling both back on failure."""
    if capture(root, record) != revision:
        raise ValueError("Asset metadata changed. Reopen the Asset before exporting.")
    changed = {parts[key].metadata_path for key in assignments}
    documents = {relative: json.loads(revision[relative].decode("utf-8-sig")) for relative in revision if relative in changed}
    for key, roles in assignments.items():
        part = parts[key]
        components = entries(documents[part.metadata_path])
        if part.metadata_entry is None or not 0 <= part.metadata_entry < len(components):
            raise ValueError("Asset component metadata changed. Reopen the Asset before exporting.")
        _rewrite(part, components[part.metadata_entry], roles, root, record)
    asset_type = parts[next(iter(parts))].asset_type
    previous_index = asset_index.snapshot_index(asset_type, root)
    prepared, backups, installed, updated = {}, {}, [], {}
    index_attempted = False
    preserve_backups = False
    try:
        for relative, document in documents.items():
            path = _path(root, record, relative)
            payload = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            updated[relative] = payload
            prepared[path] = _temporary(path, payload)
            backups[path] = _temporary(path, revision[relative])
        if capture(root, record) != revision:
            raise ValueError("Asset metadata changed. Reopen the Asset before exporting.")
        try:
            for path, temporary in prepared.items():
                os.replace(temporary, path)
                installed.append(path)
            rebuilt = asset_index.build_index(asset_type, root)
            if asset_index.find_asset_by_path(rebuilt, record["path"]) is None:
                raise ValueError("Updated Asset could not be indexed.")
            index_attempted = True
            asset_index.save_index(rebuilt)
        except BaseException:
            try:
                for path in reversed(installed):
                    os.replace(backups[path], path)
                if index_attempted:
                    asset_index.restore_index(asset_type, root, previous_index)
            except BaseException:
                preserve_backups = True
                raise ValueError("Asset texture rollback failed; recovery files remain beside the metadata.") from None
            raise
    finally:
        for temporary in [*prepared.values(), *([] if preserve_backups else backups.values())]:
            try:
                os.remove(temporary)
            except OSError:
                pass
    return {**revision, **updated}

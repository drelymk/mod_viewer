"""Filesystem boundaries for registered extracted Asset roots."""

import os

from . import folders as asset_folders


def is_disabled_folder(name):
    """Folder prefixes are case-insensitive, including manually disabled Assets."""
    return name.casefold().startswith("disabled")


def is_disabled_path(relative, *, file=False):
    if not isinstance(relative, str):
        return False
    parts = relative.replace("\\", "/").split("/")
    return any(is_disabled_folder(part) for part in (parts[:-1] if file else parts))


def _real_within(path, root):
    """Return whether two resolved paths share the registered root."""
    try:
        return os.path.commonpath((path, root)) == root
    except ValueError:
        return False


def _relative_candidate(root, relative, *, file=False):
    if not isinstance(root, str) or not isinstance(relative, str):
        return None
    if not relative or os.path.isabs(relative) or os.path.splitdrive(relative)[0]:
        return None
    if is_disabled_path(relative, file=file):
        return None
    # Windows accepts both separators even when a caller supplied a path from
    # a JSON index written on another platform.
    relative = relative.replace("/", os.sep).replace("\\", os.sep)
    root = asset_folders.normalize_path(root)
    if not root or not os.path.isdir(root):
        return None
    candidate = os.path.abspath(os.path.join(root, relative))
    resolved_root = os.path.realpath(root)
    resolved = os.path.realpath(candidate)
    if not _real_within(resolved, resolved_root):
        return None
    if is_disabled_path(os.path.relpath(resolved, resolved_root), file=file):
        return None
    return resolved


def safe_asset_path(root, relative):
    """Resolve an existing regular file below a registered Asset root.

    The real-path containment check intentionally rejects symlinks that point
    outside the root.  Returning ``None`` keeps this helper suitable for
    optional metadata and texture evidence; direct loaders turn it into a
    readable load error.
    """
    candidate = _relative_candidate(root, relative, file=True)
    if not candidate or not os.path.isfile(candidate):
        return None
    return candidate


def safe_asset_dir(root, relative):
    """Resolve an existing directory below a registered Asset root."""
    candidate = _relative_candidate(root, relative)
    if not candidate or not os.path.isdir(candidate):
        return None
    return candidate


def relative_asset_path(root, path):
    """Return a normalized root-relative path after real containment checks."""
    if not isinstance(root, str) or not isinstance(path, str):
        return None
    root = asset_folders.normalize_path(root)
    path = asset_folders.normalize_path(path)
    if not root or not path:
        return None
    resolved_root = os.path.realpath(root)
    resolved_path = os.path.realpath(path)
    if not _real_within(resolved_path, resolved_root):
        return None
    return os.path.relpath(path, root).replace(os.sep, "/")


__all__ = ["safe_asset_path", "safe_asset_dir", "relative_asset_path",
           "is_disabled_folder", "is_disabled_path"]

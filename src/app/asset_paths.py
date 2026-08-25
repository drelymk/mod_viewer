"""Filesystem boundaries for registered extracted Asset roots."""

import os

from . import asset_folders


def _real_within(path, root):
    """Return whether two resolved paths share the registered root."""
    try:
        return os.path.commonpath((path, root)) == root
    except ValueError:
        return False


def _relative_candidate(root, relative):
    if not isinstance(root, str) or not isinstance(relative, str):
        return None
    if not relative or os.path.isabs(relative) or os.path.splitdrive(relative)[0]:
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
    return resolved


def safe_asset_path(root, relative):
    """Resolve an existing regular file below a registered Asset root.

    The real-path containment check intentionally rejects symlinks that point
    outside the root.  Returning ``None`` keeps this helper suitable for
    optional metadata and texture evidence; direct loaders turn it into a
    readable load error.
    """
    candidate = _relative_candidate(root, relative)
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


__all__ = ["safe_asset_path", "safe_asset_dir", "relative_asset_path"]

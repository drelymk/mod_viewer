"""Shared sandbox rules for mod-authored resource filenames."""

import ntpath
import os


_MAX_ESCAPE_DEPTH = 1


def _canonical(path):
    """Return the normalized filesystem identity for a path."""
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def _within(target, root):
    try:
        return os.path.commonpath([target, root]) == root
    except ValueError:
        # Windows drives (and other incompatible path roots) have no common
        # path and must never be treated as descendants.
        return False


def safe_resource_path(mod_dir, relative_path):
    """Resolve a mod-authored resource while allowing one parent level.

    Canonical paths are used only for the sandbox containment check.  Return
    the candidate path in the caller's original root namespace so downstream
    relative-path identities stay stable when Windows exposes the same folder
    through aliases/junctions with different spellings.
    """
    if not relative_path:
        return None
    try:
        relative_path = os.fspath(relative_path)
    except TypeError:
        return None
    if not isinstance(relative_path, str):
        return None
    if (os.path.isabs(relative_path)
            or ntpath.isabs(relative_path)
            or os.path.splitdrive(relative_path)[0]
            or ntpath.splitdrive(relative_path)[0]):
        return None
    root_path = os.path.abspath(mod_dir)
    root = _canonical(root_path)
    target_path = os.path.abspath(os.path.join(root_path, relative_path))
    target = _canonical(target_path)
    if not _within(target, root):
        ceiling = root
        for _ in range(_MAX_ESCAPE_DEPTH):
            ceiling = os.path.dirname(ceiling)
        if not _within(target, ceiling):
            return None
    return target_path

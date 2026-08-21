"""Shared sandbox rules for mod-authored resource filenames."""

import os


_MAX_ESCAPE_DEPTH = 1


def _within(target, root):
    return target == root or target.startswith(root + os.sep)


def safe_resource_path(mod_dir, relative_path):
    """Resolve a mod-authored resource while allowing one parent level."""
    if not relative_path:
        return None
    if os.path.isabs(relative_path) or os.path.splitdrive(relative_path)[0]:
        return None
    root = os.path.abspath(mod_dir)
    target = os.path.abspath(os.path.join(root, relative_path))
    if not _within(target, root):
        ceiling = root
        for _ in range(_MAX_ESCAPE_DEPTH):
            ceiling = os.path.dirname(ceiling)
        if not _within(target, ceiling):
            return None
    return target

r"""Locate real mod libraries for the opt-in corpus tests.

These tests scan real 3DMigoto mod folders to sanity-check parsing/rewriting
against a large, real-world sample. They're skipped automatically if no
corpus is configured. To enable them, set MOD_VIEWER_TEST_CORPUS to one or
more mod-library root folders (os.pathsep-separated) in the test environment.
"""
import os


def corpus_roots():
    raw = os.environ.get("MOD_VIEWER_TEST_CORPUS", "")
    return [p for p in raw.split(os.pathsep) if p]


def _walk(root):
    """Yield active corpus directories and files below one configured root."""
    if not os.path.isdir(root):
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if not d.upper().startswith("DISABLED"))
        active_files = sorted(
            f for f in filenames if not f.upper().startswith("DISABLED"))
        yield dirpath, active_files


def active_ini_files():
    """Return every active INI in the configured corpus roots."""
    files = []
    for root in corpus_roots():
        for directory, filenames in _walk(root) or ():
            files.extend(os.path.join(directory, name) for name in filenames
                         if name.lower().endswith(".ini"))
    return files


def mod_directories():
    """Return directories containing at least one active INI."""
    directories = []
    for root in corpus_roots():
        for directory, filenames in _walk(root) or ():
            if any(name.lower().endswith(".ini") for name in filenames):
                directories.append(directory)
    return directories


def sample_inis(limit, seed=7):
    """Return a stable-size shuffled sample of active corpus INIs."""
    files = active_ini_files()
    import random
    random.Random(seed).shuffle(files)
    return files[:limit]


def sample_mods(limit, seed=11):
    """Return a stable-size shuffled sample of corpus mod directories."""
    directories = mod_directories()
    import random
    random.Random(seed).shuffle(directories)
    return directories[:limit]

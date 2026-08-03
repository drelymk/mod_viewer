r"""Locate real mod libraries for the opt-in corpus tests.

These tests scan real 3DMigoto mod folders to sanity-check parsing/rewriting
against a large, real-world sample. They're skipped automatically if no
corpus is configured. To enable them, set MOD_VIEWER_TEST_CORPUS to one or
more mod-library root folders (os.pathsep-separated), e.g. on Windows:
    set MOD_VIEWER_TEST_CORPUS=C:\path\to\Mods;C:\path\to\OtherMods
"""
import os


def corpus_roots():
    raw = os.environ.get("MOD_VIEWER_TEST_CORPUS", "")
    return [p for p in raw.split(os.pathsep) if p]

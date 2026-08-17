"""Tests for the shared real-mod corpus discovery helpers."""

import os
import tempfile
from unittest.mock import patch


from _corpus import active_ini_files, mod_directories, sample_inis, sample_mods


def test_corpus_helpers_share_disabled_filtering_and_sampling():
    with tempfile.TemporaryDirectory() as root:
        active_dir = os.path.join(root, "active")
        disabled_dir = os.path.join(root, "DISABLED-old")
        os.makedirs(active_dir)
        os.makedirs(disabled_dir)
        for path in (
                os.path.join(active_dir, "one.ini"),
                os.path.join(active_dir, "two.ini"),
                os.path.join(active_dir, "DISABLED-three.ini"),
                os.path.join(disabled_dir, "hidden.ini")):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("[Constants]\n")

        with patch.dict(os.environ, {"MOD_VIEWER_TEST_CORPUS": root}, clear=False):
            inis = active_ini_files()
            mods = mod_directories()
            assert inis == [os.path.join(active_dir, "one.ini"),
                            os.path.join(active_dir, "two.ini")]
            assert mods == [active_dir]
            assert sample_inis(1, seed=7) == [inis[0]]
            assert sample_mods(1, seed=11) == [active_dir]

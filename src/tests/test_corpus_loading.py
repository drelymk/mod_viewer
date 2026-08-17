"""Real-mod corpus regressions at the loader/provenance boundary."""

import pytest

from test_provenance import (
    _corpus_case_diffuse_resolution_corpus_sweep,
    _corpus_case_real_mods,
)


_CASES = (_corpus_case_real_mods, _corpus_case_diffuse_resolution_corpus_sweep)


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case.__name__)
def test_corpus_case(case):
    case()

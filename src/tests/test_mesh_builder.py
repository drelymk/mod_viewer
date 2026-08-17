"""Mesh-buffer binding, draw fallback, and index decoding regressions."""

import pytest

from test_provenance import (
    _mesh_case_component_name_ending_in_uppercase_abbreviation,
    _mesh_case_cross_ib_vb_reassignment_ini_parser,
    _mesh_case_cross_ib_vb_reassignment_mesh_builder,
    _mesh_case_handling_skip_with_no_drawindexed_draws_nothing,
    _mesh_case_implicit_whole_buffer_draw_keeps_its_diffuse,
    _mesh_case_mid_section_ib_reassignment_ini_parser,
    _mesh_case_mid_section_ib_reassignment_mesh_builder,
    _mesh_case_r16_index_buffer,
)


_CASES = (
    _mesh_case_mid_section_ib_reassignment_ini_parser,
    _mesh_case_mid_section_ib_reassignment_mesh_builder,
    _mesh_case_cross_ib_vb_reassignment_ini_parser,
    _mesh_case_cross_ib_vb_reassignment_mesh_builder,
    _mesh_case_handling_skip_with_no_drawindexed_draws_nothing,
    _mesh_case_component_name_ending_in_uppercase_abbreviation,
    _mesh_case_implicit_whole_buffer_draw_keeps_its_diffuse,
    _mesh_case_r16_index_buffer,
)


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case.__name__)
def test_mesh_case(case):
    case()

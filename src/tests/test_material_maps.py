"""Diffuse, normal, light, and material-map resolution regressions."""

import pytest

from test_provenance import (
    _material_case_authored_auxiliary_material_maps,
    _material_case_direct_ps_t_auxiliary_material_maps,
    _material_case_diffuse_assignment_without_ref_keyword,
    _material_case_multi_reassignment_diffuse_resolution,
    _material_case_multi_reassignment_mesh_builder,
    _material_case_packed_light_map_uses_blue_mask_without_colour_cast,
    _material_case_same_variable_partial_diffuse_chains_keep_assignment_history,
    _material_case_toggle_driven_diffuse_swap_ini_parser,
    _material_case_toggle_driven_diffuse_swap_mesh_builder,
    _material_case_two_channel_normal_reconstructs_z,
)


_CASES = (
    _material_case_authored_auxiliary_material_maps,
    _material_case_direct_ps_t_auxiliary_material_maps,
    _material_case_two_channel_normal_reconstructs_z,
    _material_case_packed_light_map_uses_blue_mask_without_colour_cast,
    _material_case_toggle_driven_diffuse_swap_ini_parser,
    _material_case_toggle_driven_diffuse_swap_mesh_builder,
    _material_case_same_variable_partial_diffuse_chains_keep_assignment_history,
    _material_case_multi_reassignment_diffuse_resolution,
    _material_case_multi_reassignment_mesh_builder,
    _material_case_diffuse_assignment_without_ref_keyword,
)


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case.__name__)
def test_material_case(case):
    case()

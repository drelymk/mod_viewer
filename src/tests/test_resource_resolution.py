"""Resource path, runtime-copy, and command-list resolution regressions."""

import pytest

from test_provenance import (
    _resource_case_absolute_resource_path_blocked,
    _resource_case_deep_resource_path_traversal_blocked,
    _resource_case_ll_skeleton_compute_output_uses_rest_position,
    _resource_case_resource_path_may_reach_a_sibling_folder,
    _resource_case_root_texture_picker_accepts_windows_case_variation,
    _resource_case_run_inlines_nested_commandlist_draws,
    _resource_case_runtime_position_copy_resolution,
    _resource_case_toggle_panel_provenance,
)


_CASES = (
    _resource_case_resource_path_may_reach_a_sibling_folder,
    _resource_case_absolute_resource_path_blocked,
    _resource_case_deep_resource_path_traversal_blocked,
    _resource_case_root_texture_picker_accepts_windows_case_variation,
    _resource_case_toggle_panel_provenance,
    _resource_case_runtime_position_copy_resolution,
    _resource_case_ll_skeleton_compute_output_uses_rest_position,
    _resource_case_run_inlines_nested_commandlist_draws,
)


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case.__name__)
def test_resource_case(case):
    case()

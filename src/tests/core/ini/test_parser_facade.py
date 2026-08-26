"""Compatibility imports retained by the INI parser facade."""

from core.ini import dnf, draw_groups, draw_scan, menu, parser, sections
from core.ini import texture_roles, toggles


def test_parser_facade_keeps_the_established_read_surface():
    assert parser.__all__ == [
        "SrcLine", "extract_resources", "discover_ini_paths", "find_inis",
        "first_source", "line_source", "merge_sections", "parse_sections",
        "sections_from_document",
        "DNF_FALSE", "DNF_TRUE", "build_bool_alias_map", "dnf_and",
        "dnf_not", "dnf_or", "normalize_dnf", "parse_condition_dnf",
        "extract_menu_toggles", "extract_menu_var_names",
        "extract_toggle_keys", "extract_toggle_var_names",
        "extract_variable_defaults", "gating_var_names", "build_draw_groups",
    ]
    assert parser.parse_sections is sections.parse_sections
    assert parser.extract_resources is sections.extract_resources
    assert parser.dnf_and is dnf.dnf_and
    assert parser.extract_menu_toggles is menu.extract_menu_toggles
    assert parser.extract_toggle_keys is toggles.extract_toggle_keys
    assert parser.build_draw_groups is draw_groups.build_draw_groups
    assert parser.gating_var_names is draw_scan.gating_var_names
    assert parser._scan_sections_for_draws is draw_scan._scan_sections_for_draws
    assert (parser._reachable_execution_sections
            is draw_scan._reachable_execution_sections)
    assert parser.TextureOverrideIndex is texture_roles.TextureOverrideIndex
    assert parser.TextureReplacement is texture_roles.TextureReplacement

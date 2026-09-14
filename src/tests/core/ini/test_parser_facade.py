"""The parser facade exposes semantic analysis, not legacy UI detectors."""

from core.ini import control_graph, dnf, draw_groups, draw_scan, parser, sections
from core.ini import texture_roles


def test_parser_facade_exposes_the_semantic_read_surface():
    assert parser.__all__ == [
        "SrcLine", "extract_resources", "discover_ini_paths", "find_inis",
        "first_source", "line_source", "merge_sections", "parse_sections",
        "sections_from_document",
        "DNF_FALSE", "DNF_TRUE", "build_bool_alias_map", "dnf_and",
        "dnf_not", "dnf_or", "normalize_dnf", "parse_condition_dnf",
        "Action", "ControlGraph", "Controller", "RenderEffect",
        "build_control_graph", "KeyInput", "ProgramFacts", "RunEdge",
        "VariableDeclaration", "VariableWrite", "scan_program", "IniSource",
        "VariableId", "VariableResolver", "source_from_path",
        "build_draw_groups",
    ]
    assert parser.parse_sections is sections.parse_sections
    assert parser.extract_resources is sections.extract_resources
    assert parser.dnf_and is dnf.dnf_and
    assert parser.build_control_graph is control_graph.build_control_graph
    assert parser.RenderEffect is control_graph.RenderEffect
    assert parser.build_draw_groups is draw_groups.build_draw_groups
    assert parser._scan_sections_for_draws is draw_scan._scan_sections_for_draws
    assert (parser._reachable_execution_sections
            is draw_scan._reachable_execution_sections)
    assert parser.TextureOverrideIndex is texture_roles.TextureOverrideIndex
    assert parser.TextureReplacement is texture_roles.TextureReplacement

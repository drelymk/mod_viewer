"""Public facade for the read-only INI analysis pipeline.

The application control path is owned by the semantic program and control
graph modules.  Legacy menu/toggle detectors remain available only from their
focused low-level modules for callers that still need format-specific parsing;
they are intentionally not part of this facade or the runtime load path.
"""

from ..geometry.buffers import DEFAULT_UV_OFFSET, POSITION_STRIDE, _res_get
from ..geometry.draw_call import AuthoredDrawCall, DrawCall, SlotTextureBinding
from ..geometry.identity import GeometryMatch, normalize_geometry_hash
from ..geometry.vertex_attributes import VertexAttributeSource
from ..mod_discovery import discover_ini_paths
from .dnf import (DNF_FALSE, DNF_TRUE, build_bool_alias_map, canonicalize_dnf,
                  dnf_and, dnf_not, dnf_or, filter_dnf, normalize_dnf,
                  parse_condition_dnf)
from .draw_groups import build_draw_groups
from .draw_resources import (
    _collect_resource_copy_sources, _extract_hash, _ib_index_size,
    _ib_res_to_component, _resolve_component_buffers, _resolve_normal_source,
    _select_draw_sections,
)
from .draw_scan import (
    _RUN_SKIP_PREFIXES, _ScannedSections, _collect_legacy_scope_roles,
    _reachable_execution_sections, _run_target_name, _scan_sections_for_draws,
)
from .sections import (SrcLine, extract_resources, first_source, line_source,
                       merge_sections, parse_sections, sections_from_document)
from .texture_roles import (
    TextureOverrideIndex, TextureReplacement,
    _LEGACY_TEXTURE_RESOURCE_RE, _SEMANTIC_TEXTURE_RESOURCE_RE,
    _SEMANTIC_TEXTURE_ROLES, _TEXTURE_SOURCE_PRIORITY,
    _collect_slot_role_hints, _collect_structural_slot_role_hints,
    _collect_texture_override_index, _condition_difference,
    _condition_group_is_consistent, _effective_role_assignments,
    _freeze_dnf, _legacy_texture_evidence, _legacy_texture_role,
    _semantic_texture_role, _thaw_dnf,
)
from .control_graph import (Action, ControlGraph, Controller, RenderEffect,
                            build_control_graph)
from .program import (KeyInput, ProgramFacts, RunEdge, VariableDeclaration,
                      VariableWrite, scan_program)
from .variables import (IniSource, VariableId, VariableResolver,
                        extract_variable_references, source_from_path)


# Compatibility name retained for tests and third-party scripts.
find_inis = discover_ini_paths


__all__ = [
    "SrcLine", "extract_resources", "discover_ini_paths", "find_inis",
    "first_source", "line_source", "merge_sections", "parse_sections",
    "sections_from_document",
    "DNF_FALSE", "DNF_TRUE", "build_bool_alias_map", "dnf_and", "dnf_not",
    "dnf_or", "normalize_dnf", "parse_condition_dnf",
    "Action", "ControlGraph", "Controller", "RenderEffect",
    "build_control_graph", "KeyInput", "ProgramFacts", "RunEdge",
    "VariableDeclaration", "VariableWrite", "scan_program", "IniSource",
    "VariableId", "VariableResolver", "source_from_path",
    "build_draw_groups",
]

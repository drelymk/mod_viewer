"""Read-only INI analysis facade; focused modules provide the implementations.

Existing imports remain available through this module.
"""

from ..geometry.buffers import (
    DEFAULT_UV_OFFSET,  # noqa: F401
    POSITION_STRIDE,  # noqa: F401
    _res_get,  # noqa: F401
)
from ..geometry.draw_call import (
    AuthoredDrawCall,  # noqa: F401
    DrawCall,  # noqa: F401
    SlotTextureBinding,  # noqa: F401
)
from ..geometry.identity import (
    GeometryMatch,  # noqa: F401
    normalize_geometry_hash,  # noqa: F401
)
from ..geometry.vertex_attributes import VertexAttributeSource  # noqa: F401
from ..mod_discovery import discover_ini_paths
from .dnf import (DNF_FALSE, DNF_TRUE, build_bool_alias_map, dnf_and, dnf_not,
                  dnf_or, normalize_dnf, parse_condition_dnf)
from .draw_groups import build_draw_groups
from .draw_resources import (
    _collect_resource_copy_sources,  # noqa: F401
    _extract_hash,  # noqa: F401
    _ib_index_size,  # noqa: F401
    _ib_res_to_component,  # noqa: F401
    _resolve_component_buffers,  # noqa: F401
    _resolve_normal_source,  # noqa: F401
    _select_draw_sections,  # noqa: F401
)
from .draw_scan import (
    _RUN_SKIP_PREFIXES,  # noqa: F401
    _ScannedSections,  # noqa: F401
    _collect_legacy_scope_roles,  # noqa: F401
    _reachable_execution_sections,  # noqa: F401
    _run_target_name,  # noqa: F401
    _scan_sections_for_draws,  # noqa: F401
    gating_var_names,
)
from .menu import (extract_controller_toggles, extract_menu_toggles,
                   extract_menu_var_names)
from .sections import (SrcLine, extract_ini_namespace, extract_resources,
                       first_source, line_source, merge_sections,
                       parse_sections, sections_from_document)
from .state import extract_state_rules  # noqa: F401
from .texture_roles import (
    TextureOverrideIndex,  # noqa: F401
    TextureReplacement,  # noqa: F401
    _LEGACY_TEXTURE_RESOURCE_RE,  # noqa: F401
    _SEMANTIC_TEXTURE_RESOURCE_RE,  # noqa: F401
    _SEMANTIC_TEXTURE_ROLES,  # noqa: F401
    _TEXTURE_SOURCE_PRIORITY,  # noqa: F401
    _collect_slot_role_hints,  # noqa: F401
    _collect_structural_slot_role_hints,  # noqa: F401
    _collect_texture_override_index,  # noqa: F401
    _condition_difference,  # noqa: F401
    _condition_group_is_consistent,  # noqa: F401
    _effective_role_assignments,  # noqa: F401
    _freeze_dnf,  # noqa: F401
    _legacy_texture_evidence,  # noqa: F401
    _legacy_texture_role,  # noqa: F401
    _semantic_texture_role,  # noqa: F401
    _thaw_dnf,  # noqa: F401
)
from .toggles import (extract_toggle_keys, extract_toggle_var_names,
                      extract_variable_defaults)


# Compatibility name retained for tests and third-party scripts.
find_inis = discover_ini_paths


__all__ = [
    "SrcLine", "extract_resources", "discover_ini_paths", "find_inis",
    "extract_ini_namespace", "first_source", "line_source", "merge_sections",
    "parse_sections",
    "sections_from_document",
    "DNF_FALSE", "DNF_TRUE", "build_bool_alias_map", "dnf_and", "dnf_not",
    "dnf_or", "normalize_dnf", "parse_condition_dnf",
    "extract_controller_toggles", "extract_menu_toggles",
    "extract_menu_var_names",
    "extract_toggle_keys", "extract_toggle_var_names", "extract_variable_defaults",
    "gating_var_names", "build_draw_groups",
]

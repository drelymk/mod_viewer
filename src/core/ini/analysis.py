"""One semantic analysis pass over an INI section projection."""

from dataclasses import dataclass, field

from .sections import canonical_var_names, extract_resources
from .toggles import (extract_toggle_keys, extract_variable_defaults)
from .menu import extract_menu_toggles
from .state import extract_state_rules
from .shapes import extract_shape_sliders
from .draw_groups import build_draw_groups
from .draw_scan import _scan_sections_for_draws
from .dnf import build_bool_alias_map
from .texture_roles import TextureOverrideIndex
from .animations import discover_animation_clocks
from ..materials.game_profile import collect_game_evidence


@dataclass
class IniAnalysis:
    """Named intermediate representation shared by controls and geometry."""

    sections: dict
    canonical_vars: dict
    resources: dict
    toggles: dict
    menu: dict
    state_rules: list
    shapes: list
    defaults: dict
    gating_vars: set
    animations: list = field(default_factory=list)
    draw_groups: list = field(default_factory=list)
    game_evidence: list = field(default_factory=list)
    runtime_evidence: list = field(default_factory=list)
    texture_api_evidence: list = field(default_factory=list)
    texture_override_index: TextureOverrideIndex = field(
        default_factory=TextureOverrideIndex)
    condition_aliases: dict = field(default_factory=dict)


def analyze_ini(sections, *, resources=None, var_prefix=None, source=None,
                seen=None, extra_gating_vars=None, qualified_vars=None,
                canonical_vars=None):
    """Analyze ``sections`` once and return all derived semantic models.

    Extractors accept the shared canonical spelling map so a normal load does
    not rescan every source line for each control family.  ``build_draw_groups``
    receives the already-known gating set instead of rediscovering toggles,
    menu variables and state rules internally. The shared draw scan also keeps
    a resolved texture index for INIs that do not produce any geometry groups.
    """
    if canonical_vars is None:
        canonical_vars = canonical_var_names(sections)
    resources = resources if resources is not None else extract_resources(sections)
    game_evidence, runtime_evidence, texture_api_evidence = \
        collect_game_evidence(sections, resources)
    toggles = extract_toggle_keys(
        sections, var_prefix=var_prefix, source=source,
        canonical_vars=canonical_vars)
    menu = extract_menu_toggles(
        sections, var_prefix=var_prefix, source=source,
        canonical_vars=canonical_vars)
    condition_aliases = build_bool_alias_map(
        sections, toggle_keys=toggles, menu=menu, var_prefix=var_prefix)
    state_rules = extract_state_rules(
        sections, var_prefix=var_prefix, canonical_vars=canonical_vars,
        condition_aliases=condition_aliases)
    shapes = extract_shape_sliders(
        sections, resources, var_prefix=var_prefix, source=source,
        canonical_vars=canonical_vars)
    defaults = extract_variable_defaults(
        sections, var_prefix=var_prefix, canonical_vars=canonical_vars)
    animation_analysis = discover_animation_clocks(
        sections, var_prefix=var_prefix, canonical_vars=canonical_vars,
        qualified_vars=qualified_vars, condition_aliases=condition_aliases)

    gating_vars = {
        var for info in toggles.values() for var in info.get("vars", {})
    }
    gating_vars.update(info["var"] for info in menu.values())
    gating_vars.update(
        effect["var"] for info in menu.values()
        for effect in info.get("effects", [])
    )
    gating_vars.update(rule["var"] for rule in state_rules)
    for var in extra_gating_vars or ():
        canonical = canonical_vars.get(str(var).casefold(), str(var))
        gating_vars.add(f"{var_prefix or ''}{canonical}")
    # Conditions are read from the source before the per-INI namespace is
    # applied by normalize_dnf, so the scanner needs the source spellings.
    # The public analysis set remains namespaced for control/panel consumers.
    scan_prefix = var_prefix or ""
    scan_gating_vars = {
        value[len(scan_prefix):] if scan_prefix and value.startswith(scan_prefix)
        else value
        for value in gating_vars
    }
    section_info = _scan_sections_for_draws(
        sections, var_prefix, scan_gating_vars, animation_analysis.frame_vars,
        qualified_vars, resources=resources, condition_aliases=condition_aliases)
    draw_groups = build_draw_groups(
        sections, resources, var_prefix=var_prefix, source=source,
        seen=seen, gating_vars=scan_gating_vars,
        animation_vars=animation_analysis.frame_vars,
        qualified_vars=qualified_vars, section_info=section_info)
    return IniAnalysis(
        sections=sections,
        canonical_vars=canonical_vars,
        resources=resources,
        toggles=toggles,
        menu=menu,
        state_rules=state_rules,
        shapes=shapes,
        defaults=defaults,
        gating_vars=gating_vars,
        animations=list(animation_analysis.clocks),
        draw_groups=draw_groups,
        game_evidence=game_evidence,
        runtime_evidence=runtime_evidence,
        texture_api_evidence=texture_api_evidence,
        texture_override_index=section_info.texture_override_index,
        condition_aliases=condition_aliases,
    )

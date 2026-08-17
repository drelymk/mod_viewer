"""One semantic analysis pass over an INI section projection."""

from dataclasses import dataclass, field

from .ini_sections import canonical_var_names, extract_resources
from .ini_toggles import (extract_toggle_keys, extract_variable_defaults)
from .ini_menu import extract_menu_toggles
from .ini_state import extract_state_rules
from .ini_shapes import extract_shape_sliders
from .ini_parser import build_draw_groups


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
    draw_groups: list = field(default_factory=list)


def analyze_ini(sections, *, resources=None, var_prefix=None, source=None,
                seen=None):
    """Analyze ``sections`` once and return all derived semantic models.

    Extractors accept the shared canonical spelling map so a normal load does
    not rescan every source line for each control family.  ``build_draw_groups``
    receives the already-known gating set instead of rediscovering toggles,
    menu variables and state rules internally.
    """
    canonical_vars = canonical_var_names(sections)
    resources = resources if resources is not None else extract_resources(sections)
    toggles = extract_toggle_keys(
        sections, var_prefix=var_prefix, source=source,
        canonical_vars=canonical_vars)
    menu = extract_menu_toggles(
        sections, var_prefix=var_prefix, source=source,
        canonical_vars=canonical_vars)
    state_rules = extract_state_rules(
        sections, var_prefix=var_prefix, canonical_vars=canonical_vars)
    shapes = extract_shape_sliders(
        sections, resources, var_prefix=var_prefix, source=source,
        canonical_vars=canonical_vars)
    defaults = extract_variable_defaults(
        sections, var_prefix=var_prefix, canonical_vars=canonical_vars)

    gating_vars = {
        var for info in toggles.values() for var in info.get("vars", {})
    }
    gating_vars.update(info["var"] for info in menu.values())
    gating_vars.update(
        effect["var"] for info in menu.values()
        for effect in info.get("effects", [])
    )
    gating_vars.update(rule["var"] for rule in state_rules)
    # Conditions are read from the source before the per-INI namespace is
    # applied by normalize_dnf, so the scanner needs the source spellings.
    # The public analysis set remains namespaced for control/panel consumers.
    scan_prefix = var_prefix or ""
    scan_gating_vars = {
        value[len(scan_prefix):] if scan_prefix and value.startswith(scan_prefix)
        else value
        for value in gating_vars
    }
    draw_groups = build_draw_groups(
        sections, resources, var_prefix=var_prefix, source=source,
        seen=seen, gating_vars=scan_gating_vars)
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
        draw_groups=draw_groups,
    )

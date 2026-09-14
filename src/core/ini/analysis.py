"""One independent INI scan for program facts and raw render effects."""

from dataclasses import dataclass, field
import os

from .sections import canonical_var_names, extract_resources, first_source
from .program import ProgramFacts, scan_program
from .variables import IniSource, VariableId, VariableResolver
from .shapes import extract_shape_effects
from .draw_groups import build_draw_groups
from .draw_scan import _scan_sections_for_draws
from .control_graph import RenderEffect
from ..materials.game_profile import collect_game_evidence


@dataclass
class IniAnalysis:
    """Named intermediate representation shared by the mod-level assembly."""

    sections: dict
    canonical_vars: dict
    resources: dict
    # Compatibility projections are populated by the mod-level assembly. The
    # independent scan itself does not run separate toggle/menu detectors.
    toggles: dict = field(default_factory=dict)
    menu: dict = field(default_factory=dict)
    state_rules: list = field(default_factory=list)
    shapes: list = field(default_factory=list)
    defaults: dict = field(default_factory=dict)
    gating_vars: set = field(default_factory=set)
    draw_groups: list = field(default_factory=list)
    game_evidence: list = field(default_factory=list)
    runtime_evidence: list = field(default_factory=list)
    texture_api_evidence: list = field(default_factory=list)
    source: IniSource | None = None
    program: ProgramFacts | None = None
    render_effects: list = field(default_factory=list)


def _memory_source(sections, resources):
    source_info = first_source(
        [line for lines in sections.values() for line in lines]) or {}
    path = source_info.get("ini_path") or "<memory>/mod.ini"
    return IniSource(
        path=path,
        relative_path=os.path.basename(path),
        namespace=None,
        sections=sections,
        resources=resources,
    )


def _resolve_clause_conditions(conditions, resolver, source, section=None):
    result = []
    for group in conditions or ():
        resolved = []
        for clause in group:
            variable = resolver.resolve(clause["var"], source, section)
            resolved.append({**clause, "var": variable.key})
        if resolved not in result:
            result.append(resolved)
    return result


def _variables_in_conditions(conditions, resolver, source, section=None):
    result = []
    for group in conditions or ():
        for clause in group:
            value = clause["var"]
            try:
                variable = VariableId.from_key(value)
            except (TypeError, ValueError):
                variable = resolver.resolve(value, source, section)
            if variable not in result:
                result.append(variable)
    return tuple(result)


def _resolve_render_effects(groups, shapes, resolver, source, scan_result=None):
    effects = []
    for group in groups:
        for draw in group.get("draws", []):
            section = next((item.get("section") for item in draw.sources
                            if item.get("section")), None)
            draw.conditions = _resolve_clause_conditions(
                draw.conditions, resolver, source, section)
            variables = _variables_in_conditions(
                draw.conditions, resolver, source, section)
            if variables:
                effects.append(RenderEffect(
                    "visibility", variables, draw.label,
                    draw.sources[0] if draw.sources else None,
                    {"group": group.get("name")}))
            for role in ("diffuse", "normal_map", "light_map",
                         "material_map", "emission_map"):
                variants = draw.texture_rules(role)
                for variant in variants:
                    variant["conditions"] = _resolve_clause_conditions(
                        variant.get("conditions"), resolver, source, section)
                variables = _variables_in_conditions(
                    [group for variant in variants
                     for group in variant.get("conditions", [])],
                    resolver, source, section)
                if variables:
                    effects.append(RenderEffect(
                        "texture", variables, draw.label,
                        draw.sources[0] if draw.sources else None,
                        {"role": role}))

    for shape in shapes:
        authored = shape.get("var") or shape.get("name")
        variable = resolver.resolve(authored, source, shape.get("section"))
        shape["var"] = variable.key
        effects.append(RenderEffect(
            "shape", (variable,), shape.get("base_file"),
            {"ini_path": shape.get("ini_path"),
             "section": shape.get("section")},
            {"shape": shape}))

    # Texture assignments are render effects even when their TextureOverride
    # has no IB/draw call of its own.  Keeping this pass on the raw section
    # scan prevents geometry selection from silently dropping texture-only
    # controls.
    if scan_result is not None:
        for section, info in scan_result.items():
            if not isinstance(info, dict):
                continue
            source_info = {"ini_path": source.path, "section": section}
            assignments = {
                "diffuse": info.get("diffuse_history_at_end") or [],
            }
            assignments.update({
                role: state.get("history") or []
                for role, state in (info.get("aux_maps_at_end") or {}).items()
                if isinstance(state, dict)
            })
            for role, variants in assignments.items():
                for variant in variants:
                    conditions = _resolve_clause_conditions(
                        variant.get("cond") or variant.get("conditions"),
                        resolver, source, section)
                    variables = _variables_in_conditions(
                        conditions, resolver, source, section)
                    if variables:
                        effects.append(RenderEffect(
                            "texture", variables, variant.get("res"),
                            source_info,
                            {"role": role, "standalone": True,
                             "assignment_source": variant.get("source")}))

        texture_index = getattr(scan_result, "texture_override_index", None)
        for texture_hash, replacements in (
                getattr(texture_index, "replacements_by_hash", {}) or {}).items():
            for replacement in replacements:
                conditions = _resolve_clause_conditions(
                    replacement.dnf, resolver, source,
                    replacement.source_section)
                variables = _variables_in_conditions(
                    conditions, resolver, source, replacement.source_section)
                if variables:
                    effects.append(RenderEffect(
                        "texture", variables,
                        replacement.file or replacement.resource,
                        {"ini_path": source.path,
                         "section": replacement.source_section},
                        {"role": "texture_override", "standalone": True,
                         "original_hash": texture_hash}))
    return effects


def analyze_ini(sections, *, resources=None, var_prefix=None, source=None,
                seen=None, ini_source=None, resolver=None):
    """Analyze one INI once, producing reusable facts and raw render effects.

    ``var_prefix`` is accepted for source compatibility but ignored by the new
    semantic path. Variable identity is resolved later against all selected
    INIs by :func:`app.mods.analysis.analyze_mod_inis`.
    """
    canonical_vars = canonical_var_names(sections)
    resources = resources if resources is not None else extract_resources(sections)
    if ini_source is None:
        ini_source = _memory_source(sections, resources)
    resolver = resolver or VariableResolver([ini_source])
    program = scan_program(ini_source, resolver)
    game_evidence, runtime_evidence, texture_api_evidence = \
        collect_game_evidence(sections, resources)
    shapes = extract_shape_effects(
        sections, resources, canonical_vars=canonical_vars)

    # The scanner receives the complete authored variable set. Relevance is
    # decided after render effects and controllers have been collected.
    scan_result = _scan_sections_for_draws(
        sections, var_prefix, None, raw_conditions=True)
    draw_groups = build_draw_groups(
        sections, resources, source=source, seen=seen,
        raw_conditions=True, scan_result=scan_result)
    render_effects = _resolve_render_effects(
        draw_groups, shapes, resolver, ini_source, scan_result=scan_result)
    defaults = {
        declaration.var.key: declaration.default
        for declaration in program.declarations
        if declaration.default is not None
    }
    return IniAnalysis(
        sections=sections, canonical_vars=canonical_vars,
        resources=resources, defaults=defaults, draw_groups=draw_groups,
        game_evidence=game_evidence, runtime_evidence=runtime_evidence,
        texture_api_evidence=texture_api_evidence, source=ini_source,
        program=program, shapes=shapes, render_effects=render_effects,
        gating_vars={variable.key for variable in program.variables},
    )


__all__ = ["IniAnalysis", "analyze_ini"]

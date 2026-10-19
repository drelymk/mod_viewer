"""Bounded conditional index and component vertex binding resolution."""

from dataclasses import replace

from ..geometry.skinning import resolve_skinning_source
from .dnf import dnf_and, normalize_dnf
from .texture_roles import _condition_group_is_consistent


_MAX_BINDING_VARIANTS = 128


def _intersect(left, right):
    """Bound growth without using the draw DNF's fail-open overflow result."""
    result = []
    for a in left:
        for b in right:
            group = dnf_and([a], [b])[0]
            if _condition_group_is_consistent(group) and group not in result:
                result.append(group)
                if len(result) > _MAX_BINDING_VARIANTS:
                    raise OverflowError
    return result


def _subtract(left, right):
    for group in right:
        result = []
        remaining = left
        for clause in group:
            # Make the alternatives disjoint: !a OR (a AND !b), rather
            # than !a OR !b. Repeated nested branch exclusions stay bounded.
            result.extend(_intersect(remaining, [[
                {**clause, "negate": not clause["negate"]}]]))
            if len(result) > _MAX_BINDING_VARIANTS:
                raise OverflowError
            remaining = _intersect(remaining, [[clause]])
        left = result
    return left


def _effective_bindings(history, conditions):
    """Select the last applicable assignment, preserving untouched states."""
    remaining = conditions
    result = []
    for binding in reversed(history):
        guard = binding.get("guard", binding["conditions"]) or [[]]
        applicable = _intersect(remaining, guard)
        if applicable:
            result.append((applicable, binding))
        remaining = _subtract(remaining, guard)
        if not remaining:
            break
    if remaining:
        result.append((remaining, None))
    return list(reversed(result))


def _binding_views(authored, histories):
    """Partition binding evidence; publish only existing visibility variables."""
    variants = [(authored.binding_conditions or authored.conditions or [[]], {})]
    visible_vars = {clause["var"] for group in authored.conditions for clause in group}
    for history in histories.values():
        visible_vars.update(clause["var"] for binding in history
                            for group in binding["conditions"] for clause in group)
    for key, history in histories.items():
        next_variants = []
        for conditions, inherited in variants:
            for guard, binding in _effective_bindings(history, conditions):
                state = dict(inherited)
                if binding is not None:
                    state[key] = binding
                next_variants.append((guard, state))
                if len(next_variants) > _MAX_BINDING_VARIANTS:
                    raise OverflowError
        variants = next_variants
    if not variants:
        return [(authored, {})]
    projected = [normalize_dnf(guard, visible_vars) for guard, _ in variants]
    for index, (_, state) in enumerate(variants):
        for other in range(index):
            if (state != variants[other][1] and _intersect(
                    projected[index] or [[]], projected[other] or [[]])):
                # Unknown runtime alternatives cannot become overlapping
                # visible meshes. Keep the established fallback when ambiguous.
                return [(authored, {})]
    return [(replace(authored, conditions=conditions, binding_conditions=guard), state)
            for (guard, state), conditions in zip(variants, projected)]


def resolve_index_bindings(authored, history):
    """Resolve conditional IB assignments made before one authored draw."""
    if len({item["resource"] for item in history}) <= 1 or any(
            authored.animation_conditions):
        return [authored]
    try:
        return [replace(draw, index_resource=state["index"]["resource"])
                if "index" in state else draw
                for draw, state in _binding_views(authored, {"index": history})
                # An untouched branch has no authored replacement IB. The
                # original object denotes an unsupported/ambiguous fallback.
                if "index" in state or draw is authored]
    except OverflowError:
        return [authored]


def inherit_vertex_bindings(authored, histories, resolve_vertex_info):
    """Return draw views with compatible Position, Texcoord and Blend state.

    These views retain the same authored occurrence and source. Direct bindings
    still win; separate Blend and Texcoord passes may both use numeric slot 1.
    Unsupported or excessive inference retains the existing static fallback.
    """
    if any(authored.animation_conditions):
        return [(authored, {})]
    selected = {}
    try:
        for role, history in histories.items():
            if role == "position" and 0 in authored.vertex_resources:
                continue
            if role == "texcoord" and any(
                    slot in authored.vertex_resources and (
                        authored.vertex_resources[slot] is None
                        or resolve_vertex_info(
                            authored.vertex_resources[slot]).get("stride", 0) != 32)
                    for slot in (1, 2)):
                continue
            by_slot = {}
            for binding in history:
                by_slot.setdefault(binding["slot"], []).append(binding)
            slots = [slot for slot, bindings in by_slot.items() if (
                role == "position" and slot == 0
                or role == "texcoord" and slot in (1, 2)
                or role == "blend" and any(resolve_skinning_source(
                    {slot: item["resource"]}, resolve_vertex_info)[0]
                    for item in bindings if item["resource"]))]
            if not any(len({item["resource"] for item in by_slot[slot]}) > 1
                       for slot in slots):
                continue
            selected.update(((role, slot), by_slot[slot]) for slot in slots)
        if not selected:
            return [(authored, {})]
        result = []
        for draw, state in _binding_views(authored, selected):
            inherited = {}
            for (role, slot), binding in sorted(state.items()):
                if role == "blend":
                    inherited.setdefault("blend", {})[slot] = binding["resource"]
                elif role == "position":
                    inherited[role] = binding
                else:
                    inherited.setdefault("normal_resources", {})[slot] = binding["resource"]
                    info = resolve_vertex_info(binding["resource"])
                    if (info.get("filename") and info.get("stride", 0) != 32
                            and "SNORM" not in str(info.get("format", "")).upper()):
                        # Separate Texcoord overrides prefer the first usable
                        # numeric slot; other slots retain their own state.
                        inherited.setdefault("texcoord", binding)
            if "normal_resources" in inherited and "texcoord" not in inherited:
                null = next((binding for (role, _), binding in state.items()
                             if role == "texcoord" and binding["resource"] is None), None)
                if null is not None:
                    inherited["texcoord"] = null
            result.append((draw, inherited))
        return result
    except OverflowError:
        return [(authored, {})]

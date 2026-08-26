"""3DMigoto draw-group extraction: which mesh is drawn from which buffers,
and what gates it.

The rest of the READ path lives in siblings and is re-exported here so
existing `from core.ini.parser import ...` callers keep working:
    core/ini/sections.py  section parsing, resource records
    mod_discovery.py bounded mod-folder INI discovery
    core/ini/dnf.py       condition -> DNF
    core/ini/toggles.py   [Key...] cycle toggles, variable defaults
    core/ini/menu.py      clickable in-game menu slots
"""

from dataclasses import dataclass, field, replace
import re

from ..geometry.draw_call import AuthoredDrawCall, DrawCall, SlotTextureBinding
from ..geometry.identity import GeometryMatch, normalize_geometry_hash
from ..geometry.mesh_builder import POSITION_STRIDE, DEFAULT_UV_OFFSET, _res_get
from ..geometry.vertex_attributes import VertexAttributeSource
from .sections import (SrcLine, extract_resources, first_source,
                           line_source, merge_sections, parse_sections,
                           sections_from_document)
from ..mod_discovery import discover_ini_paths
from .dnf import (DNF_FALSE, DNF_TRUE, build_bool_alias_map, dnf_and,
                      dnf_not, dnf_or, normalize_dnf, parse_condition_dnf)
from .menu import extract_menu_toggles, extract_menu_var_names
from .state import extract_state_rules
from .toggles import (extract_toggle_keys, extract_toggle_var_names,
                          extract_variable_defaults)

__all__ = [
    "SrcLine", "extract_resources", "discover_ini_paths", "find_inis",
    "first_source", "line_source", "merge_sections", "parse_sections",
    "sections_from_document",
    "DNF_FALSE", "DNF_TRUE", "build_bool_alias_map", "dnf_and", "dnf_not",
    "dnf_or", "normalize_dnf", "parse_condition_dnf",
    "extract_menu_toggles", "extract_menu_var_names",
    "extract_toggle_keys", "extract_toggle_var_names", "extract_variable_defaults",
    "gating_var_names", "build_draw_groups",
]


def _freeze_dnf(dnf):
    return tuple(
        tuple((clause["var"], clause["value"], bool(clause["negate"]))
              for clause in group)
        for group in (dnf or ())
    )


def _thaw_dnf(conditions):
    return [[{"var": var, "value": value, "negate": negate}
             for var, value, negate in group]
            for group in (conditions or ())]


@dataclass(frozen=True, slots=True)
class TextureReplacement:
    """One condition-aware original-hash to replacement-resource binding."""

    original_hash: str
    resource: str
    conditions: tuple = ()
    source_section: str = ""
    file: str | None = None

    @classmethod
    def from_dnf(cls, original_hash, resource, conditions, source_section):
        return cls(original_hash, resource, _freeze_dnf(conditions),
                   source_section)

    @property
    def dnf(self):
        return _thaw_dnf(self.conditions)


@dataclass(slots=True)
class TextureOverrideIndex:
    """Resource/hash and reverse hash/replacement views from one INI."""

    hashes_by_resource: dict = field(default_factory=dict)
    replacements_by_hash: dict = field(default_factory=dict)

    def with_resource_files(self, resources):
        lookup = {str(name).casefold(): info
                  for name, info in (resources or {}).items()}
        replacements = {}
        for texture_hash, items in self.replacements_by_hash.items():
            resolved = []
            for item in items:
                info = lookup.get(item.resource.casefold(), {})
                resolved.append(replace(
                    item, file=info.get("filename")))
            replacements[texture_hash] = tuple(resolved)
        return TextureOverrideIndex(
            hashes_by_resource=dict(self.hashes_by_resource),
            replacements_by_hash=replacements)


class _ScannedSections(dict):
    """Dict-compatible scan result carrying its one texture index."""

    def __init__(self, *args, texture_override_index=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.texture_override_index = texture_override_index

# Compatibility name retained for tests and third-party scripts.  Internal
# application code uses the explicitly named discovery function so loading
# ownership is visible at the call site.
find_inis = discover_ini_paths


def _ib_res_to_component(ib_res):
    s = re.sub(r"^Resource", "", ib_res or "", flags=re.I)
    s = re.sub(r"IB$", "", s, flags=re.I)
    return re.sub(r"[A-Z]$", "", s)


def _ib_index_size(fmt):
    """Bytes per index -- 3DMigoto index buffers are R16_UINT or R32_UINT."""
    return 2 if "R16" in (fmt or "").upper() else 4


def _extract_hash(name):
    """Return the first 8-hex-char hash found in a resource/section name, or None."""
    m = re.search(r'_([0-9a-f]{8})_', name, re.I)   # prefer underscore-delimited
    if m: return m.group(1).lower()
    m = re.search(r'[0-9a-f]{8}', name, re.I)         # fallback: first 8-hex run
    return m.group(0).lower() if m else None


def _collect_texture_override_index(sections, toggle_vars, alias_map,
                                    var_prefix=None):
    """Index TextureOverride hashes and conditional ``this`` assignments."""
    hashes_by_resource = {}
    replacements_by_hash = {}
    for section, lines in sections.items():
        if not str(section).lower().startswith("textureoverride"):
            continue
        hashes = set()
        cond_stack = []
        for raw in lines:
            line = raw.split(";", 1)[0].strip()
            if not line:
                continue
            match = re.match(r"(?:else\s+if|elif)\s+(.*)$", line, re.I)
            if match:
                if cond_stack:
                    frame = cond_stack[-1]
                    branch = parse_condition_dnf(
                        match.group(1).strip(), alias_map)
                    frame["cur"] = dnf_and(
                        dnf_not(frame["seen"]), branch)
                    frame["seen"] = dnf_or(frame["seen"], branch)
                continue
            if line.lower().startswith("if "):
                branch = parse_condition_dnf(line[3:].strip(), alias_map)
                cond_stack.append({"cur": branch, "seen": branch})
                continue
            if line.lower() == "else":
                if cond_stack:
                    cond_stack[-1]["cur"] = dnf_not(
                        cond_stack[-1]["seen"])
                continue
            if line.lower() == "endif":
                if cond_stack:
                    cond_stack.pop()
                continue
            match = re.match(r"hash\s*=\s*(\S+)", line, re.I)
            if match:
                texture_hash = normalize_geometry_hash(match.group(1))
                if texture_hash:
                    hashes.add(texture_hash)
                continue
            match = re.match(
                r"this\s*=\s*(?:ref\s+)?(\S+)", line, re.I)
            if not match or not hashes:
                continue
            combined = DNF_TRUE
            for frame in cond_stack:
                combined = dnf_and(combined, frame["cur"])
            conditions = normalize_dnf(combined, toggle_vars, var_prefix)
            resource = match.group(1)
            resource_key = resource.casefold()
            hashes_by_resource.setdefault(resource_key, set()).update(hashes)
            for texture_hash in hashes:
                replacement = TextureReplacement.from_dnf(
                    texture_hash, resource, conditions, str(section))
                replacements_by_hash.setdefault(texture_hash, []).append(
                    replacement)
    return TextureOverrideIndex(
        hashes_by_resource={
            resource: tuple(sorted(hashes))
            for resource, hashes in hashes_by_resource.items()},
        replacements_by_hash={
            texture_hash: tuple(items)
            for texture_hash, items in replacements_by_hash.items()})


_RUN_SKIP_PREFIXES = ("TextureOverride", "ShaderOverride", "Resource", "Present", "Key", "Constants")
_AUX_MAP_CHANNELS = {
    "normalmap": "normal_map",
    "lightmap": "light_map",
    "materialmap": "material_map",
}
_SEMANTIC_TEXTURE_ROLES = {
    "diffuse": "diffuse",
    "normalmap": "normal_map",
    "lightmap": "light_map",
    "materialmap": "material_map",
}
_SEMANTIC_TEXTURE_RESOURCE_RE = re.compile(
    r"^Resource[\\/]"
    r"(?:GIMI|ZZMI|RabbitFX|WWMI)[\\/]"
    r"(?P<role>Diffuse|NormalMap|LightMap|MaterialMap)$", re.I)
_LEGACY_TEXTURE_RESOURCE_RE = re.compile(
    r"^Resource.+(?P<role>Diffuse|NormalMap|LightMap|MaterialMap)"
    r"(?P<variant>\.\d+)?$", re.I)


def _semantic_texture_role(resource):
    """Return a role only for a framework-owned semantic resource name."""
    match = _SEMANTIC_TEXTURE_RESOURCE_RE.fullmatch(str(resource or ""))
    return (_SEMANTIC_TEXTURE_ROLES[match.group("role").casefold()]
            if match else None)


def _legacy_texture_evidence(resource, sections, section_lookup):
    """Return ``(role, family)`` for a declared legacy texture resource.

    Older GIMI mods commonly bind resources such as
    ``ResourceCharacterDiffuse`` directly to ``ps-t0``.  This fallback is
    deliberately narrower than the removed substring heuristic: the resource
    must be file-backed and end in one complete role token.  The caller adds
    scope-local family validation and sibling inheritance.
    """
    match = _LEGACY_TEXTURE_RESOURCE_RE.fullmatch(str(resource or ""))
    if not match:
        return None
    if _semantic_texture_role(resource):
        return None
    section_name = section_lookup.get(str(resource).casefold())
    if section_name is None:
        return None
    if not any(re.match(r"^filename\s*=\s*\S+", raw, re.I)
               for raw in sections[section_name]):
        return None
    role = _SEMANTIC_TEXTURE_ROLES[match.group("role").casefold()]
    family = str(resource)
    if match.group("variant"):
        family = family[:-len(match.group("variant"))]
    return role, family.casefold()


def _legacy_texture_role(resource, sections, section_lookup):
    """Compatibility view returning only the constrained legacy role."""
    evidence = _legacy_texture_evidence(resource, sections, section_lookup)
    return evidence[0] if evidence else None


def _collect_structural_slot_role_hints(sections):
    """Collect unambiguous ``ps-tN`` roles from semantic resource markers."""
    observations = {}
    for lines in sections.values():
        for raw in lines:
            line = raw.split(";", 1)[0].strip()
            if not line:
                continue
            slot = re.match(
                r"^ps-t(?P<slot>\d+)\s*=\s*(?:ref\s+)?(?P<resource>\S+)",
                line, re.I)
            if not slot:
                continue
            role = _semantic_texture_role(slot.group("resource"))
            if role:
                observations.setdefault(int(slot.group("slot")), set()).add(role)
    return {slot: next(iter(roles)) for slot, roles in observations.items()
            if len(roles) == 1}


def _run_target_name(line, section_lookup):
    """Return a traversable ``run =`` target using scanner rules."""
    match = re.match(r"run\s*=\s*(\S+)", line, re.I)
    if not match:
        return None
    target_name = section_lookup.get(match.group(1).lower())
    target_low = target_name.lower() if target_name else ""
    if (not target_name
            or any(target_low.startswith(prefix.lower())
                   for prefix in _RUN_SKIP_PREFIXES)):
        return None
    return target_name


def _reachable_execution_sections(sections, root, section_lookup):
    """Return one root section and its traversable command-list closure."""
    reachable = []
    visiting = {root}
    pending = [root]
    while pending:
        current = pending.pop()
        reachable.append(current)
        for raw in sections[current]:
            line = raw.split(";", 1)[0].strip()
            target_name = _run_target_name(line, section_lookup)
            if target_name and target_name not in visiting:
                visiting.add(target_name)
                pending.append(target_name)
    return reachable


def _collect_legacy_scope_roles(sections, root, section_lookup):
    """Infer legacy resource roles within one reachable execution scope."""
    family_slots = {}
    family_roles = {}
    family_anchors = {}
    assigned_resources = {}
    slot_roles = {}
    for section_name in _reachable_execution_sections(
            sections, root, section_lookup):
        for raw in sections[section_name]:
            line = raw.split(";", 1)[0].strip()
            if not line:
                continue
            slot = re.match(
                r"^ps-t(?P<slot>\d+)\s*=\s*(?:ref\s+)?(?P<resource>\S+)",
                line, re.I)
            if not slot:
                continue
            resource = slot.group("resource")
            evidence = _legacy_texture_evidence(
                resource, sections, section_lookup)
            if evidence is None:
                resource_key = str(resource).casefold()
                section_resource = section_lookup.get(resource_key)
                if section_resource is None:
                    continue
                if not any(re.match(r"^filename\s*=\s*\S+", raw, re.I)
                           for raw in sections[section_resource]):
                    continue
                role = None
                family = None
            else:
                role, family = evidence
            slot_number = int(slot.group("slot"))
            resource_key = str(resource).casefold()
            assigned_resources.setdefault(resource_key, set()).add(slot_number)
            if role is None:
                continue
            family_slots.setdefault(family, set()).add(slot_number)
            family_roles.setdefault(family, set()).add(role)
            family_anchors.setdefault(family, set()).add(resource_key)
            slot_roles.setdefault(slot_number, set()).add(role)

    result = {}
    for family, anchors in family_anchors.items():
        slots = set(family_slots.get(family, set()))
        roles = family_roles.get(family, set())
        for resource, resource_slots in assigned_resources.items():
            if (resource in anchors
                    or any(resource.startswith(anchor)
                           for anchor in anchors)):
                slots.update(resource_slots)
        if len(slots) != 1 or len(roles) != 1:
            continue
        slot = next(iter(slots))
        if len(slot_roles.get(slot, ())) != 1:
            continue
        role = next(iter(roles))
        for resource, resource_slots in assigned_resources.items():
            if resource_slots != {slot}:
                continue
            if (resource in anchors
                    or any(resource.startswith(anchor)
                           for anchor in anchors)):
                result[resource] = role
    return result


# Compatibility name retained for callers that used the private helper.
_collect_slot_role_hints = _collect_structural_slot_role_hints


def _condition_group_is_consistent(group):
    """Return whether one DNF conjunction can be satisfied."""
    equal = {}
    not_equal = set()
    for clause in group:
        key = clause["var"]
        value = clause["value"]
        if clause["negate"]:
            not_equal.add((key, value))
            if equal.get(key) == value:
                return False
        else:
            previous = equal.setdefault(key, value)
            if previous != value or (key, value) in not_equal:
                return False
    return True


def _condition_difference(condition, excluded):
    """Return ``condition AND NOT excluded`` in the parser's DNF form.

    An empty normalized condition means true, so ``None`` is used for an
    empty result instead of the ambiguous empty DNF list.
    """
    left = condition or DNF_TRUE
    right = excluded or DNF_TRUE
    result = [group for group in dnf_and(left, dnf_not(right))
              if _condition_group_is_consistent(group)]
    if not result:
        return None
    if any(not group for group in result):
        return []
    return result


_TEXTURE_SOURCE_PRIORITY = {
    "semantic": 30,
    "slot": 20,
    "legacy_slot": 10,
}


def _effective_role_assignments(assignments):
    """Apply semantic, structural-slot, then legacy-slot precedence."""
    result = []
    for item in assignments:
        priority = _TEXTURE_SOURCE_PRIORITY.get(item.get("source"), 0)
        higher = [candidate for candidate in assignments
                  if _TEXTURE_SOURCE_PRIORITY.get(
                      candidate.get("source"), 0) > priority]
        if not higher:
            result.append(item)
            continue
        condition = item.get("cond") or []
        for candidate in higher:
            condition = _condition_difference(condition,
                                               candidate.get("cond") or [])
            if condition is None:
                break
        if condition is not None:
            result.append({**item, "cond": condition})
    return result


def gating_var_names(sections, var_prefix=None, *, toggle_keys=None,
                     menu=None, state_rules=None):
    """Variables worth tracking as per-draw show/hide gates: those a cycle-type
    [Key...] section drives, those an in-game clickable menu mutates, plus
    literal state variables safely derived in [Present]. Other internal state
    vars like $mod_enabled remain deliberately untracked."""
    toggle_vars = extract_toggle_var_names(
        sections, var_prefix=var_prefix, toggle_keys=toggle_keys)
    menu_vars = extract_menu_var_names(
        sections, var_prefix=var_prefix, menu=menu)
    state_rules = (state_rules if state_rules is not None else
                   extract_state_rules(sections, var_prefix=var_prefix))
    return toggle_vars | menu_vars | {rule["var"] for rule in state_rules}


def _scan_sections_for_draws(sections, var_prefix=None, gating_vars=None):
    """Pass 1 of build_draw_groups: walk every TextureOverride/CommandList
    section and record its buffer refs plus each drawindexed line's gating
    condition (as normalized DNF) and provenance.

    Split out so callers that only want "what gates this drawindexed line"
    (e.g. tests/test_record_editor.py's corpus dry run) can skip the later
    geometry-resolution passes, which drop any section whose buffers don't
    resolve to real files -- irrelevant to gating, but would otherwise
    discard real conditions from the sample for no good reason.

    `run = X` lines are followed inline (recursively, with cycle protection)
    whenever X names another section in this same file that isn't itself a
    hash-matched TextureOverride/ShaderOverride/Resource/Present/Key/Constants
    section.

    Each draw also remembers the `ib` most recently assigned *before* it
    within this same flattened scan (None if none yet -- see build_draw_groups) mid-section to read a completely different
    mesh's buffers for a handful of draws inside what's otherwise another
    mesh's TextureOverride section), and the set of explicit or proven
    slot-semantic texture alternatives active for it.

    Returns {section_name: {vb0, vb1, vb2, ib, draws, diffuse, src}} — the
    same per-section shape build_draw_groups uses internally as `sec_info`.
    `draws` entries are typed AuthoredDrawCall records. Their vertex_resources
    snapshot is kept for provenance only; build_draw_groups re-derives the
    actual position/texcoord buffers for a reassigned `ib` from its component
    instead of trusting those literal values.
    """
    toggle_vars = (gating_vars if gating_vars is not None else
                   gating_var_names(sections))
    # 3DMigoto command-list names are case-insensitive.  Keep the authored
    # section key for recursion/provenance, but resolve `run =` targets by a
    # case-folded lookup so RabbitFX's real SetTextures spelling is preserved
    # even when a mod uses `commandlist\\rabbitfx\\settextures`.
    section_lookup = {str(name).lower(): name for name in sections}
    alias_map = build_bool_alias_map(sections)
    texture_override_index = _collect_texture_override_index(
        sections, toggle_vars, alias_map, var_prefix)
    resource_texture_hashes = texture_override_index.hashes_by_resource
    structural_slot_roles = _collect_structural_slot_role_hints(sections)
    seq_counter = [0]   # unique id per `if` block
    scope_legacy_resource_roles = {}

    def _geometry_match(info):
        geometry_hash = info.get("_geometry_hash")
        if geometry_hash is None:
            return None
        return GeometryMatch(
            geometry_hash,
            info.get("_match_first_index"),
            info.get("_match_index_count"),
        )

    def _slot_snapshot(info):
        result = []
        for slot, resource in sorted(
                info.get("_cur_slot_textures", {}).items()):
            structural_role = structural_slot_roles.get(slot)
            legacy_role = scope_legacy_resource_roles.get(
                resource.casefold())
            role_hint = structural_role or legacy_role
            result.append(SlotTextureBinding(
                slot=slot,
                resource=resource,
                texture_hashes=resource_texture_hashes.get(
                    resource.casefold(), ()),
                role_hint=role_hint,
                role_hint_source=(
                    "mod_slot_mapping" if structural_role
                    else "legacy_slot_mapping" if legacy_role else None),
            ))
        return result

    def _record_texture_assignment(info, role, res, cond_stack, *, source):
        """Record one role assignment in the shared execution-order IR."""
        combined = DNF_TRUE
        for frame in cond_stack:
            combined = dnf_and(combined, frame["cur"])
        cond = normalize_dnf(combined, toggle_vars, var_prefix)
        if role == "diffuse":
            if not info["diffuse"]:
                info["diffuse"] = res
            if res not in info["diffuse_pool"]:
                info["diffuse_pool"].append(res)
            state = {
                "variants": info.get("_cur_diffuse_variants") or [],
                "history": info.get("_diffuse_history") or [],
                "chain_key": info.get("_diffuse_chain_key"),
                "last_cond": info.get("_diffuse_last_cond"),
            }
        else:
            state = info["_aux_maps"].setdefault(role, {
                "variants": [], "history": [], "chain_key": None,
                "last_cond": None,
            })
        if cond_stack:
            chain_key = cond_stack[-1]["seq"]
        else:
            # Keep consecutive unconditional assignments in one active
            # stream.  Same-source writes still replace one another, while a
            # semantic and a slot assignment remain together long enough for
            # condition-aware precedence to choose the semantic one.
            chain_key = ("bare", role)
        if chain_key != state["chain_key"]:
            state["variants"] = []
            state["chain_key"] = chain_key
        elif (state["last_cond"] == cond and state["variants"]
              and state["variants"][-1].get("source") == source):
            state["variants"].pop()
        variant = {"res": res, "cond": cond, "source": source}
        texture_hashes = resource_texture_hashes.get(res.casefold(), ())
        if texture_hashes:
            variant["texture_hashes"] = texture_hashes
        state["variants"].append(variant)
        state["last_cond"] = cond
        history = {"res": res, "cond": cond, "source": source}
        if texture_hashes:
            history["texture_hashes"] = texture_hashes
        state["history"].append(history)
        if role == "diffuse":
            info["_cur_diffuse_variants"] = state["variants"]
            info["_diffuse_chain_key"] = state["chain_key"]
            info["_diffuse_last_cond"] = state["last_cond"]
            info["_diffuse_history"] = state["history"]
        if source == "slot":
            info["_texture_provenance"][role] = "mod_slot_semantic"
        elif source == "legacy_slot":
            info["_texture_provenance"][role] = "mod_slot_legacy"

    def _aux_snapshot(info):
        return {
            channel: {
                "variants": _effective_role_assignments(
                    state.get("variants") or []),
                "history": _effective_role_assignments(
                    state.get("history") or []),
            }
            for channel, state in info.get("_aux_maps", {}).items()
            if state.get("variants") or state.get("history")
        }

    def _texture_provenance_snapshot(info):
        result = dict(info.get("_texture_provenance") or {})
        assignments = {
            "diffuse": info.get("_diffuse_history") or [],
        }
        assignments.update({
            channel: state.get("history") or []
            for channel, state in info.get("_aux_maps", {}).items()
        })
        for role, history in assignments.items():
            effective = _effective_role_assignments(history)
            source_by_provenance = {
                "mod_slot_semantic": "slot",
                "mod_slot_legacy": "legacy_slot",
            }
            source = source_by_provenance.get(result.get(role))
            if (source and not any(item.get("source") == source
                                   for item in effective)):
                result.pop(role, None)
        return result

    def _scan(lines, info, cond_stack, visiting):
        # cond_stack tracks the stack of active gate branches. Each frame is
        # {"cur": <DNF active for the current branch>,
        #  "seen": <DNF of "some earlier branch at this level already matched">}
        # so `else if` / `else` correctly exclude every preceding branch. It's
        # threaded through run= recursion unchanged, so a called section's own
        # if/elif nests correctly under whichever branch called it.
        for raw in lines:
            line = raw.split(";")[0].strip()
            if not line: continue
            if info["src"] is None:
                info["src"] = line_source(raw)
            low = line.lower()
            m_elif = re.match(r'(?:else\s+if|elif)\s+(.*)$', line, re.I)
            if m_elif:
                if cond_stack:
                    frame = cond_stack[-1]
                    branch = parse_condition_dnf(m_elif.group(1).strip(), alias_map)
                    not_seen = dnf_not(frame["seen"])
                    frame["cur"] = dnf_and(not_seen, branch)
                    frame["seen"] = dnf_or(frame["seen"], branch)
                continue
            if low.startswith("if "):
                branch = parse_condition_dnf(line[3:].strip(), alias_map)
                seq_counter[0] += 1
                cond_stack.append({"cur": branch, "seen": branch, "seq": seq_counter[0]})
                continue
            if low == "else":
                if cond_stack:
                    frame = cond_stack[-1]
                    frame["cur"] = dnf_not(frame["seen"])
                continue
            if low == "endif":
                if cond_stack: cond_stack.pop()
                continue
            match = re.match(r"hash\s*=\s*(\S+)", line, re.I)
            if match:
                info["_geometry_hash"] = normalize_geometry_hash(match.group(1))
            match = re.match(r"match_first_index\s*=\s*(\d+)", line, re.I)
            if match:
                info["_match_first_index"] = int(match.group(1))
            match = re.match(r"match_index_count\s*=\s*(\d+)", line, re.I)
            if match:
                info["_match_index_count"] = int(match.group(1))
            match = re.match(
                r"ps-t(\d+)\s*=\s*(?:ref\s+)?(\S+)", line, re.I)
            if match:
                slot = int(match.group(1))
                resource = match.group(2)
                if resource.lower() == "null":
                    info["_cur_slot_textures"].pop(slot, None)
                else:
                    info["_cur_slot_textures"][slot] = resource
                    structural_role = structural_slot_roles.get(slot)
                    legacy_role = scope_legacy_resource_roles.get(
                        resource.casefold())
                    role = structural_role or legacy_role
                    if (role and _semantic_texture_role(resource) is None):
                        _record_texture_assignment(
                            info, role, resource, cond_stack,
                            source=("slot" if structural_role
                                    else "legacy_slot"))
            m = re.match(r"vb(\d+)\s*=\s*(?:ref\s+)?(\S+)", line, re.I)
            if m:
                slot = int(m.group(1))
                resource = m.group(2)
                value = None if resource.lower() == "null" else resource
                if slot <= 2 and value and not info[f"vb{slot}"]:
                    info[f"vb{slot}"] = value
                info["_cur_vertex_resources"][slot] = value
            m = re.match(r"ib\s*=\s*(\S+)", line, re.I)
            if m:
                if not info["ib"]: info["ib"] = m.group(1)
                info["_cur_ib"] = m.group(1)
            if re.match(r"handling\s*=\s*skip\b", line, re.I):
                info["handling_skip"] = True
            m = re.fullmatch(
                r"drawindexed\s*=\s*(\d+)\s*,\s*(\d+)\s*,\s*(-?\d+)\s*",
                line, re.I)
            if m:
                combined = DNF_TRUE
                for frame in cond_stack:
                    combined = dnf_and(combined, frame["cur"])
                conds = normalize_dnf(combined, toggle_vars, var_prefix)
                info["draws"].append(AuthoredDrawCall(
                    count=int(m.group(1)),
                    start=int(m.group(2)),
                    base=int(m.group(3)),
                    conditions=conds,
                    source=line_source(raw),
                    index_resource=info.get("_cur_ib"),
                    diffuse_variants=_effective_role_assignments(
                        info.get("_cur_diffuse_variants") or []),
                    diffuse_history=_effective_role_assignments(
                        info.get("_diffuse_history") or []),
                    vertex_resources=dict(info["_cur_vertex_resources"]),
                    auxiliary_maps=_aux_snapshot(info),
                    texture_provenance=_texture_provenance_snapshot(info),
                    geometry_match=_geometry_match(info),
                    slot_textures=_slot_snapshot(info),
                ))
            # "ref" is optional -- XXMI-generated mods omit it.  Only the
            # framework-owned semantic API resource names are accepted here;
            # arbitrary resource names must not imply a texture role.
            m_diff = re.match(
                r"^Resource[\\/]"
                r"(?:GIMI|ZZMI|RabbitFX|WWMI)[\\/]Diffuse\s*=\s*"
                r"(?:ref\s+)?(\S+)", line, re.I)
            if m_diff:
                _record_texture_assignment(
                    info, "diffuse", m_diff.group(1), cond_stack,
                    source="semantic")
            m_aux = re.match(
                r"^Resource[\\/]"
                r"(?:GIMI|ZZMI|RabbitFX|WWMI)[\\/]"
                r"(NormalMap|LightMap|MaterialMap)\s*=\s*"
                r"(?:ref\s+)?(\S+)", line, re.I)
            if m_aux:
                _record_texture_assignment(
                    info, _SEMANTIC_TEXTURE_ROLES[m_aux.group(1).casefold()],
                    m_aux.group(2), cond_stack, source="semantic")
            m = re.match(r"run\s*=\s*(\S+)", line, re.I)
            if m:
                target_name = _run_target_name(line, section_lookup)
                if target_name and target_name not in visiting:
                    visiting.add(target_name)
                    _scan(sections[target_name], info, cond_stack, visiting)
                    visiting.discard(target_name)

    # scan BOTH TextureOverride AND CommandList sections.
    sec_info = _ScannedSections(
        texture_override_index=texture_override_index)
    for name, lines in sections.items():
        name_low = name.lower()
        if not (name_low.startswith("textureoverride")
                or name_low.startswith("commandlist")):
            continue
        scope_legacy_resource_roles = _collect_legacy_scope_roles(
            sections, name, section_lookup)
        info: dict = dict(vb0=None, vb1=None, vb2=None, ib=None, draws=[],
                          diffuse=None, diffuse_pool=[], src=None, handling_skip=False,
                          _cur_diffuse_variants=[], _diffuse_chain_key=None,
                          _diffuse_history=[], _aux_maps={},
                          _texture_provenance={},
                          _cur_vertex_resources={}, _cur_slot_textures={},
                          _geometry_hash=None, _match_first_index=None,
                          _match_index_count=None)
        _scan(lines, info, [], {name})
        info.pop("_cur_ib", None)
        info.pop("_cur_vertex_resources", None)
        # Whatever diffuse was active at the end of the scan -- needed for a
        # section with NO drawindexed line at all (the game's original,
        # whole-buffer draw proceeds unmodified against this section's own
        # `ib =`; see build_draw_groups' synthetic placeholder draw below).
        # That implicit draw still runs with whichever explicit semantic or
        # proven slot-semantic assignment the section made, same as any real
        # drawindexed line would've seen at this point in execution order.
        info["diffuse_variants_at_end"] = _effective_role_assignments(
            info.get("_cur_diffuse_variants") or [])
        info["diffuse_history_at_end"] = _effective_role_assignments(
            info.get("_diffuse_history") or [])
        info["aux_maps_at_end"] = _aux_snapshot(info)
        info["texture_provenance_at_end"] = _texture_provenance_snapshot(info)
        info["geometry_match_at_end"] = _geometry_match(info)
        info["slot_textures_at_end"] = _slot_snapshot(info)
        info.pop("_cur_slot_textures", None)
        info.pop("_cur_diffuse_variants", None)
        info.pop("_diffuse_chain_key", None)
        info.pop("_diffuse_last_cond", None)
        info.pop("_diffuse_history", None)
        info.pop("_aux_maps", None)
        info.pop("_texture_provenance", None)
        sec_info[name] = info
    return sec_info



def _collect_resource_copy_sources(sections, resources):
    """Resolve explicit/rest-pose resource copy edges before group building."""
    resource_copy_sources = {}

    copy_re = re.compile(
        r'^\s*(Resource\S+)\s*=\s*copy(?:\s+ref)?\s+(Resource\S+)\s*$', re.I)
    for lines in sections.values():
        for raw in lines:
            line = raw.split(";", 1)[0].strip()
            match = copy_re.match(line)
            if not match:
                continue
            dest, copy_source = match.groups()
            if dest.lower() == copy_source.lower():
                continue
            sources = resource_copy_sources.setdefault(dest.lower(), [])
            if all(existing.lower() != copy_source.lower() for existing in sources):
                sources.append(copy_source)

    # LL/ZZMI skeleton skinning writes a runtime-only vertex buffer from a
    # file-backed position buffer plus the stride-32 blend buffer, then binds
    # that output with cs-u0. Treat the input position as the rest pose only
    # when the complete read/write pattern is present.
    cs_read_re = re.compile(r'^\s*cs-t([12])\s*=\s*(?:ref\s+)?(\S+)\s*$', re.I)
    cs_write_re = re.compile(r'^\s*cs-u0\s*=\s*(?:ref\s+)?(\S+)\s*$', re.I)
    for lines in sections.values():
        cs_inputs = {}
        for raw in lines:
            line = raw.split(";", 1)[0].strip()
            match = cs_read_re.match(line)
            if match:
                slot, res_name = match.groups()
                if res_name.lower() == "null":
                    cs_inputs.pop(slot, None)
                else:
                    cs_inputs[slot] = res_name
                continue
            match = cs_write_re.match(line)
            if not match or match.group(1).lower() == "null":
                continue
            output = match.group(1)
            position = cs_inputs.get("1")
            blend = cs_inputs.get("2")
            if (position and blend
                    and _res_get(resources, position).get("filename")
                    and _res_get(resources, blend).get("stride") == 32):
                sources = resource_copy_sources.setdefault(output.lower(), [])
                if all(existing.lower() != position.lower() for existing in sources):
                    sources.append(position)
    return resource_copy_sources


def _resolve_normal_source(effective_vertex_resources, resources,
                           position_file, position_stride,
                           resolve_vertex_info=None):
    """Recognize a supported authored-normal layout from effective bindings.

    This function only identifies a structural source.  Filesystem safety and
    byte-level validation happen in ``mesh_builder`` immediately before the
    source is published as geometry.
    """
    effective_vertex_resources = effective_vertex_resources or {}
    vector_resource = effective_vertex_resources.get(1)
    if vector_resource:
        vector_info = (resolve_vertex_info(vector_resource)
                       if resolve_vertex_info is not None
                       else _res_get(resources, vector_resource))
        vector_format = str(vector_info.get("format") or "").upper()
        if (vector_info.get("filename")
                and vector_info.get("stride") == 8
                and vector_format == "DXGI_FORMAT_R8G8B8A8_SNORM"):
            return VertexAttributeSource(
                file=vector_info["filename"],
                stride=8,
                offset=4,
                encoding="snorm8x3")

    if position_file and position_stride == 40:
        return VertexAttributeSource(
            file=position_file,
            stride=40,
            offset=12,
            encoding="f32x3")
    return None


def _select_draw_sections(sec_info, global_ib):
    """Select TextureOverride sections that can produce viewer geometry."""
    return [(name, info) for name, info in sec_info.items()
            if name.lower().startswith("textureoverride")
            and (info["ib"] or global_ib)
            and (info["draws"] or (info["ib"] and not info["handling_skip"]))]


def _resolve_component_buffers(sec_info, resources, resource_copy_sources):
    """Resolve component, hash and WWMI global buffer bindings.

    This stage owns resource provenance and buffer selection.  Group assembly
    can therefore focus on authored draw history and output records instead of
    interleaving GIMI, ZZMI and WWMI binding heuristics.
    """
    vertex_info_cache = {}

    def _resolve_vertex_info(res_name, visiting=None):
        """Resolve a runtime vertex resource to a file-backed source."""
        if not res_name:
            return {}
        cache_key = res_name.lower()
        if cache_key in vertex_info_cache:
            return vertex_info_cache[cache_key]

        resource_info = _res_get(resources, res_name)
        if resource_info.get("filename"):
            vertex_info_cache[cache_key] = resource_info
            return resource_info

        visiting = set(visiting or ())
        if cache_key in visiting:
            return {}
        visiting.add(cache_key)

        candidates = list(resource_copy_sources.get(cache_key, ()))
        # Existing 3DMigoto shape-key convention: a bare Position resource can
        # use a `.B` child as its rest pose without an explicit Resource copy.
        # Do not keep manufacturing `.B.B...` names when neither exists.
        if not cache_key.endswith(".b"):
            candidates.append(res_name + ".B")
        for candidate in candidates:
            resolved = _resolve_vertex_info(candidate, visiting)
            if resolved.get("filename"):
                vertex_info_cache[cache_key] = resolved
                return resolved

        vertex_info_cache[cache_key] = {}
        return {}

    comp_pos, comp_tc = {}, {}
    # Index by underscore-delimited hex hash for mods that use _<hash>_ in
    # section names.
    hash_pos: dict = {}
    hash_tc: dict = {}

    # Texcoord sections set comp_tc first (highest priority -- must win over
    # Blend).
    for name, info in sec_info.items():
        if not name.lower().startswith("textureoverride"):
            continue
        base = name[len("TextureOverride"):]
        if base.lower().endswith("texcoord"):
            comp = base[:-len("Texcoord")]
            if info["vb1"]:
                comp_tc[comp.lower()] = info["vb1"]

    # Blend and Position sections (lower priority for tc).
    for name, info in sec_info.items():
        if not name.lower().startswith("textureoverride"):
            continue
        base = name[len("TextureOverride"):]
        if base.lower().endswith("blend"):
            comp = base[:-len("Blend")]
            comp_key = comp.lower()
            if info["vb0"] and comp_key not in comp_pos:
                comp_pos[comp_key] = info["vb0"]
            # GIMI: *Blend sets vb1 to the blend buffer (stride 32), not tc.
            if info["vb1"] and comp_key not in comp_tc:
                if _res_get(resources, info["vb1"]).get("stride", 0) != 32:
                    comp_tc[comp_key] = info["vb1"]
        elif base.lower().endswith("position"):
            # GIMI: vb0 is in a *Position section, not *Blend.
            comp = base[:-len("Position")]
            comp_key = comp.lower()
            if info["vb0"] and comp_key not in comp_pos:
                comp_pos[comp_key] = info["vb0"]

        # Skip vb2 if it's a blend buffer (stride 32, ZZMI format) so it does
        # not shadow the real texcoord in vb1. WWMI uses vb2=TexCoord.
        h = _extract_hash(name)
        if h:
            if info["vb0"] and h not in hash_pos:
                hash_pos[h] = info["vb0"]
            vb2_stride = (_res_get(resources, info["vb2"]).get("stride", 0)
                          if info["vb2"] else 0)
            tc = ((info["vb2"] if info["vb2"] and vb2_stride != 32 else None)
                  or info["vb1"])
            if tc and h not in hash_tc:
                hash_tc[h] = tc

    comp_bufs = {component: {"position": comp_pos[component],
                             "texcoord": comp_tc[component]}
                 for component in comp_pos if component in comp_tc}

    # Discover global IB/position/texcoord from CommandList sections (WWMI).
    global_ib, global_pos, global_tc = None, None, None
    for name, info in sec_info.items():
        if not name.lower().startswith("commandlist"):
            continue
        if info["ib"] and not global_ib:
            global_ib = info["ib"]
        if info["vb0"] and not global_pos:
            global_pos = info["vb0"]
        tc = info["vb2"] or info["vb1"]
        if tc and not global_tc:
            global_tc = tc

    # If global_pos is computed (e.g. ResourceShapeKeyedPosition), fall back
    # to the nearest file-backed R32G32B32 position buffer.
    if global_pos and not _res_get(resources, global_pos).get("filename"):
        for res_name, res_info in resources.items():
            fmt = res_info.get("format", "")
            if res_info.get("filename") and "R32G32B32" in fmt:
                global_pos = res_name
                break

    return {
        "resolve_vertex_info": _resolve_vertex_info,
        "component_buffers": comp_bufs,
        "component_positions": comp_pos,
        "component_texcoords": comp_tc,
        "hash_positions": hash_pos,
        "hash_texcoords": hash_tc,
        "global_ib": global_ib,
        "global_position": global_pos,
        "global_texcoord": global_tc,
    }


def build_draw_groups(sections, resources, var_prefix=None, source=None, seen=None,
                      gating_vars=None):
    """Each group's `display_name` is the TextureOverride section name minus
    its "TextureOverride" prefix (e.g. "TextureOverrideBodyA" -> "BodyA") --
    what the UI shows, always clean. `name`/`label` (the mesh payload's dict
    key) starts identical but gets a "_2"/"_3"... suffix when it repeats,
    since payload keys must be unique even though the UI never shows that.
    `source` tags each group with its originating ini (see
    app.mods.loader._ini_scope), same as it already does for Toggle keys.

    `seen`, if given, is a dict shared across multiple calls (one per ini in
    an "AllInOne" mod folder — see app.mods.loader._parse_inis) so two inis
    reusing a generic name like "Component0" get distinct payload keys
    instead of the second silently overwriting the first's mesh entry; both
    still *display* as "Component0" since the per-ini source header already
    disambiguates them for the user. Defaults to a fresh dict when omitted."""
    if seen is None:
        seen = {}
    sec_info = _scan_sections_for_draws(sections, var_prefix, gating_vars)

    # Some ZZMI shape-key mods bind an empty, writable Resource as vb0 and
    # populate it in [Present]/CommandList code from a file-backed rest pose:
    #
    #   ResourceBodyPosition = copy ResourceBodyPositionBase
    #
    # `extract_resources` intentionally contains only file-backed resources,
    # so remember these explicit copy edges here and follow them whenever a
    # vertex resource has no filename of its own.  This is provenance from the
    # INI, not a fuzzy component-name match, and therefore also works for a
    # component reached by a mid-section `ib =` reassignment.
    resource_copy_sources = _collect_resource_copy_sources(sections, resources)

    resolved_buffers = _resolve_component_buffers(
        sec_info, resources, resource_copy_sources)
    resolve_vertex_info = resolved_buffers["resolve_vertex_info"]
    comp_bufs = resolved_buffers["component_buffers"]
    comp_pos = resolved_buffers["component_positions"]
    comp_tc = resolved_buffers["component_texcoords"]
    hash_pos = resolved_buffers["hash_positions"]
    hash_tc = resolved_buffers["hash_texcoords"]
    global_ib = resolved_buffers["global_ib"]
    global_pos = resolved_buffers["global_position"]
    global_tc = resolved_buffers["global_texcoord"]
    # pass 3: collect draw sections.
    # A section with `ib=` but no drawindexed lines normally lets the game's
    # original (whole-buffer) draw call proceed unmodified, just against the
    # new ib -- so it's kept as an implicit full-buffer draw. But `handling =
    # skip` means the opposite: the original draw call itself is suppressed,
    # so with no drawindexed lines to replace it, nothing is drawn at all.
    draw_secs = _select_draw_sections(sec_info, global_ib)
    texture_override_index = getattr(
        sec_info, "texture_override_index", TextureOverrideIndex())
    texture_override_index = texture_override_index.with_resource_files(
        resources)
    if not draw_secs:
        return []

    # pass 4: build group dicts
    ib_file_cache: dict = {}

    def _resolve_ib_file(ib_name):
        if ib_name not in ib_file_cache:
            ib_file_cache[ib_name] = _res_get(resources, ib_name).get("filename")
        return ib_file_cache[ib_name]

    diffuse_file_cache: dict = {}

    def _resolve_diffuse_file(res_name):
        if res_name not in diffuse_file_cache:
            diffuse_file_cache[res_name] = _res_get(resources, res_name).get("filename")
        return diffuse_file_cache[res_name]

    def _resolve_vertex_res(res_name):
        ri = resolve_vertex_info(res_name)
        return ri.get("filename"), ri.get("stride")

    def _lookup_comp_value(mapping, comp):
        candidates = [
            comp,
            re.sub(r"[A-Za-z]+$", "", comp),
            re.sub(r"(?<=.)[A-Z][a-z]+$", "", comp),
        ]
        for candidate in candidates:
            if candidate:
                value = mapping.get(candidate.lower())
                if value:
                    return value
        # All-lowercase templates lose the CamelCase boundary used above.
        # Prefer the longest declared component that prefixes the IB-derived
        # name, avoiding encounter-order dependence.
        comp_low = comp.lower()
        prefix = max(
            (key for key in mapping if comp_low.startswith(key)),
            key=len, default=None)
        return mapping.get(prefix) if prefix else None

    def _lookup_comp_buf(comp):
        return _lookup_comp_value(comp_bufs, comp)

    groups: list = []
    for sec_name, info in draw_secs:
        display_name = sec_name[len("TextureOverride"):] or sec_name
        seen[display_name] = seen.get(display_name, 0) + 1
        label = display_name
        if seen[display_name] > 1: label = f"{display_name}_{seen[display_name]}"

        ib_res = info["ib"] or global_ib
        comp   = _ib_res_to_component(ib_res)
        buf    = _lookup_comp_buf(comp)
        if not buf:
            # Some compute-skinned ZZMI components bind their runtime vb0 only
            # inside the draw CommandList, while the sibling *Texcoord section
            # still owns the UV buffer.  Combine those two authored bindings.
            position = info["vb0"] or _lookup_comp_value(comp_pos, comp)
            vb2_stride = (_res_get(resources, info["vb2"]).get("stride", 0)
                          if info["vb2"] else 0)
            texcoord = ((info["vb2"] if info["vb2"] and vb2_stride != 32 else None)
                        or info["vb1"] or _lookup_comp_value(comp_tc, comp))
            if (position and texcoord
                    and resolve_vertex_info(position).get("filename")):
                buf = {"position": position, "texcoord": texcoord}
        if not buf:
            h = _extract_hash(sec_name) or _extract_hash(ib_res)
            if h and h in hash_pos and h in hash_tc:
                buf = {"position": hash_pos[h], "texcoord": hash_tc[h]}
        if not buf and global_pos and global_tc:
            buf = {"position": global_pos, "texcoord": global_tc}  # WWMI fallback
        if not buf: continue

        pos_ri  = resolve_vertex_info(buf["position"])
        tc_ri   = _res_get(resources, buf["texcoord"])
        ib_ri   = _res_get(resources, ib_res)
        diff_ri = _res_get(resources, info["diffuse"]) if info["diffuse"] else {}

        pos_file = pos_ri.get("filename")
        tc_file  = tc_ri.get("filename")
        ib_file  = ib_ri.get("filename")
        if not (pos_file and tc_file and ib_file): continue

        tc_stride  = tc_ri.get("stride", 20)
        pos_stride = pos_ri.get("stride", POSITION_STRIDE)
        index_size = _ib_index_size(ib_ri.get("format"))
        uv_off     = DEFAULT_UV_OFFSET
        group_vertex_resources = {
            slot: info[f"vb{slot}"]
            for slot in (0, 1, 2)
            if info[f"vb{slot}"]
        }
        group_normal_source = _resolve_normal_source(
            group_vertex_resources, resources, pos_file, pos_stride,
            resolve_vertex_info)

        draws_list = list(info["draws"]) or [AuthoredDrawCall(
            count=None,
            start=0,
            base=0,
            source=info["src"],
            diffuse_variants=info.get("diffuse_variants_at_end") or [],
            diffuse_history=info.get("diffuse_history_at_end") or [],
            auxiliary_maps=info.get("aux_maps_at_end") or {},
            texture_provenance=(
                info.get("texture_provenance_at_end") or {}),
            geometry_match=info.get("geometry_match_at_end"),
            slot_textures=info.get("slot_textures_at_end") or [],
        )]
        draws = []
        for i, authored in enumerate(draws_list, 1):
            d = DrawCall(
                label=f"{label}-{i}",
                count=authored.count,
                start=authored.start,
                base=authored.base,
                conditions=authored.conditions,
                sources=[authored.source] if authored.source else [],
                ib_file=ib_file,
                index_size=index_size,
                position_file=pos_file,
                position_stride=pos_stride,
                texcoord_file=tc_file,
                texcoord_stride=tc_stride,
                normal_source=group_normal_source,
                geometry_match=authored.geometry_match,
                texture_provenance=dict(authored.texture_provenance),
                slot_textures=[
                    SlotTextureBinding(
                        slot=item.slot,
                        resource=item.resource,
                        file=_resolve_diffuse_file(item.resource) or item.file,
                        texture_hashes=item.texture_hashes,
                        role_hint=item.role_hint,
                        role_hint_source=item.role_hint_source,
                    )
                    for item in authored.slot_textures],
            )
            # Resolve the effective buffers before deduplication. Concrete
            # per-draw VB bindings win; runtime-only resources retain the
            # existing IB/component heuristic, while an authored null stays
            # unbound instead of inheriting the group's first binding.
            effective_ib = authored.index_resource or ib_res
            if effective_ib != ib_res:
                resolved_ib = _resolve_ib_file(effective_ib)
                if resolved_ib:
                    d.ib_file = resolved_ib
                    d.index_size = _ib_index_size(
                        _res_get(resources, effective_ib).get("format"))

            draw_buf = _lookup_comp_buf(_ib_res_to_component(effective_ib))
            if draw_buf and draw_buf != buf:
                pfile, pstride = _resolve_vertex_res(draw_buf["position"])
                tfile, tstride = _resolve_vertex_res(draw_buf["texcoord"])
                if pfile:
                    d.position_file = pfile
                    d.position_stride = pstride or POSITION_STRIDE
                if tfile:
                    d.texcoord_file = tfile
                    d.texcoord_stride = tstride or 20

            vertex_resources = authored.vertex_resources
            if 0 in vertex_resources:
                position_resource = vertex_resources[0]
                if position_resource is None:
                    d.position_file = None
                    d.position_stride = None
                else:
                    pfile, pstride = _resolve_vertex_res(position_resource)
                    if pfile:
                        d.position_file = pfile
                        d.position_stride = pstride or POSITION_STRIDE

            authored_tc = {
                slot: vertex_resources[slot]
                for slot in (1, 2) if slot in vertex_resources
            }
            resolved_tc = None
            for slot in (2, 1):
                resource_name = authored_tc.get(slot)
                if not resource_name:
                    continue
                tfile, tstride = _resolve_vertex_res(resource_name)
                if tfile and (tstride or 0) != 32:
                    resolved_tc = (tfile, tstride or 20)
                    break
            if resolved_tc:
                d.texcoord_file, d.texcoord_stride = resolved_tc
            elif authored_tc and any(
                    resource_name is None
                    for resource_name in authored_tc.values()):
                d.texcoord_file = None
                d.texcoord_stride = None
            effective_vertex_resources = dict(group_vertex_resources)
            effective_vertex_resources.update(vertex_resources)
            d.normal_source = _resolve_normal_source(
                effective_vertex_resources, resources, d.position_file,
                d.position_stride, resolve_vertex_info)
            # Whichever Resource\...\Diffuse line most recently ran before
            # this draw, in execution order -- the draw's own default
            # texture (see core.geometry.mesh_builder.build_mesh_payload). The first
            # entry is the resolution at this point when no toggle var is
            # bound (matches the `if` branch of an elif chain); a toggle
            # press picks a different entry via texture_variants below.
            variants = []
            for v in authored.diffuse_variants:
                file = _resolve_diffuse_file(v["res"])
                if file:
                    item = {"conditions": v["cond"], "file": file}
                    if v.get("texture_hashes"):
                        item["texture_hashes"] = tuple(v["texture_hashes"])
                    variants.append(item)
            if variants:
                d.set_texture_default("diffuse", variants[0]["file"])
                d.texture_hashes["diffuse"] = list(dict.fromkeys(
                    texture_hash
                    for item in variants
                    for texture_hash in item.get("texture_hashes", ())))
            # A toggle that swaps the diffuse texture rather than gating a draw.
            if len(variants) > 1:
                d.set_texture_variants("diffuse", variants)
            history = []
            for v in authored.diffuse_history:
                file = _resolve_diffuse_file(v["res"])
                if file:
                    item = {"conditions": v["cond"], "file": file}
                    if v.get("texture_hashes"):
                        item["texture_hashes"] = tuple(v["texture_hashes"])
                    history.append(item)
            legacy_vars = {c["var"] for v in variants
                           for group in v["conditions"] for c in group}
            history_vars = {c["var"] for v in history
                            for group in v["conditions"] for c in group}
            # `_cur_diffuse_variants` only retains the latest assignment chain.
            # If history is longer, an earlier unconditional write, partial
            # chain, or same-branch reassignment was replaced. Preserve the
            # complete stream even when every chain uses the same variable;
            # the browser applies the last matching write in source order.
            if (len(history) > 1 and
                    (history_vars - legacy_vars or
                     len(history) > len(variants))):
                d.texture_assignments = history

            # Authored PBR companions follow the same execution-order model
            # as diffuse assignments, but remain INI-only (no manual pool).
            # A conditional single assignment has no unconditional default;
            # the frontend must be able to fall back to no map when it fails.
            for channel, state in authored.auxiliary_maps.items():
                assignments = state.get("history") or state.get("variants") or []
                resolved = []
                default_file = None
                for assignment in assignments:
                    file = _resolve_diffuse_file(assignment["res"])
                    if not file:
                        continue
                    conditions = assignment["cond"]
                    item = {"conditions": conditions, "file": file}
                    if assignment.get("texture_hashes"):
                        item["texture_hashes"] = tuple(
                            assignment["texture_hashes"])
                    resolved.append(item)
                    if not conditions:
                        default_file = file
                if default_file:
                    d.set_texture_default(channel, default_file)
                hashes = list(dict.fromkeys(
                    texture_hash
                    for item in resolved
                    for texture_hash in item.get("texture_hashes", ())))
                if hashes:
                    d.texture_hashes[channel] = hashes
                if (len(resolved) > 1 or
                        (resolved and resolved[0]["conditions"])):
                    d.set_texture_variants(channel, resolved)
            d.texture_provenance = {
                role: source
                for role, source in d.texture_provenance.items()
                if d.texture_default(role) or d.texture_rules(role)
            }
            draws.append(d)
        pool_files, seen_pool_files = [], set()
        for res in info["diffuse_pool"]:
            file = _resolve_diffuse_file(res)
            if file and file not in seen_pool_files:
                seen_pool_files.add(file)
                pool_files.append({"res": res, "file": file})
        groups.append(dict(
            name=label,
            display_name=display_name,
            source=source,
            position_file=pos_file, texcoord_file=tc_file,
            position_stride=pos_stride,
            texcoord_stride=tc_stride, texcoord_uv_off=uv_off,
            normal_source=group_normal_source,
            ib_file=ib_file, diffuse_file=diff_ri.get("filename"),
            diffuse_pool_files=pool_files,
            index_size=index_size,
            geometry_match=info.get("geometry_match_at_end"),
            draws=draws,
            _texture_override_index=texture_override_index,
        ))

    return groups

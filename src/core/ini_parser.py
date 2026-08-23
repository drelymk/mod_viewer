"""3DMigoto draw-group extraction: which mesh is drawn from which buffers,
and what gates it.

The rest of the READ path lives in siblings and is re-exported here so
existing `from core.ini_parser import ...` callers keep working:
    ini_sections.py  section parsing, resource records
    mod_discovery.py bounded mod-folder INI discovery
    ini_dnf.py       condition -> DNF
    ini_toggles.py   [Key...] cycle toggles, variable defaults
    ini_menu.py      clickable in-game menu slots
"""

import re

from .draw_call import AuthoredDrawCall, DrawCall, SlotTextureBinding
from .geometry_identity import GeometryMatch, normalize_geometry_hash
from .mesh_builder import POSITION_STRIDE, DEFAULT_UV_OFFSET, _res_get
from .ini_sections import (SrcLine, extract_resources, first_source,
                           line_source, merge_sections, parse_sections,
                           sections_from_document)
from .mod_discovery import discover_ini_paths
from .ini_dnf import (DNF_FALSE, DNF_TRUE, build_bool_alias_map, dnf_and,
                      dnf_not, dnf_or, normalize_dnf, parse_condition_dnf)
from .ini_menu import extract_menu_toggles, extract_menu_var_names
from .ini_state import extract_state_rules
from .ini_toggles import (extract_toggle_keys, extract_toggle_var_names,
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


def _collect_texture_hashes(sections):
    """Map authoritative TextureOverride hash/this assignments to resources."""
    result = {}
    for section, lines in sections.items():
        if not str(section).lower().startswith("textureoverride"):
            continue
        hashes = set()
        resource = None
        for raw in lines:
            line = raw.split(";", 1)[0].strip()
            match = re.match(r"hash\s*=\s*(\S+)", line, re.I)
            if match:
                geometry_hash = normalize_geometry_hash(match.group(1))
                if geometry_hash:
                    hashes.add(geometry_hash)
                continue
            match = re.match(r"this\s*=\s*(?:ref\s+)?(\S+)", line, re.I)
            if match:
                resource = match.group(1)
        if resource and hashes:
            result.setdefault(resource.casefold(), set()).update(hashes)
    return {
        resource: tuple(sorted(hashes))
        for resource, hashes in result.items()
    }


_RUN_SKIP_PREFIXES = ("TextureOverride", "ShaderOverride", "Resource", "Present", "Key", "Constants")
_AUX_MAP_CHANNELS = {
    "normalmap": "normal_map",
    "lightmap": "light_map",
    "materialmap": "material_map",
}


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
    mesh's TextureOverride section), and the set of `Resource\\...\\Diffuse
    = [ref] X` alternatives active for it.

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
    resource_texture_hashes = _collect_texture_hashes(sections)
    seq_counter = [0]   # unique id per `if` block
    bare_counter = [0]  # unique id per diffuse line reached with an empty cond_stack
    aux_bare_counters = {channel: 0 for channel in _AUX_MAP_CHANNELS.values()}

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
        return [
            SlotTextureBinding(
                slot,
                resource,
                texture_hashes=resource_texture_hashes.get(
                    resource.casefold(), ()),
            )
            for slot, resource in sorted(
                info.get("_cur_slot_textures", {}).items())]

    def _aux_snapshot(info):
        return {
            channel: {
                "variants": list(state.get("variants") or []),
                "history": list(state.get("history") or []),
            }
            for channel, state in info.get("_aux_maps", {}).items()
            if state.get("variants") or state.get("history")
        }

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
                    diffuse_variants=list(
                        info.get("_cur_diffuse_variants") or []),
                    diffuse_history=list(info.get("_diffuse_history") or []),
                    vertex_resources=dict(info["_cur_vertex_resources"]),
                    auxiliary_maps=_aux_snapshot(info),
                    geometry_match=_geometry_match(info),
                    slot_textures=_slot_snapshot(info),
                ))
            # "ref" is optional -- XXMI-generated mods omit it (e.g. "Resource\GIMI\Diffuse = X").
            m_diff = re.match(r"Resource\\[^\\]+\\Diffuse\s*=\s*(?:ref\s+)?(\S+)", line, re.I)
            if not m_diff:
                # Direct ps-t slot: "ps-t1 = ResourceXxxDiffuse"
                m2 = re.match(r"ps-t\d+\s*=\s*(\S+)", line, re.I)
                if m2 and re.search(r"Diffuse", m2.group(1), re.I):
                    m_diff = m2
            if m_diff:
                res = m_diff.group(1)
                if not info["diffuse"]: info["diffuse"] = res
                if res not in info["diffuse_pool"]: info["diffuse_pool"].append(res)
                combined = DNF_TRUE
                for frame in cond_stack:
                    combined = dnf_and(combined, frame["cur"])
                cond = normalize_dnf(combined, toggle_vars, var_prefix)
                if cond_stack:
                    chain_key = cond_stack[-1]["seq"]
                else:
                    # No enclosing if at all: every such line is a fresh,
                    # unconditional reassignment of "the current diffuse" in
                    # execution order, never a continuation of some earlier
                    # bare line -- each needs its own always-distinct key, or
                    # two unrelated top-level reassignments would wrongly
                    # accumulate into one multi-entry variant list instead of
                    # the second replacing the first.
                    bare_counter[0] += 1
                    chain_key = ("bare", bare_counter[0])
                if chain_key != info.get("_diffuse_chain_key"):
                    info["_cur_diffuse_variants"] = []
                    info["_diffuse_chain_key"] = chain_key
                elif info.get("_diffuse_last_cond") == cond and info["_cur_diffuse_variants"]:
                    # A second diffuse line in the SAME if/elif branch as the
                    # immediately preceding one (no elif/else advanced the
                    # branch in between) -- its own condition is therefore
                    # identical to that prior line's, so this is a plain
                    # in-branch reassignment, not a new toggle alternative.
                    # Replace rather than accumulate, or the branch would
                    # wrongly end up offering two alternatives that are both
                    # active under the exact same condition.
                    info["_cur_diffuse_variants"].pop()
                variant = {"res": res, "cond": cond}
                texture_hashes = resource_texture_hashes.get(
                    res.casefold(), ())
                if texture_hashes:
                    variant["texture_hashes"] = texture_hashes
                info["_cur_diffuse_variants"].append(variant)
                info["_diffuse_last_cond"] = cond
                # Keep the complete execution-ordered assignment stream too.
                # Independent/nested condition chains can successively
                # override a diffuse; the last matching assignment wins.
                history = {"res": res, "cond": cond}
                if texture_hashes:
                    history["texture_hashes"] = texture_hashes
                info["_diffuse_history"].append(history)
            m_aux = re.match(
                r"Resource\\[^\\]+\\(NormalMap|LightMap|MaterialMap)\s*=\s*"
                r"(?:ref\s+)?(\S+)", line, re.I)
            aux_assignment = m_aux.groups() if m_aux else None
            if aux_assignment is None:
                # Some shader-oriented INIs bind auxiliary maps directly to a
                # ps-t slot. Infer the role from the authored resource name;
                # unlike diffuse, the slot number alone is not stable across
                # mod families.
                m_direct_aux = re.match(
                    r"ps-t\d+\s*=\s*(?:ref\s+)?(\S+)", line, re.I)
                if m_direct_aux:
                    m_role = re.search(
                        r"(NormalMap|LightMap|MaterialMap)",
                        m_direct_aux.group(1), re.I)
                    if m_role:
                        aux_assignment = (m_role.group(1),
                                           m_direct_aux.group(1))
            if aux_assignment:
                channel = _AUX_MAP_CHANNELS[aux_assignment[0].lower()]
                res = aux_assignment[1]
                state = info["_aux_maps"].setdefault(channel, {
                    "variants": [], "history": [], "chain_key": None,
                    "last_cond": None,
                })
                combined = DNF_TRUE
                for frame in cond_stack:
                    combined = dnf_and(combined, frame["cur"])
                cond = normalize_dnf(combined, toggle_vars, var_prefix)
                if cond_stack:
                    chain_key = cond_stack[-1]["seq"]
                else:
                    aux_bare_counters[channel] += 1
                    chain_key = ("bare", channel, aux_bare_counters[channel])
                if chain_key != state["chain_key"]:
                    state["variants"] = []
                    state["chain_key"] = chain_key
                elif state["last_cond"] == cond and state["variants"]:
                    state["variants"].pop()
                variant = {"res": res, "cond": cond}
                texture_hashes = resource_texture_hashes.get(
                    res.casefold(), ())
                if texture_hashes:
                    variant["texture_hashes"] = texture_hashes
                state["variants"].append(variant)
                state["last_cond"] = cond
                history = {"res": res, "cond": cond}
                if texture_hashes:
                    history["texture_hashes"] = texture_hashes
                state["history"].append(history)
            m = re.match(r"run\s*=\s*(\S+)", line, re.I)
            if m:
                target = m.group(1)
                target_name = section_lookup.get(target.lower())
                target_low = target_name.lower() if target_name else ""
                if (target_name and target_name not in visiting
                        and not any(target_low.startswith(p.lower())
                                   for p in _RUN_SKIP_PREFIXES)):
                    visiting.add(target_name)
                    _scan(sections[target_name], info, cond_stack, visiting)
                    visiting.discard(target_name)

    # scan BOTH TextureOverride AND CommandList sections.
    sec_info: dict = {}
    for name, lines in sections.items():
        name_low = name.lower()
        if not (name_low.startswith("textureoverride")
                or name_low.startswith("commandlist")):
            continue
        info: dict = dict(vb0=None, vb1=None, vb2=None, ib=None, draws=[],
                          diffuse=None, diffuse_pool=[], src=None, handling_skip=False,
                          _cur_diffuse_variants=[], _diffuse_chain_key=None,
                          _diffuse_history=[], _aux_maps={},
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
        # That implicit draw still runs with whichever Resource\...\Diffuse
        # (or bare ps-t0/ps-t1) assignment the section made, same as any real
        # drawindexed line would've seen at this point in execution order.
        info["diffuse_variants_at_end"] = list(info.get("_cur_diffuse_variants") or [])
        info["diffuse_history_at_end"] = list(info.get("_diffuse_history") or [])
        info["aux_maps_at_end"] = _aux_snapshot(info)
        info["geometry_match_at_end"] = _geometry_match(info)
        info["slot_textures_at_end"] = _slot_snapshot(info)
        info.pop("_cur_slot_textures", None)
        info.pop("_cur_diffuse_variants", None)
        info.pop("_diffuse_chain_key", None)
        info.pop("_diffuse_last_cond", None)
        info.pop("_diffuse_history", None)
        info.pop("_aux_maps", None)
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
    app.mod_loader._ini_scope), same as it already does for Toggle keys.

    `seen`, if given, is a dict shared across multiple calls (one per ini in
    an "AllInOne" mod folder — see app.mod_loader._parse_inis) so two inis
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
    if not draw_secs: return []

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

        draws_list = list(info["draws"]) or [AuthoredDrawCall(
            count=None,
            start=0,
            base=0,
            source=info["src"],
            diffuse_variants=info.get("diffuse_variants_at_end") or [],
            diffuse_history=info.get("diffuse_history_at_end") or [],
            auxiliary_maps=info.get("aux_maps_at_end") or {},
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
                geometry_match=authored.geometry_match,
                slot_textures=[
                    SlotTextureBinding(
                        item.slot,
                        item.resource,
                        _resolve_diffuse_file(item.resource) or item.file,
                        item.texture_hashes,
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
            # Whichever Resource\...\Diffuse line most recently ran before
            # this draw, in execution order -- the draw's own default
            # texture (see core.mesh_builder.build_mesh_payload). The first
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
            ib_file=ib_file, diffuse_file=diff_ri.get("filename"),
            diffuse_pool_files=pool_files,
            index_size=index_size,
            geometry_match=info.get("geometry_match_at_end"),
            draws=draws,
        ))

    return groups

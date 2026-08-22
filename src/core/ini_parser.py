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

from .buffer_layout import index_layout
from .draw_call import AuthoredDrawCall, DrawCall
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


def _extract_hash(name):
    """Return the first 8-hex-char hash found in a resource/section name, or None."""
    m = re.search(r'_([0-9a-f]{8})_', name, re.I)   # prefer underscore-delimited
    if m: return m.group(1).lower()
    m = re.search(r'[0-9a-f]{8}', name, re.I)         # fallback: first 8-hex run
    return m.group(0).lower() if m else None


_RUN_SKIP_PREFIXES = ("TextureOverride", "ShaderOverride", "Resource", "Present", "Key", "Constants")
_MAX_RUN_DEPTH = 64
_MAX_EXECUTION_STEPS = 200_000
_MAX_EXECUTION_DRAWS = 20_000
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
    snapshot is resolved per draw before geometry deduplication.
    """
    toggle_vars = (gating_vars if gating_vars is not None else
                   gating_var_names(sections))
    # 3DMigoto command-list names are case-insensitive.  Keep the authored
    # section key for recursion/provenance, but resolve `run =` targets by a
    # case-folded lookup so RabbitFX's real SetTextures spelling is preserved
    # even when a mod uses `commandlist\\rabbitfx\\settextures`.
    section_lookup = {str(name).lower(): name for name in sections}
    alias_map = build_bool_alias_map(sections)
    seq_counter = [0]   # unique id per `if` block
    bare_counter = [0]  # unique id per diffuse line reached with an empty cond_stack
    aux_bare_counters = {channel: 0 for channel in _AUX_MAP_CHANNELS.values()}

    def _new_resource_state():
        return {
            "vertex_resources": {},
            "ambiguous_vertex_slots": set(),
            "ambiguous_vertex_resources": {},
            "index_resource": None,
            "index_resource_bound": False,
            "ambiguous_index_resource": False,
        }

    def _copy_resource_state(state):
        return {
            "vertex_resources": dict(state["vertex_resources"]),
            "ambiguous_vertex_slots": set(
                state["ambiguous_vertex_slots"]),
            "ambiguous_vertex_resources": dict(
                state["ambiguous_vertex_resources"]),
            "index_resource": state["index_resource"],
            "index_resource_bound": state["index_resource_bound"],
            "ambiguous_index_resource": state["ambiguous_index_resource"],
        }

    unset = object()

    def _all_same(values):
        first = values[0]
        if first is unset:
            return all(value is unset for value in values)
        return all(value is not unset and value == first for value in values)

    def _merge_resource_states(states):
        """Join branch exits, retaining only state common to every path."""
        merged = _new_resource_state()
        slots = set().union(*(
            set(state["vertex_resources"])
            | state["ambiguous_vertex_slots"]
            | set(state["ambiguous_vertex_resources"])
            for state in states))
        for slot in slots:
            values = [state["vertex_resources"].get(slot, unset)
                      for state in states]
            if (any(slot in state["ambiguous_vertex_slots"]
                    for state in states) or not _all_same(values)):
                merged["ambiguous_vertex_slots"].add(slot)
                candidates = []
                for state, value in zip(states, values):
                    candidates.extend(
                        state["ambiguous_vertex_resources"].get(slot, ()))
                    if isinstance(value, str):
                        candidates.append(value)
                unique = []
                for candidate in candidates:
                    if all(existing.lower() != candidate.lower()
                           for existing in unique):
                        unique.append(candidate)
                if unique:
                    merged["ambiguous_vertex_resources"][slot] = tuple(unique)
            elif values[0] is not unset:
                merged["vertex_resources"][slot] = values[0]

        index_values = [
            state["index_resource"]
            if state["index_resource_bound"] else unset
            for state in states
        ]
        if (any(state["ambiguous_index_resource"] for state in states)
                or not _all_same(index_values)):
            merged["ambiguous_index_resource"] = True
        elif index_values[0] is not unset:
            merged["index_resource"] = index_values[0]
            merged["index_resource_bound"] = True
        return merged

    def _aux_snapshot(info):
        return {
            channel: {
                "variants": list(state.get("variants") or []),
                "history": list(state.get("history") or []),
            }
            for channel, state in info.get("_aux_maps", {}).items()
            if state.get("variants") or state.get("history")
        }

    def _record_draw(raw, info, cond_stack, operation, count, start, base,
                     auto_count=False):
        combined = DNF_TRUE
        for frame in cond_stack:
            combined = dnf_and(combined, frame["cur"])
        resource_state = info["_resource_state"]
        ambiguous_slots = tuple(sorted(
            resource_state["ambiguous_vertex_slots"]))
        ambiguous_index = resource_state["ambiguous_index_resource"]
        info["draws"].append(AuthoredDrawCall(
            count=count,
            start=start,
            base=base,
            operation=operation,
            auto_count=auto_count,
            conditions=normalize_dnf(combined, toggle_vars, var_prefix),
            source=line_source(raw),
            index_resource=resource_state["index_resource"],
            index_resource_bound=resource_state["index_resource_bound"],
            ambiguous_index_resource=ambiguous_index,
            unsupported_reason=(
                "ambiguous_resource_state"
                if ambiguous_index or ambiguous_slots else None),
            diffuse_variants=list(
                info.get("_cur_diffuse_variants") or []),
            diffuse_history=list(info.get("_diffuse_history") or []),
            vertex_resources=dict(resource_state["vertex_resources"]),
            ambiguous_vertex_slots=ambiguous_slots,
            ambiguous_vertex_resources=dict(
                resource_state["ambiguous_vertex_resources"]),
            auxiliary_maps=_aux_snapshot(info),
        ))

    def _scan(lines, info, cond_stack, visiting, budget, depth=0):
        # cond_stack tracks the stack of active gate branches. Each frame is
        # {"cur": <DNF active for the current branch>,
        #  "seen": <DNF of "some earlier branch at this level already matched">}
        # so `else if` / `else` correctly exclude every preceding branch. It's
        # threaded through run= recursion unchanged, so a called section's own
        # if/elif nests correctly under whichever branch called it.
        if depth > _MAX_RUN_DEPTH:
            raise ValueError(
                f"INI run chain exceeds {_MAX_RUN_DEPTH} nested sections")
        for raw in lines:
            budget["steps"] += 1
            if budget["steps"] > _MAX_EXECUTION_STEPS:
                raise ValueError(
                    "INI command expansion exceeds the viewer safety limit")
            line = raw.split(";")[0].strip()
            if not line: continue
            if info["src"] is None:
                info["src"] = line_source(raw)
            low = line.lower()
            m_elif = re.match(r'(?:else\s+if|elif)\s+(.*)$', line, re.I)
            if m_elif:
                if cond_stack:
                    frame = cond_stack[-1]
                    frame["resource_states"].append(_copy_resource_state(
                        info["_resource_state"]))
                    info["_resource_state"] = _copy_resource_state(
                        frame["entry_resource_state"])
                    branch = parse_condition_dnf(
                        m_elif.group(1).strip(), alias_map)
                    not_seen = dnf_not(frame["seen"])
                    frame["cur"] = dnf_and(not_seen, branch)
                    frame["seen"] = dnf_or(frame["seen"], branch)
                continue
            if low.startswith("if "):
                branch = parse_condition_dnf(line[3:].strip(), alias_map)
                seq_counter[0] += 1
                cond_stack.append({
                    "cur": branch,
                    "seen": branch,
                    "seq": seq_counter[0],
                    "entry_resource_state": _copy_resource_state(
                        info["_resource_state"]),
                    "resource_states": [],
                    "has_else": False,
                })
                continue
            if low == "else":
                if cond_stack:
                    frame = cond_stack[-1]
                    frame["resource_states"].append(_copy_resource_state(
                        info["_resource_state"]))
                    info["_resource_state"] = _copy_resource_state(
                        frame["entry_resource_state"])
                    frame["cur"] = dnf_not(frame["seen"])
                    frame["has_else"] = True
                continue
            if low == "endif":
                if cond_stack:
                    frame = cond_stack.pop()
                    frame["resource_states"].append(_copy_resource_state(
                        info["_resource_state"]))
                    if not frame["has_else"]:
                        frame["resource_states"].append(
                            frame["entry_resource_state"])
                    info["_resource_state"] = _merge_resource_states(
                        frame["resource_states"])
                continue
            m = re.match(r"vb(\d+)\s*=\s*(?:ref\s+)?(\S+)", line, re.I)
            if m:
                slot = int(m.group(1))
                resource = m.group(2)
                value = None if resource.lower() == "null" else resource
                if slot <= 2 and value and not info[f"vb{slot}"]:
                    info[f"vb{slot}"] = value
                state = info["_resource_state"]
                state["vertex_resources"][slot] = value
                state["ambiguous_vertex_slots"].discard(slot)
                state["ambiguous_vertex_resources"].pop(slot, None)
            m = re.match(r"ib\s*=\s*(\S+)", line, re.I)
            if m:
                resource = m.group(1)
                value = None if resource.lower() == "null" else resource
                if value and not info["ib"]:
                    info["ib"] = value
                state = info["_resource_state"]
                state["index_resource"] = value
                state["index_resource_bound"] = True
                state["ambiguous_index_resource"] = False
            if re.match(r"handling\s*=\s*skip\b", line, re.I):
                info["handling_skip"] = True
            m = re.fullmatch(
                r"drawindexed\s*=\s*(\d+)\s*,\s*(\d+)\s*,\s*(-?\d+)\s*",
                line, re.I)
            if m:
                _record_draw(
                    raw, info, cond_stack, "drawindexed",
                    int(m.group(1)), int(m.group(2)), int(m.group(3)))
            elif re.fullmatch(r"drawindexed\s*=\s*auto\s*", line, re.I):
                _record_draw(
                    raw, info, cond_stack, "drawindexed", None, 0, 0,
                    auto_count=True)
            else:
                m_draw = re.fullmatch(
                    r"draw\s*=\s*(\d+)\s*,\s*(\d+)\s*", line, re.I)
                if m_draw:
                    _record_draw(
                        raw, info, cond_stack, "draw",
                        int(m_draw.group(1)), int(m_draw.group(2)), 0)
            if len(info["draws"]) > _MAX_EXECUTION_DRAWS:
                raise ValueError(
                    "INI command expansion produces too many draw calls")
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
                info["_cur_diffuse_variants"].append({"res": res, "cond": cond})
                info["_diffuse_last_cond"] = cond
                # Keep the complete execution-ordered assignment stream too.
                # Independent/nested condition chains can successively
                # override a diffuse; the last matching assignment wins.
                info["_diffuse_history"].append({"res": res, "cond": cond})
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
                state["variants"].append({"res": res, "cond": cond})
                state["last_cond"] = cond
                state["history"].append({"res": res, "cond": cond})
            m = re.match(r"run\s*=\s*(\S+)", line, re.I)
            if m:
                target = m.group(1)
                target_name = section_lookup.get(target.lower())
                target_low = target_name.lower() if target_name else ""
                if (target_name and target_name not in visiting
                        and not any(target_low.startswith(p.lower())
                                   for p in _RUN_SKIP_PREFIXES)):
                    visiting.add(target_name)
                    _scan(
                        sections[target_name], info, cond_stack, visiting,
                        budget, depth + 1)
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
                          _resource_state=_new_resource_state())
        _scan(lines, info, [], {name}, {"steps": 0})
        info.pop("_resource_state", None)
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
            and (info["ib"] or global_ib or any(
                draw.operation == "draw" for draw in info["draws"]))
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

    def _resolve_vertex_binding(slot, res_name):
        if not res_name:
            return None
        info = resolve_vertex_info(res_name)
        filename = info.get("filename")
        stride = info.get("stride")
        if not filename or not stride:
            return None
        return {
            "slot": slot,
            "resource": res_name,
            "filename": filename,
            "stride": stride,
            "format": info.get("format"),
        }

    def _semantic_vertex_bindings(vertex_resources):
        """Choose supported position/UV bindings from concrete active slots.

        Slot conventions remain useful fallback evidence, but explicit
        resource/filename roles and declared formats allow newer templates to
        place those semantics in higher slots without teaching the renderer a
        new game-specific section shape.
        """
        bindings = [
            binding for slot, resource in vertex_resources.items()
            if (binding := _resolve_vertex_binding(slot, resource)) is not None
        ]

        def _format(binding):
            value = (binding.get("format") or "").strip().upper()
            return (value[len("DXGI_FORMAT_"):]
                    if value.startswith("DXGI_FORMAT_") else value)

        position_formats = {
            "R16G16B16A16_FLOAT",
            "R32G32B32_FLOAT",
            "R32G32B32A32_FLOAT",
        }
        texcoord_formats = {
            "R16G16_FLOAT",
            "R16G16B16A16_FLOAT",
            "R32G32_FLOAT",
            "R32G32B32A32_FLOAT",
        }

        # Names rank candidates only after structural admission. Established
        # slots may use the format-less XXMI dump layouts; higher slots must
        # declare a decoder-compatible format.
        position_bindings = [
            binding for binding in bindings
            if (_format(binding) in position_formats
                if _format(binding) else binding["slot"] == 0)
        ]
        texcoord_bindings = [
            binding for binding in bindings
            if (_format(binding) in texcoord_formats
                if _format(binding) else binding["slot"] in (1, 2))
        ]

        def _label(binding):
            return (binding["resource"] + " " + binding["filename"]).lower()

        def _position_score(binding):
            label = _label(binding)
            fmt = (binding.get("format") or "").upper()
            score = 30 if binding["slot"] == 0 else 0
            if "position" in label:
                score += 120
            elif "pos" in label:
                score += 45
            if "R32G32B32" in fmt or "R16G16B16A16_FLOAT" in fmt:
                score += 80
            if "texcoord" in label or "blend" in label:
                score -= 150
            return score

        position = max(position_bindings, key=_position_score, default=None)
        if position is not None and _position_score(position) <= 0:
            position = None

        def _texcoord_score(binding):
            if binding is position:
                return -1000
            label = _label(binding)
            fmt = (binding.get("format") or "").upper()
            score = 25 if binding["slot"] in (1, 2) else 0
            if "texcoord" in label:
                score += 120
            elif "uv" in label or "tc" in label:
                score += 45
            if ("R16G16_FLOAT" in fmt or "R32G32_FLOAT" in fmt):
                score += 80
            if "blend" in label or "position" in label:
                score -= 150
            if binding["stride"] == 32 and "texcoord" not in label:
                score -= 100
            return score

        texcoord = max(texcoord_bindings, key=_texcoord_score, default=None)
        if texcoord is not None and _texcoord_score(texcoord) <= 0:
            texcoord = None
        return position, texcoord

    def _has_ambiguous_geometry_state(authored):
        if (authored.operation != "draw"
                and authored.ambiguous_index_resource):
            return True
        for slot, candidates in authored.ambiguous_vertex_resources.items():
            for candidate in candidates:
                position, texcoord = _semantic_vertex_bindings(
                    {slot: candidate})
                if position or texcoord:
                    return True
        return False

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
        # Indexed components can resolve missing streams through their IB and
        # sibling override structure. A non-indexed draw has no such identity:
        # require its own execution path to bind both supported vertex
        # semantics, otherwise a Blend/upload/UI pass can be mistaken for a
        # sequential triangle list after borrowing unrelated fallback buffers.
        render_draws = []
        for authored in info["draws"]:
            if _has_ambiguous_geometry_state(authored):
                continue
            if authored.operation != "draw":
                render_draws.append(authored)
                continue
            position, texcoord = _semantic_vertex_bindings(
                authored.vertex_resources)
            if position and texcoord:
                render_draws.append(authored)
        if info["draws"] and not render_draws:
            continue
        if (not info["draws"]
                and (not info["ib"] or info["handling_skip"])):
            continue

        display_name = sec_name[len("TextureOverride"):] or sec_name
        seen[display_name] = seen.get(display_name, 0) + 1
        label = display_name
        if seen[display_name] > 1: label = f"{display_name}_{seen[display_name]}"

        ib_res = info["ib"] or global_ib
        comp   = (_ib_res_to_component(ib_res) if ib_res else display_name)
        buf    = _lookup_comp_buf(comp)
        authored_position = authored_texcoord = None
        if render_draws:
            authored_position, authored_texcoord = _semantic_vertex_bindings(
                render_draws[0].vertex_resources)
        if not buf and render_draws:
            if authored_position and authored_texcoord:
                buf = {
                    "position": authored_position["resource"],
                    "texcoord": authored_texcoord["resource"],
                }
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
        requires_ib = (not render_draws or any(
            draw.operation != "draw" for draw in render_draws))
        tc_stride  = tc_ri.get("stride", 20)
        pos_stride = pos_ri.get("stride", POSITION_STRIDE)
        position_slot = (authored_position["slot"] if authored_position
                         and authored_position["resource"] == buf["position"]
                         else None)
        texcoord_slot = (authored_texcoord["slot"] if authored_texcoord
                         and authored_texcoord["resource"] == buf["texcoord"]
                         else None)
        resolved_index_layout = (
            index_layout(ib_ri.get("format")) if ib_file else None)
        index_size = (resolved_index_layout.size
                      if resolved_index_layout else None)
        if (not (pos_file and tc_file)
                or (requires_ib and (not ib_file or index_size is None))):
            continue
        uv_off     = DEFAULT_UV_OFFSET

        draws_list = list(render_draws) or [AuthoredDrawCall(
            count=None,
            start=0,
            base=0,
            operation="implicit_indexed",
            auto_count=True,
            source=info["src"],
            diffuse_variants=info.get("diffuse_variants_at_end") or [],
            diffuse_history=info.get("diffuse_history_at_end") or [],
            auxiliary_maps=info.get("aux_maps_at_end") or {},
        )]
        draws = []
        for i, authored in enumerate(draws_list, 1):
            d = DrawCall(
                label=f"{label}-{i}",
                operation=authored.operation,
                count=authored.count,
                start=authored.start,
                base=authored.base,
                auto_count=authored.auto_count,
                conditions=authored.conditions,
                sources=[authored.source] if authored.source else [],
                ib_file=ib_file,
                index_size=index_size,
                position_file=pos_file,
                position_stride=pos_stride,
                position_slot=position_slot,
                position_format=pos_ri.get("format"),
                texcoord_file=tc_file,
                texcoord_stride=tc_stride,
                texcoord_slot=texcoord_slot,
                texcoord_format=tc_ri.get("format"),
            )
            # Resolve the effective buffers before deduplication. Concrete
            # per-draw VB bindings win; runtime-only resources retain the
            # existing IB/component heuristic, while an authored null stays
            # unbound instead of inheriting the group's first binding.
            effective_ib = (authored.index_resource
                            if authored.index_resource_bound else ib_res)
            if authored.operation == "draw":
                d.ib_file = None
                d.index_size = None
            elif effective_ib != ib_res:
                resolved_ib = _resolve_ib_file(effective_ib)
                resolved_layout = index_layout(
                    _res_get(resources, effective_ib).get("format"))
                if resolved_ib and resolved_layout:
                    d.ib_file = resolved_ib
                    d.index_size = resolved_layout.size
                else:
                    d.ib_file = None
                    d.index_size = None

            draw_buf = (_lookup_comp_buf(_ib_res_to_component(effective_ib))
                        if effective_ib else None)
            if draw_buf and draw_buf != buf:
                position_binding = _resolve_vertex_binding(
                    None, draw_buf["position"])
                texcoord_binding = _resolve_vertex_binding(
                    None, draw_buf["texcoord"])
                if position_binding:
                    d.position_file = position_binding["filename"]
                    d.position_stride = position_binding["stride"]
                    d.position_format = position_binding["format"]
                if texcoord_binding:
                    d.texcoord_file = texcoord_binding["filename"]
                    d.texcoord_stride = texcoord_binding["stride"]
                    d.texcoord_format = texcoord_binding["format"]

            vertex_resources = authored.vertex_resources
            position_binding, texcoord_binding = _semantic_vertex_bindings(
                vertex_resources)
            if position_binding:
                d.position_file = position_binding["filename"]
                d.position_stride = position_binding["stride"]
                d.position_slot = position_binding["slot"]
                d.position_format = position_binding["format"]
            elif (0 in vertex_resources and vertex_resources[0] is None) or (
                    d.position_slot in vertex_resources
                    and vertex_resources[d.position_slot] is None):
                d.position_file = None
                d.position_stride = None
                d.position_format = None
            if texcoord_binding:
                d.texcoord_file = texcoord_binding["filename"]
                d.texcoord_stride = texcoord_binding["stride"]
                d.texcoord_slot = texcoord_binding["slot"]
                d.texcoord_format = texcoord_binding["format"]
            elif any(vertex_resources.get(slot, "bound") is None
                     for slot in {1, 2, d.texcoord_slot} if slot is not None):
                d.texcoord_file = None
                d.texcoord_stride = None
                d.texcoord_format = None
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
                    variants.append({"conditions": v["cond"], "file": file})
            if variants:
                d.set_texture_default("diffuse", variants[0]["file"])
            # A toggle that swaps the diffuse texture rather than gating a draw.
            if len(variants) > 1:
                d.set_texture_variants("diffuse", variants)
            history = []
            for v in authored.diffuse_history:
                file = _resolve_diffuse_file(v["res"])
                if file:
                    history.append({"conditions": v["cond"], "file": file})
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
                    resolved.append({"conditions": conditions, "file": file})
                    if not conditions:
                        default_file = file
                if default_file:
                    d.set_texture_default(channel, default_file)
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
            position_slot=position_slot, position_format=pos_ri.get("format"),
            position_offset=0,
            texcoord_stride=tc_stride, texcoord_uv_off=uv_off,
            texcoord_slot=texcoord_slot, texcoord_format=tc_ri.get("format"),
            texcoord_offset=None,
            ib_file=ib_file, diffuse_file=diff_ri.get("filename"),
            diffuse_pool_files=pool_files,
            index_size=index_size,
            draws=draws,
        ))

    return groups

"""3DMigoto INI parsing and draw-group extraction."""

import re, os

from .mesh_builder import POSITION_STRIDE, DEFAULT_UV_OFFSET, _res_get


def find_inis(mod_dir):
    """Return all non-DISABLED .ini files in mod_dir."""
    return [
        os.path.join(mod_dir, fname)
        for fname in sorted(os.listdir(mod_dir))
        if not fname.upper().startswith("DISABLED")
        and fname.lower().endswith(".ini")
    ]


def merge_sections(ini_paths, overrides=None):
    """Parse all ini files and merge sections with the same name.

    `overrides`, if given, is {ini_path: text} — parse that ini from this
    in-memory text instead of reading it from disk. Used to preview pending,
    not-yet-exported edits (see app/edit_session.py) without writing
    anything to the real file first; an ini not present in `overrides` is
    read from disk exactly as before.
    """
    overrides = overrides or {}
    combined: dict = {}
    for path in ini_paths:
        for name, lines in parse_sections(path, text=overrides.get(path)).items():
            combined.setdefault(name, []).extend(lines)
    return combined


class SrcLine(str):
    """A section line that remembers where it came from.

    Sections are merged across every ini in a mod folder, so by the time a
    draw is built the originating file is otherwise unrecoverable. Subclassing
    `str` keeps every existing string operation working unchanged; only code
    that actually wants provenance has to know this type exists.
    """
    __slots__ = ("ini_path", "line_no", "section")

    def __new__(cls, text, ini_path=None, line_no=None, section=None):
        obj = super().__new__(cls, text)
        obj.ini_path = ini_path
        obj.line_no  = line_no
        obj.section  = section
        return obj

    def source(self):
        return {"ini_path": self.ini_path, "line_no": self.line_no,
                "section": self.section}


def line_source(line):
    """Provenance dict for a section line, or None if it carries none."""
    if isinstance(line, SrcLine) and line.ini_path is not None:
        return line.source()
    return None


def parse_sections(ini_path, text=None):
    """Parse one ini file's sections, either from disk (the default) or from
    `text` directly. The in-memory path lets a caller preview a pending,
    not-yet-exported edit (see app/edit_session.py) — `ini_path` still tags
    every SrcLine's provenance either way, it's just not opened when `text`
    is supplied.
    """
    sections, current = {}, None

    def feed(line_no, raw):
        nonlocal current
        line = raw.strip()
        if not line or line.startswith(";"): return
        # ';' is a legitimate 3DMigoto key binding, so it must not be
        # treated as an inline comment on key/back lines — e.g.
        # "key = no_ctrl no_Shift no_alt ;" binds the semicolon key.
        lhs = line.split("=", 1)[0].strip().lower()
        if lhs not in ("key", "back"):
            line = line.split(";")[0].strip()
        if not line: return
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip()
            sections.setdefault(current, [])
        elif current is not None:
            sections[current].append(SrcLine(line, ini_path, line_no, current))

    if text is not None:
        for line_no, raw in enumerate(text.splitlines(), 1):
            feed(line_no, raw)
    else:
        with open(ini_path, encoding="utf-8", errors="ignore") as f:
            for line_no, raw in enumerate(f, 1):
                feed(line_no, raw)
    return sections


def extract_resources(sections):
    """Return {resource_name: {filename, stride, format}} from Resource sections."""
    SKIP = ("TextureOverride", "CommandList", "ShaderOverride", "Present", "Key", "Constants")
    resources = {}
    for name, lines in sections.items():
        if any(name.startswith(p) for p in SKIP): continue
        res = {}
        for line in lines:
            if "=" not in line: continue
            k, _, v = line.partition("=")
            k = k.strip().lower(); v = v.strip()
            if   k == "filename": res["filename"] = v
            elif k == "stride":
                try: res["stride"] = int(v)
                except: pass
            elif k == "format":   res["format"] = v
        if "filename" in res:
            resources[name] = res
    return resources


def _format_key_combo(combo):
    """Turn 'no_ctrl no_Shift no_alt l' into a short display like 'L'."""
    mods, main = [], None
    for tok in combo.split():
        tl = tok.lower()
        if tl.startswith("no_"): continue
        if tl in ("ctrl", "shift", "alt"): mods.append(tl.capitalize())
        else: main = tok
    if main is None:
        return combo
    key_part = main.upper() if len(main) == 1 else main
    return "+".join(mods + [key_part])


def extract_toggle_keys(sections, var_prefix=None, source=None):
    """Return {group: {name, key, key_display, vars, source}} for cycle-type
    [Key...] sections, where `vars` is {variable: [cycle values]}.

    A single Key section can drive several variables at once (3DMigoto advances
    them all on one keypress), so entries are keyed by section rather than by
    variable and `vars` may hold more than one. When multiple ini files share a
    folder (AllInOne mods), the same section/variable name can appear in more
    than one file — var_prefix keeps keys unique, while `source` (e.g. the ini
    filename) tags each entry so the UI can group same-named keys into per-ini
    sub-sections instead of lengthening every display name."""
    keys = {}
    for name, lines in sections.items():
        if not name.startswith("Key"): continue
        key_combo, ktype, cvars = None, None, {}
        src = None
        for line in lines:
            if src is None:
                src = line_source(line)
            if "=" not in line: continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip()
            kl = k.lower()
            if   kl == "key":  key_combo = v
            elif kl == "type": ktype = v.lower()
            elif k.startswith("$"):
                var = k[1:].strip()
                values = [p.strip() for p in v.split(",") if p.strip()]
                if var and values:
                    cvars[f"{var_prefix}{var}" if var_prefix else var] = values
        if ktype == "cycle" and cvars:
            label = name[3:] if name[:3].lower() == "key" else name
            keys[f"{var_prefix}{name}" if var_prefix else name] = {
                "name": label,
                "key": key_combo or "",
                "key_display": _format_key_combo(key_combo) if key_combo else "",
                "vars": cvars,
                "source": source,
                "ini_path": (src or {}).get("ini_path"),
                "section": name,
            }
    return keys


def extract_toggle_var_names(sections, var_prefix=None):
    """Flat set of every variable driven by a cycle-type [Key...] section."""
    return {var
            for info in extract_toggle_keys(sections, var_prefix=var_prefix).values()
            for var in info["vars"]}


_DEFAULT_VAR_RE = re.compile(r'^(?:global\s+)?(?:persist\s+)?\$(\w+)\s*=\s*([^,]+)$', re.I)


def extract_variable_defaults(sections, var_prefix=None):
    """Return {variable: default_value} from `global [persist] $var = value` lines
    (comma-separated cycle-list assignments inside Key sections don't match).
    var_prefix namespaces keys so same-named vars from different ini files don't collide."""
    defaults = {}
    for lines in sections.values():
        for line in lines:
            m = _DEFAULT_VAR_RE.match(line.strip())
            if m:
                var_key = f"{var_prefix}{m.group(1)}" if var_prefix else m.group(1)
                if var_key not in defaults:
                    defaults[var_key] = m.group(2).strip()
    return defaults


def _ib_res_to_component(ib_res):
    s = ib_res[8:] if ib_res.startswith("Resource") else ib_res
    if s.endswith("IB"): s = s[:-2]
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


_CLAUSE_RE = re.compile(r'\$(\w+)\s*(==|!=)\s*(-?[\w.]+)')
_ASSIGN_BOOL_RE = re.compile(r'^\$(\w+)\s*=\s*(.+)$')


# Conditions are represented in DNF ("disjunctive normal form"): a list of
# OR'd alternatives, each alternative being a list of AND'd {var,value,negate}
# clauses. Two sentinel values matter:
#   DNF_TRUE  = [[]]  -> one alternative with no constraints (always visible)
#   DNF_FALSE = []    -> no satisfiable alternative (never visible)
# `[]` doubles as "no tracked constraint" once untracked vars are filtered out,
# which is why _normalize_dnf() collapses an always-true result back to [].
DNF_TRUE:  list = [[]]
DNF_FALSE: list = []

# Cap DNF growth: AND-ing/negating deeply nested ||-expressions can blow up
# combinatorially. Past this many alternatives the condition is treated as
# unconstrained (always visible), which fails open rather than hiding meshes.
_MAX_DNF_GROUPS = 128


def _dnf_or(a, b):
    out = list(a)
    for g in b:
        if g not in out:
            out.append(g)
    return out if len(out) <= _MAX_DNF_GROUPS else DNF_TRUE


def _simplify_group(group):
    """Drop `$v != x` clauses made redundant by a `$v == y` clause on the same
    variable. An elif chain accumulates the negation of every earlier branch, so
    `$v != 0 AND $v != 1 AND $v == 2` is common -- and `$v == 2` alone says it.

    Deliberately conservative: contradictions (`$v == 1 AND $v != 1`, or two
    different `==` values) are left intact rather than collapsed to an empty
    group, because an empty group is DNF_TRUE ("always visible") and would flip
    an impossible condition into an unconditional one."""
    eq: dict = {}
    for c in group:
        if not c["negate"]:
            eq.setdefault(c["var"], set()).add(c["value"])
    redundant = {v: vals.pop() for v, vals in eq.items() if len(vals) == 1}
    if not redundant:
        return group
    return [c for c in group
            if not (c["negate"] and redundant.get(c["var"], c["value"]) != c["value"])]


def _dnf_and(a, b):
    if len(a) * len(b) > _MAX_DNF_GROUPS:
        return DNF_TRUE
    out: list = []
    for ga in a:
        for gb in b:
            merged = list(ga)
            for c in gb:
                if c not in merged:
                    merged.append(c)
            merged = _simplify_group(merged)
            if merged not in out:
                out.append(merged)
    return out


def _dnf_not(dnf):
    """NOT of a DNF, via De Morgan: NOT(g1 OR g2) == NOT(g1) AND NOT(g2),
    and NOT(c1 AND c2) == (NOT c1) OR (NOT c2)."""
    result = DNF_TRUE
    for group in dnf:
        neg_group = [[{"var": c["var"], "value": c["value"], "negate": not c["negate"]}]
                     for c in group]
        result = _dnf_and(result, neg_group)
    return result


def _atom_to_dnf(atom, alias_map):
    """Convert a single comparison / bare-boolean token into DNF. Anything that
    can't be traced to a real variable (numeric literals, DRAW_TYPE, unsupported
    operators like <=) becomes DNF_TRUE so it never hides a mesh."""
    atom = atom.strip()
    if not atom:
        return DNF_TRUE
    negate_atom = False
    while atom.startswith("!"):
        negate_atom = not negate_atom
        atom = atom[1:].strip()

    m = _CLAUSE_RE.fullmatch(atom)
    if m:
        v, op, val = m.group(1), m.group(2), m.group(3)
        dnf = [[{"var": v, "value": val, "negate": op == "!="}]]
    else:
        m = re.fullmatch(r'\$(\w+)', atom)
        if m:
            # alias_map values are already DNF.
            dnf = alias_map.get(m.group(1)) or DNF_TRUE
        else:
            dnf = DNF_TRUE
    return _dnf_not(dnf) if negate_atom else dnf


_STRUCT_RE = re.compile(r'(\(|\)|&&|\|\|)')


def _parse_condition_dnf(content, alias_map):
    """Parse an `if <expr>` expression into DNF, honouring &&, || and
    parentheses. Previously every comparison found anywhere in the expression
    was blindly AND'd together, so `$x == 0 || $x == 2` became the impossible
    `$x == 0 && $x == 2` and its mesh could never be shown."""
    tokens = [t.strip() for t in _STRUCT_RE.split(content) if t and t.strip()]
    pos = 0

    def parse_or():
        nonlocal pos
        node = parse_and()
        while pos < len(tokens) and tokens[pos] == "||":
            pos += 1
            node = _dnf_or(node, parse_and())
        return node

    def parse_and():
        nonlocal pos
        node = parse_atom()
        while pos < len(tokens) and tokens[pos] == "&&":
            pos += 1
            node = _dnf_and(node, parse_atom())
        return node

    def parse_atom():
        nonlocal pos
        if pos >= len(tokens):
            return DNF_TRUE
        tok = tokens[pos]
        if tok == "(":
            pos += 1
            node = parse_or()
            if pos < len(tokens) and tokens[pos] == ")":
                pos += 1
            return node
        if tok in (")", "&&", "||"):
            pos += 1
            return DNF_TRUE
        pos += 1
        return _atom_to_dnf(tok, alias_map)

    try:
        return parse_or()
    except RecursionError:
        return DNF_TRUE


def _normalize_dnf(dnf, toggle_vars, var_prefix=None):
    """Drop clauses on untracked variables (they're assumed satisfied, matching
    long-standing behaviour), then apply var_prefix. An alternative left with no
    clauses is unconditionally true, which makes the whole condition true -> []."""
    out: list = []
    for group in dnf:
        kept = [c for c in group if c["var"] in toggle_vars]
        if not kept:
            return []
        if var_prefix:
            kept = [{"var": f"{var_prefix}{c['var']}", "value": c["value"], "negate": c["negate"]}
                    for c in kept]
        if kept not in out:
            out.append(kept)
    return out


def _build_bool_alias_map(sections):
    """Resolve WWMI-style boolean aliases such as
    `$draw_component_4_heels_flat = ($swapvar_heels == 1)` into a map of
    alias_var -> DNF, so a later bare `if $draw_component_4_heels_flat` can
    be traced back to the real toggle var. The RHS is parsed as a full
    boolean expression (not just AND'd clauses) so an ||-alias like
    `($swapvar_arm == 0) || ($swapvar_arm == 2)` doesn't collapse to the
    impossible `== 0 && == 2`. Two passes let an alias reference an earlier one."""
    raw_defs: dict = {}
    for lines in sections.values():
        for raw in lines:
            line = raw.split(";")[0].strip()
            m = _ASSIGN_BOOL_RE.match(line)
            if not m: continue
            alias, rhs = m.group(1), m.group(2).strip()
            # Only boolean expressions are aliases; `$swapvar = 0` is a value init.
            if "==" not in rhs and "!=" not in rhs: continue
            if alias not in raw_defs:
                raw_defs[alias] = rhs

    alias_map: dict = {}
    for _ in range(2):
        for alias, rhs in raw_defs.items():
            dnf = _parse_condition_dnf(rhs, alias_map)
            if dnf and dnf != DNF_TRUE:
                alias_map[alias] = dnf
    return alias_map


_RUN_SKIP_PREFIXES = ("TextureOverride", "ShaderOverride", "Resource", "Present", "Key", "Constants")


def _scan_sections_for_draws(sections, var_prefix=None):
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
    `draws` entries are (count, start, base, conds, source, ib, diffuse_variants,
    vb_snapshot) tuples, where vb_snapshot is the (vb0, vb1, vb2) most recently
    assigned before that line -- kept for provenance only; build_draw_groups
    re-derives the actual position/texcoord buffers for a reassigned `ib`
    from its component instead of trusting these literal values.
    """
    # Vars driven by a cycle-type [Key...] section — only these are worth tracking
    # as per-draw show/hide gates (internal state vars like $mod_enabled are ignored).
    toggle_vars = extract_toggle_var_names(sections)
    alias_map = _build_bool_alias_map(sections)
    seq_counter = [0]  # unique id per `if` block

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
                    branch = _parse_condition_dnf(m_elif.group(1).strip(), alias_map)
                    not_seen = _dnf_not(frame["seen"])
                    frame["cur"] = _dnf_and(not_seen, branch)
                    frame["seen"] = _dnf_or(frame["seen"], branch)
                continue
            if low.startswith("if "):
                branch = _parse_condition_dnf(line[3:].strip(), alias_map)
                seq_counter[0] += 1
                cond_stack.append({"cur": branch, "seen": branch, "seq": seq_counter[0]})
                continue
            if low == "else":
                if cond_stack:
                    frame = cond_stack[-1]
                    frame["cur"] = _dnf_not(frame["seen"])
                continue
            if low == "endif":
                if cond_stack: cond_stack.pop()
                continue
            m = re.match(r"vb0\s*=\s*(\S+)", line, re.I)
            if m:
                if not info["vb0"]: info["vb0"] = m.group(1)
                info["_cur_vb0"] = m.group(1)
            m = re.match(r"vb1\s*=\s*(\S+)", line, re.I)
            if m:
                if not info["vb1"]: info["vb1"] = m.group(1)
                info["_cur_vb1"] = m.group(1)
            m = re.match(r"vb2\s*=\s*(\S+)", line, re.I)
            if m:
                if not info["vb2"]: info["vb2"] = m.group(1)
                info["_cur_vb2"] = m.group(1)
            m = re.match(r"ib\s*=\s*(\S+)", line, re.I)
            if m:
                if not info["ib"]: info["ib"] = m.group(1)
                info["_cur_ib"] = m.group(1)
            if re.match(r"handling\s*=\s*skip\b", line, re.I):
                info["handling_skip"] = True
            m = re.match(r"drawindexed\s*=\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", line, re.I)
            if m:
                combined = DNF_TRUE
                for frame in cond_stack:
                    combined = _dnf_and(combined, frame["cur"])
                conds = _normalize_dnf(combined, toggle_vars, var_prefix)
                info["draws"].append((int(m.group(1)), int(m.group(2)),
                                      int(m.group(3)), conds, line_source(raw),
                                      info.get("_cur_ib"),
                                      list(info.get("_cur_diffuse_variants") or []),
                                      (info.get("_cur_vb0"), info.get("_cur_vb1"),
                                       info.get("_cur_vb2"))))
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
                combined = DNF_TRUE
                for frame in cond_stack:
                    combined = _dnf_and(combined, frame["cur"])
                cond = _normalize_dnf(combined, toggle_vars, var_prefix)
                chain_key = cond_stack[-1]["seq"] if cond_stack else None
                if chain_key != info.get("_diffuse_chain_key"):
                    info["_cur_diffuse_variants"] = []
                    info["_diffuse_chain_key"] = chain_key
                info["_cur_diffuse_variants"].append({"res": res, "cond": cond})
            m = re.match(r"run\s*=\s*(\S+)", line, re.I)
            if m:
                target = m.group(1)
                if (target in sections and target not in visiting
                        and not any(target.startswith(p) for p in _RUN_SKIP_PREFIXES)):
                    visiting.add(target)
                    _scan(sections[target], info, cond_stack, visiting)
                    visiting.discard(target)

    # scan BOTH TextureOverride AND CommandList sections.
    sec_info: dict = {}
    for name, lines in sections.items():
        if not (name.startswith("TextureOverride") or name.startswith("CommandList")):
            continue
        info: dict = dict(vb0=None, vb1=None, vb2=None, ib=None, draws=[],
                          diffuse=None, src=None, handling_skip=False,
                          _cur_diffuse_variants=[], _diffuse_chain_key=None)
        _scan(lines, info, [], {name})
        info.pop("_cur_ib", None)
        info.pop("_cur_vb0", None)
        info.pop("_cur_vb1", None)
        info.pop("_cur_vb2", None)
        info.pop("_cur_diffuse_variants", None)
        info.pop("_diffuse_chain_key", None)
        sec_info[name] = info
    return sec_info



def build_draw_groups(sections, resources, var_prefix=None, source=None, seen=None):
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
    sec_info = _scan_sections_for_draws(sections, var_prefix)

    comp_pos, comp_tc = {}, {}
    # Index by underscore-delimited hex hash for mods that use _<hash>_ in section names
    hash_pos: dict = {}
    hash_tc:  dict = {}

    # Texcoord sections set comp_tc first (highest priority — must win over Blend)
    for name, info in sec_info.items():
        if not name.startswith("TextureOverride"): continue
        base = name[len("TextureOverride"):]
        if base.endswith("Texcoord"):
            comp = base[:-len("Texcoord")]
            if info["vb1"]: comp_tc[comp] = info["vb1"]

    # Blend and Position sections (lower priority for tc)
    for name, info in sec_info.items():
        if not name.startswith("TextureOverride"): continue
        base = name[len("TextureOverride"):]
        if base.endswith("Blend"):
            comp = base[:-len("Blend")]
            if info["vb0"] and comp not in comp_pos: comp_pos[comp] = info["vb0"]
            # GIMI: *Blend sets vb1 to the blend buffer (stride 32), not texcoord
            if info["vb1"] and comp not in comp_tc:
                if _res_get(resources, info["vb1"]).get("stride", 0) != 32:
                    comp_tc[comp] = info["vb1"]
        elif base.endswith("Position"):
            # GIMI: vb0 is in a *Position section, not *Blend
            comp = base[:-len("Position")]
            if info["vb0"] and comp not in comp_pos: comp_pos[comp] = info["vb0"]
        # Build hash map; skip vb2 if it's a blend buffer (stride=32, ZZMI format)
        # so it doesn't shadow the real texcoord in vb1. WWMI uses vb2=TexCoord (stride≠32).
        h = _extract_hash(name)
        if h:
            if info["vb0"] and h not in hash_pos: hash_pos[h] = info["vb0"]
            vb2_stride = _res_get(resources, info["vb2"]).get("stride", 0) if info["vb2"] else 0
            tc = (info["vb2"] if info["vb2"] and vb2_stride != 32 else None) or info["vb1"]
            if tc and h not in hash_tc: hash_tc[h] = tc
    comp_bufs = {c: {"position": comp_pos[c], "texcoord": comp_tc[c]}
                 for c in comp_pos if c in comp_tc}

    # pass 2b: discover global IB/position/texcoord from CommandList sections (WWMI)
    global_ib, global_pos, global_tc = None, None, None
    for name, info in sec_info.items():
        if not name.startswith("CommandList"): continue
        if info["ib"]  and not global_ib:  global_ib  = info["ib"]
        if info["vb0"] and not global_pos: global_pos = info["vb0"]
        tc = info["vb2"] or info["vb1"]  # vb2=TexCoord, vb1=Vector in WWMI
        if tc and not global_tc: global_tc = tc

    # If global_pos is a computed buffer (no filename, e.g. ResourceShapeKeyedPosition),
    # fall back to the nearest file-backed R32G32B32 position buffer (WWMI shape keys).
    if global_pos and not _res_get(resources, global_pos).get("filename"):
        for res_name, res_info in resources.items():
            fmt = res_info.get("format", "")
            if res_info.get("filename") and "R32G32B32" in fmt:
                global_pos = res_name
                break

    # pass 3: collect draw sections.
    # A section with `ib=` but no drawindexed lines normally lets the game's
    # original (whole-buffer) draw call proceed unmodified, just against the
    # new ib -- so it's kept as an implicit full-buffer draw. But `handling =
    # skip` means the opposite: the original draw call itself is suppressed,
    # so with no drawindexed lines to replace it, nothing is drawn at all.
    draw_secs = [(n, i) for n, i in sec_info.items()
                 if n.startswith("TextureOverride")
                 and (i["ib"] or global_ib)
                 and (i["draws"] or (i["ib"] and not i["handling_skip"]))]
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

    vertex_res_cache: dict = {}

    def _resolve_vertex_res(res_name):
        if res_name not in vertex_res_cache:
            ri = _res_get(resources, res_name)
            vertex_res_cache[res_name] = (ri.get("filename"), ri.get("stride"))
        return vertex_res_cache[res_name]

    def _lookup_comp_buf(comp):
        buf = comp_bufs.get(comp)
        if not buf:
            c2 = re.sub(r"[A-Za-z]+$", "", comp)
            if c2 and c2 != comp: buf = comp_bufs.get(c2)
        if not buf:
            # Strip last CamelCase word.
            c2 = re.sub(r"(?<=.)[A-Z][a-z]+$", "", comp)
            if c2 and c2 != comp: buf = comp_bufs.get(c2)
        return buf

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
            h = _extract_hash(sec_name) or _extract_hash(ib_res)
            if h and h in hash_pos and h in hash_tc:
                buf = {"position": hash_pos[h], "texcoord": hash_tc[h]}
        if not buf and global_pos and global_tc:
            buf = {"position": global_pos, "texcoord": global_tc}  # WWMI fallback
        if not buf: continue

        pos_ri  = _res_get(resources, buf["position"])
        tc_ri   = _res_get(resources, buf["texcoord"])
        ib_ri   = _res_get(resources, ib_res)
        diff_ri = _res_get(resources, info["diffuse"]) if info["diffuse"] else {}

        pos_file = pos_ri.get("filename")
        tc_file  = tc_ri.get("filename")
        ib_file  = ib_ri.get("filename")
        if not (pos_file and tc_file and ib_file): continue

        tc_stride  = tc_ri.get("stride", 20)
        pos_stride = pos_ri.get("stride", POSITION_STRIDE)
        uv_off     = DEFAULT_UV_OFFSET

        draws_list = list(info["draws"]) or [(None, 0, 0, [], info["src"], None, [], (None, None, None))]
        draws = []
        for i, (c, s, b, cd, src, draw_ib, diff_variants, _vb_ov) in enumerate(draws_list, 1):
            d = dict(label=f"{label}-{i}", count=c, start=s, base=b,
                     conditions=cd, sources=[src] if src else [])
            # A mid-section `ib = ...` reassignment.
            if draw_ib and draw_ib != ib_res:
                resolved = _resolve_ib_file(draw_ib)
                if resolved:
                    d["ib_file"] = resolved
                    d["index_size"] = _ib_index_size(_res_get(resources, draw_ib).get("format"))
                draw_buf = _lookup_comp_buf(_ib_res_to_component(draw_ib))
                if draw_buf and draw_buf != buf:
                    pfile, pstride = _resolve_vertex_res(draw_buf["position"])
                    tfile, tstride = _resolve_vertex_res(draw_buf["texcoord"])
                    if pfile and tfile:
                        d["position_file"] = pfile
                        d["texcoord_file"] = tfile
                        d["position_stride"] = pstride or POSITION_STRIDE
                        d["texcoord_stride"] = tstride or 20
            # A toggle that swaps the diffuse texture rather than gating a draw.
            if len(diff_variants) > 1:
                variants = []
                for v in diff_variants:
                    file = _resolve_diffuse_file(v["res"])
                    if file:
                        variants.append({"conditions": v["cond"], "file": file})
                if len(variants) > 1:
                    d["texture_variants"] = variants
            draws.append(d)
        groups.append(dict(
            name=label,
            display_name=display_name,
            source=source,
            position_file=pos_file, texcoord_file=tc_file,
            position_stride=pos_stride,
            texcoord_stride=tc_stride, texcoord_uv_off=uv_off,
            ib_file=ib_file, diffuse_file=diff_ri.get("filename"),
            index_size=_ib_index_size(ib_ri.get("format")),
            draws=draws,
        ))

    return groups

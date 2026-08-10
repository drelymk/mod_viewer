"""3DMigoto binary buffer readers and in-memory mesh payload builder."""

import re, struct, os, base64, io

# ── Buffer constants ───────────────────────────────────────────────────────────
POSITION_STRIDE   = 40
POSITION_OFFSET   = 0
DEFAULT_UV_OFFSET = 4
INDEX_SIZE        = 4


def _res_get(resources, name):
    """Case-insensitive resource lookup (handles WWMI naming inconsistencies)."""
    if not name: return {}
    if name in resources: return resources[name]
    nl = name.lower()
    for k, v in resources.items():
        if k.lower() == nl: return v
    return {}


_MAX_ESCAPE_DEPTH = 1   # levels above mod_dir a `filename = ..\...` may reach


def _safe_join(mod_dir, rel):
    # Resolve a Resource section's `filename = ...` relative to mod_dir.

    if not rel: return None
    if os.path.isabs(rel) or os.path.splitdrive(rel)[0]:
        return None
    root = os.path.abspath(mod_dir)
    target = os.path.abspath(os.path.join(root, rel))
    if not _within(target, root):
        ceiling = root
        for _ in range(_MAX_ESCAPE_DEPTH):
            ceiling = os.path.dirname(ceiling)   # stops at the drive root
        if not _within(target, ceiling):
            return None
    return target


def _within(target, root):
    return target == root or target.startswith(root + os.sep)


# ── Binary readers ─────────────────────────────────────────────────────────────

def read_positions(buf_path, stride=POSITION_STRIDE):
    positions = []
    with open(buf_path, "rb") as f: data = f.read()
    for off in range(0, len(data) - 11, stride):
        positions.append(struct.unpack_from("<fff", data, off + POSITION_OFFSET))
    return positions


_MIN_AXIS_SPREAD = 1e-4   # below this an axis is constant, i.e. not a real UV set
_MIN_IN_RANGE    = 0.95   # fraction of sampled UVs that must land in [0, 2]


def _detect_uv_best(tc_path, stride, n=4096):
    """Try (offset 0 or 4) x (float16 or float32) and return the (uv_off, fmt)
    with the largest UV spread where all values are within [0, 2].

    Candidates are sampled EVENLY ACROSS THE WHOLE BUFFER, not from the first
    n vertices: consecutive vertices are spatially adjacent, so a head-only
    window sees a tiny patch of the UV map where every candidate's spread is
    near zero and noise decides the winner. Measured on a real stride-24 ZZMI
    mod (Remielle/Elysian Abyss): over the first 30 vertices the wrong '<ff'
    scored 0.0562 against the correct '<ee''s 0.0501 and won, mapping the whole
    127,886-vertex mesh through UVs whose U axis spans 0.000..0.006 — a
    one-pixel vertical stripe of the texture smeared over every triangle. Its
    stride-24 sibling in the same mod won correctly by only 0.0206 vs 0.0131,
    i.e. a coin flip rather than a real margin.

    A candidate whose U or V axis is CONSTANT is preferred against last. A
    misread float32 pair reliably decodes as (~0, plausible-looking) because
    the low half of a float16 pair lands in the mantissa of the wider read, so
    it can post a competitive total spread on the V axis alone while U carries
    no information at all. No real UV set is one-dimensional.

    A few out-of-range values are TOLERATED rather than disqualifying the whole
    candidate. Real UV sets contain occasional outliers (one real 55,474-vertex
    stride-20 buffer has 6), and rejecting on the first one handed the decision
    to a degenerate float32 read that happened to have none -- dV exactly
    0.0000 across the entire mesh. Spread is measured over the in-range samples
    only, so outliers can't inflate it either.

    The returned offset ALWAYS fits within `stride`. Some buffers reaching here
    aren't texcoord buffers at all (an ini pointing a stride-4 Blend.buf or a
    Unity `.assets` file at vb1/vb2 -- ~50 in the local corpus), and no
    candidate is credible for those; returning the bare (4, '<ee') default
    would make read_texcoords straddle the vertex boundary and yield one fewer
    UV than there are positions. A fitting offset keeps that pathological case
    merely useless (flat zero UVs) instead of misaligned.
    """
    size = os.path.getsize(tc_path)
    total = size // stride if stride else 0
    if not total:
        return (DEFAULT_UV_OFFSET, '<ee')
    step = max(1, total // n)   # stride the sample across the entire buffer
    with open(tc_path, 'rb') as f:
        data = f.read()
    # Rank every in-range candidate; prefer two live axes over a bigger total.
    scored = []
    for uv_off in (0, 4):
        for fmt in ('<ee', '<ff'):
            fmtsize = struct.calcsize(fmt)
            if uv_off + fmtsize > stride:
                continue
            us, vs, sampled = [], [], 0
            for i in range(0, total, step):
                off = i * stride + uv_off
                if off + fmtsize > size:
                    break
                u, v = struct.unpack_from(fmt, data, off)
                sampled += 1
                # NaN fails every comparison, so test for validity positively:
                # a misread buffer routinely decodes to NaN/inf, which would
                # otherwise slip through a `u < lo or u > hi` style check.
                if -0.01 <= u <= 2.0 and -0.01 <= v <= 2.0:
                    us.append(u); vs.append(v)
            if not sampled or not us:
                continue
            in_range = len(us) / sampled
            if in_range < _MIN_IN_RANGE:
                continue
            du, dv = max(us) - min(us), max(vs) - min(vs)
            both_live = du >= _MIN_AXIS_SPREAD and dv >= _MIN_AXIS_SPREAD
            scored.append((both_live, round(in_range, 3), round(du + dv, 3),
                           uv_off, fmt))
    if scored:
        # Rank: both axes live, then cleanliness, then total spread. Cleanliness
        # must outrank spread -- a misread float32 can post a huge spread (up to
        # the full 0..2 window) while being 12% garbage, and swept across the
        # corpus spread-first regressed 27 buffers where clean-first regressed
        # none.
        scored.sort(reverse=True)
        return (scored[0][3], scored[0][4])
    # Nothing decoded in range: fall back to the first offset that at least fits.
    for uv_off in (DEFAULT_UV_OFFSET, 0):
        if uv_off + 4 <= stride:
            return (uv_off, '<ee')
    return (0, '<ee')


def read_texcoords(buf_path, stride, uv_off=DEFAULT_UV_OFFSET, uv_fmt='<ee'):
    """Read one UV pair per vertex. The bound accounts for uv_fmt's real width
    (float32 pairs are 8 bytes, not 4), so a buffer whose last vertex is
    truncated is dropped instead of raising out of struct.unpack_from."""
    uvs = []
    fmtsize = struct.calcsize(uv_fmt)
    with open(buf_path, "rb") as f: data = f.read()
    for off in range(0, len(data) - uv_off - fmtsize + 1, stride):
        uvs.append(struct.unpack_from(uv_fmt, data, off + uv_off))
    return uvs


def read_indices(ib_data, start_index=0, count=None, index_size=INDEX_SIZE):
    total = len(ib_data) // index_size
    if count is None: count = total - start_index
    end = min(start_index + count, total)
    if end <= start_index: return []
    fmt = "H" if index_size == 2 else "I"
    return list(struct.unpack_from(f"<{end - start_index}{fmt}", ib_data,
                                   start_index * index_size))


# ── Encoding helpers ───────────────────────────────────────────────────────────

def _b64f(arr):
    """Pack float list as little-endian Float32 and base64-encode."""
    return base64.b64encode(struct.pack(f"<{len(arr)}f", *arr)).decode()

def _b64u(arr):
    """Pack int list as little-endian Uint32 and base64-encode."""
    return base64.b64encode(struct.pack(f"<{len(arr)}I", *arr)).decode()

def _encode_texture(dds_path, max_size=2048):
    """DDS → PNG in-memory → base64 data URI.  Returns None on failure."""
    try:
        from PIL import Image
        img = Image.open(dds_path)
        # Convert to RGB before thumbnail: LANCZOS on RGBA with alpha=0 premultiplies
        # by zero, turning all pixels black. Dropping alpha first preserves colors.
        img = img.convert('RGB')
        if max(img.size) > max_size:
            img.thumbnail((max_size, max_size), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        print(f"  texture skipped: {e}")
        return None


def encode_texture_file(mod_dir, abs_path):
    """Encode an arbitrary texture file the user picked via a native file
    dialog (app/api.py's pick_texture_file) into the same {tex_key, uri}
    shape build_mesh_payload's own textures use, so it can be merged
    straight into the frontend's shared registry (mesh-factory.js's
    addTexture). `abs_path` must resolve inside `mod_dir` -- reuses
    _safe_join's sandboxing by re-deriving a mod-relative path and rejecting
    anything that doesn't stay within it, the same constraint `filename =`
    resolution is already held to.
    """
    try:
        rel = os.path.relpath(abs_path, mod_dir)
    except ValueError:
        return {"error": "Selected file is not inside the mod folder."}
    if _safe_join(mod_dir, rel) != os.path.abspath(abs_path):
        return {"error": "Selected file is not inside the mod folder."}
    if not os.path.isfile(abs_path):
        return {"error": "Selected file does not exist."}
    uri = _encode_texture(abs_path)
    if not uri:
        return {"error": "Could not read this file as an image."}
    return {"tex_key": rel.replace(os.sep, "/"), "uri": uri}


# ── In-memory mesh payload builder ────────────────────────────────────────────

def _rel_source(src, mod_dir):
    """Provenance for the UI: ini path relative to the mod folder.

    The absolute path stays on the Python side; the browser only needs enough
    to identify the line and to echo it back on export.
    """
    path = src.get("ini_path")
    if path:
        try:
            path = os.path.relpath(path, mod_dir)
        except ValueError:
            path = os.path.basename(path)
    return {"ini": path, "line": src.get("line_no"), "section": src.get("section")}


def build_mesh_payload(groups, mod_dir, max_draws=0):
    """
    Returns {draw_label: {pos, uv, idx, tex_key, drawindexed?, source?,
    component?, texture_variants?, texture_options?}, '__textures__': {key:
    uri}}. Textures are stored once in a shared registry keyed by filename.
    `drawindexed` is the ini's [count, start, base] triple (missing for the
    rare unconditional whole-buffer draw); `source` is the per-ini tag from
    build_draw_groups, present only in multi-ini mods. `component` is the
    group's clean, never-disambiguated display name (see build_draw_groups)
    — the UI groups and labels draw rows by this instead of parsing the
    draw_label key, so a rare cross-ini name collision (label gets a "_2"
    suffix for uniqueness) never leaks an ugly suffix into the display.

    `tex_key` is this draw's own resolved default: whichever
    Resource\\...\\Diffuse line most recently ran before it in the ini's
    execution order (build_draw_groups' `texture_default_file`), NOT one
    static value per component -- a section can reassign the diffuse several
    times (see ini_parser._scan_sections_for_draws), so two draws in the same
    TextureOverride section routinely resolve to different textures.
    `texture_variants`, present only when a toggle conditionally reassigns
    the diffuse at this exact point, is a list of {conditions, tex_key}
    alternatives (same DNF shape as `conditions`) for the UI to pick between
    as toggle state changes. `texture_options`, present only when the
    component's section references 2+ distinct diffuses anywhere (regardless
    of position/condition), is the full deduplicated pool as
    {tex_key, label} for a manual per-mesh override picker -- same list
    object shared by every draw in the component, so it also serves as the
    component-level "manage textures" pool.
    """
    result:    dict = {}
    tex_uris:  dict = {}  # basename → data URI  (encoded once)
    tex_cache: dict = {}  # full path → basename  (dedup lookup)
    ib_cache:  dict = {}  # absolute ib path → raw bytes
    buf_cache: dict = {}  # (pos_path, pos_stride, tc_path, tc_stride) → (positions, uvs)

    for grp in groups:
        pos_path  = _safe_join(mod_dir, grp["position_file"])
        tc_path   = _safe_join(mod_dir, grp["texcoord_file"])
        ib_path   = _safe_join(mod_dir, grp["ib_file"])
        tc_stride = grp["texcoord_stride"]
        pos_stride = grp.get("position_stride", POSITION_STRIDE)
        index_size = grp.get("index_size", INDEX_SIZE)
        component = grp.get("display_name") or grp.get("name")
        source    = grp.get("source")

        if not all(p and os.path.exists(p) for p in [pos_path, tc_path, ib_path]):
            continue

        def _load_buf(pos_path, pos_stride, tc_path, tc_stride):
            key = (pos_path, pos_stride, tc_path, tc_stride)
            if key not in buf_cache:
                positions = read_positions(pos_path, stride=pos_stride)
                uv_off, uv_fmt = _detect_uv_best(tc_path, tc_stride)
                uvs = read_texcoords(tc_path, tc_stride, uv_off, uv_fmt)
                buf_cache[key] = (positions, uvs)
            return buf_cache[key]

        positions, uvs = _load_buf(pos_path, pos_stride, tc_path, tc_stride)
        if ib_path not in ib_cache:
            ib_cache[ib_path] = open(ib_path, "rb").read()

        # Deduplicate draws by (ib file, position/texcoord override, start, count).
        # The SAME mesh region is
        # often reused across several mutually-exclusive `if $var == value`
        # branches of one cycle variable (e.g. a shared base body part present in
        # values 0-4 of a swap key, with only an accessory part differing per
        # value). Keeping only the first occurrence's condition would hide the
        # mesh for every other value that also draws it — instead, OR together
        # every occurrence's alternatives. Each draw's "conditions" is already
        # DNF (a list of OR'd AND-groups), so a single `if $x == 0 || $x == 2`
        # contributes two alternatives on its own. If any occurrence was
        # unconditional (drawn outside an if-block), the merged result is
        # unconditional too, since it's then always visible anyway.
        merged: dict = {}
        order: list = []
        for draw in grp["draws"]:
            key = (draw.get("ib_file"), draw.get("position_file"),
                   draw.get("texcoord_file"), draw["start"], draw["count"])
            if key not in merged:
                merged[key] = {"draw": draw, "alts": [], "sources": []}
                order.append(key)
            entry = merged[key]

            alts = entry["alts"]
            # Every contributing drawindexed line must survive the merge —
            # an authoring edit to this mesh has to fan back out to all of
            # them, possibly across several ini files.
            for src in draw.get("sources") or []:
                if src not in entry["sources"]:
                    entry["sources"].append(src)
            cond_groups = draw.get("conditions") or []
            if not cond_groups:
                if [] not in alts:
                    alts.append([])
            else:
                for group in cond_groups:
                    if group not in alts:
                        alts.append(group)
        unique = []
        for key in order:
            entry = merged[key]
            draw, alts = entry["draw"], entry["alts"]
            draw["conditions"] = [] if any(not a for a in alts) else alts
            draw["sources"] = entry["sources"]
            unique.append(draw)
        if max_draws:
            unique = unique[:max_draws]

        # Encode texture once; store URI in shared registry, key per draw
        def _tex_key(dds_path):
            if not dds_path or not os.path.exists(dds_path):
                return None
            if dds_path not in tex_cache:
                # Keyed by path, not basename: variant mods routinely keep
                # same-named diffuses in per-variant folders (Texture\00..\04).
                key = os.path.relpath(dds_path, mod_dir).replace(os.sep, "/")
                if key not in tex_uris:
                    tex_uris[key] = _encode_texture(dds_path) or ""
                tex_cache[dds_path] = key
            return tex_cache[dds_path]

        # Every diffuse this component's ini ever references, resolved once
        # and shared by every draw in the group -- the UI's per-mesh texture
        # picker list (core/ini_parser.py's build_draw_groups). Uses the same
        # _tex_key cache/registry as the draws' own textures so an option
        # that's also someone's active default doesn't get encoded twice.
        texture_options = []
        for pool_entry in grp.get("diffuse_pool_files") or []:
            key = _tex_key(_safe_join(mod_dir, pool_entry["file"]))
            if key:
                res_name = pool_entry["res"]
                label = res_name[8:] if res_name.startswith("Resource") else res_name
                texture_options.append({"tex_key": key, "label": label})

        for draw in unique:
            lbl = draw["label"]
            draw_ib_path = ib_path
            if draw.get("ib_file"):
                draw_ib_path = _safe_join(mod_dir, draw["ib_file"])
                if not draw_ib_path or not os.path.exists(draw_ib_path):
                    continue
                if draw_ib_path not in ib_cache:
                    ib_cache[draw_ib_path] = open(draw_ib_path, "rb").read()
            raw = read_indices(ib_cache[draw_ib_path], draw["start"], draw["count"],
                               draw.get("index_size", index_size))
            if not raw:
                continue
            # DirectX resolves each index as index_buffer_value + BaseVertexLocation
            # against the vertex buffer -- shared/merged buffers rely on this offset
            # to pick out this draw's own vertex range.
            base = draw.get("base") or 0
            if base:
                raw = [v + base for v in raw]

            # Compact: only export vertices actually referenced
            used  = sorted(set(raw))
            remap = {old: new for new, old in enumerate(used)}

            # A mid-section `vb0/vb1/vb2 = ...` reassignment (paired with `ib`).
            draw_positions, draw_uvs = positions, uvs
            if draw.get("position_file") and draw.get("texcoord_file"):
                draw_pos_path = _safe_join(mod_dir, draw["position_file"])
                draw_tc_path  = _safe_join(mod_dir, draw["texcoord_file"])
                if not (draw_pos_path and draw_tc_path
                        and os.path.exists(draw_pos_path) and os.path.exists(draw_tc_path)):
                    continue
                draw_positions, draw_uvs = _load_buf(
                    draw_pos_path, draw.get("position_stride", pos_stride),
                    draw_tc_path, draw.get("texcoord_stride", tc_stride))

            pos_arr, uv_arr = [], []
            for vi in used:
                x, y, z = draw_positions[vi] if vi < len(draw_positions) else (0., 0., 0.)
                pos_arr += [x, y, z]
                if draw_uvs:
                    u, v = draw_uvs[vi] if vi < len(draw_uvs) else (0., 0.)
                    uv_arr += [u, 1.0 - v]   # flip V for Three.js


            idx_arr = [remap[v] for v in raw]

            default_key = _tex_key(_safe_join(mod_dir, draw.get("texture_default_file")))
            entry: dict = {"pos": _b64f(pos_arr), "idx": _b64u(idx_arr), "tex_key": default_key}
            if uv_arr:
                entry["uv"] = _b64f(uv_arr)
            if draw.get("conditions"):
                entry["conditions"] = draw["conditions"]
            if draw.get("sources"):
                entry["sources"] = [_rel_source(s, mod_dir) for s in draw["sources"]]
            # A toggle can swap the diffuse texture.
            if draw.get("texture_variants"):
                variants = []
                for v in draw["texture_variants"]:
                    key = _tex_key(_safe_join(mod_dir, v["file"]))
                    if key:
                        variants.append({"conditions": v["conditions"], "tex_key": key})
                if len(variants) > 1:
                    entry["texture_variants"] = variants
            if len(texture_options) > 1:
                entry["texture_options"] = texture_options
            # The literal `drawindexed = count, start, base` values, so the UI can
            # show a meaningful per-draw label instead of a bare "#1"/"#2" index.
            # Absent for the rare section with no drawindexed line at all (the
            # whole index buffer is read unconditionally) — draw["count"] is None.
            if draw.get("count") is not None:
                entry["drawindexed"] = [draw["count"], draw["start"], draw["base"]]
            if source:
                entry["source"] = source
            if component:
                entry["component"] = component
            result[lbl] = entry

    result["__textures__"] = {k: v for k, v in tex_uris.items() if v}
    return result

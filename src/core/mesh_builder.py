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


def _safe_join(mod_dir, rel):
    """Resolve a Resource section's `filename = ...` under mod_dir, refusing
    to leave it via an absolute path or a `..` escape.

    That filename comes straight from the mod's own ini text (see
    ini_parser.extract_resources), so a crafted mod could otherwise point it
    anywhere on disk -- e.g. `filename = ..\\..\\..\\Users\\me\\Pictures\\x.png`
    or a bare absolute path -- and have its contents read and, for a texture,
    re-encoded into the payload sent to the UI. Returns None if `rel` escapes.
    """
    if not rel: return None
    root = os.path.abspath(mod_dir)
    target = os.path.abspath(os.path.join(root, rel))
    if target != root and not target.startswith(root + os.sep):
        return None
    return target


# ── Binary readers ─────────────────────────────────────────────────────────────

def read_positions(buf_path, stride=POSITION_STRIDE):
    positions = []
    with open(buf_path, "rb") as f: data = f.read()
    for off in range(0, len(data) - 11, stride):
        positions.append(struct.unpack_from("<fff", data, off + POSITION_OFFSET))
    return positions


def _detect_uv_best(tc_path, stride, n=30):
    """Try (offset 0 or 4) x (float16 or float32) and return the (uv_off, fmt)
    with the largest UV spread where all values are within [0, 2]."""
    with open(tc_path, 'rb') as f:
        data = f.read(stride * n)
    best_score, best = -1.0, (DEFAULT_UV_OFFSET, '<ee')
    for uv_off in (0, 4):
        for fmt in ('<ee', '<ff'):
            fmtsize = 4 if fmt == '<ee' else 8
            if uv_off + fmtsize > stride:
                continue
            us, vs = [], []
            for off in range(0, len(data) - uv_off - fmtsize + 1, stride):
                u, v = struct.unpack_from(fmt, data, off + uv_off)
                if u < -0.01 or u > 2.0 or v < -0.01 or v > 2.0:
                    break
                us.append(u); vs.append(v)
            else:  # all values valid
                if us:
                    spread = (max(us) - min(us)) + (max(vs) - min(vs))
                    if spread > best_score:
                        best_score, best = spread, (uv_off, fmt)
    return best


def read_texcoords(buf_path, stride, uv_off=DEFAULT_UV_OFFSET, uv_fmt='<ee'):
    uvs = []
    with open(buf_path, "rb") as f: data = f.read()
    for off in range(0, len(data) - uv_off - 3, stride):
        uvs.append(struct.unpack_from(uv_fmt, data, off + uv_off))
    return uvs


def read_indices(ib_data, start_index=0, count=None):
    total = len(ib_data) // INDEX_SIZE
    if count is None: count = total - start_index
    end = min(start_index + count, total)
    if end <= start_index: return []
    return list(struct.unpack_from(f"<{end - start_index}I", ib_data, start_index * INDEX_SIZE))


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
    component?}, '__textures__': {key: uri}}. Textures are stored once in a
    shared registry keyed by filename. `drawindexed` is the ini's
    [count, start, base] triple (missing for the rare unconditional
    whole-buffer draw); `source` is the per-ini tag from build_draw_groups,
    present only in multi-ini mods. `component` is the group's clean,
    never-disambiguated display name (see build_draw_groups) — the UI groups
    and labels draw rows by this instead of parsing the draw_label key, so a
    rare cross-ini name collision (label gets a "_2" suffix for uniqueness)
    never leaks an ugly suffix into the display.
    """
    result:    dict = {}
    tex_uris:  dict = {}  # basename → data URI  (encoded once)
    tex_cache: dict = {}  # full path → basename  (dedup lookup)

    for grp in groups:
        pos_path  = _safe_join(mod_dir, grp["position_file"])
        tc_path   = _safe_join(mod_dir, grp["texcoord_file"])
        ib_path   = _safe_join(mod_dir, grp["ib_file"])
        tc_stride = grp["texcoord_stride"]
        diff_dds  = _safe_join(mod_dir, grp["diffuse_file"]) if grp["diffuse_file"] else None
        component = grp.get("display_name") or grp.get("name")
        source    = grp.get("source")

        if not all(p and os.path.exists(p) for p in [pos_path, tc_path, ib_path]):
            continue

        positions = read_positions(pos_path, stride=grp.get("position_stride", POSITION_STRIDE))
        uv_off, uv_fmt = _detect_uv_best(tc_path, tc_stride)
        uvs       = read_texcoords(tc_path, tc_stride, uv_off, uv_fmt)
        ib_data   = open(ib_path, "rb").read()

        # Deduplicate draws by (start, count). The SAME mesh region is often
        # reused across several mutually-exclusive `if $var == value` branches
        # of one cycle variable (e.g. a shared base body part present in
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
            key = (draw["start"], draw["count"])
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
        tex_key = None
        if diff_dds and os.path.exists(diff_dds):
            if diff_dds not in tex_cache:
                key = os.path.basename(diff_dds)
                if key not in tex_uris:
                    tex_uris[key] = _encode_texture(diff_dds) or ""
                tex_cache[diff_dds] = key
            tex_key = tex_cache[diff_dds]

        for draw in unique:
            lbl = draw["label"]
            raw = read_indices(ib_data, draw["start"], draw["count"])
            if not raw:
                continue

            # Compact: only export vertices actually referenced
            used  = sorted(set(raw))
            remap = {old: new for new, old in enumerate(used)}

            pos_arr, uv_arr = [], []
            for vi in used:
                x, y, z = positions[vi] if vi < len(positions) else (0., 0., 0.)
                pos_arr += [x, y, z]
                if uvs:
                    u, v = uvs[vi] if vi < len(uvs) else (0., 0.)
                    uv_arr += [u, 1.0 - v]   # flip V for Three.js

            idx_arr = [remap[v] for v in raw]

            entry: dict = {"pos": _b64f(pos_arr), "idx": _b64u(idx_arr), "tex_key": tex_key}
            if uv_arr:
                entry["uv"] = _b64f(uv_arr)
            if draw.get("conditions"):
                entry["conditions"] = draw["conditions"]
            if draw.get("sources"):
                entry["sources"] = [_rel_source(s, mod_dir) for s in draw["sources"]]
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

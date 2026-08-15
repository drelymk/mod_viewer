"""3DMigoto binary buffer readers and in-memory mesh payload builder."""

import re, struct, os, base64, io, threading
import warnings
from collections import OrderedDict

# ── Buffer constants ───────────────────────────────────────────────────────────
POSITION_STRIDE   = 40
POSITION_OFFSET   = 0
DEFAULT_UV_OFFSET = 4
INDEX_SIZE        = 4

# Encoded PNG data URIs are expensive to regenerate during authoring reloads.
# Keep only the current mod's entries, bounded by encoded string size. A noisy
# 2048x2048 RGB image can occupy ~16 MiB after base64, so 256 MiB retains about
# sixteen worst-case textures (and many more typical game textures).
_TEXTURE_CACHE_LIMIT = 256 * 1024 * 1024
_MAX_BUFFER_FILE_BYTES = 512 * 1024 * 1024
_MAX_TOTAL_BUFFER_BYTES = 2 * 1024 * 1024 * 1024
_MAX_DRAWS = 10_000
_MAX_IMAGE_PIXELS = 100_000_000
_texture_cache = OrderedDict()
_texture_cache_bytes = 0
_texture_cache_mod = None
_texture_cache_lock = threading.RLock()


def _begin_texture_cache(mod_dir):
    global _texture_cache_mod, _texture_cache_bytes
    root = os.path.normcase(os.path.abspath(mod_dir))
    with _texture_cache_lock:
        if root != _texture_cache_mod:
            _texture_cache.clear()
            _texture_cache_bytes = 0
            _texture_cache_mod = root


def _cache_texture(key, uri):
    global _texture_cache_bytes
    size = len(uri.encode("ascii"))
    if size > _TEXTURE_CACHE_LIMIT:
        return
    with _texture_cache_lock:
        previous = _texture_cache.pop(key, None)
        if previous is not None:
            _texture_cache_bytes -= previous[1]
        _texture_cache[key] = (uri, size)
        _texture_cache_bytes += size
        while _texture_cache_bytes > _TEXTURE_CACHE_LIMIT:
            _old_key, (_old_uri, old_size) = _texture_cache.popitem(last=False)
            _texture_cache_bytes -= old_size


def _res_get(resources, name):
    """Case-insensitive resource lookup (handles WWMI naming inconsistencies)."""
    if not name: return {}
    if name in resources: return resources[name]
    nl = name.lower()
    for k, v in resources.items():
        if k.lower() == nl: return v
    return {}


_MAX_ESCAPE_DEPTH = 1   # levels above mod_dir a `filename = ..\...` may reach


def safe_resource_path(mod_dir, rel):
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


# Backward-compatible private name for existing callers.  INI diagnostics use
# the public spelling so resource loading and health checks cannot drift into
# different path-safety rules.
_safe_join = safe_resource_path


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


def _detect_uv_best(tc_path, stride, n=4096, data=None):
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
    if data is None:
        with open(tc_path, 'rb') as f:
            data = f.read()
    size = len(data)
    total = size // stride if stride else 0
    if not total:
        return (DEFAULT_UV_OFFSET, '<ee')
    step = max(1, total // n)   # stride the sample across the entire buffer
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


def read_texcoords(buf_path, stride, uv_off=DEFAULT_UV_OFFSET, uv_fmt='<ee',
                   data=None):
    """Read one UV pair per vertex. The bound accounts for uv_fmt's real width
    (float32 pairs are 8 bytes, not 4), so a buffer whose last vertex is
    truncated is dropped instead of raising out of struct.unpack_from."""
    uvs = []
    fmtsize = struct.calcsize(uv_fmt)
    if data is None:
        with open(buf_path, "rb") as f:
            data = f.read()
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

def _encode_texture(dds_path, max_size=2048, preserve_alpha=False):
    """DDS → PNG in-memory → base64 data URI.  Returns None on failure."""
    try:
        stat = os.stat(dds_path)
        cache_key = (os.path.normcase(os.path.abspath(dds_path)), stat.st_size,
                     stat.st_mtime_ns, max_size, preserve_alpha)
        with _texture_cache_lock:
            cached = _texture_cache.pop(cache_key, None)
            if cached is not None:
                _texture_cache[cache_key] = cached
                return cached[0]
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = _MAX_IMAGE_PIXELS
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            img = Image.open(dds_path)
            img.load()
        # Material textures historically drop alpha because some DDS decoders
        # expose unusable colour in fully-transparent pixels. Menu artwork is
        # different: its authored transparency is part of the icon and must
        # survive conversion to PNG.
        img = img.convert('RGBA' if preserve_alpha else 'RGB')
        if preserve_alpha and img.getchannel('A').getextrema()[1] == 0:
            # Some menu packs deliberately point several slots at an empty
            # placeholder DDS. Sending it to the browser produces a blank or
            # black-looking tile; let the menu UI use its cycle glyph instead.
            return None
        if max(img.size) > max_size:
            img.thumbnail((max_size, max_size), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        _cache_texture(cache_key, uri)
        return uri
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
    _begin_texture_cache(mod_dir)
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
            path = os.path.relpath(path, mod_dir).replace(os.sep, "/")
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
    as toggle state changes. `texture_options`, present when the component's
    section references one or more distinct, resolved diffuses anywhere
    (regardless of position/condition), is the full deduplicated pool as
    {tex_key, label} for a manual per-mesh override picker -- same list
    object shared by every draw in the component, so it also serves as the
    component-level "manage textures" pool.
    """
    _begin_texture_cache(mod_dir)
    result:    dict = {}
    tex_uris:  dict = {}  # basename → data URI  (encoded once)
    tex_cache: dict = {}  # full path → basename  (dedup lookup)
    ib_cache:  dict = {}  # absolute ib path → raw bytes
    buf_cache: dict = {}  # (pos_path, pos_stride, tc_path, tc_stride) → (positions, uvs)

    raw_buf_cache = {}
    sparse_shape_cache = {}
    total_buffer_bytes = 0

    def _read_buffer(path):
        nonlocal total_buffer_bytes
        size = os.path.getsize(path)
        if size > _MAX_BUFFER_FILE_BYTES:
            raise ValueError(f"Buffer file is too large ({size / 1048576:.1f} MiB).")
        if total_buffer_bytes + size > _MAX_TOTAL_BUFFER_BYTES:
            raise ValueError("Mod buffer data exceeds the 2 GiB safety limit.")
        with open(path, "rb") as fh:
            data = fh.read()
        total_buffer_bytes += len(data)
        return data

    draw_total = sum(len(group.get("draws", [])) for group in groups)
    if draw_total > _MAX_DRAWS:
        raise ValueError(f"Mod has too many draws ({draw_total:,}; limit {_MAX_DRAWS:,}).")

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
                if pos_path not in raw_buf_cache:
                    raw_buf_cache[pos_path] = _read_buffer(pos_path)
                if tc_path not in raw_buf_cache:
                    raw_buf_cache[tc_path] = _read_buffer(tc_path)
                pos_data = raw_buf_cache[pos_path]
                tc_data = raw_buf_cache[tc_path]
                uv_off, uv_fmt = _detect_uv_best(
                    tc_path, tc_stride, data=tc_data)
                buf_cache[key] = (pos_data, pos_stride, tc_data, tc_stride,
                                  uv_off, uv_fmt)
            return buf_cache[key]

        buffers = _load_buf(pos_path, pos_stride, tc_path, tc_stride)
        if ib_path not in ib_cache:
            ib_cache[ib_path] = _read_buffer(ib_path)

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

        # Resolve texture keys cheaply. Pool-only options are encoded lazily
        # when selected in the UI; defaults and toggle variants are still
        # encoded now so synchronous visibility refreshes remain instant.
        def _tex_key(dds_path):
            if not dds_path or not os.path.exists(dds_path):
                return None
            if dds_path not in tex_cache:
                # Keyed by path, not basename: variant mods routinely keep
                # same-named diffuses in per-variant folders (Texture\00..\04).
                key = os.path.relpath(dds_path, mod_dir).replace(os.sep, "/")
                tex_cache[dds_path] = key
            return tex_cache[dds_path]

        def _ensure_texture(dds_path):
            key = _tex_key(dds_path)
            if key and key not in tex_uris:
                tex_uris[key] = _encode_texture(dds_path) or ""
            return key

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
                    ib_cache[draw_ib_path] = _read_buffer(draw_ib_path)
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
            draw_buffers = buffers
            effective_pos_path = pos_path
            if draw.get("position_file") and draw.get("texcoord_file"):
                draw_pos_path = _safe_join(mod_dir, draw["position_file"])
                draw_tc_path  = _safe_join(mod_dir, draw["texcoord_file"])
                if not (draw_pos_path and draw_tc_path
                        and os.path.exists(draw_pos_path) and os.path.exists(draw_tc_path)):
                    continue
                draw_buffers = _load_buf(
                    draw_pos_path, draw.get("position_stride", pos_stride),
                    draw_tc_path, draw.get("texcoord_stride", tc_stride))
                effective_pos_path = draw_pos_path

            pos_data, draw_pos_stride, tc_data, draw_tc_stride, uv_off, uv_fmt = draw_buffers
            uv_size = struct.calcsize(uv_fmt)
            pos_bytes = bytearray(len(used) * 12)
            shape_buffers = []
            for shape in grp.get("shape_sliders") or []:
                shape_base_path = _safe_join(mod_dir, shape["base_file"])
                if os.path.normcase(os.path.normpath(shape_base_path or "")) != \
                        os.path.normcase(os.path.normpath(effective_pos_path)):
                    continue
                if shape.get("shape_id") is not None:
                    paths = tuple(_safe_join(mod_dir, shape[k]) for k in
                                  ("offset_file", "vertex_id_file", "vertex_offset_file"))
                    if not all(path and os.path.exists(path) for path in paths):
                        continue
                    # WWMI aligns each 127-key batch to a 128-entry container;
                    # user-facing IDs omit that padding slot (SkapeKeySetter.hlsl).
                    key_id = shape.get(
                        "buffer_shape_id",
                        shape["shape_id"] + shape["shape_id"] // 127)
                    cache_key = paths + (key_id,)
                    if cache_key not in sparse_shape_cache:
                        for path in paths:
                            if path not in raw_buf_cache:
                                raw_buf_cache[path] = _read_buffer(path)
                        offsets, vertex_ids, deltas = (raw_buf_cache[path] for path in paths)
                        if (key_id + 2) * 4 > len(offsets):
                            continue
                        begin, end = struct.unpack_from("<II", offsets, key_id * 4)
                        entry_offset = shape.get("sparse_entry_offset", 0)
                        begin += entry_offset
                        end += entry_offset
                        limit = min(end, len(vertex_ids) // 4, len(deltas) // 12)
                        sparse = {}
                        for i in range(begin, limit):
                            vertex_id = struct.unpack_from("<I", vertex_ids, i * 4)[0]
                            delta = struct.unpack_from("<eee", deltas, i * 12)
                            prior = sparse.get(vertex_id, (0., 0., 0.))
                            sparse[vertex_id] = tuple(prior[j] + delta[j] for j in range(3))
                        sparse_shape_cache[cache_key] = sparse
                    shape_buffers.append((shape, sparse_shape_cache[cache_key],
                                          bytearray(len(used) * 12), True))
                else:
                    target_path = _safe_join(mod_dir, shape["target_file"])
                    if not target_path or not os.path.exists(target_path):
                        continue
                    if target_path not in raw_buf_cache:
                        raw_buf_cache[target_path] = _read_buffer(target_path)
                    low_data = None
                    low_bytes = None
                    if shape.get("low_file"):
                        low_path = _safe_join(mod_dir, shape["low_file"])
                        if not low_path or not os.path.exists(low_path):
                            continue
                        if low_path not in raw_buf_cache:
                            raw_buf_cache[low_path] = _read_buffer(low_path)
                        low_data = raw_buf_cache[low_path]
                        low_bytes = bytearray(len(used) * 12)
                    shape_buffers.append((shape, raw_buf_cache[target_path],
                                          bytearray(len(used) * 12), False,
                                          low_data, low_bytes))
            uv_bytes = bytearray(len(used) * 8) if tc_data else None
            for out_i, vi in enumerate(used):
                pos_off = vi * draw_pos_stride + POSITION_OFFSET
                if pos_off + 12 <= len(pos_data):
                    x, y, z = struct.unpack_from("<fff", pos_data, pos_off)
                else:
                    x, y, z = 0., 0., 0.
                struct.pack_into("<fff", pos_bytes, out_i * 12, x, y, z)
                for item in shape_buffers:
                    shape, target_data, target_bytes, sparse = item[:4]
                    if sparse:
                        dx, dy, dz = target_data.get(vi, (0., 0., 0.))
                        tx, ty, tz = x + dx, y + dy, z + dz
                    else:
                        target_off = vi * shape["stride"] + POSITION_OFFSET
                        if target_off + 12 <= len(target_data):
                            tx, ty, tz = struct.unpack_from("<fff", target_data, target_off)
                        else:
                            tx, ty, tz = x, y, z
                    struct.pack_into("<fff", target_bytes, out_i * 12, tx, ty, tz)
                    if len(item) > 4 and item[4] is not None:
                        low_data, low_bytes = item[4], item[5]
                        low_off = vi * shape["stride"] + POSITION_OFFSET
                        if low_off + 12 <= len(low_data):
                            lx, ly, lz = struct.unpack_from("<fff", low_data, low_off)
                        else:
                            lx, ly, lz = x, y, z
                        struct.pack_into("<fff", low_bytes, out_i * 12, lx, ly, lz)
                if tc_data:
                    tc_off = vi * draw_tc_stride + uv_off
                    if tc_off + uv_size <= len(tc_data):
                        u, v = struct.unpack_from(uv_fmt, tc_data, tc_off)
                    else:
                        u, v = 0., 0.
                    struct.pack_into("<ff", uv_bytes, out_i * 8,
                                     u, 1.0 - v)  # flip V for Three.js

            idx_bytes = bytearray(len(raw) * 4)
            for out_i, value in enumerate(raw):
                struct.pack_into("<I", idx_bytes, out_i * 4, remap[value])

            default_key = _ensure_texture(
                _safe_join(mod_dir, draw.get("texture_default_file")))
            entry: dict = {
                "pos": base64.b64encode(pos_bytes).decode(),
                "idx": base64.b64encode(idx_bytes).decode(),
                "tex_key": default_key,
            }
            if uv_bytes:
                entry["uv"] = base64.b64encode(uv_bytes).decode()
            if shape_buffers:
                entry["shape_targets"] = []
                for item in shape_buffers:
                    shape, _target_data, target_bytes, _sparse = item[:4]
                    target = {"var": shape["var"],
                              "pos": base64.b64encode(target_bytes).decode()}
                    if shape.get("mode"):
                        target["mode"] = shape["mode"]
                    if len(item) > 5 and item[5] is not None:
                        target["low_pos"] = base64.b64encode(item[5]).decode()
                    entry["shape_targets"].append(target)
            if draw.get("conditions"):
                entry["conditions"] = draw["conditions"]
            if draw.get("sources"):
                entry["sources"] = [_rel_source(s, mod_dir) for s in draw["sources"]]
            # A toggle can swap the diffuse texture.
            texture_rules = (draw.get("texture_assignments")
                             or draw.get("texture_variants"))
            if texture_rules:
                variants = []
                for v in texture_rules:
                    key = _ensure_texture(_safe_join(mod_dir, v["file"]))
                    if key:
                        variants.append({"conditions": v["conditions"], "tex_key": key})
                if len(variants) > 1:
                    entry["texture_variants"] = variants
            # Keep even a single resolved texture in the UI pool. A component
            # with one diffuse still has a useful texture to display/manage;
            # only components with no resolved textures need the frontend's
            # empty fallback pool.
            if texture_options:
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

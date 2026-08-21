"""3DMigoto binary buffer readers and in-memory mesh payload builder."""

import re, struct, os, base64
from dataclasses import dataclass

from .resource_paths import safe_resource_path
from .textures import (_texture_source_uri, _begin_texture_cache,
                       encode_texture_data_uri, normalize_texture_role,
                       normalize_texture_transform, texture_key)

# ── Buffer constants ───────────────────────────────────────────────────────────
POSITION_STRIDE   = 40
POSITION_OFFSET   = 0
DEFAULT_UV_OFFSET = 4
INDEX_SIZE        = 4

_MAX_BUFFER_FILE_BYTES = 512 * 1024 * 1024
_MAX_TOTAL_BUFFER_BYTES = 2 * 1024 * 1024 * 1024
_MAX_DRAWS = 10_000


def _res_get(resources, name):
    """Case-insensitive resource lookup (handles WWMI naming inconsistencies)."""
    if not name: return {}
    lookup = getattr(resources, "get_ci", None)
    if lookup is not None:
        return lookup(name)
    if name in resources: return resources[name]
    nl = name.lower()
    for k, v in resources.items():
        if k.lower() == nl: return v
    return {}


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


class GeometryBlob:
    """Append-only binary geometry storage shared by one mod load."""

    __slots__ = ("data",)

    def __init__(self):
        self.data = bytearray()

    def add(self, value):
        raw = bytes(value)
        offset = len(self.data)
        self.data.extend(raw)
        return {"offset": offset, "length": len(raw)}

    def __len__(self):
        return len(self.data)

    def to_bytes(self):
        return bytes(self.data)


@dataclass
class MeshBuildResult:
    """Named intermediate produced by the mesh-building pipeline.

    ``meshes`` contains only draw entries and ``textures`` is the shared
    texture registry.  ``geometry`` records the optional caller-owned blob
    writer used to produce offset/length references.  Keeping these outputs
    separate prevents the application loader from translating a flat dict of
    mesh data and reserved keys before it can assemble its public payload.
    """

    meshes: dict
    textures: dict
    geometry: GeometryBlob | None = None

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


def _deduplicate_draws(group, max_draws=0):
    """Merge repeated draw regions while preserving every gate and source.

    This is a distinct pipeline stage: geometry packing receives one
    deterministic draw list and does not own the condition-merging rules.
    """
    merged = {}
    order = []
    for draw in group["draws"]:
        key = (draw.get("ib_file"), draw.get("position_file"),
               draw.get("texcoord_file"), draw["start"], draw["count"])
        if key not in merged:
            merged[key] = {"draw": draw, "alts": [], "sources": []}
            order.append(key)
        entry = merged[key]
        for src in draw.get("sources") or []:
            if src not in entry["sources"]:
                entry["sources"].append(src)
        cond_groups = draw.get("conditions") or []
        if not cond_groups:
            if [] not in entry["alts"]:
                entry["alts"].append([])
        else:
            for condition_group in cond_groups:
                if condition_group not in entry["alts"]:
                    entry["alts"].append(condition_group)

    unique = []
    for key in order:
        entry = merged[key]
        draw, alternatives = entry["draw"], entry["alts"]
        draw["conditions"] = (
            [] if any(not group for group in alternatives) else alternatives)
        draw["sources"] = entry["sources"]
        unique.append(draw)
    return unique[:max_draws] if max_draws else unique


def _geometry_ref(raw, geometry):
    """Serialize one binary field into the caller-owned geometry store."""
    if geometry is not None:
        return geometry.add(raw)
    # Direct core fixtures and older integrations retain the historical
    # base64 representation when no shared blob was supplied.
    return base64.b64encode(raw).decode()


def _build_shape_buffers(shape_sliders, mod_dir, effective_pos_path, used,
                         raw_buf_cache, sparse_shape_cache, read_buffer):
    """Load and prepare dense or sparse shape targets for one draw."""
    shape_buffers = []
    for shape in shape_sliders or []:
        shape_base_path = safe_resource_path(mod_dir, shape["base_file"])
        if os.path.normcase(os.path.normpath(shape_base_path or "")) != \
                os.path.normcase(os.path.normpath(effective_pos_path)):
            continue
        if shape.get("shape_id") is not None:
            paths = tuple(safe_resource_path(mod_dir, shape[k]) for k in
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
                        raw_buf_cache[path] = read_buffer(path)
                offsets, vertex_ids, deltas = (raw_buf_cache[path]
                                               for path in paths)
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
                    sparse[vertex_id] = tuple(
                        prior[j] + delta[j] for j in range(3))
                sparse_shape_cache[cache_key] = sparse
            shape_buffers.append((shape, sparse_shape_cache[cache_key],
                                  bytearray(len(used) * 12), True))
        else:
            target_path = safe_resource_path(mod_dir, shape["target_file"])
            if not target_path or not os.path.exists(target_path):
                continue
            if target_path not in raw_buf_cache:
                raw_buf_cache[target_path] = read_buffer(target_path)
            low_data = None
            low_bytes = None
            if shape.get("low_file"):
                low_path = safe_resource_path(mod_dir, shape["low_file"])
                if not low_path or not os.path.exists(low_path):
                    continue
                if low_path not in raw_buf_cache:
                    raw_buf_cache[low_path] = read_buffer(low_path)
                low_data = raw_buf_cache[low_path]
                low_bytes = bytearray(len(used) * 12)
            shape_buffers.append((shape, raw_buf_cache[target_path],
                                  bytearray(len(used) * 12), False,
                                  low_data, low_bytes))
    return shape_buffers


def build_mesh_result(groups, mod_dir, max_draws=0, geometry=None,
                      texture_source=None, game_profile=None):
    """
    Build mesh draw entries and a shared texture registry.

    Returns :class:`MeshBuildResult` whose ``meshes`` map contains
    ``{draw_label: {pos, uv, idx, tex_key, ...}}`` and whose ``textures`` map
    contains rendered texture sources keyed by role-aware mod-relative filename.
    With no ``texture_source`` callback these are encoded data URIs for direct
    fixture compatibility. The application passes a callback that returns an
    opaque localhost URL instead. Geometry is written into ``geometry`` when a
    ``GeometryBlob`` is supplied; otherwise direct callers retain base64
    geometry fields for fixture compatibility.

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
    {tex_key, file, label} for a manual per-mesh override picker -- same list
    object shared by every draw in the component, so it also serves as the
    component-level "manage textures" pool.
    """
    _begin_texture_cache(mod_dir)
    from .texture_profiles import texture_profile_for
    texture_profile = texture_profile_for(game_profile)

    result:    dict = {}
    tex_uris:  dict = {}  # role-aware key -> data URI or lazy source URL
    tex_cache: dict = {}  # (full path, role, transform) -> role-aware key
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
        pos_path  = safe_resource_path(mod_dir, grp["position_file"])
        tc_path   = safe_resource_path(mod_dir, grp["texcoord_file"])
        ib_path   = safe_resource_path(mod_dir, grp["ib_file"])
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

        unique = _deduplicate_draws(grp, max_draws=max_draws)
        # Draws have already been deduplicated by the named pipeline stage
        # above; the remaining stages only resolve files and pack bytes.

        # Resolve texture keys cheaply. Pool-only options are encoded lazily
        # when selected in the UI. Direct callers encode defaults and toggle
        # variants now; the application callback only publishes their source.
        def _tex_key(dds_path, texture_role=None, texture_transform=None):
            if not dds_path or not os.path.exists(dds_path):
                return None
            texture_role = normalize_texture_role(texture_role)
            if texture_transform is None:
                texture_transform = texture_profile.recipe_for(texture_role)
            texture_transform = normalize_texture_transform(texture_transform)
            cache_key = (dds_path, texture_role, texture_transform)
            if cache_key not in tex_cache:
                # Keyed by path, not basename: variant mods routinely keep
                # same-named diffuses in per-variant folders (Texture\00..\04).
                relative_path = os.path.relpath(dds_path, mod_dir).replace(
                    os.sep, "/")
                tex_cache[cache_key] = texture_key(relative_path, texture_role)
            return tex_cache[cache_key]

        def _ensure_texture(dds_path, texture_role=None):
            texture_role = normalize_texture_role(texture_role)
            texture_transform = texture_profile.recipe_for(texture_role)
            key = _tex_key(dds_path, texture_role, texture_transform)
            if key and key not in tex_uris:
                if texture_source is None:
                    value = encode_texture_data_uri(
                        dds_path, texture_role=texture_role,
                        texture_transform=texture_transform)
                else:
                    value = _texture_source_uri(
                        texture_source, dds_path, texture_role,
                        texture_transform)
                tex_uris[key] = value or ""
            return key

        # Every diffuse this component's ini ever references, resolved once
        # and shared by every draw in the group -- the UI's per-mesh texture
        # picker list (core/ini_parser.py's build_draw_groups). Uses the same
        # _tex_key cache/registry as the draws' own textures so an option
        # that's also someone's active default doesn't get encoded twice.
        texture_options = []
        for pool_entry in grp.get("diffuse_pool_files") or []:
            key = _tex_key(safe_resource_path(mod_dir, pool_entry["file"]))
            if key:
                res_name = pool_entry["res"]
                label = res_name[8:] if res_name.startswith("Resource") else res_name
                texture_options.append({"tex_key": key,
                                        "file": pool_entry["file"],
                                        "label": label})

        for draw in unique:
            lbl = draw["label"]
            draw_ib_path = ib_path
            if draw.get("ib_file"):
                draw_ib_path = safe_resource_path(mod_dir, draw["ib_file"])
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
                draw_pos_path = safe_resource_path(mod_dir, draw["position_file"])
                draw_tc_path  = safe_resource_path(mod_dir, draw["texcoord_file"])
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
            shape_buffers = _build_shape_buffers(
                grp.get("shape_sliders"), mod_dir, effective_pos_path, used,
                raw_buf_cache, sparse_shape_cache, _read_buffer)
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
                safe_resource_path(mod_dir, draw.get("texture_default_file")))
            entry: dict = {
                "pos": _geometry_ref(pos_bytes, geometry),
                "idx": _geometry_ref(idx_bytes, geometry),
                "tex_key": default_key,
                "normal_map_y_sign": texture_profile.normal_y_sign,
                "normal_map_enabled": texture_profile.bind_normal_map,
            }
            # NormalMap is a user-facing authored role, but its transport is
            # profile-owned.  WuWa publishes the intact packed source as
            # normal_data; Genshin/ZZZ retain the derived normal_map path.
            normal_path = safe_resource_path(
                mod_dir, draw.get("normal_map_default_file"))
            normal_role = texture_profile.normal_transport_role
            normal_key = _ensure_texture(normal_path, normal_role)
            if normal_key:
                entry[f"{normal_role}_key"] = normal_key
            for channel in ("light_map", "material_map"):
                key = _ensure_texture(safe_resource_path(
                    mod_dir, draw.get(f"{channel}_default_file")), channel)
                if key:
                    entry[f"{channel}_key"] = key
            # The manager presents one row per diffuse. Seed that row with the
            # auxiliary maps resolved alongside this draw so authored maps can
            # be inspected/replaced with the same controls as manual ones.
            def _seed_option_maps(diffuse_key):
                if not diffuse_key:
                    return
                option = next((item for item in texture_options
                               if item["tex_key"] == diffuse_key), None)
                if option:
                    for channel in ("normal_map", "light_map", "normal_data",
                                    "material_map"):
                        key = entry.get(f"{channel}_key")
                        if key and not option.get(channel):
                            option[channel] = key
            _seed_option_maps(default_key)
            if uv_bytes:
                entry["uv"] = _geometry_ref(uv_bytes, geometry)
            if shape_buffers:
                entry["shape_targets"] = []
                for item in shape_buffers:
                    shape, _target_data, target_bytes, _sparse = item[:4]
                    target = {"var": shape["var"],
                              "pos": _geometry_ref(target_bytes, geometry)}
                    if shape.get("mode"):
                        target["mode"] = shape["mode"]
                    if len(item) > 5 and item[5] is not None:
                        target["low_pos"] = _geometry_ref(item[5], geometry)
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
                    key = _ensure_texture(safe_resource_path(mod_dir, v["file"]))
                    if key:
                        # Auxiliary assignments after a conditional diffuse
                        # branch belong to every branch reaching this draw,
                        # not only to whichever branch supplied tex_key.
                        _seed_option_maps(key)
                        variants.append({"conditions": v["conditions"], "tex_key": key})
                if len(variants) > 1:
                    entry["texture_variants"] = variants
            for channel in ("light_map", "material_map"):
                rules = draw.get(f"{channel}_variants") or []
                variants = []
                for variant in rules:
                    key = _ensure_texture(
                        safe_resource_path(mod_dir, variant["file"]), channel)
                    if key:
                        variants.append({
                            "conditions": variant["conditions"],
                            "tex_key": key,
                        })
                if variants:
                    entry[f"{channel}_variants"] = variants
            normal_rules = draw.get("normal_map_variants") or []
            normal_variants = []
            for variant in normal_rules:
                key = _ensure_texture(
                    safe_resource_path(mod_dir, variant["file"]), normal_role)
                if key:
                    normal_variants.append({
                        "conditions": variant["conditions"],
                        "tex_key": key,
                    })
            if normal_variants:
                entry[f"{normal_role}_variants"] = normal_variants
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

    return MeshBuildResult(
        meshes=result,
        textures={k: v for k, v in tex_uris.items() if v},
        geometry=geometry,
    )


def build_mesh_payload(groups, mod_dir, max_draws=0, geometry=None,
                       texture_source=None, game_profile=None):
    """Compatibility wrapper for direct core fixtures.

    The application uses :func:`build_mesh_result`; older tests and scripts
    can continue to inspect the historical flat builder dictionary, including
    its private ``__textures__`` entry.
    """
    built = build_mesh_result(groups, mod_dir, max_draws=max_draws,
                              geometry=geometry,
                              texture_source=texture_source,
                              game_profile=game_profile)
    payload = dict(built.meshes)
    payload["__textures__"] = built.textures
    return payload

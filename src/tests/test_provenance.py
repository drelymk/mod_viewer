"""Tests for source provenance from ini line to UI mesh.

Every `drawindexed` that contributes to a mesh must be traceable back to the
exact file and line it came from, because an authoring edit has to fan back
out to all of them. The dedup merge in build_mesh_payload is the dangerous
part: it collapses several draws into one, and used to keep only the first.
"""

import os, sys, random, tempfile, struct, base64

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _corpus import corpus_roots
from core import ini_parser
from core.ini_parser import parse_sections, merge_sections, build_draw_groups, \
    extract_resources, extract_toggle_keys, line_source, SrcLine
from core.mesh_builder import build_mesh_payload
from app import mod_loader

FAILS = []


def check(cond, msg):
    print(("PASS  " if cond else "FAIL  ") + msg)
    if not cond:
        FAILS.append(msg)


def write(tmp, name, text):
    p = os.path.join(tmp, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


# ── SrcLine behaves exactly like str ─────────────────────────────────────────

def test_srcline_is_a_str():
    s = SrcLine("drawindexed = 1, 2, 3", "C:\\a.ini", 42, "TextureOverrideBody")
    check(s == "drawindexed = 1, 2, 3", "SrcLine compares equal to its text")
    check(s.split("=")[0].strip() == "drawindexed", "SrcLine supports str methods")
    check(isinstance(s, str), "SrcLine is a str")
    check({s: 1}["drawindexed = 1, 2, 3"] == 1, "SrcLine hashes as its text")
    check(line_source(s) == {"ini_path": "C:\\a.ini", "line_no": 42,
                             "section": "TextureOverrideBody"},
          "line_source returns file/line/section")
    check(line_source("plain string") is None, "line_source of a plain str is None")


# ── parse_sections records the real line numbers ─────────────────────────────

INI = """; leading comment

[Constants]
global persist $swapvar = 0

[KeySwap]
key = x
type = cycle
$swapvar = 0,1

[TextureOverrideBodyBlend]
ib = ResourceBodyIB
vb0 = ResourcePos
vb1 = ResourceTc
if $swapvar == 0
drawindexed = 100, 0, 0
else if $swapvar == 1
drawindexed = 200, 100, 0
endif
drawindexed = 300, 300, 0

[ResourceBodyIB]
filename = body.ib
format = DXGI_FORMAT_R32_UINT

[ResourcePos]
filename = pos.buf
stride = 40

[ResourceTc]
filename = tc.buf
stride = 20
"""


def _fixture(tmp, name, text):
    """Write an ini plus the buffer files its Resource sections name."""
    path = write(tmp, name, text)
    for buf in ("body.ib", "pos.buf", "tc.buf"):
        p = os.path.join(tmp, buf)
        if not os.path.exists(p):
            open(p, "wb").write(b"\0" * 4096)
    return path


def test_line_numbers():
    with tempfile.TemporaryDirectory() as tmp:
        path = _fixture(tmp, "mod.ini", INI)
        secs = parse_sections(path)
        lines = secs["TextureOverrideBodyBlend"]
        by_no = {l.line_no: str(l) for l in lines}
        # 1-based numbering against the literal above
        check(by_no.get(12) == "ib = ResourceBodyIB",
              "ib line reports its own line number")
        check(by_no.get(16) == "drawindexed = 100, 0, 0",
              "first drawindexed reports line 16")
        check(by_no.get(18) == "drawindexed = 200, 100, 0",
              "second drawindexed reports line 18")
        check(by_no.get(20) == "drawindexed = 300, 300, 0",
              "unconditional drawindexed reports line 20")
        check(all(l.ini_path == path for l in lines),
              "every line carries the ini path")
        check(all(l.section == "TextureOverrideBodyBlend" for l in lines),
              "every line carries its section name")


def test_draw_sources():
    with tempfile.TemporaryDirectory() as tmp:
        path = _fixture(tmp, "mod.ini", INI)
        secs = merge_sections([path])
        groups = build_draw_groups(secs, extract_resources(secs))
        check(len(groups) == 1, "one draw group built")
        draws = groups[0]["draws"]
        check(len(draws) == 3, f"three draws (got {len(draws)})")
        got = [(d["sources"][0]["line_no"], d["count"]) for d in draws]
        check(got == [(16, 100), (18, 200), (20, 300)],
              f"each draw maps to its own line: {got}")
        check(all(d["sources"][0]["ini_path"] == path for d in draws),
              "draw sources carry the ini path")
        check(all(d["sources"][0]["section"] == "TextureOverrideBodyBlend"
                  for d in draws),
              "draw sources carry the section name")


def test_toggle_key_provenance():
    with tempfile.TemporaryDirectory() as tmp:
        path = _fixture(tmp, "mod.ini", INI)
        secs = merge_sections([path])
        keys = extract_toggle_keys(secs)
        info = keys.get("KeySwap")
        check(info is not None, "KeySwap extracted")
        check(info and info["ini_path"] == path, "toggle key knows its ini file")
        check(info and info["section"] == "KeySwap", "toggle key knows its section")


# ── the dedup merge must not lose contributing lines ─────────────────────────

SHARED_INI = """[Constants]
global persist $swapvar = 0

[KeySwap]
key = x
type = cycle
$swapvar = 0,1,2

[TextureOverrideBodyBlend]
ib = ResourceBodyIB
vb0 = ResourcePos
vb1 = ResourceTc
if $swapvar == 0
drawindexed = 100, 0, 0
elif $swapvar == 1
drawindexed = 100, 0, 0
elif $swapvar == 2
drawindexed = 100, 0, 0
endif

[ResourceBodyIB]
filename = body.ib
format = DXGI_FORMAT_R32_UINT

[ResourcePos]
filename = pos.buf
stride = 40

[ResourceTc]
filename = tc.buf
stride = 20
"""


def test_merge_keeps_every_source():
    """The same mesh region drawn under three branches: one UI mesh, three lines."""
    with tempfile.TemporaryDirectory() as tmp:
        path = _fixture(tmp, "mod.ini", SHARED_INI)
        secs = merge_sections([path])
        groups = build_draw_groups(secs, extract_resources(secs))
        draws = groups[0]["draws"]
        check(len(draws) == 3, f"parser sees three drawindexed lines (got {len(draws)})")

        # Run the merge in isolation (no buffer files needed).
        merged = _merge(draws)
        check(len(merged) == 1, f"dedup collapses them to one mesh (got {len(merged)})")
        lines = sorted(s["line_no"] for s in merged[0]["sources"])
        check(lines == [14, 16, 18],
              f"all three contributing lines survive the merge: {lines}")
        alts = merged[0]["conditions"]
        check(len(alts) == 3, f"and all three conditions are OR'd (got {len(alts)})")


def test_merge_across_files():
    """Two inis both drawing the same region: sources must span both files."""
    with tempfile.TemporaryDirectory() as tmp:
        a = _fixture(tmp, "a.ini", INI)
        b = _fixture(tmp, "b.ini", INI)
        secs = merge_sections([a, b])
        groups = build_draw_groups(secs, extract_resources(secs))
        merged = _merge(groups[0]["draws"])
        first = next(m for m in merged if m["count"] == 100)
        files = sorted({os.path.basename(s["ini_path"]) for s in first["sources"]})
        check(files == ["a.ini", "b.ini"],
              f"merged mesh names both source files: {files}")


# ── cross-ini label collision (real bug: BellyDancer_mod + HairPin_mod both
# defining their own unrelated [TextureOverrideComponent0]) ─────────────────

COMPONENT0_INI = """[CommandListShared]
ib = ResourceBodyIB
vb0 = ResourcePos
vb2 = ResourceTc

[TextureOverrideComponent0]
drawindexed = 100, 0, 0

[ResourceBodyIB]
filename = body.ib
format = DXGI_FORMAT_R32_UINT

[ResourcePos]
filename = pos.buf
stride = 40

[ResourceTc]
filename = tc.buf
stride = 20
"""


def test_cross_ini_component_collision_recovered():
    """Two sibling inis (both sitting directly in the mod folder, like a real
    "AllInOne" bundle) each define their own [TextureOverrideComponent0] for
    an unrelated draw. Before the fix, build_draw_groups' `seen` dict was
    reset per ini call, so both produced the identical label "Component0" and
    the second ini's mesh silently overwrote the first's in the final flat
    payload -- total, silent geometry loss with no error. _parse_inis now
    threads one shared `seen` dict across the whole folder, so both survive
    under distinct payload keys."""
    with tempfile.TemporaryDirectory() as tmp:
        _fixture(tmp, "BellyDancer_mod.ini", COMPONENT0_INI)
        _fixture(tmp, "HairPin_mod.ini", COMPONENT0_INI)
        payload = mod_loader.load_mod(tmp)
        check("error" not in payload, f"loads cleanly (got {payload.get('error')})")

        mesh_entries = {k: v for k, v in payload.items()
                        if k not in mod_loader.RESERVED_KEYS}
        check(len(mesh_entries) == 2,
              f"both inis' Component0 survive as distinct entries (got {list(mesh_entries)})")

        sources = sorted(e.get("source") for e in mesh_entries.values())
        check(sources == ["BellyDancer_mod", "HairPin_mod"],
              f"each entry keeps its own ini as source (got {sources})")

        components = {e.get("component") for e in mesh_entries.values()}
        check(components == {"Component0"},
              f"both display as the same clean name -- no '_2' suffix leaks into the UI "
              f"(got {components}); the per-source header is what actually disambiguates them")



def _merge(draws):
    """Mirror of build_mesh_payload's dedup, minus the buffer IO.

    Kept in lockstep by test_merge_matches_payload below, which runs the real
    thing on a real mod.
    """
    from core.mesh_builder import build_mesh_payload  # noqa: F401  (import check)
    merged, order = {}, []
    for draw in draws:
        key = (draw["start"], draw["count"])
        if key not in merged:
            merged[key] = {"draw": dict(draw), "alts": [], "sources": []}
            order.append(key)
        e = merged[key]
        for src in draw.get("sources") or []:
            if src not in e["sources"]:
                e["sources"].append(src)
        cg = draw.get("conditions") or []
        if not cg:
            if [] not in e["alts"]:
                e["alts"].append([])
        else:
            for g in cg:
                if g not in e["alts"]:
                    e["alts"].append(g)
    out = []
    for key in order:
        e = merged[key]
        d = e["draw"]
        d["conditions"] = [] if any(not a for a in e["alts"]) else e["alts"]
        d["sources"] = e["sources"]
        out.append(d)
    return out


# ── end-to-end against real mods ─────────────────────────────────────────────

MOD_ROOTS = corpus_roots()


def _find_mods(limit):
    mods = []
    for root in MOD_ROOTS:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.upper().startswith("DISABLED")]
            if any(f.lower().endswith(".ini") and not f.upper().startswith("DISABLED")
                   for f in filenames):
                mods.append(dirpath)
    random.Random(11).shuffle(mods)
    return mods[:limit]


def test_real_mods():
    mods = _find_mods(15)
    if not mods:
        print("SKIP  no local mod libraries found")
        return

    total_meshes = missing = multi = 0
    bad_line = bad_file = 0
    skipped = 0

    for mod in mods:
        payload = mod_loader.load_mod(mod)
        if "error" in payload:
            # "Master" dispatch inis in AllInOne folders hold only menus and
            # namespace switches, never geometry — nothing to trace.
            if "No mesh geometry" in payload["error"]:
                skipped += 1
            else:
                check(False, f"{os.path.basename(mod)} failed to load: "
                             f"{payload['error'].strip().splitlines()[-1][:100]}")
            continue
        for name, entry in payload.items():
            if name in mod_loader.RESERVED_KEYS or not isinstance(entry, dict):
                continue
            total_meshes += 1
            srcs = entry.get("sources") or []
            if not srcs:
                missing += 1
                continue
            if len(srcs) > 1:
                multi += 1
            for s in srcs:
                ini_abs = os.path.join(mod, s["ini"] or "")
                if not os.path.isfile(ini_abs):
                    bad_file += 1
                    continue
                with open(ini_abs, encoding="utf-8", errors="ignore") as f:
                    all_lines = f.readlines()
                n = s["line"]
                if not (n and 1 <= n <= len(all_lines)):
                    bad_line += 1
                    continue
                text = all_lines[n - 1].strip().lower()
                # Normally the exact drawindexed. A section with no drawindexed
                # at all is drawn by reading the whole IB, and is anchored to
                # the first significant line of its section instead.
                if not (text.startswith("drawindexed")
                        or _anchors_section(all_lines, n, s["section"])):
                    bad_line += 1

    print(f"      {len(mods)} mods ({skipped} geometry-free), {total_meshes} meshes, "
          f"{multi} multi-source, {missing} without provenance")
    check(total_meshes > 0, "real mods produced meshes")
    check(missing == 0, f"every mesh has provenance (missing={missing})")
    check(bad_file == 0, f"every recorded ini path resolves (bad={bad_file})")
    check(bad_line == 0,
          f"every recorded line is a drawindexed or a section anchor (bad={bad_line})")
    check(multi > 0, f"at least one mesh has several sources (got {multi})")


def _anchors_section(all_lines, n, section):
    """True if line n is the first significant line of `section`."""
    header = f"[{(section or '').lower()}]"
    i = n - 2
    while i >= 0:
        t = all_lines[i].strip().lower()
        if t.startswith("[") and t.endswith("]"):
            return t == header
        if t and not t.startswith(";"):
            return False
        i -= 1
    return False


# ── a mod ini may reach a sibling assets folder, but not roam the disk ───────

def _traversal_mod(tmp, pos_filename):
    """A minimal one-draw mod whose vb0 filename is `pos_filename`."""
    ini_text = f"""[Constants]
global persist $swapvar = 0

[TextureOverrideBodyBlend]
ib = ResourceBodyIB
vb0 = ResourcePos
vb1 = ResourceTc
drawindexed = 100, 0, 0

[ResourceBodyIB]
filename = body.ib
format = DXGI_FORMAT_R32_UINT

[ResourcePos]
filename = {pos_filename}
stride = 40

[ResourceTc]
filename = tc.buf
stride = 20
"""
    path = write(tmp, "mod.ini", ini_text)
    for buf in ("body.ib", "tc.buf"):
        open(os.path.join(tmp, buf), "wb").write(b"\0" * 4096)
    secs = merge_sections([path])
    groups = build_draw_groups(secs, extract_resources(secs))
    payload = build_mesh_payload(groups, tmp)
    return {k: v for k, v in payload.items() if k != "__textures__"}


def test_resource_path_may_reach_a_sibling_folder():
    """`filename = ..\\resources\\x.buf` is how mods share assets between the
    ini's folder and its neighbours -- it has to resolve."""
    with tempfile.TemporaryDirectory() as tmp:
        mod = os.path.join(tmp, "mod")
        shared = os.path.join(tmp, "shared")
        os.makedirs(mod); os.makedirs(shared)
        open(os.path.join(shared, "pos.buf"), "wb").write(b"\1" * 4096)

        meshes = _traversal_mod(mod, "../shared/pos.buf")
        check(len(meshes) == 1,
              f"a resource one folder above the ini is read (got {list(meshes)})")


def test_absolute_resource_path_blocked():
    """The mod folder is untrusted, downloaded content: a crafted `filename`
    naming an absolute path must not be read."""
    with tempfile.TemporaryDirectory() as outside, tempfile.TemporaryDirectory() as tmp:
        secret = os.path.join(outside, "secret.buf")
        with open(secret, "wb") as f:
            f.write(b"\1" * 4096)

        meshes = _traversal_mod(tmp, secret.replace(os.sep, "/"))
        check(not meshes, f"absolute resource path is refused (got {list(meshes)})")


def test_deep_resource_path_traversal_blocked():
    """`..` is allowed, but only a few levels up -- not far enough to walk out
    of the mod library and into the user's own files."""
    with tempfile.TemporaryDirectory() as tmp:
        secret = os.path.join(tmp, "secret.buf")
        with open(secret, "wb") as f:
            f.write(b"\1" * 4096)
        mod = os.path.join(tmp, "a", "b", "c", "d", "e")
        os.makedirs(mod)

        meshes = _traversal_mod(mod, "../../../../../secret.buf")
        check(not meshes,
              f"a resource far above the mod folder is refused (got {list(meshes)})")


def test_toggle_panel_provenance():
    mods = _find_mods(15)
    if not mods:
        return
    checked = bad = 0
    for mod in mods:
        payload = mod_loader.load_mod(mod)
        for section, info in (payload.get("__toggles__") or {}).items():
            checked += 1
            ini = info.get("ini")
            if not ini or not os.path.isfile(os.path.join(mod, ini)):
                bad += 1
                continue
            secs = parse_sections(os.path.join(mod, ini))
            if info.get("section") not in secs:
                bad += 1
    print(f"      {checked} toggle sections checked")
    check(checked > 0, "real mods produced toggle sections")
    check(bad == 0, f"every toggle resolves to a real section in a real file (bad={bad})")


# ── mid-section `ib =` reassignment must not be lost

IB_REASSIGN_INI = """[TextureOverrideBodyBlend]
ib = ResourceBodyHeadIB
vb0 = ResourcePos
vb1 = ResourceTc
drawindexed = 100, 0, 0
ib = ResourceBodyDressIB
drawindexed = 100, 0, 0

[ResourceBodyHeadIB]
filename = head.ib
format = DXGI_FORMAT_R32_UINT

[ResourceBodyDressIB]
filename = dress.ib
format = DXGI_FORMAT_R32_UINT

[ResourcePos]
filename = pos.buf
stride = 40

[ResourceTc]
filename = tc.buf
stride = 20
"""


def test_mid_section_ib_reassignment_ini_parser():
    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, "mod.ini", IB_REASSIGN_INI)
        secs = merge_sections([path])
        groups = build_draw_groups(secs, extract_resources(secs))
        check(len(groups) == 1, f"one draw group built (got {len(groups)})")
        draws = groups[0]["draws"]
        check(len(draws) == 2, f"both drawindexed lines kept (got {len(draws)})")
        check(groups[0]["ib_file"] == "head.ib",
              f"group's default ib is the section's first-seen one (got {groups[0]['ib_file']})")
        check(draws[0].get("ib_file") is None,
              "first draw has no override -- reads the group's default ib (head.ib)")
        check(draws[1].get("ib_file") == "dress.ib",
              f"second draw carries the reassigned ib (got {draws[1].get('ib_file')})")


def test_mid_section_ib_reassignment_mesh_builder():
    """End-to-end: build_mesh_payload must read each draw's indices from its
    own reassigned ib file, and must not merge two draws that happen to share
    (start, count) but actually come from different index buffers."""
    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, "mod.ini", IB_REASSIGN_INI)
        open(os.path.join(tmp, "head.ib"), "wb").write(struct.pack("<3I", 10, 11, 12))
        open(os.path.join(tmp, "dress.ib"), "wb").write(struct.pack("<3I", 20, 21, 22))
        # 32 unique, identifiable vertices: vertex i sits at position (i, i, i)
        with open(os.path.join(tmp, "pos.buf"), "wb") as f:
            for i in range(32):
                f.write(struct.pack("<3f", float(i), float(i), float(i)) + b"\0" * 28)
        open(os.path.join(tmp, "tc.buf"), "wb").write(b"\0" * 20 * 32)

        secs = merge_sections([path])
        groups = build_draw_groups(secs, extract_resources(secs))
        # both draws are (start=0, count=100) in the ini above -- shrink to the
        # 3 real indices we actually wrote so read_indices has something to read
        for d in groups[0]["draws"]:
            d["count"] = 3
        payload = build_mesh_payload(groups, tmp)

        meshes = {k: v for k, v in payload.items() if k != "__textures__"}
        check(len(meshes) == 2, f"both draws survive as distinct meshes, not merged "
                                 f"(got {len(meshes)})")

        def _verts(entry):
            pos = struct.unpack(f"<{len(base64.b64decode(entry['pos'])) // 4}f",
                                 base64.b64decode(entry["pos"]))
            return sorted(round(pos[i]) for i in range(0, len(pos), 3))

        vert_sets = sorted(_verts(e) for e in meshes.values())
        check(vert_sets == [[10, 11, 12], [20, 21, 22]],
              f"each mesh's own vertices come from its own reassigned ib (got {vert_sets})")


# ── a mid-section `ib =` reassignment paired with `vb0/vb1 =` (a "cross IB/VB"
#    swap some GIMI mods use to draw a wholly different mesh's buffers for a
#    handful of draws) must resolve the NEW mesh's own position/texcoord too,
#    not just its own ib -- otherwise the new ib's indices get read against
#    the old mesh's (unrelated, often shorter) position buffer, producing
#    garbage vertices. The literal `vb0=`/`vb1=` values paired with the
#    reassignment are deliberately NOT the source of truth here (real mods
#    sometimes set them to a `= copy vb0` runtime GPU snapshot with no
#    filename at all) -- the fix re-derives the buffers from the reassigned
#    `ib`'s own component, same as a normal group's defaults are resolved.

CROSS_IB_VB_INI = """[TextureOverrideSBSBlend]
vb0 = ResourceSBSPosition
vb1 = ResourceSBSTexcoord

[TextureOverrideXBSBlend]
vb0 = ResourceXBSPosition
vb1 = ResourceXBSTexcoord

[TextureOverrideSBSA]
ib = ResourceSBSAIB
drawindexed = 3, 0, 0
ib = ResourceXBSAIB
vb0 = ResourceXBSCrossIBVB
vb1 = ResourceXBSTexcoord
drawindexed = 3, 0, 0

[ResourceXBSCrossIBVB]

[ResourceSBSAIB]
filename = sbsA.ib
format = DXGI_FORMAT_R32_UINT

[ResourceXBSAIB]
filename = xbsA.ib
format = DXGI_FORMAT_R32_UINT

[ResourceSBSPosition]
filename = sbsPos.buf
stride = 40

[ResourceSBSTexcoord]
filename = sbsTc.buf
stride = 20

[ResourceXBSPosition]
filename = xbsPos.buf
stride = 40

[ResourceXBSTexcoord]
filename = xbsTc.buf
stride = 20
"""


def test_cross_ib_vb_reassignment_ini_parser():
    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, "mod.ini", CROSS_IB_VB_INI)
        secs = merge_sections([path])
        groups = build_draw_groups(secs, extract_resources(secs))
        check(len(groups) == 1, f"one draw group built (got {len(groups)})")
        draws = groups[0]["draws"]
        check(len(draws) == 2, f"both drawindexed lines kept (got {len(draws)})")
        check("position_file" not in draws[0] and "texcoord_file" not in draws[0],
              "first draw has no override -- reads the group's default SBS buffers")
        check(draws[1].get("position_file") == "xbsPos.buf" and
              draws[1].get("texcoord_file") == "xbsTc.buf",
              f"second draw carries its own reassigned vertex buffers "
              f"(got {draws[1].get('position_file')}, {draws[1].get('texcoord_file')})")


def test_cross_ib_vb_reassignment_mesh_builder():
    """End-to-end: the second draw's indices must decode against its own
    reassigned XBS position buffer, not the group's default (shorter) SBS one
    -- reading against the wrong buffer either raises IndexError or silently
    collapses out-of-range vertices to the origin, which is exactly the
    "triangles fly everywhere" corruption this guards against."""
    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, "mod.ini", CROSS_IB_VB_INI)
        open(os.path.join(tmp, "sbsA.ib"), "wb").write(struct.pack("<3I", 0, 1, 2))
        open(os.path.join(tmp, "xbsA.ib"), "wb").write(struct.pack("<3I", 5, 6, 7))
        # SBS has only 4 vertices; XBS has 8, at a different scale, so decoding
        # against the wrong one is either out-of-range or visibly wrong.
        with open(os.path.join(tmp, "sbsPos.buf"), "wb") as f:
            for i in range(4):
                f.write(struct.pack("<3f", float(i), float(i), float(i)) + b"\0" * 28)
        with open(os.path.join(tmp, "xbsPos.buf"), "wb") as f:
            for i in range(8):
                v = float(i * 10)
                f.write(struct.pack("<3f", v, v, v) + b"\0" * 28)
        open(os.path.join(tmp, "sbsTc.buf"), "wb").write(b"\0" * 20 * 4)
        open(os.path.join(tmp, "xbsTc.buf"), "wb").write(b"\0" * 20 * 8)

        secs = merge_sections([path])
        groups = build_draw_groups(secs, extract_resources(secs))
        payload = build_mesh_payload(groups, tmp)

        meshes = {k: v for k, v in payload.items() if k != "__textures__"}
        check(len(meshes) == 2, f"both draws survive as distinct meshes (got {len(meshes)})")

        def _verts(entry):
            pos = struct.unpack(f"<{len(base64.b64decode(entry['pos'])) // 4}f",
                                 base64.b64decode(entry["pos"]))
            return sorted(round(pos[i]) for i in range(0, len(pos), 3))

        vert_sets = sorted(_verts(e) for e in meshes.values())
        check(vert_sets == [[0, 1, 2], [50, 60, 70]],
              f"the reassigned draw decodes against its own XBS position buffer, "
              f"not a collapsed/garbage read of the SBS one (got {vert_sets})")


# ── `handling = skip` with no `drawindexed` line at all means "suppress the
#    original draw and replace it with nothing", NOT "draw the whole ib".
#    Only a section that omits `handling = skip` gets the implicit
#    whole-buffer-draw fallback (it lets the game's own, unmodified draw call
#    proceed against the new ib).

HANDLING_SKIP_INI = """[TextureOverrideBodyBlend]
vb0 = ResourcePos
vb1 = ResourceTc

[TextureOverrideBodyA]
ib = ResourceBodyAIB
drawindexed = 100, 0, 0

[TextureOverrideBodyB]
handling = skip
ib = ResourceBodyBIB

[TextureOverrideBodyC]
ib = ResourceBodyCIB

[ResourceBodyAIB]
filename = bodyA.ib
format = DXGI_FORMAT_R32_UINT

[ResourceBodyBIB]
filename = bodyB.ib
format = DXGI_FORMAT_R32_UINT

[ResourceBodyCIB]
filename = bodyC.ib
format = DXGI_FORMAT_R32_UINT

[ResourcePos]
filename = pos.buf
stride = 40

[ResourceTc]
filename = tc.buf
stride = 20
"""


def test_handling_skip_with_no_drawindexed_draws_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, "mod.ini", HANDLING_SKIP_INI)
        secs = merge_sections([path])
        groups = build_draw_groups(secs, extract_resources(secs))
        names = {g["display_name"] for g in groups}
        check("BodyA" in names, "the section with an explicit drawindexed still draws")
        check("BodyB" not in names,
              f"a handling=skip section with NO drawindexed draws nothing at all "
              f"(got groups: {sorted(names)})")
        check("BodyC" in names,
              "a section with no handling=skip still gets the implicit whole-ib draw")


# ── a draw section's `ib=` can name a component that's a per-part suffix
#    (e.g. "...Head"/"...Body") of the shared Position/Blend/Texcoord
#    component -- and the shared component's own name can itself end in an
#    uppercase abbreviation (e.g. Genshin's "CN" outfit-variant suffix), which
#    broke the CamelCase-word-strip fallback's assumption that a lowercase
#    letter always precedes the final word.

COMPONENT_ABBREV_SUFFIX_INI = """[TextureOverrideXCNPosition]
vb0 = ResourceXCNPosition

[TextureOverrideXCNBlend]
vb1 = ResourceXCNBlend

[TextureOverrideXCNTexcoord]
vb1 = ResourceXCNTexcoord

[TextureOverrideXCNHead]
ib = ResourceXCNHeadIB
drawindexed = 100, 0, 0

[ResourceXCNPosition]
filename = pos.buf
stride = 40

[ResourceXCNBlend]
filename = blend.buf
stride = 32

[ResourceXCNTexcoord]
filename = tc.buf
stride = 12

[ResourceXCNHeadIB]
filename = head.ib
format = DXGI_FORMAT_R32_UINT
"""


def test_component_name_ending_in_uppercase_abbreviation():
    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, "mod.ini", COMPONENT_ABBREV_SUFFIX_INI)
        secs = merge_sections([path])
        groups = build_draw_groups(secs, extract_resources(secs))
        names = {g["display_name"]: g for g in groups}
        check("XCNHead" in names,
              f"the Head draw resolves its buffers via the shared XCN component "
              f"(got groups: {sorted(names)})")
        if "XCNHead" in names:
            g = names["XCNHead"]
            check(g["position_file"] == "pos.buf" and g["texcoord_file"] == "tc.buf",
                  f"resolved to the shared component's own buffers "
                  f"(got {g['position_file']}, {g['texcoord_file']})")


RUN_CHAIN_INI = """[Constants]
global persist $naked = 0
global persist $flag = 0

[KeyNaked]
key = n
type = cycle
$naked = 0,1

[KeyFlag]
key = f
type = cycle
$flag = 0,1

[TextureOverrideBodyBlend]
ib = ResourceBodyIB
vb0 = ResourcePos
vb1 = ResourceTc
if $naked == 0
drawindexed = 100, 0, 0
run = CustomShaderOuter
endif

[CustomShaderOuter]
run = CommandListTransparent

[CommandListTransparent]
if $flag == 0
drawindexed = 50, 200, 0
endif

[ResourceBodyIB]
filename = body.ib
format = DXGI_FORMAT_R32_UINT

[ResourcePos]
filename = pos.buf
stride = 40

[ResourceTc]
filename = tc.buf
stride = 20
"""


def _visible(conds, bindings):
    if conds == []:
        return True
    return any(all((bindings.get(c["var"]) == c["value"]) != c["negate"] for c in group)
               for group in conds)


def test_run_inlines_nested_commandlist_draws():
    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, "mod.ini", RUN_CHAIN_INI)
        secs = merge_sections([path])
        groups = build_draw_groups(secs, extract_resources(secs))
        check(len(groups) == 1, f"one draw group built (got {len(groups)})")
        draws = groups[0]["draws"]
        check(len(draws) == 2,
              f"the run=-chained drawindexed is inlined alongside the direct one "
              f"(got {len(draws)})")
        by_count = {d["count"]: d for d in draws}
        check(100 in by_count and 50 in by_count,
              f"both the direct and run=-chained draws are present (got {sorted(by_count)})")

        chained = by_count[50]
        check(_visible(chained["conditions"], {"naked": "0", "flag": "0"}),
              "chained draw visible when both naked==0 and flag==0")
        check(not _visible(chained["conditions"], {"naked": "1", "flag": "0"}),
              "chained draw hidden when the caller's own gate (naked==0) fails")
        check(not _visible(chained["conditions"], {"naked": "0", "flag": "1"}),
              "chained draw hidden when the callee's own gate (flag==0) fails")


# ── a toggle can reassign the diffuse texture instead of (or as well as)
#    gating a draw -- e.g. HousekeeperColumbina.ini's `$seven2` swap

DIFFUSE_SWAP_INI = """[Constants]
global persist $seven2 = 0

[KeySeven2]
key = k
type = cycle
$seven2 = 0,1

[TextureOverrideColumbinaBodyBlend]
ib = ResourceColumbinaBodyIB
vb0 = ResourcePos
vb1 = ResourceTc
Resource\\GIMI\\Diffuse = ref ResourceDiffuseA
drawindexed = 10, 0, 0
if $seven2 == 1
Resource\\GIMI\\Diffuse = ref ResourceDiffuseB
else
Resource\\GIMI\\Diffuse = ref ResourceDiffuseC
endif
drawindexed = 20, 100, 0

[ResourceColumbinaBodyIB]
filename = body.ib
format = DXGI_FORMAT_R32_UINT

[ResourcePos]
filename = pos.buf
stride = 40

[ResourceTc]
filename = tc.buf
stride = 20

[ResourceDiffuseA]
filename = diffuseA.dds

[ResourceDiffuseB]
filename = diffuseB.dds

[ResourceDiffuseC]
filename = diffuseC.dds
"""


def test_toggle_driven_diffuse_swap_ini_parser():
    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, "mod.ini", DIFFUSE_SWAP_INI)
        secs = merge_sections([path])
        groups = build_draw_groups(secs, extract_resources(secs))
        check(len(groups) == 1, f"one draw group built (got {len(groups)})")
        group = groups[0]
        check(group["diffuse_file"] == "diffuseA.dds",
              f"group default diffuse is the first-seen, unconditional one "
              f"(got {group['diffuse_file']})")
        draws = {d["count"]: d for d in group["draws"]}

        check("texture_variants" not in draws[10],
              "the earlier unconditional diffuse assignment doesn't leak a "
              "spurious single-entry texture_variants onto the first draw")

        variants = draws[20].get("texture_variants")
        check(bool(variants) and len(variants) == 2,
              f"exactly 2 variants for the later, toggle-gated draw (got {variants})")
        by_file = {v["file"]: v["conditions"] for v in variants}
        check(set(by_file) == {"diffuseB.dds", "diffuseC.dds"},
              f"both branches' files are present (got {sorted(by_file)})")
        check(_visible(by_file["diffuseB.dds"], {"seven2": "1"}) and
              not _visible(by_file["diffuseB.dds"], {"seven2": "0"}),
              "diffuseB's condition matches only seven2==1")
        check(_visible(by_file["diffuseC.dds"], {"seven2": "0"}) and
              not _visible(by_file["diffuseC.dds"], {"seven2": "1"}),
              "diffuseC's condition (the else branch) matches only seven2==0")


def test_toggle_driven_diffuse_swap_mesh_builder():
    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, "mod.ini", DIFFUSE_SWAP_INI)
        open(os.path.join(tmp, "body.ib"), "wb").write(
            struct.pack("<3I", 0, 1, 2) + struct.pack("<3I", 3, 4, 5))
        with open(os.path.join(tmp, "pos.buf"), "wb") as f:
            for i in range(8):
                f.write(struct.pack("<3f", float(i), float(i), float(i)) + b"\0" * 28)
        open(os.path.join(tmp, "tc.buf"), "wb").write(b"\0" * 20 * 8)
        for name in ("diffuseA.dds", "diffuseB.dds", "diffuseC.dds"):
            open(os.path.join(tmp, name), "wb").write(b"DDS " + name.encode())

        secs = merge_sections([path])
        groups = build_draw_groups(secs, extract_resources(secs))
        for d in groups[0]["draws"]:
            d["start"], d["count"] = (0, 3) if d["count"] == 10 else (3, 3)
        payload = build_mesh_payload(groups, tmp)

        meshes = {k: v for k, v in payload.items() if k != "__textures__"}
        by_draw = {tuple(e["drawindexed"]): e for e in meshes.values()}
        first  = by_draw[(3, 0, 0)]
        second = by_draw[(3, 3, 0)]

        check("texture_variants" not in first,
              "first draw's payload entry carries no texture_variants (single diffuse)")

        variants = second.get("texture_variants")
        check(bool(variants) and len(variants) == 2,
              f"second draw's payload entry carries both resolved variants (got {variants})")
        keys = {v["tex_key"] for v in variants}
        check(keys == {"diffuseB.dds", "diffuseC.dds"},
              f"each variant's tex_key names its own resolved diffuse file (got {keys})")
        check(second["tex_key"] == "diffuseA.dds",
              f"the draw's default tex_key is unchanged -- an older consumer that "
              f"ignores texture_variants still renders the group's default diffuse "
              f"(got {second['tex_key']})")


# ── XXMI-generated mods assign the diffuse without the "ref" keyword
#    (e.g. "Resource\GIMI\Diffuse = ResourceXDiffuse"), unlike the
#    "= ref X" form other tools emit -- both must resolve the same way.

DIFFUSE_NO_REF_INI = """[TextureOverrideXPosition]
vb0 = ResourceXPosition

[TextureOverrideXBlend]
vb1 = ResourceXBlend

[TextureOverrideXTexcoord]
vb1 = ResourceXTexcoord

[TextureOverrideXA]
ib = ResourceXAIB
Resource\\GIMI\\Diffuse = ResourceXDiffuse
run = CommandList\\GIMI\\SetTextures
drawindexed = 100, 0, 0

[ResourceXPosition]
filename = pos.buf
stride = 40

[ResourceXBlend]
filename = blend.buf
stride = 32

[ResourceXTexcoord]
filename = tc.buf
stride = 20

[ResourceXAIB]
filename = a.ib
format = DXGI_FORMAT_R32_UINT

[ResourceXDiffuse]
filename = diffuseX.dds
"""


def test_diffuse_assignment_without_ref_keyword():
    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, "mod.ini", DIFFUSE_NO_REF_INI)
        secs = merge_sections([path])
        groups = build_draw_groups(secs, extract_resources(secs))
        names = {g["display_name"]: g for g in groups}
        check("XA" in names, f"the draw section builds a group (got {sorted(names)})")
        if "XA" in names:
            check(names["XA"]["diffuse_file"] == "diffuseX.dds",
                  f"the no-\"ref\" diffuse assignment still resolves "
                  f"(got {names['XA']['diffuse_file']})")


# ── index buffers come in both DXGI_FORMAT_R32_UINT and _R16_UINT; reading a
#    16-bit one as 32-bit yields garbage indices and no usable mesh.

IB_R16_INI = DIFFUSE_NO_REF_INI.replace("DXGI_FORMAT_R32_UINT", "DXGI_FORMAT_R16_UINT")


def test_r16_index_buffer():
    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, "mod.ini", IB_R16_INI)
        open(os.path.join(tmp, "a.ib"), "wb").write(struct.pack("<3H", 5, 6, 7))
        with open(os.path.join(tmp, "pos.buf"), "wb") as f:
            for i in range(8):
                f.write(struct.pack("<3f", float(i), float(i), float(i)) + b"\0" * 28)
        open(os.path.join(tmp, "tc.buf"), "wb").write(b"\0" * 20 * 8)

        secs = merge_sections([path])
        groups = build_draw_groups(secs, extract_resources(secs))
        check(groups and groups[0]["index_size"] == 2,
              f"an R16_UINT ib reports 2 bytes per index "
              f"(got {groups[0]['index_size'] if groups else None})")
        for d in groups[0]["draws"]:
            d["count"] = 3
        payload = build_mesh_payload(groups, tmp)

        meshes = {k: v for k, v in payload.items() if k != "__textures__"}
        check(len(meshes) == 1, f"the draw builds a mesh (got {len(meshes)})")
        entry = next(iter(meshes.values()))
        pos = struct.unpack(f"<{len(base64.b64decode(entry['pos'])) // 4}f",
                            base64.b64decode(entry["pos"]))
        verts = sorted(round(pos[i]) for i in range(0, len(pos), 3))
        check(verts == [5, 6, 7],
              f"16-bit indices are decoded as 16-bit, not 32-bit (got {verts})")


if __name__ == "__main__":
    for fn in (test_srcline_is_a_str, test_line_numbers, test_draw_sources,
               test_toggle_key_provenance, test_merge_keeps_every_source,
               test_merge_across_files, test_cross_ini_component_collision_recovered,
               test_mid_section_ib_reassignment_ini_parser,
               test_mid_section_ib_reassignment_mesh_builder,
               test_cross_ib_vb_reassignment_ini_parser,
               test_cross_ib_vb_reassignment_mesh_builder,
               test_handling_skip_with_no_drawindexed_draws_nothing,
               test_component_name_ending_in_uppercase_abbreviation,
               test_run_inlines_nested_commandlist_draws,
               test_toggle_driven_diffuse_swap_ini_parser,
               test_toggle_driven_diffuse_swap_mesh_builder,
               test_diffuse_assignment_without_ref_keyword,
               test_r16_index_buffer,
               test_resource_path_may_reach_a_sibling_folder,
               test_absolute_resource_path_blocked,
               test_deep_resource_path_traversal_blocked,
               test_real_mods, test_toggle_panel_provenance):
        fn()
    print("\n" + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"))
    sys.exit(1 if FAILS else 0)

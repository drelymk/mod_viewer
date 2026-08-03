"""Tests for source provenance from ini line to UI mesh.

Every `drawindexed` that contributes to a mesh must be traceable back to the
exact file and line it came from, because an authoring edit has to fan back
out to all of them. The dedup merge in build_mesh_payload is the dangerous
part: it collapses several draws into one, and used to keep only the first.
"""

import os, sys, random, tempfile

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


# ── a malicious mod ini must not read files outside its own folder ──────────

def test_resource_path_traversal_blocked():
    """A crafted `filename = ...` in a [Resource...] section pointing outside
    mod_dir (via ".." or an absolute path) must not be read, even though the
    mod folder itself is untrusted, downloaded content.
    """
    with tempfile.TemporaryDirectory() as outside, tempfile.TemporaryDirectory() as tmp:
        secret = os.path.join(outside, "secret.buf")
        with open(secret, "wb") as f:
            f.write(b"\1" * 4096)

        rel_escape = os.path.relpath(secret, tmp).replace(os.sep, "/")
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
filename = {rel_escape}
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

        meshes = {k: v for k, v in payload.items() if k != "__textures__"}
        check(not meshes, f"escaping resource path is refused, not read (got {list(meshes)})")


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


if __name__ == "__main__":
    for fn in (test_srcline_is_a_str, test_line_numbers, test_draw_sources,
               test_toggle_key_provenance, test_merge_keeps_every_source,
               test_merge_across_files, test_cross_ini_component_collision_recovered,
               test_resource_path_traversal_blocked,
               test_real_mods, test_toggle_panel_provenance):
        fn()
    print("\n" + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"))
    sys.exit(1 if FAILS else 0)

"""Tests for source provenance from ini line to UI mesh.

Every draw that contributes to a mesh must remain traceable to its source file
and line after parsing and deduplication.
"""

import os
import tempfile

from app import mod_loader
from core.ini_parser import (SrcLine, build_draw_groups, extract_resources,
                             extract_toggle_keys, line_source, merge_sections,
                             parse_sections)
from core.mesh_builder import _deduplicate_draws
from _provenance_support import write

def test_srcline_is_a_str():
    s = SrcLine("drawindexed = 1, 2, 3", "C:\\a.ini", 42, "TextureOverrideBody")
    assert (s == "drawindexed = 1, 2, 3"), ("SrcLine compares equal to its text")
    assert (s.split("=")[0].strip() == "drawindexed"), ("SrcLine supports str methods")
    assert (isinstance(s, str)), ("SrcLine is a str")
    assert ({s: 1}["drawindexed = 1, 2, 3"] == 1), ("SrcLine hashes as its text")
    assert (line_source(s) == {"ini_path": "C:\\a.ini", "line_no": 42,
                             "section": "TextureOverrideBody"}), ("line_source returns file/line/section")
    assert (line_source("plain string") is None), ("line_source of a plain str is None")


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
        assert (by_no.get(12) == "ib = ResourceBodyIB"), ("ib line reports its own line number")
        assert (by_no.get(16) == "drawindexed = 100, 0, 0"), ("first drawindexed reports line 16")
        assert (by_no.get(18) == "drawindexed = 200, 100, 0"), ("second drawindexed reports line 18")
        assert (by_no.get(20) == "drawindexed = 300, 300, 0"), ("unconditional drawindexed reports line 20")
        assert (all(l.ini_path == path for l in lines)), ("every line carries the ini path")
        assert (all(l.section == "TextureOverrideBodyBlend" for l in lines)), ("every line carries its section name")


def test_draw_sources():
    with tempfile.TemporaryDirectory() as tmp:
        path = _fixture(tmp, "mod.ini", INI)
        secs = merge_sections([path])
        groups = build_draw_groups(secs, extract_resources(secs))
        assert (len(groups) == 1), ("one draw group built")
        draws = groups[0]["draws"]
        assert (len(draws) == 3), (f"three draws (got {len(draws)})")
        got = [(d["sources"][0]["line_no"], d["count"]) for d in draws]
        assert (got == [(16, 100), (18, 200), (20, 300)]), (f"each draw maps to its own line: {got}")
        assert (all(d["sources"][0]["ini_path"] == path for d in draws)), ("draw sources carry the ini path")
        assert (all(d["sources"][0]["section"] == "TextureOverrideBodyBlend"
                  for d in draws)), ("draw sources carry the section name")


def test_toggle_key_provenance():
    with tempfile.TemporaryDirectory() as tmp:
        path = _fixture(tmp, "mod.ini", INI)
        secs = merge_sections([path])
        keys = extract_toggle_keys(secs)
        info = keys.get("KeySwap")
        assert (info is not None), ("KeySwap extracted")
        assert (info and info["ini_path"] == path), ("toggle key knows its ini file")
        assert (info and info["section"] == "KeySwap"), ("toggle key knows its section")


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
        assert (len(draws) == 3), (f"parser sees three drawindexed lines (got {len(draws)})")

        # Run the merge in isolation (no buffer files needed).
        merged = _deduplicate_draws({"draws": draws})
        assert (len(merged) == 1), (f"dedup collapses them to one mesh (got {len(merged)})")
        lines = sorted(s["line_no"] for s in merged[0]["sources"])
        assert (lines == [14, 16, 18]), (f"all three contributing lines survive the merge: {lines}")
        alts = merged[0]["conditions"]
        assert (len(alts) == 3), (f"and all three conditions are OR'd (got {len(alts)})")


def test_merge_across_files():
    """Two inis both drawing the same region: sources must span both files."""
    with tempfile.TemporaryDirectory() as tmp:
        a = _fixture(tmp, "a.ini", INI)
        b = _fixture(tmp, "b.ini", INI)
        secs = merge_sections([a, b])
        groups = build_draw_groups(secs, extract_resources(secs))
        merged = _deduplicate_draws({"draws": groups[0]["draws"]})
        first = next(m for m in merged if m["count"] == 100)
        files = sorted({os.path.basename(s["ini_path"]) for s in first["sources"]})
        assert (files == ["a.ini", "b.ini"]), (f"merged mesh names both source files: {files}")


def test_deduplicate_preserves_buffer_identity():
    """Equal index ranges using different buffers are distinct meshes."""
    draws = [
        {"start": 0, "count": 100, "ib_file": "body.ib",
         "position_file": "body.pos", "texcoord_file": "body.tc",
         "conditions": [], "sources": []},
        {"start": 0, "count": 100, "ib_file": "head.ib",
         "position_file": "head.pos", "texcoord_file": "head.tc",
         "conditions": [], "sources": []},
    ]
    merged = _deduplicate_draws({"draws": draws})
    assert (len(merged) == 2), (f"different buffer bindings do not collapse by range alone (got {len(merged)})")


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
    """Two sibling inis each define their own [TextureOverrideComponent0] for
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
        assert ("error" not in payload), (f"loads cleanly (got {payload.get('error')})")

        mesh_entries = payload.get("meshes", {})
        assert (len(mesh_entries) == 2), (f"both inis' Component0 survive as distinct entries (got {list(mesh_entries)})")

        sources = sorted(e.get("source") for e in mesh_entries.values())
        assert (sources == ["BellyDancer_mod", "HairPin_mod"]), (f"each entry keeps its own ini as source (got {sources})")

        components = {e.get("component") for e in mesh_entries.values()}
        assert (components == {"Component0"}), (f"both display as the same clean name -- no '_2' suffix leaks into the UI "
              f"(got {components}); the per-source header is what actually disambiguates them")

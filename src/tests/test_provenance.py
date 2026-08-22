"""Tests for source provenance from ini line to UI mesh.

Every draw that contributes to a mesh must remain traceable to its source file
and line after parsing and deduplication.
"""

import os
import tempfile

import pytest

from app import mod_loader
from core.draw_call import DrawCall
from core.ini_parser import (SrcLine, build_draw_groups, extract_resources,
                             extract_toggle_keys, line_source, merge_sections,
                             parse_sections)
from core.mesh_builder import _deduplicate_draws
from _provenance_support import write



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
    sizes = {"body.ib": 4 * 1024, "pos.buf": 40 * 1024,
             "tc.buf": 20 * 1024}
    for buf, size in sizes.items():
        p = os.path.join(tmp, buf)
        if not os.path.exists(p):
            open(p, "wb").write(b"\0" * size)
    return path


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


def test_deduplicate_preserves_base_index_and_material_identity():
    common = {
        "start": 0, "count": 100, "ib_file": "body.ib",
        "position_file": "body.pos", "texcoord_file": "body.tc",
        "conditions": [], "sources": [],
    }
    draws = [
        {**common, "label": "base-0", "base": 0, "index_size": 4,
         "texture_default_file": "red.dds"},
        {**common, "label": "base-1", "base": 1, "index_size": 4,
         "texture_default_file": "red.dds"},
        {**common, "label": "r16", "base": 0, "index_size": 2,
         "texture_default_file": "red.dds"},
        {**common, "label": "blue", "base": 0, "index_size": 4,
         "texture_default_file": "blue.dds"},
    ]
    merged = _deduplicate_draws({"draws": draws})
    assert len(merged) == 4


def test_draw_call_ir_normalizes_inherited_state_before_deduplication():
    group = {
        "ib_file": "body.ib", "index_size": 4,
        "position_file": "body.pos", "position_stride": 40,
        "texcoord_file": "body.tc", "texcoord_stride": 20,
        "draws": [
            {"label": "implicit", "count": 100, "start": 0, "base": 0,
             "conditions": [[{"var": "swap", "value": "0"}]],
             "sources": [{"line_no": 10}]},
            {"label": "explicit", "count": 100, "start": 0, "base": 0,
             "ib_file": "body.ib", "index_size": 4,
             "position_file": "body.pos", "position_stride": 40,
             "texcoord_file": "body.tc", "texcoord_stride": 20,
             "conditions": [[{"var": "swap", "value": "1"}]],
             "sources": [{"line_no": 20}]},
        ],
    }

    merged = _deduplicate_draws(group)

    assert len(merged) == 1
    assert isinstance(merged[0], DrawCall)
    assert [source["line_no"] for source in merged[0].sources] == [10, 20]
    assert len(merged[0].conditions) == 2


def test_draw_call_ir_rejects_unreviewed_fields():
    with pytest.raises(TypeError, match="unsupported DrawCall field"):
        _deduplicate_draws({
            "draws": [{"count": 3, "start": 0, "base": 0,
                       "conditions": [], "sources": [],
                       "future_render_state": "unclassified"}],
        })
    draw = DrawCall(count=3, start=0, base=0)
    with pytest.raises(AttributeError):
        draw.future_render_state = "unclassified"
    assert draw.to_dict()["count"] == 3


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

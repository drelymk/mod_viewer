"""Mesh-buffer binding, draw fallback, and index decoding regressions."""

import os
import struct
import tempfile

import pytest

from core.buffer_layout import texcoord_layout
from core.ini_parser import (_scan_sections_for_draws, build_draw_groups,
                             extract_resources,
                             extract_toggle_keys, merge_sections)
from _provenance_support import IB_R16_INI, build_mesh_fixture, geometry_values, write

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


def test_mid_section_ib_reassignment_mesh_builder():
    """End-to-end: build_mesh_result must read each draw's indices from its
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
        meshes, geometry = build_mesh_fixture(groups, tmp)
        assert (len(meshes) == 2), (f"both draws survive as distinct meshes, not merged "
                                 f"(got {len(meshes)})")

        def _verts(entry):
            pos = geometry_values(geometry, entry["pos"])
            return sorted(round(pos[i]) for i in range(0, len(pos), 3))

        vert_sets = sorted(_verts(e) for e in meshes.values())
        assert (vert_sets == [[10, 11, 12], [20, 21, 22]]), (f"each mesh's own vertices come from its own reassigned ib (got {vert_sets})")


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
        meshes, geometry = build_mesh_fixture(groups, tmp)
        assert (len(meshes) == 2), (f"both draws survive as distinct meshes (got {len(meshes)})")

        def _verts(entry):
            pos = geometry_values(geometry, entry["pos"])
            return sorted(round(pos[i]) for i in range(0, len(pos), 3))

        vert_sets = sorted(_verts(e) for e in meshes.values())
        assert (vert_sets == [[0, 1, 2], [50, 60, 70]]), (f"the reassigned draw decodes against its own XBS position buffer, "
              f"not a collapsed/garbage read of the SBS one (got {vert_sets})")


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
        assert ("BodyA" in names), ("the section with an explicit drawindexed still draws")
        assert ("BodyB" not in names), (f"a handling=skip section with NO drawindexed draws nothing at all "
              f"(got groups: {sorted(names)})")
        assert ("BodyC" in names), ("a section with no handling=skip still gets the implicit whole-ib draw")


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


IMPLICIT_DRAW_DIFFUSE_INI = """[TextureOverrideImplicitPosition]
vb0 = ResourceImplicitPosition

[TextureOverrideImplicitBlend]
vb1 = ResourceImplicitBlend

[TextureOverrideImplicitTexcoord]
vb1 = ResourceImplicitTexcoord

[TextureOverrideImplicitA]
ib = ResourceImplicitIB
ps-t0 = ResourceImplicitDiffuse

[ResourceImplicitIB]
filename = implicit.ib
format = DXGI_FORMAT_R32_UINT

[ResourceImplicitPosition]
filename = pos.buf
stride = 40

[ResourceImplicitBlend]
filename = blend.buf
stride = 32

[ResourceImplicitTexcoord]
filename = tc.buf
stride = 20

[ResourceImplicitDiffuse]
filename = implicit.dds
"""


def test_implicit_whole_buffer_draw_keeps_its_diffuse():
    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, "mod.ini", IMPLICIT_DRAW_DIFFUSE_INI)
        secs = merge_sections([path])
        groups = build_draw_groups(secs, extract_resources(secs))
        assert (len(groups) == 1), (f"one draw group built (got {len(groups)})")
        group = groups[0]
        assert (len(group["draws"]) == 1), ("exactly the one synthetic placeholder draw")
        draw = group["draws"][0]
        assert (draw.get("count") is None), ("the placeholder draw has no count -- it's the implicit whole-buffer read")
        assert (draw.get("texture_default_file") == "implicit.dds"), (f"the placeholder draw still resolves the section's own "
              f"ps-t0 diffuse, not None (got {draw.get('texture_default_file')})")


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
        assert (groups and groups[0]["index_size"] == 2), (f"an R16_UINT ib reports 2 bytes per index "
              f"(got {groups[0]['index_size'] if groups else None})")
        for d in groups[0]["draws"]:
            d["count"] = 3
        meshes, geometry = build_mesh_fixture(groups, tmp)
        assert (len(meshes) == 1), (f"the draw builds a mesh (got {len(meshes)})")
        entry = next(iter(meshes.values()))
        pos = geometry_values(geometry, entry["pos"])
        verts = sorted(round(pos[i]) for i in range(0, len(pos), 3))
        assert (verts == [5, 6, 7]), (f"16-bit indices are decoded as 16-bit, not 32-bit (got {verts})")


SIGNED_BASE_INI = """[TextureOverrideBodyBlend]
ib = ResourceBodyIB
vb0 = ResourcePos
vb1 = ResourceTc
drawindexed = 3, 0, -3

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


def test_negative_base_vertex_is_parsed_and_applied():
    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, "mod.ini", SIGNED_BASE_INI)
        open(os.path.join(tmp, "body.ib"), "wb").write(
            struct.pack("<3I", 3, 4, 5))
        with open(os.path.join(tmp, "pos.buf"), "wb") as file:
            for i in range(6):
                file.write(struct.pack("<3f", float(i), float(i), float(i))
                           + b"\0" * 28)
        open(os.path.join(tmp, "tc.buf"), "wb").write(b"\0" * 20 * 6)

        secs = merge_sections([path])
        groups = build_draw_groups(secs, extract_resources(secs))
        assert groups[0]["draws"][0]["base"] == -3
        meshes, geometry = build_mesh_fixture(groups, tmp)
        entry = next(iter(meshes.values()))
        positions = geometry_values(geometry, entry["pos"])
        vertices = sorted(round(positions[i])
                          for i in range(0, len(positions), 3))
        assert vertices == [0, 1, 2]


def test_negative_effective_vertex_index_skips_invalid_draw():
    with tempfile.TemporaryDirectory() as tmp:
        ini = SIGNED_BASE_INI.replace(
            "drawindexed = 3, 0, -3", "drawindexed = 3, 0, -1")
        path = write(tmp, "mod.ini", ini)
        open(os.path.join(tmp, "body.ib"), "wb").write(
            struct.pack("<3I", 0, 1, 2))
        with open(os.path.join(tmp, "pos.buf"), "wb") as file:
            for i in range(3):
                file.write(struct.pack("<3f", float(i), float(i), float(i))
                           + b"\0" * 28)
        open(os.path.join(tmp, "tc.buf"), "wb").write(b"\0" * 20 * 3)

        secs = merge_sections([path])
        groups = build_draw_groups(secs, extract_resources(secs))
        meshes, _geometry = build_mesh_fixture(groups, tmp)
        assert meshes == {}


LOWERCASE_SECTIONS_INI = """[constants]
global persist $swap = 0

[keyswap]
key = x
type = cycle
$swap = 0,1

[textureoverridebodyblend]
ib = resourcebodyib
vb0 = resourcepos
vb1 = resourcetc
drawindexed = 3, 0, 0

[resourcebodyib]
filename = body.ib
format = DXGI_FORMAT_R32_UINT

[resourcepos]
filename = pos.buf
stride = 40

[resourcetc]
filename = tc.buf
stride = 20
"""


def test_section_classification_is_case_insensitive():
    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, "mod.ini", LOWERCASE_SECTIONS_INI)
        secs = merge_sections([path])
        groups = build_draw_groups(secs, extract_resources(secs))
        toggles = extract_toggle_keys(secs)
        assert len(groups) == 1
        assert groups[0]["position_file"] == "pos.buf"
        assert groups[0]["texcoord_file"] == "tc.buf"
        assert list(toggles) == ["keyswap"]


DRAW_CAPABILITIES_INI = """[TextureOverrideBody]
vb3 = ResourceBodyPosition
vb4 = ResourceBodyTexcoord
draw = 3, 1

[ResourceBodyPosition]
filename = position.buf
stride = 8
format = DXGI_FORMAT_R16G16B16A16_FLOAT

[ResourceBodyTexcoord]
filename = texcoord.buf
stride = 8
format = DXGI_FORMAT_R32G32_FLOAT
"""


def test_nonindexed_draw_uses_typed_layouts_and_higher_vb_slots():
    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, "mod.ini", DRAW_CAPABILITIES_INI)
        with open(os.path.join(tmp, "position.buf"), "wb") as stream:
            for values in ((-1, -1, -1, 1), (1, 2, 3, 1),
                           (4, 5, 6, 1), (7, 8, 9, 1)):
                stream.write(struct.pack("<4e", *values))
        with open(os.path.join(tmp, "texcoord.buf"), "wb") as stream:
            for values in ((0, 0), (.1, .2), (.3, .4), (.5, .6)):
                stream.write(struct.pack("<2f", *values))

        sections = merge_sections([path])
        groups = build_draw_groups(sections, extract_resources(sections))
        assert groups[0]["draws"][0].operation == "draw"
        assert groups[0]["draws"][0].position_slot == 3
        assert groups[0]["draws"][0].texcoord_slot == 4

        meshes, geometry = build_mesh_fixture(groups, tmp)
        assert len(meshes) == 1
        entry = next(iter(meshes.values()))
        assert entry["draw"] == [3, 1]
        positions = geometry_values(geometry, entry["pos"])
        assert positions == (1, 2, 3, 4, 5, 6, 7, 8, 9)


def test_credible_legacy_uv_offset_wins_over_ambiguous_later_float_pairs():
    data = bytearray()
    for uv, later_pair in (
            ((0.1, 0.2), (0.0, 0.0)),
            ((0.9, 0.2), (1.0, 0.0)),
            ((0.1, 0.8), (0.0, 1.0)),
            ((0.9, 0.8), (1.0, 1.0))):
        vertex = bytearray(28)
        struct.pack_into("<2f", vertex, 4, *uv)
        struct.pack_into("<2f", vertex, 20, *later_pair)
        data.extend(vertex)

    layout = texcoord_layout(data, 28)

    assert (layout.offset, layout.format) == (4, "float32x2")


def test_broader_uv_offset_remains_supported_when_legacy_offsets_are_invalid():
    data = bytearray()
    for uv in ((0.1, 0.2), (0.9, 0.2), (0.1, 0.8), (0.9, 0.8)):
        vertex = bytearray(16)
        struct.pack_into("<2I", vertex, 0, 0x7FC00000, 0x7FC00000)
        struct.pack_into("<2f", vertex, 8, *uv)
        data.extend(vertex)

    layout = texcoord_layout(data, 16)

    assert (layout.offset, layout.format) == (8, "float32x2")


@pytest.mark.parametrize("runtime_name", ["DRAW_TYPE", "$DRAW_TYPE"])
def test_branch_local_incomplete_draw_is_not_built_as_triangle_geometry(
        runtime_name):
    ini = f"""[TextureOverrideBodyBlend]
handling = skip
if {runtime_name} == 2 || {runtime_name} == 4
vb1 = ResourceBodyTexcoord
vb2 = ResourceBodyBlend
checktextureoverride = ib
elif {runtime_name} == 1
vb0 = ResourceBodyPosition
vb2 = ResourceBodyBlend
draw = 3, 0
endif

[TextureOverrideBodyTexcoord]
vb1 = ResourceBodyTexcoord

[TextureOverrideBodyA]
handling = skip
ib = ResourceBodyIB
drawindexed = 3, 0, 0

[ResourceBodyPosition]
filename = position.buf
stride = 40

[ResourceBodyTexcoord]
filename = texcoord.buf
stride = 20

[ResourceBodyBlend]
filename = blend.buf
stride = 32

[ResourceBodyIB]
filename = body.ib
format = DXGI_FORMAT_R32_UINT
"""
    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, "mod.ini", ini)
        sections = merge_sections([path])
        scanned = _scan_sections_for_draws(sections)
        replay = scanned["TextureOverrideBodyBlend"]["draws"][0]
        assert replay.vertex_resources == {
            0: "ResourceBodyPosition",
            2: "ResourceBodyBlend",
        }

        groups = build_draw_groups(sections, extract_resources(sections))
        assert [group["display_name"] for group in groups] == ["BodyA"]
        assert groups[0]["draws"][0].operation == "drawindexed"


def test_unconditional_incomplete_draw_does_not_borrow_sibling_buffers():
    ini = """[TextureOverrideBodyPosition]
vb0 = ResourceBodyPosition

[TextureOverrideBodyBlend]
handling = skip
vb1 = ResourceBodyBlend
draw = 3, 0

[TextureOverrideBodyTexcoord]
vb1 = ResourceBodyTexcoord

[TextureOverrideBodyA]
handling = skip
ib = ResourceBodyIB
drawindexed = 3, 0, 0

[ResourceBodyPosition]
filename = position.buf
stride = 40

[ResourceBodyTexcoord]
filename = texcoord.buf
stride = 20

[ResourceBodyBlend]
filename = blend.buf
stride = 32

[ResourceBodyIB]
filename = body.ib
format = DXGI_FORMAT_R32_UINT
"""
    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, "mod.ini", ini)
        sections = merge_sections([path])
        groups = build_draw_groups(sections, extract_resources(sections))

        assert [group["display_name"] for group in groups] == ["BodyA"]


AUTO_INDEXED_INI = """[TextureOverrideBody]
ib = ResourceBodyIB
vb0 = ResourceBodyPosition
vb1 = ResourceBodyTexcoord
drawindexed = auto

[ResourceBodyIB]
filename = body.ib
format = DXGI_FORMAT_R16_UINT

[ResourceBodyPosition]
filename = position.buf
stride = 12
format = DXGI_FORMAT_R32G32B32_FLOAT

[ResourceBodyTexcoord]
filename = texcoord.buf
stride = 8
format = DXGI_FORMAT_R32G32_FLOAT
"""


def _write_auto_geometry(tmp, indices=(0, 1, 2, 2, 1, 3)):
    open(os.path.join(tmp, "body.ib"), "wb").write(
        struct.pack(f"<{len(indices)}H", *indices))
    open(os.path.join(tmp, "position.buf"), "wb").write(
        struct.pack("<12f", 0, 0, 0, 1, 0, 0,
                    0, 1, 0, 1, 1, 0))
    open(os.path.join(tmp, "texcoord.buf"), "wb").write(
        struct.pack("<8f", 0, 0, 1, 0, 0, 1, 1, 1))


def test_drawindexed_auto_uses_the_complete_aligned_index_buffer():
    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, "mod.ini", AUTO_INDEXED_INI)
        _write_auto_geometry(tmp)
        sections = merge_sections([path])
        groups = build_draw_groups(sections, extract_resources(sections))
        draw = groups[0]["draws"][0]
        assert draw.auto_count and draw.count is None

        meshes, _geometry = build_mesh_fixture(groups, tmp)
        entry = next(iter(meshes.values()))
        assert entry["drawindexed"] == ["auto"]
        assert entry["idx"]["length"] == 6 * 4


def test_out_of_range_vertex_or_index_range_rejects_whole_draw():
    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, "mod.ini", AUTO_INDEXED_INI.replace(
            "drawindexed = auto", "drawindexed = 4, 4, 0"))
        _write_auto_geometry(tmp)
        sections = merge_sections([path])
        groups = build_draw_groups(sections, extract_resources(sections))
        meshes, _geometry = build_mesh_fixture(groups, tmp)
        assert meshes == {}

        open(os.path.join(tmp, "body.ib"), "wb").write(
            struct.pack("<3H", 0, 1, 8))
        path = write(tmp, "mod.ini", AUTO_INDEXED_INI.replace(
            "drawindexed = auto", "drawindexed = 3, 0, 0"))
        sections = merge_sections([path])
        groups = build_draw_groups(sections, extract_resources(sections))
        meshes, _geometry = build_mesh_fixture(groups, tmp)
        assert meshes == {}


def test_run_expansion_has_a_depth_limit():
    sections = {"TextureOverrideBody": ["run = CommandList0"]}
    for index in range(66):
        sections[f"CommandList{index}"] = [
            f"run = CommandList{index + 1}"]
    sections["CommandList66"] = ["draw = 3, 0"]
    with pytest.raises(ValueError, match="run chain exceeds"):
        _scan_sections_for_draws(sections)

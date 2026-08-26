"""Mesh-buffer binding, draw fallback, and index decoding regressions."""

import os
import struct
import tempfile

from core.ini.parser import (build_draw_groups, extract_resources,
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
ps-t0 = Resource\\GIMI\\Diffuse
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


def test_nonfinite_geometry_drops_only_invalid_triangles():
    ini = """[TextureOverrideBody]
ib = ResourceBodyIB
vb0 = ResourceBodyPosition
vb1 = ResourceBodyTexcoord
drawindexed = 6, 0, 0

[ResourceBodyIB]
filename = body.ib
format = DXGI_FORMAT_R32_UINT

[ResourceBodyPosition]
filename = body.buf
stride = 40

[ResourceBodyTexcoord]
filename = body.texcoord
stride = 20
"""
    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, "mod.ini", ini)
        open(os.path.join(tmp, "body.ib"), "wb").write(
            struct.pack("<6I", 0, 1, 2, 0, 3, 4))
        with open(os.path.join(tmp, "body.buf"), "wb") as file:
            for vertex in ((0., 0., 0.), (1., 0., 0.), (0., 1., 0.),
                           (float("nan"), 0., 0.), (1., 1., 0.)):
                file.write(struct.pack("<3f", *vertex) + b"\0" * 28)
        open(os.path.join(tmp, "body.texcoord"), "wb").write(
            b"\0" * (20 * 5))

        secs = merge_sections([path])
        groups = build_draw_groups(secs, extract_resources(secs))
        meshes, geometry = build_mesh_fixture(groups, tmp)

        assert len(meshes) == 1
        entry = next(iter(meshes.values()))
        positions = geometry_values(geometry, entry["pos"])
        index_ref = entry["idx"]
        index_raw = geometry.data[
            index_ref["offset"]:index_ref["offset"] + index_ref["length"]]
        indices = struct.unpack(f"<{len(index_raw) // 4}I", index_raw)
        assert all(value == value for value in positions)
        assert indices == (0, 1, 2)


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

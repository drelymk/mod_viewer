"""Resource path, runtime-copy, and command-list resolution regressions."""

import os
import struct
import tempfile

from _corpus import sample_mods
from app import mod_loader
from core.ini_parser import (_scan_sections_for_draws, build_draw_groups,
                             extract_resources, merge_sections, parse_sections)
from core.textures import encode_texture_file
from _provenance_support import (build_mesh_fixture, geometry_values, visible,
                                 write)

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
    meshes, _geometry = build_mesh_fixture(groups, tmp)
    return meshes


SAME_IB_VB_STATE_INI = """[TextureOverrideBody]
ib = ResourceBodyIB
vb0 = ResourcePosA
vb1 = ResourceTcA
vb6 = ResourceUnsupported
drawindexed = 3, 0, 0
vb0 = ResourcePosB
vb1 = ResourceTcB
drawindexed = 3, 0, 0
vb0 = null
drawindexed = 3, 0, 0

[ResourceBodyIB]
filename = body.ib
format = DXGI_FORMAT_R32_UINT

[ResourcePosA]
filename = pos-a.buf
stride = 12

[ResourcePosB]
filename = pos-b.buf
stride = 12

[ResourceTcA]
filename = tc-a.buf
stride = 8

[ResourceTcB]
filename = tc-b.buf
stride = 8
"""


def test_same_ib_draws_keep_vb_snapshots_and_null_does_not_inherit():
    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, "mod.ini", SAME_IB_VB_STATE_INI)
        open(os.path.join(tmp, "body.ib"), "wb").write(
            struct.pack("<3I", 0, 1, 2))
        open(os.path.join(tmp, "pos-a.buf"), "wb").write(
            struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0))
        open(os.path.join(tmp, "pos-b.buf"), "wb").write(
            struct.pack("<9f", 10, 0, 0, 11, 0, 0, 10, 1, 0))
        texcoords = struct.pack("<6f", 0, 0, 1, 0, 0, 1)
        open(os.path.join(tmp, "tc-a.buf"), "wb").write(texcoords)
        open(os.path.join(tmp, "tc-b.buf"), "wb").write(texcoords)

        sections = merge_sections([path])
        scanned = _scan_sections_for_draws(sections)["TextureOverrideBody"]
        assert scanned["draws"][0].vertex_resources[6] == (
            "ResourceUnsupported")
        assert scanned["draws"][2].vertex_resources[0] is None

        groups = build_draw_groups(sections, extract_resources(sections))
        assert [draw.position_file for draw in groups[0]["draws"]] == [
            "pos-a.buf", "pos-b.buf", None]
        meshes, geometry = build_mesh_fixture(groups, tmp)

        assert list(meshes) == ["Body-1", "Body-2"]
        first = geometry_values(geometry, meshes["Body-1"]["pos"])
        second = geometry_values(geometry, meshes["Body-2"]["pos"])
        assert sorted(first[::3]) == [0, 0, 1]
        assert sorted(second[::3]) == [10, 10, 11]


def test_root_texture_picker_accepts_windows_case_variation():
    """The two native dialogs may spell the same Windows folder differently."""
    if os.name != "nt":
        return
    from PIL import Image

    with tempfile.TemporaryDirectory(prefix="TexturePickerCase") as tmp:
        path = os.path.join(tmp, "RootDiffuse.png")
        Image.new("RGB", (1, 1), (255, 0, 0)).save(path)

        # Root-level files retain a role-aware key; the differently-cased path
        # must still pass containment validation on case-insensitive Windows.
        result = encode_texture_file(tmp.swapcase(), path)
        assert (not result.get("error")), (f"root-level picked texture accepts equivalent path casing ({result})")
        assert (result.get("tex_key") == "diffuse::RootDiffuse.png"
              and result.get("file") == "RootDiffuse.png"), (f"root-level picked texture keeps role and source path ({result})")


RUNTIME_POSITION_COPY_INI = """[TextureOverrideBodyBlend]
vb0 = ResourceBodyPosition

[TextureOverrideBodyTexcoord]
vb1 = ResourceBodyTexcoord

[TextureOverrideLegsBlend]
vb0 = ResourceLegsPosition

[TextureOverrideLegsTexcoord]
vb1 = ResourceLegsTexcoord

[TextureOverrideLegsA]
ib = ResourceLegsAIB
drawindexed = 3, 0, 0
ib = ResourceBodyAIB
vb0 = ResourceBodyRuntimeSnapshot
vb1 = ResourceBodyTexcoord
drawindexed = 3, 0, 0

[Present]
ResourceBodyPosition = copy ResourceBodyPositionBase
ResourceLegsPosition = copy ResourceLegsPositionBase

[ResourceBodyPosition]
[ResourceLegsPosition]
[ResourceBodyRuntimeSnapshot]

[ResourceBodyPositionBase]
filename = bodyBase.buf
stride = 40

[ResourceLegsPositionBase]
filename = legsBase.buf
stride = 40

[ResourceBodyTexcoord]
filename = bodyTc.buf
stride = 20

[ResourceLegsTexcoord]
filename = legsTc.buf
stride = 20

[ResourceBodyAIB]
filename = bodyA.ib
format = DXGI_FORMAT_R32_UINT

[ResourceLegsAIB]
filename = legsA.ib
format = DXGI_FORMAT_R32_UINT
"""


def test_runtime_position_copy_resolution():
    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, "mod.ini", RUNTIME_POSITION_COPY_INI)
        secs = merge_sections([path])
        groups = build_draw_groups(secs, extract_resources(secs))

        assert (len(groups) == 1), (f"runtime position resources no longer drop the "
                                f"draw group (got {len(groups)})")
        if not groups:
            return
        group = groups[0]
        assert (group["position_file"] == "legsBase.buf"), (f"group position follows the explicit Legs -> LegsBase copy "
              f"(got {group['position_file']})")
        assert (group["draws"][1].get("position_file") == "bodyBase.buf"), (f"reassigned Body draw follows the explicit Body -> BodyBase copy "
              f"(got {group['draws'][1].get('position_file')})")


LL_SKELETON_OUTPUT_INI = """[TextureOverrideBodyBlend]
vb2 = ResourceBodyBlend

[TextureOverrideBodyTexcoord]
vb1 = ResourceBodyTexcoord

[TextureOverrideBodyA]
run = CommandListBodyA

[CommandListBodyA]
ib = ResourceBodyAIB
run = CommandListLLSkeletonSkin_Body
drawindexed = 3, 0, 0

[CommandListLLSkeletonSkin_Body]
cs-t1 = ref ResourceBodyPosition
cs-t2 = ref ResourceBodyBlend
cs-u0 = ref ResourceLLSkelOutput_Body
cs-u0 = null
cs-t1 = null
cs-t2 = null
vb0 = ref ResourceLLSkelOutput_Body

[ResourceBodyPosition]
filename = bodyPosition.buf
stride = 40

[ResourceBodyBlend]
filename = bodyBlend.buf
stride = 32

[ResourceBodyTexcoord]
filename = bodyTexcoord.buf
stride = 24

[ResourceLLSkelOutput_Body]

[ResourceBodyAIB]
filename = bodyA.ib
format = DXGI_FORMAT_R32_UINT
"""


def test_ll_skeleton_compute_output_uses_rest_position():
    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, "mod.ini", LL_SKELETON_OUTPUT_INI)
        secs = merge_sections([path])
        groups = build_draw_groups(secs, extract_resources(secs))

        assert (len(groups) == 1), (f"LL compute-skinned body is retained (got {len(groups)})")
        if not groups:
            return
        group = groups[0]
        assert (group["position_file"] == "bodyPosition.buf"), (f"runtime LL output resolves to its cs-t1 rest position "
              f"(got {group['position_file']})")
        assert (group["texcoord_file"] == "bodyTexcoord.buf"), (f"runtime LL output keeps the sibling texcoord binding "
              f"(got {group['texcoord_file']})")


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


def test_run_inlines_nested_commandlist_draws():
    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, "mod.ini", RUN_CHAIN_INI)
        secs = merge_sections([path])
        groups = build_draw_groups(secs, extract_resources(secs))
        assert (len(groups) == 1), (f"one draw group built (got {len(groups)})")
        draws = groups[0]["draws"]
        assert (len(draws) == 2), (f"the run=-chained drawindexed is inlined alongside the direct one "
              f"(got {len(draws)})")
        by_count = {d["count"]: d for d in draws}
        assert (100 in by_count and 50 in by_count), (f"both the direct and run=-chained draws are present (got {sorted(by_count)})")

        chained = by_count[50]
        assert (visible(chained["conditions"], {"naked": "0", "flag": "0"})), ("chained draw visible when both naked==0 and flag==0")
        assert (not visible(chained["conditions"], {"naked": "1", "flag": "0"})), ("chained draw hidden when the caller's own gate (naked==0) fails")
        assert (not visible(chained["conditions"], {"naked": "0", "flag": "1"})), ("chained draw hidden when the callee's own gate (flag==0) fails")

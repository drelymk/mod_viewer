"""Resource path, runtime-copy, and command-list resolution regressions."""

import os
import struct
import tempfile

from _corpus import sample_mods
from app import mod_loader
from core.ini_parser import build_draw_groups, extract_resources, merge_sections, parse_sections
from core.mesh_builder import encode_texture_file
from _provenance_support import build_mesh_fixture, visible, write

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


def test_resource_path_may_reach_a_sibling_folder():
    """`filename = ..\\resources\\x.buf` is how mods share assets between the
    ini's folder and its neighbours -- it has to resolve."""
    with tempfile.TemporaryDirectory() as tmp:
        mod = os.path.join(tmp, "mod")
        shared = os.path.join(tmp, "shared")
        os.makedirs(mod); os.makedirs(shared)
        open(os.path.join(shared, "pos.buf"), "wb").write(b"\1" * 4096)

        meshes = _traversal_mod(mod, "../shared/pos.buf")
        assert (len(meshes) == 1), (f"a resource one folder above the ini is read (got {list(meshes)})")


def test_absolute_resource_path_blocked():
    """The mod folder is untrusted, downloaded content: a crafted `filename`
    naming an absolute path must not be read."""
    with tempfile.TemporaryDirectory() as outside, tempfile.TemporaryDirectory() as tmp:
        secret = os.path.join(outside, "secret.buf")
        with open(secret, "wb") as f:
            f.write(b"\1" * 4096)

        meshes = _traversal_mod(tmp, secret.replace(os.sep, "/"))
        assert (not meshes), (f"absolute resource path is refused (got {list(meshes)})")


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
        assert (not meshes), (f"a resource far above the mod folder is refused (got {list(meshes)})")


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


def test_toggle_panel_provenance():
    mods = sample_mods(15, seed=11)
    if not mods:
        return
    checked = bad = 0
    for mod in mods:
        payload = mod_loader.load_mod(mod)
        for section, info in (payload.get("controls", {}).get("toggles") or {}).items():
            checked += 1
            ini = info.get("ini")
            if not ini or not os.path.isfile(os.path.join(mod, ini)):
                bad += 1
                continue
            secs = parse_sections(os.path.join(mod, ini))
            if info.get("section") not in secs:
                bad += 1
    print(f"      {checked} toggle sections checked")
    assert (checked > 0), ("real mods produced toggle sections")
    assert (bad == 0), (f"every toggle resolves to a real section in a real file (bad={bad})")


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

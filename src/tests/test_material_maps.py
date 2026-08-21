"""Diffuse, normal, light, and material-map resolution regressions."""

import os
import io
import struct
import tempfile

from core import ini_parser
from core.ini_parser import build_draw_groups, extract_resources, merge_sections, parse_sections
from core.textures import (TEXTURE_TRANSFORMS, encode_texture_file,
                           _reconstruct_normal_z, render_texture_png)
from _provenance_support import (DIFFUSE_NO_REF_INI, build_mesh_fixture,
                                 geometry_values, texture_file, visible, write)

AUXILIARY_MAPS_INI = """[Constants]
global $detail = 0
global $metal = 0

[KeyDetail]
type = cycle
$detail = 0,1

[KeyMetal]
type = cycle
$metal = 0,1

[TextureOverrideBodyBlend]
vb0 = ResourceBodyPosition
vb1 = ResourceBodyTexcoord

[TextureOverrideBodyA]
ib = ResourceBodyAIB
if $detail == 0
Resource\\ZZMI\\NormalMap = ref ResourceNormalA
else
Resource\\ZZMI\\NormalMap = ref ResourceNormalB
endif
Resource\\ZZMI\\LightMap = ref ResourceLight
if $metal == 1
Resource\\ZZMI\\MaterialMap = ref ResourceMaterial
endif
drawindexed = 3, 0, 0

[ResourceBodyPosition]
filename = pos.buf
stride = 40
[ResourceBodyTexcoord]
filename = tc.buf
stride = 20
[ResourceBodyAIB]
filename = body.ib
format = DXGI_FORMAT_R32_UINT
[ResourceNormalA]
filename = normal-a.dds
[ResourceNormalB]
filename = normal-b.dds
[ResourceLight]
filename = light.dds
[ResourceMaterial]
filename = material.dds
"""


def test_authored_auxiliary_material_maps():
    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, "mod.ini", AUXILIARY_MAPS_INI)
        secs = merge_sections([path])
        groups = build_draw_groups(secs, extract_resources(secs))

        assert (len(groups) == 1), (f"auxiliary-map fixture builds (got {len(groups)})")
        if not groups:
            return
        draw = groups[0]["draws"][0]
        normals = draw.get("normal_map_variants") or []
        assert ([v["file"] for v in normals] == ["normal-a.dds", "normal-b.dds"]), (f"conditional normal maps retain both authored branches (got {normals})")
        assert (draw.get("light_map_default_file") == "light.dds"), (f"unconditional light map becomes the draw default "
              f"(got {draw.get('light_map_default_file')})")
        materials = draw.get("material_map_variants") or []
        assert (len(materials) == 1 and materials[0]["file"] == "material.dds"
              and materials[0]["conditions"]), (f"a conditional-only material map retains a no-map fallback "
              f"(got {materials})")


def test_direct_ps_t_auxiliary_material_maps():
    direct = AUXILIARY_MAPS_INI
    for old, new in (
            ("ResourceNormalA", "ResourceBodyNormalMapA"),
            ("ResourceNormalB", "ResourceBodyNormalMapB"),
            ("ResourceLight", "ResourceBodyLightMap"),
            ("ResourceMaterial", "ResourceBodyMaterialMap")):
        direct = direct.replace(old, new)
    direct = direct.replace(
        r"Resource\ZZMI\NormalMap = ref ", "ps-t1 = ")
    direct = direct.replace(
        r"Resource\ZZMI\LightMap = ref ", "ps-t2 = ")
    direct = direct.replace(
        r"Resource\ZZMI\MaterialMap = ref ", "ps-t3 = ")

    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, "mod.ini", direct)
        secs = merge_sections([path])
        groups = build_draw_groups(secs, extract_resources(secs))

        assert (len(groups) == 1), (f"direct ps-t auxiliary fixture builds (got {len(groups)})")
        if not groups:
            return
        draw = groups[0]["draws"][0]
        normals = draw.get("normal_map_variants") or []
        assert ([v["file"] for v in normals] ==
              ["normal-a.dds", "normal-b.dds"]), (f"direct ps-t normal maps retain both branches (got {normals})")
        assert (draw.get("light_map_default_file") == "light.dds"), (f"direct ps-t light map becomes the draw default "
              f"(got {draw.get('light_map_default_file')})")
        materials = draw.get("material_map_variants") or []
        assert (len(materials) == 1 and materials[0]["file"] == "material.dds"
              and materials[0]["conditions"]), (f"direct ps-t material map retains its condition (got {materials})")


def test_two_channel_normal_reconstructs_z():
    from PIL import Image
    source = Image.new("RGB", (2, 1))
    source.putdata([(128, 128, 0), (255, 128, 0)])
    rebuilt = _reconstruct_normal_z(source)
    pixels = [rebuilt.getpixel((x, 0)) for x in range(2)]
    assert (pixels[0][2] == 255), (f"a flat XY normal reconstructs a forward-facing Z (got {pixels[0]})")
    assert (127 <= pixels[1][2] <= 129), (f"a full-strength X normal reconstructs a near-zero Z (got {pixels[1]})")


def test_packed_light_map_passthrough_preserves_authored_rgb():
    from PIL import Image
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "packed.png")
        source = Image.new("RGB", (1, 1), (210, 12, 94))
        source.save(path)
        packed = Image.open(io.BytesIO(render_texture_png(
            path, texture_role="light_map")))

    assert packed.mode == "RGBA"
    assert packed.getpixel((0, 0)) == (210, 12, 94, 255)


def test_texture_transforms_only_keep_live_production_recipes():
    assert TEXTURE_TRANSFORMS == ("passthrough", "normal_xy_reconstruct")


def test_packed_passthrough_preserves_rgba():
    from PIL import Image
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "packed-rgba.png")
        Image.new("RGBA", (1, 1), (10, 20, 30, 40)).save(path)
        diffuse = Image.open(io.BytesIO(render_texture_png(
            path, texture_role="diffuse", texture_transform="passthrough")))
        packed = Image.open(io.BytesIO(render_texture_png(
            path, texture_role="light_map", texture_transform="passthrough")))
        diffuse.load()
        packed.load()

    assert diffuse.mode == "RGB"
    assert diffuse.getpixel((0, 0)) == (10, 20, 30)
    assert packed.mode == "RGBA"
    assert packed.getpixel((0, 0)) == (10, 20, 30, 40)


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
        assert (len(groups) == 1), (f"one draw group built (got {len(groups)})")
        group = groups[0]
        assert (group["diffuse_file"] == "diffuseA.dds"), (f"group default diffuse is the first-seen, unconditional one "
              f"(got {group['diffuse_file']})")
        draws = {d["count"]: d for d in group["draws"]}

        assert ("texture_variants" not in draws[10]), ("the earlier unconditional diffuse assignment doesn't leak a "
              "spurious single-entry texture_variants onto the first draw")

        variants = draws[20].get("texture_variants")
        assert (bool(variants) and len(variants) == 2), (f"exactly 2 variants for the later, toggle-gated draw (got {variants})")
        by_file = {v["file"]: v["conditions"] for v in variants}
        assert (set(by_file) == {"diffuseB.dds", "diffuseC.dds"}), (f"both branches' files are present (got {sorted(by_file)})")
        assert (visible(by_file["diffuseB.dds"], {"seven2": "1"}) and
              not visible(by_file["diffuseB.dds"], {"seven2": "0"})), ("diffuseB's condition matches only seven2==1")
        assert (visible(by_file["diffuseC.dds"], {"seven2": "0"}) and
              not visible(by_file["diffuseC.dds"], {"seven2": "1"})), ("diffuseC's condition (the else branch) matches only seven2==0")


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
        meshes, _geometry = build_mesh_fixture(groups, tmp)
        by_draw = {tuple(e["drawindexed"]): e for e in meshes.values()}
        first  = by_draw[(3, 0, 0)]
        second = by_draw[(3, 3, 0)]

        assert ("texture_variants" not in first), ("first draw's payload entry carries no texture_variants (single diffuse)")

        variants = second.get("texture_variants")
        assert (bool(variants) and len(variants) == 3 and
              variants[0]["conditions"] == []), (f"second draw carries the unconditional write and both resolved "
              f"conditional writes in source order (got {variants})")
        keys = {texture_file(v["tex_key"]) for v in variants}
        assert (keys == {"diffuseA.dds", "diffuseB.dds", "diffuseC.dds"}), (f"each assignment's tex_key names its own resolved diffuse file (got {keys})")
        assert (texture_file(second["tex_key"]) == "diffuseB.dds"), (f"the draw's own default tex_key is the first/`if`-branch "
              f"alternative at this point in execution order (seven2==1 -> "
              f"diffuseB), not the group's earlier unconditional diffuseA "
              f"(got {second['tex_key']})")


SAME_VAR_PARTIAL_DIFFUSE_INI = """[Constants]
global persist $color = 0

[KeyColor]
key = c
type = cycle
$color = 0,1,2

[TextureOverrideBodyPosition]
vb0 = ResourceBodyPosition

[TextureOverrideBodyTexcoord]
vb1 = ResourceBodyTexcoord

[TextureOverrideBody]
ib = ResourceBodyIB
Resource\\GIMI\\Diffuse = ref ResourceDiffuseA
if $color == 1
Resource\\GIMI\\Diffuse = ref ResourceDiffuseB
endif
if $color == 2
Resource\\GIMI\\Diffuse = ref ResourceDiffuseC
endif
drawindexed = 3, 0, 0

[ResourceBodyIB]
filename = body.ib
[ResourceBodyPosition]
filename = pos.buf
stride = 40
[ResourceBodyTexcoord]
filename = tc.buf
stride = 20
[ResourceDiffuseA]
filename = diffuseA.dds
[ResourceDiffuseB]
filename = diffuseB.dds
[ResourceDiffuseC]
filename = diffuseC.dds
"""


def test_same_variable_partial_diffuse_chains_keep_assignment_history():
    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, "mod.ini", SAME_VAR_PARTIAL_DIFFUSE_INI)
        secs = merge_sections([path])
        draw = build_draw_groups(secs, extract_resources(secs))[0]["draws"][0]
        assignments = draw.get("texture_assignments") or []
        assert ([item["file"] for item in assignments] ==
              ["diffuseA.dds", "diffuseB.dds", "diffuseC.dds"]), (f"independent same-variable writes retain source order (got {assignments})")

        def selected(value):
            state = {"color": value}
            return next((item["file"] for item in reversed(assignments)
                         if visible(item["conditions"], state)), None)

        assert ([selected(value) for value in ("0", "1", "2")] ==
              ["diffuseA.dds", "diffuseB.dds", "diffuseC.dds"]), ("last-matching assignment selects the authored texture for every color")


MULTI_REASSIGN_INI = """[KeySuitCL]
key = l
type = cycle
$SuitCL = 0,1

[TextureOverrideMultiPosition]
vb0 = ResourceMultiPosition

[TextureOverrideMultiBlend]
vb1 = ResourceMultiBlend

[TextureOverrideMultiTexcoord]
vb1 = ResourceMultiTexcoord

[TextureOverrideMultiA]
ib = ResourceMultiIB
drawindexed = 10, 0, 0
if $SuitCL == 0
Resource\\ZZMI\\Diffuse = ref ResourceDiffuseA
elif $SuitCL == 1
Resource\\ZZMI\\Diffuse = ref ResourceDiffuseA2
endif
drawindexed = 20, 10, 0
Resource\\ZZMI\\Diffuse = ref ResourceDiffuseB
drawindexed = 30, 30, 0
Resource\\ZZMI\\Diffuse = ref ResourceDiffuseA
drawindexed = 40, 60, 0

[ResourceMultiIB]
filename = multi.ib
format = DXGI_FORMAT_R32_UINT

[ResourceMultiPosition]
filename = pos.buf
stride = 40

[ResourceMultiBlend]
filename = blend.buf
stride = 32

[ResourceMultiTexcoord]
filename = tc.buf
stride = 20

[ResourceDiffuseA]
filename = diffuseA.dds

[ResourceDiffuseA2]
filename = diffuseA2.dds

[ResourceDiffuseB]
filename = diffuseB.dds
"""


def test_multi_reassignment_diffuse_resolution():
    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, "mod.ini", MULTI_REASSIGN_INI)
        secs = merge_sections([path])
        groups = build_draw_groups(secs, extract_resources(secs))
        assert (len(groups) == 1), (f"one draw group built (got {len(groups)})")
        group = groups[0]
        by_start = {d["start"]: d for d in group["draws"]}

        assert (by_start[0].get("texture_default_file") is None), (f"draw before any diffuse assignment gets none "
              f"(got {by_start[0].get('texture_default_file')})")
        assert (by_start[10].get("texture_default_file") == "diffuseA.dds"), (f"draw after the if/elif chain's first branch gets diffuseA "
              f"(got {by_start[10].get('texture_default_file')})")
        variants = by_start[10].get("texture_variants")
        assert (bool(variants) and len(variants) == 2), (f"that same draw also carries both toggle alternatives "
              f"(got {variants})")
        assert (by_start[30].get("texture_default_file") == "diffuseB.dds"), (f"draw after the unconditional reassignment to B gets B, not "
              f"the earlier if/elif chain's A "
              f"(got {by_start[30].get('texture_default_file')})")
        assert ("texture_variants" not in by_start[30]), ("the unconditional B reassignment carries no toggle variants")
        assert (by_start[60].get("texture_default_file") == "diffuseA.dds"), (f"draw after reassigning back to A gets A again, not stuck on "
              f"B from the earlier unconditional reassignment "
              f"(got {by_start[60].get('texture_default_file')})")

        pool = [p["res"] for p in group["diffuse_pool_files"]]
        assert (pool == ["ResourceDiffuseA", "ResourceDiffuseA2", "ResourceDiffuseB"]), (f"the group's texture pool lists every distinct diffuse "
              f"referenced anywhere in the section, in first-seen order "
              f"(got {pool})")


def test_multi_reassignment_mesh_builder():
    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, "mod.ini", MULTI_REASSIGN_INI)
        open(os.path.join(tmp, "multi.ib"), "wb").write(
            struct.pack("<12I", *range(12)))
        with open(os.path.join(tmp, "pos.buf"), "wb") as f:
            for i in range(12):
                f.write(struct.pack("<3f", float(i), float(i), float(i)) + b"\0" * 28)
        open(os.path.join(tmp, "tc.buf"), "wb").write(b"\0" * 20 * 12)
        for name in ("diffuseA.dds", "diffuseA2.dds", "diffuseB.dds"):
            open(os.path.join(tmp, name), "wb").write(b"DDS " + name.encode())

        secs = merge_sections([path])
        groups = build_draw_groups(secs, extract_resources(secs))
        # Remap onto small, non-overlapping windows of the 12-index buffer --
        # the ini's own start/count values (0/10/30/60) are execution-order
        # markers only, not real offsets into this tiny fixture buffer.
        orig_starts = [d["start"] for d in groups[0]["draws"]]
        for i, d in enumerate(groups[0]["draws"]):
            d["start"], d["count"] = i * 3, 3
        meshes, _geometry = build_mesh_fixture(groups, tmp)
        by_orig_start = dict(zip(orig_starts,
            sorted(meshes.values(), key=lambda e: e["drawindexed"][1])))

        assert (by_orig_start[0]["tex_key"] is None), (f"first draw's resolved tex_key is None (got {by_orig_start[0]['tex_key']})")
        assert (texture_file(by_orig_start[10]["tex_key"]) == "diffuseA.dds"), (f"second draw resolves diffuseA (got {by_orig_start[10]['tex_key']})")
        assert (texture_file(by_orig_start[30]["tex_key"]) == "diffuseB.dds"), (f"third draw resolves diffuseB, not the earlier diffuseA "
              f"(got {by_orig_start[30]['tex_key']})")
        assert (texture_file(by_orig_start[60]["tex_key"]) == "diffuseA.dds"), (f"fourth draw resolves back to diffuseA "
              f"(got {by_orig_start[60]['tex_key']})")

        options = by_orig_start[0].get("texture_options")
        assert (bool(options) and len(options) == 3), (f"every draw in the component carries the full 3-entry "
              f"texture pool for the manual picker (got {options})")


def test_diffuse_assignment_without_ref_keyword():
    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, "mod.ini", DIFFUSE_NO_REF_INI)
        secs = merge_sections([path])
        groups = build_draw_groups(secs, extract_resources(secs))
        names = {g["display_name"]: g for g in groups}
        assert ("XA" in names), (f"the draw section builds a group (got {sorted(names)})")
        if "XA" in names:
            assert (names["XA"]["diffuse_file"] == "diffuseX.dds"), (f"the no-\"ref\" diffuse assignment still resolves "
                  f"(got {names['XA']['diffuse_file']})")

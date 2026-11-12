"""Synthetic stock merger declarations, independent of any captured model."""

import re


def stock_merged_skeleton_ini():
    return r"""[Constants]
global $epoch = 0
global $enabled = 1
global persist $mode = 0
[Present]
run = CommandListPublish
[CommandListPublish]
if $epoch
$epoch = 0
else
$epoch = 1
endif
ResourcePoseA = copy ResourcePoseARW
ResourcePoseB = copy ResourcePoseBRW
[CommandListCollect]
$\WWMIv1\custom_mesh_scale = 1.0
cs-cb8 = ref vs-cb4
cs-u6 = ResourcePoseARW
run = CustomShader\WWMIv1\SkeletonMerger
cs-cb8 = ref vs-cb3
cs-u6 = ResourcePoseBRW
run = CustomShader\WWMIv1\SkeletonMerger
[CommandListBind]
ResourceSaved = ref vb0
ib = ResourceIndices
vb0 = ResourcePoints
vb1 = ResourceVectors
vb2 = ResourceUv
vb3 = ResourceColors
vb4 = ResourceJoints
if vs-cb3 == 1234.5
vs-cb3 = ResourcePoseB
endif
if vs-cb4 == 1234.5
vs-cb4 = ResourcePoseA
endif
[CommandListChecks]
CheckTextureOverride = ps-t0
CheckTextureOverride = vs-cb3
CheckTextureOverride = vs-cb4
[CommandListCleanup]
vb0 = ref ResourceSaved
[TextureOverrideMarker]
hash = 20202020
filter_index = 1234.5
[TextureOverridePart01]
hash = 10101010
match_first_index = 12
match_index_count = 6
if $enabled
local $seen01
if $seen01 != $epoch
$seen01 = $epoch
$\WWMIv1\vg_offset = 0
$\WWMIv1\vg_count = 2
run = CommandListCollect
endif
if ResourcePoseA !== null
handling = skip
run = CommandListChecks
run = CommandListBind
ps-t17 = ResourceEffect
$\RabbitFX\brightness = 2
run = CommandList\RabbitFX\Run
drawindexed = 3, 0, 0
if $mode == 1
drawindexed = 3, 3, 0
endif
run = CommandListCleanup
endif
endif
[TextureOverridePart02]
hash = 10101010
match_first_index = 18
match_index_count = 3
if $enabled
local $seen02
if $seen02 != $epoch
$seen02 = $epoch
$\WWMIv1\vg_offset = 2
$\WWMIv1\vg_count = 1
run = CommandListCollect
endif
if ResourcePoseA !== null
handling = skip
endif
endif
[ResourcePoseA]
[ResourcePoseB]
[ResourcePoseARW]
type = RWBuffer
format = R32G32B32A32_FLOAT
array = 768
[ResourcePoseBRW]
type = RWBuffer
format = R32G32B32A32_FLOAT
array = 768
[ResourceJoints]
type = Buffer
format = DXGI_FORMAT_R8_UINT
stride = 8
filename = mesh.blend
[ResourceIndices]
format = DXGI_FORMAT_R32_UINT
stride = 12
filename = mesh.idx
[ResourcePoints]
format = DXGI_FORMAT_R32G32B32_FLOAT
stride = 12
filename = mesh.pos
[ResourceVectors]
format = DXGI_FORMAT_R8G8B8A8_SNORM
stride = 8
filename = mesh.vec
[ResourceUv]
format = DXGI_FORMAT_R16G16_FLOAT
stride = 16
filename = mesh.uv
[ResourceColors]
format = DXGI_FORMAT_R8G8B8A8_UNORM
stride = 4
filename = mesh.color
[ResourceEffect]
filename = effect.dds
"""


def modern_merged_skeleton_ini(*, remapped=False, influences=4, map_size=512):
    """Render a neutral merge-status template, optionally with one proxy pair."""
    text = stock_merged_skeleton_ini()

    def section(name, body):
        nonlocal text
        pattern = rf"(?ms)^\[{re.escape(name)}\]\n.*?(?=^\[|\Z)"
        text = re.sub(pattern, lambda _match: f"[{name}]\n{body.strip()}\n", text)

    section("Constants", "global $enabled = 1\nglobal persist $mode = 0\nglobal $merge = 0\n"
            "global $status01 = 0\nglobal $status02 = 0\nglobal $vertices = 3")
    section("CommandListCollect", r"""
if $merge == 0
if vs-cb4 == 1234.5
cs-cb8 = ref vs-cb4
cs-u6 = ResourcePoseARW
$\WWMIv1\custom_mesh_scale = 1.0
run = CustomShader\WWMIv1\SkeletonMerger
$merge = 1
endif
endif
if $merge == 1
if vs-cb4 == 1234.5 && vs-cb3 == 1234.5
cs-cb8 = ref vs-cb3
cs-u6 = ResourcePoseBRW
$\WWMIv1\custom_mesh_scale = 1.0
run = CustomShader\WWMIv1\SkeletonMerger
$merge = 2
endif
endif
""")
    section("CommandListPublish", "$status01 = 0\n$status02 = 0\n"
            "ResourcePoseA = copy ResourcePoseARW\nResourcePoseB = copy ResourcePoseBRW\n"
            + ("run = CommandListRemapPose\n" if remapped else ""))
    for number in (1, 2):
        old = (f"local $seen0{number}\nif $seen0{number} != $epoch\n$seen0{number} = $epoch\n"
               + f"$\\WWMIv1\\vg_offset = {0 if number == 1 else 2}\n"
               + f"$\\WWMIv1\\vg_count = {2 if number == 1 else 1}\nrun = CommandListCollect\nendif")
        new = (f"if $status0{number} != 2\n$\\WWMIv1\\vg_offset = {0 if number == 1 else 2}\n"
               + f"$\\WWMIv1\\vg_count = {2 if number == 1 else (255 if remapped else 1)}\n"
               + f"$merge = $status0{number}\nrun = CommandListCollect\n$status0{number} = $merge\nendif")
        text = text.replace(old, new)
    native = ("if vs-cb4 == 1234.5\nvs-cb4 = ref ResourcePoseA\nif vs-cb3 == 1234.5\n"
              "vs-cb3 = ref ResourcePoseB\nendif\nelif vs-cb3 == 1234.5\nvs-cb3 = ref ResourcePoseA\nendif")
    prefix = ("ResourceSaved = ref vb0\nib = ResourceIndices\nvb0 = ResourcePoints\n"
              "vb1 = ResourceVectors\nvb2 = ResourceUv\nvb3 = ResourceColors\n")
    if not remapped:
        section("CommandListBind", prefix + "vb4 = ResourceJoints\n" + native)
        return text
    proxy = native.replace("ResourcePoseA", "ResourceProxyA").replace("ResourcePoseB", "ResourceProxyB")
    section("CommandListBind", prefix + "if ResourceProxyBlend === null\nvb4 = ResourceJoints\n" + native
            + "\nelse\nvb4 = ref ResourceProxyBlend\n" + proxy + "\nendif")
    section("CommandListCleanup", "vb0 = ref ResourceSaved\nif ResourceProxyBlend !== null\n"
            "ResourceProxyBlend = null\nResourceProxyA = null\nResourceProxyB = null\nendif")
    text = text.replace("run = CommandListChecks\nrun = CommandListBind", "ResourceProxyBlend = ref ResourcePartBlend\n"
                        "ResourceProxyA = ref ResourcePartA\nResourceProxyB = ref ResourcePartB\n"
                        "run = CommandListChecks\nrun = CommandListBind")
    text = text.replace("run = CommandListPublish", "run = CommandListInitialize\nrun = CommandListPublish", 1)
    text = text.replace("array = 768", "array = 3072")
    section("ResourceJoints", f"type = Buffer\nformat = DXGI_FORMAT_R8_UINT\nstride = {influences * 2}\nfilename = mesh.blend")
    text += r"""
[ResourceProxyBlend]
[ResourceProxyA]
[ResourceProxyB]
[ResourcePartBlend]
[ResourcePartA]
[ResourcePartB]
[ResourceTempA]
[ResourceTempB]
[ResourceTempBlend]
[ResourcePoseCopyA]
[ResourcePoseCopyB]
[ResourceReverse]
type = Buffer
format = R16_UINT
filename = reverse.buf
[ResourceForward]
type = Buffer
format = R16_UINT
filename = forward.buf
[ResourceFullVG]
type = Buffer
format = R16_UINT
filename = full-vg.buf
[ResourceNoStride]
type = Buffer
format = DXGI_FORMAT_R8_UINT
filename = mesh.blend
[CommandListInitialize]
local $ready
if !$ready
ResourceTempA = copy ResourcePoseARW
ResourceTempB = copy ResourcePoseBRW
$\WWMIv1\custom_vertex_count = $vertices
$\WWMIv1\weights_per_vertex_count = INFLUENCES
cs-t34 = ref ResourceReverse
cs-t35 = ref ResourceFullVG
$\WWMIv1\blend_remap_id = 1
$\WWMIv1\blend_remap_bone_count = MAP_SIZE
ResourceTempBlend = copy ResourceNoStride
cs-u4 = ref ResourceTempBlend
run = CustomShader\WWMIv1\BlendRemapper
ResourcePartBlend = copy ResourceTempBlend
ResourcePartBlend = copy_desc ResourceJoints
$ready = 1
endif
[CommandListRemapPose]
ResourcePoseCopyA = copy ResourcePoseARW
ResourcePoseCopyB = copy ResourcePoseBRW
cs-t37 = ResourceForward
$\WWMIv1\blend_remap_id = 1
$\WWMIv1\blend_remap_bone_count = MAP_SIZE
$\WWMIv1\vg_count = 2
cs-t38 = ResourcePoseCopyA
cs-u5 = ResourceTempA
run = CustomShader\WWMIv1\SkeletonRemapper
ResourcePartA = copy ResourceTempA
$\WWMIv1\blend_remap_bone_count = MAP_SIZE
cs-t38 = ResourcePoseCopyB
cs-u5 = ResourceTempB
run = CustomShader\WWMIv1\SkeletonRemapper
ResourcePartB = copy ResourceTempB
"""
    return text.replace("INFLUENCES", str(influences)).replace("MAP_SIZE", str(map_size))

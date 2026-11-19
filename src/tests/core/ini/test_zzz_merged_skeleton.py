"""Merged ZZZ skeleton palette proofs."""

import pytest

from core.ini.draw_groups import build_draw_groups
from core.ini.sections import extract_resources, parse_sections
from core.ini.zzz_merged_skeleton import SKINNING_TEMPLATE
from core.mod_source import DirectoryModSource
from tests.support.model_data import standard_component_resources

_MERGE = """// Two captured palettes, concatenated row by row.
Buffer<float4> t0 : register(t0);
Buffer<float4> t1 : register(t1);
RWBuffer<float4> u0 : register(u0);
[numthreads(64, 1, 1)]
void main(uint3 tid : SV_DispatchThreadID)
{
    uint id = tid.x;
    if (id < 6) { u0[id] = t0[id - 0]; return; }
    if (id < 15) { u0[id] = t1[id - 6]; return; }
}
"""

_INI = """[TextureOverrideComponent01Blend]
hash = 33333333
handling = skip
vb2 = ResourceComponent01Blend
if DRAW_TYPE == 1
ResourcePaletteA = copy vs-t0
endif
[TextureOverrideComponent02Blend]
hash = 44444444
if DRAW_TYPE == 1
ResourcePaletteB = copy vs-t0
endif
[TextureOverrideComponent01A]
hash = 22222222
match_first_index = 0
match_index_count = 3
ib = ResourceComponent01IB
run = CommandListSkin
drawindexed = 3, 0, 0
[CommandListMerge]
if !$merged
cs-t0 = ref ResourcePaletteA
cs-t1 = ref ResourcePaletteB
cs-u0 = ref ResourceMerged
run = CustomShaderMerge
cs-u0 = null
$merged = 1
endif
[CommandListSkin]
run = CommandListMerge
cs-t0 = ref ResourceMerged
cs-t1 = ref ResourceComponent01Position
cs-t2 = ref ResourceComponent01Blend
cs-u0 = ref ResourceSkinned
run = CustomShaderSkin
cs-u0 = null
vb0 = ref ResourceSkinned
[CustomShaderMerge]
cs = shaders/merge.hlsl
dispatch = 1, 1, 1
[CustomShaderSkin]
cs = shaders/skin.hlsl
dispatch = 1, 1, 1
[ResourcePaletteA]
[ResourcePaletteB]
[ResourceMerged]
type = Buffer
[ResourceSkinned]
type = Buffer
[ResourceComponent01Blend]
type = Buffer
stride = 32
filename = blend.buf
[ResourceOtherPosition]
type = Buffer
stride = 40
filename = other.buf
[TextureOverrideComponent01Position]
hash = 11111111
vb0 = ResourceComponent01Position
[TextureOverrideComponent01Texcoord]
hash = 55555555
vb1 = ResourceComponent01Texcoord
""" + standard_component_resources(position_stride=40)


@pytest.mark.parametrize("change,accepted", [
    ("template", True),
    ("read_only_use", True),
    ("edited_kernel", False),
    ("row_gap", False),
    ("unguarded_copy", False),
    ("second_copy", False),
    ("blend_uav", False),
    ("unbound_output", False),
    ("vertex_rebind", False),
    ("kernel_input", True),
    ("shader_parameter", False),
    ("subfolder_files", True),
    ("pre_phase_rebind", False),
    ("pre_phase_binding", False),
    ("merge_macro", False),
    ("merge_helper_function", True),
])
def test_merged_palette_proof_requires_the_exact_template(tmp_path, change, accepted):
    ini, merge, skin = _INI, _MERGE, SKINNING_TEMPLATE
    if change == "read_only_use":
        ini += "[CommandListOther]\nps-t5 = ResourceComponent01Blend\nResourceCopy = copy ResourcePaletteA\n"
    elif change == "edited_kernel":
        skin = skin.replace("int4 row0_indices = indices * 3;", "int4 row0_indices = indices * 4;")
    elif change == "row_gap":
        merge = merge.replace("t1[id - 6]", "t1[id - 9]")
    elif change == "unguarded_copy":
        ini = ini.replace("if DRAW_TYPE == 1\nResourcePaletteB = copy vs-t0\nendif", "ResourcePaletteB = copy vs-t0")
    elif change == "second_copy":
        ini += "[CommandListOther]\nResourcePaletteA = copy vs-t0\n"
    elif change == "blend_uav":
        ini += "[CommandListOther]\ncs-u1 = ResourceComponent01Blend\n"
    elif change == "unbound_output":
        ini = ini.replace("vb0 = ref ResourceSkinned\n", "")
    elif change == "vertex_rebind":
        ini = ini.replace("drawindexed = 3, 0, 0", "vb0 = ResourceComponent01Position\ndrawindexed = 3, 0, 0")
    elif change == "kernel_input":
        ini = ini.replace("cs-t1 = ref ResourceComponent01Position", "cs-t1 = ref ResourceOtherPosition")
    elif change == "shader_parameter":
        ini = ini.replace("cs = shaders/merge.hlsl", "cs = shaders/merge.hlsl\ncs-t3 = ResourceComponent01Blend")
    elif change == "subfolder_files":
        ini = ini.replace("cs-t1 = ref ResourceComponent01Position", "cs-t1 = ref ResourceOtherPosition")
        ini = ini.replace("filename = blend.buf", "filename = meshes\\blend.buf")
        ini = ini.replace("filename = other.buf", "filename = meshes\\other.buf")
    elif change == "pre_phase_rebind":
        ini = ini.replace("run = CommandListSkin\n", "run = CommandListSkin\npre run = CommandListRebind\n")
        ini += "[CommandListRebind]\nvb0 = ResourceComponent01Position\n"
    elif change == "pre_phase_binding":
        ini = ini.replace("run = CustomShaderSkin", "pre cs-t2 = ResourceOtherPosition\nrun = CustomShaderSkin")
    elif change == "merge_macro":
        merge = "#define t1 t0\n" + merge
    elif change == "merge_helper_function":
        # Generated merge shaders may carry helpers that the kernel never calls.
        merge = merge.replace("[numthreads", "float3 Scale(float3 value)\n{\n    return value * 2;\n}\n\n[numthreads")
    (tmp_path / "shaders").mkdir()
    (tmp_path / "shaders" / "merge.hlsl").write_text(merge)
    (tmp_path / "shaders" / "skin.hlsl").write_text(skin)
    (tmp_path / "mod.ini").write_text(ini)
    sections = parse_sections(str(tmp_path / "mod.ini"))
    groups = build_draw_groups(sections, extract_resources(sections), resource_source=DirectoryModSource(tmp_path))
    draw = next(draw for group in groups for draw in group["draws"])
    if accepted:
        assert draw.skinning_merged_layout == (("33333333", 0, 2), ("44444444", 2, 3))
        blend = "meshes/blend.buf" if change == "subfolder_files" else "blend.buf"
        assert (draw.skinning_source.file, draw.skinning_source.encoding) == (blend, "gimi_f32_u32_4")
        assert draw.skinning_resolution["resolution_source"] == "verified_zzz_merged"
        # The draw renders the kernel's output, so its rest shape is the kernel input.
        assert draw.position_file == {"kernel_input": "other.buf", "subfolder_files": "meshes\\other.buf"}.get(
            change, "component01-position.buf")
    else:
        assert draw.skinning_merged_layout == ()

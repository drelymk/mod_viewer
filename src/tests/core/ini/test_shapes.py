"""Shape-slider discovery cases with explicit regression fixtures."""

from .test_menu import _by_slot, sections
from core.ini.menu import extract_menu_toggles
from core.ini.shapes import extract_shape_sliders
from core.ini.state import extract_state_rules
from core.ini.parser import (build_draw_groups, extract_resources,
                             gating_var_names)
from app.mods.controls import build_menu_panel





def test_repeated_full_buffer_shape_blocks_are_discovered():
    text = r"""
[CustomShaderComputeShapes]
x88 = $shape01
cs-t50 = copy ResourceComponent01Position.1
cs-t51 = copy ResourceComponent01Position.2
ResourceComponent01Position = ref cs-u5
Dispatch = 3, 1, 1
x88 = $shape03
cs-t50 = copy ResourceComponent01Position.1
cs-t51 = copy ResourceComponent01Position.3
ResourceComponent01Position = ref cs-u5
Dispatch = 3, 1, 1
[ResourceComponent01Position.1]
stride = 40
filename = Component01Position.buf
[ResourceComponent01Position.2]
stride = 40
filename = Component01Position.shape01.buf
[ResourceComponent01Position.3]
stride = 40
filename = Component01Position.shape02.buf
"""
    sliders = extract_shape_sliders(sections(text), extract_resources(sections(text)))
    by_var = {slider["var"]: slider for slider in sliders}
    assert (sorted(by_var) == ["shape01", "shape03"] and
          by_var["shape01"]["base_file"] == "Component01Position.buf" and
          by_var["shape01"]["shader_base_file"] == "Component01Position.buf" and
          by_var["shape03"]["target_file"] == "Component01Position.shape02.buf"), (f"repeated t50/t51 morph blocks share their authored base (got {sliders})")



def test_writable_u5_mapping_only_applies_to_matching_shape_base():
    text = r"""
[CustomShaderComputeShapes]
cs-u5 = copy ResourceShaderInput
ResourceRuntimePosition = ref cs-u5
x88 = $FirstShape
cs-t50 = copy ResourceShapeBase
cs-t51 = copy ResourceFirstTarget
Dispatch = 3, 1, 1
x88 = $SecondShape
cs-t50 = copy ResourceShapeBase
cs-t51 = copy ResourceSecondTarget
Dispatch = 3, 1, 1
[ResourceShaderInput]
stride = 40
filename = ShaderInput.buf
[ResourceRuntimePosition]
stride = 40
filename = RuntimePosition.buf
[ResourceShapeBase]
stride = 40
filename = ShapeBase.buf
[ResourceFirstTarget]
stride = 40
filename = FirstTarget.buf
[ResourceSecondTarget]
stride = 40
filename = SecondTarget.buf
"""
    secs = sections(text)
    sliders = extract_shape_sliders(secs, extract_resources(secs))
    assert {slider["var"] for slider in sliders} == {
        "FirstShape", "SecondShape"}
    assert all(slider["base_file"] == "ShapeBase.buf"
               and slider["shader_base_file"] == "ShapeBase.buf"
               for slider in sliders)


def test_writable_u5_mapping_validates_runtime_stride_only():
    text = r"""
[CustomShaderComputeShapes]
cs-u5 = copy ResourceOriginalPosition
ResourceRuntimePosition = ref cs-u5
x88 = $FirstShape
cs-t50 = copy ResourceOriginalPosition
cs-t51 = copy ResourceFirstTarget
Dispatch = 3, 1, 1
x88 = $SecondShape
cs-t50 = copy ResourceOriginalPosition
cs-t51 = copy ResourceSecondTarget
Dispatch = 3, 1, 1
[ResourceOriginalPosition]
stride = 20
filename = OriginalPosition.buf
[ResourceRuntimePosition]
stride = 40
filename = RuntimePosition.buf
[ResourceFirstTarget]
stride = 40
filename = FirstTarget.buf
[ResourceSecondTarget]
stride = 40
filename = SecondTarget.buf
"""
    secs = sections(text)
    sliders = extract_shape_sliders(secs, extract_resources(secs))
    assert {slider["var"] for slider in sliders} == {
        "FirstShape", "SecondShape"}
    assert all(slider["base_file"] == "RuntimePosition.buf"
               and slider["shader_base_file"] == "OriginalPosition.buf"
               for slider in sliders)


def test_wwmi_sparse_shape_slider_is_discovered():
    text = r"""
[Constants]
global persist $shape01 = 0
global $shapekey_vertex_offset_batch1 = 43085

[CommandListDrawSlider.Shape01]
x87 = $shape01 * x87

[CommandListSetshape01]
$\WWMIv1\shapekey_id = 161
$\WWMIv1\shapekey_value = $shape01

[CommandListSetupShapeKeysBatch]
cs-t33 = ResourceShapeKeyOffsetBuffer
[CommandListLoadShapeKeysBatch]
cs-t0 = ResourceShapeKeyVertexIdBuffer
cs-t1 = ResourceShapeKeyVertexOffsetBuffer
[CommandListApplyShapeKeys]
cs-t6 = ResourcePositionBuffer

[ResourcePositionBuffer]
stride = 12
filename = Meshes/Position.buf
[ResourceShapeKeyOffsetBuffer]
filename = Meshes/ShapeKeyOffset.buf
[ResourceShapeKeyVertexIdBuffer]
filename = Meshes/ShapeKeyVertexId.buf
[ResourceShapeKeyVertexOffsetBuffer]
filename = Meshes/ShapeKeyVertexOffset.buf
"""
    secs = sections(text)
    sliders = extract_shape_sliders(secs, extract_resources(secs))
    assert (len(sliders) == 1), (f"one WWMI sparse slider is found (got {sliders})")
    slider = sliders[0]
    assert (slider.get("shape_id") == 161 and slider.get("buffer_shape_id") == 162 and
          slider.get("sparse_entry_offset") == 43085 and
          slider.get("vertex_offset_file") == "Meshes/ShapeKeyVertexOffset.buf"), (f"WWMI slider aligns its key ID, batch records, and sparse buffers (got {slider})")
    assert "shader_base_file" not in slider


def test_zzmi_midpoint_pair_sliders_are_discovered():
    text = r"""
[Constants]
global persist $shape05 = 0
global persist $shape06 = 0
[CommandListDrawSlider.shape05]
x87 = 202 / $ww * $shape05
[CommandListDrawSlider.shape06]
x87 = 202 / $ww * $shape06
[CommandListKeys]
cs-t50 = copy ResourceComponent01Base
cs-t51 = copy ResourceComponent01Bigshape05
cs-t52 = copy ResourceComponent01Smallshape05
cs-t53 = copy ResourceComponent01Bigshape06
cs-t54 = copy ResourceComponent01Smallshape06
x88 = $shape05
x89 = $shape06
[ResourceComponent01Base]
stride = 40
filename = Component01.buf
[ResourceComponent01Bigshape05]
stride = 40
filename = Component01Bigshape05.buf
[ResourceComponent01Smallshape05]
stride = 40
filename = Component01Smallshape05.buf
[ResourceComponent01Bigshape06]
stride = 40
filename = Component01Bigshape06.buf
[ResourceComponent01Smallshape06]
stride = 40
filename = Component01Smallshape06.buf
"""
    secs = sections(text)
    sliders = extract_shape_sliders(secs, extract_resources(secs))
    by_var = {slider["var"]: slider for slider in sliders}
    assert (sorted(by_var) == ["shape05", "shape06"]), (f"both multi-target sliders are found (got {sorted(by_var)})")
    assert (by_var["shape05"].get("mode") == "midpoint_pair" and
          by_var["shape05"].get("low_file") == "Component01Smallshape05.buf" and
          by_var["shape05"].get("target_file") == "Component01Bigshape05.buf"), (f"bottom slider links its smaller and bigger buffers (got {by_var['shape05']})")
    assert all("shader_base_file" not in slider
               for slider in by_var.values())


def test_zzmi_midpoint_bindings_do_not_cross_commandlists():
    """x88/x89 are generic ini registers also used by unrelated UI shaders.
    A scalar from another CommandList must never claim a five-buffer shape set."""
    text = r"""
[Constants]
global persist $shape04 = 0
global persist $UIAnim = 0
[CommandListDrawSlider.shape05]
x87 = 202 / $ww * $shape04
[CommandListShapeBuffers]
cs-t50 = copy ResourceComponent01Base
cs-t51 = copy ResourceComponent01Bigshape05
cs-t52 = copy ResourceComponent01Smallshape05
cs-t53 = copy ResourceComponent01Bigshape06
cs-t54 = copy ResourceComponent01Smallshape06
[CommandListUnrelatedUI]
x88 = $UIAnim
x89 = $UIAnim
[ResourceComponent01Base]
stride = 40
filename = Component01.buf
[ResourceComponent01Bigshape05]
stride = 40
filename = Component01Bigshape05.buf
[ResourceComponent01Smallshape05]
stride = 40
filename = Component01Smallshape05.buf
[ResourceComponent01Bigshape06]
stride = 40
filename = Component01Bigshape06.buf
[ResourceComponent01Smallshape06]
stride = 40
filename = Component01Smallshape06.buf
"""
    secs = sections(text)
    sliders = extract_shape_sliders(secs, extract_resources(secs))
    assert (sliders == []), (f"unrelated register writes do not create shape sliders (got {sliders})")

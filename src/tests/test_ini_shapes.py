"""Shape-slider discovery cases with explicit regression fixtures."""

from test_ini_menu import _by_slot, sections
from core.ini_menu import extract_menu_toggles
from core.ini_shapes import extract_shape_sliders
from core.ini_state import extract_state_rules
from core.ini_parser import (build_draw_groups, extract_resources,
                             gating_var_names)
from app.mod_loader import build_menu_panel

def test_compute_shape_slider_is_discovered_and_modelled():
    text = r"""
[Constants]
global persist $currFlat = 0.5

[CustomShaderComputeShapes]
x88 = $currFlat
cs-t50 = copy ResourceBodyPosition.Base
cs-t51 = copy ResourceBodyPosition.Flat

[ResourceBodyPosition.Base]
type = Buffer
stride = 40
filename = BodyPosition.buf

[ResourceBodyPosition.Flat]
type = Buffer
stride = 40
filename = BodyPositionFlat.buf
"""
    secs = sections(text)
    sliders = extract_shape_sliders(secs, extract_resources(secs))
    assert (len(sliders) == 1), (f"one conservative two-buffer shape slider is found (got {sliders})")
    slider = sliders[0]
    assert ((slider["var"], slider["base_file"], slider["target_file"]) ==
          ("currFlat", "BodyPosition.buf", "BodyPositionFlat.buf")), (f"slider links its variable and buffers (got {slider})")

    panel = build_menu_panel({"shape": slider}, {"currFlat": "0.5"})
    entry = panel["shape"]
    assert (entry["kind"] == "shape_slider" and entry["default"] == "0.5"), (f"menu model preserves slider kind and float default (got {entry})")


def test_compute_shape_resource_names_are_case_insensitive():
    text = r"""
[Constants]
global persist $currFlat = 0
[CustomShaderComputeShapes]
x87 = $currFlat
cs-t50 = copy resourcebodyposition.base
cs-t51 = copy RESOURCEBODYPOSITION.FLAT
[ResourceBodyPosition.Base]
stride = 40
filename = BodyPosition.buf
[ResourceBodyPosition.Flat]
stride = 40
filename = BodyPositionFlat.buf
"""
    secs = sections(text)
    sliders = extract_shape_sliders(secs, extract_resources(secs))
    assert (len(sliders) == 1 and
          sliders[0].get("target_file") == "BodyPositionFlat.buf"), (f"mixed-case resource references still resolve shape buffers (got {sliders})")


def test_repeated_full_buffer_shape_blocks_are_discovered():
    text = r"""
[CustomShaderComputeShapes]
x88 = $BoobsSize
cs-t50 = copy ResourceBodyPosition.1
cs-t51 = copy ResourceBodyPosition.2
ResourceBodyPosition = ref cs-u5
Dispatch = 3, 1, 1
x88 = $NippleLength
cs-t50 = copy ResourceBodyPosition.1
cs-t51 = copy ResourceBodyPosition.3
ResourceBodyPosition = ref cs-u5
Dispatch = 3, 1, 1
[ResourceBodyPosition.1]
stride = 40
filename = BodyPosition.buf
[ResourceBodyPosition.2]
stride = 40
filename = BodyPosition.boobs.buf
[ResourceBodyPosition.3]
stride = 40
filename = BodyPosition.nipple.buf
"""
    sliders = extract_shape_sliders(sections(text), extract_resources(sections(text)))
    by_var = {slider["var"]: slider for slider in sliders}
    assert (sorted(by_var) == ["BoobsSize", "NippleLength"] and
          by_var["BoobsSize"]["base_file"] == "BodyPosition.buf" and
          by_var["NippleLength"]["target_file"] == "BodyPosition.nipple.buf"), (f"repeated t50/t51 morph blocks share their authored base (got {sliders})")


def test_inherited_shape_base_and_remapped_midpoint_are_discovered():
    text = r"""
[Constants]
global persist $BoobsSize = 0
global persist $ButtSize = 0
global persist $DickSize = 0
global $remappedDickSize = 0
global persist $AnimSpeed = 0.2

[CommandListDrawSlider.Boobs]
x87 = $BoobsSize * x87
[CommandListDrawSlider.Butt]
x87 = $ButtSize * x87
[CommandListDrawSlider.Dick]
x87 = $DickSize * x87
[CommandListDrawSlider.Anim]
x87 = $AnimSpeed * x87

[CustomShaderComputeShapes]
x88 = $BoobsSize
cs-t50 = copy ResourcePosition.B
cs-t51 = copy ResourcePosition.BOOBS
x88 = $ButtSize
cs-t51 = copy ResourcePosition.BUTT
$remappedDickSize = $DickSize * 2 - 1
x88 = $remappedDickSize * -1
cs-t51 = copy ResourcePosition.PPNE
x88 = $remappedDickSize
cs-t51 = copy ResourcePosition.PPE

[ResourcePosition]
stride = 40
filename = Position.buf
[ResourcePosition.B]
stride = 40
filename = Position.B.buf
[ResourcePosition.BOOBS]
stride = 40
filename = Position.BOOBS.buf
[ResourcePosition.BUTT]
stride = 40
filename = Position.BUTT.buf
[ResourcePosition.PPNE]
stride = 40
filename = Position.PPNE.buf
[ResourcePosition.PPE]
stride = 40
filename = Position.PPE.buf
"""
    secs = sections(text)
    sliders = extract_shape_sliders(secs, extract_resources(secs))
    by_var = {slider["var"]: slider for slider in sliders}
    assert (by_var["BoobsSize"].get("base_file") == "Position.buf" and
          by_var["ButtSize"].get("target_file") == "Position.BUTT.buf"), (f"later t51 blocks inherit the shared t50 base and attach to the runtime buffer ({by_var})")
    assert (by_var["DickSize"].get("mode") == "midpoint_pair" and
          by_var["DickSize"].get("low_file") == "Position.PPNE.buf" and
          by_var["DickSize"].get("target_file") == "Position.PPE.buf"), (f"a -1..1 remapped slider becomes a midpoint pair ({by_var['DickSize']})")
    assert ("remappedDickSize" not in by_var and
          not by_var["AnimSpeed"].get("target_file")), (f"internal remaps stay hidden and animation-only sliders stay controls ({by_var})")


def test_wwmi_sparse_shape_slider_is_discovered():
    text = r"""
[Constants]
global persist $BoobsSize = 0
global $shapekey_vertex_offset_batch1 = 43085

[CommandListDrawSlider.Boobs]
x87 = $BoobsSize * x87

[CommandListSetBoobsSize]
$\WWMIv1\shapekey_id = 161
$\WWMIv1\shapekey_value = $BoobsSize

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


def test_zzmi_midpoint_pair_sliders_are_discovered():
    text = r"""
[Constants]
global persist $Bottom = 0
global persist $Breast = 0
[CommandListDrawSlider.Bottom]
x87 = 202 / $ww * $Bottom
[CommandListDrawSlider.Breast]
x87 = 202 / $ww * $Breast
[CommandListKeys]
cs-t50 = copy ResourceBodyBase
cs-t51 = copy ResourceBodyBigBottom
cs-t52 = copy ResourceBodySmallBottom
cs-t53 = copy ResourceBodyBigBreast
cs-t54 = copy ResourceBodySmallBreast
x88 = $Bottom
x89 = $Breast
[ResourceBodyBase]
stride = 40
filename = Body.buf
[ResourceBodyBigBottom]
stride = 40
filename = BodyBigBottom.buf
[ResourceBodySmallBottom]
stride = 40
filename = BodySmallBottom.buf
[ResourceBodyBigBreast]
stride = 40
filename = BodyBigBreast.buf
[ResourceBodySmallBreast]
stride = 40
filename = BodySmallBreast.buf
"""
    secs = sections(text)
    sliders = extract_shape_sliders(secs, extract_resources(secs))
    by_var = {slider["var"]: slider for slider in sliders}
    assert (sorted(by_var) == ["Bottom", "Breast"]), (f"both multi-target sliders are found (got {sorted(by_var)})")
    assert (by_var["Bottom"].get("mode") == "midpoint_pair" and
          by_var["Bottom"].get("low_file") == "BodySmallBottom.buf" and
          by_var["Bottom"].get("target_file") == "BodyBigBottom.buf"), (f"bottom slider links its smaller and bigger buffers (got {by_var['Bottom']})")


def test_zzmi_midpoint_bindings_do_not_cross_commandlists():
    """x88/x89 are generic ini registers also used by unrelated UI shaders.
    A scalar from another CommandList must never claim a five-buffer shape set."""
    text = r"""
[Constants]
global persist $ActualBottom = 0
global persist $UIAnim = 0
[CommandListDrawSlider.Bottom]
x87 = 202 / $ww * $ActualBottom
[CommandListShapeBuffers]
cs-t50 = copy ResourceBodyBase
cs-t51 = copy ResourceBodyBigBottom
cs-t52 = copy ResourceBodySmallBottom
cs-t53 = copy ResourceBodyBigBreast
cs-t54 = copy ResourceBodySmallBreast
[CommandListUnrelatedUI]
x88 = $UIAnim
x89 = $UIAnim
[ResourceBodyBase]
stride = 40
filename = Body.buf
[ResourceBodyBigBottom]
stride = 40
filename = BodyBigBottom.buf
[ResourceBodySmallBottom]
stride = 40
filename = BodySmallBottom.buf
[ResourceBodyBigBreast]
stride = 40
filename = BodyBigBreast.buf
[ResourceBodySmallBreast]
stride = 40
filename = BodySmallBreast.buf
"""
    secs = sections(text)
    sliders = extract_shape_sliders(secs, extract_resources(secs))
    assert (sliders == []), (f"unrelated register writes do not create shape sliders (got {sliders})")

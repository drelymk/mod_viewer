"""Focused coverage for the conservative fixed-layout compute path."""

import struct

from app.mods.analysis import _attach_sparse_animations, analyze_mod_inis
from app.mods.controls import build_toggle_panel
from core.ini.animations import (_identify_compute_shader,
                                 _compute_condition_is_supported,
                                 compute_animation_control_vars,
                                 discover_animation_clocks,
                                 discover_compute_animations,
                                 discover_wwmi_sparse_animations)
from core.ini.analysis import analyze_ini
from core.geometry.mesh_builder import GeometryBlob, build_mesh_result
from core.ini.dnf import build_bool_alias_map
from core.ini.sections import extract_resources, parse_sections
from core.ini.toggles import extract_toggle_keys, extract_variable_defaults


SHAPE_SHADER = """
struct VertexAttributes { float3 position; float3 normal; float4 tangent; };
RWStructuredBuffer<VertexAttributes> rw_buffer : register(u5);
StructuredBuffer<VertexAttributes> base : register(t50);
StructuredBuffer<VertexAttributes> shapekey : register(t51);
Texture1D<float4> IniParams : register(t120);
#define FREQ IniParams[88].x
[numthreads(64, 1, 1)]
void main(uint3 threadID : SV_DispatchThreadID) {
  uint i = threadID.x;
  VertexAttributes diff;
  diff.position = shapekey[i].position - base[i].position;
  diff.normal = shapekey[i].normal - base[i].normal;
  rw_buffer[i].position += diff.position * (0.5 * (sin(FREQ * 30) + 1));
  rw_buffer[i].normal += diff.normal * (0.5 * (sin(FREQ * 30) + 1));
}
"""

LINEAR_SHAPE_SHADER = """
struct VertexAttributes { float3 position; float3 normal; float4 tangent; };
RWStructuredBuffer<VertexAttributes> rw_buffer : register(u5);
StructuredBuffer<VertexAttributes> base : register(t50);
StructuredBuffer<VertexAttributes> shapekey : register(t51);
Texture1D<float4> IniParams : register(t120);
#define VALUE IniParams[88].x
[numthreads(64, 1, 1)]
void main(uint3 threadID : SV_DispatchThreadID) {
  uint i = threadID.x;
  VertexAttributes diff;
  diff.position = shapekey[i].position - base[i].position;
  diff.normal = shapekey[i].normal - base[i].normal;
  rw_buffer[i].position += diff.position * VALUE;
  rw_buffer[i].normal += diff.normal * VALUE;
}
"""

POSE_SHADER = """
struct VertexAttributes { float3 position; float3 normal; float4 tangent; };
struct BlendAttributes { float4 weights; int4 indicies; };
struct PoseAttributes { float3 S; float3 T; float4 QR; float4 QD; };
RWStructuredBuffer<VertexAttributes> rw_buffer : register(u5);
StructuredBuffer<VertexAttributes> base : register(t50);
StructuredBuffer<BlendAttributes> blend : register(t51);
StructuredBuffer<PoseAttributes> pose : register(t52);
Texture1D<float4> IniParams : register(t120);
#define TIME IniParams[88].x
#define VG_COUNT IniParams[89].x
[numthreads(64, 1, 1)]
void main(uint3 threadID : SV_DispatchThreadID) {
  uint i = threadID.x;
  BlendAttributes b = blend[i];
  VertexAttributes v = base[i];
  float time = frac(TIME);
  PoseAttributes p = pose[b.indicies.x];
  float4 qr = normalize(p.QR);
  float4 qd = p.QD;
  float sign = dot(qr, qr);
  rw_buffer[i].position = v.position * p.S + p.T;
  rw_buffer[i].normal = normalize(v.normal);
}
"""

COLUMBINA_SHADER = """
[numthreads(64, 1, 1)]
void main(uint3 threadID : SV_DispatchThreadID) {
  float4 pos = float4(v.position.x, -v.position.z, v.position.y, 1.0f);
  float4 normal = float4(v.normal.x, -v.normal.z, v.normal.y, 0.0f);
  rw_buffer[i].position = float3(pos_result.x, pos_result.z, -pos_result.y);
  rw_buffer[i].normal = normalize(float3(normal_result.x, normal_result.z,
                                         -normal_result.y));
}
"""


def _sections(root, *, stride=40, pose_bytes=None, shader=True):
    root.mkdir()
    (root / "shape.hlsl").write_text(SHAPE_SHADER if shader else "void main() {}")
    (root / "pose.hlsl").write_text(POSE_SHADER)
    position = bytearray()
    for x in (0., 1., 2.):
        position.extend(struct.pack("<fff", x, 0., 0.))
        position.extend(struct.pack("<fff", 0., 2., 0.))
        position.extend(b"\0" * 16)
    (root / "position.buf").write_bytes(position)
    for name, delta in (("key1.buf", 1.), ("key2.buf", 2.)):
        target = bytearray()
        for x in (0., 1., 2.):
            target.extend(struct.pack("<fff", x + delta, 0., 0.))
            target.extend(struct.pack("<fff", 0., 2. + delta, 0.))
            target.extend(b"\0" * 16)
        (root / name).write_bytes(target)
    (root / "blend.buf").write_bytes(b"".join(
        struct.pack("<4f4i", 1., 0., 0., 0., 0, 0, 0, 0)
        for _ in range(3)))
    pose = b"".join(struct.pack(
        "<3f3f4f4f", 1., 1., 1., 0., 0., 0.,
        0., 0., 0., 1., 0., 0., 0., 0.)
        for _ in range(4))
    (root / "pose.buf").write_bytes(pose if pose_bytes is None else pose[:pose_bytes])
    (root / "texcoord.buf").write_bytes(b"\0" * 60)
    (root / "index.buf").write_bytes(struct.pack("<III", 0, 1, 2))
    text = f"""
[Constants]
global $pause = 0
global $Freq_key = 0
global $Freq_pose = 0
global $Speed = 0.1
global $ShapeLimit = 5.236
global $dt
global $VG_count = 2
global $strat_frame = 0
global $end_frame = 1
global persist $anime_state = 0
global $anime_loop = 0

[Present]
$present_only = 1

[CustomShaderShape]
if $pause == 0
    $Freq_key = $Freq_key + $Speed * $dt
endif
if $Freq_key > $ShapeLimit
    $Freq_key = 0
endif
x88 = $Freq_key
cs-t50 = copy ResourcePosition.2
cs-t51 = copy ResourceKey1
cs = shape.hlsl
cs-u5 = copy ResourcePosition.2
Dispatch = 1, 1, 1
x88 = $Freq_key-0.05236
cs-t51 = copy ResourceKey2
cs = shape.hlsl
ResourcePosition.1 = ref cs-u5
Dispatch = 1, 1, 1
cs-u5 = null

[CustomShaderPose]
if $pause == 0
    $Freq_pose = $Freq_pose + 30 * $dt
endif
if $anime_state == 1
    $Freq_pose = 0
    $anime_state = 0
endif
if $Freq_pose > $end_frame
    $Freq_pose = $strat_frame
endif
x88 = $Freq_pose
x89 = $VG_count
cs-t50 = copy ResourcePosition.1
cs-t51 = copy ResourceBlend
cs-t52 = copy ResourcePose
cs = pose.hlsl
cs-u5 = copy ResourcePosition.1
ResourcePosition = ref cs-u5
Dispatch = 1, 1, 1
cs-u5 = null

[TextureOverrideBody]
vb0 = ResourcePosition
vb1 = ResourceTexcoord
ib = ResourceIB
drawindexed = 3, 0, 0

[ResourcePosition]
[ResourcePosition.1]
[ResourcePosition.2]
stride = {stride}
filename = position.buf
[ResourceKey1]
stride = 40
filename = key1.buf
[ResourceKey2]
stride = 40
filename = key2.buf
[ResourceBlend]
stride = 32
filename = blend.buf
[ResourcePose]
stride = 56
filename = pose.buf
[ResourceTexcoord]
stride = 20
filename = texcoord.buf
[ResourceIB]
format = DXGI_FORMAT_R32_UINT
filename = index.buf
"""
    return parse_sections("fixture.ini", text=text)


def _discover(root, sections):
    return discover_compute_animations(
        sections, extract_resources(sections), mod_dir=str(root),
        ini_path=str(root / "fixture.ini"))


def test_animations_reject_unknown_ordered_alias_conditions():
    sections = parse_sections("fixture.ini", text="""[Constants]
global $fps = 30
global $start = 0
global $end = 1
[CommandListAlias]
$allowed = ($runtime_value > 2)
[Present]
if $allowed
$frame = time * $fps % ($end - $start + 1) + $start // 1
endif
""")
    aliases = build_bool_alias_map(sections)

    assert not _compute_condition_is_supported("$allowed", aliases)
    assert discover_animation_clocks(
        sections, condition_aliases=aliases).clocks == ()


def test_compute_animations_reject_unknown_ordered_alias_guard(tmp_path):
    root = tmp_path / "mod"
    sections = _sections(root)
    sections["CommandListAlias"] = [
        "$allowed = ($runtime_value > 2)"]
    sections["CustomShaderShape"] = [
        "if $allowed", *sections["CustomShaderShape"], "endif"]
    aliases = build_bool_alias_map(sections)

    animations = discover_compute_animations(
        sections, extract_resources(sections), mod_dir=str(root),
        ini_path=str(root / "fixture.ini"), condition_aliases=aliases)

    assert not any(animation.get("shape_passes") for animation in animations)


def _nested_sections(root):
    root.mkdir()
    (root / "shape.hlsl").write_text(SHAPE_SHADER)
    (root / "anim.hlsl").write_text(SHAPE_SHADER)
    for name, delta in (
            ("base.buf", 0.), ("static.buf", 1.), ("anim.buf", 2.)):
        position = bytearray()
        for x in (0., 1., 2.):
            position.extend(struct.pack("<fff", x + delta, 0., 0.))
            position.extend(struct.pack("<fff", 0., 2. + delta, 0.))
            position.extend(b"\0" * 16)
        (root / name).write_bytes(position)
    (root / "texcoord.buf").write_bytes(b"\0" * 60)
    (root / "index.buf").write_bytes(struct.pack("<III", 0, 1, 2))
    return parse_sections("fixture.ini", text="""
[Constants]
global $mode = 0
global $hidden = 0
global $covered = 0
global $Freq = 0
global $Speed = 0.1
global $dt

[CustomShaderParent]
if $mode == 2 && !($hidden || $covered)
cs-u5 = copy ResourcePositionBase
cs-t50 = copy ResourcePositionBase
cs-t51 = copy ResourceStaticShape
cs = shape.hlsl
Dispatch = 1, 1, 1
run = CustomShaderAnim
endif
ResourcePosition = ref cs-u5
cs-u5 = null

[CustomShaderAnim]
$Freq = $Freq + $Speed * $dt
x88 = $Freq
cs-t51 = copy ResourcePositionAnim
cs = anim.hlsl
Dispatch = 1, 1, 1

[TextureOverrideBody]
vb0 = ResourcePosition
vb1 = ResourceTexcoord
ib = ResourceIB
drawindexed = 3, 0, 0

[ResourcePosition]
[ResourcePositionBase]
stride = 40
filename = base.buf
[ResourceStaticShape]
stride = 40
filename = static.buf
[ResourcePositionAnim]
stride = 40
filename = anim.buf
[ResourceTexcoord]
stride = 20
filename = texcoord.buf
[ResourceIB]
format = DXGI_FORMAT_R32_UINT
filename = index.buf
""")


WWMI_ANIMATION_SHADER = """
Texture1D<float4> IniParams : register(t120);
#define ShapeKeyValue IniParams[0].z
RWBuffer<float4> CustomShapeKeyValuesRW : register(u5);
[numthreads(1, 1, 1)]
void main(uint3 id : SV_DispatchThreadID) {
  float shape_key_value = float(ShapeKeyValue);
  float shape_key_anim = 0.5 * (sin(shape_key_value * 30) + 1);
}
"""


def _wwmi_sparse_sections():
    return parse_sections("fixture.ini", text=r"""
[Constants]
global $ChouChaAnim = 0
global $gangChaAnim = 0
global $ChouChaAnimSpeed = 0.25
global $gangChaAnimSpeed = 0.5
global $ChouChaFreq = 0
global $gangChaFreq = 0
global $dt

[Present]
if $object_detected
    if $mod_enabled
        if $ChouChaAnim == 1
            run = CommandListAnimChouCha
        else
            run = CommandListResetChouChaAnim
        endif
        if $gangChaAnim == 1
            run = CommandListAnimgangCha
        else
            run = CommandListResetgangChaAnim
        endif
    endif
endif

[CommandListAnimChouCha]
$ChouChaFreq = $ChouChaFreq + $ChouChaAnimSpeed * $dt
run = CustomShaderChouChaAnim

[CommandListAnimGangCha]
$gangChaFreq = $gangChaFreq + $gangChaAnimSpeed * $dt
run = CustomShaderGangChaAnim

[CommandListResetChouChaAnim]
$\WWMIv1\shapekey_id =
$\WWMIv1\shapekey_value = 0
run = WWMIv1SetShapeKey

[CommandListResetGangChaAnim]
$\WWMIv1\shapekey_id =
$\WWMIv1\shapekey_value = 0
run = WWMIv1SetShapeKey

[CustomShaderChouChaAnim]
cs-u5 = ResourceCustomShapeKeyValuesRW
cs = res/anim.hlsl
x0 = 0
y0 =
z0 = $ChouChaFreq
dispatch = 1, 1, 1

[CustomShaderGangChaAnim]
cs-u5 = ResourceCustomShapeKeyValuesRW
cs = res/anim.hlsl
x0 = 0
y0 =
z0 = $gangChaFreq
dispatch = 1, 1, 1
""")


def _wwmi_static_shapes():
    return [{
        "kind": "shape_slider", "var": f"Shape{shape_id}",
        "base_file": "Meshes/Position.buf", "shape_id": shape_id,
        "buffer_shape_id": shape_id + 1, "sparse_entry_offset": 0,
        "offset_file": "Meshes/ShapeKeyOffset.buf",
        "vertex_id_file": "Meshes/ShapeKeyVertexId.buf",
        "vertex_offset_file": "Meshes/ShapeKeyVertexOffset.buf",
    } for shape_id in range(161, 165)]


def _write_wwmi_offset_table(root, slots):
    offsets = bytearray((max(slots) + 2) * 4)
    for index, slot in enumerate(slots):
        struct.pack_into("<II", offsets, slot * 4, index, index + 1)
    (root / "Meshes").mkdir(parents=True, exist_ok=True)
    (root / "Meshes" / "ShapeKeyOffset.buf").write_bytes(offsets)


def test_wwmi_sparse_animation_discovers_one_two_pass_track(tmp_path):
    (tmp_path / "res").mkdir()
    (tmp_path / "res" / "anim.hlsl").write_text(WWMI_ANIMATION_SHADER)
    _write_wwmi_offset_table(tmp_path, range(162, 168))
    discovered = discover_wwmi_sparse_animations(
        _wwmi_sparse_sections(), _wwmi_static_shapes(), mod_dir=str(tmp_path),
        ini_path=str(tmp_path / "fixture.ini"))
    assert len(discovered) == 1
    animation = discovered[0]
    assert animation["kind"] == "wwmi_sparse"
    assert animation["overlay"] is True
    assert [item["sparse_shape"]["shape_id"]
            for item in animation["shape_passes"]] == [165, 166]
    assert [item["sparse_shape"]["buffer_shape_id"]
            for item in animation["shape_passes"]] == [166, 167]
    assert animation["program"]["external_variables"] == [
        "ChouChaAnim", "ChouChaAnimSpeed", "gangChaAnim",
        "gangChaAnimSpeed"]
    assert compute_animation_control_vars(discovered) == {
        "ChouChaAnim", "ChouChaAnimSpeed", "gangChaAnim",
        "gangChaAnimSpeed",
    }
    assert animation["program"]["initials"] == {
        "ChouChaFreq": 0.0, "ChouChaAnimSpeed": 0.25,
        "gangChaFreq": 0.0, "gangChaAnimSpeed": 0.5,
    }
    groups = [{"position_file": "Meshes/Position.buf", "draws": []}]
    _attach_sparse_animations(groups, discovered)
    assert groups[0]["_compute_animation"] is animation


def test_wwmi_sparse_animation_rejects_missing_slots_or_shader(tmp_path):
    root = tmp_path / "missing-slots"
    (root / "res").mkdir(parents=True)
    (root / "res" / "anim.hlsl").write_text(WWMI_ANIMATION_SHADER)
    _write_wwmi_offset_table(root, range(162, 166))
    assert discover_wwmi_sparse_animations(
        _wwmi_sparse_sections(), _wwmi_static_shapes(), mod_dir=str(root),
        ini_path=str(root / "fixture.ini")) == []

    unsupported = tmp_path / "unsupported-shader"
    (unsupported / "res").mkdir(parents=True)
    (unsupported / "res" / "anim.hlsl").write_text(
        "[numthreads(1, 1, 1)] void main() { float value = z0; }")
    _write_wwmi_offset_table(unsupported, range(162, 168))
    assert discover_wwmi_sparse_animations(
        _wwmi_sparse_sections(), _wwmi_static_shapes(),
        mod_dir=str(unsupported), ini_path=str(unsupported / "fixture.ini")) == []

    missing = tmp_path / "missing-shader"
    missing.mkdir()
    assert discover_wwmi_sparse_animations(
        _wwmi_sparse_sections(), _wwmi_static_shapes(), mod_dir=str(missing),
        ini_path=str(missing / "fixture.ini")) == []


def test_analyze_mod_inis_attaches_sparse_animation_by_base_file(tmp_path):
    root = tmp_path / "analysis"
    root.mkdir()
    (root / "res").mkdir()
    (root / "res" / "anim.hlsl").write_text(WWMI_ANIMATION_SHADER)
    _write_wwmi_offset_table(root, range(162, 168))
    sections = _wwmi_sparse_sections()
    sections["Constants"].extend([
        "global $BoobsSize = 0", "global $NippleSize = 0",
        "global $ShortClo = 0", "global $Pussy = 0",
        "global $shapekey_vertex_offset_batch1 = 0",
    ])
    sections.update({
        "CommandListDrawSlider.Boobs": ["x87 = $BoobsSize * x87"],
        "CommandListDrawSlider.Nipple": ["x87 = $NippleSize * x87"],
        "CommandListDrawSlider.ShortClo": ["x87 = $ShortClo * x87"],
        "CommandListDrawSlider.Pussy": ["x87 = $Pussy * x87"],
        "CommandListDrawSlider.AnimSpeed": [
            "x87 = $ChouChaAnimSpeed * x87"],
        "CommandListDrawSlider.gangSpeed": [
            "x87 = $gangChaAnimSpeed * x87"],
        "CommandListSetBoobs": [
            r"$\WWMIv1\shapekey_id = 161",
            r"$\WWMIv1\shapekey_value = $BoobsSize",
        ],
        "CommandListSetNipple": [
            r"$\WWMIv1\shapekey_id = 162",
            r"$\WWMIv1\shapekey_value = $NippleSize",
        ],
        "CommandListSetShortClo": [
            r"$\WWMIv1\shapekey_id = 163",
            r"$\WWMIv1\shapekey_value = $ShortClo",
        ],
        "CommandListSetPussy": [
            r"$\WWMIv1\shapekey_id = 164",
            r"$\WWMIv1\shapekey_value = $Pussy",
        ],
        "CommandListSetupShapeKeysBatch": [
            "cs-t33 = ResourceShapeKeyOffsetBuffer"],
        "CommandListLoadShapeKeysBatch": [
            "cs-t0 = ResourceShapeKeyVertexIdBuffer",
            "cs-t1 = ResourceShapeKeyVertexOffsetBuffer"],
        "CommandListApplyShapeKeys": ["cs-t6 = ResourcePosition"],
        "TextureOverrideBody": [
            "vb0 = ResourcePosition", "vb1 = ResourceTexcoord",
            "ib = ResourceIB", "drawindexed = 3, 0, 0"],
        "ResourcePosition": ["stride = 12", "filename = Meshes/Position.buf"],
        "ResourceTexcoord": ["stride = 8", "filename = Meshes/Texcoord.buf"],
        "ResourceIB": ["format = DXGI_FORMAT_R32_UINT",
                        "filename = Meshes/Body.ib"],
        "ResourceShapeKeyOffsetBuffer": [
            "filename = Meshes/ShapeKeyOffset.buf"],
        "ResourceShapeKeyVertexIdBuffer": [
            "filename = Meshes/ShapeKeyVertexId.buf"],
        "ResourceShapeKeyVertexOffsetBuffer": [
            "filename = Meshes/ShapeKeyVertexOffset.buf"],
    })
    ini = root / "fixture.ini"
    ini.write_text("\n".join(
        line for name, lines in sections.items()
        for line in [f"[{name}]", *map(str, lines), ""]))

    parsed = analyze_mod_inis([str(ini)], str(root))
    assert len(parsed.groups) == 1
    animation = parsed.groups[0]["_compute_animation"]
    assert animation["kind"] == "wwmi_sparse"
    assert parsed.animation_control_vars == {
        "ChouChaAnim", "ChouChaAnimSpeed", "gangChaAnim",
        "gangChaAnimSpeed",
    }


def test_wwmi_sparse_animation_rejects_nonblank_y0(tmp_path):
    root = tmp_path / "nonblank-y0"
    (root / "res").mkdir(parents=True)
    (root / "res" / "anim.hlsl").write_text(WWMI_ANIMATION_SHADER)
    _write_wwmi_offset_table(root, range(162, 168))
    sections = _wwmi_sparse_sections()
    for section in ("CustomShaderChouChaAnim", "CustomShaderGangChaAnim"):
        y0_index = next(index for index, line in enumerate(sections[section])
                        if line.strip().casefold() == "y0 =")
        sections[section][y0_index] = "y0 = $SomeShape"

    assert discover_wwmi_sparse_animations(
        sections, _wwmi_static_shapes(), mod_dir=str(root),
        ini_path=str(root / "fixture.ini")) == []


def test_nested_compute_animation_uses_only_inherited_child(tmp_path):
    root = tmp_path / "nested"
    sections = _nested_sections(root)
    discovered = _discover(root, sections)

    assert len(discovered) == 1
    animation = discovered[0]
    assert animation["base_file"] == "base.buf"
    assert animation["position_resource"] == "ResourcePosition"
    assert animation["overlay"] is True
    assert animation["conditions"] == [[
        {"var": "mode", "value": "2", "negate": False},
        {"var": "hidden", "value": "0", "negate": False},
        {"var": "covered", "value": "0", "negate": False},
    ]]
    assert [item["target_file"] for item in animation["shape_passes"]] == [
        "anim.buf"]
    assert compute_animation_control_vars([animation]) >= {
        "mode", "hidden", "covered", "Speed",
    }
    program = animation["program"]
    assert program["external_variables"] == ["Speed"]
    assert any(command["op"] == "set"
               and command["variable"] == "Freq"
               for command in program["commands"])
    dispatches = [command for command in program["commands"]
                  if command["op"] == "dispatch"]
    assert len(dispatches) == 1
    assert dispatches[0]["track_id"] == animation["track_id"]

    analysis = analyze_ini(sections, resources=extract_resources(sections))
    _attach_animation(analysis.draw_groups, animation)
    built = build_mesh_result(analysis.draw_groups, str(root))
    payload = next(iter(built.meshes.values()))["animation_geometry"]
    assert payload["kind"] == "gimi_compute"
    assert payload["overlay"] is True
    assert payload["conditions"] == animation["conditions"]


def test_nested_animation_is_fallback_for_supported_parent_chain(tmp_path):
    root = tmp_path / "parent-supported"
    sections = _nested_sections(root)
    sections["CustomShaderParent"] = [
        "x88 = $Freq",
        "cs-u5 = copy ResourcePositionBase",
        "cs-t50 = copy ResourcePositionBase",
        "cs-t51 = copy ResourceStaticShape",
        "cs = shape.hlsl",
        "Dispatch = 1, 1, 1",
        "if $mode == 2",
        "run = CustomShaderAnim",
        "endif",
        "ResourcePosition = ref cs-u5",
        "cs-u5 = null",
    ]

    discovered = _discover(root, sections)

    assert len(discovered) == 1
    assert "overlay" not in discovered[0]
    assert discovered[0]["shape_passes"][0]["target_file"] == "static.buf"
    assert any(command["op"] == "dispatch"
               and command["kind"] == "shape"
               for command in discovered[0]["program"]["commands"])


def test_nested_animation_rejects_mismatched_parent_u5_and_t50(tmp_path):
    root = tmp_path / "mismatched-bindings"
    sections = _nested_sections(root)
    sections["CustomShaderParent"] = [
        line.replace(
            "cs-u5 = copy ResourcePositionBase",
            "cs-u5 = copy ResourcePositionOther")
        for line in sections["CustomShaderParent"]]

    assert not _discover(root, sections)


def test_nested_animation_rejects_unsupported_parent_condition(tmp_path):
    root = tmp_path / "unsupported-condition"
    sections = _nested_sections(root)
    sections["CustomShaderParent"][0] = "if $mode > 1"

    assert not _discover(root, sections)


def test_nested_animation_rejects_multiple_children_for_one_output(tmp_path):
    root = tmp_path / "multiple-children"
    sections = _nested_sections(root)
    sections["CustomShaderAnim2"] = list(sections["CustomShaderAnim"])
    parent = sections["CustomShaderParent"]
    parent.insert(parent.index("run = CustomShaderAnim") + 1,
                  "run = CustomShaderAnim2")

    assert not _discover(root, sections)


def _attach_animation(groups, animation):
    for group in groups:
        if (str(group.get("position_resource", "")).casefold()
                == str(animation.get("position_resource", "")).casefold()):
            group["_compute_animation"] = animation


def test_compute_animation_discovers_bindings_and_dimensions(tmp_path):
    root = tmp_path / "valid"
    sections = _sections(root)
    discovered = _discover(root, sections)
    assert len(discovered) == 1
    animation = discovered[0]
    assert [item["dispatch_vertices"] for item in animation["shape_passes"]] == [64, 64]
    assert "amplitude" not in animation["shape_passes"][0]
    assert "angular_scale" not in animation["shape_passes"][0]
    assert "bias" not in animation["shape_passes"][0]
    assert animation["pose"]["bone_count"] == 2
    assert animation["pose"]["frame_count"] == 2
    assert "base_resource" not in animation
    assert "target_resource" not in animation["shape_passes"][0]
    assert not ({"base_resource", "blend_resource", "resource",
                 "dispatch_vertices"} & set(animation["pose"]))
    assert all("phase_expr" not in item and "dispatch_key" not in item
                for item in animation["shape_passes"])
    assert all(key not in animation["pose"]
               for key in ("phase_expr", "dispatch_key"))
    assert "kind" not in animation
    program = animation["program"]
    assert "id" not in program
    assert "version" not in program
    assert any(command["op"] == "dispatch" for command in program["commands"])
    assert "present_only" not in program["external_variables"]
    assert all("condition" not in command for command in program["commands"])
    assert all("conditions" in command for command in program["commands"]
               if command["op"] == "set")
    assert all(command.get("variable") not in {"dt", "ts"}
               for command in program["commands"])

    bad_shader = tmp_path / "bad-shader"
    bad_sections = _sections(bad_shader, shader=False)
    (bad_shader / "pose.hlsl").write_text("void main() {}")
    assert not _discover(bad_shader, bad_sections)

    bad_stride = tmp_path / "bad-stride"
    bad_stride_sections = _sections(bad_stride, stride=32)
    assert not _discover(bad_stride, bad_stride_sections)

    bad_pose = tmp_path / "bad-pose"
    bad_pose_sections = _sections(bad_pose, pose_bytes=56)
    assert not _discover(bad_pose, bad_pose_sections)

    bad_indices = tmp_path / "bad-indices"
    bad_index_sections = _sections(bad_indices)
    (bad_indices / "blend.buf").write_bytes(b"".join(
        struct.pack("<4f4i", 1., 0., 0., 0., 2, 0, 0, 0)
        for _ in range(3)))
    bad_index_animation = _discover(bad_indices, bad_index_sections)
    assert len(bad_index_animation) == 1
    bad_index_analysis = analyze_ini(
        bad_index_sections, resources=extract_resources(bad_index_sections))
    _attach_animation(bad_index_analysis.draw_groups, bad_index_animation[0])
    bad_index_built = build_mesh_result(
        bad_index_analysis.draw_groups, str(bad_indices))
    assert not any(
        "animation_geometry" in mesh
        for mesh in bad_index_built.meshes.values())

    bad_bone_count = tmp_path / "bad-bone-count"
    bad_count_sections = _sections(bad_bone_count)
    bad_count_sections["Constants"] = [
        line.replace("$VG_count = 2", "$VG_count = 3")
        for line in bad_count_sections["Constants"]]
    assert not _discover(bad_bone_count, bad_count_sections)


def test_key_self_clearing_animation_input_stays_external(tmp_path):
    root = tmp_path / "key-trigger"
    sections = _sections(root)
    sections["Constants"] = [
        line.replace("Freq_pose", "Freq")
        for line in sections["Constants"]]
    sections["CustomShaderPose"] = [
        line.replace("Freq_pose", "Freq")
        for line in sections["CustomShaderPose"]]
    sections["KeyPause"] = [
        "key = p", "type = cycle", "$pause = 0,1",
    ]
    sections["KeyAnime"] = [
        "key = a", "type = cycle", "$anime_state = 0,1",
    ]

    animation = _discover(root, sections)[0]
    external = set(animation["program"]["external_variables"])
    assert {"pause", "anime_state"} <= external
    assert "Freq" not in external

    controls = compute_animation_control_vars([animation])
    panel = build_toggle_panel(
        extract_toggle_keys(sections), extract_variable_defaults(sections),
        controls, mod_dir=None)
    assert set(panel) == {"KeyPause", "KeyAnime"}


def test_compute_animation_reads_shader_metadata():
    adapter = _identify_compute_shader(POSE_SHADER)
    assert adapter["threads"] == 64
    assert adapter["coordinate_variant"] == "standard"


def test_compute_animation_identifies_columbina_basis():
    adapter = _identify_compute_shader(COLUMBINA_SHADER)
    assert adapter["coordinate_variant"] == "columbina_basis"


def test_compute_inputs_follow_compact_draw_order_and_share_pose_blob(tmp_path):
    root = tmp_path / "packed"
    sections = _sections(root)
    resources = extract_resources(sections)
    animation = _discover(root, sections)
    analysis = analyze_ini(sections, resources=resources)
    _attach_animation(analysis.draw_groups, animation[0])
    geometry = GeometryBlob()
    built = build_mesh_result(
        analysis.draw_groups, str(root), geometry=geometry)

    entry = next(iter(built.meshes.values()))
    payload = entry["animation_geometry"]
    assert payload["kind"] == "gimi_compute"
    assert payload["vertex_count"] == 3
    assert len(payload["shape_passes"]) == 2
    raw_normals = geometry.to_bytes()[
        payload["base_normals"]["offset"]:
        payload["base_normals"]["offset"] + payload["base_normals"]["length"]]
    assert struct.unpack_from("<fff", raw_normals, 0) == (0., 2., 0.)
    assert payload["pose"]["frames"]["length"] == 2 * 2 * 56
    assert payload["program_id"] == animation[0]["program_id"]
    assert payload["track_id"] == animation[0]["track_id"]
    assert "operation_id" not in payload
    assert "operation" not in payload
    assert all("phase_expr" not in item for item in payload["shape_passes"])
    assert "dispatch_vertices" not in payload["pose"]
    assert "shape_clock" not in payload
    assert "pose_clock" not in payload


def test_compute_shape_dispatch_keeps_partial_morph_coverage(tmp_path):
    root = tmp_path / "partial-morph"
    sections = _sections(root)
    (root / "shape.hlsl").write_text(
        SHAPE_SHADER.replace("[numthreads(64, 1, 1)]", "[numthreads(1, 1, 1)]"))
    animation = _discover(root, sections)[0]
    analysis = analyze_ini(sections, resources=extract_resources(sections))
    _attach_animation(analysis.draw_groups, animation)
    geometry = GeometryBlob()
    built = build_mesh_result(
        analysis.draw_groups, str(root), geometry=geometry)
    payload = next(iter(built.meshes.values()))["animation_geometry"]
    raw = geometry.to_bytes()
    deltas = raw[
        payload["shape_passes"][0]["deltas"]["offset"]:
        payload["shape_passes"][0]["deltas"]["offset"]
        + payload["shape_passes"][0]["deltas"]["length"]]
    assert struct.unpack_from("<6f", deltas, 0) == (1., 0., 0., 0., 1., 0.)
    assert struct.unpack_from("<6f", deltas, 24) == (0., 0., 0., 0., 0., 0.)


def test_compute_animation_accepts_shape_only_chain(tmp_path):
    root = tmp_path / "shape-only"
    sections = _sections(root)
    sections.pop("CustomShaderPose")
    sections["TextureOverrideBody"] = [
        line.replace("vb0 = ResourcePosition", "vb0 = ResourcePosition.1")
        for line in sections["TextureOverrideBody"]]

    discovered = _discover(root, sections)

    assert len(discovered) == 1
    assert discovered[0]["pose"] is None
    assert len(discovered[0]["shape_passes"]) == 2
    analysis = analyze_ini(sections, resources=extract_resources(sections))
    _attach_animation(analysis.draw_groups, discovered[0])
    built = build_mesh_result(analysis.draw_groups, str(root))
    payload = next(iter(built.meshes.values()))["animation_geometry"]
    assert payload["pose"] is None
    assert len(payload["shape_passes"]) == 2


def _write_lucy_shape_fixture(root, shader, *, authored_slider=False):
    root.mkdir()
    (root / "Shapes.hlsl").write_text(shader)
    vertex_data = bytearray()
    flat_data = bytearray()
    for x in (0., 1., 2.):
        vertex_data.extend(struct.pack("<fff", x, 0., 0.))
        vertex_data.extend(struct.pack("<fff", 0., 2., 0.))
        vertex_data.extend(b"\0" * 16)
        flat_data.extend(struct.pack("<fff", x + 1., 0., 0.))
        flat_data.extend(struct.pack("<fff", 0., 3., 0.))
        flat_data.extend(b"\0" * 16)
    (root / "BodyPosition.buf").write_bytes(vertex_data)
    (root / "BodyPositionFlat.buf").write_bytes(flat_data)
    (root / "BodyTexcoord.buf").write_bytes(b"\0" * 60)
    (root / "Body.ib").write_bytes(struct.pack("<III", 0, 1, 2))
    ini = root / "LucySummer.ini"
    slider_section = ("\n[CommandListDrawSlider.Flat]\n"
                      "x87 = $currFlat * x87\n" if authored_slider else "")
    ini.write_text(f"""
[Constants]
global persist $currFlat = 0.5
{slider_section}

[CustomShaderComputeShapes]
cs = Shapes.hlsl
cs-u5 = copy ResourceBodyPosition.Base
x88 = $currFlat
cs-t50 = copy ResourceBodyPosition.Base
cs-t51 = copy ResourceBodyPosition.Flat
ResourceBodyPosition = ref cs-u5
Dispatch = 1, 1, 1
cs-u5 = null

[TextureOverrideBody]
vb0 = ResourceBodyPosition
vb1 = ResourceBodyTexcoord
ib = ResourceBodyIB
drawindexed = 3, 0, 0

[ResourceBodyPosition]
stride = 40
filename = BodyPosition.buf
[ResourceBodyPosition.Base]
stride = 40
filename = BodyPosition.buf
[ResourceBodyPosition.Flat]
stride = 40
filename = BodyPositionFlat.buf
[ResourceBodyTexcoord]
stride = 20
filename = BodyTexcoord.buf
[ResourceBodyIB]
format = DXGI_FORMAT_R32_UINT
filename = Body.ib
""".strip() + "\n", encoding="utf-8")
    return ini


def test_plain_shape_slider_is_not_claimed_by_compute_animation(tmp_path):
    ini = _write_lucy_shape_fixture(
        tmp_path / "lucy-like", LINEAR_SHAPE_SHADER, authored_slider=True)

    parsed = analyze_mod_inis([str(ini)], str(ini.parent))
    group = parsed.groups[0]
    assert [slider["var"] for slider in group["shape_sliders"]] == [
        "currFlat"]
    assert group["shape_sliders"][0]["authored_slider"] is True
    assert "_compute_animation" not in group

    built = build_mesh_result(parsed.groups, str(ini.parent))
    entry = next(iter(built.meshes.values()))
    assert entry["shape_targets"]
    assert "animation_geometry" not in entry


def test_single_pass_sinusoidal_shape_without_authored_slider_stays_compute(
        tmp_path):
    ini = _write_lucy_shape_fixture(tmp_path / "sinusoidal", SHAPE_SHADER)

    parsed = analyze_mod_inis([str(ini)], str(tmp_path / "sinusoidal"))
    group = parsed.groups[0]
    assert group["shape_sliders"][0]["authored_slider"] is False
    assert group.get("_compute_animation") is not None


def test_compute_animation_is_attached_per_ini_with_duplicate_resources(tmp_path):
    ini_paths = []
    (tmp_path / "pose.hlsl").write_text(POSE_SHADER)
    for name in ("main", "acc1"):
        root = tmp_path / name
        _sections(root)
        ini_path = root / f"{name}.ini"
        ini_path.write_text(
            """
[Constants]
global $pause = 0
global $Freq_pose = 0
global $Speed = 0.1
global $dt
global $VG_count = 2

[CustomShaderPose]
if $pause == 0
    $Freq_pose = $Freq_pose + 30 * $dt
endif
x88 = $Freq_pose
x89 = $VG_count
cs-t50 = copy ResourcePosition
cs-t51 = copy ResourceBlend
cs-t52 = copy ResourcePose
cs = pose.hlsl
cs-u5 = copy ResourcePosition
ResourceOutput = ref cs-u5
Dispatch = 1, 1, 1
cs-u5 = null

[TextureOverrideBody]
vb0 = ResourceOutput
vb1 = ResourceTexcoord
ib = ResourceIB
drawindexed = 3, 0, 0

[ResourcePosition]
stride = 40
filename = position.buf
[ResourceBlend]
stride = 32
filename = blend.buf
[ResourcePose]
stride = 56
filename = pose.buf
[ResourceOutput]
[ResourceTexcoord]
stride = 20
filename = texcoord.buf
[ResourceIB]
format = DXGI_FORMAT_R32_UINT
filename = index.buf
""".strip() + "\n")
        ini_paths.append(ini_path)

    parsed = analyze_mod_inis(ini_paths, tmp_path)

    attached = [group.get("_compute_animation") for group in parsed.groups]
    assert len(attached) == 2
    assert all(attached)
    assert len({item["track_id"] for item in attached}) == 2

"""Focused coverage for the conservative fixed-layout compute path."""

import struct

from app.mods.analysis import analyze_mod_inis
from app.mods.controls import build_toggle_panel
from core.ini.animations import (_identify_compute_shader,
                                 compute_animation_control_vars,
                                 discover_compute_animations)
from core.ini.analysis import analyze_ini
from core.geometry.mesh_builder import GeometryBlob, build_mesh_result
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

"""Focused coverage for the conservative fixed-layout compute path."""

import struct

from core.ini.animations import discover_compute_animations
from core.ini.analysis import analyze_ini
from core.geometry.mesh_builder import GeometryBlob, build_mesh_result
from core.ini.draw_resources import _collect_resource_copy_sources
from core.ini.sections import extract_resources, parse_sections


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
global persist $anime_auto_play = 1
global $anime_loop = 0

[Present]
if $anime_state == 0
    $strat_frame = 0
    $end_frame = 1
elif $anime_state == 1
    $strat_frame = 0
    $end_frame = 1
endif
if $anime_auto_play == 1
    if $anime_state == 0 && $anime_loop > 50
        $anime_state = 1
        $anime_loop = 0
    elif $anime_state == 1 && $anime_loop > 50
        $anime_state = 2
        $anime_loop = 0
    elif $anime_state == 2 && $anime_loop > 1
        $anime_state = 0
        $anime_loop = 0
    endif
endif

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
x88 = $Freq_key - 0.05236
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


def test_compute_animation_requires_verified_shader_and_dimensions(tmp_path):
    root = tmp_path / "valid"
    sections = _sections(root)
    discovered = _discover(root, sections)
    assert len(discovered) == 1
    animation = discovered[0]
    assert [item["dispatch_vertices"] for item in animation["shape_passes"]] == [64, 64]
    assert [item["phase_offset"] for item in animation["shape_passes"]] == [0., -0.05236]
    assert animation["shape_passes"][0]["amplitude"] == 0.5
    assert animation["shape_passes"][0]["angular_scale"] == 30
    assert animation["shape_passes"][0]["bias"] == 0.5
    assert animation["pose"]["bone_count"] == 2
    assert animation["pose"]["frame_count"] == 2
    assert animation["pose_clock"]["rate"] == {
        "kind": "literal", "value": 30.0}
    assert animation["shape_clock"]["wrap_limit"] == {
        "kind": "variable", "variable": "ShapeLimit", "offset": 0}
    assert animation["pose_clock"]["wrap_target"] == {
        "kind": "variable", "variable": "strat_frame", "offset": 0}
    assert animation["pose_clock"]["reset_rules"] == [{
        "conditions": [[{
            "var": "anime_state", "value": "1", "negate": False}]],
        "value": {"kind": "literal", "value": 0.0},
        "clear": {
            "variable": "anime_state",
            "value": {"kind": "literal", "value": 0.0},
        },
    }]
    assert "anime_auto_play" not in repr(animation)

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
    bad_index_built = build_mesh_result(
        bad_index_analysis.draw_groups, str(bad_indices),
        compute_animations=bad_index_animation)
    assert not any(
        "animation_geometry" in mesh
        for mesh in bad_index_built.meshes.values())

    bad_bone_count = tmp_path / "bad-bone-count"
    bad_count_sections = _sections(bad_bone_count)
    bad_count_sections["Constants"] = [
        line.replace("$VG_count = 2", "$VG_count = 3")
        for line in bad_count_sections["Constants"]]
    assert not _discover(bad_bone_count, bad_count_sections)


def test_compute_inputs_follow_compact_draw_order_and_share_pose_blob(tmp_path):
    root = tmp_path / "packed"
    sections = _sections(root)
    resources = extract_resources(sections)
    animation = _discover(root, sections)
    analysis = analyze_ini(sections, resources=resources)
    geometry = GeometryBlob()
    built = build_mesh_result(
        analysis.draw_groups, str(root), geometry=geometry,
        compute_animations=animation)

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


def test_compute_resource_copy_edges_track_uav_slots_and_sections(tmp_path):
    sections = {
        "CustomShaderA": [
            "cs-u5 = copy ResourcePosition.2",
            "ResourcePosition.1 = ref cs-u5",
        ],
        "CustomShaderB": [
            "cs-u5 = copy ResourcePosition.1",
            "ResourcePosition = ref cs-u5",
        ],
        "CustomShaderNull": [
            "cs-u5 = copy ResourceA",
            "cs-u5 = null",
            "ResourceB = ref cs-u5",
        ],
        "OtherSection": ["ResourceC = ref cs-u5"],
    }
    copies = _collect_resource_copy_sources(sections, {})
    assert copies["resourceposition.1"] == ["ResourcePosition.2"]
    assert copies["resourceposition"] == ["ResourcePosition.1"]
    assert "resourceb" not in copies
    assert "resourcec" not in copies


def test_compute_animation_accepts_pose_only_and_nonstandard_thread_width(tmp_path):
    root = tmp_path / "pose-only"
    sections = _sections(root)
    sections.pop("CustomShaderShape")
    (root / "pose.hlsl").write_text(
        POSE_SHADER.replace("numthreads(64", "numthreads(32")
        .replace("frac(TIME)", "TIME - floor(TIME)")
        .replace("normalize(p.QR)", "p.QR / length(p.QR)")
        .replace("rw_buffer", "result_buffer")
        .replace("base", "source_buffer")
        .replace("blend", "weights_buffer")
        .replace("pose", "skeleton_buffer")
        .replace("pos_result", "deformed_position")
        .replace("normal_result", "deformed_normal")
    )
    sections["CustomShaderPose"] = [
        line.replace("ResourcePosition.1", "ResourceA")
             .replace("ResourceBlend", "ResourceWeights")
             .replace("ResourcePose", "ResourceSkeleton")
             .replace("ResourcePosition", "ResourceOutput")
        for line in sections["CustomShaderPose"]
    ]
    sections["Constants"] = [
        line.replace("$VG_count", "$bone_total")
        for line in sections["Constants"]
    ]
    sections["CustomShaderPose"] = [
        line.replace("$VG_count", "$bone_total")
        for line in sections["CustomShaderPose"]
    ]
    sections["CustomShaderPose"] = [
        line.replace("Dispatch = 1, 1, 1", "Dispatch = 10, 1, 1")
        for line in sections["CustomShaderPose"]
    ]
    sections["ResourceA"] = sections.pop("ResourcePosition.2")
    sections["ResourceWeights"] = sections.pop("ResourceBlend")
    sections["ResourceSkeleton"] = sections.pop("ResourcePose")
    sections["ResourceOutput"] = []
    discovered = _discover(root, sections)
    assert len(discovered) == 1
    assert discovered[0]["shape_passes"] == []
    assert discovered[0]["pose"]["dispatch_vertices"] == 320
    assert discovered[0]["pose"]["bone_count"] == 2


def test_compute_animation_uses_shape_kernel_constants(tmp_path):
    root = tmp_path / "shape-constants"
    sections = _sections(root)
    shader = (root / "shape.hlsl").read_text()
    shader = shader.replace("0.5 * (sin(FREQ * 30) + 1)",
                            "0.25 * (sin(FREQ * 12) + 1)")
    (root / "shape.hlsl").write_text(shader)
    animation = _discover(root, sections)[0]
    assert animation["shape_passes"][0]["amplitude"] == 0.25
    assert animation["shape_passes"][0]["angular_scale"] == 12
    assert animation["shape_passes"][0]["bias"] == 0.25


def test_compute_animation_keeps_pose_when_shape_phase_is_unparseable(tmp_path):
    root = tmp_path / "unparseable-shape"
    sections = _sections(root)
    sections["CustomShaderShape"] = [
        "x88 = unsupported_expression" if line.startswith("x88 =") else line
        for line in sections["CustomShaderShape"]
    ]
    animation = _discover(root, sections)[0]
    assert animation["shape_passes"] == []
    assert animation["pose"]["frame_count"] == 2

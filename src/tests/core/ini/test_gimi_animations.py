"""Focused coverage for the conservative fixed-layout compute path."""

import struct

from app.mods.analysis import analyze_mod_inis
from core.ini.animations import (_compile_condition, _identify_compute_shader,
                                 discover_compute_animations)
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


def _discover(root, sections, *, qualified_vars=None):
    return discover_compute_animations(
        sections, extract_resources(sections), mod_dir=str(root),
        ini_path=str(root / "fixture.ini"), qualified_vars=qualified_vars)


def _attach_animation(groups, animation):
    for group in groups:
        if (str(group.get("position_resource", "")).casefold()
                == str(animation.get("position_resource", "")).casefold()):
            group["_compute_animation"] = animation


def test_compute_animation_requires_verified_shader_and_dimensions(tmp_path):
    root = tmp_path / "valid"
    sections = _sections(root)
    discovered = _discover(root, sections)
    assert len(discovered) == 1
    animation = discovered[0]
    assert [item["dispatch_vertices"] for item in animation["shape_passes"]] == [64, 64]
    assert [item["phase_expr"]["kind"] for item in animation["shape_passes"]] == [
        "variable", "binary"]
    assert animation["shape_passes"][0]["amplitude"] == 0.5
    assert animation["shape_passes"][0]["angular_scale"] == 30
    assert animation["shape_passes"][0]["bias"] == 0.5
    assert animation["pose"]["bone_count"] == 2
    assert animation["pose"]["frame_count"] == 2
    assert animation["pose"]["phase_expr"] == {
        "kind": "variable", "variable": "Freq_pose"}
    program = animation["program"]
    assert "id" not in program
    assert "version" not in program
    assert any(command["op"] == "dispatch" for command in program["commands"])
    assert "anime_auto_play" in program["external_variables"]
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


def test_compute_program_keeps_truthy_gates_and_scopes_unknown_qualified_gates(
        tmp_path):
    root = tmp_path / "qualified-gates"
    sections = _sections(root)
    sections["CustomShaderPose"].extend([
        "if $pause",
        "    $anime_loop = $anime_loop + 1",
        "endif",
    ])

    animation = _discover(root, sections)[0]
    assignment = next(
        command for command in animation["program"]["commands"]
        if command.get("variable") == "anime_loop"
        and command.get("condition", {}).get("kind") == "truthy")
    assert assignment["condition"]["kind"] == "truthy"
    assert _compile_condition("$active", {})["kind"] == "truthy"
    assert _compile_condition("$active || $pause", {})["kind"] == "or"
    assert _compile_condition("!$active", {})["kind"] == "not"

    qualified = _sections(tmp_path / "qualified-rejected")
    qualified["CustomShaderPose"].insert(0, r"if $\Odette\Master\swapvar == 4")
    qualified["CustomShaderPose"].append("endif")
    discovered = _discover(tmp_path / "qualified-rejected", qualified)
    assert len(discovered) == 1
    dispatch = next(command for command in discovered[0]["program"]["commands"]
                    if command.get("op") == "dispatch"
                    and command.get("kind") == "pose")
    condition = dispatch["condition"]
    assert condition["kind"] == "compare"
    assert condition["left"]["kind"] == "qualified_unknown"


def test_compute_program_rejects_unverified_expression_operators(tmp_path):
    root = tmp_path / "unsupported-expression"
    sections = _sections(root)
    sections["CustomShaderPose"] = [
        line.replace("$Freq_pose + 30 * $dt", "$Freq_pose + 30 / $dt")
        for line in sections["CustomShaderPose"]]
    assert not _discover(root, sections)


def test_compute_animation_accepts_verified_shader_hash():
    adapter = _identify_compute_shader(POSE_SHADER)
    assert adapter["kind"] == "pose"
    assert adapter["coordinate_variant"] == "standard"
    changed = POSE_SHADER.replace(
        "rw_buffer[i].position = v.position * p.S + p.T;",
        "rw_buffer[i].position = v.position * p.T + p.S;")
    assert _identify_compute_shader(changed) is None


def test_compute_program_preserves_dispatch_guards(tmp_path):
    root = tmp_path / "guarded-dispatch"
    sections = _sections(root)
    lines = sections["CustomShaderPose"]
    dispatch_index = lines.index("Dispatch = 1, 1, 1")
    sections["CustomShaderPose"] = (
        lines[:dispatch_index]
        + ["if $pause == 1", lines[dispatch_index], "endif"]
        + lines[dispatch_index + 1:])

    animation = _discover(root, sections)[0]
    pose_dispatch = next(
        command for command in animation["program"]["commands"]
        if command.get("kind") == "pose")
    assert pose_dispatch["condition"] == {
        "kind": "compare", "op": "==",
        "left": {"kind": "variable", "variable": "pause"},
        "right": {"kind": "literal", "value": 1.0},
    }


def test_compute_program_inlines_reached_command_lists(tmp_path):
    root = tmp_path / "command-list"
    sections = _sections(root)
    assignment = "$Freq_pose = $Freq_pose + 30 * $dt"
    pose_lines = sections["CustomShaderPose"]
    sections["CustomShaderPose"] = [
        "run = CommandListPose" if line == assignment else line
        for line in pose_lines
    ]
    sections["CommandListPose"] = [assignment]

    animation = _discover(root, sections)[0]
    assert any(
        command.get("op") == "set"
        and command.get("variable") == "Freq_pose"
        for command in animation["program"]["commands"])


def test_compute_program_preserves_present_runs_and_known_qualified_gates(
        tmp_path):
    root = tmp_path / "present-program"
    sections = _sections(root)
    sections["Present"] = [
        r"if $\Owner\Master\swapvar == 1",
        "    run = CustomShaderShape",
        "    run = CustomShaderPose",
        "endif",
    ]

    animation = _discover(
        root, sections,
        qualified_vars={r"\Owner\Master\swapvar": "present_enabled"})[0]
    commands = animation["program"]["commands"]
    assert any(
        command.get("op") == "dispatch"
        and command.get("kind") == "pose"
        for command in commands)
    dispatch = next(command for command in commands
                    if command.get("op") == "dispatch"
                    and command.get("kind") == "pose")
    assert dispatch["condition"] == {
        "kind": "compare", "op": "==",
        "left": {"kind": "variable", "variable": "present_enabled"},
        "right": {"kind": "literal", "value": 1.0},
    }


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


def test_compute_animation_keeps_sequential_pose_dispatch_snapshots(tmp_path):
    root = tmp_path / "sequential-pose"
    sections = _sections(root)
    sections["Constants"] = [
        line.replace("$VG_count = 2", "$VG_count = 139")
        for line in sections["Constants"]
    ] + ["global $Acc1_count = 8"]
    sections["ResourcePose"] = [
        line.replace("pose.buf", "main_pose.buf")
        for line in sections["ResourcePose"]
    ]
    sections["ResourceAcc1"] = []
    sections["ResourceAcc1.1"] = [
        "stride = 40",
        "filename = acc1_position.buf",
    ]
    sections["ResourceAcc1Blend"] = [
        "stride = 32",
        "filename = acc1_blend.buf",
    ]
    sections["ResourceAcc1Pose"] = [
        "stride = 56",
        "filename = acc1_pose.buf",
    ]
    sections["CustomShaderPose"].extend([
        "cs-u5 = null",
        "x89 = $Acc1_count",
        "cs-t50 = copy ResourceAcc1.1",
        "cs-t51 = copy ResourceAcc1Blend",
        "cs-t52 = copy ResourceAcc1Pose",
        "cs-u5 = copy ResourceAcc1.1",
        "ResourceAcc1 = ref cs-u5",
        "Dispatch = 1, 1, 1",
        "cs-u5 = null",
    ])
    pose_record = struct.pack(
        "<3f3f4f4f", 1., 1., 1., 0., 0., 0.,
        0., 0., 0., 1., 0., 0., 0., 0.)
    (root / "main_pose.buf").write_bytes(pose_record * (139 * 2))
    (root / "acc1_position.buf").write_bytes(
        (root / "position.buf").read_bytes())
    (root / "acc1_blend.buf").write_bytes(
        (root / "blend.buf").read_bytes())
    (root / "acc1_pose.buf").write_bytes(pose_record * (8 * 2))

    discovered = _discover(root, sections)

    by_output = {item["position_resource"]: item for item in discovered}
    assert set(by_output) == {"ResourcePosition", "ResourceAcc1"}
    assert by_output["ResourcePosition"]["base_file"] == "position.buf"
    assert by_output["ResourcePosition"]["pose"]["bone_count"] == 139
    assert by_output["ResourcePosition"]["pose"]["blend_file"] == "blend.buf"
    assert by_output["ResourcePosition"]["pose"]["file"] == "main_pose.buf"
    assert by_output["ResourceAcc1"]["base_file"] == "acc1_position.buf"
    assert by_output["ResourceAcc1"]["pose"]["bone_count"] == 8
    assert by_output["ResourceAcc1"]["pose"]["blend_file"] == "acc1_blend.buf"
    assert by_output["ResourceAcc1"]["pose"]["file"] == "acc1_pose.buf"
    assert by_output["ResourcePosition"]["program_id"] == \
        by_output["ResourceAcc1"]["program_id"]
    dispatches = by_output["ResourcePosition"]["program"]["commands"]
    operations = [item["track_id"] for item in dispatches
                  if item["op"] == "dispatch"]
    assert by_output["ResourcePosition"]["track_id"] in operations
    assert by_output["ResourceAcc1"]["track_id"] in operations


def test_compute_animation_rejects_multiple_pose_passes_in_one_chain(tmp_path):
    root = tmp_path / "multiple-pose-passes"
    sections = _sections(root)
    sections["CustomShaderPose"].insert(
        sections["CustomShaderPose"].index("cs-u5 = null"),
        "Dispatch = 1, 1, 1")

    assert not _discover(root, sections)


def test_compute_animation_rejects_unsupported_shape_chain(tmp_path):
    root = tmp_path / "unsupported-shape-chain"
    sections = _sections(root)
    (root / "shape.hlsl").write_text("void main() {}")

    assert not _discover(root, sections)

    changed_math = tmp_path / "changed-shape-math"
    changed_sections = _sections(changed_math)
    (changed_math / "shape.hlsl").write_text(
        SHAPE_SHADER.replace("0.5 * (sin", "0.75 * (sin"))
    assert not _discover(changed_math, changed_sections)


def test_compute_animation_rejects_shape_chain_when_phase_is_unparseable(tmp_path):
    root = tmp_path / "unparseable-shape"
    sections = _sections(root)
    sections["CustomShaderShape"] = [
        "x88 = unsupported_expression" if line.startswith("x88 =") else line
        for line in sections["CustomShaderShape"]
    ]
    assert not _discover(root, sections)


def test_compute_animation_preserves_zero_multiplication(tmp_path):
    root = tmp_path / "zero-rate"
    sections = _sections(root)
    sections["CustomShaderPose"] = [
        line.replace("$Freq_pose + 30 * $dt",
                     "$Freq_pose + $Speed * 0")
        for line in sections["CustomShaderPose"]]

    animation = _discover(root, sections)[0]
    assignment = next(command for command in animation["program"]["commands"]
                       if command.get("op") == "set"
                       and command["variable"] == "Freq_pose")
    assert assignment["expression"]["right"] == {
        "kind": "binary", "op": "*",
        "left": {"kind": "variable", "variable": "Speed"},
        "right": {"kind": "literal", "value": 0.0},
    }


def test_compute_animation_rejects_non_linear_dispatch(tmp_path):
    root = tmp_path / "non-linear-dispatch"
    sections = _sections(root)
    sections["CustomShaderPose"] = [
        line.replace("Dispatch = 1, 1, 1", "Dispatch = 1, 2, 1")
        for line in sections["CustomShaderPose"]]
    assert not _discover(root, sections)


def test_compute_animation_preserves_ordered_assignments(tmp_path):
    root = tmp_path / "reset-clear"
    sections = _sections(root)
    sections["CustomShaderPose"] = [
        line.replace("$anime_state = 0", "$unrelated = 0")
        for line in sections["CustomShaderPose"]]

    animation = _discover(root, sections)[0]

    assert any(
        command["op"] == "set" and command["variable"] == "unrelated"
        for command in animation["program"]["commands"])


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

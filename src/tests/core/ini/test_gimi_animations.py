"""Compute animation contracts through the shared mod loader and mesh pipeline."""

import re
import struct
import zipfile

import pytest

from core.geometry.mesh_builder import GeometryBlob
from core.mod_source import DirectoryModSource, ZipModSource
from core.ini.sections import parse_sections
from tests.support.animations import (
    LINEAR_SHAPE_SHADER, POSE_SHADER, SHAPE_SHADER, SWAP_YZ_SHADER,
    compute_mod, load_mod, nested_mod, read_sections, save_sections,
)
from tests.support.provenance import geometry_values


@pytest.mark.parametrize("kind", ["shape-only", "shape-and-pose", "pose-only"])
def test_compute_packing_preserves_draw_order_and_dispatch_coverage(tmp_path, kind):
    ini = compute_mod(tmp_path / "mod")
    sections = read_sections(ini)
    posed = kind != "shape-only"
    if posed:
        (ini.parent / "pose.hlsl").write_text(SWAP_YZ_SHADER, encoding="utf-8")
    else:
        del sections["CustomShaderPose"]
        sections["TextureOverrideComponent01"][0] = "vb0 = ResourcePosition.1"
    if kind == "pose-only":
        del sections["CustomShaderShape"]
        sections["CustomShaderPose"] = [
            line.replace("ResourcePosition.1", "ResourcePosition.2")
            for line in sections["CustomShaderPose"]]
    (ini.parent / "shape.hlsl").write_text(
        SHAPE_SHADER.replace("numthreads(64, 1, 1)", "numthreads(1, 1, 1)"), encoding="utf-8")
    save_sections(ini, sections)
    geometry = GeometryBlob()
    parsed, built = load_mod(ini, ini.parent, geometry=geometry)
    entry = next(iter(built.meshes.values()))
    payload = entry["animation_geometry"]
    assert geometry_values(geometry, entry["pos"]) == (0., 0., 0., 1., 0., 0., 0., 1., 0.)
    assert struct.unpack_from("<3I", geometry.data, entry["idx"]["offset"]) == (2, 0, 1)
    assert payload["vertex_count"] == 3
    deltas = [geometry_values(geometry, item["deltas"]) for item in payload["shape_passes"]]
    assert deltas == ([] if kind == "pose-only" else [
        (1., 0., 0., 0., 1., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0.),
        (2., 0., 0., 0., 2., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0.),
    ])
    if posed:
        assert payload["coordinate_transform"] == "swap_yz_negate"
        assert (payload["pose"]["bone_count"], payload["pose"]["frame_count"]) == (2, 2)
        frames = geometry_values(geometry, payload["pose"]["frames"])
        assert (frames[3], frames[31]) == (.25, .75)
        assert {"pause", "anime_state"} <= parsed.animation_control_vars
        assert {"KeyPause", "KeyAnime"} <= set(parsed.toggles)
    else:
        assert payload["pose"] is None


def test_reload_rechecks_pose_shader_and_keeps_static_geometry_on_failure(tmp_path):
    ini = compute_mod(tmp_path / "mod")
    _, initial = load_mod(ini, ini.parent)
    assert next(iter(initial.meshes.values()))["animation_geometry"]["pose"]
    shader_path = ini.parent / "pose.hlsl"
    # Reload equivalent dataflow despite different names, workgroup sizes and helpers.
    variants = [POSE_SHADER.replace("numthreads(64, 1, 1)", "numthreads(1, 1, 1)"),
                SWAP_YZ_SHADER.replace("1e-6f", "0.000001"),
                "#define UNUSED_HELPER 1\n" + POSE_SHADER]
    renamed = re.sub(
        r"\b(base|blend|pose|rw_buffer|TIME|VG_COUNT|threadID|i|b|v|pos|qr|qd)\b",
        lambda match: "local_" + match.group(0), variants[0])
    variants.append(renamed.split("int i8toi32(uint src) {", 1)[0])
    variants.append(variants[0].replace("int4 indicies", "uint4 indices").replace(
        "b.indicies", "b.indices").replace("int4 idx_", "uint4 idx_"))
    for shader in variants:
        shader_path.write_text(shader, encoding="utf-8")
        _, reloaded = load_mod(ini, ini.parent)
        assert next(iter(reloaded.meshes.values()))["animation_geometry"]["pose"]
    for shader in (
        "[numthreads(64, 1, 1)] void main(uint3 id : SV_DispatchThreadID) {}",
        POSE_SHADER.replace("pos.xyz * scale + bias", "pos.xyz * scale + bias * 2"),
        SWAP_YZ_SHADER.replace("2.0f*(qx*qz + qw*qy)", "2.0f*(qx*qz - qw*qy)"),
        POSE_SHADER.replace("p3_next.QD * weights.w * inter * sign3_next", "p3_next.QD * weights.w * inter"),
        SWAP_YZ_SHADER.replace("pos_result.x, pos_result.z, -pos_result.y", "pos_result.x, pos_result.y, pos_result.z"),
        POSE_SHADER.replace("IniParams[89].x", "IniParams[89].y"),
        POSE_SHADER.replace("float4 qr =", "int4 qr ="),
        POSE_SHADER.replace("#define TIME IniParams[88].x", "#define TIME IniParams[88].x\n#define TIME 0"),
        "#define normalize(x) float3(1, 0, 0)\n" + POSE_SHADER,
    ):
        shader_path.write_text(shader, encoding="utf-8")
        _, reloaded = load_mod(ini, ini.parent)
        assert reloaded.meshes
        assert all("animation_geometry" not in mesh for mesh in reloaded.meshes.values())
    shader_path.write_text("// synthetic kernel\n" + POSE_SHADER.replace(" = ", "  =  "), encoding="utf-8")
    _, restored = load_mod(ini, ini.parent)
    assert next(iter(restored.meshes.values()))["animation_geometry"]["pose"]


@pytest.mark.parametrize("case", [
    "elif", "unknown-alias", "wrong-phase-channel", "truncated-pose",
    "invalid-bone-index", "ambiguous-writers", "mismatched-output",
])
def test_unsafe_compute_input_falls_back_to_renderable_static_mesh(tmp_path, case):
    ini = compute_mod(tmp_path / "mod")
    sections = read_sections(ini)
    if case == "elif":
        lines = sections["CustomShaderShape"]
        end = lines.index("endif")
        lines[end:end + 1] = ["elif $pause == 1", "$Freq_key = 0", "endif"]
    elif case == "unknown-alias":
        sections["CommandListAlias"] = ["$allowed = ($runtime_value > 2)"]
        sections["CustomShaderShape"] = ["if $allowed", *sections["CustomShaderShape"], "endif"]
        sections["Present"] = [
            "if $allowed",
            "$frame = (time * 30 % ($end_frame - $strat_frame + 1) + $strat_frame) // 1",
            "endif"]
    elif case == "wrong-phase-channel":
        (ini.parent / "shape.hlsl").write_text(
            SHAPE_SHADER.replace("IniParams[88].x", "IniParams[87].x"), encoding="utf-8")
    elif case == "truncated-pose":
        pose = ini.parent / "pose.buf"
        pose.write_bytes(pose.read_bytes()[:56])
    elif case == "invalid-bone-index":
        (ini.parent / "blend.buf").write_bytes(
            struct.pack("<4f4i", 1., 0., 0., 0., 2, 0, 0, 0) * 3)
    elif case == "ambiguous-writers":
        sections["CustomShaderShape02"] = list(sections["CustomShaderShape"])
    else:
        sections["CustomShaderPose"] = [
            line.replace("cs-u5 = copy ResourcePosition.1", "cs-u5 = copy ResourceKey1")
            for line in sections["CustomShaderPose"]]
    save_sections(ini, sections)
    parsed, built = load_mod(ini, ini.parent)
    assert built.meshes
    assert all("animation_geometry" not in mesh for mesh in built.meshes.values())
    if case == "unknown-alias":
        assert not parsed.animations


@pytest.mark.parametrize("transport", ["directory", "zip"])
def test_compute_resources_and_programs_stay_owned_by_their_ini(tmp_path, transport):
    root = tmp_path / "mod"
    inis = [compute_mod(root / name) for name in ("part-01", "part-02")]
    position = inis[1].parent / "position.buf"
    raw = bytearray(position.read_bytes())
    struct.pack_into("<f", raw, 2 * 40, 5.)
    position.write_bytes(raw)
    if transport == "zip":
        archive = tmp_path / "mod.zip"
        with zipfile.ZipFile(archive, "w") as output:
            for path in root.rglob("*"):
                if path.is_file():
                    output.write(path, "fixture/" + path.relative_to(root).as_posix())
        source = ZipModSource(str(archive))
    else:
        source = DirectoryModSource(str(root))
    paths = [source.document_path(ini.relative_to(root).as_posix()) for ini in inis]
    geometry = GeometryBlob()
    _, built = load_mod(paths, root, source=source, geometry=geometry)
    entries = list(built.meshes.values())
    assert len(entries) == 2
    payloads = [entry["animation_geometry"] for entry in entries]
    assert len({payload["track_id"] for payload in payloads}) == 2
    assert len({payload["program_id"] for payload in payloads}) == 2
    assert [geometry_values(geometry, entry["pos"])[6] for entry in entries] == [0., 5.]


@pytest.mark.parametrize("mode", ["slider", "sine", "mixed-linear"])
def test_authored_slider_and_compute_animation_keep_their_owners(tmp_path, mode):
    ini = compute_mod(tmp_path / "mod")
    sections = read_sections(ini)
    del sections["CustomShaderPose"]
    sections["Constants"].append("global $shapeControl = 0.5")
    sections["CustomShaderShape"] = [
        "cs-u5 = copy ResourcePosition.2", "x88 = $shapeControl",
        "cs-t50 = copy ResourcePosition.2", "cs-t51 = copy ResourceKey1", "cs = shape.hlsl",
        "ResourcePosition = ref cs-u5", "Dispatch = 1, 1, 1"]
    if mode != "sine":
        (ini.parent / "shape.hlsl").write_text(LINEAR_SHAPE_SHADER, encoding="utf-8")
        sections["CommandListDrawSlider.Control"] = ["x87 = $shapeControl * x87"]
    if mode == "mixed-linear":
        sections["CustomShaderShape"].extend([
            "$Freq_key = $Freq_key + $Speed * $dt", "x88 = $Freq_key",
            "cs-t51 = copy ResourceKey2", "Dispatch = 1, 1, 1"])
    sections["CustomShaderShape"].append("cs-u5 = null")
    sections["ResourcePosition"] = ["stride = 40", "filename = position.buf"]
    save_sections(ini, sections)
    _, built = load_mod(ini, ini.parent)
    entry = next(iter(built.meshes.values()))
    if mode == "slider":
        assert entry["shape_targets"]
        assert "animation_geometry" not in entry
    else:
        assert len(entry["animation_geometry"]["shape_passes"]) == (2 if mode == "mixed-linear" else 1)


def test_nested_animation_uses_inherited_child_until_parent_is_supported(tmp_path):
    ini = nested_mod(tmp_path / "mod")
    parsed, built = load_mod(ini, ini.parent)
    payload = next(iter(built.meshes.values()))["animation_geometry"]
    assert payload["overlay"] is True
    assert payload["conditions"] == [[
        {"var": "mode", "value": "2", "negate": False},
        {"var": "hidden", "value": "0", "negate": False},
        {"var": "covered", "value": "0", "negate": False}]]
    assert {"mode", "hidden", "covered", "Speed"} <= parsed.animation_control_vars
    sections = read_sections(ini)
    sections["CustomShaderParent"] = [
        "x88 = $Freq_key", "cs-u5 = copy ResourcePosition.2",
        "cs-t50 = copy ResourcePosition.2", "cs-t51 = copy ResourceKey1",
        "cs = shape.hlsl", "Dispatch = 1, 1, 1",
        "if $mode == 2", "run = CustomShaderAnim", "endif",
        "ResourcePosition = ref cs-u5", "cs-u5 = null"]
    save_sections(ini, sections)
    geometry = GeometryBlob()
    _, rebuilt = load_mod(ini, ini.parent, geometry=geometry)
    payload = next(iter(rebuilt.meshes.values()))["animation_geometry"]
    assert not payload.get("overlay")
    assert len(payload["shape_passes"]) == 1
    assert geometry_values(geometry, payload["shape_passes"][0]["deltas"])[::6] == (1., 2., 3.)


@pytest.mark.parametrize("case", [
    "ambiguous-children", "child-rebind", "stale-parent-binding", "unsupported-parent-dispatch",
])
def test_nested_animation_rejects_unverified_inheritance(tmp_path, case):
    ini = nested_mod(tmp_path / "mod")
    sections = read_sections(ini)
    if case == "ambiguous-children":
        sections["CustomShaderAnim02"] = list(sections["CustomShaderAnim"])
        lines = sections["CustomShaderParent"]
        lines.insert(lines.index("run = CustomShaderAnim"), "run = CustomShaderAnim02")
    elif case == "child-rebind":
        sections["CustomShaderAnim"].insert(0, "cs-u5 = copy ResourcePosition.2")
    elif case == "unsupported-parent-dispatch":
        lines = sections["CustomShaderParent"]
        lines[lines.index("Dispatch = 1, 1, 1")] = "Dispatch = 1, 2, 1"
    else:
        lines = sections["CustomShaderParent"]
        lines.insert(lines.index("run = CustomShaderAnim"), "cs-t50 = null")
    save_sections(ini, sections)
    _, built = load_mod(ini, ini.parent)
    assert built.meshes
    assert all("animation_geometry" not in mesh for mesh in built.meshes.values())


WWMI_ANIMATION_SHADER = """
Texture1D<float4> IniParams : register(t120);
#define ShapeKeyValue IniParams[0].z
RWBuffer<float4> CustomShapeKeyValuesRW : register(u5);
[numthreads(1, 1, 1)]
void main(uint3 id : SV_DispatchThreadID) {
  float shape_key_value = float(ShapeKeyValue);
  float shape_key_anim = (0.5*(sin(shape_key_value*30)+1));
}
"""


def _wwmi_sections():
    return parse_sections("source-01.ini", text=r"""
[Constants]
global $input33 = 0
global $input34 = 0
global $input31 = 0.25
global $input32 = 0.5
global $phase01 = 0
global $phase02 = 0
global $dt

[Present]
if $object_detected
    if $mod_enabled
        if $input33 == 1
            run = CommandListAnimTrack01
        else
            run = CommandListResetinput33
        endif
        if $input34 == 1
            run = CommandListAnimTrack02
        else
            run = CommandListResetinput34
        endif
    endif
endif

[CommandListAnimTrack01]
$phase01 = $phase01 + $input31 * $dt
run = CustomShaderinput33

[CommandListAnimTrack02]
$phase02 = $phase02 + $input32 * $dt
run = CustomShaderTrack02Anim

[CommandListResetinput33]
$\WWMIv1\shapekey_id =
$\WWMIv1\shapekey_value = 0
run = WWMIv1SetShapeKey

[CommandListResetinput34]
$\WWMIv1\shapekey_id =
$\WWMIv1\shapekey_value = 0
run = WWMIv1SetShapeKey

[CustomShaderinput33]
cs-u5 = ResourceCustomShapeKeyValuesRW
cs = res/anim.hlsl
x0 = 0
y0 =
z0 = $phase01
dispatch = 1, 1, 1

[CustomShaderTrack02Anim]
cs-u5 = ResourceCustomShapeKeyValuesRW
cs = res/anim.hlsl
x0 = 0
y0 =
z0 = $phase02
dispatch = 1, 1, 1
""")



def _sparse_mod(root):
    root.mkdir()
    (root / "res").mkdir()
    (root / "Meshes").mkdir()
    (root / "res" / "anim.hlsl").write_text(WWMI_ANIMATION_SHADER, encoding="utf-8")
    sections = _wwmi_sections()
    sections["Constants"].extend([
        "global $outer = 1", "global $shape01 = 0", "global $shapekey_vertex_offset_batch1 = 0"])
    sections["Present"] = ["if $outer == 1", *sections["Present"], "endif"]
    sections.update({
        "CommandListDrawSlider.Shape01": ["x87 = $shape01 * x87"],
        "CommandListSetShape01": [
            r"$\WWMIv1\shapekey_id = 164", r"$\WWMIv1\shapekey_value = $shape01"],
        "CommandListSetupShapeKeysBatch": ["cs-t33 = ResourceShapeKeyOffsetBuffer"],
        "CommandListLoadShapeKeysBatch": [
            "cs-t0 = ResourceShapeKeyVertexIdBuffer", "cs-t1 = ResourceShapeKeyVertexOffsetBuffer"],
        "CommandListApplyShapeKeys": ["cs-t6 = ResourcePosition"],
        "TextureOverrideComponent01": [
            "vb0 = ResourcePosition", "vb1 = ResourceTexcoord",
            "ib = ResourceIB", "drawindexed = 3, 0, 0"],
        "ResourcePosition": ["stride = 12", "filename = Meshes/Position.buf"],
        "ResourceTexcoord": ["stride = 8", "filename = Meshes/Texcoord.buf"],
        "ResourceIB": ["format = DXGI_FORMAT_R32_UINT", "filename = Meshes/Component01.ib"],
        "ResourceShapeKeyOffsetBuffer": ["filename = Meshes/ShapeKeyOffset.buf"],
        "ResourceShapeKeyVertexIdBuffer": ["filename = Meshes/ShapeKeyVertexId.buf"],
        "ResourceShapeKeyVertexOffsetBuffer": ["filename = Meshes/ShapeKeyVertexOffset.buf"],
    })
    meshes = root / "Meshes"
    (meshes / "Position.buf").write_bytes(struct.pack("<9f", 0., 0., 0., 1., 0., 0., 0., 1., 0.))
    (meshes / "Texcoord.buf").write_bytes(b"\0" * 24)
    (meshes / "Component01.ib").write_bytes(struct.pack("<3I", 0, 1, 2))
    offsets = bytearray(170 * 4)
    for index, slot in enumerate((165, 166, 167)):
        struct.pack_into("<II", offsets, slot * 4, index, index + 1)
    (meshes / "ShapeKeyOffset.buf").write_bytes(offsets)
    (meshes / "ShapeKeyVertexId.buf").write_bytes(struct.pack("<3I", 1, 1, 1))
    (meshes / "ShapeKeyVertexOffset.buf").write_bytes(b"".join(
        struct.pack("<3e", *delta) + b"\0" * 6
        for delta in ((1., 0., 0.), (0., 2., 0.), (0., 0., 3.))))
    ini = root / "source-01.ini"
    save_sections(ini, sections)
    return ini


def test_sparse_animation_keeps_outer_guard_and_static_shape_target(tmp_path):
    ini = _sparse_mod(tmp_path / "mod")
    geometry = GeometryBlob()
    parsed, built = load_mod(ini, ini.parent, geometry=geometry)
    entry = next(iter(built.meshes.values()))
    assert len(entry["shape_targets"]) == 1
    payload = entry["animation_geometry"]
    assert payload["overlay"] and payload["position_only"]
    assert [geometry_values(geometry, item["deltas"]) for item in payload["shape_passes"]] == [
        (0., 0., 0., 0., 2., 0., 0., 0., 0.),
        (0., 0., 0., 0., 0., 3., 0., 0., 0.)]
    assert {"outer", "input31", "input32", "input33", "input34"} <= parsed.animation_control_vars
    assert "outer" in payload["program"]["external_variables"]
    for command in payload["program"]["commands"]:
        assert command["conditions"][0] == {
            "kind": "compare", "op": "==",
            "left": {"kind": "variable", "variable": "outer"},
            "right": {"kind": "literal", "value": 1}}


@pytest.mark.parametrize("case", [
    "unknown-guard", "elif", "partial-conditional-pass", "missing-slot", "wrong-phase-channel",
])
def test_sparse_animation_rejects_partial_or_unsupported_reconstruction(tmp_path, case):
    ini = _sparse_mod(tmp_path / "mod")
    sections = read_sections(ini)
    if case == "unknown-guard":
        sections["Present"][0] = "if $outer > time"
    elif case == "elif":
        sections["Present"] = ["if $outer == 0", "elif $outer == 1", *sections["Present"], "endif"]
    elif case == "partial-conditional-pass":
        sections["CommandListAnimTrack01"] = [
            "if $outer == 1", *sections["CommandListAnimTrack01"], "endif"]
    elif case == "missing-slot":
        path = ini.parent / "Meshes" / "ShapeKeyOffset.buf"
        offsets = bytearray(path.read_bytes())
        struct.pack_into("<I", offsets, 168 * 4, 2)
        path.write_bytes(offsets)
    else:
        (ini.parent / "res" / "anim.hlsl").write_text(
            WWMI_ANIMATION_SHADER.replace("IniParams[0].z", "IniParams[0].y"), encoding="utf-8")
    save_sections(ini, sections)
    _, built = load_mod(ini, ini.parent)
    entry = next(iter(built.meshes.values()))
    assert entry["shape_targets"]
    assert "animation_geometry" not in entry

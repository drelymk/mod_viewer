"""Synthetic animation assets shared by loader and runtime tests."""

import struct
from pathlib import Path

from app.mods.analysis import analyze_mod_inis
from core.geometry.mesh_builder import build_mesh_result
from core.ini.sections import parse_sections

_DATA = Path(__file__).resolve().parents[1] / "data" / "animations"
SHAPE_SHADER = (_DATA / "shape.hlsl").read_text(encoding="utf-8")
LINEAR_SHAPE_SHADER = (_DATA / "linear-shape.hlsl").read_text(encoding="utf-8")
POSE_SHADER = (_DATA / "pose.hlsl").read_text(encoding="utf-8")
SWAP_YZ_SHADER = POSE_SHADER.replace(
    "v.position.x, v.position.y, v.position.z", "v.position.x, -v.position.z, v.position.y"
).replace(
    "v.normal.x, v.normal.y, v.normal.z", "v.normal.x, -v.normal.z, v.normal.y"
).replace(
    "pos_result.x, pos_result.y, pos_result.z", "pos_result.x, pos_result.z, -pos_result.y"
).replace(
    "normal_result.x, normal_result.y, normal_result.z",
    "normal_result.x, normal_result.z, -normal_result.y")
POINTS = ((0., 0., 0.), (1., 0., 0.), (0., 1., 0.))


def write_vertices(path, points, deltas=None):
    deltas = deltas if deltas is not None else [0.] * len(points)
    path.write_bytes(b"".join(
        struct.pack("<6f", x + delta, y, z, 0., 2. + delta, 0.) + b"\0" * 16
        for (x, y, z), delta in zip(points, deltas)
    ))


def read_sections(ini):
    return parse_sections(str(ini), text=ini.read_text(encoding="utf-8"))


def save_sections(ini, sections):
    ini.write_text("\n".join(
        line for name, lines in sections.items()
        for line in [f"[{name}]", *lines, ""]
    ), encoding="utf-8")


def joined_baked_mod(root, *, inherited=True, change="all", index_switching=True):
    """Two authored draws after frame-conditioned buffer assignments."""
    sections = {
        "Constants": ["$start = 0", "$end = 2", "$show = 1"],
        "KeyShow": ["key = s", "type = cycle", "$show = 0,1"],
        "Present": ["$frame = (time * 30 % ($end - $start + 1) + $start) // 1"],
        "TextureOverrideModelA": ["run = CommandListModelIndex",
                                  "drawindexed = 3, 0, 0", "if $show == 1",
                                  "drawindexed = 6, 3, 0", "endif"],
    }
    position, texcoord, index = [], [], []
    for frame in range(3):
        branch = "if" if frame == 0 else "elif"
        for lines, slot, resource in (
            (position, "vb0", "ResourceModelPosition"),
            (texcoord, "vb1", "ResourceModelTexcoord"),
            (index, "ib", "ResourceModelAIB"),
        ):
            lines.extend([f"{branch} $frame == {frame}", f"{slot} = {resource}.{frame}"])
        z = float(frame) if change in ("all", "position") else 0.
        points = [(0., 0., z), (1., 0., z), (0., 1., z), (1., 1., z), (2., 1., z)]
        write_vertices(root / f"position{frame}.buf", points)
        uv = frame / 8 if change in ("all", "uv") else 0.
        (root / f"texcoord{frame}.buf").write_bytes(
            b"".join(struct.pack("<ff", u + uv, v + uv)
                     for u, v in ((0., 0.), (.5, 0.), (0., .5), (.5, .5), (1., 1.))))
        values = [0, 1, 2, 0, 1, 2, 1, 3, 2]
        if frame and change in ("all", "indices"):
            values[4:6] = [2, 1]
        if frame == 2 and change == "mapping":
            values[7] = 4
        (root / f"index{frame}.buf").write_bytes(struct.pack("<9I", *values))
        for resource, filename, descriptor in (
            ("ResourceModelPosition", "position", "stride = 40"),
            ("ResourceModelTexcoord", "texcoord", "stride = 8"),
            ("ResourceModelAIB", "index", "format = DXGI_FORMAT_R32_UINT"),
        ):
            sections[f"{resource}.{frame}"] = [f"filename = {filename}{frame}.buf", descriptor]
    for lines in (position, texcoord, index):
        lines.append("endif")
    sections["CommandListModelIndex"] = index
    if inherited:
        sections["TextureOverrideModelBlend"] = ["run = CommandListModelBlend"]
        sections["CommandListModelBlend"] = position
        sections["TextureOverrideModelTexcoord"] = texcoord
    else:
        sections["CommandListModelIndex"] = position + texcoord + index
    if not index_switching:
        sections["CommandListModelIndex"] = ([] if inherited else position + texcoord) + [
            "ib = ResourceModelAIB.0"]
    ini = root / "source-01.ini"
    save_sections(ini, sections)
    return ini


def compute_mod(root, *, shared_outputs=False, rotating_pose=False):
    root.mkdir(parents=True)
    (root / "shape.hlsl").write_text(SHAPE_SHADER, encoding="utf-8")
    (root / "pose.hlsl").write_text(POSE_SHADER, encoding="utf-8")
    write_vertices(root / "position.buf", POINTS)
    write_vertices(root / "key1.buf", POINTS, [1., 2., 3.])
    write_vertices(root / "key2.buf", POINTS, [2., 4., 6.])
    (root / "blend.buf").write_bytes(
        struct.pack("<4f4i", 1., 0., 0., 0., 0, 0, 0, 0) * 3)
    pose = bytearray()
    for frame, translation in enumerate((.25, .75)):
        rotation = (0., 0., 2 ** -.5, 2 ** -.5) if rotating_pose and frame else (0., 0., 0., 1.)
        dual = (.05 * rotation[2], .05 * rotation[3], 0., 0.) if rotating_pose else (0.,) * 4
        pose.extend(struct.pack("<3f3f4f4f", 1., 1., 1., translation, 0., 0.,
                                *rotation, *dual) * 2)
    (root / "pose.buf").write_bytes(pose)
    (root / "texcoord.buf").write_bytes(b"\0" * 60)
    (root / "index.buf").write_bytes(struct.pack("<3I", 2, 0, 1))
    ini = root / "source-01.ini"
    text = (_DATA / "compute.ini").read_text(encoding="utf-8").format(stride=40)
    sections = parse_sections(str(ini), text=text)
    sections["KeyPause"] = ["key = p", "type = cycle", "$pause = 0,1"]
    sections["KeyAnime"] = ["key = a", "type = cycle", "$anime_state = 0,1"]
    if shared_outputs:
        pose_lines = sections["CustomShaderPose"]
        binding_start = next(index for index, line in enumerate(pose_lines)
                             if line.startswith("x88"))
        sections["CustomShaderPose02"] = [
            line.replace("ResourcePosition = ref", "ResourcePosition02 = ref")
            for line in pose_lines[binding_start:]]
        sections["ResourcePosition02"] = []
        sections["TextureOverrideComponent02"] = [
            line.replace("ResourcePosition", "ResourcePosition02")
            for line in sections["TextureOverrideComponent01"]]
    save_sections(ini, sections)
    return ini


def load_mod(ini_paths, root, *, source=None, geometry=None):
    if isinstance(ini_paths, (str, Path)):
        ini_paths = [ini_paths]
    parsed = analyze_mod_inis(
        [str(ini) if isinstance(ini, Path) else ini for ini in ini_paths],
        str(root), source=source)
    built = build_mesh_result(parsed.groups, str(root), source=source,
                             geometry=geometry, animations=parsed.animations)
    return parsed, built


def nested_mod(root):
    ini = compute_mod(root)
    (root / "anim.hlsl").write_text(SHAPE_SHADER, encoding="utf-8")
    (root / "shape.hlsl").write_text(LINEAR_SHAPE_SHADER, encoding="utf-8")
    sections = read_sections(ini)
    del sections["CustomShaderShape"]
    del sections["CustomShaderPose"]
    sections["Constants"].extend([
        "global $mode = 0", "global $hidden = 0", "global $covered = 0"])
    sections["CustomShaderParent"] = [
        "if $mode == 2 && !($hidden || $covered)",
        "cs-u5 = copy ResourcePosition.2", "cs-t50 = copy ResourcePosition.2",
        "cs-t51 = copy ResourceKey1", "cs = shape.hlsl", "Dispatch = 1, 1, 1",
        "run = CustomShaderAnim", "endif",
        "ResourcePosition = ref cs-u5", "cs-u5 = null"]
    sections["CustomShaderAnim"] = [
        "$Freq_key = $Freq_key + $Speed * $dt", "x88 = $Freq_key",
        "cs-t51 = copy ResourceKey2", "cs = anim.hlsl", "Dispatch = 1, 1, 1"]
    save_sections(ini, sections)
    return ini


def branched_compute_mod(root):
    """Two verified children with exclusive branches and a partial target."""
    ini = compute_mod(root)
    sections = read_sections(ini)
    del sections["CustomShaderShape"]
    del sections["CustomShaderPose"]
    sections["Constants"].extend([
        "global $mode = 1", "global $hidden = 0", "global $covered = 0"])
    sections["KeyMode"] = ["key = m", "type = cycle", "$mode = 0,1,2,3"]
    sections["CustomShaderParent"] = [
        "cs-u5 = copy ResourcePosition.2", "cs-t50 = copy ResourcePosition.2",
        "if $mode == 1 && !($hidden || $covered)", "run = CustomShaderAnim01",
        "elif $mode == 2 && !($hidden || $covered)", "run = CustomShaderAnim02",
        "else if $mode == 3 && !($hidden || $covered)",
        "run = CustomShaderAnim01", "run = CustomShaderAnim02",
        "else", "$Freq_key = 0", "$Freq_pose = 0", "endif",
        "ResourcePosition = ref cs-u5", "cs-u5 = null"]
    for name, phase, target in (("01", "Freq_key", "ResourceKey1"),
                                ("02", "Freq_pose", "ResourceKey2")):
        sections[f"CustomShaderAnim{name}"] = [
            f"${phase} = (${phase} + $Speed * $dt)", f"x88 = ${phase}",
            "cs-t50 = copy ResourcePosition.2", f"cs-t51 = copy {target}",
            "cs = shape.hlsl", "Dispatch = 1, 1, 1"]
    target = root / "key1.buf"
    target.write_bytes(target.read_bytes()[:80])
    save_sections(ini, sections)
    return ini

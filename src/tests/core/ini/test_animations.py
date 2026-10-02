"""Baked animation contracts at analysis and mesh publication boundaries."""

import struct

import pytest

from core.geometry.mesh_builder import GeometryBlob
from core.ini.analysis import analyze_ini
from core.ini.animations import discover_animation_clocks
from core.ini.sections import extract_resources, parse_sections
from tests.support.animations import load_mod, read_sections, save_sections, write_vertices
from tests.support.provenance import geometry_values


def test_clocks_preserve_controls_live_speed_and_independent_ranges():
    sections = parse_sections("source-01.ini", text="""
[Constants]
$fps = 30
$speed = 0.5
$start = 0
$end = 1
$animate = 1
[KeyAnimate]
type = cycle
key = a
$animate = 0,1
[Present]
if $animate == 1
$frame = (time * $fps * $speed % ($end - $start + 1) + $start) // 1
endif
$other = (time * 12.5 % 4 + 1) // 1
[TextureOverrideComponent01]
vb1 = ResourceTexcoord
ib = ResourceIB
if $animate == 1
if $frame == 0
vb0 = ResourcePosition0
drawindexed = 3, 0, 0
elif $frame == 1
vb0 = ResourcePosition1
drawindexed = 3, 0, 0
endif
endif
[ResourcePosition0]
filename = position0.buf
stride = 40
[ResourcePosition1]
filename = position1.buf
stride = 40
[ResourceTexcoord]
filename = texcoord.buf
stride = 20
[ResourceIB]
filename = index.buf
format = DXGI_FORMAT_R32_UINT
""")
    clocks = {clock.frame_var: clock for clock in discover_animation_clocks(sections).clocks}
    clock = clocks["frame"]
    assert (clock.fps_var, clock.fps_value, clock.speed_var, clock.speed_value,
            clock.frame_start, clock.frame_end) == ("fps", 30, "speed", .5, 0, 1)
    assert (clocks["other"].fps_value, clocks["other"].frame_start,
            clocks["other"].frame_end) == (12.5, 1, 4)
    analysis = analyze_ini(sections, resources=extract_resources(sections),
                           extra_gating_vars={"animate"})
    assert "frame" not in analysis.gating_vars
    draws = analysis.draw_groups[0]["draws"]
    assert [draw.conditions for draw in draws] == [clock.conditions] * 2
    assert [draw.animation_conditions[0][0]["value"] for draw in draws] == ["0", "1"]


def _baked_mod(root, *, commandlist=False, static=False):
    points = [(0., 0., 0.), (.5, .5, 0.), (1., 0., 0.), (0., 1., 0.)]
    indices = (0, 1, 2) if static else (2, 0, 3)
    write_vertices(root / "position0.buf", points)
    write_vertices(root / "position1.buf", [(x, y, 0. if static else 1.) for x, y, _ in points])
    (root / "texcoord.buf").write_bytes(b"\0" * 80)
    (root / "index.buf").write_bytes(struct.pack("<3I", *indices))
    bindings = """
if $frame == 0
vb0 = ResourcePosition0
elif $frame == 1
vb0 = ResourcePosition1
endif
"""
    draw = """
if $frame == 0
vb0 = ResourcePosition0
drawindexed = 3, 0, 0
elif $frame == 1
vb0 = ResourcePosition1
drawindexed = 3, 0, 0
endif
"""
    if commandlist:
        draw = "drawindexed = 3, 0, 0"
        bindings = bindings.replace("vb0 =", "if DRAW_TYPE == 1\nvb0 =").replace(
            "elif $frame", "endif\nelse if $frame").replace(
                "ResourcePosition1\nendif", "ResourcePosition1\nendif\nendif")
    ini = root / "source-01.ini"
    ini.write_text(f"""
[Constants]
$fps = 30
$start = 0
$end = 1
[Present]
$frame = (time * $fps % ($end - $start + 1) + $start) // 1
[TextureOverrideComponent01]
vb1 = ResourceComponent01Texcoord
ib = ResourceComponent01IB
{draw}
[TextureOverrideComponent01Blend]
run = CommandListComponent01Blend
[CommandListComponent01Blend]
{bindings if commandlist else ""}
[ResourcePosition0]
filename = position0.buf
stride = 40
[ResourcePosition1]
filename = position1.buf
stride = 40
[ResourceComponent01Texcoord]
filename = texcoord.buf
stride = 20
[ResourceComponent01IB]
filename = index.buf
format = DXGI_FORMAT_R32_UINT
""", encoding="utf-8")
    return ini, [points[index] for index in sorted(indices)]


@pytest.mark.parametrize("mode", ["draw-branches", "commandlist", "range-switch", "static"])
def test_baked_frames_publish_one_mesh_in_draw_order(tmp_path, mode):
    ini, expected_points = _baked_mod(
        tmp_path, commandlist=mode == "commandlist", static=mode == "static")
    if mode == "range-switch":
        sections = read_sections(ini)
        sections["Constants"].extend(["$animate = 1", "$longEnd = 2"])
        sections["KeyAnimate"] = ["key = a", "type = cycle", "$animate = 1,2"]
        sections["Present"] = [
            "if $animate == 1", *sections["Present"], "elif $animate == 2",
            "$frame = (time * $fps % ($longEnd - $start + 1) + $start) // 1", "endif"]
        lines = sections["TextureOverrideComponent01"]
        lines[-1:-1] = ["elif $frame == 2", "vb0 = ResourcePosition2", "drawindexed = 3, 0, 0"]
        sections["ResourcePosition2"] = ["filename = position2.buf", "stride = 40"]
        write_vertices(tmp_path / "position2.buf", [
            (0., 0., 2.), (.5, .5, 2.), (1., 0., 2.), (0., 1., 2.)])
        save_sections(ini, sections)
    geometry = GeometryBlob()
    _, built = load_mod(ini, tmp_path, geometry=geometry)
    assert len(built.meshes) == 1
    entry = next(iter(built.meshes.values()))
    expected = tuple(value for point in expected_points for value in point)
    assert geometry_values(geometry, entry["pos"]) == expected
    reference = entry["idx"]
    assert struct.unpack_from("<3I", geometry.data, reference["offset"]) == (
        (0, 1, 2) if mode == "static" else (1, 0, 2))
    if mode == "static":
        assert "animation_geometry" not in entry
        assert not built.animations
    else:
        payload = entry["animation_geometry"]
        assert payload["frames"] == (3 if mode == "range-switch" else 2)
        assert set(payload["clock_ids"]) == set(built.animations)
        assert len(built.animations) == (2 if mode == "range-switch" else 1)
        assert geometry_values(geometry, payload["positions"]) == tuple(
            value if index % 3 != 2 else float(frame)
            for frame in range(payload["frames"]) for index, value in enumerate(expected))
        assert payload["bounds"] == {
            "min": [0., 0., 0.], "max": [1., 1., float(payload["frames"] - 1)]}


@pytest.mark.parametrize("case", ["compatible", "changed-uv", "changed-topology", "missing-frame"])
def test_draw_range_animation_requires_matching_geometry(tmp_path, case):
    points = [(x, y, z) for z in (0., 1.)
              for x, y in ((0., 0.), (1., 0.), (0., 1.))]
    write_vertices(tmp_path / "position.buf", points)
    (tmp_path / "texcoord.buf").write_bytes(b"".join(
        b"\0" * 4 + struct.pack("<ee", x, y + (.25 if case == "changed-uv" and z else 0.))
        + b"\0" * 12 for x, y, z in points))
    indices = (0, 1, 2, 3, 4, 4 if case == "changed-topology" else 5)
    (tmp_path / "index.buf").write_bytes(struct.pack("<6I", *indices))
    second = "elif $frame == 1\ndrawindexed = 3, 3, 0" if case != "missing-frame" else ""
    ini = tmp_path / "source-01.ini"
    ini.write_text(f"""
[Constants]
$start = 0
$end = 1
[Present]
$frame = (time * 30 % ($end - $start + 1) + $start) // 1
[TextureOverrideComponent01]
vb0 = ResourcePosition
vb1 = ResourceTexcoord
ib = ResourceIB
if $frame == 0
drawindexed = 3, 0, 0
{second}
endif
[ResourcePosition]
filename = position.buf
stride = 40
[ResourceTexcoord]
filename = texcoord.buf
stride = 20
[ResourceIB]
filename = index.buf
format = DXGI_FORMAT_R32_UINT
""", encoding="utf-8")
    geometry = GeometryBlob()
    _, built = load_mod(ini, tmp_path, geometry=geometry)
    assert len(built.meshes) == 1
    entry = next(iter(built.meshes.values()))
    if case == "compatible":
        assert entry["animation_geometry"]["frames"] == 2
        assert geometry_values(geometry, entry["animation_geometry"]["positions"]) == (
            0., 0., 0., 1., 0., 0., 0., 1., 0.,
            0., 0., 1., 1., 0., 1., 0., 1., 1.)
    else:
        assert "animation_geometry" not in entry
        assert not built.animations
        refs = [value for value in entry.values()
                if isinstance(value, dict) and "offset" in value and "length" in value]
        assert len(geometry) == max(ref["offset"] + ref["length"] for ref in refs)

"""Baked animation contracts at analysis and mesh publication boundaries."""

import struct
import zipfile

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


@pytest.mark.parametrize("commandlist", [False, True], ids=["draw-branches", "commandlist"])
@pytest.mark.parametrize("change,animated", [
    ("unused-data", False), ("unused-vertex", False), ("normal-scale", False),
    ("unpublished-normals", False), ("late-invalid-normals", False), ("signed-zero", False),
    ("normal-direction", True), ("position", True),
])
def test_baked_detection_compares_only_published_attributes(tmp_path, commandlist, change, animated):
    ini, _ = _baked_mod(tmp_path, commandlist=commandlist)
    first = bytearray((tmp_path / "position0.buf").read_bytes())
    second = bytearray(first)
    if change == "unused-data":
        struct.pack_into("<f", second, 24, 9.)
    elif change == "unused-vertex":
        struct.pack_into("<f", second, 40, 9.)
    elif change == "normal-scale":
        struct.pack_into("<3f", first, 12, .1, .2, .3)
        struct.pack_into("<3f", second, 12, .3, .6, .9)
    elif change == "unpublished-normals":
        first[12:24] = b"\0" * 12
        struct.pack_into("<3f", second, 12, 2., 0., 0.)
    elif change == "late-invalid-normals":
        second[12:24] = b"\0" * 12
    elif change == "signed-zero":
        struct.pack_into("<f", second, 0, -0.)
    elif change == "normal-direction":
        struct.pack_into("<3f", second, 12, 2., 0., 0.)
    elif change == "position":
        struct.pack_into("<f", second, 0, 1e-8)
    (tmp_path / "position0.buf").write_bytes(first)
    (tmp_path / "position1.buf").write_bytes(second)
    _, built = load_mod(ini, tmp_path)
    assert len(built.meshes) == 1
    entry = next(iter(built.meshes.values()))
    assert ("animation_geometry" in entry) == animated
    assert bool(built.animations) == animated
    if animated:
        assert entry["animation_geometry"]["frames"] == 2
    else:
        references = [value for value in entry.values()
                      if isinstance(value, dict) and "offset" in value and "length" in value]
        assert len(built.geometry) == max(ref["offset"] + ref["length"] for ref in references)


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


@pytest.mark.parametrize("commandlist", [False, True], ids=["draw-branches", "commandlist"])
@pytest.mark.parametrize("short_frame", [0, 1], ids=["short-first", "short-later"])
def test_baked_short_vertex_stream_uses_zero_reads_without_dropping_animation(
        tmp_path, commandlist, short_frame):
    ini, _ = _baked_mod(tmp_path, commandlist=commandlist)
    short_path = tmp_path / f"position{short_frame}.buf"
    short_path.write_bytes(short_path.read_bytes()[:-40])
    _, built = load_mod(ini, tmp_path)
    entry = next(iter(built.meshes.values()))
    animation = entry["animation_geometry"]
    expected_positions = []
    expected_normals = []
    for frame in range(2):
        expected_positions.extend((0., 0., float(frame), 1., 0., float(frame)))
        expected_positions.extend((0., 0., 0.) if frame == short_frame else (0., 1., float(frame)))
        expected_normals.extend((0., 1., 0.) * 2)
        expected_normals.extend((0., 0., 0.) if frame == short_frame else (0., 1., 0.))
    assert animation["frames"] == 2
    assert geometry_values(built.geometry, animation["positions"]) == tuple(expected_positions)
    assert geometry_values(built.geometry, animation["normals"]) == tuple(expected_normals)
    assert geometry_values(built.geometry, entry["pos"]) == tuple(expected_positions[:9])
    assert geometry_values(built.geometry, entry["normal"]) == tuple(expected_normals[:9])
    assert animation["bounds"] == {"min": [0., 0., 0.], "max": [1., 1., 1.]}
    assert entry["idx"]["length"] == 12


@pytest.mark.parametrize("source_kind", ["directory", "zip"])
def test_component_animation_stays_with_its_position_owner(tmp_path, source_kind):
    sections = {
        "Constants": ["$start = 0", "$end = 1"],
        "Present": ["$frame = (time * 30 % ($end - $start + 1) + $start) // 1"],
    }
    points_by_component = {}
    for component, offset, animated in (
        ("Model", 0., True), ("ModelPin", 10., False),
        ("ModelWing", 20., False), ("ModelCharm", 30., True),
    ):
        points = [(offset, 0., 0.), (offset + 1., 0., 0.), (offset, 1., 0.)]
        points_by_component[component] = points
        position = f"Resource{component}Position.0"
        sections[f"TextureOverride{component}Position"] = [f"vb0 = {position}"]
        sections[f"TextureOverride{component}Texcoord"] = [
            f"vb1 = Resource{component}Texcoord.0"]
        sections[position] = [f"filename = {component}-position-0.buf", "stride = 40"]
        write_vertices(tmp_path / f"{component}-position-0.buf", points)
        sections[f"Resource{component}Texcoord.0"] = [
            f"filename = {component}-texcoord.buf", "stride = 20"]
        (tmp_path / f"{component}-texcoord.buf").write_bytes(b"\0" * 60)
        if animated:
            sections[f"TextureOverride{component}Position"] = [
                f"run = CommandList{component}Position"]
            sections[f"CommandList{component}Position"] = [
                "if $frame == 0", f"vb0 = {position}",
                "elif $frame == 1", f"vb0 = Resource{component}Position.1", "endif"]
            sections[f"Resource{component}Position.1"] = [
                f"filename = {component}-position-1.buf", "stride = 40"]
            write_vertices(tmp_path / f"{component}-position-1.buf",
                           [(x, y, 1.) for x, y, _ in points])

    draws = {
        "ModelHead": ("Model", True), "ModelBody": ("Model", True),
        "ModelPinA": ("ModelPin", False), "ModelPinB": ("ModelPin", False),
        "ModelWingA": ("ModelWing", False), "ModelCharmA": ("ModelCharm", True),
        "ModelDirect": ("ModelPin", False), "ModelAlias": ("Model", True),
        "ModelPathAlias": ("Model", True), "ModelDistinct": ("ModelPin", False),
    }
    for name in draws:
        sections[f"TextureOverride{name}"] = [
            f"ib = Resource{name}IB.0", "drawindexed = 3, 0, 0"]
        sections[f"Resource{name}IB.0"] = [
            "filename = index.buf", "format = DXGI_FORMAT_R32_UINT"]
    sections["TextureOverrideModelDirect"].insert(
        0, "vb0 = ResourceModelPinPosition.0")
    sections["TextureOverrideModelAlias"].insert(0, "vb0 = ResourcePositionAlias")
    sections["CommandListAlias"] = [
        "ResourcePositionAlias = copy ResourceModelPosition.0"]
    sections["ResourcePositionAlias"] = []
    (tmp_path / "Meshes").mkdir()
    (tmp_path / "Other").mkdir()
    write_vertices(tmp_path / "Meshes" / "Position.buf", points_by_component["Model"])
    write_vertices(tmp_path / "Other" / "Position.buf", points_by_component["ModelPin"])
    sections["ResourceModelPosition.0"] = ["filename = Meshes/Position.buf", "stride = 40"]
    sections["ResourcePathAlias"] = ["filename = Meshes/./Position.buf", "stride = 40"]
    sections["ResourceDistinct"] = ["filename = Other/Position.buf", "stride = 40"]
    sections["TextureOverrideModelPathAlias"].insert(0, "vb0 = ResourcePathAlias")
    sections["TextureOverrideModelDistinct"].insert(0, "vb0 = ResourceDistinct")
    (tmp_path / "index.buf").write_bytes(struct.pack("<3I", 0, 1, 2))
    ini = tmp_path / "source-01.ini"
    save_sections(ini, sections)

    source = None
    if source_kind == "zip":
        from core.mod_source import ZipModSource
        archive = tmp_path / "fixture.zip"
        with zipfile.ZipFile(archive, "w") as output:
            for path in tmp_path.rglob("*"):
                if path.is_file() and path != archive:
                    output.write(path, path.relative_to(tmp_path))
        source = ZipModSource(str(archive))
        ini = source.document_path("source-01.ini")
    parsed, built = load_mod(ini, tmp_path, source=source)
    assert len(built.meshes) == len(draws)
    groups = {group["display_name"]: group for group in parsed.groups}
    for entry in built.meshes.values():
        name = entry["component"]
        component, animated = draws[name]
        expected = tuple(value for point in points_by_component[component] for value in point)
        assert geometry_values(built.geometry, entry["pos"]) == expected
        assert bool(groups[name]["animation_vertex_bindings"]) == animated
        assert ("animation_geometry" in entry) == animated
        if animated:
            animation = entry["animation_geometry"]
            assert animation["frames"] == 2
            assert geometry_values(built.geometry, animation["positions"]) == (
                expected + tuple(1. if index % 3 == 2 else value
                                 for index, value in enumerate(expected)))

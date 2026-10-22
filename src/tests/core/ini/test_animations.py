"""Baked animation contracts at analysis and mesh publication boundaries."""

import struct
import zipfile
from dataclasses import replace
from unittest.mock import patch

import pytest

from core.geometry.mesh_builder import GeometryBlob
from core.geometry.draw_call import DrawCall
from core.geometry.vertex_attributes import VertexAttributeSource
from core.ini.analysis import analyze_ini
from core.ini.animations import discover_animation_clocks
from core.ini.sections import extract_resources, parse_sections
from tests.support.animations import (
    joined_baked_mod, load_mod, read_sections, save_sections, write_vertices,
)
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


@pytest.mark.parametrize("inherited,source_kind,index_switching", [
    (False, "directory", True), (True, "directory", True), (True, "zip", True),
    (False, "directory", False),
], ids=["direct", "component", "archive", "vertex-only-join"])
@pytest.mark.parametrize("change", ["all", "position", "uv", "indices", "static", "mapping", "missing"])
def test_joined_frame_bindings_keep_one_track_per_authored_draw(
        tmp_path, inherited, source_kind, index_switching, change):
    ini = joined_baked_mod(tmp_path, inherited=inherited, change=change,
                           index_switching=index_switching)
    if change == "missing":
        (tmp_path / "index2.buf").unlink()
    source = None
    if source_kind == "zip":
        from core.mod_source import ZipModSource
        archive = tmp_path / "fixture.zip"
        with zipfile.ZipFile(archive, "w") as output:
            for path in tmp_path.iterdir():
                if path.is_file() and path != archive:
                    output.write(path, path.name)
        source = ZipModSource(str(archive))
        ini = source.document_path("source-01.ini")
    parsed, built = load_mod(ini, tmp_path, source=source)
    assert len(built.meshes) == 2
    entries = list(built.meshes.values())
    assert "conditions" not in entries[0]
    assert entries[1]["conditions"] == [[{"var": "show", "value": "1", "negate": False}]]
    assert all(clause["var"] != "frame" for entry in entries
               for group in entry.get("conditions", []) for clause in group)
    draws = parsed.groups[0]["draws"]
    assert all(draw.animation_binding_join for draw in draws)
    assert len({draw.occurrence for draw in draws}) == 2
    from core.geometry.semantics import build_mesh_semantics
    semantics = build_mesh_semantics(parsed.groups, str(tmp_path),
                                     active_mesh_keys=set(built.meshes), source=source)
    assert set(semantics) == set(built.meshes)
    for label, entry in built.meshes.items():
        assert semantics[label]["conditions"] == entry.get("conditions", [])
        assert semantics[label]["identity"] == entry["identity"]
    expected_tracks = (2 if change in ("all", "position", "uv")
                       else 1 if change == "indices" and index_switching else 0)
    assert sum("animation_geometry" in entry for entry in entries) == expected_tracks
    assert built.diagnostics["animation_frame_count"] == expected_tracks * 3
    assert built.diagnostics["animation_prepare_calls"] <= 6
    references = [value for entry in entries
                  for container in (entry, entry.get("animation_geometry", {}))
                  for value in container.values()
                  if isinstance(value, dict) and "offset" in value and "length" in value]
    assert len(built.geometry) == sum(length for _, length in {
        (reference["offset"], reference["length"]) for reference in references})
    if expected_tracks:
        entry = entries[-1]
        animation = entry["animation_geometry"]
        assert animation["frames"] == 3
        assert ("indices" in animation) == (index_switching and change in ("all", "indices"))
        assert ("texcoords" in animation) == (change in ("all", "uv"))
        assert geometry_values(built.geometry, animation["positions"])[14] == (
            1. if change in ("all", "position") else 0.)
        if "indices" in animation:
            reference = animation["indices"]
            values = struct.unpack_from("<18I", built.geometry.data, reference["offset"])
            assert values == (0, 1, 2, 1, 3, 2, 0, 2, 1, 1, 3, 2, 0, 2, 1, 1, 3, 2)
        if "texcoords" in animation:
            values = geometry_values(built.geometry, animation["texcoords"])
            assert values == tuple(value for offset in (0., .125, .25)
                                   for u, v in ((0., 0.), (.5, 0.), (0., .5), (.5, .5))
                                   for value in (u + offset, 1. - v - offset))


@pytest.mark.parametrize("case", ["equivalent", "late-difference", "independent-clock", "static", "auto"])
def test_frame_sequence_equivalence_preserves_refresh_sources_and_fast_paths(tmp_path, case):
    from app.mods.analysis import analyze_mod_inis
    from core.geometry import animation_frames
    from core.geometry.identity import DrawOccurrence
    from core.geometry.mesh_builder import build_mesh_result, build_mesh_semantics

    ini = joined_baked_mod(tmp_path, change="static" if case == "static" else "position")
    sections = read_sections(ini)
    for frame in range(3):
        for resource, file in (("ResourceModelAIB", "index"), ("ResourceModelTexcoord", "texcoord")):
            if case == "auto" and file == "index":
                continue
            sections[f"{resource}.{frame}"][0] = f"filename = {file}0.buf"
    save_sections(ini, sections)
    write_vertices(tmp_path / "different.buf", [(0., 0., 9.), (1., 0., 9.),
                                                (0., 1., 9.), (1., 1., 9.), (2., 1., 9.)])

    def inputs():
        parsed = analyze_mod_inis([str(ini)], str(tmp_path))
        group = parsed.groups[0]
        originals = [draw for draw in group["draws"] if draw.count == 6]
        if case == "auto":
            originals = [replace(draw, count=None, start=0) for draw in originals]
        copies = []
        for frame, draw in enumerate(originals):
            occurrence = DrawOccurrence(draw.occurrence.section, 2)
            sources = [{**source, "line_no": 400, "occurrence": occurrence.to_dict()}
                       for source in draw.sources]
            other = replace(draw, label=f"copy-{frame}", occurrence=occurrence, sources=sources,
                            conditions=[[{"var": "other", "value": "1", "negate": False}]])
            if case == "late-difference" and frame == 2:
                other.position_file = "different.buf"
                other.normal_source = replace(draw.normal_source, file="different.buf")
            if case == "independent-clock":
                other.animation_conditions = [[{"var": "second_frame", "value": str(frame)}]]
            copies.append(other)
        clocks = list(parsed.animations)
        if case == "independent-clock":
            clocks.append({"id": "second-clock", "frame_var": "second_frame", "frame_start": 0,
                           "frame_end": 2, "fps": 20})
        return [{**group, "draws": originals + copies}], clocks

    groups, clocks = inputs()
    original_guards = [draw.conditions for draw in groups[0]["draws"]]
    with patch.object(animation_frames, "pack_animation_topology_frame",
                      wraps=animation_frames.pack_animation_topology_frame) as topology:
        built = build_mesh_result(groups, str(tmp_path), animations=clocks)
    assert topology.call_count == (2 if case == "auto" else 0)
    assert [draw.conditions for draw in groups[0]["draws"]] == original_guards
    equivalent = case in ("equivalent", "static", "auto")
    assert len(built.meshes) == (1 if equivalent else 2)
    if case == "static":
        assert not built.animations
    else:
        assert all("animation_geometry" in entry for entry in built.meshes.values())
    fresh_groups, fresh_clocks = inputs()
    semantics = build_mesh_semantics(fresh_groups, str(tmp_path), animations=fresh_clocks,
                                     active_mesh_keys=set(built.meshes))
    assert set(semantics) == set(built.meshes)
    for label, entry in built.meshes.items():
        assert semantics[label]["conditions"] == entry["conditions"]
        assert semantics[label]["sources"] == entry["sources"]
        assert semantics[label]["identity"] == entry["identity"]
    if equivalent:
        entry = next(iter(built.meshes.values()))
        assert [group[0]["var"] for group in entry["conditions"]] == ["show", "other"]
        assert [source["conditions"][0][0]["var"] for source in entry["sources"]] == ["show", "other"]
        assert [source["occurrence"]["ordinal"] for source in entry["sources"]] == [1, 2]


@pytest.mark.parametrize("commandlist", [False, True], ids=["draw-branches", "commandlist"])
@pytest.mark.parametrize("contiguous", [False, True], ids=["sparse", "contiguous"])
@pytest.mark.parametrize("change,animated", [
    ("unused-data", False), ("unused-vertex", False), ("normal-scale", False),
    ("unpublished-normals", False), ("late-invalid-normals", False), ("signed-zero", False),
    ("normal-direction", True), ("position", True), ("nonfinite-position", False),
])
def test_baked_detection_compares_only_published_attributes(
        tmp_path, commandlist, contiguous, change, animated):
    from core.geometry import animation_frames

    ini, _ = _baked_mod(tmp_path, commandlist=commandlist)
    if contiguous:
        (tmp_path / "index.buf").write_bytes(struct.pack("<3I", 0, 1, 2))
    first = bytearray((tmp_path / "position0.buf").read_bytes())
    second = bytearray(first)
    if change == "unused-data":
        struct.pack_into("<f", second, 24, 9.)
    elif change == "unused-vertex":
        struct.pack_into("<f", second, 120 if contiguous else 40, 9.)
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
    elif change == "nonfinite-position":
        struct.pack_into("<f", second, 0, float("nan"))
    (tmp_path / "position0.buf").write_bytes(first)
    (tmp_path / "position1.buf").write_bytes(second)
    with patch.object(animation_frames, "pack_animation_position_frame",
                      wraps=animation_frames.pack_animation_position_frame) as pack:
        _, built = load_mod(ini, tmp_path)
    if contiguous and change in {"unused-data", "unused-vertex"}:
        assert pack.call_count == 0
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


@pytest.mark.parametrize("animated", [False, True], ids=["static", "animated"])
def test_repeated_tracks_share_only_exact_frame_attributes(tmp_path, animated):
    from core.geometry import mesh_builder

    points = [(0., 0., 0.), (1., 0., 0.), (0., 1., 0.), (1., 1., 0.)]
    for directory, height in (("first", 1.), ("second", 2.)):
        folder = tmp_path / directory
        folder.mkdir()
        write_vertices(folder / "frame0.buf", points)
        write_vertices(folder / "frame1.buf", [
            (x, y, height if animated else z) for x, y, z in points])
    for name, indices in (("base", (0, 1, 2)), ("reversed", (2, 1, 0)),
                          ("subset", (1, 2, 3))):
        (tmp_path / f"{name}.ib").write_bytes(struct.pack("<3I", *indices))
    (tmp_path / "texcoord.buf").write_bytes(b"\0" * 80)
    (tmp_path / "clock-texcoord.buf").write_bytes(b"\0" * 80)
    (tmp_path / "normal.buf").write_bytes(struct.pack("<3f", 1., 0., 0.) * 4)
    draws = []
    for name, index, directory, variable in (
        ("base", "base", "first", "frame"),
        ("topology", "reversed", "first", "frame"),
        ("clock", "base", "first", "other"),
        ("subset", "subset", "first", "frame"),
        ("source", "base", "second", "frame"),
        ("normals", "base", "first", "frame"),
    ):
        for frame in range(2):
            file = f"{directory}/frame{frame}.buf"
            normal = (VertexAttributeSource("normal.buf", 12, 0, "f32x3")
                      if name == "normals" else
                      VertexAttributeSource(file, 40, 12, "f32x3"))
            draws.append(DrawCall(
                label=f"{name}-{frame}", count=3,
                conditions=[[{"var": "mode", "value": name}]],
                animation_conditions=[[{"var": variable, "value": str(frame)}]],
                ib_file=f"{index}.ib", index_size=4, position_file=file,
                position_stride=40, texcoord_file=(
                    "clock-texcoord.buf" if name == "clock" else "texcoord.buf"),
                texcoord_stride=20, normal_source=normal))
    groups = [{
        "name": "Component01", "position_file": "first/frame0.buf",
        "position_stride": 40, "texcoord_file": "texcoord.buf",
        "texcoord_stride": 20, "ib_file": "base.ib", "index_size": 4,
        "draws": draws,
    }]
    clocks = [{"id": variable, "frame_var": variable, "frame_start": 0,
               "frame_end": 1, "fps": 30} for variable in ("frame", "other")]
    def build(**kwargs):
        return mesh_builder.build_mesh_result(
            groups, str(tmp_path), animations=clocks, **kwargs)

    shared = build()
    prepare = mesh_builder._prepare_animation_family

    def without_sharing(*args, **kwargs):
        kwargs.pop("frame_cache")
        return prepare(*args, **kwargs)

    with patch.object(mesh_builder, "_prepare_animation_family", without_sharing):
        independent = build()

    def expanded(value, blob):
        if isinstance(value, dict):
            if set(value) == {"offset", "length"}:
                return bytes(blob.data[value["offset"]:
                                       value["offset"] + value["length"]])
            return {key: expanded(item, blob) for key, item in value.items()}
        if isinstance(value, list):
            return [expanded(item, blob) for item in value]
        return value

    assert len(shared.meshes) == 6
    assert expanded(shared.meshes, shared.geometry) == expanded(
        independent.meshes, independent.geometry)
    assert shared.animations == independent.animations
    assert shared.diagnostics["animation_reused_family_count"] == 2
    if animated:
        tracks = [entry["animation_geometry"] for entry in shared.meshes.values()]
        assert tracks[0]["positions"] == tracks[1]["positions"] == tracks[2]["positions"]
        assert all(track["positions"] != tracks[0]["positions"] for track in tracks[3:])
        assert tracks[2]["clock_ids"] == ["other"]
        assert len({entry["animation_id"] for entry in shared.meshes.values()}) == 6
        assert len(shared.geometry) < len(independent.geometry)
        assert shared.diagnostics["animation_geometry_bytes"] == sum(
            reference["length"] for track in tracks[2:]
            for reference in (track["positions"], track["normals"]))
        changed = bytearray((tmp_path / "first/frame1.buf").read_bytes())
        struct.pack_into("<f", changed, 0, 7.)
        reloaded = build(buffer_overrides={
            str(tmp_path / "first/frame1.buf"): bytes(changed)})
        reference = reloaded.meshes["base-0"]["animation_geometry"]["positions"]
        assert geometry_values(reloaded.geometry, reference)[9] == 7.
    else:
        assert not shared.animations
        assert all("animation_geometry" not in entry for entry in shared.meshes.values())


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

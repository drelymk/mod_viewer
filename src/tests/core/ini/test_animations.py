"""Synthetic coverage for the narrow baked-animation analysis path."""

import struct
from dataclasses import replace

import pytest

from core.ini.animations import discover_animation_clocks, frame_condition
from core.ini.analysis import analyze_ini
from core.ini.draw_scan import _scan_sections_for_draws
from core.ini.sections import extract_resources, parse_sections
from core.geometry.mesh_builder import (
    GeometryBlob, _animation_families, _static_position_records,
    _translated_frame_vertices,
    build_mesh_result,
)
from core.geometry.buffers import BufferStore, VertexStreams
from core.geometry.conventions import GeometryConvention
from core.geometry.draw_call import DrawCall
from core.geometry.packing import PreparedDrawVertices
from core.geometry.vertex_attributes import VertexAttributeSource


def _sections(text):
    return parse_sections("fixture.ini", text=text)


def test_static_switched_records_require_identical_contiguous_vertices(tmp_path):
    normal = VertexAttributeSource("first.buf", 24, 12, "f32x3")
    records = b"".join(struct.pack("<6f", *point) for point in (
        (0., 0., 0., 0., 0., 1.),
        (1., 0., 0., 0., 0., 1.),
        (0., 1., 0., 0., 0., 1.),
    ))
    (tmp_path / "first.buf").write_bytes(records)
    (tmp_path / "second.buf").write_bytes(records)
    first = DrawCall(label="first", position_file="first.buf",
                     position_stride=24, normal_source=normal)
    second = DrawCall(label="second", position_file="second.buf",
                      position_stride=24, normal_source=VertexAttributeSource(
                          "second.buf", 24, 12, "f32x3"))
    prepared = PreparedDrawVertices(
        [0, 1, 2], [0, 1, 2], {0: 0, 1: 1, 2: 2}, {},
        VertexStreams(records, 24, b"", 8, 0, "<ff"),
        str(tmp_path / "first.buf"), "")
    family = {"frame_start": 0, "draws": {0: first, 1: second}}

    def static():
        return _static_position_records(
            family, prepared, mod_dir=str(tmp_path), buffers=BufferStore(),
            source=None)

    assert static()
    changed = bytearray(records)
    struct.pack_into("<f", changed, 12, -1.)
    (tmp_path / "second.buf").write_bytes(changed)
    assert not static()
    struct.pack_into("<f", changed, 12, 0.)
    struct.pack_into("<f", changed, 0, 2.)
    (tmp_path / "second.buf").write_bytes(changed)
    assert not static()
    assert not _static_position_records(
        family, replace(prepared, used_vertices=[0, 2]),
        mod_dir=str(tmp_path), buffers=BufferStore(), source=None)


def test_translated_indices_and_uv_records_require_exact_match(tmp_path):
    uv_record = struct.pack("<ff", .25, .75)
    texcoords = uv_record * 6
    (tmp_path / "texcoord.buf").write_bytes(texcoords)
    index_path = tmp_path / "index.buf"
    index_path.write_bytes(struct.pack("<6I", 0, 1, 2, 3, 4, 5))
    canonical = PreparedDrawVertices(
        [0, 1, 2], [0, 1, 2], {0: 0, 1: 1, 2: 2}, {},
        VertexStreams(b"", 12, texcoords, 8, 0, "<ff"),
        "", str(tmp_path / "texcoord.buf"))
    draw = DrawCall(label="frame", ib_file="index.buf", start=3,
                    count=3, index_size=4, texcoord_file="texcoord.buf",
                    texcoord_stride=8)

    def translated():
        return _translated_frame_vertices(
            draw, canonical, mod_dir=str(tmp_path), buffers=BufferStore(),
            default_index_size=4, geometry_convention=GeometryConvention(),
            source=None)

    assert translated() == [3, 4, 5]
    index_path.write_bytes(struct.pack("<6I", 0, 1, 2, 3, 4, 4))
    assert translated() is None
    index_path.write_bytes(struct.pack("<6I", 0, 1, 2, 3, 4, 5))
    changed_uvs = texcoords[:-8] + struct.pack("<ff", .5, .75)
    canonical = replace(canonical, streams=replace(
        canonical.streams, texcoord_data=changed_uvs))
    assert translated() is None


def test_animation_clock_and_frame_branches_stay_out_of_toggle_state():
    sections = _sections(r"""
[KeyAnim]
type = cycle
key = a
$anim = 0, 1

[Constants]
$fps = 30
$frameStart = 0
$frameEnd = 1
$anim = 0

[Present]
if $anim == 1
$frame = (time * $fps % ($frameEnd - $frameStart + 1) + $frameStart) // 1
endif

[TextureOverrideBody]
if $anim == 1
if $frame == 0
vb0 = ResourcePosition0
vb1 = ResourceTexcoord
ib = ResourceIB
drawindexed = 3, 0, 0
elif $frame == 1
vb0 = ResourcePosition1
vb1 = ResourceTexcoord
ib = ResourceIB
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

    discovered = discover_animation_clocks(sections)
    assert len(discovered.clocks) == 1
    clock = discovered.clocks[0]
    assert (clock.frame_var, clock.fps_var, clock.fps_value,
            clock.frame_start, clock.frame_end) == (
        "frame", "fps", 30, 0, 1)
    assert clock.conditions == [[{
        "var": "anim", "value": "1", "negate": False,
    }]]

    scanned = _scan_sections_for_draws(
        sections, gating_vars={"anim"}, animation_vars=discovered.frame_vars)
    draws = scanned["TextureOverrideBody"]["draws"]
    assert [draw.conditions for draw in draws] == [
        [[{"var": "anim", "value": "1", "negate": False}]],
        [[{"var": "anim", "value": "1", "negate": False}]],
    ]
    assert [frame_condition(draw.animation_conditions, {"frame"})
            for draw in draws] == [("frame", 0), ("frame", 1)]

    analysis = analyze_ini(
        sections, resources=extract_resources(sections),
        extra_gating_vars={"anim"})
    assert "frame" not in analysis.gating_vars
    assert analysis.draw_groups[0]["draws"][0].animation_conditions


def test_animation_clock_supports_literal_fps_and_independent_ranges():
    sections = _sections(r"""
[Present]
$body = (time * 24 % ($bodyEnd - $bodyStart + 1) + $bodyStart) // 1
$face = (time * 12.5 % 4 + 1) // 1

[Constants]
$bodyStart = 1
$bodyEnd = 40
""")

    discovered = discover_animation_clocks(sections)
    by_frame = {clock.frame_var: clock for clock in discovered.clocks}
    assert set(by_frame) == {"body", "face"}
    assert (by_frame["body"].fps_var, by_frame["body"].fps_value,
            by_frame["body"].frame_start, by_frame["body"].frame_end) == (
        None, 24, 1, 40)
    assert (by_frame["face"].fps_var, by_frame["face"].fps_value,
            by_frame["face"].frame_start, by_frame["face"].frame_end) == (
        None, 12.5, 1, 4)


def test_animation_clock_supports_live_speed_multiplier():
    sections = _sections(r"""
[Constants]
global persist $fps = 50
global persist $xx = 0.9
global $frameStart = 1
global $frameEnd = 50

[Present]
$aaa = (time * $fps * $xx % ($frameEnd - $frameStart + 1) + $frameStart) // 1
""")

    clock = discover_animation_clocks(sections).clocks[0]
    assert (clock.frame_var, clock.fps_var, clock.fps_value,
            clock.speed_var, clock.speed_value,
            clock.frame_start, clock.frame_end) == (
        "aaa", "fps", 50, "xx", 0.9, 1, 50)


def test_static_vb0_bindings_are_not_animation_metadata():
    sections = _sections(r"""
[CommandListStatic]
vb0 = ResourcePosition

[ResourcePosition]
filename = position.buf
stride = 40
""")

    scanned = _scan_sections_for_draws(sections, animation_vars=set())
    assert scanned["CommandListStatic"]["animation_vertex_bindings"] == []


def test_same_frame_variable_ranges_share_one_geometry_track():
    from core.geometry.draw_call import DrawCall

    def branch(frame):
        return DrawCall(
            label="Body",
            count=3,
            animation_conditions=[[{
                "var": "swapvar", "value": str(frame), "negate": False,
            }]],
        )

    families = _animation_families(
        [branch(frame) for frame in range(3)],
        [
            {"id": "clock-a", "frame_var": "swapvar", "frame_start": 0,
             "frame_end": 1, "conditions": [[{
                 "var": "anim", "value": "1", "negate": False,
             }]]},
            {"id": "clock-b", "frame_var": "swapvar", "frame_start": 0,
             "frame_end": 2, "conditions": [[{
                 "var": "anim", "value": "4", "negate": False,
             }]]},
        ])

    assert len(families) == 1
    family = families[0]
    assert set(family["draws"]) == {0, 1, 2}
    assert list(family["clock_ids"]) == ["clock-a", "clock-b"]
    assert (family["frame_start"], family["frame_end"]) == (0, 2)


@pytest.mark.parametrize("static", [False, True])
def test_position_buffer_frames_share_one_packed_mesh(tmp_path, static):
    def write_positions(path, z):
        data = bytearray()
        for x, y in ((0., 0.), (1., 0.), (0., 1.)):
            data.extend(struct.pack("<fff", x, y, z))
            data.extend(b"\0" * 28)
        path.write_bytes(data)

    write_positions(tmp_path / "position0.buf", 0.)
    write_positions(tmp_path / "position1.buf", 0. if static else 1.)
    texcoord = bytearray()
    for u, v in ((0., 0.), (1., 0.), (0., 1.)):
        texcoord.extend(b"\0" * 4)
        texcoord.extend(struct.pack("<ee", u, v))
        texcoord.extend(b"\0" * 12)
    (tmp_path / "texcoord.buf").write_bytes(texcoord)
    (tmp_path / "index.buf").write_bytes(struct.pack("<III", 0, 1, 2))

    sections = _sections(r"""
[Constants]
$fps = 30
$frameStart = 0
$frameEnd = 1
$anim = 1

[Present]
$frame = (time * $fps % ($frameEnd - $frameStart + 1) + $frameStart) // 1

[TextureOverrideBody]
if $frame == 0
vb0 = ResourcePosition0
vb1 = ResourceTexcoord
ib = ResourceIB
drawindexed = 3, 0, 0
elif $frame == 1
vb0 = ResourcePosition1
drawindexed = 3, 0, 0
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
    analysis = analyze_ini(
        sections, resources=extract_resources(sections),
        extra_gating_vars={"anim"})
    geometry = GeometryBlob()
    built = build_mesh_result(
        analysis.draw_groups, str(tmp_path), geometry=geometry,
        animations=analysis.animations)

    assert len(built.meshes) == 1
    entry = next(iter(built.meshes.values()))
    if static:
        assert "animation_geometry" not in entry
        assert built.diagnostics["animation_static_family_count"] == 1
        assert built.diagnostics["animation_geometry_bytes"] == 0
        return
    animation = entry["animation_geometry"]
    assert animation["frames"] == 2
    assert animation["clock_ids"]
    assert animation["bounds"] == {
        "min": [0.0, 0.0, 0.0], "max": [1.0, 1.0, 1.0],
    }
    assert built.diagnostics["animation_prepare_calls"] == 1
    assert animation["position_frame_bytes"] == 36
    assert animation["positions"]["length"] == 72
    positions = geometry.to_bytes()[animation["positions"]["offset"]:]
    assert struct.unpack_from("<fff", positions, 0) == (0., 0., 0.)
    assert struct.unpack_from("<fff", positions, 36) == (0., 0., 1.)


def test_commandlist_position_bindings_form_animation_family(tmp_path):
    for name, z in (("position0.buf", 0.), ("position1.buf", 1.)):
        data = bytearray()
        for x, y in ((0., 0.), (1., 0.), (0., 1.)):
            data.extend(struct.pack("<fff", x, y, z))
            data.extend(b"\0" * 28)
        (tmp_path / name).write_bytes(data)
    texcoord = bytearray()
    for u, v in ((0., 0.), (1., 0.), (0., 1.)):
        texcoord.extend(b"\0" * 4)
        texcoord.extend(struct.pack("<ee", u, v))
        texcoord.extend(b"\0" * 12)
    (tmp_path / "texcoord.buf").write_bytes(texcoord)
    (tmp_path / "index.buf").write_bytes(struct.pack("<III", 0, 1, 2))

    sections = _sections(r"""
[Constants]
$fps = 30
$frameStart = 0
$frameEnd = 1

[Present]
$frame = (time * $fps % ($frameEnd - $frameStart + 1) + $frameStart) // 1

[TextureOverrideBody]
ib = ResourceBodyIB
vb1 = ResourceBodyTexcoord
drawindexed = 3, 0, 0

[TextureOverrideBodyBlend]
run = CommandListBodyBlend

[CommandListBodyBlend]
if $frame == 0
if DRAW_TYPE == 2
vb1 = ResourceBodyTexcoord
elif DRAW_TYPE == 1
vb0 = ResourcePosition0
endif
else if $frame == 1
if DRAW_TYPE == 2
vb1 = ResourceBodyTexcoord
elif DRAW_TYPE == 1
vb0 = ResourcePosition1
endif
endif

[ResourcePosition0]
filename = position0.buf
stride = 40
[ResourcePosition1]
filename = position1.buf
stride = 40
[ResourceBodyTexcoord]
filename = texcoord.buf
stride = 20
[ResourceBodyIB]
filename = index.buf
format = DXGI_FORMAT_R32_UINT
""")
    analysis = analyze_ini(
        sections, resources=extract_resources(sections))
    group = analysis.draw_groups[0]
    bindings = group["animation_vertex_bindings"]
    assert [frame_condition(item["animation_conditions"], {"frame"})
            for item in bindings] == [("frame", 0), ("frame", 1)]

    geometry = GeometryBlob()
    built = build_mesh_result(
        analysis.draw_groups, str(tmp_path), geometry=geometry,
        animations=analysis.animations)
    assert len(built.meshes) == 1
    entry = next(iter(built.meshes.values()))
    assert entry["animation_geometry"]["frames"] == 2
    positions = geometry.to_bytes()[
        entry["animation_geometry"]["positions"]["offset"]:]
    assert struct.unpack_from("<fff", positions, 0) == (0., 0., 0.)
    assert struct.unpack_from("<fff", positions, 36) == (0., 0., 1.)


def test_draw_range_frames_reuse_compacted_topology_and_reject_mismatch(tmp_path):
    def build(root, second_uv_offset, include_second=True):
        root.mkdir()
        positions = bytearray()
        for z in (0., 1.):
            for x, y in ((0., 0.), (1., 0.), (0., 1.)):
                positions.extend(struct.pack("<fff", x, y, z))
                positions.extend(b"\0" * 28)
        (root / "position.buf").write_bytes(positions)
        texcoord = bytearray()
        for _frame in range(2):
            for u, v in ((0., 0.), (1., 0.), (0., 1.)):
                texcoord.extend(b"\0" * 4)
                texcoord.extend(struct.pack(
                    "<ee", u, v + (second_uv_offset if _frame else 0.)))
                texcoord.extend(b"\0" * 12)
        (root / "texcoord.buf").write_bytes(texcoord)
        (root / "index.buf").write_bytes(
            struct.pack("<IIIIII", 0, 1, 2, 3, 4, 5))
        second_branch = """elif $frame == 1
drawindexed = 3, 3, 0
""" if include_second else ""
        sections = _sections(f"""
[Constants]
$frameStart = 0
$frameEnd = 1

[Present]
$frame = (time * 30 % ($frameEnd - $frameStart + 1) + $frameStart) // 1

[TextureOverrideBody]
vb0 = ResourcePosition
vb1 = ResourceTexcoord
ib = ResourceIB
if $frame == 0
drawindexed = 3, 0, 0
{second_branch}endif

[ResourcePosition]
filename = position.buf
stride = 40
[ResourceTexcoord]
filename = texcoord.buf
stride = 20
[ResourceIB]
filename = index.buf
format = DXGI_FORMAT_R32_UINT
""")
        analysis = analyze_ini(
            sections, resources=extract_resources(sections))
        geometry = GeometryBlob()
        return build_mesh_result(
            analysis.draw_groups, str(root), geometry=geometry,
            animations=analysis.animations), geometry

    def static_blob_length(built):
        entry = next(iter(built.meshes.values()))
        refs = [entry["pos"], entry["idx"]]
        if "uv" in entry:
            refs.append(entry["uv"])
        return max(ref["offset"] + ref["length"] for ref in refs)

    def static_blob_bytes(built):
        entry = next(iter(built.meshes.values()))
        refs = [entry["pos"], entry["idx"]]
        if "uv" in entry:
            refs.append(entry["uv"])
        return sum(ref["length"] for ref in refs)

    compatible, compatible_geometry = build(tmp_path / "compatible", 0.)
    compatible_entries = list(compatible.meshes.values())
    assert len(compatible_entries) == 1
    assert compatible_entries[0]["animation_geometry"]["frames"] == 2
    assert compatible.diagnostics["animation_prepare_calls"] == 1
    assert compatible.diagnostics["animation_translated_family_count"] == 1
    assert compatible.diagnostics["animation_translated_frame_count"] == 1
    assert len(compatible_geometry) > static_blob_bytes(compatible)

    mismatched, mismatched_geometry = build(tmp_path / "mismatched", 0.25)
    mismatched_entries = list(mismatched.meshes.values())
    assert len(mismatched_entries) == 1
    assert "animation_geometry" not in mismatched_entries[0]
    assert mismatched.diagnostics["animation_prepare_calls"] == 2
    assert len(mismatched_geometry) == static_blob_length(mismatched)

    missing, missing_geometry = build(
        tmp_path / "missing", 0., include_second=False)
    missing_entries = list(missing.meshes.values())
    assert len(missing_entries) == 1
    assert "animation_geometry" not in missing_entries[0]
    assert len(missing_geometry) == static_blob_length(missing)

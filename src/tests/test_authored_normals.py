"""Authored vertex-normal detection, decoding, and mesh compaction tests."""

import os
import struct

from core.draw_call import DrawCall
from core.ini_parser import (build_draw_groups, extract_resources,
                             merge_sections)
from core.mesh_builder import GeometryBlob, build_mesh_result
from core.vertex_attributes import (VertexAttributeSource, decode_normal,
                                    decode_normals, decode_snorm8)


def _write(path, name, data):
    target = os.path.join(path, name)
    with open(target, "wb") as file:
        file.write(data)
    return target


def _parse_groups(tmp_path, text):
    ini = tmp_path / "mod.ini"
    ini.write_text(text, encoding="utf-8")
    sections = merge_sections([str(ini)])
    return build_draw_groups(sections, extract_resources(sections))


def _build(groups, tmp_path):
    geometry = GeometryBlob()
    result = build_mesh_result(groups, str(tmp_path), geometry=geometry)
    return result.meshes, geometry


def _values(geometry, reference):
    raw = geometry.data[reference["offset"]:
                       reference["offset"] + reference["length"]]
    return struct.unpack(f"<{len(raw) // 4}f", raw)


INTERLEAVED_INI = """[TextureOverrideBody]
ib = ResourceIB
vb0 = ResourcePosition
vb1 = ResourceTexcoord
drawindexed = 3, 0, 0

[ResourceIB]
filename = body.ib
format = DXGI_FORMAT_R32_UINT

[ResourcePosition]
filename = position.buf
stride = 40

[ResourceTexcoord]
filename = texcoord.buf
stride = 20
"""


def test_interleaved_authored_normals_follow_vertex_id_compaction(tmp_path):
    groups = _parse_groups(tmp_path, INTERLEAVED_INI)
    assert groups[0]["draws"][0].normal_source == VertexAttributeSource(
        "position.buf", 40, 12, "f32x3")

    vertices = [
        ((0., 0., 0.), (1., 0., 0.)),
        ((1., 0., 0.), (0., 0., 1.)),
        ((0., 1., 0.), (0., 0., 1.)),
        ((4., 4., 4.), (0., 0., 1.)),
        ((0., 0., 0.), (0., 1., 0.)),
    ]
    position = bytearray()
    for point, normal in vertices:
        position.extend(struct.pack("<3f3f4f", *point, *normal,
                                     1., 0., 0., 1.))
    _write(tmp_path, "position.buf", position)
    _write(tmp_path, "texcoord.buf", b"\0" * (20 * len(vertices)))
    _write(tmp_path, "body.ib", struct.pack("<3I", 0, 1, 4))

    meshes, geometry = _build(groups, tmp_path)
    entry = next(iter(meshes.values()))
    assert _values(geometry, entry["pos"]) == (
        0., 0., 0., 1., 0., 0., 0., 0., 0.)
    assert _values(geometry, entry["normal"]) == (
        1., 0., 0., 0., 0., 1., 0., 1., 0.)


PACKED_INI = """[TextureOverrideBody]
ib = ResourceIB
vb0 = ResourcePosition
vb1 = ResourceVector
vb2 = ResourceTexcoord
drawindexed = 3, 0, 0

[ResourceIB]
filename = body.ib
format = DXGI_FORMAT_R32_UINT

[ResourcePosition]
filename = position.buf
stride = 12

[ResourceVector]
filename = vector.buf
stride = 8
format = DXGI_FORMAT_R8G8B8A8_SNORM

[ResourceTexcoord]
filename = texcoord.buf
stride = 20
"""


def test_packed_vector_normals_are_detected_and_decoded(tmp_path):
    groups = _parse_groups(tmp_path, PACKED_INI)
    draw = groups[0]["draws"][0]
    assert draw.normal_source == VertexAttributeSource(
        "vector.buf", 8, 4, "snorm8x3")
    _write(tmp_path, "position.buf", struct.pack(
        "<9f", 0., 0., 0., 1., 0., 0., 0., 1., 0.))
    _write(tmp_path, "texcoord.buf", b"\0" * 60)
    _write(tmp_path, "body.ib", struct.pack("<3I", 0, 1, 2))
    _write(tmp_path, "vector.buf", bytes((0, 0, 0, 0, 127, 0, 0, 0,
                                             0, 0, 0, 0, 0, 127, 0, 0,
                                             0, 0, 0, 0, 0, 0, 127, 0)))

    meshes, geometry = _build(groups, tmp_path)
    entry = next(iter(meshes.values()))
    normals = _values(geometry, entry["normal"])
    assert normals[0:3] == (1., 0., 0.)
    assert normals[3:6] == (0., 1., 0.)
    assert normals[6:9] == (0., 0., 1.)


def _packed_normals(values):
    return b"".join(bytes((0, 0, 0, 0, *normal, 0))
                     for normal in values)


def _write_packed_triangle(tmp_path, normal_values, draw_text=PACKED_INI):
    groups = _parse_groups(tmp_path, draw_text)
    _write(tmp_path, "position.buf", struct.pack(
        "<9f", 0., 0., 0., 1., 0., 0., 0., 1., 0.))
    _write(tmp_path, "texcoord.buf", b"\0" * 60)
    _write(tmp_path, "body.ib", struct.pack("<3I", 0, 1, 2))
    _write(tmp_path, "vector.buf", _packed_normals(normal_values))
    return _build(groups, tmp_path)


def test_reversed_authored_normals_follow_indexed_geometry_orientation(tmp_path):
    meshes, geometry = _write_packed_triangle(
        tmp_path, [(0, 0, 128), (0, 0, 128), (0, 0, 128)])
    entry = next(iter(meshes.values()))
    assert _values(geometry, entry["normal"]) == (
        0., 0., 1., 0., 0., 1., 0., 0., 1.)


def test_aligned_authored_normals_keep_their_declared_orientation(tmp_path):
    meshes, geometry = _write_packed_triangle(
        tmp_path, [(0, 0, 127), (0, 0, 127), (0, 0, 127)])
    entry = next(iter(meshes.values()))
    assert _values(geometry, entry["normal"]) == (
        0., 0., 1., 0., 0., 1., 0., 0., 1.)


def test_shared_normal_source_uses_one_orientation_for_all_draws(tmp_path):
    draw_text = PACKED_INI.replace(
        "drawindexed = 3, 0, 0",
        "drawindexed = 3, 0, 0\n"
        "drawindexed = 3, 3, 0\n"
        "drawindexed = 3, 6, 0")
    groups = _parse_groups(tmp_path, draw_text)
    points = [(0., 0., 0.), (1., 0., 0.), (0., 1., 0.)] * 3
    _write(tmp_path, "position.buf", b"".join(
        struct.pack("<3f", *point) for point in points))
    _write(tmp_path, "texcoord.buf", b"\0" * (20 * len(points)))
    _write(tmp_path, "body.ib", struct.pack("<9I", *range(9)))
    # The first draw is perpendicular/noisy; the other two clearly reverse
    # the same source. The source-wide decision must still flip every draw.
    _write(tmp_path, "vector.buf", _packed_normals(
        [(127, 0, 0)] * 3 +
        [(0, 0, 128)] * 6))

    meshes, geometry = _build(groups, tmp_path)
    assert len(meshes) == 3
    first = _values(geometry, meshes["Body-1"]["normal"])
    second = _values(geometry, meshes["Body-2"]["normal"])
    third = _values(geometry, meshes["Body-3"]["normal"])
    assert first == (-1., 0., 0., -1., 0., 0., -1., 0., 0.)
    assert second == (0., 0., 1., 0., 0., 1., 0., 0., 1.)
    assert third == (0., 0., 1., 0., 0., 1., 0., 0., 1.)


def test_ambiguous_orientation_samples_do_not_consume_shared_source_budget(
        tmp_path):
    triangles_per_draw = 4096
    vertices_per_draw = triangles_per_draw * 3
    draw_text = PACKED_INI.replace(
        "drawindexed = 3, 0, 0",
        "\n".join(
            f"drawindexed = {vertices_per_draw}, "
            f"{draw_index * vertices_per_draw}, 0"
            for draw_index in range(5)))
    groups = _parse_groups(tmp_path, draw_text)
    triangle = (struct.pack("<3f", 0., 0., 0.) +
                struct.pack("<3f", 1., 0., 0.) +
                struct.pack("<3f", 0., 1., 0.))
    _write(tmp_path, "position.buf", triangle * (triangles_per_draw * 5))
    _write(tmp_path, "texcoord.buf", b"\0" * (20 * vertices_per_draw * 5))
    _write(tmp_path, "body.ib", struct.pack(
        f"<{vertices_per_draw * 5}I", *range(vertices_per_draw * 5)))
    # Four draws contain only perpendicular evidence. The fifth draw is
    # decisively reversed and must still reach the source-wide decision.
    _write(tmp_path, "vector.buf", _packed_normals(
        [(127, 0, 0)] * (vertices_per_draw * 4) +
        [(0, 0, 128)] * vertices_per_draw))

    meshes, geometry = _build(groups, tmp_path)
    assert len(meshes) == 5
    for draw_index in range(5):
        values = _values(geometry, meshes[f"Body-{draw_index + 1}"]["normal"])
        expected = ((-1., 0., 0.) if draw_index < 4
                    else (0., 0., 1.)) * 3
        assert values[:9] == expected


def test_unrelated_vb1_layout_is_not_treated_as_authored_normals(tmp_path):
    layouts = (
        ("ResourceColor", "8", "DXGI_FORMAT_R8G8B8A8_UNORM"),
        ("ResourceBlend", "32", "DXGI_FORMAT_R32G32B32A32_FLOAT"),
        ("ResourceTexcoord", "20", "DXGI_FORMAT_R32G32_FLOAT"),
    )
    for resource, stride, format_name in layouts:
        text = PACKED_INI.replace("vb1 = ResourceVector", f"vb1 = {resource}")
        text = text.replace("[ResourceVector]", f"[{resource}]")
        text = text.replace("filename = vector.buf", "filename = other.buf")
        text = text.replace("stride = 8", f"stride = {stride}")
        text = text.replace(
            "format = DXGI_FORMAT_R8G8B8A8_SNORM", f"format = {format_name}")
        groups = _parse_groups(tmp_path, text)
        assert groups[0]["draws"][0].normal_source is None


def test_effective_vector_reassignment_and_explicit_null_are_distinct(tmp_path):
    text = PACKED_INI.replace(
        "drawindexed = 3, 0, 0",
        "drawindexed = 3, 0, 0\n"
        "vb1 = ResourceVectorB\n"
        "drawindexed = 3, 0, 0\n"
        "vb1 = null\n"
        "drawindexed = 3, 0, 0",
        1).replace(
            "[ResourceVector]\nfilename = vector.buf",
            "[ResourceVector]\nfilename = vector-a.buf\n")
    text += "\n[ResourceVectorB]\nfilename = vector-b.buf\nstride = 8\nformat = DXGI_FORMAT_R8G8B8A8_SNORM\n"
    groups = _parse_groups(tmp_path, text)
    draws = groups[0]["draws"]
    assert [draw.normal_source.file if draw.normal_source else None
            for draw in draws] == ["vector-a.buf", "vector-b.buf", None]
    assert len({draw.render_identity() for draw in draws}) == 3
    _write(tmp_path, "position.buf", struct.pack(
        "<9f", 0., 0., 0., 1., 0., 0., 0., 1., 0.))
    _write(tmp_path, "texcoord.buf", b"\0" * 60)
    _write(tmp_path, "body.ib", struct.pack("<3I", 0, 1, 2))
    vector = bytes((0, 0, 0, 0, 127, 0, 0, 0,
                    0, 0, 0, 0, 0, 127, 0, 0,
                    0, 0, 0, 0, 0, 0, 127, 0))
    _write(tmp_path, "vector-a.buf", vector)
    _write(tmp_path, "vector-b.buf", vector)
    meshes, _geometry = _build(groups, tmp_path)
    assert len(meshes) == 3
    assert "normal" in meshes["Body-1"]
    assert "normal" in meshes["Body-2"]
    assert "normal" not in meshes["Body-3"]


def test_missing_or_unsafe_authored_source_falls_back_without_normal_payload(tmp_path):
    groups = _parse_groups(tmp_path, INTERLEAVED_INI)
    _write(tmp_path, "texcoord.buf", b"\0" * 60)
    _write(tmp_path, "body.ib", struct.pack("<3I", 0, 1, 2))
    # The structurally recognized source is missing, so it must not become a
    # partial geometry field.
    _write(tmp_path, "position.buf", b"\0" * 40 * 3)
    meshes, _geometry = _build(groups, tmp_path)
    assert "normal" not in next(iter(meshes.values()))
    groups[0]["draws"][0].normal_source = VertexAttributeSource(
        "../outside.buf", 40, 12, "f32x3")
    meshes, _geometry = _build(groups, tmp_path)
    assert "normal" not in next(iter(meshes.values()))


def test_draw_mapping_inherits_group_normal_source():
    source = VertexAttributeSource("position.buf", 40, 12, "f32x3")
    draw = DrawCall.from_mapping({"label": "Body-1"}, {
        "normal_source": source,
    })
    assert draw.normal_source is source


def test_normal_decoder_boundaries_truncation_and_zero_vectors():
    assert decode_snorm8(0) == 0.0
    assert decode_snorm8(127) == 1.0
    assert decode_snorm8(128) == -1.0
    assert decode_snorm8(255) == -1.0 / 127.0
    source = VertexAttributeSource("vector.buf", 8, 4, "snorm8x3")
    data = bytes((0, 0, 0, 0, 127, 0, 0, 0))
    assert decode_normal(source, data, 0) == (1., 0., 0.)
    assert decode_normals(source, data[:6], [0]) is None
    assert decode_normals(source, bytes(8), [0]) is None
    f32_source = VertexAttributeSource("position.buf", 12, 0, "f32x3")
    assert decode_normals(f32_source, struct.pack("<3f", 100., 0., 0.), [0]) is None

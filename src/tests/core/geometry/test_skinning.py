"""Focused tests for the opt-in skin-weight preview experiment."""

import math
import struct

import pytest

from core.geometry.draw_call import DrawCall
from core.geometry.skinning import (
    SkinningSource, decode_skinning, normalize_skinning_source_file,
    resolve_skinning_source, skinning_source_descriptor, skinning_source_key,
)


def unpack_values(raw, fmt):
    return struct.unpack(f"<{fmt}", raw)


class IndexedMapping:
    """Sequence fixture that exposes accidental decoder iteration/copying."""

    def __init__(self, values):
        self.values = list(values)

    def __len__(self):
        return len(self.values)

    def __getitem__(self, index):
        return self.values[index]

    def __iter__(self):
        raise AssertionError("decoder should consume the retained mapping")


def test_decode_consumes_retained_vertex_mapping_without_materializing_tuple():
    source = SkinningSource("blend.buf", 4, 1, "rigid_u32_1")

    decoded = decode_skinning(
        source, struct.pack("<2I", 7, 11), IndexedMapping([0, 1]))

    assert unpack_values(decoded.indices, "2I") == (7, 11)


def test_decode_gimi_four_influences_to_canonical_bytes():
    source = SkinningSource("blend.buf", 32, 4, "gimi_f32_u32_4")
    raw = struct.pack("<4f4I", .6, .3, .1, 0., 7, 8, 9, 0)

    decoded = decode_skinning(source, raw, [0])

    assert unpack_values(decoded.indices, "4I") == (7, 8, 9, 0)
    assert unpack_values(decoded.weights, "4f") == pytest.approx((.6, .3, .1, 0.))
    assert decoded.bone_ids == (7, 8, 9)
    assert decoded.diagnostics["invalid_weight_vertices"] == 0
    assert decoded.bone_stats == {
        7: {"affected_vertex_count": 1, "total_weight": pytest.approx(.6)},
        8: {"affected_vertex_count": 1, "total_weight": pytest.approx(.3)},
        9: {"affected_vertex_count": 1, "total_weight": pytest.approx(.1)},
    }


def test_decode_wwmi_four_influences_divides_bytes_by_255():
    source = SkinningSource("blend.buf", 8, 4, "wwmi_u8_4")

    decoded = decode_skinning(source, bytes([3, 5, 7, 0, 128, 64, 63, 0]), [0])

    assert unpack_values(decoded.indices, "4I") == (3, 5, 7, 0)
    assert unpack_values(decoded.weights, "4f") == pytest.approx(
        (128 / 255, 64 / 255, 63 / 255, 0.))


def test_decode_wwmi_wide_keeps_all_eight_influences():
    source = SkinningSource("blend.buf", 16, 8, "wwmi_u8_8")
    raw = bytes(range(8)) + bytes([1, 2, 3, 4, 5, 6, 7, 8])

    decoded = decode_skinning(source, raw, [0])

    assert unpack_values(decoded.indices, "8I") == tuple(range(8))
    assert unpack_values(decoded.weights, "8f") == pytest.approx(
        tuple(value / 255 for value in range(1, 9)))
    assert decoded.influence_count == 8


def test_decode_wwmi_vertex_vg_remap_keeps_colliding_raw_indices_distinct():
    source = SkinningSource(
        "blend.buf", 16, 8, "wwmi_u8_8", 142,
        "remap.buf", 16, "wwmi_vertex_vg")
    raw = bytes([3] * 8) + bytes([255, 128, 64, 32, 16, 8, 4, 2])
    remap = struct.pack("<8H", 3, 259, 45, 257, 0, 1, 2, 3)

    decoded = decode_skinning(source, raw, [0], remap)

    assert unpack_values(decoded.indices, "8I") == (3, 259, 45, 257, 0, 1, 2, 3)
    assert unpack_values(decoded.weights, "8f") == pytest.approx(
        tuple(value / 255 for value in [255, 128, 64, 32, 16, 8, 4, 2]))
    assert decoded.bone_ids == (0, 1, 2, 3, 45, 257, 259)
    assert decoded.bone_stats[259] == {
        "affected_vertex_count": 1, "total_weight": pytest.approx(128 / 255),
    }
    assert decoded.diagnostics["bone_id_namespace"] == "wwmi_vertex_vg"
    assert decoded.diagnostics["vertex_vg_remap"] is True


def test_decode_bone_stats_count_each_vertex_once_and_sum_duplicate_slots():
    source = SkinningSource("blend.buf", 8, 4, "wwmi_u8_4")
    raw = bytes([2, 2, 3, 0, 128, 64, 32, 0])

    decoded = decode_skinning(source, raw, [0])

    assert decoded.bone_stats == {
        2: {"affected_vertex_count": 1, "total_weight": pytest.approx(192 / 255)},
        3: {"affected_vertex_count": 1, "total_weight": pytest.approx(32 / 255)},
    }


def test_decode_rigid_uses_one_implicit_weight():
    source = SkinningSource("blend.buf", 4, 1, "rigid_u32_1")

    decoded = decode_skinning(source, struct.pack("<I", 27), [0])

    assert unpack_values(decoded.indices, "I") == (27,)
    assert unpack_values(decoded.weights, "f") == (1.0,)
    assert decoded.bone_ids == (27,)


def test_decode_normalizes_component_offsets_into_model_namespace():
    raw = bytes([1, 0, 0, 0, 255, 0, 0, 0])
    first = decode_skinning(
        SkinningSource("body.blend", 8, 4, "wwmi_u8_4", 0), raw, [0])
    second = decode_skinning(
        SkinningSource("face.blend", 8, 4, "wwmi_u8_4", 10), raw, [0])
    repeated = decode_skinning(
        SkinningSource("shared.blend", 8, 4, "wwmi_u8_4", 0), raw, [0])

    assert unpack_values(first.indices, "4I")[0] == 1
    assert unpack_values(second.indices, "4I")[0] == 11
    assert unpack_values(repeated.indices, "4I")[0] == 1
    assert first.bone_ids == (1,)
    assert second.bone_ids == (11,)
    assert first.diagnostics["bone_id_namespace"] == "model"
    assert second.diagnostics["bone_id_offset"] == 10


def test_decode_malformed_records_are_safe_and_diagnostic():
    source = SkinningSource("blend.buf", 32, 4, "gimi_f32_u32_4")
    raw = struct.pack("<4f4I", math.nan, .3, .1, 0., 7, 8, 9, 0)

    decoded = decode_skinning(source, raw, [0, 1])

    assert decoded.diagnostics["invalid_weight_vertices"] == 1
    assert decoded.diagnostics["truncated_vertices"] == 1
    assert unpack_values(decoded.weights, "8f") == (0., 0., 0., 0., 0., 0., 0., 0.)


def test_decode_rejects_unsupported_stride():
    source = SkinningSource("blend.buf", 12, 4, "unsupported")

    with pytest.raises(ValueError, match="Unsupported skinning encoding"):
        decode_skinning(source, b"", [0])


@pytest.mark.parametrize(
    ("stride", "fmt", "encoding"),
    [
        (32, "", "gimi_f32_u32_4"),
        (8, "DXGI_FORMAT_R8_UINT", "wwmi_u8_4"),
        (16, "DXGI_FORMAT_R8_UINT", "wwmi_u8_8"),
        (4, "DXGI_FORMAT_R32_UINT", "rigid_u32_1"),
    ],
)
def test_resolver_accepts_known_blend_layouts(stride, fmt, encoding):
    resources = {"ResourceBlendBuffer": {
        "filename": "blend.buf", "stride": stride, "format": fmt,
    }}
    remap_resources = None
    expected_remap = None
    if encoding.startswith("wwmi_"):
        resources["ResourceVertexVG"] = {
            "filename": "remap.buf", "stride": 8 if stride == 8 else 16,
            "format": "DXGI_FORMAT_R16_UINT",
        }
        remap_resources = {35: "ResourceVertexVG"}
        expected_remap = "remap.buf"

    source, error = resolve_skinning_source(
        {4: "ResourceBlendBuffer"}, resources.get,
        remap_resources=remap_resources)

    assert error is None
    assert source == SkinningSource(
        "blend.buf", stride,
        8 if stride == 16 else (4 if stride in (8, 32) else 1), encoding,
        vertex_vg_file=expected_remap,
        vertex_vg_stride=(8 if stride == 8 else 16),
        bone_id_namespace=("wwmi_vertex_vg" if expected_remap else "model"))


def test_resolver_accepts_wwmi_source_without_vertex_vg_remap():
    source, error = resolve_skinning_source(
        {1: "ResourceBlendBuffer"},
        {"ResourceBlendBuffer": {
            "filename": "blend.buf", "stride": 8,
            "format": "DXGI_FORMAT_R8_UINT",
        }}.get)

    assert error is None
    assert source == SkinningSource(
        "blend.buf", 8, 4, "wwmi_u8_4", bone_id_namespace="model")
    decoded = decode_skinning(
        source, bytes([3, 4, 5, 6, 255, 128, 64, 32]), [0])
    assert decoded.bone_ids == (3, 4, 5, 6)
    assert decoded.diagnostics["vertex_vg_remap"] is False


def test_resolver_carries_the_authored_model_bone_offset():
    resources = {
        "ResourceBlendBuffer": {
            "filename": "blend.buf", "stride": 8,
            "format": "DXGI_FORMAT_R8_UINT",
        },
        "ResourceVertexVG": {
            "filename": "remap.buf", "stride": 8,
            "format": "DXGI_FORMAT_R16_UINT",
        },
    }

    source, error = resolve_skinning_source(
        {1: "ResourceBlendBuffer"}, resources.get, bone_id_offset=12,
        remap_resources={35: "ResourceVertexVG"})

    assert error is None
    assert source.bone_id_offset == 12


def test_resolver_uses_bound_vertex_vg_resource_without_applying_offset():
    resources = {
        "ResourceBlendBuffer": {
            "filename": "blend.buf", "stride": 16,
            "format": "DXGI_FORMAT_R8_UINT",
        },
        "ResourceBlendRemapVertexVGBuffer": {
            "filename": "remap.buf", "stride": 16,
            "format": "DXGI_FORMAT_R16_UINT",
        },
    }

    source, error = resolve_skinning_source(
        {1: "ResourceBlendBuffer"}, resources.get, bone_id_offset=142,
        remap_resources={35: "ResourceBlendRemapVertexVGBuffer"})

    assert error is None
    assert source == SkinningSource(
        "blend.buf", 16, 8, "wwmi_u8_8", 142,
        "remap.buf", 16, "wwmi_vertex_vg")
    assert skinning_source_descriptor(source) == {
        "key": "blend.buf|offset=142|namespace=wwmi_vertex_vg|vertex-vg=remap.buf",
        "file": "blend.buf", "bone_id_offset": 142,
        "bone_id_namespace": "wwmi_vertex_vg",
        "vertex_vg_source": "remap.buf",
    }


def test_resolver_rejects_invalid_vertex_vg_resource():
    resources = {
        "ResourceBlendBuffer": {
            "filename": "blend.buf", "stride": 16,
            "format": "DXGI_FORMAT_R8_UINT",
        },
        "ResourceRemap": {
            "filename": "remap.buf", "stride": 8,
            "format": "DXGI_FORMAT_R16_UINT",
        },
    }

    source, error = resolve_skinning_source(
        {1: "ResourceBlendBuffer"}, resources.get,
        remap_resources={35: "ResourceRemap"})

    assert source is None
    assert error == "invalid_vertex_vg_remap"


@pytest.mark.parametrize(
    ("source_file", "offset", "expected"),
    [
        ("BodyBlend.buf", 0, "bodyblend.buf|offset=0"),
        ("BodyBlend2.buf", 0, "bodyblend2.buf|offset=0"),
        (r"Hair\.\HairBlend.buf", 0,
         "hair/hairblend.buf|offset=0"),
        ("Hair/HairBlend.buf", 24, "hair/hairblend.buf|offset=24"),
        ("Accessory/HairBlend.buf", 0,
         "accessory/hairblend.buf|offset=0"),
    ],
)
def test_skinning_source_key_uses_relative_path_and_offset(
        source_file, offset, expected):
    assert skinning_source_key(source_file, offset) == expected


def test_skinning_source_normalization_accepts_safe_relative_paths():
    assert normalize_skinning_source_file("BodyBlend.buf") == "BodyBlend.buf"
    assert normalize_skinning_source_file("Hair/HairBlend.buf") == \
        "Hair/HairBlend.buf"
    assert normalize_skinning_source_file("./Hair/HairBlend.buf") == \
        "Hair/HairBlend.buf"
    assert normalize_skinning_source_file(r"Hair\HairBlend.buf") == \
        "Hair/HairBlend.buf"
    assert normalize_skinning_source_file("../Shared/SharedBlend.buf") == \
        "../Shared/SharedBlend.buf"


def test_skinning_source_normalization_rejects_unsafe_paths():
    assert normalize_skinning_source_file("../../escape.buf") is None
    assert normalize_skinning_source_file("/absolute/path.buf") is None
    assert normalize_skinning_source_file("C:/Hair/HairBlend.buf") is None


def test_skinning_source_descriptor_excludes_decoder_details():
    source = SkinningSource(
        r"Hair\HairBlend.buf", 8, 4, "wwmi_u8_4", bone_id_offset=24)

    assert skinning_source_descriptor(source) == {
        "key": "hair/hairblend.buf|offset=24",
        "file": "Hair/HairBlend.buf",
        "bone_id_offset": 24,
    }


def test_resolver_does_not_infer_blend_from_stride_alone():
    source, error = resolve_skinning_source(
        {1: "ResourceSomething"},
        lambda _name: {"filename": "stream.buf", "stride": 8},
    )

    assert source is None
    assert error is None


def test_resolver_reports_ambiguous_active_blend_candidates():
    resources = {
        "ResourceBlendA": {"filename": "a.buf", "stride": 32},
        "ResourceBlendB": {"filename": "b.buf", "stride": 32},
    }

    source, error = resolve_skinning_source(
        {1: "ResourceBlendA", 3: "ResourceBlendB"}, resources.get)

    assert source is None
    assert error == "ambiguous_skinning_source"


def test_skinning_metadata_is_not_part_of_render_identity():
    first = DrawCall(
        count=3, start=0, base=0,
        skinning_source=SkinningSource("a.buf", 32, 4, "gimi_f32_u32_4"))
    second = DrawCall(
        count=3, start=0, base=0,
        skinning_source=SkinningSource("b.buf", 8, 4, "wwmi_u8_4"))

    assert first.render_identity() == second.render_identity()

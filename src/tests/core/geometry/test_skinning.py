"""Focused tests for the opt-in skin-weight preview experiment."""

import math
import struct

import pytest

from core.geometry.draw_call import DrawCall
from core.geometry.skinning import (
    SkinningSource, decode_skinning, resolve_skinning_source,
)


def unpack_values(raw, fmt):
    return struct.unpack(f"<{fmt}", raw)


def test_decode_gimi_four_influences_to_canonical_bytes():
    source = SkinningSource("blend.buf", 32, 4, "gimi_f32_u32_4")
    raw = struct.pack("<4f4I", .6, .3, .1, 0., 7, 8, 9, 0)

    decoded = decode_skinning(source, raw, [0])

    assert unpack_values(decoded.indices, "4I") == (7, 8, 9, 0)
    assert unpack_values(decoded.weights, "4f") == pytest.approx((.6, .3, .1, 0.))
    assert decoded.bone_ids == (7, 8, 9)
    assert decoded.diagnostics["invalid_weight_vertices"] == 0


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

    source, error = resolve_skinning_source(
        {4: "ResourceBlendBuffer"}, resources.get)

    assert error is None
    assert source == SkinningSource(
        "blend.buf", stride,
        8 if stride == 16 else (4 if stride in (8, 32) else 1), encoding)


def test_resolver_carries_the_authored_model_bone_offset():
    resources = {"ResourceBlendBuffer": {
        "filename": "blend.buf", "stride": 8,
        "format": "DXGI_FORMAT_R8_UINT",
    }}

    source, error = resolve_skinning_source(
        {1: "ResourceBlendBuffer"}, resources.get, bone_id_offset=12)

    assert error is None
    assert source.bone_id_offset == 12


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

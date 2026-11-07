"""Shared array-layout and scalar-equivalent normal decoding contracts."""

import random
import struct
from unittest.mock import patch

import numpy as np
import pytest

from core.geometry import vertex_attributes as attributes
from core.geometry.array_views import UnsupportedLayout, strided_view
from core.geometry.vertex_attributes import VertexAttributeSource


def _records(encoding, stride, offset, count=513):
    rng = random.Random(937)
    data = bytearray(count * stride)
    for index in range(count):
        if encoding == "f32x3":
            values = ((-0., 0., 1.) if index == 0 else
                      (rng.uniform(-.5, .5), rng.uniform(-.5, .5), 1.))
            struct.pack_into("<3f", data, index * stride + offset, *values)
        else:
            # Cover every signed-byte value, including the -128 clamp.
            struct.pack_into("3B", data, index * stride + offset,
                             index % 256, rng.randrange(256), 127)
    return data


@pytest.mark.parametrize("encoding,width", [("f32x3", 12), ("snorm8x3", 3)])
@pytest.mark.parametrize("padding,offset", [(0, 0), (7, 3), (28, 8)])
@pytest.mark.parametrize("sequence", ["list", "tuple", "range", "generator", "array"])
def test_batch_matches_scalar_bytes_with_order_duplicates_and_chunk_boundaries(
        monkeypatch, encoding, width, padding, offset, sequence):
    stride = width + padding + offset
    source = VertexAttributeSource("normal.buf", stride, offset, encoding)
    data = _records(encoding, stride, offset)
    indices = list(range(512, -1, -1)) + [0, 512, 0, 9]
    if sequence == "tuple":
        indices = tuple(indices)
    elif sequence == "range":
        indices = range(512, -1, -1)
    elif sequence == "array":
        indices = np.asarray(indices, dtype=np.uint64)
    expected = attributes._decode_normals_python(source, data, indices)
    assert expected is not None
    if sequence == "generator":
        indices = (index for index in indices)
    monkeypatch.setattr(attributes, "_MIN_BATCH_NORMALS", 0)
    monkeypatch.setattr(attributes, "_NORMAL_BATCH_SIZE", 127)
    with patch.object(attributes, "_decode_normals_python",
                      side_effect=AssertionError("unexpected scalar fallback")):
        actual = attributes.decode_normals(source, data, indices)
    assert isinstance(actual, bytearray)
    assert actual == expected


@pytest.mark.parametrize("encoding,width", [("f32x3", 12), ("snorm8x3", 3)])
@pytest.mark.parametrize("zero_reads", [False, True])
@pytest.mark.parametrize("fault", ["partial", "missing", "all_missing", "negative", "zero", "late_zero"])
def test_invalid_and_missing_records_keep_whole_stream_semantics(
        monkeypatch, encoding, width, zero_reads, fault):
    source = VertexAttributeSource("normal.buf", width + 4, 2, encoding)
    data = _records(encoding, source.stride, source.offset, count=12)
    indices = [0, 11, 0, 3, 4, 5, 6, 7, 8, 9, 10, 11]
    if fault == "partial":
        del data[11 * source.stride + source.offset + width - 1:]
    elif fault == "missing":
        indices.extend([12, 99])
    elif fault == "all_missing":
        indices = [12, 99]
    elif fault == "negative":
        indices.append(-1)
    else:
        vertex = 0 if fault == "zero" else 11
        if fault == "late_zero":
            indices = list(range(12))
        start = vertex * source.stride + source.offset
        data[start:start + width] = bytes(width)
    monkeypatch.setattr(attributes, "_MIN_BATCH_NORMALS", 0)
    monkeypatch.setattr(attributes, "_NORMAL_BATCH_SIZE", 5)
    expected = attributes._decode_normals_python(
        source, data, indices, zero_out_of_bounds=zero_reads)
    assert attributes.decode_normals(
        source, data, indices, zero_out_of_bounds=zero_reads) == expected


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"),
                                  1e-13, .09999999, .1, .5, 1.5, 4., 4.000001])
def test_float_normal_validity_boundaries_match_scalar(monkeypatch, value):
    source = VertexAttributeSource("normal.buf", 12, 0, "f32x3")
    data = struct.pack("<3f", value, -0., 0.)
    monkeypatch.setattr(attributes, "_MIN_BATCH_NORMALS", 0)
    assert attributes.decode_normals(source, data, [0]) == \
        attributes._decode_normals_python(source, data, [0])


@pytest.mark.parametrize("plausible,total", [(5, 8), (6, 8), (7, 8), (0, 7)])
def test_plausibility_is_global_across_chunks_and_excludes_zero_reads(
        monkeypatch, plausible, total):
    source = VertexAttributeSource("normal.buf", 12, 0, "f32x3")
    data = b"".join(struct.pack("<3f", 1. if index < plausible else .2, 0., 0.)
                    for index in range(total))
    indices = [total + 1, *range(total), total + 2]
    monkeypatch.setattr(attributes, "_MIN_BATCH_NORMALS", 0)
    monkeypatch.setattr(attributes, "_NORMAL_BATCH_SIZE", 3)
    expected = attributes._decode_normals_python(
        source, data, indices, zero_out_of_bounds=True)
    assert attributes.decode_normals(source, data, indices, zero_out_of_bounds=True) == expected
    assert (expected is not None) == (total < 8 or plausible / total >= .75)


@pytest.mark.parametrize("case", [
    "overlap", "large_offset", "huge_index", "bool_indices", "mapping", "set",
])
def test_unusual_inputs_keep_scalar_fallback(monkeypatch, case):
    source = VertexAttributeSource("normal.buf", 12, 0, "f32x3")
    data = struct.pack("<12f", *((1., 0., 0.) * 4))
    indices = [0, 1]
    if case == "overlap":
        source = VertexAttributeSource("normal.buf", 4, 0, "f32x3")
    elif case == "large_offset":
        source = VertexAttributeSource("normal.buf", 12, 12, "f32x3")
    elif case == "huge_index":
        indices = [0, 2 ** 100]
    elif case == "bool_indices":
        indices = [False, True]
    elif case == "mapping":
        indices = dict.fromkeys(range(attributes._MIN_BATCH_NORMALS))
    elif case == "set":
        indices = {0, 1}
    monkeypatch.setattr(attributes, "_MIN_BATCH_NORMALS", 0)
    with patch.object(attributes, "_decode_normals_python",
                      wraps=attributes._decode_normals_python) as scalar:
        actual = attributes.decode_normals(source, data, indices, zero_out_of_bounds=True)
    assert scalar.call_count == 1
    assert actual == attributes._decode_normals_python(
        source, data, indices, zero_out_of_bounds=True)


def test_default_dispatch_keeps_small_inputs_scalar_and_batches_large_inputs():
    source = VertexAttributeSource("normal.buf", 12, 0, "f32x3")
    data = struct.pack("<3f", .6, -.8, -0.)
    with patch.object(attributes, "_decode_normals_batch",
                      wraps=attributes._decode_normals_batch) as batch:
        assert attributes.decode_normals(source, data, []) == bytearray()
        assert attributes.decode_normals(source, data, [0]) is not None
        assert batch.call_count == 0
        indices = [0] * (attributes._NORMAL_BATCH_SIZE + 1)
        actual = attributes.decode_normals(source, data, indices)
        assert batch.call_count == 1
    assert actual == attributes._decode_normals_python(source, data, indices)
    # Returned bytes remain caller-owned and resizable after array views expire.
    actual.extend(b"\0")


@pytest.mark.parametrize("dtype,width,stride,offset", [
    ("<f4", 3, 20, 4), ("<f2", 2, 7, 1), ("i1", 3, 4, 1),
])
def test_shared_view_handles_partial_records_without_copying(dtype, width, stride, offset):
    itemsize = np.dtype(dtype).itemsize
    data = bytearray(2 * stride + offset + width * itemsize)
    view = strided_view(np, data, stride=stride, width=width, dtype=dtype, offset=offset)
    assert view.shape == (3, width)
    view[2, 0] = 1
    assert np.frombuffer(data, dtype=dtype, count=1, offset=2 * stride + offset)[0] == 1
    partial = strided_view(np, data[:-1], stride=stride, width=width, dtype=dtype, offset=offset)
    assert partial.shape == (2, width)
    assert strided_view(np, b"", stride=stride, width=width, dtype=dtype, offset=offset).shape == (0, width)


@pytest.mark.parametrize("stride,width,offset", [(0, 3, 0), (8, 3, 0), (16, 0, 0), (16, 3, -1)])
def test_shared_view_rejects_unsupported_layouts(stride, width, offset):
    with pytest.raises(UnsupportedLayout):
        strided_view(np, bytes(32), stride=stride, width=width, dtype="<f4", offset=offset)

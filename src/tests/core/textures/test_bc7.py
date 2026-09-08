"""Focused tests for source-preserving BC7 block editing."""

import pytest

from core.textures import bc7


def _put(bits, start, count, value):
    return bc7.set_bits(bits, start, count, value)


def _color_block(mode):
    subsets = 3 if mode in {0, 2} else 2
    partition_bits = 4 if mode == 0 else 6
    endpoint_bits = {0: 4, 1: 6, 2: 5, 3: 7}[mode]
    index_bits = 3 if mode in {0, 1} else 2
    partition = 0 if mode == 0 else 13
    endpoints = []
    for endpoint in range(subsets * 2):
        endpoints.append(tuple(
            (8 + endpoint * 7 + channel * 5) & ((1 << endpoint_bits) - 1)
            for channel in range(3)))
    bits = 1 << mode
    bits = _put(bits, mode + 1, partition_bits, partition)
    endpoint_start = mode + 1 + partition_bits
    for channel in range(3):
        for endpoint, values in enumerate(endpoints):
            bits = _put(
                bits,
                endpoint_start
                + channel * subsets * 2 * endpoint_bits
                + endpoint * endpoint_bits,
                endpoint_bits, values[channel])
    if mode == 0:
        pbits = tuple(endpoint & 1 for endpoint in range(subsets * 2))
    elif mode == 1:
        pbits = (0, 1)
    elif mode == 3:
        pbits = (0, 1, 1, 0)
    else:
        pbits = ()
    pbit_start = endpoint_start + 3 * subsets * 2 * endpoint_bits
    for index, pbit in enumerate(pbits):
        bits = _put(bits, pbit_start + index, 1, pbit)
    indices = [(index + mode) % (1 << index_bits) for index in range(16)]
    for anchor in bc7.anchors_for_partition(subsets, partition):
        indices[anchor] = 1
    index_start = pbit_start + len(pbits)
    for pixel, index in enumerate(indices):
        width = index_bits - (pixel in bc7.anchors_for_partition(
            subsets, partition))
        bits = _put(bits, index_start, width, index)
        index_start += width
    assert index_start == 128
    return bits.to_bytes(16, "little")


def _mode6_block():
    bits = 1 << 6
    for channel, (low, high) in enumerate(((20, 110), (40, 140), (60, 170))):
        bits = _put(bits, 7 + channel * 14, 7, low >> 1)
        bits = _put(bits, 14 + channel * 14, 7, high >> 1)
    bits = _put(bits, 49, 7, 0)
    bits = _put(bits, 56, 7, 127)
    bits = _put(bits, 63, 1, 0)
    bits = _put(bits, 64, 1, 1)
    indices = [0, 1, 2, 3] * 4
    bits = _put(bits, 65, 3, indices[0])
    for pixel, index in enumerate(indices[1:], 1):
        bits = _put(bits, 68 + (pixel - 1) * 4, 4, index)
    return bits.to_bytes(16, "little")


def _mode7_block():
    partition = 13
    anchor = bc7._PARTITION_2_ANCHORS[partition]
    bits = _put(1 << 7, 8, 6, partition)
    endpoints = (
        (3, 6, 9, 4), (22, 18, 25, 30),
        (8, 15, 5, 20), (28, 26, 30, 31),
    )
    start = 14
    for channel in range(4):
        for endpoint in endpoints:
            bits = _put(bits, start, 5, endpoint[channel])
            start += 5
    for pbit in (0, 1, 1, 0):
        bits = _put(bits, start, 1, pbit)
        start += 1
    indices = [0, 1, 2, 3] * 4
    indices[0] = 0
    indices[anchor] = 1
    for pixel, index in enumerate(indices):
        width = 1 if pixel in {0, anchor} else 2
        bits = _put(bits, start, width, index)
        start += width
    assert start == 128
    return bits.to_bytes(16, "little")


def _separate_block(mode, rotation, index_mode=None):
    bits = 1 << mode
    start = mode + 1
    bits = _put(bits, start, 2, rotation)
    start += 2
    if mode == 4:
        if index_mode is None:
            index_mode = 1 if rotation % 2 else 0
        bits = _put(bits, start, 1, index_mode)
        start += 1
    precisions = (5, 5, 5, 6) if mode == 4 else (7, 7, 7, 8)
    for channel, precision in enumerate(precisions):
        for endpoint in range(2):
            bits = _put(bits, start, precision,
                        (channel * 7 + endpoint * 15 + 3)
                        & ((1 << precision) - 1))
            start += precision
    first = [0, 1, 2, 3] * 4
    second = ([0, 1, 2, 3, 4, 5, 6, 7] * 2
              if mode == 4 else [0, 1, 2, 3] * 4)
    bits = _put(bits, start, 1, first[0])
    start += 1
    for value in first[1:]:
        bits = _put(bits, start, 2, value)
        start += 2
    second_precision = 3 if mode == 4 else 2
    bits = _put(bits, start, second_precision - 1, second[0])
    start += second_precision - 1
    for value in second[1:]:
        bits = _put(bits, start, second_precision, value)
        start += second_precision
    assert start == 128
    return bits.to_bytes(16, "little")


_EDGE_SIZES = ((4, 4), (3, 4), (4, 3), (2, 2), (2, 1), (1, 2), (1, 1))


@pytest.mark.parametrize(
    ("mode", "valid_width", "valid_height"),
    [(mode, width, height) for mode in range(8)
     for width, height in _EDGE_SIZES])
def test_decode_and_recolor_supports_every_bc7_mode(
        mode, valid_width, valid_height):
    block = (_color_block(mode) if mode < 4 else
             _separate_block(mode, 1) if mode in {4, 5} else
             _mode6_block() if mode == 6 else _mode7_block())
    source = bc7.decode_block(block)
    if mode < 4:
        assert all(pixel[3] == 255 for pixel in source)
    target = tuple(
        (min(255, red + 31), max(0, green - 17), min(255, blue + 23),
         0 if mode < 4 else alpha)
        for red, green, blue, alpha in source)

    result = bc7.recolor_block(
        block, target, valid_width=valid_width, valid_height=valid_height)

    assert result.mode == mode
    assert len(result.block) == 16
    assert bc7.block_mode(result.block) == mode
    assert result.candidate_error <= result.source_error
    assert [pixel[3] for pixel in result.candidate_pixels] == [
        pixel[3] for pixel in source]


def test_invalid_unary_prefix_is_reported_as_corrupt_bc7():
    with pytest.raises(bc7.BC7Error):
        bc7.decode_block(bytes(16))


def test_recolor_uses_source_block_when_target_cannot_improve_it():
    block = _color_block(2)
    source = bc7.decode_block(block)

    result = bc7.recolor_block(block, source)

    assert result.block == block
    assert result.candidate_pixels == result.source_pixels
    assert result.candidate_error == result.source_error == 0


def test_recolor_can_reuse_decoded_source_pixels(monkeypatch):
    block = _mode7_block()
    source = bc7.decode_block(block)
    target = tuple((red + 5, green, blue, alpha)
                   for red, green, blue, alpha in source)

    def decode_must_not_run(_block):
        raise AssertionError("source BC7 block was decoded twice")

    monkeypatch.setattr(bc7, "decode_block", decode_must_not_run)
    result = bc7.recolor_block(block, target, source_pixels=source)

    assert result.source_pixels == source


def _reference_fit_fixed_index_endpoints(
        targets, indices, weights, endpoint_codec, *, original_raw=(0, 0),
        pbit0=None, pbit1=None, neighborhood=2):
    if not targets:
        return original_raw
    fractions = tuple(weights[index] / 64.0 for index in indices)
    aa = sum((1.0 - fraction) ** 2 for fraction in fractions)
    ab = sum((1.0 - fraction) * fraction for fraction in fractions)
    bb = sum(fraction ** 2 for fraction in fractions)
    at = sum((1.0 - fraction) * target
             for fraction, target in zip(fractions, targets))
    bt = sum(fraction * target
             for fraction, target in zip(fractions, targets))
    tt = sum(target * target for target in targets)
    determinant = aa * bb - ab * ab
    if determinant > 1e-9:
        estimate0 = (at * bb - bt * ab) / determinant
        estimate1 = (bt * aa - at * ab) / determinant
    else:
        estimate0 = estimate1 = sum(targets) / len(targets)
    center0 = bc7._quantize_endpoint(estimate0, endpoint_codec, pbit0)
    center1 = bc7._quantize_endpoint(estimate1, endpoint_codec, pbit1)
    raw0_values = {
        max(0, min(endpoint_codec.raw_max, center0 + delta))
        for delta in range(-neighborhood, neighborhood + 1)
    }
    raw1_values = {
        max(0, min(endpoint_codec.raw_max, center1 + delta))
        for delta in range(-neighborhood, neighborhood + 1)
    }
    raw0_values.update((0, endpoint_codec.raw_max, original_raw[0]))
    raw1_values.update((0, endpoint_codec.raw_max, original_raw[1]))
    for value in (min(targets), max(targets)):
        raw0_values.add(bc7._quantize_endpoint(value, endpoint_codec, pbit0))
        raw1_values.add(bc7._quantize_endpoint(value, endpoint_codec, pbit1))

    def error(raw0, raw1):
        endpoint0 = endpoint_codec.decode(raw0, pbit0)
        endpoint1 = endpoint_codec.decode(raw1, pbit1)
        return (aa * endpoint0 * endpoint0
                + 2.0 * ab * endpoint0 * endpoint1
                + bb * endpoint1 * endpoint1
                - 2.0 * at * endpoint0
                - 2.0 * bt * endpoint1
                + tt)

    best = (error(*original_raw), original_raw[0], original_raw[1])
    for raw0 in sorted(raw0_values):
        for raw1 in sorted(raw1_values):
            candidate = (error(raw0, raw1), raw0, raw1)
            if candidate < best:
                best = candidate
    return best[1], best[2]


_ENDPOINT_CASES = (
    ("raw5", bc7._EndpointCodec(
        31, lambda raw, _pbit: bc7._unquantize(raw, 5)), None, None),
    ("pbit5", bc7._EndpointCodec(
        15, lambda raw, pbit: bc7._unquantize((raw << 1) | pbit, 5)), 0, 1),
    ("raw6", bc7._EndpointCodec(
        63, lambda raw, _pbit: bc7._unquantize(raw, 6)), None, None),
    ("pbit6", bc7._EndpointCodec(
        31, lambda raw, pbit: bc7._unquantize((raw << 1) | pbit, 6)), 1, 0),
    ("raw7", bc7._EndpointCodec(
        127, lambda raw, _pbit: bc7._unquantize(raw, 7)), None, None),
    ("pbit7", bc7._EndpointCodec(
        63, lambda raw, pbit: bc7._unquantize((raw << 1) | pbit, 7)), 0, 1),
)


def test_fixed_index_fitter_empty_and_degenerate_inputs_match_reference():
    codec = bc7._EndpointCodec(
        31, lambda raw, _pbit: bc7._unquantize(raw, 5))
    for targets, indices in (((), ()), ((0, 128, 255), (2, 2, 2))):
        expected = _reference_fit_fixed_index_endpoints(
            targets, indices, bc7.WEIGHTS_3, codec, original_raw=(7, 19))
        actual = bc7._fit_fixed_index_endpoints(
            targets, indices, bc7.WEIGHTS_3, codec, original_raw=(7, 19))
        prepared = bc7._fit_fixed_index_endpoints(
            targets, indices, bc7.WEIGHTS_3, codec, original_raw=(7, 19),
            basis=bc7._prepare_fixed_index_fit_basis(
                indices, bc7.WEIGHTS_3))
        assert actual == expected
        assert prepared == expected


@pytest.mark.parametrize(
    ("weights", "indices", "targets"),
    ((
        (bc7.WEIGHTS_2, (0, 1, 2, 3), (0, 255, 128, 64)),
        (bc7.WEIGHTS_3, (0, 1, 2, 3, 4), (17, 42, 201, 88, 254)),
        (bc7.WEIGHTS_4, (0, 3, 6, 9), (255, 3, 127, 64)),
    )),
    ids=("weights2", "weights3", "weights4"))
@pytest.mark.parametrize(
    "codec_case", _ENDPOINT_CASES,
    ids=lambda case: case[0])
def test_fixed_index_prepared_basis_matches_reference(
        weights, indices, targets, codec_case):
    _codec_name, codec, pbit0, pbit1 = codec_case
    original_raw = (
        min(codec.raw_max, 7), min(codec.raw_max, 19))
    expected = _reference_fit_fixed_index_endpoints(
        targets, indices, weights, codec,
        original_raw=original_raw, pbit0=pbit0, pbit1=pbit1)
    generic = bc7._fit_fixed_index_endpoints(
        targets, indices, weights, codec,
        original_raw=original_raw, pbit0=pbit0, pbit1=pbit1)
    prepared = bc7._prepare_fixed_index_fit_basis(indices, weights)
    optimized = bc7._fit_fixed_index_endpoints(
        targets, indices, weights, codec,
        original_raw=original_raw, pbit0=pbit0, pbit1=pbit1,
        basis=prepared)

    assert generic == expected
    assert optimized == generic


@pytest.mark.parametrize("rotation", range(4))
@pytest.mark.parametrize("index_mode", range(2))
@pytest.mark.parametrize("valid_width, valid_height", _EDGE_SIZES)
def test_mode4_rotation_and_index_mode_match_reference(
        monkeypatch, rotation, index_mode, valid_width, valid_height):
    block = _separate_block(4, rotation, index_mode)
    source = bc7.decode_block(block)
    target = tuple(
        (min(255, red + 31), max(0, green - 17),
         min(255, blue + 23), alpha)
        for red, green, blue, alpha in source)
    optimized = bc7.recolor_block(
        block, target, valid_width=valid_width, valid_height=valid_height)

    def reference_fitter(*args, **kwargs):
        kwargs.pop("basis", None)
        return _reference_fit_fixed_index_endpoints(*args, **kwargs)

    monkeypatch.setattr(bc7, "_fit_fixed_index_endpoints", reference_fitter)
    reference = bc7.recolor_block(
        block, target, valid_width=valid_width, valid_height=valid_height)

    assert optimized.block == reference.block
    assert optimized.candidate_pixels == reference.candidate_pixels
    assert optimized.source_error == reference.source_error
    assert optimized.candidate_error == reference.candidate_error


def test_mode4_deterministic_corpus_matches_reference(monkeypatch):
    cases = []
    for case in range(96):
        rotation = case % 4
        index_mode = (case // 4) % 2
        valid_width = 1 + (case * 3) % 4
        valid_height = 1 + (case * 5) % 4
        block = _separate_block(4, rotation, index_mode)
        source = bc7.decode_block(block)
        target = tuple(
            ((red + case * 7 + pixel * 3) & 0xff,
             (green - case * 5 - pixel * 2) & 0xff,
             (blue + case * 11 + pixel) & 0xff,
             alpha)
            for pixel, (red, green, blue, alpha) in enumerate(source))
        cases.append((block, target, valid_width, valid_height))

    optimized = [bc7.recolor_block(
        block, target, valid_width, valid_height)
        for block, target, valid_width, valid_height in cases]

    def reference_fitter(*args, **kwargs):
        kwargs.pop("basis", None)
        return _reference_fit_fixed_index_endpoints(*args, **kwargs)

    monkeypatch.setattr(bc7, "_fit_fixed_index_endpoints", reference_fitter)
    reference = [bc7.recolor_block(
        block, target, valid_width, valid_height)
        for block, target, valid_width, valid_height in cases]

    assert optimized == reference


@pytest.mark.parametrize("mode", range(8))
@pytest.mark.parametrize("valid_width, valid_height", ((4, 4), (2, 3)))
def test_recolor_block_matches_reference_fitter(
        monkeypatch, mode, valid_width, valid_height):
    block = (_color_block(mode) if mode < 4 else
             _separate_block(mode, 1) if mode in {4, 5} else
             _mode6_block() if mode == 6 else _mode7_block())
    source = bc7.decode_block(block)
    target = tuple(
        (min(255, red + 31), max(0, green - 17), blue, alpha)
        for red, green, blue, alpha in source)
    optimized = bc7.recolor_block(
        block, target, valid_width=valid_width, valid_height=valid_height)

    def reference_fitter(*args, **kwargs):
        kwargs.pop("basis", None)
        return _reference_fit_fixed_index_endpoints(*args, **kwargs)

    monkeypatch.setattr(bc7, "_fit_fixed_index_endpoints", reference_fitter)
    reference = bc7.recolor_block(
        block, target, valid_width=valid_width, valid_height=valid_height)

    assert optimized.block == reference.block
    assert optimized.source_pixels == reference.source_pixels
    assert optimized.candidate_pixels == reference.candidate_pixels
    assert optimized.source_error == reference.source_error
    assert optimized.candidate_error == reference.candidate_error
    assert optimized.mode == reference.mode
    assert [pixel[3] for pixel in optimized.candidate_pixels] == [
        pixel[3] for pixel in optimized.source_pixels]

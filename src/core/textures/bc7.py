"""Small, source-preserving BC7 decoder and endpoint refitter."""


class BC7Error(ValueError):
    """An invalid BC7 block or unsupported codec operation."""


WEIGHTS_2 = (0, 21, 43, 64)
WEIGHTS_3 = (0, 9, 18, 27, 37, 46, 55, 64)
WEIGHTS_4 = (0, 4, 9, 13, 17, 21, 26, 30,
            34, 38, 43, 47, 51, 55, 60, 64)

# Bit p in a mask means that raster-order texel p belongs to subset 1.
# These are the BC7 two-subset patterns from the format's fixed partition set.
_PARTITION_2_MASKS = (
    0xCCCC, 0x8888, 0xEEEE, 0xECC8, 0xC880, 0xFEEC, 0xFEC8, 0xEC80,
    0xC800, 0xFFEC, 0xFE80, 0xE800, 0xFFE8, 0xFF00, 0xFFF0, 0xF000,
    0xF710, 0x008E, 0x7100, 0x08CE, 0x008C, 0x7310, 0x3100, 0x8CCE,
    0x088C, 0x3110, 0x6666, 0x366C, 0x17E8, 0x0FF0, 0x718E, 0x399C,
    0xAAAA, 0xF0F0, 0x5A5A, 0x33CC, 0x3C3C, 0x55AA, 0x9696, 0xA55A,
    0x73CE, 0x13C8, 0x324C, 0x3BDC, 0x6996, 0xC33C, 0x9966, 0x0660,
    0x0272, 0x04E4, 0x4E40, 0x2720, 0xC936, 0x936C, 0x39C6, 0x639C,
    0x9336, 0x9CC6, 0x817E, 0xE718, 0xCCF0, 0x0FCC, 0x7744, 0xEE22,
)

# Subset 0 always anchors texel 0. This table gives subset 1's anchor.
_PARTITION_2_ANCHORS = (
    15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15,
    15, 2, 8, 2, 2, 8, 8, 15, 2, 8, 2, 2, 8, 8, 2, 2,
    15, 15, 6, 8, 2, 8, 15, 15, 2, 8, 2, 2, 2, 15, 15, 6,
    6, 2, 6, 8, 15, 15, 2, 2, 15, 15, 15, 15, 15, 2, 2, 15,
)


def _require_block(block):
    if len(block) != 16:
        raise BC7Error("DDS BC7 blocks must contain exactly 16 bytes.")


def block_mode(block):
    """Return the BC7 mode encoded by the unary prefix in byte zero."""
    _require_block(block)
    for mode in range(8):
        if block[0] & (1 << mode):
            return mode
    raise BC7Error("DDS payload contains an invalid BC7 block.")


def get_bits(bits, start, count):
    """Read one little-endian bit field from a 128-bit block integer."""
    return (bits >> start) & ((1 << count) - 1)


def set_bits(bits, start, count, value):
    """Replace one little-endian bit field in a 128-bit block integer."""
    mask = ((1 << count) - 1) << start
    return (bits & ~mask) | ((value & ((1 << count) - 1)) << start)


def _unquantize(value, precision):
    shifted = value << (8 - precision)
    return shifted | (shifted >> precision)


def _read_index_set(bits, start, precision, anchors=(0,)):
    values = []
    for pixel in range(16):
        width = precision - 1 if pixel in anchors else precision
        values.append(get_bits(bits, start, width))
        start += width
    return tuple(values), start


def mode6_parameters(block):
    """Read mode-6 endpoints, P-bits, and the shared index set."""
    if block_mode(block) != 6:
        raise BC7Error("DDS block is not BC7 mode 6.")
    bits = int.from_bytes(block, "little")
    pbits = (get_bits(bits, 63, 1), get_bits(bits, 64, 1))
    endpoints = [[0, 0, 0, 0], [0, 0, 0, 0]]
    for channel in range(4):
        for endpoint in range(2):
            base = get_bits(bits, 7 + channel * 14 + endpoint * 7, 7)
            endpoints[endpoint][channel] = (base << 1) | pbits[endpoint]
    indices, _start = _read_index_set(bits, 65, 4)
    return (tuple(pbits), tuple(tuple(endpoint) for endpoint in endpoints),
            indices)


def mode6_decode_block(block):
    """Decode one mode-6 block with BC7's exact integer interpolation."""
    _pbits, endpoints, indices = mode6_parameters(block)
    return tuple(
        tuple(
            (endpoints[0][channel] * (64 - WEIGHTS_4[index])
             + endpoints[1][channel] * WEIGHTS_4[index] + 32) >> 6
            for channel in range(4))
        for index in indices)


def _mode6_channel_error(base0, base1, p0, p1, targets, indices):
    endpoint0 = (base0 << 1) | p0
    endpoint1 = (base1 << 1) | p1
    return sum(
        (((endpoint0 * (64 - WEIGHTS_4[index])
           + endpoint1 * WEIGHTS_4[index] + 32) >> 6) - target) ** 2
        for target, index in zip(targets, indices))


def _fit_mode6_channel(targets, indices, p0, p1):
    """Fit two 7-bit mode-6 endpoint fields while retaining their P-bits."""
    if not targets:
        return 0, 0
    fractions = tuple(WEIGHTS_4[index] / 64.0 for index in indices)
    bb = sum(fraction ** 2 for fraction in fractions)

    def quantize(value, pbit):
        return max(0, min(127, int(round((value - pbit) / 2.0))))

    best = None
    for base0 in range(128):
        endpoint0 = (base0 << 1) | p0
        if bb:
            endpoint1 = sum(
                fraction * (target - endpoint0 * (1.0 - fraction))
                for fraction, target in zip(fractions, targets)) / bb
        else:
            endpoint1 = sum(targets) / len(targets)
        center1 = quantize(endpoint1, p1)
        candidates1 = {
            max(0, min(127, center1 + delta))
            for delta in range(-4, 5)
        }
        candidates1.update((0, 127, quantize(min(targets), p1),
                            quantize(max(targets), p1)))
        for base1 in candidates1:
            key = (_mode6_channel_error(
                base0, base1, p0, p1, targets, indices), base0, base1)
            if best is None or key < best[0]:
                best = (key, (base0, base1))
    return best[1]


def recolor_mode6(block, target_pixels, valid_width, valid_height):
    """Change only mode-6 RGB endpoints and preserve alpha-bearing bits."""
    pbits, _endpoints, indices = mode6_parameters(block)
    targets_by_channel = [[], [], []]
    index_values = []
    for row in range(valid_height):
        for column in range(valid_width):
            pixel_index = row * 4 + column
            index_values.append(indices[pixel_index])
            for channel in range(3):
                targets_by_channel[channel].append(
                    target_pixels[pixel_index][channel])

    bits = int.from_bytes(block, "little")
    for channel in range(3):
        base0, base1 = _fit_mode6_channel(
            targets_by_channel[channel], index_values, pbits[0], pbits[1])
        bits = set_bits(bits, 7 + channel * 14, 7, base0)
        bits = set_bits(bits, 14 + channel * 14, 7, base1)
    candidate = bits.to_bytes(16, "little")
    return candidate, mode6_decode_block(candidate)


def separate_parameters(block):
    """Read the rotation-aware endpoint and index fields of mode 4 or 5."""
    mode = block_mode(block)
    if mode not in {4, 5}:
        raise BC7Error("DDS block is not a separate-alpha BC7 mode.")
    bits = int.from_bytes(block, "little")
    start = mode + 1
    rotation = get_bits(bits, start, 2)
    start += 2
    index_mode = get_bits(bits, start, 1) if mode == 4 else 0
    if mode == 4:
        start += 1
    precisions = (5, 5, 5, 6) if mode == 4 else (7, 7, 7, 8)
    raw_endpoints = [[0, 0, 0, 0], [0, 0, 0, 0]]
    endpoints = [[0, 0, 0, 0], [0, 0, 0, 0]]
    for channel, precision in enumerate(precisions):
        for endpoint in range(2):
            value = get_bits(bits, start, precision)
            start += precision
            raw_endpoints[endpoint][channel] = value
            endpoints[endpoint][channel] = _unquantize(value, precision)
    first_indices, start = _read_index_set(bits, start, 2)
    second_precision = 3 if mode == 4 else 2
    second_indices, start = _read_index_set(bits, start, second_precision)
    if start != 128:
        raise BC7Error("DDS BC7 index layout is invalid.")
    return (mode, rotation, index_mode,
            tuple(tuple(endpoint) for endpoint in raw_endpoints),
            tuple(tuple(endpoint) for endpoint in endpoints),
            first_indices, second_indices)


def _rotation_output_channel(internal_channel, rotation):
    if not rotation:
        return internal_channel
    if internal_channel == 3:
        return rotation - 1
    if internal_channel == rotation - 1:
        return 3
    return internal_channel


def separate_decode_block(block):
    """Decode one mode-4/5 block with its component rotation applied."""
    (mode, rotation, index_mode, _raw_endpoints, endpoints,
     first_indices, second_indices) = separate_parameters(block)
    if mode == 4:
        vector_indices = second_indices if index_mode else first_indices
        scalar_indices = first_indices if index_mode else second_indices
        vector_precision = 3 if index_mode else 2
        scalar_precision = 2 if index_mode else 3
    else:
        vector_indices = first_indices
        scalar_indices = second_indices
        vector_precision = scalar_precision = 2
    vector_weights = WEIGHTS_3 if vector_precision == 3 else WEIGHTS_2
    scalar_weights = WEIGHTS_3 if scalar_precision == 3 else WEIGHTS_2
    pixels = []
    for pixel_index in range(16):
        internal = [
            ((endpoints[0][channel] *
              (64 - vector_weights[vector_indices[pixel_index]])
              + endpoints[1][channel] *
              vector_weights[vector_indices[pixel_index]] + 32) >> 6)
            for channel in range(3)
        ]
        scalar_index = scalar_indices[pixel_index]
        internal.append(
            (endpoints[0][3] * (64 - scalar_weights[scalar_index])
             + endpoints[1][3] * scalar_weights[scalar_index] + 32) >> 6)
        if rotation:
            internal[3], internal[rotation - 1] = (
                internal[rotation - 1], internal[3])
        pixels.append(tuple(internal))
    return tuple(pixels)


def _separate_channel_error(
        raw0, raw1, endpoint_precision, targets, indices, index_precision):
    endpoint0 = _unquantize(raw0, endpoint_precision)
    endpoint1 = _unquantize(raw1, endpoint_precision)
    weights = WEIGHTS_3 if index_precision == 3 else WEIGHTS_2
    return sum(
        (((endpoint0 * (64 - weights[index])
           + endpoint1 * weights[index] + 32) >> 6) - target) ** 2
        for target, index in zip(targets, indices))


def _fit_separate_channel(
        targets, indices, endpoint_precision, index_precision):
    if not targets:
        return 0, 0
    weights = WEIGHTS_3 if index_precision == 3 else WEIGHTS_2
    fractions = tuple(weights[index] / 64.0 for index in indices)
    bb = sum(fraction ** 2 for fraction in fractions)
    max_value = (1 << endpoint_precision) - 1

    def quantize(value):
        return max(0, min(max_value, int(round(
            value * max_value / 255.0))))

    best = None
    for raw0 in range(max_value + 1):
        endpoint0 = _unquantize(raw0, endpoint_precision)
        if bb:
            endpoint1 = sum(
                fraction * (target - endpoint0 * (1.0 - fraction))
                for fraction, target in zip(fractions, targets)) / bb
        else:
            endpoint1 = sum(targets) / len(targets)
        center1 = quantize(endpoint1)
        candidates1 = {
            max(0, min(max_value, center1 + delta))
            for delta in range(-4, 5)
        }
        candidates1.update((0, max_value, quantize(min(targets)),
                            quantize(max(targets))))
        for raw1 in candidates1:
            key = (_separate_channel_error(
                raw0, raw1, endpoint_precision, targets, indices,
                index_precision), raw0, raw1)
            if best is None or key < best[0]:
                best = (key, (raw0, raw1))
    return best[1]


def recolor_separate(block, target_pixels, valid_width, valid_height):
    """Refit mode-4/5 RGB fields while freezing the decoded alpha path."""
    (mode, rotation, index_mode, _raw_endpoints, _endpoints,
     first_indices, second_indices) = separate_parameters(block)
    if mode == 4:
        vector_indices = second_indices if index_mode else first_indices
        scalar_indices = first_indices if index_mode else second_indices
        vector_precision = 3 if index_mode else 2
        scalar_precision = 2 if index_mode else 3
        precisions = (5, 5, 5, 6)
    else:
        vector_indices = first_indices
        scalar_indices = second_indices
        vector_precision = scalar_precision = 2
        precisions = (7, 7, 7, 8)
    alpha_internal = 3 if not rotation else rotation - 1
    targets_by_channel = [[] for _channel in range(4)]
    indices_by_channel = [vector_indices] * 3 + [scalar_indices]
    for row in range(valid_height):
        for column in range(valid_width):
            pixel_index = row * 4 + column
            for channel in range(4):
                output_channel = _rotation_output_channel(channel, rotation)
                targets_by_channel[channel].append(
                    target_pixels[pixel_index][output_channel])

    bits = int.from_bytes(block, "little")
    endpoint_offset = mode + 1 + 2 + (1 if mode == 4 else 0)
    for channel, precision in enumerate(precisions):
        if channel == alpha_internal:
            endpoint_offset += precision * 2
            continue
        index_precision = (vector_precision if channel < 3
                           else scalar_precision)
        raw0, raw1 = _fit_separate_channel(
            targets_by_channel[channel], indices_by_channel[channel],
            precision, index_precision)
        bits = set_bits(bits, endpoint_offset, precision, raw0)
        bits = set_bits(bits, endpoint_offset + precision, precision, raw1)
        endpoint_offset += precision * 2
    candidate = bits.to_bytes(16, "little")
    return candidate, separate_decode_block(candidate)


def mode7_subset_for_pixel(partition, pixel):
    """Return the mode-7 subset for one raster-order texel."""
    if not 0 <= partition < 64 or not 0 <= pixel < 16:
        raise BC7Error("BC7 mode-7 partition or pixel is invalid.")
    return (_PARTITION_2_MASKS[partition] >> pixel) & 1


def mode7_parameters(block):
    """Read mode-7 partition, P-bits, endpoints, and fixed indices."""
    if block_mode(block) != 7:
        raise BC7Error("DDS block is not BC7 mode 7.")
    bits = int.from_bytes(block, "little")
    partition = get_bits(bits, 8, 6)
    raw_endpoints = [[0, 0, 0, 0] for _endpoint in range(4)]
    start = 14
    for channel in range(4):
        for endpoint in range(4):
            raw_endpoints[endpoint][channel] = get_bits(bits, start, 5)
            start += 5
    pbits = tuple(get_bits(bits, start + endpoint, 1)
                  for endpoint in range(4))
    start += 4
    indices, start = _read_index_set(
        bits, start, 2, (0, _PARTITION_2_ANCHORS[partition]))
    if start != 128:
        raise BC7Error("DDS BC7 mode-7 index layout is invalid.")
    endpoints = tuple(
        tuple(_unquantize((raw_endpoints[endpoint][channel] << 1)
                          | pbits[endpoint], 6)
              for channel in range(4))
        for endpoint in range(4))
    return (partition,
            tuple(tuple(endpoint) for endpoint in raw_endpoints),
            endpoints, pbits, indices)


def mode7_decode_block(block):
    """Decode one mode-7 block using the fixed partition and anchor tables."""
    partition, _raw_endpoints, endpoints, _pbits, indices = (
        mode7_parameters(block))
    pixels = []
    for pixel, index in enumerate(indices):
        subset = mode7_subset_for_pixel(partition, pixel)
        endpoint0 = endpoints[subset * 2]
        endpoint1 = endpoints[subset * 2 + 1]
        weight = WEIGHTS_2[index]
        pixels.append(tuple(
            (endpoint0[channel] * (64 - weight)
             + endpoint1[channel] * weight + 32) >> 6
            for channel in range(4)))
    return tuple(pixels)


def _mode7_channel_error(raw0, raw1, p0, p1, targets, indices):
    endpoint0 = _unquantize((raw0 << 1) | p0, 6)
    endpoint1 = _unquantize((raw1 << 1) | p1, 6)
    return sum(
        (((endpoint0 * (64 - WEIGHTS_2[index])
           + endpoint1 * WEIGHTS_2[index] + 32) >> 6) - target) ** 2
        for target, index in zip(targets, indices))


def _fit_mode7_channel(targets, indices, p0, p1):
    if not targets:
        return 0, 0
    fractions = tuple(WEIGHTS_2[index] / 64.0 for index in indices)
    bb = sum(fraction ** 2 for fraction in fractions)

    def quantize(value, pbit):
        return max(0, min(31, int(round((value - pbit) / 2.0))))

    best = None
    for raw0 in range(32):
        endpoint0 = _unquantize((raw0 << 1) | p0, 6)
        if bb:
            endpoint1 = sum(
                fraction * (target - endpoint0 * (1.0 - fraction))
                for fraction, target in zip(fractions, targets)) / bb
        else:
            endpoint1 = sum(targets) / len(targets)
        center1 = quantize(endpoint1, p1)
        candidates1 = {
            max(0, min(31, center1 + delta))
            for delta in range(-4, 5)
        }
        candidates1.update((0, 31, quantize(min(targets), p1),
                            quantize(max(targets), p1)))
        for raw1 in candidates1:
            key = (_mode7_channel_error(
                raw0, raw1, p0, p1, targets, indices), raw0, raw1)
            if best is None or key < best[0]:
                best = (key, (raw0, raw1))
    return best[1]


def recolor_mode7(block, target_pixels, valid_width, valid_height):
    """Refit mode-7 RGB endpoints while freezing all alpha structure."""
    partition, _raw_endpoints, _endpoints, pbits, indices = (
        mode7_parameters(block))
    targets_by_subset = [[[] for _channel in range(3)] for _subset in range(2)]
    indices_by_subset = [[], []]
    for row in range(valid_height):
        for column in range(valid_width):
            pixel = row * 4 + column
            subset = mode7_subset_for_pixel(partition, pixel)
            indices_by_subset[subset].append(indices[pixel])
            for channel in range(3):
                targets_by_subset[subset][channel].append(
                    target_pixels[pixel][channel])

    bits = int.from_bytes(block, "little")
    endpoint_start = 14
    for subset in range(2):
        for channel in range(3):
            raw0, raw1 = _fit_mode7_channel(
                targets_by_subset[subset][channel], indices_by_subset[subset],
                pbits[subset * 2], pbits[subset * 2 + 1])
            endpoint_offset = endpoint_start + channel * 20 + subset * 10
            bits = set_bits(bits, endpoint_offset, 5, raw0)
            bits = set_bits(bits, endpoint_offset + 5, 5, raw1)
    candidate = bits.to_bytes(16, "little")
    return candidate, mode7_decode_block(candidate)


__all__ = [
    "BC7Error", "WEIGHTS_2", "WEIGHTS_3", "WEIGHTS_4", "block_mode",
    "get_bits", "set_bits", "mode6_parameters", "mode6_decode_block",
    "recolor_mode6", "separate_parameters", "separate_decode_block",
    "recolor_separate", "mode7_subset_for_pixel", "mode7_parameters",
    "mode7_decode_block", "recolor_mode7",
]

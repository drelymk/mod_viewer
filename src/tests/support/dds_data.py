"""Canonical synthetic DX10 and BC7 inputs for texture tests."""

import struct

from core.textures import bc7


def dx10_dds(payload=b"", *, dxgi_format=98, width=4, height=4,
             mip_count=1, array_size=1, cube=False, volume=False):
    """Build a DX10 DDS; callers supply payload for malformed/edited cases."""
    header = bytearray(148)
    header[:4] = b"DDS "
    struct.pack_into("<I", header, 4, 124)
    struct.pack_into("<II", header, 12, height, width)
    struct.pack_into("<I", header, 28, mip_count)
    struct.pack_into("<I", header, 76, 32)
    struct.pack_into(
        "<II", header, 80, 4, int.from_bytes(b"DX10", "little"))
    struct.pack_into(
        "<IIIII", header, 128, dxgi_format, 3,
        4 if cube else 0, array_size, 0)
    if volume:
        struct.pack_into("<I", header, 112, 0x200000)
    return bytes(header) + bytes(payload)


def mode6_block(endpoints=((20, 110), (40, 140), (60, 170))):
    bits = 1 << 6
    for channel, (low, high) in enumerate(endpoints):
        bits = bc7.set_bits(bits, 7 + channel * 14, 7, low >> 1)
        bits = bc7.set_bits(bits, 14 + channel * 14, 7, high >> 1)
    bits = bc7.set_bits(bits, 49, 7, 0)
    bits = bc7.set_bits(bits, 56, 7, 127)
    bits = bc7.set_bits(bits, 63, 1, 0)
    bits = bc7.set_bits(bits, 64, 1, 1)
    indices = [0, 1, 2, 3] * 4
    bits = bc7.set_bits(bits, 65, 3, indices[0])
    for pixel, index in enumerate(indices[1:], 1):
        bits = bc7.set_bits(bits, 68 + (pixel - 1) * 4, 4, index)
    return bits.to_bytes(16, "little")

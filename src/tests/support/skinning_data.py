"""Canonical generated skin streams for decoder and preview tests."""

import struct


def gimi_four_influence(*, bone_ids=(7, 8, 9, 0), weights=(.6, .3, .1, 0.)):
    return struct.pack("<4f4I", *weights, *bone_ids)


def wwmi_u8_four(*, bone_ids=(3, 5, 7, 0), weights=(128, 64, 63, 0)):
    return bytes(bone_ids) + bytes(weights)


def wwmi_u8_eight(*, bone_ids=tuple(range(8)), weights=tuple(range(1, 9))):
    return bytes(bone_ids) + bytes(weights)


def wwmi_u16_eight(*, bone_ids=(3, 259, 45, 257, 0, 1, 2, 3),
                   weights=(65535, 32768, 16384, 8192, 0, 1, 2, 3)):
    return struct.pack("<8H8H", *bone_ids, *weights)


def vertex_vg_remap(values=(3, 259, 45, 257, 0, 1, 2, 3)):
    return struct.pack("<8H", *values)

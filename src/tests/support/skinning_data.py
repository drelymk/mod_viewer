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


def write_gimi_skinning_mod(tmp_path):
    ini = tmp_path / "mod.ini"
    ini.write_text(
        """[TextureOverrideComponent01Blend]
ib = ResourceComponent01IB
vb0 = ResourceComponent01Position
vb1 = ResourceComponent01Blend
vb2 = ResourceComponent01Texcoord
drawindexed = 6, 0, 0

[TextureOverrideComponent01Texcoord]
vb1 = ResourceComponent01Texcoord

[ResourceComponent01IB]
filename = component01.ib
format = DXGI_FORMAT_R32_UINT

[ResourceComponent01Position]
filename = component01.pos
stride = 12

[ResourceComponent01Blend]
filename = component01.blend
stride = 32

[ResourceComponent01Texcoord]
filename = component01.tc
stride = 20
""",
        encoding="utf-8",
    )
    (tmp_path / "component01.ib").write_bytes(struct.pack(
        "<6I", 2, 0, 1, 1, 3, 2))
    (tmp_path / "component01.pos").write_bytes(b"".join(
        struct.pack("<3f", float(i), 0., 0.) for i in range(4)))
    (tmp_path / "component01.tc").write_bytes(b"\0" * 20 * 4)
    (tmp_path / "component01.blend").write_bytes(
        gimi_four_influence() * 4)
    return ini


def write_wwmi_remap_mod(tmp_path):
    ini = tmp_path / "wwmi.ini"
    ini.write_text(
        """[TextureOverrideComponent01Blend]
ib = ResourceComponent01IB
vb0 = ResourceComponent01Position
vb1 = ResourceComponent01Blend
vb2 = ResourceComponent01Texcoord
run = CommandListRemap
drawindexed = 3, 0, 0

[CommandListRemap]
cs-t35 = ref ResourceBlendRemapVertexVGBuffer

[ResourceComponent01IB]
filename = component01.ib
format = DXGI_FORMAT_R32_UINT

[ResourceComponent01Position]
filename = component01.pos
stride = 12

[ResourceComponent01Blend]
filename = component01.blend
format = DXGI_FORMAT_R8_UINT
stride = 16

[ResourceComponent01Texcoord]
filename = component01.tc
stride = 20

[ResourceBlendRemapVertexVGBuffer]
filename = component01.vertex_vg
format = DXGI_FORMAT_R16_UINT
stride = 16
""",
        encoding="utf-8",
    )
    (tmp_path / "component01.ib").write_bytes(struct.pack("<3I", 0, 1, 2))
    (tmp_path / "component01.pos").write_bytes(b"".join(
        struct.pack("<3f", float(i), 0., 0.) for i in range(3)))
    (tmp_path / "component01.tc").write_bytes(b"\0" * 20 * 3)
    (tmp_path / "component01.blend").write_bytes(b"".join(
        bytes([3] * 8 + [255, 128, 0, 0, 0, 0, 0, 0])
        for _ in range(3)))
    (tmp_path / "component01.vertex_vg").write_bytes(
        vertex_vg_remap((3, 259, 0, 0, 0, 0, 0, 0)) * 3)
    return ini

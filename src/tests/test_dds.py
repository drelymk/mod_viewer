"""DDS eligibility and payload-shape regressions for native transport."""

import struct

import pytest

from core.dds import inspect_dds, native_dds_info


_DXGI = {
    "bc1_unorm": 71, "bc1_srgb": 72, "bc2_unorm": 74, "bc2_srgb": 75,
    "bc3_unorm": 77, "bc3_srgb": 78, "bc4_unorm": 80, "bc4_snorm": 81,
    "bc5_unorm": 83, "bc5_snorm": 84, "bc6h_ufloat": 95,
    "bc6h_float": 96, "bc7_unorm": 98, "bc7_srgb": 99,
}
_LEGACY = {
    "DXT1": "bc1_unorm", "DXT3": "bc2_unorm", "DXT5": "bc3_unorm",
    "ATI1": "bc4_unorm", "BC4U": "bc4_unorm", "BC4S": "bc4_snorm",
    "ATI2": "bc5_unorm", "BC5U": "bc5_unorm", "BC5S": "bc5_snorm",
}


def _level_size(width, height, format_name):
    if format_name.startswith(("bc1", "bc4")):
        block_bytes = 8
    elif format_name.startswith("bc"):
        block_bytes = 16
    else:
        return width * height * 4
    return ((width + 3) // 4) * ((height + 3) // 4) * block_bytes


def _dds(width=4, height=4, format_name="bc7_unorm", mip_count=1,
         array_size=1, cube=False, volume=False, payload=True):
    compressed = format_name.startswith("bc")
    is_dx10 = compressed and format_name not in _LEGACY
    header_size = 148 if is_dx10 else 128
    data = bytearray(header_size)
    data[:4] = b"DDS "
    struct.pack_into("<I", data, 4, 124)
    struct.pack_into("<I", data, 8, 0x21007)
    struct.pack_into("<II", data, 12, height, width)
    struct.pack_into("<II", data, 24, 0, mip_count)
    struct.pack_into("<I", data, 76, 32)
    if is_dx10:
        struct.pack_into("<II", data, 80, 4, int.from_bytes(b"DX10", "little"))
        struct.pack_into("<IIIII", data, 128, _DXGI[format_name], 3,
                         array_size, 4 if cube else 0, 0)
    elif compressed:
        legacy_code = next(code for code, value in _LEGACY.items()
                           if value == format_name)
        struct.pack_into("<II", data, 80, 4,
                         int.from_bytes(legacy_code.encode("ascii"), "little"))
    else:
        struct.pack_into("<II", data, 80, 0x41, 0)
        struct.pack_into("<I", data, 88, 32)
        masks = (0x000000FF, 0x0000FF00, 0x00FF0000, 0xFF000000)
        if format_name == "bgra8":
            masks = (0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000)
        struct.pack_into("<IIII", data, 92, *masks)
    caps2 = (0x200 if cube else 0) | (0x200000 if volume else 0)
    struct.pack_into("<I", data, 112, caps2)
    if payload:
        w, h = width, height
        for _ in range(max(1, mip_count)):
            data.extend(bytes(_level_size(w, h, format_name)))
            w, h = max(1, w // 2), max(1, h // 2)
    return bytes(data)


@pytest.mark.parametrize("format_name", list(_DXGI))
def test_dx10_formats_are_reported_with_canonical_ids(tmp_path, format_name):
    path = tmp_path / f"{format_name}.dds"
    path.write_bytes(_dds(format_name=format_name))

    info = inspect_dds(path)

    assert info is not None
    assert info.format == format_name
    assert info.compressed and info.requires_bc
    assert native_dds_info(path).format == format_name


@pytest.mark.parametrize("fourcc, expected", list(_LEGACY.items()))
def test_legacy_compressed_formats_are_supported(tmp_path, fourcc, expected):
    path = tmp_path / f"{fourcc}.dds"
    path.write_bytes(_dds(format_name=expected))
    assert inspect_dds(path).format == expected


@pytest.mark.parametrize("format_name", ["rgba8", "bgra8"])
def test_32_bit_rgba_layouts_do_not_require_bc(tmp_path, format_name):
    path = tmp_path / f"{format_name}.dds"
    path.write_bytes(_dds(format_name=format_name))
    info = inspect_dds(path)
    assert info.format == format_name
    assert not info.compressed and not info.requires_bc


@pytest.mark.parametrize("kwargs", [
    {"payload": False},
    {"array_size": 2},
    {"cube": True},
    {"volume": True},
    {"format_name": "bc7_unorm", "mip_count": 4},
])
def test_invalid_or_unsafe_dds_falls_back(tmp_path, kwargs):
    path = tmp_path / "unsafe.dds"
    path.write_bytes(_dds(**kwargs))
    assert inspect_dds(path) is None
    assert native_dds_info(path) is None


def test_transform_and_size_caps_disable_native_delivery(tmp_path):
    path = tmp_path / "normal.dds"
    path.write_bytes(_dds())
    assert native_dds_info(path, transform="normal_xy_reconstruct") is None
    assert native_dds_info(path, max_size=2) is None


def test_typeless_dxgi_is_rejected(tmp_path):
    path = tmp_path / "typeless.dds"
    data = bytearray(_dds(format_name="bc7_unorm"))
    struct.pack_into("<I", data, 128, 97)  # BC7 typeless
    path.write_bytes(data)
    assert inspect_dds(path) is None

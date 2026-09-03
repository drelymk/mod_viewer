"""DDS eligibility and payload-shape regressions for native transport."""

import struct

import pytest

from core.textures.dds import inspect_dds, inspect_dds_layout, native_dds_info


_DXGI = {
    "bc1_unorm": 71, "bc2_unorm": 74, "bc3_unorm": 77,
    "bc7_unorm": 98, "bc7_srgb": 99,
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
         array_size=1, cube=False, volume=False, payload=True, fourcc=None):
    compressed = format_name.startswith("bc")
    is_dx10 = compressed and fourcc is None
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
                         4 if cube else 0, array_size, 0)
    elif compressed:
        struct.pack_into("<II", data, 80, 4,
                         int.from_bytes(fourcc.encode("ascii"), "little"))
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


def test_legacy_compressed_formats_are_supported(tmp_path):
    path = tmp_path / "DXT1.dds"
    path.write_bytes(_dds(format_name="bc1_unorm", fourcc="DXT1"))
    assert inspect_dds(path).format == "bc1_unorm"
    assert inspect_dds_layout(path).data_offset == 128


@pytest.mark.parametrize("kwargs", [
    {"payload": False},
    {"cube": True},
    {"format_name": "bc7_srgb", "mip_count": 4},
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


@pytest.mark.parametrize(("format_name", "width", "height", "expected"), [
    ("rgba8", 3, 5, [(3, 5, 60, 128), (1, 2, 8, 188), (1, 1, 4, 196)]),
    ("bc1_unorm", 5, 7, [(5, 7, 32, 148), (2, 3, 8, 180), (1, 1, 8, 188)]),
])
def test_layout_reports_exact_odd_dimension_mip_offsets(
        tmp_path, format_name, width, height, expected):
    path = tmp_path / "layout.dds"
    path.write_bytes(_dds(width, height, format_name, mip_count=3))

    layout = inspect_dds_layout(path)

    assert layout is not None
    assert [(mip.width, mip.height, mip.length, mip.offset)
            for mip in layout.mips] == expected
    assert layout.payload_end == len(path.read_bytes())


@pytest.mark.parametrize("format_name", [
    "bc1_unorm", "bc2_unorm", "bc3_unorm", "bc7_unorm", "rgba8", "bgra8",
])
def test_layout_uses_format_specific_unit_sizes(tmp_path, format_name):
    path = tmp_path / f"{format_name}.dds"
    path.write_bytes(_dds(5, 7, format_name, mip_count=2))

    layout = inspect_dds_layout(path)
    assert layout.info.format == format_name
    assert layout.info.compressed is format_name.startswith("bc")
    assert layout.info.requires_bc is format_name.startswith("bc")
    bytes_per_unit = (4 if not format_name.startswith("bc")
                      else 8 if format_name.startswith("bc1") else 16)

    assert [mip.bytes_per_unit for mip in layout.mips] == [
        bytes_per_unit, bytes_per_unit]
    units = ([(2, 2), (1, 1)] if format_name.startswith("bc")
             else [(5, 7), (2, 3)])
    assert [mip.length for mip, (units_x, units_y) in zip(
        layout.mips, units)] == [
            units_x * units_y * bytes_per_unit
            for units_x, units_y in units]

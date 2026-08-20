"""Header-only DDS inspection for conservative native texture publication."""

from dataclasses import dataclass
import os
import struct


_DDS_MAGIC = b"DDS "
_DDS_HEADER_SIZE = 124
_PIXEL_FORMAT_SIZE = 32
_DX10_FOURCC = b"DX10"
_DDPF_RGB = 0x40
_DDPF_FOURCC = 0x4
_DDSCAPS2_CUBEMAP = 0x200
_DDSCAPS2_VOLUME = 0x200000
_D3D10_RESOURCE_DIMENSION_TEXTURE2D = 3
_D3D10_RESOURCE_MISC_TEXTURECUBE = 0x4
_MAX_DDS_DIMENSION = 65536


@dataclass(frozen=True)
class DDSInfo:
    """The validated DDS properties needed by the two texture transports."""

    width: int
    height: int
    mip_count: int
    format: str
    compressed: bool
    requires_bc: bool


_DXGI_FORMATS = {
    71: "bc1_unorm",
    72: "bc1_srgb",
    74: "bc2_unorm",
    75: "bc2_srgb",
    77: "bc3_unorm",
    78: "bc3_srgb",
    80: "bc4_unorm",
    81: "bc4_snorm",
    83: "bc5_unorm",
    84: "bc5_snorm",
    95: "bc6h_ufloat",
    96: "bc6h_float",
    98: "bc7_unorm",
    99: "bc7_srgb",
}

_COMPRESSED_FORMATS = frozenset(_DXGI_FORMATS.values())


def _u32(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def _fourcc(data, offset):
    return bytes(data[offset:offset + 4])


def _mip_count(raw_count, width, height):
    count = max(1, raw_count)
    max_dimension = max(width, height)
    # A full chain has one level for each bit in the largest dimension. More
    # levels cannot describe a 2D image and usually indicate corrupt headers.
    if count > max_dimension.bit_length():
        return None
    return count


def _info(width, height, mip_count, format_name):
    compressed = format_name in _COMPRESSED_FORMATS
    return DDSInfo(width, height, mip_count, format_name, compressed,
                   compressed)


def _inspect_header(header):
    if len(header) < 128 or header[:4] != _DDS_MAGIC:
        return None
    if _u32(header, 4) != _DDS_HEADER_SIZE:
        return None
    if _u32(header, 76) != _PIXEL_FORMAT_SIZE:
        return None

    height = _u32(header, 12)
    width = _u32(header, 16)
    if not (0 < width <= _MAX_DDS_DIMENSION
            and 0 < height <= _MAX_DDS_DIMENSION):
        return None
    if _u32(header, 24) > 1:
        return None
    mip_count = _mip_count(_u32(header, 28), width, height)
    if mip_count is None:
        return None

    caps2 = _u32(header, 112)
    if caps2 & (_DDSCAPS2_CUBEMAP | _DDSCAPS2_VOLUME):
        return None

    pixel_flags = _u32(header, 80)
    fourcc = _fourcc(header, 84)
    if fourcc == _DX10_FOURCC:
        if len(header) < 148:
            return None
        dxgi_format = _u32(header, 128)
        format_name = _DXGI_FORMATS.get(dxgi_format)
        if format_name is None:
            return None
        if _u32(header, 132) != _D3D10_RESOURCE_DIMENSION_TEXTURE2D:
            return None
        if _u32(header, 136) != 1:
            return None
        if _u32(header, 140) & _D3D10_RESOURCE_MISC_TEXTURECUBE:
            return None
        return _info(width, height, mip_count, format_name)

    legacy_formats = {
        b"DXT1": "bc1_unorm",
        b"DXT3": "bc2_unorm",
        b"DXT5": "bc3_unorm",
        b"ATI1": "bc4_unorm",
        b"BC4U": "bc4_unorm",
        b"BC4S": "bc4_snorm",
        b"ATI2": "bc5_unorm",
        b"BC5U": "bc5_unorm",
        b"BC5S": "bc5_snorm",
    }
    if fourcc in legacy_formats:
        if not (pixel_flags & _DDPF_FOURCC):
            return None
        return _info(width, height, mip_count, legacy_formats[fourcc])

    if not (pixel_flags & _DDPF_RGB) or _u32(header, 88) != 32:
        return None
    masks = tuple(_u32(header, offset) for offset in (92, 96, 100, 104))
    if masks == (0x000000FF, 0x0000FF00, 0x00FF0000, 0xFF000000):
        return _info(width, height, mip_count, "rgba8")
    if masks == (0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000):
        return _info(width, height, mip_count, "bgra8")
    return None


def inspect_dds(path):
    """Inspect only the DDS header and return ``None`` for unsafe inputs."""
    try:
        with open(path, "rb") as stream:
            header = stream.read(148)
            file_size = os.fstat(stream.fileno()).st_size
    except (OSError, TypeError):
        return None
    info = _inspect_header(header)
    if info is None:
        return None
    header_size = 148 if _fourcc(header, 84) == _DX10_FOURCC else 128
    width, height = info.width, info.height
    payload_size = 0
    for _ in range(info.mip_count):
        if info.compressed:
            block_size = 8 if info.format.startswith(("bc1", "bc4")) else 16
            payload_size += ((width + 3) // 4) * ((height + 3) // 4) * block_size
        else:
            payload_size += width * height * 4
        width = max(1, width // 2)
        height = max(1, height // 2)
    if file_size < header_size + payload_size:
        return None
    return info


def native_dds_info(path, max_size=2048, transform="passthrough"):
    """Return native-delivery metadata when the source meets PR21 rules."""
    try:
        path_string = os.fsdecode(os.fspath(path))
    except (TypeError, ValueError):
        return None
    if (not isinstance(path, (str, bytes, os.PathLike))
            or not path_string.lower().endswith(".dds")
            or transform != "passthrough"):
        return None
    try:
        max_size = int(max_size)
    except (TypeError, ValueError):
        return None
    if max_size <= 0:
        return None
    info = inspect_dds(path)
    if info is None or max(info.width, info.height) > max_size:
        return None
    return info

"""Wrap a stored top mip (Unity Texture2D data, or any DXGI format) in a DX10 DDS the viewer uploads without
decoding, and read the top mip a captured DDS file holds."""

import struct

# Unity TextureFormat -> (DXGI unorm, DXGI sRGB, bytes per 4x4 block or None for 4 bytes per pixel).
FORMATS = {
    4: (28, 29, None),  # RGBA32
    10: (71, 72, 8),  # DXT1 / BC1
    12: (77, 78, 16),  # DXT5 / BC3
    24: (95, 95, 16),  # BC6H
    25: (98, 99, 16),  # BC7
    26: (80, 80, 8),  # BC4
    27: (83, 83, 16),  # BC5
}
MAX_DIMENSION = 16384
# Bytes per 4x4 block (None: 4 bytes per pixel) of the DXGI formats and legacy FourCCs captures use.
_DXGI_BLOCKS = {28: None, 29: None, 87: None, 91: None, 70: 8, 71: 8, 72: 8, 79: 8, 80: 8, 81: 8,
                73: 16, 74: 16, 75: 16, 76: 16, 77: 16, 78: 16, 82: 16, 83: 16, 84: 16,
                94: 16, 95: 16, 96: 16, 97: 16, 98: 16, 99: 16}
_FOURCC_BLOCKS = {b"DXT1": 8, b"ATI1": 8, b"BC4U": 8, b"BC4S": 8,
                  b"DXT2": 16, b"DXT3": 16, b"DXT4": 16, b"DXT5": 16, b"ATI2": 16, b"BC5U": 16, b"BC5S": 16}


def top_mip(raw):
    """A DDS file's top-mip image bytes, or None.

    Game textures are compared by their top mip, so a capture saved with its mip chain
    yields only the first level; a format whose level size is unknown keeps every byte
    after the header.
    """
    if raw[:4] != b"DDS " or len(raw) < 148:
        return None
    height, width = struct.unpack_from("<II", raw, 12)
    dx10 = raw[84:88] == b"DX10"
    data = raw[148:] if dx10 else raw[128:]
    if dx10:
        key, blocks = struct.unpack_from("<I", raw, 128)[0], _DXGI_BLOCKS
    else:
        key, blocks = raw[84:88], _FOURCC_BLOCKS
    if not 0 < width <= MAX_DIMENSION or not 0 < height <= MAX_DIMENSION or key not in blocks:
        return data
    size = _level_size(width, height, blocks[key])
    return data[:size] if len(data) >= size else None


def _level_size(width, height, block):
    return width * height * 4 if block is None else ((width + 3) // 4) * ((height + 3) // 4) * block


def mip0_size(width, height, unity_format):
    return _level_size(width, height, FORMATS[unity_format][2])


def encode(width, height, unity_format, data, *, srgb):
    """A single-mip DDS of the stored top mip, or ValueError for unsupported data."""
    if unity_format not in FORMATS or type(width) is not int or type(height) is not int:
        raise ValueError("Unsupported texture format.")
    if not (0 < width <= MAX_DIMENSION and 0 < height <= MAX_DIMENSION):
        raise ValueError("Unsupported texture size.")
    size = mip0_size(width, height, unity_format)
    if len(data) < size:
        raise ValueError("The texture data is incomplete.")
    unorm, srgb_format, _block = FORMATS[unity_format]
    return wrap(width, height, srgb_format if srgb else unorm, data[:size])


def wrap(width, height, dxgi_format, data):
    """A single-mip DX10 DDS holding ``data`` (one complete top mip) unchanged."""
    # DDSD_CAPS | HEIGHT | WIDTH | PIXELFORMAT | LINEARSIZE; DDSCAPS_TEXTURE.
    header = struct.pack("<7I44x", 124, 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000, height, width, len(data), 0, 1)
    pixel_format = struct.pack("<II4s5I", 32, 0x4, b"DX10", 0, 0, 0, 0, 0)
    caps = struct.pack("<5I", 0x1000, 0, 0, 0, 0)
    dx10 = struct.pack("<5I", dxgi_format, 3, 0, 1, 0)
    return b"DDS " + header + pixel_format + caps + dx10 + bytes(data)

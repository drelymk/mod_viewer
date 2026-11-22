"""Wrap a stored top mip (Unity Texture2D data, or any DXGI format) in a DX10 DDS the viewer uploads without decoding."""

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


def mip0_size(width, height, unity_format):
    _unorm, _srgb, block = FORMATS[unity_format]
    if block is None:
        return width * height * 4
    return ((width + 3) // 4) * ((height + 3) // 4) * block


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

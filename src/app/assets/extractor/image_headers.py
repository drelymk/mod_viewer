"""Read image dimensions without decoding pixels or an imaging dependency."""
import struct

_SOF = frozenset((0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                  0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF))
_MAX_HEADER = 1024 * 1024


def _jpeg_size(stream):
    stream.seek(2)
    for _ in range(4096):
        if stream.tell() >= _MAX_HEADER or stream.read(1) != b"\xff":
            return None
        marker = stream.read(1)
        while marker == b"\xff" and stream.tell() < _MAX_HEADER:
            marker = stream.read(1)
        if not marker or marker[0] in (0, 0xD9, 0xDA):
            return None
        code = marker[0]
        if code == 0x01 or 0xD0 <= code <= 0xD8:
            continue
        raw = stream.read(2)
        if len(raw) != 2:
            return None
        length, = struct.unpack(">H", raw)
        if length < 2 or stream.tell() + length - 2 > _MAX_HEADER:
            return None
        if code in _SOF:
            data = stream.read(5)
            if length < 8 or len(data) != 5:
                return None
            height, width = struct.unpack_from(">HH", data, 1)
            return width, height
        stream.seek(length - 2, 1)
    return None


def image_metadata(path):
    unknown = dict(width=0, height=0, mips=None)
    try:
        with path.open("rb") as stream:
            header = stream.read(128)
            mips = None
            if header[:4] == b"DDS " and len(header) == 128:
                if struct.unpack_from("<I", header, 4)[0] != 124:
                    return unknown
                height, width = struct.unpack_from("<II", header, 12)
                mips, = struct.unpack_from("<I", header, 28)
            elif header[:8] == b"\x89PNG\r\n\x1a\n" and len(header) >= 33:
                if header[8:16] != b"\x00\x00\x00\rIHDR":
                    return unknown
                width, height = struct.unpack_from(">II", header, 16)
            elif header[:2] == b"\xff\xd8":
                size = _jpeg_size(stream)
                if size is None:
                    return unknown
                width, height = size
            else:
                return unknown
            if not (0 < width <= 0x7FFFFFFF and 0 < height <= 0x7FFFFFFF):
                return unknown
            return dict(width=width, height=height, mips=mips)
    except (OSError, ValueError, struct.error):
        return unknown

"""Generated shared-memory manifests and in-memory capture sections."""

import struct

from tests.support.extraction_data import make_capture


PREFIX = "Local\\ModManager.Capture.7." + "1" * 32 + "."


class Seed:
    def __init__(self, store, path=""):
        self.store, self.path = store, path

    def __truediv__(self, name):
        return Seed(self.store, self.path + "/" + name)

    def mkdir(self):
        pass

    def write_bytes(self, value):
        self.store[self.path.rsplit("/", 1)[-1]] = value

    def write_text(self, value, **_kwargs):
        self.write_bytes(value.encode())


class Block:
    def __init__(self, data, name, capacity, offset):
        self.data, self.name, self.capacity, self.offset = data, name, capacity, offset

    def read(self, offset, length):
        return self.data[offset : offset + length]

    def close(self):
        pass


def capture_data(data=None):
    if data is None:
        data = {}
        make_capture(Seed(data))
        # A minimal PNG exercises memory-backed texture copying during export.
        png = (
            b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR"
            + struct.pack(">II", 16, 16)
            + b"\x08\x02\0\0\0"
            + b"\0" * 16
        )
        data["000002-ps-t0=66666666.png"] = png
    manifest = {
        "version": 1,
        "prefix": PREFIX,
        "capture": "frameanalysis-sample",
        "names": {},
        "files": {},
    }
    blocks = {}
    for identity, (name, payload) in enumerate(data.items(), 1):
        mapping = PREFIX + str(identity) + ".0"
        blocks[mapping] = payload
        capacity = max(65536, (len(payload) + 65535) // 65536 * 65536)
        manifest["names"]["frameanalysis-sample/" + name] = identity
        manifest["files"][str(identity)] = {
            "size": len(payload),
            "mappings": [{"name": mapping, "capacity": capacity, "offset": 0}],
        }

    def factory(name, capacity, offset):
        return Block(blocks[name], name, capacity, offset)

    return manifest, factory

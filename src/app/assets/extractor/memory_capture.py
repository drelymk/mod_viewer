"""Read a completed capture through existing shared-memory sections, never files."""

import ctypes
from ctypes import wintypes
import io
import os
from pathlib import PurePosixPath, PureWindowsPath
import re
from types import SimpleNamespace
from .budget import CaptureBudget


def member(value):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 32768
        or re.search(r"[\x00-\x1f:]", value)
    ):
        raise ValueError("Invalid capture member")
    parts = value.replace("\\", "/").split("/")
    if PureWindowsPath(value).drive or any(p in {"", ".", ".."} for p in parts):
        raise ValueError("Capture member escapes its source")
    return "/".join(parts)


class Mapping:
    def __init__(self, name, capacity, offset):
        if os.name != "nt":
            raise OSError("Windows shared memory required")
        self.name, self.capacity, self.offset = name, capacity, offset
        self.kernel = kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenFileMappingW.argtypes = (
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        )
        kernel.OpenFileMappingW.restype = wintypes.HANDLE
        kernel.MapViewOfFile.argtypes = (
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_size_t,
        )
        kernel.MapViewOfFile.restype = ctypes.c_void_p
        kernel.UnmapViewOfFile.argtypes = (ctypes.c_void_p,)
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        self.handle = kernel.OpenFileMappingW(4, False, name)
        self.view = None
        if not self.handle:
            raise OSError("Capture source no longer exists")
        self.view = kernel.MapViewOfFile(self.handle, 4, 0, 0, capacity)
        if not self.view:
            self.close()
            raise OSError("Cannot map capture source")

    def read(self, offset, length):
        if offset < 0 or length < 0 or offset + length > self.capacity or not self.view:
            raise ValueError("Invalid capture read extent")
        return ctypes.string_at(self.view + offset, length)

    def close(self):
        if self.view:
            self.kernel.UnmapViewOfFile(self.view)
            self.view = None
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class CaptureStream(io.RawIOBase):
    def __init__(self, size, blocks):
        super().__init__()
        self.size, self.blocks, self.position = size, blocks, 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        position = offset + (
            self.position if whence == 1 else self.size if whence == 2 else 0
        )
        if whence not in (0, 1, 2) or position < 0:
            raise ValueError("Invalid capture seek")
        self.position = position
        return position

    def readinto(self, target):
        count = min(len(target), max(0, self.size - self.position))
        copied = 0
        for block in self.blocks:
            if copied == count:
                break
            if self.position >= block.offset + block.capacity:
                continue
            offset = self.position - block.offset
            length = min(count - copied, block.capacity - offset)
            target[copied : copied + length] = block.read(offset, length)
            self.position += length
            copied += length
        if copied != count:
            raise ValueError("Capture has an unmapped extent")
        return count


class CapturePath:
    def __init__(self, source, path=""):
        self.source, self.path = source, source.canonical_member(path)

    def __str__(self):
        return "memory:" + self.path

    def __lt__(self, other):
        return self.path < other.path

    def __truediv__(self, name):
        return CapturePath(
            self.source, member(self.path + "/" + name if self.path else name)
        )

    @property
    def name(self):
        return PurePosixPath(self.path).name

    @property
    def suffix(self):
        return PurePosixPath(self.path).suffix

    def resolve(self):
        return self

    def is_relative_to(self, root):
        return self.source is root.source

    def exists(self):
        return self.is_dir() or self.is_file()

    def is_dir(self):
        return not self.path

    def is_file(self):
        return self.path in self.source.names

    def iterdir(self):
        return [
            CapturePath(self.source, name)
            for name in self.source.names
            if "/" not in name
        ]

    def stat(self):
        return SimpleNamespace(
            st_size=self.source.files[self.source.names[self.path]][0]
        )

    def open(self, mode="r", encoding="utf-8", errors="strict"):
        if any(c in mode for c in "wax+"):
            raise ValueError("Capture sources are read-only")
        if not self.is_file():
            raise FileNotFoundError(self.path)
        size, blocks = self.source.files[self.source.names[self.path]]
        stream = io.BufferedReader(CaptureStream(size, blocks))
        return (
            stream
            if "b" in mode
            else io.TextIOWrapper(stream, encoding=encoding, errors=errors)
        )

    def read_bytes(self):
        with self.open("rb") as stream:
            return stream.read()


class MemoryCapture:
    """Validated capability-backed capture; no member name authorizes filesystem access."""

    def __init__(self, manifest, *, mapping_factory=Mapping, budget=None):
        self.budget = budget or CaptureBudget()
        self.files, self.names = {}, {}
        self._members = {}
        self.root = CapturePath(self)
        self._blocks = []
        try:
            if not isinstance(manifest, dict):
                raise ValueError("Invalid capture manifest")
            prefix, capture = manifest.get("prefix"), member(manifest.get("capture"))
            if (
                manifest.get("version") != 1
                or not isinstance(prefix, str)
                or re.fullmatch(
                    r"Local\\ModManager\.Capture\.[1-9][0-9]*\.[0-9a-f]{32}\.", prefix
                )
                is None
            ):
                raise ValueError("Invalid capture capability")
            files, names = manifest.get("files"), manifest.get("names")
            if (
                not isinstance(files, dict)
                or not isinstance(names, dict)
                or len(files) > self.budget.max_files
                or len(names) > self.budget.max_files
            ):
                raise ValueError("Invalid capture inventory")
            sections = []
            for identity, file in files.items():
                if (
                    not isinstance(identity, str)
                    or not identity.isdecimal()
                    or not 0 < int(identity) < 2**64
                ):
                    raise ValueError("Invalid capture identity")
                if not isinstance(file, dict):
                    raise ValueError("Invalid capture file")
                size = file.get("size")
                mappings = file.get("mappings")
                if (
                    type(size) is not int
                    or size < 0
                    or not isinstance(mappings, list)
                    or len(mappings) > self.budget.max_mappings
                ):
                    raise ValueError("Invalid capture size")
                self.budget.source(int(identity), size)
                offset, blocks = 0, []
                for i, raw in enumerate(mappings):
                    if not isinstance(raw, dict):
                        raise ValueError("Invalid capture section")
                    capacity = raw.get("capacity")
                    if (
                        raw.get("offset") != offset
                        or type(capacity) is not int
                        or not 65536 <= capacity <= 8 * 1024**2
                        or raw.get("name") != prefix + identity + "." + str(i)
                    ):
                        raise ValueError("Invalid capture section")
                    self.budget.mapping(capacity)
                    sections.append((blocks, raw["name"], capacity, offset))
                    offset += capacity
                if size > offset:
                    raise ValueError("Capture extent has no backing")
                self.files[int(identity)] = size, blocks
            for name, identity in names.items():
                path = member(name)
                if type(identity) is not int or identity not in self.files:
                    raise ValueError("Invalid capture alias")
                if path.startswith(capture + "/"):
                    path = path[len(capture) + 1 :]
                elif path.startswith("frameanalysisdeduped/"):
                    path = "deduped/" + path.split("/", 1)[1]
                else:
                    raise ValueError("Capture contains another frame")
                if path.casefold() in self._members:
                    raise ValueError("Ambiguous capture alias")
                self._members[path.casefold()] = path
                self.names[path] = identity
            if "log.txt" not in self._members:
                raise ValueError("Capture log is missing")
            # Validate the whole manifest before opening any section handles.
            for blocks, name, capacity, offset in sections:
                block = mapping_factory(name, capacity, offset)
                blocks.append(block)
                self._blocks.append(block)
        except BaseException:
            self.close()
            raise

    def canonical_member(self, path):
        """Match Windows capture members without changing stored spelling."""
        return self._members.get(path.casefold(), path)

    def close(self):
        for block in self._blocks:
            block.close()
        self._blocks.clear()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

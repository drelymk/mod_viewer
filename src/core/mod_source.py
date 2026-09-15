"""Lazy sources for files owned by the currently opened mod.

The model pipeline deals in source-relative resource names. This module is
the narrow boundary that maps those names to either ordinary files or virtual
archive members; callers do not need an archive-specific loading path.
"""

from __future__ import annotations

from dataclasses import dataclass
import ntpath
import os
import posixpath
import threading
import zipfile

from .resource_paths import safe_resource_path
from .sevenzip import SevenZipCLI, SevenZipError


ARCHIVE_EXTENSIONS = {
    ".zip": "zip",
    ".7z": "7z",
    ".rar": "rar",
}
_ARCHIVE_LABELS = {"zip": "ZIP", "7z": "7z", "rar": "RAR"}
_MAX_ARCHIVE_MEMBERS = 100_000
_MAX_ARCHIVE_MEMBER_BYTES = 512 * 1024 * 1024
_MAX_ARCHIVE_READ_BYTES = 2 * 1024 * 1024 * 1024

# Keep the old names available for tests and compatibility with existing ZIP
# limit tuning.
_MAX_ZIP_MEMBERS = _MAX_ARCHIVE_MEMBERS
_MAX_ZIP_MEMBER_BYTES = _MAX_ARCHIVE_MEMBER_BYTES
_MAX_ZIP_READ_BYTES = _MAX_ARCHIVE_READ_BYTES


class ModSourceError(ValueError):
    """A selected mod source is invalid or cannot be read safely."""


class ModSource:
    """Minimal source contract shared by directory and archive-backed mods."""

    kind = None
    source_path = None
    virtual = False
    read_only = True

    def exists(self, reference):
        return self.is_file(reference)

    def read(self, reference):
        return self.read_bytes(reference)

    def resolve(self, relative_path):
        return self.resolve_resource(relative_path)

    def size(self, reference):
        raise NotImplementedError

    def read_prefix(self, reference, length):
        raise NotImplementedError


class SourcePath(str):
    """A string-compatible source reference with its logical identity."""

    __slots__ = ("source", "logical_path", "member_name")

    def __new__(cls, value, source, logical_path, member_name=None):
        result = super().__new__(cls, value)
        result.source = source
        result.logical_path = logical_path
        result.member_name = member_name
        return result


def archive_kind_for_path(path):
    """Return the supported archive kind for a path, or ``None``."""
    try:
        value = os.fsdecode(os.fspath(path))
    except (TypeError, ValueError):
        return None
    return ARCHIVE_EXTENSIONS.get(os.path.splitext(value)[1].casefold())


def is_archive_path(path):
    return archive_kind_for_path(path) is not None


def _as_path(value):
    try:
        value = os.fspath(value)
    except TypeError:
        return None
    return value if isinstance(value, str) else None


def _normalized_relative(value, *, allow_parent=False):
    """Normalize a mod-authored path and reject filesystem escape syntax."""
    value = _as_path(value)
    if not value or "\x00" in value:
        return None
    if (os.path.isabs(value) or ntpath.isabs(value)
            or os.path.splitdrive(value)[0] or ntpath.splitdrive(value)[0]):
        return None
    value = value.replace("\\", "/")
    parts = []
    for part in value.split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            if not parts and not allow_parent:
                return None
            if parts and parts[-1] != "..":
                parts.pop()
            elif allow_parent and len(parts) == 0:
                parts.append(part)
            else:
                return None
        else:
            parts.append(part)
    result = "/".join(parts)
    if result.startswith("../") and not allow_parent:
        return None
    if result == ".." and not allow_parent:
        return None
    return result


@dataclass(frozen=True)
class _ArchiveMember:
    raw_name: str
    size: int
    encrypted: bool = False
    is_dir: bool = False
    backend_data: object = None


class DirectoryModSource(ModSource):
    kind = "directory"
    virtual = False
    read_only = False

    def __init__(self, source_path):
        path = _as_path(source_path)
        if not path:
            raise ModSourceError("Mod source path is required.")
        self.source_path = os.path.abspath(path)
        self.root = self.source_path

    def list_files(self):
        result = []
        for base, dirs, files in os.walk(self.root):
            dirs.sort()
            for name in sorted(files):
                result.append(os.path.relpath(
                    os.path.join(base, name), self.root).replace("\\", "/"))
        return result

    def document_path(self, logical_path):
        return os.path.join(self.root, *str(logical_path).replace(
            "\\", "/").split("/"))

    def logical_path(self, reference):
        if isinstance(reference, SourcePath) and reference.source is self:
            return reference.logical_path
        try:
            return os.path.relpath(reference, self.root).replace("\\", "/")
        except (TypeError, ValueError):
            return str(reference).replace("\\", "/")

    def is_resource_reference(self, reference):
        if isinstance(reference, SourcePath):
            return reference.source is self
        try:
            target = os.path.abspath(os.fspath(reference))
            return os.path.commonpath((target, self.root)) == self.root
        except (OSError, TypeError, ValueError):
            return False

    def resolve_resource(self, relative_path):
        resolved = safe_resource_path(self.root, relative_path)
        if resolved is None:
            return None
        return resolved

    def exists(self, reference):
        return self.is_file(reference)

    def is_file(self, reference):
        try:
            return os.path.isfile(os.fspath(reference))
        except (OSError, TypeError, ValueError):
            return False

    def size(self, reference):
        return os.path.getsize(os.fspath(reference))

    def read_bytes(self, reference):
        with open(os.fspath(reference), "rb") as stream:
            return stream.read()

    def read_prefix(self, reference, length):
        with open(os.fspath(reference), "rb") as stream:
            return stream.read(int(length))

    def read_text(self, reference):
        return self.read_bytes(reference).decode("utf-8")

    def same_reference(self, left, right):
        try:
            return os.path.normcase(os.path.abspath(os.fspath(left))) == \
                os.path.normcase(os.path.abspath(os.fspath(right)))
        except (OSError, TypeError, ValueError):
            return False


class _VirtualArchiveModSource(ModSource):
    """Shared logical path and safety behavior for virtual archives."""

    virtual = True
    read_only = True

    def __init__(self, source_path, members, *, kind, member_count=None):
        self.source_path = os.path.abspath(source_path)
        self.kind = kind
        self._archive_label = _ARCHIVE_LABELS.get(kind, kind)
        members = list(members)
        count = len(members) if member_count is None else int(member_count)
        if count > self._max_members():
            raise ModSourceError(
                f"{self._archive_label} contains too many members "
                f"(limit {self._max_members():,}).")

        normalized = []
        seen = set()
        seen_folded = set()
        for entry in members:
            name = self._normalize_member_name(entry.raw_name)
            if name is None:
                raise ModSourceError(
                    f"{self._archive_label} contains an unsafe member name: "
                    f"{entry.raw_name!r}.")
            if not name or entry.is_dir:
                continue
            if name in seen:
                raise ModSourceError(
                    f"{self._archive_label} contains duplicate member: {name}.")
            if name.casefold() in seen_folded:
                raise ModSourceError(
                    f"{self._archive_label} contains case-ambiguous members: "
                    f"{name}.")
            seen.add(name)
            seen_folded.add(name.casefold())
            normalized.append((name, entry))

        top_levels = {name.split("/", 1)[0] for name, _entry in normalized}
        wrapper = ""
        if len(top_levels) == 1:
            candidate = next(iter(top_levels))
            if all(name.startswith(candidate + "/")
                   for name, _entry in normalized):
                wrapper = candidate
        self.wrapper_root = wrapper
        self._members = {}
        self._folded = {}
        for full_name, entry in normalized:
            logical = full_name
            if wrapper:
                logical = full_name[len(wrapper) + 1:]
            if not logical:
                continue
            self._members[logical] = entry
            self._folded.setdefault(logical.casefold(), []).append(logical)
        self._requested_members = set()
        self._requested_bytes = 0
        self._read_lock = threading.RLock()

    @staticmethod
    def _normalize_member_name(name):
        value = _as_path(name)
        if not value or "\x00" in value:
            return None
        value = value.replace("\\", "/")
        if (value.startswith("/") or ntpath.isabs(value)
                or ntpath.splitdrive(value)[0]):
            return None
        parts = []
        for part in value.split("/"):
            if not part or part == ".":
                continue
            if part == "..":
                if not parts:
                    return None
                parts.pop()
                continue
            parts.append(part)
        return "/".join(parts)

    def _logical_name(self, reference):
        if isinstance(reference, SourcePath) and reference.source is self:
            return reference.logical_path
        value = _as_path(reference)
        if value and value.startswith(self.source_path + "::"):
            value = value[len(self.source_path) + 2:]
        return _normalized_relative(value, allow_parent=True)

    def _lookup(self, reference):
        logical = self._logical_name(reference)
        if logical is None:
            return None
        if logical.startswith("../") or logical == "..":
            if not self.wrapper_root:
                return None
            full = posixpath.normpath(self.wrapper_root + "/" + logical)
            if full.startswith("../") or full == "..":
                return None
            member = full
        else:
            member = logical
        exact = self._members.get(member)
        if exact is not None:
            return logical, exact
        candidates = self._folded.get(member.casefold(), ())
        if len(candidates) != 1:
            return None
        return candidates[0], self._members[candidates[0]]

    def list_files(self):
        return sorted(self._members)

    def document_path(self, logical_path):
        logical = _normalized_relative(logical_path)
        lookup = self._lookup(logical) if logical else None
        if lookup is None:
            raise ModSourceError(
                f"{self._archive_label} member is not a file: {logical_path!r}")
        resolved_logical, entry = lookup
        return SourcePath(
            f"{self.source_path}::{resolved_logical}", self,
            resolved_logical, entry.raw_name)

    def logical_path(self, reference):
        value = self._logical_name(reference)
        return value if value is not None else str(reference).replace("\\", "/")

    def is_resource_reference(self, reference):
        return isinstance(reference, SourcePath) and reference.source is self

    def resolve_resource(self, relative_path):
        logical = _normalized_relative(relative_path, allow_parent=True)
        if logical is None:
            return None
        lookup = self._lookup(logical)
        if lookup is None:
            if logical.startswith("../") and not self.wrapper_root:
                return None
            return SourcePath(logical, self, logical, None)
        resolved_logical, entry = lookup
        return SourcePath(
            resolved_logical, self, resolved_logical, entry.raw_name)

    def exists(self, reference):
        return self.is_file(reference)

    def is_file(self, reference):
        return self._lookup(reference) is not None

    def _member_size(self, entry):
        try:
            size = int(entry.size)
        except (TypeError, ValueError) as error:
            raise ModSourceError(
                f"{self._archive_label} member {entry.raw_name!r} has an "
                "invalid size.") from error
        if size < 0:
            raise ModSourceError(
                f"{self._archive_label} member {entry.raw_name!r} has an "
                "invalid size.")
        if size > self._max_member_bytes():
            raise ModSourceError(
                f"{self._archive_label} member is too large "
                f"({size / 1048576:.1f} MiB).")
        return size

    def size(self, reference):
        lookup = self._lookup(reference)
        if lookup is None:
            raise FileNotFoundError(str(reference))
        return self._member_size(lookup[1])

    def read_bytes(self, reference):
        with self._read_lock:
            lookup = self._lookup(reference)
            if lookup is None:
                raise FileNotFoundError(str(reference))
            _logical, entry = lookup
            if entry.encrypted:
                raise ModSourceError(
                    f"Encrypted {self.kind} members are not supported.")
            size = self._member_size(entry)
            member_key = entry.raw_name.casefold()
            additional = (0 if member_key in self._requested_members else size)
            if self._requested_bytes + additional > self._max_read_bytes():
                raise ModSourceError(
                    f"Requested {self.kind} member data exceeds the 2 GiB "
                    "safety limit.")
            try:
                data = self._read_member(entry)
                data = bytes(data)
            except ModSourceError:
                raise
            except (OSError, TypeError, ValueError) as error:
                raise ModSourceError(
                    f"Could not read {self.kind} member "
                    f"{self.logical_path(reference)!r}: {error}") from error
            if len(data) != size:
                raise ModSourceError(
                    f"{self.kind} member {self.logical_path(reference)!r} "
                    "has an invalid size.")
            if member_key not in self._requested_members:
                self._requested_members.add(member_key)
                self._requested_bytes += len(data)
            return data

    def read_prefix(self, reference, length):
        with self._read_lock:
            lookup = self._lookup(reference)
            if lookup is None:
                raise FileNotFoundError(str(reference))
            _logical, entry = lookup
            if entry.encrypted:
                raise ModSourceError(
                    f"Encrypted {self.kind} members are not supported.")
            self._member_size(entry)
            try:
                length = int(length)
                if length < 0:
                    raise ValueError("prefix length must not be negative")
                data = bytes(self._read_prefix_member(entry, length))
            except ModSourceError:
                raise
            except (OSError, TypeError, ValueError) as error:
                raise ModSourceError(
                    f"Could not read {self.kind} member "
                    f"{self.logical_path(reference)!r}: {error}") from error
            if len(data) > length:
                raise ModSourceError(
                    f"{self.kind} member {self.logical_path(reference)!r} "
                    "returned an invalid prefix.")
            return data

    def read_text(self, reference):
        try:
            return self.read_bytes(reference).decode("utf-8")
        except UnicodeDecodeError as error:
            raise ModSourceError(
                f"{self._archive_label} member {self.logical_path(reference)!r} "
                "is not UTF-8.") from error

    def same_reference(self, left, right):
        left_lookup = self._lookup(left)
        right_lookup = self._lookup(right)
        return bool(left_lookup and right_lookup
                    and left_lookup[1].raw_name.casefold()
                    == right_lookup[1].raw_name.casefold())

    def _read_member(self, entry):
        raise NotImplementedError

    def _read_prefix_member(self, entry, length):
        raise NotImplementedError

    @staticmethod
    def _max_members():
        return _MAX_ARCHIVE_MEMBERS

    @staticmethod
    def _max_member_bytes():
        return _MAX_ARCHIVE_MEMBER_BYTES

    @staticmethod
    def _max_read_bytes():
        return _MAX_ARCHIVE_READ_BYTES


class ZipModSource(_VirtualArchiveModSource):
    kind = "zip"

    def __init__(self, source_path):
        path = _as_path(source_path)
        if not path:
            raise ModSourceError("ZIP source path is required.")
        if archive_kind_for_path(path) != "zip":
            raise ModSourceError("Only .zip mod sources are supported.")
        absolute = os.path.abspath(path)
        if not os.path.isfile(absolute):
            raise ModSourceError("ZIP mod source does not exist.")
        try:
            with zipfile.ZipFile(absolute) as archive:
                infos = archive.infolist()
        except (OSError, zipfile.BadZipFile, RuntimeError) as error:
            raise ModSourceError(
                f"Could not read ZIP mod source: {error}") from error
        entries = [
            _ArchiveMember(
                raw_name=info.filename, size=info.file_size,
                encrypted=bool(info.flag_bits & 0x1),
                is_dir=(info.is_dir()
                        or info.filename.replace("\\", "/").endswith("/")),
                backend_data=info)
            for info in infos
        ]
        super().__init__(absolute, entries, kind="zip",
                         member_count=len(infos))

    @staticmethod
    def _max_members():
        return _MAX_ZIP_MEMBERS

    @staticmethod
    def _max_member_bytes():
        return _MAX_ZIP_MEMBER_BYTES

    @staticmethod
    def _max_read_bytes():
        return _MAX_ZIP_READ_BYTES

    def _read_member(self, entry):
        try:
            with zipfile.ZipFile(self.source_path) as archive:
                with archive.open(entry.raw_name, "r") as stream:
                    data = stream.read(self._max_member_bytes() + 1)
        except (OSError, KeyError, RuntimeError, zipfile.BadZipFile) as error:
            raise ModSourceError(
                f"Could not read ZIP member {entry.raw_name!r}: {error}") \
                from error
        if len(data) > self._max_member_bytes():
            raise ModSourceError(
                f"ZIP member {entry.raw_name!r} has an invalid size.")
        return data

    def _read_prefix_member(self, entry, length):
        try:
            with zipfile.ZipFile(self.source_path) as archive:
                with archive.open(entry.raw_name, "r") as stream:
                    return stream.read(length)
        except (OSError, KeyError, RuntimeError, zipfile.BadZipFile) as error:
            raise ModSourceError(
                f"Could not read ZIP member {entry.raw_name!r}: {error}") \
                from error


class SevenZipModSource(_VirtualArchiveModSource):
    """Virtual source backed by the user's installed 7-Zip executable."""

    def __init__(self, source_path, *, client=None):
        path = _as_path(source_path)
        kind = archive_kind_for_path(path)
        if kind not in {"7z", "rar"}:
            raise ModSourceError("Only .7z and .rar mod sources are supported.")
        absolute = os.path.abspath(path)
        if not os.path.isfile(absolute):
            raise ModSourceError(
                f"{_ARCHIVE_LABELS[kind]} mod source does not exist.")
        try:
            self._client = SevenZipCLI() if client is None else client
            entries = self._client.list_members(absolute)
        except SevenZipError as error:
            detail = str(error)
            if detail == "Password-protected archives are not supported.":
                raise ModSourceError(detail) from error
            raise ModSourceError(
                f"Could not read {kind} mod source: {detail}") from error
        members = [
            _ArchiveMember(
                raw_name=entry.raw_name, size=entry.size,
                encrypted=entry.encrypted, is_dir=entry.is_dir,
                backend_data=entry)
            for entry in entries
        ]
        super().__init__(absolute, members, kind=kind,
                         member_count=len(entries))

    def _read_member(self, entry):
        try:
            return self._client.read_member(self.source_path, entry.raw_name)
        except SevenZipError as error:
            if str(error) == "Password-protected archives are not supported.":
                raise ModSourceError(str(error)) from error
            raise ModSourceError(
                f"Could not read {self.kind} member {entry.raw_name!r}: "
                f"{error}") from error

    def _read_prefix_member(self, entry, length):
        try:
            return self._client.read_prefix(
                self.source_path, entry.raw_name, length)
        except SevenZipError as error:
            if str(error) == "Password-protected archives are not supported.":
                raise ModSourceError(str(error)) from error
            raise ModSourceError(
                f"Could not read {self.kind} member {entry.raw_name!r}: "
                f"{error}") from error


def mod_source_for_path(path):
    """Create the source selected by a filesystem path."""
    kind = archive_kind_for_path(path)
    if kind == "zip":
        return ZipModSource(path)
    if kind in {"7z", "rar"}:
        return SevenZipModSource(path)
    return DirectoryModSource(path)


__all__ = [
    "ARCHIVE_EXTENSIONS", "DirectoryModSource", "ModSource",
    "SevenZipModSource", "SourcePath", "ZipModSource", "ModSourceError",
    "archive_kind_for_path", "is_archive_path",
    "mod_source_for_path", "_MAX_ARCHIVE_MEMBERS",
    "_MAX_ARCHIVE_MEMBER_BYTES", "_MAX_ARCHIVE_READ_BYTES",
    "_MAX_ZIP_MEMBERS", "_MAX_ZIP_MEMBER_BYTES", "_MAX_ZIP_READ_BYTES",
]

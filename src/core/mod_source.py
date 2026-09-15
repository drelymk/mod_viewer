"""Lazy sources for files owned by the currently opened mod.

The model pipeline deals in source-relative resource names.  This module is
the narrow boundary that maps those names to either ordinary files or ZIP
members; callers do not need an archive-specific loading path.
"""

from __future__ import annotations

import ntpath
import os
import posixpath
import threading
import zipfile

from .resource_paths import safe_resource_path


_MAX_ZIP_MEMBERS = 100_000
_MAX_ZIP_MEMBER_BYTES = 512 * 1024 * 1024
_MAX_ZIP_READ_BYTES = 2 * 1024 * 1024 * 1024


class ModSourceError(ValueError):
    """A selected mod source is invalid or cannot be read safely."""


class ModSource:
    """Minimal source contract shared by directory and ZIP-backed mods."""

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
    """A string-compatible source reference with its logical ZIP identity."""

    __slots__ = ("source", "logical_path", "member_name")

    def __new__(cls, value, source, logical_path, member_name=None):
        result = super().__new__(cls, value)
        result.source = source
        result.logical_path = logical_path
        result.member_name = member_name
        return result


def is_zip_path(path):
    try:
        return os.fspath(path).casefold().endswith(".zip")
    except (TypeError, ValueError):
        return False


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


class ZipModSource(ModSource):
    kind = "zip"
    virtual = True
    read_only = True

    def __init__(self, source_path):
        path = _as_path(source_path)
        if not path:
            raise ModSourceError("ZIP source path is required.")
        if not path.casefold().endswith(".zip"):
            raise ModSourceError("Only .zip mod sources are supported.")
        self.source_path = os.path.abspath(path)
        if not os.path.isfile(self.source_path):
            raise ModSourceError("ZIP mod source does not exist.")
        try:
            with zipfile.ZipFile(self.source_path) as archive:
                infos = archive.infolist()
        except (OSError, zipfile.BadZipFile, RuntimeError) as error:
            raise ModSourceError(f"Could not read ZIP mod source: {error}") from error
        if len(infos) > _MAX_ZIP_MEMBERS:
            raise ModSourceError(
                f"ZIP contains too many members (limit {_MAX_ZIP_MEMBERS:,}).")

        normalized = []
        seen = set()
        seen_folded = set()
        for info in infos:
            name = self._normalize_member_name(info.filename)
            if name is None:
                raise ModSourceError(
                    f"ZIP contains an unsafe member name: {info.filename!r}.")
            if not name:
                continue
            if info.is_dir() or info.filename.replace("\\", "/").endswith("/"):
                continue
            if name in seen:
                raise ModSourceError(f"ZIP contains duplicate member: {name}.")
            if name.casefold() in seen_folded:
                raise ModSourceError(
                    f"ZIP contains case-ambiguous members: {name}.")
            seen.add(name)
            seen_folded.add(name.casefold())
            normalized.append((name, info))

        top_levels = {name.split("/", 1)[0] for name, _info in normalized}
        wrapper = ""
        if len(top_levels) == 1:
            candidate = next(iter(top_levels))
            if all(name.startswith(candidate + "/") for name, _info in normalized):
                wrapper = candidate
        self.wrapper_root = wrapper
        self._members = {}
        self._folded = {}
        for full_name, info in normalized:
            logical = full_name
            if wrapper:
                logical = full_name[len(wrapper) + 1:]
            if not logical:
                continue
            # Keep the archive spelling for ZipFile.open(); the normalized
            # name is only the virtual/logical identity exposed to callers.
            self._members[logical] = (info.filename, info)
            self._folded.setdefault(logical.casefold(), []).append(logical)
        # Count each archive member only once.  The same source is reused by
        # staged editing and reloads, so charging repeated reads against a
        # lifetime budget would eventually reject an otherwise safe reload.
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
        # A resource reference may contain one parent segment when a wrapper
        # directory was detected. It must still land inside the ZIP.
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
            raise ModSourceError(f"ZIP member is not a file: {logical_path!r}")
        resolved_logical, (member, _info) = lookup
        return SourcePath(
            f"{self.source_path}::{resolved_logical}", self,
            resolved_logical, member)

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
            # Resolution is also used for existence checks; an absent member
            # remains a valid, source-contained reference.
            if logical.startswith("../") and not self.wrapper_root:
                return None
            return SourcePath(logical, self, logical, None)
        resolved_logical, (member, _info) = lookup
        return SourcePath(resolved_logical, self, resolved_logical, member)

    def exists(self, reference):
        return self.is_file(reference)

    def is_file(self, reference):
        return self._lookup(reference) is not None

    def size(self, reference):
        lookup = self._lookup(reference)
        if lookup is None:
            raise FileNotFoundError(str(reference))
        _logical, (_member, info) = lookup
        if info.file_size > _MAX_ZIP_MEMBER_BYTES:
            raise ModSourceError(
                f"ZIP member is too large ({info.file_size / 1048576:.1f} MiB).")
        return info.file_size

    def read_bytes(self, reference):
        with self._read_lock:
            lookup = self._lookup(reference)
            if lookup is None:
                raise FileNotFoundError(str(reference))
            _logical, (member, info) = lookup
            if info.flag_bits & 0x1:
                raise ModSourceError("Encrypted ZIP members are not supported.")
            size = self.size(reference)
            additional = 0 if member in self._requested_members else size
            if self._requested_bytes + additional > _MAX_ZIP_READ_BYTES:
                raise ModSourceError(
                    "Requested ZIP member data exceeds the 2 GiB safety limit.")
            try:
                with zipfile.ZipFile(self.source_path) as archive:
                    with archive.open(member, "r") as stream:
                        data = stream.read(_MAX_ZIP_MEMBER_BYTES + 1)
            except (OSError, KeyError, RuntimeError, zipfile.BadZipFile) as error:
                raise ModSourceError(f"Could not read ZIP member {member!r}: {error}") from error
            if len(data) > _MAX_ZIP_MEMBER_BYTES or len(data) != size:
                raise ModSourceError(f"ZIP member {member!r} has an invalid size.")
            if member not in self._requested_members:
                self._requested_members.add(member)
                self._requested_bytes += len(data)
            return data

    def read_prefix(self, reference, length):
        with self._read_lock:
            lookup = self._lookup(reference)
            if lookup is None:
                raise FileNotFoundError(str(reference))
            _logical, (member, info) = lookup
            if info.flag_bits & 0x1:
                raise ModSourceError("Encrypted ZIP members are not supported.")
            try:
                with zipfile.ZipFile(self.source_path) as archive:
                    with archive.open(member, "r") as stream:
                        return stream.read(int(length))
            except (OSError, KeyError, RuntimeError, zipfile.BadZipFile) as error:
                raise ModSourceError(f"Could not read ZIP member {member!r}: {error}") from error

    def read_text(self, reference):
        try:
            return self.read_bytes(reference).decode("utf-8")
        except UnicodeDecodeError as error:
            raise ModSourceError(
                f"ZIP member {self.logical_path(reference)!r} is not UTF-8.") \
                from error

    def same_reference(self, left, right):
        left_lookup = self._lookup(left)
        right_lookup = self._lookup(right)
        return bool(left_lookup and right_lookup
                    and left_lookup[1][0].casefold()
                    == right_lookup[1][0].casefold())


def mod_source_for_path(path):
    """Create the source selected by a filesystem path."""
    return ZipModSource(path) if is_zip_path(path) else DirectoryModSource(path)


__all__ = [
    "DirectoryModSource", "ModSource", "ZipModSource", "SourcePath",
    "ModSourceError",
    "is_zip_path", "mod_source_for_path", "_MAX_ZIP_MEMBERS",
    "_MAX_ZIP_MEMBER_BYTES", "_MAX_ZIP_READ_BYTES",
]

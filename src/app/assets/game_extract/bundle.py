"""Materialize one bundled helper on explicit extraction, sharing stored file content."""

import hashlib
import json
import lzma
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import tempfile
from threading import RLock
import zipfile

from app.settings import paths

KINDS = ("game-extract", "zzz-extract")
MAX_FILE = 160 * 1024 * 1024
MAX_RUNTIME = 512 * 1024 * 1024
MAX_ARCHIVE = 256 * 1024 * 1024
_DIGEST = re.compile(r"[0-9a-f]{64}")
_lock = RLock()
_cache = {}


def _relative(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 1024
            or PurePosixPath(value).is_absolute() or PureWindowsPath(value).drive
            or "\\" in value or ":" in value or any(ord(c) < 32 for c in value)
            or any(part in ("", ".", "..") for part in value.split("/"))):
        raise ValueError("Unsafe native helper archive path.")
    return value


def runtime_directory(kind, cancel, *, vendor=None):
    from .runtime import check_cancelled

    check_cancelled(cancel)
    if kind not in KINDS:
        raise ValueError("Unknown native helper bundle.")
    vendor = Path(vendor) if vendor is not None else Path(paths.vendor_dir())
    original = vendor / kind
    # Source checkouts keep normal publish directories for native development.
    if original.is_dir() or not (vendor / "extractors.zip").is_file():
        return original
    key = str(vendor.resolve()), kind
    with _lock:
        check_cancelled(cancel)
        if key in _cache:
            return Path(_cache[key].name) / kind
        temporary = tempfile.TemporaryDirectory(prefix="mod-viewer-native-", ignore_cleanup_errors=True)
        try:
            target = Path(temporary.name) / kind
            target.mkdir()
            _unpack(vendor / "extractors.zip", kind, target, lambda: check_cancelled(cancel))
            _cache[key] = temporary
            return target
        except BaseException:
            temporary.cleanup()
            raise


def _unpack(file, kind, target, checkpoint):
    try:
        if file.stat().st_size > MAX_ARCHIVE:
            raise ValueError("Native helper archive exceeds its size limit.")
        with zipfile.ZipFile(file) as archive:
            members = archive.infolist()
            names = [item.filename for item in members]
            if (len(members) > 4001 or len(set(names)) != len(names)
                    or archive.getinfo("catalog.json").file_size > 4 * 1024 * 1024):
                raise ValueError("Invalid native helper archive index.")
            catalog = json.loads(archive.read("catalog.json"))
            if not isinstance(catalog, dict) or catalog.get("format") != 1:
                raise ValueError("Unsupported native helper archive.")
            files = catalog["runtimes"][kind]
            if not isinstance(files, dict) or not 1 <= len(files) <= 2001:
                raise ValueError("Invalid native helper archive files.")
            if len({name.casefold() for name in files}) != len(files):
                raise ValueError("Ambiguous native helper archive paths.")
            total = 0
            for relative, digest in files.items():
                checkpoint()
                _relative(relative)
                if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
                    raise ValueError("Invalid native helper archive digest.")
                info = archive.getinfo(digest)
                total += info.file_size
                if info.file_size > MAX_FILE or total > MAX_RUNTIME:
                    raise ValueError("Native helper archive exceeds its size limits.")
                output = target / relative
                output.parent.mkdir(parents=True, exist_ok=True)
                checksum, size = hashlib.sha256(), 0
                with archive.open(info) as source, output.open("wb") as destination:
                    while chunk := source.read(1024 * 1024):
                        checkpoint()
                        size += len(chunk)
                        if size > info.file_size or size > MAX_FILE:
                            raise ValueError("Native helper archive exceeded its declared size.")
                        checksum.update(chunk)
                        destination.write(chunk)
                if size != info.file_size or checksum.hexdigest() != digest:
                    raise ValueError("Native helper archive checksum verification failed.")
    except (OSError, KeyError, TypeError, json.JSONDecodeError, zipfile.BadZipFile, lzma.LZMAError, RuntimeError) as error:
        raise ValueError("The bundled native helper archive is missing or invalid.") from error

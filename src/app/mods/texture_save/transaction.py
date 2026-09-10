"""Filesystem transaction helpers for texture saves."""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import os
import tempfile

from .errors import TextureSaveError


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _read_source(path):
    try:
        with open(path, "rb") as stream:
            return stream.read()
    except OSError as error:
        raise TextureSaveError(
            "texture_read_failed", "The source DDS could not be read.") from error


def _assert_source_unchanged(path, original_hash):
    try:
        current_hash = _sha256_bytes(_read_source(path))
    except TextureSaveError as error:
        raise TextureSaveError(
            "texture_changed_during_save",
            "The DDS changed while it was being saved.") from error
    if current_hash != original_hash:
        raise TextureSaveError(
            "texture_changed_during_save",
            "The DDS changed while it was being saved.")


def _write_backup(path, original):
    directory = os.path.dirname(path)
    stem = os.path.splitext(os.path.basename(path))[0]
    moment = datetime.now()
    for _index in range(10000):
        backup = os.path.join(
            directory, f"{stem}-{moment.strftime('%Y%m%d%H%M%S')}.dds")
        try:
            with open(backup, "xb") as stream:
                stream.write(original)
                stream.flush()
                os.fsync(stream.fileno())
            return backup
        except FileExistsError:
            moment += timedelta(seconds=1)
            continue
        except OSError as error:
            raise TextureSaveError(
                "backup_failed", "The DDS backup could not be created.") from error
    raise TextureSaveError(
        "backup_failed", "The DDS backup could not be created.")


def _write_temp(path, data):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="wb", dir=os.path.dirname(path),
                prefix=f".{os.path.basename(path)}.", suffix=".tmp",
                delete=False) as stream:
            temporary = stream.name
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        return temporary
    except OSError as error:
        if temporary and os.path.exists(temporary):
            os.remove(temporary)
        raise TextureSaveError(
            "texture_write_failed", "The DDS temporary file could not be written.") from error


def _replacement_completed(source_path, temporary_path, final):
    """Detect a replace call that completed before surfacing an OSError."""
    try:
        if os.path.exists(temporary_path):
            return False
        return _read_source(source_path) == final
    except Exception:
        return False


def replace_source(path, temporary, original, original_hash, candidate):
    """Back up and atomically replace a source after two change checks."""
    try:
        _assert_source_unchanged(path, original_hash)
        backup_path = _write_backup(path, original)
        _assert_source_unchanged(path, original_hash)
        try:
            os.replace(temporary, path)
        except OSError as error:
            if not _replacement_completed(path, temporary, candidate):
                raise TextureSaveError(
                    "texture_write_failed",
                    "The DDS could not be replaced safely.") from error
        return backup_path
    finally:
        if temporary and os.path.exists(temporary):
            os.remove(temporary)

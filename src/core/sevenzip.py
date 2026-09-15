"""Small subprocess adapter for the user's installed 7-Zip executable."""

from __future__ import annotations

from dataclasses import dataclass
import os
import shutil
import subprocess


class SevenZipError(RuntimeError):
    """A 7-Zip discovery, listing, or extraction failure."""


@dataclass(frozen=True)
class SevenZipEntry:
    """One record returned by 7-Zip's technical listing."""

    raw_name: str
    size: int
    is_dir: bool = False
    encrypted: bool = False


_PATH_CANDIDATES = ("7z.exe", "7z", "7zz.exe", "7zz")
_MISSING_MESSAGE = (
    "7-Zip is not installed or 7z.exe could not be found. "
    "Install 7-Zip and try again.")
_PASSWORD_WORDS = (
    "password", "encrypted headers", "can not open encrypted archive",
    "cannot open encrypted archive", "wrong password", "enter password",
)
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def find_7zip(*, which=None, environ=None, is_file=None):
    """Return a suitable 7-Zip executable from PATH or standard locations."""
    which = shutil.which if which is None else which
    environ = os.environ if environ is None else environ
    is_file = os.path.isfile if is_file is None else is_file
    for candidate in _PATH_CANDIDATES:
        resolved = which(candidate)
        if resolved:
            return resolved
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        root = environ.get(variable)
        if not root:
            continue
        candidate = os.path.join(root, "7-Zip", "7z.exe")
        if is_file(candidate):
            return candidate
    return None


def _diagnostic(stderr):
    if isinstance(stderr, bytes):
        text = stderr.decode("utf-8", errors="replace")
    else:
        text = str(stderr or "")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1][:400] if lines else "7-Zip reported an unknown error."


def _is_password_error(detail):
    folded = detail.casefold()
    return any(word in folded for word in _PASSWORD_WORDS)


def _error(detail):
    if _is_password_error(detail):
        return SevenZipError("Password-protected archives are not supported.")
    return SevenZipError(detail)


def parse_listing(output):
    """Parse 7-Zip ``l -slt`` output into file and directory entries."""
    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
    records = []
    current = {}

    def flush():
        if current:
            records.append(dict(current))
            current.clear()

    for line in str(output).splitlines():
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        if stripped and set(stripped) == {"-"}:
            flush()
            continue
        if " = " not in line:
            continue
        key, value = line.split(" = ", 1)
        current[key.strip()] = value
    flush()

    entries = []
    for record in records:
        raw_name = record.get("Path")
        # The first technical record describes the archive itself and has
        # Physical Size rather than a member Size; it is not a member.
        if not raw_name or "Size" not in record:
            continue
        folder = record.get("Folder", "-").strip().casefold()
        attributes = record.get("Attributes", "").strip()
        is_dir = folder in {"+", "1", "true", "yes"} \
            or "d" in attributes.casefold()
        try:
            size = int(record.get("Size", "0"))
        except (TypeError, ValueError) as error:
            raise SevenZipError(
                f"Invalid member size for {raw_name!r}.") from error
        if size < 0:
            raise SevenZipError(f"Invalid member size for {raw_name!r}.")
        encrypted = record.get("Encrypted", "-").strip().casefold() \
            in {"+", "1", "true", "yes"}
        entries.append(SevenZipEntry(
            raw_name=raw_name, size=size, is_dir=is_dir,
            encrypted=encrypted))
    return entries


class SevenZipCLI:
    """Run 7-Zip listing and bulk extraction operations."""

    def __init__(self, executable=None):
        self.executable = executable or find_7zip()
        if not self.executable:
            raise SevenZipError(_MISSING_MESSAGE)

    def _run(self, arguments):
        command = [self.executable, *arguments]
        try:
            return subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                check=False,
                creationflags=_CREATE_NO_WINDOW,
            )
        except OSError as error:
            raise SevenZipError(str(error)) from error

    def list_members(self, archive_path):
        result = self._run([
            "l", "-slt", "-ba", "-sccUTF-8", "--", os.fspath(archive_path)])
        if result.returncode != 0:
            raise _error(_diagnostic(result.stderr))
        return parse_listing(result.stdout)

    def extract_all(self, archive_path, output_dir):
        """Extract an archive into a caller-owned temporary directory."""
        result = self._run([
            "x", "-y", "-bd", "-bb0", "-sccUTF-8", "-sns-",
            f"-o{os.fspath(output_dir)}", "--", os.fspath(archive_path),
        ])
        if result.returncode != 0:
            raise _error(_diagnostic(result.stderr))


__all__ = [
    "SevenZipCLI", "SevenZipEntry", "SevenZipError", "find_7zip",
    "parse_listing",
]

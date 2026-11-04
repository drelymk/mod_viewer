"""Apply destination ACL inheritance when publishing generated Asset files."""

import os
import ctypes
from ctypes import wintypes
from pathlib import Path
import subprocess
import sys


def _volume_supports_acls(directory):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetVolumePathNameW.argtypes = (wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD)
    kernel.GetVolumePathNameW.restype = wintypes.BOOL
    kernel.GetVolumeInformationW.argtypes = (
        wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.DWORD)
    kernel.GetVolumeInformationW.restype = wintypes.BOOL
    volume = ctypes.create_unicode_buffer(32768)
    if not kernel.GetVolumePathNameW(str(Path(directory).resolve()), volume, len(volume)):
        raise ctypes.WinError(ctypes.get_last_error())
    flags = wintypes.DWORD()
    if not kernel.GetVolumeInformationW(
            volume.value, None, 0, None, None, ctypes.byref(flags), None, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return bool(flags.value & 0x00000008)  # FILE_PERSISTENT_ACLS


def inherit_directory_permissions(directory):
    """Reset generated Windows files to their current parent's inherited ACLs."""
    if sys.platform != "win32":
        return
    try:
        if not _volume_supports_acls(directory):
            return
        executable = Path(os.environ["SystemRoot"]) / "System32" / "icacls.exe"
        subprocess.run(
            [str(executable), str(directory), "/reset", "/T", "/L", "/Q"],
            check=True, capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.CalledProcessError) as error:
        raise OSError(
            "Could not apply destination permissions to the extracted Asset.") from error

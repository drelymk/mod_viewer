"""Serialize Viewer and headless extraction config/index transactions on Windows."""

from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import hashlib
import os
import threading

_state = threading.local()


@contextmanager
def process_lock(filename):
    if os.name != "nt" or getattr(_state, "depth", 0):
        yield
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.ReleaseMutex.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    identity = hashlib.sha256(
        os.path.normcase(os.path.realpath(filename)).encode()
    ).hexdigest()
    handle = kernel.CreateMutexW(None, False, "Local\\ModViewer.Config." + identity)
    if not handle:
        raise OSError("Cannot lock Viewer configuration")
    acquired = False
    try:
        acquired = kernel.WaitForSingleObject(handle, 30000) in (0, 0x80)
        if not acquired:
            raise OSError(
                "Viewer configuration is busy; retry when the other operation finishes"
            )
        _state.depth = 1
        yield
    finally:
        _state.depth = 0
        if acquired:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)

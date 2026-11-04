"""Native Windows ACL inheritance across standalone and installed Asset exports."""

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import re
import sys

import pytest

from app.assets import extraction, folders
from app.assets.extractor.discovery import analyze
from app.assets.extractor.export import Selection, export, extract
from app.settings import paths
from tests.support.extraction_data import make_capture


pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows ACL contract")


def _dacl(path):
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security.GetNamedSecurityInfoW.argtypes = (
        wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p))
    security.GetNamedSecurityInfoW.restype = wintypes.DWORD
    security.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = (
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(wintypes.LPWSTR), ctypes.c_void_p)
    security.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = (ctypes.c_void_p,)
    kernel.LocalFree.restype = ctypes.c_void_p
    descriptor = ctypes.c_void_p()
    text = wintypes.LPWSTR()
    result = security.GetNamedSecurityInfoW(
        str(path), 1, 4, None, None, None, None, ctypes.byref(descriptor))
    if result:
        raise ctypes.WinError(result)
    try:
        if not security.ConvertSecurityDescriptorToStringSecurityDescriptorW(
                descriptor, 1, 4, ctypes.byref(text), None):
            raise ctypes.WinError(ctypes.get_last_error())
        return text.value
    finally:
        kernel.LocalFree(text)
        kernel.LocalFree(descriptor)


def _assert_inherited(directory, extra_files=()):
    for path in [directory, *directory.rglob("*"), *extra_files]:
        dacl = _dacl(path)
        assert "P" not in dacl.split("(", 1)[0], (path, dacl)
        entries = [entry.split(";") for entry in re.findall(r"\(([^()]*)\)", dacl)]
        assert any(entry[-1] == "BG" and "ID" in entry[1] for entry in entries), (path, dacl)


@pytest.mark.parametrize("kind", ["standalone", "library"])
def test_exports_inherit_actual_destination_permissions(tmp_path, monkeypatch, kind):
    root = tmp_path / "library"
    category = root / "PlayerCharacterData"
    category.mkdir(parents=True)
    # Give the category an inheritable marker absent from the library root.
    subprocess.run(
        [str(Path(os.environ["SystemRoot"]) / "System32" / "icacls.exe"),
         str(category), "/grant", "*S-1-5-32-546:(OI)(CI)(RX)", "/Q"],
        check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    dump = make_capture(tmp_path)
    if kind == "standalone":
        report = export(extract(analyze(dump), Selection("Sample")), category)
        _assert_inherited(Path(report.output), [Path(report.report_path)])
    else:
        config = tmp_path / "config.json"
        config.write_text(json.dumps({"version": 1, "modFolders": [], "assetFolders": [
            {"type": "ZZMI", "path": str(root)}]}), encoding="utf-8")
        monkeypatch.setattr(paths, "config_path", lambda: str(config))
        monkeypatch.setattr(paths, "asset_index_dir", lambda: str(tmp_path / "indexes"))
        result = extraction.extract_asset(str(category), "Sample", str(dump))
        _assert_inherited(Path(result["path"]))
        result = extraction.extract_asset(
            str(category), "Updated", str(dump), result["path"])
        _assert_inherited(Path(result["path"]))
        assert not (category / "Sample").exists()
        assert not list(root.glob(".asset-extract-*"))
        assert folders.normalize_path(category) == folders.normalize_path(Path(result["path"]).parent)

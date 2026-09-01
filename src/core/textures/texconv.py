"""Small, deterministic adapter around the bundled DirectXTex encoder."""

from __future__ import annotations

import os
import shutil
import subprocess


FORMAT_TO_TEXCONV = {
    "bc1_unorm": "BC1_UNORM",
    "bc1_srgb": "BC1_UNORM_SRGB",
    "bc2_unorm": "BC2_UNORM",
    "bc2_srgb": "BC2_UNORM_SRGB",
    "bc3_unorm": "BC3_UNORM",
    "bc3_srgb": "BC3_UNORM_SRGB",
    "bc7_unorm": "BC7_UNORM",
    "bc7_srgb": "BC7_UNORM_SRGB",
    "rgba8": "R8G8B8A8_UNORM",
    "bgra8": "B8G8R8A8_UNORM",
}

DEFAULT_TIMEOUT = 120


class TexconvError(RuntimeError):
    """The encoder ran but did not produce a usable result."""


class TexconvUnavailableError(TexconvError):
    """No trusted or development texconv executable was available."""


def texconv_path():
    """Return the bundled encoder, or a development PATH fallback."""
    try:
        from app.settings.paths import is_frozen
        from app.settings.paths import texconv_path as configured_path
        bundled = configured_path()
    except (ImportError, AttributeError):
        is_frozen = lambda: False
        bundled = None
    if bundled and os.path.isfile(bundled):
        return bundled
    if is_frozen():
        return None
    return shutil.which("texconv.exe") or shutil.which("texconv")


def build_texconv_command(executable, input_png, output_dir, format_name,
                          mip_count, *, srgb=False):
    """Build the no-shell command for an exact-format DDS conversion."""
    dxgi_format = FORMAT_TO_TEXCONV.get(format_name)
    if dxgi_format is None:
        raise ValueError(f"Unsupported texconv format: {format_name}")
    try:
        mip_count = int(mip_count)
    except (TypeError, ValueError):
        raise ValueError("DDS mip count is invalid") from None
    if mip_count <= 0:
        raise ValueError("DDS mip count is invalid")
    command = [
        os.fspath(executable), "-nologo", "-y", "-ft", "DDS",
        "-f", dxgi_format, "-m", str(mip_count), "-if", "LINEAR",
        "-sepalpha", "-nogpu", "-o", os.fspath(output_dir),
        os.fspath(input_png),
    ]
    if srgb:
        # LINEAR selects the resize filter; it does not select the colorspace
        # used while filtering. Diffuse bake PNGs contain editor-sRGB bytes,
        # so they must opt into linear-light mip generation regardless of the
        # source DDS's physical UNORM/sRGB format.
        command.insert(command.index("-sepalpha"), "-srgb")
    return command


def _hidden_startupinfo():
    if os.name != "nt":
        return None
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return startupinfo


def encode_png_to_dds(input_png, output_dir, format_name, mip_count,
                      *, executable=None, runner=None,
                      timeout=DEFAULT_TIMEOUT, srgb=False):
    """Encode a PNG and return its candidate DDS path.

    The runner is injectable so command construction and failure handling can
    be tested without requiring the Windows-only bundled executable.
    """
    executable = executable or texconv_path()
    if not executable:
        raise TexconvUnavailableError("texconv.exe is unavailable")
    command = build_texconv_command(
        executable, input_png, output_dir, format_name, mip_count, srgb=srgb)
    runner = subprocess.run if runner is None else runner
    kwargs = {
        "shell": False,
        "check": False,
        "capture_output": True,
        "text": True,
        "timeout": timeout,
    }
    startupinfo = _hidden_startupinfo()
    if startupinfo is not None:
        kwargs["startupinfo"] = startupinfo
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = runner(command, **kwargs)
    except FileNotFoundError as error:
        raise TexconvUnavailableError("texconv.exe is unavailable") from error
    except subprocess.TimeoutExpired as error:
        raise TexconvError("texconv timed out") from error
    except OSError as error:
        raise TexconvError("texconv could not be started") from error
    if completed.returncode != 0:
        details = (completed.stderr or completed.stdout or "").strip()
        raise TexconvError(
            "texconv failed" + (f": {details}" if details else ""))
    candidate = os.path.join(
        os.fspath(output_dir),
        os.path.splitext(os.path.basename(os.fspath(input_png)))[0] + ".dds")
    if not os.path.isfile(candidate):
        raise TexconvError("texconv did not produce a DDS candidate")
    return candidate


__all__ = [
    "DEFAULT_TIMEOUT", "FORMAT_TO_TEXCONV", "build_texconv_command",
    "TexconvError", "TexconvUnavailableError", "encode_png_to_dds",
    "texconv_path",
]

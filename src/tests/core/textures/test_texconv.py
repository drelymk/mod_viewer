"""Deterministic tests for the native DDS encoder adapter."""

from types import SimpleNamespace
import os
import struct

import pytest
from PIL import Image

from core.textures import texconv
from core.textures.dds import inspect_dds_layout


def test_build_texconv_command_preserves_format_and_mip_count():
    command = texconv.build_texconv_command(
        "texconv.exe", "bake.png", "out", "bc7_srgb", 5, srgb=True)

    assert command[:2] == ["texconv.exe", "-nologo"]
    assert command[command.index("-f") + 1] == "BC7_UNORM_SRGB"
    assert command[command.index("-m") + 1] == "5"
    assert command[command.index("-if") + 1] == "LINEAR"
    assert "-srgb" in command
    assert "-resize" not in command
    assert "-flip" not in command


@pytest.mark.parametrize(("format_name", "dxgi_format"), [
    ("bc7_unorm", "BC7_UNORM"),
    ("bc7_srgb", "BC7_UNORM_SRGB"),
    ("rgba8", "R8G8B8A8_UNORM"),
])
def test_build_texconv_command_uses_diffuse_srgb_semantics(
        format_name, dxgi_format):
    command = texconv.build_texconv_command(
        "texconv.exe", "bake.png", "out", format_name, 2, srgb=True)

    assert command[command.index("-f") + 1] == dxgi_format
    assert "-srgb" in command


def test_build_texconv_command_leaves_colorspace_unspecified_for_data():
    command = texconv.build_texconv_command(
        "texconv.exe", "bake.png", "out", "bc7_unorm", 2, srgb=False)

    assert "-srgb" not in command


def test_encode_png_to_dds_uses_no_shell_and_checks_candidate(tmp_path):
    source = tmp_path / "bake.png"
    source.write_bytes(b"png fixture")
    output = tmp_path / "out"
    output.mkdir()
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        (output / "bake.dds").write_bytes(b"DDS candidate")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    result = texconv.encode_png_to_dds(
        source, output, "rgba8", 3, executable="texconv.exe", runner=runner,
        srgb=True)

    assert result == str(output / "bake.dds")
    command, kwargs = calls[0]
    assert command[-1] == str(source)
    assert kwargs["shell"] is False
    assert kwargs["check"] is False
    assert kwargs["timeout"] == texconv.DEFAULT_TIMEOUT
    assert "-srgb" in command


def test_encode_png_to_dds_reports_missing_encoder(monkeypatch, tmp_path):
    monkeypatch.setattr(texconv, "texconv_path", lambda: None)

    with pytest.raises(texconv.TexconvUnavailableError):
        texconv.encode_png_to_dds(tmp_path / "bake.png", tmp_path, "rgba8", 1)


@pytest.mark.parametrize("format_name", ["bc7_srgb", "bc7_unorm"])
def test_real_pinned_texconv_uses_srgb_mip_filtering_when_available(
        tmp_path, format_name):
    """Exercise the bundled encoder's colorspace behavior when packaged."""
    from app.settings.paths import texconv_path

    executable = texconv_path()
    if not executable or not os.path.isfile(executable):
        pytest.skip("bundled texconv is unavailable")

    source = tmp_path / "high-contrast.png"
    image = Image.new("RGBA", (2, 2))
    image.putdata([
        (0, 0, 0, 255), (255, 255, 255, 255),
        (255, 255, 255, 255), (0, 0, 0, 255),
    ])
    image.save(source)
    output = tmp_path / "out"
    output.mkdir()

    candidate = texconv.encode_png_to_dds(
        source, output, format_name, 2, executable=executable, srgb=True)
    layout = inspect_dds_layout(candidate)
    assert layout is not None
    assert layout.info.format == format_name
    mip = layout.mips[1]
    raw = bytearray(open(candidate, "rb").read(layout.data_offset))
    struct.pack_into("<II", raw, 12, 1, 1)
    struct.pack_into("<I", raw, 28, 1)
    one_mip = tmp_path / "mip1.dds"
    with open(candidate, "rb") as stream:
        stream.seek(mip.offset)
        mip_bytes = stream.read(mip.length)
    one_mip.write_bytes(bytes(raw) + mip_bytes)
    try:
        decoded = Image.open(one_mip).convert("RGBA")
    except Exception as error:
        pytest.skip(f"Pillow cannot decode BC7 in this environment: {error}")

    red = decoded.getpixel((0, 0))[0]
    assert 170 <= red <= 205
    assert abs(red - 128) > 25

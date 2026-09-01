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
        "texconv.exe", "bake.png", "out", "bc7_srgb", 5)

    assert command[:2] == ["texconv.exe", "-nologo"]
    assert command[command.index("-f") + 1] == "BC7_UNORM_SRGB"
    assert command[command.index("-m") + 1] == "5"
    assert command[command.index("-if") + 1] == "LINEAR"
    assert "-srgb" in command
    assert "-resize" not in command
    assert "-flip" not in command


@pytest.mark.parametrize(("format_name", "expects_srgb"), [
    ("bc7_srgb", True), ("bc3_srgb", True),
    ("bc7_unorm", False), ("rgba8", False),
])
def test_build_texconv_command_sets_srgb_colorspace_explicitly(
        format_name, expects_srgb):
    command = texconv.build_texconv_command(
        "texconv.exe", "bake.png", "out", format_name, 2)

    assert ("-srgb" in command) is expects_srgb


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
        source, output, "rgba8", 3, executable="texconv.exe", runner=runner)

    assert result == str(output / "bake.dds")
    command, kwargs = calls[0]
    assert command[-1] == str(source)
    assert kwargs["shell"] is False
    assert kwargs["check"] is False
    assert kwargs["timeout"] == texconv.DEFAULT_TIMEOUT


def test_encode_png_to_dds_reports_missing_encoder(monkeypatch, tmp_path):
    monkeypatch.setattr(texconv, "texconv_path", lambda: None)

    with pytest.raises(texconv.TexconvUnavailableError):
        texconv.encode_png_to_dds(tmp_path / "bake.png", tmp_path, "rgba8", 1)


def test_real_pinned_texconv_uses_srgb_mip_filtering_when_available(tmp_path):
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
        source, output, "bc7_srgb", 2, executable=executable)
    layout = inspect_dds_layout(candidate)
    assert layout is not None
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

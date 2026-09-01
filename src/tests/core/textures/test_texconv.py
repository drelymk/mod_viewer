"""Deterministic tests for the native DDS encoder adapter."""

from types import SimpleNamespace

import pytest

from core.textures import texconv


def test_build_texconv_command_preserves_format_and_mip_count():
    command = texconv.build_texconv_command(
        "texconv.exe", "bake.png", "out", "bc7_srgb", 5)

    assert command[:2] == ["texconv.exe", "-nologo"]
    assert command[command.index("-f") + 1] == "BC7_UNORM_SRGB"
    assert command[command.index("-m") + 1] == "5"
    assert command[command.index("-if") + 1] == "LINEAR"
    assert "-resize" not in command
    assert "-flip" not in command


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

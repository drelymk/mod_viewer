"""Focused contracts for the shared texture subsystem."""

import io
import struct
import subprocess
import sys
from pathlib import Path

from core import textures


def test_texture_keys_keep_generic_and_field_owned_roles_distinct():
    assert textures.texture_key("foo.dds", "normal_data") == (
        "normal_data::foo.dds")
    assert textures.normalize_texture_key(
        "normal_data::foo.dds", "diffuse") == "normal_data::foo.dds"
    assert textures.texture_key_for_role(
        "normal_data::foo.dds", "diffuse") == "diffuse::foo.dds"


def test_texture_keys_normalize_legacy_paths_and_unknown_roles():
    assert textures.normalize_texture_key(r"Texture\Foo.dds") == (
        "diffuse::Texture/Foo.dds")
    assert textures.normalize_texture_role("not-a-role") == "diffuse"
    assert textures.normalize_texture_transform("not-a-transform") == (
        "passthrough")
    assert textures.normalize_texture_key("") is None


def test_typed_srgb_dds_fallback_rewrites_only_the_decoded_copy():
    data = bytearray(148)
    data[:4] = b"DDS "
    data[84:88] = b"DX10"
    struct.pack_into("<I", data, 128, 78)

    converted = textures._srgb_dds_as_unorm(data)

    assert struct.unpack_from("<I", data, 128)[0] == 78
    assert struct.unpack_from("<I", converted, 128)[0] == 77


def test_typed_srgb_dds_decode_retries_with_unorm_header(tmp_path, monkeypatch):
    path = tmp_path / "typed-srgb.dds"
    data = bytearray(148)
    data[:4] = b"DDS "
    data[84:88] = b"DX10"
    struct.pack_into("<I", data, 128, 78)
    path.write_bytes(data)

    from PIL import Image

    class Decoded:
        size = (1, 1)

        def load(self):
            return None

        def convert(self, mode):
            return Image.new(mode, self.size)

    def open_image(source):
        if source == path:
            raise OSError("typed sRGB unsupported")
        assert isinstance(source, io.BytesIO)
        assert struct.unpack_from("<I", source.getvalue(), 128)[0] == 77
        return Decoded()

    monkeypatch.setattr(Image, "open", open_image)

    image = textures.load_texture_image(path)

    assert image.size == (1, 1)


def test_typed_srgb_retry_does_not_read_failed_non_dds(tmp_path, monkeypatch):
    path = tmp_path / "failed.png"
    path.write_bytes(b"not an image")

    import builtins
    from PIL import Image

    def open_image(source):
        assert source == path
        raise OSError("invalid image")

    real_open = builtins.open

    def unexpected_fallback_open(source, *args, **kwargs):
        if source == path:
            raise AssertionError("non-DDS fallback attempted")
        return real_open(source, *args, **kwargs)

    monkeypatch.setattr(Image, "open", open_image)
    monkeypatch.setattr(builtins, "open", unexpected_fallback_open)

    assert textures.load_texture_image(path) is None


def test_texture_module_does_not_depend_on_mesh_builder():
    root = Path(__file__).resolve().parents[2]
    texture_result = subprocess.run(
        [sys.executable, "-c", (
            "import sys; sys.path.insert(0, 'src'); "
            "import core.textures; "
            "assert 'core.mesh_builder' not in sys.modules"
        )],
        cwd=root, check=False, capture_output=True, text=True)
    resource_result = subprocess.run(
        [sys.executable, "-c", (
            "import sys; sys.path.insert(0, 'src'); "
            "import core.resource_paths; "
            "assert 'core.mesh_builder' not in sys.modules; "
            "assert 'core.textures' not in sys.modules"
        )],
        cwd=root, check=False, capture_output=True, text=True)
    assert texture_result.returncode == 0, texture_result.stderr
    assert resource_result.returncode == 0, resource_result.stderr

"""Tests for conservative, game-independent DDS role evidence."""

import struct

from PIL import Image

from core import dds_classifier
from core.dds import DDSInfo


def _write_rgba_dds(path, pixels, width, height):
    data = bytearray(128)
    data[:4] = b"DDS "
    struct.pack_into("<I", data, 4, 124)
    struct.pack_into("<II", data, 12, height, width)
    struct.pack_into("<I", data, 28, 1)
    struct.pack_into("<I", data, 76, 32)
    struct.pack_into("<I", data, 80, 0x40)
    struct.pack_into("<I", data, 88, 32)
    struct.pack_into(
        "<IIII", data, 92,
        0x000000FF, 0x0000FF00, 0x00FF0000, 0xFF000000)
    data.extend(bytes(pixels))
    path.write_bytes(data)


def test_invalid_dds_is_unknown(tmp_path):
    path = tmp_path / "corrupt-name.dds"
    path.write_bytes(b"not a dds")

    result = dds_classifier.classify_dds(path)

    assert result.role is None
    assert result.texture_class == "unknown"


def test_bc5_is_a_high_confidence_normal_without_decoding(tmp_path):
    path = tmp_path / "arbitrary-file-name.dds"
    data = bytearray(148 + 64)
    data[:4] = b"DDS "
    struct.pack_into("<I", data, 4, 124)
    struct.pack_into("<II", data, 12, 8, 8)
    struct.pack_into("<I", data, 76, 32)
    struct.pack_into("<II", data, 80, 4, int.from_bytes(b"DX10", "little"))
    struct.pack_into("<IIIII", data, 128, 83, 3, 0, 1, 0)
    path.write_bytes(data)

    result = dds_classifier.classify_dds(path)

    assert result == dds_classifier.DDSClassification(
        "normal_map", "packed_normal", "high",
        ("format:bc5_unorm", "two_channel_block_format"))


def test_srgb_color_pixels_are_diffuse_and_filename_is_ignored(
        tmp_path, monkeypatch):
    image = Image.new("RGB", (8, 8))
    image.putdata([
        ((index * 31) % 256, (index * 67) % 256,
         (index * 103) % 256) for index in range(64)])
    info = DDSInfo(8, 8, 1, "bc7_srgb", True, True)
    monkeypatch.setattr(dds_classifier, "inspect_dds", lambda _path: info)
    monkeypatch.setattr(
        dds_classifier, "load_texture_image", lambda _path, **_kwargs: image)

    first = dds_classifier.classify_dds(tmp_path / "first.dds")
    second = dds_classifier.classify_dds(tmp_path / "different-name.dds")

    assert first.role == "diffuse"
    assert first.texture_class == "color"
    assert first.confidence == "high"
    assert second == first


def test_linear_packed_pixels_are_normal(tmp_path, monkeypatch):
    image = Image.new("RGB", (8, 8))
    image.putdata([
        (112 + (index % 8) * 4, 112 + (index // 8) * 4, 0)
        for index in range(64)])
    info = DDSInfo(8, 8, 1, "bc7_unorm", True, True)
    monkeypatch.setattr(dds_classifier, "inspect_dds", lambda _path: info)
    monkeypatch.setattr(
        dds_classifier, "load_texture_image", lambda _path, **_kwargs: image)

    result = dds_classifier.classify_dds(tmp_path / "packed.dds")

    assert result.role == "normal_map"
    assert result.texture_class == "packed_normal"
    assert result.confidence == "high"


def test_flat_and_gently_varying_centered_normals_are_accepted(
        tmp_path, monkeypatch):
    images = [
        Image.new("RGB", (8, 8), (128, 128, 0)),
        Image.new("RGB", (8, 8), (128, 128, 0)),
    ]
    images[1].putdata([
        (120 + index % 9, 122 + (index // 8) * 2, 0)
        for index in range(64)])
    info = DDSInfo(8, 8, 1, "bc7_unorm", True, True)
    monkeypatch.setattr(dds_classifier, "inspect_dds", lambda _path: info)

    for image in images:
        monkeypatch.setattr(
            dds_classifier, "load_texture_image",
            lambda _path, image=image, **_kwargs: image)
        result = dds_classifier.classify_dds(tmp_path / "normal.dds")
        assert result.role == "normal_map"


def test_off_center_packed_data_is_not_promoted_to_normal(
        tmp_path, monkeypatch):
    image = Image.new("RGB", (8, 8), (32, 128, 0))
    info = DDSInfo(8, 8, 1, "bc7_unorm", True, True)
    monkeypatch.setattr(dds_classifier, "inspect_dds", lambda _path: info)
    monkeypatch.setattr(
        dds_classifier, "load_texture_image", lambda _path, **_kwargs: image)

    result = dds_classifier.classify_dds(tmp_path / "packed.dds")

    assert result.role is None
    assert result.texture_class == "packed_data"


def test_effects_masks_and_grayscale_images_are_not_diffuse(
        tmp_path, monkeypatch):
    info = DDSInfo(8, 8, 1, "bc7_srgb", True, True)
    monkeypatch.setattr(dds_classifier, "inspect_dds", lambda _path: info)
    images = [
        Image.new("RGB", (8, 8), (80, 160, 220)),
        Image.new("RGB", (8, 8), (0, 0, 0)),
        Image.new("RGB", (8, 8), (128, 128, 128)),
    ]
    images[1].putpixel((0, 0), (255, 0, 0))
    for index, image in enumerate(images):
        monkeypatch.setattr(
            dds_classifier, "load_texture_image",
            lambda _path, image=image, **_kwargs: image)
        result = dds_classifier.classify_dds(
            tmp_path / f"effect-{index}.dds")
        assert result.role is None


def test_tiny_texture_is_diagnostic_lookup_only(tmp_path, monkeypatch):
    info = DDSInfo(4, 4, 1, "bc7_srgb", True, True)
    monkeypatch.setattr(dds_classifier, "inspect_dds", lambda _path: info)
    monkeypatch.setattr(
        dds_classifier, "load_texture_image",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("tiny textures do not need decoding")))

    result = dds_classifier.classify_dds(tmp_path / "lookup.dds")

    assert result.role is None
    assert result.texture_class == "lookup"

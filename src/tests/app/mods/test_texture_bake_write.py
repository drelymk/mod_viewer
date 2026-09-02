"""Safe DDS color-bake write-path regressions."""

from types import SimpleNamespace
from datetime import datetime
import logging
from pathlib import Path
import struct

import pytest
from PIL import Image

from app.mods import texture_bake
from core.textures.dds import inspect_dds, inspect_dds_layout
from core.textures.uv_coverage import UVCoverage


def _backups(directory, stem):
    return sorted(directory.glob(f"{stem}-??????????????.dds"))


def _rgba8_dds(payload, format_name="rgba8"):
    header = bytearray(128)
    header[:4] = b"DDS "
    header[4:8] = (124).to_bytes(4, "little")
    header[12:16] = (1).to_bytes(4, "little")
    header[16:20] = (2).to_bytes(4, "little")
    header[76:80] = (32).to_bytes(4, "little")
    header[80:84] = (0x41).to_bytes(4, "little")
    header[88:92] = (32).to_bytes(4, "little")
    masks = ((0x000000FF, 0x0000FF00, 0x00FF0000, 0xFF000000)
             if format_name == "rgba8" else
             (0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000))
    for offset, value in zip((92, 96, 100, 104), masks):
        header[offset:offset + 4] = value.to_bytes(4, "little")
    return bytes(header) + bytes(payload)


def _dx10_dds(payload, dxgi_format, width=4, height=4, mip_count=1):
    header = bytearray(148)
    header[:4] = b"DDS "
    struct.pack_into("<I", header, 4, 124)
    struct.pack_into("<II", header, 12, height, width)
    struct.pack_into("<I", header, 28, mip_count)
    struct.pack_into("<I", header, 76, 32)
    struct.pack_into("<II", header, 80, 4, int.from_bytes(b"DX10", "little"))
    struct.pack_into("<IIIII", header, 128, dxgi_format, 3, 0, 1, 0)
    return bytes(header) + bytes(payload)


def _bc7_mode6_block(alpha0, alpha1, indices, rgb0=(40, 60, 80),
                     rgb1=(120, 140, 160)):
    """Build a small valid mode-6 block for deterministic fallback tests."""
    assert len(indices) == 16
    assert 0 <= alpha0 <= 255 and 0 <= alpha1 <= 255
    pbits = (alpha0 & 1, alpha1 & 1)
    bits = 1 << 6

    def put(start, count, value):
        nonlocal bits
        bits = texture_bake._bc7_set_bits(bits, start, count, value)

    for channel in range(3):
        put(7 + channel * 14, 7, rgb0[channel] >> 1)
        put(14 + channel * 14, 7, rgb1[channel] >> 1)
    put(49, 7, alpha0 >> 1)
    put(56, 7, alpha1 >> 1)
    put(63, 1, pbits[0])
    put(64, 1, pbits[1])
    put(65, 3, indices[0])
    for index, value in enumerate(indices[1:], 1):
        put(68 + (index - 1) * 4, 4, value)
    return bits.to_bytes(16, "little")


def _bc7_separate_block(mode, rotation, index_mode, endpoints,
                        first_indices, second_indices):
    """Build a valid single-subset BC7 mode-4 or mode-5 block."""
    assert mode in {4, 5}
    assert rotation in range(4)
    assert len(first_indices) == len(second_indices) == 16
    bits = 1 << mode

    def put(start, count, value):
        nonlocal bits
        bits = texture_bake._bc7_set_bits(bits, start, count, value)

    start = mode + 1
    put(start, 2, rotation)
    start += 2
    if mode == 4:
        put(start, 1, index_mode)
        start += 1
    precisions = (5, 5, 5, 6) if mode == 4 else (7, 7, 7, 8)
    for channel, precision in enumerate(precisions):
        for endpoint in range(2):
            put(start, precision, endpoints[channel][endpoint])
            start += precision

    put(start, 1, first_indices[0])
    start += 1
    for value in first_indices[1:]:
        put(start, 2, value)
        start += 2
    second_precision = 3 if mode == 4 else 2
    put(start, second_precision - 1, second_indices[0])
    start += second_precision - 1
    for value in second_indices[1:]:
        put(start, second_precision, value)
        start += second_precision
    assert start == 128
    return bits.to_bytes(16, "little")


def _bc7_mode7_block(partition, pbits, endpoints, indices):
    """Build a valid BC7 mode-7 block for a fixed partition."""
    assert 0 <= partition < 64
    assert len(pbits) == 4
    assert all(pbit in {0, 1} for pbit in pbits)
    assert len(endpoints) == 4
    assert all(len(endpoint) == 4 for endpoint in endpoints)
    assert all(0 <= value <= 31
               for endpoint in endpoints for value in endpoint)
    assert len(indices) == 16
    assert all(0 <= value <= 3 for value in indices)
    anchor = texture_bake._bc7_codec._PARTITION_2_ANCHORS[partition]
    assert indices[0] < 2 and indices[anchor] < 2
    bits = 1 << 7

    def put(start, count, value):
        nonlocal bits
        bits = texture_bake._bc7_set_bits(bits, start, count, value)

    put(8, 6, partition)
    start = 14
    for channel in range(4):
        for endpoint in range(4):
            put(start, 5, endpoints[endpoint][channel])
            start += 5
    for pbit in pbits:
        put(start, 1, pbit)
        start += 1
    for pixel, value in enumerate(indices):
        width = 1 if pixel in {0, anchor} else 2
        put(start, width, value)
        start += width
    assert start == 128
    return bits.to_bytes(16, "little")


def _prepared(path):
    layout = inspect_dds_layout(path)
    selected_pixels = SimpleNamespace(mask=bytearray([1, 1]))
    selected = {"semantic_key": "Body-1", "tex_key": "diffuse::body.dds"}
    return SimpleNamespace(
        selected_path=str(path), info=layout.info, layout=layout,
        selected_pixels=selected_pixels,
        safe_masks=(bytearray([1, 0]),),
        shared_masks=(bytearray([0, 1]),),
        entries=(selected,), unresolved=(),
    )


def test_bake_replaces_only_safe_units_and_keeps_backup(tmp_path, monkeypatch):
    source = tmp_path / "body.dds"
    original = _rgba8_dds([10, 20, 30, 40, 50, 60, 70, 80])
    source.write_bytes(original)
    prepared = _prepared(source)
    monkeypatch.setattr(texture_bake, "_prepare_texture_bake",
                        lambda *args, **kwargs: prepared)
    monkeypatch.setattr(
        texture_bake, "load_texture_image_full",
        lambda *_args, **_kwargs: Image.frombytes(
            "RGBA", (2, 1), bytes([10, 20, 30, 40, 50, 60, 70, 80])))

    def encode(_png, output, _format, _mips, **_kwargs):
        candidate = Path(output) / "bake.dds"
        candidate.write_bytes(_rgba8_dds(
            [200, 201, 202, 203, 50, 60, 70, 81]))
        return str(candidate)

    monkeypatch.setattr(texture_bake, "encode_png_to_dds", encode)
    result = texture_bake.bake_texture_color(
        SimpleNamespace(mod_dir=str(tmp_path)), {}, {"Body-1"}, "Body-1",
        "diffuse::body.dds", [
            {"semantic_key": "Body-1", "tex_key": "diffuse::body.dds"},
        ], {"hue": 30})

    assert result["status"] == "ok"
    assert result["patched"]["mip0_units"] == 1
    updated = source.read_bytes()
    assert updated[:128] == original[:128]
    assert updated[128:132] == bytes([200, 201, 202, 40])
    assert updated[132:] == original[132:]
    backups = _backups(tmp_path, "body")
    assert len(backups) == 1
    assert backups[0].read_bytes() == original


def test_structural_patch_validation_rejects_unauthorized_unit_change(tmp_path):
    source = tmp_path / "source.dds"
    original = _rgba8_dds([10, 20, 30, 40, 50, 60, 70, 80])
    candidate = _rgba8_dds([200, 201, 202, 203, 50, 60, 70, 81])
    source.write_bytes(original)
    layout = inspect_dds_layout(source)
    candidate_path = tmp_path / "candidate.dds"
    candidate_path.write_bytes(candidate)
    candidate_layout = inspect_dds_layout(candidate_path)
    masks = (bytearray([1, 0]),)
    final = texture_bake._patch_dds_units(
        original, candidate, layout, masks, candidate_layout)

    texture_bake._validate_patched_units(
        original, final, layout, masks, candidate, candidate_layout)
    tampered = bytearray(final)
    tampered[132] ^= 1
    with pytest.raises(texture_bake.TextureBakeAnalysisError) as error:
        texture_bake._validate_patched_units(
            original, bytes(tampered), layout, masks, candidate,
            candidate_layout)
    assert error.value.code == "texture_validation_failed"


def test_bake_aborts_on_stale_source_before_creating_backup(tmp_path, monkeypatch):
    source = tmp_path / "body.dds"
    original = _rgba8_dds([10, 20, 30, 40, 50, 60, 70, 80])
    source.write_bytes(original)
    prepared = _prepared(source)
    monkeypatch.setattr(texture_bake, "_prepare_texture_bake",
                        lambda *args, **kwargs: prepared)
    monkeypatch.setattr(
        texture_bake, "load_texture_image_full",
        lambda *_args, **_kwargs: Image.frombytes(
            "RGBA", (2, 1), bytes([10, 20, 30, 40, 50, 60, 70, 80])))
    def encode(_png, output, _format, _mips, **_kwargs):
        candidate = Path(output) / "bake.dds"
        candidate.write_bytes(_rgba8_dds(
            [200, 201, 202, 203, 50, 60, 70, 81]))
        return str(candidate)

    monkeypatch.setattr(texture_bake, "encode_png_to_dds", encode)

    reads = 0

    # Keep the real reader available without recursively calling the patched
    # function for the candidate path.
    real_read = texture_bake._read_source
    def read(path):
        nonlocal reads
        if str(path) == str(source):
            reads += 1
            return original if reads == 1 else b"changed"
        return real_read(path)
    monkeypatch.setattr(texture_bake, "_read_source", read)

    result = texture_bake.bake_texture_color(
        SimpleNamespace(mod_dir=str(tmp_path)), {}, {"Body-1"}, "Body-1",
        "diffuse::body.dds", [
            {"semantic_key": "Body-1", "tex_key": "diffuse::body.dds"},
        ], {"hue": 30})

    assert result["code"] == "texture_changed_during_bake"
    assert source.read_bytes() == original
    assert not _backups(tmp_path, "body")


def test_patch_copies_only_safe_units_at_each_mip(tmp_path):
    header = bytearray(128)
    header[:4] = b"DDS "
    header[4:8] = (124).to_bytes(4, "little")
    header[12:16] = (4).to_bytes(4, "little")
    header[16:20] = (4).to_bytes(4, "little")
    header[28:32] = (2).to_bytes(4, "little")
    header[76:80] = (32).to_bytes(4, "little")
    header[80:84] = (0x41).to_bytes(4, "little")
    header[88:92] = (32).to_bytes(4, "little")
    for offset, value in zip((92, 96, 100, 104),
                             (0x000000FF, 0x0000FF00,
                              0x00FF0000, 0xFF000000)):
        header[offset:offset + 4] = value.to_bytes(4, "little")
    original = bytes(header) + bytes(range(80))
    candidate = bytes(header) + bytes([100 + value for value in range(80)])
    path = tmp_path / "mipped.dds"
    path.write_bytes(original)
    layout = inspect_dds_layout(path)

    final = texture_bake._patch_dds_units(
        original, candidate, layout,
        (bytearray([1] + [0] * 15), bytearray([0, 0, 1, 0])))

    assert final[128:131] == candidate[128:131]
    assert final[131:132] == original[131:132]
    assert final[132:192] == original[132:192]
    assert final[192:200] == original[192:200]
    assert final[200:203] == candidate[200:203]
    assert final[203:204] == original[203:204]
    assert final[204:208] == original[204:208]


@pytest.mark.parametrize("format_name", ["rgba8", "bgra8"])
def test_patch_preserves_uncompressed_alpha(tmp_path, format_name):
    source = tmp_path / f"{format_name}.dds"
    original = _rgba8_dds(
        [10, 20, 30, 40, 50, 60, 70, 80], format_name)
    candidate = _rgba8_dds(
        [200, 201, 202, 203, 210, 211, 212, 213], format_name)
    source.write_bytes(original)
    layout = inspect_dds_layout(source)

    final = texture_bake._patch_dds_units(
        original, candidate, layout, (bytearray([1, 1]),))

    assert final[128:136] == bytes([200, 201, 202, 40, 210, 211, 212, 80])


def test_patch_preserves_bc3_alpha_sub_block(tmp_path):
    source = tmp_path / "bc3.dds"
    original = _dx10_dds(bytes(range(16)), 77)
    candidate = _dx10_dds(bytes(range(100, 116)), 77)
    source.write_bytes(original)
    layout = inspect_dds_layout(source)

    final = texture_bake._patch_dds_units(
        original, candidate, layout, (bytearray([1]),))

    assert final[148:156] == original[148:156]
    assert final[156:164] == candidate[156:164]


def _bc1_block(color0, color1, selectors=0):
    return struct.pack("<HHI", color0, color1, selectors)


def test_bc1_alpha_compatibility_is_per_block_and_edge_aware(tmp_path):
    source = tmp_path / "bc1-mixed.dds"
    original = _dx10_dds(
        _bc1_block(0, 0) + _bc1_block(0, 0, 0xffffffff),
        71, width=8, height=4)
    candidate = _dx10_dds(
        _bc1_block(0x7bef, 0) + _bc1_block(0xffff, 0),
        71, width=8, height=4)
    source.write_bytes(original)
    candidate_path = tmp_path / "bc1-mixed-candidate.dds"
    candidate_path.write_bytes(candidate)
    layout = inspect_dds_layout(source)
    candidate_layout = inspect_dds_layout(candidate_path)

    compatible, stats = texture_bake._alpha_compatibility_for_units(
        original, layout, layout.mips[0], candidate, candidate_layout,
        candidate_layout.mips[0], bytearray([1, 1]))

    assert list(compatible) == [1, 0]
    assert stats.tested_units == 2
    assert stats.compatible_units == 1
    assert stats.protected_units == 1
    assert stats.changed_pixels == 16
    assert stats.max_delta == 255

    one_pixel_candidate = _dx10_dds(
        _bc1_block(0, 0, 3 << (2 * 5)), 71, width=4, height=4)
    one_pixel_source = _dx10_dds(
        _bc1_block(0, 0), 71, width=4, height=4)
    one_pixel_source_path = tmp_path / "bc1-one-pixel-source.dds"
    one_pixel_path = tmp_path / "bc1-one-pixel-candidate.dds"
    one_pixel_source_path.write_bytes(one_pixel_source)
    one_pixel_path.write_bytes(one_pixel_candidate)
    one_pixel_source_layout = inspect_dds_layout(one_pixel_source_path)
    one_pixel_layout = inspect_dds_layout(one_pixel_path)
    one_pixel_compatible, one_pixel_stats = (
        texture_bake._alpha_compatibility_for_units(
            one_pixel_source, one_pixel_source_layout,
            one_pixel_source_layout.mips[0], one_pixel_candidate,
            one_pixel_layout,
            one_pixel_layout.mips[0], bytearray([1])))
    assert list(one_pixel_compatible) == [0]
    assert one_pixel_stats.changed_pixels == 1

    edge_source = _dx10_dds(_bc1_block(0, 0), 71, width=2, height=2)
    edge_candidate = _dx10_dds(
        _bc1_block(0, 0, 3 << (2 * 15)), 71, width=2, height=2)
    edge_source_path = tmp_path / "bc1-edge.dds"
    edge_candidate_path = tmp_path / "bc1-edge-candidate.dds"
    edge_source_path.write_bytes(edge_source)
    edge_candidate_path.write_bytes(edge_candidate)
    edge_layout = inspect_dds_layout(edge_source_path)
    edge_candidate_layout = inspect_dds_layout(edge_candidate_path)
    edge_compatible, _stats = texture_bake._alpha_compatibility_for_units(
        edge_source, edge_layout, edge_layout.mips[0], edge_candidate,
        edge_candidate_layout, edge_candidate_layout.mips[0], bytearray([1]))
    assert list(edge_compatible) == [1]


def test_validate_mip_candidate_requires_one_matching_authored_mip(tmp_path):
    source = tmp_path / "source.dds"
    source.write_bytes(_dx10_dds(
        _bc1_block(0, 0) * 2 + _bc1_block(0, 0),
        71, width=8, height=4, mip_count=2))
    layout = inspect_dds_layout(source)
    candidate = tmp_path / "candidate.dds"
    candidate.write_bytes(_dx10_dds(
        _bc1_block(0x7bef, 0) * 2, 71, width=8, height=4))

    assert texture_bake._validate_mip_candidate(
        layout, layout.mips[0], candidate, candidate.read_bytes()).info.mip_count == 1

    invalid = tmp_path / "invalid-candidate.dds"
    invalid.write_bytes(_dx10_dds(
        _bc1_block(0x7bef, 0) * 3,
        71, width=8, height=4, mip_count=2))
    with pytest.raises(texture_bake.TextureBakeAnalysisError) as error:
        texture_bake._validate_mip_candidate(
            layout, layout.mips[0], invalid, invalid.read_bytes())
    assert error.value.code == "texconv_output_invalid"


def test_bc7_alpha_compatibility_reports_exact_pixel_deltas(
        tmp_path, monkeypatch):
    source = tmp_path / "bc7-source.dds"
    original = _dx10_dds(bytes([1]) * 16, 98)
    candidate = _dx10_dds(bytes([2]) * 16, 98)
    source.write_bytes(original)
    candidate_path = tmp_path / "bc7-candidate.dds"
    candidate_path.write_bytes(candidate)
    layout = inspect_dds_layout(source)
    candidate_layout = inspect_dds_layout(candidate_path)

    def decode(data, _layout, mip):
        alpha = 128 if data[mip.offset] == 1 else 127
        return Image.new("RGBA", (mip.width, mip.height), (0, 0, 0, alpha))

    monkeypatch.setattr(texture_bake, "_decode_alpha_coupled_mip_rgba", decode)
    compatible, stats = texture_bake._alpha_compatibility_for_units(
        original, layout, layout.mips[0], candidate, candidate_layout,
        candidate_layout.mips[0], bytearray([1]))

    assert list(compatible) == [0]
    assert stats.tested_units == 1
    assert stats.compatible_units == 0
    assert stats.protected_units == 1
    assert stats.changed_pixels == 16
    assert stats.max_delta == 1
    with pytest.raises(texture_bake.TextureBakeAnalysisError) as error:
        texture_bake._validate_patched_alpha(
            original, candidate, layout, (bytearray([1]),))
    assert error.value.code == "texture_validation_failed"


def _coupled_prepared(source, layout, safe_masks, pixel_masks, tex_key):
    selected = {"semantic_key": "Body-1", "tex_key": tex_key}
    return SimpleNamespace(
        selected_path=str(source), info=layout.info, layout=layout,
        selected_pixels=SimpleNamespace(mask=pixel_masks[0].mask),
        selected_pixel_coverages=tuple(pixel_masks),
        safe_masks=tuple(safe_masks),
        shared_masks=tuple(bytearray(len(mask)) for mask in safe_masks),
        entries=(selected,), unresolved=(),
    )


def test_safe_block_atlas_preserves_unselected_pixels_and_measures_rgb_error(
        tmp_path):
    source = tmp_path / "atlas-source.dds"
    source.write_bytes(_dx10_dds(bytes([1]) * 32, 98, width=8, height=4))
    layout = inspect_dds_layout(source)
    image = Image.new("RGBA", (8, 4), (0, 0, 0, 255))
    for y in range(4):
        for x in range(4, 8):
            image.putpixel((x, y), (10, 20, 30, 100))
    pixel_mask = bytearray(8 * 4)
    for y in range(4):
        for x in range(5, 8):
            pixel_mask[y * 8 + x] = 1

    atlas_rgba, safe_indices, atlas_width, atlas_height = (
        texture_bake._build_safe_block_atlas(
            image, layout.mips[0], pixel_mask, bytearray([0, 1]),
            {"brightness": 2}))

    assert safe_indices == (1,)
    assert (atlas_width, atlas_height) == (4, 4)
    target = Image.frombytes("RGBA", (atlas_width, atlas_height), atlas_rgba)
    assert target.getpixel((0, 0)) == (10, 20, 30, 100)
    assert target.getpixel((1, 0)) == (20, 40, 60, 100)

    candidate = Image.new("RGBA", (4, 4), (11, 20, 30, 100))
    candidate_mip = SimpleNamespace(
        width=4, height=4, units_x=1, units_y=1)
    quality = texture_bake._block_candidate_quality(
        target.tobytes(), candidate.tobytes(), layout.mips[0], candidate_mip,
        1, 0, True)
    assert quality.alpha_exact is True
    assert quality.rgb_absolute_error > 0
    assert quality.rgb_max_error == 30


def test_bc1_bake_rejects_protected_mip0_blocks(
        tmp_path, monkeypatch):
    source = tmp_path / "bc1-partial.dds"
    original = _dx10_dds(
        _bc1_block(0, 0) + _bc1_block(0, 0, 0xffffffff),
        71, width=8, height=4)
    candidate = _dx10_dds(
        _bc1_block(0x7bef, 0) + _bc1_block(0xffff, 0),
        71, width=8, height=4)
    source.write_bytes(original)
    layout = inspect_dds_layout(source)
    pixel_masks = (SimpleNamespace(mask=bytearray([1] * 32)),)
    prepared = _coupled_prepared(
        source, layout, (bytearray([1, 1]),), pixel_masks,
        "diffuse::bc1-partial.dds")
    monkeypatch.setattr(texture_bake, "_prepare_texture_bake",
                        lambda *args, **kwargs: prepared)
    monkeypatch.setattr(
        texture_bake, "_decode_alpha_coupled_mip_rgba",
        lambda *_args: Image.new("RGBA", (8, 4), (10, 20, 30, 128)))

    def encode(_png, output, _format, mip_count, **_kwargs):
        assert mip_count == 1
        candidate_path = Path(output) / "bake-mip-0.dds"
        candidate_path.write_bytes(candidate)
        return str(candidate_path)

    monkeypatch.setattr(texture_bake, "encode_png_to_dds", encode)
    result = texture_bake.bake_texture_color(
        SimpleNamespace(mod_dir=str(tmp_path)), {}, {"Body-1"}, "Body-1",
        "diffuse::bc1-partial.dds", [
            {"semantic_key": "Body-1", "tex_key": "diffuse::bc1-partial.dds"},
        ], {"hue": 30})

    updated = source.read_bytes()
    assert result["code"] == "alpha_preservation_unsupported"
    assert updated == original
    assert not _backups(tmp_path, "bc1-partial")


def test_bc7_bake_processes_authored_mips_and_reports_protected_lower_mip(
        tmp_path, monkeypatch):
    source = tmp_path / "bc7-mipped.dds"
    original = _dx10_dds(
        bytes([1]) * 16 + bytes([2]) * 16 + bytes([3]) * 16,
        98, width=4, height=4, mip_count=3)
    source.write_bytes(original)
    layout = inspect_dds_layout(source)
    pixel_masks = tuple(
        SimpleNamespace(mask=bytearray([1] * (mip.width * mip.height)))
        for mip in layout.mips)
    prepared = _coupled_prepared(
        source, layout, (bytearray([1]), bytearray([1]), bytearray([1])),
        pixel_masks, "diffuse::bc7-mipped.dds")
    monkeypatch.setattr(texture_bake, "_prepare_texture_bake",
                        lambda *args, **kwargs: prepared)
    encoded_sizes = []
    adjust_inputs = []
    compression_backends = []
    alpha_weights = []
    bc_flags = []

    def decode(data, _layout, mip):
        marker = data[mip.offset]
        alpha = 127 if data[mip.offset] == 12 else 128
        return Image.new("RGBA", (mip.width, mip.height),
                         (marker, 20, 30, alpha))

    monkeypatch.setattr(texture_bake, "_decode_alpha_coupled_mip_rgba", decode)
    def passthrough_adjustment(rgba, width, height, _adjustment, _mask):
        adjust_inputs.append(Image.frombytes(
            "RGBA", (width, height), rgba).getpixel((0, 0)))
        return rgba

    monkeypatch.setattr(texture_bake, "adjust_rgba_bytes",
                        passthrough_adjustment)

    def encode(png, output, _format, mip_count, **_kwargs):
        assert mip_count == 1
        compression_backends.append(_kwargs.get("compression_backend"))
        alpha_weights.append(_kwargs.get("alpha_weight"))
        bc_flags.append(_kwargs.get("bc_flags"))
        level = int(Path(png).stem.split("-")[2])
        with Image.open(png) as image:
            encoded_sizes.append((level, image.size))
        candidate_path = Path(output) / f"candidate-{level}.dds"
        candidate_path.write_bytes(_dx10_dds(
            bytes([11 + level]) * 16, 98,
            width=4, height=4))
        return str(candidate_path)

    monkeypatch.setattr(texture_bake, "encode_png_to_dds", encode)
    result = texture_bake.bake_texture_color(
        SimpleNamespace(mod_dir=str(tmp_path)), {}, {"Body-1"}, "Body-1",
        "diffuse::bc7-mipped.dds", [
            {"semantic_key": "Body-1", "tex_key": "diffuse::bc7-mipped.dds"},
        ], {"hue": 30})

    updated = source.read_bytes()
    assert result["status"] == "ok"
    assert result["patched"]["mip0_units"] == 1
    assert result["patched"]["alpha_protected_units"] == 1
    assert result["patched"]["alpha_protected_mip0_units"] == 0
    assert result["patched"]["alpha_protected_levels"] == [1]
    assert adjust_inputs == [(1, 20, 30, 128), (2, 20, 30, 128)] + [
        (2, 20, 30, 128)] * 5 + [(3, 20, 30, 128)]
    assert encoded_sizes == [
        (0, (4, 4)), (1, (4, 4)),
        (1, (4, 4)), (1, (4, 4)), (1, (4, 4)),
        (1, (4, 4)), (1, (4, 4)),
        (2, (4, 4)),
    ]
    assert compression_backends == ["auto"] * 8
    assert alpha_weights == [None, None, 2.0, 4.0, 8.0, 16.0, 32.0, None]
    assert bc_flags == [None] * 8
    assert updated[148:164] == bytes([11]) * 16
    assert updated[164:180] == bytes([2]) * 16
    assert updated[180:196] == bytes([13]) * 16
    final_layout = inspect_dds_layout(source)
    assert final_layout.info.format == "bc7_unorm"
    assert final_layout.info.width == 4
    assert final_layout.info.height == 4
    assert final_layout.info.mip_count == 3
    assert _backups(tmp_path, "bc7-mipped")


def test_bc7_retries_choose_lowest_rgb_error_alpha_exact_candidate(
        tmp_path, monkeypatch):
    source = tmp_path / "bc7-quality.dds"
    original = _dx10_dds(bytes([1]) * 16, 98)
    source.write_bytes(original)
    layout = inspect_dds_layout(source)
    prepared = _coupled_prepared(
        source, layout, (bytearray([1]),),
        (SimpleNamespace(mask=bytearray([1] * 16)),),
        "diffuse::bc7-quality.dds")
    encoded = []

    def decode(data, _layout, mip):
        marker = data[mip.offset]
        alpha = 127 if marker == 12 else 128
        return Image.new("RGBA", (mip.width, mip.height),
                         (marker, 20, 30, alpha))

    monkeypatch.setattr(texture_bake, "_decode_alpha_coupled_mip_rgba", decode)
    monkeypatch.setattr(texture_bake, "adjust_rgba_bytes",
                        lambda rgba, *_args: rgba)

    def encode(_png, output, _format, _mips, **kwargs):
        weight = kwargs.get("alpha_weight")
        encoded.append((kwargs.get("compression_backend"),
                        kwargs.get("bc_flags"), weight))
        marker = {
            None: 12,
            2.0: 20,
            4.0: 3,
            8.0: 4,
            16.0: 5,
            32.0: 6,
        }[weight]
        candidate_path = Path(output) / "candidate.dds"
        candidate_path.write_bytes(_dx10_dds(bytes([marker]) * 16, 98))
        return str(candidate_path)

    monkeypatch.setattr(texture_bake, "encode_png_to_dds", encode)
    candidate, writable, protected, stats = (
        texture_bake._encode_alpha_coupled_mips(
            original, prepared, {"hue": 30}, str(tmp_path)))

    assert encoded == [
        ("auto", None, None), ("auto", None, 2.0),
        ("auto", None, 4.0), ("auto", None, 8.0),
        ("auto", None, 16.0), ("auto", None, 32.0),
    ]
    assert candidate[148:164] == bytes([3]) * 16
    assert list(writable[0]) == [1]
    assert list(protected[0]) == [0]
    assert stats[0].compatible_units == 1
    assert stats[0].source_mode6_tested == 0


def test_bc7_mode6_retry_uses_q_flag_and_reports_compatibility(
        tmp_path, monkeypatch):
    source = tmp_path / "bc7-mode6.dds"
    original = _dx10_dds(bytes([0x40]) * 16, 98)
    source.write_bytes(original)
    layout = inspect_dds_layout(source)
    prepared = _coupled_prepared(
        source, layout, (bytearray([1]),),
        (SimpleNamespace(mask=bytearray([1] * 16)),),
        "diffuse::bc7-mode6.dds")
    encoded = []

    def decode(data, _layout, mip):
        marker = data[mip.offset]
        alpha = 127 if marker == 12 else 128
        return Image.new("RGBA", (mip.width, mip.height),
                         (marker, 20, 30, alpha))

    monkeypatch.setattr(texture_bake, "_decode_alpha_coupled_mip_rgba", decode)
    monkeypatch.setattr(texture_bake, "adjust_rgba_bytes",
                        lambda rgba, *_args: rgba)

    def encode(_png, output, _format, _mips, **kwargs):
        encoded.append((kwargs.get("bc_flags"), kwargs.get("alpha_weight")))
        marker = 3 if kwargs.get("bc_flags") == "q" else 12
        candidate_path = Path(output) / "candidate.dds"
        candidate_path.write_bytes(_dx10_dds(bytes([marker]) * 16, 98))
        return str(candidate_path)

    monkeypatch.setattr(texture_bake, "encode_png_to_dds", encode)
    candidate, writable, protected, stats = (
        texture_bake._encode_alpha_coupled_mips(
            original, prepared, {"hue": 30}, str(tmp_path)))

    assert encoded == [
        (None, None), (None, 2.0), (None, 4.0),
        (None, 8.0), (None, 16.0), (None, 32.0),
        ("q", 2.0), ("q", 4.0), ("q", 8.0),
        ("q", 16.0), ("q", 32.0),
    ]
    assert candidate[148:164] == bytes([3]) * 16
    assert list(writable[0]) == [1]
    assert list(protected[0]) == [0]
    assert stats[0].source_mode6_tested == 1
    assert stats[0].source_mode6_compatible == 1


@pytest.mark.parametrize("mode,rotation,index_mode", [
    (4, 0, 0), (4, 1, 1), (4, 2, 0), (4, 3, 1),
    (5, 0, 0), (5, 1, 0), (5, 2, 0), (5, 3, 0),
])
def test_bc7_separate_fallback_preserves_alpha_for_rotations(
        tmp_path, mode, rotation, index_mode):
    first_indices = [0, 1, 2, 3] * 4
    second_indices = ([0, 1, 2, 3, 4, 5, 6, 7] * 2
                      if mode == 4 else [0, 1, 2, 3] * 4)
    block = _bc7_separate_block(
        mode, rotation, index_mode,
        ((4, 24), (8, 28), (12, 31), (3, 57))
        if mode == 4 else
        ((18, 92), (28, 104), (38, 116), (32, 224)),
        first_indices, second_indices)
    source = tmp_path / f"bc7-mode-{mode}-{rotation}.dds"
    original = _dx10_dds(block, 98)
    source.write_bytes(original)
    layout = inspect_dds_layout(source)
    prepared = _coupled_prepared(
        source, layout, (bytearray([1]),),
        (SimpleNamespace(mask=bytearray([1] * 16)),),
        f"diffuse::bc7-mode-{mode}-{rotation}.dds")
    source_image = texture_bake._decode_alpha_coupled_mip_rgba(
        original, layout, layout.mips[0])
    timings = {}
    candidate_blocks, stats = texture_bake._encode_bc7_source_fallback(
        original, prepared, layout.mips[0], source_image,
        bytearray([1] * 16), bytearray([1]),
        {"hue": 120, "saturation": 2}, timings, (mode,), "test_fallback")

    assert set(candidate_blocks) == {0}
    assert stats.compatible_units == 1
    candidate_block = candidate_blocks[0][0]
    source_parameters = texture_bake._bc7_separate_parameters(block)
    candidate_parameters = texture_bake._bc7_separate_parameters(candidate_block)
    assert candidate_parameters[:3] == source_parameters[:3]
    assert candidate_parameters[5:] == source_parameters[5:]
    alpha_internal = 3 if rotation == 0 else rotation - 1
    assert candidate_parameters[3][0][alpha_internal] == (
        source_parameters[3][0][alpha_internal])
    assert candidate_parameters[3][1][alpha_internal] == (
        source_parameters[3][1][alpha_internal])

    candidate = _dx10_dds(candidate_block, 98)
    candidate_path = tmp_path / "candidate.dds"
    candidate_path.write_bytes(candidate)
    candidate_layout = inspect_dds_layout(candidate_path)
    candidate_image = texture_bake._decode_alpha_coupled_mip_rgba(
        candidate, candidate_layout, candidate_layout.mips[0])
    assert (candidate_image.getchannel("A").tobytes()
            == source_image.getchannel("A").tobytes())
    assert candidate_image.convert("RGB").tobytes() != (
        source_image.convert("RGB").tobytes())
    assert timings["test_fallback"] >= 0


def test_bc7_coupled_bake_dispatches_mode4_and_mode5_fallback(
        tmp_path, monkeypatch):
    first_indices = [0, 1, 2, 3] * 4
    mode4 = _bc7_separate_block(
        4, 2, 0, ((4, 24), (8, 28), (12, 31), (3, 57)),
        first_indices, [0, 1, 2, 3, 4, 5, 6, 7] * 2)
    mode5 = _bc7_separate_block(
        5, 3, 0, ((18, 92), (28, 104), (38, 116), (32, 224)),
        first_indices, [0, 1, 2, 3] * 4)
    source = tmp_path / "bc7-separate-dispatch.dds"
    original = _dx10_dds(mode4 + mode5, 98, width=8, height=4)
    source.write_bytes(original)
    layout = inspect_dds_layout(source)
    prepared = _coupled_prepared(
        source, layout, (bytearray([1, 1]),),
        (SimpleNamespace(mask=bytearray([1] * 32)),),
        "diffuse::bc7-separate-dispatch.dds")
    monkeypatch.setattr(
        texture_bake, "_encode_alpha_candidate",
        lambda *_args, **_kwargs: (
            {}, texture_bake.AlphaCompatibilityStats(0, 0, 0, 0, 0)))

    candidate, writable, protected, stats = (
        texture_bake._encode_alpha_coupled_mips(
            original, prepared, {"hue": 120, "saturation": 2},
            str(tmp_path)))

    assert list(writable[0]) == [1, 1]
    assert list(protected[0]) == [0, 0]
    assert stats[0].compatible_units == 2
    assert stats[0].protected_mode_counts == ()
    candidate_layout = inspect_dds_layout(source)
    candidate_image = texture_bake._decode_alpha_coupled_mip_rgba(
        candidate, candidate_layout, candidate_layout.mips[0])
    source_image = texture_bake._decode_alpha_coupled_mip_rgba(
        original, layout, layout.mips[0])
    assert (candidate_image.getchannel("A").tobytes()
            == source_image.getchannel("A").tobytes())


@pytest.mark.parametrize("partition", [1, 13, 14, 15])
def test_bc7_mode7_fallback_preserves_partition_indices_pbits_and_alpha(
        tmp_path, partition):
    anchor = texture_bake._bc7_codec._PARTITION_2_ANCHORS[partition]
    indices = [0, 1, 2, 3] * 4
    indices[0] = 0
    indices[anchor] = 1
    block = _bc7_mode7_block(
        partition, (0, 1, 1, 0),
        ((3, 6, 9, 4), (22, 18, 25, 30),
         (8, 15, 5, 20), (28, 26, 30, 31)), indices)
    source = tmp_path / f"bc7-mode7-{partition}.dds"
    original = _dx10_dds(block, 98)
    source.write_bytes(original)
    layout = inspect_dds_layout(source)
    prepared = _coupled_prepared(
        source, layout, (bytearray([1]),),
        (SimpleNamespace(mask=bytearray([1] * 16)),),
        f"diffuse::bc7-mode7-{partition}.dds")
    source_image = texture_bake._decode_alpha_coupled_mip_rgba(
        original, layout, layout.mips[0])
    target_rgba, _safe_indices, target_width, target_height = (
        texture_bake._build_safe_block_atlas(
            source_image, layout.mips[0], bytearray([1] * 16),
            bytearray([1]), {"hue": 120, "saturation": 2}))
    target_pixels = tuple(
        tuple(target_rgba[index:index + 4])
        for index in range(0, len(target_rgba), 4))
    candidate_block, candidate_pixels = texture_bake._recolor_bc7_mode7_block(
        block, target_pixels, 4, 4)

    source_parameters = texture_bake._bc7_mode7_parameters(block)
    candidate_parameters = texture_bake._bc7_mode7_parameters(candidate_block)
    assert candidate_parameters[0] == source_parameters[0]
    assert candidate_parameters[3:] == source_parameters[3:]
    assert [endpoint[3] for endpoint in candidate_parameters[1]] == [
        endpoint[3] for endpoint in source_parameters[1]]
    assert candidate_pixels != texture_bake._bc7_mode7_decode_block(block)
    assert [pixel[3] for pixel in candidate_pixels] == [
        pixel[3] for pixel in texture_bake._bc7_mode7_decode_block(block)]

    candidate = _dx10_dds(candidate_block, 98)
    candidate_path = tmp_path / "candidate.dds"
    candidate_path.write_bytes(candidate)
    candidate_layout = inspect_dds_layout(candidate_path)
    candidate_image = texture_bake._decode_alpha_coupled_mip_rgba(
        candidate, candidate_layout, candidate_layout.mips[0])
    assert (candidate_image.getchannel("A").tobytes()
            == source_image.getchannel("A").tobytes())

    def rgb_error(pixels):
        return sum((pixels[index][channel] - target_pixels[index][channel]) ** 2
                   for index in range(16) for channel in range(3))

    assert rgb_error(candidate_pixels) < rgb_error(
        texture_bake._bc7_mode7_decode_block(block))


@pytest.mark.parametrize("p0,p1", [(0, 0), (0, 1), (1, 0), (1, 1)])
@pytest.mark.parametrize(
    "targets",
    [(96, 128, 160, 96, 160, 128, 96, 160),
     (160, 96, 128, 160, 96, 128, 160, 96)])
def test_bc7_mode7_channel_fit_matches_exhaustive_reference(
        p0, p1, targets):
    indices = (0, 1, 2, 3, 3, 2, 1, 0)
    expected = min(
        (texture_bake._bc7_codec._mode7_channel_error(
            raw0, raw1, p0, p1, targets, indices), raw0, raw1)
        for raw0 in range(32)
        for raw1 in range(32))

    actual = texture_bake._bc7_codec._fit_mode7_channel(
        targets, indices, p0, p1)

    assert actual == expected[1:]
    assert texture_bake._bc7_codec._mode7_channel_error(
        *actual, p0, p1, targets, indices) == expected[0]


def test_bc7_source_fallback_rejects_worse_rgb_candidate(
        tmp_path, monkeypatch):
    indices = [0, 1, 2, 3] * 4
    indices[texture_bake._bc7_codec._PARTITION_2_ANCHORS[13]] = 1
    block = _bc7_mode7_block(
        13, (0, 1, 1, 0),
        ((3, 6, 9, 4), (22, 18, 25, 30),
         (8, 15, 5, 20), (28, 26, 30, 31)), indices)
    source = tmp_path / "bc7-worse-fallback.dds"
    original = _dx10_dds(block, 98)
    source.write_bytes(original)
    layout = inspect_dds_layout(source)
    prepared = _coupled_prepared(
        source, layout, (bytearray([1]),),
        (SimpleNamespace(mask=bytearray([1] * 16)),),
        "diffuse::bc7-worse-fallback.dds")
    source_image = texture_bake._decode_alpha_coupled_mip_rgba(
        original, layout, layout.mips[0])
    bad_bits = int.from_bytes(block, "little")
    for channel in range(3):
        bad_bits = texture_bake._bc7_set_bits(
            bad_bits, 14 + channel * 20, 10, 0)
    bad_block = bad_bits.to_bytes(16, "little")
    monkeypatch.setattr(
        texture_bake, "_recolor_bc7_mode7_block",
        lambda *_args: (
            bad_block, texture_bake._bc7_mode7_decode_block(bad_block)))

    with pytest.raises(texture_bake.TextureBakeAnalysisError,
                       match="worse RGB result"):
        texture_bake._encode_bc7_source_fallback(
            original, prepared, layout.mips[0], source_image,
            bytearray([1] * 16), bytearray([1]),
            {"hue": 120, "saturation": 2}, {}, (7,),
            "bc7_source_fallback")


def test_bc7_coupled_bake_dispatches_all_alpha_modes(
        tmp_path, monkeypatch):
    first_indices = [0, 1, 2, 3] * 4
    mode4 = _bc7_separate_block(
        4, 2, 0, ((4, 24), (8, 28), (12, 31), (3, 57)),
        first_indices, [0, 1, 2, 3, 4, 5, 6, 7] * 2)
    mode5 = _bc7_separate_block(
        5, 3, 0, ((18, 92), (28, 104), (38, 116), (32, 224)),
        first_indices, [0, 1, 2, 3] * 4)
    mode6 = _bc7_mode6_block(0, 255, [0, 15] * 8)
    mode7_indices = [0, 1, 2, 3] * 4
    mode7_indices[texture_bake._bc7_codec._PARTITION_2_ANCHORS[13]] = 1
    mode7 = _bc7_mode7_block(
        13, (0, 1, 1, 0),
        ((3, 6, 9, 4), (22, 18, 25, 30),
         (8, 15, 5, 20), (28, 26, 30, 31)),
        mode7_indices)
    source = tmp_path / "bc7-all-alpha-modes.dds"
    original = _dx10_dds(
        mode4 + mode5 + mode6 + mode7, 98, width=16, height=4)
    source.write_bytes(original)
    layout = inspect_dds_layout(source)
    prepared = _coupled_prepared(
        source, layout, (bytearray([1, 1, 1, 1]),),
        (SimpleNamespace(mask=bytearray([1] * 64)),),
        "diffuse::bc7-all-alpha-modes.dds")
    monkeypatch.setattr(
        texture_bake, "_encode_alpha_candidate",
        lambda *_args, **_kwargs: (
            {}, texture_bake.AlphaCompatibilityStats(0, 0, 0, 0, 0)))

    candidate, writable, protected, stats = (
        texture_bake._encode_alpha_coupled_mips(
            original, prepared, {"hue": 120, "saturation": 2},
            str(tmp_path)))

    assert list(writable[0]) == [1, 1, 1, 1]
    assert list(protected[0]) == [0, 0, 0, 0]
    assert stats[0].protected_mode_counts == ()
    candidate_layout = inspect_dds_layout(source)
    candidate_image = texture_bake._decode_alpha_coupled_mip_rgba(
        candidate, candidate_layout, candidate_layout.mips[0])
    source_image = texture_bake._decode_alpha_coupled_mip_rgba(
        original, layout, layout.mips[0])
    assert (candidate_image.getchannel("A").tobytes()
            == source_image.getchannel("A").tobytes())


def test_mode6_fallback_preserves_alpha_and_recolors_source_blocks(
        tmp_path, monkeypatch):
    blocks = (
        _bc7_mode6_block(0, 255, [0, 15] * 8),
        _bc7_mode6_block(128, 128, [0] * 16, rgb0=(50, 70, 90),
                          rgb1=(170, 190, 210)),
        _bc7_mode6_block(254, 254, [15] * 16, rgb0=(70, 90, 110),
                          rgb1=(190, 210, 230)),
        _bc7_mode6_block(255, 255, [15] * 16, rgb0=(90, 110, 130),
                          rgb1=(210, 230, 250)),
    )
    source = tmp_path / "bc7-mode6-fallback.dds"
    original = _dx10_dds(b"".join(blocks), 98, width=8, height=8)
    source.write_bytes(original)
    layout = inspect_dds_layout(source)
    prepared = _coupled_prepared(
        source, layout, (bytearray([1, 1, 1, 1]),),
        (SimpleNamespace(mask=bytearray([1] * 64)),),
        "diffuse::bc7-mode6-fallback.dds")
    source_image = texture_bake._decode_alpha_coupled_mip_rgba(
        original, layout, layout.mips[0])
    assert {0, 128, 254, 255} <= set(
        source_image.getchannel("A").tobytes())
    timings = {}
    candidate_blocks, stats = texture_bake._encode_mode6_fallback(
        original, prepared, layout.mips[0], source_image,
        bytearray([1] * 64), bytearray([1, 1, 1, 1]),
        {"hue": 120, "saturation": 2}, timings)

    assert set(candidate_blocks) == {0, 1, 2, 3}
    assert stats.compatible_units == 4
    assert stats.protected_units == 0
    assert timings["mode6_fallback"] >= 0

    candidate_payload = b"".join(
        candidate_blocks[index][0] for index in range(4))
    candidate = _dx10_dds(candidate_payload, 98, width=8, height=8)
    candidate_path = tmp_path / "bc7-mode6-fallback-candidate.dds"
    candidate_path.write_bytes(candidate)
    candidate_layout = inspect_dds_layout(candidate_path)
    candidate_image = texture_bake._decode_alpha_coupled_mip_rgba(
        candidate, candidate_layout, candidate_layout.mips[0])
    assert (candidate_image.getchannel("A").tobytes()
            == source_image.getchannel("A").tobytes())
    assert (candidate_image.convert("RGB").tobytes()
            != source_image.convert("RGB").tobytes())
    for index, block in enumerate(blocks):
        source_pbits, source_endpoints, source_indices = (
            texture_bake._bc7_mode6_parameters(block))
        candidate_pbits, candidate_endpoints, candidate_indices = (
            texture_bake._bc7_mode6_parameters(candidate_blocks[index][0]))
        assert candidate_pbits == source_pbits
        assert candidate_indices == source_indices
        assert [endpoint[3] for endpoint in candidate_endpoints] == [
            endpoint[3] for endpoint in source_endpoints]
        assert ([endpoint[:3] for endpoint in candidate_endpoints]
                != [endpoint[:3] for endpoint in source_endpoints])
    target_rgba, _safe_indices, target_width, target_height = (
        texture_bake._build_safe_block_atlas(
            source_image, layout.mips[0], bytearray([1] * 64),
            bytearray([1, 1, 1, 1]), {"hue": 120, "saturation": 2}))
    target_image = Image.frombytes(
        "RGBA", (target_width, target_height), target_rgba)

    def rgb_error(left, right):
        return sum((left[index] - right[index]) ** 2
                   for index in range(len(left)) if index % 4 != 3)

    assert rgb_error(candidate_image.tobytes(), target_image.tobytes()) < (
        rgb_error(source_image.tobytes(), target_image.tobytes()))


def test_bc7_bake_uses_mode6_fallback_before_mip0_abort(
        tmp_path, monkeypatch):
    source_block = _bc7_mode6_block(0, 255, [0, 15] * 8)
    rejected_block = _bc7_mode6_block(1, 254, [0, 15] * 8)
    source = tmp_path / "bc7-mode6-bake.dds"
    original = _dx10_dds(source_block, 98)
    source.write_bytes(original)
    layout = inspect_dds_layout(source)
    prepared = _coupled_prepared(
        source, layout, (bytearray([1]),),
        (SimpleNamespace(mask=bytearray([1] * 16)),),
        "diffuse::bc7-mode6-bake.dds")
    monkeypatch.setattr(texture_bake, "_prepare_texture_bake",
                        lambda *args, **kwargs: prepared)
    encoded = []

    def encode(_png, output, _format, _mips, **kwargs):
        encoded.append((kwargs.get("bc_flags"), kwargs.get("alpha_weight")))
        candidate_path = Path(output) / "candidate.dds"
        candidate_path.write_bytes(_dx10_dds(rejected_block, 98))
        return str(candidate_path)

    monkeypatch.setattr(texture_bake, "encode_png_to_dds", encode)
    result = texture_bake.bake_texture_color(
        SimpleNamespace(mod_dir=str(tmp_path)), {}, {"Body-1"}, "Body-1",
        "diffuse::bc7-mode6-bake.dds", [
            {"semantic_key": "Body-1", "tex_key": "diffuse::bc7-mode6-bake.dds"},
        ], {"hue": 30})

    updated = source.read_bytes()
    assert encoded == [
        (None, None), (None, 2.0), (None, 4.0),
        (None, 8.0), (None, 16.0), (None, 32.0),
        ("q", 2.0), ("q", 4.0), ("q", 8.0),
        ("q", 16.0), ("q", 32.0),
    ]
    assert result["status"] == "ok"
    assert result["patched"]["mip0_units"] == 1
    assert result["patched"]["alpha_protected_mip0_units"] == 0
    assert updated[148:164] != source_block
    final_layout = inspect_dds_layout(source)
    final_image = texture_bake._decode_alpha_coupled_mip_rgba(
        updated, final_layout, final_layout.mips[0])
    source_image = texture_bake._decode_alpha_coupled_mip_rgba(
        original, layout, layout.mips[0])
    assert (final_image.getchannel("A").tobytes()
            == source_image.getchannel("A").tobytes())


def test_bc7_mode_mask_identifies_unary_prefix_mode():
    assert texture_bake._bc7_block_mode(bytes([0x40]) + bytes(15)) == 6
    assert texture_bake._bc7_block_mode(bytes([0x02]) + bytes(15)) == 1
    with pytest.raises(texture_bake.TextureBakeAnalysisError) as error:
        texture_bake._bc7_block_mode(bytes(16))
    assert error.value.code == "texture_validation_failed"


def test_bc7_bake_with_no_compatible_mip0_does_not_write_or_backup(
        tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger=texture_bake.__name__)
    source = tmp_path / "bc7-unsupported.dds"
    original = _dx10_dds(bytes([1]) * 16, 98)
    source.write_bytes(original)
    layout = inspect_dds_layout(source)
    pixel_masks = (SimpleNamespace(mask=bytearray([1] * 16)),)
    prepared = _coupled_prepared(
        source, layout, (bytearray([1]),), pixel_masks,
        "diffuse::bc7-unsupported.dds")
    monkeypatch.setattr(texture_bake, "_prepare_texture_bake",
                        lambda *args, **kwargs: prepared)
    monkeypatch.setattr(
        texture_bake, "_decode_alpha_coupled_mip_rgba",
        lambda data, _layout, mip: Image.new(
            "RGBA", (mip.width, mip.height), (10, 20, 30,
                                                128 if data[mip.offset] == 1
                                                else 127)))
    called = []

    def encode(_png, output, _format, _mips, **_kwargs):
        called.append(True)
        candidate_path = Path(output) / "candidate.dds"
        candidate_path.write_bytes(_dx10_dds(bytes([2]) * 16, 98))
        return str(candidate_path)

    monkeypatch.setattr(texture_bake, "encode_png_to_dds", encode)
    result = texture_bake.bake_texture_color(
        SimpleNamespace(mod_dir=str(tmp_path)), {}, {"Body-1"}, "Body-1",
        "diffuse::bc7-unsupported.dds", [
            {"semantic_key": "Body-1", "tex_key": "diffuse::bc7-unsupported.dds"},
        ], {"hue": 30})

    assert result["code"] == "alpha_preservation_unsupported"
    assert "any unique color blocks" in result["error"]
    assert result["details"] == {
        "mip": 0,
        "unresolved_units": 1,
        "bc7_modes": {"0": 1},
    }
    assert "protected_mode_counts': {0: 1}" in caplog.text
    assert called == [True] * 6
    assert source.read_bytes() == original
    assert not _backups(tmp_path, "bc7-unsupported")


def test_bake_reports_committed_state_when_replace_raises_after_replacing(
        tmp_path, monkeypatch):
    source = tmp_path / "replace-race.dds"
    original = _rgba8_dds([10, 20, 30, 40, 50, 60, 70, 80])
    source.write_bytes(original)
    prepared = _prepared(source)
    monkeypatch.setattr(texture_bake, "_prepare_texture_bake",
                        lambda *args, **kwargs: prepared)
    monkeypatch.setattr(
        texture_bake, "load_texture_image_full",
        lambda *_args, **_kwargs: Image.frombytes(
            "RGBA", (2, 1), bytes([10, 20, 30, 40, 50, 60, 70, 80])))

    def encode(_png, output, _format, _mips, **_kwargs):
        candidate = Path(output) / "bake.dds"
        candidate.write_bytes(_rgba8_dds(
            [200, 201, 202, 203, 50, 60, 70, 81]))
        return str(candidate)

    monkeypatch.setattr(texture_bake, "encode_png_to_dds", encode)
    real_replace = texture_bake.os.replace

    def replace_then_raise(source_path, target_path):
        real_replace(source_path, target_path)
        raise OSError("reported after replacement")

    monkeypatch.setattr(texture_bake.os, "replace", replace_then_raise)

    result = texture_bake.bake_texture_color(
        SimpleNamespace(mod_dir=str(tmp_path)), {}, {"Body-1"}, "Body-1",
        "diffuse::body.dds", [
            {"semantic_key": "Body-1", "tex_key": "diffuse::body.dds"},
        ], {"hue": 30})

    assert result["status"] == "ok"
    assert source.read_bytes() != original


def test_lower_mip_collision_makes_only_that_level_shared(tmp_path, monkeypatch):
    header = bytearray(148)
    header[:4] = b"DDS "
    header[4:8] = (124).to_bytes(4, "little")
    header[12:16] = (8).to_bytes(4, "little")
    header[16:20] = (8).to_bytes(4, "little")
    header[28:32] = (2).to_bytes(4, "little")
    header[76:80] = (32).to_bytes(4, "little")
    header[80:84] = (4).to_bytes(4, "little")
    header[84:88] = b"DX10"
    header[128:148] = (
        (71).to_bytes(4, "little") + (3).to_bytes(4, "little")
        + (0).to_bytes(4, "little") + (1).to_bytes(4, "little")
        + (0).to_bytes(4, "little"))
    source = tmp_path / "lower-mip.dds"
    source.write_bytes(bytes(header) + bytes(40))
    info = inspect_dds(source)
    selected_draw = (SimpleNamespace(label="selected"), {})
    other_draw = (SimpleNamespace(label="other"), {})
    entries = (
        {"semantic_key": "selected", "tex_key": "diffuse::lower-mip.dds"},
        {"semantic_key": "other", "tex_key": "diffuse::lower-mip.dds"},
    )
    parsed = SimpleNamespace(game=SimpleNamespace(game="unknown"))
    monkeypatch.setattr(
        texture_bake, "_resolve_request",
        lambda *args: (entries, str(source), info, parsed, selected_draw,
                       (("other", other_draw),)),
    )
    packed = {
        "selected": SimpleNamespace(semantic="selected"),
        "other": SimpleNamespace(semantic="other"),
    }
    prepare_calls = []
    def prepare(draw, *_args):
        prepare_calls.append(draw.label)
        return packed[draw.label]
    monkeypatch.setattr(
        texture_bake, "_prepare_uv_geometry", prepare,
    )

    def coverage(geometry, width, height, unit_width, unit_height):
        if geometry.semantic == "selected" and unit_width == 1:
            return UVCoverage(8, 8, bytearray([1] + [0] * 63), 1,
                              (0, 0, 0, 0), 1, 0)
        if geometry.semantic == "selected" and width == 8:
            return UVCoverage(2, 2, bytearray([1, 0, 0, 0]), 1,
                              (0, 0, 0, 0), 1, 0)
        if geometry.semantic == "other" and width == 8:
            return UVCoverage(2, 2, bytearray([0, 1, 0, 0]), 1,
                              (1, 0, 1, 0), 1, 0)
        return UVCoverage(1, 1, bytearray([1]), 1, (0, 0, 0, 0), 1, 0)

    monkeypatch.setattr(texture_bake, "_rasterize_geometry", coverage)
    # The collision fixture supplies edit-unit masks directly; the dedicated
    # consumer-padding behavior is covered by the pixel-mask tests.
    monkeypatch.setattr(
        texture_bake, "_protected_consumer_coverages",
        lambda geometry, layout, _info: tuple(
            coverage(geometry, mip.width, mip.height, 4, 4)
            for mip in layout.mips),
    )
    prepared = texture_bake._prepare_texture_bake(
        SimpleNamespace(mod_dir=str(tmp_path)), {}, {"selected", "other"},
        "selected", "diffuse::lower-mip.dds", list(entries))

    assert [sum(mask) for mask in prepared.shared_masks] == [0, 1]
    assert [sum(mask) for mask in prepared.safe_masks] == [1, 0]
    assert prepared.safety == "shared"
    assert prepare_calls == ["selected", "other"]


def test_backup_names_never_overwrite_previous_backup(tmp_path, monkeypatch):
    source = tmp_path / "body.dds"
    source.write_bytes(b"original")
    monkeypatch.setattr(
        texture_bake, "datetime",
        SimpleNamespace(now=lambda: datetime(2026, 9, 1, 15, 22, 30)))

    first = texture_bake._write_backup(str(source), b"first")
    second = texture_bake._write_backup(str(source), b"second")

    assert Path(first).name.endswith(".dds")
    assert Path(second).name.endswith(".dds")
    assert Path(first).stem.startswith("body-")
    assert Path(second).stem.startswith("body-")
    assert Path(first).name == "body-20260901152230.dds"
    assert Path(second).name == "body-20260901152231.dds"
    assert first != second
    assert Path(first).read_bytes() == b"first"
    assert Path(second).read_bytes() == b"second"


@pytest.mark.parametrize(("texture_key", "code"), [
    ("diffuse::asset/root/body.dds", "asset_texture_read_only"),
    ("diffuse::../outside.dds", "texture_not_found"),
])
def test_bake_rejects_non_mod_texture_paths_before_encoder(
        tmp_path, monkeypatch, texture_key, code):
    called = []
    monkeypatch.setattr(
        texture_bake, "encode_png_to_dds",
        lambda *args, **kwargs: called.append((args, kwargs)))

    result = texture_bake.bake_texture_color(
        SimpleNamespace(mod_dir=str(tmp_path)), {}, {"Body-1"}, "Body-1",
        texture_key, [{"semantic_key": "Body-1", "tex_key": texture_key}],
        {"hue": 30})

    assert result["code"] == "stale_mesh_state"
    assert called == []


def test_bake_refuses_when_no_top_level_unit_is_unique(tmp_path, monkeypatch):
    source = tmp_path / "body.dds"
    source.write_bytes(_rgba8_dds([10, 20, 30, 40, 50, 60, 70, 80]))
    prepared = _prepared(source)
    prepared.safe_masks = (bytearray([0, 0]),)
    monkeypatch.setattr(texture_bake, "_prepare_texture_bake",
                        lambda *args, **kwargs: prepared)
    called = []
    monkeypatch.setattr(
        texture_bake, "load_texture_image_full",
        lambda *args: called.append(args))
    monkeypatch.setattr(
        texture_bake, "encode_png_to_dds",
        lambda *args, **kwargs: called.append((args, kwargs)))

    result = texture_bake.bake_texture_color(
        SimpleNamespace(mod_dir=str(tmp_path)), {}, {"Body-1"}, "Body-1",
        "diffuse::body.dds", [
            {"semantic_key": "Body-1", "tex_key": "diffuse::body.dds"},
        ], {"hue": 30})

    assert result["code"] == "no_unique_texture_coverage"
    assert called == []

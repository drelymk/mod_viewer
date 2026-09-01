"""Safe DDS color-bake write-path regressions."""

from types import SimpleNamespace
from datetime import datetime
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


def test_bc1_bake_patches_compatible_blocks_and_protects_the_rest(
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
    assert result["status"] == "ok"
    assert result["patched"]["mip0_units"] == 1
    assert result["patched"]["total_units"] == 2
    assert result["patched"]["alpha_protected_units"] == 1
    assert result["patched"]["alpha_protected_mip0_units"] == 1
    assert result["patched"]["alpha_protected_levels"] == [0]
    assert updated[148:156] == candidate[148:156]
    assert updated[156:164] == original[156:164]
    assert _backups(tmp_path, "bc1-partial")


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
        level = int(Path(png).stem.rsplit("-", 1)[-1])
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
    assert adjust_inputs == [(1, 20, 30, 128), (2, 20, 30, 128),
                             (3, 20, 30, 128)]
    assert encoded_sizes == [(0, (4, 4)), (1, (4, 4)), (2, (4, 4))]
    assert compression_backends == ["auto", "auto", "auto"]
    assert updated[148:164] == bytes([11]) * 16
    assert updated[164:180] == bytes([2]) * 16
    assert updated[180:196] == bytes([13]) * 16
    final_layout = inspect_dds_layout(source)
    assert final_layout.info.format == "bc7_unorm"
    assert final_layout.info.width == 4
    assert final_layout.info.height == 4
    assert final_layout.info.mip_count == 3
    assert _backups(tmp_path, "bc7-mipped")


def test_bc7_bake_with_no_compatible_mip0_does_not_write_or_backup(
        tmp_path, monkeypatch):
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
    assert called == [True]
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

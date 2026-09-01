"""Safe DDS color-bake write-path regressions."""

from types import SimpleNamespace
from pathlib import Path
import struct

import pytest
from PIL import Image

from app.mods import texture_bake
from core.textures.dds import inspect_dds, inspect_dds_layout
from core.textures.uv_coverage import UVCoverage


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
    assert (tmp_path / "body.dds.modviewer.bak").read_bytes() == original


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
    assert not (tmp_path / "body.dds.modviewer.bak").exists()


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


def test_bc1_alpha_validation_is_per_safe_block_and_per_mip(tmp_path):
    source = tmp_path / "bc1-mipped.dds"
    source.write_bytes(_dx10_dds(
        _bc1_block(0xffff, 0) + _bc1_block(0, 0, 0xffffffff)
        + _bc1_block(0, 0, 0xffffffff),
        71, width=8, height=4, mip_count=2))
    layout = inspect_dds_layout(source)

    # The unrelated transparent block does not prevent changing the opaque
    # block that coverage marked safe.
    texture_bake._validate_alpha_preservation(
        source.read_bytes(), layout, (bytearray([1, 0]), bytearray([0])))

    with pytest.raises(texture_bake.TextureBakeAnalysisError) as error:
        texture_bake._validate_alpha_preservation(
            source.read_bytes(), layout, (bytearray([1, 0]), bytearray([1])))
    assert error.value.code == "alpha_preservation_unsupported"


def test_bc1_alpha_validation_rejects_transparent_safe_block(tmp_path):
    source = tmp_path / "bc1-transparent-safe.dds"
    source.write_bytes(_dx10_dds(
        _bc1_block(0, 0, 0xffffffff), 71, width=4, height=4))
    layout = inspect_dds_layout(source)

    with pytest.raises(texture_bake.TextureBakeAnalysisError) as error:
        texture_bake._validate_alpha_preservation(
            source.read_bytes(), layout, (bytearray([1]),))
    assert error.value.code == "alpha_preservation_unsupported"


def test_bc7_alpha_validation_checks_only_safe_blocks_at_each_mip(
        tmp_path, monkeypatch):
    source = tmp_path / "bc7-mipped.dds"
    source.write_bytes(_dx10_dds(bytes(48), 98, width=8, height=4,
                                 mip_count=2))
    layout = inspect_dds_layout(source)

    def decode(_source, _layout, mip):
        image = Image.new("RGBA", (mip.width, mip.height), (0, 0, 0, 255))
        if mip.level == 0:
            for y in range(mip.height):
                for x in range(4, mip.width):
                    image.putpixel((x, y), (0, 0, 0, 0))
        elif mip.level == 1:
            image.putpixel((0, 0), (0, 0, 0, 0))
        return image

    monkeypatch.setattr(texture_bake, "_decode_dds_mip_rgba", decode)
    # A transparent block outside the safe mask is allowed.
    texture_bake._validate_alpha_preservation(
        source.read_bytes(), layout, (bytearray([1, 0]), bytearray([0])))

    with pytest.raises(texture_bake.TextureBakeAnalysisError) as error:
        texture_bake._validate_alpha_preservation(
            source.read_bytes(), layout, (bytearray([1, 0]), bytearray([1])))
    assert error.value.code == "alpha_preservation_unsupported"


def test_bc7_transparency_is_rejected_before_backup_or_encoder(
        tmp_path, monkeypatch):
    source = tmp_path / "transparent-bc7.dds"
    source.write_bytes(_dx10_dds(bytes(16), 98))
    layout = inspect_dds_layout(source)
    prepared = SimpleNamespace(
        selected_path=str(source), info=layout.info, layout=layout,
        selected_pixels=SimpleNamespace(mask=bytearray(16)),
        safe_masks=(bytearray([1]),), shared_masks=(bytearray([0]),),
        entries=({"semantic_key": "Body-1", "tex_key": "diffuse::transparent-bc7.dds"},),
        unresolved=(),
    )
    prepared.selected_pixels.mask[0] = 1
    monkeypatch.setattr(texture_bake, "_prepare_texture_bake",
                        lambda *args, **kwargs: prepared)
    monkeypatch.setattr(
        texture_bake, "load_texture_image_full",
        lambda *_args, **_kwargs: Image.new("RGBA", (4, 4), (10, 20, 30, 128)))
    monkeypatch.setattr(
        texture_bake, "_decode_dds_mip_rgba",
        lambda *_args: Image.new("RGBA", (4, 4), (10, 20, 30, 128)))
    called = []
    monkeypatch.setattr(
        texture_bake, "encode_png_to_dds",
        lambda *args, **kwargs: called.append((args, kwargs)))

    result = texture_bake.bake_texture_color(
        SimpleNamespace(mod_dir=str(tmp_path)), {}, {"Body-1"}, "Body-1",
        "diffuse::transparent-bc7.dds", [
            {"semantic_key": "Body-1", "tex_key": "diffuse::transparent-bc7.dds"},
        ], {"hue": 30})

    assert result["code"] == "alpha_preservation_unsupported"
    assert called == []
    assert not (tmp_path / "transparent-bc7.dds.modviewer.bak").exists()


def test_opaque_bc7_can_be_baked(tmp_path, monkeypatch):
    source = tmp_path / "opaque-bc7.dds"
    original = _dx10_dds(bytes(16), 98)
    source.write_bytes(original)
    layout = inspect_dds_layout(source)
    prepared = SimpleNamespace(
        selected_path=str(source), info=layout.info, layout=layout,
        selected_pixels=SimpleNamespace(mask=bytearray([1] + [0] * 15)),
        safe_masks=(bytearray([1]),), shared_masks=(bytearray([0]),),
        entries=({"semantic_key": "Body-1", "tex_key": "diffuse::opaque-bc7.dds"},),
        unresolved=(),
    )
    monkeypatch.setattr(texture_bake, "_prepare_texture_bake",
                        lambda *args, **kwargs: prepared)
    monkeypatch.setattr(
        texture_bake, "load_texture_image_full",
        lambda *_args, **_kwargs: Image.new("RGBA", (4, 4), (10, 20, 30, 255)))
    monkeypatch.setattr(
        texture_bake, "_decode_dds_mip_rgba",
        lambda *_args: Image.new("RGBA", (4, 4), (10, 20, 30, 255)))

    def encode(_png, output, _format, _mips, **_kwargs):
        candidate = Path(output) / "bake.dds"
        candidate.write_bytes(_dx10_dds(bytes(range(100, 116)), 98))
        return str(candidate)

    monkeypatch.setattr(texture_bake, "encode_png_to_dds", encode)

    result = texture_bake.bake_texture_color(
        SimpleNamespace(mod_dir=str(tmp_path)), {}, {"Body-1"}, "Body-1",
        "diffuse::opaque-bc7.dds", [
            {"semantic_key": "Body-1", "tex_key": "diffuse::opaque-bc7.dds"},
        ], {"hue": 30})

    assert result["status"] == "ok"


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
    prepared = texture_bake._prepare_texture_bake(
        SimpleNamespace(mod_dir=str(tmp_path)), {}, {"selected", "other"},
        "selected", "diffuse::lower-mip.dds", list(entries))

    assert [sum(mask) for mask in prepared.shared_masks] == [0, 1]
    assert [sum(mask) for mask in prepared.safe_masks] == [1, 0]
    assert prepared.safety == "shared"
    assert prepare_calls == ["selected", "other"]


def test_backup_names_never_overwrite_previous_backup(tmp_path):
    source = tmp_path / "body.dds"
    source.write_bytes(b"original")

    first = texture_bake._write_backup(str(source), b"first")
    second = texture_bake._write_backup(str(source), b"second")

    assert first.endswith("body.dds.modviewer.bak")
    assert second.endswith("body.dds.modviewer.2.bak")
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

    assert result["code"] == code
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

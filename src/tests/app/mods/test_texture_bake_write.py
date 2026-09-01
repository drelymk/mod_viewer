"""Safe DDS color-bake write-path regressions."""

from types import SimpleNamespace
from pathlib import Path

import pytest
from PIL import Image

from app.mods import texture_bake
from core.textures.dds import inspect_dds, inspect_dds_layout
from core.textures.uv_coverage import UVCoverage


def _rgba8_dds(payload):
    header = bytearray(128)
    header[:4] = b"DDS "
    header[4:8] = (124).to_bytes(4, "little")
    header[12:16] = (1).to_bytes(4, "little")
    header[16:20] = (2).to_bytes(4, "little")
    header[76:80] = (32).to_bytes(4, "little")
    header[80:84] = (0x41).to_bytes(4, "little")
    header[88:92] = (32).to_bytes(4, "little")
    for offset, value in zip((92, 96, 100, 104),
                             (0x000000FF, 0x0000FF00,
                              0x00FF0000, 0xFF000000)):
        header[offset:offset + 4] = value.to_bytes(4, "little")
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

    def encode(_png, output, _format, _mips):
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
    assert updated[128:132] == bytes([200, 201, 202, 203])
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
    def encode(_png, output, _format, _mips):
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

    assert final[128:132] == candidate[128:132]
    assert final[132:192] == original[132:192]
    assert final[192:200] == original[192:200]
    assert final[200:204] == candidate[200:204]
    assert final[204:208] == original[204:208]


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
        texture_bake, "encode_png_to_dds", lambda *args: called.append(args))

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
        texture_bake, "encode_png_to_dds", lambda *args: called.append(args))

    result = texture_bake.bake_texture_color(
        SimpleNamespace(mod_dir=str(tmp_path)), {}, {"Body-1"}, "Body-1",
        "diffuse::body.dds", [
            {"semantic_key": "Body-1", "tex_key": "diffuse::body.dds"},
        ], {"hue": 30})

    assert result["code"] == "no_unique_texture_coverage"
    assert called == []

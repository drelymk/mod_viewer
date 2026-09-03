"""Atomic BC7 Save to Texture regressions."""

import struct
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.mods import texture_save
from core.geometry.draw_call import DrawCall
from core.textures import bc7
from core.textures.color_adjustment import (
    apply_prepared_color_adjustment, prepare_color_adjustment,
)
from core.textures.dds import inspect_dds_layout
from core.textures.uv_coverage import UVCoverage


def _dx10_dds(payload, dxgi_format=98, width=4, height=4, mip_count=1):
    header = bytearray(148)
    header[:4] = b"DDS "
    struct.pack_into("<I", header, 4, 124)
    struct.pack_into("<II", header, 12, height, width)
    struct.pack_into("<I", header, 28, mip_count)
    struct.pack_into("<I", header, 76, 32)
    struct.pack_into("<II", header, 80, 4, int.from_bytes(b"DX10", "little"))
    struct.pack_into("<IIIII", header, 128, dxgi_format, 3, 0, 1, 0)
    return bytes(header) + bytes(payload)


def _mode6_block(endpoints=((20, 110), (40, 140), (60, 170))):
    bits = 1 << 6
    for channel, (low, high) in enumerate(endpoints):
        bits = bc7.set_bits(bits, 7 + channel * 14, 7, low >> 1)
        bits = bc7.set_bits(bits, 14 + channel * 14, 7, high >> 1)
    bits = bc7.set_bits(bits, 49, 7, 0)
    bits = bc7.set_bits(bits, 56, 7, 127)
    bits = bc7.set_bits(bits, 63, 1, 0)
    bits = bc7.set_bits(bits, 64, 1, 1)
    indices = [0, 1, 2, 3] * 4
    bits = bc7.set_bits(bits, 65, 3, indices[0])
    for pixel, index in enumerate(indices[1:], 1):
        bits = bc7.set_bits(bits, 68 + (pixel - 1) * 4, 4, index)
    return bits.to_bytes(16, "little")


def _role_keys(diffuse="diffuse::body.dds"):
    return {
        "diffuse": diffuse,
        "normal_map": None,
        "normal_data": None,
        "light_map": None,
        "material_map": None,
        "emission_map": None,
    }


def _write_prepared_save(path):
    layout = inspect_dds_layout(path)
    return SimpleNamespace(
        selected_path=str(path),
        info=layout.info,
        layout=layout,
        entries=({
            "semantic_key": "Anchor", "texture_keys": _role_keys(),
        },),
        targets=(SimpleNamespace(
            semantic_key="Target", metadata_key="Target::one"),),
        mip0_claims=bytearray([1] * 16),
        intent_adjustments=(None, prepare_color_adjustment({"hue": 30})),
        mip0_affected_blocks=(0,),
    )


def _write_stats():
    return texture_save.BC7SaveStats(
        touched_blocks=1, improved_blocks=1, unchanged_blocks=0,
        source_rgb_error=10, final_rgb_error=5, modes={6: 1})


def test_save_is_bc7_only_and_returns_a_clean_public_result(tmp_path, monkeypatch):
    source = tmp_path / "body.dds"
    original = _dx10_dds(bytes(16))
    source.write_bytes(original)
    layout = inspect_dds_layout(source)
    target = SimpleNamespace(
        semantic_key="Body-1",
        metadata_key="Body::one",
        adjustment={"hue": 30},
    )
    prepared = SimpleNamespace(
        selected_path=str(source),
        info=layout.info,
        layout=layout,
        entries=({"semantic_key": "Body-1", "texture_keys": _role_keys()},),
        targets=(target,),
        mip0_claims=bytearray([1] * 16),
        intent_adjustments=(None, prepare_color_adjustment({"hue": 30})),
        mip0_affected_blocks=(0,),
    )
    stats = texture_save.BC7SaveStats(
        touched_blocks=1, improved_blocks=1, unchanged_blocks=0,
        source_rgb_error=10, final_rgb_error=5, modes={6: 1})
    monkeypatch.setattr(
        texture_save, "_prepare_texture_save",
        lambda *args, **kwargs: prepared)
    monkeypatch.setattr(
        texture_save, "_save_bc7_blocks",
        lambda *args, **kwargs: (original, stats))

    result = texture_save.save_texture_color(
        SimpleNamespace(mod_dir=str(tmp_path)), {}, {"Body-1"},
        "diffuse::body.dds", [{
            "semantic_key": "Body-1", "metadata_key": "Body::one",
            "adjustment": {"hue": 30},
        }], [{"semantic_key": "Body-1", "texture_keys": _role_keys()}])

    assert result["status"] == "ok"
    assert "patched" not in result
    assert result["diagnostics"]["bc7"]["touched_blocks"] == 1
    backups = list(tmp_path.glob("body-??????????????.dds"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == original


def test_save_aborts_on_stale_source_before_creating_backup(tmp_path, monkeypatch):
    source = tmp_path / "body.dds"
    original = _dx10_dds(bytes(16))
    source.write_bytes(original)
    prepared = _write_prepared_save(source)
    monkeypatch.setattr(
        texture_save, "_prepare_texture_save",
        lambda *args, **kwargs: prepared)
    monkeypatch.setattr(
        texture_save, "_save_bc7_blocks",
        lambda *args, **kwargs: (original, _write_stats()))

    reads = iter((original, b"changed"))
    real_read = texture_save._read_source

    def read_source(path):
        if str(path) == str(source):
            return next(reads)
        return real_read(path)

    monkeypatch.setattr(texture_save, "_read_source", read_source)
    result = texture_save.save_texture_color(
        SimpleNamespace(mod_dir=str(tmp_path)), {}, {"Anchor"},
        "diffuse::body.dds", [], [])

    assert result["code"] == "texture_changed_during_save"
    assert source.read_bytes() == original
    assert not list(tmp_path.glob("body-??????????????.dds"))


def test_save_rejects_changed_candidate_layout_before_backup(tmp_path, monkeypatch):
    source = tmp_path / "body.dds"
    original = _dx10_dds(bytes(16))
    source.write_bytes(original)
    prepared = _write_prepared_save(source)
    candidate = _dx10_dds(bytes(32), width=8, height=4)
    monkeypatch.setattr(
        texture_save, "_prepare_texture_save",
        lambda *args, **kwargs: prepared)
    monkeypatch.setattr(
        texture_save, "_save_bc7_blocks",
        lambda *args, **kwargs: (candidate, _write_stats()))

    result = texture_save.save_texture_color(
        SimpleNamespace(mod_dir=str(tmp_path)), {}, {"Anchor"},
        "diffuse::body.dds", [], [])

    assert result["code"] == "texture_validation_failed"
    assert source.read_bytes() == original
    assert not list(tmp_path.glob("body-??????????????.dds"))


def test_save_reports_commit_when_replace_raises_after_replacement(
        tmp_path, monkeypatch):
    source = tmp_path / "body.dds"
    original = _dx10_dds(bytes(16))
    candidate = bytearray(original)
    candidate[-1] ^= 1
    source.write_bytes(original)
    prepared = _write_prepared_save(source)
    monkeypatch.setattr(
        texture_save, "_prepare_texture_save",
        lambda *args, **kwargs: prepared)
    monkeypatch.setattr(
        texture_save, "_save_bc7_blocks",
        lambda *args, **kwargs: (bytes(candidate), _write_stats()))
    real_replace = texture_save.os.replace

    def replace_then_raise(source_path, target_path):
        real_replace(source_path, target_path)
        raise OSError("reported after replacement")

    monkeypatch.setattr(texture_save.os, "replace", replace_then_raise)
    result = texture_save.save_texture_color(
        SimpleNamespace(mod_dir=str(tmp_path)), {}, {"Anchor"},
        "diffuse::body.dds", [], [])

    assert result["status"] == "ok"
    assert source.read_bytes() == bytes(candidate)


def test_backup_names_never_overwrite_previous_backup(tmp_path, monkeypatch):
    source = tmp_path / "body.dds"
    source.write_bytes(b"original")
    monkeypatch.setattr(
        texture_save, "datetime",
        SimpleNamespace(now=lambda: datetime(2026, 9, 1, 15, 22, 30)))

    first = texture_save._write_backup(str(source), b"first")
    second = texture_save._write_backup(str(source), b"second")

    assert first != second
    assert (tmp_path / "body-20260901152230.dds").read_bytes() == b"first"
    assert (tmp_path / "body-20260901152231.dds").read_bytes() == b"second"


def test_save_request_rejects_legacy_diffuse_alias():
    with pytest.raises(texture_save.TextureSaveError) as raised:
        texture_save._validate_usage(
            {"Body-1"},
            [{"semantic_key": "Body-1", "tex_key": "diffuse::body.dds"}])

    assert raised.value.code == "stale_mesh_state"


def test_save_request_requires_all_texture_roles():
    roles = _role_keys()
    roles.pop("emission_map")
    with pytest.raises(texture_save.TextureSaveError) as raised:
        texture_save._validate_usage(
            {"Body-1"},
            [{"semantic_key": "Body-1", "texture_keys": roles}])

    assert raised.value.code == "stale_mesh_state"


def test_save_preparation_keeps_only_bc7_intent_and_target_coverage(
        tmp_path, monkeypatch):
    source = tmp_path / "body.dds"
    source.write_bytes(_dx10_dds(bytes(16)))
    anchor = SimpleNamespace(label="Anchor")
    draw = SimpleNamespace(label="Body-1")
    group = {}
    parsed = SimpleNamespace(game=SimpleNamespace(game="unknown"), groups=())
    geometry = SimpleNamespace(
        indices=(0, 1, 2), source_uvs=())
    coverage = UVCoverage(
        4, 4, bytearray([1] + [0] * 15), 1, (0, 0, 0, 0), 1, 0)
    monkeypatch.setattr(
        texture_save, "resolved_draws",
        lambda *_args: (parsed, {
            "Anchor": (anchor, group), "Body-1": (draw, group),
        }))
    monkeypatch.setattr(
        texture_save, "_prepare_uv_geometry", lambda *_args: geometry)
    monkeypatch.setattr(
        texture_save, "_draw_metadata_key", lambda *_args: "Body::one")
    monkeypatch.setattr(
        texture_save, "_rasterize_geometry", lambda *_args: coverage)

    prepared = texture_save._prepare_texture_save(
        SimpleNamespace(mod_dir=str(tmp_path)), {}, {"Anchor", "Body-1"},
        "diffuse::body.dds", [{
            "semantic_key": "Body-1", "metadata_key": "Body::one",
            "adjustment": {"hue": 30},
        }], [{
            "semantic_key": "Anchor", "texture_keys": _role_keys(),
        }, {
            "semantic_key": "Body-1",
            "texture_keys": _role_keys("diffuse::nested/../body.dds"),
        }])

    assert list(prepared.mip0_claims) == [1] + [0] * 15
    assert prepared.mip0_affected_blocks == (0,)
    assert prepared.targets[0].semantic_key == "Body-1"
    assert prepared.targets[0].metadata_key == "Body::one"
    assert not hasattr(prepared.targets[0], "pixel_coverage")
    assert not hasattr(prepared, "safe_masks")
    assert not hasattr(prepared, "target_pixel_masks")


def test_save_rejects_target_on_different_physical_dds(tmp_path, monkeypatch):
    (tmp_path / "body.dds").write_bytes(_dx10_dds(bytes(16)))
    (tmp_path / "other.dds").write_bytes(_dx10_dds(bytes(16)))
    group = {}
    parsed = SimpleNamespace(game=SimpleNamespace(game="unknown"), groups=())
    monkeypatch.setattr(
        texture_save, "resolved_draws",
        lambda *_args: (parsed, {
            "Anchor": (SimpleNamespace(label="Anchor"), group),
            "Target": (SimpleNamespace(label="Target"), group),
        }))

    with pytest.raises(texture_save.TextureSaveError) as raised:
        texture_save._prepare_texture_save(
            SimpleNamespace(mod_dir=str(tmp_path)), {}, {"Anchor", "Target"},
            "diffuse::body.dds", [{
                "semantic_key": "Target", "metadata_key": "Target::one",
                "adjustment": {"hue": 30},
            }], [{
                "semantic_key": "Anchor", "texture_keys": _role_keys(),
            }, {
                "semantic_key": "Target",
                "texture_keys": _role_keys("diffuse::other.dds"),
            }])

    assert raised.value.code == "stale_mesh_state"


@pytest.mark.parametrize(
    ("texture_key", "expected_code"),
    [
        ("diffuse::asset/root/body.dds", "asset_texture_read_only"),
        ("diffuse::../outside.dds", "texture_not_found"),
    ],
)
def test_save_rejects_asset_and_mod_root_escape_paths(
        tmp_path, texture_key, expected_code):
    result = texture_save.save_texture_color(
        SimpleNamespace(mod_dir=str(tmp_path)), {}, {"Anchor"}, texture_key,
        [{
            "semantic_key": "Anchor", "metadata_key": "Anchor::one",
            "adjustment": {"hue": 30},
        }], [{
            "semantic_key": "Anchor", "texture_keys": _role_keys(texture_key),
        }])

    assert result["code"] == expected_code
    assert not list(tmp_path.glob("*.dds"))


@pytest.mark.parametrize(
    "normal_key", ["normal_map::body.dds", "normal_map::nested/../body.dds"])
def test_save_rejects_live_cross_role_physical_usage(
        tmp_path, monkeypatch, normal_key):
    (tmp_path / "body.dds").write_bytes(_dx10_dds(bytes(16)))
    parsed = SimpleNamespace(game=SimpleNamespace(game="unknown"), groups=())
    monkeypatch.setattr(
        texture_save, "resolved_draws",
        lambda *_args: (parsed, {
            "Anchor": (SimpleNamespace(label="Anchor"), {}),
            "Other": (SimpleNamespace(label="Other"), {}),
        }))
    other_keys = _role_keys(None)
    other_keys["normal_map"] = normal_key

    with pytest.raises(texture_save.TextureSaveError) as raised:
        texture_save._resolve_save_request(
            SimpleNamespace(mod_dir=str(tmp_path)), {}, {"Anchor", "Other"},
            "diffuse::body.dds", [{
                "semantic_key": "Anchor", "texture_keys": _role_keys(),
            }, {
                "semantic_key": "Other", "texture_keys": other_keys,
            }])

    assert raised.value.code == "cross_role_texture_usage"


def test_save_rejects_authored_inactive_cross_role_variant(tmp_path, monkeypatch):
    (tmp_path / "body.dds").write_bytes(_dx10_dds(bytes(16)))
    selected = DrawCall(
        label="Anchor", count=3, texture_default_file="body.dds")
    inactive = DrawCall(
        label="Inactive", count=3, texture_default_file="face.dds",
        normal_map_variants=[{
            "conditions": [[{"var": "toggle", "value": "1"}]],
            "file": "body.dds",
        }])
    selected_group = {"draws": [selected]}
    inactive_group = {"draws": [inactive]}
    parsed = SimpleNamespace(
        game=SimpleNamespace(game="unknown"),
        groups=[selected_group, inactive_group],
    )
    monkeypatch.setattr(
        texture_save, "resolved_draws",
        lambda *_args: (parsed, {
            "Anchor": (selected, selected_group),
            "Inactive": (inactive, inactive_group),
        }))

    with pytest.raises(texture_save.TextureSaveError) as raised:
        texture_save._resolve_save_request(
            SimpleNamespace(mod_dir=str(tmp_path)), {}, {"Anchor"},
            "diffuse::body.dds", [{
                "semantic_key": "Anchor", "texture_keys": _role_keys(),
            }])

    assert raised.value.code == "cross_role_texture_usage"
    assert "Normal Map" in raised.value.message


def test_save_rejects_stale_canonical_metadata_key(tmp_path, monkeypatch):
    (tmp_path / "body.dds").write_bytes(_dx10_dds(bytes(16)))
    draw = SimpleNamespace(label="Anchor")
    group = {}
    parsed = SimpleNamespace(game=SimpleNamespace(game="unknown"), groups=())
    monkeypatch.setattr(
        texture_save, "resolved_draws",
        lambda *_args: (parsed, {"Anchor": (draw, group)}))
    monkeypatch.setattr(
        texture_save, "_draw_metadata_key", lambda *_args: "Anchor::actual")

    with pytest.raises(texture_save.TextureSaveError) as raised:
        texture_save._prepare_texture_save(
            SimpleNamespace(mod_dir=str(tmp_path)), {}, {"Anchor"},
            "diffuse::body.dds", [{
                "semantic_key": "Anchor", "metadata_key": "Anchor::stale",
                "adjustment": {"hue": 30},
            }], [{
                "semantic_key": "Anchor", "texture_keys": _role_keys(),
            }])

    assert raised.value.code == "stale_mesh_state"


def _single_intent_claims(width, height, selected):
    return [
        1 if (x, y) in selected else 0
        for y in range(height)
        for x in range(width)
    ]


def _bc7_block_state(width, height, claims, adjustments=None):
    if adjustments is None:
        adjustments = (None, prepare_color_adjustment({"brightness": 1.5}))
    prepared = SimpleNamespace(
        layout=SimpleNamespace(mips=(SimpleNamespace(
            width=width, height=height,
            units_x=(width + 3) // 4),)),
        mip0_claims=claims,
        intent_adjustments=adjustments,
    )
    return texture_save._bc7_intent_level(prepared)


def test_bc7_block_intent_classes_only_report_explicit_mip0_claims():
    claims = bytearray([0] * 16)
    claims[5] = 1
    claims[6] = 1
    claims[9] = 2
    original = claims[:]
    state = _bc7_block_state(
        4, 4, claims,
        (None, prepare_color_adjustment({"hue": 30}),
         prepare_color_adjustment({"hue": 120})))
    mip = SimpleNamespace(
        width=4, height=4, units_x=1)

    assert texture_save._bc7_block_intent_classes(
        state, mip, 0) == {1, 2}
    assert claims == original


@pytest.mark.parametrize(
    ("width", "height", "selected"),
    [
        (4, 4, {(1, 1)}),
        (4, 4, {(0, 1), (1, 1), (2, 1), (3, 1)}),
        (4, 4, {(0, 0), (1, 0), (0, 1)}),
        (4, 4, {(0, 0), (1, 0), (0, 1), (1, 1)}),
        (2, 2, {(0, 0)}),
        (1, 1, {(0, 0)}),
    ],
)
def test_single_intent_partial_block_pads_valid_rgb_without_changing_alpha(
        width, height, selected):
    source_block = _mode6_block()
    source_pixels = bc7.decode_block(source_block)
    adjustment = prepare_color_adjustment({"brightness": 1.5})
    state = _bc7_block_state(
        width, height, bytearray(_single_intent_claims(
            width, height, selected)), (None, adjustment))
    mip = SimpleNamespace(
        width=width, height=height, units_x=1)

    _source, target, valid_width, valid_height = \
        texture_save._bc7_target_block_pixels(
            source_block, mip, 0, state, (None, adjustment))

    assert (valid_width, valid_height) == (width, height)
    expected_rgb = {
        (x, y): texture_save.apply_prepared_color_u8(
            source_pixels[y * 4 + x][:3], adjustment)
        for y in range(height)
        for x in range(width)
    }
    for y in range(height):
        for x in range(width):
            pixel = target[y * 4 + x]
            assert pixel[:3] == expected_rgb[(x, y)]
            assert pixel[3] == source_pixels[y * 4 + x][3]
    for y in range(height, 4):
        for x in range(4):
            assert target[y * 4 + x] == source_pixels[y * 4 + x]
    for y in range(height):
        for x in range(width, 4):
            assert target[y * 4 + x] == source_pixels[y * 4 + x]


def test_multi_intent_block_keeps_per_pixel_logical_targets_and_no_padding():
    source_block = _mode6_block()
    source_pixels = bc7.decode_block(source_block)
    adjustments = (
        None,
        prepare_color_adjustment({"brightness": 1.5}),
        prepare_color_adjustment({"hue": 120}),
    )
    claims = bytearray([0] * 16)
    claims[5] = 1
    claims[6] = 2
    state = _bc7_block_state(4, 4, claims, adjustments)
    mip = SimpleNamespace(width=4, height=4, units_x=1)

    _source, target, valid_width, valid_height = \
        texture_save._bc7_target_block_pixels(
            source_block, mip, 0, state, adjustments)

    assert (valid_width, valid_height) == (4, 4)
    for pixel, claim in enumerate(claims):
        expected = texture_save._bc7_intent_rgb(
            source_pixels[pixel][:3], state, pixel, adjustments)
        assert target[pixel][:3] == expected
        assert target[pixel][3] == source_pixels[pixel][3]
    assert target[0][:3] == source_pixels[0][:3]


def test_bc7_save_pads_single_intent_block_and_preserves_unrelated_blocks(
        tmp_path, monkeypatch):
    first = _mode6_block()
    second = _mode6_block(((30, 120), (50, 150), (70, 180)))
    original = _dx10_dds(first + second, width=8, height=4)
    source = tmp_path / "body.dds"
    source.write_bytes(original)
    layout = inspect_dds_layout(source)
    adjustment = prepare_color_adjustment({"hue": 120})
    claims = bytearray([0] * 32)
    claims[1] = 1
    prepared = SimpleNamespace(
        selected_path=str(source), info=layout.info, layout=layout,
        entries=({
            "semantic_key": "Anchor", "texture_keys": _role_keys(),
        },),
        targets=(SimpleNamespace(
            semantic_key="Anchor", metadata_key="Anchor::one"),),
        mip0_claims=claims, intent_adjustments=(None, adjustment),
        mip0_affected_blocks=(0,))
    monkeypatch.setattr(
        texture_save, "_prepare_texture_save",
        lambda *args, **kwargs: prepared)

    result = texture_save.save_texture_color(
        SimpleNamespace(mod_dir=str(tmp_path)), {}, {"Anchor"},
        "diffuse::body.dds", [], [])

    assert result["status"] == "ok"
    candidate = source.read_bytes()
    assert candidate[layout.mips[0].offset:layout.mips[0].offset + 16] != first
    assert candidate[layout.mips[0].offset + 16:
                     layout.mips[0].offset + 32] == second
    assert inspect_dds_layout(source) == layout
    stats = result["diagnostics"]["bc7"]
    assert stats["touched_blocks"] == 1
    assert stats["partial_blocks"] == 1
    assert stats["full_blocks"] == 0
    assert stats["single_intent_partial_blocks"] == 1
    assert stats["multi_intent_blocks"] == 0
    assert stats["source_blocks_kept"] == 0
    source_pixels = bc7.decode_block(first)
    candidate_pixels = bc7.decode_block(candidate[layout.mips[0].offset:
                                                    layout.mips[0].offset + 16])
    assert [pixel[3] for pixel in candidate_pixels] == [
        pixel[3] for pixel in source_pixels]


def test_bc7_stats_count_source_blocks_by_block_identity(tmp_path, monkeypatch):
    source_block = _mode6_block()
    source = tmp_path / "body.dds"
    source.write_bytes(_dx10_dds(source_block))
    layout = inspect_dds_layout(source)
    adjustment = prepare_color_adjustment({"hue": 30})
    claims = bytearray([1] * 16)
    prepared = SimpleNamespace(
        selected_path=str(source), info=layout.info, layout=layout,
        mip0_claims=claims, intent_adjustments=(None, adjustment),
        mip0_affected_blocks=(0,))

    monkeypatch.setattr(
        texture_save._bc7_codec, "recolor_block",
        lambda *_args: SimpleNamespace(
            block=source_block, source_error=10, candidate_error=1, mode=6))

    _candidate, stats = texture_save._save_bc7_blocks(
        source.read_bytes(), prepared)

    assert stats.source_blocks_kept == 1
    assert stats.improved_blocks == 1
    assert stats.unchanged_blocks == 0


@pytest.mark.parametrize(
    ("width", "height", "selected", "expected_changed", "expected_total"),
    [
        (8, 8, {(x, y) for y in range(8) for x in range(8)}, 64, 64),
        (8, 8, {(x, y) for y in range(4) for x in range(4)}, 16, 64),
        (15, 9, {(x, y) for y in range(9) for x in range(15)}, 135, 135),
    ],
)
def test_single_intent_mip_counts_keep_exact_weighting(
        width, height, selected, expected_changed, expected_total):
    adjustment = prepare_color_adjustment({"brightness": 1.5})
    prepared = SimpleNamespace(
        layout=SimpleNamespace(mips=(SimpleNamespace(
            width=width, height=height),)),
        mip0_claims=_single_intent_claims(width, height, selected),
        intent_adjustments=(None, adjustment),
    )
    state = texture_save._bc7_intent_level(prepared)
    base = (100, 100, 100)
    base_float = tuple(channel / 255.0 for channel in base)
    adjusted = apply_prepared_color_adjustment(
        base_float, adjustment)
    target_width, target_height = width, height
    while (target_width, target_height) != (1, 1):
        target_width = max(1, target_width // 2)
        target_height = max(1, target_height // 2)
        state = texture_save._bc7_next_intent_level(
            state, target_width, target_height, 2)
        assert all(0 <= changed <= total for changed, total in zip(
            state["changed_counts"], state["total_counts"]))

    changed = state["changed_counts"][0]
    total = state["total_counts"][0]
    assert (changed, total) == (expected_changed, expected_total)
    expected = tuple(min(255, max(0, round(
        (base_float[channel] * (total - changed)
         + adjusted[channel] * changed) / total * 255.0)))
                      for channel in range(3))
    assert texture_save._bc7_intent_rgb(
        base, state, 0, (None, adjustment)) == expected


def test_save_rejects_non_bc7_dds(tmp_path):
    source = tmp_path / "body.dds"
    source.write_bytes(_dx10_dds(bytes(8), dxgi_format=71))

    with pytest.raises(texture_save.TextureSaveError) as raised:
        texture_save._inspect_save_texture(str(source))

    assert raised.value.code == "unsupported_texture_format"

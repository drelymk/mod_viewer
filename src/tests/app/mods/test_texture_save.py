"""Atomic BC7 Save to Texture regressions."""

import struct
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.mods import texture_save
from core.geometry.draw_call import DrawCall
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

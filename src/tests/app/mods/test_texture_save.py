"""Atomic BC7 Save to Texture regressions."""

from types import SimpleNamespace
import struct

import pytest

from app.mods import texture_save
from core.textures.color_adjustment import prepare_color_adjustment
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
        lambda *args, **kwargs: (original, (bytearray([1]),), stats))

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


def test_save_request_rejects_legacy_diffuse_alias():
    with pytest.raises(texture_save.TextureSaveError) as raised:
        texture_save._validate_usage(
            {"Body-1"}, "Body-1", "diffuse::body.dds",
            [{"semantic_key": "Body-1", "tex_key": "diffuse::body.dds"}])

    assert raised.value.code == "stale_mesh_state"


def test_save_request_requires_all_texture_roles():
    roles = _role_keys()
    roles.pop("emission_map")
    with pytest.raises(texture_save.TextureSaveError) as raised:
        texture_save._validate_usage(
            {"Body-1"}, "Body-1", "diffuse::body.dds",
            [{"semantic_key": "Body-1", "texture_keys": roles}])

    assert raised.value.code == "stale_mesh_state"


def test_save_preparation_keeps_only_bc7_intent_and_target_coverage(
        tmp_path, monkeypatch):
    source = tmp_path / "body.dds"
    source.write_bytes(_dx10_dds(bytes(16)))
    draw = SimpleNamespace(label="Body-1")
    group = {}
    parsed = SimpleNamespace(game=SimpleNamespace(game="unknown"), groups=())
    geometry = SimpleNamespace(
        indices=(0, 1, 2), source_uvs=(), triangle_count=1,
        degenerate_triangle_count=0)
    coverage = UVCoverage(
        4, 4, bytearray([1] + [0] * 15), 1, (0, 0, 0, 0), 1, 0)
    monkeypatch.setattr(
        texture_save, "resolved_draws",
        lambda *_args: (parsed, {"Body-1": (draw, group)}))
    monkeypatch.setattr(
        texture_save, "_prepare_uv_geometry", lambda *_args: geometry)
    monkeypatch.setattr(
        texture_save, "_draw_metadata_key", lambda *_args: "Body::one")
    monkeypatch.setattr(
        texture_save, "_rasterize_geometry", lambda *_args: coverage)

    prepared = texture_save._prepare_texture_save(
        SimpleNamespace(mod_dir=str(tmp_path)), {}, {"Body-1"},
        "diffuse::body.dds", [{
            "semantic_key": "Body-1", "metadata_key": "Body::one",
            "adjustment": {"hue": 30},
        }], [{"semantic_key": "Body-1", "texture_keys": _role_keys()}])

    assert list(prepared.mip0_claims) == [1] + [0] * 15
    assert prepared.mip0_affected_blocks == (0,)
    assert prepared.targets[0].pixel_coverage is coverage
    assert not hasattr(prepared, "safe_masks")
    assert not hasattr(prepared, "target_pixel_masks")


def test_save_rejects_non_bc7_dds(tmp_path):
    source = tmp_path / "body.dds"
    source.write_bytes(_dx10_dds(bytes(8), dxgi_format=71))

    with pytest.raises(texture_save.TextureSaveError) as raised:
        texture_save._inspect_save_texture(str(source))

    assert raised.value.code == "unsupported_texture_format"

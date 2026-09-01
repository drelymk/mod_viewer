"""Read-only texture coverage analysis regressions."""

from types import SimpleNamespace

import pytest

from app.mods import texture_bake
from core.geometry.buffers import BufferStore
from core.geometry.conventions import geometry_convention_for
from core.geometry.draw_call import DrawCall
from core.textures.uv_coverage import UVCoverage
from core.textures.uv_coverage import rasterize_uv_coverage


def _info(format_name="bc7_unorm", compressed=True):
    return SimpleNamespace(
        width=8, height=8, mip_count=1, format=format_name,
        compressed=compressed, requires_bc=compressed)


def _context(mod_dir):
    return SimpleNamespace(
        mod_dir=str(mod_dir), ini_paths=[], docs={},
        game=SimpleNamespace(game="unknown"))


def _draw(label):
    return SimpleNamespace(label=label)


def _coverage(mask):
    return UVCoverage(
        grid_width=2, grid_height=2, mask=bytearray(mask),
        count=sum(mask), bounds=(0, 0, 1, 1),
        triangle_count=1, degenerate_triangle_count=0)


def _patch_analysis(monkeypatch, draws, coverage_by_label):
    parsed = SimpleNamespace(game=SimpleNamespace(game="unknown"))
    groups = {label: ({"draws": []}) for label in draws}
    monkeypatch.setattr(
        texture_bake, "resolved_draws",
        lambda *_args: (parsed, {
            label: (draw, groups[label]) for label, draw in draws.items()
        }),
    )
    monkeypatch.setattr(
        texture_bake, "_inspect_color_texture", lambda _path: _info())
    monkeypatch.setattr(
        texture_bake, "_coverage",
        lambda draw, *_args: _coverage(coverage_by_label[draw.label]),
    )


def test_coverage_uses_packed_geometry_and_restores_source_v(tmp_path):
    import struct

    source_uvs = [(0.125, 0.25), (0.875, 0.25), (0.125, 0.75)]
    (tmp_path / "position.buf").write_bytes(struct.pack(
        "<9f", 0., 0., 0., 1., 0., 0., 0., 1., 0.))
    (tmp_path / "texcoord.buf").write_bytes(struct.pack(
        "<6f", *(component for uv in source_uvs for component in uv)))
    (tmp_path / "body.ib").write_bytes(struct.pack("<3I", 0, 1, 2))
    draw = DrawCall(
        label="Body-1", count=3, ib_file="body.ib", index_size=4,
        position_file="position.buf", position_stride=12,
        texcoord_file="texcoord.buf", texcoord_stride=8)
    group = {
        "position_file": "position.buf", "position_stride": 12,
        "texcoord_file": "texcoord.buf", "texcoord_stride": 8,
        "ib_file": "body.ib", "index_size": 4, "draws": [draw],
    }

    result = texture_bake._coverage(
        draw, group, str(tmp_path), _info("rgba8", False), BufferStore(), {},
        geometry_convention_for("unknown"))
    expected = rasterize_uv_coverage(
        [0, 1, 2], source_uvs, 8, 8)

    assert result.mask == expected.mask
    assert result.count == expected.count


def test_shared_physical_texture_reports_overlap_and_hidden_consumer(
        tmp_path, monkeypatch):
    texture = tmp_path / "body.dds"
    original = b"unchanged DDS fixture"
    texture.write_bytes(original)
    before = texture.stat().st_mtime_ns
    draws = {"Body-1": _draw("Body-1"), "Body-2": _draw("Body-2")}
    _patch_analysis(monkeypatch, draws, {
        "Body-1": [1, 1, 0, 0], "Body-2": [0, 1, 1, 0],
    })
    usage = [
        {"semantic_key": "Body-1", "tex_key": "diffuse::body.dds"},
        # This consumer is hidden in the viewport in the real UI, but remains
        # part of the model-wide texture usage snapshot.
        {"semantic_key": "Body-2", "tex_key": "diffuse::body.dds"},
    ]

    result = texture_bake.analyze_texture_bake(
        _context(tmp_path), {}, set(draws), "Body-1",
        "diffuse::body.dds", usage)

    assert result["status"] == "ok"
    assert result["safety"] == "shared"
    assert result["coverage"]["shared_units"] == 1
    assert result["shared_with"] == [{
        "semantic_key": "Body-2", "shared_units": 1,
    }]
    assert texture.read_bytes() == original
    assert texture.stat().st_mtime_ns == before


def test_unanalyzable_same_texture_consumer_is_unknown(
        tmp_path, monkeypatch):
    texture = tmp_path / "body.dds"
    texture.write_bytes(b"fixture")
    draws = {"Body-1": _draw("Body-1"), "Body-2": _draw("Body-2")}
    _patch_analysis(monkeypatch, draws, {"Body-1": [1, 0, 0, 0]})

    def coverage(draw, *_args):
        if draw.label == "Body-2":
            raise texture_bake.TextureBakeAnalysisError(
                "geometry_not_available", "missing geometry")
        return _coverage([1, 0, 0, 0])

    monkeypatch.setattr(texture_bake, "_coverage", coverage)
    result = texture_bake.analyze_texture_bake(
        _context(tmp_path), {}, set(draws), "Body-1",
        "diffuse::body.dds", [
            {"semantic_key": "Body-1", "tex_key": "diffuse::body.dds"},
            {"semantic_key": "Body-2", "tex_key": "diffuse::body.dds"},
        ])

    assert result["status"] == "ok"
    assert result["safety"] == "unknown"
    assert result["unresolved_consumers"] == ["Body-2"]
    assert result["coverage"]["unique_units"] is None
    assert result["unresolved_consumer_details"][0]["code"] == \
        "geometry_not_available"


@pytest.mark.parametrize(("key", "code", "status"), [
    (None, "no_diffuse", "error"),
    ("normal_map::body.dds", "not_diffuse_texture", "error"),
    ("diffuse::asset/root/body.dds", "asset_texture_read_only", "unsupported"),
    ("diffuse::body.png", "unsupported_texture_type", "unsupported"),
    ("diffuse::missing.dds", "texture_not_found", "error"),
    ("diffuse::../outside.dds", "texture_not_found", "error"),
])
def test_selected_texture_eligibility_is_safe_and_stable(
        tmp_path, monkeypatch, key, code, status):
    monkeypatch.setattr(
        texture_bake, "resolved_draws",
        lambda *_args: (SimpleNamespace(game=SimpleNamespace(game="unknown")), {}),
    )
    usage = [{"semantic_key": "Body-1", "tex_key": key}]

    result = texture_bake.analyze_texture_bake(
        _context(tmp_path), {}, {"Body-1"}, "Body-1", key, usage)

    assert result["code"] == code
    assert result["status"] == status


@pytest.mark.parametrize("format_name", [
    "bc1_unorm", "bc2_srgb", "bc3_unorm", "bc7_srgb", "rgba8", "bgra8",
])
def test_supported_color_formats_are_analyzable(tmp_path, monkeypatch, format_name):
    texture = tmp_path / "body.dds"
    texture.write_bytes(b"fixture")
    draws = {"Body-1": _draw("Body-1")}
    _patch_analysis(monkeypatch, draws, {"Body-1": [1, 0, 0, 0]})
    monkeypatch.setattr(
        texture_bake, "_inspect_color_texture",
        lambda _path: _info(format_name, format_name in texture_bake._UNSUPPORTED_COLOR_FORMATS
                            or format_name.startswith("bc")),
    )
    result = texture_bake.analyze_texture_bake(
        _context(tmp_path), {}, set(draws), "Body-1",
        "diffuse::body.dds", [{
            "semantic_key": "Body-1", "tex_key": "diffuse::body.dds",
        }])

    assert result["status"] == "ok"


@pytest.mark.parametrize("format_name", [
    "bc4_unorm", "bc5_snorm", "bc6h_ufloat", "unknown",
])
def test_non_color_bake_formats_are_rejected(tmp_path, monkeypatch, format_name):
    texture = tmp_path / "body.dds"
    texture.write_bytes(b"fixture")
    monkeypatch.setattr(
        texture_bake, "inspect_dds", lambda _path: _info(format_name))

    result = texture_bake.analyze_texture_bake(
        _context(tmp_path), {}, {"Body-1"}, "Body-1",
        "diffuse::body.dds", [{
            "semantic_key": "Body-1", "tex_key": "diffuse::body.dds",
        }])

    assert result["status"] == "unsupported"
    assert result["code"] == "unsupported_color_bake_format"


def test_invalid_dds_is_rejected_without_falling_back_to_texture_decode(
        tmp_path, monkeypatch):
    texture = tmp_path / "body.dds"
    texture.write_bytes(b"not a DDS")
    monkeypatch.setattr(texture_bake, "inspect_dds", lambda _path: None)

    result = texture_bake.analyze_texture_bake(
        _context(tmp_path), {}, {"Body-1"}, "Body-1",
        "diffuse::body.dds", [{
            "semantic_key": "Body-1", "tex_key": "diffuse::body.dds",
        }])

    assert result == {
        "status": "error",
        "code": "invalid_dds",
        "error": "The selected texture is not a valid supported DDS.",
    }

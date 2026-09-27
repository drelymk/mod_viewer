"""Atomic BC7 Save to Texture regressions."""

from concurrent.futures import Future
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.mods.texture_save import bc7_recolor, coverage as save_coverage, errors, progress, request, service, transaction
from core.geometry.draw_call import DrawCall
from core.textures import bc7
from core.textures.color_adjustment import (
    prepare_color_adjustment,
)
from core.textures.dds import inspect_dds_layout
from core.textures.uv_coverage import UVCoverage
from tests.support.dds_data import dx10_dds, mode6_block


def test_save_texture_color_rejects_read_only_zip_source_before_io():
    result = service.save_texture_color(
        SimpleNamespace(source=SimpleNamespace(read_only=True)), None,
        "diffuse::component01.dds", [], [],)

    assert result == {
        "status": "unsupported",
        "code": "read_only_source",
        "error": "Save to Texture is unavailable for compressed mods.",
    }


def _role_keys(diffuse="diffuse::component01.dds"):
    return {
        "diffuse": diffuse,
        "normal_map": None,
        "normal_data": None,
        "light_map": None,
        "material_map": None,
        "emission_map": None,
    }


def test_texture_coverage_uses_staged_buffer_overrides(tmp_path, monkeypatch):
    texture = tmp_path / "component01.dds"
    texture.write_bytes(dx10_dds(bytes(16), width=1, height=1))
    source = object()
    staged = {str(tmp_path / "Component01.ib"): b"staged-index-buffer"}
    context = SimpleNamespace(
        mod_dir=str(tmp_path), source=source, buffer_overrides=staged)
    draw = DrawCall(count=3, start=0, base=0)
    group = {"display_name": "Component01"}
    entries = ({"semantic_key": "Component01-1", "texture_keys": _role_keys()},)
    parsed = SimpleNamespace(game=SimpleNamespace(game="GIMI"))
    monkeypatch.setattr(save_coverage, "resolve_save_request",
                        lambda *_args: (
                            entries, str(texture), SimpleNamespace(), parsed,
                            {"Component01-1": (draw, group)}))
    monkeypatch.setattr(save_coverage, "geometry_convention_for",
                        lambda _game: object())
    monkeypatch.setattr(save_coverage, "draw_metadata_key",
                        lambda _draw, _group: "Component01::one")
    captured = {}

    class ProbeBufferStore:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(save_coverage, "BufferStore", ProbeBufferStore)
    monkeypatch.setattr(
        save_coverage, "prepare_uv_geometry",
        lambda _draw, _group, _mod_dir, buffers, _cache, _convention: (
            captured.setdefault("consumer", buffers), object())[1])
    monkeypatch.setattr(save_coverage, "rasterize_geometry",
                        lambda *_args: SimpleNamespace(mask=(1,)))

    prepared = save_coverage.prepare_texture_save(
        context, ["Component01-1"], "diffuse::component01.dds", [{
            "semantic_key": "Component01-1", "metadata_key": "Component01::one",
            "adjustment": {"hue": 30},
        }], [{"semantic_key": "Component01-1", "texture_keys": _role_keys()}])

    assert captured["source"] is source
    assert captured["overrides"] == staged
    assert captured["consumer"].__class__ is ProbeBufferStore
    assert len(prepared.targets) == 1


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


def _prepared_bc7(path, layout, claims, adjustments, affected_blocks):
    return SimpleNamespace(
        selected_path=str(path), info=layout.info, layout=layout,
        mip0_claims=claims, intent_adjustments=adjustments,
        mip0_affected_blocks=affected_blocks,
    )


class _InlineExecutor:
    def submit(self, function, argument):
        future = Future()
        future.set_result(function(argument))
        return future

    def shutdown(self, **_kwargs):
        pass


def test_save_is_bc7_only_and_returns_a_clean_public_result(tmp_path, monkeypatch):
    source = tmp_path / "component01.dds"
    original = dx10_dds(bytes(16))
    source.write_bytes(original)
    layout = inspect_dds_layout(source)
    target = SimpleNamespace(
        semantic_key="Component01-1",
        metadata_key="Component01::one",
        adjustment={"hue": 30},
    )
    prepared = SimpleNamespace(
        selected_path=str(source),
        info=layout.info,
        layout=layout,
        entries=({"semantic_key": "Component01-1", "texture_keys": _role_keys()},),
        targets=(target,),
        mip0_claims=bytearray([1] * 16),
        intent_adjustments=(None, prepare_color_adjustment({"hue": 30})),
        mip0_affected_blocks=(0,),
    )
    monkeypatch.setattr(
        service, "prepare_texture_save",
        lambda *args, **kwargs: prepared)
    monkeypatch.setattr(
        service, "_save_bc7_blocks",
        lambda *args, **kwargs: original)
    cleanup = []
    monkeypatch.setattr(
        service.metadata, "clear_mesh_color_adjustments_if_unchanged",
        lambda folder, expected: cleanup.append((folder, expected)) or {
            "cleared": ["Component01::one"], "preserved": [], "failed": [],
        })

    result = service.save_texture_color(
        SimpleNamespace(mod_dir=str(tmp_path)), {"Component01-1"},
        "diffuse::component01.dds", [{
            "semantic_key": "Component01-1", "metadata_key": "Component01::one",
            "adjustment": {"hue": 30},
        }], [{"semantic_key": "Component01-1", "texture_keys": _role_keys()}])

    assert result["status"] == "ok"
    assert result["texture"] == {"file": "component01.dds"}
    assert "patched" not in result
    assert "diagnostics" not in result
    assert cleanup == [(str(tmp_path), {"Component01::one": {"hue": 30}})]
    backups = list(tmp_path.glob("component01-??????????????.dds"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == original


def test_save_progress_reports_stages_and_ignores_callback_errors(
        tmp_path, monkeypatch):
    source = tmp_path / "component01.dds"
    original = dx10_dds(bytes(16))
    source.write_bytes(original)
    prepared = _write_prepared_save(source)
    monkeypatch.setattr(
        service, "prepare_texture_save",
        lambda *args, **kwargs: prepared)

    def save_blocks(*args, **kwargs):
        reporter = kwargs["progress_reporter"]
        reporter.processing(0, 1, 1, 1)
        return original

    monkeypatch.setattr(service, "_save_bc7_blocks", save_blocks)
    events = []

    def callback(event):
        events.append(event)
        raise RuntimeError("synthetic progress listener failure")

    result = service.save_texture_color(
        SimpleNamespace(mod_dir=str(tmp_path)), {"Anchor"},
        "diffuse::component01.dds", [], [], progress_callback=callback)

    assert result["status"] == "ok"
    assert [event["stage"] for event in events] == [
        "preparing", "reading", "processing", "writing", "complete",
    ]
    assert all("request_id" not in event for event in events)


@pytest.mark.parametrize(
    ("saved_meshes", "expected_failed"),
    [
        ([{"semantic_key": "Component01-1", "metadata_key": "Component01::missing"}],
         ["Component01::missing"]),
        ([
            {"semantic_key": "Component01-1", "metadata_key": "Shared::one"},
            {"semantic_key": "Component01-2", "metadata_key": "Shared::one"},
        ], ["Shared::one"]),
    ],
)
def test_committed_cleanup_fails_safe_for_unmatched_saved_identity(
        monkeypatch, saved_meshes, expected_failed):
    called = []
    monkeypatch.setattr(
        service.metadata, "clear_mesh_color_adjustments_if_unchanged",
        lambda *_args: called.append(True))

    receipt, failed = service._clear_committed_color_adjustments(
        "mod", [
            {"semantic_key": "Component01-1", "metadata_key": "Component01::one",
             "adjustment": {"hue": 30}},
            {"semantic_key": "Component01-2", "metadata_key": "Shared::one",
             "adjustment": {"hue": 45}},
        ], saved_meshes)

    assert called == []
    assert receipt == {"cleared": [], "preserved": [],
                       "failed": expected_failed}
    assert failed is False


def test_committed_cleanup_preserves_save_when_metadata_write_raises(
        monkeypatch):
    def fail_cleanup(*_args):
        raise RuntimeError("synthetic cleanup failure")

    monkeypatch.setattr(
        service.metadata, "clear_mesh_color_adjustments_if_unchanged",
        fail_cleanup)
    receipt, failed = service._clear_committed_color_adjustments(
        "mod", [{
            "semantic_key": "Component01-1", "metadata_key": "Component01::one",
            "adjustment": {"hue": 30},
        }], [{"semantic_key": "Component01-1", "metadata_key": "Component01::one"}])

    assert receipt == {"cleared": [], "preserved": [],
                       "failed": ["Component01::one"]}
    assert failed is True


def test_save_progress_throttles_intermediate_blocks_but_keeps_final(
        monkeypatch):
    events = []
    reporter = progress.SaveProgressReporter(events.append)
    monkeypatch.setattr(progress, "SAVE_PROGRESS_INTERVAL", 60.0)

    reporter.processing(0, 1, 0, 10)
    reporter.processing(0, 1, 1, 10)
    reporter.processing(0, 1, 10, 10)

    assert [event["completed_blocks"] for event in events] == [0, 10]


def test_save_aborts_on_stale_source_before_creating_backup(tmp_path, monkeypatch):
    source = tmp_path / "component01.dds"
    original = dx10_dds(bytes(16))
    source.write_bytes(original)
    prepared = _write_prepared_save(source)
    monkeypatch.setattr(
        service, "prepare_texture_save",
        lambda *args, **kwargs: prepared)
    monkeypatch.setattr(
        service, "_save_bc7_blocks",
        lambda *args, **kwargs: original)

    reads = iter((original, b"changed"))
    real_read = transaction._read_source

    def read_source(path):
        if str(path) == str(source):
            return next(reads)
        return real_read(path)

    monkeypatch.setattr(transaction, "_read_source", read_source)
    result = service.save_texture_color(
        SimpleNamespace(mod_dir=str(tmp_path)), {"Anchor"},
        "diffuse::component01.dds", [], [])

    assert result["code"] == "texture_changed_during_save"
    assert source.read_bytes() == original
    assert not list(tmp_path.glob("component01-??????????????.dds"))


def test_save_rejects_changed_candidate_layout_before_backup(tmp_path, monkeypatch):
    source = tmp_path / "component01.dds"
    original = dx10_dds(bytes(16))
    source.write_bytes(original)
    prepared = _write_prepared_save(source)
    candidate = dx10_dds(bytes(32), width=8, height=4)
    monkeypatch.setattr(
        service, "prepare_texture_save",
        lambda *args, **kwargs: prepared)
    monkeypatch.setattr(
        service, "_save_bc7_blocks",
        lambda *args, **kwargs: candidate)

    result = service.save_texture_color(
        SimpleNamespace(mod_dir=str(tmp_path)), {"Anchor"},
        "diffuse::component01.dds", [], [])

    assert result["code"] == "texture_validation_failed"
    assert source.read_bytes() == original
    assert not list(tmp_path.glob("component01-??????????????.dds"))


def test_save_reports_commit_when_replace_raises_after_replacement(
        tmp_path, monkeypatch):
    source = tmp_path / "component01.dds"
    original = dx10_dds(bytes(16))
    candidate = bytearray(original)
    candidate[-1] ^= 1
    source.write_bytes(original)
    prepared = _write_prepared_save(source)
    monkeypatch.setattr(
        service, "prepare_texture_save",
        lambda *args, **kwargs: prepared)
    monkeypatch.setattr(
        service, "_save_bc7_blocks",
        lambda *args, **kwargs: bytes(candidate))
    real_replace = transaction.os.replace

    def replace_then_raise(source_path, target_path):
        real_replace(source_path, target_path)
        raise OSError("reported after replacement")

    monkeypatch.setattr(transaction.os, "replace", replace_then_raise)
    result = service.save_texture_color(
        SimpleNamespace(mod_dir=str(tmp_path)), {"Anchor"},
        "diffuse::component01.dds", [], [])

    assert result["status"] == "ok"
    assert source.read_bytes() == bytes(candidate)


def test_save_marks_cleanup_uncertain_after_committed_cleanup_raises(
        tmp_path, monkeypatch):
    source = tmp_path / "component01.dds"
    original = dx10_dds(bytes(16))
    candidate = bytearray(original)
    candidate[-1] ^= 1
    source.write_bytes(original)
    prepared = _write_prepared_save(source)
    monkeypatch.setattr(
        service, "prepare_texture_save",
        lambda *args, **kwargs: prepared)
    monkeypatch.setattr(
        service, "_save_bc7_blocks",
        lambda *args, **kwargs: bytes(candidate))

    def fail_after_commit(*_args, **_kwargs):
        raise RuntimeError("synthetic cleanup failure")

    monkeypatch.setattr(
        service, "_clear_committed_color_adjustments", fail_after_commit)
    result = service.save_texture_color(
        SimpleNamespace(mod_dir=str(tmp_path)), {"Anchor"},
        "diffuse::component01.dds", [{
            "semantic_key": "Target", "metadata_key": "Target::one",
            "adjustment": {"hue": 30},
        }], [])

    assert result["status"] == "ok"
    assert result["warning"] == "color_state_reset_failed"
    assert result["metadata_reset"] == {
        "cleared": [], "preserved": [], "failed": ["Target::one"],
    }
    assert source.read_bytes() == bytes(candidate)


def test_backup_names_never_overwrite_previous_backup(tmp_path, monkeypatch):
    source = tmp_path / "component01.dds"
    source.write_bytes(b"original")
    monkeypatch.setattr(
        transaction, "datetime",
        SimpleNamespace(now=lambda: datetime(2026, 9, 1, 15, 22, 30)))

    first = transaction._write_backup(str(source), b"first")
    second = transaction._write_backup(str(source), b"second")

    assert first != second
    assert (tmp_path / "component01-20260901152230.dds").read_bytes() == b"first"
    assert (tmp_path / "component01-20260901152231.dds").read_bytes() == b"second"


def test_save_request_rejects_legacy_diffuse_alias():
    with pytest.raises(errors.TextureSaveError) as raised:
        request.validate_usage(
            {"Component01-1"},
            [{"semantic_key": "Component01-1", "tex_key": "diffuse::component01.dds"}])

    assert raised.value.code == "stale_mesh_state"


def test_save_request_requires_all_texture_roles():
    roles = _role_keys()
    roles.pop("emission_map")
    with pytest.raises(errors.TextureSaveError) as raised:
        request.validate_usage(
            {"Component01-1"},
            [{"semantic_key": "Component01-1", "texture_keys": roles}])

    assert raised.value.code == "stale_mesh_state"


def test_save_preparation_keeps_only_bc7_intent_and_target_coverage(
        tmp_path, monkeypatch):
    source = tmp_path / "component01.dds"
    source.write_bytes(dx10_dds(bytes(16)))
    anchor = SimpleNamespace(label="Anchor")
    draw = SimpleNamespace(label="Component01-1")
    group = {}
    parsed = SimpleNamespace(game=SimpleNamespace(game="unknown"), groups=())
    geometry = SimpleNamespace(
        indices=(0, 1, 2), source_uvs=())
    coverage = UVCoverage(
        4, 4, bytearray([1] + [0] * 15), 1, (0, 0, 0, 0), 1, 0)
    monkeypatch.setattr(
        request, "resolved_draws",
        lambda *_args: (parsed, {
            "Anchor": (anchor, group), "Component01-1": (draw, group),
        }))
    monkeypatch.setattr(
        save_coverage, "prepare_uv_geometry", lambda *_args: geometry)
    monkeypatch.setattr(
        save_coverage, "draw_metadata_key", lambda *_args: "Component01::one")
    monkeypatch.setattr(
        save_coverage, "rasterize_geometry", lambda *_args: coverage)

    prepared = save_coverage.prepare_texture_save(
        SimpleNamespace(mod_dir=str(tmp_path)), {"Anchor", "Component01-1"},
        "diffuse::component01.dds", [{
            "semantic_key": "Component01-1", "metadata_key": "Component01::one",
            "adjustment": {"hue": 30},
        }], [{
            "semantic_key": "Anchor", "texture_keys": _role_keys(),
        }, {
            "semantic_key": "Component01-1",
            "texture_keys": _role_keys("diffuse::nested/../component01.dds"),
        }])

    assert list(prepared.mip0_claims) == [1] + [0] * 15
    assert prepared.mip0_affected_blocks == (0,)
    assert prepared.targets[0].semantic_key == "Component01-1"
    assert prepared.targets[0].metadata_key == "Component01::one"
    assert not hasattr(prepared.targets[0], "pixel_coverage")
    assert not hasattr(prepared, "safe_masks")
    assert not hasattr(prepared, "target_pixel_masks")


def test_bc7_single_intent_is_weighted_when_propagated_to_lower_mip(
        tmp_path, monkeypatch):
    source_block = mode6_block()
    source = tmp_path / "component01.dds"
    source.write_bytes(dx10_dds(
        source_block + source_block, width=4, height=4, mip_count=2))
    layout = inspect_dds_layout(source)
    adjustment = prepare_color_adjustment({"brightness": 1.5})
    prepared = _prepared_bc7(
        source, layout, bytearray([1] + [0] * 15),
        (None, adjustment), (0,))
    captured = []

    def capture_target(block, target, *_args):
        captured.append(target)
        return SimpleNamespace(
            block=block, source_error=2, candidate_error=0)

    monkeypatch.setattr(
        bc7_recolor._bc7_codec, "recolor_block", capture_target)
    bc7_recolor._save_bc7_blocks(source.read_bytes(), prepared)

    lower_source = bc7.decode_block(source_block)
    assert len(captured) == 2
    assert captured[1][0][:3] == bc7_recolor._bc7_weighted_single_rgb(
        lower_source[0][:3], adjustment, 1, 4)
    assert captured[1][1][:3] == lower_source[1][:3]
    assert captured[1][0][3] == lower_source[0][3]


def test_bc7_lower_intent_rejects_changed_weight_above_total():
    state = {
        "level": 0, "width": 2, "height": 2, "single": True,
        "changed_counts": (2, 0, 0, 0),
        "total_counts": (1, 1, 1, 1),
    }

    with pytest.raises(errors.TextureSaveError) as raised:
        bc7_recolor._bc7_next_intent_level(state, 1, 1, 2)

    assert raised.value.code == "texture_validation_failed"
    assert raised.value.message == "Changed color intent exceeds total mip weight."


def test_bc7_serial_and_parallel_multi_adjustment_lower_mips_match(
        tmp_path, monkeypatch):
    width, height = 8, 4
    source = tmp_path / "component01.dds"
    blocks = mode6_block() * 3
    original = dx10_dds(blocks, width=width, height=height, mip_count=2)
    source.write_bytes(original)
    layout = inspect_dds_layout(source)
    claims = bytearray(
        1 if x < width // 2 else 2
        for _y in range(height)
        for x in range(width))
    adjustments = (
        None, prepare_color_adjustment({"hue": 60}),
        prepare_color_adjustment({"brightness": 1.5}),
    )
    prepared = _prepared_bc7(
        source, layout, claims, adjustments, (0, 1))
    monkeypatch.setattr(bc7_recolor, "_bc7_worker_count", lambda: 2)
    monkeypatch.setattr(
        bc7_recolor, "ProcessPoolExecutor",
        lambda **_kwargs: _InlineExecutor())

    monkeypatch.setattr(bc7_recolor, "_BC7_PARALLEL_THRESHOLD", 10000)
    serial = bc7_recolor._save_bc7_blocks(original, prepared)
    monkeypatch.setattr(bc7_recolor, "_BC7_PARALLEL_THRESHOLD", 0)
    parallel = bc7_recolor._save_bc7_blocks(original, prepared)

    assert parallel == serial


def test_save_rejects_target_on_different_physical_dds(tmp_path, monkeypatch):
    (tmp_path / "component01.dds").write_bytes(dx10_dds(bytes(16)))
    (tmp_path / "other.dds").write_bytes(dx10_dds(bytes(16)))
    group = {}
    parsed = SimpleNamespace(game=SimpleNamespace(game="unknown"), groups=())
    monkeypatch.setattr(
        request, "resolved_draws",
        lambda *_args: (parsed, {
            "Anchor": (SimpleNamespace(label="Anchor"), group),
            "Target": (SimpleNamespace(label="Target"), group),
        }))

    with pytest.raises(errors.TextureSaveError) as raised:
        save_coverage.prepare_texture_save(
            SimpleNamespace(mod_dir=str(tmp_path)), {"Anchor", "Target"},
            "diffuse::component01.dds", [{
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
        ("diffuse::asset/root/component01.dds", "asset_texture_read_only"),
        ("diffuse::../outside.dds", "texture_not_found"),
    ],
)
def test_save_rejects_asset_and_mod_root_escape_paths(
        tmp_path, texture_key, expected_code):
    result = service.save_texture_color(
        SimpleNamespace(mod_dir=str(tmp_path)), {"Anchor"}, texture_key,
        [{
            "semantic_key": "Anchor", "metadata_key": "Anchor::one",
            "adjustment": {"hue": 30},
        }], [{
            "semantic_key": "Anchor", "texture_keys": _role_keys(texture_key),
        }])

    assert result["code"] == expected_code
    assert not list(tmp_path.glob("*.dds"))


@pytest.mark.parametrize(
    "normal_key", ["normal_map::component01.dds", "normal_map::nested/../component01.dds"])
def test_save_rejects_live_cross_role_physical_usage(
        tmp_path, monkeypatch, normal_key):
    (tmp_path / "component01.dds").write_bytes(dx10_dds(bytes(16)))
    parsed = SimpleNamespace(game=SimpleNamespace(game="unknown"), groups=())
    monkeypatch.setattr(
        request, "resolved_draws",
        lambda *_args: (parsed, {
            "Anchor": (SimpleNamespace(label="Anchor"), {}),
            "Other": (SimpleNamespace(label="Other"), {}),
        }))
    other_keys = _role_keys(None)
    other_keys["normal_map"] = normal_key

    with pytest.raises(errors.TextureSaveError) as raised:
        request.resolve_save_request(
            SimpleNamespace(mod_dir=str(tmp_path)), {"Anchor", "Other"},
            "diffuse::component01.dds", [{
                "semantic_key": "Anchor", "texture_keys": _role_keys(),
            }, {
                "semantic_key": "Other", "texture_keys": other_keys,
            }])

    assert raised.value.code == "cross_role_texture_usage"


def test_save_rejects_authored_inactive_cross_role_variant(tmp_path, monkeypatch):
    (tmp_path / "component01.dds").write_bytes(dx10_dds(bytes(16)))
    selected = DrawCall(
        label="Anchor", count=3, texture_default_file="component01.dds")
    inactive = DrawCall(
        label="Inactive", count=3, texture_default_file="face.dds",
        normal_map_variants=[{
            "conditions": [[{"var": "toggle", "value": "1"}]],
            "file": "component01.dds",
        }])
    selected_group = {"draws": [selected]}
    inactive_group = {"draws": [inactive]}
    parsed = SimpleNamespace(
        game=SimpleNamespace(game="unknown"),
        groups=[selected_group, inactive_group],
    )
    monkeypatch.setattr(
        request, "resolved_draws",
        lambda *_args: (parsed, {
            "Anchor": (selected, selected_group),
            "Inactive": (inactive, inactive_group),
        }))

    with pytest.raises(errors.TextureSaveError) as raised:
        request.resolve_save_request(
            SimpleNamespace(mod_dir=str(tmp_path)), {"Anchor"},
            "diffuse::component01.dds", [{
                "semantic_key": "Anchor", "texture_keys": _role_keys(),
            }])

    assert raised.value.code == "cross_role_texture_usage"
    assert "Normal Map" in raised.value.message


def test_save_rejects_stale_canonical_metadata_key(tmp_path, monkeypatch):
    (tmp_path / "component01.dds").write_bytes(dx10_dds(bytes(16)))
    draw = SimpleNamespace(label="Anchor")
    group = {}
    parsed = SimpleNamespace(game=SimpleNamespace(game="unknown"), groups=())
    monkeypatch.setattr(
        request, "resolved_draws",
        lambda *_args: (parsed, {"Anchor": (draw, group)}))
    monkeypatch.setattr(
        save_coverage, "draw_metadata_key", lambda *_args: "Anchor::actual")

    with pytest.raises(errors.TextureSaveError) as raised:
        save_coverage.prepare_texture_save(
            SimpleNamespace(mod_dir=str(tmp_path)), {"Anchor"},
            "diffuse::component01.dds", [{
                "semantic_key": "Anchor", "metadata_key": "Anchor::stale",
                "adjustment": {"hue": 30},
            }], [{
                "semantic_key": "Anchor", "texture_keys": _role_keys(),
            }])

    assert raised.value.code == "stale_mesh_state"


def test_invalid_bc7_has_the_same_save_error_in_serial_and_parallel_paths(
        tmp_path, monkeypatch):
    source = tmp_path / "component01.dds"
    source.write_bytes(dx10_dds(bytes(16)))
    layout = inspect_dds_layout(source)
    prepared = SimpleNamespace(
        selected_path=str(source), info=layout.info, layout=layout,
        mip0_claims=bytearray([1] * 16),
        intent_adjustments=(None, prepare_color_adjustment({"hue": 30})),
        mip0_affected_blocks=(0,),
    )

    with pytest.raises(errors.TextureSaveError) as serial:
        bc7_recolor._save_bc7_blocks(source.read_bytes(), prepared)

    class InlineExecutor:
        def submit(self, function, argument):
            future = Future()
            future.set_result(function(argument))
            return future

        def shutdown(self, **_kwargs):
            pass

    monkeypatch.setattr(
        bc7_recolor, "ProcessPoolExecutor", lambda **_kwargs: InlineExecutor())
    monkeypatch.setattr(bc7_recolor, "_BC7_PARALLEL_THRESHOLD", 0)

    with pytest.raises(errors.TextureSaveError) as parallel:
        bc7_recolor._save_bc7_blocks(source.read_bytes(), prepared)

    assert serial.value.code == parallel.value.code == "invalid_bc7"
    assert serial.value.message == parallel.value.message == \
        "The texture contains invalid BC7 data."


def test_bc7_representability_gate_keeps_error_details_private(
        tmp_path, monkeypatch):
    source_block = mode6_block()
    source = tmp_path / "component01.dds"
    source.write_bytes(dx10_dds(source_block))
    layout = inspect_dds_layout(source)
    adjustment = prepare_color_adjustment({"hue": 30})
    prepared = SimpleNamespace(
        selected_path=str(source), info=layout.info, layout=layout,
        mip0_claims=bytearray([1] * 16),
        intent_adjustments=(None, adjustment),
        mip0_affected_blocks=(0,))
    monkeypatch.setattr(
        bc7_recolor._bc7_codec, "recolor_block",
        lambda *_args: SimpleNamespace(
            block=source_block, source_error=10, candidate_error=10))

    with pytest.raises(errors.TextureSaveError) as raised:
        bc7_recolor._save_bc7_blocks(source.read_bytes(), prepared)

    assert raised.value.code == "texture_color_not_representable"
    assert raised.value.details == {}


def test_parallel_bc7_worker_failure_is_reported(tmp_path, monkeypatch):
    source = tmp_path / "component01.dds"
    source.write_bytes(dx10_dds(mode6_block()))
    layout = inspect_dds_layout(source)
    prepared = SimpleNamespace(
        selected_path=str(source), info=layout.info, layout=layout,
        mip0_claims=bytearray([1] * 16),
        intent_adjustments=(None, prepare_color_adjustment({"hue": 120})),
        mip0_affected_blocks=(0,),
    )

    class FailingExecutor:
        def __init__(self, **_kwargs):
            self.shutdown_called = False

        def submit(self, *_args):
            future = Future()
            future.set_exception(RuntimeError("synthetic worker failure"))
            return future

        def shutdown(self, **_kwargs):
            self.shutdown_called = True

    executor = FailingExecutor()
    monkeypatch.setattr(
        bc7_recolor, "ProcessPoolExecutor", lambda **_kwargs: executor)
    monkeypatch.setattr(bc7_recolor, "_BC7_PARALLEL_THRESHOLD", 0)
    monkeypatch.setattr(bc7_recolor, "_bc7_worker_count", lambda: 2)

    with pytest.raises(errors.TextureSaveError) as raised:
        bc7_recolor._save_bc7_blocks(source.read_bytes(), prepared)

    assert raised.value.code == "texture_processing_failed"
    assert executor.shutdown_called


def test_save_rejects_non_bc7_dds(tmp_path):
    source = tmp_path / "component01.dds"
    source.write_bytes(dx10_dds(bytes(8), dxgi_format=71))

    with pytest.raises(errors.TextureSaveError) as raised:
        request.inspect_save_texture(str(source))

    assert raised.value.code == "unsupported_texture_format"

"""Atomic BC7 Save to Texture regressions."""

import struct
from concurrent.futures import Future
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.mods.texture_save import bc7_recolor, coverage as save_coverage, errors, progress, request, service, transaction
from core.geometry.draw_call import DrawCall
from core.textures import bc7
from core.textures.color_adjustment import (
    apply_prepared_color_adjustment, apply_prepared_color_u8, prepare_color_adjustment,
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


def _color_mode_block(mode):
    subsets = 3 if mode in {0, 2} else 2
    partition_bits = 4 if mode == 0 else 6
    endpoint_bits = {0: 4, 1: 6, 2: 5, 3: 7}[mode]
    index_bits = 3 if mode in {0, 1} else 2
    partition = 0 if mode == 0 else 13
    endpoints = []
    for endpoint in range(subsets * 2):
        endpoints.append(tuple(
            (8 + endpoint * 7 + channel * 5)
            & ((1 << endpoint_bits) - 1)
            for channel in range(3)))
    bits = 1 << mode
    bits = bc7.set_bits(bits, mode + 1, partition_bits, partition)
    endpoint_start = mode + 1 + partition_bits
    for channel in range(3):
        for endpoint, values in enumerate(endpoints):
            bits = bc7.set_bits(
                bits,
                endpoint_start
                + channel * subsets * 2 * endpoint_bits
                + endpoint * endpoint_bits,
                endpoint_bits, values[channel])
    if mode == 0:
        pbits = tuple(endpoint & 1 for endpoint in range(subsets * 2))
    elif mode == 1:
        pbits = (0, 1)
    elif mode == 3:
        pbits = (0, 1, 1, 0)
    else:
        pbits = ()
    pbit_start = endpoint_start + 3 * subsets * 2 * endpoint_bits
    for index, pbit in enumerate(pbits):
        bits = bc7.set_bits(bits, pbit_start + index, 1, pbit)
    indices = [(index + mode) % (1 << index_bits) for index in range(16)]
    anchors = bc7.anchors_for_partition(subsets, partition)
    for anchor in anchors:
        indices[anchor] = 1
    index_start = pbit_start + len(pbits)
    for pixel, index in enumerate(indices):
        width = index_bits - (pixel in anchors)
        bits = bc7.set_bits(bits, index_start, width, index)
        index_start += width
    assert index_start == 128
    return bits.to_bytes(16, "little")


def _mode5_block():
    bits = 1 << 5
    bits = bc7.set_bits(bits, 6, 2, 1)
    start = 8
    for channel, precision in enumerate((7, 7, 7, 8)):
        for endpoint in range(2):
            bits = bc7.set_bits(
                bits, start, precision,
                (channel * 7 + endpoint * 15 + 3)
                & ((1 << precision) - 1))
            start += precision
    first = [0, 1, 2, 3] * 4
    second = [0, 1, 2, 3] * 4
    bits = bc7.set_bits(bits, start, 1, first[0])
    start += 1
    for value in first[1:]:
        bits = bc7.set_bits(bits, start, 2, value)
        start += 2
    bits = bc7.set_bits(bits, start, 1, second[0])
    start += 1
    for value in second[1:]:
        bits = bc7.set_bits(bits, start, 2, value)
        start += 2
    assert start == 128
    return bits.to_bytes(16, "little")


def _separate_block(mode, rotation=1):
    bits = 1 << mode
    start = mode + 1
    bits = bc7.set_bits(bits, start, 2, rotation)
    start += 2
    index_mode = 1 if mode == 4 and rotation % 2 else 0
    if mode == 4:
        bits = bc7.set_bits(bits, start, 1, index_mode)
        start += 1
    precisions = (5, 5, 5, 6) if mode == 4 else (7, 7, 7, 8)
    for channel, precision in enumerate(precisions):
        for endpoint in range(2):
            bits = bc7.set_bits(
                bits, start, precision,
                (channel * 7 + endpoint * 15 + 3)
                & ((1 << precision) - 1))
            start += precision
    first = [0, 1, 2, 3] * 4
    second = ([0, 1, 2, 3, 4, 5, 6, 7] * 2
              if mode == 4 else [0, 1, 2, 3] * 4)
    bits = bc7.set_bits(bits, start, 1, first[0])
    start += 1
    for value in first[1:]:
        bits = bc7.set_bits(bits, start, 2, value)
        start += 2
    second_precision = 3 if mode == 4 else 2
    bits = bc7.set_bits(bits, start, second_precision - 1, second[0])
    start += second_precision - 1
    for value in second[1:]:
        bits = bc7.set_bits(bits, start, second_precision, value)
        start += second_precision
    assert start == 128
    return bits.to_bytes(16, "little")


def _mode7_block():
    partition = 13
    anchor = bc7._PARTITION_2_ANCHORS[partition]
    bits = bc7.set_bits(1 << 7, 8, 6, partition)
    endpoints = (
        (3, 6, 9, 4), (22, 18, 25, 30),
        (8, 15, 5, 20), (28, 26, 30, 31),
    )
    start = 14
    for channel in range(4):
        for endpoint in endpoints:
            bits = bc7.set_bits(bits, start, 5, endpoint[channel])
            start += 5
    for pbit in (0, 1, 1, 0):
        bits = bc7.set_bits(bits, start, 1, pbit)
        start += 1
    indices = [0, 1, 2, 3] * 4
    indices[0] = 0
    indices[anchor] = 1
    for pixel, index in enumerate(indices):
        width = 1 if pixel in {0, anchor} else 2
        bits = bc7.set_bits(bits, start, width, index)
        start += width
    assert start == 128
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
            "cleared": ["Body::one"], "preserved": [], "failed": [],
        })

    result = service.save_texture_color(
        SimpleNamespace(mod_dir=str(tmp_path)), {}, {"Body-1"},
        "diffuse::body.dds", [{
            "semantic_key": "Body-1", "metadata_key": "Body::one",
            "adjustment": {"hue": 30},
        }], [{"semantic_key": "Body-1", "texture_keys": _role_keys()}])

    assert result["status"] == "ok"
    assert result["texture"] == {"file": "body.dds"}
    assert "patched" not in result
    assert "diagnostics" not in result
    assert cleanup == [(str(tmp_path), {"Body::one": {"hue": 30}})]
    backups = list(tmp_path.glob("body-??????????????.dds"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == original


def test_save_progress_reports_stages_and_ignores_callback_errors(
        tmp_path, monkeypatch):
    source = tmp_path / "body.dds"
    original = _dx10_dds(bytes(16))
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
        SimpleNamespace(mod_dir=str(tmp_path)), {}, {"Anchor"},
        "diffuse::body.dds", [], [], progress_callback=callback)

    assert result["status"] == "ok"
    assert [event["stage"] for event in events] == [
        "preparing", "reading", "processing", "writing", "complete",
    ]
    assert all("request_id" not in event for event in events)


def test_committed_cleanup_uses_only_saved_targets(monkeypatch):
    compared = []
    monkeypatch.setattr(
        service.metadata, "clear_mesh_color_adjustments_if_unchanged",
        lambda folder, expected: compared.append((folder, expected)) or {
            "cleared": ["Body::one"], "preserved": [], "failed": [],
        })

    receipt, failed = service._clear_committed_color_adjustments(
        "mod", [
            {"semantic_key": "Body-1", "metadata_key": "Body::one",
             "adjustment": {"hue": 30}},
            {"semantic_key": "Body-2", "metadata_key": "Body::two",
             "adjustment": {"hue": 45}},
        ], [{"semantic_key": "Body-1", "metadata_key": "Body::one"}])

    assert compared == [("mod", {"Body::one": {"hue": 30}})]
    assert receipt == {
        "cleared": ["Body::one"], "preserved": [], "failed": [],
    }
    assert failed is False


def test_committed_cleanup_reports_structured_status(monkeypatch):
    monkeypatch.setattr(
        service.metadata, "clear_mesh_color_adjustments_if_unchanged",
        lambda *_args: {
            "cleared": ["Body::one"], "preserved": ["Body::two"],
            "failed": [],
        })

    receipt, failed = service._clear_committed_color_adjustments(
        "mod", [
            {"semantic_key": "Body-1", "metadata_key": "Body::one",
             "adjustment": {"hue": 30}},
            {"semantic_key": "Body-2", "metadata_key": "Body::two",
             "adjustment": {"hue": 45}},
        ], [
            {"semantic_key": "Body-1", "metadata_key": "Body::one"},
            {"semantic_key": "Body-2", "metadata_key": "Body::two"},
        ])

    assert receipt == {
        "cleared": ["Body::one"], "preserved": ["Body::two"],
        "failed": [],
    }
    assert failed is False


@pytest.mark.parametrize(
    ("saved_meshes", "expected_failed"),
    [
        ([{"semantic_key": "Body-1", "metadata_key": "Body::missing"}],
         ["Body::missing"]),
        ([
            {"semantic_key": "Body-1", "metadata_key": "Shared::one"},
            {"semantic_key": "Body-2", "metadata_key": "Shared::one"},
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
            {"semantic_key": "Body-1", "metadata_key": "Body::one",
             "adjustment": {"hue": 30}},
            {"semantic_key": "Body-2", "metadata_key": "Shared::one",
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
            "semantic_key": "Body-1", "metadata_key": "Body::one",
            "adjustment": {"hue": 30},
        }], [{"semantic_key": "Body-1", "metadata_key": "Body::one"}])

    assert receipt == {"cleared": [], "preserved": [],
                       "failed": ["Body::one"]}
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
    source = tmp_path / "body.dds"
    original = _dx10_dds(bytes(16))
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
        service, "prepare_texture_save",
        lambda *args, **kwargs: prepared)
    monkeypatch.setattr(
        service, "_save_bc7_blocks",
        lambda *args, **kwargs: candidate)

    result = service.save_texture_color(
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
        SimpleNamespace(mod_dir=str(tmp_path)), {}, {"Anchor"},
        "diffuse::body.dds", [], [])

    assert result["status"] == "ok"
    assert source.read_bytes() == bytes(candidate)


def test_save_marks_cleanup_uncertain_after_committed_cleanup_raises(
        tmp_path, monkeypatch):
    source = tmp_path / "body.dds"
    original = _dx10_dds(bytes(16))
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
        SimpleNamespace(mod_dir=str(tmp_path)), {}, {"Anchor"},
        "diffuse::body.dds", [{
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
    source = tmp_path / "body.dds"
    source.write_bytes(b"original")
    monkeypatch.setattr(
        transaction, "datetime",
        SimpleNamespace(now=lambda: datetime(2026, 9, 1, 15, 22, 30)))

    first = transaction._write_backup(str(source), b"first")
    second = transaction._write_backup(str(source), b"second")

    assert first != second
    assert (tmp_path / "body-20260901152230.dds").read_bytes() == b"first"
    assert (tmp_path / "body-20260901152231.dds").read_bytes() == b"second"


def test_save_request_rejects_legacy_diffuse_alias():
    with pytest.raises(errors.TextureSaveError) as raised:
        request.validate_usage(
            {"Body-1"},
            [{"semantic_key": "Body-1", "tex_key": "diffuse::body.dds"}])

    assert raised.value.code == "stale_mesh_state"


def test_save_request_requires_all_texture_roles():
    roles = _role_keys()
    roles.pop("emission_map")
    with pytest.raises(errors.TextureSaveError) as raised:
        request.validate_usage(
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
        request, "resolved_draws",
        lambda *_args: (parsed, {
            "Anchor": (anchor, group), "Body-1": (draw, group),
        }))
    monkeypatch.setattr(
        save_coverage, "prepare_uv_geometry", lambda *_args: geometry)
    monkeypatch.setattr(
        save_coverage, "draw_metadata_key", lambda *_args: "Body::one")
    monkeypatch.setattr(
        save_coverage, "rasterize_geometry", lambda *_args: coverage)

    prepared = save_coverage.prepare_texture_save(
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
        request, "resolved_draws",
        lambda *_args: (parsed, {
            "Anchor": (SimpleNamespace(label="Anchor"), group),
            "Target": (SimpleNamespace(label="Target"), group),
        }))

    with pytest.raises(errors.TextureSaveError) as raised:
        save_coverage.prepare_texture_save(
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
    result = service.save_texture_color(
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
        request, "resolved_draws",
        lambda *_args: (parsed, {
            "Anchor": (SimpleNamespace(label="Anchor"), {}),
            "Other": (SimpleNamespace(label="Other"), {}),
        }))
    other_keys = _role_keys(None)
    other_keys["normal_map"] = normal_key

    with pytest.raises(errors.TextureSaveError) as raised:
        request.resolve_save_request(
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
        request, "resolved_draws",
        lambda *_args: (parsed, {
            "Anchor": (selected, selected_group),
            "Inactive": (inactive, inactive_group),
        }))

    with pytest.raises(errors.TextureSaveError) as raised:
        request.resolve_save_request(
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
        request, "resolved_draws",
        lambda *_args: (parsed, {"Anchor": (draw, group)}))
    monkeypatch.setattr(
        save_coverage, "draw_metadata_key", lambda *_args: "Anchor::actual")

    with pytest.raises(errors.TextureSaveError) as raised:
        save_coverage.prepare_texture_save(
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
    return bc7_recolor._bc7_intent_level(prepared)


def _bc7_lower_single_state(
        width, height, changed_counts, total_counts, level=1):
    return {
        "level": level,
        "width": width,
        "height": height,
        "single": True,
        "changed_counts": changed_counts,
        "total_counts": total_counts,
    }


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
        bc7_recolor._bc7_target_block_pixels(
            source_block, mip, 0, state, (None, adjustment))

    assert (valid_width, valid_height) == (width, height)
    expected_rgb = {
        (x, y): apply_prepared_color_u8(
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


@pytest.mark.parametrize(
    "adjustment",
    [
        {"hue": 30},
        {"saturation": 0.25},
        {"brightness": 1.5},
        {"contrast": 1.75},
        {"red": 0.5},
        {"green": 1.5},
        {"blue": 2.0},
        {"tint": "#4080c0"},
        {
            "hue": 30, "saturation": 0.5, "brightness": 1.5,
            "contrast": 1.25, "red": 0.75, "green": 1.5,
            "blue": 0.5, "tint": "#d08040",
        },
    ],
)
@pytest.mark.parametrize(
    ("valid_width", "valid_height"),
    [(4, 4), (3, 4), (4, 3), (1, 2)],
)
def test_shared_single_intent_target_matches_parent_target(
        adjustment, valid_width, valid_height):
    source_block = _mode6_block()
    source_pixels = bc7.decode_block(source_block)
    prepared_adjustment = prepare_color_adjustment(adjustment)
    state = _bc7_block_state(
        valid_width, valid_height,
        bytearray([1] * (valid_width * valid_height)),
        (None, prepared_adjustment))
    mip = SimpleNamespace(
        width=valid_width, height=valid_height, units_x=1)

    _source, expected, expected_width, expected_height = \
        bc7_recolor._bc7_target_block_pixels(
            source_block, mip, 0, state, (None, prepared_adjustment))
    actual = bc7_recolor._bc7_single_adjustment_target_pixels(
        source_pixels, prepared_adjustment, valid_width, valid_height)

    assert (expected_width, expected_height) == \
        (valid_width, valid_height)
    assert actual == expected


@pytest.mark.parametrize(
    "counts",
    [(0, 1), (1, 1), (1, 2), (3, 4), (7, 16), (31, 64),
     (255, 1024), (1023, 4096)],
)
@pytest.mark.parametrize(
    "adjustment",
    [
        {"hue": 30},
        {"saturation": 0.25},
        {"brightness": 1.5},
        {"contrast": 1.75},
        {"red": 0.5, "green": 1.5, "blue": 2.0},
        {"tint": "#4080c0"},
        {
            "hue": 30, "saturation": 0.5, "brightness": 1.5,
            "contrast": 1.25,
        },
        {
            "hue": 30, "saturation": 0.5, "brightness": 1.5,
            "contrast": 1.25, "red": 0.75, "green": 1.5,
            "blue": 0.5, "tint": "#d08040",
        },
    ],
)
def test_weighted_single_rgb_matches_exact_reference_matrix(
        counts, adjustment):
    source_rgb = (37, 101, 203)
    changed_count, total_count = counts
    prepared_adjustment = prepare_color_adjustment(adjustment)
    actual = bc7_recolor._bc7_weighted_single_rgb(
        source_rgb, prepared_adjustment, changed_count, total_count)

    if not changed_count or not total_count:
        expected = source_rgb
    else:
        base = tuple(channel / 255.0 for channel in source_rgb)
        adjusted = apply_prepared_color_adjustment(base, prepared_adjustment)
        expected = tuple(min(255, max(0, round(value * 255.0)))
                         for value in (
                             (base[channel] * (total_count - changed_count)
                              + adjusted[channel] * changed_count)
                             / total_count
                             for channel in range(3)))
    assert actual == expected


def test_weighted_single_target_validates_count_parity_and_preserves_padding():
    source_pixels = bc7.decode_block(_mode6_block())
    adjustment = prepare_color_adjustment({"brightness": 1.5})

    with pytest.raises(errors.TextureSaveError) as raised:
        bc7_recolor._bc7_weighted_single_target_pixels(
            source_pixels, adjustment, (1, 2), (1,), 1, 1)
    assert raised.value.code == "texture_validation_failed"

    target = bc7_recolor._bc7_weighted_single_target_pixels(
        source_pixels, adjustment, (0, 1), (1, 1), 1, 2)
    assert target[0] == source_pixels[0]
    assert target[4][:3] != source_pixels[4][:3]
    assert target[4][3] == source_pixels[4][3]
    for local in range(16):
        if local not in {0, 4}:
            assert target[local] == source_pixels[local]


def test_weighted_single_rgb_keeps_source_for_zero_total_weight():
    adjustment = prepare_color_adjustment({"tint": "#ffdd00"})
    assert bc7_recolor._bc7_weighted_single_rgb(
        (37, 101, 203), adjustment, 0, 0) == (37, 101, 203)


def test_weighted_single_full_weights_do_not_use_mip0_shortcut(monkeypatch):
    source_block = _mode6_block()
    source_pixels = bc7.decode_block(source_block)
    adjustment = prepare_color_adjustment({"hue": 120})
    job = bc7_recolor._BC7WeightedSingleIntentJob(
        start=0, source_block=source_block, adjustment=adjustment,
        changed_counts=(1,) * 16, total_counts=(1,) * 16,
        valid_width=4, valid_height=4)

    def unexpected_shortcut(*_args, **_kwargs):
        raise AssertionError("weighted jobs must not use the mip-0 shortcut")

    monkeypatch.setattr(
        bc7_recolor, "_bc7_single_adjustment_target_pixels",
        unexpected_shortcut)
    real_decode = bc7_recolor._bc7_codec.decode_block
    decode_calls = []

    def counting_decode(block):
        decode_calls.append(block)
        return real_decode(block)

    monkeypatch.setattr(
        bc7_recolor._bc7_codec, "decode_block", counting_decode)
    result = bc7_recolor._recolor_bc7_chunk((job,))[0]

    assert result.error is None
    assert len(decode_calls) == 1


@pytest.mark.parametrize(
    ("block", "valid_width", "valid_height"),
    [
        (_separate_block(4), 4, 4),
        (_mode5_block(), 4, 4),
        (_mode6_block(), 4, 4),
        (_mode7_block(), 4, 4),
        (_separate_block(4), 3, 2),
        (_mode5_block(), 3, 2),
        (_mode6_block(), 3, 2),
        (_mode7_block(), 3, 2),
        (_separate_block(4), 1, 1),
        (_mode5_block(), 1, 1),
        (_mode6_block(), 1, 1),
        (_mode7_block(), 1, 1),
    ],
)
def test_weighted_single_worker_matches_parent_prepared_worker(
        block, valid_width, valid_height):
    source = bytearray(block)
    mip = SimpleNamespace(
        offset=0, bytes_per_unit=16,
        width=valid_width, height=valid_height, units_x=1)
    adjustment = prepare_color_adjustment({"hue": 120, "brightness": 1.5})
    area = valid_width * valid_height
    state = _bc7_lower_single_state(
        valid_width, valid_height, (1,) * area, (2,) * area)
    block_intent = bc7_recolor._bc7_block_intent_info(state, mip, 0)

    legacy_job = bc7_recolor._prepare_bc7_block_job(
        source, mip, 0, state, (None, adjustment), block_intent)
    weighted_job = bc7_recolor._prepare_bc7_weighted_single_intent_job(
        source, mip, 0, state, (None, adjustment), block_intent)
    legacy_result = bc7_recolor._recolor_bc7_chunk((legacy_job,))[0]
    weighted_result = bc7_recolor._recolor_bc7_chunk((weighted_job,))[0]

    assert isinstance(weighted_job, bc7_recolor._BC7WeightedSingleIntentJob)
    assert weighted_result == legacy_result


def test_weighted_single_worker_reports_validation_errors():
    job = bc7_recolor._BC7WeightedSingleIntentJob(
        start=0, source_block=_mode6_block(),
        adjustment=prepare_color_adjustment({"hue": 30}),
        changed_counts=(2,), total_counts=(1,),
        valid_width=1, valid_height=1)

    result = bc7_recolor._recolor_bc7_chunk((job,))[0]

    assert result.error_code == "texture_validation_failed"
    assert result.error == "Changed color intent exceeds total mip weight."


@pytest.mark.parametrize(
    ("block", "valid_width", "valid_height"),
    [
        (_color_mode_block(3), 4, 4),
        (_mode5_block(), 4, 4),
        (_mode6_block(), 4, 4),
        (_color_mode_block(3), 3, 2),
        (_mode5_block(), 3, 2),
        (_mode6_block(), 3, 2),
    ],
)
def test_compact_single_intent_worker_matches_parent_prepared_worker(
        block, valid_width, valid_height):
    source = bytearray(block)
    mip = SimpleNamespace(
        offset=0, bytes_per_unit=16,
        width=valid_width, height=valid_height, units_x=1)
    adjustment = prepare_color_adjustment({"hue": 120, "brightness": 1.5})
    state = _bc7_block_state(
        valid_width, valid_height,
        bytearray([1] * (valid_width * valid_height)),
                             (None, adjustment))
    block_intent = bc7_recolor._bc7_block_intent_info(state, mip, 0)

    legacy_job = bc7_recolor._prepare_bc7_block_job(
        source, mip, 0, state, (None, adjustment), block_intent)
    compact_job = bc7_recolor._prepare_bc7_single_intent_job(
        source, mip, 0, (None, adjustment), block_intent)
    legacy_result = bc7_recolor._recolor_bc7_chunk((legacy_job,))[0]
    compact_result = bc7_recolor._recolor_bc7_chunk((compact_job,))[0]

    assert isinstance(compact_job, bc7_recolor._BC7SingleIntentJob)
    assert compact_result == legacy_result


def test_compact_single_intent_worker_decodes_once_and_preserves_source_pixels(
        monkeypatch):
    source_block = _mode6_block()
    adjustment = prepare_color_adjustment({"brightness": 1.5})
    job = bc7_recolor._BC7SingleIntentJob(
        start=16, source_block=source_block, adjustment=adjustment,
        valid_width=3, valid_height=2)
    real_decode = bc7_recolor._bc7_codec.decode_block
    decode_calls = []

    def counting_decode(block):
        decode_calls.append(block)
        return real_decode(block)

    monkeypatch.setattr(
        bc7_recolor._bc7_codec, "decode_block", counting_decode)
    result = bc7_recolor._recolor_bc7_chunk((job,))[0]

    assert len(decode_calls) == 1
    assert result.error is None
    assert result.start == 16


@pytest.mark.parametrize(
    ("claims", "level", "expected_kind"),
    [
        (bytearray([1] * 16), 0, "compact"),
        (bytearray([1] * 15 + [2]), 0, "legacy"),
        (bytearray([0] * 16), 0, "legacy"),
        (None, 1, "weighted"),
    ],
)
def test_parallel_job_selection_chooses_compact_single_intent_paths(
        claims, level, expected_kind, monkeypatch):
    mip = SimpleNamespace(
        offset=0, bytes_per_unit=16, width=4, height=4, units_x=1)
    adjustment = prepare_color_adjustment({"hue": 30})
    if level == 0:
        state = _bc7_block_state(
            4, 4, claims, (None, adjustment, adjustment))
    else:
        state = {
            "level": 1, "single": True,
            "width": 4, "height": 4,
            "changed_counts": [1] * 16,
            "total_counts": [1] * 16,
        }
    selected = []
    monkeypatch.setattr(
        bc7_recolor, "_prepare_bc7_single_intent_job",
        lambda *args: selected.append("compact") or "compact")
    monkeypatch.setattr(
        bc7_recolor, "_prepare_bc7_weighted_single_intent_job",
        lambda *args: selected.append("weighted") or "weighted")
    monkeypatch.setattr(
        bc7_recolor, "_prepare_bc7_block_job",
        lambda *args: selected.append("legacy") or "legacy")

    jobs = list(bc7_recolor._iter_bc7_jobs(
        b"", mip, (0,), state, (None, adjustment),
        defer_single_intent=True))

    assert jobs == [expected_kind]
    assert selected == [expected_kind]


def test_parallel_lower_mip_multi_intent_keeps_parent_prepared_job(
        monkeypatch):
    mip = SimpleNamespace(
        offset=0, bytes_per_unit=16, width=4, height=4, units_x=1)
    adjustment = prepare_color_adjustment({"hue": 30})
    state = {
        "level": 1, "width": 4, "height": 4, "single": False,
        "class_count": 3,
        "counts": ([0] * 16, [1] * 8 + [0] * 8,
                    [0] * 8 + [1] * 8),
    }
    selected = []
    monkeypatch.setattr(
        bc7_recolor, "_prepare_bc7_weighted_single_intent_job",
        lambda *args: selected.append("weighted") or "weighted")
    monkeypatch.setattr(
        bc7_recolor, "_prepare_bc7_block_job",
        lambda *args: selected.append("legacy") or "legacy")

    jobs = list(bc7_recolor._iter_bc7_jobs(
        b"", mip, (0,), state,
        (None, adjustment, adjustment),
        defer_single_intent=True))

    assert jobs == ["legacy"]
    assert selected == ["legacy"]


@pytest.mark.parametrize("intent_class", [1, 2, 3])
def test_parallel_lower_mip_single_class_uses_that_weighted_adjustment(
        intent_class):
    source = bytearray(_mode6_block())
    mip = SimpleNamespace(
        offset=0, bytes_per_unit=16, width=4, height=4, units_x=1)
    adjustments = tuple(
        [None] + [prepare_color_adjustment({"hue": 30 * index})
                  for index in range(1, 4)])
    counts = [[0] * 16 for _ in range(4)]
    counts[0] = [3] * 16
    counts[intent_class] = [2] * 16
    state = {
        "level": 1, "width": 4, "height": 4, "single": False,
        "class_count": 4, "counts": tuple(counts),
    }
    block_intent = bc7_recolor._bc7_block_intent_info(state, mip, 0)

    job = bc7_recolor._prepare_bc7_parallel_job(
        source, mip, 0, state, adjustments, block_intent)

    assert isinstance(job, bc7_recolor._BC7WeightedSingleIntentJob)
    assert job.adjustment is adjustments[intent_class]
    assert job.changed_counts == (2,) * 16
    assert job.total_counts == (5,) * 16


@pytest.mark.parametrize(
    ("source_rgb", "changed_count", "total_count"),
    [((0, 0, 0), 1, 2), ((37, 101, 203), 3, 7),
     ((255, 128, 1), 1023, 4096)],
)
@pytest.mark.parametrize("intent_class", [1, 2, 3])
@pytest.mark.parametrize(
    "adjustment",
    [{"hue": 30}, {"brightness": 1.5, "contrast": 1.25},
     {"red": 0.5, "green": 1.5, "blue": 2.0, "tint": "#4080c0"}],
)
def test_lower_mip_single_class_weighted_rgb_matches_generic_intent(
        source_rgb, changed_count, total_count, intent_class, adjustment):
    prepared_adjustment = prepare_color_adjustment(adjustment)
    counts = [[0] for _ in range(4)]
    counts[0][0] = total_count - changed_count
    counts[intent_class][0] = changed_count
    state = {
        "level": 1, "width": 1, "height": 1, "single": False,
        "class_count": 4, "counts": tuple(counts),
    }

    generic = bc7_recolor._bc7_intent_rgb(
        source_rgb, state, 0,
        (None, prepared_adjustment, prepared_adjustment, prepared_adjustment))
    weighted = bc7_recolor._bc7_weighted_single_rgb(
        source_rgb, prepared_adjustment, changed_count, total_count)

    assert weighted == generic


@pytest.mark.parametrize(
    ("block", "valid_width", "valid_height"),
    [(_separate_block(4), 4, 4), (_mode5_block(), 3, 4),
     (_mode6_block(), 4, 3), (_mode7_block(), 3, 2),
     (_separate_block(4), 1, 1)],
)
def test_lower_mip_weighted_multi_class_worker_matches_parent_prepared_worker(
        block, valid_width, valid_height):
    source = bytearray(block)
    mip = SimpleNamespace(
        offset=0, bytes_per_unit=16, width=valid_width, height=valid_height,
        units_x=1)
    area = valid_width * valid_height
    adjustments = (None, prepare_color_adjustment({"hue": 30}),
                   prepare_color_adjustment({"hue": 120}))
    state = {
        "level": 1, "width": valid_width, "height": valid_height,
        "single": False, "class_count": 3,
        "counts": ([0] * area, [0] * area, [1] * area),
    }
    block_intent = bc7_recolor._bc7_block_intent_info(state, mip, 0)

    legacy_job = bc7_recolor._prepare_bc7_block_job(
        source, mip, 0, state, adjustments, block_intent)
    weighted_job = bc7_recolor._prepare_bc7_weighted_single_intent_job(
        source, mip, 0, state, adjustments, block_intent)
    legacy_result = bc7_recolor._recolor_bc7_chunk((legacy_job,))[0]
    weighted_result = bc7_recolor._recolor_bc7_chunk((weighted_job,))[0]

    assert weighted_job.adjustment is adjustments[2]
    assert weighted_result == legacy_result


def test_lower_mip_weighted_multi_class_job_preserves_wide_counts():
    mip = SimpleNamespace(
        offset=0, bytes_per_unit=16, width=1, height=1, units_x=1)
    state = {
        "level": 1, "width": 1, "height": 1, "single": False,
        "class_count": 3,
        "counts": ([65535], [0], [1]),
    }
    block_intent = bc7_recolor._bc7_block_intent_info(state, mip, 0)

    changed, total, valid_width, valid_height = \
        bc7_recolor._bc7_single_weighted_block_counts(
            state, mip, 0, block_intent)

    assert (changed, total, valid_width, valid_height) == ((1,), (65536,), 1, 1)


@pytest.mark.parametrize(
    "state",
    [{
        "level": 1, "width": 4, "height": 4, "single": False,
        "class_count": 3, "counts": ([0] * 16, [1] * 15, [0] * 16),
    }, {
        "level": 1, "width": 4, "height": 4, "single": False,
        "class_count": 2, "counts": ([0] * 16, [1] * 16, [0] * 16),
    }, {
        "level": 1, "width": 4, "height": 4, "single": False,
        "class_count": 3, "counts": ([0] * 16, [-1] * 16, [0] * 16),
    }],
)
def test_lower_mip_malformed_multi_class_counts_are_stable(state):
    mip = SimpleNamespace(
        offset=0, bytes_per_unit=16, width=4, height=4, units_x=1)

    with pytest.raises(errors.TextureSaveError) as raised:
        bc7_recolor._bc7_block_intent_info(state, mip, 0)

    assert raised.value.code == "texture_validation_failed"


def test_lower_mip_weighted_job_rejects_invalid_adjustment_class():
    mip = SimpleNamespace(
        offset=0, bytes_per_unit=16, width=4, height=4, units_x=1)
    state = {
        "level": 1, "width": 4, "height": 4, "single": False,
        "class_count": 3,
        "counts": ([0] * 16, [1] * 16, [0] * 16),
    }
    block_intent = SimpleNamespace(classes=(3,))

    with pytest.raises(errors.TextureSaveError) as raised:
        bc7_recolor._prepare_bc7_weighted_single_intent_job(
            bytearray(_mode6_block()), mip, 0, state,
            (None, prepare_color_adjustment({"hue": 30})), block_intent)

    assert raised.value.code == "texture_validation_failed"


def test_worker_chunk_accepts_compact_and_parent_prepared_jobs_in_order():
    source_block = _mode6_block()
    mip = SimpleNamespace(
        offset=0, bytes_per_unit=16, width=4, height=4, units_x=1)
    adjustment = prepare_color_adjustment({"hue": 30})
    state = _bc7_block_state(4, 4, bytearray([1] * 16),
                             (None, adjustment))
    block_intent = bc7_recolor._bc7_block_intent_info(state, mip, 0)
    compact_job = bc7_recolor._prepare_bc7_single_intent_job(
        bytearray(source_block), mip, 0, (None, adjustment), block_intent)
    weighted_job = bc7_recolor._BC7WeightedSingleIntentJob(
        start=16, source_block=source_block, adjustment=adjustment,
        changed_counts=(1,) * 16, total_counts=(1,) * 16,
        valid_width=4, valid_height=4)
    legacy_job = bc7_recolor._prepare_bc7_block_job(
        bytearray(source_block), mip, 0, state, (None, adjustment),
        block_intent)
    legacy_job = bc7_recolor._BC7BlockJob(
        start=32, source_block=legacy_job.source_block,
        source_pixels=legacy_job.source_pixels,
        target_pixels=legacy_job.target_pixels,
        valid_width=legacy_job.valid_width,
        valid_height=legacy_job.valid_height)

    results = bc7_recolor._recolor_bc7_chunk(
        (compact_job, weighted_job, legacy_job))

    assert [result.start for result in results] == [0, 16, 32]
    assert results[0].block == results[1].block
    assert results[1].block == results[2].block
    assert results[0].source_error == results[1].source_error == \
        results[2].source_error
    assert results[0].candidate_error == results[1].candidate_error == \
        results[2].candidate_error


def test_invalid_bc7_has_the_same_save_error_in_serial_and_parallel_paths(
        tmp_path, monkeypatch):
    source = tmp_path / "body.dds"
    source.write_bytes(_dx10_dds(bytes(16)))
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
        bc7_recolor._bc7_target_block_pixels(
            source_block, mip, 0, state, adjustments)

    assert (valid_width, valid_height) == (4, 4)
    for pixel, claim in enumerate(claims):
        expected = bc7_recolor._bc7_intent_rgb(
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
        service, "prepare_texture_save",
        lambda *args, **kwargs: prepared)

    result = service.save_texture_color(
        SimpleNamespace(mod_dir=str(tmp_path)), {}, {"Anchor"},
        "diffuse::body.dds", [], [])

    assert result["status"] == "ok"
    candidate = source.read_bytes()
    assert candidate[layout.mips[0].offset:layout.mips[0].offset + 16] != first
    assert candidate[layout.mips[0].offset + 16:
                     layout.mips[0].offset + 32] == second
    assert inspect_dds_layout(source) == layout
    source_pixels = bc7.decode_block(first)
    candidate_pixels = bc7.decode_block(candidate[layout.mips[0].offset:
                                                    layout.mips[0].offset + 16])
    assert [pixel[3] for pixel in candidate_pixels] == [
        pixel[3] for pixel in source_pixels]


def test_bc7_representability_gate_keeps_error_details_private(
        tmp_path, monkeypatch):
    source_block = _mode6_block()
    source = tmp_path / "body.dds"
    source.write_bytes(_dx10_dds(source_block))
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


def test_parallel_bc7_save_matches_serial_bytes(tmp_path, monkeypatch):
    blocks = b"".join(_mode6_block(((20 + index, 110 + index),
                                      (40 + index, 140 + index),
                                      (60 + index, 170 + index)))
                       for index in range(7))
    original = _dx10_dds(blocks, width=28, height=4)
    source = tmp_path / "body.dds"
    source.write_bytes(original)
    layout = inspect_dds_layout(source)
    prepared = SimpleNamespace(
        selected_path=str(source), info=layout.info, layout=layout,
        mip0_claims=bytearray([1] * (28 * 4)),
        intent_adjustments=(None, prepare_color_adjustment({"hue": 120})),
        mip0_affected_blocks=tuple(range(7)),
    )
    monkeypatch.setattr(bc7_recolor, "_bc7_worker_count", lambda: 2)
    monkeypatch.setattr(bc7_recolor, "_BC7_CHUNK_SIZE", 2)
    monkeypatch.setattr(progress, "SAVE_PROGRESS_INTERVAL", 0)
    monkeypatch.setattr(bc7_recolor, "_BC7_PARALLEL_THRESHOLD", 10000)
    serial_events = []
    serial_progress = progress.SaveProgressReporter(
        serial_events.append)
    serial_bytes = bc7_recolor._save_bc7_blocks(
        original, prepared, progress_reporter=serial_progress)

    monkeypatch.setattr(bc7_recolor, "_BC7_PARALLEL_THRESHOLD", 0)
    parallel_events = []
    parallel_progress = progress.SaveProgressReporter(
        parallel_events.append)
    parallel_bytes = bc7_recolor._save_bc7_blocks(
        original, prepared, progress_reporter=parallel_progress)

    assert parallel_bytes == serial_bytes
    for events in (serial_events, parallel_events):
        processing = [event for event in events
                      if event["stage"] == "processing"]
        assert processing[0]["completed_blocks"] == 0
        assert [event["completed_blocks"] for event in processing] == \
            list(range(8))
        assert processing[-1]["completed_blocks"] == \
            processing[-1]["total_blocks"]


def test_parallel_bc7_multi_mip_weighted_jobs_match_serial_results(
        tmp_path, monkeypatch):
    blocks = _mode6_block() * 3
    original = _dx10_dds(blocks, width=4, height=4, mip_count=3)
    source = tmp_path / "body.dds"
    source.write_bytes(original)
    layout = inspect_dds_layout(source)
    prepared = SimpleNamespace(
        selected_path="body.dds", info=layout.info, layout=layout,
        mip0_claims=bytearray([1] * 16),
        intent_adjustments=(None, prepare_color_adjustment({"hue": 120})),
        mip0_affected_blocks=(0,))
    monkeypatch.setattr(bc7_recolor, "_bc7_worker_count", lambda: 2)
    monkeypatch.setattr(bc7_recolor, "_BC7_CHUNK_SIZE", 2)
    monkeypatch.setattr(progress, "SAVE_PROGRESS_INTERVAL", 0)

    class InlineExecutor:
        def submit(self, function, argument):
            future = Future()
            future.set_result(function(argument))
            return future

        def shutdown(self, **_kwargs):
            pass

    monkeypatch.setattr(
        bc7_recolor, "ProcessPoolExecutor", lambda **_kwargs: InlineExecutor())
    serial_events = []
    monkeypatch.setattr(bc7_recolor, "_BC7_PARALLEL_THRESHOLD", 10000)
    serial_bytes = bc7_recolor._save_bc7_blocks(
        original, prepared,
        progress_reporter=progress.SaveProgressReporter(
            serial_events.append))

    parallel_events = []
    monkeypatch.setattr(bc7_recolor, "_BC7_PARALLEL_THRESHOLD", 0)
    parallel_bytes = bc7_recolor._save_bc7_blocks(
        original, prepared,
        progress_reporter=progress.SaveProgressReporter(
            parallel_events.append))

    assert parallel_bytes == serial_bytes
    for events in (serial_events, parallel_events):
        processing = [event for event in events
                      if event["stage"] == "processing"]
        assert [(event["mip"], event["completed_blocks"])
                for event in processing if event["completed_blocks"]]
        assert [event["mip"] for event in processing
                if event["completed_blocks"] == event["total_blocks"]] == [
                    0, 1, 2]


def test_parallel_bc7_multi_adjustment_lower_mips_match_serial_results(
        tmp_path, monkeypatch):
    width, height = 20, 4
    block_count = 5 + 3 + 2
    original = _dx10_dds(_mode6_block() * block_count,
                         width=width, height=height, mip_count=3)
    source = tmp_path / "body.dds"
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
    prepared = SimpleNamespace(
        selected_path="body.dds", info=layout.info, layout=layout,
        mip0_claims=claims, intent_adjustments=adjustments,
        mip0_affected_blocks=tuple(range(5)),)
    monkeypatch.setattr(bc7_recolor, "_bc7_worker_count", lambda: 2)
    monkeypatch.setattr(bc7_recolor, "_BC7_CHUNK_SIZE", 2)
    monkeypatch.setattr(progress, "SAVE_PROGRESS_INTERVAL", 0)

    class InlineExecutor:
        def submit(self, function, argument):
            future = Future()
            future.set_result(function(argument))
            return future

        def shutdown(self, **_kwargs):
            pass

    monkeypatch.setattr(
        bc7_recolor, "ProcessPoolExecutor", lambda **_kwargs: InlineExecutor())
    monkeypatch.setattr(bc7_recolor, "_BC7_PARALLEL_THRESHOLD", 10000)
    serial_bytes = bc7_recolor._save_bc7_blocks(
        original, prepared)

    monkeypatch.setattr(bc7_recolor, "_BC7_PARALLEL_THRESHOLD", 0)
    parallel_bytes = bc7_recolor._save_bc7_blocks(
        original, prepared)

    assert parallel_bytes == serial_bytes


def test_bc7_result_application_uses_block_offsets():
    final = bytearray(32)
    totals = {"touched": 0, "source_error": 0, "final_error": 0}
    results = (
        bc7_recolor._BC7BlockResult(
            16, b"B" * 16, 4, 2),
        bc7_recolor._BC7BlockResult(
            0, b"A" * 16, 3, 1),
    )

    for result in results:
        bc7_recolor._record_bc7_result(final, result, totals)

    assert bytes(final) == b"A" * 16 + b"B" * 16
    assert totals["touched"] == 2
    assert totals["source_error"] == 7
    assert totals["final_error"] == 3


@pytest.mark.parametrize(
    ("cpu_count", "expected"),
    [(None, 1), (1, 1), (2, 1), (4, 3), (32, 6)],
)
def test_bc7_worker_count_leaves_one_cpu(monkeypatch, cpu_count, expected):
    monkeypatch.setattr(bc7_recolor.os, "cpu_count", lambda: cpu_count)
    assert bc7_recolor._bc7_worker_count() == expected


def test_parallel_bc7_worker_failure_is_reported(tmp_path, monkeypatch):
    source = tmp_path / "body.dds"
    source.write_bytes(_dx10_dds(_mode6_block()))
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
    state = bc7_recolor._bc7_intent_level(prepared)
    base = (100, 100, 100)
    base_float = tuple(channel / 255.0 for channel in base)
    adjusted = apply_prepared_color_adjustment(
        base_float, adjustment)
    target_width, target_height = width, height
    while (target_width, target_height) != (1, 1):
        target_width = max(1, target_width // 2)
        target_height = max(1, target_height // 2)
        state = bc7_recolor._bc7_next_intent_level(
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
    assert bc7_recolor._bc7_intent_rgb(
        base, state, 0, (None, adjustment)) == expected


def test_save_rejects_non_bc7_dds(tmp_path):
    source = tmp_path / "body.dds"
    source.write_bytes(_dx10_dds(bytes(8), dxgi_format=71))

    with pytest.raises(errors.TextureSaveError) as raised:
        request.inspect_save_texture(str(source))

    assert raised.value.code == "unsupported_texture_format"

"""WWMI candidate discovery stays separate from semantic texture bindings."""

from dataclasses import asdict
import os

import pytest

from app.assets.resolver import AssetComponentBinding
from app.assets.wuwa_texture_fallback import apply
from app.assets.wuwa_texture_names import texture_component_ordinals
from core.geometry.draw_call import DrawCall, SlotTextureBinding
from core.ini.parser import TextureOverrideIndex, TextureReplacement


@pytest.mark.parametrize("filename, expected", [
    ("Components-2 t=abc.dds", frozenset({2})),
    ("folder\\Components-0-1-4 t=abc.dds", frozenset({0, 1, 4})),
    ("SomeTexture.dds", None),
    ("Components-2 t=abc.png", None),
])
def test_texture_component_ordinals(filename, expected):
    assert texture_component_ordinals(filename) == expected


def _draw(root, *, status="exact", **kwargs):
    return DrawCall(
        asset_binding=AssetComponentBinding(
            status=status, component_status="exact", range_status="exact",
            asset_type="WWMI", root=str(root), asset="Matched",
            metadata="Matched/Metadata.json",
            detail_metadata="Matched/TextureUsage.json", component_ordinal=2),
        asset_slot_evidence=[{"texture_hash": "aaaaaaaa"}], **kwargs)


def _group(draw, name="Component2"):
    return {"name": name, "display_name": name, "draws": [draw]}


def test_three_sources_preserve_bindings_and_cache_only_matched_asset_directory(
        tmp_path, monkeypatch):
    mod = tmp_path / "mod"
    assets = tmp_path / "assets"
    matched = assets / "Matched"
    mod.mkdir()
    matched.mkdir(parents=True)
    named = "Components-2 t=aaaaaaaa.dds"
    files = [named, "variants/custom.png", "other/custom.png"]
    for filename in files:
        path = mod / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic texture")
    for filename in (named, "dump_AAAAAAAA.jpg", "unrelated_bbbbbbbb.dds",
                     "notes_aaaaaaaa.txt"):
        (matched / filename).write_bytes(b"synthetic asset")
    (assets / "Sibling").mkdir()
    (assets / "Sibling" / named).write_bytes(b"unrelated asset")
    index = TextureOverrideIndex(replacements_by_hash={
        "aaaaaaaa": tuple(TextureReplacement(
            "aaaaaaaa", f"Resource{number}", (), "TextureOverrideSkin", filename)
            for number, filename in enumerate([named, *files])),
    })
    draw = _draw(
        assets, texture_default_file="existing.dds",
        normal_map_default_file="existing-normal.dds",
        texture_provenance={"diffuse": "mod_semantic"},
        slot_textures=[SlotTextureBinding(0, "ResourceSlot", file="slot.dds")])
    group = _group(draw)
    second = _group(_draw(assets), "Component3")
    before = asdict(draw)
    scans = []
    scandir = os.scandir

    def scan(path):
        scans.append(os.fspath(path))
        return scandir(path)

    def no_decode(*args, **kwargs):
        raise AssertionError("Candidate discovery must not decode textures")

    monkeypatch.setattr("app.assets.wuwa_texture_fallback.os.scandir", scan)
    monkeypatch.setattr("app.assets.enrichment.classify_dds", no_decode)
    apply([group, second], str(mod), resource_files=[named],
          texture_indexes=[index])

    candidates = group["discovered_textures"]
    assert [item["file"] for item in candidates[:3]] == files
    assert [item["source"] for item in candidates] == [
        "wuwa_filename", "wuwa_hash", "wuwa_hash",
        "wuwa_asset_hash", "wuwa_asset_hash"]
    assert [item["label"] for item in candidates[3:]] == [
        "Components-2 t=aaaaaaaa (Asset)", "dump_AAAAAAAA (Asset)"]
    assert scans == [str(matched.resolve())]
    assert asdict(draw) == before

    # Removing the matched location does not remove mod-side candidates.
    for path in matched.iterdir():
        path.unlink()
    matched.rmdir()
    apply([group], str(mod), resource_files=[named], texture_indexes=[index])
    assert [item["file"] for item in group["discovered_textures"]] == files
    assert asdict(draw) == before


def test_mod_candidates_reject_missing_unsafe_and_non_image_resources(tmp_path):
    named = "Components-2 t=safe.dds"
    (tmp_path / named).write_bytes(b"synthetic")
    (tmp_path / "buffer.buf").write_bytes(b"synthetic")
    draw = _draw(tmp_path)
    group = _group(draw)
    filenames = [
        named, "./" + named, None, "", "missing.dds", "buffer.buf",
        "../../outside.dds", str(tmp_path / named),
    ]
    index = TextureOverrideIndex(replacements_by_hash={
        "aaaaaaaa": tuple(TextureReplacement(
            "aaaaaaaa", "ResourceTexture", (), "TextureOverrideSkin", filename)
            for filename in filenames),
    })
    apply([group], str(tmp_path), resource_files=filenames,
          texture_indexes=[index])
    assert group["discovered_textures"] == [
        {"file": named, "source": "wuwa_filename"}]


@pytest.mark.parametrize("status", ["partial", "ambiguous", "not_found"])
def test_inexact_assets_and_slot_bindings_do_not_add_a_fourth_source(
        tmp_path, status):
    (tmp_path / "slot.dds").write_bytes(b"synthetic texture")
    (tmp_path / "Matched").mkdir()
    (tmp_path / "Matched" / "aaaaaaaa.dds").write_bytes(b"synthetic asset")
    draw = _draw(tmp_path, status=status, slot_textures=[
        SlotTextureBinding(0, "ResourceTexture", file="slot.dds")])
    group = _group(draw)
    index = TextureOverrideIndex(replacements_by_hash={
        "aaaaaaaa": (TextureReplacement(
            "aaaaaaaa", "ResourceTexture", (), "TextureOverrideSkin", "slot.dds"),)
    })
    apply([group], str(tmp_path), resource_files=["slot.dds"],
          texture_indexes=[index])
    assert group["discovered_textures"] == []
    assert draw.texture_default("diffuse") is None
    assert draw.texture_provenance == {}


def test_asset_metadata_directory_cannot_escape_registered_root(tmp_path):
    root = tmp_path / "assets"
    root.mkdir()
    (tmp_path / "aaaaaaaa.dds").write_bytes(b"outside texture")
    draw = DrawCall(
        asset_binding=AssetComponentBinding(
            status="exact", component_status="exact", range_status="exact",
            asset_type="WWMI", root=str(root), metadata="../Metadata.json"),
        asset_slot_evidence=[{"texture_hash": "aaaaaaaa"}])
    group = _group(draw)
    apply([group], str(tmp_path))
    assert group["discovered_textures"] == []

"""Extraction pixel fallback precedence, association, and failure contracts."""

import copy
import json
import random
from pathlib import Path

from PIL import Image
import pytest

from app.assets import index, loader
from app.assets.extractor.capture import Inventory
from app.assets.extractor.core import Cancelled, Context
from app.assets.extractor.discovery import analyze
from app.assets.extractor.export import Selection, export, extract
from app.assets.extractor import pixel_roles
from app.assets.extractor.textures import digest
from core.geometry.mesh_builder import GeometryBlob
from tests.support.dds_data import uncompressed_dds
from tests.support.extraction_data import make_capture


def _textures(tmp_path):
    diffuse = Image.new("RGB", (128, 128))
    normal = Image.new("RGB", diffuse.size)
    packed = Image.new("RGB", diffuse.size)
    for y in range(128):
        for x in range(128):
            inside = (x // 16 + y // 16) % 3 == 0
            diffuse.putpixel((x, y), (220, 180, 130) if inside else (25, 30, 40))
            normal.putpixel((x, y), (166, 153, 247) if inside else (128, 128, 255))
            packed.putpixel((x, y), (0, 240, 0) if inside else (240, 0, 0))
    records = []
    for slot, (image, role, confidence) in enumerate([
        (diffuse, "Diffuse", "inferred_capture"),
        (normal, "TextureSlot01", "unknown"),
        (packed, "TextureSlot02", "unknown"),
    ]):
        path = tmp_path / f"sample-{slot}.png"
        image.save(path)
        geometry_hash = f"{slot + 6:08x}"
        records.append(dict(file=path.name, sha256=digest(path), width=128, height=128,
                            hash=geometry_hash, resource={"hash": geometry_hash},
                            slot=slot, role=role, role_confidence=confidence,
                            auxiliary=False, format="unknown"))
    return records


def test_pixel_fallback_preserves_reliable_roles_caches_pixels_and_keeps_masks_ambiguous(tmp_path, monkeypatch):
    textures = _textures(tmp_path)
    original = copy.deepcopy(textures)
    inv = Inventory(tmp_path)
    calls = []
    decode = pixel_roles.load_texture_image

    def counted(path, **kwargs):
        calls.append(Path(path).name)
        return decode(path, **kwargs)

    monkeypatch.setattr(pixel_roles, "load_texture_image", counted)
    pixel_roles.apply_fallback(inv, textures, True, Context())
    assert textures[0] == original[0]
    assert textures[1]["role"] == "NormalMap"
    assert textures[1]["role_confidence"] == "inferred_pixels_high"
    assert textures[1]["pixel_analysis"]["diffuse_layout_match"] >= 0.8
    assert textures[2]["role"] == "TextureSlot02"
    assert textures[2]["pixel_analysis"]["suggested_roles"] == ["LightMap", "MaterialMap"]
    assert textures[2]["pixel_analysis"]["confidence"] == "ambiguous"
    first_calls = list(calls)
    repeated = copy.deepcopy(original)
    pixel_roles.apply_fallback(inv, repeated, True, Context())
    assert repeated == textures
    assert calls == first_calls
    # A confirmed role blocks a competing inferred assignment on the same part.
    confirmed = dict(original[2], role="NormalMap", role_confidence="inferred_capture")
    competing = [copy.deepcopy(original[0]), copy.deepcopy(original[1]), confirmed]
    pixel_roles.apply_fallback(inv, competing, True, Context())
    assert competing[-1] == confirmed
    assert competing[1]["role"] == "TextureSlot01"
    # Fully classified material sets never invoke pixel decoding.
    monkeypatch.setattr(pixel_roles, "load_texture_image", lambda *_a, **_kw: pytest.fail("Reliable roles must not be decoded"))
    reliable = [dict(t, role_confidence="inferred_capture") for t in textures]
    pixel_roles.apply_fallback(Inventory(tmp_path), reliable, True, Context())


@pytest.mark.parametrize("case", ["main_maps", "colored_padding", "foreign", "ambiguous_light", "unbound", "auxiliary", "unrelated_slot", "srgb", "confirmed"])
def test_gimi_packed_rg_normals_and_light_channels_have_distinct_fallback_roles(tmp_path, case):
    dump = make_capture(tmp_path)
    textures = _textures(dump)
    normal = Image.new("RGB", (128, 128))
    if case == "colored_padding":
        normal.paste((0, 128, 0), (0, 0, 128, 128))
    light = Image.new("RGB", normal.size)
    for y in range(128):
        for x in range(128):
            if (x // 16 + y // 16) % 3 == 0:
                if case != "colored_padding" or (16 < x < 112 and 16 < y < 112):
                    normal.putpixel((x, y), (140, 116, 255 if y % 8 < 4 else 0))
                light.putpixel((x, y), (255, 180, 64) if y % 8 < 4 else (0, 220, 255))
    normal.save(dump / textures[1]["file"])
    light.save(dump / textures[2]["file"])
    if case == "ambiguous_light":
        light.save(dump / textures[1]["file"])
    elif case == "unbound":
        textures[2]["resource"] = None
    elif case == "auxiliary":
        textures[2]["auxiliary"] = True
    elif case == "unrelated_slot":
        textures[2]["slot"] = 21
    elif case == "srgb":
        textures[2]["format"] = "BC7_UNORM_SRGB"
    elif case == "confirmed":
        textures[2].update(role="NormalMap", role_confidence="inferred_capture")
    for t in textures:
        t["sha256"] = digest(dump / t["file"])
    original = copy.deepcopy(textures)
    inv = Inventory(dump)
    inv.gimi_evidence = case != "foreign"
    pixel_roles.apply_fallback(inv, textures, True, Context())
    assert textures[0] == original[0]
    if case in ("main_maps", "colored_padding"):
        assert [t["role"] for t in textures] == ["Diffuse", "NormalMap", "LightMap"]
        assert textures[1]["pixel_analysis"]["active_xy_centered"] >= 0.7
        assert textures[1]["pixel_analysis"]["game_profile"] == "gimi_packed_channels"
        assert textures[2]["pixel_analysis"]["game_profile"] == "gimi_packed_channels"
        package = extract(analyze(dump), Selection("Asset_001"))
        package.meshes[0].component.parts[0].textures = textures
        report = export(package, tmp_path / "exports")
        root = Path(report.output).parent
        current = index.build_index("GIMI", str(root))
        loaded = loader.load_asset("GIMI", str(root), current["assets"][0], geometry=GeometryBlob())
        part, = loaded.parts
        assert set(part.textures) == {"diffuse", "normal_map", "light_map"}
        for role, source in zip(("diffuse", "normal_map", "light_map"), textures):
            assert digest(Path(part.textures[role].path)) == source["sha256"]
    elif case == "confirmed":
        assert textures[2] == original[2]
        assert textures[1]["role"] == "TextureSlot01"
    else:
        assert not any(t["role"] == "LightMap" for t in textures)
    if case != "foreign":
        assert all("MaterialMap" not in t.get("pixel_analysis", {}).get("suggested_roles", [])
                   for t in textures)


@pytest.mark.parametrize("case", ["unrelated", "flat", "duplicate", "srgb", "dds_srgb", "float_encoding", "hdr_encoding", "typeless_encoding", "unbound", "contaminated", "auxiliary", "corrupt", "no_color", "cancelled"])
def test_pixel_fallback_rejects_ambiguous_or_unavailable_maps(tmp_path, case):
    textures = _textures(tmp_path)
    normal = tmp_path / textures[1]["file"]
    if case == "unrelated":
        image = Image.new("RGB", (128, 128))
        image.putdata([(166, 153, 247) if (x // 16) % 2 else (128, 128, 255)
                       for y in range(128) for x in range(128)])
        image.save(normal)
    elif case == "flat":
        Image.new("RGB", (128, 128), (128, 128, 255)).save(normal)
    elif case == "duplicate":
        textures.append(dict(textures[1], file="second.png", slot=3, role="TextureSlot03"))
        (tmp_path / "second.png").write_bytes(normal.read_bytes())
    elif case == "srgb":
        textures[1]["format"] = "R8G8B8A8_UNORM_SRGB"
    elif case == "dds_srgb":
        with Image.open(normal) as image:
            pixels = image.convert("RGBA").tobytes()
        normal.write_bytes(uncompressed_dds(pixels, width=128, height=128, dxgi_format=29))
    elif case in ("float_encoding", "hdr_encoding", "typeless_encoding"):
        textures[1]["format"] = {"float_encoding": "R16G16B16A16_FLOAT",
                                  "hdr_encoding": "BC6H_UF16", "typeless_encoding": "R8G8B8A8_TYPELESS"}[case]
    elif case == "unbound":
        textures[1]["resource"] = None
    elif case == "contaminated":
        textures[1]["contaminated"] = True
    elif case == "auxiliary":
        textures[1]["auxiliary"] = True
    elif case == "corrupt":
        normal.write_bytes(b"invalid image")
    textures[1]["sha256"] = digest(normal)
    context = Context()
    if case == "cancelled":
        context.cancel.set()
        with pytest.raises(Cancelled):
            pixel_roles.apply_fallback(Inventory(tmp_path), textures, True, context)
    else:
        pixel_roles.apply_fallback(Inventory(tmp_path), textures, case != "no_color", context)
    assert textures[0]["role"] == "Diffuse"
    assert all(t["role"] != "NormalMap" for t in textures)


@pytest.mark.parametrize("case", ["atlas", "noise", "ambiguous"])
def test_pixel_fallback_exports_unique_color_without_dump_roles(tmp_path, case):
    dump = make_capture(tmp_path)
    source = dump / "000002-ps-t0=66666666.dds"
    randomizer = random.Random(17)
    palette = [tuple(randomizer.randrange(256) for _ in range(3)) + (255,) for _ in range(64)]
    pixels = bytes(v for y in range(128) for x in range(128)
                   for v in palette[(y // 16) * 8 + x // 16])
    if case == "noise":
        pixels = bytes(randomizer.randrange(256) for _ in range(128 * 128 * 4))
    source.write_bytes(uncompressed_dds(pixels, width=128, height=128, dxgi_format=29))
    Image.new("RGB", (16, 16)).save(dump / "000002-o0=12345678.png")
    log = dump / "log.txt"
    log.write_text(log.read_text(encoding="utf-8").replace(
        "000002 DrawIndexed", """000002 PSSetShaderResources(StartSlot:0, NumViews:1)
       0: resource=0x60 hash=66666666
000002 DrawIndexed"""), encoding="utf-8")
    if case == "ambiguous":
        (dump / "000002-ps-t1=77777777.dds").write_bytes(source.read_bytes())
        log.write_text(log.read_text(encoding="utf-8").replace(
            "NumViews:1", "NumViews:2").replace(
            "       0: resource=0x60 hash=66666666\n",
            "       0: resource=0x60 hash=66666666\n       1: resource=0x70 hash=77777777\n"), encoding="utf-8")
    analysis = analyze(dump)
    textures = analysis.components[0].parts[0].textures
    texture = textures[0]
    if case == "atlas":
        assert texture["role"] == "Diffuse"
        assert texture["role_confidence"] == "inferred_pixels_high"
    else:
        assert all(t["role"].startswith("TextureSlot") for t in textures)
    report = export(extract(analysis, Selection("Asset_001")), tmp_path / "exports")
    asset = Path(report.output)
    metadata = json.loads((asset / "11111111" / "hash.json").read_text(encoding="utf-8"))
    assert metadata["components"][0]["texture_hashes"][0][0][0] == texture["role"]
    details = json.loads(Path(report.report_path).read_text(encoding="utf-8"))
    assert details["outputs"][0]["textures"][0]["pixel_analysis"]
    current = index.build_index("GIMI", str(asset.parent))
    loaded = loader.load_asset("GIMI", str(asset.parent), current["assets"][0], geometry=GeometryBlob())
    part, = loaded.parts
    if case == "atlas":
        assert set(part.textures) == {"diffuse"}
        assert digest(Path(part.textures["diffuse"].path)) == digest(source)
    else:
        assert not part.textures

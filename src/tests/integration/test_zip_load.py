"""ZIP-backed model loading through the ordinary mod pipeline."""

import struct
import zipfile

from app.mods import loader


def test_load_mod_reads_deep_geometry_and_reports_zip_source_metadata(tmp_path):
    ini = (
        "[TextureOverrideComponent01Position]\n"
        "vb0 = ResourceComponent01Position\n"
        "[TextureOverrideComponent01Texcoord]\n"
        "vb1 = ResourceComponent01Texcoord\n"
        "[TextureOverrideComponent01]\n"
        "ib = ResourceComponent01IB\n"
        "drawindexed = 3, 0, 0\n"
        "[ResourceComponent01Position]\n"
        "filename = p.buf\n"
        "stride = 12\n"
        "[ResourceComponent01Texcoord]\n"
        "filename = t.buf\n"
        "stride = 8\n"
        "[ResourceComponent01IB]\n"
        "filename = i.buf\n"
        "format = R32_UINT\n")
    archive_path = tmp_path / "packed.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("Export/some/random/deep/mod.ini", ini)
        archive.writestr("Export/some/random/deep/i.buf", struct.pack(
            "<3I", 0, 1, 2))
        archive.writestr("Export/some/random/deep/p.buf", struct.pack(
            "<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0))
        archive.writestr("Export/some/random/deep/t.buf", struct.pack(
            "<6f", 0, 0, 1, 0, 0, 1))

    payload = loader.load_mod(str(archive_path))

    assert not payload.get("error")
    assert payload["metadata"]["source_kind"] == "zip"
    assert payload["metadata"]["source_read_only"] is True
    assert len(payload["meshes"]) == 1
    assert payload["meshes"]["Component01-1"]["identity"]["source"] \
        == "some/random/deep/mod.ini"


def test_load_mod_does_not_cap_zip_ini_discovery_before_geometry(tmp_path):
    ini = (
        "[TextureOverrideComponent01Position]\n"
        "vb0 = ResourceComponent01Position\n"
        "[TextureOverrideComponent01Texcoord]\n"
        "vb1 = ResourceComponent01Texcoord\n"
        "[TextureOverrideComponent01]\n"
        "ib = ResourceComponent01IB\n"
        "drawindexed = 3, 0, 0\n"
        "[ResourceComponent01Position]\n"
        "filename = p.buf\n"
        "stride = 12\n"
        "[ResourceComponent01Texcoord]\n"
        "filename = t.buf\n"
        "stride = 8\n"
        "[ResourceComponent01IB]\n"
        "filename = i.buf\n"
        "format = R32_UINT\n")
    archive_path = tmp_path / "many-inis.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for index in range(10):
            archive.writestr(f"Export/{index:02}.ini", "[KeyUnused]\nkey = F1\n")
        archive.writestr("Export/zz-geometry.ini", ini)
        archive.writestr("Export/i.buf", struct.pack("<3I", 0, 1, 2))
        archive.writestr(
            "Export/p.buf",
            struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0))
        archive.writestr("Export/t.buf", struct.pack("<6f", 0, 0, 1, 0, 0, 1))

    payload = loader.load_mod(str(archive_path))

    assert not payload.get("error")
    assert payload["meshes"]["Component01-1"]["identity"]["source"] \
        == "zz-geometry.ini"

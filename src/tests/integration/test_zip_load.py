"""ZIP-backed model loading through the ordinary mod pipeline."""

import struct
import zipfile

from app.mods import loader


def test_load_mod_reads_geometry_and_reports_zip_source_metadata(tmp_path):
    ini = (
        "[TextureOverrideBodyPosition]\n"
        "vb0 = ResourceBodyPosition\n"
        "[TextureOverrideBodyTexcoord]\n"
        "vb1 = ResourceBodyTexcoord\n"
        "[TextureOverrideBody]\n"
        "ib = ResourceBodyIB\n"
        "drawindexed = 3, 0, 0\n"
        "[ResourceBodyPosition]\n"
        "filename = p.buf\n"
        "stride = 12\n"
        "[ResourceBodyTexcoord]\n"
        "filename = t.buf\n"
        "stride = 8\n"
        "[ResourceBodyIB]\n"
        "filename = i.buf\n"
        "format = R32_UINT\n")
    archive_path = tmp_path / "packed.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("Export/mod.ini", ini)
        archive.writestr("Export/i.buf", struct.pack("<3I", 0, 1, 2))
        archive.writestr("Export/p.buf", struct.pack(
            "<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0))
        archive.writestr("Export/t.buf", struct.pack(
            "<6f", 0, 0, 1, 0, 0, 1))

    payload = loader.load_mod(str(archive_path))

    assert not payload.get("error")
    assert payload["metadata"]["source_kind"] == "zip"
    assert payload["metadata"]["source_read_only"] is True
    assert len(payload["meshes"]) == 1
    assert payload["meshes"]["Body-1"]["identity"]["source"] == "mod.ini"

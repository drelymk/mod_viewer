"""7-Zip-backed model loading through the ordinary mod pipeline."""

import struct

import pytest

from app.mods import loader
from core import mod_source
from core.sevenzip import SevenZipEntry, SevenZipError


class FakeSevenZipClient:
    def __init__(self, members):
        self.members = members

    def list_members(self, _archive_path):
        return [SevenZipEntry(name, len(data))
                for name, data in self.members.items()]

    def read_member(self, _archive_path, member_name):
        return self.members[member_name]

    def read_prefix(self, _archive_path, member_name, length):
        return self.members[member_name][:length]


@pytest.mark.parametrize("extension,kind", [(".7z", "7z"), (".rar", "rar")])
def test_load_mod_uses_the_normal_pipeline_for_7zip_formats(
        tmp_path, monkeypatch, extension, kind):
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
    archive_path = tmp_path / f"packed{extension}"
    members = {
        "Export/some/random/deep/mod.ini": ini.encode(),
        "Export/some/random/deep/i.buf": struct.pack("<3I", 0, 1, 2),
        "Export/some/random/deep/p.buf": struct.pack(
            "<9f", 0, 0, 0, 1, 0, 0, 0, 0, 1),
        "Export/some/random/deep/t.buf": struct.pack(
            "<6f", 0, 0, 1, 0, 0, 1),
    }
    archive_path.write_bytes(b"mock archive")
    client = FakeSevenZipClient(members)
    monkeypatch.setattr(mod_source, "SevenZipCLI", lambda: client)

    payload = loader.load_mod(str(archive_path))

    assert not payload.get("error")
    assert payload["metadata"]["source_kind"] == kind
    assert payload["metadata"]["source_read_only"] is True
    assert len(payload["meshes"]) == 1
    assert payload["meshes"]["Body-1"]["identity"]["source"] == (
        "some/random/deep/mod.ini")


def test_load_mod_reports_missing_sevenzip_as_a_structured_error(
        tmp_path, monkeypatch):
    archive_path = tmp_path / "missing.7z"
    archive_path.write_bytes(b"mock archive")

    def missing_cli():
        raise SevenZipError(
            "7-Zip is not installed or 7z.exe could not be found. "
            "Install 7-Zip and try again.")

    monkeypatch.setattr(mod_source, "SevenZipCLI", missing_cli)

    payload = loader.load_mod(str(archive_path))

    assert payload["error"] == (
        "Could not read 7z mod source: 7-Zip is not installed or "
        "7z.exe could not be found. Install 7-Zip and try again.")

"""7-Zip-backed model loading through the ordinary mod pipeline."""

import os

import pytest

from app.mods import loader
from core import mod_source
from core.sevenzip import SevenZipEntry, SevenZipError
from tests.support.model_data import basic_model_ini, triangle_geometry


class FakeSevenZipClient:
    def __init__(self, members):
        self.members = members
        self.calls = []

    def list_members(self, _archive_path):
        self.calls.append("list")
        return [SevenZipEntry(name, len(data))
                for name, data in self.members.items()]

    def extract_all(self, _archive_path, output_dir):
        self.calls.append("extract")
        for name, data in self.members.items():
            target = os.path.join(
                output_dir, *name.replace("\\", "/").split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as stream:
                stream.write(data)


@pytest.mark.parametrize("extension,kind", [(".7z", "7z"), (".rar", "rar")])
def test_load_mod_uses_the_normal_pipeline_for_7zip_formats(
        tmp_path, monkeypatch, extension, kind):
    ini = basic_model_ini()
    geometry = triangle_geometry()
    archive_path = tmp_path / f"packed{extension}"
    members = {
        "Export/some/random/deep/mod.ini": ini.encode(),
        **{f"Export/some/random/deep/{name}": data
           for name, data in geometry.items()},
    }
    archive_path.write_bytes(b"mock archive")
    client = FakeSevenZipClient(members)
    monkeypatch.setattr(mod_source, "SevenZipCLI", lambda: client)

    payload = loader.load_mod(str(archive_path))

    assert not payload.get("error")
    assert payload["metadata"]["source_kind"] == kind
    assert payload["metadata"]["source_read_only"] is True
    assert len(payload["meshes"]) == 1
    assert payload["meshes"]["Component01-1"]["identity"]["source"] == (
        "some/random/deep/mod.ini")
    assert client.calls == ["list", "extract"]


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

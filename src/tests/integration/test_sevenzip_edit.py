"""Read-only staged editing contracts for 7z and RAR sources."""

import os

import pytest

from app.bridge import toggle as toggle_api
from app.session import edit as edit_session
from core import mod_source
from core.mod_discovery import discover_ini_paths
from core.sevenzip import SevenZipEntry
from core.mod_source import SevenZipModSource


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


@pytest.mark.parametrize("extension", [".7z", ".rar"])
def test_sevenzip_edits_stay_staged_and_export_never_writes(
        tmp_path, monkeypatch, extension):
    original = "[KeyComponent01]\r\nkey = F1\r\n"
    archive_path = tmp_path / f"packed{extension}"
    archive_bytes = b"mock archive bytes"
    archive_path.write_bytes(archive_bytes)
    client = FakeSevenZipClient({"Wrapper/mod.ini": original.encode()})
    monkeypatch.setattr(mod_source, "SevenZipCLI", lambda: client)
    source = SevenZipModSource(archive_path, client=client)
    paths = discover_ini_paths(str(archive_path), source=source)

    try:
        edit_session.load_documents(str(archive_path), paths, source=source)
        edit_session.update_text(
            str(archive_path), "mod.ini", "[KeyComponent01]\nkey = F2\n")

        result = toggle_api.export_changes(str(archive_path))

        assert result["error"] == "Export is unavailable for compressed mods."
        assert edit_session.has_pending(str(archive_path))
        assert archive_path.read_bytes() == archive_bytes
        assert not (tmp_path / ".mod_viewer.json").exists()
        assert client.calls == ["list", "extract"]
    finally:
        edit_session.discard(str(archive_path))

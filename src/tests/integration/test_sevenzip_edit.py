"""Read-only staged editing contracts for 7z and RAR sources."""

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

    def list_members(self, _archive_path):
        return [SevenZipEntry(name, len(data))
                for name, data in self.members.items()]

    def read_member(self, _archive_path, member_name):
        return self.members[member_name]

    def read_prefix(self, _archive_path, member_name, length):
        return self.members[member_name][:length]


@pytest.mark.parametrize("extension", [".7z", ".rar"])
def test_sevenzip_edits_stay_staged_and_export_never_writes(
        tmp_path, monkeypatch, extension):
    original = "[KeyBody]\r\nkey = F1\r\n"
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
            str(archive_path), "mod.ini", "[KeyBody]\nkey = F2\n")

        result = toggle_api.export_changes(str(archive_path))

        assert result["error"] == "Export is unavailable for compressed mods."
        assert edit_session.has_pending(str(archive_path))
        assert archive_path.read_bytes() == archive_bytes
        assert not (tmp_path / ".mod_viewer.json").exists()
    finally:
        edit_session.discard(str(archive_path))

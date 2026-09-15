"""ZIP mod integration and read-only editing contracts."""

import zipfile

from app.bridge import toggle as toggle_api
from app.session import edit as edit_session
from core.mod_discovery import discover_ini_paths
from core.mod_source import ZipModSource


def test_zip_edit_session_keeps_staged_text_in_memory_and_export_is_blocked(
        tmp_path):
    archive_path = tmp_path / "packed.zip"
    original = "[KeyBody]\r\nkey = F1\r\n"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("Wrapper/mod.ini", original)
    source = ZipModSource(archive_path)
    paths = discover_ini_paths(str(archive_path), source=source)

    try:
        edit_session.load_documents(str(archive_path), paths, source=source)
        assert edit_session.editable_text(edit_session.peek(
            str(archive_path), paths[0])) == original.replace("\r\n", "\n")
        edit_session.update_text(
            str(archive_path), "mod.ini", "[KeyBody]\nkey = F2\n")

        result = toggle_api.export_changes(str(archive_path))

        assert result["error"] == "Export is unavailable for compressed mods."
        assert edit_session.has_pending(str(archive_path))
        with zipfile.ZipFile(archive_path) as archive:
            assert archive.read("Wrapper/mod.ini").decode() == original
    finally:
        edit_session.discard(str(archive_path))

"""ZIP mod integration and read-only editing contracts."""

import zipfile

from app.bridge.api import ModViewerAPI
from app.bridge import present as present_api
from app.bridge import toggle as toggle_api
from app.session import edit as edit_session
from core.mod_discovery import discover_ini_paths
from core.editing.present import SECTION_NAME
from core.mod_source import ZipModSource


def test_zip_edit_session_keeps_staged_text_in_memory_and_export_is_blocked(
        tmp_path):
    archive_path = tmp_path / "packed.zip"
    original = "[KeyComponent01]\r\nkey = F1\r\n"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("Wrapper/mod.ini", original)
    source = ZipModSource(archive_path)
    paths = discover_ini_paths(str(archive_path), source=source)

    try:
        edit_session.load_documents(str(archive_path), paths, source=source)
        assert edit_session.editable_text(edit_session.peek(
            str(archive_path), paths[0])) == original.replace("\r\n", "\n")
        edit_session.update_text(
            str(archive_path), "mod.ini", "[KeyComponent01]\nkey = F2\n")

        result = toggle_api.export_changes(str(archive_path))

        assert result["error"] == "Export is unavailable for compressed mods."
        assert edit_session.has_pending(str(archive_path))
        with zipfile.ZipFile(archive_path) as archive:
            assert archive.read("Wrapper/mod.ini").decode() == original
    finally:
        edit_session.discard(str(archive_path))


def test_zip_present_lifecycle_stages_ini_without_metadata_or_archive_write(
        tmp_path):
    archive_path = tmp_path / "present.zip"
    original = (
        "[KeyHat]\n"
        "key = h\n"
        "type = cycle\n"
        "$Hat = 0,1\n")
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("Wrapper/deep/variant/mod.ini", original)
    original_archive = archive_path.read_bytes()
    source = ZipModSource(archive_path)
    paths = discover_ini_paths(str(archive_path), source=source)
    api = ModViewerAPI()
    mod_dir = api._access.remember_mod_picker_selection(str(archive_path))

    try:
        edit_session.load_documents(mod_dir, paths, source=source)
        snapshot = {"deep/variant/mod.ini": {"Hat": "0"}}

        added = present_api.add_present(mod_dir, "p", "shift p", snapshot)
        assert added["ok"] is True
        assert present_api.edit_present(mod_dir, "ctrl p", "")["ok"] is True
        captured = present_api.capture_present(
            mod_dir, {"deep/variant/mod.ini": {"Hat": "1"}},
            "Alternate")
        assert captured["ok"] is True
        renamed = present_api.capture_present(
            mod_dir, {"deep/variant/mod.ini": {"Hat": "1"}},
            "Renamed", position=1)
        assert renamed["ok"] is True
        removed = present_api.delete_present_position(mod_dir, 0)
        assert removed["ok"] is True

        state = api.get_present_state(mod_dir)
        assert state["present"]["item"]["names"] == ["Renamed"]

        doc = edit_session.peek(mod_dir, paths[0])
        assert doc.section(SECTION_NAME) is not None
        assert "key = ctrl p" in doc.to_string()
        assert "$Hat = 1" in doc.to_string()
        assert edit_session.has_pending(mod_dir)
        assert toggle_api.export_changes(mod_dir)["error"] \
            == "Export is unavailable for compressed mods."
        assert archive_path.read_bytes() == original_archive
        assert not (tmp_path / ".mod_viewer.json").exists()
    finally:
        edit_session.discard(mod_dir)

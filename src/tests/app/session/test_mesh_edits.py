import hashlib
import os

from app.session import edit as edit_session


def test_staged_index_buffer_is_an_override_and_exports_once(tmp_path):
    ini = tmp_path / "component01.ini"
    ib = tmp_path / "Component01.ib"
    ini.write_text("[Component01]\ndrawindexed = 6, 0, 4\n", encoding="utf-8")
    original = b"0123456789abcdef"
    ib.write_bytes(original)
    edit_session.load_documents(str(tmp_path), [str(ini)])

    candidate = original[:3] + b"XYZ" + original[6:]
    with edit_session.transaction(str(tmp_path), [str(ini)]) as transaction:
        transaction.document(str(ini)).replace_lines(
            1, 2, ["drawindexed = 6, 0, 5"])
        transaction.stage_ib_edit(
            str(ib), candidate, hashlib.sha256(original).hexdigest(),
            [(3, 6)], dependent_inis={"component01.ini"})

    assert edit_session.has_pending(str(tmp_path))
    assert edit_session.ib_overrides_for(str(tmp_path))[str(ib)] == candidate
    result = edit_session.export(str(tmp_path))
    assert result["buffers_saved"] == [str(ib)]
    assert ib.read_bytes() == candidate
    assert len(list(tmp_path.glob("Component01-*.ib"))) == 1
    assert not edit_session.has_pending(str(tmp_path))


def test_buf_index_backup_preserves_source_stem(tmp_path):
    path = tmp_path / "Component01.buf"
    path.write_bytes(b"buffer")
    backup = edit_session._write_buffer_backup(str(path), b"buffer")
    assert os.path.basename(backup).startswith("Component01-")
    assert backup.lower().endswith(".buf")


def test_export_revalidates_committed_buffer_before_retrying_ini(
        tmp_path, monkeypatch):
    ini = tmp_path / "component01.ini"
    ib = tmp_path / "Component01.ib"
    ini.write_text("[Component01]\ndrawindexed = 6, 0, 4\n", encoding="utf-8")
    original = b"0123456789abcdef"
    candidate = original[:3] + b"XYZ" + original[6:]
    ib.write_bytes(original)
    edit_session.load_documents(str(tmp_path), [str(ini)])
    with edit_session.transaction(str(tmp_path), [str(ini)]) as transaction:
        transaction.document(str(ini)).replace_lines(
            1, 2, ["drawindexed = 6, 0, 5"])
        transaction.stage_ib_edit(
            str(ib), candidate, hashlib.sha256(original).hexdigest(),
            [(3, 6)], dependent_inis={"component01.ini"})

    from core.ini.document import IniDocument
    original_save = IniDocument.save
    calls = {"count": 0}

    def fail_once(document):
        if calls["count"] == 0:
            calls["count"] += 1
            raise OSError("simulated ini save failure")
        return original_save(document)

    monkeypatch.setattr(IniDocument, "save", fail_once)
    first = edit_session.export(str(tmp_path))
    assert first["buffers_saved"] == [str(ib)]
    assert first["failed"][0]["ini"] == "component01.ini"
    assert len(list(tmp_path.glob("Component01-*.ib"))) == 1

    ib.write_bytes(b"changed outside viewer")
    second = edit_session.export(str(tmp_path))

    assert len(second["buffers_failed"]) == 1
    assert "committed index buffer changed" in second["buffers_failed"][0]["error"]
    assert second["saved"] == []
    assert calls["count"] == 1
    assert "drawindexed = 6, 0, 4" in ini.read_text(encoding="utf-8")
    assert len(list(tmp_path.glob("Component01-*.ib"))) == 1

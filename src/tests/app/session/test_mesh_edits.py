import hashlib
import os

from app.session import edit as edit_session


def test_staged_index_buffer_is_an_override_and_exports_once(tmp_path):
    ini = tmp_path / "body.ini"
    ib = tmp_path / "Body.ib"
    ini.write_text("[Body]\ndrawindexed = 6, 0, 4\n", encoding="utf-8")
    original = b"0123456789abcdef"
    ib.write_bytes(original)
    edit_session.load_documents(str(tmp_path), [str(ini)])

    candidate = original[:3] + b"XYZ" + original[6:]
    with edit_session.transaction(str(tmp_path), [str(ini)]) as transaction:
        transaction.document(str(ini)).replace_lines(
            1, 2, ["drawindexed = 6, 0, 5"])
        transaction.stage_ib_edit(
            str(ib), candidate, hashlib.sha256(original).hexdigest(),
            [(3, 6)], dependent_inis={"body.ini"})

    assert edit_session.has_pending(str(tmp_path))
    assert edit_session.ib_overrides_for(str(tmp_path))[str(ib)] == candidate
    result = edit_session.export(str(tmp_path))
    assert result["buffers_saved"] == [str(ib)]
    assert ib.read_bytes() == candidate
    assert len(list(tmp_path.glob("Body-*.ib"))) == 1
    assert not edit_session.has_pending(str(tmp_path))


def test_buf_index_backup_uses_ib_suffix(tmp_path):
    path = tmp_path / "Body.buf"
    path.write_bytes(b"buffer")
    backup = edit_session._write_buffer_backup(str(path), b"buffer")
    assert os.path.basename(backup).startswith("BodyIB-")
    assert backup.lower().endswith(".buf")

from types import SimpleNamespace

from app.bridge import mesh_edit
from app.session import edit as edit_session
from core.geometry.draw_call import DrawCall
from core.geometry.identity import mesh_identity_for_draw
from core.mod_source import DirectoryModSource


def _fixture(tmp_path):
    ini = tmp_path / "body.ini"
    ib = tmp_path / "Body.ib"
    ini.write_text("[Body]\ndrawindexed = 6, 1, 7 ; keep\n", encoding="utf-8")
    original = b"\x99\x99" + bytes(range(12)) + b"\x88\x88"
    ib.write_bytes(original)
    source = DirectoryModSource(tmp_path)
    edit_session.load_documents(str(tmp_path), [str(ini)], source=source)
    draw = DrawCall(
        label="Body-1", count=6, start=1, base=7,
        sources=[{
            "ini_path": str(ini), "line_no": 2, "section": "Body",
            "occurrence": {"section": "Body", "ordinal": 0, "path": []},
        }],
        ib_file="Body.ib", index_size=2,
    )
    group = {"identity_source": "body.ini", "display_name": "Body",
             "name": "Body"}
    context = SimpleNamespace(
        mod_dir=str(tmp_path), source=source, ini_paths=[str(ini)],
        docs=edit_session.documents_for(str(tmp_path)),
    )
    return ini, ib, original, draw, group, context


def test_apply_component_mesh_changes_stages_lossless_ini_and_ib(tmp_path,
                                                                  monkeypatch):
    ini, ib, original, draw, group, context = _fixture(tmp_path)
    monkeypatch.setattr(mesh_edit, "resolved_draws",
                        lambda _context, _overrides: (None, {
                            draw.label: (draw, group)}))
    identity = mesh_identity_for_draw(draw, group).to_dict()
    request = {
        "component": "Body",
        "meshes": [{
            "identity": identity,
            "drawindexed": [6, 1, 7],
            "sources": [{
                "ini": "body.ini", "line": 2, "section": "Body",
                "occurrence": {"section": "Body", "ordinal": 0, "path": []},
            }],
            "parts": [[1], [0]],
        }],
    }

    result = mesh_edit.apply_component_mesh_changes(context, {}, request)

    assert result == {"ok": True, "component": "Body", "meshes": 1}
    assert ib.read_bytes() == original
    staged_text = edit_session.peek(str(tmp_path), str(ini)).to_string()
    staged_text = staged_text.replace("\r\n", "\n")
    assert "drawindexed = 3, 1, 7 ; keep\n drawindexed" not in staged_text
    assert "drawindexed = 3, 1, 7 ; keep\n" in (
        staged_text)
    assert "drawindexed = 3, 4, 7\n" in (
        staged_text)
    staged = edit_session.ib_overrides_for(str(tmp_path))[str(ib)]
    assert staged == b"\x99\x99" + bytes(range(6, 12)) + bytes(range(0, 6)) \
        + b"\x88\x88"


def test_apply_rejects_incomplete_partition_without_staging(tmp_path, monkeypatch):
    _ini, ib, original, draw, group, context = _fixture(tmp_path)
    monkeypatch.setattr(mesh_edit, "resolved_draws",
                        lambda _context, _overrides: (None, {
                            draw.label: (draw, group)}))
    request = {
        "component": "Body",
        "meshes": [{
            "identity": mesh_identity_for_draw(draw, group).to_dict(),
            "sources": [{
                "ini": "body.ini", "line": 2, "section": "Body",
                "occurrence": {"section": "Body", "ordinal": 0, "path": []},
            }],
            "parts": [[0], [0]],
        }],
    }

    result = mesh_edit.apply_component_mesh_changes(context, {}, request)

    assert "overlap" in result["error"]
    assert ib.read_bytes() == original
    assert not edit_session.has_pending(str(context.mod_dir))


def test_apply_resolves_stale_line_after_another_draw_shifts(tmp_path, monkeypatch):
    ini = tmp_path / "body.ini"
    ib = tmp_path / "Body.ib"
    ini.write_text(
        "[Body]\n"
        "drawindexed = 6, 1, 7\n"
        "[Hair]\n"
        "drawindexed = 6, 7, 9\n",
        encoding="utf-8")
    original = b"".join(value.to_bytes(2, "little") for value in range(13))
    ib.write_bytes(original)
    source = DirectoryModSource(tmp_path)
    edit_session.load_documents(str(tmp_path), [str(ini)], source=source)

    body = DrawCall(
        label="Body-1", count=6, start=1, base=7,
        sources=[{
            "ini_path": str(ini), "line_no": 2, "section": "Body",
            "occurrence": {"section": "Body", "ordinal": 0, "path": []},
        }],
        ib_file="Body.ib", index_size=2)
    hair = DrawCall(
        label="Hair-1", count=6, start=7, base=9,
        sources=[{
            "ini_path": str(ini), "line_no": 4, "section": "Hair",
            "occurrence": {"section": "Hair", "ordinal": 0, "path": []},
        }],
        ib_file="Body.ib", index_size=2)
    body_group = {"identity_source": "body.ini", "display_name": "Body",
                  "name": "Body"}
    hair_group = {"identity_source": "body.ini", "display_name": "Hair",
                  "name": "Hair"}
    context = SimpleNamespace(
        mod_dir=str(tmp_path), source=source, ini_paths=[str(ini)],
        docs=edit_session.documents_for(str(tmp_path)))
    monkeypatch.setattr(mesh_edit, "resolved_draws",
                        lambda _context, _overrides: (None, {
                            body.label: (body, body_group),
                            hair.label: (hair, hair_group),
                        }))

    def request(component, source_ref, draw):
        return {
            "component": component,
            "meshes": [{
                "identity": mesh_identity_for_draw(
                    draw, body_group if component == "Body" else hair_group
                ).to_dict(),
                "sources": [source_ref],
                "parts": [[1], [0]],
            }],
        }

    body_source = {
        "ini": "body.ini", "line": 2, "section": "Body",
        "occurrence": {"section": "Body", "ordinal": 0, "path": []},
    }
    hair_source = {
        "ini": "body.ini", "line": 4, "section": "Hair",
        "occurrence": {"section": "Hair", "ordinal": 0, "path": []},
    }
    first = mesh_edit.apply_component_mesh_changes(
        context, {}, request("Body", body_source, body))
    second = mesh_edit.apply_component_mesh_changes(
        context, {}, request("Hair", hair_source, hair))

    assert first == {"ok": True, "component": "Body", "meshes": 1}
    assert second == {"ok": True, "component": "Hair", "meshes": 1}
    staged = edit_session.peek(str(tmp_path), str(ini)).to_string()
    staged = staged.replace("\r\n", "\n")
    assert "drawindexed = 3, 1, 7\n" in staged
    assert "drawindexed = 3, 4, 7\n" in staged
    assert "drawindexed = 3, 7, 9\n" in staged
    assert "drawindexed = 3, 10, 9\n" in staged


def _overlap_fixture(tmp_path, other_start, other_count):
    ini = tmp_path / "body.ini"
    ib = tmp_path / "Body.ib"
    ini.write_text(
        "[Body]\n"
        "drawindexed = 12, 0, 0\n"
        "[Overlay]\n"
        f"drawindexed = {other_count}, {other_start}, 0\n",
        encoding="utf-8")
    original = b"".join(value.to_bytes(2, "little") for value in range(12))
    ib.write_bytes(original)
    source = DirectoryModSource(tmp_path)
    edit_session.load_documents(str(tmp_path), [str(ini)], source=source)
    body = DrawCall(
        label="Body-1", count=12, start=0, base=0,
        sources=[{
            "ini_path": str(ini), "line_no": 2, "section": "Body",
            "occurrence": {"section": "Body", "ordinal": 0, "path": []},
        }],
        ib_file="Body.ib", index_size=2)
    overlay = DrawCall(
        label="Overlay-1", count=other_count, start=other_start, base=0,
        sources=[{
            "ini_path": str(ini), "line_no": 4, "section": "Overlay",
            "occurrence": {"section": "Overlay", "ordinal": 0, "path": []},
        }],
        ib_file="Body.ib", index_size=2)
    body_group = {"identity_source": "body.ini", "display_name": "Body",
                  "name": "Body"}
    overlay_group = {"identity_source": "body.ini",
                     "display_name": "Overlay", "name": "Overlay"}
    context = SimpleNamespace(
        mod_dir=str(tmp_path), source=source, ini_paths=[str(ini)],
        docs=edit_session.documents_for(str(tmp_path)))
    return ini, ib, original, body, overlay, body_group, overlay_group, context


def test_apply_rejects_partial_overlap_with_another_draw(tmp_path, monkeypatch):
    ini, ib, original, body, overlay, body_group, overlay_group, context = (
        _overlap_fixture(tmp_path, 6, 6))
    monkeypatch.setattr(mesh_edit, "resolved_draws",
                        lambda _context, _overrides: (None, {
                            body.label: (body, body_group),
                            overlay.label: (overlay, overlay_group),
                        }))
    request = {
        "component": "Body",
        "meshes": [{
            "identity": mesh_identity_for_draw(body, body_group).to_dict(),
            "sources": [{
                "ini": "body.ini", "line": 2, "section": "Body",
                "occurrence": {"section": "Body", "ordinal": 0, "path": []},
            }],
            "parts": [[0, 2], [1, 3]],
        }],
    }

    result = mesh_edit.apply_component_mesh_changes(context, {}, request)

    assert "overlaps another draw" in result["error"]
    assert ib.read_bytes() == original
    assert not edit_session.has_pending(str(context.mod_dir))
    assert edit_session.ib_overrides_for(str(context.mod_dir)) == {}
    staged = edit_session.peek(str(context.mod_dir), str(ini)).to_string()
    assert staged.replace("\r\n", "\n") == ini.read_text(
        encoding="utf-8").replace("\r\n", "\n")


def test_apply_allows_identical_complete_overlap_with_another_draw(
        tmp_path, monkeypatch):
    ini, ib, _original, body, overlay, body_group, overlay_group, context = (
        _overlap_fixture(tmp_path, 0, 12))
    monkeypatch.setattr(mesh_edit, "resolved_draws",
                        lambda _context, _overrides: (None, {
                            body.label: (body, body_group),
                            overlay.label: (overlay, overlay_group),
                        }))
    request = {
        "component": "Body",
        "meshes": [{
            "identity": mesh_identity_for_draw(body, body_group).to_dict(),
            "sources": [{
                "ini": "body.ini", "line": 2, "section": "Body",
                "occurrence": {"section": "Body", "ordinal": 0, "path": []},
            }],
            "parts": [[0, 2], [1, 3]],
        }],
    }

    result = mesh_edit.apply_component_mesh_changes(context, {}, request)

    assert result == {"ok": True, "component": "Body", "meshes": 1}
    assert edit_session.has_pending(str(context.mod_dir))
    assert str(ib) in edit_session.ib_overrides_for(str(context.mod_dir))

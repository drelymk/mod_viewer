from types import SimpleNamespace

from app.bridge import mesh_edit
from app.session import edit as edit_session
from core.geometry.draw_call import DrawCall
from core.geometry.identity import mesh_identity_for_draw
from core.mod_source import DirectoryModSource


def _fixture(tmp_path):
    ini = tmp_path / "component01.ini"
    ib = tmp_path / "Component01.ib"
    ini.write_text("[Component01]\ndrawindexed = 6, 1, 7 ; keep\n", encoding="utf-8")
    original = b"\x99\x99" + bytes(range(12)) + b"\x88\x88"
    ib.write_bytes(original)
    source = DirectoryModSource(tmp_path)
    edit_session.load_documents(str(tmp_path), [str(ini)], source=source)
    draw = DrawCall(
        label="Component01-1", count=6, start=1, base=7,
        sources=[{
            "ini_path": str(ini), "line_no": 2, "section": "Component01",
            "occurrence": {"section": "Component01", "ordinal": 0, "path": []},
        }],
        ib_file="Component01.ib", index_size=2,
    )
    group = {"identity_source": "component01.ini", "display_name": "Component01",
             "name": "Component01"}
    context = SimpleNamespace(
        mod_dir=str(tmp_path), source=source, ini_paths=[str(ini)],
        docs=edit_session.documents_for(str(tmp_path)),
    )
    return ini, ib, original, draw, group, context


def test_apply_component_mesh_changes_stages_lossless_ini_and_ib(tmp_path,
                                                                  monkeypatch):
    ini, ib, original, draw, group, context = _fixture(tmp_path)
    monkeypatch.setattr(mesh_edit, "resolved_draws",
                        lambda _context: (None, {
                            draw.label: (draw, group)}))
    identity = mesh_identity_for_draw(draw, group).key
    request = {
        "component": "Component01",
        "mesh": {
            "key": identity,
            "sources": [{
                "ini": "component01.ini", "line": 2, "section": "Component01",
                "occurrence": {"section": "Component01", "ordinal": 0, "path": []},
            }],
            "parts": [[1], [0]],
        },
    }

    result = mesh_edit.apply_component_mesh_changes(context, request)

    assert result == {"ok": True, "component": "Component01"}
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
                        lambda _context: (None, {
                            draw.label: (draw, group)}))
    request = {
        "component": "Component01",
        "mesh": {
            "key": mesh_identity_for_draw(draw, group).key,
            "sources": [{
                "ini": "component01.ini", "line": 2, "section": "Component01",
                "occurrence": {"section": "Component01", "ordinal": 0, "path": []},
            }],
            "parts": [[0], [0]],
        },
    }

    result = mesh_edit.apply_component_mesh_changes(context, request)

    assert "overlap" in result["error"]
    assert ib.read_bytes() == original
    assert not edit_session.has_pending(str(context.mod_dir))


def test_apply_resolves_stale_line_after_another_draw_shifts(tmp_path, monkeypatch):
    ini = tmp_path / "component01.ini"
    ib = tmp_path / "Component01.ib"
    ini.write_text(
        "[Component01]\n"
        "drawindexed = 6, 1, 7\n"
        "[Component02]\n"
        "drawindexed = 6, 7, 9\n",
        encoding="utf-8")
    original = b"".join(value.to_bytes(2, "little") for value in range(13))
    ib.write_bytes(original)
    source = DirectoryModSource(tmp_path)
    edit_session.load_documents(str(tmp_path), [str(ini)], source=source)

    component01 = DrawCall(
        label="Component01-1", count=6, start=1, base=7,
        sources=[{
            "ini_path": str(ini), "line_no": 2, "section": "Component01",
            "occurrence": {"section": "Component01", "ordinal": 0, "path": []},
        }],
        ib_file="Component01.ib", index_size=2)
    component02 = DrawCall(
        label="Component02-1", count=6, start=7, base=9,
        sources=[{
            "ini_path": str(ini), "line_no": 4, "section": "Component02",
            "occurrence": {"section": "Component02", "ordinal": 0, "path": []},
        }],
        ib_file="Component01.ib", index_size=2)
    component01_group = {"identity_source": "component01.ini", "display_name": "Component01",
                  "name": "Component01"}
    component02_group = {"identity_source": "component01.ini", "display_name": "Component02",
                  "name": "Component02"}
    context = SimpleNamespace(
        mod_dir=str(tmp_path), source=source, ini_paths=[str(ini)],
        docs=edit_session.documents_for(str(tmp_path)))
    monkeypatch.setattr(mesh_edit, "resolved_draws",
                        lambda _context: (None, {
                            component01.label: (component01, component01_group),
                            component02.label: (component02, component02_group),
                        }))

    def request(component, source_ref, draw):
        return {
            "component": component,
            "mesh": {
                "key": mesh_identity_for_draw(
                    draw, component01_group if component == "Component01" else component02_group
                ).key,
                "sources": [source_ref],
                "parts": [[1], [0]],
            },
        }

    component01_source = {
        "ini": "component01.ini", "line": 2, "section": "Component01",
        "occurrence": {"section": "Component01", "ordinal": 0, "path": []},
    }
    component02_source = {
        "ini": "component01.ini", "line": 4, "section": "Component02",
        "occurrence": {"section": "Component02", "ordinal": 0, "path": []},
    }
    first = mesh_edit.apply_component_mesh_changes(
        context, request("Component01", component01_source, component01))
    second = mesh_edit.apply_component_mesh_changes(
        context, request("Component02", component02_source, component02))

    assert first == {"ok": True, "component": "Component01"}
    assert second == {"ok": True, "component": "Component02"}
    staged = edit_session.peek(str(tmp_path), str(ini)).to_string()
    staged = staged.replace("\r\n", "\n")
    assert "drawindexed = 3, 1, 7\n" in staged
    assert "drawindexed = 3, 4, 7\n" in staged
    assert "drawindexed = 3, 7, 9\n" in staged
    assert "drawindexed = 3, 10, 9\n" in staged


def _overlap_fixture(tmp_path, other_start, other_count,
                     other_ib_file="Component01.ib"):
    ini = tmp_path / "component01.ini"
    ib = tmp_path / "Component01.ib"
    ini.write_text(
        "[Component01]\n"
        "drawindexed = 12, 0, 0\n"
        "[Overlay]\n"
        f"drawindexed = {other_count}, {other_start}, 0\n",
        encoding="utf-8")
    original = b"".join(value.to_bytes(2, "little") for value in range(12))
    ib.write_bytes(original)
    source = DirectoryModSource(tmp_path)
    edit_session.load_documents(str(tmp_path), [str(ini)], source=source)
    component01 = DrawCall(
        label="Component01-1", count=12, start=0, base=0,
        sources=[{
            "ini_path": str(ini), "line_no": 2, "section": "Component01",
            "occurrence": {"section": "Component01", "ordinal": 0, "path": []},
        }],
        ib_file="Component01.ib", index_size=2)
    overlay = DrawCall(
        label="Overlay-1", count=other_count, start=other_start, base=0,
        sources=[{
            "ini_path": str(ini), "line_no": 4, "section": "Overlay",
            "occurrence": {"section": "Overlay", "ordinal": 0, "path": []},
        }],
        ib_file=other_ib_file, index_size=2)
    component01_group = {"identity_source": "component01.ini", "display_name": "Component01",
                  "name": "Component01"}
    overlay_group = {"identity_source": "component01.ini",
                     "display_name": "Overlay", "name": "Overlay"}
    context = SimpleNamespace(
        mod_dir=str(tmp_path), source=source, ini_paths=[str(ini)],
        docs=edit_session.documents_for(str(tmp_path)))
    return ini, ib, original, component01, overlay, component01_group, overlay_group, context


def test_apply_rejects_partial_overlap_with_another_draw(tmp_path, monkeypatch):
    ini, ib, original, component01, overlay, component01_group, overlay_group, context = (
        _overlap_fixture(tmp_path, 6, 6))
    monkeypatch.setattr(mesh_edit, "resolved_draws",
                        lambda _context: (None, {
                            component01.label: (component01, component01_group),
                            overlay.label: (overlay, overlay_group),
                        }))
    request = {
        "component": "Component01",
        "mesh": {
            "key": mesh_identity_for_draw(component01, component01_group).key,
            "sources": [{
                "ini": "component01.ini", "line": 2, "section": "Component01",
                "occurrence": {"section": "Component01", "ordinal": 0, "path": []},
            }],
            "parts": [[0, 2], [1, 3]],
        },
    }

    result = mesh_edit.apply_component_mesh_changes(context, request)

    assert "overlaps another draw" in result["error"]
    assert ib.read_bytes() == original
    assert not edit_session.has_pending(str(context.mod_dir))
    assert edit_session.ib_overrides_for(str(context.mod_dir)) == {}
    staged = edit_session.peek(str(context.mod_dir), str(ini)).to_string()
    assert staged.replace("\r\n", "\n") == ini.read_text(
        encoding="utf-8").replace("\r\n", "\n")


def test_apply_allows_identical_complete_overlap_with_another_draw(
        tmp_path, monkeypatch):
    ini, ib, _original, component01, overlay, component01_group, overlay_group, context = (
        _overlap_fixture(tmp_path, 0, 12))
    monkeypatch.setattr(mesh_edit, "resolved_draws",
                        lambda _context: (None, {
                            component01.label: (component01, component01_group),
                            overlay.label: (overlay, overlay_group),
                        }))
    request = {
        "component": "Component01",
        "mesh": {
            "key": mesh_identity_for_draw(component01, component01_group).key,
            "sources": [{
                "ini": "component01.ini", "line": 2, "section": "Component01",
                "occurrence": {"section": "Component01", "ordinal": 0, "path": []},
            }],
            "parts": [[0, 2], [1, 3]],
        },
    }

    result = mesh_edit.apply_component_mesh_changes(context, request)

    assert result == {"ok": True, "component": "Component01"}
    assert edit_session.has_pending(str(context.mod_dir))
    assert str(ib) in edit_session.ib_overrides_for(str(context.mod_dir))


def test_apply_ignores_unrelated_missing_index_buffer(tmp_path, monkeypatch):
    ini, ib, _original, component01, overlay, component01_group, overlay_group, context = (
        _overlap_fixture(tmp_path, 6, 6, other_ib_file="Missing.ib"))
    monkeypatch.setattr(mesh_edit, "resolved_draws",
                        lambda _context: (None, {
                            component01.label: (component01, component01_group),
                            overlay.label: (overlay, overlay_group),
                        }))
    request = {
        "component": "Component01",
        "mesh": {
            "key": mesh_identity_for_draw(component01, component01_group).key,
            "sources": [{
                "ini": "component01.ini", "line": 2, "section": "Component01",
                "occurrence": {"section": "Component01", "ordinal": 0, "path": []},
            }],
            "parts": [[0, 2], [1, 3]],
        },
    }

    result = mesh_edit.apply_component_mesh_changes(context, request)

    assert result == {"ok": True, "component": "Component01"}
    assert edit_session.has_pending(str(context.mod_dir))
    assert str(ib) in edit_session.ib_overrides_for(str(context.mod_dir))


def test_apply_tracks_each_index_buffer_dependency_to_its_sources(
        tmp_path, monkeypatch):
    a_ini = tmp_path / "a.ini"
    b_ini = tmp_path / "b.ini"
    a_ib = tmp_path / "A.ib"
    b_ib = tmp_path / "B.ib"
    a_ini.write_text("[Component01]\ndrawindexed = 6, 0, 0\n", encoding="utf-8")
    b_ini.write_text("[Component01]\ndrawindexed = 6, 0, 0\n", encoding="utf-8")
    a_ib.write_bytes(bytes(range(12)))
    b_ib.write_bytes(bytes(range(12)))
    source = DirectoryModSource(tmp_path)
    edit_session.load_documents(
        str(tmp_path), [str(a_ini), str(b_ini)], source=source)
    draw_a = DrawCall(
        label="A-1", count=6, start=0, base=0,
        sources=[{
            "ini_path": str(a_ini), "line_no": 2, "section": "Component01",
            "occurrence": {"section": "Component01", "ordinal": 0, "path": []},
        }],
        ib_file="A.ib", index_size=2)
    draw_b = DrawCall(
        label="B-1", count=6, start=0, base=0,
        sources=[{
            "ini_path": str(b_ini), "line_no": 2, "section": "Component01",
            "occurrence": {"section": "Component01", "ordinal": 0, "path": []},
        }],
        ib_file="B.ib", index_size=2)
    group_a = {"identity_source": "a.ini", "display_name": "Component01",
               "name": "Component01"}
    group_b = {"identity_source": "b.ini", "display_name": "Component01",
               "name": "Component01"}
    context = SimpleNamespace(
        mod_dir=str(tmp_path), source=source,
        ini_paths=[str(a_ini), str(b_ini)],
        docs=edit_session.documents_for(str(tmp_path)))
    monkeypatch.setattr(mesh_edit, "resolved_draws",
                        lambda _context: (None, {
                            draw_a.label: (draw_a, group_a),
                            draw_b.label: (draw_b, group_b),
                        }))

    def entry(draw, ini_name, group):
        return {
            "key": mesh_identity_for_draw(draw, group).key,
            "sources": [{
                "ini": ini_name, "line": 2, "section": "Component01",
                "occurrence": {"section": "Component01", "ordinal": 0, "path": []},
            }],
            "parts": [[1], [0]],
        }

    first = mesh_edit.apply_component_mesh_changes(context, {
        "component": "Component01",
        "mesh": entry(draw_a, "a.ini", group_a),
    })
    second = mesh_edit.apply_component_mesh_changes(context, {
        "component": "Component01",
        "mesh": entry(draw_b, "b.ini", group_b),
    })

    assert first == {"ok": True, "component": "Component01"}
    assert second == {"ok": True, "component": "Component01"}
    a_ib.write_bytes(b"external")
    exported = edit_session.export(str(tmp_path))

    assert exported["buffers_failed"] == [{
        "buffer": str(a_ib),
        "error": "The index buffer changed outside the viewer.",
    }]
    assert exported["buffers_saved"] == [str(b_ib)]
    assert exported["saved"] == ["b.ini"]
    assert exported["failed"] == []
    assert b_ib.read_bytes() != bytes(range(12))
    assert "drawindexed = 6, 0, 0" in a_ini.read_text(encoding="utf-8")

from types import SimpleNamespace
import struct

import pytest

from app.bridge import mesh_edit
from app.session import edit as edit_session
from core.geometry.draw_call import DrawCall
from core.geometry.identity import mesh_identity_for_draw
from core.geometry.semantics import _rel_source
from core.mod_source import DirectoryModSource
from tests.support_snapshot import snapshot_context
from tests.support.provenance import geometry_values, visible


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


def _auto_fixture(tmp_path, index_size=2, transform=lambda text: text):
    ini = tmp_path / "model.ini"
    text = "[TextureOverrideFamilyZ]\nhash = 10101010\nhandling = skip\ndrawindexed = auto ; original\n"
    for number, suffix in enumerate("ABC"):
        text += (
            f"[TextureOverrideFamily{suffix}]\nhash = 10101010\n"
            f"match_first_index = {number * 9}\nib = ResourceIndices{number}\n"
            "vb0 = ResourceVertices\nvb1 = ResourceUV\n"
            f"ps-t0 = ResourceTexture{number}\n"
            f"ps-t1 = ResourceLightmap{number}\n"
            "run = CommandList\\shared\\Material\\Bind\n\n; component end\n")
        text += (
            f"[ResourceIndices{number}]\ntype = Buffer\n"
            f"format = R{index_size * 8}_UINT\nfilename = indices{number}.ib\n"
            f"[ResourceTexture{number}]\nfilename = texture{number}.dds\n"
            f"[ResourceLightmap{number}]\nfilename = lightmap{number}.dds\n")
        (tmp_path / f"indices{number}.ib").write_bytes(
            struct.pack("<6" + ("H" if index_size == 2 else "I"), *range(6)))
    text += (
        "[ResourceVertices]\ntype = Buffer\nfilename = vertices.buf\nstride = 12\n"
        "[ResourceUV]\ntype = Buffer\nfilename = uv.buf\nstride = 8\n")
    ini.write_text(transform(text), encoding="utf-8")
    (tmp_path / "vertices.buf").write_bytes(struct.pack(
        "<18f", 0, 0, 0, 1, 0, 0, 0, 1, 0, 2, 0, 0, 3, 0, 0, 2, 1, 0))
    (tmp_path / "uv.buf").write_bytes(struct.pack("<12f", *([0, 0, 1, 0, 0, 1] * 2)))
    edit_session.load_documents(str(tmp_path), [str(ini)])
    return ini


def _auto_context(ini):
    documents = edit_session.documents_for(str(ini.parent))
    return snapshot_context(str(ini.parent), list(documents), documents=documents)


def _auto_request(context, suffix):
    _parsed, draws = mesh_edit.resolved_draws(context)
    draw, group = next((draw, group) for draw, group in draws.values()
                       if group["name"] == f"Family{suffix}")
    return {"component": group["name"], "mesh": {
        "key": mesh_identity_for_draw(draw, group).key,
        "sources": [_rel_source(source, context.mod_dir) for source in draw.sources],
        "parts": [[1], [0]],
    }}


@pytest.mark.parametrize("index_size", [2, 4])
@pytest.mark.parametrize("first_edit", list("ABC"))
@pytest.mark.parametrize("rebind", [False, True])
def test_shared_auto_apply_export_reload_and_subsequent_local_edits(
        tmp_path, index_size, first_edit, rebind):
    from core.geometry.mesh_builder import build_mesh_result

    ini = _auto_fixture(tmp_path, index_size, transform=(
        lambda text: text.replace("ib = ResourceIndices1",
                                  "ib = ResourceIndices0\nib = ResourceIndices1")
        if rebind else text))
    original_text = ini.read_bytes()
    originals = {number: (tmp_path / f"indices{number}.ib").read_bytes() for number in range(3)}
    parsed, _draws = mesh_edit.resolved_draws(_auto_context(ini))
    before = build_mesh_result(parsed.groups, str(tmp_path))
    original_draws = {group["name"]: group["draws"][0].render_identity() for group in parsed.groups}
    assert not edit_session.has_pending(str(tmp_path))

    order = first_edit + "".join(suffix for suffix in "ABC" if suffix != first_edit)
    for step, suffix in enumerate(order):
        context = _auto_context(ini)
        result = mesh_edit.apply_component_mesh_changes(context, _auto_request(context, suffix))
        assert result == {"ok": True, "component": f"Family{suffix}"}
        staged = edit_session.peek(str(tmp_path), str(ini))
        for component in "ABC":
            lines = [line.text for line in staged.section(f"TextureOverrideFamily{component}").lines]
            call = lines.index(r"run = CommandList\shared\Material\Bind")
            assert all(index > call for index, text in enumerate(lines)
                       if text.startswith("drawindexed ="))
            assert sum(text.startswith("run =") for text in lines) == 1
        number = "ABC".index(suffix)
        assert (tmp_path / f"indices{number}.ib").read_bytes() == originals[number]
        if step == 0:
            assert ini.read_bytes() == original_text
            staged = edit_session.peek(str(tmp_path), str(ini))
            assert [line.text for line in staged.section("TextureOverrideFamilyZ").lines] == [
                "hash = 10101010", "handling = skip"]
            assert sum(line.text == "drawindexed = auto" for line in staged.lines) == 2
        exported = edit_session.export(str(tmp_path))
        assert exported["saved"] == ["model.ini"]
        assert exported["buffers_saved"] == [str(tmp_path / f"indices{number}.ib")]
        assert not exported["failed"] and not exported["buffers_failed"]
        assert (tmp_path / f"indices{number}.ib").read_bytes() == (
            originals[number][3 * index_size:] + originals[number][:3 * index_size])
        edit_session.discard(str(tmp_path))
        edit_session.load_documents(str(tmp_path), [str(ini)])
        parsed, _draws = mesh_edit.resolved_draws(_auto_context(ini))
        after = build_mesh_result(parsed.groups, str(tmp_path))
        assert len(after.meshes) == 4 + step
        for group in parsed.groups:
            if group["name"][-1] in order[step + 1:]:
                label = group["name"] + "-1"
                assert group["draws"][0].render_identity() == original_draws[group["name"]]
                for field in ("pos", "idx", "uv"):
                    assert geometry_values(before.geometry, before.meshes[label][field]) == geometry_values(
                        after.geometry, after.meshes[label][field])


def _guard_auto_sections(text, guard):
    from core.ini.document import IniDocument

    doc = IniDocument.from_string(text)
    for section in reversed(doc.sections):
        if section.name.startswith("TextureOverrideFamily"):
            doc.replace_lines(section.end, section.end, ["endif"] * len(guard))
            doc.replace_lines(section.start + 1, section.start + 1,
                              [line for branch in guard for line in branch])
    return (
        "[Constants]\nglobal persist $enabled = 1\nglobal persist $mode = 1\n"
        "[KeyEnabled]\nkey = k\ntype = cycle\n$enabled = 0, 1\n"
        "[KeyMode]\nkey = m\ntype = cycle\n$mode = 0, 1, 2\n" + doc.to_string())


@pytest.mark.parametrize("guard", [
    (("if $enabled == 1",),),
    (("if $mode == 0", "elif $mode == 1"), ("if $enabled == 1",)),
    (("if $mode == 0", "else if $mode == 1", "else"), ("if $enabled == 1",)),
    ((r"if $\shared\variant == 3",), ("if $enabled == 1",)),
])
@pytest.mark.parametrize("call", [
    "run = CommandListMissing",
    "run = CommandList\\shared\\Material\\Bind\nps-t0 = null",
    "if $enabled == 1\nrun = CommandListMissing\nendif",
])
def test_guarded_shared_auto_migrates_and_preserves_sibling_draws(tmp_path, guard, call):
    def transform(text):
        text = text.replace(r"run = CommandList\shared\Material\Bind", "")
        for number in range(3):
            text = text.replace(f"ib = ResourceIndices{number}",
                                f"{call}\nib = ResourceIndices{number}")
        return _guard_auto_sections(text, guard)

    ini = _auto_fixture(tmp_path, index_size=4, transform=transform)
    original = ini.read_bytes()
    original_sections = {
        section.name: [line.raw for line in section.lines]
        for section in edit_session.peek(str(tmp_path), str(ini)).sections}
    original_buffers = {number: (tmp_path / f"indices{number}.ib").read_bytes()
                        for number in range(3)}
    _parsed, draws = mesh_edit.resolved_draws(_auto_context(ini))
    conditions = {group["name"]: draw.conditions for draw, group in draws.values()}
    assert all(draw.auto_draw and draw.auto_draw["family"]
               for draw, _group in draws.values())
    active_mode = "2" if any("else" in branch for branch in guard) else "1"
    for draw, _group in draws.values():
        assert not visible(draw.conditions, {"enabled": "0", "mode": active_mode})
        assert visible(draw.conditions, {"enabled": "1", "mode": active_mode})
    assert not edit_session.has_pending(str(tmp_path))

    for step, suffix in enumerate("ABC"):
        context = _auto_context(ini)
        assert mesh_edit.apply_component_mesh_changes(
            context, _auto_request(context, suffix)) == {
                "ok": True, "component": f"Family{suffix}"}
        staged = edit_session.peek(str(tmp_path), str(ini))
        assert not staged.structure_errors()
        if step == 0:
            assert ini.read_bytes() == original
            assert not staged.section("TextureOverrideFamilyZ").find("draw")
            for number, component in enumerate("ABC"):
                section = staged.section(f"TextureOverrideFamily{component}")
                texts = [line.text for line in section.lines]
                expected = ([line for branch in guard for line in branch]
                            + (["drawindexed = 3, 0, 0", "drawindexed = 3, 3, 0"]
                               if component == "A" else ["drawindexed = auto"])
                            + ["endif"] * len(guard))
                assert texts[-len(expected):] == expected
                assert [line.raw for line in section.lines[:-len(expected)]] == original_sections[section.name]
                assert any("; original" in line.raw for line in section.find("draw"))
                assert texts.count("run = " + call.split("run = ", 1)[1].splitlines()[0]) == 1
                assert (tmp_path / f"indices{number}.ib").read_bytes() == original_buffers[number]
        _parsed, current = mesh_edit.resolved_draws(_auto_context(ini))
        for draw, group in current.values():
            assert draw.conditions == conditions[group["name"]]
        exported = edit_session.export(str(tmp_path))
        assert exported["saved"] == ["model.ini"]
        assert len(exported["buffers_saved"]) == 1
        assert not exported["failed"] and not exported["buffers_failed"]
        for number in range(3):
            expected_buffer = original_buffers[number]
            if number <= step:
                expected_buffer = expected_buffer[12:] + expected_buffer[:12]
            assert (tmp_path / f"indices{number}.ib").read_bytes() == expected_buffer
        edit_session.discard(str(tmp_path))
        edit_session.load_documents(str(tmp_path), [str(ini)])
        _parsed, current = mesh_edit.resolved_draws(_auto_context(ini))
        assert len(current) == 4 + step
        for draw, group in current.values():
            assert draw.conditions == conditions[group["name"]]


@pytest.mark.parametrize("old,new", [
    ("handling = skip", ""),
    ("handling = skip", "if $enabled == 1\nhandling = skip\nendif"),
    ("handling = skip", "handling = skip\nps-t0 = ResourceTexture0"),
    ("match_first_index = 9", "match_first_index = 0"),
    ("match_first_index = 9", "match_first_index = 9\nmatch_index_count = 6"),
    ("TextureOverrideFamilyZ", "TextureOverrideFamily0"),
    ("match_first_index = 9", "match_first_index = 9\nmatch_priority = 1"),
    ("drawindexed = auto ; original", "post drawindexed = auto"),
    ("ib = ResourceIndices1", "if $enabled == 1\nib = ResourceIndices1\nendif"),
    ("ib = ResourceIndices1", "ib = ResourceIndices1\nib = ResourceIndices1"),
    ("ib = ResourceIndices1", "ib = ResourceIndices0\nib = ResourceIndices1"),
    ("ib = ResourceIndices1", "post ib = ResourceIndices1"),
    (r"run = CommandList\shared\Material\Bind",
     r"post run = CommandList\shared\Material\Bind"),
    (r"run = CommandList\shared\Material\Bind", "run = CustomShaderMissing"),
    (r"run = CommandList\shared\Material\Bind", "run = CommandListRecursive"),
])
def test_shared_auto_migration_ignores_execution_restrictions(tmp_path, old, new):
    def transform(text):
        text = text.replace(old, new)
        if new == "run = CommandListRecursive":
            text += "[CommandListRecursive]\nrun = CommandListRecursive\n"
        return text

    ini = _auto_fixture(tmp_path, transform=transform)
    original = ini.read_bytes()
    original_buffers = {number: (tmp_path / f"indices{number}.ib").read_bytes()
                        for number in range(3)}
    documents = edit_session.peek(str(tmp_path), str(ini))
    bodies = {section.name: [line.raw for line in section.lines]
              for section in documents.sections}
    draw_line = next(line for line in documents.lines
                     if line.text.endswith("drawindexed = auto"))
    owner = draw_line.section.name
    phase = "post " if draw_line.text.startswith("post ") else ""
    context = _auto_context(ini)
    assert mesh_edit.apply_component_mesh_changes(
        context, _auto_request(context, "A")) == {"ok": True, "component": "FamilyA"}
    staged = edit_session.peek(str(tmp_path), str(ini))
    assert not staged.structure_errors()
    assert ini.read_bytes() == original
    assert [line.raw for line in staged.section(owner).lines] == [
        raw for raw in bodies[owner] if raw != draw_line.raw]
    for number, component in enumerate("ABC"):
        section = staged.section(f"TextureOverrideFamily{component}")
        expected = ([f"{phase}drawindexed = 3, 0, 0",
                     f"{phase}drawindexed = 3, 3, 0"]
                    if component == "A" else [f"{phase}drawindexed = auto"])
        assert [line.text for line in section.lines[-len(expected):]] == expected
        assert [line.raw for line in section.lines[:-len(expected)]] == bodies[section.name]
        assert (tmp_path / f"indices{number}.ib").read_bytes() == original_buffers[number]
    exported = edit_session.export(str(tmp_path))
    assert exported["saved"] == ["model.ini"]
    assert exported["buffers_saved"] == [str(tmp_path / "indices0.ib")]
    assert not exported["failed"] and not exported["buffers_failed"]
    edit_session.discard(str(tmp_path))
    edit_session.load_documents(str(tmp_path), [str(ini)])
    _parsed, draws = mesh_edit.resolved_draws(_auto_context(ini))
    assert len(draws) == 4
    for number in range(3):
        expected = original_buffers[number]
        if number == 0:
            expected = expected[6:] + expected[:6]
        assert (tmp_path / f"indices{number}.ib").read_bytes() == expected


@pytest.mark.parametrize("old,new", [
    ("drawindexed = auto ; original", "drawindexed = auto\ndrawindexed = auto"),
    ("ib = ResourceIndices1", "ib = ResourceIndices0"),
    ("filename = indices1.ib", "filename = indices0.ib"),
    ("format = R16_UINT", "format = UNKNOWN"),
    ("filename = indices1.ib", "filename = missing.ib"),
    ("filename = indices1.ib", "filename = indices1.ib\nbyte_width = 6"),
])
def test_shared_auto_rejects_ambiguous_families_without_partial_staging(tmp_path, old, new):
    ini = _auto_fixture(tmp_path, transform=lambda text: text.replace(old, new))
    context = _auto_context(ini)
    original = ini.read_bytes()
    result = mesh_edit.apply_component_mesh_changes(context, _auto_request(context, "A"))
    assert "error" in result
    assert not edit_session.has_pending(str(tmp_path))
    assert not edit_session.ib_overrides_for(str(tmp_path))
    assert edit_session.peek(str(tmp_path), str(ini)).to_string().encode() == original
    assert ini.read_bytes() == original


@pytest.mark.parametrize("guarded", [False, True])
def test_auto_migration_rolls_back_every_section_and_buffer_on_staging_failure(
        tmp_path, monkeypatch, guarded):
    ini = _auto_fixture(tmp_path, transform=(
        lambda text: _guard_auto_sections(text, (("if $enabled == 1",),))
        if guarded else text))
    context = _auto_context(ini)
    request = _auto_request(context, "B")
    original = ini.read_bytes()
    stage = edit_session._EditTransaction.stage_ib_edit

    def fail_after_stage(transaction, *args, **kwargs):
        stage(transaction, *args, **kwargs)
        raise ValueError("simulated staging failure")

    monkeypatch.setattr(edit_session._EditTransaction, "stage_ib_edit", fail_after_stage)
    result = mesh_edit.apply_component_mesh_changes(context, request)
    assert result == {"error": "simulated staging failure"}
    assert not edit_session.has_pending(str(tmp_path))
    assert not edit_session.ib_overrides_for(str(tmp_path))
    assert edit_session.peek(str(tmp_path), str(ini)).to_string().encode() == original


@pytest.mark.parametrize("phase", ["", "post "])
def test_local_auto_rewrites_in_place_without_migration_restrictions(tmp_path, phase):
    def local_draws(text):
        text = text.replace("ps-t0 = ResourceTexture1", "ps-t0 = ResourceTexture1\n"
                            "Resource\\GIMI\\Diffuse = ResourceTexture1")
        text = text.replace("drawindexed = auto ; original\n", "").replace(
            "run = CommandList\\shared\\Material\\Bind\n",
            "run = CommandList\\shared\\Material\\Bind\n"
            f"if $ready == 1\n{phase}drawindexed = auto ; local\nendif\n"
            f"{phase}ps-t0 = null\n{phase}Resource\\GIMI\\Diffuse = null\n"
            f"{phase}vb0 = null\n")
        return ("[Constants]\nglobal persist $ready = 0\n"
                "[KeyReady]\nkey = k\ntype = cycle\n$ready = 0, 1\n" + text)

    ini = _auto_fixture(tmp_path, transform=local_draws)
    context = _auto_context(ini)
    _parsed, draws = mesh_edit.resolved_draws(context)
    original = next(draw for draw, group in draws.values() if group["name"] == "FamilyB")
    assert original.conditions == [[{"var": "ready", "value": "1", "negate": False}]]
    assert not visible(original.conditions, {"ready": "0"})
    assert visible(original.conditions, {"ready": "1"})
    assert original.position_file == "vertices.buf"
    assert original.texture_default_file == "texture1.dds"
    request = _auto_request(context, "B")
    before = context.docs[str(ini)].to_string().replace("\r\n", "\n").splitlines(keepends=True)
    line = request["mesh"]["sources"][0]["line"]
    before[line - 1:line] = [
        f"{phase}drawindexed = 3, 0, 0 ; local\n", f"{phase}drawindexed = 3, 3, 0\n"]
    assert mesh_edit.apply_component_mesh_changes(context, request) == {
        "ok": True, "component": "FamilyB"}
    assert edit_session.peek(str(tmp_path), str(ini)).to_string().replace("\r\n", "\n") == "".join(before)
    for exported in (False, True):
        if exported:
            result = edit_session.export(str(tmp_path))
            assert result["saved"] == ["model.ini"]
            assert not result["failed"] and not result["buffers_failed"]
            edit_session.discard(str(tmp_path))
            edit_session.load_documents(str(tmp_path), [str(ini)])
        _parsed, draws = mesh_edit.resolved_draws(_auto_context(ini))
        parts = [draw for draw, group in draws.values() if group["name"] == "FamilyB"]
        assert [(draw.count, draw.start) for draw in parts] == [(3, 0), (3, 3)]
        for draw in parts:
            assert draw.conditions == original.conditions
            assert draw.animation_conditions == original.animation_conditions
            assert draw.position_file == original.position_file
            assert draw.texcoord_file == original.texcoord_file
            assert draw.texture_default_file == original.texture_default_file
            assert draw.slot_textures == original.slot_textures


@pytest.mark.parametrize("corruption", [
    "partial-index", "partial-triangle", "sibling-triangle", "duplicate-source", "synthetic",
    "sibling-ini", "shared-local-source",
])
def test_auto_rejects_invalid_buffer_or_provenance(tmp_path, corruption, monkeypatch):
    def shared_source(text):
        if corruption != "shared-local-source":
            return text
        return text.replace("drawindexed = auto ; original\n", "").replace(
            r"run = CommandList\shared\Material\Bind", "run = CommandListDraw") + (
                "[CommandListDraw]\ndrawindexed = auto\n")

    ini = _auto_fixture(tmp_path, transform=shared_source)
    if corruption == "sibling-ini":
        other = tmp_path / "other.ini"
        other.write_text("[TextureOverrideOther]\nhash = 10101010\n", encoding="utf-8")
        edit_session.load_documents(str(tmp_path), [str(other)])
    context = _auto_context(ini)
    request = _auto_request(context, "B")
    if corruption in {"partial-index", "partial-triangle"}:
        path = tmp_path / "indices1.ib"
        path.write_bytes(path.read_bytes()[:-1 if corruption == "partial-index" else -2])
    elif corruption == "sibling-triangle":
        path = tmp_path / "indices2.ib"
        path.write_bytes(path.read_bytes()[:-2])
    elif corruption == "duplicate-source":
        request["mesh"]["sources"] *= 2
    elif corruption == "synthetic":
        parsed, draws = mesh_edit.resolved_draws(context)
        next(draw for draw, group in draws.values() if group["name"] == "FamilyB").auto_draw = None
        monkeypatch.setattr(mesh_edit, "resolved_draws", lambda _context: (parsed, draws))
    result = mesh_edit.apply_component_mesh_changes(context, request)
    assert "error" in result
    assert not edit_session.has_pending(str(tmp_path))
    assert not edit_session.ib_overrides_for(str(tmp_path))

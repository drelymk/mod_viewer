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
from tests.support.provenance import geometry_values


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
            f"ps-t0 = ResourceTexture{number}\nrun = CommandListBindings\n")
        text += (
            f"[ResourceIndices{number}]\ntype = Buffer\n"
            f"format = R{index_size * 8}_UINT\nfilename = indices{number}.ib\n"
            f"[ResourceTexture{number}]\nfilename = texture{number}.dds\n")
        (tmp_path / f"indices{number}.ib").write_bytes(
            struct.pack("<6" + ("H" if index_size == 2 else "I"), *range(6)))
    text += (
        "[ResourceVertices]\nfilename = vertices.buf\nstride = 12\n"
        "[ResourceUV]\nfilename = uv.buf\nstride = 8\n"
        "[CommandListBindings]\nif $enabled == 1\nps-t1 = ResourceTexture0\nendif\n")
    ini.write_text(transform(text), encoding="utf-8")
    (tmp_path / "vertices.buf").write_bytes(struct.pack(
        "<18f", 0, 0, 0, 1, 0, 0, 0, 1, 0, 2, 0, 0, 3, 0, 0, 2, 1, 0))
    (tmp_path / "uv.buf").write_bytes(struct.pack("<12f", *([0, 0, 1, 0, 0, 1] * 2)))
    edit_session.load_documents(str(tmp_path), [str(ini)])
    return ini


def _auto_context(ini):
    return snapshot_context(str(ini.parent), [ini], documents=edit_session.documents_for(str(ini.parent)))


def _auto_request(context, suffix):
    _parsed, draws = mesh_edit.resolved_draws(context)
    draw, group = next((draw, group) for draw, group in draws.values()
                       if group["name"] == f"Family{suffix}")
    return {"component": group["name"], "mesh": {
        "key": mesh_identity_for_draw(draw, group).key,
        "sources": [_rel_source(source, context.mod_dir) for source in draw.sources],
        "parts": [[1], [0]],
    }}


def _runtime_draws(doc, first_index, buffers):
    """Small source-order trace of the supported 3DMigoto override contract."""
    state, draws = {}, []
    sections = {section.name.casefold(): section for section in doc.sections}

    def value(section, key, default=None):
        return next((line.text.partition("=")[2].strip() for line in section.lines
                     if line.text.partition("=")[0].strip().casefold() == key), default)

    def execute(section):
        for line in section.lines:
            key, _, target = line.text.partition("=")
            key, target = key.strip().casefold(), target.strip()
            if key in {"ib", "vb0", "vb1", "ps-t0"}:
                state[key] = target
            elif key == "run":
                execute(sections[target.casefold()])
            elif key == "handling":
                state["handling"] = target
            elif key == "drawindexed":
                data, size = buffers[state["ib"]]
                count, start, base = ((len(data) // size, 0, 0) if target == "auto"
                                      else tuple(map(int, target.split(","))))
                draws.append((state["ib"], data[start * size:(start + count) * size],
                              base, state["vb0"], state["vb1"], state["ps-t0"]))

    family = [section for section in doc.sections
              if value(section, "hash") == "10101010" and
              value(section, "match_first_index", str(first_index)) == str(first_index)]
    for section in sorted(family, key=lambda section: (
            int(value(section, "match_priority", "0")), section.name.casefold())):
        execute(section)
    return draws, state["handling"]


@pytest.mark.parametrize("index_size", [2, 4])
@pytest.mark.parametrize("first_edit", list("ABC"))
def test_shared_auto_apply_export_reload_and_subsequent_local_edits(
        tmp_path, index_size, first_edit):
    from core.geometry.mesh_builder import build_mesh_result

    ini = _auto_fixture(tmp_path, index_size)
    original_text = ini.read_bytes()
    originals = {number: (tmp_path / f"indices{number}.ib").read_bytes() for number in range(3)}
    buffers = {f"ResourceIndices{number}": (data, index_size) for number, data in originals.items()}
    context = _auto_context(ini)
    parsed, _draws = mesh_edit.resolved_draws(context)
    before = build_mesh_result(parsed.groups, str(tmp_path))
    original_draws = {group["name"]: (group["draws"][0].render_identity(),
                                     group["draws"][0].conditions)
                      for group in parsed.groups}
    original_doc = context.docs[str(ini)]
    traces = {number: _runtime_draws(original_doc, number * 9, buffers) for number in range(3)}
    # Loading and previewing do not create a pending edit.
    assert not edit_session.has_pending(str(tmp_path))

    order = first_edit + "".join(suffix for suffix in "ABC" if suffix != first_edit)
    for step, suffix in enumerate(order):
        context = _auto_context(ini)
        result = mesh_edit.apply_component_mesh_changes(context, _auto_request(context, suffix))
        assert result == {"ok": True, "component": f"Family{suffix}"}
        number = "ABC".index(suffix)
        assert (tmp_path / f"indices{number}.ib").read_bytes() == originals[number]
        if step == 0:
            assert ini.read_bytes() == original_text
            staged = edit_session.peek(str(tmp_path), str(ini))
            shared = next(section for section in staged.sections if section.name == "TextureOverrideFamilyZ")
            assert [line.text for line in shared.lines] == ["hash = 10101010", "handling = skip"]
            assert sum(line.text == "drawindexed = auto" for line in staged.lines) == 2
        exported = edit_session.export(str(tmp_path))
        assert exported["saved"] == ["model.ini"]
        assert exported["buffers_saved"] == [str(tmp_path / f"indices{number}.ib")]
        assert not exported["failed"] and not exported["buffers_failed"]
        assert not edit_session.has_pending(str(tmp_path))
        buffers[f"ResourceIndices{number}"] = (
            (tmp_path / f"indices{number}.ib").read_bytes(), index_size)
        assert buffers[f"ResourceIndices{number}"][0] == (
            originals[number][3 * index_size:] + originals[number][:3 * index_size])
        context = _auto_context(ini)
        parsed, _draws = mesh_edit.resolved_draws(context)
        after = build_mesh_result(parsed.groups, str(tmp_path))
        assert len(after.meshes) == 3 + step + 1
        for untouched in (item for item in "ABC" if item not in order[:step + 1]):
            label = f"Family{untouched}-1"
            group = next(group for group in parsed.groups if group["name"] == f"Family{untouched}")
            assert (group["draws"][0].render_identity(), group["draws"][0].conditions) == original_draws[group["name"]]
            # Geometry blob offsets change as parts are added, compare decoded arrays.
            for field in ("pos", "idx", "uv"):
                assert geometry_values(before.geometry, before.meshes[label][field]) == geometry_values(
                    after.geometry, after.meshes[label][field])
        for component in range(3):
            current, skip = _runtime_draws(context.docs[str(ini)], component * 9, buffers)
            original, original_skip = traces[component]
            assert skip == original_skip == "skip"
            assert all((draw[0], *draw[2:]) == (original[0][0], *original[0][2:]) for draw in current)
            actual_triangles = sorted(draw[1][offset:offset + 3 * index_size]
                                      for draw in current for offset in range(0, len(draw[1]), 3 * index_size))
            expected_triangles = sorted(original[0][1][offset:offset + 3 * index_size]
                                        for offset in range(0, len(original[0][1]), 3 * index_size))
            assert actual_triangles == expected_triangles


@pytest.mark.parametrize("old,new", [
    ("drawindexed = auto ; original", "drawindexed = auto\ndrawindexed = auto"),
    ("handling = skip", "if $enabled == 1\nhandling = skip\nendif"),
    ("handling = skip", "handling = skip\nps-t0 = ResourceTexture0"),
    ("match_first_index = 9", "match_first_index = 0"),
    ("match_first_index = 9", "match_first_index = 9\nmatch_index_count = 6"),
    ("ib = ResourceIndices1", "ib = ResourceIndices0"),
    ("filename = indices1.ib", "filename = indices0.ib"),
    ("TextureOverrideFamilyZ", "TextureOverrideFamily0"),
    ("match_first_index = 9", "match_first_index = 9\nmatch_priority = 1"),
    ("if $enabled == 1", "post ps-t0 = ResourceTexture0\nif $enabled == 1"),
    ("if $enabled == 1", "ib = ResourceIndices0\nif $enabled == 1"),
    ("format = R16_UINT", "format = UNKNOWN"),
    ("filename = indices1.ib", "filename = missing.ib"),
    ("drawindexed = auto ; original", "post drawindexed = auto"),
    ("run = CommandListBindings", "drawindexed = auto"),
    ("if $enabled == 1", "run = CommandListBindings\nif $enabled == 1"),
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


def test_auto_migration_rolls_back_every_section_and_buffer_on_staging_failure(tmp_path, monkeypatch):
    ini = _auto_fixture(tmp_path)
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


def test_authored_local_auto_rewrites_in_place_with_its_comment_and_commands(tmp_path):
    def local_draws(text):
        return text.replace("drawindexed = auto ; original\n", "").replace(
            "run = CommandListBindings\n", "run = CommandListBindings\ndrawindexed = auto ; local\n").replace(
            "match_first_index = 9\n", "match_first_index = 9\nhandling = skip\n")

    ini = _auto_fixture(tmp_path, transform=local_draws)
    context = _auto_context(ini)
    original = edit_session.peek(str(tmp_path), str(ini)).to_string().replace("\r\n", "\n")
    assert mesh_edit.apply_component_mesh_changes(context, _auto_request(context, "B")) == {
        "ok": True, "component": "FamilyB"}
    expected = original.replace(
        "handling = skip\nib = ResourceIndices1\nvb0 = ResourceVertices\nvb1 = ResourceUV\n"
        "ps-t0 = ResourceTexture1\nrun = CommandListBindings\ndrawindexed = auto ; local",
        "handling = skip\nib = ResourceIndices1\nvb0 = ResourceVertices\nvb1 = ResourceUV\n"
        "ps-t0 = ResourceTexture1\nrun = CommandListBindings\ndrawindexed = 3, 0, 0 ; local\n"
        "drawindexed = 3, 3, 0")
    assert expected != original
    assert edit_session.peek(str(tmp_path), str(ini)).to_string().replace("\r\n", "\n") == expected


@pytest.mark.parametrize("corruption", [
    "partial-index", "partial-triangle", "sibling-triangle", "duplicate-source", "synthetic",
])
def test_auto_rejects_invalid_buffer_or_provenance(tmp_path, corruption, monkeypatch):
    ini = _auto_fixture(tmp_path)
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
    else:
        parsed, draws = mesh_edit.resolved_draws(context)
        next(draw for draw, group in draws.values() if group["name"] == "FamilyB").auto_draw = None
        monkeypatch.setattr(mesh_edit, "resolved_draws", lambda _context: (parsed, draws))
    result = mesh_edit.apply_component_mesh_changes(context, request)
    assert "error" in result
    assert not edit_session.has_pending(str(tmp_path))
    assert not edit_session.ib_overrides_for(str(tmp_path))

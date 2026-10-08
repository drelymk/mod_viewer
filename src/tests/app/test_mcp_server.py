"""MCP filesystem authorization contracts."""

import pytest
import json
import urllib.request
import zipfile
from urllib.error import HTTPError

import mcp_server
from app.session import edit as edit_session
from app.settings import mod_folders as mod_folders
from core.ini.document import IniDocument
from app.runtime import server
from core.geometry.transport import GeometryBlob
from tests.support.model_data import basic_model_ini, triangle_geometry
from tests.support.dds_data import write_bc7_dds


def _entry(path):
    return [{"name": "Root", "path": mod_folders.normalize_path(path)}]


@pytest.fixture(autouse=True)
def isolated_inspection_publication(monkeypatch):
    monkeypatch.setattr(mcp_server, "_inspection_publication", None)
    yield
    server.release_texture_publication(mcp_server._inspection_publication)


@pytest.mark.parametrize("call", [
    lambda path: mcp_server.inspect_mod(path),
    lambda path: mcp_server.list_toggle_source_inis(path),
    lambda path: mcp_server.get_toggle_details(path, "mod.ini", "KeyA"),
    lambda path: mcp_server.add_mod_toggle(
        path, "mod.ini", "Toggle", "F1", "Mode", ["0", "1"]),
    lambda path: mcp_server.edit_mod_toggle(path, "mod.ini", "KeyA", {}),
    lambda path: mcp_server.delete_mod_toggle(path, "mod.ini", "KeyA"),
    lambda path: mcp_server.export_mod_changes(path),
    lambda path: mcp_server.discard_mod_changes(path),
])
def test_every_mcp_tool_rejects_unregistered_folder(tmp_path, monkeypatch, call):
    monkeypatch.setattr(mcp_server.mod_folders, "load_registry", lambda: [])

    with pytest.raises(PermissionError):
        call(str(tmp_path))


def test_mcp_allows_registered_root_descendant_before_loading(tmp_path, monkeypatch):
    root = tmp_path / "library"
    child = root / "mod"
    child.mkdir(parents=True)
    monkeypatch.setattr(mcp_server.mod_folders, "load_registry", lambda: _entry(root))
    seen = []

    def fake_load(**kwargs):
        seen.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(mcp_server.mod_loader, "load_mod", fake_load)

    assert mcp_server.inspect_mod(str(child)) == {"ok": True}
    assert len(seen) == 1
    assert seen[0]["context"].mod_dir == mod_folders.normalize_path(child)
    assert seen[0]["context"].docs == {}
    assert seen[0]["pending_new_sections"] == {}
    assert seen[0]["texture_source"].__self__ is mcp_server._inspection_publication
    assert seen[0]["menu_image_source"].__self__ is mcp_server._inspection_publication


def test_inspect_mod_passes_staged_documents_without_serializing(tmp_path, monkeypatch):
    root = tmp_path / "library"
    child = root / "mod"
    child.mkdir(parents=True)
    path = str(child / "A.ini")
    sibling = str(child / "B.ini")
    (child / "A.ini").write_text("[Constants]\nglobal $mode = 0\n", encoding="utf-8")
    (child / "B.ini").write_text("[Constants]\nglobal $other = 0\n", encoding="utf-8")
    folder = mod_folders.normalize_path(child)
    document = IniDocument.from_string("[Constants]\nglobal $mode = 1\n", path=path)
    edit_session.load_documents(folder, [path], documents={path: document})
    monkeypatch.setattr(mcp_server.mod_folders, "load_registry", lambda: _entry(root))
    monkeypatch.setattr(
        edit_session,
        "overrides_for",
        lambda _folder: (_ for _ in ()).throw(
            AssertionError("staged text was serialized")
        ),
    )
    captured = {}

    def load(**kwargs):
        captured.update(kwargs)
        context = kwargs["context"]
        return {"paths": context.ini_paths, "documents": context.docs}

    monkeypatch.setattr(mcp_server.mod_loader, "load_mod", load)
    try:
        result = mcp_server.inspect_mod(folder)
        assert "ini_paths" not in captured
        assert {item.casefold() for item in result["paths"]} == {
            path.casefold(),
            sibling.casefold(),
        }
        assert captured["context"].docs == result["documents"]
        assert any(item is document for item in result["documents"].values())
        assert any(
            item.casefold() == sibling.casefold() for item in result["documents"]
        )
    finally:
        edit_session.discard(folder)


@pytest.mark.parametrize("archived", [False, True])
def test_direct_and_mcp_loads_publish_accessible_geometry_and_textures(
    tmp_path, monkeypatch, archived
):
    monkeypatch.setattr(server.paths, "has_vendored_three", lambda: False)
    monkeypatch.setattr(server, "_geometry_blobs", {})
    monkeypatch.setattr(server, "_texture_publications", {})
    monkeypatch.setattr(server, "_active_texture_publication", None)
    mcp_server._preview_base_url.cache_clear()
    with pytest.raises(RuntimeError, match="Vendored Three.js"):
        server.start()
    ini = basic_model_ini().replace(
        "ib = ResourceComponent01IB\n",
        "ib = ResourceComponent01IB\nps-t0 = ResourceComponent01Diffuse\n",
    )
    ini += (
        "[ResourceComponent01Diffuse]\nfilename = diffuse.dds\n"
        "[Constants]\nglobal $palette = 0\nglobal $anchor = 0\nglobal $guide = 0\n"
        "[CommandListButton7Right]\n$palette = $palette + 1\n"
        "if $palette > 1\n$palette = 0\nendif\n"
        "[CommandListButton8Right]\n$anchor = $anchor + 1\n"
        "if $anchor > 1\n$anchor = 0\nendif\n"
        "[CommandListButton9Right]\n$guide = $guide + 1\n"
        "if $guide > 1\n$guide = 0\nendif\n"
        "[CommandListIcon7]\nps-t100 = ResourceIcon\n"
        "[ResourceIcon]\nfilename = icon.dds\n"
    )
    (tmp_path / "mod.ini").write_text(ini, encoding="utf-8")
    diffuse = tmp_path / "diffuse.dds"
    icon = tmp_path / "icon.dds"
    write_bc7_dds(diffuse)
    write_bc7_dds(icon)
    buffers = triangle_geometry()
    for name, data in buffers.items():
        (tmp_path / name).write_bytes(data)
    load_path = str(tmp_path)
    if archived:
        archive_path = tmp_path / "model.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            for path in (
                tmp_path / "mod.ini",
                diffuse,
                icon,
                *(tmp_path / name for name in buffers),
            ):
                archive.write(path, "Pack/" + path.name)
        load_path = str(archive_path)
    monkeypatch.setattr(
        mcp_server.mod_folders, "load_registry", lambda: _entry(tmp_path)
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base_url = mcp_server._preview_base_url()

    direct = mcp_server.mod_loader.load_mod(load_path)
    supplied = GeometryBlob()
    owned = mcp_server.mod_loader.load_mod(load_path, geometry=supplied)
    assert not direct.get("error") and not owned.get("error")
    assert owned["geometry"] is None and len(supplied) > 0

    def reject_conversion(*_args, **_kwargs):
        raise AssertionError("MCP DDS inspection entered PNG conversion")

    monkeypatch.setattr("PIL.Image.Image.save", reject_conversion)
    viewer = server.begin_texture_publication(str(tmp_path))
    viewer_texture_url = base_url + viewer.register(str(diffuse))
    viewer.commit()
    inspected = mcp_server.inspect_mod(load_path)
    json.dumps(inspected)
    assert not inspected.get("error")
    assert inspected["geometry"]["url"].startswith(base_url + "/geometry/")
    texture_url = inspected["textures"]["diffuse::diffuse.dds"]
    menu_url = next(
        item["image"]
        for item in inspected["controls"]["menu"].values()
        if item.get("image")
    )
    for url, expected in (
        (texture_url, diffuse.read_bytes()),
        (menu_url, icon.read_bytes()),
        (viewer_texture_url, diffuse.read_bytes()),
    ):
        assert url.startswith(base_url + "/texture/") and url.endswith(".dds")
        with opener.open(url, timeout=5) as response:
            assert response.headers["Content-Type"] == "image/vnd-ms.dds"
            assert response.read() == expected
    assert server.active_texture_publication() is viewer
    for payload, url in (
        (direct, base_url + direct["geometry"]["url"]),
        (inspected, inspected["geometry"]["url"]),
    ):
        with opener.open(url, timeout=5) as response:
            blob = response.read()
        assert len(blob) == payload["geometry"]["length"] == len(supplied)
        assert blob == supplied.to_bytes()
        mesh = next(iter(payload["meshes"].values()))
        ref = mesh["pos"]
        assert blob[ref["offset"] : ref["offset"] + ref["length"]] == buffers["p.buf"]

    viewer_url = base_url + server.publish_geometry(b"viewer")
    weight_url = base_url + server.publish_geometry(b"weight", replace=False)
    inspections = [mcp_server.inspect_mod(load_path) for _ in range(8)]
    assert len(server._texture_publications) == 2
    with pytest.raises(HTTPError) as expired_texture:
        opener.open(texture_url, timeout=5)
    assert expired_texture.value.code == 404
    assert server.geometry_stats() == {
        "pending_blob_count": 4,
        "pending_blob_bytes": 12 + 2 * len(supplied),
    }
    with pytest.raises(HTTPError) as expired:
        opener.open(inspections[0]["geometry"]["url"], timeout=5)
    assert expired.value.code == 404

    # Scale the aggregate byte limit down without allocating large fixtures.
    monkeypatch.setattr(server, "_MAX_GEOMETRY_BYTES", len(supplied))
    latest = mcp_server.inspect_mod(load_path)
    previous_publication = mcp_server._inspection_publication
    missing = tmp_path / "missing"
    missing.mkdir()
    assert mcp_server.inspect_mod(str(missing)).get("error")
    assert mcp_server._inspection_publication is previous_publication
    with monkeypatch.context() as failure:

        def broken_load(**kwargs):
            kwargs["texture_source"](str(diffuse))
            raise RuntimeError("inspection failed")

        failure.setattr(mcp_server.mod_loader, "load_mod", broken_load)
        with pytest.raises(RuntimeError, match="inspection failed"):
            mcp_server.inspect_mod(load_path)
    assert len(server._texture_publications) == 2
    for url in (latest["textures"]["diffuse::diffuse.dds"], viewer_texture_url):
        with opener.open(url, timeout=5) as response:
            assert response.read() == diffuse.read_bytes()
    assert server.geometry_stats() == {
        "pending_blob_count": 3,
        "pending_blob_bytes": 12 + len(supplied),
    }
    with pytest.raises(ValueError, match="safety limit"):
        server.publish_geometry(bytes(len(supplied) + 1), replace=False, auxiliary=True)
    for url, expected in (
        (viewer_url, b"viewer"),
        (weight_url, b"weight"),
        (latest["geometry"]["url"], supplied.to_bytes()),
    ):
        with opener.open(url, timeout=5) as response:
            assert response.read() == expected
        with pytest.raises(HTTPError) as consumed:
            opener.open(url, timeout=5)
        assert consumed.value.code == 404
    assert server.geometry_stats() == {
        "pending_blob_count": 0,
        "pending_blob_bytes": 0,
    }
    mcp_server._preview_base_url.cache_clear()


def test_mcp_reads_registry_for_each_invocation(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    state = [_entry(root), []]
    monkeypatch.setattr(
        mcp_server.mod_folders, "load_registry", lambda: state.pop(0))

    assert mcp_server._authorized_mod_folder(str(root)) == (
        mod_folders.normalize_path(root))
    with pytest.raises(PermissionError):
        mcp_server._authorized_mod_folder(str(root))


def test_mcp_malformed_registry_is_a_permission_error(tmp_path, monkeypatch):
    def broken_registry():
        raise mod_folders.ModFolderError("bad config")

    monkeypatch.setattr(mcp_server.mod_folders, "load_registry",
                        broken_registry)

    with pytest.raises(PermissionError, match="Could not read"):
        mcp_server._authorized_mod_folder(str(tmp_path))

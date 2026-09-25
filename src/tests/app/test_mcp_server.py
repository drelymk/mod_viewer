"""MCP filesystem authorization contracts."""

import pytest

import mcp_server
from app.session import edit as edit_session
from app.settings import mod_folders as mod_folders
from core.ini.document import IniDocument


def _entry(path):
    return [{"name": "Root", "path": mod_folders.normalize_path(path)}]


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


def test_mcp_allows_registered_root_descendant_before_loading(tmp_path,
                                                               monkeypatch):
    root = tmp_path / "library"
    child = root / "mod"
    child.mkdir(parents=True)
    monkeypatch.setattr(mcp_server.mod_folders, "load_registry",
                        lambda: _entry(root))
    seen = []

    def fake_load(folder_path, **kwargs):
        seen.append((folder_path, kwargs))
        return {"ok": True}

    monkeypatch.setattr(mcp_server.mod_loader, "load_mod", fake_load)

    assert mcp_server.inspect_mod(str(child)) == {"ok": True}
    assert seen == [(mod_folders.normalize_path(child), {
        "documents": None, "pending_new_sections": {},
    })]


def test_inspect_mod_passes_staged_documents_without_serializing(tmp_path,
                                                                monkeypatch):
    root = tmp_path / "library"
    child = root / "mod"
    child.mkdir(parents=True)
    path = str(child / "A.ini")
    sibling = str(child / "B.ini")
    (child / "A.ini").write_text(
        "[Constants]\nglobal $mode = 0\n", encoding="utf-8")
    (child / "B.ini").write_text(
        "[Constants]\nglobal $other = 0\n", encoding="utf-8")
    folder = mod_folders.normalize_path(child)
    document = IniDocument.from_string(
        "[Constants]\nglobal $mode = 1\n", path=path)
    edit_session.load_documents(folder, [path], documents={path: document})
    monkeypatch.setattr(mcp_server.mod_folders, "load_registry",
                        lambda: _entry(root))
    monkeypatch.setattr(edit_session, "overrides_for", lambda _folder: (
        _ for _ in ()).throw(AssertionError("staged text was serialized")))
    captured = {}

    def load(loaded_folder, **kwargs):
        captured.update(kwargs)
        context = mcp_server.mod_loader._resolve_context(
            loaded_folder, documents=kwargs["documents"])
        return {"paths": context.ini_paths, "documents": context.docs}

    monkeypatch.setattr(mcp_server.mod_loader, "load_mod", load)
    try:
        result = mcp_server.inspect_mod(folder)
        assert "ini_paths" not in captured
        assert {item.casefold() for item in result["paths"]} == {
            path.casefold(), sibling.casefold()}
        assert captured["documents"][path] is document
        assert any(item is document for item in result["documents"].values())
        assert any(item.casefold() == sibling.casefold()
                   for item in result["documents"])
    finally:
        edit_session.discard(folder)


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

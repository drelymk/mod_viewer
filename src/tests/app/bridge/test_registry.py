import os
from types import SimpleNamespace

from app.assets import index as asset_index
from app.bridge.registry import AssetFolderRegistry, ModFolderRegistry


def test_asset_registry_notifies_fill_cache_after_successful_changes(
        tmp_path, monkeypatch):
    root = str(tmp_path / "asset-root")
    entries = [{"type": "GIMI", "path": root, "enabled": True}]
    events = []

    class Access:
        def __init__(self):
            self.granted = []
            self.refreshed = []

        def was_picker_selected(self, _path):
            return True

        def refresh_asset_roots(self, value):
            self.refreshed.append(value)

        def grant_asset_folder(self, value):
            self.granted.append(value)

    access = Access()
    registry = AssetFolderRegistry(access, on_changed=lambda: events.append(True))
    monkeypatch.setattr(
        "app.bridge.registry.asset_index.index_status",
        lambda *_args: {"status": "ready"})
    monkeypatch.setattr(
        "app.bridge.registry.asset_catalog.add", lambda *_args: entries)
    monkeypatch.setattr(
        "app.bridge.registry.asset_catalog.edit", lambda *_args: entries)
    monkeypatch.setattr(
        "app.bridge.registry.asset_catalog.delete", lambda *_args: [])
    monkeypatch.setattr(
        "app.bridge.registry.asset_catalog.set_enabled", lambda *_args: entries)
    monkeypatch.setattr(
        "app.bridge.registry.asset_catalog.rebuild", lambda *_args: entries)

    assert registry.add_asset_folder("GIMI", root)["folders"]
    assert registry.edit_asset_folder(root, "GIMI", root)["folders"]
    assert registry.delete_asset_folder(root)["folders"] == []
    assert registry.set_asset_folder_enabled(root, False)["folders"]
    assert registry.rebuild_asset_index(root)["folders"]

    assert len(events) == 5
    expected_root = os.path.normcase(os.path.abspath(root))
    assert access.granted == [expected_root, expected_root]
    assert len(access.refreshed) == 5


def test_asset_registry_keeps_tree_browseable_when_index_is_invalid(monkeypatch):
    entry = {"type": "GIMI", "path": "asset-root", "enabled": True}
    access = SimpleNamespace()
    registry = AssetFolderRegistry(access)
    monkeypatch.setattr(
        "app.bridge.registry.asset_folders.load_registry", lambda: [entry])
    monkeypatch.setattr(
        "app.bridge.registry.asset_folders.is_within", lambda *_args: True)
    monkeypatch.setattr(
        "app.bridge.registry.asset_index.load_index",
        lambda *_args: (_ for _ in ()).throw(
            asset_index.AssetIndexError("invalid index")))
    monkeypatch.setattr(
        "app.bridge.registry.asset_folders.list_subfolders",
        lambda *_args, **_kwargs: [{"name": "Character", "asset": True}])

    result = registry.list_asset_subfolders("asset-root/Character")

    assert result == {"folders": [{"name": "Character", "asset": True}]}


def test_mod_registry_returns_exists_flag_and_narrowest_registered_root(
        tmp_path, monkeypatch):
    outer = tmp_path / "outer"
    inner = outer / "inner"
    requested = inner / "requested"
    requested.mkdir(parents=True)
    entries = [
        {"name": "Outer", "path": str(outer)},
        {"name": "Inner", "path": str(inner)},
    ]
    access = SimpleNamespace(refresh_mod_roots=lambda _entries: None)
    registry = ModFolderRegistry(access)
    monkeypatch.setattr(
        "app.bridge.registry.mod_folders.load_registry", lambda: entries)
    monkeypatch.setattr(
        "app.bridge.registry.mod_folders.list_subfolders",
        lambda folder, root: [{"folder": folder, "root": root}])

    result = registry.list_subfolders(str(requested))

    assert result["folders"][0]["root"] == str(inner)
    assert registry.get_mod_folders()["folders"][0]["exists"] is True

"""Concurrent app-config mutations preserve sequential transaction semantics."""

from concurrent.futures import Future
from functools import partial
import json
from threading import Event, Thread, current_thread

import pytest

from app.assets import catalog, folders as asset_folders
from app.settings import config, mod_folders, paths, viewer_preferences
from tests.support.asset_data import gimi_asset_root


def _overlap_at_first_read(monkeypatch, first, second):
    first_read = Event()
    release_read = Event()
    second_attempt = Event()
    second_read = Event()
    original_lock = config._config_lock
    original_read = config.read_config

    class ObservedLock:
        def __enter__(self):
            if not original_lock.acquire(blocking=False):
                second_attempt.set()
                original_lock.acquire()

        def __exit__(self, *_args):
            original_lock.release()

    def paused_read(config_file=None):
        value = original_read(config_file)
        if current_thread() is second_thread:
            second_read.set()
        if current_thread() is first_thread and not first_read.is_set():
            first_read.set()
            assert release_read.wait(5), "first mutation was not released"
        return value

    def run(operation, result, *, competing=False):
        try:
            result.set_result(operation())
        except BaseException as error:
            result.set_exception(error)
        finally:
            # Without a transaction the second mutation finishes while the
            # first holds a stale snapshot, deterministically losing its update.
            if competing:
                second_attempt.set()

    first_result = Future()
    second_result = Future()
    first_thread = Thread(target=run, args=(first, first_result), daemon=True)
    second_thread = Thread(target=run, args=(second, second_result),
                           kwargs={"competing": True}, daemon=True)
    monkeypatch.setattr(config, "_config_lock", ObservedLock())
    monkeypatch.setattr(config, "read_config", paused_read)
    first_thread.start()
    try:
        assert first_read.wait(5), "first mutation did not read config"
        second_thread.start()
        # Observe lock contention (or an unprotected completed mutation),
        # rather than relying on sleeps or scheduler timing.
        assert second_attempt.wait(5), "second mutation did not overlap"
        assert not second_read.is_set(), "second mutation read an uncommitted snapshot"
    finally:
        release_read.set()
        first_thread.join(5)
        if second_thread.ident is not None:
            second_thread.join(5)
    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    first_result.result(timeout=0)
    second_result.result(timeout=0)


@pytest.mark.parametrize("mutation", [
    "viewer", "opacity", "language", "mod-add", "mod-edit", "mod-delete",
    "asset-add", "asset-edit", "asset-delete", "asset-enabled", "asset-entries",
    "catalog-add", "catalog-edit", "catalog-delete", "mod-add-pair", "asset-add-pair",
    "viewer-mod-add", "viewer-asset-add", "viewer-catalog-add",
])
def test_concurrent_config_mutations_preserve_both_changes(
        tmp_path, monkeypatch, mutation):
    filename = tmp_path / "config.json"
    monkeypatch.setattr(paths, "config_path", lambda: filename)
    mod_root = tmp_path / "mod-root"
    mod_root.mkdir()
    new_mod_root = tmp_path / "new-mod-root"
    new_mod_root.mkdir()
    asset_root = config.normalize_path(gimi_asset_root(tmp_path, "asset-root"))
    new_asset_root = config.normalize_path(gimi_asset_root(tmp_path, "new-asset-root"))
    mod_folders.add_folder("Library", str(mod_root))
    asset_folders.add_folder("GIMI", str(asset_root))
    initial = filename.read_bytes()
    operations = {
        "viewer": lambda: viewer_preferences.save_preferences({"bloom": True}),
        "opacity": lambda: mod_folders.save_panel_opacity(35),
        "language": lambda: mod_folders.save_language("ja"),
        "mod-add": lambda: mod_folders.add_folder("New", str(new_mod_root)),
        "mod-edit": lambda: mod_folders.edit_folder(
            str(mod_root), "Renamed", str(mod_root)),
        "mod-delete": lambda: mod_folders.delete_folder(str(mod_root)),
        "asset-add": lambda: asset_folders.add_folder("GIMI", str(new_asset_root)),
        "asset-edit": lambda: asset_folders.edit_folder(
            str(asset_root), "ZZMI", str(asset_root)),
        "asset-delete": lambda: asset_folders.delete_folder(str(asset_root)),
        "asset-enabled": lambda: asset_folders.set_enabled(str(asset_root), False),
        "asset-entries": lambda: asset_folders.write_entries([]),
        "catalog-add": lambda: catalog.add("GIMI", str(new_asset_root)),
        "catalog-edit": lambda: catalog.edit(
            str(asset_root), "GIMI", str(asset_root)),
        "catalog-delete": lambda: catalog.delete(str(asset_root)),
    }
    if mutation.startswith("viewer-"):
        first = operations["viewer"]
        second = operations[mutation.removeprefix("viewer-")]
    elif mutation.endswith("-pair"):
        # Same-registry additions must lock before their initial entry read,
        # not just while merging the already prepared list into config.
        kind = mutation.removesuffix("-pair")
        first = operations[kind]
        if kind == "mod-add":
            second = partial(mod_folders.add_folder, "Another", str(asset_root))
        else:
            second = partial(asset_folders.add_folder, "ZZMI", str(mod_root))
    else:
        first = operations[mutation]
        second = partial(viewer_preferences.save_preferences, {"environment": "studio"})

    first()
    second()
    expected = json.loads(filename.read_text(encoding="utf-8"))
    filename.write_bytes(initial)
    _overlap_at_first_read(monkeypatch, first, second)
    assert json.loads(filename.read_text(encoding="utf-8")) == expected
    assert not filename.with_suffix(".json.tmp").exists()

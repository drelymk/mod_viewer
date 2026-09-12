from types import SimpleNamespace

from app.bridge.api import ModViewerAPI
from app.session import edit as edit_session
from app.settings import mod_folders
from app.settings import paths


EXPECTED_API_METHODS = {
    "add_asset_folder",
    "add_mod_folder",
    "add_present",
    "add_toggle",
    "capture_present",
    "consume_startup_request",
    "delete_asset_folder",
    "delete_mod_folder",
    "delete_present",
    "delete_present_position",
    "delete_rig_pose_preset",
    "clear_humanoid_control_rig",
    "delete_toggle",
    "discard_changes",
    "edit_asset_folder",
    "edit_mod_folder",
    "edit_present",
    "edit_toggle",
    "export_changes",
    "get_asset_folders",
    "get_control_state",
    "get_diagnostics",
    "get_ini_text",
    "get_mesh_semantics",
    "save_texture_color",
    "get_mod_folders",
    "get_panel_opacity",
    "get_present_state",
    "get_record_positions",
    "get_model_skinning_preview",
    "get_toggle_details",
    "has_pending_changes",
    "list_asset_subfolders",
    "list_ini_files",
    "list_subfolders",
    "list_toggle_source_inis",
    "load_asset",
    "load_missing_asset_parts",
    "load_mod",
    "pick_asset_texture_file",
    "pick_texture_file",
    "rebuild_asset_index",
    "record_toggle",
    "remove_missing_asset_parts",
    "rename_rig_pose_preset",
    "save_component_material_kind",
    "save_mesh_color_adjustment",
    "save_mesh_names",
    "save_mesh_textures",
    "save_rig_pose_preset",
    "save_humanoid_control_rig",
    "save_weight_selection",
    "select_asset_folder",
    "select_folder",
    "set_asset_folder_enabled",
    "set_panel_opacity",
    "update_ini_text",
}


def test_mod_viewer_api_surface_is_explicit_and_private_state_stays_private():
    api = ModViewerAPI()

    public = {
        name for name in dir(api)
        if not name.startswith("_") and callable(getattr(api, name))
    }

    assert public == EXPECTED_API_METHODS
    assert all(name.startswith("_") for name in vars(api))


def test_startup_request_is_consumed_once_and_authorizes_valid_folder(
        tmp_path, monkeypatch):
    root = tmp_path / "unregistered-mod"
    root.mkdir()
    monkeypatch.setattr(paths, "config_path", lambda: str(tmp_path / "config.json"))

    api = ModViewerAPI(startup_mod=str(root))
    normalized = mod_folders.normalize_path(str(root))

    assert api.consume_startup_request() == {
        "path": normalized,
        "disabled_ini": False,
    }
    assert api.consume_startup_request() is None
    assert api._access.was_picker_selected(str(root)) is False
    assert api._access.mod_folder(str(root)) == normalized


def test_startup_request_preserves_disabled_ini_flag(tmp_path, monkeypatch):
    root = tmp_path / "mod"
    root.mkdir()
    monkeypatch.setattr(paths, "config_path", lambda: str(tmp_path / "config.json"))

    api = ModViewerAPI(startup_mod=str(root), startup_disabled_ini=True)

    assert api.consume_startup_request()["disabled_ini"] is True


def test_invalid_startup_request_is_reported_without_failing_api(
        tmp_path, monkeypatch):
    missing = tmp_path / "missing-mod"
    monkeypatch.setattr(paths, "config_path", lambda: str(tmp_path / "config.json"))

    api = ModViewerAPI(startup_mod=str(missing))

    request = api.consume_startup_request()
    assert request["error"].startswith("Startup mod folder does not exist:")
    assert api.consume_startup_request() is None


def test_facade_composes_picker_registry_preview_and_editing(tmp_path, monkeypatch):
    root = tmp_path / "mods"
    root.mkdir()
    ini = root / "mod.ini"
    ini.write_text("[Constants]\n$Value = 0\n", encoding="utf-8")
    monkeypatch.setattr(paths, "config_path", lambda: str(tmp_path / "config.json"))

    api = ModViewerAPI()
    api._window = SimpleNamespace(
        create_file_dialog=lambda *_args, **_kwargs: [str(root)])

    try:
        selected = api.select_folder()
        assert api.add_mod_folder("Mods", selected)["folders"]
        assert api.get_present_state(selected).get("error") is None

        changed = api.update_ini_text(selected, "mod.ini",
                                      "[Constants]\n$Value = 1\n")

        assert changed["ok"] is True
        assert "$Value = 1" in api.get_ini_text(selected, "mod.ini")["text"]
        assert "$Value = 0" in ini.read_text(encoding="utf-8")
    finally:
        edit_session.discard(selected)


def test_missing_asset_parts_preserves_unauthorized_folder_error(
        tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "config_path", lambda: str(tmp_path / "config.json"))
    api = ModViewerAPI()

    result = api.load_missing_asset_parts(str(tmp_path / "unselected"))

    assert result == {
        "status": "error",
        "error": "This folder was not selected through the native folder picker.",
    }


def test_load_mod_forwards_disabled_ini_flag(monkeypatch):
    api = ModViewerAPI()
    calls = []
    monkeypatch.setattr(api._asset_preview, "clear_fill", lambda: None)
    monkeypatch.setattr(
        api._mod_preview, "load_mod",
        lambda path, disabled_ini=False: calls.append((path, disabled_ini))
        or {"ok": True},
    )

    assert api.load_mod("mod", True) == {"ok": True}
    assert calls == [("mod", True)]


def test_load_asset_clears_loaded_mod_state_before_transition(monkeypatch):
    api = ModViewerAPI()
    calls = []
    monkeypatch.setattr(
        api._mod_preview, "clear_loaded_model",
        lambda: calls.append("clear"))
    monkeypatch.setattr(
        api._asset_preview, "load_asset",
        lambda path: calls.append(("asset", path)) or {"ok": True},
    )

    assert api.load_asset("asset") == {"ok": True}
    assert calls == ["clear", ("asset", "asset")]


def test_texture_save_forwards_targets_and_usage(monkeypatch):
    api = ModViewerAPI()
    calls = []
    monkeypatch.setattr(
        api._mod_preview, "save_texture_color",
        lambda *args, **kwargs: calls.append((args, kwargs))
        or {"status": "ok"},
    )
    targets = [{"semantic_key": "Body-1", "metadata_key": "Body::one",
                "adjustment": {"hue": 30}}]
    usage = [{
        "semantic_key": "Body-1",
        "texture_keys": {
            "diffuse": "diffuse::body.dds", "normal_map": None,
            "normal_data": None, "light_map": None,
            "material_map": None, "emission_map": None,
        },
    }]

    result = api.save_texture_color(
        "mod", "diffuse::body.dds", targets, usage)

    assert result == {"status": "ok"}
    assert calls[0][0] == ("mod", "diffuse::body.dds", targets, usage)
    assert callable(calls[0][1]["progress_callback"])


def test_humanoid_control_rig_methods_forward_through_preview(monkeypatch):
    api = ModViewerAPI()
    calls = []
    monkeypatch.setattr(
        api._mod_preview, "save_humanoid_control_rig",
        lambda *args: calls.append(("save", args)) or {"saved": True})
    monkeypatch.setattr(
        api._mod_preview, "clear_humanoid_control_rig",
        lambda *args: calls.append(("clear", args)) or {"saved": True})

    value = {"version": 1, "controls": {}}
    assert api.save_humanoid_control_rig("mod", value) == {"saved": True}
    assert api.clear_humanoid_control_rig("mod") == {"saved": True}
    assert calls == [("save", ("mod", value)), ("clear", ("mod",))]


def test_texture_save_progress_event_includes_request_id_and_is_best_effort():
    api = ModViewerAPI()
    captured = []

    class Window:
        def run_js(self, script):
            captured.append(script)

    def save(*_args, **kwargs):
        kwargs["progress_callback"]({
            "stage": "processing", "mip": 0, "mip_count": 1,
            "completed_blocks": 3, "total_blocks": 7,
        })
        return {"status": "ok"}

    api._mod_preview.save_texture_color = save
    api._window = Window()
    result = api.save_texture_color(
        "mod", "diffuse::body.dds", [], [], "request-7")

    assert result == {"status": "ok"}
    assert len(captured) == 1
    assert "mod-viewer-texture-save-progress" in captured[0]
    assert '"request_id":"request-7"' in captured[0]
    assert '"completed_blocks":3' in captured[0]

    class BrokenWindow:
        def run_js(self, _script):
            raise RuntimeError("closed WebView")

    api._window = BrokenWindow()
    assert api.save_texture_color(
        "mod", "diffuse::body.dds", [], [], "request-8") == {"status": "ok"}

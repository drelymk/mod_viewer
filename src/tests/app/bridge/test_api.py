from app.bridge.api import ModViewerAPI


EXPECTED_API_METHODS = {
    "add_asset_folder",
    "add_mod_folder",
    "add_present",
    "add_toggle",
    "capture_present",
    "delete_asset_folder",
    "delete_mod_folder",
    "delete_present",
    "delete_present_position",
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
    "get_mod_folders",
    "get_panel_opacity",
    "get_present_state",
    "get_record_positions",
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
    "save_component_material_kind",
    "save_mesh_names",
    "save_mesh_textures",
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

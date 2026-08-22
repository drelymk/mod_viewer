"""Optional MCP companion for 3DMigoto Mod Viewer.

Run with ``python src/mcp_server.py`` from an MCP client.  The server reuses
the same staged edit session as the desktop UI; INI files are not changed
until ``export_mod_changes`` is explicitly called.
"""

from mcp.server.fastmcp import FastMCP

from app import edit_session, mod_folders, mod_loader, toggle_api

mcp = FastMCP("3DMigoto Mod Viewer")


def _authorized_mod_folder(folder_path):
    """Resolve a requested folder against the current Mod Library registry."""
    requested = mod_folders.normalize_path(folder_path)
    try:
        entries = mod_folders.load_registry()
    except mod_folders.ModFolderError as error:
        raise PermissionError(
            f"Could not read the Mod Library configuration: {error}") from error

    roots = mod_folders.registered_paths(entries)
    if not requested or not any(
            mod_folders.is_within(requested, root) for root in roots):
        raise PermissionError(
            "This folder is not inside a registered Mod Library folder.")
    return requested


@mcp.tool()
def inspect_mod(folder_path: str) -> dict:
    """Load a mod and return its meshes, toggles, menus, and textures."""
    folder_path = _authorized_mod_folder(folder_path)
    return mod_loader.load_mod(
        folder_path,
        overrides=edit_session.overrides_for(folder_path),
        pending_new_sections=edit_session.new_sections_for(folder_path),
    )


@mcp.tool()
def list_toggle_source_inis(folder_path: str) -> list:
    """List INI files that can receive staged toggle edits."""
    folder_path = _authorized_mod_folder(folder_path)
    return toggle_api.list_source_inis(folder_path)


@mcp.tool()
def get_toggle_details(folder_path: str, ini_rel: str, section_name: str) -> dict:
    """Inspect one toggle before editing it."""
    folder_path = _authorized_mod_folder(folder_path)
    return toggle_api.get_toggle_details(folder_path, ini_rel, section_name)


@mcp.tool()
def add_mod_toggle(folder_path: str, ini_rel: str, name: str, key_combo: str,
                   var: str, values: list[str], default: str | None = None,
                   back_combo: str | None = None) -> dict:
    """Stage a new toggle; use export_mod_changes to write it."""
    folder_path = _authorized_mod_folder(folder_path)
    return toggle_api.add_toggle(
        folder_path, ini_rel, name, key_combo, var, values,
        {"default": default, "back_combo": back_combo},
    )


@mcp.tool()
def edit_mod_toggle(folder_path: str, ini_rel: str, section_name: str,
                    changes: dict) -> dict:
    """Stage edits to a toggle, including its display name."""
    folder_path = _authorized_mod_folder(folder_path)
    return toggle_api.edit_toggle(folder_path, ini_rel, section_name, changes)


@mcp.tool()
def delete_mod_toggle(folder_path: str, ini_rel: str, section_name: str) -> dict:
    """Stage deletion of a toggle."""
    folder_path = _authorized_mod_folder(folder_path)
    return toggle_api.delete_toggle(folder_path, ini_rel, section_name)


@mcp.tool()
def export_mod_changes(folder_path: str) -> dict:
    """Write staged changes and create the viewer's timestamped backups."""
    folder_path = _authorized_mod_folder(folder_path)
    return toggle_api.export_changes(folder_path)


@mcp.tool()
def discard_mod_changes(folder_path: str) -> dict:
    """Discard staged changes without writing them."""
    folder_path = _authorized_mod_folder(folder_path)
    return toggle_api.discard_changes(folder_path)


if __name__ == "__main__":
    mcp.run()

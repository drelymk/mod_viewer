"""Optional MCP companion for 3DMigoto Mod Viewer.

Run with ``python src/mcp_server.py`` from an MCP client.  The server reuses
the same staged edit session as the desktop UI; INI files are not changed
until ``export_mod_changes`` is explicitly called.
"""

from mcp.server.fastmcp import FastMCP
from functools import cache
import threading

from app.bridge import toggle as toggle_api
from app.mods import loader as mod_loader
from app.session import edit as edit_session
from app.settings import mod_folders
from app.runtime import server
from core.mod_source import ModSourceError

mcp = FastMCP("3DMigoto Mod Viewer")
_inspection_lock = threading.Lock()
_inspection_publication = None


@cache
def _preview_base_url():
    """Serve inspection blobs on one process-local localhost server."""
    return server.start(require_ui_assets=False)


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
    global _inspection_publication
    folder_path = _authorized_mod_folder(folder_path)
    with _inspection_lock:
        documents = edit_session.documents_for(folder_path)
        try:
            context = mod_loader._resolve_context(
                folder_path, documents=documents or None
            )
        except ModSourceError as error:
            return {"error": str(error)}
        publication = server.begin_texture_publication(
            folder_path, source=context.source
        )
        try:
            payload = mod_loader.load_mod(
                context=context,
                pending_new_sections=edit_session.new_sections_for(folder_path),
                texture_source=publication.register,
                menu_image_source=publication.register_menu_image,
            )
            if payload.get("error"):
                publication.discard()
                return payload
            textures = payload.get("textures", {})
            menu = payload.get("controls", {}).get("menu", {})
            if (
                payload.get("geometry")
                or textures
                or any(item.get("image") for item in menu.values())
            ):
                base_url = _preview_base_url()
                if payload.get("geometry"):
                    payload["geometry"]["url"] = base_url + payload["geometry"]["url"]
                for key, url in textures.items():
                    textures[key] = base_url + url
                for item in menu.values():
                    if item.get("image"):
                        item["image"] = base_url + item["image"]
            publication.commit(replace=False)
        except Exception:
            publication.discard()
            raise
        server.release_texture_publication(_inspection_publication)
        _inspection_publication = publication
        return payload


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

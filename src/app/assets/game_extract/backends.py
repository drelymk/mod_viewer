"""Game-specific extraction adapters behind the shared bridge and UI contract.

Register an adapter here when another game's verified extraction is available.
Folder grants, background jobs and atomic sidecar installation stay in the bridge.
The WWMI modules implement only the Wuthering Waves archive/layout contract.
"""

from pathlib import Path

from app.settings.game_folders import GAME_KINDS
from . import wwmi


class UnsupportedGame(ValueError):
    def __init__(self):
        super().__init__("Game rig/animation extraction is not available for this game yet.")


_BACKENDS = {"WWMI": wwmi}


def backend_for(kind):
    if kind not in GAME_KINDS:
        raise ValueError("Unknown game folder.")
    return _BACKENDS.get(kind)


def require_backend(kind):
    backend = backend_for(kind)
    if backend is None:
        raise UnsupportedGame()
    return backend


def capabilities():
    return {kind: {"extraction": backend_for(kind) is not None,
                   "index": backend_for(kind) is not None} for kind in GAME_KINDS}


def validate_folder(kind, folder):
    if not Path(folder).is_dir():
        raise ValueError("Select an existing folder.")
    backend = backend_for(kind)
    if backend is not None:
        backend.validate_folder(folder)


def error_response(error):
    result = {"error": str(error)}
    if isinstance(error, UnsupportedGame):
        result["error_code"] = "gameExtract.unavailable"
    return result

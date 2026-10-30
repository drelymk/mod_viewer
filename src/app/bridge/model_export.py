"""Native-picked, atomic GLB saves with bounded bridge transfers."""

import atexit
import base64
import binascii
import json
import os
from pathlib import Path
import secrets
import struct
import tempfile
import threading

import webview

from core.mod_source import mod_source_for_path


MAX_CHUNK_CHARS = 2 * 1024 * 1024


def _authorize(access, folder_path):
    folder = access.mod_folder(folder_path)
    if mod_source_for_path(folder).read_only:
        raise ValueError("Export is unavailable for compressed mods.")
    return folder


def _decode(encoded):
    if not isinstance(encoded, str):
        raise ValueError("Invalid GLB data.")
    try:
        return base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("Invalid GLB data.") from error


def _validate(stream, size):
    header = stream.read(20)
    if len(header) != 20 or struct.unpack_from("<4sII", header) != (b"glTF", 2, size):
        raise ValueError("Invalid GLB header.")
    length, kind = struct.unpack_from("<II", header, 12)
    if kind != 0x4E4F534A or length % 4 or 20 + length > size:
        raise ValueError("Invalid GLB document.")
    document = json.loads(stream.read(length))
    if not isinstance(document, dict) or not document.get("meshes"):
        raise ValueError("The GLB contains no meshes.")
    if any("uri" in entry for key in ("buffers", "images")
           for entry in document.get(key, [])):
        raise ValueError("The GLB must contain all resources.")


def _destination(window, folder):
    result = window.create_file_dialog(
        webview.FileDialog.SAVE, save_filename=Path(folder).name + ".glb",
        file_types=("glTF Binary (*.glb)",))
    if not result:
        return None
    destination = Path(result if isinstance(result, str) else result[0])
    if destination.suffix.lower() != ".glb":
        destination = destination.with_name(destination.name + ".glb")
        if destination.exists():
            raise ValueError("Select the existing .glb file in the save dialog to replace it.")
    return destination


class ModelExport:
    """Keep one native destination private across small bridge messages."""

    def __init__(self, access):
        self._access = access
        self._pending = None
        self._lock = threading.RLock()
        atexit.register(self._abort)

    def _abort(self):
        pending, self._pending = self._pending, None
        if pending is not None:
            pending["stream"].close()
            try:
                os.unlink(pending["temporary"])
            except FileNotFoundError:
                pass

    def save(self, window, folder_path, data):
        with self._lock:
            owned = False
            try:
                folder = _authorize(self._access, folder_path)
                if not isinstance(data, dict):
                    raise ValueError("Invalid model export request.")
                action = data.get("action")
                if action == "begin":
                    if self._pending is not None:
                        raise ValueError("A model export is already in progress.")
                    size = data.get("size")
                    if type(size) is not int or not 20 <= size <= 0xFFFFFFFF:
                        raise ValueError("Invalid GLB length.")
                    destination = _destination(window, folder)
                    if destination is None:
                        return {"cancelled": True}
                    stream = tempfile.NamedTemporaryFile(
                        dir=destination.parent, prefix=".model-export-", delete=False)
                    token = secrets.token_urlsafe(32)
                    self._pending = {"folder": folder, "destination": destination,
                                     "temporary": stream.name, "stream": stream,
                                     "size": size, "written": 0, "token": token}
                    return {"token": token}
                pending = self._pending
                if (pending is None or pending["folder"] != folder
                        or not isinstance(data.get("token"), str)
                        or not secrets.compare_digest(data["token"], pending["token"])):
                    raise ValueError("Invalid model export session.")
                owned = True
                if action == "cancel":
                    self._abort()
                    return {"cancelled": True}
                if action == "append":
                    chunk = data.get("chunk")
                    if not isinstance(chunk, str) or len(chunk) > MAX_CHUNK_CHARS:
                        raise ValueError("Invalid GLB chunk.")
                    decoded = _decode(chunk)
                    if not decoded or pending["written"] + len(decoded) > pending["size"]:
                        raise ValueError("Invalid GLB chunk length.")
                    pending["stream"].write(decoded)
                    pending["written"] += len(decoded)
                    return {"ok": True}
                if action != "finish" or pending["written"] != pending["size"]:
                    raise ValueError("Incomplete GLB export.")
                stream = pending["stream"]
                stream.flush()
                os.fsync(stream.fileno())
                if stream.seek(0, os.SEEK_END) != pending["size"]:
                    raise ValueError("Incomplete GLB export.")
                stream.seek(0)
                _validate(stream, pending["size"])
                stream.close()
                os.replace(pending["temporary"], pending["destination"])
                self._pending = None
                return {"ok": True, "name": pending["destination"].name}
            except (OSError, TypeError, ValueError) as error:
                if owned:
                    self._abort()
                return {"error": str(error)}

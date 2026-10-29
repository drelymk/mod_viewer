"""Save a self-contained model snapshot only to a native-picked destination."""

import base64
import binascii
import json
import os
from pathlib import Path
import struct
import tempfile

import webview

from core.mod_source import mod_source_for_path


def save_glb(window, access, folder_path, encoded):
    temporary = None
    try:
        folder = access.mod_folder(folder_path)
        if mod_source_for_path(folder).read_only:
            return {"error": "Export is unavailable for compressed mods."}
        if not isinstance(encoded, str):
            raise ValueError("Invalid GLB data.")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError("Invalid GLB data.") from error
        if len(data) < 20 or struct.unpack_from("<4sII", data) != (
                b"glTF", 2, len(data)):
            raise ValueError("Invalid GLB header.")
        length, kind = struct.unpack_from("<II", data, 12)
        if kind != 0x4E4F534A or length % 4 or 20 + length > len(data):
            raise ValueError("Invalid GLB document.")
        document = json.loads(data[20:20 + length])
        if not isinstance(document, dict) or not document.get("meshes"):
            raise ValueError("The GLB contains no meshes.")
        if any("uri" in entry for key in ("buffers", "images")
               for entry in document.get(key, [])):
            raise ValueError("The GLB must contain all resources.")
        result = window.create_file_dialog(
            webview.FileDialog.SAVE, save_filename=Path(folder).name + ".glb",
            file_types=("glTF Binary (*.glb)",))
        if not result:
            return {"cancelled": True}
        destination = Path(result if isinstance(result, str) else result[0])
        if destination.suffix.lower() != ".glb":
            destination = destination.with_name(destination.name + ".glb")
            if destination.exists():
                raise ValueError("Select the existing .glb file in the save dialog to replace it.")
        with tempfile.NamedTemporaryFile(
                dir=destination.parent, prefix=".model-export-", delete=False) as stream:
            temporary = stream.name
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        temporary = None
        return {"ok": True, "name": destination.name}
    except (OSError, TypeError, ValueError) as error:
        return {"error": str(error)}
    finally:
        if temporary is not None:
            os.unlink(temporary)

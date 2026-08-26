"""
3DMigoto Mod Viewer - Desktop App
---------------------------------
Native window app: pick any 3DMigoto mod folder and preview all meshes in 3D.
Mesh data is built in-memory (no OBJ/MTL/PNG files written to disk).

Install:  pip install pywebview Pillow
Run:      python viewer_app.py

This module is only the entry point. The application lives in the `app`
package and the user interface in `web/`.
"""

import sys

try:
    import webview
except ImportError:
    print("Missing dependency.  Run:  pip install pywebview")
    raise

from app.bridge.api import ModViewerAPI
from app.runtime import server, webview2

__all__ = ["ModViewerAPI", "main"]


def main():
    if not webview2.is_installed():
        webview2.report_missing()
        return 1

    api = ModViewerAPI()
    window = webview.create_window(
        "3DMigoto Mod Viewer",
        url=server.start(),
        js_api=api,
        width=1400,
        height=900,
        min_size=(900, 600),
    )
    api._window = window
    webview.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())

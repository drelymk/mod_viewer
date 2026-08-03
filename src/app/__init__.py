"""3DMigoto Mod Viewer application package.

Layout:
    paths.py       filesystem roots (source tree vs PyInstaller bundle)
    webview2.py    Evergreen WebView2 Runtime detection
    server.py      localhost server for the web/ UI and vendored Three.js
    features.py    build-time feature flags (which optional actions a built
                   exe exposes -- see features.ini at the repo root, baked
                   into the exe by build.py rather than bundled as a file)
    mod_loader.py  mod folder -> JSON payload (no GUI dependencies)
    api.py         the object bridged into JavaScript as window.pywebview.api
"""

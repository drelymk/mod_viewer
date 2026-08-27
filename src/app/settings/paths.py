"""Filesystem roots, resolved the same way whether running from source or from
a PyInstaller bundle.

When frozen, PyInstaller unpacks bundled data under ``sys._MEIPASS``; from
source everything hangs off the repository directory. Every path the app reads
at runtime goes through here so that difference is stated exactly once.
"""

import os
import sys

APP_VERSION = "2.0.0"


def app_root():
    """Directory that bundled data files live under."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return meipass
    # app/settings/paths.py -> app/settings/ -> app/ -> source root
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))


def is_frozen():
    """True when running as a PyInstaller-built exe, False when running
    directly from a source checkout (`python viewer_app.py`). Same
    ``sys._MEIPASS`` marker app_root() already relies on -- see app/settings/features.py,
    the one place this currently gates behaviour."""
    return bool(getattr(sys, "_MEIPASS", None))


def config_path():
    """Persistent application configuration, outside frozen bundle data."""
    base = os.path.dirname(sys.executable) if is_frozen() else app_root()
    return os.path.join(base, "config.json")


def asset_index_dir():
    """Persistent Asset Folder indexes, kept beside the application config."""
    return os.path.join(os.path.dirname(config_path()), "asset-index")


def web_dir():
    """The HTML/CSS/JS UI served to the webview."""
    return os.path.join(app_root(), "web")


def vendor_dir():
    """Vendored third-party browser assets (Three.js), populated by build.py.

    A source checkout must run build.py once before the app can start; the
    viewer does not load runtime third-party scripts from a CDN.
    """
    return os.path.join(app_root(), "assets")


def has_vendored_three():
    required = (
        "three.core.js",
        "three.webgpu.js",
        "three.tsl.js",
        os.path.join("addons", "controls", "ArcballControls.js"),
        os.path.join("addons", "tsl", "display", "GTAONode.js"),
        os.path.join("addons", "tsl", "display", "BloomNode.js"),
    )
    return all(os.path.isfile(os.path.join(vendor_dir(), rel)) for rel in required)

"""Filesystem roots, resolved the same way whether running from source or from
a PyInstaller bundle.

When frozen, PyInstaller unpacks bundled data under ``sys._MEIPASS``; from
source everything hangs off the repository directory. Every path the app reads
at runtime goes through here so that difference is stated exactly once.
"""

import os
import sys

APP_VERSION = "1.5.0"


def app_root():
    """Directory that bundled data files live under."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return meipass
    # app/paths.py -> app/ -> repository root
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def is_frozen():
    """True when running as a PyInstaller-built exe, False when running
    directly from a source checkout (`python viewer_app.py`). Same
    ``sys._MEIPASS`` marker app_root() already relies on -- see app/features.py,
    the one place this currently gates behaviour."""
    return bool(getattr(sys, "_MEIPASS", None))


def web_dir():
    """The HTML/CSS/JS UI served to the webview."""
    return os.path.join(app_root(), "web")


def vendor_dir():
    """Vendored third-party browser assets (Three.js), populated by build.py.

    Absent when running from a fresh source checkout, in which case the app
    falls back to the CDN.
    """
    return os.path.join(app_root(), "assets")


def has_vendored_three():
    return os.path.isfile(os.path.join(vendor_dir(), "three.module.js"))

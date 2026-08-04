"""Localhost HTTP server for the web UI.

The UI is loaded into WebView2 over http://127.0.0.1:<port> rather than
pushed in as an HTML string -- NavigateToString gives the document an opaque
origin, which blocks relative `<script src>` and ES-module imports, and caps
the content at ~2 MB (Three.js alone exceeds that). Serving over a real
origin lets index.html, the stylesheet and the JS modules be ordinary files.

Two roots are exposed:
    /            the web/ directory (index.html, css/, js/)
    /vendor/     the vendored Three.js copy, when build.py has fetched it

index.html is rendered rather than served verbatim, so the importmap can
point at either the vendored copy or the CDN.
"""

import functools
import http.server
import os
import socketserver
import threading

from . import features, paths

THREE_VERSION = "0.165.0"
CDN_THREE = f"https://cdn.jsdelivr.net/npm/three@{THREE_VERSION}/build/three.module.js"
CDN_ADDONS = f"https://cdn.jsdelivr.net/npm/three@{THREE_VERSION}/examples/jsm/"

REPO_URL = "https://github.com/drelymk/mod_viewer"

_VENDOR_PREFIX = "/vendor/"


class _Handler(http.server.SimpleHTTPRequestHandler):
    """Serves web/ with two extras: a /vendor/ mount and a rendered index.html."""

    vendor_root = ""
    template_vars: dict = {}

    def do_GET(self):
        if self._request_path() in ("/", "/index.html"):
            return self._send_index()
        return super().do_GET()

    def translate_path(self, path):
        rel = self._request_path()
        if rel.startswith(_VENDOR_PREFIX):
            return self._safe_join(self.vendor_root, rel[len(_VENDOR_PREFIX):])
        return super().translate_path(path)

    def guess_type(self, path):
        # ES modules must be served with a JS MIME type or the browser refuses
        # to execute them.
        if path.endswith(".js"):
            return "text/javascript"
        return super().guess_type(path)

    def log_message(self, *args):
        pass

    # -- helpers ---------------------------------------------------------

    def _request_path(self):
        return self.path.split("?", 1)[0].split("#", 1)[0]

    @staticmethod
    def _safe_join(root, rel):
        """Resolve `rel` under `root`, refusing to escape it via `..`."""
        root = os.path.abspath(root)
        target = os.path.abspath(os.path.join(root, rel.lstrip("/")))
        if target != root and not target.startswith(root + os.sep):
            return root   # traversal attempt -> a directory, which 404s
        return target

    def _send_index(self):
        index = os.path.join(self.directory, "index.html")
        try:
            with open(index, encoding="utf-8") as fh:
                html = fh.read()
        except OSError:
            self.send_error(404, "index.html not found")
            return
        for placeholder, value in self.template_vars.items():
            html = html.replace(placeholder, value)
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # The UI is regenerated per launch; a cached copy across runs would
        # silently serve a stale build.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def start():
    """Serve the UI on an ephemeral localhost port; return its base URL.

    Binds to 127.0.0.1 so the port is never reachable from off the machine.
    """
    three_url, addons_url = CDN_THREE, CDN_ADDONS
    if paths.has_vendored_three():
        three_url = f"{_VENDOR_PREFIX}three.module.js"
        addons_url = f"{_VENDOR_PREFIX}addons/"

    # Feature flags only ever hide a button (app/features.py) -- baked into
    # a <body> class server-side so there's no flash of a button appearing
    # then disappearing after load.
    flags = features.get_features()
    body_classes = []
    if not flags["export"]:
        body_classes.append("feature-export-off")
    if not flags["modify_toggle"]:
        body_classes.append("feature-modify-toggle-off")

    handler = functools.partial(
        _Handler,
        directory=paths.web_dir(),
    )
    _Handler.vendor_root = paths.vendor_dir()
    _Handler.template_vars = {
        "__THREE_URL__": three_url,
        "__ADDONS_URL__": addons_url,
        "__BODY_CLASS__": " ".join(body_classes),
        "__APP_VERSION__": paths.APP_VERSION,
        "__REPO_URL__": REPO_URL,
    }

    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}"

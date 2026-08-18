"""Localhost HTTP server for the web UI.

The UI is loaded into WebView2 over http://127.0.0.1:<port> rather than
pushed in as an HTML string -- NavigateToString gives the document an opaque
origin, which blocks relative `<script src>` and ES-module imports, and caps
the content at ~2 MB (Three.js alone exceeds that). Serving over a real
origin lets index.html, the stylesheet and the JS modules be ordinary files.

Two roots are exposed:
    /            the web/ directory (index.html, css/, js/)
    /vendor/     the vendored Three.js copy, when build.py has fetched it
    /texture/    opaque URLs for the active load's lazily rendered PNGs

index.html is rendered rather than served verbatim, so the importmap can
point at either the vendored copy or the CDN.
"""

import functools
import base64
import http.server
import os
import socketserver
import threading
import uuid
from dataclasses import dataclass

from core.mesh_builder import _render_texture_png, normalize_texture_role
from . import features, paths

THREE_VERSION = "0.165.0"
REPO_URL = "https://github.com/drelymk/mod_viewer"

_VENDOR_PREFIX = "/vendor/"
_GEOMETRY_PREFIX = "/geometry/"
_TEXTURE_PREFIX = "/texture/"
_MAX_GEOMETRY_BYTES = 512 * 1024 * 1024
_TEXTURE_ENCODE_CONCURRENCY = 2
_geometry_lock = threading.RLock()
_geometry_blobs = {}
_texture_lock = threading.RLock()
_texture_publications = {}
_active_texture_publication = None
_texture_encode_semaphore = threading.BoundedSemaphore(
    _TEXTURE_ENCODE_CONCURRENCY)


@dataclass(frozen=True)
class TextureSource:
    """One filesystem source registered in a committed texture publication."""

    path: str
    role: str = "diffuse"
    max_size: int = 2048
    preserve_alpha: bool = False


class TexturePublication:
    """Transactional, opaque URL registry for one application model load."""

    def __init__(self, mod_dir=None):
        self.token = uuid.uuid4().hex
        self.mod_dir = (os.path.normcase(os.path.abspath(mod_dir))
                        if mod_dir else None)
        self._sources = {}
        self._dedupe = {}
        self._state = "pending"

    def register(self, path, role=None, max_size=2048, preserve_alpha=False,
                 validate=False):
        """Publish a source once and return its opaque same-origin URL.

        The caller has already resolved the path through the core sandbox. The
        registry still requires a real file and never exposes that path in the
        URL, so browser requests can only address sources registered here.
        ``validate=True`` is reserved for explicit manual picks and performs
        one immediate render so corrupt files return an error before they are
        persisted in viewer metadata.
        """
        if not path:
            return None
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            return None
        role = normalize_texture_role(role)
        try:
            max_size = int(max_size)
        except (TypeError, ValueError):
            return None
        if max_size <= 0:
            return None
        preserve_alpha = bool(preserve_alpha)
        dedupe_key = (os.path.normcase(path), role, max_size, preserve_alpha)
        existing_source = None
        with _texture_lock:
            if (_texture_publications.get(self.token) is not self
                    or self._state == "discarded"):
                return None
            source_id = self._dedupe.get(dedupe_key)
            if source_id is not None:
                existing_source = self._sources[source_id]
                if not validate:
                    return f"{_TEXTURE_PREFIX}{self.token}/{source_id}"

        source = existing_source or TextureSource(
            path=path, role=role, max_size=max_size,
            preserve_alpha=preserve_alpha)
        if validate and _render_texture_source(source) is None:
            return None

        with _texture_lock:
            if (_texture_publications.get(self.token) is not self
                    or self._state == "discarded"):
                return None
            source_id = self._dedupe.get(dedupe_key)
            if source_id is None:
                source_id = str(len(self._sources))
                self._dedupe[dedupe_key] = source_id
                self._sources[source_id] = source
            return f"{_TEXTURE_PREFIX}{self.token}/{source_id}"

    def commit(self):
        """Make this publication active and retire every older publication."""
        global _active_texture_publication
        with _texture_lock:
            if self._state == "discarded":
                return False
            _texture_publications.clear()
            _texture_publications[self.token] = self
            _active_texture_publication = self
            self._state = "committed"
            return True

    def discard(self):
        """Drop only this unfinished publication, preserving the active one."""
        with _texture_lock:
            if self._state == "committed":
                return False
            _texture_publications.pop(self.token, None)
            self._state = "discarded"
            self._sources.clear()
            self._dedupe.clear()
            return True


def begin_texture_publication(mod_dir=None):
    """Create a pending texture publication without changing the active one."""
    publication = TexturePublication(mod_dir)
    with _texture_lock:
        _texture_publications[publication.token] = publication
    return publication


def active_texture_publication(mod_dir=None):
    """Return the committed publication for ``mod_dir``, if it matches."""
    with _texture_lock:
        publication = _active_texture_publication
        if publication is None:
            return None
        if mod_dir is not None:
            requested = os.path.normcase(os.path.abspath(mod_dir))
            if publication.mod_dir != requested:
                return None
        return publication


def _lookup_texture(token, source_id):
    with _texture_lock:
        publication = _texture_publications.get(token)
        if publication is None:
            return None
        return publication._sources.get(source_id)


def _render_texture_source(source):
    """Render one source while bounding concurrent image decode/encoding."""
    with _texture_encode_semaphore:
        return _render_texture_png(
            source.path,
            max_size=source.max_size,
            preserve_alpha=source.preserve_alpha,
            texture_role=source.role,
        )


def publish_geometry(blob):
    """Publish one load's packed geometry and discard every older load."""
    if len(blob) > _MAX_GEOMETRY_BYTES:
        raise ValueError("Generated geometry exceeds the 512 MiB safety limit.")
    token = uuid.uuid4().hex
    with _geometry_lock:
        _geometry_blobs.clear()
        _geometry_blobs[token] = bytes(blob)
    return f"{_GEOMETRY_PREFIX}{token}"


def publish_payload_geometry(payload, geometry=None):
    """Publish the structured payload's packed geometry and its references.

    Normal loads pass the builder's append-only blob, so no encoded geometry
    string is created or decoded.  The structured mesh map also supports a
    base64-to-blob fallback for tests that deliberately exercise the direct
    builder form.
    """
    meshes = payload.setdefault("meshes", {})
    if geometry is not None:
        blob = (geometry.to_bytes() if hasattr(geometry, "to_bytes")
                else bytes(geometry))
        if blob:
            payload["geometry"] = {
                "url": publish_geometry(blob),
                "length": len(blob),
            }
        else:
            payload["geometry"] = None
        return

    blob = bytearray()
    for _name, entry in meshes.items():
        if not isinstance(entry, dict) or entry.get("error"):
            continue
        for field in ("pos", "uv", "idx"):
            encoded = entry.get(field)
            if not isinstance(encoded, str):
                continue
            raw = base64.b64decode(encoded)
            offset = len(blob)
            blob.extend(raw)
            entry[field] = {"offset": offset, "length": len(raw)}
        for target in entry.get("shape_targets") or []:
            for field in ("pos", "low_pos"):
                encoded = target.get(field)
                if not isinstance(encoded, str):
                    continue
                raw = base64.b64decode(encoded)
                offset = len(blob)
                blob.extend(raw)
                target[field] = {"offset": offset, "length": len(raw)}
    if blob:
        payload["geometry"] = {
            "url": publish_geometry(blob),
            "length": len(blob),
        }
    else:
        payload["geometry"] = None


class _Handler(http.server.SimpleHTTPRequestHandler):
    """Serves web/ with two extras: a /vendor/ mount and a rendered index.html."""

    vendor_root = ""
    template_vars: dict = {}

    def end_headers(self):
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                         "style-src 'self' 'unsafe-inline'; "
                         "img-src 'self' data: blob:; connect-src 'self'; "
                         "object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        super().end_headers()

    def do_GET(self):
        path = self._request_path()
        if path in ("/", "/index.html"):
            return self._send_index()
        if path.startswith(_GEOMETRY_PREFIX):
            return self._send_geometry(path[len(_GEOMETRY_PREFIX):])
        if path.startswith(_TEXTURE_PREFIX):
            return self._send_texture(path[len(_TEXTURE_PREFIX):])
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

    def _send_geometry(self, token):
        with _geometry_lock:
            blob = _geometry_blobs.pop(token, None)
        if blob is None:
            self.send_error(404, "Geometry load expired")
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(blob)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(blob)

    def _send_texture(self, address):
        """Serve one registered PNG without consuming its publication entry."""
        parts = address.split("/")
        if len(parts) != 2 or not all(parts):
            self.send_error(404, "Texture not found")
            return
        token, source_id = parts
        source = _lookup_texture(token, source_id)
        if source is None:
            self.send_error(404, "Texture not found")
            return
        png = _render_texture_source(source)
        if png is None:
            self.send_error(404, "Texture unavailable")
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(png)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(png)


class _ThreadingTCPServer(socketserver.ThreadingTCPServer):
    """Serve independent browser requests without serializing the UI."""

    allow_reuse_address = True
    daemon_threads = True


def start():
    """Serve the UI on an ephemeral localhost port; return its base URL.

    Binds to 127.0.0.1 so the port is never reachable from off the machine.
    """
    if not paths.has_vendored_three():
        raise RuntimeError("Vendored Three.js assets are required; run src/build.py to fetch them.")
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

    httpd = _ThreadingTCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}"

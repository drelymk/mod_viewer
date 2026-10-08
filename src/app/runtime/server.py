"""Localhost HTTP server for the web UI.

The UI is loaded into WebView2 over http://127.0.0.1:<port> rather than
pushed in as an HTML string -- NavigateToString gives the document an opaque
origin, which blocks relative `<script src>` and ES-module imports, and caps
the content at ~2 MB (Three.js alone exceeds that). Serving over a real
origin lets index.html, the stylesheet and the JS modules be ordinary files.

Two roots are exposed:
    /            the web/ directory (index.html, css/, js/)
    /vendor/     the vendored Three.js copy, when build.py has fetched it
    /texture/    opaque URLs for the active load's original texture files

index.html is rendered rather than served verbatim, so the importmap points at
the vendored WebGPU and TSL entry points.
"""

import functools
import http.server
import os
import secrets
import socketserver
import shutil
import threading
import uuid
from dataclasses import dataclass

from core.textures.dds import (MAX_MODEL_DDS_SIZE, native_dds_info,
                               native_dds_info_from_header)
from core.textures import load_texture_image_full, normalize_texture_role
from core.mod_source import ModSourceError
from core.textures.profiles import texture_profile_for
from app.settings import features, paths

THREE_VERSION = "0.185.0"
REPO_URL = "https://github.com/drelymk/mod_viewer"

_VENDOR_PREFIX = "/vendor/"
_GEOMETRY_PREFIX = "/geometry/"
_TEXTURE_PREFIX = "/texture/"
_MAX_GEOMETRY_BYTES = 512 * 1024 * 1024
_MAX_AUXILIARY_GEOMETRY_BLOBS = 2
_TEXTURE_CONTENT_TYPES = {
    ".dds": "image/vnd-ms.dds", ".png": "image/png",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
    ".bmp": "image/bmp", ".gif": "image/gif",
}
_geometry_lock = threading.RLock()
_geometry_blobs = {}
_texture_lock = threading.RLock()
_texture_publications = {}
_active_texture_publication = None


@dataclass(frozen=True)
class GeometryPublication:
    data: bytes
    auxiliary: bool


@dataclass(frozen=True)
class TextureSource:
    """One source registered in a committed texture publication."""

    path: str | None = None
    role: str = "diffuse"
    suffix: str = ".png"
    native_dds: bool = False
    data: bytes | None = None
    logical_path: str | None = None
    mod_source: object | None = None
    source_ref: object | None = None


class TexturePublication:
    """Transactional, opaque URL registry for one application model load."""

    def __init__(self, mod_dir=None, source=None):
        self.token = uuid.uuid4().hex
        self.mod_dir = (os.path.normcase(os.path.abspath(mod_dir))
                        if mod_dir else None)
        self._sources = {}
        self._dedupe = {}
        self.source = source
        self._state = "pending"
        self.game_profile = "unknown"

    def set_game_profile(self, game):
        """Retain the detected profile for manual normal-map role selection."""
        self.game_profile = texture_profile_for(game).name
        return self.game_profile

    def register(self, path, role=None, validate=False):
        """Publish a source once and return its opaque same-origin URL.

        The caller has already resolved the path through the core sandbox. The
        registry still requires a real file and never exposes that path in the
        URL, so browser requests can only address sources registered here.
        ``validate=True`` is reserved for explicit manual picks and performs
        one immediate validation so corrupt files return an error before they
        are persisted in viewer metadata.
        """
        if not path:
            return None
        source_ref = (self.source is not None
                      and getattr(self.source, "virtual", False)
                      and self.source.is_resource_reference(path))
        if source_ref:
            if not self.source.is_file(path):
                return None
            logical_path = self.source.logical_path(path)
            data = None
            path_identity = ("mod", logical_path.casefold())
        else:
            path = os.path.abspath(path)
            if not os.path.isfile(path):
                return None
            logical_path = None
            data = None
            path_identity = ("file", os.path.normcase(path))
        role = normalize_texture_role(role)
        suffix = os.path.splitext(logical_path if source_ref else path)[1].lower()
        if suffix not in _TEXTURE_CONTENT_TYPES:
            return None
        dedupe_key = (path_identity, role)
        existing_source = None
        with _texture_lock:
            if (_texture_publications.get(self.token) is not self
                    or self._state == "discarded"):
                return None
            source_id = self._dedupe.get(dedupe_key)
            if source_id is not None:
                existing_source = self._sources[source_id]
                if not validate:
                    return _texture_url(self.token, source_id, existing_source)

        is_dds = suffix == ".dds"

        if source_ref:
            dds_info = None
            if is_dds:
                try:
                    header = self.source.read_prefix(path, 148)
                    file_size = self.source.size(path)
                except (OSError, ModSourceError):
                    pass
                else:
                    dds_info = native_dds_info_from_header(
                        header, file_size, MAX_MODEL_DDS_SIZE,
                        source_name=logical_path)
        else:
            dds_info = (native_dds_info(
                path, MAX_MODEL_DDS_SIZE, source_name=logical_path)
                if is_dds else None)
        if is_dds and dds_info is None:
            return None
        source = existing_source or TextureSource(
            path=None if source_ref else path, data=data,
            logical_path=logical_path, role=role, suffix=suffix,
            native_dds=dds_info is not None,
            mod_source=self.source if source_ref else None,
            source_ref=path if source_ref else None)
        if validate and not source.native_dds and not _validate_texture_source(source):
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
            return _texture_url(self.token, source_id, source)

    def register_menu_image(self, path):
        """Publish a lazy menu source without changing its image bytes."""
        return self.register(path, "diffuse")

    def commit(self, *, replace=True):
        """Commit a publication, optionally retaining the active one."""
        global _active_texture_publication
        with _texture_lock:
            if self._state == "discarded":
                return False
            if replace:
                _texture_publications.clear()
            _texture_publications[self.token] = self
            if replace:
                _active_texture_publication = self
            self._state = "committed"
            return True

    def release(self):
        """Retire a committed auxiliary publication after session removal."""
        global _active_texture_publication
        with _texture_lock:
            _texture_publications.pop(self.token, None)
            if _active_texture_publication is self:
                _active_texture_publication = None
            self._sources.clear()
            self._dedupe.clear()
            self._state = "discarded"
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


def begin_texture_publication(mod_dir=None, source=None):
    """Create a pending texture publication without changing the active one."""
    publication = TexturePublication(mod_dir, source=source)
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


def _texture_url(token, source_id, source):
    return f"{_TEXTURE_PREFIX}{token}/{source_id}{source.suffix}"


def _validate_texture_source(source):
    """Validate explicit image picks without encoding a display copy."""
    try:
        data = _texture_source_data(source)
    except (OSError, ModSourceError):
        return False
    image = load_texture_image_full(data, source_name=source.logical_path)
    if image is None:
        return False
    image.close()
    return True


def _texture_source_data(source):
    """Resolve a registered source only for an explicit validation request."""
    if source.data is not None:
        return source.data
    if source.mod_source is not None:
        return source.mod_source.read_bytes(source.source_ref)
    return source.path


def publish_geometry(blob, *, replace=True, auxiliary=False):
    """Publish geometry; auxiliary loads retain at most two blobs and 512 MiB."""
    if len(blob) > _MAX_GEOMETRY_BYTES:
        raise ValueError("Generated geometry exceeds the 512 MiB safety limit.")
    token = uuid.uuid4().hex
    with _geometry_lock:
        if replace:
            _geometry_blobs.clear()
        if auxiliary:
            pending = [key for key, item in _geometry_blobs.items()
                       if item.auxiliary]
            total_bytes = len(blob) + sum(
                len(_geometry_blobs[key].data) for key in pending)
            while pending and (len(pending) >= _MAX_AUXILIARY_GEOMETRY_BLOBS
                               or total_bytes > _MAX_GEOMETRY_BYTES):
                total_bytes -= len(_geometry_blobs.pop(pending.pop(0)).data)
        _geometry_blobs[token] = GeometryPublication(bytes(blob), auxiliary)
    return f"{_GEOMETRY_PREFIX}{token}"


def publish_payload_geometry(payload, geometry, *, replace=True, auxiliary=False):
    """Publish the structured payload's packed geometry and its references.

    The builder owns one append-only binary blob for static and animated data.
    """
    blob = geometry.to_bytes() if hasattr(geometry, "to_bytes") else bytes(geometry)
    if blob:
        payload["geometry"] = {
            "url": publish_geometry(blob, replace=replace, auxiliary=auxiliary),
            "length": len(blob),
        }
    else:
        payload["geometry"] = None


def release_texture_publication(publication):
    """Retire one auxiliary texture publication without touching the active one."""
    if publication is None:
        return False
    return publication.release()


def release_geometry(url):
    """Release an unpublished geometry blob by its opaque URL."""
    if not isinstance(url, str) or not url.startswith(_GEOMETRY_PREFIX):
        return False
    token = url[len(_GEOMETRY_PREFIX):]
    if not token or "/" in token:
        return False
    with _geometry_lock:
        return _geometry_blobs.pop(token, None) is not None


def geometry_stats():
    """Return the currently retained geometry publication footprint."""
    with _geometry_lock:
        return {
            "pending_blob_count": len(_geometry_blobs),
            "pending_blob_bytes": sum(
                len(item.data) for item in _geometry_blobs.values()),
        }


class _Handler(http.server.SimpleHTTPRequestHandler):
    """Serves web/ with two extras: a /vendor/ mount and a rendered index.html."""

    vendor_root = ""
    template_vars: dict = {}
    csp_nonce = ""

    def end_headers(self):
        script_src = "'self'"
        if self.csp_nonce:
            script_src += f" 'nonce-{self.csp_nonce}'"
        self.send_header("Content-Security-Policy",
                         f"default-src 'self'; script-src {script_src}; "
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
            publication = _geometry_blobs.pop(token, None)
        if publication is None:
            self.send_error(404, "Geometry load expired")
            return
        blob = publication.data
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(blob)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(blob)

    def _send_texture(self, address):
        """Serve the original bytes of one registered texture."""
        parts = address.split("/")
        if len(parts) != 2 or not all(parts):
            self.send_error(404, "Texture not found")
            return
        token, requested_id = parts
        source_id, separator, suffix = requested_id.rpartition(".")
        if not separator:
            source_id, suffix = requested_id, "png"
        source = _lookup_texture(token, source_id)
        if source is None or source.suffix != "." + suffix:
            self.send_error(404, "Texture not found")
            return
        return self._send_texture_source(token, source_id, source)

    def _send_texture_source(self, token, source_id, source):
        """Stream a registered texture while checking its publication lifetime."""
        if source.data is not None or source.mod_source is not None:
            if _lookup_texture(token, source_id) is not source:
                self.send_error(404, "Texture unavailable")
                return
            if source.data is not None:
                data = source.data
            else:
                try:
                    data = source.mod_source.read_bytes(source.source_ref)
                except (OSError, ModSourceError):
                    self.send_error(404, "Texture unavailable")
                    return
            if _lookup_texture(token, source_id) is not source:
                self.send_error(404, "Texture unavailable")
                return
            self.send_response(200)
            self.send_header("Content-Type", _TEXTURE_CONTENT_TYPES[source.suffix])
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        try:
            stream = open(source.path, "rb")
            size = os.fstat(stream.fileno()).st_size
        except OSError:
            self.send_error(404, "Texture unavailable")
            return
        try:
            if _lookup_texture(token, source_id) is not source:
                self.send_error(404, "Texture unavailable")
                return
            self.send_response(200)
            self.send_header("Content-Type", _TEXTURE_CONTENT_TYPES[source.suffix])
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            shutil.copyfileobj(stream, self.wfile, length=1024 * 1024)
        finally:
            stream.close()


class _ThreadingTCPServer(socketserver.ThreadingTCPServer):
    """Serve independent browser requests without serializing the UI."""

    daemon_threads = True


def start(*, require_ui_assets=True):
    """Serve the UI on an ephemeral localhost port; return its base URL.

    Binds to 127.0.0.1 so the port is never reachable from off the machine.
    Geometry clients may skip the UI asset check while using the same server.
    """
    if require_ui_assets and not paths.has_vendored_three():
        raise RuntimeError("Vendored Three.js assets are required; run src/build.py to fetch them.")
    csp_nonce = secrets.token_urlsafe(32)
    three_url = f"{_VENDOR_PREFIX}three.webgpu.js"
    tsl_url = f"{_VENDOR_PREFIX}three.tsl.js"
    addons_url = f"{_VENDOR_PREFIX}addons/"

    # Feature flags only ever hide optional UI actions
    # (app/settings/features.py) -- baked into a <body> class server-side so
    # there's no flash of a control appearing then disappearing after load.
    flags = features.get_features()
    body_classes = []
    if not flags["export"]:
        body_classes.append("feature-export-off")
    if not flags["modify_toggle"]:
        body_classes.append("feature-modify-toggle-off")
    if not flags["open_disabled_mod"]:
        body_classes.append("feature-open-disabled-mod-off")
    if not flags["edit_mesh"]:
        body_classes.append("feature-edit-mesh-off")

    template_vars = {
        "__THREE_URL__": three_url,
        "__THREE_TSL_URL__": tsl_url,
        "__ADDONS_URL__": addons_url,
        "__BODY_CLASS__": " ".join(body_classes),
        "__APP_VERSION__": paths.APP_VERSION,
        "__REPO_URL__": REPO_URL,
        "__CSP_NONCE__": csp_nonce,
    }
    launch_handler = type(
        "_LaunchHandler", (_Handler,), {
            "vendor_root": paths.vendor_dir(),
            "template_vars": template_vars,
            "csp_nonce": csp_nonce,
        })
    handler = functools.partial(launch_handler, directory=paths.web_dir())

    httpd = _ThreadingTCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}"

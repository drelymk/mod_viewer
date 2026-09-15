"""CSP and template isolation contracts for the localhost UI server."""

import re
import zipfile
import urllib.request
from unittest.mock import patch

from app.runtime import server as server
from core.mod_source import ZipModSource


def _read_index(tmp_path, monkeypatch, feature_flags=None):
    web = tmp_path / "web"
    web.mkdir(parents=True)
    (web / "index.html").write_text(
        '<body class="__BODY_CLASS__"><script type="importmap" '
        'nonce="__CSP_NONCE__"></script></body>',
        encoding="utf-8")
    monkeypatch.setattr(server.paths, "has_vendored_three", lambda: True)
    monkeypatch.setattr(server.paths, "web_dir", lambda: str(web))
    monkeypatch.setattr(server.paths, "vendor_dir", lambda: str(web))
    monkeypatch.setattr(server.features, "get_features", lambda: {
        "export": True, "modify_toggle": True, "open_disabled_mod": True,
    } if feature_flags is None else feature_flags)

    response = urllib.request.urlopen(server.start(), timeout=5)
    return response.headers, response.read().decode("utf-8")


def test_server_generates_fresh_nonce_and_applies_it_consistently(
        tmp_path, monkeypatch):
    nonces = []
    for name in ("first", "second"):
        headers, body = _read_index(tmp_path / name, monkeypatch)
        csp = headers["Content-Security-Policy"]
        match = re.search(r"script-src 'self' 'nonce-([^']+)'", csp)

        assert match
        nonce = match.group(1)
        assert "script-src 'self' 'unsafe-inline'" not in csp
        assert "style-src 'self' 'unsafe-inline'" in csp
        assert f'nonce="{nonce}"' in body
        assert "__CSP_NONCE__" not in body
        nonces.append(nonce)

    assert nonces[0] != nonces[1]


def test_server_marks_disabled_mod_feature_as_hidden(tmp_path, monkeypatch):
    _headers, body = _read_index(
        tmp_path, monkeypatch,
        {"export": True, "modify_toggle": True, "open_disabled_mod": False},
    )

    assert "feature-open-disabled-mod-off" in body


def test_zip_texture_publication_keeps_member_bytes_private(tmp_path):
    archive_path = tmp_path / "mod.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("Mod/body.png", b"png-bytes")
    source = ZipModSource(archive_path)
    reads = []
    original_read_bytes = source.read_bytes

    def read_bytes(reference):
        reads.append(reference)
        return original_read_bytes(reference)

    source.read_bytes = read_bytes
    publication = server.begin_texture_publication(
        str(archive_path), source=source)
    try:
        member = source.resolve_resource("body.png")
        url = publication.register(member)
        duplicate = publication.register(member)
        entry = server._lookup_texture(publication.token, "0")

        assert url.endswith(".png")
        assert duplicate == url
        assert entry.path is None
        assert entry.data is None
        assert entry.logical_path == "body.png"
        assert entry.mod_source is source
        assert entry.source_ref is member
        assert reads == []

        with patch("app.runtime.server.render_texture_png",
                   return_value=b"PNG"):
            assert server._render_texture_request(
                publication.token, "0", entry) == b"PNG"
        assert reads == [member]
    finally:
        publication.discard()

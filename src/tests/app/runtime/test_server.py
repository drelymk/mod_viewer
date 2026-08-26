"""CSP and template isolation contracts for the localhost UI server."""

import re
import urllib.request

from app.runtime import server as server


def _read_index(tmp_path, monkeypatch):
    web = tmp_path / "web"
    web.mkdir(parents=True)
    (web / "index.html").write_text(
        '<script type="importmap" nonce="__CSP_NONCE__"></script>',
        encoding="utf-8")
    monkeypatch.setattr(server.paths, "has_vendored_three", lambda: True)
    monkeypatch.setattr(server.paths, "web_dir", lambda: str(web))
    monkeypatch.setattr(server.paths, "vendor_dir", lambda: str(web))
    monkeypatch.setattr(server.features, "get_features", lambda: {
        "export": True, "modify_toggle": True,
    })

    response = urllib.request.urlopen(server.start(), timeout=5)
    return response.headers, response.read().decode("utf-8")


def test_server_uses_one_nonce_for_csp_and_importmap(tmp_path, monkeypatch):
    headers, body = _read_index(tmp_path, monkeypatch)

    csp = headers["Content-Security-Policy"]
    match = re.search(r"script-src 'self' 'nonce-([^']+)'", csp)
    assert match
    nonce = match.group(1)
    assert "script-src 'self' 'unsafe-inline'" not in csp
    assert "style-src 'self' 'unsafe-inline'" in csp
    assert f'nonce="{nonce}"' in body
    assert "__CSP_NONCE__" not in body


def test_server_nonce_changes_between_launches(tmp_path, monkeypatch):
    _headers, first_body = _read_index(tmp_path / "first", monkeypatch)
    _headers, second_body = _read_index(tmp_path / "second", monkeypatch)

    first = re.search(r'nonce="([^"]+)"', first_body).group(1)
    second = re.search(r'nonce="([^"]+)"', second_body).group(1)
    assert first != second

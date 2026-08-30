"""CSP and template isolation contracts for the localhost UI server."""

import re
import urllib.request

from app.runtime import server as server


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

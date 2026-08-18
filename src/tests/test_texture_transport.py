"""Regression tests for lazy application texture transport."""

import functools
import os
import socketserver
import struct
import threading
from urllib.error import HTTPError
from urllib.request import urlopen
from unittest.mock import patch

import pytest
from PIL import Image

from app import metadata, server
from core.mesh_builder import (GeometryBlob, _encode_texture,
                               _render_texture_png, build_mesh_result)


def _write_geometry(root):
    with open(os.path.join(root, "p.buf"), "wb") as stream:
        stream.write(struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0))
    with open(os.path.join(root, "t.buf"), "wb") as stream:
        stream.write(struct.pack("<6f", 0, 0, 1, 0, 0, 1))
    with open(os.path.join(root, "i.buf"), "wb") as stream:
        stream.write(struct.pack("<3I", 0, 1, 2))


def _group(texture_names):
    draw = {
        "label": "Body-1", "count": 3, "start": 0, "base": 0,
        "conditions": [],
    }
    for field, name in texture_names.items():
        draw[f"{field}_default_file"] = name
    return [{
        "name": "Body", "display_name": "Body",
        "position_file": "p.buf", "texcoord_file": "t.buf",
        "position_stride": 12, "texcoord_stride": 8,
        "ib_file": "i.buf", "index_size": 4,
        "draws": [draw],
    }]


def test_render_cache_stores_png_bytes_but_direct_wrapper_stays_data_uri(tmp_path):
    path = tmp_path / "shared.png"
    Image.new("RGB", (2, 1), (128, 128, 32)).save(path)

    png = _render_texture_png(str(path))
    uri = _encode_texture(str(path))

    assert isinstance(png, bytes) and png.startswith(b"\x89PNG")
    assert uri.startswith("data:image/png;base64,")


def test_mesh_builder_publishes_sources_without_rendering(tmp_path):
    _write_geometry(str(tmp_path))
    Image.new("RGB", (1, 1), (128, 128, 32)).save(tmp_path / "shared.png")
    registered = []

    def register(path, role):
        registered.append((os.path.basename(path), role))
        return f"/texture/test/{len(registered) - 1}"

    with patch("core.mesh_builder._render_texture_png",
               side_effect=AssertionError("lazy app path rendered a texture")):
        built = build_mesh_result(
            _group({
                "texture": "shared.png",
                "normal_map": "shared.png",
                "light_map": "shared.png",
            }), str(tmp_path), geometry=GeometryBlob(),
            texture_source=register)

    assert built.textures == {
        "diffuse::shared.png": "/texture/test/0",
        "normal_map::shared.png": "/texture/test/1",
        "light_map::shared.png": "/texture/test/2",
    }
    assert registered == [
        ("shared.png", "diffuse"),
        ("shared.png", "normal_map"),
        ("shared.png", "light_map"),
    ]


def test_publication_deduplicates_by_role_and_invalidates_old_load(tmp_path):
    path = tmp_path / "shared.png"
    Image.new("RGB", (1, 1), (128, 128, 32)).save(path)

    first = server.begin_texture_publication(str(tmp_path))
    diffuse = first.register(str(path), "diffuse")
    same_diffuse = first.register(str(path), "diffuse")
    normal = first.register(str(path), "normal_map")
    first.commit()

    assert diffuse == same_diffuse
    assert normal != diffuse
    assert server._lookup_texture(first.token, "0").role == "diffuse"

    second = server.begin_texture_publication(str(tmp_path))
    second_url = second.register(str(path), "diffuse")
    second.commit()

    assert server._lookup_texture(first.token, "0") is None
    assert server._lookup_texture(second.token, "0").path == str(path)


def test_texture_endpoint_serves_png_and_keeps_source_reusable(tmp_path):
    path = tmp_path / "shared.png"
    Image.new("RGB", (2, 1), (128, 128, 32)).save(path)
    publication = server.begin_texture_publication(str(tmp_path))
    texture_url = publication.register(str(path))
    publication.commit()

    handler = functools.partial(server._Handler, directory=str(tmp_path))
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{httpd.server_address[1]}{texture_url}"
        with urlopen(url) as response:
            first = response.read()
            assert response.headers["Content-Type"].startswith("image/png")
        with urlopen(url) as response:
            second = response.read()
        assert first == second and first.startswith(b"\x89PNG")

        with pytest.raises(HTTPError) as error:
            urlopen(url + "/not-an-id")
        assert error.value.code == 404
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_metadata_hydration_registers_saved_textures_without_rendering(tmp_path):
    path = tmp_path / "shared.png"
    Image.new("RGB", (1, 1), (128, 128, 32)).save(path)
    payload = {
        "meshes": {
            "Body-1": {
                "component": "Body", "drawindexed": [3, 0, 0],
                "texture_options": [],
            },
        },
        "textures": {},
    }
    data = {"textures": {"Body::3,0,0": {
        "tex_key": "shared.png", "label": "Shared", "manual": True,
        "normal_map": "shared.png",
    }}}
    registered = []

    def register(source, role):
        registered.append(role)
        return f"/texture/test/{role}"

    with patch("core.mesh_builder._render_texture_png",
               side_effect=AssertionError("metadata hydration rendered a texture")):
        metadata.hydrate_textures(
            str(tmp_path), payload, data, texture_source=register)

    assert registered == ["diffuse", "normal_map"]
    assert payload["textures"] == {
        "diffuse::shared.png": "/texture/test/diffuse",
        "normal_map::shared.png": "/texture/test/normal_map",
    }

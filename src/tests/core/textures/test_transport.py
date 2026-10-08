"""Regression tests for lazy application texture transport."""

import functools
import os
import socketserver
import struct
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError
from urllib.request import urlopen
from unittest.mock import patch

import pytest
from PIL import Image

from app.mods import metadata as metadata
from app.mods import loader as mod_loader
from tests.support_snapshot import snapshot_context
from app.runtime import server as server
from app.bridge.api import ModViewerAPI
from core.ini.document import IniDocument
from core.geometry.mesh_builder import GeometryBlob, build_mesh_result
from core.mod_source import DirectoryModSource, SevenZipModSource, ZipModSource
from core.sevenzip import SevenZipEntry
from core.textures.pipeline import encode_texture_key
from tests.support.dds_data import write_bc7_dds


def test_unsupported_native_candidate_retains_choice_and_reports_manual_pick_error(tmp_path):
    header = bytearray(128)
    header[:4] = b'DDS '
    for offset, value in {4: 124, 8: 0x100F, 12: 4, 16: 4, 20: 4,
                          76: 32, 80: 0x20000, 88: 8, 92: 0xFF,
                          108: 0x1000}.items():
        struct.pack_into('<I', header, offset, value)
    path = tmp_path / 'single-channel.dds'
    path.write_bytes(bytes(header) + bytes([128]) * 16)
    with Image.open(path) as image:
        assert image.mode == 'L'
        assert image.size == (4, 4)
    key = 'diffuse::single-channel.dds'
    publication = server.begin_texture_publication(str(tmp_path))
    try:
        with patch('PIL.Image.Image.save',
                   side_effect=AssertionError('Model DDS must not render eagerly')):
            payload = {'meshes': {'mesh-01': {
                'component': 'Component1',
                'identity': {'key': 'mesh-identity-01'},
                'texture_options': [{'tex_key': key, 'file': path.name,
                                     'label': 'single-channel'}],
            }}, 'textures': {}}
            metadata.hydrate_textures(
                str(tmp_path), payload, data={},
                texture_source=publication.register, texture_profile='wuwa')
            assert payload['texture_pools']['p0'][0]['tex_key'] == key
            assert key not in payload['textures']
            result = encode_texture_key(
                str(tmp_path), key, texture_source=functools.partial(
                    publication.register, validate=True))
        assert result == {
            'error': 'Could not read this file as an image.',
            'error_code': 'texture_load_failed', 'file': path.name}
        assert not publication._sources
    finally:
        publication.discard()


def _write_geometry(root):
    with open(os.path.join(root, "p.buf"), "wb") as stream:
        stream.write(struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0))
    with open(os.path.join(root, "t.buf"), "wb") as stream:
        stream.write(struct.pack("<6f", 0, 0, 1, 0, 0, 1))
    with open(os.path.join(root, "i.buf"), "wb") as stream:
        stream.write(struct.pack("<3I", 0, 1, 2))


def _group(texture_names):
    draw = {
        "label": "Component01-1", "count": 3, "start": 0, "base": 0,
        "conditions": [],
    }
    for field, name in texture_names.items():
        draw[f"{field}_default_file"] = name
    return [{
        "name": "Component01", "display_name": "Component01",
        "position_file": "p.buf", "texcoord_file": "t.buf",
        "position_stride": 12, "texcoord_stride": 8,
        "ib_file": "i.buf", "index_size": 4,
        "draws": [draw],
    }]


def test_mesh_builder_publishes_sources_without_rendering(tmp_path):
    _write_geometry(str(tmp_path))
    Image.new("RGB", (1, 1), (128, 128, 32)).save(tmp_path / "shared.png")
    registered = []

    def register(path, role):
        registered.append((os.path.basename(path), role))
        return f"/texture/test/{len(registered) - 1}"

    with patch("PIL.Image.Image.save",
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


def test_mod_loader_app_path_never_renders_model_textures(tmp_path):
    _write_geometry(str(tmp_path))
    Image.new("RGB", (1, 1), (128, 128, 32)).save(tmp_path / "shared.png")
    ini_path = tmp_path / "mod.ini"
    ini_path.write_text(
        "[TextureOverrideComponent01Position]\n"
        "vb0 = ResourceComponent01Position\n"
        "[TextureOverrideComponent01Texcoord]\n"
        "vb1 = ResourceComponent01Texcoord\n"
        "[TextureOverrideComponent01]\n"
        "ib = ResourceComponent01IB\n"
        "Resource\\GIMI\\Diffuse = ResourceComponent01Diffuse\n"
        "drawindexed = 3, 0, 0\n"
        "[ResourceComponent01Position]\n"
        "filename = p.buf\n"
        "stride = 12\n"
        "[ResourceComponent01Texcoord]\n"
        "filename = t.buf\n"
        "stride = 8\n"
        "[ResourceComponent01IB]\n"
        "filename = i.buf\n"
        "format = R32_UINT\n"
        "[ResourceComponent01Diffuse]\n"
        "filename = shared.png\n",
        encoding="utf-8",
    )
    context = snapshot_context(
        str(tmp_path), [str(ini_path)],
        {str(ini_path): IniDocument.load(str(ini_path))}, {})
    registered = []

    def register(path, role):
        registered.append((os.path.basename(path), role))
        return f"/texture/integration/{len(registered) - 1}"

    with patch("PIL.Image.Image.save",
               side_effect=AssertionError("loader rendered a model texture")):
        loaded = mod_loader.load_mod(
            context=context, geometry=GeometryBlob(), texture_source=register)

    assert not loaded.get("error")
    assert loaded["textures"] == {"diffuse::shared.png": "/texture/integration/0"}
    assert registered == [("shared.png", "diffuse")]


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
    second.discard()

    later_manual_url = first.register(str(path), "material_map")
    assert second_url.endswith("/0.png")
    assert server._lookup_texture(first.token, "0").path == str(path)
    assert server._lookup_texture(first.token, "2").role == "material_map"
    assert later_manual_url.endswith("/2.png")
    assert server._lookup_texture(second.token, "0") is None

    replacement = server.begin_texture_publication(str(tmp_path))
    replacement.register(str(path), "diffuse")
    replacement.commit()
    assert server._lookup_texture(first.token, "0") is None
    assert server._lookup_texture(replacement.token, "0").path == str(path)


def test_auxiliary_publication_retains_active_mod_publication(tmp_path):
    path = tmp_path / "shared.png"
    Image.new("RGB", (1, 1), (128, 128, 32)).save(path)

    mod = server.begin_texture_publication(str(tmp_path))
    mod.register(str(path), "diffuse")
    mod.commit()
    fill = server.begin_texture_publication(str(tmp_path))
    fill.register(str(path), "normal_map")
    fill.commit(replace=False)

    assert server.active_texture_publication(str(tmp_path)) is mod
    assert server._lookup_texture(mod.token, "0") is not None
    assert server._lookup_texture(fill.token, "0") is not None

    fill.release()
    assert server.active_texture_publication(str(tmp_path)) is mod
    assert server._lookup_texture(mod.token, "0") is not None
    assert server._lookup_texture(fill.token, "0") is None


def test_wuwa_manual_normal_pick_publishes_only_raw_source(tmp_path):
    path = tmp_path / "normal.png"
    Image.new("RGBA", (1, 1), (128, 128, 12, 34)).save(path)
    publication = server.begin_texture_publication(str(tmp_path))
    publication.set_game_profile("wuwa")
    publication.commit()

    class Dialog:
        def create_file_dialog(self, *_args, **_kwargs):
            return [str(path)]

    api = ModViewerAPI()
    api._window = Dialog()
    api._access.remember_mod_picker_selection(tmp_path)

    result = api.pick_texture_file(str(tmp_path), "normal_map")

    assert result["tex_key"] == "normal_data::normal.png"
    assert result["role"] == "normal_data"
    assert "normal_data_key" not in result
    assert "normal_data_file" not in result
    assert "normal_data_uri" not in result
    assert server._lookup_texture(publication.token, "0").role == "normal_data"


def test_hydrate_texture_pool_publishes_all_roles_without_rendering(tmp_path):
    assert not hasattr(ModViewerAPI, "load_texture_file")
    for name in ("pool.png", "normal.png", "packed.png", "light.png",
                 "material.png"):
        Image.new("RGBA", (1, 1), (128, 128, 32, 255)).save(tmp_path / name)
    payload = {
        "meshes": {
            "Component01-1": {
                "source": "Root.ini", "component": "Component01",
                "drawindexed": [3, 0, 0],
                "identity": {"key": "mesh-identity-01"},
                "texture_options": [{
                    "tex_key": "diffuse::pool.png", "file": "pool.png",
                    "label": "Pool", "normal_map": "normal.png",
                    "normal_data": "packed.png", "light_map": "light.png",
                    "material_map": "material.png",
                }],
            },
        },
        "textures": {},
    }
    registered = []

    def register(path, role):
        registered.append((os.path.basename(path), role))
        return f"/texture/test/{role}"

    with patch("PIL.Image.Image.save",
               side_effect=AssertionError("pool publication rendered a texture")):
        metadata.hydrate_textures(
            str(tmp_path), payload, texture_source=register)

    assert payload["meshes"]["Component01-1"]["texture_pool_id"] == "p0"
    assert "texture_options" not in payload["meshes"]["Component01-1"]
    assert set(payload["textures"]) == {
        "diffuse::pool.png", "normal_map::normal.png",
        "normal_data::packed.png", "light_map::light.png",
        "material_map::material.png",
    }
    assert {role for _name, role in registered} == {
        "diffuse", "normal_map", "normal_data", "light_map", "material_map",
    }


def test_native_dds_endpoint_streams_original_bytes_and_rejects_png_alias(tmp_path):
    dds = tmp_path / "native.dds"
    write_bc7_dds(dds)
    invalid = tmp_path / "unsupported.dds"
    invalid.write_bytes(b"not a DDS")
    publication = server.begin_texture_publication(str(tmp_path))
    native_url = publication.register(str(dds))
    assert publication.register_menu_image(str(dds)) == native_url
    rejected_url = publication.register(str(invalid))
    publication.commit()

    assert native_url.endswith("/0.dds")
    assert rejected_url is None
    assert server._lookup_texture(publication.token, "0").native_dds

    handler = functools.partial(server._Handler, directory=str(tmp_path))
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        with patch("PIL.Image.Image.save", side_effect=AssertionError("image encoding")):
            for attempt in range(2):
                with urlopen(base_url + native_url) as response:
                    if attempt == 0:
                        assert response.headers["Content-Type"] == "image/vnd-ms.dds"
                        assert int(response.headers["Content-Length"]) == dds.stat().st_size
                    assert response.read() == dds.read_bytes()

        with patch("PIL.Image.Image.save",
                   side_effect=AssertionError("model DDS rendered to PNG")):
            for alias in (native_url[:-4] + ".png", native_url[:-4]):
                with pytest.raises(HTTPError) as error:
                    urlopen(base_url + alias)
                assert error.value.code == 404
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_normal_roles_use_native_dds(tmp_path):
    dds = tmp_path / "shared.dds"
    write_bc7_dds(dds)
    publication = server.begin_texture_publication(str(tmp_path))

    normal_map_url = publication.register(str(dds), "normal_map")
    normal_data_url = publication.register(str(dds), "normal_data")
    within_limit = tmp_path / "within-limit.dds"
    write_bc7_dds(within_limit, width=2049, height=4)
    within_limit_url = publication.register(str(within_limit))

    assert normal_map_url.endswith(".dds")
    assert normal_data_url.endswith(".dds")
    assert within_limit_url.endswith(".dds")
    assert server._lookup_texture(publication.token, "0").native_dds is True
    assert server._lookup_texture(publication.token, "1").native_dds is True
    assert server._lookup_texture(publication.token, "2").native_dds is True


def test_model_and_menu_dds_share_the_dimension_limit(tmp_path):
    accepted = tmp_path / "accepted.dds"
    rejected = tmp_path / "rejected.dds"
    write_bc7_dds(accepted, width=8192, height=4)
    write_bc7_dds(rejected, width=8193, height=4)
    publication = server.begin_texture_publication(str(tmp_path))
    try:
        url = publication.register(str(accepted))
        assert url.endswith(".dds")
        assert publication.register_menu_image(str(accepted)) == url
        assert publication.register(str(rejected)) is None
        assert publication.register_menu_image(str(rejected)) is None
        assert server._lookup_texture(publication.token, "0").suffix == ".dds"
    finally:
        publication.discard()


@pytest.mark.parametrize("extension,archived,content_type", [
    ("png", False, "image/png"), ("jpg", True, "image/jpeg"),
    ("dds", True, "image/vnd-ms.dds"),
])
def test_textures_stream_original_bytes_lazily_from_disk_and_archive(
        tmp_path, extension, archived, content_type):
    path = tmp_path / f"image.{extension}"
    if extension == "dds":
        write_bc7_dds(path)
    else:
        Image.new("RGBA" if extension == "png" else "RGB", (300, 2),
                  (30, 60, 90, 128) if extension == "png" else (30, 60, 90)).save(path)
    original = path.read_bytes()
    if archived:
        archive_path = tmp_path / "mod.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr(path.name, original)
        source = ZipModSource(archive_path)
    else:
        source = DirectoryModSource(tmp_path)
    candidate = source.resolve_resource(path.name)
    publication = server.begin_texture_publication(str(tmp_path), source=source)
    httpd = server._ThreadingTCPServer(("127.0.0.1", 0), functools.partial(
        server._Handler, directory=str(tmp_path)))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        with patch.object(source, "read_bytes", wraps=source.read_bytes) as read, \
                patch.object(source, "read_prefix", wraps=source.read_prefix) as prefix, \
                patch("PIL.Image.Image.save", side_effect=AssertionError("image encoding")), \
                patch("app.runtime.server.load_texture_image_full", side_effect=AssertionError("image decode")):
            url = publication.register_menu_image(candidate)
            assert url == publication.register(candidate, "diffuse")
            assert url.endswith("." + extension)
            read.assert_not_called()
            if extension == "dds":
                prefix.assert_called_once_with(candidate, 148)
            publication.commit()
            with urlopen(base_url + url) as response:
                assert response.headers["Content-Type"] == content_type
                assert response.read() == original
            assert read.call_count == int(archived)
        with patch("PIL.Image.Image.save", side_effect=AssertionError("image encoding")):
            assert publication.register(candidate, validate=True) == url
            invalid = tmp_path / f"invalid.{extension}"
            invalid.write_bytes(original[:8])
            assert publication.register(str(invalid), validate=True) is None
        unsupported = tmp_path / "unsupported.bin"
        unsupported.write_bytes(original)
        assert publication.register(str(unsupported)) is None
        publication.release()
        with pytest.raises(HTTPError) as expired:
            urlopen(base_url + url)
        assert expired.value.code == 404
    finally:
        publication.release()
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("archive_suffix", [".7z", ".rar"])
def test_sevenzip_native_dds_transport_reads_prefix_then_original_member(
        tmp_path, archive_suffix):
    dds = tmp_path / "native.dds"
    write_bc7_dds(dds)
    dds_bytes = dds.read_bytes()
    archive_path = tmp_path / f"mod{archive_suffix}"
    archive_path.write_bytes(b"mock archive")

    class Client:
        def __init__(self):
            self.calls = []

        def list_members(self, _path):
            self.calls.append("list")
            return [SevenZipEntry("Wrapper/native.dds", len(dds_bytes))]

        def extract_all(self, _path, output_dir):
            self.calls.append("extract")
            target = os.path.join(output_dir, "Wrapper", "native.dds")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as stream:
                stream.write(dds_bytes)

    client = Client()
    source = SevenZipModSource(archive_path, client=client)
    publication = server.begin_texture_publication(
        str(archive_path), source=source)
    httpd = None
    try:
        member = source.resolve_resource("native.dds")
        url = publication.register(member)
        assert url.endswith(".dds")
        assert client.calls == ["list", "extract"]

        publication.commit()
        handler = functools.partial(server._Handler, directory=str(tmp_path))
        httpd = server._ThreadingTCPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{httpd.server_address[1]}"
        with urlopen(base_url + url) as response:
            assert response.headers["Content-Type"] == "image/vnd-ms.dds"
            assert response.read() == dds_bytes
        assert client.calls == ["list", "extract"]
    finally:
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        publication.discard()


def test_retired_texture_request_is_rejected_after_source_open(tmp_path):
    import builtins

    path = tmp_path / "image.png"
    Image.new("RGB", (1, 1), (30, 60, 90)).save(path)
    old = server.begin_texture_publication(str(tmp_path))
    old_url = old.register(str(path))
    old.commit()
    current = server.begin_texture_publication(str(tmp_path))
    current_url = current.register(str(path))
    opened = threading.Event()
    release = threading.Event()
    original_open = builtins.open

    def blocked_open(file, *args, **kwargs):
        if str(file) == str(path):
            opened.set()
            assert release.wait(5), "source gate was not released"
        return original_open(file, *args, **kwargs)

    handler = functools.partial(server._Handler, directory=str(tmp_path))
    httpd = server._ThreadingTCPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{httpd.server_address[1]}"
    def fetch(url):
        with urlopen(base_url + url, timeout=5) as response:
            return response.read()
    try:
        with patch("builtins.open", side_effect=blocked_open), ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(fetch, old_url)
            assert opened.wait(2)
            current.commit()
            release.set()
            with pytest.raises(HTTPError) as expired:
                pending.result(timeout=5)
            assert expired.value.code == 404
            assert fetch(current_url) == path.read_bytes()
    finally:
        release.set()
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
        current.release()

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
from core.mod_source import SevenZipModSource, ZipModSource
from core.sevenzip import SevenZipEntry
from core.textures import (encode_texture_data_uri, render_texture_png,
                           set_texture_profile_hook)


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


def _write_bc7_dds(path, width=4, height=4):
    """Write one valid DX10 BC7 block for transport endpoint tests."""
    data = bytearray(148)
    data[:4] = b"DDS "
    struct.pack_into("<I", data, 4, 124)
    struct.pack_into("<II", data, 12, height, width)
    struct.pack_into("<I", data, 76, 32)
    struct.pack_into("<II", data, 80, 4, int.from_bytes(b"DX10", "little"))
    struct.pack_into("<IIIII", data, 128, 98, 3, 0, 1, 0)
    data.extend(bytes(((width + 3) // 4) * ((height + 3) // 4) * 16))
    path.write_bytes(data)






def test_mesh_builder_publishes_sources_without_rendering(tmp_path):
    _write_geometry(str(tmp_path))
    Image.new("RGB", (1, 1), (128, 128, 32)).save(tmp_path / "shared.png")
    registered = []

    def register(path, role):
        registered.append((os.path.basename(path), role))
        return f"/texture/test/{len(registered) - 1}"

    with patch("core.textures.render_texture_png",
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
        "[TextureOverrideBodyPosition]\n"
        "vb0 = ResourceBodyPosition\n"
        "[TextureOverrideBodyTexcoord]\n"
        "vb1 = ResourceBodyTexcoord\n"
        "[TextureOverrideBody]\n"
        "ib = ResourceBodyIB\n"
        "Resource\\GIMI\\Diffuse = ResourceBodyDiffuse\n"
        "drawindexed = 3, 0, 0\n"
        "[ResourceBodyPosition]\n"
        "filename = p.buf\n"
        "stride = 12\n"
        "[ResourceBodyTexcoord]\n"
        "filename = t.buf\n"
        "stride = 8\n"
        "[ResourceBodyIB]\n"
        "filename = i.buf\n"
        "format = R32_UINT\n"
        "[ResourceBodyDiffuse]\n"
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

    with patch("core.textures.render_texture_png",
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
            "Body-1": {
                "source": "Root.ini", "component": "Body",
                "drawindexed": [3, 0, 0],
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

    with patch("core.textures.render_texture_png",
               side_effect=AssertionError("pool publication rendered a texture")):
        metadata.hydrate_textures(
            str(tmp_path), payload, texture_source=register)

    assert payload["meshes"]["Body-1"]["texture_pool_id"] == "p0"
    assert "texture_options" not in payload["meshes"]["Body-1"]
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
    _write_bc7_dds(dds)
    invalid = tmp_path / "unsupported.dds"
    invalid.write_bytes(b"not a DDS")
    publication = server.begin_texture_publication(str(tmp_path))
    native_url = publication.register(str(dds))
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
        class RejectEncodeSemaphore:
            def __enter__(self):
                raise AssertionError("native DDS entered the PNG semaphore")

            def __exit__(self, *_args):
                return False

        with patch.object(server, "_texture_encode_semaphore",
                          RejectEncodeSemaphore()):
            for attempt in range(2):
                with urlopen(base_url + native_url) as response:
                    if attempt == 0:
                        assert response.headers["Content-Type"] == (
                            "image/vnd-ms.dds")
                        assert int(response.headers["Content-Length"]) == (
                            dds.stat().st_size)
                    assert response.read() == dds.read_bytes()

        with patch("app.runtime.server.render_texture_png",
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
    _write_bc7_dds(dds)
    publication = server.begin_texture_publication(str(tmp_path))

    normal_map_url = publication.register(str(dds), "normal_map")
    normal_data_url = publication.register(str(dds), "normal_data")
    within_limit = tmp_path / "within-limit.dds"
    _write_bc7_dds(within_limit, width=2049, height=4)
    within_limit_url = publication.register(str(within_limit))

    assert normal_map_url.endswith(".dds")
    assert normal_data_url.endswith(".dds")
    assert within_limit_url.endswith(".dds")
    assert server._lookup_texture(publication.token, "0").native_dds is True
    assert server._lookup_texture(publication.token, "1").native_dds is True
    assert server._lookup_texture(publication.token, "2").native_dds is True


def test_model_dds_limit_is_independent_of_png_size(tmp_path):
    accepted = tmp_path / "accepted.dds"
    rejected = tmp_path / "rejected.dds"
    _write_bc7_dds(accepted, width=8192, height=4)
    _write_bc7_dds(rejected, width=8193, height=4)
    publication = server.begin_texture_publication(str(tmp_path))
    try:
        url = publication.register(str(accepted))
        assert url.endswith(".dds")
        assert publication.register(str(rejected)) is None
        assert server._lookup_texture(publication.token, "0").max_size == 2048
    finally:
        publication.discard()


def test_menu_dds_publication_defers_png_render_until_requested(tmp_path):
    dds = tmp_path / "menu.dds"
    _write_bc7_dds(dds)
    publication = server.begin_texture_publication(str(tmp_path))
    try:
        with patch("app.runtime.server.render_texture_png",
                   return_value=b"PNG") as render:
            url = publication.register_menu_image(str(dds))
            assert url.endswith(".png")
            assert render.call_count == 0

            source_id = url.rsplit("/", 1)[1][:-4]
            source = server._lookup_texture(publication.token, source_id)
            assert source is not None
            assert source.native_dds is False

            assert server._render_texture_request(
                publication.token, source_id, source) == b"PNG"
            render.assert_called_once()
            assert render.call_args.args[0] == str(dds)
            assert render.call_args.kwargs["max_size"] == 256
            assert render.call_args.kwargs["preserve_alpha"] is True
    finally:
        publication.discard()


def test_zip_native_dds_reads_header_at_registration_and_original_bytes_on_request(
        tmp_path):
    dds = tmp_path / "native.dds"
    _write_bc7_dds(dds)
    dds_bytes = dds.read_bytes()
    archive_path = tmp_path / "mod.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("Mod/native.dds", dds_bytes)

    source = ZipModSource(archive_path)
    prefix_reads = []
    full_reads = []
    original_read_prefix = source.read_prefix
    original_read_bytes = source.read_bytes

    def read_prefix(reference, length):
        prefix_reads.append((reference, length))
        return original_read_prefix(reference, length)

    def read_bytes(reference):
        full_reads.append(reference)
        return original_read_bytes(reference)

    source.read_prefix = read_prefix
    source.read_bytes = read_bytes
    publication = server.begin_texture_publication(
        str(archive_path), source=source)
    httpd = None
    try:
        member = source.resolve_resource("native.dds")
        url = publication.register(member)
        entry = server._lookup_texture(publication.token, "0")

        assert url.endswith(".dds")
        assert entry.native_dds is True
        assert prefix_reads == [(member, 148)]
        assert full_reads == []

        publication.commit()
        handler = functools.partial(server._Handler, directory=str(tmp_path))
        httpd = server._ThreadingTCPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{httpd.server_address[1]}"
        with urlopen(base_url + url) as response:
            assert response.headers["Content-Type"] == "image/vnd-ms.dds"
            assert response.read() == dds_bytes
        assert full_reads == [member]
    finally:
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        publication.discard()


@pytest.mark.parametrize("archive_suffix", [".7z", ".rar"])
def test_sevenzip_native_dds_transport_reads_prefix_then_original_member(
        tmp_path, archive_suffix):
    dds = tmp_path / "native.dds"
    _write_bc7_dds(dds)
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


def test_texture_requests_are_threaded_but_rendering_is_bounded(tmp_path):
    paths = []
    for index in range(3):
        path = tmp_path / f"texture-{index}.png"
        Image.new("RGB", (1, 1), (index, 128, 32)).save(path)
        paths.append(path)

    publication = server.begin_texture_publication(str(tmp_path))
    texture_urls = [publication.register(str(path)) for path in paths]
    publication.commit()

    active = 0
    peak = 0
    state_lock = threading.Lock()
    two_started = threading.Event()
    release = threading.Event()

    def blocked_render(*args, **kwargs):
        nonlocal active, peak
        with state_lock:
            active += 1
            peak = max(peak, active)
            if active == 2:
                two_started.set()
        try:
            assert release.wait(5), "test render gate was not released"
            return b"PNG"
        finally:
            with state_lock:
                active -= 1

    handler = functools.partial(server._Handler, directory=str(tmp_path))
    httpd = server._ThreadingTCPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{httpd.server_address[1]}"

    def fetch(texture_url):
        with urlopen(base_url + texture_url, timeout=5) as response:
            return response.read()

    reached_two = False
    try:
        with patch("app.runtime.server.render_texture_png", side_effect=blocked_render):
            with ThreadPoolExecutor(max_workers=3) as executor:
                futures = [executor.submit(fetch, texture_url)
                           for texture_url in texture_urls]
                reached_two = two_started.wait(2)
                release.set()
                results = [future.result(timeout=5) for future in futures]
        assert results == [b"PNG"] * 3
    finally:
        release.set()
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)

    assert reached_two
    assert peak == server._TEXTURE_ENCODE_CONCURRENCY == 2


def test_retired_texture_request_skips_render_after_waiting_for_slot(tmp_path):
    old_first = tmp_path / "old-first.png"
    old_queued = tmp_path / "old-queued.png"
    current = tmp_path / "current.png"
    for path, color in ((old_first, (1, 128, 32)),
                        (old_queued, (2, 128, 32)),
                        (current, (3, 128, 32))):
        Image.new("RGB", (1, 1), color).save(path)

    old_publication = server.begin_texture_publication(str(tmp_path / "old"))
    old_first_url = old_publication.register(str(old_first))
    old_queued_url = old_publication.register(str(old_queued))
    old_publication.commit()
    old_first_source = server._lookup_texture(old_publication.token, "0")
    old_queued_source = server._lookup_texture(old_publication.token, "1")

    current_publication = server.begin_texture_publication(
        str(tmp_path / "current"))
    current_url = current_publication.register(str(current))
    current_source = server._lookup_texture(current_publication.token, "0")

    class ObservableSemaphore:
        def __init__(self):
            self._semaphore = threading.BoundedSemaphore(1)
            self.waiting = threading.Event()

        def __enter__(self):
            if not self._semaphore.acquire(blocking=False):
                self.waiting.set()
                self._semaphore.acquire()
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            self._semaphore.release()

    semaphore = ObservableSemaphore()
    first_started = threading.Event()
    release_first = threading.Event()
    rendered_paths = []
    rendered_paths_lock = threading.Lock()

    def blocked_render(path, **kwargs):
        with rendered_paths_lock:
            rendered_paths.append(path)
        if path == str(old_first):
            first_started.set()
            if not release_first.wait(5):
                raise RuntimeError("test render gate was not released")
        return b"PNG"

    with patch.object(server, "_texture_encode_semaphore", semaphore), \
            patch("app.runtime.server.render_texture_png", side_effect=blocked_render):
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                first_future = executor.submit(
                    server._render_texture_request,
                    old_publication.token, "0", old_first_source)
                assert first_started.wait(2)
                queued_future = executor.submit(
                    server._render_texture_request,
                    old_publication.token, "1", old_queued_source)
                assert semaphore.waiting.wait(2)

                current_publication.commit()
                release_first.set()

                assert first_future.result(timeout=5) == b"PNG"
                assert queued_future.result(timeout=5) is None
                assert executor.submit(
                    server._render_texture_request,
                    current_publication.token, "0", current_source,
                ).result(timeout=5) == b"PNG"
        finally:
            release_first.set()

    assert old_first_url.endswith("/0.png")
    assert old_queued_url.endswith("/1.png")
    assert current_url.endswith("/0.png")
    assert rendered_paths == [str(old_first), str(current)]

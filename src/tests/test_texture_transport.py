"""Regression tests for lazy application texture transport."""

import functools
import os
import socketserver
import struct
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError
from urllib.request import urlopen
from unittest.mock import patch

import pytest
from PIL import Image

from app import metadata, mod_loader, server
from app.api import ModViewerAPI
from core.ini_document import IniDocument
from core.mesh_builder import GeometryBlob, build_mesh_result
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


def test_render_cache_stores_png_bytes_but_direct_wrapper_stays_data_uri(tmp_path):
    path = tmp_path / "shared.png"
    Image.new("RGB", (2, 1), (128, 128, 32)).save(path)

    png = render_texture_png(str(path))
    uri = encode_texture_data_uri(str(path))

    assert isinstance(png, bytes) and png.startswith(b"\x89PNG")
    assert uri.startswith("data:image/png;base64,")


def test_texture_profile_hook_reports_cache_hit_and_miss(tmp_path):
    path = tmp_path / "profiled.png"
    Image.new("RGB", (2, 1), (128, 128, 32)).save(path)
    events = []
    previous = set_texture_profile_hook(
        lambda stage, seconds, details: events.append((stage, details)))
    try:
        first = render_texture_png(str(path))
        second = render_texture_png(str(path))
    finally:
        set_texture_profile_hook(previous)

    stages = [stage for stage, _details in events]
    assert first == second and first.startswith(b"\x89PNG")
    assert stages.count("cache_miss") == 1
    assert stages.count("cache_hit") == 1
    assert stages.count("encoded") == 1
    assert all(stage in stages for stage in (
        "decode", "rgb_rgba_conversion", "png_encoding"))


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


def test_wuwa_mesh_builder_publishes_only_raw_normal_source(tmp_path):
    _write_geometry(str(tmp_path))
    Image.new("RGBA", (1, 1), (128, 128, 12, 34)).save(tmp_path / "normal.png")
    registered = []

    def register(path, role):
        registered.append((os.path.basename(path), role))
        return f"/texture/test/raw-{len(registered) - 1}"

    with patch("core.textures.render_texture_png",
               side_effect=AssertionError("lazy app path rendered a texture")):
        built = build_mesh_result(
            _group({"normal_map": "normal.png"}), str(tmp_path),
            geometry=GeometryBlob(), texture_source=register,
            game_profile="wuwa")

    assert set(built.textures) == {"normal_data::normal.png"}
    assert registered == [
        ("normal.png", "normal_data"),
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
    context = mod_loader.ModLoadContext(
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


def test_publication_normalizes_removed_channel_transform_to_passthrough(tmp_path):
    path = tmp_path / "packed.png"
    Image.new("RGB", (1, 1), (210, 12, 94)).save(path)
    publication = server.begin_texture_publication(str(tmp_path))

    packed = publication.register(
        str(path), "light_map", transform="passthrough")
    packed_again = publication.register(
        str(path), "light_map", transform="passthrough")
    legacy_transform = publication.register(
        str(path), "light_map", transform="channel_b")

    assert packed == packed_again
    assert legacy_transform == packed
    assert server._lookup_texture(publication.token, "0").transform == "passthrough"


def test_publication_profile_selects_manual_normal_recipe(tmp_path):
    path = tmp_path / "normal.png"
    Image.new("RGB", (1, 1), (128, 128, 0)).save(path)
    publication = server.begin_texture_publication(str(tmp_path))
    publication.set_game_profile("zzz")
    publication.register(str(path), "normal_map")
    assert server._lookup_texture(publication.token, "0").transform == (
        "normal_xy_reconstruct")


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
    api._authorized_folders.add(os.path.normcase(os.path.abspath(tmp_path)))

    result = api.pick_texture_file(str(tmp_path), "normal_map")

    assert result["tex_key"] == "normal_data::normal.png"
    assert result["role"] == "normal_data"
    assert "normal_data_key" not in result
    assert "normal_data_file" not in result
    assert "normal_data_uri" not in result
    assert server._lookup_texture(publication.token, "0").transform == (
        "passthrough")


def test_explicit_manual_validation_rejects_invalid_image(tmp_path):
    invalid = tmp_path / "broken.dds"
    invalid.write_bytes(b"not an image")
    valid = tmp_path / "valid.png"
    Image.new("RGB", (1, 1), (128, 128, 32)).save(valid)
    valid_dds = tmp_path / "valid.dds"
    _write_bc7_dds(valid_dds)
    publication = server.begin_texture_publication(str(tmp_path))

    assert publication.register(str(invalid))
    assert publication.register(str(invalid), validate=True) is None
    valid_url = publication.register(str(valid), validate=True)
    assert valid_url and server._lookup_texture(publication.token, "1").path == str(valid)
    validated_dds_url = publication.register(str(valid_dds), validate=True)
    assert validated_dds_url.endswith(".dds")


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

    def register(path, role, transform=None):
        registered.append((os.path.basename(path), role, transform))
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
    assert {role for _name, role, _transform in registered} == {
        "diffuse", "normal_map", "normal_data", "light_map", "material_map",
    }


def test_texture_endpoint_serves_png_and_keeps_source_reusable(tmp_path):
    path = tmp_path / "shared.png"
    Image.new("RGB", (2, 1), (128, 128, 32)).save(path)
    invalid = tmp_path / "broken.dds"
    invalid.write_bytes(b"not an image")
    publication = server.begin_texture_publication(str(tmp_path))
    texture_url = publication.register(str(path))
    invalid_url = publication.register(str(invalid))
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
            urlopen(f"http://127.0.0.1:{httpd.server_address[1]}{invalid_url}")
        assert error.value.code == 404

        with pytest.raises(HTTPError) as error:
            urlopen(url + "/not-an-id")
        assert error.value.code == 404
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_native_dds_endpoint_streams_original_bytes_and_png_alias_is_valid(tmp_path):
    dds = tmp_path / "native.dds"
    _write_bc7_dds(dds)
    invalid = tmp_path / "unsupported.dds"
    invalid.write_bytes(b"not a DDS")
    publication = server.begin_texture_publication(str(tmp_path))
    native_url = publication.register(str(dds))
    fallback_url = publication.register(str(invalid))
    publication.commit()

    assert native_url.endswith("/0.dds")
    assert fallback_url.endswith("/1.png")
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

        with patch("app.server.render_texture_png", return_value=b"PNG"):
            with urlopen(base_url + native_url[:-4] + ".png") as response:
                assert response.headers["Content-Type"].startswith("image/png")
                assert response.read() == b"PNG"

        with pytest.raises(HTTPError) as error:
            urlopen(base_url + fallback_url[:-4] + ".dds")
        assert error.value.code == 404
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_native_eligibility_is_role_and_transform_aware(tmp_path):
    dds = tmp_path / "shared.dds"
    _write_bc7_dds(dds)
    publication = server.begin_texture_publication(str(tmp_path))

    normal_map_url = publication.register(
        str(dds), "normal_map", transform="normal_xy_reconstruct")
    normal_data_url = publication.register(
        str(dds), "normal_data", transform="passthrough")
    oversized_path = tmp_path / "oversized.dds"
    _write_bc7_dds(oversized_path, width=2049, height=4)
    oversized_url = publication.register(str(oversized_path))

    assert normal_map_url.endswith(".png")
    assert normal_data_url.endswith(".dds")
    assert oversized_url.endswith(".png")
    assert server._lookup_texture(publication.token, "0").native_dds is False
    assert server._lookup_texture(publication.token, "1").native_dds is True
    assert server._lookup_texture(publication.token, "2").native_dds is False


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
        with patch("app.server.render_texture_png", side_effect=blocked_render):
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
            patch("app.server.render_texture_png", side_effect=blocked_render):
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

    with patch("core.textures.render_texture_png",
               side_effect=AssertionError("metadata hydration rendered a texture")):
        metadata.hydrate_textures(
            str(tmp_path), payload, data, texture_source=register)

    assert registered == ["diffuse", "normal_map"]
    assert payload["textures"] == {
        "diffuse::shared.png": "/texture/test/diffuse",
        "normal_map::shared.png": "/texture/test/normal_map",
    }
    assert payload["texture_pools"]["p0"][0]["tex_key"] == (
        "diffuse::shared.png")
    assert "texture_options" not in payload["meshes"]["Body-1"]

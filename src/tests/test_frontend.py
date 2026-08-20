"""Real-server Edge smoke coverage for frontend state transitions."""

import base64
import copy
import io
import json
import struct
import zlib

import pytest
from PIL import Image

from app import paths, server
from core.material_profiles import material_profile_for

playwright = pytest.importorskip("playwright.sync_api")

_PNG_URI = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/"
    "ScLkWQAAAABJRU5ErkJggg==")


def _f32(*values):
    return base64.b64encode(struct.pack(f"<{len(values)}f", *values)).decode()


def _u32(*values):
    return base64.b64encode(struct.pack(f"<{len(values)}I", *values)).decode()


def _flat_png_uri(rgba, size=4):
    """Build a real multi-pixel PNG so WebGPU texture mip sampling is tested."""
    raw = b"".join(b"\x00" + bytes(rgba) * size for _ in range(size))

    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw))
           + chunk(b"IEND", b""))
    return "data:image/png;base64," + base64.b64encode(png).decode()


def _banded_png_uri(rgba_columns, height=4):
    """Build a nearest-column diagnostic texture for per-pixel debug tests."""
    width = len(rgba_columns)
    raw_row = b"".join(bytes(rgba) for rgba in rgba_columns)
    raw = b"".join(b"\x00" + raw_row for _ in range(height))

    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw))
           + chunk(b"IEND", b""))
    return "data:image/png;base64," + base64.b64encode(png).decode()


def _bc7_dds_bytes(width=8, height=4, mip_count=2):
    data = bytearray(148)
    data[:4] = b"DDS "
    struct.pack_into("<I", data, 4, 124)
    struct.pack_into("<II", data, 12, height, width)
    struct.pack_into("<I", data, 28, mip_count)
    struct.pack_into("<I", data, 76, 32)
    struct.pack_into("<II", data, 80, 4, int.from_bytes(b"DX10", "little"))
    struct.pack_into("<IIIII", data, 128, 98, 3, 1, 0, 0)
    w, h = width, height
    for level in range(mip_count):
        data.extend(bytes([level + 1]) * (((w + 3) // 4) * ((h + 3) // 4) * 16))
        w, h = max(1, w // 2), max(1, h // 2)
    return bytes(data)


def _payload(label="A"):
    texture_pool = [
        {"tex_key": f"diffuse::{label}-one.png", "label": f"{label} one"},
        {"tex_key": f"diffuse::{label}-two.png", "label": f"{label} two"},
    ]
    return {
        "meshes": {
            f"Body-{label}-0": {
                "component": f"Body {label}",
                "drawindexed": [3, 0, 0],
                "pos": _f32(0, 0, 0, 1, 0, 0, 0, 1, 0),
                "idx": _u32(0, 1, 2),
                "tex_key": texture_pool[0]["tex_key"],
                "texture_options": texture_pool,
                "texture_variants": [{
                    "conditions": [[{"var": "menu", "value": "1", "negate": False}]],
                    "tex_key": texture_pool[1]["tex_key"],
                }],
                "shape_targets": [{
                    "var": "shape",
                    "pos": _f32(0, 0, 0, 1.2, 0, 0, 0, 1.2, 0),
                }],
                "conditions": [],
                "sources": [{"ini": f"{label}.ini", "line": 10}],
            },
        },
        "textures": {},
        "controls": {
            "toggles": {
                f"Key{label}": {
                    "name": f"Toggle {label}", "ini": f"{label}.ini",
                    "section": f"Key{label}", "wired": True,
                    "vars": [{"var": "toggle", "default": "0", "values": ["0", "1"]}],
                },
            },
            "menu": {
                "menu": {"name": "Menu", "slot": 1, "var": "menu",
                         "default": "0", "values": ["0", "1"], "effects": []},
                "shape": {"name": "Shape", "var": "shape", "kind": "shape_slider",
                          "default": "0", "min": "0", "max": "1", "step": "0.1"},
            },
            "present": {"target_inis": []},
        },
        "state": {"rules": [], "defaults": {"toggle": "0", "menu": "0", "shape": "0"}},
        "geometry": None,
        "metadata": {"mesh_names": {}, "material_profiles": {}},
        "health": {"summary": {"issues": 0, "errors": 0}, "files": {}, "issues": []},
    }


def _packed_material_payload(profile_id="zzz:zzmi"):
    payload = _payload("Packed")
    entry = payload["meshes"]["Body-Packed-0"]
    entry["uv"] = _f32(0, 0, 1, 0, 0, 1)
    entry["light_map_key"] = "light_map::Packed-light.png"
    entry["material_map_key"] = "material_map::Packed-material.png"
    entry["normal_data_key"] = "normal_data::Packed-normal.png"
    payload["textures"] = {
        "diffuse::Packed-one.png": _PNG_URI,
        "light_map::Packed-light.png": _PNG_URI,
        "material_map::Packed-material.png": _PNG_URI,
        "normal_data::Packed-normal.png": _PNG_URI,
    }
    profile_args = {
        "zzz:zzmi": ("zzz", "zzmi"),
        "genshin:gimi": ("genshin", "gimi"),
        "wuwa:rabbitfx": ("wuwa", "rabbitfx"),
        "wuwa:rabbitfx:body": ("wuwa", "rabbitfx", "body"),
        "wuwa:raw": ("wuwa", "raw"),
    }
    profile = (material_profile_for(*profile_args[profile_id]).to_metadata()
               if profile_id in profile_args
               else material_profile_for("unknown", "unknown").to_metadata())
    entry["material_kind"] = "body"
    entry["material_kind_reliable"] = False
    entry["material_profile_id"] = profile["id"]
    payload["metadata"]["material_profiles"] = {profile["id"]: profile}
    return payload


def _construction_failure_payload():
    payload = _payload("Broken")
    payload["textures"] = {
        "diffuse::Broken-one.png":
        "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLkWQAAAABJRU5ErkJggg==",
    }
    # Keep one valid mesh first so the frontend has already allocated scene,
    # material and texture state when the next mesh construction fails.
    payload["meshes"]["Broken-after-first"] = {
        "component": "Broken after",
        "drawindexed": [3, 0, 0],
        "pos": "!",  # invalid base64: decodeF32 must reject this buffer
        "idx": _u32(0, 1, 2),
        "conditions": [],
        "sources": [{"ini": "Broken.ini", "line": 20}],
    }
    return payload


def _state_sync_payload():
    payload = _payload("Sync")
    payload["controls"]["menu"]["sibling"] = {
        "name": "Sibling", "slot": 2, "var": "sibling",
        "default": "0", "values": ["0", "1"], "effects": [],
    }
    payload["controls"]["menu"]["menu"]["effects"] = [
        {"var": "sibling", "value": "1"},
    ]
    payload["controls"]["present"] = {
        "target_inis": [],
        "item": {
            "key": "p", "back": "", "count": 2, "names": ["Base", "Alt"],
            "vars": [
                {"var": "toggle", "values": ["0", "1"]},
                {"var": "menu", "values": ["0", "1"]},
            ],
            "capture_vars": ["toggle", "menu"], "missing_inis": [],
        },
    }
    payload["state"] = {
        "defaults": {"toggle": "0", "menu": "0", "sibling": "0", "shape": "0"},
        "rules": [{
            "conditions": [[{"var": "toggle", "value": "1", "negate": False}]],
            "var": "menu", "value": "1",
        }],
    }
    return payload


def _source_payload():
    payload = _payload("Source")
    template = next(iter(payload["meshes"].values()))
    payload["meshes"] = {}
    for key, source in [("BodyRoot-0", "Root.ini"),
                        ("BodyNested-0", "variants/sub"),
                        ("BodyNested-1", "variants/sub")]:
        entry = copy.deepcopy(template)
        entry["component"] = "Body"
        entry["source"] = source
        payload["meshes"][key] = entry

    first_toggle = next(iter(payload["controls"]["toggles"].values()))
    payload["controls"]["toggles"] = {
        "KeyRoot": {**copy.deepcopy(first_toggle), "name": "Duplicate", "source": "Root.ini"},
        "KeyNested": {**copy.deepcopy(first_toggle), "name": "Duplicate", "source": "variants/sub"},
    }
    menu = payload["controls"]["menu"]
    menu["menu"]["source"] = "Root.ini"
    menu["shape"]["source"] = "variants/sub"
    return payload


@pytest.fixture(scope="session")
def frontend_url():
    if not paths.has_vendored_three():
        pytest.skip("frontend smoke tests require the vendored Three.js assets")
    return server.start()


@pytest.fixture(scope="session")
def edge_browser():
    with playwright.sync_playwright() as runtime:
        try:
            browser = runtime.chromium.launch(channel="msedge", headless=True)
        except playwright.Error as error:
            pytest.skip(f"installed Edge is required for frontend smoke tests: {error}")
        yield browser
        browser.close()


def _page(edge_browser, frontend_url, responses, pending=None, picks=None):
    # Playwright's wait_for_function uses eval internally. Bypass the app's
    # production CSP only in this isolated test context so behavioral waits
    # do not require weakening the served application's policy.
    context = edge_browser.new_context(bypass_csp=True)
    state = {
        "responses": responses,
        "pending": pending or {},
        "picks": picks or [],
    }
    encoded_state = json.dumps(json.dumps(state))
    context.add_init_script(
        """
        {
          const state = window.__fakeApi = JSON.parse(__STATE__);
          const copy = value => value == null ? value : structuredClone(value);
          window.pywebview = { api: {
            select_folder: async () => {
              const path = state.nextPath || null;
              state.nextPath = null;
              return path;
            },
            load_mod: async path => copy(state.responses[path]),
            has_pending_changes: async path => !!state.pending[path],
            discard_changes: async path => { state.pending[path] = false; },
            get_diagnostics: async () => ({summary: {issues: 0, errors: 0}, files: {}, issues: []}),
            list_toggle_source_inis: async () => [{value: 'A.ini', label: 'A.ini'}],
            list_ini_files: async () => [{value: 'A.ini', label: 'A.ini', dirty: false}],
            get_ini_text: async () => ({ini: 'A.ini', text: '[Test]\\nkey = 1\\n', dirty: false}),
            update_ini_text: async () => ({pending: true}),
            save_mesh_textures: async () => ({}),
            save_mesh_names: async () => ({}),
            save_component_material_kind: async () => ({}),
            pick_texture_file: async () => copy(state.picks.shift() || null),
            load_texture_file: async (path, key) => ({tex_key: key, uri: ''}),
            get_record_positions: async () => ({positions: 2, vars: ['toggle']}),
          }};
        }
        """.replace("__STATE__", encoded_state),
    )
    page = context.new_page()
    page.goto(frontend_url)
    page.wait_for_function("window.modViewer !== undefined")
    return context, page


def _open(page, path):
    page.evaluate("path => { window.__fakeApi.nextPath = path; }", path)
    page.locator("#open-btn").click()


def _sample_mesh_pixel(page):
    return _sample_mesh_pixel_at(page, 0.25, 0.25)


def _sample_mesh_pixel_at(page, x, y):
    point = page.evaluate("""
      async ({x, y}) => {
        const THREE = await import('three');
        const {camera, renderer} = await import('./js/scene.js');
        const mesh = window.modViewer.activeMeshes[0];
        const projected = new THREE.Vector3(x, y, 0)
          .applyMatrix4(mesh.matrixWorld).project(camera);
        const rect = renderer.domElement.getBoundingClientRect();
        return {
          x: Math.round(rect.left + (projected.x + 1) * rect.width / 2),
          y: Math.round(rect.top + (1 - projected.y) * rect.height / 2),
        };
      }
    """, {"x": x, "y": y})
    image = Image.open(io.BytesIO(page.screenshot())).convert("RGB")
    return image.getpixel((point["x"], point["y"]))


def test_webgpu_startup_uses_actual_webgpu_backend(edge_browser, frontend_url):
    context = edge_browser.new_context(bypass_csp=True)
    page = context.new_page()
    try:
        page.goto(frontend_url)
        page.locator("#open-btn:not([disabled])").wait_for(timeout=10000)
        page.wait_for_function(
            "import('./js/scene.js').then(({renderer}) => renderer.currentSamples === 4)")
        state = page.evaluate("""async () => {
          const {renderer} = await import('./js/scene.js');
          return {
            isWebGPURenderer: renderer.isWebGPURenderer === true,
            isWebGPUBackend: renderer.backend?.isWebGPUBackend === true,
            compatibilityMode: renderer.backend?.compatibilityMode,
            samples: renderer.samples,
            animationLoop: renderer.getAnimationLoop() !== null,
            outputColorSpace: renderer.outputColorSpace,
            toneMapping: renderer.toneMapping,
            clearAlpha: renderer.getClearAlpha(),
          };
        }""")
        assert state["isWebGPURenderer"]
        assert state["isWebGPUBackend"]
        assert state["compatibilityMode"] is False
        assert state["samples"] == 4
        assert state["animationLoop"]
        assert state["outputColorSpace"] == "srgb"
        assert state["toneMapping"] == 0
        assert state["clearAlpha"] == 1
    finally:
        context.close()


def test_browser_dds_parser_and_loader_preserve_authored_mips(
        edge_browser, frontend_url):
    context = edge_browser.new_context(bypass_csp=True)
    page = context.new_page()
    encoded = base64.b64encode(_bc7_dds_bytes()).decode()
    try:
        page.goto(frontend_url)
        state = page.evaluate("""async encoded => {
          const {parseDDS, loadDDSTexture} = await import('./js/dds-loader.js');
          const THREE = await import('three');
          const bytes = Uint8Array.from(atob(encoded), value => value.charCodeAt(0));
          const parsed = parseDDS(bytes);
          let malformed = false;
          try { parseDDS(bytes.slice(0, -1)); } catch (_error) { malformed = true; }
          const dataUrl = 'data:application/octet-stream;base64,' + encoded;
          const loaded = await new Promise((resolve, reject) => {
            const stable = loadDDSTexture(dataUrl,
              value => resolve({stable, value}), reject);
          });
          return {
            parsed: {
              formatId: parsed.formatId,
              compressed: parsed.compressed,
              mipCount: parsed.mipCount,
              mipLengths: parsed.mipmaps.map(mip => mip.data.byteLength),
            },
            malformed,
            loaded: {
              sameObject: loaded.stable === loaded.value,
              compressed: loaded.stable.isCompressedTexture === true,
              mipCount: loaded.stable.mipmaps.length,
              authoredFilter: loaded.stable.minFilter === THREE.LinearMipmapLinearFilter,
            },
          };
        }""", encoded)
        assert state["parsed"] == {
            "formatId": "bc7_unorm", "compressed": True,
            "mipCount": 2, "mipLengths": [32, 16],
        }
        assert state["malformed"]
        assert state["loaded"] == {
            "sameObject": True, "compressed": True,
            "mipCount": 2, "authoredFilter": True,
        }
    finally:
        context.close()


def test_eligible_dds_uses_stable_compressed_texture_when_bc_is_available(
        edge_browser, frontend_url, tmp_path):
    dds_path = tmp_path / "direct.dds"
    dds_path.write_bytes(_bc7_dds_bytes(width=4, height=4, mip_count=1))
    publication = server.begin_texture_publication(str(tmp_path))
    dds_url = publication.register(str(dds_path))
    publication.commit()
    payload = _payload("DirectDDS")
    payload["textures"] = {"diffuse::DirectDDS-one.png": dds_url}
    context, page = _page(edge_browser, frontend_url, {"DirectDDS": payload})
    try:
        _open(page, "DirectDDS")
        page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.map?.image?.width === 4")
        state = page.evaluate("""async () => {
          const {supportsBCTextureCompression} =
            await import('./js/renderer-capabilities.js');
          const mesh = window.modViewer.activeMeshes[0];
          const material = mesh.material;
          const beforeVersion = material.version;
          return {
            bc: supportsBCTextureCompression(),
            compressed: material.map?.isCompressedTexture === true,
            colorSpace: material.map?.colorSpace,
            stableMaterial: material === mesh.material,
            version: material.version,
            beforeVersion,
          };
        }""")
        assert state["stableMaterial"]
        assert state["version"] == state["beforeVersion"]
        assert state["colorSpace"] == "srgb"
        assert state["compressed"] is state["bc"]
    finally:
        context.close()


def test_webgpu_unsupported_state_is_visible_and_never_falls_back(
        edge_browser, frontend_url):
    context = edge_browser.new_context(bypass_csp=True)
    context.add_init_script("""
      Object.defineProperty(Navigator.prototype, 'gpu', {
        configurable: true, get: () => undefined,
      });
      window.__webglFallbackRequested = false;
      const getContext = HTMLCanvasElement.prototype.getContext;
      HTMLCanvasElement.prototype.getContext = function(type, ...args) {
        if (type === 'webgl' || type === 'webgl2') {
          window.__webglFallbackRequested = true;
        }
        return getContext.call(this, type, ...args);
      };
    """)
    page = context.new_page()
    try:
        page.goto(frontend_url)
        page.locator("#renderer-error.show").wait_for(timeout=5000)
        assert "WebGPU is required" in page.locator("#renderer-error").inner_text()
        assert page.locator("#open-btn").is_disabled()
        assert not page.evaluate("window.__webglFallbackRequested")
    finally:
        context.close()


def test_webgpu_device_loss_stops_loop_and_surfaces_error(edge_browser, frontend_url):
    context = edge_browser.new_context(bypass_csp=True)
    page = context.new_page()
    try:
        page.goto(frontend_url)
        page.locator("#open-btn:not([disabled])").wait_for(timeout=10000)
        loop_stopped = page.evaluate("""async () => {
          const {renderer} = await import('./js/scene.js');
          renderer.onDeviceLost({message: 'simulated device loss'});
          return renderer.getAnimationLoop() === null;
        }""")
        page.locator("#renderer-error.show").wait_for(timeout=5000)
        assert loop_stopped
        assert "device was lost" in page.locator("#renderer-error").inner_text()
        assert page.locator("#open-btn").is_disabled()
    finally:
        context.close()


def test_webgpu_device_loss_keeps_open_disabled_after_open_finishes(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {})
    try:
        page.evaluate("""
          () => {
            window.__resolveFolder = null;
            window.pywebview.api.select_folder = () => new Promise(resolve => {
              window.__resolveFolder = resolve;
            });
          }
        """)
        page.locator("#open-btn").click()
        page.wait_for_function("window.__resolveFolder !== null")
        page.evaluate("""
          import('./js/scene.js').then(({renderer}) =>
            renderer.onDeviceLost({message: 'simulated open-time loss'}))
        """)
        page.locator("#renderer-error.show").wait_for(timeout=5000)
        page.evaluate("window.__resolveFolder(null)")
        page.wait_for_timeout(100)
        assert page.locator("#open-btn").is_disabled()
    finally:
        context.close()


@pytest.mark.parametrize("flat_shading", [False, True])
def test_double_side_fallback_normal_faces_back_light(
        edge_browser, frontend_url, flat_shading):
    payload = _payload("A")
    entry = payload["meshes"]["Body-A-0"]
    entry["uv"] = _f32(0, 0, 1, 0, 0, 1)
    entry["normal_map_key"] = "normal_map::A-neutral.png"
    payload["textures"] = {
        "normal_map::A-neutral.png": _flat_png_uri((128, 128, 255, 255)),
    }
    context, page = _page(edge_browser, frontend_url, {"A": payload})
    try:
        _open(page, "A")
        page.locator(".draw-item").wait_for()
        page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial"
            "?.bindings.normal_map.textureNode.value.image?.width === 4")
        page.evaluate("""
          async flatShading => {
            const THREE = await import('three');
            const {scene, camera, controls} = await import('./js/scene.js');
            let key = null;
            scene.traverse(object => {
              if (object.isAmbientLight || object.isHemisphereLight
                  || object.isGridHelper || object.isSprite) {
                if (object.isAmbientLight || object.isHemisphereLight) {
                  object.intensity = 0;
                } else {
                  object.visible = false;
                }
              }
              if (object.isDirectionalLight) {
                if (!key) key = object;
                else object.intensity = 0;
              }
            });
            const target = new THREE.Vector3(0.25, 0.25, 0);
            const mesh = window.modViewer.activeMeshes[0];
            mesh.material.flatShading = flatShading;
            mesh.material.needsUpdate = true;
            camera.position.set(0.25, 0.25, -3);
            controls.target.copy(target);
            controls.update();
            key.target.position.copy(target);
            key.position.set(0.25, 0.25, -3);
            key.intensity = 1;
          }
        """, flat_shading)
        page.evaluate("""
          async () => {
            const {setMeshTextureState} = await import('./js/mesh-factory.js');
            const mesh = window.modViewer.activeMeshes[0];
            setMeshTextureState(mesh, {
              diffuse: mesh.userData.texKey,
              normal_map: null,
              normal_data: null,
              light_map: null,
              material_map: null,
            });
          }
        """)
        page.wait_for_timeout(700)
        pixel = _sample_mesh_pixel(page)
        assert min(pixel) > 60, (flat_shading, pixel)
    finally:
        context.close()


@pytest.mark.parametrize("failed_payload", [
    {"error": "loader failed", "health": {"summary": {"issues": 1, "errors": 1}}},
    {"meshes": {}, "textures": {}, "controls": {}, "state": {},
     "geometry": {"url": "/geometry/missing", "length": 12}},
], ids=["loader-error", "geometry-error"])
def test_failed_mod_switch_clears_previous_ui_and_pending_state(
        edge_browser, frontend_url, failed_payload):
    context, page = _page(
        edge_browser, frontend_url,
        {"A": _payload("A"), "B": failed_payload},
        pending={"A": True, "B": False},
    )
    try:
        _open(page, "A")
        page.locator(".draw-item").wait_for()
        page.locator("#pending-indicator.show").wait_for()
        # Keep the visible indicator stale while allowing the switch itself
        # to proceed without the discard-confirmation dialog.
        page.evaluate("window.__fakeApi.pending.A = false")

        _open(page, "B")
        page.locator("#dialog-backdrop.show").wait_for()
        assert page.locator(".draw-item").count() == 0
        assert page.locator("#toggle-list .toggle-item").count() == 0
        assert page.locator("#menu-list .menu-item").count() == 0
        assert page.locator("#present-list .toggle-item").count() == 0
        assert not page.locator("#sidebar").is_visible()
        assert not page.locator("#camera-panel").is_visible()
        assert page.locator("#pending-indicator.show").count() == 0
        assert page.locator("#export-btn").is_disabled()
        assert page.locator("#mod-path").inner_text() == "B"
        assert not page.locator("#ini-view-btn").is_disabled()
        page.locator("#dialog-ok").click()
    finally:
        context.close()


def test_frontend_construction_failure_rolls_back_partial_scene(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url,
        {"A": _payload("A"), "B": _construction_failure_payload()},
        pending={"A": True, "B": False},
    )
    try:
        _open(page, "A")
        page.locator(".draw-item").wait_for()
        page.evaluate("window.__fakeApi.pending.A = false")

        _open(page, "B")
        page.locator("#dialog-backdrop.show").wait_for()
        assert page.locator(".draw-item").count() == 0
        assert page.locator("#mesh-list").inner_text() == ""
        assert not page.locator("#sidebar").is_visible()
        assert not page.locator("#camera-panel").is_visible()
        assert not page.locator("#toggle-panel").is_visible()
        assert not page.locator("#menu-panel").is_visible()
        assert not page.locator("#present-panel").is_visible()
        assert page.locator("#mod-path").inner_text() == "B"
        assert not page.locator("#ini-view-btn").is_disabled()
        page.locator("#dialog-ok").click()
    finally:
        context.close()


@pytest.mark.parametrize("profile_id", ["zzz:zzmi", "genshin:gimi"])
def test_packed_material_profile_uses_tsl_nodes_and_stable_bindings(
        edge_browser, frontend_url, profile_id):
    context, page = _page(
        edge_browser, frontend_url,
        {"Packed": _packed_material_payload(profile_id)},
    )
    runtime_errors = []
    page.on("pageerror", lambda error: runtime_errors.append(str(error)))
    page.on("console", lambda message: runtime_errors.append(message.text)
            if message.type == "error" and "Failed to load resource" not in message.text
            else None)
    try:
        _open(page, "Packed")
        page.locator(".draw-item").wait_for()
        page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial")
        packed_role = "material_map" if profile_id == "zzz:zzmi" else "light_map"
        page.wait_for_function(
            f"window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial"
            f"?.bindings?.{packed_role}?.enabledNode?.value")

        state = page.evaluate("""() => {
          const mesh = window.modViewer.activeMeshes[0];
          const material = mesh.material;
          const game = material.userData.gameMaterial;
          const packedRole = game.profile.id.startsWith('zzz') ? 'material_map' : 'light_map';
          const lightingModel = material.setupLightingModel();
          return {
            physical: material.isMeshPhysicalNodeMaterial,
            profile: game.profile.id,
            hasGeneratedShaderState: Object.hasOwn(game, 'shader'),
            hasLegacyUniformState: Object.hasOwn(game, 'uniforms'),
            lightMap: game.bindings.light_map.enabledNode.value,
            materialMap: game.bindings.material_map.enabledNode.value,
            sampledRole: game.bindings[packedRole].textureNode.value
              !== game.bindings[packedRole].placeholder,
            hasMetalnessNode: !!material.metalnessNode,
            hasSpecularResponseNode: !!game.specularResponseNode,
            lightingModel: lightingModel.constructor.name,
            metalnessScale: game.profile.metalness_scale,
            specularScale: game.profile.specular_scale,
            specularInfluence: game.profile.specular_influence,
            shadowThreshold: game.shadowThresholdNode.value,
            shadowSoftness: game.shadowSoftnessNode.value,
            shadowMaskStrength: game.shadowMaskStrengthNode.value,
            shadowInfluence: game.shadowInfluenceNode.value,
            materialKind: mesh.userData.materialKind,
            materialKindReliable: mesh.userData.materialKindReliable,
            materialProfileId: mesh.userData.materialProfileId,
            version: material.version,
          };
        }""")
        assert state["physical"]
        assert state["profile"] == profile_id
        assert not state["hasGeneratedShaderState"]
        assert not state["hasLegacyUniformState"]
        assert state["metalnessScale"] == (1 if profile_id == "zzz:zzmi" else 0.08)
        assert state["specularScale"] == 1
        assert state["specularInfluence"] == (None if profile_id == "zzz:zzmi" else 0.15)
        assert (state["shadowThreshold"], state["shadowSoftness"],
                state["shadowMaskStrength"], state["shadowInfluence"]) == (
                    0.5, 0.08, 0.5, 1)
        assert (state["materialKind"], state["materialKindReliable"],
                state["materialProfileId"]) == ("body", False, profile_id)
        assert state["lightingModel"] == (
            "GenshinLightingModel" if profile_id == "genshin:gimi"
            else "PhysicalLightingModel")
        assert state["hasSpecularResponseNode"]
        assert state["sampledRole"]
        assert not runtime_errors, "\n".join(runtime_errors)
        assert state["materialMap"] if profile_id == "zzz:zzmi" else state["lightMap"]

        after = page.evaluate("""async () => {
          const {setMeshTextureState} = await import('./js/mesh-factory.js');
          const mesh = window.modViewer.activeMeshes[0];
          const material = mesh.material;
          const game = material.userData.gameMaterial;
          const packedRole = game.profile.id.startsWith('zzz') ? 'material_map' : 'light_map';
          const packedNode = game.bindings[packedRole].textureNode;
          const packedEnabled = game.bindings[packedRole].enabledNode;
          const version = material.version;
          setMeshTextureState(mesh, {
            diffuse: mesh.userData.texKey,
            normal_map: mesh.userData.normalMapKey,
            normal_data: null,
            light_map: null,
            material_map: null,
          });
          return {
            sameTextureNode: game.bindings[packedRole].textureNode === packedNode,
            sameEnabledNode: game.bindings[packedRole].enabledNode === packedEnabled,
            sameVersion: material.version === version,
            mapEnabled: packedEnabled.value,
            usesPlaceholder: game.bindings[packedRole].textureNode.value
              === game.bindings[packedRole].placeholder,
          };
        }""")
        assert after == {"sameTextureNode": True, "sameEnabledNode": True,
                         "sameVersion": True, "mapEnabled": False,
                         "usesPlaceholder": True}
    finally:
        context.close()


def test_each_mesh_resolves_its_own_profile_and_packed_source(
        edge_browser, frontend_url):
    payload = _packed_material_payload("zzz:zzmi")
    body_name, body = next(iter(payload["meshes"].items()))
    face_name = "Face-Packed-0"
    face = copy.deepcopy(body)
    face["component"] = "Face Packed"
    face["material_kind"] = "face"
    face["material_kind_reliable"] = False
    face["material_profile_id"] = "genshin:gimi"
    payload["meshes"] = {body_name: body, face_name: face}
    payload["metadata"]["material_profiles"]["genshin:gimi"] = \
        material_profile_for("genshin", "gimi").to_metadata()

    context, page = _page(edge_browser, frontend_url, {"Packed": payload})
    try:
        _open(page, "Packed")
        page.wait_for_function(
            "window.modViewer.activeMeshes.length === 2 && "
            "window.modViewer.activeMeshes.every(mesh => "
            "mesh.material.userData.gameMaterial)")
        states = page.evaluate("""() => window.modViewer.activeMeshes.map(mesh => ({
          profileId: mesh.userData.materialProfileId,
          kind: mesh.userData.materialKind,
          lightMap: mesh.material.userData.gameMaterial.bindings.light_map.enabledNode.value,
          materialMap: mesh.material.userData.gameMaterial.bindings.material_map.enabledNode.value,
        }))""")
        assert states == [
            {"profileId": "zzz:zzmi", "kind": "body",
             "lightMap": False, "materialMap": True},
            {"profileId": "genshin:gimi", "kind": "face",
             "lightMap": True, "materialMap": False},
        ]
        assert page.evaluate("window.modViewer.getMaterialState(1).profileId") == (
            "genshin:gimi")
    finally:
        context.close()


def test_missing_material_profile_uses_conservative_material_without_packed_maps(
        edge_browser, frontend_url):
    payload = _packed_material_payload("genshin:gimi")
    entry = payload["meshes"]["Body-Packed-0"]
    entry["material_profile_id"] = "missing:profile"

    context, page = _page(edge_browser, frontend_url, {"Packed": payload})
    try:
        _open(page, "Packed")
        page.wait_for_function("window.modViewer.activeMeshes.length === 1")
        state = page.evaluate("""() => {
          const mesh = window.modViewer.activeMeshes[0];
          const material = mesh.material;
          return {
            profileId: mesh.userData.materialProfileId,
            profile: window.modViewer.getMaterialState(0).profile,
            physical: !!material.isMeshPhysicalNodeMaterial,
            gameMaterial: !!material.userData.gameMaterial,
            packedResponse: material.userData.gameMaterial?.packedResponse ?? false,
            lightMap: material.userData.gameMaterial?.bindings?.light_map
              ?.enabledNode?.value ?? false,
            map: !!material.map,
            normalMap: !!material.normalMap,
            lightMapKey: mesh.userData.lightMapKey,
          };
        }""")
        assert state == {
            "profileId": "missing:profile", "profile": None,
            "physical": False, "gameMaterial": True,
            "packedResponse": False, "lightMap": False,
            "map": True, "normalMap": False,
            "lightMapKey": "light_map::Packed-light.png",
        }
    finally:
        context.close()


def test_genshin_no_uv_keeps_a_and_b_out_of_conservative_material_graph(
        edge_browser, frontend_url):
    payload = _packed_material_payload("genshin:gimi")
    payload["meshes"]["Body-Packed-0"].pop("uv")
    uv_messages = []
    context, page = _page(edge_browser, frontend_url, {"Packed": payload})
    page.on("console", lambda message: uv_messages.append(message.text)
            if "uv" in message.text.lower() else None)
    try:
        _open(page, "Packed")
        page.wait_for_function("window.modViewer.activeMeshes.length === 1")
        state = page.evaluate("""() => {
          const mesh = window.modViewer.activeMeshes[0];
          const material = mesh.material;
          const game = material.userData.gameMaterial;
          window.__rawMaterial = material;
          window.__rawColorNode = material.colorNode;
          return {
            standard: !!material.isMeshStandardNodeMaterial,
            physical: !!material.isMeshPhysicalNodeMaterial,
            packedResponse: game.packedResponse,
            hasMaterialId: game.hasMaterialId,
            hasSpecularArea: game.hasSpecularArea,
            lightMap: game.bindings.light_map.enabledNode.value,
          };
        }""")
        assert state == {
            "standard": True, "physical": False,
            "packedResponse": False,
            "hasMaterialId": False, "hasSpecularArea": False,
            "lightMap": False,
        }
        assert not uv_messages, uv_messages
    finally:
        context.close()


def test_genshin_material_id_decoder_and_uniform_debug_modes(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url,
        {"Packed": _packed_material_payload("genshin:gimi")},
    )
    try:
        values = page.evaluate("""async () => {
          const {decodeMaterialIdValue} = await import('./js/material-profile.js');
          return [0.10, 0.90, 0.50, 0.30, 0.70, 0.20, 0.40, 0.60, 0.80]
            .map(raw => decodeMaterialIdValue(raw, 'genshin_5_region'));
        }""")
        assert values == [1, 2, 3, 4, 5, 4, 4, 5, 5]

        _open(page, "Packed")
        page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial")
        state = page.evaluate("""() => {
          const mesh = window.modViewer.activeMeshes[0];
          const material = mesh.material;
          const game = material.userData.gameMaterial;
          const colorNode = material.colorNode;
          const version = material.version;
          const beforeMaterial = material;
          return {
            before: window.modViewer.getMaterialState(0),
            results: [
              window.modViewer.setMaterialDebugMode('material-id'),
              window.modViewer.getMaterialState(0).debugMode,
              game.debugModeNode.value,
              window.modViewer.setMaterialDebugMode('specular-area'),
              window.modViewer.getMaterialState(0).debugMode,
              game.debugModeNode.value,
              window.modViewer.setMaterialDebugMode('off'),
              window.modViewer.getMaterialState(0).debugMode,
              game.debugModeNode.value,
            ],
            sameMaterial: material === beforeMaterial,
            sameColorNode: material.colorNode === colorNode,
            sameVersion: material.version === version,
          };
        }""")
        assert state["before"]["reason"] == ""
        assert state["before"]["materialIdDecoder"] == "genshin_5_region"
        assert state["before"]["hasMaterialId"] is True
        assert state["before"]["hasSpecularArea"] is True
        assert state["results"] == [
            "material-id", "material-id", 1,
            "specular-area", "specular-area", 2,
            "off", "off", 0,
        ]
        assert state["sameMaterial"]
        assert state["sameColorNode"]
        assert state["sameVersion"]
    finally:
        context.close()


def test_genshin_debug_modes_render_flat_material_id_and_raw_area_values(
        edge_browser, frontend_url):
    payload = _packed_material_payload("genshin:gimi")
    entry = payload["meshes"]["Body-Packed-0"]
    light_key = "light_map::Packed-debug-bands.png"
    entry["light_map_key"] = light_key
    payload["textures"] = {
        key: _flat_png_uri((255, 255, 255, 255))
        for key in payload["textures"]
    }
    payload["textures"][light_key] = _banded_png_uri([
        (0, 255, 0, 26), (0, 255, 64, 77), (0, 255, 128, 128),
        (0, 255, 192, 179), (0, 255, 255, 230),
    ])

    context, page = _page(edge_browser, frontend_url, {"Packed": payload})
    try:
        _open(page, "Packed")
        page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial"
            "?.bindings.light_map.textureNode.value.image?.width === 5")
        points = [0.1, 0.3, 0.5, 0.7, 0.9]
        page.evaluate("window.modViewer.setMaterialDebugMode('material-id')")
        page.wait_for_timeout(300)
        id_pixels = [_sample_mesh_pixel_at(page, x, 0.05) for x in points]
        assert len({pixel for pixel in id_pixels}) == 5, id_pixels

        page.evaluate("window.modViewer.setMaterialDebugMode('specular-area')")
        page.wait_for_timeout(300)
        area_pixels = [_sample_mesh_pixel_at(page, x, 0.05) for x in points]
        assert all(max(pixel) - min(pixel) <= 3 for pixel in area_pixels), area_pixels
        assert [sum(pixel) for pixel in area_pixels] == sorted(
            sum(pixel) for pixel in area_pixels), area_pixels
    finally:
        context.close()


def test_wuwa_debug_modes_are_capability_gated_and_uniform_only(
        edge_browser, frontend_url):
    payload = _packed_material_payload("wuwa:rabbitfx")
    entry = payload["meshes"]["Body-Packed-0"]
    normal_low = "normal_data::Packed-debug-low.png"
    normal_high = "normal_data::Packed-debug-high.png"
    entry["normal_data_key"] = normal_low
    payload["textures"][normal_low] = _flat_png_uri((0, 0, 0, 255))
    payload["textures"][normal_high] = _flat_png_uri((0, 0, 255, 255))
    context, page = _page(
        edge_browser, frontend_url,
        {"Packed": payload},
    )
    try:
        _open(page, "Packed")
        page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial")
        state = page.evaluate("""() => {
          const mesh = window.modViewer.activeMeshes[0];
          const material = mesh.material;
          const game = material.userData.gameMaterial;
          const colorNode = material.colorNode;
          const version = material.version;
          const before = window.modViewer.getMaterialState(0);
          const modes = [
            window.modViewer.setMaterialDebugMode('material-id'),
            window.modViewer.getMaterialState(0).debugMode,
            window.modViewer.setMaterialDebugMode('normal-data-b'),
            window.modViewer.getMaterialState(0).debugMode,
            window.modViewer.setMaterialDebugMode('normal-data-a'),
            window.modViewer.getMaterialState(0).debugMode,
          ];
          return {
            before,
            modes,
            sameMaterial: material === mesh.material,
            sameColorNode: material.colorNode === colorNode,
            sameVersion: material.version === version,
            hasDebugNodes: !!game.normalDataBNode && !!game.normalDataANode,
          };
        }""")
        assert state["before"]["supportedDebugModes"] == [
            "shadow-mask", "normal-data-b", "normal-data-a"]
        assert state["before"]["hasShadowMask"] is True
        assert state["before"]["hasNormalData"] is True
        assert state["before"]["hasNormalDataB"] is True
        assert state["before"]["hasNormalDataA"] is True
        assert state["modes"] == [
            "material-id", "material-id", "normal-data-b", "normal-data-b",
            "normal-data-a", "normal-data-a",
        ]
        assert state["sameMaterial"]
        assert state["sameColorNode"]
        assert state["sameVersion"]
        assert state["hasDebugNodes"]

        page.evaluate("window.modViewer.setMaterialDebugMode('normal-data-b')")
        page.wait_for_timeout(300)
        low_pixel = _sample_mesh_pixel(page)
        page.evaluate("""async key => {
          const {setMeshTextureState} = await import('./js/mesh-factory.js');
          const mesh = window.modViewer.activeMeshes[0];
          setMeshTextureState(mesh, {
            diffuse: mesh.userData.texKey,
            normal_map: null,
            normal_data: key,
            light_map: null,
            material_map: null,
          });
        }""", normal_high)
        page.wait_for_timeout(400)
        high_pixel = _sample_mesh_pixel(page)
        assert sum(high_pixel) > sum(low_pixel), (low_pixel, high_pixel)
    finally:
        context.close()


def test_wuwa_body_profile_binds_normal_data_without_stock_pbr_mapping(
        edge_browser, frontend_url):
    payload = _packed_material_payload("wuwa:rabbitfx:body")
    entry = payload["meshes"]["Body-Packed-0"]
    entry["material_kind"] = "body"
    entry["material_kind_reliable"] = True
    entry["material_kind_reason"] = "viewer material-kind override"
    entry["material_kind_override"] = "body"
    context, page = _page(edge_browser, frontend_url, {"Packed": payload})
    try:
        _open(page, "Packed")
        page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial")
        page.wait_for_timeout(300)
        assert page.locator(".component-material-kind-control").count() == 1
        assert page.locator(".draw-item .material-kind-select").count() == 0
        state = page.evaluate("""() => {
          const mesh = window.modViewer.activeMeshes[0];
          const game = mesh.material.userData.gameMaterial;
          return {
            physical: !!mesh.material.isMeshPhysicalNodeMaterial,
            profileId: game.profile.id,
            directShadowModel: game.profile.direct_shadow_model,
            directSpecularModel: game.profile.direct_specular_model,
            materialKindOverride: window.modViewer.getMaterialState(0)
              .materialKindOverride,
            normalData: game.bindings.normal_data.enabledNode.value,
            lightMap: game.bindings.light_map.enabledNode.value,
            materialMap: game.bindings.material_map.enabledNode.value,
            stockMetalness: game.profile.metalness,
            stockSpecular: game.profile.specular,
            model: mesh.material.setupLightingModel().constructor.name,
          };
        }""")
        assert state == {
            "physical": True,
            "profileId": "wuwa:rabbitfx:body",
            "directShadowModel": "wuwa_base",
            "directSpecularModel": "wuwa_body",
            "materialKindOverride": "body",
            "normalData": True,
            "lightMap": True,
            "materialMap": False,
            "stockMetalness": None,
            "stockSpecular": None,
            "model": "WuwaBodyLightingModel",
        }
        _sample_mesh_pixel(page)
        before = page.evaluate("""() => {
          window.__bodyMaterial = window.modViewer.activeMeshes[0].material;
          return window.__bodyMaterial.version;
        }""")
        page.evaluate("""async () => {
          const {setMeshTextureState} = await import('./js/mesh-factory.js');
          const mesh = window.modViewer.activeMeshes[0];
          setMeshTextureState(mesh, {
            diffuse: mesh.userData.texKey,
            normal_map: null,
            normal_data: null,
            light_map: mesh.userData.lightMapKey,
            material_map: null,
          });
        }""")
        page.wait_for_timeout(250)
        missing = page.evaluate("""() => {
          const mesh = window.modViewer.activeMeshes[0];
          return {
            bound: mesh.material.userData.gameMaterial
              .bindings.normal_data.enabledNode.value,
            sameMaterial: mesh.material === window.__bodyMaterial,
            version: mesh.material.version,
          };
        }""")
        assert missing["bound"] is False
        assert missing["sameMaterial"]
        assert missing["version"] == before
    finally:
        context.close()


def test_wuwa_body_b_threshold_controls_toon_classification(
        edge_browser, frontend_url):
    payload = _packed_material_payload("wuwa:rabbitfx:body")
    entry = payload["meshes"]["Body-Packed-0"]
    low_key = "normal_data::Packed-body-b-low.png"
    high_key = "normal_data::Packed-body-b-high.png"
    payload["textures"] = {
        key: _flat_png_uri((255, 255, 255, 255))
        for key in payload["textures"]
    }
    payload["textures"]["diffuse::Packed-one.png"] = _flat_png_uri(
        (72, 72, 72, 255))
    payload["textures"][low_key] = _flat_png_uri((128, 128, 124, 0))
    payload["textures"][high_key] = _flat_png_uri((128, 128, 130, 0))
    entry["normal_data_key"] = low_key
    context, page = _page(edge_browser, frontend_url, {"Packed": payload})
    try:
        _open(page, "Packed")
        page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial"
            "?.bindings.normal_data.enabledNode.value === true")
        page.evaluate("window.modViewer.setEnvironmentPreset('studio')")
        page.evaluate("""
          async () => {
            const THREE = await import('three');
            const {scene, controls} = await import('./js/scene.js');
            scene.traverse(object => {
              if (object.isAmbientLight || object.isHemisphereLight) object.intensity = 0;
              if (object.isSprite || object.isGridHelper) object.visible = false;
            });
            const key = scene.children.find(object => object.isDirectionalLight);
            key.target.position.copy(controls.target);
            key.position.copy(controls.target).add(new THREE.Vector3(0, 0, 3));
            key.intensity = 3;
          }
        """)
        page.wait_for_timeout(400)
        low_pixels = [_sample_mesh_pixel_at(page, x, 0.1)
                      for x in (0.1, 0.3, 0.5, 0.7, 0.9)]
        page.evaluate("""async key => {
          const {setMeshTextureState} = await import('./js/mesh-factory.js');
          const mesh = window.modViewer.activeMeshes[0];
          setMeshTextureState(mesh, {
            diffuse: mesh.userData.texKey,
            normal_map: null,
            normal_data: key,
            light_map: mesh.userData.lightMapKey,
            material_map: null,
          });
        }""", high_key)
        page.wait_for_timeout(400)
        high_pixels = [_sample_mesh_pixel_at(page, x, 0.1)
                       for x in (0.1, 0.3, 0.5, 0.7, 0.9)]
        assert any(sum(high) != sum(low)
                   for low, high in zip(low_pixels, high_pixels)), (
                       low_pixels, high_pixels)
    finally:
        context.close()


def test_wuwa_body_a_route_matches_near_binary_reference_boundary(
        edge_browser, frontend_url):
    payload = _packed_material_payload("wuwa:rabbitfx:body")
    entry = payload["meshes"]["Body-Packed-0"]
    boundary_low_key = "normal_data::Packed-body-a-160.png"
    boundary_high_key = "normal_data::Packed-body-a-161.png"
    payload["textures"] = {
        key: _flat_png_uri((255, 255, 255, 255))
        for key in payload["textures"]
    }
    payload["textures"]["diffuse::Packed-one.png"] = _flat_png_uri(
        (72, 72, 72, 255))
    # B is above the first-lobe classification threshold in every sample.
    for key, a in (
        (boundary_low_key, 160), (boundary_high_key, 161),
    ):
        payload["textures"][key] = _flat_png_uri((128, 128, 200, a))
    entry["normal_data_key"] = boundary_low_key
    context, page = _page(edge_browser, frontend_url, {"Packed": payload})
    try:
        _open(page, "Packed")
        page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial"
            "?.bindings.normal_data.enabledNode.value === true")
        page.evaluate("window.modViewer.setEnvironmentPreset('studio')")
        page.evaluate("""
          async () => {
            const THREE = await import('three');
            const {scene, controls} = await import('./js/scene.js');
            scene.traverse(object => {
              if (object.isAmbientLight || object.isHemisphereLight) object.intensity = 0;
              if (object.isSprite || object.isGridHelper) object.visible = false;
            });
            const key = scene.children.find(object => object.isDirectionalLight);
            key.target.position.copy(controls.target);
            key.position.copy(controls.target).add(new THREE.Vector3(0, 0, 3));
            key.intensity = 3;
          }
        """)

        def set_normal_data(key):
            page.evaluate("""async key => {
              const {setMeshTextureState} = await import('./js/mesh-factory.js');
              const mesh = window.modViewer.activeMeshes[0];
              setMeshTextureState(mesh, {
                diffuse: mesh.userData.texKey,
                normal_map: null,
                normal_data: key,
                light_map: mesh.userData.lightMapKey,
                material_map: null,
              });
            }""", key)
            page.wait_for_timeout(300)
            return [_sample_mesh_pixel_at(page, x, 0.1)
                    for x in (0.1, 0.3, 0.5, 0.7, 0.9)]

        boundary_low_pixel = set_normal_data(boundary_low_key)
        boundary_high_pixel = set_normal_data(boundary_high_key)

        route_values = page.evaluate("""async () => {
          const {wuwaMetalRouteValue} = await import('./js/material-profile.js');
          return [0.10, 160 / 255, 161 / 255, 0.80]
            .map(wuwaMetalRouteValue);
        }""")
        assert route_values[0] == 0
        assert route_values[1] == 0
        assert route_values[2] == 1
        assert route_values[3] == 1
        assert any(sum(low) != sum(high)
                   for low, high in zip(boundary_low_pixel, boundary_high_pixel)), (
            boundary_low_pixel, boundary_high_pixel)
    finally:
        context.close()


def test_wuwa_raw_normal_data_debug_is_lazy_and_keeps_standard_material(
        edge_browser, frontend_url):
    payload = _packed_material_payload("wuwa:raw")
    payload["textures"]["normal_data::Packed-normal.png"] = _flat_png_uri(
        (0, 0, 128, 255))
    context, page = _page(edge_browser, frontend_url, {"Packed": payload})
    try:
        _open(page, "Packed")
        page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial")
        initial = page.evaluate("""() => {
          const mesh = window.modViewer.activeMeshes[0];
          const material = mesh.material;
          const game = material.userData.gameMaterial;
          window.__rawMaterial = material;
          window.__rawColorNode = material.colorNode;
          return {
            standard: !!material.isMeshStandardNodeMaterial,
            physical: !!material.isMeshPhysicalNodeMaterial,
            normalData: game.bindings.normal_data.enabledNode.value,
            lightMap: game.bindings.light_map.enabledNode.value,
            supported: window.modViewer.getMaterialState(0).supportedDebugModes,
            version: material.version,
          };
        }""")
        assert initial["standard"] is True
        assert initial["physical"] is False
        assert initial["normalData"] is False
        assert initial["lightMap"] is False
        assert initial["supported"] == ["normal-data-b", "normal-data-a"]

        page.evaluate("window.modViewer.setMaterialDebugMode('normal-data-b')")
        page.wait_for_function(
            "window.modViewer.activeMeshes[0].material.userData.gameMaterial"
            ".bindings.normal_data.enabledNode.value === true")
        after_b = page.evaluate("""() => {
          const mesh = window.modViewer.activeMeshes[0];
          const game = mesh.material.userData.gameMaterial;
          return {
            normalData: game.bindings.normal_data.enabledNode.value,
            version: mesh.material.version,
            sameMaterial: mesh.material === window.__rawMaterial,
            sameColorNode: mesh.material.colorNode === window.__rawColorNode,
            imageWidth: game.bindings.normal_data.textureNode.value.image?.width || 0,
          };
        }""")
        assert after_b["normalData"] is True
        assert after_b["imageWidth"] == 4
        assert after_b["version"] == initial["version"]
        assert after_b["sameMaterial"]
        assert after_b["sameColorNode"]

        page.evaluate("""window.__rawNormalDataTexture =
          window.modViewer.activeMeshes[0].material.userData.gameMaterial
            .bindings.normal_data.textureNode.value;
          window.modViewer.setMaterialDebugMode('normal-data-a')""")
        after_a = page.evaluate("""() => {
          const mesh = window.modViewer.activeMeshes[0];
          const game = mesh.material.userData.gameMaterial;
          return {
            sameTexture: game.bindings.normal_data.textureNode.value
              === window.__rawNormalDataTexture,
            version: mesh.material.version,
          };
        }""")
        assert after_a["sameTexture"]
        assert after_a["version"] == initial["version"]
    finally:
        context.close()


def test_wuwa_missing_lightmap_disables_shadow_mask_without_rebuilding(
        edge_browser, frontend_url):
    payload = _packed_material_payload("wuwa:rabbitfx")
    entry = payload["meshes"]["Body-Packed-0"]
    low_key = "light_map::Packed-shadow-low.png"
    payload["textures"] = {
        key: _flat_png_uri((255, 255, 255, 255))
        for key in payload["textures"]
    }
    payload["textures"]["diffuse::Packed-one.png"] = _flat_png_uri(
        (24, 24, 24, 255))
    payload["textures"][low_key] = _flat_png_uri((0, 0, 0, 255))
    entry["light_map_key"] = low_key

    context, page = _page(edge_browser, frontend_url, {"Packed": payload})
    try:
        _open(page, "Packed")
        page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial"
            "?.bindings.light_map.enabledNode.value === true")
        page.evaluate("""
          async () => {
            const THREE = await import('three');
            const {scene, controls} = await import('./js/scene.js');
            scene.traverse(object => {
              if (object.isAmbientLight || object.isHemisphereLight) {
                object.intensity = 0;
              } else if (object.isSprite || object.isGridHelper) {
                object.visible = false;
              }
            });
            const key = scene.children.find(object => object.isDirectionalLight);
            key.target.position.copy(controls.target);
            key.position.copy(controls.target).add(new THREE.Vector3(0.8, 0, 0.35));
            key.intensity = 1;
          }
        """)
        page.wait_for_timeout(400)
        shadowed_pixel = _sample_mesh_pixel(page)
        before_version = page.evaluate("""() => {
          const mesh = window.modViewer.activeMeshes[0];
          window.__missingLightmapMaterial = mesh.material;
          return mesh.material.version;
        }""")
        page.evaluate("""async () => {
          const {setMeshTextureState} = await import('./js/mesh-factory.js');
          const mesh = window.modViewer.activeMeshes[0];
          setMeshTextureState(mesh, {
            diffuse: mesh.userData.texKey,
            normal_map: null,
            normal_data: null,
            light_map: null,
            material_map: null,
          });
        }""")
        page.wait_for_timeout(400)
        state = page.evaluate("""() => {
          const mesh = window.modViewer.activeMeshes[0];
          return {
            bound: mesh.material.userData.gameMaterial
              .bindings.light_map.enabledNode.value,
            sameMaterial: mesh.material === window.__missingLightmapMaterial,
            version: mesh.material.version,
          };
        }""")
        missing_pixel = _sample_mesh_pixel(page)
        assert state["bound"] is False
        assert state["version"] == before_version
        assert state["sameMaterial"]
        assert sum(missing_pixel) > sum(shadowed_pixel), (
            shadowed_pixel, missing_pixel)
    finally:
        context.close()


def test_wuwa_normal_data_ba_do_not_change_normal_rendering_when_debug_is_off(
        edge_browser, frontend_url):
    payload = _packed_material_payload("wuwa:rabbitfx")
    entry = payload["meshes"]["Body-Packed-0"]
    low_key = "normal_data::Packed-neutral-low.png"
    high_key = "normal_data::Packed-neutral-high.png"
    payload["textures"] = {
        key: _flat_png_uri((255, 255, 255, 255))
        for key in payload["textures"]
    }
    payload["textures"]["diffuse::Packed-one.png"] = _flat_png_uri(
        (80, 80, 80, 255))
    payload["textures"][low_key] = _flat_png_uri((128, 128, 0, 0))
    payload["textures"][high_key] = _flat_png_uri((128, 128, 255, 255))
    entry["normal_data_key"] = low_key

    context, page = _page(edge_browser, frontend_url, {"Packed": payload})
    try:
        _open(page, "Packed")
        page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial")
        page.wait_for_timeout(400)
        low_pixel = _sample_mesh_pixel(page)
        page.evaluate("""async key => {
          const {setMeshTextureState} = await import('./js/mesh-factory.js');
          const mesh = window.modViewer.activeMeshes[0];
          setMeshTextureState(mesh, {
            diffuse: mesh.userData.texKey,
            normal_map: null,
            normal_data: key,
            light_map: mesh.userData.lightMapKey,
            material_map: null,
          });
        }""", high_key)
        page.wait_for_timeout(400)
        high_pixel = _sample_mesh_pixel(page)
        bound = page.evaluate("""
          window.modViewer.activeMeshes[0].material.userData.gameMaterial
            .bindings.normal_data.enabledNode.value
        """)
        assert bound is False
        assert abs(sum(high_pixel) - sum(low_pixel)) <= 3, (
            low_pixel, high_pixel)
    finally:
        context.close()


def test_wuwa_two_direct_lights_shadow_each_light_contribution_once(
        edge_browser, frontend_url):
    payload = _packed_material_payload("wuwa:rabbitfx")
    entry = payload["meshes"]["Body-Packed-0"]
    payload["textures"] = {
        key: _flat_png_uri((255, 255, 255, 255))
        for key in payload["textures"]
    }
    payload["textures"]["diffuse::Packed-one.png"] = _flat_png_uri(
        (24, 24, 24, 255))
    light_key = "light_map::Packed-two-light.png"
    payload["textures"][light_key] = _flat_png_uri((0, 255, 0, 255))
    entry["light_map_key"] = light_key

    context, page = _page(edge_browser, frontend_url, {"Packed": payload})
    try:
        _open(page, "Packed")
        page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial"
            "?.bindings.light_map.enabledNode.value === true")
        page.evaluate("window.modViewer.setEnvironmentPreset('studio')")
        page.evaluate("""
          async () => {
            const THREE = await import('three');
            const {scene, controls} = await import('./js/scene.js');
            scene.traverse(object => {
              if (object.isAmbientLight || object.isHemisphereLight) {
                object.intensity = 0;
              } else if (object.isSprite || object.isGridHelper) {
                object.visible = false;
              }
            });
            const lights = [];
            scene.traverse(object => {
              if (object.isDirectionalLight) lights.push(object);
            });
            if (lights.length < 2) throw new Error('WuWa two-light test needs two directional lights');
            const positions = [
              new THREE.Vector3(0, 0, 3),
              new THREE.Vector3(0.8, 0, 0.35),
            ];
            lights.forEach((light, index) => {
              light.target.position.copy(controls.target);
              light.position.copy(controls.target).add(positions[index]);
              light.intensity = 0;
            });
            window.__wuwaLights = lights;
            window.__setWuWaLights = indices => lights.forEach((light, index) => {
              light.intensity = indices.includes(index) ? 0.2 : 0;
            });
          }
        """)

        def render_for(indices):
            page.evaluate("indices => window.__setWuWaLights(indices)", indices)
            page.wait_for_timeout(300)
            return _sample_mesh_pixel(page)

        light_a = render_for([0])
        light_b = render_for([1])
        both = render_for([0, 1])

        def linear_luminance(pixel):
            return sum((channel / 255) ** 2.2 for channel in pixel)

        expected = linear_luminance(light_a) + linear_luminance(light_b)
        actual = linear_luminance(both)
        assert actual > max(linear_luminance(light_a), linear_luminance(light_b))
        assert abs(actual - expected) <= max(0.015, expected * 0.25), (
            light_a, light_b, both, expected, actual)
    finally:
        context.close()


def test_genshin_tsl_material_renders_with_environment_accent_directional_light(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url,
        {"Packed": _packed_material_payload("genshin:gimi")},
    )
    runtime_errors = []
    page.on("pageerror", lambda error: runtime_errors.append(str(error)))
    page.on("console", lambda message: runtime_errors.append(message.text)
            if message.type == "error" and "Failed to load resource" not in message.text
            else None)
    try:
        _open(page, "Packed")
        page.locator(".draw-item").wait_for()
        page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial")

        page.evaluate("window.modViewer.setEnvironmentPreset('studio')")
        page.wait_for_timeout(800)
        state = page.evaluate("""async () => {
          const {scene} = await import('./js/scene.js');
          let directionalLights = 0;
          scene.traverse(object => {
            if (object.isDirectionalLight && object.visible) directionalLights += 1;
          });
          return {
            directionalLights,
            activeMeshes: window.modViewer.activeMeshes.length,
          };
        }""")
        assert state["directionalLights"] == 2
        assert state["activeMeshes"] == 1
        assert not runtime_errors, "\n".join(runtime_errors)
    finally:
        context.close()


@pytest.mark.parametrize(("profile_id", "role", "low", "high", "offset",
                          "roughness", "comparison"), [
    ("genshin:gimi", "light_map", (0, 0, 0, 255), (0, 255, 0, 255),
     (0.8, 0, 0.35), 0.35, True),
    ("genshin:gimi", "light_map", (0, 255, 0, 255), (255, 255, 0, 255),
     (0, 0, 3), 0.2, True),
    ("wuwa:rabbitfx", "light_map", (0, 0, 0, 255), (0, 255, 0, 255),
     (0.8, 0, 0.35), 0.35, True),
    ("zzz:zzmi", "material_map", (0, 0, 0, 255), (0, 255, 0, 255),
     (0, 0, 3), 0.35, True),
    ("zzz:zzmi", "material_map", (0, 0, 0, 255), (0, 0, 255, 255),
     (0, 0, 3), 0.35, True),
    ("zzz:zzmi", "material_map", (0, 255, 0, 255), (0, 255, 255, 255),
     (0, 0, 3), 0.35, None),
], ids=["genshin-shadow-g", "genshin-specular-r", "wuwa-shadow-g",
       "zzz-metalness-g", "zzz-specular-b", "zzz-metallic-b-independent"])
def test_packed_channel_changes_rendered_luminance(
        edge_browser, frontend_url, profile_id, role, low, high, offset,
        roughness, comparison):
    payload = _packed_material_payload(profile_id)
    entry = payload["meshes"]["Body-Packed-0"]
    white = _flat_png_uri((255, 255, 255, 255))
    payload["textures"] = {key: white for key in payload["textures"]}
    payload["textures"]["diffuse::Packed-one.png"] = _flat_png_uri(
        (24, 24, 24, 255))
    low_key = f"{role}::Packed-probe-low.png"
    high_key = f"{role}::Packed-probe-high.png"
    entry[f"{role}_key"] = low_key
    payload["textures"][low_key] = _flat_png_uri(low)
    payload["textures"][high_key] = _flat_png_uri(high)

    context, page = _page(
        edge_browser, frontend_url, {"Packed": payload})
    try:
        _open(page, "Packed")
        page.locator(".draw-item").wait_for()
        page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial"
            "?.bindings.diffuse.textureNode.value.image?.width === 4")
        page.evaluate("""
          async ({offset, roughness}) => {
            const THREE = await import('three');
            const {scene, controls} = await import('./js/scene.js');
            scene.traverse(object => {
              if (object.isAmbientLight || object.isHemisphereLight) {
                object.intensity = 0;
              } else if (object.isSprite || object.isGridHelper) {
                object.visible = false;
              }
            });
            const key = scene.children.find(object => object.isDirectionalLight);
            const target = controls.target;
            key.target.position.copy(target);
            key.position.copy(target).add(new THREE.Vector3(...offset));
            key.intensity = 1;
            window.modViewer.activeMeshes[0].material.roughness = roughness;
          }
        """, {"offset": offset, "roughness": roughness})
        page.wait_for_timeout(400)
        low_pixel = _sample_mesh_pixel(page)

        page.evaluate("""
          async ({role, key}) => {
            const {setMeshTextureState} = await import('./js/mesh-factory.js');
            const mesh = window.modViewer.activeMeshes[0];
            const state = {
              diffuse: mesh.userData.texKey,
              normal_map: null,
              normal_data: null,
              light_map: null,
              material_map: null,
            };
            state[role] = key;
            setMeshTextureState(mesh, state);
          }
        """, {"role": role, "key": high_key})
        page.wait_for_timeout(500)
        high_pixel = _sample_mesh_pixel(page)
        low_luminance = sum(low_pixel)
        high_luminance = sum(high_pixel)
        if comparison is True:
            assert high_luminance > low_luminance, (low_pixel, high_pixel)
        elif comparison is False:
            assert high_luminance < low_luminance, (low_pixel, high_pixel)
        else:
            assert abs(high_luminance - low_luminance) <= 3, (
                low_pixel, high_pixel)
    finally:
        context.close()


def test_genshin_lightmap_b_gates_direct_specular_area_without_changing_r_response(
        edge_browser, frontend_url):
    payload = _packed_material_payload("genshin:gimi")
    entry = payload["meshes"]["Body-Packed-0"]
    white = _flat_png_uri((255, 255, 255, 255))
    payload["textures"] = {key: white for key in payload["textures"]}
    payload["textures"]["diffuse::Packed-one.png"] = _flat_png_uri(
        (24, 24, 24, 255))
    low_key = "light_map::Packed-area-low.png"
    high_key = "light_map::Packed-area-high.png"
    max_key = "light_map::Packed-area-max.png"
    entry["light_map_key"] = low_key
    # R stays fixed (non-metal) and G/A stay fixed. Only B crosses the
    # authored threshold so this is an area-gate regression, not an R test.
    payload["textures"][low_key] = _flat_png_uri((51, 255, 0, 255))
    payload["textures"][high_key] = _flat_png_uri((51, 255, 204, 255))
    payload["textures"][max_key] = _flat_png_uri((51, 255, 255, 255))

    context, page = _page(edge_browser, frontend_url, {"Packed": payload})
    try:
        _open(page, "Packed")
        page.locator(".draw-item").wait_for()
        page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial"
            "?.bindings.diffuse.textureNode.value.image?.width === 4")
        page.evaluate("""
          async () => {
            const THREE = await import('three');
            const {scene, controls} = await import('./js/scene.js');
            scene.traverse(object => {
              if (object.isAmbientLight || object.isHemisphereLight) {
                object.intensity = 0;
              } else if (object.isSprite || object.isGridHelper) {
                object.visible = false;
              }
            });
            const key = scene.children.find(object => object.isDirectionalLight);
            key.target.position.copy(controls.target);
            key.position.copy(controls.target).add(new THREE.Vector3(0, 0, 3));
            key.intensity = 1;
            window.modViewer.activeMeshes[0].material.roughness = 0.2;
          }
        """)
        page.wait_for_timeout(400)
        low_pixel = _sample_mesh_pixel(page)
        page.evaluate("""
          async key => {
            const {setMeshTextureState} = await import('./js/mesh-factory.js');
            const mesh = window.modViewer.activeMeshes[0];
            setMeshTextureState(mesh, {
              diffuse: mesh.userData.texKey,
              normal_map: null, normal_data: null,
              light_map: key, material_map: null,
            });
          }
        """, high_key)
        page.wait_for_timeout(500)
        high_pixel = _sample_mesh_pixel(page)
        assert sum(high_pixel) > sum(low_pixel), (low_pixel, high_pixel)
        page.evaluate("""
          async key => {
            const {setMeshTextureState} = await import('./js/mesh-factory.js');
            const mesh = window.modViewer.activeMeshes[0];
            setMeshTextureState(mesh, {
              diffuse: mesh.userData.texKey,
              normal_map: null, normal_data: null,
              light_map: key, material_map: null,
            });
          }
        """, max_key)
        page.wait_for_timeout(500)
        max_pixel = _sample_mesh_pixel(page)
        assert abs(sum(max_pixel) - sum(high_pixel)) <= 3, (
            high_pixel, max_pixel)
    finally:
        context.close()


def test_genshin_high_r_region_bypasses_lightmap_b_specular_gate(
        edge_browser, frontend_url):
    payload = _packed_material_payload("genshin:gimi")
    entry = payload["meshes"]["Body-Packed-0"]
    white = _flat_png_uri((255, 255, 255, 255))
    payload["textures"] = {key: white for key in payload["textures"]}
    payload["textures"]["diffuse::Packed-one.png"] = _flat_png_uri(
        (24, 24, 24, 255))
    low_key = "light_map::Packed-metal-low.png"
    high_key = "light_map::Packed-metal-high.png"
    entry["light_map_key"] = low_key
    payload["textures"][low_key] = _flat_png_uri((255, 255, 0, 255))
    payload["textures"][high_key] = _flat_png_uri((255, 255, 255, 255))

    context, page = _page(edge_browser, frontend_url, {"Packed": payload})
    try:
        _open(page, "Packed")
        page.locator(".draw-item").wait_for()
        page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial"
            "?.bindings.diffuse.textureNode.value.image?.width === 4")
        page.evaluate("""
          async () => {
            const THREE = await import('three');
            const {scene, controls} = await import('./js/scene.js');
            scene.traverse(object => {
              if (object.isAmbientLight || object.isHemisphereLight) {
                object.intensity = 0;
              } else if (object.isSprite || object.isGridHelper) {
                object.visible = false;
              }
            });
            const key = scene.children.find(object => object.isDirectionalLight);
            key.target.position.copy(controls.target);
            key.position.copy(controls.target).add(new THREE.Vector3(0, 0, 3));
            key.intensity = 1;
            window.modViewer.activeMeshes[0].material.roughness = 0.2;
          }
        """)
        page.wait_for_timeout(400)
        low_pixel = _sample_mesh_pixel(page)
        page.evaluate("""
          async key => {
            const {setMeshTextureState} = await import('./js/mesh-factory.js');
            const mesh = window.modViewer.activeMeshes[0];
            setMeshTextureState(mesh, {
              diffuse: mesh.userData.texKey,
              normal_map: null, normal_data: null,
              light_map: key, material_map: null,
            });
          }
        """, high_key)
        page.wait_for_timeout(500)
        high_pixel = _sample_mesh_pixel(page)
        assert abs(sum(high_pixel) - sum(low_pixel)) <= 3, (
            low_pixel, high_pixel)
    finally:
        context.close()


@pytest.mark.parametrize(("profile_id", "expected"), [
    ("zzz:zzmi", {
        "physical": True, "profile": "zzz:zzmi", "normalData": False,
        "lightMap": False, "materialMap": True,
    }),
    ("genshin:gimi", {
        "physical": True, "profile": "genshin:gimi", "normalData": False,
        "lightMap": True, "materialMap": False,
    }),
    ("wuwa:rabbitfx", {
        "physical": True, "profile": "wuwa:rabbitfx", "normalData": False,
        "lightMap": True, "materialMap": False,
    }),
    ("none", {
        "physical": False, "profile": "none", "normalData": False,
        "lightMap": False, "materialMap": False,
    }),
])
def test_packed_material_sources_are_loaded_only_when_sampled(
        edge_browser, frontend_url, profile_id, expected):
    context, page = _page(
        edge_browser, frontend_url,
        {"Packed": _packed_material_payload(profile_id)},
    )
    try:
        _open(page, "Packed")
        page.locator(".draw-item").wait_for()
        page.wait_for_timeout(300)
        state = page.evaluate("""() => {
          const material = window.modViewer.activeMeshes[0].material;
          const game = material.userData.gameMaterial;
          const bindings = game?.bindings || {};
          return {
            physical: !!material.isMeshPhysicalNodeMaterial,
            profile: game?.profile?.id || null,
            normalData: !!bindings.normal_data?.enabledNode?.value,
            lightMap: !!bindings.light_map?.enabledNode?.value,
            materialMap: !!bindings.material_map?.enabledNode?.value,
          };
        }""")
        assert state == expected
    finally:
        context.close()


def test_texture_rows_are_reused_for_control_changes_and_rebuilt_for_pool_changes(
        edge_browser, frontend_url):
    pick = {
        "tex_key": "diffuse::added.png", "file": "added.png", "uri":
        "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLkWQAAAABJRU5ErkJggg==",
    }
    context, page = _page(edge_browser, frontend_url, {"A": _payload("A")}, picks=[pick])
    try:
        _open(page, "A")
        page.locator(".draw-item").wait_for()
        page.evaluate("window.__textureRows = [...document.querySelectorAll('.tex-item')]")

        page.locator("#toggle-list .toggle-cycle-btn").click()
        assert page.evaluate("window.__textureRows.every((row, i) => row === document.querySelectorAll('.tex-item')[i])")
        page.locator("#menu-list .toggle-cycle-btn").click()
        assert page.evaluate("window.__textureRows.every((row, i) => row === document.querySelectorAll('.tex-item')[i])")
        page.locator("#menu-list .menu-slider").evaluate(
            "input => { input.value = '0.5'; input.dispatchEvent(new Event('input', {bubbles: true})); }")
        assert page.evaluate("window.__textureRows.every((row, i) => row === document.querySelectorAll('.tex-item')[i])")

        page.locator(".group-tex-btn").click()
        page.locator("#texm-add").click()
        page.wait_for_function("document.querySelectorAll('.tex-item').length === 3")
        assert not page.evaluate("window.__textureRows[0] === document.querySelector('.tex-item')")
        page.evaluate("window.__textureRows = [...document.querySelectorAll('.tex-item')]")
        page.locator(".texm-row .toggle-icon-btn").last.click()
        page.wait_for_function("document.querySelectorAll('.tex-item').length === 2")
        assert not page.evaluate("window.__textureRows[0] === document.querySelector('.tex-item')")
    finally:
        context.close()


def test_record_handler_is_replaced_and_restored(edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"A": _payload("A")})
    try:
        _open(page, "A")
        cycle = page.locator("#toggle-list .toggle-cycle-btn")
        cycle.wait_for()
        original_label = page.locator("#toggle-list .toggle-value").inner_text()
        original_state = page.evaluate(
            "import('./js/visibility.js').then(module => module.getToggleState())")
        page.evaluate("""
          () => { window.__cycleHandler = document.querySelector('#toggle-list .toggle-cycle-btn').onclick; }
        """)
        page.locator("#toggle-list [title^='Record']").click()
        page.locator("#toggle-list .toggle-row.recording").wait_for()
        recording_state = page.evaluate(
            "import('./js/visibility.js').then(module => module.getToggleState())")
        assert page.evaluate("window.__cycleHandler !== document.querySelector('#toggle-list .toggle-cycle-btn').onclick")
        page.locator("#toggle-list .toggle-record-cancel").click()
        assert page.evaluate("window.__cycleHandler === document.querySelector('#toggle-list .toggle-cycle-btn').onclick")
        restored_state = page.evaluate(
            "import('./js/visibility.js').then(module => module.getToggleState())")
        assert page.locator("#toggle-list .toggle-value").inner_text() == original_label, (
            original_state, recording_state, restored_state)
    finally:
        context.close()


def test_tool_panel_is_in_left_dock_and_available_before_load(edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {})
    try:
        assert page.evaluate("document.querySelector('#tool-panel').parentElement.id") == "left-dock"
        placement = page.evaluate("""
          () => {
            const panel = document.querySelector('#tool-panel');
            const footer = document.querySelector('#footer').getBoundingClientRect();
            const rect = panel.getBoundingClientRect();
            return {
              position: getComputedStyle(panel).position,
              center: rect.left + rect.width / 2,
              viewportCenter: window.innerWidth / 2,
              aboveFooter: rect.bottom < footer.top,
            };
          }
        """)
        assert placement["position"] == "fixed"
        assert abs(placement["center"] - placement["viewportCenter"]) < 1
        assert placement["aboveFooter"]
        assert page.locator("#tool-panel").is_visible()
        assert page.locator("#tool-buttons .tool-btn").count() == 7
        assert page.locator("#outline-btn").get_attribute("aria-label") == (
            "Silhouette outlines")
    finally:
        context.close()


def test_outline_child_shares_geometry_and_render_modes_suppress_it(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"A": _payload("A")})
    try:
        _open(page, "A")
        page.locator(".draw-item").wait_for()
        structure = page.evaluate("""async () => {
          const THREE = await import('three');
          const mesh = window.modViewer.activeMeshes[0];
          const outline = mesh.children.filter(
            child => child.userData.isViewerOutline);
          const item = outline[0];
          return {
            count: outline.length,
            sharedGeometry: item?.geometry === mesh.geometry,
            isMeshBasicNodeMaterial: item?.material?.isMeshBasicNodeMaterial === true,
            side: item?.material?.side,
            backSide: THREE.BackSide,
            depthTest: item?.material?.depthTest,
            depthWrite: item?.material?.depthWrite,
            renderOrder: item?.renderOrder,
            baseRenderOrder: mesh.renderOrder,
            map: item?.material?.map || null,
            state: window.modViewer.getOutlineState(0),
          };
        }""")
        assert structure["count"] == 1
        assert structure["sharedGeometry"]
        assert structure["isMeshBasicNodeMaterial"]
        assert structure["side"] == structure["backSide"]
        assert structure["depthTest"] is True
        assert structure["depthWrite"] is False
        assert structure["renderOrder"] > structure["baseRenderOrder"]
        assert structure["map"] is None
        assert structure["state"] == {
            "attached": True, "visible": False, "globalEnabled": False,
            "widthPixels": 1.5, "suppressedByWireframe": False,
            "suppressedByDebug": False,
        }

        page.locator("#outline-btn").click()
        assert page.evaluate("window.modViewer.getOutlineState(0)") == {
            "attached": True, "visible": True, "globalEnabled": True,
            "widthPixels": 1.5, "suppressedByWireframe": False,
            "suppressedByDebug": False,
        }
        page.locator("#wire-btn").click()
        assert page.evaluate("window.modViewer.getOutlineState(0)") == {
            "attached": True, "visible": False, "globalEnabled": True,
            "widthPixels": 1.5, "suppressedByWireframe": True,
            "suppressedByDebug": False,
        }
        page.locator("#wire-btn").click()
        page.evaluate("window.modViewer.setMaterialDebugMode('shadow-mask')")
        assert page.evaluate("window.modViewer.getOutlineState(0)") == {
            "attached": True, "visible": False, "globalEnabled": True,
            "widthPixels": 1.5, "suppressedByWireframe": False,
            "suppressedByDebug": True,
        }
        page.evaluate("window.modViewer.setMaterialDebugMode('off')")
        assert page.evaluate("window.modViewer.getOutlineState(0).visible")

        page.evaluate("window.modViewer.reloadCurrentMod()")
        page.wait_for_function("window.modViewer.activeMeshes.length === 1")
        assert page.evaluate("window.modViewer.getOutlineState(0)") == {
            "attached": True, "visible": True, "globalEnabled": True,
            "widthPixels": 1.5, "suppressedByWireframe": False,
            "suppressedByDebug": False,
        }
    finally:
        context.close()


def test_outline_render_is_outer_silhouette_without_covering_front_surface(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"A": _payload("A")})
    try:
        _open(page, "A")
        page.locator(".draw-item").wait_for()
        bounds = page.evaluate("""async () => {
          const THREE = await import('three');
          const {scene, camera} = await import('./js/scene.js');
          const {attachOutline, setOutlinesEnabled} =
            await import('./js/outline-renderer.js');
          const probe = new THREE.Mesh(
            new THREE.BoxGeometry(0.6, 0.6, 0.6),
            new THREE.MeshBasicMaterial({color: 0xf0f0f0}));
          probe.position.set(0.5, 0.5, 0);
          scene.add(probe);
          attachOutline(probe);
          setOutlinesEnabled(false);
          scene.updateMatrixWorld(true);
          const box = new THREE.Box3().setFromObject(probe);
          const points = [];
          for (const x of [box.min.x, box.max.x]) {
            for (const y of [box.min.y, box.max.y]) {
              for (const z of [box.min.z, box.max.z]) {
                points.push(new THREE.Vector3(x, y, z).project(camera));
              }
            }
          }
          const rect = scene.userData.__outlineCanvasRect =
            document.querySelector('#canvas-container canvas').getBoundingClientRect();
          const xs = points.map(p => rect.left + (p.x + 1) * rect.width / 2);
          const ys = points.map(p => rect.top + (1 - p.y) * rect.height / 2);
          return {
            minX: Math.min(...xs), maxX: Math.max(...xs),
            minY: Math.min(...ys), maxY: Math.max(...ys),
            centerX: (Math.min(...xs) + Math.max(...xs)) / 2,
            centerY: (Math.min(...ys) + Math.max(...ys)) / 2,
          };
        }""")
        page.wait_for_timeout(120)
        off = Image.open(io.BytesIO(page.screenshot())).convert("RGB")
        page.evaluate("window.modViewer.setOutlineEnabled(true)")
        page.wait_for_timeout(300)
        on = Image.open(io.BytesIO(page.screenshot())).convert("RGB")

        left = max(0, round(bounds["minX"]) - 1)
        center_y = min(on.height - 1, max(0, round(bounds["centerY"])))
        assert off.getpixel((left, center_y)) != on.getpixel((left, center_y)), (
            bounds, off.getpixel((left, center_y)), on.getpixel((left, center_y)))

        center = (round(bounds["centerX"]), round(bounds["centerY"]))
        interior_delta = max(
            abs(a - b) for a, b in zip(off.getpixel(center), on.getpixel(center)))
        assert interior_delta <= 3, (
            bounds, off.getpixel(center), on.getpixel(center))
    finally:
        context.close()


def test_outline_width_uses_css_viewport_height_and_camera_distance(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"A": _payload("A")})
    try:
        _open(page, "A")
        page.locator(".draw-item").wait_for()
        widths = page.evaluate("""async () => {
          const {camera, controls, renderer} = await import('./js/scene.js');
          const {updateOutlineCameraScale} =
            await import('./js/outline-renderer.js');
          const target = controls.target;
          const original = camera.position.clone();
          const direction = original.clone().sub(target).normalize();
          const distance = original.distanceTo(target);
          const height = renderer.domElement.clientHeight;
          const near = updateOutlineCameraScale(camera, target, height);
          camera.position.copy(target).addScaledVector(direction, distance * 2);
          const far = updateOutlineCameraScale(camera, target, height);
          const resized = updateOutlineCameraScale(camera, target, height * 2);
          camera.position.copy(original);
          return {near, far, resized, height};
        }""")
        assert widths["near"] > 0
        assert widths["far"] / widths["near"] == pytest.approx(2, rel=1e-5)
        assert widths["resized"] / widths["far"] == pytest.approx(
            0.5, rel=1e-5)
        assert widths["height"] > 0
    finally:
        context.close()


def test_control_updates_synchronize_all_current_panels(edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"Sync": _state_sync_payload()})
    try:
        _open(page, "Sync")
        page.locator("#present-list .toggle-item").wait_for()

        page.locator("#toggle-list .toggle-cycle-btn").click()
        assert "toggle=1" in page.locator("#toggle-list .toggle-value").inner_text()
        menu_values = page.evaluate("""
          Object.fromEntries([...document.querySelectorAll('#menu-list .menu-item')]
            .map(item => [item.querySelector('.menu-name').textContent,
                          item.querySelector('.menu-value').textContent]))
        """)
        assert menu_values["Menu"] == "1"

        page.locator("#menu-list .menu-item").filter(has_text="Menu").locator("button").click()
        menu_values = page.evaluate("""
          Object.fromEntries([...document.querySelectorAll('#menu-list .menu-item')]
            .map(item => [item.querySelector('.menu-name').textContent,
                          item.querySelector('.menu-value').textContent]))
        """)
        assert menu_values["Sibling"] == "1"

        page.locator("#present-list .toggle-cycle-btn").click()
        assert "toggle=0" in page.locator("#toggle-list .toggle-value").inner_text()
        assert page.locator("#menu-list .menu-item").filter(
            has_text="Menu").locator(".menu-value").inner_text() == "0"
    finally:
        context.close()


def test_selection_uses_view_binding_and_reload_replaces_sync_callbacks(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"A": _payload("A")})
    try:
        _open(page, "A")
        page.locator(".draw-item").wait_for()
        page.locator(".group-hdr").click()
        assert page.locator(".group-items.collapsed").count() == 1
        page.evaluate("""
          async () => {
            const row = document.querySelector('.draw-item');
            row.scrollIntoView = () => { window.__selectionScrolled = true; };
            const selection = await import('./js/selection.js');
            selection.selectMesh(window.modViewer.activeMeshes[0]);
          }
        """)
        assert page.locator(".draw-item.selected").count() == 1
        assert page.locator(".group-items.collapsed").count() == 0
        assert page.evaluate("window.__selectionScrolled")

        before = page.evaluate("import('./js/view-sync.js').then(module => module.viewSyncCount())")
        page.evaluate("window.__oldDrawRow = document.querySelector('.draw-item')")
        page.evaluate("window.modViewer.reloadCurrentMod()")
        page.wait_for_function("""
          document.querySelector('.draw-item') !== window.__oldDrawRow
            && !document.querySelector('#loading').classList.contains('show')
        """)
        after = page.evaluate("import('./js/view-sync.js').then(module => module.viewSyncCount())")
        assert before == after == 4
    finally:
        context.close()


def test_view_gizmo_click_drag_wheel_and_keyboard(edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"A": _payload("A")})
    try:
        _open(page, "A")
        page.locator(".draw-item").wait_for()
        page.locator("#view-gizmo .axis-x.positive circle").click()
        page.wait_for_timeout(240)
        direction = page.evaluate("""
          import('./js/scene.js').then(({camera, controls}) =>
            camera.position.clone().sub(controls.target).normalize().toArray())
        """)
        assert direction[0] > 0.98

        capture_count = page.evaluate("""
          () => {
            const gizmo = document.querySelector('#view-gizmo');
            const axis = gizmo.querySelector('.axis-z.positive');
            window.__gizmoCaptures = 0;
            gizmo.setPointerCapture = () => { window.__gizmoCaptures += 1; };
            gizmo.hasPointerCapture = () => false;
            axis.dispatchEvent(new PointerEvent('pointerdown', {
              bubbles: true, button: 0, pointerId: 71, clientX: 50, clientY: 50,
            }));
            gizmo.dispatchEvent(new PointerEvent('pointermove', {
              bubbles: true, pointerId: 71, clientX: 51, clientY: 50,
            }));
            gizmo.dispatchEvent(new PointerEvent('pointerup', {
              bubbles: true, pointerId: 71, clientX: 51, clientY: 50,
            }));
            return window.__gizmoCaptures;
          }
        """)
        assert capture_count == 0
        page.wait_for_timeout(240)
        direction = page.evaluate("""
          import('./js/scene.js').then(({camera, controls}) =>
            camera.position.clone().sub(controls.target).normalize().toArray())
        """)
        assert direction[2] > 0.98

        drag_result = page.evaluate("""
          async () => {
            const {camera} = await import('./js/scene.js');
            const gizmo = document.querySelector('#view-gizmo');
            const before = camera.position.toArray();
            window.__gizmoCaptures = 0;
            gizmo.setPointerCapture = () => { window.__gizmoCaptures += 1; };
            gizmo.hasPointerCapture = () => true;
            gizmo.releasePointerCapture = () => {};
            gizmo.dispatchEvent(new PointerEvent('pointerdown', {
              bubbles: true, button: 0, pointerId: 72, clientX: 50, clientY: 50,
            }));
            gizmo.dispatchEvent(new PointerEvent('pointermove', {
              bubbles: true, pointerId: 72, clientX: 64, clientY: 57,
            }));
            gizmo.dispatchEvent(new PointerEvent('pointerup', {
              bubbles: true, pointerId: 72, clientX: 64, clientY: 57,
            }));
            return {before, after: camera.position.toArray(), captures: window.__gizmoCaptures};
          }
        """)
        assert drag_result["captures"] == 1
        assert drag_result["before"] != drag_result["after"]
        assert page.locator("#view-gizmo.dragging").count() == 0

        distances = page.evaluate("""
          async () => {
            const {camera, controls} = await import('./js/scene.js');
            const before = camera.position.distanceTo(controls.target);
            document.querySelector('#view-gizmo').dispatchEvent(
              new WheelEvent('wheel', {bubbles: true, cancelable: true, deltaY: 120}));
            return [before, camera.position.distanceTo(controls.target)];
          }
        """)
        assert distances[1] > distances[0]

        page.locator("#view-gizmo .axis-y.positive").focus()
        page.locator("#view-gizmo .axis-y.positive").press("Enter")
        page.wait_for_timeout(240)
        direction = page.evaluate("""
          import('./js/scene.js').then(({camera, controls}) =>
            camera.position.clone().sub(controls.target).normalize().toArray())
        """)
        assert direction[1] > 0.98
    finally:
        context.close()


def test_key_light_camera_controls_and_narrow_framing(edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"A": _payload("A")})
    try:
        _open(page, "A")
        page.locator(".draw-item").wait_for()
        page.wait_for_timeout(100)

        light_info = page.evaluate("""
          async () => {
            const {scene, camera, renderer} = await import('./js/scene.js');
            const handle = scene.children.find(object => object.isSprite);
            const light = scene.children.find(object => object.isDirectionalLight);
            const point = handle.position.clone().project(camera);
            const rect = renderer.domElement.getBoundingClientRect();
            return {
              x: rect.left + (point.x + 1) * rect.width / 2,
              y: rect.top + (1 - point.y) * rect.height / 2,
              position: light.position.toArray(),
            };
          }
        """)
        page.mouse.move(light_info["x"], light_info["y"])
        assert page.evaluate(
            "document.querySelector('#canvas-container canvas').style.cursor") == "crosshair"
        page.mouse.down()
        page.mouse.move(light_info["x"] + 28, light_info["y"] + 12)
        page.mouse.up()
        dragged = page.evaluate("""
          import('./js/scene.js').then(({scene}) =>
            scene.children.find(object => object.isDirectionalLight).position.toArray())
        """)
        assert dragged != light_info["position"]

        light_info = page.evaluate("""
          async () => {
            const {scene, camera, renderer} = await import('./js/scene.js');
            const handle = scene.children.find(object => object.isSprite);
            const point = handle.position.clone().project(camera);
            const rect = renderer.domElement.getBoundingClientRect();
            return {x: rect.left + (point.x + 1) * rect.width / 2,
                    y: rect.top + (1 - point.y) * rect.height / 2,
                    position: handle.position.toArray()};
          }
        """)
        page.keyboard.down("Shift")
        page.mouse.move(light_info["x"], light_info["y"])
        page.mouse.down()
        page.mouse.move(light_info["x"], light_info["y"] - 24)
        page.mouse.up()
        page.keyboard.up("Shift")
        depth_dragged = page.evaluate("""
          import('./js/scene.js').then(({scene}) =>
            scene.children.find(object => object.isDirectionalLight).position.toArray())
        """)
        assert depth_dragged != light_info["position"]

        occluded = page.evaluate("""
          async () => {
            const THREE = await import('three');
            const {scene, camera, renderer} = await import('./js/scene.js');
            const handle = scene.children.find(object => object.isSprite);
            const blocker = new THREE.Mesh(
              new THREE.PlaneGeometry(100, 100),
              new THREE.MeshBasicMaterial({side: THREE.DoubleSide}));
            blocker.position.lerpVectors(camera.position, handle.position, 0.5);
            blocker.quaternion.copy(camera.quaternion);
            scene.add(blocker);
            scene.updateMatrixWorld(true);
            window.__lightBlocker = blocker;
            const point = handle.position.clone().project(camera);
            const rect = renderer.domElement.getBoundingClientRect();
            return {x: rect.left + (point.x + 1) * rect.width / 2,
                    y: rect.top + (1 - point.y) * rect.height / 2};
          }
        """)
        page.mouse.move(0, 0)
        page.mouse.move(occluded["x"], occluded["y"])
        assert page.evaluate(
            "document.querySelector('#canvas-container canvas').style.cursor") != "crosshair"
        page.evaluate("""
          import('./js/scene.js').then(({scene}) => scene.remove(window.__lightBlocker))
        """)

        page.locator("#light-btn").click()
        page.locator("#light-btn").click()
        off_state = page.evaluate("""
          import('./js/scene.js').then(({scene}) => ({
            intensity: scene.children.find(object => object.isDirectionalLight).intensity,
            visible: scene.children.find(object => object.isSprite).visible,
          }))
        """)
        assert off_state == {"intensity": 0, "visible": False}
        page.locator("#light-btn").click()
        before_environment = page.evaluate("""
          import('./js/scene.js').then(({scene}) =>
            scene.children.find(object => object.isDirectionalLight).intensity)
        """)
        page.evaluate("window.modViewer.setEnvironmentPreset('studio')")
        page.evaluate("window.modViewer.setEnvironmentPreset('default')")
        after_environment = page.evaluate("""
          import('./js/scene.js').then(({scene}) =>
            scene.children.find(object => object.isDirectionalLight).intensity)
        """)
        assert before_environment == after_environment == 1

        home_quaternion = page.evaluate(
            "window.modViewer.activeMeshes[0].quaternion.toArray()")
        page.locator("#camera-flip-btn").click()
        turned = page.evaluate("window.modViewer.activeMeshes[0].quaternion.toArray()")
        page.locator("#camera-flip-horizontal-btn").click()
        tilted = page.evaluate("window.modViewer.activeMeshes[0].quaternion.toArray()")
        assert turned != home_quaternion
        assert tilted != turned
        page.locator("#camera-reset-view-btn").click()
        assert page.evaluate(
            "window.modViewer.activeMeshes[0].quaternion.toArray()") == home_quaternion

        page.set_viewport_size({"width": 900, "height": 600})
        page.evaluate("window.__oldDrawRow = document.querySelector('.draw-item')")
        page.evaluate("window.modViewer.reloadCurrentMod()")
        page.wait_for_function("""
          document.querySelector('.draw-item') !== window.__oldDrawRow
            && !document.querySelector('#loading').classList.contains('show')
        """)
        framing = page.evaluate("""
          async () => {
            const THREE = await import('three');
            const {camera, renderer} = await import('./js/scene.js');
            const box = new THREE.Box3().setFromObject(window.modViewer.activeMeshes[0]);
            const projected = box.getCenter(new THREE.Vector3()).project(camera);
            const canvas = renderer.domElement.getBoundingClientRect();
            return {
              x: canvas.left + (projected.x + 1) * canvas.width / 2,
              left: document.querySelector('#left-dock').getBoundingClientRect().right,
              right: document.querySelector('#right-dock').getBoundingClientRect().left,
            };
          }
        """)
        assert framing["left"] < framing["x"] < framing["right"]
    finally:
        context.close()


def test_source_grouping_and_collapse_are_shared_without_losing_duplicates(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url,
        {"Single": _payload("Single"), "Sources": _source_payload()},
    )
    try:
        _open(page, "Single")
        page.locator(".draw-item").wait_for()
        assert page.locator("#mesh-list .mesh-src-hdr").count() == 0
        assert page.locator("#toggle-list .toggle-src-hdr").count() == 0
        assert page.locator("#menu-list .toggle-src-hdr").count() == 0

        page.evaluate("window.__oldDrawRow = document.querySelector('.draw-item')")
        _open(page, "Sources")
        page.wait_for_function(
            "document.querySelector('.draw-item') !== window.__oldDrawRow")
        expected = ["Root.ini", "variants/sub"]
        assert page.locator("#mesh-list .mesh-src-hdr .group-name").all_inner_texts() == expected
        assert page.locator("#toggle-list .toggle-src-hdr .group-name").all_inner_texts() == expected
        assert page.locator("#menu-list .toggle-src-hdr .group-name").all_inner_texts() == expected
        assert page.locator("#mesh-list .group-hdr .group-name").all_inner_texts() == ["Body", "Body"]
        assert page.locator("#toggle-list .toggle-name").all_inner_texts() == ["Duplicate", "Duplicate"]

        first_header = page.locator("#mesh-list .mesh-src-hdr").first
        first_header.click()
        assert page.locator("#mesh-list .mesh-src-items.collapsed").count() == 1
        assert first_header.locator(".group-toggle.collapsed").count() == 1
        first_header.click()
        assert page.locator("#mesh-list .mesh-src-items.collapsed").count() == 0
    finally:
        context.close()


def test_modal_shell_accessibility_and_ace_escape_behavior(edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"A": _payload("A")})
    try:
        _open(page, "A")
        page.locator(".draw-item").wait_for()

        page.locator(".group-tex-btn").click()
        texture_modal = page.locator("#texture-modal-backdrop .modal")
        assert texture_modal.get_attribute("role") == "dialog"
        assert texture_modal.get_attribute("aria-modal") == "true"
        assert texture_modal.get_attribute("aria-labelledby") == "texm-title"
        page.keyboard.press("Escape")
        assert page.locator("#texture-modal-backdrop.show").count() == 0
        page.locator(".group-tex-btn").click()
        page.locator("#texture-modal-backdrop").dispatch_event("click")
        assert page.locator("#texture-modal-backdrop.show").count() == 0

        page.locator("#toggle-add-btn").click()
        page.locator("#toggle-modal-backdrop.show").wait_for()
        toggle_modal = page.locator("#toggle-modal-backdrop .modal")
        assert toggle_modal.get_attribute("aria-labelledby") == "tm-title"
        page.keyboard.press("Escape")
        assert page.locator("#toggle-modal-backdrop.show").count() == 0

        page.locator("#health-btn").click()
        page.locator("#health-modal-backdrop.show").wait_for()
        page.keyboard.press("Escape")
        assert page.locator("#health-modal-backdrop.show").count() == 0

        page.locator("#ini-view-btn").click()
        page.locator("#ini-editor-backdrop.show").wait_for()
        page.evaluate("window.ace.edit(document.querySelector('#ini-editor-text')).focus()")
        page.keyboard.press("Control+f")
        page.locator("#ini-editor-text .ace_search").wait_for()
        page.keyboard.press("Escape")
        assert page.locator("#ini-editor-backdrop.show").count() == 1
        assert page.locator("#ini-editor-text .ace_search").is_hidden()
        page.locator("#ini-editor-close-x").click()
    finally:
        context.close()


def test_feature_flag_css_keeps_cycle_preview_and_core_invariants(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"A": _payload("A")})
    try:
        _open(page, "A")
        page.locator("#toggle-list .toggle-cycle-btn").wait_for()
        page.evaluate("""
          document.body.classList.add('feature-export-off', 'feature-modify-toggle-off')
        """)
        assert page.locator("#export-btn").is_hidden()
        assert page.locator("#toggle-add-btn").is_hidden()
        assert page.locator("#toggle-list .toggle-actions").is_hidden()
        assert page.locator("#toggle-list .toggle-cycle-btn").is_visible()

        css = (paths.web_dir() + "/css/app.css")
        with open(css, encoding="utf-8") as handle:
            source = handle.read().replace(" ", "")
        assert "#menu-list.image-layout.collapsed{display:none;}" in source
        with open(paths.web_dir() + "/index.html", encoding="utf-8") as handle:
            html = handle.read()
        assert "https://cdn" not in html.lower()
        assert "http://" not in html.lower()
    finally:
        context.close()

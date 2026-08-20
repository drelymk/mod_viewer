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
        "metadata": {"mesh_names": {}},
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
    }
    profile = (material_profile_for(*profile_args[profile_id]).to_metadata()
               if profile_id in profile_args
               else material_profile_for("unknown", "unknown").to_metadata())
    payload["metadata"]["material_profile"] = profile
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
    point = page.evaluate("""
      async () => {
        const THREE = await import('three');
        const {camera, renderer} = await import('./js/scene.js');
        const mesh = window.modViewer.activeMeshes[0];
        const projected = new THREE.Vector3(0.25, 0.25, 0)
          .applyMatrix4(mesh.matrixWorld).project(camera);
        const rect = renderer.domElement.getBoundingClientRect();
        return {
          x: Math.round(rect.left + (projected.x + 1) * rect.width / 2),
          y: Math.round(rect.top + (1 - projected.y) * rect.height / 2),
        };
      }
    """)
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
          const material = window.modViewer.activeMeshes[0].material;
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
    ("zzz:zzmi", "material_map", (0, 0, 0, 255), (0, 255, 0, 255),
     (0, 0, 3), 0.35, True),
    ("zzz:zzmi", "material_map", (0, 0, 0, 255), (0, 0, 255, 255),
     (0, 0, 3), 0.35, True),
    ("zzz:zzmi", "material_map", (0, 255, 0, 255), (0, 255, 255, 255),
     (0, 0, 3), 0.35, None),
], ids=["genshin-shadow-g", "genshin-specular-r", "zzz-metalness-g",
       "zzz-specular-b", "zzz-metallic-b-independent"])
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
        "physical": False, "profile": "wuwa:rabbitfx", "normalData": False,
        "lightMap": False, "materialMap": False,
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
        assert page.locator("#tool-buttons .tool-btn").count() == 6
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

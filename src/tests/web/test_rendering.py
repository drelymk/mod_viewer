from .support import *
from PIL import ImageChops

def test_webgpu_startup_uses_actual_webgpu_backend(edge_browser, frontend_url):
    context = edge_browser.new_context(bypass_csp=True)
    page = context.new_page()
    try:
        page.goto(frontend_url)
        page.locator("#open-btn:not([disabled])").wait_for(timeout=10000)
        page.wait_for_function(
            "import('./js/scene/scene.js').then(({renderer}) => renderer.currentSamples === 4)")
        state = page.evaluate("""async () => {
          const {renderer, scene} = await import('./js/scene/scene.js');
          const keyLight = scene.children.find(object => object.isDirectionalLight);
          return {
            isWebGPURenderer: renderer.isWebGPURenderer === true,
            isWebGPUBackend: renderer.backend?.isWebGPUBackend === true,
            compatibilityMode: renderer.backend?.compatibilityMode,
            samples: renderer.samples,
            animationLoop: renderer.getAnimationLoop() !== null,
            outputColorSpace: renderer.outputColorSpace,
            toneMapping: renderer.toneMapping,
            clearAlpha: renderer.getClearAlpha(),
            shadowsEnabled: renderer.shadowMap.enabled,
            keyCastsShadow: keyLight?.castShadow,
            shadowAutoUpdate: keyLight?.shadow?.autoUpdate,
            shadowMapSize: [keyLight?.shadow?.mapSize?.x, keyLight?.shadow?.mapSize?.y],
          };
        }""")
        assert state["isWebGPURenderer"]
        assert state["isWebGPUBackend"]
        assert state["compatibilityMode"] is False
        assert state["samples"] == 4
        assert not state["animationLoop"]
        assert state["outputColorSpace"] == "srgb"
        assert state["toneMapping"] == 0
        assert state["clearAlpha"] == 1
        assert state["shadowsEnabled"]
        assert state["keyCastsShadow"]
        assert state["shadowAutoUpdate"] is False
        assert state["shadowMapSize"] == [2048, 2048]
    finally:
        context.close()

def test_viewport_pipeline_uses_character_layers_and_stable_ao_settings(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"Pipeline": _payload("Pipeline")})
    errors = []
    page.on("pageerror", lambda error: errors.append(error))
    try:
        _open(page, "Pipeline")
        page.locator(".draw-item").wait_for()
        page.wait_for_function("window.modViewer.getRenderCount() > 0")
        state = page.evaluate("""async () => {
          const THREE = await import('three');
          const {getViewportRenderPipelineDebugState, scene} =
            await import('./js/scene/scene.js');
          const {CHARACTER_AO_LAYER} =
            await import('./js/scene/viewer-layers.js');
          const mesh = window.modViewer.activeMeshes[0];
          const layer = new THREE.Layers();
          layer.set(CHARACTER_AO_LAYER);
          const helpers = [];
          scene.traverse(object => {
            if (object.userData.isViewerGround || object.isGridHelper
                || object.isSprite || object.userData.isViewerOutline) {
              helpers.push({
                ground: object.userData.isViewerGround === true,
                grid: object.isGridHelper === true,
                sprite: object.isSprite === true,
                outline: object.userData.isViewerOutline === true,
                hasCharacterLayer: object.layers.test(layer),
              });
            }
          });
          return {
            pipeline: getViewportRenderPipelineDebugState(),
            meshHasCharacterLayer: mesh.layers.test(layer),
            helpers,
            viewerRenderCount: window.modViewer.getRenderCount(),
          };
        }""")
        assert state["pipeline"]["hasRenderPipeline"]
        assert state["pipeline"]["hasPrePass"]
        assert state["pipeline"]["hasGTAO"]
        assert state["pipeline"]["prePassLayerMask"] == 1 << 1
        assert state["pipeline"]["characterAOLayer"] == 1
        assert state["pipeline"]["samples"] == 16
        assert state["pipeline"]["resolutionScale"] == pytest.approx(0.5)
        assert state["pipeline"]["temporalFiltering"] is False
        assert state["pipeline"]["strength"] == pytest.approx(0.22)
        assert not state["pipeline"]["pipelineNeedsUpdate"]
        assert state["pipeline"]["renderCount"] == state["viewerRenderCount"]
        assert state["meshHasCharacterLayer"]
        assert all(not helper["hasCharacterLayer"] for helper in state["helpers"])
        assert not errors, "\n".join(str(error) for error in errors)
    finally:
        context.close()

def test_viewport_pipeline_uses_model_scale_and_wireframe_bypass(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"Pipeline": _payload("Pipeline")})
    try:
        _open(page, "Pipeline")
        page.locator(".draw-item").wait_for()
        page.wait_for_function("window.modViewer.getRenderCount() > 0")
        initial = page.evaluate("""async () => {
          const {getViewportRenderPipelineDebugState} =
            await import('./js/scene/scene.js');
          return getViewportRenderPipelineDebugState();
        }""")
        assert initial["modelSize"] > 0
        assert initial["radius"] == pytest.approx(initial["modelSize"] * 0.15)
        assert initial["effectiveStrength"] == pytest.approx(initial["strength"])

        page.locator("#wire-btn").click()
        page.wait_for_function("""async () => {
          const {getViewportRenderPipelineDebugState} =
            await import('./js/scene/scene.js');
          return getViewportRenderPipelineDebugState().effectiveStrength === 0;
        }""")
        suppressed = page.evaluate("""async () => {
          const {getViewportRenderPipelineDebugState} =
            await import('./js/scene/scene.js');
          return getViewportRenderPipelineDebugState();
        }""")
        assert suppressed["enabled"]
        assert suppressed["strength"] == pytest.approx(initial["strength"])
        assert suppressed["effectiveStrength"] == 0
        assert not suppressed["pipelineNeedsUpdate"]

        page.locator("#wire-btn").click()
        page.wait_for_function("""async () => {
          const {getViewportRenderPipelineDebugState} =
            await import('./js/scene/scene.js');
          return getViewportRenderPipelineDebugState().effectiveStrength > 0;
        }""")
        restored = page.evaluate("""async () => {
          const {getViewportRenderPipelineDebugState} =
            await import('./js/scene/scene.js');
          return getViewportRenderPipelineDebugState();
        }""")
        assert restored["effectiveStrength"] == pytest.approx(initial["strength"])
        assert restored["modelSize"] == pytest.approx(initial["modelSize"])
        assert restored["radius"] == pytest.approx(initial["radius"])
    finally:
        context.close()

@pytest.mark.parametrize("scale", [0.01, 1, 100])
def test_viewport_gtao_radius_scales_with_model_bounds(edge_browser, frontend_url, scale):
    label = f"PipelineScale{scale}"
    payload = _payload(label)
    entry = payload["meshes"][f"Body-{label}-0"]
    entry["pos"] = _f32(0, 0, 0, scale, 0, 0, 0, scale, scale * 0.25)
    context, page = _page(edge_browser, frontend_url, {label: payload})
    try:
        _open(page, label)
        page.locator(".draw-item").wait_for()
        page.wait_for_function("window.modViewer.getRenderCount() > 0")
        state = page.evaluate("""async () => {
          const {getViewportRenderPipelineDebugState} =
            await import('./js/scene/scene.js');
          return getViewportRenderPipelineDebugState();
        }""")
        assert math.isfinite(state["modelSize"])
        assert math.isfinite(state["radius"])
        assert state["modelSize"] > 0
        assert state["radius"] == pytest.approx(state["modelSize"] * 0.15)
    finally:
        context.close()

def test_viewport_pipeline_resizes_pass_targets_without_rebuilding(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"Resize": _payload("Resize")})
    try:
        _open(page, "Resize")
        page.locator(".draw-item").wait_for()
        page.wait_for_function("window.modViewer.getRenderCount() > 0")
        initial = page.evaluate("""async () => {
          const {getViewportRenderPipelineDebugState} =
            await import('./js/scene/scene.js');
          return getViewportRenderPipelineDebugState();
        }""")
        assert initial["resolution"][0] > 0
        assert initial["resolution"][1] > 0
        render_count = page.evaluate("window.modViewer.getRenderCount()")

        page.evaluate("""async () => {
          const {renderer} = await import('./js/scene/scene.js');
          const {requestRender} = await import('./js/scene/render-scheduler.js');
          renderer.setSize(900, 640);
          requestRender();
        }""")
        page.wait_for_function(
            "count => window.modViewer.getRenderCount() > count", arg=render_count)
        resized = page.evaluate("""async () => {
          const {getViewportRenderPipelineDebugState} =
            await import('./js/scene/scene.js');
          return getViewportRenderPipelineDebugState();
        }""")
        assert resized["resolution"][0] > 0
        assert resized["resolution"][1] > 0
        assert resized["resolutionScale"] == initial["resolutionScale"]
        assert not resized["pipelineNeedsUpdate"]
    finally:
        context.close()

def test_viewport_gtao_is_visible_and_does_not_add_continuous_frames(
        edge_browser, frontend_url):
    payload = _payload("AO")
    entry = payload["meshes"]["Body-AO-0"]
    entry["drawindexed"] = [12, 0, 0]
    entry["pos"] = _f32(
        -0.7, 0, -0.5, 0.7, 0, -0.5, 0.7, 0, 0.5, -0.7, 0, 0.5,
        -0.7, 0, -0.5, 0.7, 0, -0.5, 0.7, 0.9, -0.5, -0.7, 0.9, -0.5,
    )
    entry["idx"] = _u32(0, 2, 1, 0, 3, 2, 4, 5, 6, 4, 6, 7)
    context, page = _page(edge_browser, frontend_url, {"AO": payload})
    try:
        _open(page, "AO")
        page.locator(".draw-item").wait_for()
        page.wait_for_function("window.modViewer.getRenderCount() > 0")
        page.evaluate("""async () => {
          const {setAmbientOcclusionStrength} =
            await import('./js/scene/scene.js');
          setAmbientOcclusionStrength(1);
        }""")
        page.wait_for_timeout(250)
        with_ao = Image.open(io.BytesIO(page.screenshot())).convert("RGB")
        render_count = page.evaluate("window.modViewer.getRenderCount()")
        page.wait_for_timeout(200)
        assert page.evaluate("window.modViewer.getRenderCount()") == render_count

        page.evaluate("""async () => {
          const {setAmbientOcclusionEnabled} =
            await import('./js/scene/scene.js');
          setAmbientOcclusionEnabled(false);
        }""")
        page.wait_for_function(
            "count => window.modViewer.getRenderCount() > count", arg=render_count)
        without_ao = Image.open(io.BytesIO(page.screenshot())).convert("RGB")
        assert ImageChops.difference(with_ao, without_ao).getbbox()
    finally:
        context.close()

def test_direct_dds_matches_png_orientation_and_diffuse_color(
        edge_browser, frontend_url, tmp_path):
    dds_path = tmp_path / "orientation.dds"
    dds_path.write_bytes(_dxt1_vertical_gradient())
    publication = server.begin_texture_publication(str(tmp_path))
    dds_url = publication.register(str(dds_path))
    publication.commit()
    png_url = dds_url[:-4] + ".png"
    payload = _parity_payload(dds_url)
    context, page = _page(edge_browser, frontend_url, {"Parity": payload})
    try:
        _open(page, "Parity")
        page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial"
            "?.bindings?.diffuse?.textureNode?.value?.image?.width === 4")
        page.wait_for_timeout(250)
        direct_pixels = [
            _sample_mesh_pixel_at(page, 0, 0.65),
            _sample_mesh_pixel_at(page, 0, -0.65),
        ]

        page.evaluate("""async ({key, uri}) => {
          const {refreshMeshTexture, setTextures} = await import('./js/mesh/mesh-factory.js');
          const mesh = window.modViewer.activeMeshes[0];
          setTextures({[key]: uri});
          refreshMeshTexture(mesh);
        }""", {"key": "diffuse::Parity-one.png", "uri": png_url})
        page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial"
            "?.bindings?.diffuse?.textureNode?.value?.image?.width === 4"
            " && window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial"
            "?.bindings?.diffuse?.textureNode?.value?.isCompressedTexture !== true")
        page.wait_for_timeout(250)
        png_pixels = [
            _sample_mesh_pixel_at(page, 0, 0.65),
            _sample_mesh_pixel_at(page, 0, -0.65),
        ]

        assert all(
            max(abs(left - right) for left, right in zip(direct, fallback)) <= 40
            for direct, fallback in zip(direct_pixels, png_pixels)), (
                direct_pixels, png_pixels)
        assert direct_pixels[0] != direct_pixels[1]
        assert png_pixels[0] != png_pixels[1]
    finally:
        context.close()

def test_texture_stays_fallback_until_png_load_completes(
        edge_browser, frontend_url):
    payload = _payload("Delayed")
    entry = payload["meshes"]["Body-Delayed-0"]
    entry["pos"] = _f32(-1, -1, 0, 1, -1, 0, 1, 1, 0, -1, 1, 0)
    entry["idx"] = _u32(0, 1, 2, 0, 2, 3)
    entry["uv"] = _f32(0, 0, 1, 0, 1, 1, 0, 1)
    key = entry["tex_key"]
    payload["textures"][key] = f"{frontend_url}/delayed.png"
    texture_uri = _flat_png_uri((236, 42, 38, 255))
    pending = {"requests": 0, "route": None}

    context, page = _page(edge_browser, frontend_url, {"Delayed": payload})

    def hold(route):
        pending["requests"] += 1
        pending["route"] = route

    page.route("**/delayed.png", hold)
    try:
        _open(page, "Delayed")
        page.locator(".draw-item").wait_for()
        page.wait_for_function("""
          () => {
            const mesh = window.modViewer.activeMeshes[0];
            return mesh?.material?.userData?.gameMaterial
              ?.bindings?.diffuse?.enabledNode?.value === false;
          }
        """)
        page.wait_for_function("window.modViewer.getRenderCount() > 0")
        state = page.evaluate("""() => {
          const mesh = window.modViewer.activeMeshes[0];
          const game = mesh.material.userData.gameMaterial;
          return {
            diffuseEnabled: game.bindings.diffuse.enabledNode.value,
            fallbackColor: mesh.material.color.getHex(),
          };
        }""")
        assert pending["requests"] == 1
        assert state == {"diffuseEnabled": False, "fallbackColor": 0xcccccc}
        pending_pixel = _sample_mesh_pixel(page)
        assert min(pending_pixel) > 20
        assert max(pending_pixel) - min(pending_pixel) < 30

        idle_count = page.evaluate("window.modViewer.getRenderCount()")
        page.wait_for_timeout(200)
        assert page.evaluate("window.modViewer.getRenderCount()") == idle_count

        page.evaluate("window.modViewer.setEnvironmentPreset('studio')")
        page.wait_for_function(
            "count => window.modViewer.getRenderCount() > count", arg=idle_count)
        changed_count = page.evaluate("window.modViewer.getRenderCount()")

        pending["route"].fulfill(
            status=200,
            content_type="image/png",
            body=base64.b64decode(texture_uri.split(",", 1)[1]),
        )
        page.wait_for_function("""
          () => {
            const binding = window.modViewer.activeMeshes[0]?.material
              ?.userData?.gameMaterial?.bindings?.diffuse;
            return binding?.enabledNode?.value === true
              && binding.textureNode.value?.image?.width === 4;
          }
        """)
        page.wait_for_function(
            "count => window.modViewer.getRenderCount() > count", arg=changed_count)
        loaded_pixel = _sample_mesh_pixel(page)
        assert loaded_pixel[0] > loaded_pixel[1] * 1.5, loaded_pixel
        assert loaded_pixel != pending_pixel, (pending_pixel, loaded_pixel)
    finally:
        context.close()

def test_view_gizmo_snap_renders_only_during_animation(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"Snap": _payload("Snap")})
    try:
        _open(page, "Snap")
        page.locator(".draw-item").wait_for()
        page.wait_for_function("window.modViewer.getRenderCount() > 0")
        page.wait_for_timeout(200)
        idle_count = page.evaluate("window.modViewer.getRenderCount()")

        page.evaluate("""
          () => document.querySelector('.gizmo-axis.positive')
            .dispatchEvent(new KeyboardEvent('keydown', {
              key: 'Enter', bubbles: true,
            }))
        """)
        page.wait_for_function(
            "count => window.modViewer.getRenderCount() >= count + 2",
            arg=idle_count)
        page.wait_for_timeout(350)
        settled_count = page.evaluate("window.modViewer.getRenderCount()")
        page.wait_for_timeout(200)
        assert page.evaluate("window.modViewer.getRenderCount()") == settled_count
        assert settled_count > idle_count + 2
    finally:
        context.close()

def test_nonfinite_mesh_positions_do_not_poison_camera_fit(
        edge_browser, frontend_url):
    payload = _payload("InvalidGeometry")
    entry = payload["meshes"]["Body-InvalidGeometry-0"]
    entry["pos"] = _f32(
        0, 0, 0, 1, 0, 0, 0, 1, 0,
        float("nan"), float("nan"), float("nan"),
    )
    entry["idx"] = _u32(0, 1, 2)
    context, page = _page(
        edge_browser, frontend_url, {"InvalidGeometry": payload})
    try:
        _open(page, "InvalidGeometry")
        page.wait_for_function("window.modViewer.activeMeshes.length === 1")
        camera = page.evaluate("""async () => {
          const {camera, controls} = await import('./js/scene/scene.js');
          return {
            position: camera.position.toArray(),
            target: controls.target.toArray(),
            near: camera.near,
            far: camera.far,
          };
        }""")
        assert all(math.isfinite(value)
                   for value in camera["position"] + camera["target"])
        assert math.isfinite(camera["near"])
        assert math.isfinite(camera["far"])
    finally:
        context.close()

def test_shared_texture_waits_once_and_updates_all_meshes(
        edge_browser, frontend_url):
    payload = _payload("Shared")
    entry = payload["meshes"]["Body-Shared-0"]
    entry["uv"] = _f32(0, 0, 1, 0, 0, 1)
    second = copy.deepcopy(entry)
    second["component"] = "Face Shared"
    payload["meshes"]["Face-Shared-0"] = second
    key = entry["tex_key"]
    payload["textures"][key] = f"{frontend_url}/shared.png"
    pending = {"requests": 0, "route": None}

    context, page = _page(edge_browser, frontend_url, {"Shared": payload})

    def hold(route):
        pending["requests"] += 1
        pending["route"] = route

    page.route("**/shared.png", hold)
    try:
        _open(page, "Shared")
        page.wait_for_function("window.modViewer.activeMeshes.length === 2")
        page.wait_for_function("""
          () => window.modViewer.activeMeshes.every(mesh =>
            mesh.material.userData.gameMaterial.bindings.diffuse.enabledNode.value
              === false)
        """)
        assert pending["requests"] == 1

        pending["route"].fulfill(
            status=200,
            content_type="image/png",
            body=base64.b64decode(_PNG_URI.split(",", 1)[1]),
        )
        page.wait_for_function("""
          () => window.modViewer.activeMeshes.every(mesh =>
            mesh.material.userData.gameMaterial.bindings.diffuse.enabledNode.value
              === true)
        """)
    finally:
        context.close()

@pytest.mark.parametrize(("profile_id", "normal_role"), [
    ("zzz:zzmi", "normal_map"),
    ("wuwa:rabbitfx:body", "normal_data"),
])
def test_diffuse_normal_mode_keeps_only_color_and_normal_bindings(
        edge_browser, frontend_url, profile_id, normal_role):
    payload = _packed_material_payload(profile_id)
    entry = payload["meshes"]["Body-Packed-0"]
    if normal_role == "normal_map":
        normal_key = "normal_map::Packed-surface-normal.png"
        entry["normal_map_key"] = normal_key
        payload["textures"][normal_key] = _PNG_URI
    context, page = _page(edge_browser, frontend_url, {"Packed": payload})
    try:
        _open(page, "Packed")
        page.wait_for_function("""normalRole => {
          const bindings = window.modViewer.activeMeshes[0]?.material
            ?.userData?.gameMaterial?.bindings;
          return bindings?.diffuse.enabledNode.value === true
            && bindings[normalRole].enabledNode.value === true;
        }""", arg=normal_role)

        page.locator("#texture-btn").click()
        options = page.locator("#texture-popover .ui-popover-option")
        assert options.all_inner_texts() == [
            "All maps", "Diffuse and NormalMap", "Diffuse only", "No textures"]
        options.filter(has_text="Diffuse and NormalMap").click()
        bindings = page.evaluate("""() => {
          const values = window.modViewer.activeMeshes[0].material
            .userData.gameMaterial.bindings;
          return Object.fromEntries(Object.entries(values).map(
            ([role, binding]) => [role, binding.enabledNode.value]));
        }""")
        expected_bindings = {
            "diffuse": True,
            "normal_map": False,
            "normal_data": False,
            "light_map": False,
            "material_map": False,
        }
        expected_bindings[normal_role] = True
        assert bindings == expected_bindings
        button = page.locator("#texture-btn")
        assert "diffuse-normal" in (button.get_attribute("class") or "")
        assert button.get_attribute("aria-label") == (
            "Textures: diffuse and normal map")
        assert button.evaluate("button => getComputedStyle(button).color") == (
            "rgb(242, 204, 114)")
    finally:
        context.close()

def test_failed_texture_stays_fallback_without_retrying(
        edge_browser, frontend_url):
    payload = _payload("FailedTexture")
    entry = payload["meshes"]["Body-FailedTexture-0"]
    entry["uv"] = _f32(0, 0, 1, 0, 0, 1)
    key = entry["tex_key"]
    payload["textures"][key] = f"{frontend_url}/failed.png"
    pending = {"requests": 0, "route": None}

    context, page = _page(
        edge_browser, frontend_url, {"FailedTexture": payload})

    def hold(route):
        pending["requests"] += 1
        pending["route"] = route

    page.route("**/failed.png", hold)
    try:
        _open(page, "FailedTexture")
        page.locator(".draw-item").wait_for()
        page.wait_for_function("""
          () => window.modViewer.activeMeshes[0]?.material?.userData
            ?.gameMaterial?.bindings?.diffuse?.enabledNode?.value === false
        """)
        pending["route"].abort()
        page.wait_for_function(
            """async key => {
              const {hasTexture} = await import('./js/mesh/mesh-factory.js');
              return hasTexture(key) === false;
            }""",
            arg=key,
        )
        page.wait_for_timeout(100)
        assert pending["requests"] == 1
        assert page.evaluate("""() => {
          const mesh = window.modViewer.activeMeshes[0];
          return {
            enabled: mesh.material.userData.gameMaterial
              .bindings.diffuse.enabledNode.value,
            fallbackColor: mesh.material.color.getHex(),
          };
        }""") == {"enabled": False, "fallbackColor": 0xcccccc}
    finally:
        context.close()

def test_native_dds_failure_falls_back_without_black_frame(
        edge_browser, frontend_url):
    payload = _payload("NativeDDS")
    entry = payload["meshes"]["Body-NativeDDS-0"]
    entry["uv"] = _f32(0, 0, 1, 0, 0, 1)
    key = "diffuse::NativeDDS.dds"
    entry["tex_key"] = key
    payload["textures"] = {key: f"{frontend_url}/native.dds"}
    pending = {"dds": None}
    requests = {"dds": 0, "png": 0}

    context, page = _page(edge_browser, frontend_url, {"NativeDDS": payload})
    try:
        supported = page.evaluate("""
          async () => {
            const {supportsBCTextureCompression} =
              await import('./js/scene/renderer-capabilities.js');
            return supportsBCTextureCompression();
          }
        """)
        if not supported:
            pytest.skip("native DDS is not supported by the test renderer")

        def hold_dds(route):
            requests["dds"] += 1
            pending["dds"] = route

        def fulfill_png(route):
            requests["png"] += 1
            route.fulfill(
                status=200,
                content_type="image/png",
                body=base64.b64decode(_PNG_URI.split(",", 1)[1]),
            )

        page.route("**/native.dds", hold_dds)
        page.route("**/native.png", fulfill_png)
        _open(page, "NativeDDS")
        page.locator(".draw-item").wait_for()
        page.wait_for_function("""
          () => window.modViewer.activeMeshes[0]?.material?.userData
            ?.gameMaterial?.bindings?.diffuse?.enabledNode?.value === false
        """)
        assert requests == {"dds": 1, "png": 0}

        pending["dds"].abort()
        page.wait_for_function("""
          () => {
            const binding = window.modViewer.activeMeshes[0]?.material
              ?.userData?.gameMaterial?.bindings?.diffuse;
            return binding?.enabledNode?.value === true
              && binding.textureNode.value?.isCompressedTexture !== true
              && binding.textureNode.value?.image?.width === 1;
          }
        """)
        assert requests == {"dds": 1, "png": 1}
    finally:
        context.close()

def test_replaced_pending_texture_ignores_stale_completion(
        edge_browser, frontend_url):
    payload = _payload("Replacement")
    entry = payload["meshes"]["Body-Replacement-0"]
    entry["uv"] = _f32(0, 0, 1, 0, 0, 1)
    key = entry["tex_key"]
    payload["textures"][key] = f"{frontend_url}/old.png"
    pending = {"old": None}
    requests = {"old": 0, "new": 0}

    context, page = _page(edge_browser, frontend_url, {"Replacement": payload})
    try:
        def hold_old(route):
            requests["old"] += 1
            pending["old"] = route

        def fulfill_new(route):
            requests["new"] += 1
            route.fulfill(
                status=200,
                content_type="image/png",
                body=base64.b64decode(_PNG_URI.split(",", 1)[1]),
            )

        page.route("**/old.png", hold_old)
        page.route("**/new.png", fulfill_new)
        _open(page, "Replacement")
        page.locator(".draw-item").wait_for()
        page.wait_for_function("""
          () => window.modViewer.activeMeshes[0]?.material?.userData
            ?.gameMaterial?.bindings?.diffuse?.enabledNode?.value === false
        """)
        assert requests == {"old": 1, "new": 0}

        page.evaluate("""async ({key, uri}) => {
          const {addTexture, refreshMeshTexture} =
            await import('./js/mesh/mesh-factory.js');
          const mesh = window.modViewer.activeMeshes[0];
          addTexture(key, uri);
          refreshMeshTexture(mesh);
        }""", {"key": key, "uri": f"{frontend_url}/new.png"})
        page.wait_for_function("""
          () => window.modViewer.activeMeshes[0]?.material?.userData
            ?.gameMaterial?.bindings?.diffuse?.enabledNode?.value === true
        """)
        assert requests == {"old": 1, "new": 1}
        page.evaluate("""() => {
          window.__replacementTexture = window.modViewer.activeMeshes[0]
            .material.userData.gameMaterial.bindings.diffuse.textureNode.value;
        }""")

        pending["old"].fulfill(
            status=200,
            content_type="image/png",
            body=base64.b64decode(_flat_png_uri((255, 0, 0, 255))
                                 .split(",", 1)[1]),
        )
        page.wait_for_timeout(200)
        assert page.evaluate("""
          () => {
            const binding = window.modViewer.activeMeshes[0].material
              .userData.gameMaterial.bindings.diffuse;
            return binding.enabledNode.value === true
              && binding.textureNode.value === window.__replacementTexture;
          }
        """)
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

def test_packed_material_profile_uses_tsl_nodes_and_stable_bindings(
        edge_browser, frontend_url):
    profile_id = "zzz:zzmi"
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
        assert state["lightMap"] and state["materialMap"] \
            if profile_id == "zzz:zzmi" else state["lightMap"]

        after = page.evaluate("""async () => {
          const {setMeshTextureState} = await import('./js/mesh/mesh-factory.js');
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
             "lightMap": True, "materialMap": True},
            {"profileId": "genshin:gimi", "kind": "face",
             "lightMap": True, "materialMap": False},
        ]
        assert page.evaluate("window.modViewer.getMaterialState(1).profileId") == (
            "genshin:gimi")
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
        page.wait_for_function("""
          () => {
            const binding = window.modViewer.activeMeshes[0]?.material
              ?.userData?.gameMaterial?.bindings?.normal_data;
            return binding?.enabledNode?.value === true
              && binding.textureNode.value?.image?.width === 4;
          }
        """)
        page.wait_for_timeout(300)
        low_pixel = _sample_mesh_pixel(page)
        page.evaluate("""async key => {
          const {setMeshTextureState} = await import('./js/mesh/mesh-factory.js');
          const mesh = window.modViewer.activeMeshes[0];
          window.__normalLowTexture = mesh.material.userData.gameMaterial
            .bindings.normal_data.textureNode.value;
          setMeshTextureState(mesh, {
            diffuse: mesh.userData.texKey,
            normal_map: null,
            normal_data: key,
            light_map: null,
            material_map: null,
          });
        }""", normal_high)
        page.wait_for_function("""
          () => {
            const binding = window.modViewer.activeMeshes[0]?.material
              ?.userData?.gameMaterial?.bindings?.normal_data;
            return binding?.enabledNode?.value === true
              && binding.textureNode.value !== window.__normalLowTexture
              && binding.textureNode.value?.image?.width === 4;
          }
        """)
        page.wait_for_timeout(300)
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
        page.locator("#inspector-tab").click()
        page.locator(".group-hdr .group-name").first.click()
        assert page.locator(".inspector-material-kind-control").count() == 1
        assert page.locator(".draw-item .material-kind-select").count() == 0
        layout = page.evaluate("""
          () => {
            const header = document.querySelector('.group-hdr');
            const name = header.querySelector('.group-name');
            const select = header.querySelector('.material-kind-select');
            const texture = header.querySelector('.group-tex-btn');
            return {
              hasMaterialLabel: !!header.querySelector('label'),
              hasMaterialControl: !!select,
              hasTextureControl: !!texture,
              nameTitle: name.title,
              nameOverflow: getComputedStyle(name).textOverflow,
              nameWhiteSpace: getComputedStyle(name).whiteSpace,
            };
          }
        """)
        assert layout == {
            "hasMaterialLabel": False,
            "hasMaterialControl": False,
            "hasTextureControl": False,
            "nameTitle": "Body Packed",
            "nameOverflow": "ellipsis",
            "nameWhiteSpace": "nowrap",
        }
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
            normalSource: game.normalSource,
            normalPacking: game.normalPacking,
            normalMapKey: mesh.userData.normalMapKey,
            stockNormalMap: mesh.material.normalMap === null,
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
            "normalSource": "normal_data",
            "normalPacking": "rg",
            "normalMapKey": None,
            "stockNormalMap": True,
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
          const {setMeshTextureState} = await import('./js/mesh/mesh-factory.js');
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
            const {scene, controls} = await import('./js/scene/scene.js');
            const {requestRender} = await import('./js/scene/render-scheduler.js');
            scene.traverse(object => {
              if (object.isAmbientLight || object.isHemisphereLight) object.intensity = 0;
              if (object.isSprite || object.isGridHelper) object.visible = false;
            });
            const key = scene.children.find(object => object.isDirectionalLight);
            key.target.position.copy(controls.target);
            key.position.copy(controls.target).add(new THREE.Vector3(0, 0, 3));
            key.intensity = 3;
            requestRender();
          }
        """)
        page.wait_for_timeout(400)
        low_pixels = [_sample_mesh_pixel_at(page, x, 0.1)
                      for x in (0.1, 0.3, 0.5, 0.7, 0.9)]
        page.evaluate("""async key => {
          const {setMeshTextureState} = await import('./js/mesh/mesh-factory.js');
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

def test_wuwa_body_missing_toon_mask_keeps_physical_direct_specular(
        edge_browser, frontend_url):
    body_payload = _packed_material_payload("wuwa:rabbitfx:body")
    entry = body_payload["meshes"]["Body-Packed-0"]
    body_payload["textures"] = {
        key: _flat_png_uri((255, 255, 255, 255))
        for key in body_payload["textures"]
    }
    body_payload["textures"]["diffuse::Packed-one.png"] = _flat_png_uri(
        (80, 80, 80, 255))
    low_mask_key = "normal_data::Packed-body-no-toon-mask.png"
    body_payload["textures"][low_mask_key] = _flat_png_uri((128, 128, 0, 0))
    entry["normal_data_key"] = low_mask_key
    endpoint_light_key = entry["light_map_key"]
    body_payload["textures"][endpoint_light_key] = _flat_png_uri(
        (255, 0, 0, 255))

    base_payload = copy.deepcopy(body_payload)
    base_entry = base_payload["meshes"]["Body-Packed-0"]
    base_entry["material_profile_id"] = "wuwa:rabbitfx"
    base_entry["light_map_key"] = None
    base_profile = material_profile_for("wuwa", "rabbitfx").to_metadata()
    base_payload["metadata"]["material_profiles"] = {
        base_profile["id"]: base_profile,
    }

    def render(payload):
        context, page = _page(edge_browser, frontend_url, {"Packed": payload})
        try:
            _open(page, "Packed")
            page.wait_for_function(
                "window.modViewer.activeMeshes[0]?.material?.userData"
                "?.gameMaterial?.bindings.normal_data.enabledNode.value === true")
            page.evaluate("""
              async () => {
                const THREE = await import('three');
                const {scene, controls} = await import('./js/scene/scene.js');
                const {requestRender} = await import('./js/scene/render-scheduler.js');
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
                key.intensity = 3;
                window.modViewer.activeMeshes[0].material.roughness = 0.2;
                requestRender();
              }
            """)
            page.wait_for_timeout(400)
            return _sample_mesh_pixel(page)
        finally:
            context.close()

    packed_pixel = render(body_payload)
    physical_pixel = render(base_payload)
    assert abs(sum(packed_pixel) - sum(physical_pixel)) <= 3, (
        packed_pixel, physical_pixel)

def test_wuwa_packed_rg_normal_matches_derived_reference_and_y_sign(
        edge_browser, frontend_url):
    # Keep the source constant so the comparison exercises normal decoding,
    # not texture filtering or mip selection.  These are the same channels
    # used to build the old CPU-derived RGB reference.
    red, green = 160, 192
    x = red / 127.5 - 1.0
    y = green / 127.5 - 1.0
    z = max(0.0, 1.0 - x * x - y * y) ** 0.5
    blue = round((z * 0.5 + 0.5) * 255.0)
    diffuse_uri = _flat_png_uri((120, 120, 120, 255))
    packed_uri = _flat_png_uri((red, green, 17, 241))
    derived_uri = _flat_png_uri((red, green, blue, 255))

    def configure_light(page):
        page.evaluate("""
          async () => {
                const THREE = await import('three');
                const {scene, controls} = await import('./js/scene/scene.js');
                const {requestRender} = await import('./js/scene/render-scheduler.js');
            let key = null;
            scene.traverse(object => {
              if (object.isAmbientLight || object.isHemisphereLight) {
                object.intensity = 0;
              } else if (object.isSprite || object.isGridHelper) {
                object.visible = false;
              } else if (object.isDirectionalLight) {
                if (!key) key = object;
                else object.intensity = 0;
              }
            });
            key.target.position.copy(controls.target);
            key.position.copy(controls.target)
              .add(new THREE.Vector3(0.8, 0.4, 2.0));
            key.intensity = 1;
            requestRender();
          }
        """)
        page.wait_for_timeout(400)

    def sample_quad(page):
        return [
            _sample_mesh_pixel_at(page, x, y)
            for x, y in ((0.15, 0.15), (0.5, 0.15), (0.85, 0.15),
                         (0.15, 0.85), (0.85, 0.85))
        ]

    reference = _parity_payload(diffuse_uri)
    reference_entry = reference["meshes"]["Body-Parity-0"]
    reference_key = "normal_map::Parity-reference.png"
    reference_entry["normal_map_key"] = reference_key
    reference["textures"][reference_key] = derived_uri

    reference_context, reference_page = _page(
        edge_browser, frontend_url, {"Reference": reference})
    packed_context = None
    try:
        _open(reference_page, "Reference")
        reference_page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData"
            "?.gameMaterial?.bindings.normal_map.textureNode.value.image"
            "?.width === 4")
        configure_light(reference_page)
        reference_pixels = sample_quad(reference_page)

        packed = _packed_material_payload("wuwa:raw")
        packed_entry = packed["meshes"]["Body-Packed-0"]
        packed_entry["drawindexed"] = [6, 0, 0]
        packed_entry["pos"] = _f32(
            -1, -1, 0, 1, -1, 0, 1, 1, 0, -1, 1, 0)
        packed_entry["uv"] = _f32(0, 0, 1, 0, 1, 1, 0, 1)
        packed_entry["idx"] = _u32(0, 1, 2, 0, 2, 3)
        packed_entry["normal_data_key"] = "normal_data::Packed-reference.png"
        packed["textures"] = {
            "diffuse::Packed-one.png": diffuse_uri,
            packed_entry["normal_data_key"]: packed_uri,
        }
        packed_context, packed_page = _page(
            edge_browser, frontend_url, {"Packed": packed})
        _open(packed_page, "Packed")
        packed_page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData"
            "?.gameMaterial?.bindings.normal_data.textureNode.value.image"
            "?.width === 4")
        configure_light(packed_page)
        packed_pixels = sample_quad(packed_page)

        assert all(
            max(abs(a - b) for a, b in zip(reference_pixel, packed_pixel)) <= 8
            for reference_pixel, packed_pixel
            in zip(reference_pixels, packed_pixels)
        ), (reference_pixels, packed_pixels, (red, green, blue))

        packed_page.evaluate("""
          async () => {
            const {setMeshTextureState} = await import('./js/mesh/mesh-factory.js');
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
        packed_page.wait_for_timeout(300)
        geometry_pixels = sample_quad(packed_page)
        assert any(
            sum(abs(a - b) for a, b in zip(packed_pixel, geometry_pixel)) > 6
            for packed_pixel, geometry_pixel
            in zip(packed_pixels, geometry_pixels)
        ), (packed_pixels, geometry_pixels)

        packed_page.evaluate("""
          async key => {
            const {setMeshTextureState} = await import('./js/mesh/mesh-factory.js');
            const mesh = window.modViewer.activeMeshes[0];
            mesh.userData.normalMapYSign = 1;
            setMeshTextureState(mesh, {
              diffuse: mesh.userData.texKey,
              normal_map: null,
              normal_data: key,
              light_map: null,
              material_map: null,
            });
          }
        """, packed_entry["normal_data_key"])
        packed_page.wait_for_timeout(300)
        positive_y_pixels = sample_quad(packed_page)
        assert any(
            sum(abs(a - b) for a, b in zip(packed_pixel, positive_pixel)) > 6
            for packed_pixel, positive_pixel
            in zip(packed_pixels, positive_y_pixels)
        ), (packed_pixels, positive_y_pixels)
    finally:
        reference_context.close()
        if packed_context is not None:
            packed_context.close()

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
    payload["textures"][entry["normal_data_key"]] = _flat_png_uri(
        (128, 128, 255, 255))
    # Keep the low value below the shadow cutoff but above the invalid
    # endpoint tolerance; an exact zero is intentionally treated as absent.
    payload["textures"][low_key] = _flat_png_uri((0, 16, 0, 255))
    entry["light_map_key"] = low_key

    context, page = _page(edge_browser, frontend_url, {"Packed": payload})
    try:
        _open(page, "Packed")
        page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial"
            "?.bindings.light_map.enabledNode.value === true")
        page.wait_for_function("""
          () => {
            const game = window.modViewer.activeMeshes[0]?.material
              ?.userData?.gameMaterial;
            return game?.bindings?.diffuse?.enabledNode?.value === true
              && game.bindings.normal_data.enabledNode.value === true
              && game.bindings.light_map.enabledNode.value === true;
          }
        """)
        page.evaluate("""
          async () => {
            const THREE = await import('three');
            const {scene, controls} = await import('./js/scene/scene.js');
            const {requestRender} = await import('./js/scene/render-scheduler.js');
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
            requestRender();
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
          const {setMeshTextureState} = await import('./js/mesh/mesh-factory.js');
          const mesh = window.modViewer.activeMeshes[0];
          setMeshTextureState(mesh, {
            diffuse: mesh.userData.texKey,
            normal_map: null,
            normal_data: mesh.userData.normalDataKey,
            light_map: null,
            material_map: null,
          });
        }""")
        page.wait_for_function("""
          () => window.modViewer.activeMeshes[0]?.material?.userData
            ?.gameMaterial?.bindings?.light_map?.enabledNode?.value === false
        """)
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
        page.locator("#inspector-tab").click()
        page.locator(".group-hdr .group-name").first.click()
        page.locator(".draw-item").first.click()
        page.evaluate("""() => {
          window.__meshStateEvents = 0;
          window.addEventListener('mod-viewer-mesh-state-changed', () => {
            window.__meshStateEvents += 1;
          });
        }""")
        page.locator(".inspector-texture-option", has_text="A two").click()
        assert page.evaluate("window.__meshStateEvents") == 1
        assert page.evaluate(
            "window.modViewer.activeMeshes[0].userData.manualTexOverride"
            " === 'diffuse::A-two.png'")
        page.evaluate("window.__textureRows = [...document.querySelectorAll('.inspector-texture-option')]")

        page.locator("#controls-tab").click()
        page.locator("#toggle-list .toggle-cycle-btn").click()
        assert page.evaluate("window.__textureRows.every((row, i) => row === document.querySelectorAll('.inspector-texture-option')[i])")
        page.locator("#menu-list .toggle-cycle-btn").click()
        assert page.evaluate("window.__textureRows.every((row, i) => row === document.querySelectorAll('.inspector-texture-option')[i])")
        page.locator("#menu-list .menu-slider").evaluate(
            "input => { input.value = '0.5'; input.dispatchEvent(new Event('input', {bubbles: true})); }")
        assert page.evaluate("window.__textureRows.every((row, i) => row === document.querySelectorAll('.inspector-texture-option')[i])")

        page.locator("#inspector-tab").click()
        page.locator(".inspector-manage-textures").click()
        page.locator("#texm-add").click()
        page.wait_for_function("document.querySelectorAll('.inspector-texture-option').length === 5")
        assert not page.evaluate("window.__textureRows[0] === document.querySelector('.inspector-texture-option')")
        page.evaluate("window.__textureRows = [...document.querySelectorAll('.inspector-texture-option')]")
        page.locator(".texm-row .toggle-icon-btn").last.click()
        page.wait_for_function("document.querySelectorAll('.inspector-texture-option').length === 4")
        assert not page.evaluate("window.__textureRows[0] === document.querySelector('.inspector-texture-option')")
    finally:
        context.close()

def test_authored_normals_survive_render_modes_and_neutral_shape(
        edge_browser, frontend_url):
    payload = _payload("Normals")
    entry = payload["meshes"]["Body-Normals-0"]
    entry["normal"] = _f32(1, 0, 0, 0, 1, 0, 0, 0, 1)
    entry["shape_targets"] = [{
        "var": "shape",
        "pos": _f32(0, 0, 0, 1, 0, 1, 0, 1, 0),
    }]
    context, page = _page(edge_browser, frontend_url, {"Normals": payload})
    try:
        _open(page, "Normals")
        page.locator(".draw-item").wait_for()
        initial = page.evaluate("""() => {
          const mesh = window.modViewer.activeMeshes[0];
          return {
            normals: [...mesh.geometry.attributes.normal.array],
            authored: mesh.userData.hasAuthoredNormals,
            base: [...mesh.userData.baseNormals],
            flat: mesh.material.flatShading,
          };
        }""")
        assert initial["authored"]
        assert initial["normals"] == initial["base"]
        assert not initial["flat"]

        page.locator("#shading-btn").click()
        shaded = page.evaluate("""() => {
          const mesh = window.modViewer.activeMeshes[0];
          return {
            normals: [...mesh.geometry.attributes.normal.array],
            flat: mesh.material.flatShading,
          };
        }""")
        assert shaded["flat"]
        assert shaded["normals"] == initial["normals"]

        page.evaluate("""async () => {
          const {setControlValue} = await import('./js/editing/control-state.js');
          const {refreshMeshes} = await import('./js/mesh/mesh-state.js');
          setControlValue('shape', '1');
          refreshMeshes();
        }""")
        deformed = page.evaluate(
            "() => [...window.modViewer.activeMeshes[0].geometry.attributes.normal.array]")
        assert deformed != initial["normals"]

        page.evaluate("""async () => {
          const {setControlValue} = await import('./js/editing/control-state.js');
          const {refreshMeshes} = await import('./js/mesh/mesh-state.js');
          setControlValue('shape', '0');
          refreshMeshes();
        }""")
        restored = page.evaluate(
            "() => [...window.modViewer.activeMeshes[0].geometry.attributes.normal.array]")
        assert restored == initial["normals"]
    finally:
        context.close()

def test_glossy_tool_applies_to_all_mesh_materials(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"A": _payload("A")})
    try:
        _open(page, "A")
        page.locator(".draw-item").wait_for()
        assert page.locator("#glossy-btn").get_attribute("class") == "tool-btn off"
        assert page.locator("#glossy-btn").get_attribute("aria-pressed") == "false"
        before = page.evaluate("""
          () => window.modViewer.activeMeshes.map(mesh => mesh.material.roughness)
        """)
        assert before == [1]

        page.locator("#glossy-btn").click()
        glossy = page.evaluate("""
          () => window.modViewer.activeMeshes.map(mesh => mesh.material.roughness)
        """)
        assert glossy == [0.2]
        assert page.locator("#glossy-btn").get_attribute("aria-label") == (
            "Glossy materials: on")
        assert "off" not in (page.locator("#glossy-btn").get_attribute("class") or "")
        assert page.locator("#glossy-btn").get_attribute("aria-pressed") == "true"

        page.locator("#glossy-btn").click()
        restored = page.evaluate("""
          () => window.modViewer.activeMeshes.map(mesh => mesh.material.roughness)
        """)
        assert restored == [1]
        assert "off" in (page.locator("#glossy-btn").get_attribute("class") or "")
        assert page.locator("#glossy-btn").get_attribute("aria-pressed") == "false"
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

def test_wuwa_models_start_with_a_180_degree_base_turn(
        edge_browser, frontend_url):
    payload = _payload("WuWa")
    payload["metadata"]["game"] = {
        "id": "wuwa", "runtime": "wwmi", "texture_api": "raw",
        "confidence": "high",
    }
    context, page = _page(edge_browser, frontend_url, {"WuWa": payload})
    try:
        _open(page, "WuWa")
        page.locator(".draw-item").wait_for()

        initial = page.evaluate("""() => ({
          position: window.modViewer.activeMeshes[0].position.toArray(),
          quaternion: window.modViewer.activeMeshes[0].quaternion.toArray(),
        })""")
        assert initial["quaternion"] == pytest.approx([0, 1, 0, 0])

        page.locator("#camera-reset-view-btn").click()
        reset = page.evaluate("""() => ({
          position: window.modViewer.activeMeshes[0].position.toArray(),
          quaternion: window.modViewer.activeMeshes[0].quaternion.toArray(),
        })""")
        assert reset["position"] == pytest.approx(initial["position"])
        assert reset["quaternion"] == pytest.approx(initial["quaternion"])

        reloaded = page.evaluate("""async () => {
          await window.modViewer.reloadCurrentMod();
          return window.modViewer.activeMeshes[0].quaternion.toArray();
        }""")
        assert reloaded == pytest.approx([0, 1, 0, 0])
    finally:
        context.close()

def test_conditional_only_texture_survives_component_run_reconciliation(
        edge_browser, frontend_url):
    payload = _payload("ConditionalOnly")
    entry = payload["meshes"]["Body-ConditionalOnly-0"]
    entry["tex_key"] = None
    entry["texture_variants"] = [{
        "conditions": [[{
            "var": "menu", "value": "0", "negate": False,
        }]],
        "tex_key": "diffuse::ConditionalOnly-two.png",
    }]
    context, page = _page(
        edge_browser, frontend_url, {"ConditionalOnly": payload})
    try:
        _open(page, "ConditionalOnly")
        page.locator(".draw-item").wait_for()
        assert page.evaluate("window.modViewer.activeMeshes[0].userData.resolvedTexKey") == \
            "diffuse::ConditionalOnly-two.png"
        assert page.evaluate("window.modViewer.activeMeshes[0].userData.texKey") == \
            "diffuse::ConditionalOnly-two.png"
    finally:
        context.close()


def test_cached_model_bounds_do_not_rescan_positions(edge_browser, frontend_url):
    context = edge_browser.new_context(bypass_csp=True)
    page = context.new_page()
    try:
        page.goto(frontend_url)
        page.locator("#open-btn:not([disabled])").wait_for(timeout=10000)
        state = page.evaluate("""async () => {
          const THREE = await import('three');
          const {expandByModelMesh} = await import('./js/scene/model-bounds.js');
          const geometry = new THREE.BufferGeometry();
          const positions = new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]);
          geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
          const mesh = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial());
          const initial = new THREE.Box3();
          expandByModelMesh(initial, mesh);
          positions[0] = Infinity;
          const cached = new THREE.Box3();
          expandByModelMesh(cached, mesh);
          return {
            initialEmpty: initial.isEmpty(),
            cachedEmpty: cached.isEmpty(),
            boundsShared: cached.equals(initial),
          };
        }""")
        assert state == {
            "initialEmpty": False, "cachedEmpty": False, "boundsShared": True,
        }
    finally:
        context.close()

def test_character_shadows_are_on_demand_and_visibility_keeps_stable_ground(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"Shadow": _payload("Shadow")})
    try:
        _open(page, "Shadow")
        page.locator(".draw-item").wait_for()
        page.wait_for_function("""async () => {
          const {getCharacterShadowDebugState} = await import('./js/scene/scene.js');
          return getCharacterShadowDebugState().shadowUpdateCount > 0;
        }""")
        initial = page.evaluate("""async () => {
          const {getCharacterShadowDebugState, renderer, scene} =
            await import('./js/scene/scene.js');
          const ground = [];
          scene.traverse(object => { if (object.userData.isViewerGround) ground.push(object); });
          return {
            enabled: renderer.shadowMap.enabled,
            ground: ground.map(object => ({
              receive: object.receiveShadow, cast: object.castShadow,
              shadowMaterial: object.material.isShadowMaterial,
              y: object.position.y,
            })),
            debug: getCharacterShadowDebugState(),
          };
        }""")
        assert initial["enabled"]
        assert initial["ground"] == [{
            "receive": True, "cast": False, "shadowMaterial": True,
            "y": initial["ground"][0]["y"],
        }]
        assert initial["debug"]["modelBounds"] is not None
        assert initial["debug"]["casterBounds"] is not None

        updated = page.evaluate("""async () => {
          const {applyMeshVisibility} = await import('./js/mesh/mesh-state.js');
          const {getCharacterShadowDebugState} = await import('./js/scene/scene.js');
          const mesh = window.modViewer.activeMeshes[0];
          mesh.userData.manualVisible = false;
          applyMeshVisibility(mesh);
          return getCharacterShadowDebugState();
        }""")
        page.wait_for_function(
            "previous => window.modViewer.getRenderCount() > previous",
            arg=page.evaluate("window.modViewer.getRenderCount()"))
        after = page.evaluate("""async () => {
          const {getCharacterShadowDebugState, scene} = await import('./js/scene/scene.js');
          let ground = null;
          scene.traverse(object => { if (object.userData.isViewerGround) ground = object; });
          return {debug: getCharacterShadowDebugState(), groundY: ground.position.y};
        }""")
        assert after["debug"]["fitCount"] > updated["fitCount"]
        assert after["debug"]["shadowUpdateCount"] > updated["shadowUpdateCount"]
        assert after["groundY"] == pytest.approx(initial["ground"][0]["y"])
    finally:
        context.close()


def test_wireframe_toggles_rim_uniform_without_rebuilding_material(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"Rim": _payload("Rim")})
    try:
        _open(page, "Rim")
        page.locator(".draw-item").wait_for()
        initial = page.evaluate("""() => {
          const material = window.modViewer.activeMeshes[0].material;
          window.__rimMaterial = material;
          const state = material.userData.gameMaterial;
          return {
            version: material.version,
            enabled: state.rimEnabledNode.value,
            strength: state.rimStrengthNode.value,
            power: state.rimPowerNode.value,
          };
        }""")
        assert initial["enabled"] is True
        assert initial["strength"] > 0
        assert initial["power"] > 0
        page.locator("#wire-btn").click()
        disabled = page.evaluate("""() => {
          const material = window.modViewer.activeMeshes[0].material;
          return {same: material === window.__rimMaterial, version: material.version,
            enabled: material.userData.gameMaterial.rimEnabledNode.value};
        }""")
        assert disabled == {"same": True, "version": initial["version"], "enabled": False}
        page.locator("#wire-btn").click()
        assert page.evaluate(
            "window.modViewer.activeMeshes[0].material.userData.gameMaterial.rimEnabledNode.value")
    finally:
        context.close()


def test_key_light_mode_changes_restore_ground_without_refitting_shadows(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"LightMode": _payload("LightMode")})
    try:
        _open(page, "LightMode")
        page.locator(".draw-item").wait_for()
        page.wait_for_function("""async () => {
          const {getCharacterShadowDebugState} = await import('./js/scene/scene.js');
          return getCharacterShadowDebugState().groundVisible;
        }""")
        before = page.evaluate("""async () => {
          const {getCharacterShadowDebugState, setLightMode} = await import('./js/scene/scene.js');
          setLightMode('off');
          return getCharacterShadowDebugState();
        }""")
        page.wait_for_function("""async () => {
          const {getCharacterShadowDebugState} = await import('./js/scene/scene.js');
          return !getCharacterShadowDebugState().groundVisible;
        }""")
        off = page.evaluate("""async () => {
          const {getCharacterShadowDebugState, setLightMode} = await import('./js/scene/scene.js');
          setLightMode('current');
          return getCharacterShadowDebugState();
        }""")
        page.wait_for_function("""async () => {
          const {getCharacterShadowDebugState} = await import('./js/scene/scene.js');
          return getCharacterShadowDebugState().groundVisible;
        }""")
        restored = page.evaluate("""async () => {
          const {getCharacterShadowDebugState} = await import('./js/scene/scene.js');
          return getCharacterShadowDebugState();
        }""")
        assert off["fitCount"] == before["fitCount"]
        assert off["shadowUpdateCount"] == before["shadowUpdateCount"]
        assert restored["fitCount"] == before["fitCount"]
        assert restored["shadowUpdateCount"] == before["shadowUpdateCount"]
    finally:
        context.close()


@pytest.mark.parametrize("scale", [0.01, 1, 100])
def test_shadow_fit_and_bias_scale_with_model_size(edge_browser, frontend_url, scale):
    label = f"ShadowScale{scale}"
    payload = _payload(label)
    entry = payload["meshes"][f"Body-{label}-0"]
    entry["pos"] = _f32(0, 0, 0, scale, 0, 0, 0, scale, scale * 0.25)
    context, page = _page(edge_browser, frontend_url, {label: payload})
    try:
        _open(page, label)
        page.locator(".draw-item").wait_for()
        state = page.evaluate("""async () => {
          const {getCharacterShadowDebugState, scene} = await import('./js/scene/scene.js');
          const light = scene.children.find(object => object.isDirectionalLight);
          const camera = light.shadow.camera;
          return {
            debug: getCharacterShadowDebugState(),
            camera: [camera.left, camera.right, camera.bottom, camera.top,
              camera.near, camera.far],
          };
        }""")
        assert all(math.isfinite(value) for value in state["camera"])
        left, right, bottom, top, near, far = state["camera"]
        assert left < right and bottom < top and near < far
        bounds = state["debug"]["modelBounds"]
        diagonal = math.dist(bounds["min"], bounds["max"])
        assert state["debug"]["normalBias"] == pytest.approx(
            max(diagonal, 0.001) * 0.0015)
    finally:
        context.close()


def test_shape_and_view_changes_refit_character_shadows(edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"ShadowShape": _payload("ShadowShape")})
    try:
        _open(page, "ShadowShape")
        page.locator(".draw-item").wait_for()
        initial = page.evaluate("""async () => {
          const {getCharacterShadowDebugState} = await import('./js/scene/scene.js');
          return getCharacterShadowDebugState().fitCount;
        }""")
        page.evaluate("""async () => {
          const {setControlValue} = await import('./js/editing/control-state.js');
          const {refreshMeshes} = await import('./js/mesh/mesh-state.js');
          setControlValue('shape', '1');
          refreshMeshes();
        }""")
        page.wait_for_function("""async count => {
          const {getCharacterShadowDebugState} = await import('./js/scene/scene.js');
          return getCharacterShadowDebugState().fitCount > count;
        }""", arg=initial)
        after_shape = page.evaluate("""async () => {
          const {getCharacterShadowDebugState} = await import('./js/scene/scene.js');
          return getCharacterShadowDebugState().fitCount;
        }""")
        for button_id in ("#camera-flip-btn", "#camera-flip-horizontal-btn", "#camera-reset-view-btn"):
            page.locator(button_id).click()
            page.wait_for_function("""async count => {
              const {getCharacterShadowDebugState} = await import('./js/scene/scene.js');
              return getCharacterShadowDebugState().fitCount > count;
            }""", arg=after_shape)
            after_shape = page.evaluate("""async () => {
              const {getCharacterShadowDebugState} = await import('./js/scene/scene.js');
              return getCharacterShadowDebugState().fitCount;
            }""")
    finally:
        context.close()


def test_camera_motion_reuses_shadow_map_but_light_motion_updates_it(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"ShadowMotion": _payload("ShadowMotion")})
    try:
        _open(page, "ShadowMotion")
        page.locator(".draw-item").wait_for()
        page.wait_for_function("""async () => {
          const {getCharacterShadowDebugState} = await import('./js/scene/scene.js');
          return getCharacterShadowDebugState().groundVisible;
        }""")
        baseline = page.evaluate("""async () => {
          const {getCharacterShadowDebugState} = await import('./js/scene/scene.js');
          return getCharacterShadowDebugState();
        }""")
        render_count = page.evaluate("window.modViewer.getRenderCount()")
        page.evaluate("""async () => {
          const {camera} = await import('./js/scene/scene.js');
          const {requestRender} = await import('./js/scene/render-scheduler.js');
          camera.position.x += 0.2;
          requestRender();
        }""")
        page.wait_for_function(
            "count => window.modViewer.getRenderCount() > count", arg=render_count)
        after_camera = page.evaluate("""async () => {
          const {getCharacterShadowDebugState} = await import('./js/scene/scene.js');
          return getCharacterShadowDebugState();
        }""")
        assert after_camera["fitCount"] == baseline["fitCount"]
        assert after_camera["shadowUpdateCount"] == baseline["shadowUpdateCount"]

        page.evaluate("""async () => {
          const THREE = await import('three');
          const {scene} = await import('./js/scene/scene.js');
          const {requestRender} = await import('./js/scene/render-scheduler.js');
          scene.children.find(object => object.isDirectionalLight)
            .position.add(new THREE.Vector3(0.25, 0, 0));
          requestRender();
        }""")
        page.wait_for_function("""async state => {
          const {getCharacterShadowDebugState} = await import('./js/scene/scene.js');
          const next = getCharacterShadowDebugState();
          return next.fitCount > state.fitCount
            && next.shadowUpdateCount > state.shadowUpdateCount;
        }""", arg=after_camera)
    finally:
        context.close()


def test_debug_output_bypasses_viewer_rim_lighting(edge_browser, frontend_url):
    payload = _packed_material_payload("wuwa:rabbitfx")
    context, page = _page(edge_browser, frontend_url, {"DebugRim": payload})
    try:
        _open(page, "DebugRim")
        page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial")
        page.evaluate("""async () => {
          const {requestRender} = await import('./js/scene/render-scheduler.js');
          const game = window.modViewer.activeMeshes[0].material.userData.gameMaterial;
          game.rimStrengthNode.value = 0;
          window.modViewer.setMaterialDebugMode('normal-data-b');
          requestRender();
        }""")
        page.wait_for_timeout(250)
        without_rim = _sample_mesh_pixel(page)
        page.evaluate("""async () => {
          const {requestRender} = await import('./js/scene/render-scheduler.js');
          window.modViewer.activeMeshes[0].material.userData.gameMaterial
            .rimStrengthNode.value = 20;
          requestRender();
        }""")
        page.wait_for_timeout(250)
        with_rim = _sample_mesh_pixel(page)
        assert with_rim == pytest.approx(without_rim, abs=2)
    finally:
        context.close()

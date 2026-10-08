"""Real WebGPU output and control visibility with generated inputs."""

import base64
import io
import struct

from PIL import Image
from app.assets.loader import AssetLoadResult, AssetMeshPart
from app.assets.loader.models import AssetTexture
from app.runtime import server
from core.geometry.transport import GeometryBlob
from tests.support.dds_data import dx10_dds, mode6_block

from .payloads import append_stream, model_payload, solid_texture, split_color_dds, textured_payload, weighted_payload
from .support import bridge_calls, mesh_pixel, mesh_pixels, open_model, project_mesh_points, wait_loaded, wait_texture


def _open_fixture(viewer, payload, extra=None):
    page = viewer({'fixture-01': payload, **(extra or {})})
    open_model(page, 'fixture-01')
    wait_loaded(page, len(payload['meshes']))
    return page


def _activate_rig(page, *, humanoid=False):
    if humanoid:
        page.evaluate("""() => {
          window.modViewer.activeMeshes[0].userData.humanoidRestPositions = new Float32Array([
            -1, 0, 0, 1, 0, 0, 0, 1, 0, 0, 2, 0, -1, 1.5, 0, 1, 1.5, 0,
          ]);
        }""")
    page.locator('#weight-rig-tab').click()
    page.evaluate("""async () => {
      const {weightRigApi} = await import('./js/weight-rig/weight-rig-core.js');
      window.__rigApi = weightRigApi;
    }""")
    page.wait_for_function('window.__rigApi.getModelRigState().loaded')


def test_webgpu_texture_and_visibility_reach_the_frame(viewer):
    payload = model_payload()
    payload['meshes']['mesh-00'].update(
        tex_key='diffuse::texture-01.png', texture_pool_id='pool-01',
        conditions=[[{'var': 'input01', 'value': '1', 'negate': False}]])
    payload['textures'] = {'diffuse::texture-01.png': solid_texture((240, 24, 24))}
    payload['texture_pools'] = {'pool-01': [{'tex_key': 'diffuse::texture-01.png', 'label': 'texture-01'}]}
    payload['controls']['toggles'] = {'KeyFixture': {
        'name': 'control-01', 'ini': 'source-01.ini', 'section': 'KeyFixture', 'wired': True,
        'vars': [{'var': 'input01', 'default': '1', 'values': ['1', '0']},
                 {'var': 'input02', 'default': '0', 'values': ['0', '1']}],
    }}
    payload['state']['defaults'] = {'input01': '1', 'input02': '0'}
    page = _open_fixture(viewer, payload)
    backend = page.evaluate("""async () => {
      const {renderer, rendererReady} = await import('./js/scene/scene.js');
      await rendererReady;
      return renderer.backend.isWebGPUBackend === true && renderer.backend.compatibilityMode !== true;
    }""")
    assert backend
    wait_texture(page)
    before = mesh_pixel(page)
    assert before[0] > before[1] + 25 and before[0] > before[2] + 25
    page.locator('#toggle-list .toggle-cycle-btn').first.click()
    page.wait_for_function('!window.modViewer.activeMeshes[0].visible')
    assert page.evaluate("""async () => {
      const {getControlState} = await import('./js/editing/control-state.js');
      return getControlState();
    }""") == {'input01': '0', 'input02': '1'}
    hidden = mesh_pixel(page)
    assert sum(abs(a - b) for a, b in zip(before, hidden)) > 60
    page.locator('#toggle-list .toggle-cycle-btn').first.click()
    page.wait_for_function('window.modViewer.activeMeshes[0].visible')
    restored = mesh_pixel(page)
    assert max(abs(a - b) for a, b in zip(before, restored)) < 15


def test_shared_texture_uploads_preserve_role_specific_color_spaces(viewer):
    payload = textured_payload(2)
    payload['textures']['normal_map::texture-01.png'] = solid_texture((128, 128, 255))
    for mesh in payload['meshes'].values():
        mesh['normal_map_key'] = 'normal_map::texture-01.png'
    page = _open_fixture(viewer, payload)
    wait_texture(page)
    wait_texture(page, role='normal_map')
    result = page.evaluate("""async () => {
      const {getGameMaterialTexture} = await import('./js/mesh/material-profile.js');
      const [first, second] = window.modViewer.activeMeshes;
      const diffuse = getGameMaterialTexture(first.material, 'diffuse');
      const auxiliary = getGameMaterialTexture(first.material, 'normal_map');
      return {shared: diffuse === getGameMaterialTexture(second.material, 'diffuse'),
        separate: diffuse !== auxiliary, color: diffuse.colorSpace, data: auxiliary.colorSpace};
    }""")
    assert result == {'shared': True, 'separate': True, 'color': 'srgb', 'data': ''}


def test_asset_packed_normal_shading_ignores_blue_and_retains_rg_detail(viewer, tmp_path):
    key = 'normal_map::texture-01.png'
    part = AssetMeshPart(
        key='part-01', label='Component01', asset_type='ZZMI',
        asset_path='Asset01', geometry_hash='12345678',
        component_name='Component01', classification='A',
        component_ordinal=0, first_index=0, index_count=3,
        positions=struct.pack('<9f', 0, 0, 0, 1, 0, 0, 0, 0, 1),
        indices=struct.pack('<3I', 0, 1, 2),
        normals=struct.pack('<9f', 0, -1, 0, 0, -1, 0, 0, -1, 0),
        uvs=struct.pack('<6f', 0, 0, 1, 0, 0, 1),
        textures={'normal_map': AssetTexture(
            'normal_map', 'texture-01.png', key, 'texture-01',
            uri=solid_texture((128, 128, 0)))})
    geometry = GeometryBlob()
    payload = AssetLoadResult.from_parts(
        'ZZMI', str(tmp_path), {'path': 'Asset01'}, [part],
        geometry=geometry).payload
    payload['_fixture_blob'] = bytes(geometry.data)
    page = _open_fixture(viewer, payload)
    wait_texture(page, role='normal_map')
    sample = [[0.25, 0, 0.25]]
    packed = mesh_pixels(page, sample)[0]
    page.evaluate("""async () => {
      const {updateGameMaterialTextures} = await import('./js/mesh/material-profile.js');
      const mesh = window.modViewer.activeMeshes[0];
      updateGameMaterialTextures(mesh, {normal_map: null});
    }""")
    geometry_normal = mesh_pixels(page, sample)[0]
    assert max(abs(a-b) for a,b in zip(packed, geometry_normal)) < 10
    page.evaluate("""async () => {
      const {DataTexture, RGBAFormat, UnsignedByteType, NoColorSpace} = await import('three');
      const {updateGameMaterialTextures} = await import('./js/mesh/material-profile.js');
      window.__setNormalPixels = rgba => {
        const texture = new DataTexture(new Uint8Array(rgba), 1, 1, RGBAFormat, UnsignedByteType);
        texture.colorSpace = NoColorSpace;
        texture.needsUpdate = true;
        updateGameMaterialTextures(window.modViewer.activeMeshes[0], {normal_map: texture});
      };
      window.__setNormalPixels([128, 128, 255, 255]);
    }""")
    other_blue = mesh_pixels(page, sample)[0]
    assert max(abs(a-b) for a,b in zip(packed, other_blue)) < 5
    page.evaluate('window.__setNormalPixels([220, 128, 0, 255])')
    tilted = mesh_pixels(page, sample)[0]
    assert max(abs(a-b) for a,b in zip(packed, tilted)) > 10, (
        packed, geometry_normal, other_blue, tilted,
        page.evaluate('window.modViewer.activeMeshes[0].material.userData.gameMaterial.normalPacking'))


def test_failed_texture_uses_renderable_fallback_without_retry_and_new_source_recovers(viewer):
    broken = textured_payload()
    broken['textures']['diffuse::texture-01.png'] = '/texture/fixture-missing.png'
    page = viewer({'fixture-01': broken, 'fixture-02': textured_payload()})
    requests = []
    page.route('**/texture/fixture-missing.png', lambda route: (requests.append(route.request.url), route.fulfill(status=404)))
    open_model(page, 'fixture-01')
    wait_loaded(page)
    fallback = mesh_pixel(page)
    assert max(fallback) - min(fallback) < 20
    page.evaluate("""async () => {
      const {refreshMeshTexture} = await import('./js/mesh/mesh-factory.js');
      refreshMeshTexture(window.modViewer.activeMeshes[0]);
    }""")
    mesh_pixel(page)
    assert len(requests) == 1
    open_model(page, 'fixture-02')
    wait_loaded(page)
    wait_texture(page)
    recovered = mesh_pixel(page)
    assert recovered[0] > recovered[2] + 25


def test_replaced_pending_texture_cannot_apply_stale_pixels(viewer):
    payload = textured_payload()
    payload['textures']['diffuse::texture-01.png'] = '/texture/fixture-delayed.png'
    page = viewer({'fixture-01': payload})
    delayed = []
    page.route('**/texture/fixture-delayed.png', lambda route: delayed.append(route))
    open_model(page, 'fixture-01')
    wait_loaded(page)
    assert len(delayed) == 1
    page.evaluate("""async () => {
      const {setManualTexOverride} = await import('./js/mesh/mesh-state.js');
      setManualTexOverride(window.modViewer.activeMeshes[0], 'diffuse::texture-02.png');
    }""")
    wait_texture(page)
    before = mesh_pixel(page)
    delayed[0].fulfill(status=200, content_type='image/png', body=base64.b64decode(solid_texture((240, 24, 24)).split(',')[1]))
    after = mesh_pixel(page)
    assert after[2] > after[0] + 25
    assert max(abs(a-b) for a,b in zip(before, after)) < 15
    assert page.evaluate('window.modViewer.activeMeshes[0].userData.texKey') == 'diffuse::texture-02.png'


def test_manual_texture_override_survives_controls_and_clear_restores_authored_binding(viewer):
    payload = textured_payload()
    payload['state']['defaults'] = {'input01': '0'}
    payload['meshes']['mesh-00']['texture_variants'] = [{
        'conditions': [[{'var': 'input01', 'value': '1', 'negate': False}]],
        'tex_key': 'diffuse::texture-02.png',
    }]
    page = _open_fixture(viewer, payload)
    result = page.evaluate("""async () => {
      const state = await import('./js/mesh/mesh-state.js');
      const controls = await import('./js/editing/control-state.js');
      const mesh = window.modViewer.activeMeshes[0];
      state.setManualTexOverride(mesh, 'diffuse::texture-01.png');
      controls.setControlValue('input01', '1'); state.applyTextureVariant(mesh);
      const sticky = mesh.userData.texKey;
      state.setManualTexOverride(mesh, null); const none = mesh.userData.texKey;
      state.setManualTexOverride(mesh, undefined); const automatic = mesh.userData.texKey;
      controls.setControlValue('input01', '0'); state.applyTextureVariant(mesh);
      return [sticky, none, automatic, mesh.userData.texKey];
    }""")
    assert result == ['diffuse::texture-01.png', None, 'diffuse::texture-02.png', 'diffuse::texture-01.png']


def test_color_preview_changes_pixels_and_reset_reuses_material_and_texture(viewer):
    page = _open_fixture(viewer, textured_payload())
    wait_texture(page)
    before = mesh_pixel(page)
    page.evaluate("""async () => {
      const {setMeshColorAdjustment} = await import('./js/mesh/mesh-color-session.js');
      const {getGameMaterialTexture} = await import('./js/mesh/material-profile.js');
      const mesh = window.modViewer.activeMeshes[0];
      window.__material = mesh.material;
      window.__texture = getGameMaterialTexture(mesh.material, 'diffuse');
      setMeshColorAdjustment(mesh, {brightness: 0.2});
    }""")
    changed = mesh_pixel(page)
    assert before[0] > changed[0] + 20
    result = page.evaluate("""async () => {
      const {resetMeshColorAdjustment} = await import('./js/mesh/mesh-color-session.js');
      const {getGameMaterialTexture} = await import('./js/mesh/material-profile.js');
      const mesh = window.modViewer.activeMeshes[0];
      resetMeshColorAdjustment(mesh);
      return mesh.material === window.__material && getGameMaterialTexture(mesh.material, 'diffuse') === window.__texture;
    }""")
    assert result
    restored = mesh_pixel(page)
    assert max(abs(a-b) for a,b in zip(before, restored)) < 15
    assert bridge_calls(page, 'textureSave') == []


def test_texture_save_requires_confirmation_flushes_metadata_and_blocks_duplicate_submit(viewer):
    page = _open_fixture(viewer, textured_payload(2, extension='dds'))
    page.evaluate("""async () => {
      const {setMeshColorAdjustment} = await import('./js/mesh/mesh-color-session.js');
      for (const mesh of window.modViewer.activeMeshes) setMeshColorAdjustment(mesh, {brightness: 0.6}, {persist: true});
      window.modViewer.activeMeshes[1].visible = false;
      window.__bridge.blocked.textureSave = true;
    }""")
    page.locator('#inspector-tab').click()
    page.locator('.draw-item').first.click()
    page.locator('.inspector-texture-bake').click()
    page.locator('#texture-bake-modal-backdrop.show').wait_for()
    assert bridge_calls(page, 'textureSave') == []
    page.locator('#texture-bake-close').click()
    assert bridge_calls(page, 'textureSave') == []
    page.locator('.inspector-texture-bake').click()
    page.locator('#texture-bake-confirm').click()
    page.wait_for_function('window.__bridge.calls.some(call => call.name === "textureSave")')
    page.locator('#texture-bake-close').click()
    assert page.locator('#texture-bake-modal-backdrop.show').is_visible()
    page.locator('#texture-bake-confirm').evaluate('button => button.click()')
    assert len(bridge_calls(page, 'textureSave')) == 1
    request = bridge_calls(page, 'textureSave')[0]
    assert request[:2] == ['fixture-01', 'diffuse::texture-01.dds']
    assert len(request[2]) == 2
    assert len(request[3]) == 2
    calls = page.evaluate('window.__bridge.calls.map(call => call.name)')
    assert calls.index('color') < calls.index('textureSave')
    page.evaluate('window.__bridge.release("textureSave")')
    page.locator('#texture-bake-error').wait_for(state='visible')
    assert page.evaluate('window.modViewer.activeMeshes[0].userData.colorAdjustment.brightness') == 0.6
    page.locator('#texture-bake-close').click()
    page.evaluate("""() => {
      const meshes = window.modViewer.activeMeshes;
      window.__bridge.results.textureSave = {
        status: 'ok', tex_key: 'diffuse::texture-01.dds', affected_tex_keys: ['diffuse::texture-01.dds'],
        saved_meshes: meshes.map(mesh => ({semantic_key: mesh.userData.semanticKey, metadata_key: mesh.userData.metadataKey})),
        metadata_reset: {cleared: meshes.map(mesh => mesh.userData.metadataKey), preserved: [], failed: []},
      };
      window.__materials = meshes.map(mesh => mesh.material);
    }""")
    page.locator('.inspector-texture-bake').click()
    page.locator('#texture-bake-confirm').click()
    page.wait_for_function('window.modViewer.activeMeshes.every(mesh => mesh.userData.colorAdjustment.brightness === 1)')
    page.locator('.texture-bake-summary').first.wait_for(state='visible')
    assert len(bridge_calls(page, 'textureSave')) == 2
    assert page.evaluate('window.modViewer.activeMeshes.every((mesh, i) => mesh.material === window.__materials[i])')


def test_shape_changes_update_stable_attributes_and_restore_authored_normals(viewer):
    payload = model_payload()
    mesh = payload['meshes']['mesh-00']
    mesh['normal'] = append_stream(payload, 'f', [0,0,1] * 3)
    mesh['shape_targets'] = [{'var': 'shape01', 'pos': append_stream(payload, 'f', [0,0,0, 2,0,0, 0,2,0])}]
    payload['state']['defaults'] = {'shape01': '0'}
    page = _open_fixture(viewer, payload)
    result = page.evaluate("""async () => {
      const {refreshMeshes} = await import('./js/mesh/mesh-state.js');
      const {setControlValue} = await import('./js/editing/control-state.js');
      const mesh = window.modViewer.activeMeshes[0];
      const position = mesh.geometry.attributes.position;
      const normal = mesh.geometry.attributes.normal;
      const baseline = [...mesh.userData.basePositions];
      setControlValue('shape01', '1'); refreshMeshes({force: {shapes: true}});
      const shaped = [...position.array];
      setControlValue('shape01', '0'); refreshMeshes({force: {shapes: true}});
      return {shaped, restored: [...position.array], baseline,
        stable: position === mesh.geometry.attributes.position && normal === mesh.geometry.attributes.normal,
        normals: [...normal.array]};
    }""")
    assert result['shaped'] == [0,0,0,2,0,0,0,2,0]
    assert result['restored'] == result['baseline'] == [0,0,0,1,0,0,0,1,0]
    assert result['normals'] == [0,0,1] * 3
    assert result['stable']


def test_animation_suspension_and_release_restore_canonical_geometry_then_resume(viewer):
    payload = model_payload()
    mesh = payload['meshes']['mesh-00']
    mesh['animation_id'] = 'clock-01'
    mesh['animation_geometry'] = {
        'frames': 2, 'frame_start': 0, 'position_frame_bytes': 36,
        'positions': append_stream(payload, 'f', [0,0,0,1,0,0,0,1,0, 0,0,0.5,1,0,0.5,0,1,0.5]),
    }
    payload['animations'] = {'clock-01': {'frame_start': 0, 'frame_end': 1, 'fps': 30}}
    page = _open_fixture(viewer, payload, {'fixture-02': model_payload()})
    page.wait_for_function('window.modViewer.activeMeshes[0].geometry.attributes.position.array[2] === 0.5')
    page.evaluate('const mesh = window.modViewer.activeMeshes[0]; mesh.userData.animationSuspended = true; mesh.geometry.attributes.position.array.fill(7)')
    page.evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
    assert page.evaluate('window.modViewer.activeMeshes[0].geometry.attributes.position.array.every(value => value === 7)')
    result = page.evaluate("""async () => {
      const {resumeAnimatedMesh} = await import('./js/mesh/animation-runtime.js');
      const mesh = window.modViewer.activeMeshes[0], position = mesh.geometry.attributes.position;
      mesh.userData.animationSuspended = false;
      const resumed = resumeAnimatedMesh(mesh);
      return {resumed, stable: mesh.geometry.attributes.position === position, canonical: [...position.array]};
    }""")
    assert result == {'resumed': True, 'stable': True, 'canonical': [0,0,0,1,0,0,0,1,0]}
    page.wait_for_function('window.modViewer.activeMeshes[0].geometry.attributes.position.array[2] === 0.5')
    open_model(page, 'fixture-02')
    wait_loaded(page)
    assert page.evaluate("import('./js/mesh/animation-runtime.js').then(module => module.animationRuntimeSnapshot().clocks)") == 0


def test_wireframe_suppresses_outlines_and_restores_preference_without_rebuilding(viewer):
    page = _open_fixture(viewer, model_payload())
    result = page.evaluate("""async () => {
      const modes = await import('./js/scene/render-modes.js');
      const outlines = await import('./js/scene/outline-renderer.js');
      const mesh = window.modViewer.activeMeshes[0], material = mesh.material, geometry = mesh.geometry;
      outlines.setOutlinesEnabled(true);
      const enabled = outlines.getOutlineState(mesh).visible;
      modes.toggleWireframeMode([mesh]);
      const suppressed = outlines.getOutlineState(mesh).visible;
      const retained = outlines.isOutlineEnabled();
      modes.toggleWireframeMode([mesh]);
      return {enabled, suppressed, retained, restored: outlines.getOutlineState(mesh).visible,
        stable: mesh.material === material && mesh.geometry === geometry};
    }""")
    assert result == {'enabled': True, 'suppressed': False, 'retained': True, 'restored': True, 'stable': True}


def test_thin_surfaces_keep_depth_order_through_distant_and_close_zoom(viewer):
    payload = textured_payload(2)
    keys = list(payload['textures'])
    payload['textures'][keys[0]] = solid_texture((24, 240, 24))
    payload['textures'][keys[1]] = solid_texture((240, 24, 24))
    payload['meshes']['mesh-01'].update(
        pos=append_stream(payload, 'f', [0, 0, -0.001,
                                       1, 0, -0.001,
                                       0, 1, -0.001]),
        tex_key=keys[1])
    page = _open_fixture(viewer, payload)
    wait_texture(page, 0)
    wait_texture(page, 1)
    page.evaluate("""async () => {
      const {camera, controls} = await import('./js/scene/scene.js');
      window.__zoomCamera = camera; window.__zoomControls = controls;
      controls.target.set(0.25, 0.25, 0);
      // Render the rear layer last to expose any equal-depth collisions.
      window.modViewer.activeMeshes.forEach((mesh, index) => mesh.renderOrder = index);
    }""")
    states = []
    for distance in (2, 12, 0.02, 2):
        page.evaluate("""distance => {
          const camera = window.__zoomCamera, controls = window.__zoomControls;
          camera.position.copy(controls.target); camera.position.z += distance;
          camera.lookAt(controls.target); camera.updateMatrixWorld();
        }""", distance)
        pixel = mesh_pixels(page, [[0.25, 0.25, 0]])[0]
        assert pixel[1] > pixel[0] + 50, (distance, pixel)
        states.append(page.evaluate('window.__zoomCamera.near'))
    assert states[1] > states[0]
    assert 0 < states[2] < 0.02
    assert states[3] == states[0]


def test_outline_keeps_shallow_backfaces_behind_the_visible_surface(viewer):
    payload = model_payload(2)
    payload['meshes']['mesh-01'].update(
        pos=append_stream(payload, 'f', [0.2, 0.2, -0.001,
                                       0.2, 0.4, -0.001,
                                       0.4, 0.2, -0.001]))
    for mesh in payload['meshes'].values():
        mesh['normal'] = append_stream(payload, 'f', [0, 0, 1] * 3)
    page = _open_fixture(viewer, payload)
    before = mesh_pixel(page)
    page.evaluate("""async () => {
      const {setOutlinesEnabled} = await import('./js/scene/outline-renderer.js');
      setOutlinesEnabled(true);
    }""")
    outlined = mesh_pixel(page)
    assert max(abs(a - b) for a, b in zip(before, outlined)) < 15


def test_outline_and_selection_preserve_coplanar_surface_with_visible_border(viewer):
    payload = model_payload()
    mesh = payload['meshes']['mesh-00']
    mesh['idx'] = append_stream(payload, 'I', [0, 2, 1])
    mesh['normal'] = append_stream(payload, 'f', [
        -0.707, 0, -0.707, 0.707, 0, -0.707, 0, 0.707, -0.707,
    ])
    page = _open_fixture(viewer, payload)
    points = [[x, y, 0] for x in (0.15, 0.3, 0.6)
              for y in (0.15, 0.3, 0.6) if x + y < 0.9]
    before = mesh_pixels(page, points)
    page.evaluate("""async () => {
      window.__outlines = await import('./js/scene/outline-renderer.js');
      window.__outlines.setOutlinesEnabled(true);
    }""")
    outlined = mesh_pixels(page, points)
    assert all(max(abs(a - b) for a, b in zip(old, new)) < 15
               for old, new in zip(before, outlined))
    page.evaluate("""async () => {
      const {selectMesh} = await import('./js/scene/selection.js');
      selectMesh(window.modViewer.activeMeshes[0]);
    }""")
    selected = mesh_pixels(page, points)
    assert all(max(abs(a - b) for a, b in zip(old, new)) < 15
               for old, new in zip(before, selected))
    corners = project_mesh_points(page, [[0, 0, 0], [1, 0, 0], [0, 1, 0]])
    with Image.open(io.BytesIO(page.screenshot())) as image:
        rgb = image.convert('RGB')
        yellow = sum(
            r > 180 and g > 120 and b < 80
            for x in range(min(p[0] for p in corners) - 3,
                           max(p[0] for p in corners) + 4)
            for y in range(min(p[1] for p in corners) - 3,
                           max(p[1] for p in corners) + 4)
            for r, g, b in [rgb.getpixel((x, y))]
        )
    assert yellow > 20, 'The selection silhouette must remain visible'
    page.evaluate("""async () => {
      window.__outlines.setOutlinesEnabled(false);
      const {selectMesh} = await import('./js/scene/selection.js');
      selectMesh(null);
    }""")
    restored = mesh_pixels(page, points)
    assert all(max(abs(a - b) for a, b in zip(old, new)) < 15
               for old, new in zip(before, restored))


def test_directional_shadows_keep_original_shape_with_a_low_angle_floor_fallback(viewer):
    payload = model_payload()
    mesh = payload['meshes']['mesh-00']
    mesh['pos'] = append_stream(payload, 'f', [
        -0.5, 0, -0.5, 0.5, 0, -0.5, 0.5, 2, -0.5, -0.5, 2, -0.5,
        -0.5, 0, 0.5, 0.5, 0, 0.5, 0.5, 2, 0.5, -0.5, 2, 0.5,
    ])
    mesh['idx'] = append_stream(payload, 'I', [
        0, 2, 1, 0, 3, 2, 4, 5, 6, 4, 6, 7,
        0, 1, 5, 0, 5, 4, 3, 7, 6, 3, 6, 2,
        0, 4, 7, 0, 7, 3, 1, 2, 6, 1, 6, 5,
    ])
    mesh['drawindexed'] = [36, 0, 0]
    mesh.pop('uv')
    page = _open_fixture(viewer, payload)
    midpoint = page.evaluate("""async () => {
      const {scene, camera, controls, setKeyLightIntensity} = await import('./js/scene/scene.js');
      const {requestRender} = await import('./js/scene/render-scheduler.js');
      scene.background.setHex(0x808080);
      camera.position.set(8, 7, 12);
      camera.lookAt(controls.target); camera.updateMatrixWorld();
      const light = scene.children.find(object => object.isDirectionalLight && object.castShadow);
      window.__shadowCameras = new Set();
      window.modViewer.activeMeshes[0].onBeforeShadow = (_renderer, _object, _camera, shadowCamera) =>
        window.__shadowCameras.add(shadowCamera.uuid);
      window.__lowerKeyLight = height => {
        window.__shadowCameras.clear(); light.position.set(-2, height, -2); requestRender();
      };
      setKeyLightIntensity(0);
      return controls.target.y;
    }""")
    points = [[1, -0.001, 1], [1.5, -0.001, -0.9], [3.5, -0.001, -2]]
    baseline = mesh_pixels(page, points)
    unshadowed = baseline[0]
    page.evaluate("""async () => {
      const {setKeyLightIntensity, invalidateCharacterShadowMap} = await import('./js/scene/scene.js');
      window.__shadowCameras.clear(); setKeyLightIntensity(1); invalidateCharacterShadowMap();
    }""")
    initial = mesh_pixels(page, points)
    assert page.evaluate('window.__shadowCameras.size') == 1
    assert sum(baseline[1]) - sum(initial[1]) > 30
    assert max(abs(a - b) for a, b in zip(baseline[2], initial[2])) < 5
    for height, maps in ((3, 1), (midpoint, 2), (-1, 1), (3, 1)):
        page.evaluate('height => window.__lowerKeyLight(height)', height)
        shadowed = mesh_pixels(page, [[1, -0.001, 1]])[0]
        assert page.evaluate('window.__shadowCameras.size') == maps
        if height > 0:
            assert sum(unshadowed) - sum(shadowed) > 30, height
        else:
            assert max(abs(a - b) for a, b in zip(unshadowed, shadowed)) < 5, height


def test_close_zoom_surface_shadow_stays_smooth_without_redrawing_the_map(viewer):
    payload = model_payload()
    mesh = payload['meshes']['mesh-00']
    mesh.update(pos=append_stream(payload, 'f', [-4,-4,0, 4,-4,0, 4,4,0, -4,4,0]),
                idx=append_stream(payload, 'I', [0,1,2, 0,2,3]), drawindexed=[6,0,0])
    mesh.pop('uv')
    page = _open_fixture(viewer, payload)
    page.evaluate("""async () => {
      const THREE = await import('three/webgpu');
      const {scene, camera, controls, adoptModelMeshes,
        invalidateCharacterShadowGeometry} = await import('./js/scene/scene.js');
      const caster = new THREE.Mesh(new THREE.PlaneGeometry(8, 8),
        new THREE.MeshStandardNodeMaterial({side: THREE.DoubleSide}));
      caster.position.set(-4.685, 0, 0.6);
      caster.castShadow = true;
      scene.add(caster); adoptModelMeshes([caster]); caster.rotation.z = 0.35;
      const light = scene.children.find(object => object.isDirectionalLight && object.castShadow);
      light.position.set(-5, 2, 6);
      controls.target.set(0, 0, 0);
      camera.position.set(0, 0, 1.2);
      camera.lookAt(controls.target); camera.updateMatrixWorld(); controls.update();
      invalidateCharacterShadowGeometry();
    }""")
    states = []
    for distance in (1.2, 0.9):
        page.evaluate("""async distance => {
          const {camera, controls} = await import('./js/scene/scene.js');
          camera.position.z = distance;
          camera.lookAt(controls.target); camera.updateMatrixWorld(); controls.update();
        }""", distance)
        mesh_pixels(page, [[0,0,0]])
        center = project_mesh_points(page, [[0,0,0]])[0]
        clip = {'x': center[0]-180, 'y': center[1]-180, 'width': 360, 'height': 360}
        with Image.open(io.BytesIO(page.screenshot(clip=clip))) as image:
            gray = image.convert('L')
            rows = [[gray.getpixel((x, y)) for x in range(360)] for y in range(60, 300)]
        # A flat receiver has one monotonic transition; per-pixel sample noise
        # introduces repeated backwards steps inside that shadow edge.
        assert all(row[-1] - row[0] > 30 for row in rows)
        backwards = sum(max(left-right, 0) for row in rows for left, right in zip(row, row[1:]))
        assert backwards < len(rows) / 2, backwards
        states.append(page.evaluate("""async () => {
          const {getCharacterShadowDebugState} = await import('./js/scene/scene.js');
          const {fitCount, shadowUpdateCount} = getCharacterShadowDebugState();
          return {fitCount, shadowUpdateCount};
        }"""))
    assert states[1] == states[0]


def test_compressed_dds_upload_matches_reference_colors_and_orientation(viewer, tmp_path):
    dds = tmp_path / 'texture-01.dds'
    dds.write_bytes(split_color_dds())
    reference = tmp_path / 'texture-02.png'
    with Image.new('RGB', (4, 4), (255, 0, 0)) as image:
        image.paste((0, 0, 255), (0, 2, 4, 4))
        image.save(reference)
    publication = server.begin_texture_publication(str(tmp_path))
    urls = [publication.register(str(path)) for path in [dds, reference]]
    publication.commit()
    payload = model_payload()
    mesh = payload['meshes']['mesh-00']
    mesh.update(pos=append_stream(payload, 'f', [0,0,0, 1,0,0, 1,1,0, 0,1,0]),
                uv=append_stream(payload, 'f', [0,0, 1,0, 1,1, 0,1]),
                idx=append_stream(payload, 'I', [0,1,2, 0,2,3]), drawindexed=[6,0,0],
                tex_key='diffuse::texture-01.dds')
    payload['textures'] = dict(zip(['diffuse::texture-01.dds', 'diffuse::texture-02.png'], urls))
    page = _open_fixture(viewer, payload)
    wait_texture(page)
    assert page.evaluate("""async () => {
      const {getGameMaterialTexture} = await import('./js/mesh/material-profile.js');
      const {renderer, rendererReady} = await import('./js/scene/scene.js');
      await rendererReady;
      return renderer.backend.isWebGPUBackend && !renderer.backend.compatibilityMode
        && getGameMaterialTexture(window.modViewer.activeMeshes[0].material, 'diffuse').isCompressedTexture;
    }""")
    points = [[0.5, 0.2, 0], [0.5, 0.8, 0]]
    compressed = mesh_pixels(page, points)
    page.evaluate("""async () => {
      const {setManualTexOverride} = await import('./js/mesh/mesh-state.js');
      setManualTexOverride(window.modViewer.activeMeshes[0], 'diffuse::texture-02.png');
    }""")
    page.wait_for_function("""() => {
      const texture = window.__getTexture(window.modViewer.activeMeshes[0].material, 'diffuse');
      return texture?.image && !texture.isCompressedTexture;
    }""")
    reference_pixels = mesh_pixels(page, points)
    assert compressed[0][2] > compressed[0][0] + 60
    assert compressed[1][0] > compressed[1][2] + 60
    assert all(max(abs(a-b) for a,b in zip(left, right)) < 40
               for left, right in zip(compressed, reference_pixels))


def test_menu_dds_canvas_preserves_pixels_and_viewport(viewer, tmp_path):
    expected = [(96, 144, 192, 128), (192, 96, 48, 128)]
    blocks = []
    for *rgb, alpha in expected:
        bits = int.from_bytes(mode6_block(tuple((value, value) for value in rgb)), 'little')
        for offset in (49, 56):
            bits = (bits & ~(127 << offset)) | ((alpha >> 1) << offset)
        blocks.append(bits.to_bytes(16, 'little'))
    path = tmp_path / 'menu.dds'
    path.write_bytes(dx10_dds(b''.join(blocks), dxgi_format=99, width=4, height=8))
    publication = server.begin_texture_publication(str(tmp_path))
    url = publication.register_menu_image(str(path))
    publication.commit()
    try:
        page = _open_fixture(viewer, textured_payload())
        wait_texture(page)
        before_pixels = mesh_pixel(page)
        before_state = page.evaluate("""async url => {
          const {renderer} = await import('./js/scene/scene.js');
          window.previewState = () => [renderer.getRenderTarget()?.uuid || null,
            renderer.getClearAlpha(), renderer.getScissorTest(), renderer.outputColorSpace,
            renderer.toneMapping, renderer.getPixelRatio(), renderer.autoClear];
          const before = previewState();
          const {buildMenuPanel} = await import('./js/panels/menu-panel.js');
          buildMenuPanel({option01: {name: 'Option01', var: 'option01', values: ['0', '1'],
            default: '0', image: url}});
          return before;
        }""", url)
        page.wait_for_function("document.querySelector('#menu-list canvas')?.dataset.previewReady === 'true'")
        result = page.evaluate("""() => ({state: previewState(), pixels: [64, 192].map(y =>
          [...document.querySelector('#menu-list canvas').getContext('2d').getImageData(128, y, 1, 1).data])})""")
        assert result['state'] == before_state
        assert all(max(abs(left - right) for left, right in zip(actual, reference)) <= 4
                   for actual, reference in zip(result['pixels'], expected))
        assert max(abs(left - right) for left, right in zip(before_pixels, mesh_pixel(page))) < 5
    finally:
        publication.release()


def test_weight_rig_lazy_load_pose_deforms_vertices_and_ui_reset_restores_them(viewer):
    payload, weights = weighted_payload(include_ineligible=True)
    page = _open_fixture(viewer, payload, {'fixture-weights': weights})
    page.evaluate('window.modViewer.activeMeshes[1].visible = false')
    assert bridge_calls(page, 'weights') == []
    _activate_rig(page, humanoid=True)

    assert page.evaluate("""() => {
      const rig = window.__rigApi;
      rig.beginWeightModelPicking();
      rig.beginRigJointPicking({type: 'selected-joint'});
      const rigOwnsPicking = !rig.getModelWeightState().picking
        && rig.getModelRigState().jointPickIntent?.type === 'selected-joint';
      rig.beginWeightModelPicking();
      const weightOwnsPicking = rig.getModelWeightState().picking && !rig.getModelRigState().jointPickIntent;
      rig.cancelWeightModelPicking();
      return rigOwnsPicking && weightOwnsPicking;
    }""")
    page.evaluate("""() => {
      const rig = window.__rigApi, source = rig.getModelWeightState().sources[0];
      rig.setBoneSelected(source.key, source.availableBoneIds.at(-1), true);
    }""")
    page.wait_for_function('window.__rigApi.getModelPhysicsState().participantCount > 0')
    assert page.evaluate("""() => {
      const rig = window.__rigApi, frequency = rig.getModelPhysicsState().frequencyHz;
      rig.setPhysicsFrequency(frequency + 1.25);
      const changed = rig.getModelPhysicsState().frequencyHz === frequency + 1.25;
      rig.resetModelPhysics();
      return changed && rig.getModelPhysicsState().frequencyHz === frequency;
    }""")
    assert page.evaluate("""() => {
      const rig = window.__rigApi;
      rig.clearSelectedBones();
      const physics = rig.getModelPhysicsState();
      return rig.getModelWeightState().selectedBones.length === 0 && !physics.enabled && physics.participantCount === 0;
    }""")

    joint = page.evaluate("""() => {
      const model = window.__rigApi.getModelRigState().model;
      window.__rigJoint = model.components[0].nodeIds.find(id => id !== model.components[0].rootId);
      window.__rigPosition = window.modViewer.activeMeshes[0].geometry.attributes.position;
      window.__rigBaseline = [...window.__rigPosition.array];
      return window.__rigJoint;
    }""")
    page.locator('.rig-bone-select').select_option(str(joint))
    baseline_pixel = mesh_pixel(page)
    assert page.evaluate("""() => {
      const rig = window.__rigApi;
      rig.setRigJointRotation(window.__rigJoint, [0, 0, Math.SQRT1_2, Math.SQRT1_2], {dragging: true});
      rig.finishRigJointPose(window.__rigJoint);
      return [...window.__rigPosition.array].some((value, i) => Math.abs(value-window.__rigBaseline[i]) > 1e-4);
    }""")
    posed_pixel = mesh_pixel(page)
    assert sum(abs(a-b) for a,b in zip(baseline_pixel, posed_pixel)) > 30
    page.locator('.rig-reset-pose').click()
    assert page.evaluate("""() =>
      window.modViewer.activeMeshes[0].geometry.attributes.position === window.__rigPosition
      && window.__rigPosition.array.every((value, i) => Math.abs(value-window.__rigBaseline[i]) < 1e-5)
    """)
    restored_pixel = mesh_pixel(page)
    assert max(abs(a-b) for a,b in zip(baseline_pixel, restored_pixel)) < 15

    restored = page.evaluate("""async () => {
      const rig = window.__rigApi, api = window.pywebview.api;
      const component = rig.getModelRigState().model.components[0], originalRoot = component.rootId;
      const presetRoot = component.nodeIds.find(id => id !== originalRoot);
      rig.setRigJointRoot(presetRoot);
      rig.setRigJointRotation(originalRoot, [0, 0, Math.SQRT1_2, Math.SQRT1_2]);
      rig.finishRigJointPose(originalRoot);
      let captured;
      const previousSave = api.save_rig_pose_preset;
      api.save_rig_pose_preset = async (path, preset) => {
        captured = preset;
        return {saved: true, presets: [preset]};
      };
      await rig.saveRigPosePreset('Pose 01');
      api.save_rig_pose_preset = previousSave;
      rig.resetRigPose();
      const applied = rig.applyRigPosePresetById(captured.id), state = rig.getModelRigState();
      return {success: applied.success, root: state.model.components[0].rootId,
        posed: Object.hasOwn(state.model.poseRotationByJointId, String(originalRoot)), expectedRoot: presetRoot};
    }""")
    assert restored['success'] and restored['posed']
    assert restored['root'] == restored['expectedRoot']
    assert bridge_calls(page, 'weights') == [['fixture-01']]


def test_weight_rig_overlapping_unregister_rebuilds_using_final_membership(viewer):
    payload, weights = weighted_payload(include_third_member=True)
    page = _open_fixture(viewer, payload, {'fixture-weights': weights})
    page.evaluate('window.modViewer.activeMeshes.slice(1).forEach(mesh => { mesh.visible = false; })')
    _activate_rig(page, humanoid=True)
    initial_joints = page.evaluate('window.__rigApi.getModelRigState().model.joints.length')

    for index in (1, 2):
        assert page.evaluate("""async index => {
          const {unregisterWeightRigMesh} = await import('./js/weight-rig/weight-rig-core.js');
          unregisterWeightRigMesh(window.modViewer.activeMeshes[index]);
          const state = window.__rigApi.getModelRigState();
          return state.loading && !state.loaded && state.model === null;
        }""", index)
    page.wait_for_function('window.__rigApi.getModelRigState().loaded && !window.__rigApi.getModelRigState().loading')
    rebuilt_joints = page.evaluate('window.__rigApi.getModelRigState().model.joints.length')
    assert initial_joints > rebuilt_joints == 2


def test_weight_rig_shape_change_invalidates_and_rebuilds_preserving_root(viewer):
    payload, weights = weighted_payload()
    payload['meshes']['mesh-00']['shape_targets'] = [
        {'var': 'shape01', 'pos': append_stream(payload, 'f', [0, 0, 0, 2, 0, 0, 0, 2, 0])},
    ]
    payload['state']['defaults'] = {'shape01': '0'}
    page = _open_fixture(viewer, payload, {'fixture-weights': weights})
    _activate_rig(page, humanoid=True)
    joint = page.evaluate("""() => {
      const rig = window.__rigApi, component = rig.getModelRigState().model.components[0];
      const joint = component.nodeIds.find(id => id !== component.rootId);
      rig.setRigJointRoot(joint);
      return joint;
    }""")

    assert page.evaluate("""async () => {
      const {setControlValue} = await import('./js/editing/control-state.js');
      const {refreshMeshes} = await import('./js/mesh/mesh-state.js');
      setControlValue('shape01', '1'); refreshMeshes({force: {shapes: true}});
      const state = window.__rigApi.getModelRigState();
      return !state.loaded && !state.loading && state.model === null;
    }""")
    rebuilt = page.evaluate("""async () => {
      await window.__rigApi.activateWeightRig();
      const state = window.__rigApi.getModelRigState();
      return {root: state.model.components[0].rootId, selected: state.selectedJointId};
    }""")
    assert rebuilt == {'root': joint, 'selected': None}


def test_weight_rig_collapsed_source_keeps_weights_and_other_physics_then_recovers(viewer):
    payload, weights = weighted_payload(include_second_member=True)
    weights['meshes']['mesh-01']['source'] = {
        'key': 'stream-02.buf|offset=0', 'file': 'stream-02.buf', 'bone_id_offset': 0,
    }
    payload['meshes']['mesh-01']['shape_targets'] = [
        {'var': 'shape01', 'pos': append_stream(payload, 'f', [0] * 9)},
    ]
    payload['state']['defaults'] = {'shape01': '0'}
    page = _open_fixture(viewer, payload, {'fixture-weights': weights})
    _activate_rig(page)
    page.evaluate("""() => {
      const rig = window.__rigApi;
      rig.getModelWeightState().sources.forEach(source => rig.setBoneSelected(source.key, source.availableBoneIds.at(-1), true));
    }""")
    page.wait_for_function('window.__rigApi.getModelPhysicsState().participantCount === 2')

    for value, participants, errors in [('1', 1, 1), ('0', 2, 0)]:
        state = page.evaluate("""async value => {
          const {setControlValue} = await import('./js/editing/control-state.js');
          const {refreshMeshes} = await import('./js/mesh/mesh-state.js');
          setControlValue('shape01', value); refreshMeshes({force: {shapes: true}});
          await window.__rigApi.activateWeightRig();
          const weight = window.__rigApi.getModelWeightState(), rig = window.__rigApi.getModelRigState();
          return {selections: weight.selectedBones.length, errors: Object.keys(rig.sourceErrors).length};
        }""", value)
        assert state == {'selections': 2, 'errors': errors}
        page.wait_for_function(
            'expected => window.__rigApi.getModelPhysicsState().participantCount === expected',
            arg=participants,
        )
        if errors:
            assert 'Rig unavailable: no usable faces.' in page.locator('.weight-rig-status').inner_text()

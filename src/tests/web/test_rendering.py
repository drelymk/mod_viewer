"""Real WebGPU output and control visibility with generated inputs."""

import base64

from .payloads import append_stream, model_payload, solid_texture, textured_payload
from .support import bridge_calls, mesh_pixel, open_model, wait_loaded, wait_texture


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
    page = viewer({'fixture-01': payload})
    open_model(page, 'fixture-01')
    wait_loaded(page)
    backend = page.evaluate("""async () => {
      const {renderer, rendererReady} = await import('./js/scene/scene.js');
      await rendererReady;
      return renderer.backend.isWebGPUBackend === true && renderer.backend.compatibilityMode !== true;
    }""")
    assert backend
    page.wait_for_function("""async () => {
      const {getGameMaterialTexture} = await import('./js/mesh/material-profile.js');
      return getGameMaterialTexture(window.modViewer.activeMeshes[0].material, 'diffuse')?.image != null;
    }""")
    page.wait_for_function('window.modViewer.getRenderCount() > 0')
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
    page = viewer({'fixture-01': payload})
    open_model(page, 'fixture-01')
    wait_loaded(page, 2)
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
    page = viewer({'fixture-01': payload})
    open_model(page, 'fixture-01')
    wait_loaded(page)
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
      return {sticky, none, automatic, restored: mesh.userData.texKey,
        defaultKey: mesh.userData.defaultTexKey};
    }""")
    assert result == {'sticky': 'diffuse::texture-01.png', 'none': None,
                      'automatic': 'diffuse::texture-02.png', 'restored': 'diffuse::texture-01.png',
                      'defaultKey': 'diffuse::texture-01.png'}
    assert bridge_calls(page, 'export') == []


def test_color_preview_changes_pixels_and_reset_reuses_material_and_texture(viewer):
    page = viewer({'fixture-01': textured_payload()})
    open_model(page, 'fixture-01')
    wait_loaded(page)
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
    assert bridge_calls(page, 'export') == []


def test_texture_save_requires_confirmation_flushes_metadata_and_blocks_duplicate_submit(viewer):
    page = viewer({'fixture-01': textured_payload(2, extension='dds')})
    open_model(page, 'fixture-01')
    wait_loaded(page, 2)
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
    assert bridge_calls(page, 'export') == []


def test_shape_changes_update_stable_attributes_and_restore_authored_normals(viewer):
    payload = model_payload()
    mesh = payload['meshes']['mesh-00']
    mesh['normal'] = append_stream(payload, 'f', [0,0,1] * 3)
    mesh['shape_targets'] = [{'var': 'shape01', 'pos': append_stream(payload, 'f', [0,0,0, 2,0,0, 0,2,0])}]
    payload['state']['defaults'] = {'shape01': '0'}
    page = viewer({'fixture-01': payload})
    open_model(page, 'fixture-01')
    wait_loaded(page)
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
    page = viewer({'fixture-01': payload, 'fixture-02': model_payload()})
    open_model(page, 'fixture-01')
    wait_loaded(page)
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
    page = viewer({'fixture-01': model_payload()})
    open_model(page, 'fixture-01')
    wait_loaded(page)
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
    assert bridge_calls(page, 'export') == []

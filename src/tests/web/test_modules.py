"""Ordered control replay independent of GPU availability."""

import math

import pytest


def test_semantic_refresh_endpoints_and_stale_completion_lifecycle(module_page):
    stubs = {
        'mesh/visibility.js': """
export const refreshAll = value => window.effects.push(['refresh', value]);
export const setStateRules = () => window.effects.push(['controls']);
export const updateMeshSemantics = () => {
  window.effects.push(['meshes']);
  return {success: window.meshUpdateSuccess, materialChangedMeshes: ['changed-01']};
};
""",
        'panels/mesh-panel.js': """
export const refreshAutomaticTextureBoundaries = () => {};
export const refreshMeshAssetDiagnostics = () => {};
""",
        'panels/menu-panel.js': 'export const buildMenuPanel = () => {};',
        'panels/toggle-panel.js': 'export const buildTogglePanel = () => {};',
        'panels/present-panel.js': """
export const buildPresentPanel = (value, context) => window.effects.push(['present', context]);
""",
        'panels/health-report.js': """
export const refreshHealthReport = () => window.effects.push(['health']);
export const setAssetResolution = () => {};
""",
        'i18n/index.js': 'export const t = key => key;',
        'ui/dialogs.js': 'export const alertDialog = async text => window.effects.push(["alert", text]);',
    }
    for path, source in stubs.items():
        module_page.route(f'**/js/{path}', lambda route, *, source=source: route.fulfill(
            content_type='text/javascript', body=source))
    result = module_page.evaluate("""async () => {
      const refresh = await import('./js/app/semantic-refresh.js');
      const {viewerState} = await import('./js/app/state.js');
      viewerState.currentModPath = 'mod-01';
      window.effects = [];
      window.meshUpdateSuccess = true;
      const requests = [];
      const api = {};
      for (const endpoint of ['get_present_state', 'get_control_state', 'get_mesh_semantics', 'get_semantic_state']) {
        api[endpoint] = async path => {requests.push([endpoint, path]); return {present: {}, controls: {}, meshes: {}};};
      }
      window.pywebview = {api};
      const handlers = {
        syncViewportControlPlacement: () => effects.push(['placement']),
        refreshPendingState: async (path, current) => {
          await Promise.resolve(); effects.push(['pending', path, current()]);
        },
      };
      const change = {selectedPosition: 2, applySelection: true};
      const snapshots = [];
      for (const [name, args] of [
        ['refreshPresentState', [change, handlers]], ['refreshControlSemantics', [handlers]],
        ['refreshMeshSemantics', [handlers]], ['refreshSemanticState', [handlers, change]],
      ]) {
        effects.length = 0;
        const success = await refresh[name](...args);
        snapshots.push({success, effects: structuredClone(effects), request: requests.at(-1)});
      }
      let release;
      api.get_semantic_state = path => {requests.push(['get_semantic_state', path]); return new Promise(resolve => release = resolve);};
      const obsolete = refresh.refreshSemanticState(handlers);
      await refresh.refreshPresentState(change, handlers);
      effects.length = 0;
      release({controls: {}, meshes: {}});
      const obsoleteResult = await obsolete;
      const obsoleteEffects = structuredClone(effects);

      api.get_mesh_semantics = async () => {throw new Error('fixture failure');};
      const failure = await refresh.refreshMeshSemantics(handlers);
      const failureEffects = structuredClone(effects);
      effects.length = 0;
      api.get_semantic_state = async () => ({controls: {}, meshes: {}});
      window.meshUpdateSuccess = false;
      const mismatch = await refresh.refreshSemanticState(handlers);
      const mismatchEffects = structuredClone(effects);
      effects.length = 0;
      viewerState.currentModPath = null;
      const absent = await refresh.refreshControlSemantics(handlers);
      return {snapshots, obsoleteResult, obsoleteEffects, failure, failureEffects,
        mismatch, mismatchEffects, absent, absentEffects: effects};
    }""")
    for snapshot, endpoint, mesh_updates, control_updates in zip(
            result['snapshots'],
            ['get_present_state', 'get_control_state', 'get_mesh_semantics', 'get_semantic_state'],
            [0, 0, 1, 1], [0, 1, 0, 1]):
        assert snapshot['success'] is True
        assert snapshot['request'] == [endpoint, 'mod-01']
        effects = snapshot['effects']
        assert sum(item[0] == 'meshes' for item in effects) == mesh_updates
        assert sum(item[0] == 'controls' for item in effects) == control_updates
        assert effects[-2:] == [['pending', 'mod-01', True], ['health']]
        if mesh_updates:
            assert next(item[1] for item in effects if item[0] == 'refresh') == {
                'force': {'visibility': True, 'textures': True},
                'additionalMeshes': ['changed-01']}
        else:
            present = next(item[1] for item in effects if item[0] == 'present')
            assert present['modPath'] == 'mod-01'
    assert result['snapshots'][0]['effects'][0] == [
        'present', {'modPath': 'mod-01', 'onChange': None,
                    'selectedPosition': 2, 'applySelection': True}]
    assert result['obsoleteResult'] is False and result['obsoleteEffects'] == []
    assert result['failure'] is False
    assert result['failureEffects'] == [
        ['alert', 'errors.refreshSemantics'], ['pending', 'mod-01', True], ['health']]
    assert result['mismatch'] is False
    assert result['mismatchEffects'] == [
        ['meshes'], ['alert', 'errors.refreshSemantics'],
        ['pending', 'mod-01', True], ['health']]
    assert result['absent'] is False and result['absentEffects'] == []


def test_payload_meshes_consume_identity_for_mod_and_asset_choices(module_page):
    stubs = {
        'mesh-factory.js': 'export const buildMesh = () => {window.built++; return {userData: {}};};',
        'mesh-state.js': 'export const addMesh = () => {};',
        'color-adjustment.js': 'export const normalizeColorAdjustment = value => value || null;',
        'mesh-color-session.js': 'export const syncMeshColorAdjustment = () => {};',
        'animation-runtime.js': 'export const registerAnimatedMesh = () => {};',
    }
    for path, source in stubs.items():
        module_page.route(f'**/js/mesh/{path}', lambda route, *, source=source: route.fulfill(
            content_type='text/javascript', body=source))
    result = module_page.evaluate("""async () => {
      const {buildPayloadMeshes} = await import('./js/mesh/mesh-model-builder.js');
      window.built = 0;
      const entries = {
        'mesh-01': {component: 'Component01', drawindexed: [3,0,0], identity: {key: 'identity-01'}},
        'asset-01': {component: 'Component01', drawindexed: [3,0,0], identity: {key: 'identity-02'}, asset_fill: true},
      };
      const meshes = buildPayloadMeshes(entries, 'mod-01', {
        'identity-01': 'Name01', 'identity-02': 'Name02', 'Component01::3,0,0': 'LegacyName',
      }, {}, {colorAdjustments: {'identity-01': {hue: 30}}});
      const choices = [...meshes].map(([key, mesh]) => ({key, metadataKey: mesh.userData.metadataKey,
        name: mesh.userData.displayName, adjustment: mesh.userData.colorAdjustment,
        identityShared: mesh.userData.identity === entries[key].identity}));
      let rejected = false;
      try {buildPayloadMeshes({'invalid-01': {component: 'Component01', drawindexed: [3,0,0]}});}
      catch {rejected = true;}
      return {choices, rejected, built};
    }""")
    assert result == {
        'choices': [
            {'key': 'mesh-01', 'metadataKey': 'identity-01', 'name': 'Name01',
             'adjustment': {'hue': 30}, 'identityShared': True},
            {'key': 'asset-01', 'metadataKey': 'identity-02', 'name': 'Name02',
             'adjustment': None, 'identityShared': True},
        ], 'rejected': True, 'built': 2}


def test_menu_grid_keeps_four_columns_and_icon_fallback_through_rebuilds(module_page):
    module_page.route('**/js/mesh/visibility.js', lambda route: route.fulfill(
        content_type='text/javascript', body="""
export {getControlValue as getToggleValue, setControlValue as setToggleValue}
  from '../editing/control-state.js';
export {syncViews as refreshAll} from '../scene/view-sync.js';
"""))
    module_page.evaluate("""async () => {
      const style = document.createElement('link');
      style.rel = 'stylesheet'; style.href = './css/app.css';
      await new Promise((resolve, reject) => {
        style.onload = resolve; style.onerror = reject;
        document.head.append(style);
      });
      document.body.innerHTML = '<div id="menu-panel"><div id="menu-list"></div></div>';
      const {buildMenuPanel} = await import('./js/panels/menu-panel.js');
      window.menuFixture = {build: buildMenuPanel, menu: Object.fromEntries(
        Array.from({length: 5}, (_, index) => [`option${index}`, {
          name: `option${index}`, var: `option${index}`, slot: index,
          source: null, values: ['0', '1'], default: '0', effects: [],
        }]))};
      buildMenuPanel(window.menuFixture.menu);
    }""")
    columns = "getComputedStyle(document.querySelector('#menu-list')).gridTemplateColumns.split(' ').length"
    assert module_page.evaluate(columns) == 4
    assert module_page.locator('.menu-item .ui-icon').count() == 5
    module_page.locator('.menu-item button').first.click()
    assert module_page.locator('.menu-value').first.inner_text() == '1'

    module_page.set_viewport_size({"width": 800, "height": 600})
    module_page.evaluate("""() => {
      const {menu, build} = window.menuFixture;
      for (const [index, info] of Object.values(menu).entries()) {
        info.source = index < 4 ? 'source01' : 'source02';
      }
      menu.option0.image = './fixture-missing-image.png';
      menu.option4.image = 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2"/>';
      build(menu);
    }""")
    module_page.wait_for_function("""() =>
      document.querySelectorAll('.menu-item .ui-icon').length === 4 &&
      document.querySelector('.menu-item img')?.naturalWidth > 0
    """)
    assert module_page.evaluate(columns) == 4
    assert module_page.evaluate("""() => [...document.querySelectorAll('.toggle-src-items')]
      .every(items => getComputedStyle(items).gridTemplateColumns.split(' ').length === 4)
    """)
    module_page.locator('.toggle-src-hdr').first.click()
    assert not module_page.locator('.toggle-src-items').first.is_visible()
    module_page.evaluate("window.menuFixture.build({})")
    assert not module_page.locator('#menu-panel').is_visible()
    module_page.evaluate("window.menuFixture.build(window.menuFixture.menu)")
    assert module_page.locator('#menu-panel').is_visible()
    assert module_page.locator('.menu-item').count() == 5


def test_outline_zoom_keeps_distant_edges_visible_and_restores_reference(module_page):
    result = module_page.evaluate("""async () => {
      const THREE = await import('three');
      const {resetOutlineProjectionReference, updateOutlineProjectionScale,
        getOutlineState} = await import('./js/scene/outline-renderer.js');
      const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 1000);
      const target = new THREE.Vector3();
      camera.position.set(0, 0, 10);
      const snapshot = () => {
        updateOutlineProjectionScale(camera, target, 800);
        const state = getOutlineState();
        return {pixels: state.effectiveWidthPixels,
          extrusion: state.scalePerDepth * camera.position.distanceTo(target)};
      };
      resetOutlineProjectionReference(camera, target);
      const fitted = snapshot();
      camera.position.z = 40;
      const distant = snapshot();
      camera.position.z = 160;
      const farther = snapshot();
      camera.position.z = 1;
      const close = snapshot();
      camera.position.z = 10;
      const restored = snapshot();
      return {fitted, distant, farther, close, restored};
    }""")
    fitted = result['fitted']
    assert result['distant']['pixels'] == pytest.approx(0.5)
    assert result['farther']['pixels'] == pytest.approx(result['distant']['pixels'])
    assert result['distant']['pixels'] < fitted['pixels'] < result['close']['pixels']
    assert result['close']['pixels'] == pytest.approx(1.5)
    assert result['restored'] == pytest.approx(fitted)


def test_camera_clipping_tracks_zoom_pan_and_model_lifecycle(module_page):
    result = module_page.evaluate("""async () => {
      const THREE = await import('three');
      const {createCameraFrame} = await import('./js/scene/camera-frame.js');
      const {computeModelBounds} = await import('./js/scene/model-bounds.js');
      const camera = new THREE.PerspectiveCamera(45, 2, 0.001, 1000);
      camera.coordinateSystem = THREE.WebGPUCoordinateSystem;
      const renderer = {domElement: {getBoundingClientRect: () =>
        ({left: 0, top: 0, width: 800, height: 600})}};
      const controls = {target: new THREE.Vector3(), saveState() {}, setCamera() {},
        update() {camera.lookAt(this.target); camera.updateMatrixWorld();}};
      const mesh = new THREE.Mesh(new THREE.BoxGeometry(2, 4, 0.02));
      const frame = createCameraFrame({camera, renderer, controls, grid: new THREE.GridHelper()});
      frame.fitTo([mesh]);
      const minimumNear = mesh.geometry.boundingBox.getSize(new THREE.Vector3()).length() * 0.0005;
      mesh.geometry.computeBoundingBox = () => {throw new Error('Cached bounds must not rescan vertices');};
      const snapshot = (meshes = [mesh]) => {
        frame.updateClipping();
        const box = computeModelBounds(meshes, {visibleOnly: true});
        const depths = [];
        if (!box.isEmpty()) for (const x of [box.min.x, box.max.x])
          for (const y of [box.min.y, box.max.y]) for (const z of [box.min.z, box.max.z])
            depths.push(new THREE.Vector3(x, y, z).project(camera).z);
        return {near: camera.near, far: camera.far, depths};
      };
      const fitted = snapshot();
      const offset = camera.position.clone().sub(controls.target);
      camera.position.copy(controls.target).addScaledVector(offset, 4); controls.update();
      const distant = snapshot();
      const pan = new THREE.Vector3(8000, 0, 0);
      camera.position.add(pan); controls.target.add(pan); controls.update();
      const panned = snapshot();
      frame.translateModel([mesh], pan);
      const translated = snapshot();
      const added = new THREE.Mesh(new THREE.BoxGeometry(0.2, 0.2, 0.2));
      added.position.z = camera.position.z - 0.4;
      frame.adoptModelMeshes([added]);
      const adopted = snapshot([mesh, added]);
      added.visible = false;
      const hidden = snapshot([mesh, added]);
      frame.forgetModelMeshes([added]); added.visible = true;
      const removed = snapshot();
      frame.rotateModelQuarterTurn([mesh]);
      const rotated = snapshot();
      frame.resetView();
      const restored = snapshot();
      controls.target.set(0, 0, 0); camera.position.set(0, 0, 0.02); controls.update();
      const close = snapshot();
      camera.position.set(0, 0, 0); controls.target.set(0, 0, -1); controls.update();
      const inside = snapshot();
      mesh.visible = false;
      const empty = snapshot();
      frame.resetModelOrientation();
      const replacement = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1));
      replacement.position.z = 20;
      frame.fitTo([replacement]);
      const switched = snapshot([replacement]);
      return {minimumNear, fitted, distant, panned, translated, adopted, hidden,
        removed, rotated, restored, close, inside, empty, switched};
    }""")
    for name in ('fitted', 'distant', 'panned', 'translated', 'adopted',
                 'hidden', 'removed', 'rotated', 'restored', 'close', 'switched'):
        state = result[name]
        assert 0 < state['near'] < state['far'], name
        assert all(0 <= depth <= 1 for depth in state['depths']), name
    assert result['distant']['near'] > result['fitted']['near'] * 3
    assert result['panned']['near'] == pytest.approx(result['distant']['near'])
    assert result['translated']['near'] == pytest.approx(result['panned']['near'])
    assert result['adopted']['near'] < result['translated']['near'] / 10
    assert result['hidden']['near'] == pytest.approx(result['removed']['near'])
    assert result['restored'] == pytest.approx(result['fitted'])
    assert 0 < result['close']['near'] < 0.01
    assert result['inside']['near'] == pytest.approx(result['minimumNear'])
    assert result['empty']['near'] == pytest.approx(result['minimumNear'])


def test_key_light_projection_and_visible_floor_follow_mesh_changes(module_page):
    result = module_page.evaluate("""async () => {
      const THREE = await import('three/webgpu');
      const {createCharacterShadowController} = await import('./js/scene/character-shadow-controller.js');
      const {createKeyLightController} = await import('./js/scene/key-light-controller.js');
      const renderer = {shadowMap: {}, domElement: document.createElement('canvas')};
      const scene = new THREE.Scene(), light = new THREE.DirectionalLight();
      scene.add(light, light.target);
      const camera = new THREE.PerspectiveCamera();
      camera.position.set(0, 0, 10);
      const controls = {target: new THREE.Vector3(7, 4, -3)};
      const mesh = new THREE.Mesh(new THREE.BoxGeometry(2, 4, 2));
      mesh.position.copy(controls.target);
      const alternate = new THREE.Mesh(mesh.geometry);
      alternate.position.set(7, -6, -3); alternate.visible = false;
      const grid = new THREE.GridHelper();
      scene.add(mesh, alternate, grid);
      const key = createKeyLightController({scene, light, renderer, camera, controls});
      const shadow = createCharacterShadowController({scene, light, renderer, grid});
      shadow.setMeshes([mesh, alternate]);
      const box = new THREE.Box3().setFromObject(mesh);
      key.rebase(box.getSize(new THREE.Vector3()).length()); key.update();
      const projection = () => ({position: light.position.toArray(), target: light.target.position.toArray()});
      const before = projection();
      shadow.update();
      const after = projection();
      const direction = new THREE.Vector3().fromArray(before.position).sub(controls.target).normalize();
      light.position.copy(controls.target).addScaledVector(direction, 0.05);
      key.update(); shadow.update();
      const depths = [];
      for (const x of [box.min.x, box.max.x]) for (const y of [box.min.y, box.max.y])
        for (const z of [box.min.z, box.max.z])
          depths.push(new THREE.Vector3(x,y,z).project(light.shadow.camera).z);
      const floors = [];
      for (const alternativeVisible of [false, true, false]) {
        mesh.visible = !alternativeVisible; alternate.visible = alternativeVisible;
        shadow.invalidateVisibility(); shadow.update();
        const ground = scene.children.find(object => object.userData.isViewerGround);
        floors.push({grid: grid.position.y, ground: ground.position.y});
      }
      mesh.visible = false;
      shadow.invalidateVisibility(); shadow.update();
      const clearedFloors = [grid.position.y];
      mesh.visible = true;
      shadow.invalidateVisibility(); shadow.update();
      clearedFloors.push(grid.position.y);
      shadow.reset(); clearedFloors.push(grid.position.y);
      return {before, after, depths, floors, clearedFloors, parallel: light.shadow.camera.isOrthographicCamera};
    }""")
    assert result['after'] == result['before']
    assert result['parallel']
    assert all(abs(depth) <= 1 for depth in result['depths'])
    for floor, expected in zip(result['floors'], [2, -8, 2]):
        assert floor['grid'] == expected
        assert expected - 0.01 < floor['ground'] < expected
    assert result['clearedFloors'] == [0, 2, 0]


def test_preference_bridge_readiness_and_serial_writes_preserve_latest_choices(module_page):
    result = module_page.evaluate("""async () => {
      const {createPreferencePersistence} = await import('./js/ui/preference-persistence.js');
      const applied = [], writes = [], stored = {};
      let loadResolve, writeResolve, firstWriteResolve, finishedResolve;
      let active = 0, maxActive = 0, opacity = 58;
      const firstWrite = new Promise(resolve => firstWriteResolve = resolve);
      const finished = new Promise(resolve => finishedResolve = resolve);
      const prefs = createPreferencePersistence({getMethod:'get_viewer_preferences',
        setMethod:'set_viewer_preferences', apply:(key,value)=>applied.push([key,value])});
      prefs.change('grid', false);
      prefs.change('environment', 'studio', {persist:false});
      window.pywebview = {api:{
        get_viewer_preferences:()=>new Promise(resolve => loadResolve=resolve),
        set_viewer_preferences:async changes=>{
          active++; maxActive=Math.max(maxActive,active); writes.push(changes);
          if(writes.length===1) {
            firstWriteResolve(); await new Promise(resolve=>writeResolve=resolve);
          }
          Object.assign(stored,changes); active--; return {value:{...stored}};
        },
        get_panel_opacity:async()=>({value:opacity}),
        set_panel_opacity:async value=>{
          active++; maxActive=Math.max(maxActive,active); opacity=value; active--;
          if(value===58) finishedResolve(); return {value};
        },
      }};
      window.dispatchEvent(new Event('pywebviewready'));
      await Promise.resolve();
      loadResolve({value:{grid:true,environment:'indoor',bloom:true}});
      await firstWrite;
      const panel = createPreferencePersistence({getMethod:'get_panel_opacity',
        setMethod:'set_panel_opacity', key:'panelOpacity', apply:()=>{}});
      panel.change('panelOpacity',35);
      await Promise.resolve();
      panel.change('panelOpacity',58);
      prefs.change('grid',true);
      prefs.change('wireframe',true);
      prefs.change('environment','studio');
      writeResolve(); await finished; await prefs.ready; await panel.ready;
      const count = writes.length, restored = {};
      window.pywebview.api.get_viewer_preferences = async()=>({value:{...stored}});
      const restoredPrefs = createPreferencePersistence({getMethod:'get_viewer_preferences',
        setMethod:'set_viewer_preferences', apply:(key,value)=>restored[key]=value});
      await restoredPrefs.ready;
      return {applied,writes,stored,restored,maxActive,opacity,noStartupSave:writes.length===count};
    }""")
    assert result == {
        'applied': [['bloom', True]],
        'writes': [{'grid': False}, {'grid': True, 'wireframe': True, 'environment': 'studio'}],
        'stored': {'grid': True, 'wireframe': True, 'environment': 'studio'},
        'restored': {'grid': True, 'wireframe': True, 'environment': 'studio'},
        'maxActive': 1, 'opacity': 58, 'noStartupSave': True,
    }


def test_required_persistence_reports_failures_and_retains_pending_choices(module_page):
    _stub_mesh_scene(module_page)
    result = module_page.evaluate("""async () => {
      const {createPreferencePersistence} = await import('./js/ui/preference-persistence.js');
      const {saveTextureState} = await import('./js/mesh/mesh-texture-state.js');
      const {persistCurrentMeshColorAdjustment, flushMeshColorAdjustmentPersistence} =
        await import('./js/mesh/mesh-color-session.js');
      const {viewerState} = await import('./js/app/state.js');
      const {initPersistenceFeedback} = await import('./js/ui/persistence-feedback.js');
      document.body.innerHTML = '<div id="dialog-backdrop" class="show"></div>';
      initPersistenceFeedback();
      const errors = [], writes = [];
      window.addEventListener('viewer-persistence-error', event => errors.push(event.detail.message));
      window.pywebview = {api:{}};
      const api = window.pywebview.api;
      const preferences = createPreferencePersistence({getMethod:'get_viewer_preferences',
        setMethod:'set_viewer_preferences', apply:()=>{}});
      preferences.change('grid', false);
      await preferences.ready;
      Object.assign(viewerState, {currentModPath:'mod-01', currentSource:{kind:'mod', readOnly:false}});
      const mesh = {userData:{modPath:'mod-01', metadataKey:'mesh-01'}};
      const rejected = async request => {try {await request; return false;} catch {return true;}};
      const missingTexture = await rejected(saveTextureState('mod-01'));
      const missingColor = await rejected(persistCurrentMeshColorAdjustment(mesh));
      const flushRejected = await rejected(flushMeshColorAdjustmentPersistence(mesh));
      let resolveWrite;
      const saved = new Promise(resolve => resolveWrite=resolve);
      api.get_viewer_preferences = async()=>({value:{grid:true}});
      api.set_viewer_preferences = async changes=>{writes.push(changes); resolveWrite(); return {};};
      preferences.change('wireframe', true); await saved;
      api.save_mesh_textures = async()=>({error:'fixture backend error'});
      const backendError = await rejected(saveTextureState('mod-01'));
      api.save_mesh_color_adjustment = async()=>{throw new Error('fixture rejected write');};
      const writeError = await rejected(persistCurrentMeshColorAdjustment(mesh));
      const count = errors.length;
      const notice = document.getElementById('persistence-feedback');
      const {setLocale, t} = await import('./js/i18n/index.js');
      setLocale('ja');
      const feedback = notice.querySelector('span').textContent === t('errors.viewerPersistence') &&
        document.querySelectorAll('#persistence-feedback').length === 1 &&
        document.getElementById('dialog-backdrop').classList.contains('show');
      notice.querySelector('button').click();
      const dismissed = document.getElementById('persistence-feedback') === null;
      viewerState.currentSource = {kind:'mod', readOnly:true};
      const archiveBlocked = saveTextureState('mod-01') === undefined &&
        persistCurrentMeshColorAdjustment(mesh) === null;
      viewerState.currentSource = {kind:'asset'};
      const assetBlocked = saveTextureState('mod-01') === undefined &&
        persistCurrentMeshColorAdjustment(mesh) === null;
      return {missingTexture, missingColor, flushRejected, backendError, writeError,
        writes, archiveBlocked, assetBlocked, feedback, dismissed, errorCount:count, unchanged:errors.length===count};
    }""")
    assert result == {
        'missingTexture': True, 'missingColor': True, 'flushRejected': True,
        'backendError': True, 'writeError': True,
        'writes': [{'grid': False, 'wireframe': True}],
        'feedback': True, 'dismissed': True,
        'archiveBlocked': True, 'assetBlocked': True, 'errorCount': 5, 'unchanged': True,
    }


def test_bridge_readiness_is_shared_by_folders_and_presets_and_reports_failures(module_page):
    result = module_page.evaluate("""async () => {
      const html = await (await fetch('./index.html')).text();
      document.body.innerHTML = new DOMParser().parseFromString(html, 'text/html').body.innerHTML;
      const {bridgeReady} = await import('./js/app/bridge.js');
      const {initModFolderPanel} = await import('./js/panels/mod-folder-panel.js');
      const {initAssetFolderPanel} = await import('./js/panels/asset-folder-panel.js');
      const {createRigPresetSession} = await import('./js/weight-rig/rig-preset-session.js');
      const {createRigRuntimeState} = await import('./js/weight-rig/weight-runtime.js');
      const calls = [];
      const nativeAdd = window.addEventListener.bind(window);
      let listeners = 0;
      window.addEventListener = (name, ...args) => {
        if (name === 'pywebviewready') listeners++;
        return nativeAdd(name, ...args);
      };
      const folders = initModFolderPanel({switchMod:()=>{}, onRegistryChanged:()=>{}});
      const assets = initAssetFolderPanel();
      const runtime = createRigRuntimeState();
      const signature = JSON.stringify(['source-01#bone=1']);
      const rig = {joints:[{jointId:1,signature}], poseRotationByJointId:new Map()};
      Object.assign(runtime.modelRigState, {loaded:true,explicitRootSignatures:new Set([signature])});
      const presets = createRigPresetSession({state:runtime.rigPresetState,
        getModelRig:()=>rig, getModelRigState:()=>runtime.modelRigState,
        getKnownMeshes:()=>[{userData:{modPath:'mod-01'}}]});
      const queued = presets.save('pose-01');
      const shared = bridgeReady() === bridgeReady();
      await Promise.resolve();
      const waiting = calls.length === 0 && runtime.rigPresetState.loading;
      const api = {
        get_mod_folders:async()=>{calls.push('folders');return {folders:[]};},
        get_asset_folders:async()=>{calls.push('assets');return {folders:[]};},
        save_rig_pose_preset:async(path,preset)=>{calls.push('save');return {saved:true};},
      };
      window.pywebview = {api};
      window.dispatchEvent(new Event('pywebviewready'));
      const saved = await queued;
      await Promise.all([folders.refresh(),assets.refresh()]);
      const id = saved.preset.id;
      const missing = await presets.remove(id);
      const presetFailure = !missing.saved && !!runtime.rigPresetState.error;
      delete api.get_asset_folders;
      let folderFailure = false;
      try {await assets.refresh();} catch {folderFailure=true;}
      api.delete_rig_pose_preset = async()=>({saved:true});
      const retried = await presets.remove(id);
      const retry = retried.saved && runtime.rigPresetState.presets.length===0;
      presets.setMetadata({version:1,presets:[saved.preset]});
      let releaseWrite, entered;
      const started = new Promise(resolve=>entered=resolve);
      api.rename_rig_pose_preset = async()=>{entered();return new Promise(resolve=>releaseWrite=resolve);};
      const oldRename = presets.rename(id,'pose-renamed');
      await started;
      presets.setMetadata({version:1,presets:[]});
      releaseWrite({saved:true,presets:[saved.preset]});
      const staleCompletion = (await oldRename).stale && runtime.rigPresetState.presets.length===0;
      presets.setMetadata({version:1,presets:[saved.preset]});
      let rejectWrite, deleting;
      const deleteStarted = new Promise(resolve=>deleting=resolve);
      api.delete_rig_pose_preset = async()=>{deleting();return new Promise((resolve,reject)=>rejectWrite=reject);};
      const oldDelete = presets.remove(id);
      await deleteStarted;
      presets.setMetadata({version:1,presets:[]});
      rejectWrite(new Error('fixture failure'));
      await oldDelete;
      const staleFailure = runtime.rigPresetState.error===null && !runtime.rigPresetState.loading;
      const staleWrite = presets.save('pose-02');
      presets.reset();
      const stale = await staleWrite;
      return {listeners,shared,waiting,saved:saved.saved,folderFailure,presetFailure,
        retry,staleCompletion,staleFailure,
        stale:stale.stale && !stale.saved, writes:calls.filter(call=>call==='save').length};
    }""")
    assert result == {'listeners': 1, 'shared': True, 'waiting': True, 'saved': True,
                      'folderFailure': True, 'presetFailure': True, 'retry': True,
                      'stale': True, 'staleCompletion': True, 'staleFailure': True, 'writes': 1}


def test_geometry_decoder_rejects_invalid_references_and_uses_shared_views(module_page):
    result = module_page.evaluate("""async () => {
      const {setGeometryBlob, decodeF32, decodeU32, decodeI32} = await import('./js/textures/decode.js');
      const rejected = action => {try {action(); return false;} catch {return true;}};
      const unloaded = rejected(()=>decodeF32({offset:0,length:4}));
      const blob = new ArrayBuffer(16); new Float32Array(blob).set([1,2,3,4]);
      setGeometryBlob(blob);
      const invalid = ['AAAA', null, {}, {offset:-4,length:4}, {offset:1,length:4},
        {offset:0,length:3}, {offset:0,length:20}, {offset:Infinity,length:4}];
      const refs = invalid.map(value => rejected(()=>decodeF32(value)));
      const view = decodeF32({offset:4,length:8});
      const shared = [decodeU32,decodeI32].every(decode=>decode({offset:0,length:4}).buffer===blob);
      new Float32Array(blob)[1] = 7;
      return {unloaded, refs, shared:shared && view.buffer===blob, values:[...view]};
    }""")
    assert result == {'unloaded': True, 'refs': [True] * 8, 'shared': True, 'values': [7, 3]}


def test_refresh_meshes_requires_options_and_accepts_explicit_intent(module_page):
    _stub_mesh_scene(module_page)
    result = module_page.evaluate("""async () => {
      const {refreshMeshes} = await import('./js/mesh/mesh-state.js');
      let omitted = false, nullOptions = false;
      try {refreshMeshes();} catch (error) {omitted=error instanceof TypeError;}
      try {refreshMeshes(null);} catch (error) {nullOptions=error instanceof TypeError;}
      const empty = refreshMeshes({});
      const forced = refreshMeshes({force:{visibility:true,textures:true,shapes:true}});
      return {omitted, nullOptions, empty, forced};
    }""")
    unchanged = {'visibilityChanged': False, 'texturesChanged': False,
                 'shapesChanged': False, 'changedMeshes': []}
    assert result == {'omitted': True, 'nullOptions': True, 'empty': unchanged, 'forced': unchanged}


def test_appearance_controls_share_persistence_and_save_explicit_defaults(module_page):
    module_page.evaluate("""async () => {
      document.body.innerHTML = `<div class="appearance-wrap">
        <button id="appearance-btn"></button><div id="appearance-popover" hidden>
        <label for="panel-opacity"></label><input id="panel-opacity" type="range" min="0" max="100">
        <output id="panel-opacity-value"></output></div></div>
        <div id="language-control"><button id="language-btn"></button>
        <div id="language-popover" hidden><select id="app-language">
        <option value="en">English</option><option value="ja">Japanese</option>
        <option value="es">Spanish</option></select></div></div>`;
      const saved = window.__preferences = {panelOpacity:35, language:'ja', writes:[]};
      window.pywebview = {api:{
        get_panel_opacity:async()=>({value:saved.panelOpacity}),
        set_panel_opacity:async value=>{saved.panelOpacity=value; saved.writes.push(['opacity',value]); return {value};},
        get_language:async()=>({value:saved.language}),
        set_language:async value=>{saved.language=value; saved.writes.push(['language',value]); return {value};},
      }};
      const appearance = await import('./js/ui/appearance.js');
      appearance.initPanelOpacityControl(); appearance.initLanguageControl();
    }""")
    module_page.wait_for_function('document.getElementById("panel-opacity").value === "35" && document.getElementById("app-language").value === "ja"')
    assert module_page.evaluate('window.__preferences.writes') == []
    module_page.evaluate("""() => {
      const opacity=document.getElementById('panel-opacity'); opacity.value='0';
      opacity.dispatchEvent(new Event('input',{bubbles:true}));
    }""")
    assert module_page.evaluate('window.__preferences.writes') == []
    assert module_page.evaluate('document.documentElement.style.getPropertyValue("--panel-opacity")') == '0'
    module_page.evaluate("""() => {
      const opacity=document.getElementById('panel-opacity');
      opacity.dispatchEvent(new Event('change',{bubbles:true}));
      const language=document.getElementById('app-language'); language.value='es';
      language.dispatchEvent(new Event('change',{bubbles:true}));
    }""")
    module_page.wait_for_function('window.__preferences.writes.length === 2')
    module_page.evaluate("""() => {
      const opacity=document.getElementById('panel-opacity'); opacity.value='58';
      opacity.dispatchEvent(new Event('input',{bubbles:true}));
      opacity.dispatchEvent(new Event('change',{bubbles:true}));
      const language=document.getElementById('app-language'); language.value='en';
      language.dispatchEvent(new Event('change',{bubbles:true}));
    }""")
    module_page.wait_for_function('window.__preferences.writes.length === 4')
    assert module_page.evaluate('window.__preferences') == {
        'panelOpacity': 58, 'language': 'en',
        'writes': [['opacity', 0], ['language', 'es'], ['opacity', 58], ['language', 'en']],
    }


def _prepare_rig_overlay(page, orthographic=False):
    page.evaluate("""async orthographic => {
      const THREE = await import('three/webgpu');
      const overlay = await import('./js/scene/rig-overlay-controller.js');
      const canvas = document.createElement('canvas');
      canvas.style.cssText = 'width:800px;height:600px';
      document.body.appendChild(canvas);
      const captured = new Set();
      canvas.setPointerCapture = id => captured.add(id);
      canvas.hasPointerCapture = id => captured.has(id);
      canvas.releasePointerCapture = id => captured.delete(id);
      const camera = orthographic ? new THREE.OrthographicCamera(-2,2,1.5,-1.5,0.1,100)
        : new THREE.PerspectiveCamera(50, 4/3, 0.1, 100);
      camera.position.z = 5; camera.updateMatrixWorld();
      const scene = new THREE.Scene(), mesh = new THREE.Object3D();
      scene.add(mesh);
      const positions = {chest:[0,0.6,0], pelvis:[0,-0.4,0], neck:[0,0.8,0], head:[0,1,0],
        leftShoulder:[-0.35,0.6,0], leftElbow:[-0.6,0.3,0], leftHand:[-0.9,0,0],
        rightShoulder:[0.35,0.6,0], rightElbow:[0.6,0.3,0], rightHand:[0.9,0,0],
        leftHip:[-0.2,-0.4,0], leftKnee:[-0.2,-0.8,0], leftFoot:[-0.2,-1.2,0],
        rightHip:[0.2,-0.4,0], rightKnee:[0.2,-0.8,0], rightFoot:[0.2,-1.2,0]};
      const rig = {available:true, accepted:true, source:'humanoid_control_rig',
        controls:Object.fromEntries(Object.entries(positions).map(([key,position]) => [key,{position}]))};
      const joints = [0,1,2].map(jointId => ({jointId, restCenter:[jointId*0.5,0,0], restPivot:[jointId*0.5,0,0]}));
      const snapshot = {selectedJointId:1, rotationSnapDegrees:15, jointPickIntent:null,
        model:{key:'model-01', structureRevision:1, joints, humanoidControlRig:rig,
          components:[{rootId:0,nodeIds:[0,1,2]}], forestEdges:[{jointA:0,jointB:1},{jointA:1,jointB:2}]},
        ik:{enabled:false, available:true, controlKeys:['leftShoulder','leftElbow','leftHand']},
        humanoidRigEdit:{editing:false, controls:structuredClone(rig.controls), mappedJointIdByControl:{}}};
      const arcball = Object.assign(new THREE.EventDispatcher(), {enabled:true,
        leftRotation:true, unsetMouseAction(){this.leftRotation=false;}, setMouseAction(){this.leftRotation=true;}});
      const calls = {rotations:[], solves:[], finished:[], carries:[], drafts:[], picks:[], surfaces:[], unavailable:0};
      const ctx = window.__overlay = {THREE, overlay, canvas, camera, scene, mesh, snapshot, arcball, captured, calls,
        frameReads:0, frames:new Map(joints.map(joint => [joint.jointId,{pivot:[...joint.restPivot],
          gizmoRotation:[0,0,0,1], parentRotation:[0,0,0,1], restRotation:[0,0,0,1]}]))};
      ctx.notify = () => window.dispatchEvent(new CustomEvent('mod-viewer-model-rig-changed',{detail:ctx.snapshot}));
      ctx.pointer = (type, id, x, y, button=0, extra={}) => canvas.dispatchEvent(new PointerEvent(type,
        {pointerId:id, clientX:x, clientY:y, button, bubbles:true, cancelable:true, ...extra}));
      ctx.screen = point => overlay.projectRigPointToClient({point,camera,canvas,worldMatrix:ctx.controller.group.matrixWorld});
      ctx.controller = overlay.createRigOverlayController({scene,camera,canvas,arcballControls:arcball,
        getMeshes:()=>[mesh], getRigState:()=>ctx.snapshot, getHumanoidRigEditSnapshot:()=>ctx.snapshot.humanoidRigEdit,
        getRigJointPoseFrame:id=>{ctx.frameReads++; return ctx.frames.get(id);},
        setRigJointRotation:(id,q,options)=>calls.rotations.push({id,q:q.toArray(),options}),
        solveRigIkTarget:(target,options)=>{calls.solves.push({target,options});
          ctx.snapshot.model.humanoidControlRig.controls[ctx.snapshot.ik.controlKeys[2]].position=[...target]; ctx.notify();},
        finishRigJointPose:id=>calls.finished.push(id), onRigJointPicked:id=>calls.picks.push(id),
        onRigSurfacePickRequested:point=>calls.surfaces.push(point),
        onTransformControlsUnavailable:()=>calls.unavailable++,
        beginHumanoidControlCarry:key=>{calls.carries.push('begin'); ctx.snapshot.humanoidRigEdit.carryingControlKey=key; ctx.notify(); return true;},
        finishHumanoidControlCarry:()=>{calls.carries.push('finish'); ctx.snapshot.humanoidRigEdit.carryingControlKey=null; ctx.notify();},
        cancelHumanoidControlCarry:()=>{calls.carries.push('cancel'); ctx.snapshot.humanoidRigEdit.carryingControlKey=null; ctx.notify();},
        updateHumanoidControlDraft:(key,point,options)=>{calls.drafts.push({key,point,options});
          ctx.snapshot.humanoidRigEdit.controls[key].position=point;},
      });
      ctx.controller.refresh();
    }""", orthographic)


def test_rig_overlay_pose_edit_and_disposal_restore_interaction_ownership(module_page):
    _prepare_rig_overlay(module_page)
    result = module_page.evaluate("""async () => {
      const ctx = window.__overlay, {controller,snapshot,arcball,canvas,calls} = ctx;
      const controls = await controller.ensureTransformControls();
      ctx.pointer('pointerdown',7,400,300);
      controls.dragging=true;
      const blocked = !arcball.enabled && ctx.overlay.isRigTransformInteractionActive();
      snapshot.humanoidRigEdit.editing=true; ctx.notify();
      const editRestored = arcball.enabled && !ctx.overlay.isRigTransformInteractionActive()
        && !controller.getDebugState().controlsAttached && ctx.captured.size===0;
      const point = ctx.screen(snapshot.humanoidRigEdit.controls.leftHand.position);
      ctx.pointer('pointerdown',11,point.x,point.y);
      const carrying = controller.getDebugState().carryingControlKey==='leftHand'
        && !arcball.leftRotation && ctx.captured.has(11);
      controller.dispose(); controller.dispose();
      ctx.pointer('pointermove',11,point.x+20,point.y);
      return {blocked,editRestored,carrying, enabled:arcball.enabled,leftRotation:arcball.leftRotation,
        captures:ctx.captured.size,cursor:canvas.style.cursor,carries:calls.carries,
        noDraftAfterDispose:calls.drafts.length===0,
        removed:controller.group.parent===null, interaction:ctx.overlay.isRigTransformInteractionActive()};
    }""")
    assert result == {'blocked':True,'editRestored':True,'carrying':True,'enabled':True,'leftRotation':True,
                      'captures':0,'cursor':'','carries':['begin','cancel'],'noDraftAfterDispose':True,
                      'removed':True,'interaction':False}


def test_rig_overlay_joint_pick_uses_own_pointer_and_releases_capture_on_exit(module_page):
    _prepare_rig_overlay(module_page)
    result = module_page.evaluate("""async () => {
      const ctx = window.__overlay; await ctx.controller.ensureTransformControls();
      ctx.snapshot.jointPickIntent={kind:'select'}; ctx.notify();
      const p = ctx.screen([0.5,0,0]);
      ctx.pointer('pointerdown',11,p.x,p.y);
      ctx.pointer('pointerup',22,p.x,p.y);
      const foreignIgnored = ctx.calls.picks.length===0 && ctx.captured.has(11);
      ctx.pointer('pointerup',11,p.x,p.y);
      ctx.pointer('pointerdown',33,p.x,p.y);
      ctx.snapshot.jointPickIntent=null; ctx.notify();
      const released = ctx.captured.size===0;
      ctx.controller.dispose();
      return {foreignIgnored,released,picks:ctx.calls.picks,surfaces:ctx.calls.surfaces.length};
    }""")
    assert result == {'foreignIgnored':True,'released':True,'picks':[1],'surfaces':0}


def test_rig_overlay_pick_and_control_carry_preserve_navigation_and_magnetic_exclusions(module_page):
    _prepare_rig_overlay(module_page)
    result = module_page.evaluate("""async () => {
      const ctx=window.__overlay, {controller,snapshot,calls}=ctx;
      await controller.ensureTransformControls();
      snapshot.jointPickIntent={kind:'select'}; ctx.notify();
      const joint=ctx.screen([0.5,0,0]);
      ctx.pointer('pointermove',11,joint.x,joint.y);
      const acquired=controller.getDebugState().hoveredJointId===1;
      ctx.pointer('pointermove',11,joint.x+10,joint.y);
      const retained=controller.getDebugState().hoveredJointId===1;
      ctx.pointer('pointermove',11,joint.x+14,joint.y);
      const released=controller.getDebugState().hoveredJointId===null;
      ctx.pointer('pointerdown',11,joint.x,joint.y,0,{altKey:true});
      const altFree=ctx.captured.size===0;
      ctx.pointer('pointerdown',11,20,20); ctx.pointer('pointerup',11,20,20);
      ctx.pointer('pointerdown',11,joint.x,joint.y); ctx.pointer('pointerup',11,joint.x+5,joint.y);
      const surfaceOnly=calls.surfaces.length===1 && calls.picks.length===0;
      snapshot.jointPickIntent=null; snapshot.humanoidRigEdit.editing=true;
      snapshot.humanoidRigEdit.mappedJointIdByControl={leftShoulder:0,leftHand:2}; ctx.notify();
      const hand=ctx.screen(snapshot.humanoidRigEdit.controls.leftHand.position);
      ctx.pointer('pointerdown',11,hand.x,hand.y); ctx.pointer('pointerup',11,hand.x,hand.y);
      const carrySurvivesRelease=controller.getDebugState().carryingControlKey==='leftHand' && ctx.captured.size===0;
      const excluded=ctx.screen([0,0,0]); ctx.pointer('pointermove',11,excluded.x,excluded.y);
      const otherMappingExcluded=calls.drafts.at(-1).options.candidateJointId!==0;
      const mappedDistance=calls.drafts.at(-1).options.mappedDistance;
      ctx.pointer('pointermove',11,joint.x,joint.y);
      const nearest=calls.drafts.at(-1).options;
      const beforeNavigation=calls.drafts.length;
      ctx.pointer('pointerdown',22,joint.x,joint.y,2);
      ctx.camera.position.x+=0.1; ctx.camera.updateMatrixWorld(); ctx.arcball.dispatchEvent({type:'change'});
      ctx.pointer('pointermove',11,joint.x+10,joint.y,0,{buttons:2});
      const navigationFree=calls.drafts.length===beforeNavigation && ctx.arcball.enabled;
      ctx.pointer('pointerup',22,joint.x+10,joint.y,2);
      ctx.pointer('pointerdown',11,joint.x+10,joint.y);
      const finished=ctx.arcball.leftRotation && controller.getDebugState().carryingControlKey===null;
      const next=ctx.screen(snapshot.humanoidRigEdit.controls.leftHand.position);
      ctx.pointer('pointerdown',11,next.x,next.y);
      document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',cancelable:true}));
      const cancelled=ctx.arcball.leftRotation && ctx.captured.size===0 && controller.getDebugState().carryingControlKey===null;
      controller.dispose();
      return {acquired,retained,released,altFree,surfaceOnly,carrySurvivesRelease,otherMappingExcluded,
        mappedDistanceFinite:Number.isFinite(mappedDistance),magneticJoint:nearest.candidateJointId,
        magneticDistance:nearest.candidateDistance,navigationFree,finished,cancelled,carries:calls.carries};
    }""")
    assert all(result[key] for key in ('acquired','retained','released','altFree','surfaceOnly',
                                     'carrySurvivesRelease','otherMappingExcluded','mappedDistanceFinite',
                                     'navigationFree','finished','cancelled')), result
    assert result['magneticJoint'] == 1
    assert result['magneticDistance'] == pytest.approx(0)
    assert result['carries'] == ['begin','finish','begin','cancel']


@pytest.mark.parametrize('orthographic', [False, True])
def test_rig_overlay_projection_and_pick_ties_are_deterministic(module_page, orthographic):
    _prepare_rig_overlay(module_page, orthographic)
    result = module_page.evaluate("""async () => {
      const ctx=window.__overlay; await ctx.controller.ensureTransformControls();
      const params={camera:ctx.camera,canvas:ctx.canvas,worldMatrix:ctx.controller.group.matrixWorld};
      const pointer=ctx.screen([0,0,0]);
      const ties=ctx.overlay.findNearestRigJoint({...params,pointer,candidates:[
        {jointId:2,pivot:[0,0,0]},{jointId:1,pivot:[0,0,0]}]});
      const depth=ctx.overlay.findNearestRigJoint({...params,pointer,candidates:[
        {jointId:1,pivot:[0,0,0]},{jointId:5,pivot:[0,0,0.5]}]});
      const control=ctx.overlay.findNearestHumanoidControl({...params,pointer,controls:{
        control02:[0,0,0],control01:[0,0,0]}});
      const clipped=ctx.overlay.projectRigPointToClient({...params,point:[0,0,6]})===null;
      const outside=ctx.overlay.findNearestRigJoint({...params,pointer:{x:-100,y:-100},
        candidates:[{jointId:0,pivot:[0,0,0]}]})===null;
      ctx.controller.dispose();
      return {tie:ties.jointId,ordered:ties.candidates.map(item=>item.jointId),depth:depth.jointId,
        control:control.key,clipped,outside};
    }""")
    assert result == {'tie':1,'ordered':[1,2],'depth':5,'control':'control01','clipped':True,'outside':True}


def test_rig_overlay_fk_ik_gestures_keep_proxy_ownership_and_finalize_once(module_page):
    _prepare_rig_overlay(module_page)
    result = module_page.evaluate("""async () => {
      const ctx=window.__overlay, {THREE,controller,snapshot,calls,frames}=ctx;
      const controls=await controller.ensureTransformControls();
      const parent=new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1,0,0),0.3);
      const rest=new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0,1,0),0.2);
      const delta=new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0,0,1),0.4);
      frames.get(1).parentRotation=parent.toArray(); frames.get(1).restRotation=rest.toArray(); ctx.notify();
      ctx.pointer('pointerdown',11,400,300);
      controls.dragging=true;
      ctx.pointer('pointerup',22,400,300);
      const foreignIgnored=controls.dragging && !ctx.arcball.enabled && ctx.captured.has(11);
      controls.object.quaternion.copy(parent).multiply(delta).multiply(rest);
      const owned=controls.object.quaternion.clone();
      controls.dispatchEvent({type:'objectChange'});
      window.dispatchEvent(new CustomEvent('mod-viewer-model-rig-pose-changed',{detail:{jointId:1}}));
      ctx.notify();
      const preserved=controls.object.quaternion.angleTo(owned)<1e-6;
      const converted=new THREE.Quaternion(...calls.rotations[0].q).angleTo(delta)<1e-6;
      const snap=controls.rotationSnap;
      controls.dragging=false;
      controls.dispatchEvent({type:'dragging-changed',value:false});
      ctx.pointer('pointerup',11,400,300);
      const releaseDeferred=ctx.overlay.isRigTransformInteractionActive();
      snapshot.ik.enabled=true; snapshot.selectedJointId=null; ctx.notify();
      controls.dragging=true;
      await Promise.resolve();
      const newerProtected=ctx.overlay.isRigTransformInteractionActive();
      const target=[-0.8,0.1,0.05]; controls.object.position.set(...target);
      controls.dispatchEvent({type:'objectChange'});
      const ikOwned=controls.object.position.toArray();
      controls.dragging=false;
      controls.dispatchEvent({type:'dragging-changed',value:false});
      await Promise.resolve();
      const endpoint=controls.object.position.toArray();
      const finished={fk:[...calls.finished], ik:calls.solves.map(call=>call.options.dragging)};
      const restored=ctx.arcball.enabled && !ctx.overlay.isRigTransformInteractionActive();
      snapshot.ik.enabled=false; snapshot.selectedJointId=1; ctx.notify();
      ctx.pointer('pointerdown',33,400,300); controls.dragging=true;
      ctx.pointer('pointercancel',33,400,300); await Promise.resolve();
      const cancelled=!controls.dragging && ctx.arcball.enabled && !ctx.overlay.isRigTransformInteractionActive()
        && !ctx.captured.has(33) && calls.finished.length===2;
      controller.dispose();
      return {preserved,converted,snap,releaseDeferred,newerProtected,ikOwned,endpoint,finished,restored,foreignIgnored,cancelled};
    }""")
    assert result['preserved'] and result['converted'] and result['foreignIgnored'] and result['cancelled']
    assert result['snap'] == pytest.approx(0.2617993877991494)
    assert result['releaseDeferred'] and result['newerProtected'] and result['restored']
    assert result['ikOwned'] == result['endpoint'] == [-0.8, 0.1, 0.05]
    assert result['finished'] == {'fk':[1], 'ik':[True,False]}


@pytest.mark.parametrize('orthographic', [False, True])
def test_rig_overlay_reuses_visible_geometry_and_keeps_scaled_marker_sizes(module_page, orthographic):
    _prepare_rig_overlay(module_page, orthographic)
    result = module_page.evaluate("""async () => {
      const ctx=window.__overlay, {THREE,controller,snapshot,mesh}=ctx;
      await controller.ensureTransformControls();
      const find=name=>controller.group.getObjectByName(name);
      const lines=controller.group.children[0].children.find(item=>item.isLineSegments);
      const marker=find('viewer-inferred-rig-model-joint-markers');
      const materials=()=>controller.group.children.find(item=>item.name==='viewer-humanoid-control-rig-overlay')
        .children.filter(item=>item.isSprite).map(item=>item.material.uuid);
      const original={geometry:lines.geometry.uuid,materials:JSON.stringify(materials())};
      ctx.frameReads=0;
      window.dispatchEvent(new CustomEvent('mod-viewer-model-rig-pose-changed',{detail:{jointId:1}}));
      const reads=ctx.frameReads;
      const color=new THREE.Color(); marker.getColorAt(0,color);
      const defaultColor=color.toArray();
      snapshot.humanoidRigEdit.editing=true; ctx.notify();
      const reused=lines.geometry.uuid===original.geometry && JSON.stringify(materials())===original.materials;
      snapshot.humanoidRigEdit.candidateJointId=0; ctx.notify();
      marker.getColorAt(0,color); const candidateColor=color.toArray();
      snapshot.humanoidRigEdit.candidateJointId=null;
      const widths=[];
      for(const modelScale of [1,3]) {
        mesh.scale.setScalar(modelScale);
        window.dispatchEvent(new CustomEvent('mod-viewer-model-transform-changed'));
        const sprite=find('viewer-humanoid-point-leftHand');
        const left=sprite.position.clone(), right=sprite.position.clone();
        left.x-=sprite.scale.x/2; right.x+=sprite.scale.x/2;
        widths.push(Math.abs(ctx.screen(right).x-ctx.screen(left).x));
      }
      const pointObjects=[]; controller.group.traverse(item=>{if(item.isPoints)pointObjects.push(item);});
      snapshot.model={...snapshot.model,key:'model-02',joints:snapshot.model.joints.map(joint=>
        ({...joint,restPivot:[joint.jointId,0,0]}))};
      ctx.notify();
      const changedModelRebuilt=controller.getDebugState().rebuildCount===2;
      controller.dispose();
      return {reused,widths,defaultColor,candidateColor,reads,pointObjects:pointObjects.length,changedModelRebuilt};
    }""")
    assert result['reused'] and result['changedModelRebuilt']
    assert result['widths'] == pytest.approx([10,10])
    assert result['defaultColor'] == pytest.approx([0.29,0.42,0.5])
    assert result['candidateColor'] == pytest.approx([1,0.78,0.08])
    assert result['reads'] <= 4
    assert result['pointObjects'] == 1


@pytest.mark.parametrize('interrupt', ['edit', 'dispose', 'failure'])
def test_rig_overlay_lazy_controls_ignore_interrupted_load_then_recover(module_page, interrupt):
    routes = []
    module_page.route('**/TransformControls.js', lambda route: routes.append(route))
    with module_page.expect_request('**/TransformControls.js'):
        _prepare_rig_overlay(module_page)
    module_page.evaluate("""interrupt => {
      const ctx=window.__overlay;
      ctx.pending=ctx.controller.ensureTransformControls();
      if (interrupt==='dispose') ctx.controller.dispose();
      else if (interrupt==='edit') {ctx.snapshot.humanoidRigEdit.editing=true; ctx.notify();}
    }""", interrupt)
    assert len(routes) == 1
    if interrupt == 'failure':
        routes[0].abort()
    else:
        routes[0].continue_()
    result = module_page.evaluate("""async interrupt => {
      const ctx=window.__overlay;
      const pending=await ctx.pending;
      const skipped=pending===null && !ctx.controller.getDebugState().controlsCreated;
      let recovered=true;
      if(interrupt==='edit') {
        ctx.snapshot.humanoidRigEdit.editing=false; ctx.notify();
        recovered=!!(await ctx.controller.ensureTransformControls());
      }
      const unavailable=ctx.calls.unavailable;
      ctx.controller.dispose();
      return {skipped,recovered,unavailable};
    }""", interrupt)
    assert result == {'skipped':True,'recovered':True,'unavailable':int(interrupt == 'failure')}


def test_rig_overlay_picking_follows_runtime_fk_ik_and_reset(module_page):
    _prepare_rig_overlay(module_page)
    result = module_page.evaluate("""async () => {
      const ctx=window.__overlay, {THREE,controller}=ctx;
      const {createRigPoseRuntime}=await import('./js/weight-rig/rig-pose-runtime.js');
      const binding=await import('./js/weight-rig/humanoid-rig-binding.js');
      const ik=await import('./js/weight-rig/humanoid-rig-ik.js');
      const deformation=await import('./js/weight-rig/weight-deformation.js');
      const controlRig=structuredClone(ctx.snapshot.model.humanoidControlRig);
      const keys=['leftShoulder','leftElbow','leftHand'], sourceKey='stream-01.buf';
      const joints=keys.map((key,jointId)=>({jointId,signature:`joint-${jointId}`,
        restPivot:[...controlRig.controls[key].position],
        restCenter:controlRig.controls[key].position.map((v,i)=>v+(i===2?0.05:0)),
        members:[{sourceKey,boneId:jointId}]}));
      const component={componentId:0,rootId:0,nodeIds:[0,1,2],parentById:{0:null,1:0,2:1},
        childrenById:{0:[1],1:[2],2:[]}};
      const source={sourceKey,boneIds:[0,1,2],poseRotationByBoneId:new Map(),
        inferredForest:{components:[component],componentByBoneId:{0:0,1:0,2:0}}};
      const rig={joints,components:[component],componentByJointId:new Map([[0,0],[1,0],[2,0]]),
        inferredForest:{components:[component]},sourceRigs:[source],structureRevision:2,
        edges:[{jointA:0,jointB:1},{jointA:1,jointB:2}],
        centerByJointId:new Map(joints.map(j=>[j.jointId,j.restCenter])),
        jointPivotByJointId:new Map(joints.map(j=>[j.jointId,j.restPivot])),
        restFrameByJointId:new Map(joints.map(j=>[j.jointId,new THREE.Quaternion()])),
        poseRotationByJointId:new Map(),poseTransforms:new Map(),poseRotations:new Map(),
        poseTransformCache:new Map(),
        sourceTransformAliases:new Map(),sourceRotationAliases:new Map(),
        poseActiveVerticesByMesh:new Map(),poseSourceBoneIdsByMesh:new Map(),poseAffectedJointIds:new Set(),
        humanoidControlRig:controlRig};
      rig.defaultComponents=[component]; rig.defaultComponentByJointId=new Map(rig.componentByJointId);
      rig.defaultJointPivotByJointId=new Map(rig.jointPivotByJointId);
      rig.defaultRestFrameByJointId=new Map(rig.restFrameByJointId);
      rig.defaultRestDirectionByJointId=new Map(); rig.defaultRestContinuationChildByJointId=new Map();
      rig.humanoidBinding=binding.buildHumanoidRigBinding({modelRig:rig,controlRig,
        controlMappings:new Map(keys.map((key,jointId)=>[key,{jointId}]))});
      const baseline=new Float32Array(joints.flatMap(j=>j.restPivot));
      const skin={indices:new Uint32Array([0,1,2]),weights:new Float32Array([1,1,1]),influenceCount:1};
      let vertices=new Float32Array(baseline);
      const state={loaded:true,humanoidPose:{},ikEnabled:false,selectedJointId:1,explicitRootSignatures:new Set()};
      const publish=()=>{ctx.snapshot.selectedJointId=state.selectedJointId;
        ctx.snapshot.model={key:'model-01',structureRevision:rig.structureRevision,joints:rig.joints,
          components:rig.components,forestEdges:rig.edges,humanoidControlRig:{...controlRig,
            controls:Object.fromEntries(Object.entries(controlRig.controls).map(([key,control])=>
              [key,{...control,position:state.humanoidPose[key]||control.position}]))}}; ctx.notify();};
      const runtime=createRigPoseRuntime({state,getRig:()=>rig,sourceSkinningRigs:new Map([[sourceKey,source]]),
        skinningRuntime:{applyDeformation:()=>{vertices=deformation.applyWeightedTransformDeformation(
          baseline,skin.indices,skin.weights,1,skin.poseTransforms);return true;},finalizeDeformationGeometry:()=>false},
        physicsRuntime:{forEachRigMesh:(_rig,callback)=>callback(ctx.mesh,skin)},hasActivePhysics:()=>false,
        getModelJointId:(_source,id)=>id,quaternionIsIdentity:q=>Math.abs(q.w)>1-1e-10,
        notifyChanged:publish,notifyPoseChanged:(_source,id)=>window.dispatchEvent(new CustomEvent(
          'mod-viewer-model-rig-pose-changed',{detail:{jointId:id}})),requestRender:()=>{},
        cloneForest:structuredClone,invalidateShadow:()=>{},getPrimaryLimb:()=>({available:true,role:'left_arm',keys}),
        solveControlIk:ik.solveHumanoidControlIk,mergeLimbPose:ik.mergeHumanoidLimbPose});
      ctx.frames={get:id=>runtime.getFrame(id)};
      publish(); await controller.ensureTransformControls();
      const close=(a,b)=>a.every((v,i)=>Math.abs(v-b[i])<1e-5);
      const inspect=()=>{
        ctx.snapshot.jointPickIntent={type:'selected-joint'}; publish();
        const markers=controller.group.getObjectByName('viewer-inferred-rig-model-joint-markers');
        const matrix=new THREE.Matrix4();
        return joints.every(j=>{const frame=runtime.getFrame(j.jointId); markers.getMatrixAt(j.jointId,matrix);
          const transformedCenter=new THREE.Vector3(...j.restCenter).applyMatrix4(
            rig.poseTransforms.get(j.jointId)||new THREE.Matrix4()).toArray();
          const parent=new THREE.Quaternion(...frame.parentRotation), local=rig.poseRotationByJointId.get(j.jointId)||new THREE.Quaternion();
          const recovered=parent.multiply(local).multiply(new THREE.Quaternion(...frame.restRotation));
          return close(frame.pivot,[...vertices.slice(j.jointId*3,j.jointId*3+3)])
            && close(frame.center,transformedCenter) && close(new THREE.Vector3().setFromMatrixPosition(matrix).toArray(),frame.pivot)
            && recovered.angleTo(new THREE.Quaternion(...frame.gizmoRotation))<1e-5;});
      };
      const before=baseline.slice(6,9);
      const fkApplied=runtime.setRotation(1,new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0,0,1),-0.7),{dragging:true});
      const fkFollows=inspect(), fkMoved=!close([...vertices.slice(6,9)],[...before]);
      ctx.snapshot.jointPickIntent=null; publish(); state.ikEnabled=true;
      const target=[...controlRig.controls.leftHand.position]; target[1]+=0.25;target[2]+=0.05;
      const solved=runtime.solveTarget(target);
      const ikFollows=inspect(),ikMoved=!close([...vertices.slice(6,9)],[...before]);
      const hand=ctx.screen(runtime.getFrame(2).pivot);
      ctx.pointer('pointermove',11,hand.x,hand.y);
      const raisedHover=controller.getDebugState().hoveredJointId===2;
      ctx.pointer('pointerdown',11,hand.x,hand.y);ctx.pointer('pointerup',11,hand.x,hand.y);
      const old=ctx.screen(before);ctx.pointer('pointermove',11,old.x,old.y);
      const staleMiss=controller.getDebugState().hoveredJointId!==2;
      const reset=runtime.resetPose(); publish();
      const resetFollows=inspect(),restored=close([...vertices],[...baseline]);
      controller.dispose();
      return {fkApplied,fkFollows,fkMoved,ikApplied:solved.applied,ikFollows,ikMoved,raisedHover,staleMiss,
        picked:ctx.calls.picks,reset,resetFollows,restored,authoredUnchanged:close([...baseline],joints.flatMap(j=>j.restPivot))};
    }""")
    assert result.pop('picked') == [2]
    assert all(result.values()), result


def _stub_mesh_scene(page):
    page.route('**/js/panels/health-report.js', lambda route: route.fulfill(
        content_type='text/javascript', body='export function setHealthReport() {}'))
    page.route('**/js/scene/scene.js', lambda route: route.fulfill(
        content_type='text/javascript', body="""
export const scene = {};
export function invalidateCharacterShadowGeometry() {}
export function invalidateCharacterShadowVisibility() {}
export function forgetModelMeshes() {}
export function resetCharacterShadows() {}
export function resetModelOrientation() {}
export function setAmbientOcclusionSuppressedByWireframe() {}
export function setBloomSuppressedByWireframe() {}
"""))


def _set_geometry_blob(page, geometry):
    page.evaluate("""async bytes => {
      const {setGeometryBlob} = await import('./js/textures/decode.js');
      setGeometryBlob(new Uint8Array(bytes).buffer);
    }""", list(geometry.data))


def _prepare_compute_clock(page):
    page.evaluate("""async () => {
      const THREE = await import('three/webgpu');
      window.__animation = await import('./js/mesh/animation-runtime.js');
      window.__controls = await import('./js/editing/control-state.js');
      const {setGeometryBlob} = await import('./js/textures/decode.js');
      let blob = new Uint8Array();
      const pending = new Map();
      let serial = 0;
      window.requestAnimationFrame = callback => {pending.set(++serial, callback); return serial;};
      window.cancelAnimationFrame = id => pending.delete(id);
      window.__frame = now => {
        const id = Math.min(...pending.keys()), callback = pending.get(id);
        if (!callback) throw new Error('No animation frame scheduled');
        pending.delete(id); callback(now);
      };
      window.__pendingFrames = () => pending.size;
      window.__appendGeometry = array => {
        const bytes = new Uint8Array(array.buffer, array.byteOffset, array.byteLength);
        const offset = blob.length, next = new Uint8Array(offset + bytes.length);
        next.set(blob); next.set(bytes, offset); blob = next;
        setGeometryBlob(blob.buffer);
        return {offset, length: bytes.length};
      };
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.Float32BufferAttribute([0,0,0, 1,0,0, 0,1,0], 3));
      geometry.setAttribute('normal', new THREE.Float32BufferAttribute([0,0,2, 0,0,2, 0,0,2], 3));
      geometry.setIndex([0,1,2]);
      window.__mesh = new THREE.Mesh(geometry);
      window.__mesh.userData.basePositions = new Float32Array(geometry.attributes.position.array);
      window.__position = geometry.attributes.position;
      window.__normal = geometry.attributes.normal;
      window.__literal = value => ({kind: 'literal', value});
      window.__variable = variable => ({kind: 'variable', variable});
      window.__enabled = variable => [{kind: 'compare', op: '==',
        left: window.__variable(variable), right: window.__literal(1)}];
      window.__advance = (variable, speed, conditions) => ({op: 'set', variable, conditions,
        expression: {kind: 'binary', op: '+', left: window.__variable(variable),
          right: {kind: 'binary', op: '*', left: window.__literal(speed), right: {kind: 'dt'}}}});
    }""")


def test_control_replay_and_refresh_preserve_legal_live_values(module_page):
    result = module_page.evaluate("""async () => {
      const state = await import('./js/editing/control-state.js');
      state.resetControlState();
      const condition = (variable, value) => [[{var: variable, value, negate: false}]];
      const controls = {toggles: {key01: {vars: [{var: 'input01', values: ['0', '1']}]}}};
      const rules = [
        {var: 'derived01', value: '1', conditions: condition('input01', '1')},
        {var: 'derived02', value: '1', conditions: condition('derived01', '1')},
      ];
      state.setControlStateRules(rules, {input01: '0', derived01: '0', derived02: '0'}, controls);
      state.setControlValue('input01', '1');
      state.replayControlStateRules();
      const visible = state.dnfSatisfied(condition('derived02', '1'));
      state.reconcileControlState(rules, {input01: '0', derived01: '0', derived02: '0'}, controls);
      const preserved = state.getControlValue('input01');
      controls.toggles.key01.vars[0].values = ['0'];
      state.reconcileControlState([], {input01: '0'}, controls);
      return {visible, preserved, final: state.getControlState()};
    }""")
    assert result == {'visible': True, 'preserved': '1', 'final': {'input01': '0'}}


def test_loose_partition_merge_and_cleanup_preserve_authored_triangle_identity(module_page):
    result = module_page.evaluate("""async () => {
      const THREE = await import('three/webgpu');
      const parts = await import('./js/mesh/loose-parts.js');
      const create = () => {
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute('position', new THREE.Float32BufferAttribute([
          0,0,0, 1,0,0, 0,1,0, 4,0,0, 5,0,0, 4,1,0, 8,0,0, 9,0,0, 8,1,0], 3));
        geometry.setIndex([0,1,2,3,4,5,6,7,8]);
        return new THREE.Mesh(geometry, new THREE.MeshBasicNodeMaterial());
      };
      const source = create(), sibling = create();
      const position = source.geometry.attributes.position;
      const index = source.geometry.index;
      const originalRange = {...source.geometry.drawRange};
      const partition = parts.separateLooseParts(source);
      const other = parts.separateLooseParts(sibling);
      const initial = partition.map(part => part.userData.loosePartTriangles);
      const shared = partition.every(part => part.geometry.attributes.position === position && part.material === source.material);
      const crossSource = parts.canMergeLooseParts([partition[0], other[0]]);
      parts.mergeLooseParts([partition[2], partition[0]]);
      const merged = parts.getLooseParts(source).map(part => part.userData.loosePartTriangles);
      parts.clearLooseParts(source);
      parts.clearLooseParts(sibling);
      return {initial, shared, crossSource, merged, clean: parts.getLooseParts(source).length === 0,
        restored: source.geometry.index === index && source.geometry.drawRange.count === originalRange.count};
    }""")
    assert result['initial'] == [[0], [1], [2]]
    assert result['shared'] and result['restored'] and result['clean']
    assert result['crossSource'] is False
    assert sorted(result['merged']) == [[0, 2], [1]]


def test_weight_selection_keeps_equal_bone_ids_scoped_to_exact_sources(module_page):
    result = module_page.evaluate("""async () => {
      const weights = await import('./js/weight-rig/weight-selection.js');
      const selection = weights.normalizeBoneSelection([
        {source: 'folder-01/stream.buf', bone_id_offset: 0, bone_ids: [2, 2, 4]},
        {source: 'folder-02/stream.buf', bone_id_offset: 0, bone_ids: [2]},
        {source: 'folder-01/stream.buf', bone_id_offset: 0, bone_ids: [6]},
      ]);
      const first = [...weights.selectionForSource(selection, 'folder-01/stream.buf|offset=0')];
      const serialized = weights.serializeBoneSelection(selection);
      return {first, serialized, count: weights.selectedBoneCount(selection)};
    }""")
    assert result['first'] == [2, 4, 6]
    assert result['count'] == 4
    assert [entry['source'] for entry in result['serialized']] == ['folder-01/stream.buf', 'folder-02/stream.buf']
    assert [entry['bone_ids'] for entry in result['serialized']] == [[2, 4, 6], [2]]


def test_saved_humanoid_arm_rebinds_after_builder_change_and_deforms_vertices(module_page):
    result = module_page.evaluate("""async () => {
      const humanoid = await import('./js/weight-rig/humanoid-control-rig.js');
      const bindingApi = await import('./js/weight-rig/humanoid-rig-binding.js');
      const ik = await import('./js/weight-rig/humanoid-rig-ik.js');
      const deformation = await import('./js/weight-rig/weight-deformation.js');
      const automatic = humanoid.buildHumanoidControlRig({meshes: [{userData: {
        humanoidRestPositions: new Float32Array([-1,0,0, 1,0,0, 0,2,0, -1,1.5,0, 1,1.5,0]),
      }}]});
      const keys = ['leftShoulder', 'leftElbow', 'leftHand'];
      const positions = ['rightHand', ...keys].map(key => automatic.controls[key].position);
      const model = {joints: positions.map((restPivot, jointId) => ({jointId, restPivot})),
        components: [{rootId: 0, nodeIds: [0], parentById: {0: null}, childrenById: {0: []}},
          {rootId: 1, nodeIds: [1,2,3], parentById: {1: null, 2: 1, 3: 2},
            childrenById: {1: [2], 2: [3], 3: []}}]};
      const baseline = new Float32Array(positions.flat());
      const indices = new Uint32Array([10,11,12,13]);
      const weights = new Float32Array([1,1,1,1]);
      const authored = {indices: [...indices], weights: [...weights]};
      const target = [...positions[3]]; target[1] += 0.1; target[2] += 0.05;
      const rows = [];
      for (const version of [1, 2, humanoid.MODEL_RIG_BUILDER_VERSION - 1, humanoid.MODEL_RIG_BUILDER_VERSION]) {
        const current = version === humanoid.MODEL_RIG_BUILDER_VERSION;
        const saved = {version: humanoid.HUMANOID_CONTROL_RIG_VERSION,
          model_rig_builder_version: version, controls: Object.fromEntries(keys.map((key, index) =>
            [key, {semantic: automatic.controls[key].semantic, joint_id: index + (current ? 1 : 0)}]))};
        const originalSaved = JSON.stringify(saved);
        const mappings = humanoid.resolveHumanoidControlMappings({savedOverrides: saved, modelRig: model});
        const controlRig = humanoid.applyHumanoidControlRigOverrides({automaticRig: automatic,
          savedOverrides: saved, modelRig: model, resolvedMappings: mappings});
        const binding = bindingApi.buildHumanoidRigBinding({controlRig, modelRig: model, controlMappings: mappings});
        const solved = ik.solveHumanoidControlIk({controlRig, role: 'left_arm', target});
        const posedControls = ik.mergeHumanoidLimbPose({}, solved.positions, solved.keys);
        const deform = pose => {
          const driver = bindingApi.buildHumanoidDriverBaseTransforms({binding, controlRig,
            modelRig: model, posedControls: pose});
          const aliases = new Map([...driver.result].map(([id, matrix]) => [id + 10, matrix]));
          return deformation.applyWeightedTransformDeformation(baseline, indices, weights, 1, aliases);
        };
        const posed = deform(posedControls), restored = deform(null);
        const close = (actual, expected) => actual.every((value, i) => Math.abs(value - expected[i]) < 1e-5);
        rows.push({current, mappedIds: [...mappings.values()].map(value => value.jointId),
          rejected: [...mappings.rejectedControlKeys],
          anchors: keys.map(key => binding.diagnostics.bindingsByControl[key].anchorJointId),
          reached: solved.reached, handFollows: close([...posed.slice(9,12)], solved.end),
          handMoved: !close([...posed.slice(9,12)], positions[3]),
          otherHandUnchanged: close([...posed.slice(0,3)], positions[0]),
          restored: close([...restored], [...baseline]), savedUnchanged: JSON.stringify(saved) === originalSaved});
      }
      return {rows, authoredUnchanged: JSON.stringify(authored) ===
        JSON.stringify({indices: [...indices], weights: [...weights]})};
    }""")
    assert result['authoredUnchanged']
    for row in result['rows']:
        assert row['mappedIds'] == ([1, 2, 3] if row['current'] else [])
        assert row['rejected'] == []
        assert row['anchors'] == [1, 2, 3]
        assert all(row[key] for key in ('reached', 'handFollows', 'handMoved',
                                       'otherHandUnchanged', 'restored', 'savedUnchanged'))


@pytest.mark.parametrize(('builder', 'joint_id', 'valid_semantic'), [
    (None, 0, True), (0, 0, True), ('future', 0, True), (True, 0, True),
    (1, -1, True), (1, None, True), (1, '0', True), (1, 0, False),
    ('current', -1, True), ('current', 99, True),
])
def test_invalid_humanoid_mapping_provenance_stays_unbound(module_page, builder, joint_id, valid_semantic):
    result = module_page.evaluate("""async ({builder, jointId, validSemantic}) => {
      const humanoid = await import('./js/weight-rig/humanoid-control-rig.js');
      if (builder === 'current') builder = humanoid.MODEL_RIG_BUILDER_VERSION;
      if (builder === 'future') builder = humanoid.MODEL_RIG_BUILDER_VERSION + 1;
      const semantic = validSemantic ? {sideN: -0.2, height01: 0.7, depthN: 0} : {height01: 0.7};
      const mappings = humanoid.resolveHumanoidControlMappings({
        savedOverrides: {model_rig_builder_version: builder,
          controls: {leftHand: {joint_id: jointId, semantic}}},
        modelRig: {joints: [{jointId: 0}]},
      });
      return {mapped: [...mappings], rejected: [...mappings.rejectedControlKeys]};
    }""", {'builder': builder, 'jointId': joint_id, 'validSemantic': valid_semantic})
    assert result == {'mapped': [], 'rejected': ['leftHand']}


@pytest.fixture
def reconciliation_page(module_page):
    module_page.evaluate("""async () => {
      window.__reconciliation = await import('./js/weight-rig/weight-rig-reconcile.js');
      window.__sourceRig = (sourceKey, rows, modelWide = false, direction = [1, 0, 0]) => {
        const nodes = rows.map(([boneId, center, parent, weight = 1]) => ({
          boneId, weightedCenter: center, weightedRadius: 1, totalWeight: weight, affectedVertexCount: 32,
        }));
        const parentById = Object.fromEntries(rows.map(([id, , parent]) => [id, parent]));
        const childrenById = Object.fromEntries(rows.map(([id]) => [id,
          rows.filter(([, , parent]) => parent === id).map(([child]) => child)]));
        const components = rows.filter(([, , parent]) => parent === null).map(([rootId], componentId) => {
          const nodeIds = [], depthById = {[rootId]: 0}, pending = [rootId];
          while (pending.length) {
            const id = pending.shift(); nodeIds.push(id);
            childrenById[id].forEach(child => {depthById[child] = depthById[id] + 1; pending.push(child);});
          }
          return {componentId, rootId, nodeIds, depthById,
            parentById: Object.fromEntries(nodeIds.map(id => [id, parentById[id]])),
            childrenById: Object.fromEntries(nodeIds.map(id => [id, childrenById[id]])),
            edges: nodeIds.filter(id => parentById[id] !== null).map(id =>
              ({boneA: parentById[id], boneB: id, treeEdgeScore: 1})),
          };
        });
        return {sourceKey, boneIdsModelWide: modelWide, boneIds: nodes.map(node => node.boneId),
          influenceGraph: {nodes, relationships: components.flatMap(component => component.edges)},
          inferredForest: {components, componentByBoneId: new Map(components.flatMap(component =>
            component.nodeIds.map(id => [id, component.componentId])))},
          centerByBoneId: new Map(nodes.map(node => [node.boneId, node.weightedCenter])),
          restDirectionByBoneId: new Map(nodes.map(node => [node.boneId, direction])),
          restFrameEvidenceByBoneId: new Map(nodes.map(node => [node.boneId, {directionSource: 'geometry'}])),
          restFrameByBoneId: new Map(), jointPivotByBoneId: new Map(), vertexEvidence: [],
        };
      };
      window.__buildRig = (rigs, options = {}, work = {}) =>
        window.__reconciliation.buildModelRigReconciliationCooperative(rigs, options,
          {budget: {checkpoint: async () => {}}, ...work});
    }""")
    return module_page


@pytest.mark.parametrize(('marked', 'model_wide'), [(False, False), (True, False), (True, True)])
def test_reconciliation_preserves_exact_sources_and_stable_rebuild(reconciliation_page, marked, model_wide):
    result = reconciliation_page.evaluate("""async ({marked, modelWide}) => {
      const {sourceBoneKey} = window.__reconciliation;
      const keys = [1, 2].map(id => marked ? `stream#bone=part-0${id}.buf|offset=0`
        : `folder-0${id}/stream.buf|offset=0`);
      const rigs = keys.map(key => window.__sourceRig(key, [[1, [0, 0, 0], null]], modelWide));
      const first = await window.__buildRig(rigs);
      const permuted = await window.__buildRig([...rigs].reverse());
      const rebuilt = await window.__buildRig(rigs);
      const signature = rig => rig.joints.map(joint => [joint.jointId, joint.signature]);
      const separate = await window.__buildRig([
        window.__sourceRig(keys[0], [[1, [0, 0, 0], null]], true),
        window.__sourceRig(keys[1], [[1, [10, 0, 0], null]], false),
      ], {modelReferenceRadius: 1});
      return {jointCount: first.joints.length, members: first.joints[0].members.map(member => member.sourceKey),
        mapped: keys.map(key => first.sourceBoneToModelJointMap.get(sourceBoneKey(key, 1))),
        stable: JSON.stringify(signature(first)) === JSON.stringify(signature(permuted)),
        rebuilt: JSON.stringify(signature(first)) === JSON.stringify(signature(rebuilt)),
        separateCount: separate.joints.length};
    }""", {'marked': marked, 'modelWide': model_wide})
    assert result['jointCount'] == 1
    assert len(set(result['members'])) == 2
    assert result['mapped'] == [0, 0]
    assert result['stable'] and result['rebuilt']
    assert result['separateCount'] == 2


def test_reconciliation_skips_unneeded_vertex_work_and_completes_strict_chains(reconciliation_page):
    result = reconciliation_page.evaluate("""async () => {
      const source = window.__sourceRig('source-01', [[1, [0, 0, 0], null]]);
      source.vertexEvidence = [{get positions() {throw new Error('unused vertex evidence was read');}}];
      const single = await window.__buildRig([source]);
      const empty = window.__sourceRig('source-02', []);
      empty.vertexEvidence = source.vertexEvidence;
      const withEmpty = await window.__buildRig([source, empty]);
      const chain = (key, first) => window.__sourceRig(key, Array.from({length: 100}, (_,i) =>
        [first+i, [i, 0, 0], i ? first+i-1 : null]));
      let completeCheckpoints = 0;
      const complete = await window.__buildRig([chain('source-01', 1), chain('source-02', 201)], {},
        {budget: {checkpoint: async () => {completeCheckpoints += 1;}}});
      return {singleCount: single.joints.length, withEmptyCount: withEmpty.joints.length,
        candidateCount: single.reconciliation.candidateCount,
        completeCount: complete.joints.length, edges: complete.edges.length,
        completedWithoutPathWork: completeCheckpoints < 100,
        strictCount: complete.reconciliation.acceptedEquivalences.filter(item => item.pass === 'strict').length,
        uniqueSources: complete.joints.every(joint => new Set(joint.members.map(item => item.sourceKey)).size
          === joint.members.length)};
    }""")
    assert result == {'singleCount': 1, 'withEmptyCount': 1, 'candidateCount': 0, 'completeCount': 100,
                      'edges': 99, 'completedWithoutPathWork': True, 'strictCount': 100, 'uniqueSources': True}


@pytest.mark.parametrize('model_wide', [False, True])
def test_reconciliation_cancels_at_collection_and_graph_checkpoints(reconciliation_page, model_wide):
    result = reconciliation_page.evaluate("""async modelWide => {
      const rigs = ['source-01', 'source-02'].map((key, source) => window.__sourceRig(key,
        Array.from({length: 12}, (_, i) => {
          const first = modelWide ? 1 : source * 100 + 1;
          return [first + i, [i, 0, 0], i ? first + i - 1 : null];
        }), modelWide));
      for (const rig of rigs) {
        rig.vertexEvidence = [{positions: new Float32Array(258 * 3),
          indices: new Uint32Array(258).fill(rig.boneIds[0]),
          weights: new Float32Array(258).fill(1), influenceCount: 1}];
      }
      const authored = rigs.map(rig => JSON.stringify(rig.vertexEvidence));
      let totalCheckpoints = 0;
      const expected = await window.__buildRig(rigs, {},
        {budget: {checkpoint: async () => {totalCheckpoints += 1;}}});
      const cancelled = [];
      for (const cancelAt of [1, 2, ...[0.25, 0.5, 0.75].map(part => Math.ceil(totalCheckpoints * part)), totalCheckpoints]) {
        let current = true, checkpoints = 0;
        const result = await window.__buildRig(rigs, {}, {isCurrent: () => current,
          budget: {checkpoint: async () => {
            await new Promise(resolve => setTimeout(resolve, 0));
            if (++checkpoints === cancelAt) current = false;
          }}});
        cancelled.push(result === null);
      }
      const recovered = await window.__buildRig(rigs);
      return {cancelled, graphCheckpoints: totalCheckpoints > 4,
        authoredUnchanged: rigs.every((rig, i) => JSON.stringify(rig.vertexEvidence) === authored[i]),
        recovered: JSON.stringify(recovered) === JSON.stringify(expected)};
    }""", model_wide)
    assert result == {'cancelled': [True] * 6, 'graphCheckpoints': True,
                      'authoredUnchanged': True, 'recovered': True}


def test_reconciliation_rejects_equal_endpoint_competition(reconciliation_page):
    result = reconciliation_page.evaluate("""async () => {
      const rigs = [window.__sourceRig('source-01', [[1, [0, 0, 0], null]]),
        window.__sourceRig('source-02', [[2, [0, 0, 0], null], [3, [0, 0, 0], null]])];
      const built = await window.__buildRig(rigs);
      const reversed = await window.__buildRig([...rigs].reverse());
      return {joints: built.joints.length, accepted: built.reconciliation.acceptedEquivalences.length,
        ambiguous: built.reconciliation.rejectedCandidates.some(item => item.rejectionReason === 'ambiguous'),
        stable: JSON.stringify(built) === JSON.stringify(reversed)};
    }""")
    assert result == {'joints': 3, 'accepted': 0, 'ambiguous': True, 'stable': True}


@pytest.mark.parametrize('parent', [3, 99])
def test_reconciliation_recovers_source_cycles_and_missing_parents(reconciliation_page, parent):
    result = reconciliation_page.evaluate("""async parent => {
      const rig = window.__sourceRig('source-01',
        [[1, [0, 0, 0], null], [2, [1, 0, 0], 1], [3, [2, 0, 0], 2]]);
      rig.inferredForest.components[0].parentById[1] = parent;
      const built = await window.__buildRig([rig]);
      const component = built.components[0];
      return {joints: built.joints.length, edges: built.edges.length, root: component.rootId,
        roots: Object.values(component.parentById).filter(value => value === null).length,
        reachable: component.nodeIds.every(id => Number.isFinite(component.depthById[id]))};
    }""", parent)
    assert result == {'joints': 3, 'edges': 2, 'root': 0, 'roots': 1, 'reachable': True}


def test_reconciliation_missing_neighbours_are_not_equivalence_evidence(reconciliation_page):
    result = reconciliation_page.evaluate("""async () => {
      const rigs = ['source-01', 'source-02'].map((key, index) => {
        const rig = window.__sourceRig(key, [[index + 1, [index * 0.05, 0, 0], null]]);
        rig.inferredForest.components[0].parentById[index + 1] = 999;
        return rig;
      });
      const built = await window.__buildRig(rigs, {modelReferenceRadius: 1});
      return {joints: built.joints.length, accepted: built.reconciliation.acceptedEquivalences.length,
        separateMembers: built.joints.every(joint => joint.members.length === 1)};
    }""")
    assert result == {'joints': 2, 'accepted': 0, 'separateMembers': True}


def test_reconciliation_preserves_first_source_edge_provenance_and_sync_rerooting(reconciliation_page):
    result = reconciliation_page.evaluate("""async () => {
      const rig = window.__sourceRig('source-01',
        [[1, [0,0,0], null], [2, [1,0,0], 1], [3, [10,0,0], null], [4, [11,0,0], 3]]);
      rig.inferredForest.components[0].edges = [
        {boneA: '2', boneB: '1', treeEdgeScore: 0.25},
        {boneA: 1, boneB: 2, treeEdgeScore: 0.75},
      ];
      rig.influenceGraph.relationships = [
        {boneA: '2', boneB: '1', jointCenter: [0.4,0,0], jointWeightTotal: 2},
        {boneA: 1, boneB: 2, jointCenter: [0.8,0,0], jointWeightTotal: 8},
        {boneA: 3, boneB: 4, jointCenter: [10.5,0,0], jointWeightTotal: 3},
      ];
      const before = JSON.stringify(rig.influenceGraph);
      const built = await window.__buildRig([rig]);
      const edges = [...built.edges].sort((a,b) => a.jointA - b.jointA);
      const roots = Object.fromEntries(built.components.map(component =>
        [component.componentId, component.nodeIds.at(-1)]));
      const oriented = window.__reconciliation.orientModelRigForest(built.joints, built.edges, roots);
      return {scores: edges.map(edge => edge.combinedTreeScore),
        provenance: edges.map(edge => edge.sourceEdges[0]),
        synchronous: !(oriented instanceof Promise),
        roots: oriented.components.map(component => component.rootId),
        parents: built.joints.map(joint => joint.parentId),
        authoredUnchanged: JSON.stringify(rig.influenceGraph) === before};
    }""")
    assert result == {'scores': [0.25, 1], 'provenance': [
        {'sourceKey': 'source-01', 'parentBoneId': 1, 'childBoneId': 2,
         'treeEdgeScore': 0.25, 'jointCenter': [0.4, 0, 0], 'jointWeightTotal': 2},
        {'sourceKey': 'source-01', 'parentBoneId': 3, 'childBoneId': 4,
         'treeEdgeScore': 1, 'jointCenter': [10.5, 0, 0], 'jointWeightTotal': 3}],
        'synchronous': True, 'roots': [1, 3], 'parents': [1, None, 3, None], 'authoredUnchanged': True}


@pytest.mark.parametrize('competing_host', [False, True])
@pytest.mark.parametrize('chained', [False, True])
def test_reconciliation_preserves_boundary_orientation_and_rejects_ambiguous_hosts(reconciliation_page, competing_host, chained):
    result = reconciliation_page.evaluate("""async ({competingHost, chained}) => {
      const {sourceBoneKey} = window.__reconciliation;
      const body = key => window.__sourceRig(key,
        [[10, [-2, 0, 0], null, 32], [11, [0, 0, 0], 10, 32], [12, [2, 0, 0], 11, 32]], false,
        key === 'source-02' ? [0, 1, 0] : [1, 0, 0]);
      const accessory = window.__sourceRig('source-03',
        [[20, [0, 0, 0.5], null, 4], [21, [0, 0, 0.08], 20, 4]], false, [0, 0, 1]);
      const rigs = [body('source-01'), accessory];
      if (competingHost) rigs.push(body('source-02'));
      if (chained) rigs.push(window.__sourceRig('source-04',
        [[30, [0, 0, 1], null], [31, [0, 0, 0.58], 30]]));
      const built = await window.__buildRig(rigs, {modelReferenceRadius: 2});
      const reversed = await window.__buildRig([...rigs].reverse(), {modelReferenceRadius: 2});
      const id = (key, bone) => built.sourceBoneToModelJointMap.get(sourceBoneKey(key, bone));
      const parent = (key, bone) => built.joints[id(key, bone)].parentId;
      return {attachments: built.reconciliation.attachmentCount,
        hostPreserved: parent('source-01', 11) === id('source-01', 10)
          && parent('source-01', 12) === id('source-01', 11),
        boundaryPreserved: competingHost ? parent('source-03', 21) === id('source-03', 20)
          : parent('source-03', 21) === id('source-01', 11) && parent('source-03', 20) === id('source-03', 21),
        chainPreserved: !chained || parent('source-04', 31) === id('source-03', 20)
          && parent('source-04', 30) === id('source-04', 31),
        ambiguity: built.reconciliation.attachmentDiagnostics.some(item => item.rejectionReason === 'attachment_ambiguous'),
        reachable: built.components.every(component => component.nodeIds.every(id => component.depthById[id] !== null)),
        stable: JSON.stringify(built.joints) === JSON.stringify(reversed.joints),
        edgeCount: built.edges.length};
    }""", {'competingHost': competing_host, 'chained': chained})
    assert result['attachments'] == (0 if competing_host else 1) + int(chained)
    assert result['hostPreserved'] and result['boundaryPreserved'] and result['chainPreserved']
    assert result['reachable'] and result['stable']
    assert result['ambiguity'] is competing_host
    assert result['edgeCount'] == (5 if competing_host else 4) + 2 * int(chained)


def test_weight_session_preserves_model_wide_descriptor_through_saved_selection_and_save(module_page):
    result = module_page.evaluate("""async () => {
      const selection = await import('./js/weight-rig/weight-selection.js');
      const {createWeightRuntimeState} = await import('./js/weight-rig/weight-runtime.js');
      const {createWeightModelSession} = await import('./js/weight-rig/weight-model-session.js');
      const runtime = createWeightRuntimeState();
      const mesh = {userData: {modPath: 'fixture-root', semanticKey: 'mesh-a'}, geometry: {index: null}};
      runtime.knownMeshes.add(mesh);
      const sourceKey = 'fixture/stream.buf|offset=7';
      const descriptor = {sourceKey, sourceFile: 'fixture/stream.buf', boneIdOffset: 7, boneIdsModelWide: true};
      let saved = null;
      const refreshedSelections = [];
      const snapshot = () => ({
        loaded: runtime.modelWeightState.loaded,
        selectedBones: selection.selectionRecordsFromMap(
          runtime.modelWeightState.selectedBonesBySource, runtime.modelWeightState.sourceDescriptors),
      });
      const session = createWeightModelSession({
        modelWeightState: runtime.modelWeightState,
        states: runtime.states,
        stateFor: runtime.stateFor,
        knownMeshes: runtime.knownMeshes,
        modelWeightSnapshot: snapshot,
        selectionMapFromEntries: selection.selectionMapFromEntries,
        sourceSelectionEntries: map => selection.sourceSelectionEntries(map, runtime.modelWeightState.sourceDescriptors),
        refreshSelectedWeightMask: (_mesh, _state, boneIds) => refreshedSelections.push([...(boneIds || [])]),
        updateModelWeightHeatmap: () => {},
        installSkinningEntry: () => {
          const state = runtime.stateFor(mesh);
          Object.assign(state, {loaded: true, skinningSourceKey: sourceKey, boneIds: [3, 5], influenceCount: 1,
            indices: new Uint32Array([3, 5]), weights: new Float32Array([1, 0]), baselinePositions: new Float32Array([0, 0, 0])});
          return {source: descriptor};
        },
        syncPhysicsToSelection: () => true,
        serializeBoneSelection: selection.serializeBoneSelection,
        eligibleSkinningMesh: () => true,
        notifyChanged: () => {}, requestRender: () => {}, getGeneration: () => 1,
      });
      const previousApi = window.pywebview?.api;
      const loading = session.ensureLoaded();
      const waitedForBridge = runtime.modelWeightState.loading && !runtime.modelWeightState.loaded;
      window.pywebview = {api: {
        get_model_skinning_preview: async () => ({
          saved_bones: [{source: descriptor.sourceFile, source_key: sourceKey, bone_id_offset: 7, bone_ids: [3]}],
          meshes: {'mesh-a': {status: 'ok'}}, data: null,
        }),
        save_weight_selection: async (path, entries) => {
          saved = {path, entries};
          return {saved: true, selected_bones: entries};
        },
      }};
      try {
        window.dispatchEvent(new Event('pywebviewready'));
        const ready = await loading;
        const beforeMasks = {
          selected: ready.selectedBones[0]?.boneIds,
          descriptor: runtime.modelWeightState.sourceDescriptors.get(sourceKey)?.boneIdsModelWide,
          masksRestored: runtime.modelWeightState.savedSelectionMasksRestored,
        };
        session.setBoneSelected(sourceKey, 5, true);
        await session.restoreSavedSelection();
        await session.saveSelection();
        const saver = window.pywebview.api.save_weight_selection;
        delete window.pywebview.api.save_weight_selection;
        await session.saveSelection();
        const saveFailureReported = !!runtime.modelWeightState.selectionSaveError;
        window.pywebview.api.save_weight_selection = saver;
        await session.saveSelection();
        return {waitedForBridge,saveFailureReported,
          beforeMasks,
          refreshedSelections,
          afterSave: selection.selectionRecordsFromMap(
            runtime.modelWeightState.selectedBonesBySource, runtime.modelWeightState.sourceDescriptors),
          descriptorAfterSave: runtime.modelWeightState.sourceDescriptors.get(sourceKey)?.boneIdsModelWide,
          saved: saved && {path: saved.path, entries: saved.entries},
        };
      } finally {
        window.pywebview = {api: previousApi};
      }
    }""")
    assert result['waitedForBridge'] and result['saveFailureReported']
    assert result['beforeMasks'] == {'selected': [3], 'descriptor': True, 'masksRestored': False}
    assert result['refreshedSelections'] == [[3, 5], [3, 5]]
    assert result['afterSave'][0]['boneIds'] == [3, 5]
    assert result['afterSave'][0]['boneIdsModelWide'] is True
    assert result['descriptorAfterSave'] is True
    assert result['saved']['path'] == 'fixture-root'
    assert result['saved']['entries'][0]['bone_ids'] == [3, 5]


def test_weight_rig_activation_sequences_ready_paint_and_retries_independent_stages(module_page):
    result = module_page.evaluate("""async () => {
      const {createWeightRigActivationSession} = await import('./js/weight-rig/weight-rig-activation-session.js');
      let generation = 4, releasePaint;
      const paintGate = new Promise(resolve => { releasePaint = resolve; });
      const calls = [];
      let rigAttempt = 0;
      const session = createWeightRigActivationSession({
        getGeneration: () => generation,
        ensureWeightsLoaded: async () => { calls.push('weights'); return {loaded: true}; },
        waitForWeightReadyPaint: async () => { calls.push('paint-wait'); await paintGate; calls.push('painted'); },
        restoreSavedSelection: async () => { calls.push('selection'); return {savedSelectionMasksRestored: true}; },
        syncPhysicsToSelection: async () => { calls.push('physics'); return true; },
        ensureRigLoaded: async () => {
          calls.push('rig'); rigAttempt += 1;
          return rigAttempt === 1 ? {loaded: false, error: 'fixture rig failure'} : {loaded: true};
        },
      });
      const first = session.activate();
      const duplicate = session.activate();
      await Promise.resolve();
      const beforePaint = [...calls];
      releasePaint();
      const firstResult = await first;
      const retryResult = await session.activate();
      const afterRetry = [...calls];
      const completedResult = await session.activate();
      return {samePromise: first === duplicate, beforePaint, firstRig: firstResult.rig,
        retryRig: retryResult.rig, afterRetry, afterComplete: calls, completedRig: completedResult.rig};
    }""")
    assert result['samePromise'] is True
    assert result['beforePaint'] == ['weights', 'paint-wait']
    assert result['firstRig']['error'] == 'fixture rig failure'
    assert result['retryRig']['loaded'] is True
    assert result['afterRetry'].count('physics') == 1
    assert result['afterRetry'].count('rig') == 2
    assert result['afterComplete'] == result['afterRetry']


def test_weight_rig_activation_cancels_after_model_rebaseline(module_page):
    result = module_page.evaluate("""async () => {
      const {createWeightRigActivationSession} = await import('./js/weight-rig/weight-rig-activation-session.js');
      let generation = 2, releasePaint;
      const paintGate = new Promise(resolve => { releasePaint = resolve; });
      const calls = [];
      const session = createWeightRigActivationSession({
        getGeneration: () => generation,
        ensureWeightsLoaded: async () => ({loaded: true}),
        waitForWeightReadyPaint: async () => paintGate,
        restoreSavedSelection: async () => { calls.push('selection'); },
        syncPhysicsToSelection: async () => { calls.push('physics'); },
        ensureRigLoaded: async () => { calls.push('rig'); return {loaded: true}; },
      });
      const pending = session.activate();
      await Promise.resolve();
      session.invalidate();
      releasePaint();
      const result = await pending;
      return {result, calls};
    }""")
    assert result['result']['stale'] is True
    assert result['calls'] == []


def test_source_rig_surface_failure_recovery_and_cache_lifecycle(module_page):
    result = module_page.evaluate("""async () => {
      const THREE = await import('three');
      const {createRigSourceSession} = await import('./js/weight-rig/rig-model-session.js');
      const {createSkinningRuntime} = await import('./js/weight-rig/skinning-runtime.js');
      const {createWeightRuntimeState} = await import('./js/weight-rig/weight-runtime.js');
      const runtime = createWeightRuntimeState();
      const skin = createSkinningRuntime({...runtime, requestRender: () => {}});
      const sourceKey = 'fixture/stream.buf|offset=0';
      const otherKey = 'fixture/stream-02.buf|offset=0';
      const makeMesh = (name, boneId, source, triangles) => {
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute('position', new THREE.Float32BufferAttribute([0,0,0, 1,0,0, 0,1,0], 3));
        geometry.setIndex(triangles);
        const mesh = new THREE.Mesh(geometry);
        mesh.userData.semanticKey = name;
        const state = runtime.stateFor(mesh);
        Object.assign(state, {loaded: true, skinningSourceKey: source, influenceCount: 1,
          boneIds: [boneId], indices: new Uint32Array(3).fill(boneId), weights: new Float32Array([1,1,1])});
        runtime.knownMeshes.add(mesh);
        runtime.modelWeightState.sourceDescriptors.set(source, {sourceKey: source, sourceFile: 'fixture/stream.buf', boneIdOffset: 0});
        return mesh;
      };
      const first = makeMesh('mesh-01', 1, sourceKey, [0,1,2]);
      const duplicate = makeMesh('mesh-02', 1, sourceKey, [0,1,2]);
      const invalid = makeMesh('mesh-03', 2, sourceKey, [0,0,0]);
      const sourceSkinningRigs = new Map();
      let preparationCount = 0;
      const session = createRigSourceSession({...runtime, sourceSkinningRigs,
        ensureRigMeshPrepared: (...args) => { preparationCount++; return skin.ensureRigMeshPrepared(...args); },
        ensureInfluenceGraphCooperative: (...args) => skin.ensureInfluenceGraphCooperative(...args),
        rebuildRestFrames: () => {}, cloneForest: structuredClone});
      let rejected = false;
      try { await session.buildAllCooperative(); } catch (error) { rejected = error.message.includes('triangle geometry'); }
      const missing = {rejected, committed: sourceSkinningRigs.size, errors: Object.keys(session.getErrors())};
      const other = makeMesh('mesh-04', 3, otherKey, [0,1,2]);
      const partial = await session.buildAllCooperative();
      invalid.geometry.setIndex([0,1,2]);
      skin.rebaseAfterShapeChange(invalid);
      const members = [first, duplicate, invalid];
      const repaired = await session.ensureCooperative(sourceKey, members);
      const callsAfterBuild = preparationCount;
      const cached = await session.ensureCooperative(sourceKey, members);
      return {missing, partial: partial.map(rig => rig.sourceKey), sameObject: repaired === cached,
        callsAfterBuild, callsAfterCacheHit: preparationCount, errors: session.getErrors(),
        memberCount: repaired.influenceGraph.memberCount, uniqueMemberCount: repaired.influenceGraph.uniqueMemberCount,
        meshCount: repaired.meshes.size, vertexEvidenceCount: repaired.vertexEvidence.length,
        mode: repaired.influenceGraph.evidenceMode,
        weightsUsable: [...runtime.knownMeshes].every(mesh => runtime.stateFor(mesh).loaded),
        noVertexPreparation: [...runtime.knownMeshes].every(mesh => !('influenceNodes' in runtime.stateFor(mesh))),
        authoredWeights: [...runtime.stateFor(first).weights]};
    }""")
    assert result['missing'] == {'rejected': True, 'committed': 0, 'errors': ['fixture/stream.buf|offset=0']}
    assert result['partial'] == ['fixture/stream-02.buf|offset=0']
    assert result['sameObject'] and result['weightsUsable'] and result['noVertexPreparation']
    assert result['callsAfterCacheHit'] == result['callsAfterBuild']
    assert result['errors'] == {}
    assert result['memberCount'] == result['meshCount'] == result['vertexEvidenceCount'] == 3
    assert result['uniqueMemberCount'] == 2
    assert result['mode'] == 'surface'
    assert result['authoredWeights'] == [1, 1, 1]


def test_skinning_install_requires_canonical_wire_fields(module_page):
    result = module_page.evaluate("""async () => {
      const THREE = await import('three');
      const {createSkinningRuntime} = await import('./js/weight-rig/skinning-runtime.js');
      const {createWeightRuntimeState} = await import('./js/weight-rig/weight-runtime.js');
      const {aggregateModelWeightBoneStats} = await import('./js/weight-rig/weight-runtime.js');
      const runtime = createWeightRuntimeState();
      const skin = createSkinningRuntime({...runtime, requestRender: () => {}});
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.Float32BufferAttribute([0,0,0, 1,0,0, 0,1,0], 3));
      const mesh = new THREE.Mesh(geometry);
      const buffer = new ArrayBuffer(24);
      new Uint32Array(buffer, 0, 3).set([1,2,1]);
      new Float32Array(buffer, 12, 3).set([1,0.5,1]);
      const entry = {vertex_count: 3, influence_count: 1, bone_ids: [1,2],
        source: {key: 'fixture/stream.buf|offset=0', file: 'fixture/stream.buf', bone_id_offset: 0},
        data: {indices: {offset: 0, length: 12, type: 'u32'}, weights: {offset: 12, length: 12, type: 'f32'}},
        weight_stats: {'1': {affected_vertex_count: 2, total_weight: 2}, '2': {affected_vertex_count: 1, total_weight: 0.5}}};
      const variants = [{bone_ids: undefined}, {bone_ids: null}, {bone_ids: [true]}, {bone_ids: [-1]},
        {bone_ids: [1.5]}, {weight_stats: {'1': {affectedVertexCount: 2, totalWeight: 2}}}];
      const rejected = variants.map(overrides => {
        try { skin.installSkinningEntry(mesh, {...entry, ...overrides}, buffer); return false; }
        catch { return !runtime.stateFor(mesh).loaded; }
      });
      skin.installSkinningEntry(mesh, entry, buffer);
      return {rejected, bones: runtime.stateFor(mesh).boneIds,
        stats: aggregateModelWeightBoneStats([runtime.stateFor(mesh).weightBoneStats])};
    }""")
    assert result['rejected'] == [True] * 6
    assert result['bones'] == [1, 2]
    assert result['stats'] == {'1': {'affectedVertexCount': 2, 'averageInfluence': 1},
                               '2': {'affectedVertexCount': 1, 'averageInfluence': 0.5}}


def test_surface_graph_rebaseline_discards_inflight_evidence(module_page):
    result = module_page.evaluate("""async () => {
      const THREE = await import('three');
      const {createSkinningRuntime} = await import('./js/weight-rig/skinning-runtime.js');
      const {createWeightRuntimeState} = await import('./js/weight-rig/weight-runtime.js');
      const runtime = createWeightRuntimeState();
      const skin = createSkinningRuntime({...runtime, requestRender: () => {}});
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.Float32BufferAttribute([0,0,0, 1,0,0, 0,1,0], 3));
      geometry.setIndex([0,1,2]);
      const mesh = new THREE.Mesh(geometry);
      const state = runtime.stateFor(mesh);
      Object.assign(state, {loaded: true, indices: new Uint32Array([1,1,1]), weights: new Float32Array([1,1,1]),
        boneIds: [1], influenceCount: 1});
      let release, started;
      const ready = new Promise(resolve => started = resolve);
      const paused = new Promise(resolve => release = resolve);
      const old = skin.ensureInfluenceGraphCooperative(mesh, state, {budget: {checkpoint: async () => {started(); await paused;}}});
      await ready;
      skin.rebaseAfterShapeChange(mesh, {positions: new Float32Array([10,0,0, 11,0,0, 10,1,0])});
      const current = await skin.ensureInfluenceGraphCooperative(mesh, state);
      release();
      const obsolete = await old;
      const cached = await skin.ensureInfluenceGraphCooperative(mesh, state);
      return {obsolete: obsolete === null, cachePreserved: current === cached,
        center: current.nodes[0].weightedCenter, weights: [...state.weights]};
    }""")
    assert result['obsolete'] and result['cachePreserved']
    assert result['center'] == pytest.approx([10 + 1 / 3, 1 / 3, 0])
    assert result['weights'] == [1, 1, 1]


def test_control_conditions_preserve_or_groups_negation_and_contradictions(module_page):
    result = module_page.evaluate("""async () => {
      const state = await import('./js/editing/control-state.js');
      state.resetControlState(); state.setControlValue('input01', '1');
      const is = value => ({var: 'input01', value, negate: false});
      const not = value => ({var: 'input01', value, negate: true});
      return [state.dnfSatisfied([]), state.dnfSatisfied([[is('0'), is('1')]]),
        state.dnfSatisfied([[is('0')], [is('1')]]), state.dnfSatisfied([[not('1')]]),
        state.dnfSatisfied([[not('0')]])];
    }""")
    assert result == [True, False, True, False, True]


@pytest.mark.parametrize("pose_only", [False, True], ids=["shape-and-pose", "pose-only"])
def test_shared_compute_program_plays_pauses_resets_and_releases(module_page, tmp_path, pose_only):
    from tests.support.animations import POSE_SHADER, compute_mod, load_mod, read_sections, save_sections

    ini = compute_mod(tmp_path / "mod", shared_outputs=True, rotating_pose=True)
    sections = read_sections(ini)
    sections["CustomShaderPose"] = [
        line.replace("30 * $dt", "10 * $dt") for line in sections["CustomShaderPose"]]
    if pose_only:
        del sections["CustomShaderShape"]
        (ini.parent / "pose.hlsl").write_text(
            POSE_SHADER.replace("numthreads(64, 1, 1)", "numthreads(1, 1, 1)"), encoding="utf-8")
        for section in ("CustomShaderPose", "CustomShaderPose02"):
            sections[section] = [
                line.replace("ResourcePosition.1", "ResourcePosition.2").replace(
                    "Dispatch = 1, 1, 1", "Dispatch = 3, 1, 1") for line in sections[section]]
    save_sections(ini, sections)
    _, built = load_mod(ini, ini.parent)
    entries = list(built.meshes.values())
    assert len(entries) == 2
    _prepare_compute_clock(module_page)
    _set_geometry_blob(module_page, built.geometry)
    result = module_page.evaluate("""async entries => {
      const THREE = await import('three/webgpu');
      const {decodeF32} = await import('./js/textures/decode.js');
      const runtime = window.__animation, controls = window.__controls;
      const meshes = entries.map(entry => {
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute('position', new THREE.Float32BufferAttribute(decodeF32(entry.pos), 3));
        geometry.setAttribute('normal', new THREE.Float32BufferAttribute(
          decodeF32(entry.animation_geometry.base_normals), 3));
        const mesh = new THREE.Mesh(geometry);
        mesh.userData.basePositions = geometry.attributes.position.array.slice();
        return mesh;
      });
      const attributes = meshes.map(mesh => ({...mesh.geometry.attributes}));
      const registered = entries.map((entry, index) => runtime.registerAnimatedMesh(
        meshes[index], entry.animation_geometry.track_id, entry.animation_geometry));
      const positions = () => meshes.map(mesh => [...mesh.geometry.attributes.position.array]);
      window.__frame(0);
      const first = positions();
      window.__frame(10);
      const throttled = positions();
      window.__frame(40);
      const advanced = positions();
      controls.setControlValue('pause', '1'); window.__frame(80);
      const paused = positions(), sleeping = window.__pendingFrames() === 0;
      controls.setControlValue('pause', '0'); runtime.wakeAnimationRuntime(); window.__frame(5000);
      const resumed = positions();
      window.__frame(5040);
      const continued = positions();
      controls.setControlValue('anime_state', '1'); runtime.wakeAnimationRuntime(); window.__frame(5040);
      const reset = positions(), trigger = controls.getControlValue('anime_state');
      runtime.resetAnimationRuntime();
      return {registered, first, throttled, advanced, paused, resumed, continued, reset, trigger, sleeping,
        stable: meshes.every((mesh, index) =>
          mesh.geometry.attributes.position === attributes[index].position &&
          mesh.geometry.attributes.normal === attributes[index].normal),
        normals: [...meshes[0].geometry.attributes.normal.array],
        cleared: runtime.animationRuntimeSnapshot().clocks, pending: window.__pendingFrames()};
    }""", entries)
    assert result["registered"] == [True, True]
    assert result["stable"] and result["sleeping"]
    for state in ("first", "advanced", "paused", "resumed", "continued", "reset"):
        assert result[state][0] == pytest.approx(result[state][1])
    def positions(phase, translation, interpolation):
        weight = 0 if pose_only else (
            .5 * (math.sin(phase * 30) + 1) + math.sin((phase - .05236) * 30) + 1)
        qz = interpolation * 2 ** -.5
        qw = 1 - interpolation + interpolation * 2 ** -.5
        angle = 2 * math.atan2(qz, qw)
        cosine, sine = math.cos(angle), math.sin(angle)
        return [value for x, y in (
            (weight + translation, 0), (1 + 2 * weight + translation, 0),
            (3 * weight + translation, 1))
            for value in (cosine * x - sine * y, sine * x + cosine * y + .1, 0)]
    assert result["first"][0] == pytest.approx(positions(0, .25, 0), abs=1e-5)
    assert result["throttled"] == result["first"]
    assert result["advanced"][0] == pytest.approx(positions(.004, .45, .4))
    assert result["paused"] == result["resumed"] == result["advanced"]
    assert result["continued"][0] == pytest.approx(positions(.008, .65, .8))
    assert result["reset"][0] == pytest.approx(positions(.008, .25, 0))
    assert result["trigger"] == "0"
    assert result["normals"] == [0, 1, 0] * 3
    assert result["cleared"] == result["pending"] == 0


@pytest.mark.parametrize("target_size", [0, 80], ids=["empty-target", "partial-target"])
def test_branched_compute_children_play_separately_together_and_keep_missing_vertices(
        module_page, tmp_path, target_size):
    from tests.support.animations import branched_compute_mod, load_mod

    ini = branched_compute_mod(tmp_path / "mod")
    target = ini.parent / "key1.buf"
    target.write_bytes(target.read_bytes()[:target_size])
    _, built = load_mod(ini, ini.parent)
    entry = next(iter(built.meshes.values()))
    _prepare_compute_clock(module_page)
    _set_geometry_blob(module_page, built.geometry)
    result = module_page.evaluate("""async entry => {
      const {decodeF32} = await import('./js/textures/decode.js');
      const runtime = window.__animation, controls = window.__controls, mesh = window.__mesh;
      window.__position.array.set(decodeF32(entry.pos));
      mesh.userData.basePositions = window.__position.array.slice();
      const attributes = {...mesh.geometry.attributes};
      const registered = runtime.registerAnimatedMesh(mesh, entry.animation_geometry.track_id,
        entry.animation_geometry);
      const positions = () => [...window.__position.array];
      const select = (mode, now) => {
        controls.setControlValue('mode', mode); runtime.wakeAnimationRuntime(); window.__frame(now);
        return positions();
      };
      window.__frame(0); const first = positions();
      window.__frame(40); const advanced = positions();
      const second = select('2', 80);
      window.__frame(120); const secondAdvanced = positions();
      const both = select('3', 160);
      window.__frame(200); const bothAdvanced = positions();
      controls.setControlValue('hidden', '1'); runtime.wakeAnimationRuntime(); window.__frame(240);
      window.__frame(280);
      const blocked = positions(), sleeping = window.__pendingFrames() === 0;
      controls.setControlValue('hidden', '0');
      const restarted = select('1', 5000);
      // Mutating a guard inside its selected branch must not select another branch.
      runtime.resetAnimationRuntime(); controls.resetControlState();
      const program = entry.animation_geometry.program;
      const start = program.commands.findIndex(command => command.op === 'if') + 1;
      program.commands.splice(start, 0,
        {op: 'set', variable: 'mode', expression: {kind: 'literal', value: 2}},
        {op: 'set', variable: 'hidden', expression: {kind: 'literal', value: 1}});
      runtime.registerAnimatedMesh(mesh, entry.animation_geometry.track_id, entry.animation_geometry);
      window.__frame(0); const selectedBranch = positions();
      runtime.resetAnimationRuntime();
      return {registered, first, advanced, second, secondAdvanced, both, bothAdvanced,
        blocked, sleeping, restarted, selectedBranch,
        stable: mesh.geometry.attributes.position === attributes.position &&
          mesh.geometry.attributes.normal === attributes.normal};
    }""", entry)
    def positions(first, second):
        first = first if target_size else 0
        return [first + 2 * second, 0, 0, 1 + 2 * first + 4 * second, 0, 0, 6 * second, 1, 0]
    initial, advanced = .5, .5 * (math.sin(.004 * 30) + 1)
    later = .5 * (math.sin(.008 * 30) + 1)
    assert result["registered"] and result["stable"] and result["sleeping"]
    assert result["first"] == pytest.approx(positions(initial, 0))
    assert result["advanced"] == pytest.approx(positions(advanced, 0))
    assert result["second"] == pytest.approx(positions(0, initial))
    assert result["secondAdvanced"] == pytest.approx(positions(0, advanced))
    assert result["both"] == pytest.approx(positions(advanced, advanced))
    assert result["bothAdvanced"] == pytest.approx(positions(later, later))
    assert result["blocked"] == pytest.approx(positions(0, 0))
    assert result["restarted"] == result["selectedBranch"] == pytest.approx(positions(initial, 0))


@pytest.mark.parametrize("source, values, expected", [
    ("$a == 1 && ($b != 0 || !$c)", {"a": 1, "b": 0, "c": 0}, True),
    ("$a == 1 && ($b != 0 || !$c)", {"a": 0, "b": 1, "c": 0}, False),
    ("($a >= .5 && $a <= 1.) || $b < -1", {"a": .75, "b": 0}, True),
    ("$a > 1 || $a !== 1", {"a": 1}, False),
    ("!($a === 1) && $b", {"a": 0, "b": 2}, True),
    ("($a + .5) * 2 == 3", {"a": 1}, True),
])
def test_shared_condition_compiler_and_runtime_agree(module_page, source, values, expected):
    from core.ini.condition import compile_expression

    expression = compile_expression(source)
    result = module_page.evaluate("""async ({expression, values}) => {
      const {evaluateCondition, compareValues} = await import('./js/editing/conditions.js');
      const controls = await import('./js/editing/control-state.js');
      controls.resetControlState();
      for (const [name, value] of Object.entries(values)) controls.setControlValue(name, String(value));
      const shared = Object.fromEntries(Object.keys(values).map(name => [name, controls.getControlValue(name)]));
      return {condition: evaluateCondition(expression, {variables: shared, dt: 0}),
        controls: controls.dnfSatisfied([[{var: 'a', value: String(values.a), negate: false}]]),
        comparison: compareValues(shared.a, '==', String(values.a))};
    }""", {"expression": expression, "values": values})
    assert result == {"condition": expected, "controls": True, "comparison": True}


def test_sparse_compute_overlay_retains_rest_geometry_and_independent_pass_state(module_page):
    _prepare_compute_clock(module_page)
    result = module_page.evaluate("""() => {
      const runtime = window.__animation, controls = window.__controls, mesh = window.__mesh;
      const append = window.__appendGeometry, variable = window.__variable;
      mesh.userData.humanoidRestPositions = new Float32Array([10,0,0, 11,0,0, 10,1,0]);
      mesh.userData.humanoidRestNormals = new Float32Array([0,0,1, 0,0,1, 0,0,1]);
      window.__position.array.set(mesh.userData.humanoidRestPositions);
      window.__normal.array.set(mesh.userData.humanoidRestNormals);
      controls.setControlValue('input01', '1'); controls.setControlValue('input02', '0');
      const commands = ['input01','input02'].flatMap((input, pass) => [
        window.__advance('phase' + pass, pass + 1, window.__enabled(input)),
        {op: 'dispatch', track_id: 'track-01', kind: 'shape', pass,
          phase: variable('phase' + pass)},
      ]);
      const registered = runtime.registerAnimatedMesh(mesh, 'track-01', {
        kind: 'gimi_compute', program_id: 'program-01', track_id: 'track-01', vertex_count: 3,
        program: {external_variables: ['input01','input02'], initials: {phase0: 0.2, phase1: 0.3}, commands},
        overlay: true, position_only: true, pose: null,
        shape_passes: [[1,0,0, 1,0,0, 1,0,0], [0,2,0, 0,2,0, 0,2,0]].map(deltas => ({
          deltas: append(new Float32Array(deltas)), weight_operation: {kind: 'linear'}})),
      });
      window.__frame(0);
      const first = [...window.__position.array];
      controls.setControlValue('input02', '1'); runtime.wakeAnimationRuntime();
      window.__frame(100); window.__frame(200);
      const both = [...window.__position.array];
      controls.setControlValue('input01', '0'); runtime.wakeAnimationRuntime();
      window.__frame(300); window.__frame(400);
      const independent = [...window.__position.array];
      controls.setControlValue('input02', '0'); runtime.wakeAnimationRuntime(); window.__frame(500);
      const frozen = [...window.__position.array], sleeping = window.__pendingFrames() === 0;
      runtime.resetAnimationRuntime();
      return {registered, first, both, independent, frozen, sleeping,
        stable: window.__position === mesh.geometry.attributes.position,
        normals: [...window.__normal.array], baseline: [...mesh.userData.basePositions]};
    }""")
    assert result['registered'] and result['stable'] and result['sleeping']
    assert result['first'] == pytest.approx([10.2,0.6,0, 11.2,0.6,0, 10.2,1.6,0])
    assert result['both'][0] > result['first'][0]
    assert result['both'][1] > result['first'][1]
    assert result['independent'][0] == pytest.approx(result['both'][0])
    assert result['independent'][1] > result['both'][1]
    assert result['frozen'] == result['independent']
    assert result['normals'] == [0,0,1] * 3
    assert result['baseline'] == [0,0,0,1,0,0,0,1,0]

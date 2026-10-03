"""Selection reaches the Inspector without changing staged source state."""

from .payloads import append_stream, model_payload
from .support import bridge_calls, open_model, project_mesh_points, wait_loaded


def test_key_light_drag_restores_controls_after_capture_loss_and_release(viewer):
    page = viewer({'fixture-01': model_payload()})
    open_model(page, 'fixture-01')
    wait_loaded(page)
    marker = page.evaluate("""async () => {
      const {scene, camera, renderer, controls} = await import('./js/scene/scene.js');
      const light = scene.children.find(object => object.isDirectionalLight && object.castShadow);
      const point = light.position.clone().project(camera), rect = renderer.domElement.getBoundingClientRect();
      window.__lightDrag = {canvas: renderer.domElement, controls};
      window.addEventListener('pointerdown', event => window.__lightDrag.pointerId = event.pointerId,
        {capture: true});
      return [rect.left + (point.x + 1) * rect.width / 2, rect.top + (1 - point.y) * rect.height / 2];
    }""")
    for enabled, lose_capture in ((True, True), (False, False)):
        page.evaluate('enabled => window.__lightDrag.controls.enabled = enabled', enabled)
        page.mouse.move(*marker)
        page.mouse.down()
        page.mouse.move(marker[0] + 1, marker[1] + 1)
        assert not page.evaluate('window.__lightDrag.controls.enabled')
        if lose_capture:
            page.evaluate('window.__lightDrag.canvas.releasePointerCapture(window.__lightDrag.pointerId)')
            page.mouse.move(5, 5)
            page.wait_for_function('window.__lightDrag.controls.enabled')
        page.mouse.up()
        assert page.evaluate('window.__lightDrag.controls.enabled') == enabled


def test_selection_and_panel_navigation_preserve_loaded_geometry(viewer):
    page = viewer({'fixture-01': model_payload(2)})
    open_model(page, 'fixture-01')
    wait_loaded(page, 2)
    page.evaluate('window.__originalMeshes = [...window.modViewer.activeMeshes]')
    page.locator('#inspector-tab').click()
    rows = page.locator('.draw-item')
    rows.nth(0).click()
    assert page.locator('.draw-item.selected').count() == 1
    assert page.locator('#inspector-content').is_visible()
    rows.nth(1).click(modifiers=['Control'])
    assert page.locator('.draw-item.selected').count() == 2
    rows.nth(1).click()
    assert page.locator('.draw-item.selected').count() == 1
    assert 'selected' in rows.nth(1).get_attribute('class')
    page.locator('#controls-tab').click()
    page.locator('#inspector-tab').click()
    assert page.locator('.draw-item.selected').count() == 1
    assert page.evaluate('window.modViewer.activeMeshes.every((mesh, i) => mesh === window.__originalMeshes[i])')
    assert bridge_calls(page, 'export') == []
    assert bridge_calls(page, 'load') == [['fixture-01', False]]


def test_visibility_button_and_reset_synchronize_viewer_without_staging(viewer):
    page = viewer({'fixture-01': model_payload()})
    open_model(page, 'fixture-01')
    wait_loaded(page)
    button = page.locator('.draw-item .mesh-state-btn')
    button.click()
    page.wait_for_function('!window.modViewer.activeMeshes[0].visible')
    page.evaluate("""async () => {
      const {resetMeshState} = await import('./js/mesh/visibility.js');
      resetMeshState();
    }""")
    page.wait_for_function('window.modViewer.activeMeshes[0].visible')
    assert button.get_attribute('aria-pressed') == 'true'
    assert bridge_calls(page, 'export') == []
    assert page.evaluate('!window.__bridge.pending["fixture-01"]')


def test_opaque_display_labels_render_as_text_in_panel_and_inspector(viewer):
    payload = model_payload()
    label = '<b data-fixture="marker-01">fixture-01</b>'
    payload['metadata']['mesh_names'] = {'component-00::3,0,0': label}
    page = viewer({'fixture-01': payload})
    open_model(page, 'fixture-01')
    wait_loaded(page)
    assert label in page.locator('.draw-item').inner_text()
    page.locator('#inspector-tab').click()
    page.locator('.draw-item').click()
    assert label in page.locator('#inspector-content').inner_text()
    assert page.locator('[data-fixture="marker-01"]').count() == 0
    assert bridge_calls(page, 'names') == []


def test_panel_preference_survives_page_reload_without_loading_or_editing_source(viewer):
    page = viewer({'fixture-01': model_payload()})
    open_model(page, 'fixture-01')
    wait_loaded(page)
    page.locator('#inspector-tab').click()
    page.reload()
    page.wait_for_function('window.modViewer !== undefined')
    assert bridge_calls(page, 'load') == []
    assert bridge_calls(page, 'export') == []
    open_model(page, 'fixture-01')
    wait_loaded(page)
    assert page.locator('#inspector-panel').is_visible()
    assert page.locator('#controls-panel').is_hidden()


def test_library_folder_context_menus_open_native_folders_without_loading(viewer):
    page = viewer({}, native={
        'folders': {'folders': [{'name': 'Library', 'path': 'root-01', 'exists': True}]},
        'assets': {'folders': [{'type': 'GIMI', 'path': 'root-02', 'exists': True}]},
        'modChildren': {'folders': [{'name': 'Child', 'path': 'root-01/child'},
                                   {'name': 'Archive', 'path': 'root-01/archive.zip', 'kind': 'archive'}]},
        'assetChildren': {'folders': [{'name': 'Category', 'path': 'root-02/category'},
                                     {'name': 'Asset', 'path': 'root-02/asset', 'asset': True}]},
    })
    for tab, prefix, root, children, method in [
        ('mod-library', 'mod-folder', 'root-01', ['root-01/child'], 'modFolderOpen'),
        ('assets', 'asset-folder', 'root-02', ['root-02/category', 'root-02/asset'], 'assetFolderOpen'),
    ]:
        page.locator(f'[data-left-tab="{tab}"]').click()
        root_row = page.locator(f'.{prefix}-row[data-{prefix}-path="{root}"]')
        root_row.click(button='right')
        menu = page.locator(f'.{prefix}-action-menu:not([hidden])')
        assert menu.locator(f'.{prefix}-open').inner_text() == 'Open Folder'
        assert menu.locator(f'.{prefix}-edit').count() == 1
        assert menu.locator(f'.{prefix}-remove').count() == 1
        menu.locator(f'.{prefix}-open').click()
        root_row.locator(f'.{prefix}-expand').click()
        for child in children:
            row = page.locator(f'.{prefix}-row[data-{prefix}-path="{child}"]')
            row.click(button='right')
            menu = page.locator(f'.{prefix}-action-menu:not([hidden])')
            assert menu.locator('button').count() == 1
            menu.locator(f'.{prefix}-open').click()
        assert bridge_calls(page, method) == [[root], *[[child] for child in children]]
        root_row.click(button='right')
        page.keyboard.press('Escape')
        assert page.locator(f'.{prefix}-action-menu:not([hidden])').count() == 0
    archive_row = page.locator('.mod-folder-row[data-mod-folder-path="root-01/archive.zip"]')
    assert archive_row.locator('.mod-folder-open').count() == 0
    for name in ['load', 'asset', 'export', 'discard', 'names', 'textures']:
        assert bridge_calls(page, name) == []
    page.evaluate('window.__bridge.results.assetFolderOpen = {error:"fixture failure"}')
    page.locator('.asset-folder-row[data-asset-folder-path="root-02/category"]').click(button='right')
    page.locator('.asset-folder-action-menu:not([hidden]) .asset-folder-open').click()
    page.wait_for_function('document.getElementById("asset-folder-error").textContent === "fixture failure"')


def test_environment_and_tools_restore_and_save_without_orientation_or_source_edits(viewer):
    settings = {
        'environment': 'studio', 'wireframe': True, 'outlines': True,
        'ambientOcclusion': 0.4, 'bloom': True, 'glossy': True,
        'toonShading': True, 'grid': False, 'smoothShading': False,
        'textureMode': 'diffuse', 'keyLightIntensity': 0.75,
        'navigationGizmo': False,
    }
    page = viewer({'fixture-01': model_payload()}, preferences=settings)
    page.wait_for_function('window.modViewer.getEnvironmentPreset().id === "studio"')
    assert bridge_calls(page, 'preferencesSave') == []
    for control in ['wire', 'outline', 'glossy', 'toon']:
        assert page.locator(f'#{control}-btn').get_attribute('aria-pressed') == 'true'
    for control in ['grid', 'shading', 'trackball']:
        assert page.locator(f'#{control}-btn').get_attribute('aria-pressed') == 'false'
    assert page.locator('#ao-slider').input_value() == '40'
    assert page.locator('#light-slider').input_value() == '50'
    assert 'diffuse-only' in page.locator('#texture-btn').get_attribute('class')
    assert page.locator('#bloom-btn').is_disabled()
    assert page.evaluate('window.modViewer.getBloomEnabled()') is True
    page.locator('#environment-btn').click()
    page.locator('#environment-popover button').nth(1).click()
    assert bridge_calls(page, 'preferencesSave') == []

    open_model(page, 'fixture-01')
    wait_loaded(page)
    page.locator('#texture-btn').click()
    page.locator('#texture-popover button').nth(2).click()
    assert page.evaluate("""() => {
      const material = window.modViewer.activeMeshes[0].material;
      return material.wireframe && material.flatShading && material.roughness === 0.2;
    }""")
    assert bridge_calls(page, 'preferencesSave') == []
    # Make emission capability observable without requesting a source texture.
    page.evaluate("""() => {
      const mesh = window.modViewer.activeMeshes[0];
      mesh.material.userData.gameMaterial.profile = {
        ...mesh.material.userData.gameMaterial.profile, emission_source:'emission_map_rgb'};
      mesh.userData.emissionMapKey = 'emission_map::texture-01';
      window.dispatchEvent(new Event('mod-viewer-mesh-state-changed'));
    }""")
    assert page.locator('#bloom-btn').get_attribute('aria-pressed') == 'true'
    for control in ['wire', 'outline', 'bloom', 'glossy', 'toon', 'grid', 'shading', 'trackball']:
        page.locator(f'#{control}-btn').click()
    page.locator('#environment-btn').click()
    page.locator('#environment-popover button').first.click()
    page.locator('#texture-btn').click()
    page.locator('#texture-popover button').first.click()
    page.evaluate("""() => {
      for(const id of ['ao-slider','light-slider']) {
        const slider=document.getElementById(id); slider.value='0';
        slider.dispatchEvent(new Event('input',{bubbles:true}));
        slider.dispatchEvent(new Event('change',{bubbles:true}));
      }
    }""")
    page.wait_for_function('window.__bridge.calls.filter(call => call.name === "preferencesSave").length === 12')
    patches = bridge_calls(page, 'preferencesSave')
    saved = {name: value for args in patches for name, value in args[0].items()}
    expected = {
        'environment': 'default', 'wireframe': False, 'outlines': False,
        'ambientOcclusion': 0, 'bloom': False, 'glossy': False,
        'toonShading': False, 'grid': True, 'smoothShading': True,
        'textureMode': 'all', 'keyLightIntensity': 0, 'navigationGizmo': True,
    }
    assert saved == expected
    page.locator('#camera-flip-btn').click()
    page.locator('#camera-flip-horizontal-btn').click()
    page.locator('#camera-reset-view-btn').click()
    assert bridge_calls(page, 'preferencesSave') == patches
    assert bridge_calls(page, 'export') == []
    assert page.evaluate('!window.__bridge.pending["fixture-01"]')
    page.reload()
    page.wait_for_function('window.modViewer !== undefined && document.getElementById("light-slider").value === "0"')
    assert page.locator('#environment-btn').get_attribute('data-environment') == 'default'
    assert page.locator('#grid-btn').get_attribute('aria-pressed') == 'true'
    assert page.locator('#wire-btn').get_attribute('aria-pressed') == 'false'
    assert page.locator('#ao-slider').input_value() == '0'
    assert page.locator('#light-slider').input_value() == '0'
    assert bridge_calls(page, 'preferencesSave') == []
    assert bridge_calls(page, 'load') == []


def test_loose_part_apply_requires_confirmation_and_stages_complete_partition(viewer):
    payload = model_payload()
    mesh = payload['meshes']['mesh-00']
    mesh['pos'] = append_stream(payload, 'f', [0,0,0,1,0,0,0,1,0, 4,0,0,5,0,0,4,1,0])
    mesh['idx'] = append_stream(payload, 'I', [0,1,2,3,4,5])
    mesh['drawindexed'] = [6,0,0]
    mesh['identity'] = {'key': 'identity-01', 'component': 'component-00'}
    page = viewer({'fixture-01': payload})
    open_model(page, 'fixture-01')
    wait_loaded(page)
    page.locator('.draw-item').click(button='right')
    page.locator('.mesh-context-menu [data-i18n="mesh.separateLooseParts"]').click()
    page.locator('#dialog-backdrop.show').wait_for()
    page.locator('#dialog-ok').click()
    page.wait_for_function('document.querySelectorAll(".draw-item").length === 2')
    assert bridge_calls(page, 'meshApply') == []
    assert page.locator('#export-btn').is_disabled()
    page.locator('.group-name').first.click(button='right')
    page.locator('.mesh-context-menu [data-i18n="mesh.applyMeshChanges"]').click()
    page.locator('#dialog-backdrop.show').wait_for()
    page.locator('#dialog-cancel').click()
    assert bridge_calls(page, 'meshApply') == []
    page.locator('.group-name').first.click(button='right')
    page.locator('.mesh-context-menu [data-i18n="mesh.applyMeshChanges"]').click()
    page.locator('#dialog-backdrop.show').wait_for()
    page.locator('#dialog-ok').click()
    page.wait_for_function('window.__bridge.calls.some(call => call.name === "meshApply") && window.__bridge.calls.filter(call => call.name === "load").length === 2')
    wait_loaded(page)
    request = bridge_calls(page, 'meshApply')[0]
    assert request[0] == 'fixture-01'
    assert request[1]['component'] == 'component-00'
    assert request[1]['mesh']['key'] == 'identity-01'
    assert request[1]['mesh']['parts'] == [[0], [1]]
    assert request[1]['mesh']['sources'] == mesh['sources']
    assert bridge_calls(page, 'export') == []
    assert page.evaluate('window.__bridge.pending["fixture-01"]')


def test_present_cycles_aligned_multi_variable_state_and_blocks_unsynchronized_data(viewer):
    payload = model_payload()
    item = {'count': 2, 'synchronized': True, 'names': ['state-01', 'state-02'],
            'vars': [{'var': 'input01', 'values': ['0', '1']},
                     {'var': 'input02', 'values': ['1', '0']}],
            'capture_vars': [], 'missing_inis': []}
    payload['controls']['present'] = {'target_inis': ['source-01.ini', 'source-02.ini'], 'item': item}
    payload['state']['defaults'] = {'input01': '0', 'input02': '1'}
    page = viewer({'fixture-01': payload})
    open_model(page, 'fixture-01')
    wait_loaded(page)
    page.locator('#present-list .toggle-cycle-btn').click()
    result = page.evaluate("import('./js/editing/control-state.js').then(module => module.getControlState())")
    assert result == {'input01': '1', 'input02': '0'}
    assert page.locator('#present-list .toggle-value').inner_text() == 'state-02'
    page.evaluate("""async () => {
      const {buildPresentPanel} = await import('./js/panels/present-panel.js');
      const item = window.__bridge.controls['fixture-01'].present.item;
      item.synchronized = false; item.sync_error = 'fixture failure';
      buildPresentPanel({item, target_inis: ['source-01.ini', 'source-02.ini']}, {modPath: 'fixture-01'});
    }""")
    assert page.locator('#present-list .toggle-cycle-btn').is_disabled()
    assert page.locator('.present-sync-error').inner_text() == 'fixture failure'
    assert page.evaluate("import('./js/editing/control-state.js').then(module => module.getControlState())") == result
    assert bridge_calls(page, 'export') == []


def test_viewport_context_click_retains_selected_meshes_and_inspector_target(viewer):
    page = viewer({'fixture-01': model_payload(2)})
    open_model(page, 'fixture-01')
    wait_loaded(page, 2)
    page.locator('#inspector-tab').click()
    rows = page.locator('.draw-item')
    rows.nth(0).click()
    rows.nth(1).click(modifiers=['Control'])
    inspector = page.locator('#inspector-content').inner_text()
    page.evaluate("""async () => {
      const {getSelectedMeshes} = await import('./js/scene/selection.js');
      window.__selection = getSelectedMeshes();
    }""")
    point = project_mesh_points(page, [[0.25, 0.25, 0]])[0]
    page.mouse.click(*point, button='right')
    assert page.locator('.draw-item.selected').count() == 2
    assert page.evaluate("""async () => {
      const {getSelectedMeshes} = await import('./js/scene/selection.js');
      return getSelectedMeshes().every((mesh, i) => mesh === window.__selection[i]);
    }""")
    assert page.locator('#inspector-content').is_visible()
    assert page.locator('#inspector-content').inner_text() == inspector
    assert bridge_calls(page, 'meshApply') == []


def test_face_selection_cancel_and_apply_preserve_complete_authored_partition(viewer):
    payload = model_payload()
    mesh = payload['meshes']['mesh-00']
    mesh.update(pos=append_stream(payload, 'f', [0,0,0, 1,0,0, 1,1,0, 0,1,0]),
                idx=append_stream(payload, 'I', [0,1,2, 0,2,3]), drawindexed=[6,0,0],
                identity={'key': 'identity-01', 'component': 'component-00'})
    page = viewer({'fixture-01': payload})
    open_model(page, 'fixture-01')
    wait_loaded(page)
    point = project_mesh_points(page, [[0.7, 0.3, 0]])[0]
    for action in ['cancelSelection', 'applySelection']:
        page.locator('.draw-item').first.click(button='right')
        page.locator('[data-i18n="mesh.separateBySelection"]').click()
        page.mouse.click(*point)
        page.mouse.click(*point, button='right')
        page.locator(f'[data-i18n="mesh.{action}"]').click()
        assert bridge_calls(page, 'meshApply') == []
        if action == 'cancelSelection':
            assert page.locator('.draw-item').count() == 1
            assert page.evaluate('!window.__bridge.pending["fixture-01"]')
    assert page.locator('.draw-item').count() == 2
    page.locator('.group-name').first.click(button='right')
    page.locator('[data-i18n="mesh.applyMeshChanges"]').click()
    page.locator('#dialog-backdrop.show').wait_for()
    page.locator('#dialog-ok').click()
    page.wait_for_function('window.__bridge.calls.some(call => call.name === "meshApply")')
    wait_loaded(page)
    request = bridge_calls(page, 'meshApply')[0][1]['mesh']
    assert sorted(request['parts']) == [[0], [1]]
    assert request['sources'] == mesh['sources']
    assert page.evaluate('window.__bridge.pending["fixture-01"]')
    assert bridge_calls(page, 'export') == []


def test_edit_mesh_feature_off_hides_only_edit_context_actions(viewer):
    page = viewer({'fixture-01': model_payload()})
    open_model(page, 'fixture-01')
    wait_loaded(page)
    page.locator('.draw-item').click(button='right')
    menu = page.locator('.mesh-context-menu')
    assert menu.locator('[data-i18n="mesh.separateLooseParts"]').is_visible()
    page.evaluate("""() => {
      const action = document.createElement('button');
      action.dataset.fixture = 'action-01';
      action.textContent = 'action-01';
      action.setAttribute('role', 'menuitem');
      document.querySelector('.mesh-context-menu').append(action);
      document.body.classList.add('feature-edit-mesh-off');
    }""")
    page.locator('.draw-item').click(button='right')
    assert menu.is_visible()
    assert menu.locator('[data-fixture="action-01"]').is_visible()
    actions = menu.locator('.mesh-edit-context-action')
    assert actions.count() > 0
    assert all(actions.nth(index).is_hidden() for index in range(actions.count()))
    assert bridge_calls(page, 'meshApply') == []

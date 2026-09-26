"""Selection reaches the Inspector without changing staged source state."""

from .payloads import append_stream, model_payload
from .support import bridge_calls, open_model, project_mesh_points, wait_loaded


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

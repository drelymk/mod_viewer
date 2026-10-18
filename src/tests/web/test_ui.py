"""Selection reaches the Inspector without changing staged source state."""

from .payloads import append_stream, controlled_payload, model_payload, textured_payload
from .support import bridge_calls, open_model, project_mesh_points, wait_loaded


def test_unavailable_texture_selection_reports_localized_error_without_mutation(viewer):
    payload = textured_payload()
    unavailable = 'diffuse::texture-03.dds'
    payload['texture_pools']['pool-01'].append({
        'tex_key': unavailable, 'file': 'texture-03.dds', 'label': 'texture-03'})
    page = viewer({'fixture-01': payload})
    open_model(page, 'fixture-01')
    wait_loaded(page)
    page.locator('#inspector-tab').click()
    page.locator('.draw-item').click()
    messages = []
    for locale in ('en', 'zh-CN', 'ja', 'ko', 'es', 'ru'):
        page.evaluate("""async locale => {
          const {setLocale} = await import('./js/i18n/index.js');
          setLocale(locale);
        }""", locale)
        page.locator(f'.inspector-texture-option[data-texture-value="{unavailable}"]').click()
        page.locator('#dialog-backdrop.show').wait_for()
        message = page.locator('#dialog-message').inner_text()
        expected = page.evaluate("""async () => {
          const {t} = await import('./js/i18n/index.js');
          return t('texture.loadFailed', {file: 'texture-03.dds'});
        }""")
        assert message == expected
        messages.append(message)
        page.locator('#dialog-ok').click()
        assert page.evaluate('window.modViewer.activeMeshes[0].userData.manualTexOverride === undefined')
    assert len(set(messages)) == len(messages)
    assert bridge_calls(page, 'textures') == []
    assert bridge_calls(page, 'export') == []
    page.locator('.inspector-texture-option[data-texture-value="diffuse::texture-02.png"]').click()
    page.wait_for_function('window.modViewer.activeMeshes[0].userData.manualTexOverride === "diffuse::texture-02.png"')
    assert len(bridge_calls(page, 'textures')) == 1
    page.locator('.inspector-manage-textures').click()
    page.evaluate("""() => {
      window.pywebview.api.pick_texture_file = async () => ({
        error: 'untranslated fixture error', error_code: 'texture_load_failed', file: 'texture-03.dds'
      });
    }""")
    page.locator('#texm-add').click()
    page.locator('#texm-list [role="alert"]').wait_for()
    assert page.locator('#texm-list [role="alert"]').inner_text() == messages[-1]
    page.evaluate("""async () => {
      const {setLocale} = await import('./js/i18n/index.js');
      setLocale('en');
    }""")
    assert page.locator('#texm-list [role="alert"]').inner_text() == messages[0]
    assert page.locator('.texm-row').count() == 3
    page.locator('.texm-map-cell').first.click()
    assert page.locator('#texm-list [role="alert"]').inner_text() == messages[0]
    assert len(bridge_calls(page, 'textures')) == 1


def test_delayed_texture_load_errors_only_report_the_current_manual_choice(viewer):
    payload = textured_payload()
    for index in (3, 4):
        key = f'diffuse::texture-{index:02d}.dds'
        payload['textures'][key] = f'/fixture-{index:02d}.dds'
        payload['texture_pools']['pool-01'].append({
            'tex_key': key, 'label': f'texture-{index:02d}'})
    page = viewer({'fixture-01': payload})
    pending = []
    page.route('**/fixture-03.dds', lambda route: route.fulfill(
        status=200, content_type='image/vnd-ms.dds', body=b'unsupported DDS'))
    page.route('**/fixture-04.dds', lambda route: pending.append(route))
    open_model(page, 'fixture-01')
    wait_loaded(page)
    page.evaluate("""() => {
      window.__textureErrors = [];
      window.addEventListener('mod-viewer-texture-load-error', event =>
        window.__textureErrors.push(event.detail.key));
    }""")
    page.locator('#inspector-tab').click()
    page.locator('.draw-item').click()
    page.locator('.inspector-texture-option[data-texture-value="diffuse::texture-03.dds"]').click()
    page.locator('#dialog-backdrop.show').wait_for()
    assert 'texture-03.dds' in page.locator('#dialog-message').inner_text()
    page.locator('#dialog-ok').click()
    with page.expect_request('**/fixture-04.dds'):
        page.locator('.inspector-texture-option[data-texture-value="diffuse::texture-04.dds"]').click()
    page.locator('.inspector-texture-option[data-texture-value="diffuse::texture-02.png"]').click()
    assert len(pending) == 1
    pending[0].fulfill(status=200, content_type='image/vnd-ms.dds', body=b'unsupported DDS')
    page.wait_for_function('window.__textureErrors.includes("diffuse::texture-04.dds")')
    assert page.locator('#dialog-backdrop').is_hidden()
    assert page.evaluate('window.modViewer.activeMeshes[0].userData.manualTexOverride === "diffuse::texture-02.png"')
    assert bridge_calls(page, 'export') == []


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
    payload = model_payload(3)
    payload['meshes']['mesh-01']['component'] = 'component-00'
    payload['meshes']['mesh-01']['identity']['component'] = 'component-00'
    for index, mesh in enumerate(payload['meshes'].values()):
        x = index * 2
        mesh['pos'] = append_stream(payload, 'f', [x,0,0, x+1,0,0, x,1,0])
    page = viewer({'fixture-01': payload})
    open_model(page, 'fixture-01')
    wait_loaded(page, 3)
    page.evaluate("""async () => {
      const THREE = await import('three/webgpu');
      const {camera, controls} = await import('./js/scene/scene.js');
      const {computeModelBounds} = await import('./js/scene/model-bounds.js');
      const {getSelectedMeshes} = await import('./js/scene/selection.js');
      window.__originalMeshes = [...window.modViewer.activeMeshes];
      window.__view = () => ({position: camera.position.toArray(), target: controls.target.toArray()});
      window.__isFramed = () => {
        const center = computeModelBounds(getSelectedMeshes()).getCenter(new THREE.Vector3());
        return controls.target.distanceTo(center) < 1e-6 &&
          camera.getWorldDirection(new THREE.Vector3()).angleTo(center.sub(camera.position)) < 1e-6;
      };
      window.__viewDistance = () => camera.position.distanceTo(controls.target);
    }""")
    rows = page.locator('.draw-item')
    status = page.locator('#selected-mesh-status')
    assert page.locator('#controls-panel').is_visible()
    initial_view = page.evaluate('window.__view()')
    rows.nth(0).click()
    assert page.locator('.draw-item.selected').count() == 1
    assert page.locator('#controls-panel').is_visible()
    assert status.inner_text() == 'component-00 > mesh-00'
    assert page.evaluate('window.__view()') == initial_view
    rows.nth(1).click(modifiers=['Control'])
    assert page.locator('.draw-item.selected').count() == 2
    assert status.inner_text() == '2 meshes selected · component-00'
    rows.nth(2).click(modifiers=['Control'])
    assert status.inner_text() == '3 meshes selected · 2 components'
    page.evaluate("import('./js/i18n/index.js').then(module => module.setLocale('ja'))")
    assert status.inner_text() == '3 個のメッシュを選択 · 2 個のコンポーネント'
    page.evaluate("import('./js/i18n/index.js').then(module => module.setLocale('en'))")
    assert status.inner_text() == '3 meshes selected · 2 components'
    rows.nth(2).click(modifiers=['Control'])
    assert page.locator('.draw-item.selected').count() == 2
    assert page.locator('#controls-panel').is_visible()

    rows.nth(2).click()
    points = project_mesh_points(page, [[-0.1, 1.1, 0], [3.1, -0.1, 0]])
    page.keyboard.down('Control')
    page.mouse.move(*points[0])
    page.mouse.down()
    page.mouse.move(*points[1], steps=5)
    page.mouse.up()
    page.keyboard.up('Control')
    assert page.locator('.draw-item.selected').count() == 3
    assert page.locator('#controls-panel').is_visible()
    rows.nth(2).click()
    first = rows.nth(0).bounding_box()
    second = rows.nth(1).bounding_box()
    page.keyboard.down('Control')
    page.mouse.move(first['x'] + first['width'] - 5, first['y'] + first['height'] / 2)
    page.mouse.down()
    page.mouse.move(second['x'] + second['width'] - 5, second['y'] + second['height'] / 2, steps=5)
    page.mouse.up()
    page.keyboard.up('Control')
    assert page.locator('.draw-item.selected').count() == 3
    assert page.locator('#controls-panel').is_visible()

    page.locator('.group-name').first.click()
    assert status.inner_text() == 'component-00'
    assert page.locator('.draw-item.selected').count() == 0
    assert page.locator('#controls-panel').is_visible()
    point = project_mesh_points(page, [[2.25, 0.25, 0]])[0]
    page.mouse.click(*point)
    assert page.locator('.draw-item.selected').count() == 1
    assert 'selected' in rows.nth(1).get_attribute('class')
    assert page.locator('#controls-panel').is_visible()

    page.locator('#inspector-tab').click()
    rows.nth(0).click()
    assert status.inner_text() == 'component-00 > mesh-00'
    assert page.locator('#inspector-content').is_visible()
    page.locator('#inspector-tab').click()
    assert page.locator('#inspector-panel').is_hidden()
    rows.nth(1).click(modifiers=['Control'])
    # A different hit prevents the preceding single-click from consuming this double-click.
    point = project_mesh_points(page, [[0.25, 0.25, 0]])[0]
    page.evaluate("""async () => {
      const {controls} = await import('./js/scene/scene.js');
      controls.focusAnimationTime = 20;
    }""")
    page.mouse.dblclick(*point)
    assert page.locator('#inspector-panel').is_visible()
    assert page.locator('.draw-item.selected').count() == 1
    assert 'selected' in rows.nth(0).get_attribute('class')
    assert page.evaluate("""async () => {
      const {controls} = await import('./js/scene/scene.js');
      const started = performance.now();
      let frames = 0;
      await new Promise(resolve => {
        const nextFrame = now => {
          if (++frames >= 3 && now - started >= controls.focusAnimationTime) resolve();
          else requestAnimationFrame(nextFrame);
        };
        requestAnimationFrame(nextFrame);
      });
      return window.__isFramed();
    }""")
    single_distance = page.evaluate('window.__viewDistance()')

    page.locator('#controls-tab').click()
    rows.nth(1).click(modifiers=['Control'])
    page.keyboard.press('f')
    assert page.evaluate('window.__isFramed()')
    assert page.evaluate('window.__viewDistance()') > single_distance
    assert page.locator('#controls-panel').is_visible()
    rows.nth(2).click()
    page.keyboard.press('f')
    assert page.evaluate('window.__isFramed()')
    assert page.locator('#controls-panel').is_visible()

    for click_name in (False, True):
        if page.locator('#controls-panel').is_hidden():
            page.locator('#controls-tab').click()
        rows.nth(2).click(modifiers=['Control'])
        if click_name:
            rows.nth(0).locator('.mesh-name').dblclick(modifiers=['Control'])
        else:
            box = rows.nth(0).bounding_box()
            rows.nth(0).dblclick(position={'x': box['width'] - 5, 'y': box['height'] / 2})
        assert page.locator('#inspector-panel').is_visible()
        assert page.locator('.draw-item.selected').count() == 1
        assert 'selected' in rows.nth(0).get_attribute('class')
        assert page.evaluate('window.__isFramed()')
        assert page.locator('.mesh-name-input').count() == 0

    page.locator('#controls-tab').click()
    rows.nth(0).locator('.mesh-state-btn').dblclick()
    assert page.locator('#controls-panel').is_visible()
    assert page.evaluate('window.modViewer.activeMeshes.every(mesh => mesh.visible)')
    rows.nth(0).click(button='right')
    assert page.locator('#controls-panel').is_visible()
    page.locator('[data-i18n="mesh.rename"]').click()
    editor = page.locator('.mesh-name-input')
    editor.fill('mesh-renamed')
    view = page.evaluate('window.__view()')
    editor.dblclick()
    assert page.locator('#controls-panel').is_visible()
    assert page.evaluate('window.__view()') == view
    editor.press('Enter')
    assert rows.nth(0).locator('.mesh-name').inner_text() == 'mesh-renamed'
    assert status.inner_text() == 'component-00 > mesh-renamed'
    assert len(bridge_calls(page, 'names')) == 1
    rows.nth(0).click(button='right')
    page.locator('[data-i18n="mesh.rename"]').click()
    editor.fill('mesh-cancelled')
    editor.press('Escape')
    assert rows.nth(0).locator('.mesh-name').inner_text() == 'mesh-renamed'
    assert len(bridge_calls(page, 'names')) == 1

    page.locator('.group-name').first.click()
    view = page.evaluate('window.__view()')
    page.keyboard.press('f')
    assert page.evaluate('window.__view()') == view
    rows.nth(0).click()
    rect = page.locator('#canvas-container canvas').first.bounding_box()
    page.mouse.dblclick(rect['x'] + rect['width'] / 2, rect['y'] + rect['height'] * 0.9)
    assert page.locator('#controls-panel').is_visible()
    assert status.inner_text() == ''
    assert page.evaluate('window.__view()') == view
    assert page.evaluate('window.modViewer.activeMeshes.every((mesh, i) => mesh === window.__originalMeshes[i])')
    assert bridge_calls(page, 'export') == []
    assert bridge_calls(page, 'load') == [['fixture-01', False]]


def test_shift_click_ranges_follow_expanded_rows_and_preserve_selection_contract(viewer):
    payload = model_payload(6)
    for index, mesh in enumerate(payload['meshes'].values()):
        mesh['source'] = f'source-{index // 4:02d}.ini'
    page = viewer({'fixture-01': payload, 'fixture-02': model_payload(2)})
    open_model(page, 'fixture-01')
    wait_loaded(page, 6)
    page.evaluate("""() => {
      window.__selectionEvents = [];
      window.addEventListener('mod-viewer-mesh-selected', ({detail}) => {
        const meshes = window.modViewer.activeMeshes;
        window.__selectionEvents.push({primary: meshes.indexOf(detail.mesh),
          selected: detail.meshes.map(mesh => meshes.indexOf(mesh)).sort((a, b) => a - b)});
      });
    }""")
    rows = page.locator('.draw-item')

    def assert_selection(indices, primary):
        assert rows.evaluate_all("""rows => rows.flatMap((row, index) =>
          row.classList.contains('selected') ? [index] : [])""") == indices
        assert page.evaluate('window.__selectionEvents.at(-1)') == {
            'primary': primary, 'selected': indices}

    rows.nth(2).click(modifiers=['Shift'])
    assert_selection([2], 2)
    count = page.evaluate('window.__selectionEvents.length')
    rows.nth(5).click(modifiers=['Shift'])
    assert_selection([2, 3, 4, 5], 5)
    assert page.evaluate('window.__selectionEvents.length') == count + 1
    rows.nth(3).click(modifiers=['Shift'])
    assert_selection([2, 3], 3)
    rows.nth(0).click(modifiers=['Shift'])
    assert_selection([0, 1, 2], 0)
    rows.nth(5).click(modifiers=['Control'])
    assert_selection([0, 1, 2, 5], 5)
    rows.nth(4).click(modifiers=['Control', 'Shift'])
    assert_selection([0, 1, 2, 4, 5], 4)
    rows.nth(3).click(modifiers=['Shift'])
    assert_selection([3, 4, 5], 3)
    rows.nth(0).click()
    assert_selection([0], 0)
    page.locator('.group-hdr .group-toggle').nth(2).click()
    rows.nth(1).locator('.mesh-state-btn').click()
    rows.nth(4).click(modifiers=['Shift'])
    assert_selection([0, 1, 3, 4], 4)
    assert page.locator('.group-items.collapsed').count() == 1
    assert not page.evaluate('window.modViewer.activeMeshes[1].visible')
    assert page.evaluate('window.getSelection().toString()') == ''
    rows.nth(5).locator('.mesh-state-btn').click(modifiers=['Shift'])
    assert_selection([0, 1, 3, 4], 4)
    rows.nth(3).click(modifiers=['Shift'])
    assert_selection([0, 1, 3], 3)
    page.locator('.group-hdr .group-name').first.click()
    rows.nth(5).click(modifiers=['Shift'])
    assert_selection([5], 5)
    page.evaluate("""async () => {
      const {selectMesh} = await import('./js/scene/selection.js');
      selectMesh(window.modViewer.activeMeshes[1]);
    }""")
    rows.nth(4).click(modifiers=['Shift'])
    assert_selection([1, 3, 4], 4)
    page.locator('.mesh-src-hdr .group-toggle').first.click()
    rows.nth(5).click(modifiers=['Shift'])
    assert_selection([5], 5)
    assert page.locator('.mesh-src-items.collapsed').count() == 1
    open_model(page, 'fixture-02')
    wait_loaded(page, 2)
    rows.nth(1).click(modifiers=['Shift'])
    assert_selection([1], 1)
    for operation in ('record', 'export', 'names'):
        assert bridge_calls(page, operation) == []
    assert page.evaluate('Object.values(window.__bridge.pending).every(value => !value)')


def test_visibility_eyes_keyboard_and_reset_synchronize_without_staging(viewer):
    page = viewer({'fixture-01': model_payload(3)})
    open_model(page, 'fixture-01')
    wait_loaded(page, 3)
    page.locator('#inspector-tab').click()
    rows = page.locator('.draw-item')
    rows.nth(0).click()
    eye = page.locator('.inspector-visibility-btn')
    button = rows.nth(0).locator('.mesh-state-btn')
    page.evaluate('window.__inspectorEye = document.querySelector(".inspector-visibility-btn")')
    eye.click()
    page.wait_for_function('!window.modViewer.activeMeshes[0].visible')
    assert eye.get_attribute('aria-pressed') == button.get_attribute('aria-pressed') == 'false'
    assert 'state-manual' in eye.get_attribute('class')
    assert page.locator('.draw-item.selected').count() == 1
    assert page.evaluate('window.modViewer.activeMeshes[1].visible')
    eye.click()
    assert eye.get_attribute('aria-pressed') == button.get_attribute('aria-pressed') == 'true'
    assert 'state-manual' not in eye.get_attribute('class')
    button.click()
    assert eye.get_attribute('aria-pressed') == 'false'
    page.keyboard.press('h')
    assert not page.evaluate('window.modViewer.activeMeshes[0].visible')
    rows.nth(0).click()
    for _ in range(2):
        page.keyboard.press('h')
        assert not page.evaluate('window.modViewer.activeMeshes[0].visible')
        assert eye.get_attribute('aria-pressed') == button.get_attribute('aria-pressed') == 'false'
        assert page.locator('.draw-item.selected').count() == 1
    eye.click()
    assert page.evaluate('window.__inspectorEye === document.querySelector(".inspector-visibility-btn")')

    rows.nth(1).locator('.mesh-state-btn').click()
    rows.nth(1).click(modifiers=['Control'])
    assert page.evaluate('window.modViewer.activeMeshes.map(mesh => mesh.visible)') == [True, False, True]
    eye.click()
    assert page.evaluate('window.modViewer.activeMeshes.map(mesh => mesh.visible)') == [True, True, True]
    button.click()
    assert page.evaluate('window.modViewer.activeMeshes.map(mesh => mesh.visible)') == [False, False, True]
    button.click()
    assert page.evaluate('window.modViewer.activeMeshes.every(mesh => mesh.visible)')
    rows.nth(2).locator('.mesh-state-btn').click()
    assert page.evaluate('window.modViewer.activeMeshes.map(mesh => mesh.visible)') == [True, True, False]
    assert page.locator('.draw-item.selected').count() == 2
    rows.nth(2).locator('.mesh-state-btn').click()
    page.locator('#mesh-list').focus()
    for _ in range(2):
        page.keyboard.press('h')
        assert page.evaluate('window.modViewer.activeMeshes.map(mesh => mesh.visible)') == [False, False, True]
        assert page.locator('.draw-item.selected').count() == 2
        assert eye.get_attribute('aria-pressed') == 'false'
    page.evaluate("""async () => {
      const {resetMeshState} = await import('./js/mesh/visibility.js');
      resetMeshState();
    }""")
    assert page.evaluate('window.modViewer.activeMeshes.every(mesh => mesh.visible)')
    assert button.get_attribute('aria-pressed') == 'true'
    assert eye.get_attribute('aria-pressed') == 'true'

    rows.nth(1).click()
    rows.nth(1).click(button='right')
    page.locator('[data-i18n="mesh.rename"]').click()
    page.evaluate("""async () => {
      const {camera, controls} = await import('./js/scene/scene.js');
      window.__view = () => ({position: camera.position.toArray(), target: controls.target.toArray()});
    }""")
    view = page.evaluate('window.__view()')
    for key in ('h', 'f'):
        page.keyboard.press(key)
    assert page.evaluate('window.modViewer.activeMeshes.every(mesh => mesh.visible)')
    assert page.evaluate('window.__view()') == view
    page.keyboard.press('Escape')
    for tag in ('textarea', 'select', 'button', 'div'):
        page.evaluate("""tag => {
          const editor = document.createElement(tag);
          editor.id = 'fixture-editor';
          if (tag === 'div') editor.contentEditable = 'true';
          document.body.appendChild(editor);
          editor.focus();
        }""", tag)
        for key in ('h', 'f'):
            page.keyboard.press(key)
        assert page.evaluate('window.modViewer.activeMeshes.every(mesh => mesh.visible)')
        assert page.evaluate('window.__view()') == view
        page.evaluate('document.getElementById("fixture-editor").remove()')
    for interaction in ('transform', 'picking'):
        page.evaluate("""async interaction => {
          const rig = await import('./js/scene/rig-overlay-state.js');
          window.__setRigInteraction = interaction === 'transform'
            ? rig.setRigTransformInteractionActive : rig.setRigJointPickingActive;
          window.__setRigInteraction(true);
        }""", interaction)
        for key in ('h', 'f'):
            page.keyboard.press(key)
        assert page.evaluate('window.modViewer.activeMeshes.every(mesh => mesh.visible)')
        assert page.evaluate('window.__view()') == view
        page.evaluate('window.__setRigInteraction(false)')
    for key in ('Control+h', 'Alt+h', 'Meta+h', 'Control+f', 'Alt+f', 'Meta+f'):
        page.keyboard.press(key)
        assert page.evaluate('window.modViewer.activeMeshes.every(mesh => mesh.visible)')
        assert page.evaluate('window.__view()') == view
    assert page.locator('.draw-item.selected').count() == 1
    page.locator('.group-name').first.click()
    assert eye.count() == 0
    page.keyboard.press('h')
    assert page.evaluate('window.modViewer.activeMeshes.every(mesh => mesh.visible)')
    assert page.locator('.draw-item.selected').count() == 0
    assert bridge_calls(page, 'names') == []
    assert bridge_calls(page, 'record') == []
    assert bridge_calls(page, 'export') == []
    assert page.evaluate('!window.__bridge.pending["fixture-01"]')


def test_mesh_select_all_respects_panel_viewport_and_editing_context(viewer):
    page = viewer({'fixture-01': model_payload(4)})
    open_model(page, 'fixture-01')
    wait_loaded(page, 4)
    rows = page.locator('.draw-item')
    rows.nth(1).locator('.mesh-state-btn').click()
    page.locator('.group-hdr .group-toggle').nth(2).click()
    page.locator('#mesh-list').focus()
    page.keyboard.press('Control+a')
    assert page.locator('.draw-item.selected').count() == 4
    assert page.locator('#mesh-list').evaluate('''list => {
      const style = getComputedStyle(list);
      return document.activeElement === list && style.boxShadow === 'none' && style.outlineStyle === 'none';
    }''')
    assert rows.nth(0).evaluate("row => getComputedStyle(row).outlineStyle !== 'none'")
    assert page.evaluate('window.modViewer.activeMeshes.map(mesh => mesh.visible)') == [True, False, True, True]
    page.keyboard.press('h')
    assert page.evaluate('window.modViewer.activeMeshes.every(mesh => !mesh.visible)')
    rows.nth(0).locator('.mesh-state-btn').click()
    assert page.evaluate('window.modViewer.activeMeshes.every(mesh => mesh.visible)')
    rows.nth(1).click()
    rows.nth(1).locator('.mesh-state-btn').click()
    canvas = page.locator('#canvas-container > canvas')
    canvas.focus()
    page.mouse.move(2, 2)
    page.keyboard.press('Control+a')
    assert page.locator('.draw-item.selected').count() == 3
    assert not rows.nth(1).evaluate('row => row.classList.contains("selected")')
    page.keyboard.press('h')
    assert page.evaluate('window.modViewer.activeMeshes.every(mesh => !mesh.visible)')
    page.keyboard.press('Control+a')
    assert page.locator('.draw-item.selected').count() == 0
    page.locator('#reset-state-btn').click()
    rows.nth(0).click()
    if page.locator('#controls-panel').is_hidden():
        page.locator('#controls-tab').click()
    page.locator('#controls-panel').evaluate("panel => {panel.tabIndex = 0; panel.focus();}")
    page.locator('#controls-panel').hover(position={'x': 10, 'y': 10})
    page.keyboard.press('Control+a')
    assert page.locator('.draw-item.selected').count() == 1
    for tag in ('input', 'textarea', 'div'):
        page.evaluate("""tag => {
          const editor = document.createElement(tag);
          editor.id = 'fixture-editor';
          if (tag === 'div') editor.contentEditable = 'true';
          document.getElementById('mesh-list').appendChild(editor);
          editor.focus();
        }""", tag)
        page.keyboard.press('Control+a')
        assert page.locator('.draw-item.selected').count() == 1
        page.evaluate('document.getElementById("fixture-editor").remove()')
    for interaction in ('transform', 'picking'):
        page.evaluate("""async interaction => {
          const rig = await import('./js/scene/rig-overlay-state.js');
          window.__setRigInteraction = interaction === 'transform'
            ? rig.setRigTransformInteractionActive : rig.setRigJointPickingActive;
          window.__setRigInteraction(true);
        }""", interaction)
        canvas.focus()
        page.keyboard.press('Control+a')
        assert page.locator('.draw-item.selected').count() == 1
        page.evaluate('window.__setRigInteraction(false)')
    rows.nth(0).click(button='right')
    page.locator('[data-i18n="mesh.rename"]').click()
    page.keyboard.press('Control+a')
    assert page.locator('.draw-item.selected').count() == 1
    page.keyboard.press('Escape')
    assert bridge_calls(page, 'record') == bridge_calls(page, 'export') == []
    assert page.evaluate('!window.__bridge.pending["fixture-01"]')


def test_toggle_visibility_changes_only_wired_meshes(viewer):
    payload = controlled_payload(4)
    payload['controls']['toggles']['KeyFixture02'] = {
        'name': 'control-02', 'ini': 'source-01.ini', 'section': 'KeyFixture02', 'wired': True,
        'vars': [{'var': 'input02', 'default': '0', 'values': ['0', '1']}],
    }
    payload['state']['defaults'].update(input02='0', derived01='0')
    payload['state']['rules'] = [
        {'var': 'derived01', 'value': str(value),
         'conditions': [[{'var': 'input01', 'value': str(value), 'negate': False}]]}
        for value in (0, 1)]
    payload['meshes']['mesh-01']['conditions'][0][0]['var'] = 'input02'
    payload['meshes']['mesh-02']['conditions'] = []
    payload['meshes']['mesh-03']['conditions'][0][0]['var'] = 'derived01'
    page = viewer({'fixture-01': payload})
    open_model(page, 'fixture-01')
    wait_loaded(page, 4)
    rows = page.locator('.draw-item')
    for index in (1, 2):
        rows.nth(index).locator('.mesh-state-btn').click()
    if page.locator('#controls-panel').is_hidden():
        page.locator('#controls-tab').click()
    cycles = page.locator('#toggle-list .toggle-cycle-btn')
    cycles.nth(0).click()
    assert page.evaluate('window.modViewer.activeMeshes.every(mesh => !mesh.visible)')
    assert page.evaluate('window.modViewer.activeMeshes.map(mesh => mesh.userData.manuallyToggled)') == [False, True, True, False]
    rows.nth(0).locator('.mesh-state-btn').click()
    cycles.nth(1).click()
    assert page.evaluate('window.modViewer.activeMeshes.map(mesh => mesh.visible)') == [True, False, False, False]
    assert page.evaluate('window.modViewer.activeMeshes.map(mesh => mesh.userData.manuallyToggled)') == [True, False, True, False]
    cycles.nth(0).click()
    assert page.evaluate('window.modViewer.activeMeshes.map(mesh => mesh.visible)') == [True, False, False, True]
    cycles.nth(1).click()
    assert page.evaluate('window.modViewer.activeMeshes.map(mesh => mesh.visible)') == [True, True, False, True]
    page.evaluate("""async () => {
      const {refreshAll} = await import('./js/mesh/visibility.js');
      refreshAll();
    }""")
    assert page.evaluate('window.modViewer.activeMeshes.map(mesh => mesh.visible)') == [True, True, False, True]
    assert rows.nth(2).locator('.mesh-state-btn').get_attribute('aria-pressed') == 'false'
    assert bridge_calls(page, 'record') == bridge_calls(page, 'export') == []
    assert page.evaluate('!window.__bridge.pending["fixture-01"]')


def test_component_and_ini_eyes_follow_child_visibility_without_staging(viewer):
    single = model_payload(1)
    multiple = model_payload(4)
    for index, mesh in enumerate(multiple['meshes'].values()):
        source = 'source-01.ini' if index < 3 else 'source-02.ini'
        component = 'component-00' if index < 2 else f'component-{index:02d}'
        mesh.update(source=source, component=component)
        mesh['identity'].update(source=source, component=component)
        mesh['sources'][0]['ini'] = source
    grouped = model_payload(2)
    for index, mesh in enumerate(grouped['meshes'].values()):
        mesh['source'] = 'folder-01'
        mesh['identity']['source'] = f'folder-01/source-{index:02d}.ini'
        mesh['sources'][0]['ini'] = mesh['identity']['source']
    page = viewer({'fixture-01': single, 'fixture-02': multiple, 'fixture-03': grouped})
    open_model(page, 'fixture-01')
    wait_loaded(page)
    assert page.locator('.component-visibility-btn .ui-icon').count() == 1
    assert page.locator('.group-hdr input[type="checkbox"]').count() == 0
    assert page.locator('.source-visibility-btn').count() == 0
    open_model(page, 'fixture-02')
    wait_loaded(page, 4)
    rows = page.locator('.draw-item')
    components = page.locator('.component-visibility-btn')
    sources = page.locator('.source-visibility-btn')
    assert components.count() == 3
    assert sources.count() == 2
    assert sources.locator('.ui-icon').count() == 2
    rows.nth(0).click()
    rows.nth(0).locator('.mesh-state-btn').click()
    assert components.nth(0).get_attribute('aria-pressed') == 'mixed'
    assert sources.nth(0).get_attribute('aria-pressed') == 'mixed'
    components.nth(0).click()
    assert page.evaluate('window.modViewer.activeMeshes.every(mesh => mesh.visible)')
    components.nth(0).click()
    assert page.evaluate('window.modViewer.activeMeshes.map(mesh => mesh.visible)') == [False, False, True, True]
    assert components.nth(0).get_attribute('aria-pressed') == 'false'
    assert sources.nth(0).get_attribute('aria-pressed') == 'mixed'
    sources.nth(0).click()
    assert page.evaluate('window.modViewer.activeMeshes.every(mesh => mesh.visible)')
    sources.nth(0).focus()
    sources.nth(0).press('Space')
    assert page.evaluate('window.modViewer.activeMeshes.map(mesh => mesh.visible)') == [False, False, False, True]
    assert sources.nth(0).get_attribute('aria-pressed') == 'false'
    assert 'state-hidden' in sources.nth(0).get_attribute('class')
    assert components.nth(1).get_attribute('aria-pressed') == 'false'
    assert sources.nth(1).get_attribute('aria-pressed') == 'true'
    assert page.locator('.draw-item.selected').count() == 1
    assert page.locator('.mesh-src-items.collapsed, .group-items.collapsed').count() == 0
    page.locator('.mesh-src-hdr .group-toggle').first.click()
    sources.nth(0).click()
    assert page.evaluate('window.modViewer.activeMeshes.every(mesh => mesh.visible)')
    assert page.locator('.mesh-src-items.collapsed').count() == 1
    sources.nth(0).click()
    page.evaluate("""async () => {
      const {resetMeshState} = await import('./js/mesh/visibility.js');
      resetMeshState();
    }""")
    assert components.nth(0).get_attribute('aria-pressed') == 'true'
    assert sources.nth(0).get_attribute('aria-pressed') == 'true'
    assert 'state-manual' not in sources.nth(0).get_attribute('class')
    assert bridge_calls(page, 'record') == []
    assert bridge_calls(page, 'export') == []
    assert page.evaluate('!window.__bridge.pending["fixture-02"]')
    open_model(page, 'fixture-03')
    wait_loaded(page, 2)
    assert sources.count() == 1
    sources.click()
    assert page.evaluate('window.modViewer.activeMeshes.every(mesh => !mesh.visible)')


def test_keyboard_and_inspector_visibility_record_like_meshes_eye(viewer):
    payload = controlled_payload(2)
    payload['meshes']['mesh-01']['conditions'] = []
    payload['meshes']['mesh-01']['sources'][0]['line'] = 2
    page = viewer({'fixture-01': payload})
    open_model(page, 'fixture-01')
    wait_loaded(page, 2)
    requests = []
    for action in ('keyboard', 'inspector', 'panel'):
        if page.locator('#controls-panel').is_hidden():
            page.locator('#controls-tab').click()
        page.locator('#toggle-list [aria-label="Record toggle mesh visibility"]').click()
        page.locator('.toggle-row.recording').wait_for()
        page.locator('.draw-item').nth(1).click()
        point = project_mesh_points(page, [[0.25, 0.25, 0]])[0]
        page.mouse.click(*point, button='right')
        assert page.locator('.mesh-context-menu:visible').count() == 0
        if action == 'keyboard':
            page.evaluate('document.activeElement.blur()')
            page.keyboard.press('h')
        elif action == 'inspector':
            if page.locator('#inspector-panel').is_hidden():
                page.locator('#inspector-tab').click()
            page.locator('.inspector-visibility-btn').click()
        else:
            page.locator('.draw-item').nth(1).locator('.mesh-state-btn').click()
        assert page.evaluate('window.modViewer.activeMeshes.map(mesh => mesh.visible)') == [True, False]
        if page.locator('#controls-panel').is_hidden():
            page.locator('#controls-tab').click()
        page.locator('.toggle-cycle-btn').click()
        assert page.evaluate('window.modViewer.activeMeshes.map(mesh => mesh.visible)') == [False, True]
        assert len(bridge_calls(page, 'record')) == len(requests)
        page.locator('.toggle-record-save').click()
        page.wait_for_function('count => window.__bridge.calls.filter(call => call.name === "record").length > count',
                               arg=len(requests))
        page.wait_for_function('!document.querySelector(".toggle-row.recording")')
        requests.append(bridge_calls(page, 'record')[-1])
    assert all(request == requests[0] for request in requests)
    assert requests[0][:4] == ['fixture-01', 'source-01.ini', 'KeyFixture', {'0': [1], '1': [2]}]
    assert [target['line'] for target in requests[0][4]] == [1, 2]
    assert bridge_calls(page, 'export') == []
    assert page.evaluate('window.__bridge.pending["fixture-01"]')


def test_opaque_display_labels_render_as_text_in_panel_and_inspector(viewer):
    payload = model_payload()
    label = '<b data-fixture="marker-01">fixture-01</b>'
    payload['metadata']['mesh_names'] = {payload['meshes']['mesh-00']['identity']['key']: label}
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
        'environment': 'sunset', 'wireframe': True, 'outlines': True,
        'ambientOcclusion': 0.4, 'bloom': True, 'glossy': True,
        'toonShading': True, 'grid': False, 'smoothShading': False,
        'textureMode': 'diffuse', 'keyLightIntensity': 0.75,
        'navigationGizmo': False,
    }
    page = viewer({'fixture-01': model_payload()}, preferences=settings)
    page.wait_for_function('window.modViewer.getEnvironmentPreset().id === "sunset"')
    assert page.locator('#environment-icon [data-environment-icon="sunset"]').is_visible()
    assert page.locator('#environment-btn').get_attribute('title') == 'Environment: Sunset (click to change)'
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
    page.locator('#environment-popover button', has_text='Sunset').click()
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
    page.wait_for_function("""() => Object.keys(Object.assign({}, ...window.__bridge.calls
      .filter(call => call.name === 'preferencesSave').map(call => call.args[0]))).length === 12""")
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
    page.locator('#inspector-tab').click()
    page.locator('.draw-item').click(button='right')
    page.locator('.mesh-context-menu [data-i18n="mesh.separateLooseParts"]').click()
    page.locator('#dialog-backdrop.show').wait_for()
    page.locator('#dialog-ok').click()
    page.wait_for_function('document.querySelectorAll(".draw-item").length === 2')
    rows = page.locator('.draw-item')
    rows.nth(0).click(button='right')
    merge = page.locator('[data-i18n="mesh.mergeLooseParts"]')
    assert merge.is_disabled()
    assert merge.get_attribute('title') == 'Select at least 2 parts from the same mesh'
    page.keyboard.press('Escape')
    rows.nth(0).click()
    rows.nth(1).click(modifiers=['Shift'])
    rows.nth(0).click(button='right')
    assert merge.is_enabled()
    assert merge.get_attribute('title') == ''
    page.keyboard.press('Escape')
    # Check the same component menu as its edit availability changes.
    page.evaluate('window.__editComponent = window.modViewer.activeMeshes[0].userData.componentDescriptor')
    for field, value, reason in [
        ('meshEditWritable', False, 'Mesh changes cannot be written'),
        ('meshEditApplying', True, 'Mesh changes are being applied'),
    ]:
        page.evaluate('([field, value]) => {window.__editComponent[field] = value;}', [field, value])
        page.locator('.group-name').first.click(button='right')
        for key in ('applyMeshChanges', 'cancelMeshChanges'):
            action = page.locator(f'[data-i18n="mesh.{key}"]')
            assert action.is_disabled()
            assert action.get_attribute('title') == reason
        page.keyboard.press('Escape')
        page.evaluate('([field, value]) => {window.__editComponent[field] = value;}', [field, not value])
    page.locator('.group-name').first.click(button='right')
    for key in ('applyMeshChanges', 'cancelMeshChanges'):
        action = page.locator(f'[data-i18n="mesh.{key}"]')
        assert action.is_enabled()
        assert action.get_attribute('title') == ''
    page.keyboard.press('Escape')
    page.keyboard.press('h')
    assert page.evaluate('window.modViewer.activeMeshes[0].userData.looseParts.every(part => !part.visible)')
    assert page.locator('.draw-item.selected').count() == 2
    rows.nth(0).locator('.mesh-state-btn').click()
    assert page.evaluate('window.modViewer.activeMeshes[0].userData.looseParts.every(part => part.visible && part.userData.manuallyToggled)')
    assert page.locator('.inspector-visibility-btn').get_attribute('aria-pressed') == 'true'
    assert all(rows.nth(index).locator('.mesh-state-btn').get_attribute('aria-pressed') == 'true' for index in (0, 1))
    point = project_mesh_points(page, [[0.25, 0.25, 0]])[0]
    page.mouse.click(*point, button='right')
    assert page.locator('[data-i18n="mesh.mergeLooseParts"]').is_visible()
    assert page.locator('.mesh-create-toggle-action').is_hidden()
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


def test_viewport_context_menu_shares_selection_and_create_toggle(viewer):
    payload = model_payload(3)
    for index, mesh in enumerate(payload['meshes'].values()):
        x = index * 2
        mesh['pos'] = append_stream(payload, 'f', [x,0,0, x+1,0,0, x,1,0])
        source = mesh['sources'][0]
        source.update(line=10 + index, occurrence={
            'section': source['section'], 'ordinal': index, 'path': []})
    page = viewer({'fixture-01': payload})
    open_model(page, 'fixture-01')
    wait_loaded(page, 3)
    page.locator('#inspector-tab').click()
    rows = page.locator('.draw-item')
    rows.nth(0).click(button='right')
    menu = page.locator('.mesh-context-menu')
    panel_actions = menu.locator('button:visible').evaluate_all(
        'buttons => buttons.map(button => [button.dataset.i18n, button.disabled])')
    page.keyboard.press('Escape')
    points = project_mesh_points(page, [[0.25, 0.25, 0], [4.25, 0.25, 0]])
    page.mouse.click(*points[0], button='right')
    assert menu.is_visible()
    assert menu.locator('button:visible').evaluate_all(
        'buttons => buttons.map(button => [button.dataset.i18n, button.disabled])') == panel_actions
    assert menu.count() == 1
    page.keyboard.press('Escape')
    rows.nth(0).click()
    rows.nth(1).click(modifiers=['Control'])
    inspector = page.locator('#inspector-content').inner_text()
    page.evaluate("""async () => {
      const {getSelectedMeshes} = await import('./js/scene/selection.js');
      window.__selection = getSelectedMeshes();
    }""")
    page.mouse.click(*points[0], button='right')
    assert page.locator('.draw-item.selected').count() == 2
    assert page.evaluate("""async () => {
      const {getSelectedMeshes} = await import('./js/scene/selection.js');
      return getSelectedMeshes().every((mesh, i) => mesh === window.__selection[i]);
    }""")
    assert page.locator('#inspector-content').is_visible()
    assert page.locator('#inspector-content').inner_text() == inspector
    assert menu.locator('.mesh-create-toggle-action').is_visible()
    menu.locator('.mesh-create-toggle-action').click()
    page.wait_for_function('!document.getElementById("tm-save").disabled')
    assert page.locator('#tm-name').input_value() == 'mesh-00'
    assert page.locator('#tm-var').input_value() == 'mesh_00'
    page.locator('#tm-cancel').click()
    page.mouse.click(*points[1], button='right')
    assert page.locator('.draw-item.selected').count() == 1
    assert 'selected' in rows.nth(2).get_attribute('class')
    assert page.evaluate("""async () => {
      const {getSelectedMeshes} = await import('./js/scene/selection.js');
      return getSelectedMeshes()[0] === window.modViewer.activeMeshes[2];
    }""")
    page.keyboard.press('Escape')
    rect = page.locator('#canvas-container canvas').first.bounding_box()
    page.mouse.click(rect['x'] + 20, rect['y'] + rect['height'] - 20, button='right')
    assert menu.is_hidden()
    assert page.locator('.draw-item.selected').count() == 1
    assert bridge_calls(page, 'toggleAdd') == []
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
        page.mouse.click(*point, button='right')
        apply = page.locator('[data-i18n="mesh.applySelection"]')
        assert apply.is_disabled()
        assert apply.get_attribute('title') == 'Select one or more faces first'
        page.keyboard.press('Escape')
        # Escape closes the context menu and ends face selection; start again.
        page.locator('.draw-item').first.click(button='right')
        page.locator('[data-i18n="mesh.separateBySelection"]').click()
        page.mouse.click(*point)
        page.evaluate("""async () => {
          const {camera, controls} = await import('./js/scene/scene.js');
          window.__view = () => ({position: camera.position.toArray(), target: controls.target.toArray()});
        }""")
        view = page.evaluate('window.__view()')
        page.keyboard.press('f')
        assert page.evaluate('window.__view()') == view
        page.keyboard.press('h')
        assert page.evaluate('window.modViewer.activeMeshes[0].visible')
        page.mouse.click(*point, button='right')
        assert page.locator('[data-i18n="mesh.applySelection"]').is_visible()
        assert apply.is_enabled()
        assert apply.get_attribute('title') == ''
        assert page.locator('[data-i18n="mesh.cancelSelection"]').is_visible()
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


def test_inspector_mesh_tools_preview_history_and_staging(viewer):
    payload = model_payload(3)
    mesh = payload['meshes']['mesh-00']
    mesh.update(pos=append_stream(payload, 'f', [
        0,0,0, 1,0,0, 1,1,0, 0,1,0,
        4,0,0, 5,0,0, 4,1,0, 8,0,0, 9,0,0, 8,1,0]),
        idx=append_stream(payload, 'I', [0,1,2,0,2,3,4,5,6,7,8,9]),
        drawindexed=[12,0,0], identity={'key': 'identity-01', 'component': 'component-00'})
    for index in (1, 2):
        x = 10 + index * 2
        payload['meshes'][f'mesh-{index:02d}']['pos'] = append_stream(
            payload, 'f', [x,0,0,x+1,0,0,x,1,0])
    page = viewer({'fixture-01': payload})
    open_model(page, 'fixture-01')
    wait_loaded(page, 3)
    page.locator('#inspector-tab').click()
    rows = page.locator('.draw-item')
    rows.nth(0).click()
    section = page.locator('.inspector-mesh-edit-section')
    def action(name):
        if name in ('undo', 'redo'):
            return page.locator('.group-hdr').first.locator(f'[data-mesh-edit-action="{name}"]')
        if name == 'isolate':
            rows.nth(0).click(button='right')
            return page.locator('.mesh-isolate-action')
        return page.locator('.inspector-heading').locator(f'[data-mesh-edit-action="{name}"]')
    page.evaluate("""() => {
      window.__sourceGeometry = window.modViewer.activeMeshes[0].geometry;
      window.__sourcePosition = window.__sourceGeometry.attributes.position;
      window.__sourceIndex = window.__sourceGeometry.index;
    }""")
    assert action('undo').is_disabled()
    action('separate').click()
    page.locator('#dialog-ok').click()
    assert rows.count() == 5
    assert 'Parts: 3' in section.inner_text()
    assert 'Preview edits' in section.inner_text()
    assert page.locator('.inspector-heading [data-mesh-edit-action="apply"], .inspector-heading [data-mesh-edit-action="cancel"]').count() == 0
    assert section.locator('[data-mesh-edit-action="undo"], [data-mesh-edit-action="redo"]').count() == 0
    assert section.locator('button').count() == 0
    assert page.locator('.inspector-heading [data-mesh-edit-action="isolate"]').count() == 0
    action('isolate').click()
    assert page.evaluate('window.modViewer.activeMeshes[0].visible')
    assert page.evaluate('window.modViewer.activeMeshes[0].userData.looseParts.map(part => part.visible)') == [True, False, False]
    action('isolate').click()
    assert page.evaluate('window.modViewer.activeMeshes[0].userData.looseParts.map(part => part.visible)') == [True, False, False]
    page.locator('#mesh-list').focus()
    page.keyboard.press('Control+a')
    assert page.locator('.draw-item.selected').count() == 5
    page.mouse.move(2, 2)
    page.locator('#canvas-container > canvas').focus()
    page.keyboard.press('Control+a')
    assert page.locator('.draw-item.selected').count() == 1
    assert rows.nth(0).evaluate('row => row.classList.contains("selected")')
    rows.nth(1).click()
    rows.nth(1).click(button='right')
    page.locator('.mesh-isolate-action').click()
    assert page.evaluate('window.modViewer.activeMeshes[0].userData.looseParts.map(part => part.visible)') == [False, True, False]
    page.locator('#reset-state-btn').click()
    assert page.evaluate('window.modViewer.activeMeshes.every(mesh => mesh.visible)')
    assert page.evaluate('window.modViewer.activeMeshes[0].userData.looseParts.every(part => part.visible)')
    assert all(rows.nth(index).locator('.mesh-state-btn').get_attribute('aria-pressed') == 'true' for index in range(5))
    action('undo').click()
    assert rows.count() == 3
    action('redo').click()
    assert rows.count() == 5
    rows.nth(0).click()
    rows.nth(1).click(modifiers=['Shift'])
    action('merge').click()
    assert rows.count() == 4
    action('undo').click()
    assert rows.count() == 5
    action('redo').click()
    assert rows.count() == 4
    action('undo').click()
    action('undo').click()
    assert rows.count() == 3
    action('separate').click()
    page.locator('#dialog-ok').click()
    page.locator('.group-hdr .group-name').first.click()
    action('cancel').click()
    page.locator('#dialog-ok').click()
    assert rows.count() == 3
    assert action('undo').is_disabled()
    action('separateSelection').click()
    selected = 'async () => (await import("./js/scene/selection.js")).getSelectedFaceTriangles()'
    page.keyboard.press('Control+a')
    assert page.evaluate(selected) == [0, 1, 2, 3]
    assert 'Faces: 4' in section.inner_text()
    assert action('applySelection').is_disabled()
    assert page.locator('.draw-item.selected').count() == 1
    page.keyboard.press('Control+a')
    action('undo').click()
    assert page.evaluate(selected) == []
    action('redo').click()
    assert page.evaluate(selected) == [0, 1, 2, 3]
    action('undo').click()
    point = project_mesh_points(page, [[0.7, 0.3, 0]])[0]
    page.mouse.click(*point)
    assert 'Faces: 1' in section.inner_text()
    action('connected').click()
    assert 'Faces: 2' in section.inner_text()
    action('invert').click()
    assert page.evaluate(selected) == [2, 3]
    action('undo').click()
    assert page.evaluate(selected) == [0, 1]
    action('redo').click()
    assert page.evaluate(selected) == [2, 3]
    page.evaluate('''() => {
      const editor = document.createElement('input');
      editor.id = 'fixture-editor';
      document.body.append(editor);
      editor.focus();
    }''')
    page.keyboard.press('Control+a')
    assert page.evaluate(selected) == [2, 3]
    page.evaluate('document.getElementById("fixture-editor").remove()')
    page.evaluate("document.body.classList.add('feature-edit-mesh-off')")
    page.keyboard.press('Control+a')
    assert page.evaluate(selected) == [2, 3]
    page.evaluate("document.body.classList.remove('feature-edit-mesh-off')")
    section.locator('input[type="checkbox"]').check()
    assert section.locator('input[type="checkbox"]').is_checked()
    action('applySelection').click()
    assert rows.count() == 4
    action('undo').click()
    assert rows.count() == 3
    assert page.evaluate(selected) == [2, 3]
    action('redo').click()
    assert rows.count() == 4
    rows.nth(1).click()
    action('separateSelection').click()
    page.locator('#mesh-list').focus()
    page.keyboard.press('Control+a')
    assert page.evaluate(selected) == [2, 3]
    assert page.locator('.draw-item.selected').count() == 1
    assert action('applySelection').is_disabled()
    action('cancelSelection').click()
    assert page.evaluate("""window.modViewer.activeMeshes[0].geometry === window.__sourceGeometry
      && window.__sourceGeometry.attributes.position === window.__sourcePosition
      && window.__sourceGeometry.index === window.__sourceIndex""")
    assert bridge_calls(page, 'meshApply') == []
    assert page.locator('#export-btn').is_disabled()
    page.locator('.group-hdr .group-name').first.click()
    action('apply').click()
    page.locator('#dialog-ok').click()
    page.wait_for_function('window.__bridge.calls.some(call => call.name === "meshApply")')
    wait_loaded(page, 3)
    section.wait_for()
    assert 'Changes staged' in section.inner_text()
    assert action('undo').is_disabled()
    assert sorted(bridge_calls(page, 'meshApply')[0][1]['mesh']['parts']) == [[0, 1], [2, 3]]
    assert bridge_calls(page, 'export') == []
    assert page.evaluate('window.__bridge.pending["fixture-01"]')


def test_inspector_header_icons_and_isolation_menus_share_selection(viewer):
    payload = model_payload(4)
    label = 'generated mesh label ' * 5
    for index, mesh in enumerate(payload['meshes'].values()):
        x = index * 2
        mesh['pos'] = append_stream(payload, 'f', [x,0,0,x+1,0,0,x,1,0])
    payload['metadata']['mesh_names'][payload['meshes']['mesh-00']['identity']['key']] = label.strip()
    page = viewer({'fixture-01': payload})
    open_model(page, 'fixture-01')
    wait_loaded(page, 4)
    page.locator('#inspector-tab').click()
    rows = page.locator('.draw-item')
    rows.nth(3).locator('.mesh-state-btn').click()
    rows.nth(0).click()
    heading = page.locator('.inspector-heading')
    assert heading.locator('h3').get_attribute('title') == label.strip()
    assert heading.locator('[data-mesh-edit-action="isolate"]').count() == 0
    assert page.locator('.inspector-mesh-edit-section button').count() == 0
    rows.nth(1).click(modifiers=['Control'])
    page.evaluate("""() => {
      document.body.classList.add('feature-edit-mesh-off');
    }""")
    first = project_mesh_points(page, [[0.25,0.25,0]])[0]
    page.mouse.click(*first, button='right')
    isolate = page.locator('.mesh-isolate-action')
    assert isolate.inner_text() == 'Isolate Selected'
    isolate.click()
    assert page.evaluate('window.modViewer.activeMeshes.map(mesh => mesh.visible)') == [True, True, False, False]
    assert page.locator('.draw-item.selected').count() == 2
    rows.nth(1).click(button='right')
    assert isolate.inner_text() == 'Isolate Selected'
    isolate.click()
    assert page.evaluate('window.modViewer.activeMeshes.map(mesh => mesh.visible)') == [True, True, False, False]
    rows.nth(2).click()
    rows.nth(2).click(button='right')
    assert isolate.inner_text() == 'Isolate Selected'
    isolate.click()
    assert page.evaluate('window.modViewer.activeMeshes.map(mesh => mesh.visible)') == [False, False, True, False]
    page.locator('#reset-state-btn').click()
    assert page.evaluate('window.modViewer.activeMeshes.every(mesh => mesh.visible)')
    page.evaluate("document.body.classList.remove('feature-edit-mesh-off')")
    rows.nth(0).click()
    heading.locator('[data-mesh-edit-action="separateSelection"]').click()
    icons = heading.locator('.inspector-edit-btn')
    assert icons.evaluate_all('buttons => buttons.map(button => button.dataset.meshEditAction)') == [
        'applySelection', 'cancelSelection', 'connected', 'invert']
    assert icons.locator('use').evaluate_all('icons => icons.map(icon => icon.getAttribute("href"))') == [
        '#icon-check', '#icon-close', '#icon-faces-connected', '#icon-selection-invert']
    for index in range(icons.count()):
        button = icons.nth(index)
        assert button.inner_text() == ''
        assert button.get_attribute('aria-label')
        assert button.get_attribute('title').startswith(button.get_attribute('aria-label'))
    assert 'Select one or more faces first' in icons.first.get_attribute('title')
    for width in (1280, 960):
        page.set_viewport_size({'width': width, 'height': 900})
        bounds = heading.locator('button').evaluate_all("""buttons => buttons.map(button => {
          const rect = button.getBoundingClientRect();
          return {left:rect.left, right:rect.right, centerY:rect.top + rect.height / 2};
        })""")
        eye = bounds[-1]
        assert all(item['right'] <= eye['left'] and abs(item['centerY'] - eye['centerY']) < 0.5
                   for item in bounds[:-1])
        outer = heading.bounding_box()
        assert bounds[0]['left'] >= outer['x']
        assert eye['right'] <= outer['x'] + outer['width'] + 0.5
    heading.locator('[data-mesh-edit-action="cancelSelection"]').click()
    assert heading.locator('.inspector-edit-btn').count() == 2
    assert bridge_calls(page, 'meshApply') == bridge_calls(page, 'export') == []
    assert page.evaluate('!window.__bridge.pending["fixture-01"]')


def test_component_history_icons_and_shortcuts_respect_editing_context(viewer):
    payload = model_payload(2)
    mesh = payload['meshes']['mesh-00']
    component = 'component-' + 'generated-' * 8
    mesh.update(pos=append_stream(payload, 'f', [0,0,0,1,0,0,0,1,0,4,0,0,5,0,0,4,1,0]),
                idx=append_stream(payload, 'I', [0,1,2,3,4,5]), drawindexed=[6,0,0],
                component=component, identity={'key': 'identity-01', 'component': component})
    page = viewer({'fixture-01': payload})
    open_model(page, 'fixture-01')
    wait_loaded(page, 2)
    page.locator('#inspector-tab').click()
    rows = page.locator('.draw-item')
    rows.nth(0).click()
    page.locator('[data-mesh-edit-action="separate"]').click()
    page.locator('#dialog-ok').click()
    header = page.locator('.group-hdr').first
    undo = header.locator('[data-mesh-edit-action="undo"]')
    redo = header.locator('[data-mesh-edit-action="redo"]')
    assert undo.get_attribute('aria-keyshortcuts') == 'Control+Z'
    assert undo.get_attribute('aria-label') == 'Undo'
    assert redo.get_attribute('title') == 'Redo (Ctrl+Y)'
    assert undo.locator('use').get_attribute('href') == '#icon-undo'
    assert redo.locator('use').get_attribute('href') == '#icon-redo'
    assert page.locator('.group-hdr').nth(1).locator('.mesh-history-actions').is_hidden()
    for width in (1280, 960):
        page.set_viewport_size({'width': width, 'height': 900})
        bounds = [item.bounding_box() for item in (header.locator('.mesh-edit-badge'), undo, redo)]
        assert bounds[0]['x'] + bounds[0]['width'] <= bounds[1]['x']
        assert bounds[1]['x'] + bounds[1]['width'] <= bounds[2]['x']
        outer = header.bounding_box()
        assert bounds[2]['x'] + bounds[2]['width'] <= outer['x'] + outer['width'] + 0.5
    page.keyboard.press('Control+z')
    assert rows.count() == 2
    assert header.locator('.mesh-edit-badge').is_hidden()
    assert redo.is_visible() and redo.is_enabled()
    page.keyboard.press('Control+y')
    assert rows.count() == 3
    undo.click()
    assert rows.count() == 2
    redo.click()
    assert rows.count() == 3
    assert page.locator('.group-hdr.selected').count() == 0
    assert page.locator('.draw-item.selected').count() == 1
    for tag in ('input', 'textarea', 'div'):
        page.evaluate("""tag => {
          const editor = document.createElement(tag);
          editor.id = 'fixture-editor';
          if (tag === 'div') editor.contentEditable = 'true';
          document.body.appendChild(editor); editor.focus();
        }""", tag)
        page.keyboard.type('fixture text')
        page.keyboard.press('Control+z')
        assert rows.count() == 3
        assert page.locator('#fixture-editor').evaluate('node => node.value ?? node.textContent') == ''
        page.evaluate('document.getElementById("fixture-editor").remove()')
    page.locator('.group-hdr .group-name').first.click()
    page.locator('[data-mesh-edit-action="cancel"]').click()
    page.keyboard.press('Control+z')
    assert rows.count() == 3
    page.locator('#dialog-cancel').click()
    page.evaluate("document.body.classList.add('feature-edit-mesh-off')")
    assert undo.is_hidden() and redo.is_hidden()
    page.keyboard.press('Control+z')
    assert rows.count() == 3
    page.evaluate("document.body.classList.remove('feature-edit-mesh-off')")
    for field, value, restored in [('meshEditApplying', True, False), ('meshEditWritable', False, True)]:
        page.evaluate("""([field, value]) => {
          const component = window.modViewer.activeMeshes[0].userData.componentDescriptor;
          component[field] = value; component.syncEditState();
        }""", [field, value])
        assert undo.is_disabled()
        page.keyboard.press('Control+z')
        assert rows.count() == 3
        page.evaluate("""([field, value]) => {
          const component = window.modViewer.activeMeshes[0].userData.componentDescriptor;
          component[field] = value; component.syncEditState();
        }""", [field, restored])
    for interaction in ('transform', 'picking'):
        page.evaluate("""async interaction => {
          const rig = await import('./js/scene/rig-overlay-state.js');
          window.__setRigInteraction = interaction === 'transform'
            ? rig.setRigTransformInteractionActive : rig.setRigJointPickingActive;
          window.__setRigInteraction(true);
        }""", interaction)
        page.keyboard.press('Control+z')
        assert rows.count() == 3
        page.evaluate('window.__setRigInteraction(false)')
    for key in ('Control+Shift+z', 'Control+Alt+z', 'Meta+z'):
        page.keyboard.press(key)
        assert rows.count() == 3
    page.evaluate("""() => {
      window.__blockUndo = event => {if (event.key === 'z') event.preventDefault();};
      document.addEventListener('keydown', window.__blockUndo, true);
    }""")
    page.keyboard.press('Control+z')
    assert rows.count() == 3
    page.evaluate("document.removeEventListener('keydown', window.__blockUndo, true)")
    page.keyboard.press('Control+z')
    assert rows.count() == 2
    page.keyboard.press('Control+y')
    assert rows.count() == 3
    page.keyboard.press('Control+z')
    rows.nth(1).click()
    page.locator('[data-mesh-edit-action="separateSelection"]').click()
    assert redo.is_disabled()
    page.keyboard.press('Control+y')
    assert rows.count() == 2
    page.keyboard.press('Escape')
    assert bridge_calls(page, 'meshApply') == bridge_calls(page, 'export') == []
    assert page.evaluate('!window.__bridge.pending["fixture-01"]')


def test_edit_mesh_feature_off_preserves_visibility_actions(viewer):
    payload = model_payload()
    payload['meshes']['mesh-00']['sources'][0]['occurrence'] = {
        'section': 'TextureOverrideFixture', 'ordinal': 0, 'path': []}
    page = viewer({'fixture-01': payload})
    open_model(page, 'fixture-01')
    wait_loaded(page)
    page.locator('#inspector-tab').click()
    page.locator('.draw-item').click()
    section = page.locator('.inspector-mesh-edit-section')
    assert section.is_visible()
    header_actions = page.locator('.inspector-mesh-edit-actions')
    assert header_actions.is_visible()
    page.locator('.draw-item').click(button='right')
    menu = page.locator('.mesh-context-menu')
    assert menu.locator('[data-i18n="mesh.separateLooseParts"]').is_visible()
    page.evaluate("""() => {
      document.body.classList.add('feature-edit-mesh-off');
    }""")
    assert section.is_hidden()
    assert header_actions.is_hidden()
    assert menu.locator('.mesh-isolate-action').is_visible()
    page.evaluate("""() => {
      const mesh = window.modViewer.activeMeshes[0];
      mesh.userData.componentDescriptor.getMeshEditTools(mesh).run('separateSelection');
    }""")
    assert page.evaluate('async () => !(await import("./js/scene/selection.js")).getFaceSelection()')
    page.evaluate("""() => {
      document.body.classList.remove('feature-edit-mesh-off');
      window.modViewer.activeMeshes[0].userData.componentDescriptor.meshEditWritable = false;
      window.dispatchEvent(new CustomEvent('mod-viewer-mesh-edit-tools-changed'));
    }""")
    assert header_actions.locator('[data-mesh-edit-action="separate"]').is_disabled()
    assert header_actions.locator('[data-mesh-edit-action="separateSelection"]').is_disabled()
    page.evaluate("document.body.classList.add('feature-edit-mesh-off')")
    point = project_mesh_points(page, [[0.25, 0.25, 0]])[0]
    page.mouse.click(*point, button='right')
    assert menu.is_visible()
    assert menu.locator('.mesh-create-toggle-action').is_visible()
    actions = menu.locator('.mesh-edit-context-action')
    assert actions.count() > 0
    assert all(actions.nth(index).is_hidden() for index in range(actions.count()))
    page.evaluate("document.body.classList.add('feature-modify-toggle-off')")
    page.mouse.click(*point, button='right')
    assert menu.is_visible()
    assert menu.locator('button:visible').evaluate_all(
        'buttons => buttons.map(button => button.dataset.i18n)') == ['mesh.rename', 'mesh.isolateSelected']
    assert bridge_calls(page, 'meshApply') == []


def test_mesh_create_toggle_reuses_modal_selection_and_semantic_refresh(viewer):
    payload = model_payload(2)
    for index, mesh in enumerate(payload['meshes'].values()):
        source = mesh['sources'][0]
        source.update(line=10 + index, occurrence={
            'section': source['section'], 'ordinal': index, 'path': []})
    payload['meshes']['mesh-01']['display_name'] = 'Component01-1'
    page = viewer({'fixture-01': payload})
    open_model(page, 'fixture-01')
    wait_loaded(page, 2)
    rows = page.locator('.draw-item')
    action = page.locator('.mesh-create-toggle-action')
    rows.nth(0).click(button='right')
    action.click()
    page.locator('#tm-save').wait_for(state='visible')
    page.wait_for_function('!document.getElementById("tm-save").disabled')
    assert rows.nth(0).locator('.mesh-name').inner_text() == '3, 0, 0'
    assert page.locator('#tm-name').input_value() == 'mesh-00'
    assert page.locator('#tm-var').input_value() == 'mesh_00'
    assert page.locator('#tm-key').input_value() == "no_ctrl no_Shift no_alt '"
    assert page.locator('#tm-back').input_value() == ''
    assert page.locator('#tm-back').is_enabled()
    assert page.locator('#tm-key').is_enabled()
    for field, value in [('ini', 'source-01.ini'), ('values', '0,1'), ('default', '0')]:
        assert page.locator(f'#tm-{field}').input_value() == value
        assert page.locator(f'#tm-{field}').is_disabled()
    page.locator('#tm-cancel').click()

    rows.nth(1).click(modifiers=['Control'])
    rows.nth(1).click(button='right')
    assert page.locator('.draw-item.selected').count() == 2
    action.click()
    page.wait_for_function('!document.getElementById("tm-save").disabled')
    assert page.locator('#tm-name').input_value() == 'Component01-1'
    assert page.locator('#tm-var').input_value() == 'Component01_1'
    page.locator('#tm-save').click()
    page.wait_for_function('window.__bridge.calls.some(call => call.name === "semanticState")')
    request = bridge_calls(page, 'toggleAdd')[0]
    assert request[:7] == ['fixture-01', 'source-01.ini', 'Component01-1',
                          "no_ctrl no_Shift no_alt '", 'Component01_1', ['0', '1'],
                          {'default': '0', 'record_targets': [
                              {'ini': source['ini'], 'line': source['line'],
                               'section': source['section'], 'occurrence': source['occurrence'],
                               'drawindexed': mesh['drawindexed']}
                              for mesh in payload['meshes'].values() for source in mesh['sources']]}]
    assert bridge_calls(page, 'record') == []
    assert len(bridge_calls(page, 'load')) == 1
    assert len(bridge_calls(page, 'semanticState')) == 1
    assert bridge_calls(page, 'export') == []

    if page.locator('#controls-panel').is_hidden():
        page.locator('#controls-tab').click()
    page.locator('#toggle-add-btn').click()
    page.wait_for_function('!document.getElementById("tm-save").disabled')
    for field in ('ini', 'values', 'default'):
        assert page.locator(f'#tm-{field}').is_enabled()
    for field in ('name', 'var', 'key', 'values', 'default'):
        assert page.locator(f'#tm-{field}').input_value() == ''
    assert len(bridge_calls(page, 'toggleKey')) == 2
    page.locator('#tm-cancel').click()
    page.evaluate('window.__bridge.results.toggleKey = {key: ""}')
    rows.nth(0).click(button='right')
    action.click()
    page.wait_for_function('!document.getElementById("tm-save").disabled')
    assert 'No automatic key binding available' in page.locator('#tm-error').inner_text()
    assert page.locator('#tm-key').input_value() == ''
    page.locator('#tm-key').fill('F9')
    page.locator('#tm-cancel').click()


def test_mesh_create_toggle_refuses_incomplete_or_transient_selection(viewer):
    payload = controlled_payload(2)
    for index, mesh in enumerate(payload['meshes'].values()):
        source = mesh['sources'][0]
        source.update(line=10 + index, occurrence={
            'section': source['section'], 'ordinal': index, 'path': []})
    page = viewer({'fixture-01': payload})
    open_model(page, 'fixture-01')
    wait_loaded(page, 2)
    rows = page.locator('.draw-item')
    rows.nth(0).click()
    rows.nth(1).click(modifiers=['Control'])
    page.evaluate('window.__sourceSnapshot = window.modViewer.activeMeshes.map(mesh => structuredClone(mesh.userData.sources))')
    for state in ('mixed', 'merged', 'unapplied', 'loose', 'assetFill', 'missing', 'commandPath', 'clean'):
        page.evaluate("""state => {
          const [first, second] = window.modViewer.activeMeshes;
          for (const [index, mesh] of [first, second].entries()) {
            mesh.userData.sources = structuredClone(window.__sourceSnapshot[index]);
            mesh.userData.componentDescriptor.meshEditState = 'clean';
            mesh.userData.assetFill = false;
            delete mesh.userData.loosePartParent;
          }
          if (state === 'mixed') second.userData.sources[0].ini = 'source-02.ini';
          if (state === 'merged') first.userData.sources.push({...first.userData.sources[0], ini: 'source-02.ini'});
          if (state === 'unapplied') second.userData.componentDescriptor.meshEditState = 'edited';
          if (state === 'assetFill') second.userData.assetFill = true;
          if (state === 'loose') second.userData.loosePartParent = first;
          if (state === 'missing') second.userData.sources = [];
          if (state === 'commandPath') second.userData.sources[0].occurrence.path = [['CommandListFixture', 0]];
        }""", state)
        rows.nth(0).click(button='right')
        assert page.locator('.draw-item.selected').count() == 2
        assert page.locator('.mesh-create-toggle-action').is_visible() == (state == 'clean')
    page.locator('[data-i18n="mesh.separateBySelection"]').click()
    rows.nth(0).click(button='right')
    assert page.locator('.mesh-create-toggle-action').is_hidden()
    page.locator('[data-i18n="mesh.cancelSelection"]').click()
    if page.locator('#controls-panel').is_hidden():
        page.locator('#controls-tab').click()
    page.locator('#toggle-list [aria-label="Record toggle mesh visibility"]').click()
    page.locator('.toggle-row.recording').wait_for()
    rows.nth(0).click(button='right')
    assert page.locator('.mesh-context-menu').is_hidden()
    page.locator('.toggle-record-cancel').click()
    assert bridge_calls(page, 'toggleAdd') == []
    assert bridge_calls(page, 'record') == []

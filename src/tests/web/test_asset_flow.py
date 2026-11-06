"""Explicit Asset viewing remains read-only through the UI."""

import pytest

from .payloads import model_payload, textured_payload
from .support import bridge_calls, open_model, wait_loaded


@pytest.mark.parametrize('asset_type,game,upright', [
    ('WWMI', 'wuwa', True), ('ZZMI', 'zzz', True), ('GIMI', 'genshin', False),
])
def test_asset_game_metadata_preserves_upright_orientation_across_reload_and_reset(viewer, asset_type, game, upright):
    asset = model_payload()
    asset['metadata'].update(source_kind='asset', asset={'type': asset_type},
                             game={'id': game, 'runtime': 'asset',
                                   'texture_api': asset_type.lower(), 'confidence': 'authoritative'})
    entry = {'asset': True, 'asset_type': asset_type}
    mod = model_payload()
    mod['metadata']['game'] = {'id': game}
    page = viewer({'fixture-01': mod, 'fixture-02': asset})
    open_model(page, 'fixture-01')
    wait_loaded(page)
    baseline = page.evaluate('window.modViewer.activeMeshes[0].quaternion.toArray()')
    assert page.evaluate("entry => window.modViewer.switchAsset('fixture-02', entry)", entry)
    wait_loaded(page)
    assert page.evaluate('window.modViewer.activeMeshes[0].quaternion.toArray()') == pytest.approx(baseline)
    assert page.evaluate('''async upright => {
      const THREE = await import('three');
      const mesh = window.modViewer.activeMeshes[0];
      const up = new THREE.Vector3(...(upright ? [0, 0, 1] : [0, 1, 0])).applyQuaternion(mesh.quaternion);
      return up.distanceTo(new THREE.Vector3(0, 1, 0)) < 1e-6;
    }''', upright)
    page.locator('#camera-flip-horizontal-btn').click()
    rotated = page.evaluate('window.modViewer.activeMeshes[0].quaternion.toArray()')
    assert rotated != pytest.approx(baseline)
    assert page.evaluate("entry => window.modViewer.switchAsset('fixture-02', entry)", entry)
    wait_loaded(page)
    assert page.evaluate('window.modViewer.activeMeshes[0].quaternion.toArray()') == pytest.approx(rotated)
    page.locator('#camera-reset-view-btn').click()
    assert page.evaluate('window.modViewer.activeMeshes[0].quaternion.toArray()') == pytest.approx(baseline)
    assert bridge_calls(page, 'asset') == [['fixture-02'], ['fixture-02']]
    assert bridge_calls(page, 'export') == []


def test_asset_transition_removes_source_edit_actions(viewer):
    asset = model_payload()
    source = asset['meshes']['mesh-00']['sources'][0]
    source['occurrence'] = {'section': source['section'], 'ordinal': 0, 'path': []}
    asset['metadata'].update(source_kind='asset', source_read_only=True)
    page = viewer({'fixture-01': model_payload(), 'fixture-02': asset})
    open_model(page, 'fixture-01')
    wait_loaded(page)
    page.evaluate("window.modViewer.switchAsset('fixture-02', {asset: 'asset-01'})")
    page.wait_for_function("window.modViewer.getCurrentSource()?.kind === 'asset'")
    wait_loaded(page)
    assert bridge_calls(page, 'asset') == [['fixture-02']]
    assert page.locator('#export-btn').is_disabled()
    assert page.locator('#ini-view-btn').is_disabled()
    page.locator('#inspector-tab').click()
    page.locator('.draw-item').click()
    assert page.locator('.inspector-material-kind-control').is_disabled()
    page.locator('.draw-item').click(button='right')
    assert page.locator('.mesh-create-toggle-action').is_hidden()
    assert page.evaluate('window.modViewer.activeMeshes[0].userData.componentDescriptor.meshEditWritable') is False
    assert bridge_calls(page, 'export') == []
    assert bridge_calls(page, 'names') == []
    assert bridge_calls(page, 'color') == []


def test_archive_preview_stages_toggle_but_cannot_export_or_persist_metadata(viewer):
    payload = textured_payload(extension='dds')
    source = payload['meshes']['mesh-00']['sources'][0]
    source['occurrence'] = {'section': source['section'], 'ordinal': 0, 'path': []}
    payload['metadata'].update(source_kind='archive', source_read_only=True)
    page = viewer({'fixture-01.zip': payload})
    open_model(page, 'fixture-01.zip')
    wait_loaded(page)
    assert page.evaluate('window.modViewer.getCurrentSource().readOnly')
    assert page.locator('#export-btn').is_disabled()
    page.locator('#inspector-tab').click()
    page.locator('.draw-item').click()
    page.evaluate("document.body.classList.add('feature-edit-mesh-off')")
    page.locator('.inspector-uv-section summary').click()
    page.wait_for_function("document.querySelector('.inspector-uv-texture').dataset.previewReady === 'true'")
    assert page.locator('.inspector-uv-outlines').evaluate('canvas => canvas.width > 1')
    page.evaluate("document.body.classList.remove('feature-edit-mesh-off')")
    result = page.evaluate("""async () => {
      const {setMeshColorAdjustment} = await import('./js/mesh/mesh-color-session.js');
      const {saveTextureState} = await import('./js/mesh/mesh-texture-state.js');
      const {canSaveTexture} = await import('./js/mesh/texture-save-session.js');
      const mesh = window.modViewer.activeMeshes[0];
      setMeshColorAdjustment(mesh, {brightness: 0.5}, {persist: true});
      saveTextureState('fixture-01.zip');
      return {reason: canSaveTexture(mesh).reason, writable: mesh.userData.componentDescriptor.meshEditWritable};
    }""")
    assert result == {'reason': 'compressed-mod', 'writable': False}
    assert bridge_calls(page, 'textures') == []
    assert bridge_calls(page, 'export') == []
    assert bridge_calls(page, 'names') == []
    assert bridge_calls(page, 'color') == []
    page.locator('.draw-item').click(button='right')
    action = page.locator('.mesh-create-toggle-action')
    assert action.is_visible()
    action.click()
    page.locator('#tm-save').click()
    page.wait_for_function('window.__bridge.calls.some(call => call.name === "semanticState")')
    request = bridge_calls(page, 'toggleAdd')[0]
    assert request[0] == 'fixture-01.zip'
    assert request[-1] == {'default': '0', 'record_targets': [{
        **source, 'drawindexed': payload['meshes']['mesh-00']['drawindexed']}]}
    assert page.evaluate('window.__bridge.pending["fixture-01.zip"]')
    assert page.locator('#export-btn').is_disabled()
    assert len(bridge_calls(page, 'load')) == 1
    assert bridge_calls(page, 'export') == []


def test_dirty_source_to_asset_requires_confirmation_and_preserves_cancelled_edits(viewer):
    page = viewer({'fixture-01': model_payload(), 'fixture-02': model_payload(2)},
                  pending={'fixture-01': True})
    open_model(page, 'fixture-01')
    wait_loaded(page)
    page.evaluate("window.__switch = window.modViewer.switchAsset('fixture-02', {asset: 'asset-01'}); void 0")
    page.locator('#dialog-backdrop.show').wait_for()
    page.locator('#dialog-cancel').click()
    assert page.evaluate('window.__switch') is False
    assert bridge_calls(page, 'asset') == []
    assert page.evaluate('window.__bridge.pending["fixture-01"]')
    page.evaluate("window.__switch = window.modViewer.switchAsset('fixture-02', {asset: 'asset-01'}); void 0")
    page.locator('#dialog-backdrop.show').wait_for()
    page.locator('#dialog-ok').click()
    assert page.evaluate('window.__switch')
    wait_loaded(page, 2)
    assert bridge_calls(page, 'discard') == [['fixture-01']]
    assert bridge_calls(page, 'asset') == [['fixture-02']]


def test_asset_load_failure_clears_old_preview_and_folder_load_recovers(viewer):
    page = viewer({'fixture-01': model_payload(), 'fixture-02': {'error': 'fixture failure'}})
    open_model(page, 'fixture-01')
    wait_loaded(page)
    page.evaluate("window.__switch = window.modViewer.switchAsset('fixture-02', {asset: 'asset-01'}); void 0")
    page.locator('#dialog-backdrop.show').wait_for()
    assert page.locator('.draw-item').count() == 0
    assert page.evaluate('window.modViewer.activeMeshes.length') == 0
    assert page.locator('#export-btn').is_disabled()
    page.locator('#dialog-ok').click()
    assert page.evaluate('window.__switch') is False
    open_model(page, 'fixture-01')
    wait_loaded(page)
    assert page.evaluate('window.modViewer.getCurrentSource().kind') == 'mod'


def test_missing_asset_parts_are_explicit_transient_and_removed_on_reload(viewer):
    base = model_payload()
    base['asset_resolution'] = {'configured_roots': 1}
    fill = model_payload()
    fill['meshes'] = {'fill-00': fill['meshes'].pop('mesh-00')}
    fill['meshes']['fill-00']['component'] = 'component-01'
    fill['meshes']['fill-00']['asset_fill'] = True
    page = viewer({'fixture-01': base, 'fixture-fill': fill})
    open_model(page, 'fixture-01')
    wait_loaded(page)
    assert bridge_calls(page, 'fill') == []
    page.locator('#asset-fill-btn').click()
    page.wait_for_function('window.modViewer.activeMeshes.length === 2 && document.querySelector("#asset-fill-btn").dataset.state === "remove"')
    assert bridge_calls(page, 'fill') == [['fixture-01']]
    assert page.evaluate('window.modViewer.activeMeshes.filter(mesh => mesh.userData.assetFill).length') == 1
    page.locator('#asset-fill-btn').click()
    page.wait_for_function('window.modViewer.activeMeshes.length === 1 && document.querySelector("#asset-fill-btn").dataset.state === "load"')
    page.locator('#asset-fill-btn').click()
    page.wait_for_function('window.modViewer.activeMeshes.length === 2')
    assert page.evaluate('window.modViewer.reloadCurrentMod()')
    wait_loaded(page)
    assert page.evaluate('window.modViewer.activeMeshes.every(mesh => !mesh.userData.assetFill)')
    assert bridge_calls(page, 'export') == []


def test_settings_auto_restore_lifecycle_is_persistent_and_preview_only(viewer):
    base = model_payload()
    base['asset_resolution'] = {'configured_roots': 1}
    fill = model_payload()
    fill['meshes'] = {'fill-00': fill['meshes'].pop('mesh-00')}
    fill['meshes']['fill-00'].update(component='component-01', asset_fill=True)
    asset = model_payload()
    asset['metadata']['source_kind'] = 'asset'
    page = viewer({'fixture-01': base, 'fixture-fill': fill,
                   'fixture-02': asset, 'fixture-03': model_payload()})
    open_model(page, 'fixture-01')
    wait_loaded(page)
    assert bridge_calls(page, 'fill') == bridge_calls(page, 'preferencesSave') == []
    page.locator('#environment-btn').click()
    page.locator('#settings-btn').click()
    assert not page.locator('#environment-popover').is_visible()
    assert page.locator('#settings-dialog').get_attribute('aria-modal') == 'true'
    checkbox = page.locator('#settings-auto-restore')
    assert checkbox.evaluate('(element) => element === document.activeElement')
    assert not checkbox.is_checked()
    page.keyboard.press('Shift+Tab')
    assert page.locator('#app-language').evaluate('(element) => element === document.activeElement')
    page.keyboard.press('Shift+Tab')
    assert page.locator('#panel-opacity').evaluate('(element) => element === document.activeElement')
    page.keyboard.press('Shift+Tab')
    assert page.locator('#settings-close').evaluate('(element) => element === document.activeElement')
    page.keyboard.press('Shift+Tab')
    assert page.locator('#settings-done').evaluate('(element) => element === document.activeElement')
    checkbox.check()
    page.wait_for_function('window.__bridge.calls.some(call => call.name === "preferencesSave")')
    assert bridge_calls(page, 'preferencesSave') == [[{'autoRestoreMissingAssetParts': True}]]
    assert bridge_calls(page, 'fill') == []
    page.keyboard.press('Escape')
    assert not page.locator('#settings-backdrop').is_visible()
    assert page.locator('#settings-btn').evaluate('(element) => element === document.activeElement')
    assert page.evaluate('window.modViewer.reloadCurrentMod()')
    wait_loaded(page, 2)
    assert bridge_calls(page, 'fill') == [['fixture-01']]
    page.locator('#asset-fill-btn').click()
    page.wait_for_function('window.modViewer.activeMeshes.length === 1')
    assert bridge_calls(page, 'fill') == [['fixture-01']]
    assert page.evaluate('window.modViewer.reloadCurrentMod()')
    wait_loaded(page, 2)
    assert bridge_calls(page, 'fill') == [['fixture-01'], ['fixture-01']]
    assert page.evaluate('window.modViewer.activeMeshes.filter(mesh => mesh.userData.assetFill).length') == 1
    assert page.locator('#export-btn').is_disabled()
    assert not page.evaluate('window.__bridge.pending["fixture-01"] || false')
    assert bridge_calls(page, 'export') == []

    page.reload()
    page.wait_for_function('window.modViewer !== undefined')
    page.locator('#settings-btn').click()
    assert checkbox.is_checked()
    page.locator('#settings-close').click()
    open_model(page, 'fixture-01')
    wait_loaded(page, 2)
    assert bridge_calls(page, 'fill') == [['fixture-01']]
    assert page.evaluate("window.modViewer.switchAsset('fixture-02', {asset: true})")
    wait_loaded(page)
    open_model(page, 'fixture-03')
    wait_loaded(page)
    assert bridge_calls(page, 'fill') == [['fixture-01']]
    page.locator('#settings-btn').click()
    checkbox.uncheck()
    page.locator('#settings-done').click()
    open_model(page, 'fixture-01')
    wait_loaded(page)
    assert bridge_calls(page, 'fill') == [['fixture-01']]
    page.reload()
    page.wait_for_function('window.modViewer !== undefined')
    page.locator('#settings-btn').click()
    assert not checkbox.is_checked()
    page.locator('#settings-backdrop').click(position={'x': 5, 'y': 5})
    assert not page.locator('#settings-backdrop').is_visible()
    assert bridge_calls(page, 'preferencesSave') == []


def test_slow_auto_restore_allows_mod_switch_and_rolls_back_stale_result(viewer):
    base = model_payload()
    base['asset_resolution'] = {'configured_roots': 1}
    fill = model_payload()
    fill['meshes']['mesh-00']['asset_fill'] = True
    page = viewer({'fixture-01': base, 'fixture-02': model_payload(2), 'fixture-fill': fill},
                  preferences={'autoRestoreMissingAssetParts': True})
    page.evaluate('window.__bridge.blocked.fill = true')
    open_model(page, 'fixture-01')
    page.wait_for_function('window.__bridge.waiters.fill?.length === 1')
    page.wait_for_function('!document.querySelector("#open-btn").disabled', timeout=3000)
    assert page.locator('#asset-fill-btn').is_disabled()

    open_model(page, 'fixture-02')
    wait_loaded(page, 2)
    snapshot = """async () => {
      const {viewerState} = await import('./js/app/state.js');
      return {source: viewerState.currentSource, fill: {
        epoch: viewerState.assetFill.epoch, loading: viewerState.assetFill.loading,
        loaded: viewerState.assetFill.loaded, fillId: viewerState.assetFill.fillId,
      }, meshes: window.modViewer.activeMeshes.map(mesh => ({
        id: mesh.uuid, visible: mesh.visible,
        positions: [...mesh.geometry.attributes.position.array],
      }))};
    }"""
    new_scene = page.evaluate(snapshot)
    assert new_scene['source']['path'] == 'fixture-02'
    assert bridge_calls(page, 'fill') == [['fixture-01']]
    assert page.evaluate('window.__bridge.waiters.fill.length') == 1

    page.evaluate('window.__bridge.release("fill")')
    page.wait_for_function('window.__bridge.calls.some(call => call.name === "fillRemove")')
    assert bridge_calls(page, 'fillRemove') == [['fixture-01', 'fill-01']]
    assert page.evaluate(snapshot) == new_scene
    assert page.evaluate('window.modViewer.activeMeshes.every(mesh => !mesh.userData.assetFill)')
    assert not page.locator('#dialog-backdrop').is_visible()
    assert bridge_calls(page, 'export') == []


@pytest.mark.parametrize('response', [
    {'status': 'nothing_missing'}, {'status': 'asset_ambiguous'},
    {'status': 'asset_not_found'}, {'status': 'error', 'error': 'fixture failure'},
    {'status': 'unknown'}, None,
])
def test_auto_restore_responses_are_silent_but_manual_action_explains(viewer, response):
    base = model_payload()
    base['asset_resolution'] = {'configured_roots': 1}
    page = viewer({'fixture-01': base}, preferences={'autoRestoreMissingAssetParts': True},
                  native={'fill': response}, startup={'path': 'fixture-01'})
    wait_loaded(page)
    assert bridge_calls(page, 'fill') == [['fixture-01']]
    assert not page.locator('#dialog-backdrop').is_visible()
    page.locator('#asset-fill-btn').click()
    page.locator('#dialog-backdrop.show').wait_for()
    assert page.locator('#dialog-message').inner_text()
    page.locator('#dialog-ok').click()
    assert bridge_calls(page, 'fill') == [['fixture-01'], ['fixture-01']]
    assert bridge_calls(page, 'export') == []


@pytest.mark.parametrize('automatic', [False, True])
def test_failed_asset_fill_rolls_back_frontend_and_backend_transaction(viewer, automatic):
    base = model_payload()
    base['asset_resolution'] = {'configured_roots': 1}
    fill = model_payload()
    fill['_fixture_missing_geometry'] = True
    page = viewer({'fixture-01': base, 'fixture-fill': fill},
                  preferences={'autoRestoreMissingAssetParts': automatic})
    if automatic:
        page.evaluate('window.__bridge.blocked.fill = true')
    open_model(page, 'fixture-01')
    if automatic:
        page.wait_for_function('window.__bridge.waiters.fill?.length > 0')
        page.locator('#settings-btn').click()
        page.evaluate('window.__bridge.release("fill")')
    else:
        wait_loaded(page)
        page.locator('#asset-fill-btn').click()
    if automatic:
        wait_loaded(page)
        page.wait_for_function('!document.querySelector("#asset-fill-btn").disabled')
        assert not page.locator('#dialog-backdrop').is_visible()
    else:
        page.locator('#dialog-backdrop.show').wait_for()
    assert page.evaluate('window.modViewer.activeMeshes.length') == 1
    assert bridge_calls(page, 'fillRemove') == [['fixture-01', 'fill-01']]
    if automatic:
        assert page.locator('#settings-backdrop').is_visible()
        page.locator('#settings-close').click()
    else:
        page.locator('#dialog-ok').click()
    page.wait_for_function('!document.querySelector("#asset-fill-btn").disabled')
    assert page.locator('#asset-fill-btn').get_attribute('data-state') == 'load'

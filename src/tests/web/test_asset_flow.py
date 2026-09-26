"""Explicit Asset viewing remains read-only through the UI."""

from .payloads import model_payload, textured_payload
from .support import bridge_calls, open_model, wait_loaded


def test_asset_transition_removes_source_edit_actions(viewer):
    asset = model_payload()
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
    assert page.evaluate('window.modViewer.activeMeshes[0].userData.componentDescriptor.meshEditWritable') is False
    assert bridge_calls(page, 'export') == []
    assert bridge_calls(page, 'names') == []
    assert bridge_calls(page, 'color') == []


def test_archive_preview_cannot_export_or_persist_metadata(viewer):
    payload = textured_payload(extension='dds')
    payload['metadata'].update(source_kind='archive', source_read_only=True)
    page = viewer({'fixture-01.zip': payload})
    open_model(page, 'fixture-01.zip')
    wait_loaded(page)
    assert page.evaluate('window.modViewer.getCurrentSource().readOnly')
    assert page.locator('#export-btn').is_disabled()
    page.locator('#inspector-tab').click()
    page.locator('.draw-item').click()
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


def test_failed_asset_fill_rolls_back_frontend_and_backend_transaction(viewer):
    base = model_payload()
    base['asset_resolution'] = {'configured_roots': 1}
    fill = model_payload()
    fill['_fixture_missing_geometry'] = True
    page = viewer({'fixture-01': base, 'fixture-fill': fill})
    open_model(page, 'fixture-01')
    wait_loaded(page)
    page.locator('#asset-fill-btn').click()
    page.locator('#dialog-backdrop.show').wait_for()
    assert page.evaluate('window.modViewer.activeMeshes.length') == 1
    assert bridge_calls(page, 'fillRemove') == [['fixture-01', 'fill-01']]
    page.locator('#dialog-ok').click()
    page.wait_for_function('!document.querySelector("#asset-fill-btn").disabled')
    assert page.locator('#asset-fill-btn').get_attribute('data-state') == 'load'

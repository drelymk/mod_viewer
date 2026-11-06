"""Asset texture Export is explicit, source-scoped and safe across async saves."""

from .payloads import model_payload, solid_texture, textured_payload
from .support import bridge_calls, wait_loaded


def test_asset_texture_export_failure_retry_pending_edits_and_stale_completion(module_page):
    module_page.route('**/js/ui/dialogs.js', lambda route: route.fulfill(
        content_type='text/javascript', body='export const alertDialog = async message => window.__alerts.push(message);'))
    result = module_page.evaluate("""async () => {
      const {viewerState} = await import('./js/app/state.js');
      const {initializeAssetTextureExport:initialize,assetTextureExportState:state,exportAssetTextures:save,
        recordAssetTextureAssignment:assign}
        = await import('./js/app/asset-texture-export.js');
      window.__alerts = [];
      const writes = [];
      let response = {error:'synthetic failure'}, release;
      window.pywebview = {api:{export_asset_textures:async (...args) => {
        writes.push(args); return response === null ? new Promise(resolve => release=resolve) : response;
      }}};
      viewerState.currentSource = {kind:'asset',path:'asset-01'};
      initialize('session-01');
      const initial = state();
      assign('component-01','diffuse','diffuse::asset/root/a.png','diffuse::asset/root/a.png');
      const reselected = state();
      assign('component-01','diffuse','diffuse::asset/root/b.png','diffuse::asset/root/a.png');
      assign('component-01','diffuse','diffuse::asset/root/a.png');
      const reverted = state();
      assign('component-01','diffuse','diffuse::asset/root/b.png');
      await save();
      const failed = state();
      response = null;
      const pending = save();
      await Promise.resolve();
      const busy = state();
      await save();
      assign('component-01','diffuse','diffuse::asset/root/a.png');
      release({saved:2}); await pending;
      const newer = state();
      assign('component-01','diffuse','diffuse::asset/root/c.png');
      assign('component-01','normal_map',null);
      response = {saved:2}; await save();
      const saved = state();
      assign('component-01','light_map',null);
      response = null;
      const oldSave = save(); await Promise.resolve();
      viewerState.currentSource = {kind:'asset',path:'asset-02'};
      initialize('session-02');
      assign('component-02','diffuse','diffuse::asset/root/d.png');
      release({saved:2}); await oldSave;
      const switched = state();
      const count = writes.length;
      for (const source of [{kind:'mod',readOnly:false},{kind:'mod',readOnly:true}]) {
        viewerState.currentSource=source; initialize(); assign('component-01','diffuse',null); await save();
      }
      return {initial,reselected,reverted,failed,busy,newer,saved,switched,writes,alerts:window.__alerts,
        modBlocked:count===writes.length,final:state()};
    }""")
    assert result['initial'] == {'pending': False, 'saving': False, 'available': False}
    assert result['reselected'] == result['reverted'] == result['initial']
    assert result['failed'] == {'pending': True, 'saving': False, 'available': True}
    assert result['busy'] == {'pending': True, 'saving': True, 'available': False}
    assert result['newer'] == result['switched'] == result['failed']
    assert result['saved'] == result['initial'] == result['final']
    assert result['modBlocked'] and len(result['writes']) == 4
    assert len(result['alerts']) == 1 and 'synthetic failure' in result['alerts'][0]
    first = result['writes'][0]
    assert first[:2] == ['asset-01', 'session-01']
    assert first[2] == {'component-01': {'diffuse': 'diffuse::asset/root/b.png'}}
    assert result['writes'][2][2] == {'component-01': {'diffuse': 'diffuse::asset/root/c.png', 'normal_map': None}}
    assert result['writes'][-1][2] == {'component-01': {'light_map': None}}


def test_open_asset_export_button_dropdown_overflow_and_switch_guard(viewer):
    payload = textured_payload(3)
    first, second, _other = payload['meshes'].values()
    first['asset_component_key'] = second['asset_component_key'] = 'mesh-00'
    second['component'] = second['identity']['component'] = first['component']
    second['tex_key'] = 'diffuse::texture-02.png'
    for index in (1, 2):
        payload['textures'][f'light_map::texture-{index:02d}.png'] = payload['textures'][f'diffuse::texture-{index:02d}.png']
    first['light_map_key'] = 'light_map::texture-01.png'
    payload['texture_pools']['pool-01'][0]['light_map'] = first['light_map_key']
    payload['metadata'].update(source_kind='asset', asset={'texture_session':'session-01'})
    page = viewer({'asset-01':payload,'asset-02':payload})
    assert page.evaluate("window.modViewer.switchAsset('asset-01',{asset:true})")
    wait_loaded(page, 3)
    assert page.locator('#export-control').is_visible()
    assert page.locator('#export-btn').is_disabled()
    page.evaluate("""() => {
      const mesh = window.modViewer.activeMeshes[0];
      window.__originalTexture = mesh.userData.texKey;
      mesh.userData.componentDescriptor.setTextureOverride(mesh,null);
    }""")
    assert page.locator('#export-btn').is_enabled()
    assert page.locator('#pending-indicator').is_visible()
    page.evaluate('window.__bridge.blocked.assetTextures = true')
    page.locator('#export-btn').click()
    page.wait_for_function('window.__bridge.waiters.assetTextures?.length === 1')
    assert page.evaluate("""async () => {
      const viewer=window.modViewer;
      return [await viewer.switchAsset('asset-02',{asset:true}),
        await viewer.switchMod('mod-01'), await viewer.openMod()];
    }""") == [False, False, False]
    assert bridge_calls(page, 'asset') == [['asset-01']]
    assert bridge_calls(page, 'load') == bridge_calls(page, 'pick') == []
    assert page.locator('#dialog-backdrop.show').count() == 0
    page.evaluate('window.__bridge.release("assetTextures")')
    page.wait_for_function('!document.querySelector("#pending-indicator").classList.contains("show")')
    assert bridge_calls(page,'assetTextures')[0][0:2] == ['asset-01','session-01']
    assert bridge_calls(page,'assetTextures')[0][2]['mesh-00']['diffuse'] is None
    assert page.locator('#pending-indicator').is_hidden()
    page.evaluate("""() => {
      const mesh=window.modViewer.activeMeshes[0];
      mesh.userData.componentDescriptor.setTextureOverride(mesh,undefined);
    }""")
    assert page.evaluate("""() => {
      const [mesh,sibling,other]=window.modViewer.activeMeshes;
      const data=mesh.userData;
      return data.texKey === null && data.defaultTexKey === null && data.resolvedTexKey === null &&
        sibling.userData.texKey === null && other.userData.texKey === 'diffuse::texture-01.png';
    }""")
    assert page.locator('#export-btn').is_disabled()
    page.evaluate("""() => {
      const mesh=window.modViewer.activeMeshes[0];
      mesh.userData.componentDescriptor.setTextureAssignment('light_map','light_map::texture-02.png');
      mesh.userData.componentDescriptor.setTextureOverride(mesh,window.__originalTexture);
    }""")
    page.locator('#export-menu-btn').click()
    assert page.locator('#export-edits-choice').is_hidden()
    assert page.locator('#export-glb-choice').is_disabled()
    page.locator('#export-asset-textures-choice').click()
    page.wait_for_function('window.__bridge.calls.filter(call=>call.name==="assetTextures").length===2')
    assert page.locator('#export-menu').is_hidden()
    page.wait_for_function('document.querySelector("#export-btn").disabled')
    assert bridge_calls(page,'assetTextures')[1][2] == {'mesh-00': {
        'diffuse': 'diffuse::texture-02.png', 'light_map': 'light_map::texture-02.png'}}
    assert page.evaluate("""() => {
      const mesh=window.modViewer.activeMeshes[0];
      mesh.userData.componentDescriptor.setTextureOverride(mesh,undefined);
      return mesh.userData.texKey === window.__originalTexture &&
        mesh.userData.defaultLightMapKey === 'light_map::texture-02.png' &&
        mesh.userData.resolvedLightMapKey === mesh.userData.lightMapKey;
    }""")
    assert page.locator('#export-btn').is_disabled()
    page.evaluate("""() => {
      const mesh=window.modViewer.activeMeshes[0];
      mesh.userData.componentDescriptor.setTextureOverride(mesh,null);
      window.__switch=window.modViewer.switchAsset('asset-02',{asset:true});
    }""")
    page.locator('#dialog-backdrop.show').wait_for()
    page.locator('#dialog-cancel').click()
    assert page.evaluate('window.__switch') is False
    page.set_viewport_size({'width':640,'height':800})
    page.locator('#toolbar-more').click()
    page.locator('#toolbar-overflow [data-toolbar-target="export-btn"]').click()
    page.wait_for_function('window.__bridge.calls.filter(call=>call.name==="assetTextures").length===3')
    assert bridge_calls(page,'export') == [] and bridge_calls(page,'textures') == []


def test_asset_component_assignments_replace_preview_save_and_clear(viewer):
    payload = textured_payload(3)
    first, follower, other = payload['meshes'].values()
    follower['component'] = follower['identity']['component'] = first['component']
    other['component'] = other['identity']['component'] = first['component']
    first['asset_component_key'] = follower['asset_component_key'] = 'component-01'
    other['asset_component_key'] = 'component-02'
    payload['textures']['light_map::texture-02.png'] = payload['textures']['diffuse::texture-02.png']
    payload['metadata'].update(source_kind='asset', asset={'texture_session':'session-01'})
    page = viewer({'asset-01': payload})
    assert page.evaluate("window.modViewer.switchAsset('asset-01',{asset:true})")
    wait_loaded(page, 3)
    assert page.evaluate("""() => {
      const [first,second,other]=window.modViewer.activeMeshes;
      const component=first.userData.componentDescriptor;
      component.setTextureOverride(first,'diffuse::texture-01.png');
      second.userData.componentDescriptor.setTextureOverride(second,'diffuse::texture-02.png');
      return component === second.userData.componentDescriptor &&
        first.userData.texKey === 'diffuse::texture-02.png' && second.userData.texKey === first.userData.texKey &&
        other.userData.texKey === 'diffuse::texture-01.png';
    }""")
    page.evaluate("""uri => {
      window.pywebview.api.pick_asset_texture_file=async () => ({role:'light_map',
        tex_key:'light_map::texture-02.png',uri,file:'texture-02.png'});
      window.modViewer.activeMeshes[0].userData.componentDescriptor.openTextureManager();
    }""", payload['textures']['light_map::texture-02.png'])
    page.locator('.texm-map-cell').first.click()
    page.wait_for_function("window.modViewer.activeMeshes[0].userData.lightMapKey === 'light_map::texture-02.png'")
    page.locator('#texm-close').click()
    page.locator('#export-btn').click()
    page.wait_for_function('!document.querySelector("#pending-indicator").classList.contains("show")')
    assert bridge_calls(page, 'assetTextures')[0][2] == {'component-01': {
        'diffuse': 'diffuse::texture-02.png', 'light_map': 'light_map::texture-02.png'}}
    assert page.evaluate("""() => {
      const [owner,follower,other]=window.modViewer.activeMeshes;
      owner.userData.componentDescriptor.setTextureOverride(owner,undefined);
      owner.userData.componentDescriptor.setTextureOverride(owner,'diffuse::texture-02.png');
      owner.userData.componentDescriptor.setTextureOverride(owner,'diffuse::texture-01.png');
      owner.userData.componentDescriptor.setTextureOverride(owner,'diffuse::texture-02.png');
      return owner.userData.texKey === 'diffuse::texture-02.png' &&
        follower.userData.texKey === owner.userData.texKey &&
        follower.userData.lightMapKey === 'light_map::texture-02.png' && !other.userData.lightMapKey;
    }""")
    assert page.locator('#export-btn').is_disabled()
    page.evaluate("""() => {
      const mesh=window.modViewer.activeMeshes[1];
      mesh.userData.componentDescriptor.setTextureOverride(mesh,null);
    }""")
    assert page.evaluate("window.modViewer.activeMeshes.slice(0,2).every(mesh=>mesh.userData.texKey===null && mesh.userData.lightMapKey==='light_map::texture-02.png')")
    page.locator('#export-btn').click()
    page.wait_for_function('!document.querySelector("#pending-indicator").classList.contains("show")')
    assert bridge_calls(page, 'assetTextures')[1][2] == {'component-01': {'diffuse': None}}


def test_asset_without_diffuse_candidates_can_edit_maps_and_select_a_picked_image(viewer):
    payload = model_payload(2)
    normal = 'normal_map::normal-01.png'
    payload['textures'][normal] = solid_texture((128, 128, 255))
    payload['texture_pools'] = {'pool-empty': []}
    for mesh in payload['meshes'].values():
        mesh.update(component='component-01', asset_component_key='component-01',
                    texture_pool_id='pool-empty', normal_map_key=normal)
        mesh['identity']['component'] = 'component-01'
    payload['metadata'].update(source_kind='asset', asset={'texture_session':'session-01'})
    page = viewer({'asset-01': payload})
    assert page.evaluate("window.modViewer.switchAsset('asset-01',{asset:true})")
    wait_loaded(page, 2)
    page.evaluate("""uri => {
      window.pywebview.api.pick_asset_texture_file=async (_path,role) => ({
        role:role || 'diffuse',tex_key:(role || 'diffuse')+'::picked-01.png',uri,file:'picked-01.png'});
      window.modViewer.activeMeshes[0].userData.componentDescriptor.openTextureManager();
    }""", solid_texture((128, 140, 150)))
    assert page.locator('.texm-map-cell').count() == 4
    page.locator('.texm-map-cell').first.click()
    page.wait_for_function("window.modViewer.activeMeshes.every(mesh=>mesh.userData.lightMapKey==='light_map::picked-01.png')")
    page.locator('#texm-add').click()
    page.locator('.texm-candidate-row').wait_for()
    assert page.locator('.texm-map-cell').count() == 4
    assert page.evaluate("""() => {
      const meshes=window.modViewer.activeMeshes, pool=meshes[0].userData.componentDescriptor.texturePool;
      return pool.length===1 && meshes.every(mesh=>mesh.userData.texturePool===pool);
    }""")
    page.locator('#texm-close').click()
    page.locator('#inspector-tab').click()
    page.locator('.draw-item').first.click()
    page.locator('.inspector-texture-option[data-texture-value="diffuse::picked-01.png"]').click()
    assert page.evaluate("window.modViewer.activeMeshes.every(mesh=>mesh.userData.texKey==='diffuse::picked-01.png')")
    page.locator('#export-btn').click()
    page.wait_for_function('!document.querySelector("#pending-indicator").classList.contains("show")')
    assert bridge_calls(page, 'assetTextures')[0][2] == {'component-01': {
        'diffuse': 'diffuse::picked-01.png', 'light_map': 'light_map::picked-01.png'}}
    assert page.evaluate("window.modViewer.activeMeshes.every(mesh=>mesh.userData.normalMapKey==='normal_map::normal-01.png')")

"""Asset texture Export is explicit, source-scoped and safe across async saves."""

from .payloads import textured_payload
from .support import bridge_calls, wait_loaded


def test_asset_section_context_remove_failure_retry_last_section_and_switch_guard(viewer):
    payload = textured_payload(3)
    keys = list(payload['meshes'])
    first, second = 'Asset01/00000001/hash.json', 'Asset01/00000002/hash.json'
    for ordinal, mesh in enumerate(payload['meshes'].values()):
        mesh['source'] = first if ordinal < 2 else second
    payload['metadata'].update(source_kind='asset', asset={
        'texture_session': 'session-01', 'sections': {
            first: {'parts': keys[:2]}, second: {'parts': keys[2:]}}})
    page = viewer({'asset-01': payload, 'asset-02': payload})
    page.evaluate("""() => {
      window.__assetLoad = window.modViewer.switchAsset('asset-01',{asset:true});
      window.__assetLoad.then(value => window.__assetLoaded = value);
    }""")
    page.wait_for_function('window.__assetLoaded !== undefined || document.querySelector("#dialog-backdrop").classList.contains("show")')
    assert not page.locator('#dialog-backdrop.show').count(), page.locator('#dialog-message').inner_text()
    assert page.evaluate('window.__assetLoad')
    wait_loaded(page, 3)
    assert page.locator('.mesh-src-hdr .group-name').all_text_contents() == [first, second]
    page.locator('.mesh-src-hdr').first.click(button='right')
    remove = page.locator('.mesh-context-menu [data-i18n="asset.removeSection"]')
    assert remove.inner_text() == 'Remove'
    remove.click()
    page.wait_for_function('window.modViewer.activeMeshes.length === 1')
    assert page.locator('.mesh-src-hdr .group-name').all_text_contents() == [second]
    assert bridge_calls(page, 'assetTextures') == []
    assert page.locator('#export-btn').is_enabled()
    page.evaluate("""source => window.__bridge.results.assetTextures = {
        saved:0, removed:[], failed:[{section:source,error:'synthetic rename failure'}]
      }""", first)
    page.locator('#export-btn').click()
    page.locator('#dialog-backdrop.show').wait_for()
    page.locator('#dialog-ok').click()
    assert page.locator('#pending-indicator').is_visible()
    page.evaluate("source => window.__bridge.results.assetTextures = {saved:0,removed:[source],failed:[]}", first)
    page.locator('#export-btn').click()
    page.wait_for_function('document.querySelector("#export-btn").disabled')
    assert bridge_calls(page, 'assetTextures')[-1] == ['asset-01', 'session-01', {}, [first]]
    page.locator('.mesh-src-hdr').click(button='right')
    remove.click()
    page.wait_for_function('window.modViewer.activeMeshes.length === 0')
    assert page.locator('.mesh-src-hdr').count() == 0
    page.evaluate("() => { window.__switch=window.modViewer.switchAsset('asset-02',{asset:true}); }")
    page.locator('#dialog-backdrop.show').wait_for()
    page.locator('#dialog-cancel').click()
    assert page.evaluate('window.__switch') is False
    page.evaluate("source => window.__bridge.results.assetTextures = {saved:0,removed:[source],failed:[]}", second)
    page.locator('#export-btn').click()
    page.wait_for_function('document.querySelector("#export-btn").disabled')
    assert bridge_calls(page, 'assetTextures')[-1] == ['asset-01', 'session-01', {}, [second]]
    assert page.locator('#pending-indicator').is_hidden()


def test_asset_texture_export_failure_retry_pending_edits_and_stale_completion(module_page):
    module_page.route('**/js/mesh/mesh-state.js', lambda route: route.fulfill(
        content_type='text/javascript', body='''
          export const activeMeshes = [];
          export const TEXTURE_ROLE_FIELDS = [
            ['diffuse','texKey'], ['normal_map','normalMapKey'], ['normal_data','normalDataKey'],
            ['light_map','lightMapKey'], ['material_map','materialMapKey'], ['emission_map','emissionMapKey']
          ].map(([role,current])=>({role,current}));
          export const updateAssetTextureDefaults = changes => window.__defaults.push(changes);
        '''))
    module_page.route('**/js/ui/dialogs.js', lambda route: route.fulfill(
        content_type='text/javascript', body='export const alertDialog = async message => window.__alerts.push(message);'))
    result = module_page.evaluate("""async () => {
      const {viewerState} = await import('./js/app/state.js');
      const {activeMeshes} = await import('./js/mesh/mesh-state.js');
      const {initializeAssetTextureExport:initialize,assetTextureExportState:state,exportAssetTextures:save}
        = await import('./js/app/asset-texture-export.js');
      window.__alerts = [];
      window.__defaults = [];
      const writes = [];
      let response = {error:'synthetic failure'}, release;
      window.pywebview = {api:{export_asset_textures:async (...args) => {
        writes.push(args); return response === null ? new Promise(resolve => release=resolve) : response;
      }}};
      const mesh = {userData:{semanticKey:'part-01',texKey:'diffuse::asset/root/a.png',lightMapKey:'light_map::asset/root/a.png',
        texturePool:[{tex_key:'diffuse::asset/root/c.png'}]}};
      activeMeshes.push(mesh,{visible:false,userData:{semanticKey:'part-02',texKey:'diffuse::asset/root/a.png'}});
      viewerState.currentSource = {kind:'asset',path:'asset-01'};
      initialize('session-01');
      const initial = state();
      mesh.userData.texKey = 'diffuse::asset/root/b.png';
      mesh.userData.manualTexOverride = mesh.userData.texKey;
      await save();
      const failed = state();
      response = null;
      const pending = save();
      await Promise.resolve();
      const busy = state();
      await save();
      mesh.userData.texKey = 'diffuse::asset/root/c.png';
      mesh.userData.manualTexOverride = mesh.userData.texKey;
      release({saved:2}); await pending;
      const newer = state();
      // Explicitly clearing an unavailable role still counts as an edit.
      mesh.userData.texturePool[0].normal_map_manual = true;
      response = {saved:2}; await save();
      const saved = state();
      mesh.userData.lightMapKey = null;
      mesh.userData.texturePool[0].light_map_manual = true;
      response = null;
      const oldSave = save(); await Promise.resolve();
      viewerState.currentSource = {kind:'asset',path:'asset-02'};
      initialize('session-02');
      mesh.userData.texKey = 'diffuse::asset/root/d.png';
      mesh.userData.manualTexOverride = mesh.userData.texKey;
      release({saved:2}); await oldSave;
      const switched = state();
      const count = writes.length;
      for (const source of [{kind:'mod',readOnly:false},{kind:'mod',readOnly:true}]) {
        viewerState.currentSource=source; initialize(); await save();
      }
      return {initial,failed,busy,newer,saved,switched,writes,alerts:window.__alerts,defaults:window.__defaults,
        modBlocked:count===writes.length,final:state()};
    }""")
    assert result['initial'] == {'pending': False, 'saving': False, 'available': False}
    assert result['failed'] == {'pending': True, 'saving': False, 'available': True}
    assert result['busy'] == {'pending': True, 'saving': True, 'available': False}
    assert result['newer'] == result['switched'] == result['failed']
    assert result['saved'] == result['initial'] == result['final']
    assert result['modBlocked'] and len(result['writes']) == 4
    assert len(result['alerts']) == 1 and 'synthetic failure' in result['alerts'][0]
    first = result['writes'][0]
    assert first[:2] == ['asset-01', 'session-01']
    assert first[2] == {'part-01': {'diffuse': 'diffuse::asset/root/b.png'}}
    assert result['writes'][-1][2] == {'part-01': {'light_map': None}}
    assert result['defaults'] == [
        {'part-01': {'diffuse': 'diffuse::asset/root/b.png'}},
        {'part-01': {'diffuse': 'diffuse::asset/root/c.png', 'normal_map': None}},
    ]


def test_open_asset_export_button_dropdown_overflow_and_switch_guard(viewer):
    payload = textured_payload(2)
    first, second = payload['meshes'].values()
    second['component'] = second['identity']['component'] = first['component']
    second['tex_key'] = 'diffuse::texture-02.png'
    for index in (1, 2):
        payload['textures'][f'light_map::texture-{index:02d}.png'] = payload['textures'][f'diffuse::texture-{index:02d}.png']
    first['light_map_key'] = 'light_map::texture-01.png'
    payload['texture_pools']['pool-01'][0]['light_map'] = first['light_map_key']
    payload['metadata'].update(source_kind='asset', asset={'texture_session':'session-01'})
    page = viewer({'asset-01':payload,'asset-02':payload})
    assert page.evaluate("window.modViewer.switchAsset('asset-01',{asset:true})")
    wait_loaded(page, 2)
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
      const [mesh,sibling]=window.modViewer.activeMeshes;
      const data=mesh.userData;
      return data.texKey === null && data.defaultTexKey === null && data.resolvedTexKey === null &&
        sibling.userData.texKey === 'diffuse::texture-02.png';
    }""")
    assert page.locator('#export-btn').is_disabled()
    page.evaluate("""() => {
      const mesh=window.modViewer.activeMeshes[0];
      const option=mesh.userData.texturePool.find(item=>item.tex_key===window.__originalTexture);
      option.light_map='light_map::texture-02.png';
      option.light_map_manual=true;
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
        'diffuse': 'diffuse::texture-01.png', 'light_map': 'light_map::texture-02.png'}}
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


def test_asset_export_saves_run_owner_without_pinning_inherited_roles(viewer):
    payload = textured_payload(2)
    first, follower = payload['meshes'].values()
    follower['component'] = follower['identity']['component'] = first['component']
    follower['tex_key'] = None
    payload['textures']['light_map::texture-02.png'] = payload['textures']['diffuse::texture-02.png']
    payload['metadata'].update(source_kind='asset', asset={'texture_session':'session-01'})
    page = viewer({'asset-01': payload})
    assert page.evaluate("window.modViewer.switchAsset('asset-01',{asset:true})")
    wait_loaded(page, 2)
    assert page.evaluate("""() => {
      const [owner,follower]=window.modViewer.activeMeshes;
      const option=owner.userData.texturePool.find(item=>item.tex_key==='diffuse::texture-02.png');
      option.light_map='light_map::texture-02.png';
      option.light_map_manual=true;
      owner.userData.componentDescriptor.setTextureOverride(owner,option.tex_key);
      return follower.userData.texKey === option.tex_key &&
        follower.userData.lightMapKey === option.light_map && !follower.userData.automaticTextureBoundary;
    }""")
    page.locator('#export-btn').click()
    page.wait_for_function('!document.querySelector("#pending-indicator").classList.contains("show")')
    assert bridge_calls(page, 'assetTextures')[0][2] == {'mesh-00': {
        'diffuse': 'diffuse::texture-02.png', 'light_map': 'light_map::texture-02.png'}}
    assert page.evaluate("""() => {
      const [owner,follower]=window.modViewer.activeMeshes;
      owner.userData.componentDescriptor.setTextureOverride(owner,undefined);
      return owner.userData.texKey === 'diffuse::texture-02.png' &&
        follower.userData.texKey === owner.userData.texKey && follower.userData.defaultTexKey === null &&
        follower.userData.defaultLightMapKey === null && !follower.userData.assetTextureDefaultExplicit;
    }""")
    assert page.locator('#export-btn').is_disabled()

import math
import pytest
import struct

from .payloads import model_payload, weighted_payload
from .support import bridge_calls, open_model, wait_loaded


@pytest.mark.parametrize('stage', ['texture', 'fill', 'weights'])
def test_auto_weight_rig_cancels_deferred_work_on_model_switch(viewer, stage):
    payload, weights = weighted_payload()
    payload['asset_resolution'] = {'configured_roots': 1}
    if stage == 'texture':
        payload['textures'] = {'diffuse::texture01.png': '/fixture-pending.png'}
        payload['meshes']['mesh-00']['tex_key'] = 'diffuse::texture01.png'
    page = viewer({'model01': payload, 'model02': model_payload(), 'fixture-weights': weights},
                  preferences={'autoLoadWeightRig': True, 'autoRestoreMissingAssetParts': True},
                  native={'fill': {'status': 'nothing_missing'}})
    requests = []
    page.route('**/fixture-pending.png', lambda route: requests.append(route))
    page.evaluate('''stage => {
      window.__bridge.blocked[stage]=true;
      const original=window.pywebview.api.get_model_skinning_preview;
      window.pywebview.api.get_model_skinning_preview=async (...args)=>{
        const result=await original(...args);window.__weightRequestFinished=true;return result;
      };
    }''', stage)
    open_model(page, 'model01')
    wait_loaded(page)
    if stage == 'texture':
        page.wait_for_function("performance.getEntriesByType('resource').length > 0")
        assert requests
    else:
        page.wait_for_function("stage=>window.__bridge.calls.some(call=>call.name===stage)", arg=stage)
    if stage != 'weights':
        assert bridge_calls(page, 'weights') == []
    open_model(page, 'model02')
    wait_loaded(page)
    if stage == 'texture':
        requests[0].abort()
    else:
        page.evaluate('stage=>window.__bridge.release(stage)', stage)
    if stage == 'weights':
        page.wait_for_function('window.__weightRequestFinished')
    page.evaluate('''async () => {
      window.__weightFeatureModule=await import('./js/weight-rig/weight-rig-feature.js');
    }''')
    page.wait_for_function('''() => {
      const feature=window.__weightFeatureModule.getLoadedWeightRigFeature();
      return feature && !feature.isWeightRigActivating() && !feature.getModelRigState().loading;
    }''')
    assert page.evaluate('''async () => {
      const {getLoadedWeightRigFeature}=await import('./js/weight-rig/weight-rig-feature.js');
      const feature=getLoadedWeightRigFeature();
      return !feature.getModelRigState().loaded && !feature.getRigAnimationState().clips.length &&
        feature.getModelWeightState().sources.length===0;
    }''')
    assert bridge_calls(page, 'weights') == ([['model01']] if stage == 'weights' else [])
    assert page.locator('#animation-playback').is_hidden()


@pytest.mark.parametrize('kind', ['mod', 'asset'])
def test_auto_weight_rig_load_order_and_viewport_playback_lifecycle(viewer, kind):
    payload, weights = weighted_payload()
    payload['asset_resolution'] = {'configured_roots': 1}
    if kind == 'asset':
        payload['metadata'].update(source_kind='asset', asset=dict(rig_session='session01', path='Asset01'))
    bones = [dict(name='Root', parent=-1, translation=[0, 0, 0], rotation=[0, 0, 0, 1]),
             dict(name='Joint01', parent=0, translation=[1, 0, 0], rotation=[0, 0, 0, 1])]
    sequence = dict(id='sequence01', name='Sequence01', cyclic=True, restart=0,
                    steps=[dict(clip=clip, start=0, leave=.5, fade=0) for clip in ['clip01', 'clip02']])
    weights['game_rigs'] = {'rig01': dict(id='rig01', version=2, bones=bones, animation_session='session01',
        animations=[dict(id=clip, name=clip, duration=1) for clip in ['clip01', 'clip02']],
        animation_sequences=[sequence])}
    weights['meshes']['mesh-00'].update(game_rig_id='rig01', game_bone_map={'0': 0, '1': 1})
    page = viewer({'model01': payload, 'model02': model_payload(), 'fixture-weights': weights},
                  preferences={'autoLoadWeightRig': True, 'autoRestoreMissingAssetParts': True},
                  native={'fill': {'status': 'nothing_missing'}})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.evaluate('''() => {
      const weights=window.pywebview.api.get_model_skinning_preview;
      const preview=async (...args)=>{
        const value=await weights(...args);
        if(window.__noClips)value.game_rigs.rig01.animations=[];
        return value;
      };
      window.pywebview.api.get_model_skinning_preview=preview;
      window.pywebview.api.get_asset_skinning_preview=preview;
      const values=new Float32Array([
        0,0,0,0,0,0,1, 1,0,0,0,0,0,1,
        1,0,0,0,0,0,1, 1,0,0,0,0,0,1,
      ]);
      const url=URL.createObjectURL(new Blob([values]));
      const load=async (token,id)=>({status:'ok',clip:{skeleton_id:'rig01',frame_count:2,
        bone_count:2,duration:1,data:{url,length:values.byteLength}}});
      window.pywebview.api.get_model_animation=load;
      window.pywebview.api.get_asset_animation=load;
      window.__bridge.blocked.fill=true;
      window.__bridge.blocked.weights=true;
    }''')
    if kind == 'mod':
        open_model(page, 'model01')
        wait_loaded(page)
        page.wait_for_function("window.__bridge.calls.some(call=>call.name==='fill')")
        assert bridge_calls(page, 'weights') == []
        page.evaluate("window.__bridge.release('fill')")
    else:
        assert page.evaluate("window.modViewer.switchAsset('model01',{asset:true})")
        wait_loaded(page)
        assert bridge_calls(page, 'fill') == []
    page.wait_for_function("window.__bridge.calls.some(call=>call.name==='weights')")
    assert page.locator('#weight-rig-panel').is_hidden()
    assert not page.evaluate('''async () => {
      const {getLoadedWeightRigFeature}=await import('./js/weight-rig/weight-rig-feature.js');
      return getLoadedWeightRigFeature().getRigAnimationState().playing;
    }''')
    page.evaluate("window.__bridge.release('weights')")
    page.wait_for_function("document.querySelector('#animation-clip .animation-picker-name').textContent==='clip01'")
    page.wait_for_function("!document.querySelector('#animation-play-pause').disabled")
    assert page.locator('#animation-playback').is_visible()
    assert page.locator('#animation-frame').is_visible()
    assert page.evaluate('''async () => {
      const {getLoadedWeightRigFeature}=await import('./js/weight-rig/weight-rig-feature.js');
      window.__autoRig=getLoadedWeightRigFeature();
      return window.__autoRig.getModelRigState().loaded && window.__autoRig.getRigAnimationState().playing;
    }''')
    playing = "id => window.__autoRig.getRigAnimationState().selected===id && window.__autoRig.getRigAnimationState().playing"
    revision = page.evaluate('window.__autoRig.getModelRigState().structureRevision')
    # Toolbar popovers stay anchored to their button and overlay the dock.
    page.locator('#light-btn').click()
    assert page.evaluate('''() => {
      const popover = document.getElementById('light-popover').getBoundingClientRect();
      const dock = document.getElementById('animation-playback').getBoundingClientRect();
      const button = document.getElementById('light-btn').getBoundingClientRect();
      const top = document.elementFromPoint(popover.left + popover.width / 2, popover.bottom - 2);
      return popover.bottom <= button.top && popover.bottom > dock.top && !!top?.closest('#light-popover');
    }''')
    page.keyboard.press('Escape')
    for control, clip in [('next', 'clip02'), ('next', 'clip01'), ('previous', 'clip02')]:
        page.locator(f'#animation-{control}').click()
        page.wait_for_function(playing, arg=clip)
    page.locator('#animation-play-pause').click()
    assert page.evaluate('!window.__autoRig.getRigAnimationState().playing')
    page.locator('#animation-frame').evaluate("node=>{node.value='0.5';node.dispatchEvent(new Event('input'));}")
    assert page.evaluate('window.__autoRig.getRigAnimationState().time') == .5
    # The picker lists Rest, then clips and sequences in labelled groups; the
    # query matches every term and Enter picks the highlighted match.
    picker = page.locator('#animation-picker')
    names = "[...document.querySelectorAll('#animation-options .animation-option-name')].map(node=>node.textContent)"
    page.locator('#animation-clip').click()
    assert picker.is_visible()
    assert page.evaluate(names) == ['Rest pose', 'clip01', 'clip02', 'Sequence01']
    assert page.locator('.animation-option[aria-selected="true"]').get_attribute('data-value') == 'clip02'
    page.keyboard.type('CLIP 1')
    assert page.evaluate(names) == ['clip01']
    page.keyboard.press('Enter')
    page.wait_for_function(playing, arg='clip01')
    assert picker.is_hidden()
    assert page.evaluate("document.activeElement.id") == 'animation-clip'
    assert page.evaluate('window.__autoRig.getModelRigState().structureRevision') == revision
    # Sequences replace the scrubber with a step bar; Next stays within sequences.
    page.locator('#animation-clip').click()
    page.locator('.animation-filter[data-filter="sequences"]').click()
    assert page.evaluate(names) == ['Sequence01']
    page.locator('.animation-option[data-value="sequence01"]').click()
    page.wait_for_function(playing, arg='sequence01')
    page.wait_for_function("window.__autoRig.getRigAnimationState().stepCount===2")
    assert page.locator('#animation-steps .animation-step').count() == 2
    assert page.locator('#animation-frame').is_enabled() and page.locator('#animation-frame').is_visible()
    page.locator('#animation-play-pause').click()
    page.locator('#animation-frame').evaluate("node=>{node.value='0.75';node.dispatchEvent(new Event('input'));}")
    state = page.evaluate('window.__autoRig.getRigAnimationState()')
    assert (state['step'], state['sequenceTime'], state['sequenceDuration']) == (1, pytest.approx(.75), 1)
    assert page.locator('#animation-frame-counter').text_content() == 'Step 2 / 2 · 0.75 / 1.00 s'
    page.locator('#animation-play-pause').click()
    page.locator('#animation-next').click()
    page.wait_for_function(playing, arg='sequence01')
    page.locator('#animation-clip').click()
    page.locator('.animation-filter[data-filter="all"]').click()
    page.locator('.animation-option[data-value=""]').click()
    assert page.evaluate('!window.__autoRig.getRigAnimationState().selected')
    assert page.locator('#animation-clip .animation-picker-name').text_content() == 'Rest pose'
    # Play, Space and Stop resume or leave the last choice instead of the first clip.
    page.locator('#animation-play-pause').click()
    page.wait_for_function(playing, arg='sequence01')
    page.locator('#animation-stop').click()
    assert page.evaluate('!window.__autoRig.getRigAnimationState().selected')
    assert page.locator('#animation-stop').is_disabled()
    page.evaluate('document.activeElement.blur()')
    page.keyboard.press(' ')
    page.wait_for_function(playing, arg='sequence01')
    page.keyboard.press(' ')
    assert page.evaluate('!window.__autoRig.getRigAnimationState().playing')
    # Collapsing the dock keeps play/pause beside the toolbar toggle.
    toggle = page.locator('#animation-toggle-btn')
    toggle.click()
    assert page.locator('#animation-playback').is_hidden()
    assert toggle.get_attribute('aria-pressed') == 'false'
    assert page.locator('#animation-tools #animation-play-pause').is_visible()
    page.locator('#animation-play-pause').click()
    page.wait_for_function(playing, arg='sequence01')
    toggle.click()
    assert page.locator('#animation-transport #animation-play-pause').is_visible()
    open_model(page, 'model02')
    wait_loaded(page)
    page.wait_for_function('!window.__autoRig.isWeightRigActivating()')
    assert page.locator('#animation-playback').is_hidden()
    assert page.locator('#animation-tools').is_hidden()
    assert page.evaluate('!window.__autoRig.getRigAnimationState().playing')
    page.locator('#settings-btn').click()
    checkbox = page.locator('#settings-auto-load-weight-rig')
    assert checkbox.is_checked()
    checkbox.uncheck()
    page.locator('#settings-done').click()
    previous_loads = len(bridge_calls(page, 'weights'))
    open_model(page, 'model01')
    wait_loaded(page)
    assert len(bridge_calls(page, 'weights')) == previous_loads
    page.evaluate('window.__noClips=true')
    page.locator('#settings-btn').click()
    checkbox.check()
    page.locator('#settings-done').click()
    assert page.evaluate('window.modViewer.reloadCurrentMod()')
    page.wait_for_function('window.__autoRig.getModelRigState().loaded && !window.__autoRig.isWeightRigActivating()')
    assert page.locator('#animation-playback').is_hidden()
    assert bridge_calls(page, 'export') == []
    assert errors == []


def test_asset_playback_keeps_proven_attachments_and_skips_unproven_ones(viewer):
    payload, weights = weighted_payload()
    extra = model_payload(3)
    for key in ('mesh-01', 'mesh-02'):
        mesh = extra['meshes'][key]
        for field in ('pos', 'uv', 'idx'):
            data = extra['_fixture_blob'][mesh[field]['offset']:mesh[field]['offset'] + mesh[field]['length']]
            mesh[field] = {'offset': len(payload['_fixture_blob']), 'length': len(data)}
            payload['_fixture_blob'] += data
        # Weightless Asset parts: only a returned proven attachment entry may bind them.
        payload['meshes'][key] = {**mesh, 'rig_attachment_available': True}
    payload['metadata'].update(source_kind='asset', asset=dict(rig_session='session01', path='Asset01'))
    bones = [dict(name='Root', parent=-1, translation=[0, 0, 0], rotation=[0, 0, 0, 1]),
             dict(name='Joint01', parent=0, translation=[1, 0, 0], rotation=[0, 0, 0, 1])]
    weights['game_rigs'] = {'rig01': dict(id='rig01', version=2, bones=bones, animation_session='session01',
        animations=[dict(id='clip01', name='clip01', duration=1)])}
    weights['meshes']['mesh-00'].update(game_rig_id='rig01', game_bone_map={'0': 0, '1': 1})
    offset = len(weights['_fixture_blob'])
    # Native weights adopted for a weightless capture: four influences per vertex.
    weights['_fixture_blob'] += struct.pack('<12I12f', *[1, 0, 0, 0] * 3, *[.75, .25, 0, 0] * 3)
    weights['meshes']['mesh-01'] = {
        'status': 'ok', 'vertex_count': 3, 'influence_count': 4, 'bone_ids': [0, 1], 'encoding': 'gimi_f32_u32_4',
        'weight_stats': {'0': {'bone_id': 0, 'affected_vertex_count': 3, 'total_weight': .75},
                         '1': {'bone_id': 1, 'affected_vertex_count': 3, 'total_weight': 2.25}},
        'source': {'key': 'part-01.txt|attachment', 'file': 'part-01.txt', 'bone_id_offset': 0},
        'data': {'indices': {'offset': offset, 'length': 48, 'type': 'u32'},
                 'weights': {'offset': offset + 48, 'length': 48, 'type': 'f32'}},
        'game_rig_id': 'rig01', 'game_bone_map': {'0': 0, '1': 1}}
    page = viewer({'model01': payload, 'fixture-weights': weights}, preferences={'autoLoadWeightRig': True})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.evaluate('''() => {
      window.pywebview.api.get_asset_skinning_preview=window.pywebview.api.get_model_skinning_preview;
      const values=new Float32Array([0,0,0,0,0,0,1, 1,0,0,0,0,0,1, 0,0,0,0,0,0,1, 1,0,0,0,0,0,1]);
      const url=URL.createObjectURL(new Blob([values]));
      window.pywebview.api.get_asset_animation=async ()=>({status:'ok',clip:{skeleton_id:'rig01',frame_count:2,
        bone_count:2,duration:1,data:{url,length:values.byteLength}}});
    }''')
    assert page.evaluate("window.modViewer.switchAsset('model01',{asset:true})")
    wait_loaded(page, 3)
    page.evaluate('''async () => {
      window.__attachmentFeature=await import('./js/weight-rig/weight-rig-feature.js');
    }''')
    page.wait_for_function('window.__attachmentFeature.getLoadedWeightRigFeature()?.getRigAnimationState().playing')
    page.evaluate('window.__attachmentRig=window.__attachmentFeature.getLoadedWeightRigFeature()')
    report = page.evaluate('''async () => {
      const {weightRigSkinningRuntime:skin}=await import('./js/weight-rig/weight-rig-core.js');
      const meshes=window.modViewer.activeMeshes;
      return {visible:meshes.map(mesh=>mesh.visible),
        loaded:meshes.map(mesh=>!!skin.getSkinningState(mesh)?.loaded),
        errors:meshes.map(mesh=>skin.getSkinningState(mesh)?.error || null),
        weightError:window.__attachmentRig.getModelWeightState().error || null};
    }''')
    # The proven attachment plays with the skinned part; the unproven one is filtered, not an error.
    assert report == {'visible': [True, True, False], 'loaded': [True, True, False],
                      'errors': [None, None, None], 'weightError': None}, report
    assert errors == []


def test_affine_clip_preserves_reference_scale_and_normal_inverse_transpose(module_page):
    result = module_page.evaluate('''async () => {
      const T = await import('three');
      const {sampleAnimation} = await import('./js/weight-rig/rig-animation.js');
      const {sampleMorphWeights} = await import('./js/weight-rig/animation-morphs.js');
      const {isGameRigSkeleton,buildGameRigReconciliation} = await import('./js/weight-rig/game-rig.js');
      const {applyWeightedNormalDeformationInto} = await import('./js/weight-rig/weight-deformation.js');
      const rig = {id:'rig01',version:2,bones:[{name:'Root',parent:-1,
        translation:[0,0,0],rotation:[0,0,0,1],scale:[1,2,1]}]};
      const clip = {version:2,layout:'frame-major-tqs-f32le',duration:1,
        frame_count:2,bone_count:1,morphs:{names:['Shape01'],weights:new Float32Array(1)}};
      const values = new Float32Array([0,0,0,0,0,0,1,1,2,1, 0,0,0,0,0,0,1,3,4,1, 0,1]);
      const transform = sampleAnimation(rig,clip,values,.5).get(0);
      const normalMatrix = new T.Matrix3().getNormalMatrix(transform);
      const normals = new Float32Array(3);
      applyWeightedNormalDeformationInto(normals,new Float32Array([1,1,0]),
        new Uint32Array([0]),new Float32Array([1]),1,new Map([[0,normalMatrix]]));
      const mirrored = {...rig,bones:[{...rig.bones[0],scale:[-1,2,1]}]};
      const reconciled = buildGameRigReconciliation([{sourceKey:'Blend01',boneIds:[0],
        gameRig:{...mirrored,boneMap:{0:0}}}]);
      return {accepted:isGameRigSkeleton(rig),legacyRejected:!isGameRigSkeleton({...rig,version:1}),
        mirroredFrame:reconciled.authoredRestFrames.get(0).toArray(),
        position:new T.Vector3(1,1,0).applyMatrix4(transform).toArray(),normal:Array.from(normals),
        rest:new T.Vector3(1,1,0).applyMatrix4(sampleAnimation(rig,clip,values,0).get(0)).toArray(),
        morph:Array.from(sampleMorphWeights(clip,values,.5).weights)};
    }''')
    assert result['accepted'] and result['legacyRejected']
    assert result['position'] == pytest.approx([2, 1.5, 0])
    assert result['normal'] == pytest.approx([.6, .8, 0])
    assert result['rest'] == pytest.approx([1, 1, 0])
    assert result['morph'] == pytest.approx([.5])
    assert result['mirroredFrame'] == pytest.approx([0, 0, 0, 1])


def test_animation_interpolation_playback_and_stale_selection_lifecycle(module_page):
    result = module_page.evaluate('''async () => {
      const T = await import('three');
      const {sampleAnimation,createRigAnimation} = await import('./js/weight-rig/rig-animation.js');
      const rig = {id:'rig01',bones:[
        {name:'Root',parent:-1,translation:[0,0,0],rotation:[0,0,0,1]},
        {name:'Joint01',parent:0,translation:[1,0,0],rotation:[0,0,0,1]},
      ],animations:['clip01','clip02','clip03'].map(id=>({id,name:id,duration:1})),animation_session:'session01'};
      const clip = {skeleton_id:rig.id,frame_count:2,bone_count:2,duration:1};
      const values = new Float32Array([
        0,0,0,0,0,0,1, 1,0,0,0,0,0,1,
        2,0,0,0,0,1,0, 1,0,0,0,0,0,1,
      ]);
      const sample = sampleAnimation(rig,clip,values,.5);
      const point = new T.Vector3(2,0,0).applyMatrix4(sample.get(1)).toArray();
      const endpoint = new T.Vector3(2,0,0).applyMatrix4(sampleAnimation(rig,clip,values,1).get(1)).toArray();
      const url = URL.createObjectURL(new Blob([values]));
      let resolve;
      let latest = 'unset';
      let suspended = false;
      let prepared = 0;
      let filtered = false;
      let failPrepare = false;
      let scheduled = null;
      window.requestAnimationFrame = callback => {scheduled=callback;return 1;};
      window.cancelAnimationFrame = () => {scheduled=null;};
      Object.defineProperty(performance,'now',{value:()=>0});
      const runtime = createRigAnimation({getRig:()=>rig,
        loadClip:()=>new Promise(r=>{resolve=r;}),applyPose:p=>{latest=p;if(!p)filtered=false;},
        suspendPhysics:v=>{suspended=v;},prepare:()=>{
          prepared++;filtered=true;if(failPrepare)throw new Error('Fixture preparation failed');
        },notify:()=>{}});
      const response={status:'ok',clip:{...clip,data:{url,length:values.byteLength}}};
      const first = runtime.select('clip01');
      runtime.stop(); resolve(response); await first;
      const stale = !runtime.snapshot().selected && latest===null && !suspended && prepared===0;
      const second = runtime.select('clip01');resolve(response);await second;
      const started=runtime.snapshot().playing&&suspended&&prepared===1;
      scheduled(16.6);
      const firstFrame=runtime.snapshot().time;
      scheduled(33.4);
      const secondFrame=runtime.snapshot().time;
      runtime.setPlaying(false);runtime.seek(.5);
      const paused=runtime.snapshot();
      const posed=latest instanceof Map;
      const heldPose=latest;
      const switching=runtime.select('clip02'), resolveSecond=resolve;
      const held=latest===heldPose&&suspended&&prepared===1&&scheduled===null&&runtime.snapshot().loading;
      const newest=runtime.select('clip03'), resolveNewest=resolve;
      resolveSecond(response);await switching;
      const superseded=latest===heldPose&&prepared===1&&runtime.snapshot().selected==='clip03';
      resolveNewest(response);await newest;
      const replaced=runtime.snapshot().playing&&runtime.snapshot().time===0&&latest!==heldPose&&prepared===2;
      const brokenSwitch=runtime.select('clip02');resolve({status:'error',error:'Fixture unavailable'});await brokenSwitch;
      const failedSwitch=latest===null&&!suspended&&!filtered&&!runtime.snapshot().loading&&!!runtime.snapshot().error;
      const retry=runtime.select('clip03');resolve(response);await retry;
      const cancelledSwitch=runtime.select('clip02');runtime.stop();resolve(response);await cancelledSwitch;
      const stopped=latest===null&&!suspended&&!runtime.snapshot().selected;
      failPrepare=true;
      const failed=runtime.select('clip01');resolve(response);await failed;
      const recovered=latest===null&&!suspended&&!filtered&&!runtime.snapshot().playing&&!!runtime.snapshot().error;
      runtime.stop();
      failPrepare=false;
      const wrong=runtime.select('clip01');
      resolve({...response,clip:{...response.clip,skeleton_id:'rig02'}});await wrong;
      const {setLocale,t}=await import('./js/i18n/index.js');setLocale('ja');
      const translatedError=runtime.snapshot().errorKey==='animation.rigMismatch'&&
        t(runtime.snapshot().errorKey)!==runtime.snapshot().error;
      runtime.stop();
      URL.revokeObjectURL(url);
      return {point,endpoint,stale,started,firstFrame,secondFrame,paused,posed,held,superseded,replaced,failedSwitch,stopped,recovered,translatedError};
    }''')
    assert result['point'] == pytest.approx([1, 2, 0], abs=1e-6)
    assert result['endpoint'] == pytest.approx([0, 0, 0], abs=1e-6)
    assert result['stale'] and result['started'] and result['posed'] and result['stopped'] and result['recovered']
    assert result['held'] and result['superseded'] and result['replaced'] and result['failedSwitch']
    assert result['translatedError']
    assert result['firstFrame'] == pytest.approx(.0166)
    assert result['secondFrame'] == pytest.approx(.0334)
    assert not result['paused']['playing'] and result['paused']['time'] == .5


@pytest.mark.parametrize('cached', [True, False])
def test_sequence_playback_crossfades_steps_and_caches_its_clips(module_page, cached):
    result = module_page.evaluate('''async (cached) => {
      const T = await import('three');
      const {createRigAnimation,fillRigClipSelect} = await import('./js/weight-rig/rig-animation.js');
      const sequence = {id:'seq-01',name:'Group01: StateA – StateB',cyclic:true,restart:.3,steps:[
        {clip:'clip01',start:0,leave:.5,fade:.2},{clip:'clip02',start:.1,leave:.8,fade:.2}]};
      const rig = {id:'rig01',bones:[
        {name:'Root',parent:-1,translation:[0,0,0],rotation:[0,0,0,1]},
        {name:'Joint01',parent:0,translation:[1,0,0],rotation:[0,0,0,1]},
      ],animations:['clip01','clip02'].map(id=>({id,name:id,duration:1})),animation_session:'session01',
        animation_sequences:[sequence]};
      // Each clip holds its root at a fixed x and one constant facial weight.
      const frame = x => [x,0,0,0,0,0,1, 1,0,0,0,0,0,1];
      const urls = [];
      const blob = data => { const url = URL.createObjectURL(new Blob([data])); urls.push(url); return url; };
      const data = {clip01:new Float32Array([...frame(0),...frame(0),.25,.25]),
                    clip02:new Float32Array([...frame(2),...frame(2),.75,.75])};
      const targets = blob(new Uint8Array(4));
      const respond = id => ({status:'ok',clip:{id,skeleton_id:rig.id,frame_count:2,bone_count:2,duration:1,
        data:{url:blob(data[id]),length:data[id].byteLength},
        morphs:{names:['Shape01'],components:[],data:{url:targets,length:4}}}});
      const pending = [];
      const sources = new Set();
      let latest = 'unset', latestMorphs = null, scheduled = null;
      window.requestAnimationFrame = callback => {scheduled=callback;return 1;};
      window.cancelAnimationFrame = () => {scheduled=null;};
      Object.defineProperty(performance,'now',{value:()=>0,configurable:true});
      const until = async test => { for (let i = 0; i < 400 && !test(); i++) await new Promise(r=>setTimeout(r,5)); };
      const runtime = createRigAnimation({getRig:()=>rig,
        loadClip:(token,id)=>new Promise(resolve=>pending.push({id,resolve})),
        applyPose:(pose,{morphs=null}={})=>{latest=pose;latestMorphs=morphs;if(morphs)sources.add(morphs);},
        suspendPhysics:()=>{},prepare:()=>{},notify:()=>{},
        // Without cache room, later steps load one handoff ahead.
        ...(cached ? {} : {sequenceCacheBytes:0})});
      const rootX = () => new T.Vector3().applyMatrix4(latest.get(0)).x;
      const resolveNext = async () => { await until(()=>pending.some(entry=>!entry.done));
        const item = pending.find(entry => !entry.done); item.done = true; item.resolve(respond(item.id)); return item.id; };
      const selecting = runtime.select('seq-01');
      const loaded = [await resolveNext(), await resolveNext()];
      await selecting;
      const started = runtime.snapshot();
      const startedX = rootX();
      scheduled(500);
      const fadeStart = {x:rootX(),morph:latestMorphs.weights[0],snapshot:runtime.snapshot()};
      scheduled(600);
      const midpoint = {x:rootX(),morph:latestMorphs.weights[0]};
      const loadsDuringFade = pending.length;
      scheduled(700);
      const settled = {x:rootX(),loads:pending.map(entry=>entry.id)};
      if (!cached) await resolveNext();
      await new Promise(r=>setTimeout(r,50));
      scheduled(1300);
      const wrapped = runtime.snapshot();
      const morphSources = sources.size;
      // Seeking runs along the whole sequence timeline and swaps in that step's clip.
      const drain = async () => { for (let i = 0; i < 4; i++) { await new Promise(r=>setTimeout(r,20));
        const item = pending.find(entry=>!entry.done); if (!item) return;
        item.done = true; item.resolve(respond(item.id)); } };
      const seeks = [];
      for (const position of [.95, .2]) {
        runtime.seek(position);
        await drain();
        const state = runtime.snapshot();
        seeks.push({step:state.step,time:state.time,position:state.sequenceTime,
          length:state.sequenceDuration,x:rootX()});
      }
      const reselect = runtime.select('seq-01');
      runtime.stop();
      await until(()=>pending.some(entry=>!entry.done));
      await resolveNext();
      await reselect;
      const stale = runtime.snapshot();
      const select = document.createElement('select');
      const order = fillRigClipSelect(select, rig.animations, rig.animation_sequences).map(item=>item.id);
      const group = select.querySelector('optgroup');
      urls.forEach(url=>URL.revokeObjectURL(url));
      return {loaded,started:{step:started.step,count:started.stepCount,time:started.time,playing:started.playing,
        sequences:started.sequences.length},startedX,fadeStart:{x:fadeStart.x,morph:fadeStart.morph,
        step:fadeStart.snapshot.step,time:fadeStart.snapshot.time},midpoint,loadsDuringFade,settled,seeks,
        wrapped:{step:wrapped.step,time:wrapped.time},morphSources,stale:{selected:stale.selected,latest:latest,step:stale.step,
        count:stale.stepCount},order,group:group?.label,grouped:[...(group?.children||[])].map(option=>option.value)};
    }''', cached)
    assert result['loaded'] == ['clip01', 'clip02']
    assert result['started'] == {'step': 0, 'count': 2, 'time': 0, 'playing': True, 'sequences': 1}
    assert result['startedX'] == pytest.approx(0, abs=1e-6)
    # Leaving step 0 at 0.5 s starts the incoming step at its offset with the outgoing pose.
    assert result['fadeStart']['step'] == 1 and result['fadeStart']['time'] == pytest.approx(.1)
    assert result['fadeStart']['x'] == pytest.approx(0, abs=1e-6) and result['fadeStart']['morph'] == pytest.approx(.25)
    # Halfway through the fade the pose is blended. Cached steps share one merged facial
    # source whose weights crossfade; otherwise weights switch to the incoming clip.
    assert result['midpoint']['x'] == pytest.approx(1, abs=1e-5)
    assert result['midpoint']['morph'] == pytest.approx(.5 if cached else .75)
    assert result['morphSources'] == (1 if cached else 2)
    # Cached step clips are never downloaded again; without cache room the next step
    # loads only once the fade has released the outgoing clip.
    assert result['loadsDuringFade'] == 2
    assert result['settled'] == {'x': pytest.approx(2, abs=1e-6),
                                 'loads': ['clip01', 'clip02'] + ([] if cached else ['clip01'])}
    # The cyclic sequence wraps to its restart time without waiting for a download when cached;
    # the 0.1 s this frame ran past the 0.8 s leave already plays the restarted step.
    assert result['wrapped'] == {'step': 0, 'time': pytest.approx(.4)}
    # Step spans of 0.5 s and 0.7 s make a 1.2 s timeline; uncached step clips download first.
    assert result['seeks'] == [
        {'step': 1, 'time': pytest.approx(.55), 'position': pytest.approx(.95), 'length': pytest.approx(1.2),
         'x': pytest.approx(2, abs=1e-6)},
        {'step': 0, 'time': pytest.approx(.2), 'position': pytest.approx(.2), 'length': pytest.approx(1.2),
         'x': pytest.approx(0, abs=1e-6)},
    ]
    assert result['stale'] == {'selected': None, 'latest': None, 'step': None, 'count': 0}
    assert result['order'] == ['clip01', 'clip02', 'seq-01']
    assert result['group'] == 'Sequences' and result['grouped'] == ['seq-01']


def test_sequence_steps_carry_root_motion_and_return_to_origin_each_pass(module_page):
    result = module_page.evaluate('''async () => {
      const T = await import('three');
      const {createRigAnimation} = await import('./js/weight-rig/rig-animation.js');
      const sequence = {id:'seq-01',name:'Group01: StateA – StateB',cyclic:true,restart:0,steps:[
        {clip:'clip01',start:0,leave:.5,fade:0},{clip:'clip02',start:0,leave:.5,fade:0}]};
      // Each clip places the body absolutely; its Animator-space root-motion track
      // (MotionT.xyz, MotionQ.xyzw per frame) follows the bone transforms.
      const rig = {id:'rig01',bones:['Top','Body'].map((name,index)=>(
        {name,parent:index-1,translation:[0,0,0],rotation:[0,0,0,1]})),
        animations:['clip01','clip02'].map(id=>({id,name:id,duration:1})),animation_session:'session01',
        animation_sequences:[sequence]};
      const turn = [0,0,Math.SQRT1_2,Math.SQRT1_2];
      const pose = x => [0,0,0,0,0,0,1, x,0,0,0,0,0,1];
      const motion = (x, rotation=[0,0,0,1]) => [x,0,0,...rotation];
      const data = {clip01:new Float32Array([...pose(0),...pose(1),...motion(0),...motion(1)]),
                    clip02:new Float32Array([...pose(5),...pose(6),...motion(5,turn),...motion(6,turn)])};
      const urls = [];
      const respond = id => { const url = URL.createObjectURL(new Blob([data[id]])); urls.push(url);
        return {status:'ok',clip:{id,skeleton_id:rig.id,frame_count:2,bone_count:2,duration:1,
          root_motion:{bone:0},data:{url,length:data[id].byteLength}}}; };
      let latest = null, scheduled = null;
      window.requestAnimationFrame = callback => {scheduled=callback;return 1;};
      window.cancelAnimationFrame = () => {scheduled=null;};
      Object.defineProperty(performance,'now',{value:()=>0,configurable:true});
      const runtime = createRigAnimation({getRig:()=>rig,loadClip:async(_token,id)=>respond(id),
        applyPose:pose=>{latest=pose;},suspendPhysics:()=>{},prepare:()=>{},notify:()=>{}});
      const body = () => new T.Vector3().applyMatrix4(latest.get(1)).toArray().map(v=>Math.round(v*1e5)/1e5);
      await runtime.select('seq-01');
      const started = body();
      // This frame crosses the 0.5 s leave by 20 ms; that remainder plays the entering step.
      scheduled(520);
      const entered = {step:runtime.snapshot().step,time:runtime.snapshot().time,body:body()};
      scheduled(750);
      const travelled = body();
      scheduled(1000);
      const wrapped = {step:runtime.snapshot().step,body:body()};
      runtime.seek(.75);
      await new Promise(r=>setTimeout(r,20));
      const seeked = {step:runtime.snapshot().step,body:body()};
      runtime.stop();
      urls.forEach(url=>URL.revokeObjectURL(url));
      return {started,entered,travelled,wrapped,seeked};
    }''')
    assert result['started'] == [0, 0, 0]
    # The second clip starts its root motion 5 units away and turned 90 degrees; the
    # body continues from where the first step left it instead of jumping there.
    assert result['entered'] == {'step': 1, 'time': pytest.approx(.02), 'body': [.5, -.02, 0]}
    # Its forward travel follows the carried heading: the motion's turn is undone too.
    assert result['travelled'] == [.5, -.25, 0]
    # A cyclic sequence returns to the origin when it wraps to its first step.
    assert result['wrapped'] == {'step': 0, 'body': [0, 0, 0]}
    # Seeking replays the travel that the earlier resident steps carry.
    assert result['seeked'] == {'step': 1, 'body': [.5, -.25, 0]}

def test_merged_clip_morphs_keep_each_clips_deltas_and_reject_conflicts(module_page):
    result = module_page.evaluate('''async () => {
      const {bindAnimationMorphs,mergeClipMorphs,sampleMergedMorphWeights} =
        await import('./js/weight-rig/animation-morphs.js');
      // Sparse rows: vertex id, position delta xyz, normal delta xyz.
      const rows = list => { const view = new DataView(new ArrayBuffer(list.length * 28));
        list.forEach((row, index) => row.forEach((value, axis) => axis ? view.setFloat32(index*28+axis*4, value, true)
          : view.setUint32(index*28, value, true))); return view.buffer; };
      const join = (...buffers) => { const out = new Uint8Array(buffers.reduce((n, b) => n + b.byteLength, 0));
        let offset = 0; for (const b of buffers) { out.set(new Uint8Array(b), offset); offset += b.byteLength; }
        return out.buffer; };
      const shared = rows([[0,1,0,0,0,0,0]]), first = rows([[1,0,2,0,0,0,0]]), second = rows([[1,0,0,3,0,0,0]]);
      const component = targets => ({vertex_sha256:'v'.repeat(64),index_sha256:'i'.repeat(64),targets});
      const clip = (names, buffer, targets) => ({morphs:{names,components:[component(targets)],buffer,
        weights:new Float32Array(names.length)}});
      const a = clip(['Shape01','Shape02'], join(shared, first),
        [{name:'Shape01',offset:0,count:1},{name:'Shape02',offset:28,count:1}]);
      const b = clip(['Shape03','Shape01'], join(second, shared),
        [{name:'Shape03',offset:0,count:1},{name:'Shape01',offset:28,count:1}]);
      const merged = mergeClipMorphs([a, b, a]);
      const state = {gameMorphBinding:{vertex_sha256:'v'.repeat(64),index_sha256:'i'.repeat(64),vertices:[0,1]},
        indices:new Uint32Array(2),influenceCount:1};
      const bound = bindAnimationMorphs(state, merged);
      // Each clip's values land on its own names in the union, scaled by its factor.
      const frame = (weights) => ({clip:{duration:1,frame_count:1,bone_count:0,morphs:null},values:Float32Array.from(weights)});
      const left = {...frame([.2,.4]), clip:{...frame([]).clip, morphs:a.morphs}};
      const right = {...frame([.6,.8]), clip:{...frame([]).clip, morphs:b.morphs}};
      sampleMergedMorphWeights(merged, [[left.clip,left.values,0,.5],[right.clip,right.values,0,.5]]);
      const conflict = mergeClipMorphs([a, clip(['Shape01'], rows([[0,9,0,0,0,0,0]]), [{name:'Shape01',offset:0,count:1}])]);
      const truncated = mergeClipMorphs([clip(['Shape01'], new ArrayBuffer(10), [{name:'Shape01',offset:0,count:1}])]);
      return {names:merged.names,targets:merged.components[0].targets.map(t=>t.name),bytes:merged.buffer.byteLength,
        entries:[...bound.entries],ranges:[...bound.ranges],weights:[...merged.weights],conflict,truncated};
    }''')
    assert result['names'] == ['Shape01', 'Shape02', 'Shape03']
    assert result['targets'] == ['Shape01', 'Shape02', 'Shape03'] and result['bytes'] == 3 * 28
    # Vertex 0 keeps the shared delta once; vertex 1 keeps both clips' own deltas.
    assert result['ranges'] == [0, 1, 1, 3]
    assert result['entries'] == pytest.approx([0, 1, 0, 0, 0, 0, 0, 1, 0, 2, 0, 0, 0, 0, 2, 0, 0, 3, 0, 0, 0])
    assert result['weights'] == pytest.approx([.1 + .4, .2, .3])
    assert result['conflict'] is None and result['truncated'] is None


@pytest.mark.parametrize('case,expected', [
    ('partial', [0]), ('same_skeleton', [0, 1]), ('detached_skeleton', [0, 1]), ('invalid_palette', [0]),
    ('unmapped_bone', [0, 1]),
    ('different_skeleton', [0]), ('competing_libraries', None),
    ('invalid_skeleton', None), ('no_library', None),
])
def test_animation_mesh_binding_uses_only_verified_palettes(module_page, case, expected):
    result = module_page.evaluate('''async (variant) => {
      const {buildAnimationMeshBinding} = await import('./js/weight-rig/rig-animation-meshes.js');
      const {buildGameRigReconciliation} = await import('./js/weight-rig/game-rig.js');
      const bones=[{name:'Root',parent:-1,translation:[0,0,0],rotation:[0,0,0,1]}];
      const rig={id:'rig01',bones,animations:[{id:'clip01'}],boneMap:{7:0}};
      const meshes=[{},{}];
      const first={loaded:true,skinningSourceKey:'stream-01.buf',boneIds:[7],gameRig:rig};
      const second={loaded:true,skinningSourceKey:'stream-01.buf',boneIds:[7],gameRig:null};
      if(variant==='same_skeleton')second.gameRig={...rig};
      if(variant==='detached_skeleton')second.gameRig={...rig,bones:bones.map(b=>({...b}))};
      if(variant==='invalid_palette')second.gameRig={...rig,boneMap:{7:9}};
      if(variant==='unmapped_bone')second.gameRig={...rig},second.boneIds=[7,8];
      if(variant==='different_skeleton')second.gameRig={...rig,id:'rig02',animations:[]};
      if(variant==='competing_libraries')second.gameRig={...rig,id:'rig02'};
      if(variant==='invalid_skeleton')rig.bones=[{...bones[0],parent:0}];
      if(variant==='no_library')rig.animations=[];
      const states=new Map([[meshes[0],first],[meshes[1],second]]);
      let serializations=0;bones.toJSON=()=>{serializations++;return bones;};
      const binding=buildAnimationMeshBinding(meshes,states);
      return {included:binding?[...binding.meshes.keys()].map(mesh=>meshes.indexOf(mesh)):null,
        serializations,
        mapped:binding?.meshes.has(meshes[1])?[...binding.meshes.get(meshes[1])]:null,
        manualRig:variant==='partial'?buildGameRigReconciliation([first,second]):null};
    }''', case)
    assert result['included'] == expected
    # A used ID the proven palette lacks gets no transform, so its weight stays at rest.
    assert result['mapped'] == ([[7, 0]] if expected and 1 in expected else None)
    assert result['manualRig'] is None
    assert result['serializations'] == (1 if case == 'detached_skeleton' else 0)


def test_animation_mesh_preparation_is_lazy_and_frame_maps_are_reused(module_page):
    result = module_page.evaluate('''async () => {
      const {Matrix4}=await import('three');
      const {createAnimationMeshSession}=await import('./js/weight-rig/rig-animation-meshes.js');
      const meshes=[true,false].map(visible=>({visible,userData:{},geometry:{attributes:{position:{count:3}}}}));
      const states=new Map(meshes.map(mesh=>[mesh,{}]));
      const prepared=[],applied=[];
      const session=createAnimationMeshSession({states,setVisibility:()=>{},invalidateShadow:()=>{},requestRender:()=>{},
        skinningRuntime:{ensureRigMeshPrepared:(mesh)=>{prepared.push(mesh);return true;},
          applyDeformation:mesh=>{applied.push(mesh);return true;},finalizeDeformationGeometry:()=>{}}});
      const binding={meshes:new Map(meshes.map(mesh=>[mesh,new Map([[7,0]])]))};
      session.prepare(binding,{deform:true});
      const before=prepared.length;
      session.apply(new Map([[0,new Matrix4().makeTranslation(1,0,0)]]));
      const first=states.get(meshes[0]),maps=[first.poseTransforms,first.poseRotations],rotation=first.poseRotations.get(7);
      session.apply(new Map([[0,new Matrix4().makeTranslation(2,0,0)]]));
      const live={prepared:prepared.length,applied:applied.length,
        reused:first.poseTransforms===maps[0]&&first.poseRotations===maps[1]&&first.poseRotations.get(7)===rotation};
      session.apply(new Map([[0,new Matrix4()]]),{finalize:true});
      const finalized=prepared.length;
      session.stop();
      const cleared=[...states.values()].every(state=>!state.poseTransforms&&!state.poseActiveVertices.length);
      meshes[1].userData.assetFill=true;
      prepared.length=0;applied.length=0;
      session.prepare(binding,{deform:false});
      session.apply(new Map([[0,new Matrix4()]]),{finalize:true});
      const fillOnly=prepared.length===1&&prepared[0]===meshes[1]&&applied.length===1;
      session.stop();
      return {before,live,finalized,cleared,fillOnly};
    }''')
    assert result == {'before': 0, 'live': {'prepared': 1, 'applied': 2, 'reused': True}, 'finalized': 2, 'cleared': True, 'fillOnly': True}


@pytest.mark.parametrize('kind', ['asset', 'mod'])
@pytest.mark.parametrize('partial', [False, True])
def test_animation_panel_and_weight_rig_model_lifecycle(viewer, tmp_path, kind, partial):
    import hashlib
    import json
    import numpy as np
    from app.assets.animations import AnimationSessions
    from app.assets.game_extract.zzz_morphs import export_glb
    from tests.support.animations import compute_mod, load_mod

    payload, weights = weighted_payload()
    if partial:
        _, weights = weighted_payload(include_third_member=True)
        payload = model_payload(4)
        for key in weights['meshes']:
            payload['meshes'][key]['skinning_available'] = True
        # The first two meshes share a Blend source, but only the first has a proven palette.
        weights['meshes']['mesh-02']['source'].update(key='stream-02.buf|offset=0', file='stream-02.buf')
    bones = [dict(name='Root', parent=-1, translation=[0, 0, 0], rotation=[0, 0, 0, 1]),
             dict(name='Joint01', parent=0, translation=[1, 0, 0], rotation=[0, 0, 0, 1])]
    weights['game_rigs'] = {'rig01': dict(id='rig01', version=2, bones=bones, animation_session='session01',
        animations=[dict(id='clip01', name='Clip 01', duration=1)])}
    weights['meshes']['mesh-00'].update(game_rig_id='rig01', game_bone_map={'0': 0, '1': 1})
    weights['meshes']['mesh-00']['game_morph_binding'] = dict(vertex_sha256='a' * 64, index_sha256='b' * 64,
                                                               vertices=[2, 0, 1])
    if partial:
        weights['game_rigs']['rig02'] = dict(id='rig02', bones=bones, animations=[])
        weights['meshes']['mesh-02'].update(game_rig_id='rig02', game_bone_map={'4': 0, '5': 1})
    if kind == 'asset':
        payload['metadata'].update(source_kind='asset', asset=dict(rig_session='asset-session', path='Asset01'))
    data = struct.pack('<28f', *([0, 0, 0, 0, 0, 0, 1, 1, 0, 0, 0, 0, 0, 1]
                              + [2, 0, 0, 0, 0, 0, 1, 1, 0, 0, 0, 0, 0, 1]))
    data = b''.join(struct.pack('<10f', *row, 1, 1, 1) for row in struct.iter_unpack('<7f', data))
    data += struct.pack('<2f', 0, 1)
    library = tmp_path / 'Asset01/Animation'
    library.mkdir(parents=True)
    facial = dict(components=[dict(part=dict(positions=np.array([[0,0,0],[1,0,0],[0,1,0]]),
        normals=None, uv=None, indices=np.array([[0,1,2]]), source_vertices=[0,1,2], source_vertex_count=3,
        vertex_sha256='a' * 64, index_sha256='b' * 64), targets={'Shape01': {2: [0,0,2,0,0,0]}})])
    morph = export_glb(facial, dict(names=['Shape01'], weights=np.array([[0],[1]])), 2, 1, library)
    metadata = dict(format='mod-viewer-animation', version=2, layout='frame-major-tqs-f32le', coordinate_space='asset',
        skeleton_id='rig01', frame_count=2, bone_count=2, duration=1, data_file='clip01.bin',
        sha256=hashlib.sha256(data).hexdigest(), morphs=morph)
    (library / 'clip01.bin').write_bytes(data)
    (library / 'clip01.json').write_text(json.dumps(metadata))
    (library / 'manifest.json').write_text(json.dumps(dict(format='mod-viewer-animations', version=1,
        skeleton_id='rig01', clips=[dict(id='clip01', name='Clip 01', file='clip01.json', duration=1)])))
    sessions = AnimationSessions()
    token = sessions.catalog(str(tmp_path), 'Asset01', dict(id='rig01', bones=bones), lambda: True)['animation_session']
    ini = compute_mod(tmp_path / 'mod')
    parsed, built = load_mod(ini, ini.parent)
    replacement = model_payload()
    replacement['meshes'] = built.meshes
    replacement['_fixture_blob'] = bytes(built.geometry.data)
    replacement['state']['defaults'] = parsed.defaults
    fill_payload = model_payload()
    fill_mesh = fill_payload['meshes'].pop('mesh-00')
    fill_mesh.update(asset_fill=True, skinning_available=False, animation_skinning_available=True)
    fill_payload['meshes'] = {'asset-fill::part01': fill_mesh}
    fill_payload['metadata'].update(source_kind='asset-fill', asset=dict(rig_session='fill-token'))
    page = viewer({'model01': payload, 'model02': replacement, 'fixture-weights': weights, 'fixture-fill': fill_payload})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    def load_clip(_source):
        return sessions.load(token, 'clip01')
    page.expose_binding('__animationClip', load_clip)
    page.evaluate('''() => {
      window.pywebview.api.get_asset_skinning_preview = async token => {
        const preview=await window.__fixtureLoad('fixture-weights');
        if(token==='fill-token') {
          preview.meshes={'asset-fill::part01':preview.meshes['mesh-00']};
          for(const rig of Object.values(preview.game_rigs))rig.animation_source='asset';
        }
        return preview;
      };
      const clip = () => window.__failAnimationClip ? {status:'error',error:'Fixture unavailable'} : window.__animationClip();
      window.pywebview.api.get_asset_animation = clip;
      window.pywebview.api.get_model_animation = clip;
    }''')
    open_model(page, 'model01')
    wait_loaded(page, 4 if partial else 1)
    assert bridge_calls(page, 'weights') == []
    page.evaluate('''() => {
      window.__rigAutoStages=[];
      window.addEventListener('mod-viewer-model-rig-changed',event=>{
        if(event.detail.loading)window.__rigAutoStages.push({
          clips:document.querySelectorAll('.rig-animation-select option').length,
          blocked:document.querySelector('.rig-animation-select').disabled,
        });
      });
    }''')
    restore_during_weights = kind == 'mod' and not partial
    if restore_during_weights:
        page.evaluate("window.__bridge.blocked.weights=true")
    page.locator('#weight-rig-tab').click()
    if restore_during_weights:
        page.wait_for_function("window.__bridge.calls.some(call=>call.name==='weights')")
        page.evaluate('''async () => {
          const {loadMissingAssetParts}=await import('./js/app/asset-fill.js');
          await loadMissingAssetParts();
          window.__bridge.release('weights');
        }''')
    page.wait_for_function("document.querySelectorAll('.rig-animation-select option').length === 2")
    page.wait_for_function("!document.querySelector('.rig-animation-select').disabled")
    assert page.locator('.rig-load').count() == 0
    assert page.evaluate('window.__rigAutoStages') == [{'clips': 2, 'blocked': True}]
    assert page.evaluate('''async () => {
      const {weightRigApi:f,weightRigSkinningRuntime:skin} = await import('./js/weight-rig/weight-rig-core.js');
      window.__animationApi = f;
      window.__animationBaseline = [...window.modViewer.activeMeshes[0].geometry.attributes.position.array];
      window.__autoRigRevision=f.getModelRigState().structureRevision;
      window.__autoRigGraphs=window.modViewer.activeMeshes.map(mesh=>skin.getSkinningState(mesh)?.influenceGraph);
      return f.getModelRigState().loaded && !f.getModelRigState().loading;
    }''')
    assert page.locator('.rig-save-preset').is_disabled() == (kind == 'asset')
    if restore_during_weights:
        restored_playback = page.evaluate('''async () => {
          const {removeMissingAssetParts}=await import('./js/app/asset-fill.js');
          const {weightRigSkinningRuntime:skin}=await import('./js/weight-rig/weight-rig-core.js');
          const f=window.__animationApi, fill=window.modViewer.activeMeshes.find(mesh=>mesh.userData.assetFill);
          await f.selectRigAnimation('clip01');f.setRigAnimationPlaying(false);f.seekRigAnimation(.5);
          const report={loaded:skin.getSkinningState(fill)?.loaded,visible:fill.visible};
          f.stopRigAnimation();await removeMissingAssetParts();return report;
        }''')
        assert restored_playback == {'loaded': True, 'visible': True}
    if partial:
        page.evaluate('''async () => {
          const {setManualMeshVisibility} = await import('./js/mesh/mesh-state.js');
          const {separateSelectedTriangles} = await import('./js/mesh/loose-parts.js');
          setManualMeshVisibility(window.modViewer.activeMeshes[3],false);
          const source=window.modViewer.activeMeshes[2];
          source.geometry.setIndex([0,1,2,0,1,2]);
          separateSelectedTriangles(source,[0]);
          window.__unsupportedBaselines=window.modViewer.activeMeshes.slice(1)
            .map(mesh=>[...mesh.geometry.attributes.position.array]);
        }''')
    page.locator('.rig-animation-select').select_option('clip01')
    page.wait_for_function('window.__animationApi.getRigAnimationState().playing')
    page.wait_for_function('''async () => {
      const {weightRigSkinningRuntime:skin}=await import('./js/weight-rig/weight-rig-core.js');
      return !!skin.getSkinningState(window.modViewer.activeMeshes[0])?.animationGpu;
    }''')
    assert page.evaluate('''async () => {
      const mesh=window.modViewer.activeMeshes[0],raw=mesh.geometry.attributes.position.array;
      const snapshot=raw.slice();
      await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
      return raw.every((value,index)=>Object.is(value,snapshot[index]));
    }''')
    if partial:
        assert page.evaluate('window.modViewer.activeMeshes.map(mesh=>mesh.visible)') == [True, False, False, False]
        assert page.evaluate('window.modViewer.activeMeshes[2].userData.looseParts.every(part=>!part.visible)')
        assert page.evaluate('''async () => {
          const {setManualMeshVisibility,setMeshIsolation} = await import('./js/mesh/mesh-state.js');
          const meshes=window.modViewer.activeMeshes;
          setManualMeshVisibility(meshes[1],true);
          setManualMeshVisibility(meshes[1],false);
          setMeshIsolation([meshes[0],meshes[2]]);
          return meshes.map(mesh=>mesh.visible);
        }''') == [True, False, False, False]
    hidden_report = page.evaluate('''async () => {
      const f=window.__animationApi, mesh=window.modViewer.activeMeshes[0];
      mesh.visible=false;
      const before=[...mesh.geometry.attributes.position.array], start=f.getRigAnimationState().time;
      await new Promise((resolve)=>setTimeout(resolve,250));
      return {advanced:f.getRigAnimationState().time!==start,
        frozen:[...mesh.geometry.attributes.position.array].every((v,i)=>Object.is(v,before[i]))};
    }''')
    assert hidden_report == {'advanced': True, 'frozen': True}, hidden_report
    page.locator('.rig-animation-play').click()
    page.locator('.rig-animation-time').evaluate("node => {node.value='0.5';node.dispatchEvent(new Event('input'));}")
    seek_report = page.evaluate('''() => {
      const f=window.__animationApi;
      const mesh=window.modViewer.activeMeshes[0];
      const values=mesh.geometry.attributes.position.array;
      mesh.visible=true;
      return {playing:f.getRigAnimationState().playing,time:f.getRigAnimationState().time,
        values:[...values],baseline:window.__animationBaseline,
        minimum:window.modViewer.activeMeshes[0].geometry.boundingBox.min.x};
    }''')
    assert not seek_report['playing'] and seek_report['time'] == .5, seek_report
    if partial:
        assert page.evaluate('window.modViewer.activeMeshes.map(mesh=>mesh.visible)') == [True, False, False, False]
        assert page.evaluate('''window.modViewer.activeMeshes.slice(1).every((mesh,index)=>
          [...mesh.geometry.attributes.position.array].every((v,i)=>Object.is(v,window.__unsupportedBaselines[index][i])))''')
    assert seek_report['minimum'] == pytest.approx(1), seek_report
    assert seek_report['values'] == pytest.approx([v + (1 if i % 3 == 0 else 0) + (1 if i == 2 else 0)
        for i,v in enumerate(seek_report['baseline'])], abs=1e-5), seek_report
    if kind == 'mod' and not partial:
        assert page.evaluate('''async () => {
          const f=window.__animationApi, mesh=window.modViewer.activeMeshes[0];
          const before=[...mesh.geometry.attributes.position.array];
          const original=window.pywebview.api.get_model_animation;
          let release;
          window.pywebview.api.get_model_animation=(...args)=>new Promise(resolve=>{release=()=>resolve(original(...args));});
          const switching=f.selectRigAnimation('clip01');
          await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
          const held=f.getRigAnimationState().loading&&!f.getRigAnimationState().playing&&
            before.every((value,index)=>Object.is(value,mesh.geometry.attributes.position.array[index]));
          release();await switching;f.setRigAnimationPlaying(false);f.seekRigAnimation(.5);
          window.pywebview.api.get_model_animation=original;
          return held;
        }''')
    assert page.evaluate('''async () => {
      const {weightRigSkinningRuntime:skin}=await import('./js/weight-rig/weight-rig-core.js');
      const rig=window.__animationApi.getModelRigState();
      return rig.loaded && rig.structureRevision===window.__autoRigRevision &&
        window.modViewer.activeMeshes.every((mesh,index)=>
          skin.getSkinningState(mesh)?.influenceGraph===window.__autoRigGraphs[index]);
    }''')
    page.locator('.rig-animation-stop').click()
    assert page.evaluate('''() => window.modViewer.activeMeshes[0].geometry.attributes.position.array
      .every((v,i)=>Object.is(v,window.__animationBaseline[i]))''')
    if partial:
        assert page.evaluate('window.modViewer.activeMeshes.map(mesh=>mesh.visible)') == [True, False, True, False]
        assert page.evaluate('window.modViewer.activeMeshes[2].userData.looseParts.every(part=>part.visible)')
        assert page.evaluate('''async () => {
          const {clearMeshIsolation,isMeshIsolationActive} = await import('./js/mesh/mesh-state.js');
          const active=isMeshIsolationActive();clearMeshIsolation();return active;
        }''')
        page.evaluate('window.__failAnimationClip=true')
        page.locator('.rig-animation-select').select_option('clip01')
        page.wait_for_function('window.__animationApi.getRigAnimationState().error')
        assert page.evaluate('window.modViewer.activeMeshes.map(mesh=>mesh.visible)') == [True, False, True, False]
        page.evaluate('window.__failAnimationClip=false')
        page.locator('.rig-animation-stop').click()
    assert page.evaluate('window.__animationApi.getModelRigState().loaded')
    assert page.locator('.rig-save-preset').is_disabled() == (kind == 'asset')
    if not partial:
        assert page.locator('.rig-bone-select option').all_text_contents()[1:] == ['Root', 'Joint01']
    if kind == 'mod' and not partial:
        fill_report = page.evaluate('''async () => {
          const {loadMissingAssetParts,removeMissingAssetParts}=await import('./js/app/asset-fill.js');
          const {weightRigSkinningRuntime:skin}=await import('./js/weight-rig/weight-rig-core.js');
          const f=window.__animationApi, revision=f.getModelRigState().structureRevision;
          const sourceCount=f.getModelWeightState().sources.length;
          // Base weights stay installed across a fill; only the fill rig offers clips.
          const baseRig=skin.getSkinningState(window.modViewer.activeMeshes[0]).gameRig;
          const baseClips=baseRig.animations;
          baseRig.animations=[];
          let assetClipLoads=0;
          const assetClip=window.pywebview.api.get_asset_animation;
          window.pywebview.api.get_asset_animation=(...args)=>{assetClipLoads++;return assetClip(...args);};
          await loadMissingAssetParts();
          const fill=window.modViewer.activeMeshes.find(mesh=>mesh.userData.assetFill);
          const baseline=[...fill.geometry.attributes.position.array];
          await f.selectRigAnimation('clip01');f.setRigAnimationPlaying(false);f.seekRigAnimation(.5);
          const moved=[...fill.geometry.attributes.position.array];
          const during={bound:!!skin.getSkinningState(fill)?.gameRig,visible:fill.visible,
            readOnly:!fill.userData.skinningAvailable,
            stableRig:f.getModelRigState().structureRevision===revision,
            stableSources:f.getModelWeightState().sources.length===sourceCount};
          f.stopRigAnimation();
          const restored=baseline.every((value,index)=>Object.is(value,fill.geometry.attributes.position.array[index]));
          await removeMissingAssetParts();
          baseRig.animations=baseClips;
          return {baseline,moved,restored,...during,assetClipLoads,removed:!window.modViewer.activeMeshes.includes(fill)};
        }''')
        assert all(fill_report[key] for key in ['bound', 'visible', 'readOnly', 'stableRig', 'stableSources', 'restored', 'removed']), fill_report
        assert fill_report['assetClipLoads'] == 1
        assert fill_report['moved'] == pytest.approx([v + (1 if i % 3 == 0 else 0) + (1 if i == 2 else 0)
            for i, v in enumerate(fill_report['baseline'])])
    page.evaluate('''() => {
      const f=window.__animationApi,source=f.getModelWeightState().sources[0];
      f.setBoneSelected(source.key,1,true);
    }''')
    page.locator('.rig-animation-select').select_option('clip01')
    page.wait_for_function('window.__animationApi.getRigAnimationState().playing')
    page.evaluate('window.__animationApi.setRigJointRotation(1,[0,0,0,1])')
    assert page.evaluate('!window.__animationApi.getRigAnimationState().selected')
    if partial:
        assert page.evaluate('window.modViewer.activeMeshes.map(mesh=>mesh.visible)') == [True, False, True, False]
    page.locator('.rig-animation-select').select_option('clip01')
    page.wait_for_function('window.__animationApi.getRigAnimationState().playing')
    open_model(page, 'model02')
    wait_loaded(page)
    page.wait_for_function('window.modViewer.activeMeshes[0].userData.animationState?.gpu != null')
    assert page.evaluate('''async () => {
      const raw=window.modViewer.activeMeshes[0].geometry.attributes.position.array,snapshot=raw.slice();
      await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
      return raw.every((value,index)=>Object.is(value,snapshot[index]));
    }''')
    assert page.locator('#renderer-error').is_hidden()
    assert errors == []
    assert page.evaluate('''() => {
      const state = window.__animationApi.getRigAnimationState();
      return !state.playing && !state.loading && !state.selected && state.clips.length===0 &&
        window.modViewer.activeMeshes.every(mesh=>mesh.visible);
    }''')
    page.evaluate('''() => {
      const wait=()=>new Promise(resolve=>{window.__pendingWeightsResolve=resolve;});
      window.pywebview.api.get_asset_skinning_preview=wait;
      window.pywebview.api.get_model_skinning_preview=wait;
    }''')
    open_model(page, 'model01')
    wait_loaded(page, 4 if partial else 1)
    page.evaluate('''() => {window.__pendingLibrary=window.__animationApi.loadRigAnimationLibrary();}''')
    page.wait_for_function('!!window.__pendingWeightsResolve')
    open_model(page, 'model02')
    wait_loaded(page)
    assert page.evaluate('''async () => {
      window.__pendingWeightsResolve({status:'ok',meshes:{}});
      const result=await window.__pendingLibrary;
      const rig=window.__animationApi.getModelRigState();
      return result.stale && !rig.loading && !rig.loaded && !window.__animationApi.getRigAnimationState().clips.length;
    }''')
    sessions.clear()


def test_morph_compaction_interpolation_cpu_skinning_and_release(module_page):
    result = module_page.evaluate('''async () => {
      const T=await import('three');
      const {bindAnimationMorphs,sampleMorphWeights}=await import('./js/weight-rig/animation-morphs.js');
      const {createSkinningRuntime}=await import('./js/weight-rig/skinning-runtime.js');
      const {createWeightRuntimeState}=await import('./js/weight-rig/weight-runtime.js');
      const runtime=createWeightRuntimeState(),skin=createSkinningRuntime({...runtime,requestRender:()=>{}});
      const geometry=new T.BufferGeometry();
      geometry.setAttribute('position',new T.Float32BufferAttribute([1,0,0,0,1,0,1,1,0],3));
      geometry.setAttribute('normal',new T.Float32BufferAttribute([0,0,1,0,0,1,0,0,1],3));
      const mesh=new T.Mesh(geometry,new T.MeshBasicMaterial()),state=runtime.stateFor(mesh);
      const baseline=geometry.attributes.position.array.slice();
      Object.assign(state,{loaded:true,baselinePositions:baseline,baselineNormals:geometry.attributes.normal.array.slice(),
        indices:new Uint32Array([7,7,7]),weights:new Float32Array([1,1,1]),influenceCount:1,
        gameMorphBinding:{vertex_sha256:'vb01',index_sha256:'ib01',vertices:[2,0,2]}});
      const buffer=new ArrayBuffer(28),view=new DataView(buffer);
      view.setUint32(0,2,true);view.setFloat32(4,2,true);view.setFloat32(20,1,true);
      const morphs={names:['Shape01'],weights:new Float32Array(1),buffer,
        components:[{vertex_sha256:'vb01',index_sha256:'ib01',vertex_count:3,
          targets:[{name:'Shape01',offset:0,count:1}]}]};
      const wrong=bindAnimationMorphs({...state,gameMorphBinding:{...state.gameMorphBinding,vertex_sha256:'other'}},morphs);
      const mod=bindAnimationMorphs({...state,gameMorphBinding:null},morphs);
      state.animationMorph=bindAnimationMorphs(state,morphs);
      const clip={frame_count:2,bone_count:1,duration:1,morphs},values=new Float32Array(16);
      values[15]=1;sampleMorphWeights(clip,values,.5);
      const rotation=new T.Quaternion().setFromAxisAngle(new T.Vector3(0,0,1),Math.PI/2);
      state.poseTransforms=new Map([[7,new T.Matrix4().makeRotationFromQuaternion(rotation)]]);
      state.poseRotations=new Map([[7,rotation]]);state.poseActiveVertices=new Uint32Array([0,1,2]);
      skin.applyDeformation(mesh,state,{invalidateShadow:false});
      const positions=[...geometry.attributes.position.array],normals=[...geometry.attributes.normal.array];
      state.animationMorph=null;state.poseTransforms=null;state.poseRotations=null;state.poseActiveVertices=new Uint32Array();
      skin.applyDeformation(mesh,state,{invalidateShadow:false});skin.finalizeDeformationGeometry(mesh,state);
      const restored=[...geometry.attributes.position.array];skin.disposeMesh(mesh);geometry.dispose();mesh.material.dispose();
      return {positions,normals,restored,weight:morphs.weights[0],wrong:wrong===null,mod:mod===null};
    }''')
    assert result['positions'] == pytest.approx([0,2,0,-1,0,0,-1,2,0], abs=1e-6)
    assert result['normals'] == pytest.approx([-.5 / 1.25 ** .5,0,1 / 1.25 ** .5,0,0,1,
                                             -.5 / 1.25 ** .5,0,1 / 1.25 ** .5], abs=1e-6)
    assert result['restored'] == [1,0,0,0,1,0,1,1,0]
    assert result['weight'] == .5 and result['wrong'] and result['mod']


def test_gpu_animation_bounds_enclose_posed_vertices_until_finalize(module_page):
    result = module_page.evaluate('''async () => {
      const T=await import('three');
      const {createSkinningRuntime}=await import('./js/weight-rig/skinning-runtime.js');
      const {createWeightRuntimeState}=await import('./js/weight-rig/weight-runtime.js');
      const runtime=createWeightRuntimeState(),skin=createSkinningRuntime({...runtime,requestRender:()=>{}});
      const geometry=new T.BufferGeometry();
      geometry.setAttribute('position',new T.Float32BufferAttribute([0,0,0,1,0,0,0,1,0],3));
      geometry.setAttribute('normal',new T.Float32BufferAttribute([0,0,1,0,0,1,0,0,1],3));
      geometry.computeBoundingBox();geometry.computeBoundingSphere();
      const mesh=new T.Mesh(geometry,new T.MeshBasicMaterial()),state=runtime.stateFor(mesh);
      Object.assign(state,{loaded:true,baselinePositions:geometry.attributes.position.array.slice(),
        baselineNormals:geometry.attributes.normal.array.slice(),influenceCount:2,
        indices:new Uint32Array([1,0,1,0,1,2]),weights:new Float32Array([1,0,1,0,.5,.5])});
      const forward=new T.Matrix4().makeTranslation(0,0,5);
      state.poseTransforms=new Map([[1,forward]]);state.poseRotations=new Map([[1,new T.Quaternion()]]);
      state.poseActiveVertices=new Uint32Array([0,1,2]);
      skin.applyDeformation(mesh,state,{invalidateShadow:false,gpu:true});
      const positions=[...geometry.attributes.position.array];
      const posed=geometry.boundingBox.clone(),culled=mesh.frustumCulled;
      const sphere=geometry.boundingSphere.clone();
      skin.finalizeDeformationGeometry(mesh,state);
      const exact=geometry.boundingBox.clone(),restored=mesh.frustumCulled;
      const contains=positions.every((value,index)=>{
        const axis=index%3;return value>=posed.min.getComponent(axis)&&value<=posed.max.getComponent(axis);});
      const points=[0,1,2].map(i=>new T.Vector3().fromArray(positions,i*3));
      skin.disposeMesh(mesh);geometry.dispose();mesh.material.dispose();
      return {positions,contains,posedMax:posed.max.toArray(),posedMin:posed.min.toArray(),culled,restored,
        sphereHolds:points.every(point=>sphere.containsPoint(point)),exact:[...exact.min.toArray(),...exact.max.toArray()]};
    }''')
    assert result['positions'] == pytest.approx([0, 0, 5, 1, 0, 5, 0, 1, 2.5], abs=1e-6)
    assert result['contains'] and result['sphereHolds']
    assert result['posedMax'][2] >= 5 and result['posedMax'][0] < 1.5
    assert result['culled'] is False and result['restored'] is True
    assert result['exact'] == pytest.approx([0, 0, 2.5, 1, 1, 5], abs=1e-6)


def test_idle_blink_schedule_and_clips_without_facial_curves(module_page):
    result = module_page.evaluate('''async () => {
      const {idleBlinkWeight}=await import('./js/weight-rig/idle-blink.js');
      const {createRigAnimation}=await import('./js/weight-rig/rig-animation.js');
      const samples=Array.from({length:3000},(_,i)=>idleBlinkWeight(i/100));
      const blinks=samples.filter((value,i)=>value>0.99&&samples[i-1]<=0.99).length;
      const repeat=samples.every((value,i)=>value===idleBlinkWeight(i/100));
      const rig={id:'rig01',bones:[
        {name:'Root',parent:-1,translation:[0,0,0],rotation:[0,0,0,1]},
        {name:'Joint01',parent:0,translation:[1,0,0],rotation:[0,0,0,1]}],
        animations:['plain','facial','broken','scaled','unscaled'].map(id=>({id,name:id,duration:1})),animation_session:'session01'};
      const values=new Float32Array(28);
      const bones=URL.createObjectURL(new Blob([values]));
      const shapes=URL.createObjectURL(new Blob([new Uint8Array(28)]));
      const facial=URL.createObjectURL(new Blob([new Float32Array(30)]));
      const idle={names:['E_Close'],components:[{vertex_sha256:'b'.repeat(64),index_sha256:'c'.repeat(64),
        vertex_count:3,targets:[{name:'E_Close',offset:0,count:1}]}],data:{url:shapes,length:28}};
      const base={skeleton_id:rig.id,frame_count:2,bone_count:2,duration:1,data:{url:bones,length:values.byteLength}};
      const clips={
        plain:{...base,idle_morphs:idle},
        facial:{...base,data:{url:facial,length:120},morphs:{names:['Smile'],components:idle.components,data:{url:shapes,length:28}},idle_morphs:idle},
        broken:{...base,idle_morphs:{...idle,data:{url:shapes,length:27}}},
        // A crossfade from a captured default pose: the closed shape rises as the default falls.
        scaled:{...base,idle_morphs:{...idle,names:['E_Close','E_Default'],scales:[1,-1]}},
        unscaled:{...base,idle_morphs:{...idle,names:['E_Close','E_Default'],scales:[1]}},
      };
      let scheduled=null,applied=null;
      window.requestAnimationFrame=callback=>{scheduled=callback;return 1;};
      window.cancelAnimationFrame=()=>{scheduled=null;};
      Object.defineProperty(performance,'now',{value:()=>0,configurable:true});
      const runtime=createRigAnimation({getRig:()=>rig,loadClip:async (_,id)=>({status:'ok',clip:{id,...clips[id]}}),
        applyPose:(transforms,{morphs}={})=>{applied={transforms,morphs};},suspendPhysics:()=>{},prepare:()=>{},notify:()=>{}});
      const play=async id=>{await runtime.select(id);return applied.morphs;};
      const first=await play('plain');
      const seeded=first&&first.weights[0]===0&&first.names[0]==='E_Close'&&first.buffer.byteLength===28;
      let peak=0,frames=0,stable=true;
      for(let now=16.7;now<=6000;now+=16.7){
        scheduled(now);
        stable=stable&&applied.morphs===first;peak=Math.max(peak,applied.morphs.weights[0]);
        if(applied.morphs.weights[0]>0)frames++;
      }
      runtime.setPlaying(false);
      const pausedOpen=applied.morphs.weights[0]===0;
      const own=await play('facial');
      const brokenMorphs=await play('broken');
      const scaled=await play('scaled');
      let crossfade=true,closed=0;
      for(let now=16.7;now<=6000;now+=16.7){
        scheduled(now);
        const [close,open]=applied.morphs.weights;
        crossfade=crossfade&&Math.abs(close+open)<1e-6;closed=Math.max(closed,close);
      }
      const unscaled=await play('unscaled');
      runtime.stop();
      [bones,shapes,facial].forEach(url=>URL.revokeObjectURL(url));
      return {blinks,repeat,low:Math.min(...samples),high:Math.max(...samples),seeded,peak,frames,stable,pausedOpen,
        ownNames:own.names,ownIdle:own===first,brokenMorphs,scaledNames:scaled.names,crossfade,closed,unscaled};
    }''')
    assert result['repeat'] and result['low'] == 0 and result['high'] == pytest.approx(1)
    assert 8 <= result['blinks'] <= 10
    assert result['seeded'] and result['stable'] and result['pausedOpen']
    assert result['peak'] == pytest.approx(1) and 5 <= result['frames'] <= 40
    assert result['ownNames'] == ['Smile'] and not result['ownIdle']
    assert result['brokenMorphs'] is None
    assert result['scaledNames'] == ['E_Close', 'E_Default'] and result['crossfade']
    assert result['closed'] == pytest.approx(1)
    # Mismatched scales drop the blink rather than playing a partial one.
    assert result['unscaled'] is None


def test_secondary_motion_simulates_only_listed_secondary_bones(module_page):
    result = module_page.evaluate('''async () => {
      const T = await import('three');
      const {sampleLocalPose} = await import('./js/weight-rig/rig-animation.js');
      const {createSecondaryMotion,secondaryBoneMask} = await import('./js/weight-rig/secondary-motion.js');
      const bone = (name,parent,translation=[0,0,0]) => ({name,parent,translation,rotation:[0,0,0,1],scale:[1,1,1]});
      const rig = {id:'rig01',bones:[
        bone('Root',-1),bone('Rig/Chain_A_01',0),bone('Rig/Chain_A_02',1,[-.1,0,0]),
        bone('Rig/Strand_B_01',0),bone('Rig/Spine',0),
        bone('Rig/Helper_01',0),bone('Rig/Chain_C_01',0),
      ]};
      const count = rig.bones.length;
      // The root slides along +Y while the unlisted Strand bone turns. The clip
      // lists Chain_A and Chain_C (and an out-of-range index) as secondary bones;
      // the unlisted Strand, Spine and Helper keep their authored pose.
      const frame = (y,angle) => rig.bones.flatMap((item,index) => [
        item.translation[0], index === 0 ? y : item.translation[1], item.translation[2],
        0, 0, index === 3 ? Math.sin(angle / 2) : 0, index === 3 ? Math.cos(angle / 2) : 1,
        1, 1, 1,
      ]);
      const clip = {skeleton_id:'rig01',layout:'frame-major-tqs-f32le',version:2,duration:1,frame_count:2,bone_count:count,
        secondary_bones:[1,2,6,6,99]};
      const values = new Float32Array([...frame(0,0), ...frame(4,1)]);
      const mask = secondaryBoneMask(rig, clip);
      const bare = {...clip};
      delete bare.secondary_bones;
      const unlisted = secondaryBoneMask(rig, bare);
      const motion = createSecondaryMotion();
      const locals = new Float64Array(count * 10);
      const pose = (time, elapsed) => motion.apply(rig, sampleLocalPose(rig, clip, values, time, locals), mask, elapsed);
      const rotation = (index) => Array.from(locals.slice(index * 10 + 3, index * 10 + 7));
      const identity = new T.Quaternion();
      pose(0, 0);
      const rest = {skirt:rotation(1), tip:rotation(2)};
      let peak = 0;
      for (let step = 1; step <= 30; step++) {
        pose(step / 60, 1 / 60);
        for (const index of [1, 2]) peak = Math.max(peak, new T.Quaternion(...rotation(index)).angleTo(identity));
      }
      const moved = {hair:rotation(3), spine:rotation(4)};
      sampleLocalPose(rig, clip, values, 30 / 60, locals);
      const authoredHair = Array.from(locals.slice(33, 37));
      motion.reset();
      pose(30 / 60, 0);
      const reseeded = {skirt:rotation(1), tip:rotation(2)};
      // A stalled frame clamps its step instead of exploding.
      pose(30 / 60, 1000);
      const clamped = Array.from(locals).every(Number.isFinite);
      return {mask:Array.from(mask), unlisted, rest, peak, moved, authoredHair, reseeded, clamped};
    }''')
    assert result['mask'] == [0, 1, 1, 0, 0, 0, 1]
    assert result['unlisted'] is None
    assert result['rest']['skirt'] == pytest.approx([0, 0, 0, 1])
    # The chain lags the sliding body, within the 35 degree per-joint bend limit.
    assert 0.02 < result['peak'] <= math.radians(35) + 1e-6
    # Bones the clip does not list keep their authored pose.
    assert result['moved']['hair'] == pytest.approx(result['authoredHair'])
    assert result['moved']['spine'] == pytest.approx([0, 0, 0, 1])
    assert result['reseeded']['skirt'] == pytest.approx([0, 0, 0, 1], abs=1e-9)
    assert result['reseeded']['tip'] == pytest.approx([0, 0, 0, 1], abs=1e-9)
    assert result['clamped']


def test_secondary_motion_hangs_under_gravity_and_clears_leg_capsules(module_page):
    result = module_page.evaluate('''async () => {
      const T = await import('three');
      const {sampleLocalPose} = await import('./js/weight-rig/rig-animation.js');
      const {createSecondaryMotion,secondaryBoneMask} = await import('./js/weight-rig/secondary-motion.js');
      const bone = (name,parent,translation) => ({name,parent,translation,rotation:[0,0,0,1],scale:[1,1,1]});
      // A Z-up humanoid; the authored head and feet give its up axis and the
      // hips, knees and feet its leg capsules. The skirt hangs in front.
      const rig = {id:'rig02',bones:[
        bone('Root',-1,[0,0,0]),bone('Rig/Pelvis',0,[0,0,1]),bone('Rig/Spine',1,[0,0,.1]),bone('Rig/Head',2,[0,0,.6]),
        bone('Rig/L_Thigh',1,[.1,0,0]),bone('Rig/L_Calf',4,[0,0,-.45]),bone('Rig/L_Foot',5,[0,0,-.45]),
        bone('Rig/R_Thigh',1,[-.1,0,0]),bone('Rig/R_Calf',7,[0,0,-.45]),bone('Rig/R_Foot',8,[0,0,-.45]),
        bone('Rig/Chain_A_01',2,[.1,-.12,-.05]),bone('Rig/Chain_A_02',10,[0,0,-.15]),
      ],humanoid_controls:{pelvis:1,head:3,leftHip:4,leftKnee:5,leftFoot:6,rightHip:7,rightKnee:8,rightFoot:9}};
      const count = rig.bones.length;
      const turn = (axis,angle) => new T.Quaternion().setFromAxisAngle(new T.Vector3(...axis),angle).toArray();
      // A two-frame clip; each argument maps bone index to its local quaternion.
      const clip = (first, last) => {
        const frame = (rotations) => rig.bones.flatMap((item,index) => [...item.translation, ...(rotations[index] || [0,0,0,1]), 1,1,1]);
        return {clip:{skeleton_id:'rig02',layout:'frame-major-tqs-f32le',version:2,duration:1,frame_count:2,bone_count:count,
                  secondary_bones:[10,11]},
                values:new Float32Array([...frame(first), ...frame(last)])};
      };
      const worlds = (locals) => {
        const out = [];
        rig.bones.forEach((item,index) => {
          const m = new T.Matrix4().compose(new T.Vector3().fromArray(locals,index*10),
            new T.Quaternion().fromArray(locals,index*10+3),new T.Vector3(1,1,1));
          out.push(item.parent >= 0 ? m.premultiply(out[item.parent]) : m);
        });
        return out;
      };
      // Skirt points: the first link's tail (the chain-end bone's head, its
      // only particle) and the far end of that rigidly following end bone.
      const particles = (locals) => {
        const w = worlds(locals);
        return [new T.Vector3().setFromMatrixPosition(w[11]), new T.Vector3(0,0,-.15).applyMatrix4(w[11])];
      };
      // Start at the clip's first frame, then play its last frame for 2 s.
      const run = ({clip,values}) => {
        const mask = secondaryBoneMask(rig, clip);
        const motion = createSecondaryMotion();
        const locals = new Float64Array(count * 10);
        motion.apply(rig, sampleLocalPose(rig, clip, values, 0, locals), mask, 0);
        const animated = particles(sampleLocalPose(rig, clip, values, 1, locals));
        for (let step = 1; step <= 120; step++) motion.apply(rig, sampleLocalPose(rig, clip, values, 1, locals), mask, 1 / 60);
        return {mask:Array.from(mask), animated, simulated:particles(locals), locals:Array.from(locals)};
      };
      const pitched = {1:turn([1,0,0],Math.PI / 2)};
      // A pose held from the start never moves on its own: the chain's
      // authored hang there is its rest, whatever its orientation.
      const held = run(clip(pitched, pitched));
      // The body pitching forward onto its front during playback: the clip
      // keeps the skirt along the torso, gravity pulls its tip back down.
      const leaning = run(clip({}, pitched));
      // The left thigh kicking forward into the skirt.
      const kicked = run(clip({}, {4:turn([1,0,0],-Math.PI / 3)}));
      const w = worlds(kicked.locals);
      const hip = new T.Vector3().setFromMatrixPosition(w[4]);
      const knee = new T.Vector3().setFromMatrixPosition(w[5]);
      const line = new T.Line3(hip, knee);
      const clearance = (point) => line.closestPointToPoint(point, true, new T.Vector3()).distanceTo(point);
      return {
        mask:leaning.mask,
        still:Math.max(...held.animated.map((point, index) => point.distanceTo(held.simulated[index]))),
        drop:leaning.animated[1].z - leaning.simulated[1].z,
        before:clearance(kicked.animated[0]), after:clearance(kicked.simulated[0]), radius:.45 * .17,
        finite:[...held.locals, ...leaning.locals, ...kicked.locals].every(Number.isFinite),
      };
    }''')
    assert result['mask'] == [0] * 10 + [1, 1]
    assert result['still'] < 1e-3
    assert result['drop'] > 0.01
    # The clip puts the skirt deep inside the kicking thigh; soft contact
    # pushes it most of the way out (within the joint bend limit).
    assert result['before'] < result['radius'] - 0.04
    assert result['after'] > result['before'] + 0.01
    assert result['finite']

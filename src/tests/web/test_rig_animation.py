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
    weights['game_rigs'] = {'rig01': dict(id='rig01', version=2, bones=bones, animation_session='session01',
        animations=[dict(id=clip, name=clip, duration=1) for clip in ['clip01', 'clip02']])}
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
    page.wait_for_function("document.querySelector('#animation-clip').value==='clip01'")
    page.wait_for_function("!document.querySelector('#animation-play-pause').disabled")
    assert page.locator('#animation-playback').is_visible()
    assert page.evaluate('''async () => {
      const {getLoadedWeightRigFeature}=await import('./js/weight-rig/weight-rig-feature.js');
      window.__autoRig=getLoadedWeightRigFeature();
      return window.__autoRig.getModelRigState().loaded && window.__autoRig.getRigAnimationState().playing;
    }''')
    revision = page.evaluate('window.__autoRig.getModelRigState().structureRevision')
    page.locator('#animation-next').click()
    page.wait_for_function("window.__autoRig.getRigAnimationState().selected==='clip02' && window.__autoRig.getRigAnimationState().playing")
    page.locator('#animation-next').click()
    page.wait_for_function("window.__autoRig.getRigAnimationState().selected==='clip01' && window.__autoRig.getRigAnimationState().playing")
    page.locator('#animation-previous').click()
    page.wait_for_function("window.__autoRig.getRigAnimationState().selected==='clip02' && window.__autoRig.getRigAnimationState().playing")
    page.locator('#animation-play-pause').click()
    assert page.evaluate('!window.__autoRig.getRigAnimationState().playing')
    page.locator('#animation-frame').evaluate("node=>{node.value='0.5';node.dispatchEvent(new Event('input'));}")
    assert page.evaluate('window.__autoRig.getRigAnimationState().time') == .5
    page.locator('#animation-clip').select_option('clip01')
    page.wait_for_function('window.__autoRig.getRigAnimationState().playing')
    assert page.evaluate('window.__autoRig.getModelRigState().structureRevision') == revision
    page.locator('#animation-clip').select_option('')
    assert page.evaluate('!window.__autoRig.getRigAnimationState().selected')
    page.locator('#animation-play-pause').click()
    page.wait_for_function('window.__autoRig.getRigAnimationState().playing')
    open_model(page, 'model02')
    wait_loaded(page)
    page.wait_for_function('!window.__autoRig.isWeightRigActivating()')
    assert page.locator('#animation-playback').is_hidden()
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


@pytest.mark.parametrize('case,expected', [
    ('partial', [0]), ('same_skeleton', [0, 1]), ('detached_skeleton', [0, 1]), ('invalid_palette', [0]),
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
      if(variant==='different_skeleton')second.gameRig={...rig,id:'rig02',animations:[]};
      if(variant==='competing_libraries')second.gameRig={...rig,id:'rig02'};
      if(variant==='invalid_skeleton')rig.bones=[{...bones[0],parent:0}];
      if(variant==='no_library')rig.animations=[];
      const states=new Map([[meshes[0],first],[meshes[1],second]]);
      let serializations=0;bones.toJSON=()=>{serializations++;return bones;};
      const binding=buildAnimationMeshBinding(meshes,states);
      return {included:binding?[...binding.meshes.keys()].map(mesh=>meshes.indexOf(mesh)):null,
        serializations,
        manualRig:variant==='partial'?buildGameRigReconciliation([first,second]):null};
    }''', case)
    assert result['included'] == expected
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

import pytest
import struct

from app.runtime import server
from .payloads import model_payload, weighted_payload
from .support import bridge_calls, open_model, wait_loaded


def test_animation_interpolation_playback_and_stale_selection_lifecycle(module_page):
    result = module_page.evaluate('''async () => {
      const T = await import('three');
      const {sampleAnimation,createRigAnimation} = await import('./js/weight-rig/rig-animation.js');
      const rig = {id:'rig01',bones:[
        {name:'Root',parent:-1,translation:[0,0,0],rotation:[0,0,0,1]},
        {name:'Joint01',parent:0,translation:[1,0,0],rotation:[0,0,0,1]},
      ],animations:[{id:'clip01',name:'Clip 01',duration:1}],animation_session:'session01'};
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
      runtime.setPlaying(false);runtime.seek(.5);
      const paused=runtime.snapshot();
      const posed=latest instanceof Map;
      runtime.stop();
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
      return {point,endpoint,stale,started,paused,posed,stopped,recovered,translatedError};
    }''')
    assert result['point'] == pytest.approx([1, 2, 0], abs=1e-6)
    assert result['endpoint'] == pytest.approx([0, 0, 0], abs=1e-6)
    assert result['stale'] and result['started'] and result['posed'] and result['stopped'] and result['recovered']
    assert result['translatedError']
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
      return {before,live,finalized,cleared:[...states.values()].every(state=>!state.poseTransforms&&!state.poseActiveVertices.length)};
    }''')
    assert result == {'before': 0, 'live': {'prepared': 1, 'applied': 2, 'reused': True}, 'finalized': 2, 'cleared': True}


@pytest.mark.parametrize('kind', ['asset', 'mod'])
@pytest.mark.parametrize('partial', [False, True])
def test_animation_panel_and_weight_rig_model_lifecycle(viewer, kind, partial):
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
    weights['game_rigs'] = {'rig01': dict(id='rig01', bones=bones, animation_session='session01',
        animations=[dict(id='clip01', name='Clip 01', duration=1)])}
    weights['meshes']['mesh-00'].update(game_rig_id='rig01', game_bone_map={'0': 0, '1': 1})
    if partial:
        weights['game_rigs']['rig02'] = dict(id='rig02', bones=bones, animations=[])
        weights['meshes']['mesh-02'].update(game_rig_id='rig02', game_bone_map={'4': 0, '5': 1})
    if kind == 'asset':
        payload['metadata'].update(source_kind='asset', asset=dict(rig_session='asset-session', path='Asset01'))
    data = struct.pack('<28f', *([0, 0, 0, 0, 0, 0, 1, 1, 0, 0, 0, 0, 0, 1]
                              + [2, 0, 0, 0, 0, 0, 1, 1, 0, 0, 0, 0, 0, 1]))
    clip = dict(status='ok', clip=dict(id='clip01', name='Clip 01', skeleton_id='rig01',
        frame_count=2, bone_count=2, duration=1, data=dict(length=len(data))))
    page = viewer({'model01': payload, 'model02': model_payload(), 'fixture-weights': weights})
    def load_clip(_source):
        clip['clip']['data']['url'] = server.publish_geometry(data, replace=False)
        return clip
    page.expose_binding('__animationClip', load_clip)
    page.evaluate('''() => {
      window.pywebview.api.get_asset_skinning_preview = () => window.__fixtureLoad('fixture-weights');
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
    page.locator('#weight-rig-tab').click()
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
    assert seek_report['values'] == pytest.approx([v + (1 if i % 3 == 0 else 0)
        for i,v in enumerate(seek_report['baseline'])], abs=1e-5), seek_report
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

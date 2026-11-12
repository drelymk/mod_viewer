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
      const runtime = createRigAnimation({getRig:()=>rig,
        loadClip:()=>new Promise(r=>{resolve=r;}),applyPose:p=>{latest=p;},
        suspendPhysics:v=>{suspended=v;},prepare:()=>{prepared++;},notify:()=>{}});
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
      URL.revokeObjectURL(url);
      return {point,endpoint,stale,started,paused,posed,stopped};
    }''')
    assert result['point'] == pytest.approx([1, 2, 0], abs=1e-6)
    assert result['endpoint'] == pytest.approx([0, 0, 0], abs=1e-6)
    assert result['stale'] and result['started'] and result['posed'] and result['stopped']
    assert not result['paused']['playing'] and result['paused']['time'] == .5


@pytest.mark.parametrize('kind', ['asset', 'mod'])
def test_animation_panel_and_weight_rig_model_lifecycle(viewer, kind):
    payload, weights = weighted_payload()
    bones = [dict(name='Root', parent=-1, translation=[0, 0, 0], rotation=[0, 0, 0, 1]),
             dict(name='Joint01', parent=0, translation=[1, 0, 0], rotation=[0, 0, 0, 1])]
    weights['game_rigs'] = {'rig01': dict(id='rig01', bones=bones, animation_session='session01',
        animations=[dict(id='clip01', name='Clip 01', duration=1)])}
    weights['meshes']['mesh-00'].update(game_rig_id='rig01', game_bone_map={'0': 0, '1': 1})
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
      window.pywebview.api.get_asset_animation = () => window.__animationClip();
      window.pywebview.api.get_model_animation = () => window.__animationClip();
    }''')
    open_model(page, 'model01')
    wait_loaded(page)
    assert bridge_calls(page, 'weights') == []
    page.locator('#weight-rig-tab').click()
    page.wait_for_function("document.querySelectorAll('.rig-animation-select option').length === 2")
    assert page.locator('.rig-bone-select option').all_text_contents()[1:] == ['Root', 'Joint01']
    assert page.evaluate('''async () => {
      const {weightRigApi:f} = await import('./js/weight-rig/weight-rig-core.js');
      window.__animationApi = f;
      window.__animationBaseline = [...window.modViewer.activeMeshes[0].geometry.attributes.position.array];
      const source = f.getModelWeightState().sources[0];
      f.setBoneSelected(source.key, 1, true);
      return f.getModelRigState().loaded;
    }''')
    assert page.locator('.rig-save-preset').is_disabled() == (kind == 'asset')
    page.locator('.rig-animation-select').select_option('clip01')
    page.wait_for_function('window.__animationApi.getRigAnimationState().playing')
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
    assert seek_report['minimum'] == pytest.approx(1), seek_report
    assert seek_report['values'] == pytest.approx([v + (1 if i % 3 == 0 else 0)
        for i,v in enumerate(seek_report['baseline'])], abs=1e-5), seek_report
    page.locator('.rig-animation-stop').click()
    assert page.evaluate('''() => window.modViewer.activeMeshes[0].geometry.attributes.position.array
      .every((v,i)=>Object.is(v,window.__animationBaseline[i]))''')
    page.locator('.rig-animation-select').select_option('clip01')
    page.wait_for_function('window.__animationApi.getRigAnimationState().playing')
    page.evaluate('window.__animationApi.setRigJointRotation(1,[0,0,0,1])')
    assert page.evaluate('!window.__animationApi.getRigAnimationState().selected')
    page.locator('.rig-animation-select').select_option('clip01')
    page.wait_for_function('window.__animationApi.getRigAnimationState().playing')
    open_model(page, 'model02')
    wait_loaded(page)
    assert page.evaluate('''() => {
      const state = window.__animationApi.getRigAnimationState();
      return !state.playing && !state.loading && !state.selected && state.clips.length===0;
    }''')

"""Blender-compatible snapshots preserve the live draw without changing it."""

import base64
import json
import struct

import pytest

from .payloads import append_stream, model_payload, textured_payload
from .support import bridge_calls, open_model, wait_loaded
from tests.support.dds_data import uncompressed_dds


def test_glb_textured_animation_and_loose_part_lifecycle(viewer, tmp_path):
    payload = textured_payload()
    mesh = payload['meshes']['mesh-00']
    positions = [0,0,0, 1,0,0, 0,1,0, 2,0,0, 3,0,0, 2,1,0]
    animated = [value + (0.5 if index % 3 == 2 else 0) for index, value in enumerate(positions)]
    mesh.update(pos=append_stream(payload, 'f', positions),
                idx=append_stream(payload, 'I', [0,1,2,3,4,5]),
                uv=append_stream(payload, 'f', [0,0,1,0,0,1] * 2), animation_id='clock-01',
                animation_geometry={'frames': 2, 'frame_start': 0, 'position_frame_bytes': 72,
                                    'positions': append_stream(payload, 'f', positions + animated)})
    payload['animations'] = {'clock-01': {'frame_start': 0, 'frame_end': 1, 'fps': 30}}
    page = viewer({'fixture-01': payload})
    open_model(page, 'fixture-01')
    wait_loaded(page)
    page.evaluate("""async () => {
      const {setAnimationFrame} = await import('./js/mesh/animation-runtime.js');
      window.__setFrame = setAnimationFrame;
      setAnimationFrame(0);
      window.__exportSnapshot = async () => {
        const {snapshotModel,encodeModelGlb} = await import('./js/export/model-glb.js');
        const {getTexturePreviewSource} = await import('./js/mesh/mesh-factory.js');
        const snapshot = snapshotModel(window.modViewer.activeMeshes,getTexturePreviewSource);
        try {
          const result = await encodeModelGlb(snapshot, {loadImage:async url => {
            const image = new Image(); image.src=url; await image.decode(); return image;
          }});
          return await new Promise(resolve => {
            const reader = new FileReader(); reader.onload=()=>resolve(reader.result.split(',')[1]);
            reader.readAsDataURL(new Blob([result.buffer]));
          });
        } finally {snapshot.dispose();}
      };
    }""")
    for name, frame, parts in [('textured', 0, 1), ('animated', 1, 1), ('loose-parts', 1, 2)]:
        if name == 'loose-parts':
            page.evaluate("""async () => {
              const {separateLooseParts} = await import('./js/mesh/loose-parts.js');
              separateLooseParts(window.modViewer.activeMeshes[0]);
            }""")
        page.evaluate('frame => window.__setFrame(frame)', frame)
        assert page.evaluate('window.modViewer.activeMeshes[0].geometry.attributes.position.getZ(0)') == (0.5 if frame else 0)
        data = base64.b64decode(page.evaluate('window.__exportSnapshot()'))
        (tmp_path / f'{name}.glb').write_bytes(data)
        json_length = struct.unpack_from('<I', data, 12)[0]
        document = json.loads(data[20:20 + json_length])
        assert len(document['meshes']) == parts
        assert len(document['images']) == 1
        for exported in document['meshes']:
            attributes = exported['primitives'][0]['attributes']
            assert set(attributes) == {'POSITION', 'NORMAL', 'TEXCOORD_0'}
            accessor = document['accessors'][attributes['POSITION']]
            view = document['bufferViews'][accessor['bufferView']]
            values = struct.unpack_from(f'<{accessor["count"] * 3}f', data,
                                        28 + json_length + view['byteOffset'] + accessor.get('byteOffset', 0))
            assert values[2::3] == (0.5 if frame else 0,) * accessor['count']


def test_switch_during_glb_destination_selection_cancels_stale_save(viewer):
    page = viewer({'fixture-01': model_payload(), 'fixture-02': model_payload(2)})
    open_model(page, 'fixture-01')
    wait_loaded(page)
    page.evaluate("""() => {
      window.__glbSaveCalls = [];
      window.pywebview.api.export_model_glb = async (path,data) => {
        window.__glbSaveCalls.push({path,action:data.action});
        if(data.action==='begin')return new Promise(resolve=>{window.__releaseBegin=()=>resolve({token:'session-01'});});
        return {cancelled:true};
      };
    }""")
    page.locator('#export-menu-btn').click()
    page.locator('#export-glb-choice').click()
    page.wait_for_function('window.__releaseBegin !== undefined')
    assert page.evaluate('window.modViewer.switchMod("fixture-02")')
    wait_loaded(page, 2)
    page.evaluate('window.__releaseBegin()')
    page.wait_for_function('!document.querySelector("#export-glb-choice").disabled')
    assert page.evaluate('window.__glbSaveCalls') == [
        {'path': 'fixture-01', 'action': 'begin'}, {'path': 'fixture-01', 'action': 'cancel'}]
    assert page.evaluate('window.modViewer.getCurrentSource().path') == 'fixture-02'
    assert page.locator('#dialog-backdrop').is_hidden()


def test_export_dds_full_resolution_orientation_and_renderer_state(viewer):
    page = viewer({})
    width, height = 2052, 4
    top, bottom = (96, 144, 192, 0), (192, 96, 48, 0)
    pixels = bytes(top) * (width * 2) + bytes(bottom) * (width * 2)
    page.route('**/export-image.dds', lambda route: route.fulfill(
        content_type='image/vnd-ms.dds', body=uncompressed_dds(pixels, width=width, height=height)))
    result = page.evaluate("""async () => {
      const {renderer,rendererReady} = await import('./js/scene/scene.js');
      await rendererReady;
      const state = () => [renderer.getRenderTarget()?.uuid || null, renderer.getClearAlpha(),
        renderer.getScissorTest(), renderer.outputColorSpace, renderer.toneMapping, renderer.autoClear];
      const before = state();
      const {exportDDSCanvas} = await import('./js/textures/dds-preview.js');
      const canvas = await exportDDSCanvas('/export-image.dds', () => true);
      const context = canvas.getContext('2d');
      return {width:canvas.width,height:canvas.height,before,after:state(),
        pixels:[0,3].map(y => [...context.getImageData(2000,y,1,1).data])};
    }""")
    assert (result['width'], result['height']) == (width, height)
    assert result['before'] == result['after']
    for actual, expected in zip(result['pixels'], [top, bottom]):
        assert actual[3] == 255
        assert max(abs(left - right) for left, right in zip(actual[:3], expected[:3])) <= 2


def test_export_dropdown_snapshot_cancel_retry_and_narrow_toolbar(viewer):
    page = viewer({'fixture-01': model_payload()}, pending={'fixture-01': True})
    assert page.locator('#export-menu-btn').is_disabled()
    page.evaluate("""() => {
      window.__glbSaves = [];
      window.__glbResult = {cancelled:true};
      window.pywebview.api.export_model_glb = async (path,data) => {
        if(data.action==='begin'){
          window.__glbSaves.push({path,chunks:[]});
          return window.__glbResult.cancelled?{cancelled:true}:{token:'session-01'};
        }
        if(data.action==='append'){
          window.__glbSaves.at(-1).chunks.push(data.chunk);return {ok:true};
        }
        return window.__glbResult;
      };
    }""")
    open_model(page, 'fixture-01')
    wait_loaded(page)
    page.locator('#export-menu-btn').click()
    assert page.locator('#export-menu').is_visible()
    page.keyboard.press('Escape')
    assert page.locator('#export-menu').is_hidden()
    page.locator('#export-menu-btn').click()
    page.locator('#export-glb-choice').click()
    page.wait_for_function('window.__glbSaves.length === 1 && !document.querySelector("#loading").classList.contains("show")')
    assert page.evaluate('window.__bridge.pending["fixture-01"]')
    assert bridge_calls(page, 'export') == []
    assert page.locator('#pending-indicator.show').is_visible()
    assert page.locator('#export-btn').is_enabled()

    # GLB export stays available with no pending INI edits, including overflow.
    page.locator('#export-btn').click()
    page.wait_for_function('!window.__bridge.pending["fixture-01"]')
    page.wait_for_function('document.querySelector("#export-btn").disabled')
    assert page.locator('#export-menu-btn').is_enabled()
    page.set_viewport_size({'width': 640, 'height': 800})
    page.locator('#toolbar-more').click()
    page.evaluate('window.__glbResult = {error:"fixture failure"}')
    page.locator('#toolbar-overflow [data-toolbar-target="export-glb-choice"]').click()
    page.locator('#dialog-backdrop.show').wait_for()
    assert 'fixture failure' in page.locator('#dialog-message').inner_text()
    page.locator('#dialog-ok').click()
    page.wait_for_function('!document.querySelector("#loading").classList.contains("show")')
    page.evaluate('window.__glbResult = {ok:true,name:"model-01.glb"}')
    page.locator('#toolbar-more').click()
    page.locator('#toolbar-overflow [data-toolbar-target="export-glb-choice"]').click()
    page.locator('#dialog-backdrop.show').wait_for()
    assert 'model-01.glb' in page.locator('#dialog-message').inner_text()
    page.locator('#dialog-ok').click()
    page.wait_for_function('!document.querySelector("#loading").classList.contains("show")')
    assert page.evaluate('window.__glbSaves.length') == 3
    assert bridge_calls(page, 'export') == [['fixture-01']]
    assert bridge_calls(page, 'discard') == []
    assert page.evaluate("""() => {
      const saved = window.__glbSaves[2];
      const bytes = Uint8Array.from(atob(saved.chunks[0]), c => c.charCodeAt(0));
      return saved.path === 'fixture-01' && new DataView(bytes.buffer).getUint32(0,true) === 0x46546c67;
    }""")

    page.set_viewport_size({'width':1280,'height':900})
    page.locator('#export-menu-btn').click()
    page.locator('#canvas-container').click(position={'x':400,'y':300})
    assert page.locator('#export-menu').is_hidden()
    page.evaluate('document.body.classList.add("feature-export-off")')
    assert page.locator('#export-control').is_hidden()
    page.set_viewport_size({'width':640,'height':800})
    page.locator('#toolbar-more').click()
    assert page.locator('#toolbar-overflow [data-toolbar-target="export-glb-choice"]').is_hidden()


def test_glb_snapshot_excludes_debug_attributes_and_keeps_texture_identity(module_page):
    result = module_page.evaluate("""async () => {
      const T = await import('three');
      const {snapshotModel, encodeModelGlb} = await import('./js/export/model-glb.js');
      const geometry = new T.BufferGeometry();
      geometry.setAttribute('position', new T.Float32BufferAttribute(
        [99,99,99, 0,0,0, 1,0,0, 0,1,0], 3));
      geometry.setAttribute('normal', new T.Float32BufferAttribute(
        [1,0,0, 0,0,-1, 0,0,-1, 0,0,-1], 3));
      geometry.setAttribute('uv', new T.Float32BufferAttribute([0,0, 0,0, 2,0, 0,2], 2));
      geometry.setAttribute('color', new T.Float32BufferAttribute(
        [1,1,1, 1,0,0, 0,1,0, 0,0,1], 3));
      geometry.setIndex([0,1,2, 1,2,3]);
      geometry.setDrawRange(3,3);
      const mesh = new T.Mesh(geometry, new T.MeshStandardMaterial());
      mesh.name = 'draw-01';
      mesh.position.set(4,5,6);
      mesh.userData = {identity: {key: 'source-01::draw-01'}, displayName: 'Mesh 01',
        texKey: 'diffuse::folder-01/image.png', normalDataKey: 'normal_data::folder-01/image.png',
        normalMapKey: 'normal_map::folder-01/image.png', materialMapKey: 'material_map::folder-01/image.png',
        emissionMapKey: 'emission_map::folder-01/image.png',
        materialProfile: {id:'profile-01',emission_source:'emission_map_rgb',emission_strength:0.5},
        lightMapKey: 'light_map::folder-02/image.png', colorAdjustment: {hue: 30},
        sources: [{ini:'source-01.ini', line: 12}]};
      const hidden = mesh.clone(); hidden.visible = false;
      const snapshot = snapshotModel([mesh, hidden], key => key ? '/' + key.split('::')[1] : null);
      // Animation and viewer edits after the snapshot must not change its contents.
      geometry.attributes.position.setXYZ(1, 8,8,8);
      mesh.position.set(9,9,9);
      mesh.userData.colorAdjustment.hue = 90;
      const requests = [];
      const canvas = document.createElement('canvas'); canvas.width = canvas.height = 4;
      canvas.getContext('2d').fillRect(0,0,4,4);
      let released = 0;
      const output = await encodeModelGlb(snapshot, {
        releaseImage:image => {released++; image.width=image.height=0;},
        loadImage: async url => { requests.push(url); return canvas; }
      });
      const view = new DataView(output.buffer);
      const jsonLength = view.getUint32(12,true);
      const doc = JSON.parse(new TextDecoder().decode(new Uint8Array(output.buffer,20,jsonLength)));
      const binaryOffset = 20 + jsonLength + 8;
      const attribute = name => {
        const accessor = doc.accessors[doc.meshes[0].primitives[0].attributes[name]];
        const bv = doc.bufferViews[accessor.bufferView];
        return Array.from(new Float32Array(output.buffer, binaryOffset + bv.byteOffset + (accessor.byteOffset || 0),
          accessor.count * {VEC2:2,VEC3:3,VEC4:4}[accessor.type]));
      };
      const extras = doc.materials[0].extras.modViewer;
      const referencedViews = new Set([
        ...doc.accessors.map(accessor => accessor.bufferView),
        ...doc.images.map(image => image.bufferView),
      ]);
      const first = {magic: view.getUint32(0,true), version:view.getUint32(4,true),
        size: view.getUint32(8,true) === output.buffer.byteLength, count: output.meshCount,
        positions:attribute('POSITION'), normals:attribute('NORMAL'), uvs:attribute('TEXCOORD_0'),
        attributes:Object.keys(doc.meshes[0].primitives[0].attributes),
        name:doc.nodes[0].name, matrix:doc.nodes[0].matrix,
        material:doc.materials[0], textureKeys:extras.textures, requests,
        noOriginals:referencedViews.size === doc.bufferViews.length, released,
        imageWidth:new DataView(output.buffer).getUint32(binaryOffset + doc.bufferViews[doc.images[0].bufferView].byteOffset + 16),
        embedded:doc.images.every(image => image.bufferView !== undefined && !image.uri),
        warnings:output.warnings, color:extras.colorAdjustment.hue};
      snapshot.dispose();
      // Loose parts replace their source draw and retain independent visibility/transforms.
      mesh.geometry.setDrawRange(0,0);
      const part = new T.Mesh(geometry.clone(),mesh.material);
      part.geometry.setDrawRange(3,3);
      part.position.set(1,0,0); mesh.add(part);
      const hiddenPart = part.clone(); hiddenPart.visible = false; mesh.add(hiddenPart);
      mesh.userData.looseParts = [part,hiddenPart];
      const parts = snapshotModel([mesh]);
      const second = {count:parts.scene.children.length, translation:parts.scene.children[0].matrix.elements.slice(12,15),
        sourceRange:mesh.geometry.drawRange.count};
      parts.dispose();
      return {first,second};
    }""")
    first = result['first']
    assert first['magic'] == 0x46546C67 and first['version'] == 2 and first['size']
    assert first['count'] == 1 and first['name'] == 'Mesh 01'
    assert first['positions'] == [0, 0, 0, 1, 0, 0, 0, 1, 0]
    assert first['normals'] == [0, 0, -1] * 3
    assert first['uvs'] == [0, 0, 2, 0, 0, 2]
    assert set(first['attributes']) == {'POSITION', 'NORMAL', 'TEXCOORD_0'}
    assert first['matrix'][12:15] == [4, 5, 6]
    assert first['color'] == 30
    assert first['requests'] == ['/folder-01/image.png']
    assert first['embedded'] and first['noOriginals'] and first['warnings'] == []
    assert first['released'] == 1 and first['imageWidth'] == 4
    assert first['textureKeys'] == {role: f'{role}::folder-{2 if role == "light_map" else 1:02d}/image.png'
                                    for role in ['diffuse', 'normal_map', 'normal_data', 'light_map', 'material_map', 'emission_map']}
    assert first['material']['pbrMetallicRoughness']['baseColorTexture']['index'] == 0
    assert len({first['material'][field]['index'] for field in ['normalTexture', 'emissiveTexture']} | {
        first['material']['pbrMetallicRoughness']['baseColorTexture']['index']}) == 3
    assert first['material']['emissiveFactor'] == [0.5] * 3
    assert first['material']['doubleSided']
    assert result['second'] == {'count': 1, 'translation': [10, 9, 9], 'sourceRange': 0}


def test_glb_missing_texture_is_cached_across_materials_and_roles(module_page):
    result = module_page.evaluate("""async () => {
      const T = await import('three');
      const {snapshotModel,encodeModelGlb} = await import('./js/export/model-glb.js');
      const mesh = new T.Mesh(new T.PlaneGeometry(),new T.MeshStandardMaterial());
      mesh.userData.texKey = 'diffuse::folder-01/image.png';
      mesh.userData.normalMapKey = 'normal_map::folder-01/image.png';
      const shared = mesh.clone();
      const distinct = mesh.clone();
      distinct.userData.texKey = 'diffuse::folder-02/image.png';
      distinct.userData.normalMapKey = null;
      const snapshot = snapshotModel([mesh,shared,distinct], key => key ? '/' + key.split('::')[1] : null);
      const requests = [];
      try {
        const result = await encodeModelGlb(snapshot, {
          loadImage:url => { requests.push(url); throw Error('fixture failure'); }
        });
        return {count:result.meshCount, warnings:result.warnings,requests};
      } finally { snapshot.dispose(); }
    }""")
    assert result == {'count': 3, 'warnings': ['diffuse::folder-01/image.png',
                      'normal_map::folder-01/image.png', 'diffuse::folder-02/image.png'],
                      'requests': ['/folder-01/image.png', '/folder-02/image.png']}


@pytest.mark.parametrize('failed_read', [1, 2])
def test_glb_packing_bypasses_both_upstream_binary_blob_reads(module_page, failed_read):
    result = module_page.evaluate("""async failedRead => {
      const T = await import('three');
      const {GLTFExporter} = await import('three/addons/exporters/GLTFExporter.js');
      const {snapshotModel,encodeModelGlb} = await import('./js/export/model-glb.js');
      const mesh = new T.Mesh(new T.PlaneGeometry(),new T.MeshStandardMaterial());
      mesh.name = 'Mesh \u03b1';
      mesh.userData.texKey = 'diffuse::image.png';
      const canvas = document.createElement('canvas');canvas.width=canvas.height=4;
      canvas.getContext('2d').fillRect(0,0,4,4);
      const snapshot = snapshotModel([mesh],()=>'/image.png');
      const options = {loadImage:async()=>canvas};
      await encodeModelGlb(snapshot,options);
      const normal = await new GLTFExporter().parseAsync(snapshot.scene,{binary:true});
      const read = FileReader.prototype.readAsArrayBuffer;
      let binaryReads = 0;
      let packed;
      let onError;
      try {
        FileReader.prototype.readAsArrayBuffer=function(blob){
          if(blob.type==='application/octet-stream' && ++binaryReads===failedRead){
            Object.defineProperty(this,'result',{value:null});
            queueMicrotask(()=>this.onloadend());
          }else read.call(this,blob);
        };
        const error = new Promise(resolve=>{
          onError=event=>{event.preventDefault();resolve({error:event.message});};
          window.addEventListener('error',onError);
        });
        packed = await Promise.race([encodeModelGlb(snapshot,options),error]);
      }finally{
        FileReader.prototype.readAsArrayBuffer=read;
        window.removeEventListener('error',onError);snapshot.dispose();
      }
      if(packed.error)return {error:packed.error,binaryReads};
      const left=new Uint8Array(normal),right=new Uint8Array(packed.buffer);
      return {binaryReads,sameGlb:left.length===right.length && left.every((value,index)=>value===right[index])};
    }""", failed_read)
    assert result == {'binaryReads': 0, 'sameGlb': True}


def test_glb_image_blob_read_failure_rejects_instead_of_hanging(module_page):
    result = module_page.evaluate("""async () => {
      const T = await import('three');
      const {snapshotModel,encodeModelGlb} = await import('./js/export/model-glb.js');
      const mesh = new T.Mesh(new T.PlaneGeometry(),new T.MeshStandardMaterial());
      mesh.userData.texKey='diffuse::image.png';
      const canvas=document.createElement('canvas');canvas.width=canvas.height=4;
      const snapshot=snapshotModel([mesh],()=>'/image.png');
      const read=Blob.prototype.arrayBuffer;
      try{
        Blob.prototype.arrayBuffer=function(){return Promise.reject(Error('fixture image read failure'));};
        await encodeModelGlb(snapshot,{loadImage:async()=>canvas});
        return 'unexpected success';
      }catch(error){return error.message;}
      finally{Blob.prototype.arrayBuffer=read;snapshot.dispose();}
    }""")
    assert result == 'fixture image read failure'


@pytest.mark.parametrize('size', [0, 1, 2, 3, 49151, 49152, 49153, 1024 * 1024])
def test_base64_preserves_padding_and_chunk_boundaries_without_blob_reads(module_page, size):
    result = module_page.evaluate("""async size => {
      const {base64Buffer} = await import('./js/export/base64-buffer.js');
      const bytes=new Uint8Array(size).map((_,index)=>(index*17)%256);
      const decoded=atob(base64Buffer(bytes.buffer));
      return {length:decoded.length,identical:bytes.every((value,index)=>value===decoded.charCodeAt(index))};
    }""", size)
    assert result == {'length': size, 'identical': True}


@pytest.mark.parametrize(('size', 'append_count'), [(317, 1), (2 * 1024 * 1024 + 37, 3)])
def test_all_glb_sizes_use_one_bounded_cancellable_save_lifecycle(module_page, size, append_count):
    result = module_page.evaluate("""async size => {
      const {saveGlbBuffer} = await import('./js/export/glb-save.js');
      const bytes=new Uint8Array(size).map((_,index)=>(index*17)%256);
      const run=async mode=>{
        const actions=[],decoded=[];let current=true,maxChunk=0;
        const api={export_model_glb:async(path,data)=>{
          if(path!=='fixture-01' || typeof data==='string')throw Error('unexpected request');
          actions.push(data.action);
          if(data.action==='begin')return mode==='picker-cancel'?{cancelled:true}:{token:'session-01'};
          if(data.token!=='session-01')throw Error('wrong session');
          if(data.action==='append'){
            maxChunk=Math.max(maxChunk,data.chunk.length);decoded.push(atob(data.chunk));
            if(mode==='stale')current=false;
            if(mode==='failure')return {error:'fixture failure'};
          }
          return {ok:true,name:'model.glb'};
        }};
        const result=await saveGlbBuffer(api,'fixture-01',bytes.buffer,()=>current);
        const combined=decoded.join('');
        return {actions,maxChunk,result,identical:combined.length===bytes.length &&
                bytes.every((value,index)=>value===combined.charCodeAt(index))};
      };
      return {success:await run('success'),cancelled:await run('picker-cancel'),
              stale:await run('stale'),failed:await run('failure')};
    }""", size)
    assert result['success']['actions'] == ['begin'] + ['append'] * append_count + ['finish']
    assert result['success']['maxChunk'] <= 1400000
    assert result['success']['identical']
    assert result['cancelled']['actions'] == ['begin']
    assert result['cancelled']['result'] == {'cancelled': True}
    assert result['stale']['actions'] == ['begin', 'append', 'cancel']
    assert result['stale']['result'] == {'cancelled': True}
    assert result['failed']['actions'] == ['begin', 'append', 'cancel']
    assert result['failed']['result'] == {'error': 'fixture failure'}

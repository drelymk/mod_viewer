"""Blender-compatible snapshots preserve the live draw without changing it."""

from .payloads import model_payload
from .support import bridge_calls, open_model, wait_loaded
from tests.support.dds_data import uncompressed_dds


def test_switch_during_glb_encoding_cancels_stale_native_save(viewer):
    page = viewer({'fixture-01': model_payload(), 'fixture-02': model_payload(2)})
    open_model(page, 'fixture-01')
    wait_loaded(page)
    page.evaluate("""() => {
      window.__glbSaveCalls = [];
      window.pywebview.api.export_model_glb = async (...args) => {window.__glbSaveCalls.push(args);return {cancelled:true};};
      const read = FileReader.prototype.readAsDataURL;
      FileReader.prototype.readAsDataURL = function(blob) {
        window.__releaseEncoding = () => read.call(this,blob);
      };
    }""")
    page.locator('#export-menu-btn').click()
    page.locator('#export-glb-choice').click()
    page.wait_for_function('window.__releaseEncoding !== undefined')
    assert page.evaluate('window.modViewer.switchMod("fixture-02")')
    wait_loaded(page, 2)
    page.evaluate('window.__releaseEncoding()')
    page.wait_for_function('!document.querySelector("#export-glb-choice").disabled')
    assert page.evaluate('window.__glbSaveCalls') == []
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
        window.__glbSaves.push({path,data});
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
      const bytes = Uint8Array.from(atob(saved.data), c => c.charCodeAt(0));
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


def test_glb_snapshot_geometry_materials_and_original_texture_identity(module_page):
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
      const snapshot = snapshotModel([mesh, hidden], key => key ? 'data:application/octet-stream,' + key : null);
      // Animation and viewer edits after the snapshot must not change its contents.
      geometry.attributes.position.setXYZ(1, 8,8,8);
      mesh.position.set(9,9,9);
      mesh.userData.colorAdjustment.hue = 90;
      const requests = [];
      const canvas = document.createElement('canvas'); canvas.width = canvas.height = 4;
      canvas.getContext('2d').fillRect(0,0,4,4);
      const output = await encodeModelGlb(snapshot, {
        loadImage: async () => canvas,
        loadBytes: async url => { requests.push(url); return new Uint8Array([1,2,3,4,5]).buffer; }
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
      const resources = Object.fromEntries(Object.entries(extras.textures).map(([role, info]) => {
        const bv = doc.bufferViews[info.bufferView];
        return [role, Array.from(new Uint8Array(output.buffer, binaryOffset + bv.byteOffset, info.byteLength))];
      }));
      const first = {magic: view.getUint32(0,true), version:view.getUint32(4,true),
        size: view.getUint32(8,true) === output.buffer.byteLength, count: output.meshCount,
        positions:attribute('POSITION'), normals:attribute('NORMAL'), uvs:attribute('TEXCOORD_0'),
        colors:attribute('COLOR_0'), name:doc.nodes[0].name, matrix:doc.nodes[0].matrix,
        material:doc.materials[0], resources, requests:requests.length,
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
    assert first['colors'] == [1, 0, 0, 0, 1, 0, 0, 0, 1]
    assert first['matrix'][12:15] == [4, 5, 6]
    assert first['color'] == 30
    assert first['requests'] == 6 and first['embedded'] and first['warnings'] == []
    assert first['resources'] == {role: [1, 2, 3, 4, 5] for role in [
        'diffuse', 'normal_map', 'normal_data', 'light_map', 'material_map', 'emission_map']}
    assert first['material']['pbrMetallicRoughness']['baseColorTexture']['index'] == 0
    assert len({first['material'][field]['index'] for field in ['normalTexture', 'emissiveTexture']} | {
        first['material']['pbrMetallicRoughness']['baseColorTexture']['index']}) == 3
    assert first['material']['emissiveFactor'] == [0.5] * 3
    assert first['material']['doubleSided']
    assert result['second'] == {'count': 1, 'translation': [10, 9, 9], 'sourceRange': 0}


def test_glb_missing_texture_is_reported_without_losing_geometry(module_page):
    result = module_page.evaluate("""async () => {
      const T = await import('three');
      const {snapshotModel,encodeModelGlb} = await import('./js/export/model-glb.js');
      const mesh = new T.Mesh(new T.PlaneGeometry(),new T.MeshStandardMaterial());
      mesh.userData.texKey = 'diffuse::image.png';
      const snapshot = snapshotModel([mesh], () => '/missing-image.png');
      try {
        const result = await encodeModelGlb(snapshot, {
          loadImage:async () => {throw Error('fixture failure');},
          loadBytes:async () => {throw Error('fixture failure');}
        });
        return {count:result.meshCount, warnings:result.warnings};
      } finally { snapshot.dispose(); }
    }""")
    assert result == {'count': 1, 'warnings': ['diffuse::image.png']}

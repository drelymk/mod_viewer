"""Real WebGPU output and control visibility with generated inputs."""

import base64
import io
import struct

import pytest
from PIL import Image, ImageChops
from app.assets.loader import AssetLoadResult, AssetMeshPart
from app.assets.loader.models import AssetTexture
from app.runtime import server
from core.geometry.transport import GeometryBlob
from core.materials.profiles import material_profile_for
from tests.support.dds_data import dx10_dds, mode6_block, uncompressed_dds

from .payloads import append_stream, model_payload, solid_texture, split_color_dds, textured_payload, weighted_payload
from .support import bridge_calls, mesh_pixel, mesh_pixels, open_model, project_mesh_points, wait_loaded, wait_texture


def _open_fixture(viewer, payload, extra=None):
    page = viewer({'fixture-01': payload, **(extra or {})})
    open_model(page, 'fixture-01')
    wait_loaded(page, len(payload['meshes']))
    return page


def _activate_rig(page, *, humanoid=False):
    if humanoid:
        page.evaluate("""() => {
          window.modViewer.activeMeshes[0].userData.humanoidRestPositions = new Float32Array([
            -1, 0, 0, 1, 0, 0, 0, 1, 0, 0, 2, 0, -1, 1.5, 0, 1, 1.5, 0,
          ]);
        }""")
    page.locator('#weight-rig-tab').click()
    page.evaluate("""async () => {
      const {weightRigApi} = await import('./js/weight-rig/weight-rig-core.js');
      window.__rigApi = weightRigApi;
    }""")
    page.wait_for_function('window.__rigApi.getModelRigState().loaded')


def test_environment_backgrounds_are_independent_of_model_geometry_and_clipping(viewer):
    page = viewer({'fixture-01': model_payload(), 'fixture-02': model_payload()})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.evaluate("""async () => {
      const s = await import('./js/scene/scene.js'); await s.rendererReady;
      window.__environmentScene = s;
      s.setEnvironmentPreset('studio');
    }""")
    page.wait_for_function('window.__environmentScene.scene.backgroundNode?.isNode')
    assert page.evaluate('window.modViewer.activeMeshes.length') == 0
    open_model(page, 'fixture-01')
    wait_loaded(page)
    backgrounds = []
    presets = ['studio', 'indoor', 'outdoor', 'sunset']
    for preset in presets:
        page.evaluate('id => window.modViewer.setEnvironmentPreset(id)', preset)
        page.wait_for_function('!window.__environmentScene.getEnvironmentDebugState().preparationInFlight')
        independent = page.evaluate("""async () => {
          const s = window.__environmentScene;
          const key = s.scene.children.find(light => light.isDirectionalLight && light.castShadow);
          const presetLights = [];
          s.scene.traverse(light => {
            if(light.isLight && light !== key) presetLights.push(light);
          });
          const snapshot = () => JSON.stringify({
            lights:presetLights.map(light => [light.uuid,light.visible,light.intensity,light.color.getHex()]),
            environment:s.scene.environment.uuid, intensity:s.scene.environmentIntensity,
            generations:s.getEnvironmentDebugState().totalPmremGenerationCount,
          });
          const original = snapshot(), position = key.position.clone();
          s.setKeyLightIntensity(0);
          const disabled = key.intensity === 0 && snapshot() === original;
          s.setKeyLightIntensity(1);
          return disabled && key.intensity === 1 && key.position.equals(position) && snapshot() === original;
        }""")
        assert independent
        colors = []
        for mode in ['direct', 'ao', 'bloom', 'ao-bloom']:
            previous = page.evaluate('window.modViewer.getRenderCount()')
            page.evaluate("""async mode => {
              const s = await import('./js/scene/scene.js');
              s.setAmbientOcclusionStrength(mode.includes('ao') ? 0.5 : 0);
              s.setBloomAvailable(true); s.setBloomEnabled(mode.includes('bloom'));
              const {requestRender} = await import('./js/scene/render-scheduler.js'); requestRender();
            }""", mode)
            page.wait_for_function('count => window.modViewer.getRenderCount() > count', arg=previous)
            with Image.open(io.BytesIO(page.screenshot())) as image:
                colors.append(image.convert('RGB').getpixel((700, 250)))
        assert all(max(abs(a-b) for a, b in zip(colors[0], color)) <= 2 for color in colors[1:])
        backgrounds.append(colors[0])
    assert len(set(backgrounds)) == len(presets)
    for preset, expected_color in zip(presets, backgrounds):
        result = page.evaluate("""async preset => {
          const s = await import('./js/scene/scene.js');
          const {Raycaster, Vector2} = await import('three/webgpu');
          const children = [...s.scene.children];
          s.setEnvironmentPreset(preset);
          const background = s.scene.backgroundNode;
          const initial = {near:s.camera.near, far:s.camera.far, position:s.camera.position.clone()};
          s.camera.near = 0.00001; s.camera.far = 0.01; s.camera.updateProjectionMatrix();
          s.camera.position.addScalar(100);
          s.renderer.render(s.scene, s.camera);
          await s.renderer.backend.device.queue.onSubmittedWorkDone();
          const clippedFrame = s.renderer.domElement.toDataURL();
          const rect = s.renderer.domElement.getBoundingClientRect();
          const isolated = s.scene.children.length === children.length && children.every(c => s.scene.children.includes(c));
          const raycaster = new Raycaster(); raycaster.setFromCamera(new Vector2(), s.camera);
          const picking = raycaster.intersectObjects(s.scene.children, true).every(hit => !hit.object.isSkyMesh);
          s.camera.near = initial.near; s.camera.far = initial.far; s.camera.updateProjectionMatrix();
          s.camera.position.copy(initial.position);
          s.setAmbientOcclusionStrength(0); s.setBloomEnabled(false);
          s.setEnvironmentPreset('default');
          return {procedural:background.isNode, isolated, picking, fog:s.scene.fog,
            clippedFrame, origin:[rect.left,rect.top],
            generations:s.getEnvironmentDebugState().totalPmremGenerationCount,
            restored:s.scene.background.isColor && !s.scene.backgroundNode, active:s.getEnvironmentDebugState().environmentActive};
        }""", preset)
        clipped_frame = result.pop('clippedFrame').split(',', 1)[1]
        left, top = result.pop('origin')
        with Image.open(io.BytesIO(base64.b64decode(clipped_frame))) as image:
            clipped_color = image.convert('RGB').getpixel((int(700-left), int(250-top)))
        assert max(abs(a-b) for a, b in zip(expected_color, clipped_color)) <= 2, (preset, expected_color, clipped_color)
        assert result == {'procedural': True, 'isolated': True, 'picking': True, 'fog': None,
                          'generations': 4, 'restored': True, 'active': False}
    page.evaluate("window.modViewer.setEnvironmentPreset('indoor')")
    for scale in [0.001, 1000, 1]:
        page.evaluate("""async scale => {
          const s = await import('./js/scene/scene.js');
          const mesh = window.modViewer.activeMeshes[0]; mesh.scale.setScalar(scale);
          s.fitTo([mesh]); s.frameView([mesh]);
        }""", scale)
    page.locator('#view-gizmo .gizmo-axis[data-axis="z"][data-sign="1"]').press('Enter')
    page.locator('#camera-reset-view-btn').click()
    page.set_viewport_size({'width': 1400, 'height': 950})
    point = project_mesh_points(page, [[0.25, 0.25, 0]])[0]
    page.mouse.click(*point)
    assert page.locator('.draw-item.selected').count() == 1
    open_model(page, 'fixture-02')
    wait_loaded(page)
    assert page.evaluate('window.modViewer.getEnvironmentPreset().id') == 'indoor'
    assert page.evaluate('window.__environmentScene.getEnvironmentDebugState().totalPmremGenerationCount') == 4
    before = page.evaluate('window.modViewer.getRenderCount()')
    page.wait_for_timeout(250)
    assert page.evaluate('window.modViewer.getRenderCount()') == before
    assert errors == []


def test_webgpu_texture_and_visibility_reach_the_frame(viewer):
    payload = model_payload()
    payload['meshes']['mesh-00'].update(
        tex_key='diffuse::texture-01.png', texture_pool_id='pool-01',
        conditions=[[{'var': 'input01', 'value': '1', 'negate': False}]])
    payload['textures'] = {'diffuse::texture-01.png': solid_texture((240, 24, 24))}
    payload['texture_pools'] = {'pool-01': [{'tex_key': 'diffuse::texture-01.png', 'label': 'texture-01'}]}
    payload['controls']['toggles'] = {'KeyFixture': {
        'name': 'control-01', 'ini': 'source-01.ini', 'section': 'KeyFixture', 'wired': True,
        'vars': [{'var': 'input01', 'default': '1', 'values': ['1', '0']},
                 {'var': 'input02', 'default': '0', 'values': ['0', '1']}],
    }}
    payload['state']['defaults'] = {'input01': '1', 'input02': '0'}
    page = _open_fixture(viewer, payload)
    backend = page.evaluate("""async () => {
      const {renderer, rendererReady} = await import('./js/scene/scene.js');
      await rendererReady;
      return renderer.backend.isWebGPUBackend === true && renderer.backend.compatibilityMode !== true;
    }""")
    assert backend
    wait_texture(page)
    before = mesh_pixel(page)
    assert before[0] > before[1] + 25 and before[0] > before[2] + 25
    page.locator('#toggle-list .toggle-cycle-btn').first.click()
    page.wait_for_function('!window.modViewer.activeMeshes[0].visible')
    assert page.evaluate("""async () => {
      const {getControlState} = await import('./js/editing/control-state.js');
      return getControlState();
    }""") == {'input01': '0', 'input02': '1'}
    hidden = mesh_pixel(page)
    assert sum(abs(a - b) for a, b in zip(before, hidden)) > 60
    page.locator('#toggle-list .toggle-cycle-btn').first.click()
    page.wait_for_function('window.modViewer.activeMeshes[0].visible')
    restored = mesh_pixel(page)
    assert max(abs(a - b) for a, b in zip(before, restored)) < 15


def test_filtered_ao_contacts_and_render_graph_lifecycle(module_page):
    result = module_page.evaluate("""async profile => {
      const T = await import('three/webgpu');
      const M = await import('./js/mesh/material-profile.js');
      const {createViewportRenderPipeline} = await import('./js/scene/viewport-render-pipeline.js');
      const renderer = new T.WebGPURenderer({antialias:true, samples:4});
      renderer._getFallback = null;
      const errors = [];
      renderer.onError = info => errors.push(info.message);
      renderer.setPixelRatio(1); renderer.setSize(320,320);
      document.body.append(renderer.domElement);
      await renderer.init();
      const scene = new T.Scene(); scene.background = new T.Color(0x203040);
      const ambient = new T.AmbientLight(0xffffff,1); scene.add(ambient);
      const key = new T.DirectionalLight(0xffffff,0); key.position.set(0,0,3); scene.add(key);
      const camera = new T.PerspectiveCamera(45,1,0.01,10);
      camera.position.z = 3;
      const standard = M.createGameMaterial(null,0xcccccc,{hasUv:false});
      const physical = M.createGameMaterial(profile,0xcccccc);
      M.setGameMaterialRimEnabled(standard,false); M.setGameMaterialRimEnabled(physical,false);
      const plate = new T.Mesh(new T.PlaneGeometry(2,2),standard);
      const contact = new T.Mesh(new T.BoxGeometry(0.65,0.65,0.09),physical);
      contact.position.z = 0.05;
      const emissionMaterial = new T.MeshStandardNodeMaterial({color:0,emissive:0xff6600});
      const emission = new T.Mesh(new T.PlaneGeometry(0.2,0.2),emissionMaterial);
      emission.position.set(-0.65,0.65,0.02);
      const transparentMaterial = new T.MeshStandardNodeMaterial({color:0xffffff,transparent:true,opacity:0.5});
      const transparent = new T.Mesh(new T.PlaneGeometry(0.2,0.2),transparentMaterial);
      transparent.position.set(0.65,0.65,0.02);
      const helperMaterial = new T.MeshBasicNodeMaterial({color:0xffffff});
      const helper = new T.Mesh(new T.PlaneGeometry(0.2,0.2),helperMaterial);
      helper.position.set(0.65,-0.65,0.02);
      const meshes = [plate,contact,emission,transparent];
      meshes.forEach(mesh => mesh.layers.enable(1));
      let draws = [];
      for(const [index,mesh] of [...meshes,helper].entries()) {
        scene.add(mesh);
        mesh.onBeforeRender = (_renderer,currentScene) => {
          if(currentScene.name === 'Character AO pre-pass') draws.push(index);
        };
      }
      const pipeline = createViewportRenderPipeline({renderer,scene,camera});
      pipeline.setMeshes(meshes);
      const capture = async () => {
        draws = [];
        await new Promise(requestAnimationFrame); pipeline.render();
        await renderer.backend.device.queue.onSubmittedWorkDone();
        return {image:renderer.domElement.toDataURL(), state:pipeline.getDebugState(),
          draws:[...draws], byteNormals:pipeline.getDebugState().prePassNormalType === T.UnsignedByteType};
      };
      try {
        const direct = await capture();
        pipeline.setAmbientOcclusionStrength(1);
        const ao = await capture(), repeated = await capture();
        pipeline.setAmbientOcclusionSuppressedByWireframe(true);
        const wire = await capture();
        pipeline.setAmbientOcclusionSuppressedByWireframe(false);
        pipeline.setBloomAvailable(true); pipeline.setBloomEnabled(true);
        const combined = await capture();
        pipeline.setAmbientOcclusionStrength(0);
        const bloom = await capture();
        const memory = renderer.info.memory.textures;
        for(let i=0;i<3;i++) {
          pipeline.setAmbientOcclusionStrength(1); await capture();
          pipeline.setAmbientOcclusionStrength(0); await capture();
        }
        const stableMemory = renderer.info.memory.textures === memory;
        pipeline.setBloomEnabled(false);
        ambient.intensity = 0; key.intensity = 1;
        const directLight = await capture();
        pipeline.setAmbientOcclusionStrength(1);
        const directLightAO = await capture();
        contact.visible = false;
        const hidden = await capture();
        renderer.setSize(321,241); camera.aspect = 321/241; camera.updateProjectionMatrix();
        const resized = await capture();
        pipeline.reset(); pipeline.setAmbientOcclusionStrength(0);
        pipeline.setMeshes([plate]); pipeline.setAmbientOcclusionStrength(1);
        const replaced = await capture();
        pipeline.setAmbientOcclusionStrength(0);
        const restored = await capture();
        return {direct,ao,repeated,wire,combined,bloom,directLight,directLightAO,hidden,
          resized,replaced,restored,stableMemory,errors,webgpu:renderer.backend.isWebGPUBackend===true};
      } finally {
        pipeline.dispose();
        for(const mesh of [...meshes,helper]) mesh.geometry.dispose();
        for(const material of [standard,physical]) {M.disposeGameMaterial(material); material.dispose();}
        emissionMaterial.dispose(); transparentMaterial.dispose(); helperMaterial.dispose();
        await renderer.dispose();
      }
    }""", material_profile_for('genshin', 'gimi').to_metadata())

    def pixels(name):
        data = result[name]['image'].split(',', 1)[1]
        with Image.open(io.BytesIO(base64.b64decode(data))) as image:
            return image.convert('RGB').copy()

    direct, ao = pixels('direct'), pixels('ao')
    darkened = ImageChops.subtract(direct.convert('L'), ao.convert('L')).histogram()
    assert sum(darkened[4:]) > 50
    for point in [(60, 260), (10, 10), (76, 76), (244, 76)]:
        assert max(abs(a-b) for a, b in zip(direct.getpixel(point), ao.getpixel(point))) <= 2
    assert pixels('repeated').tobytes() == ao.tobytes()
    assert pixels('wire').tobytes() == direct.tobytes()
    difference = ImageChops.difference(pixels('directLight'), pixels('directLightAO'))
    assert all(maximum <= 2 for _minimum, maximum in difference.getextrema())
    lit = pixels('directLight')
    assert max(abs(a-b) for a, b in zip(lit.getpixel((160, 160)), lit.getpixel((60, 260)))) <= 5
    for name, mode in [('direct', 'direct'), ('ao', 'ao'), ('wire', 'direct'),
                       ('combined', 'ao-bloom'), ('bloom', 'bloom'), ('restored', 'direct')]:
        assert result[name]['state']['activeRenderMode'] == mode
    assert result['wire']['state']['strength'] == 1
    assert result['ao']['state']['blurEnabled']
    assert sorted(result['ao']['draws']) == [0, 1, 2]
    assert 1 not in result['hidden']['draws']
    for name in ['direct', 'wire', 'bloom', 'restored']:
        assert result[name]['draws'] == []
    for name in ['ao', 'combined', 'resized', 'replaced']:
        assert result[name]['byteNormals']
    assert result['resized']['state']['resolution'] == [161, 121]
    assert result['replaced']['state']['modelSize'] > 0
    assert result['stableMemory'] and result['webgpu'] and result['errors'] == []


def test_genshin_light_map_preserves_glossy_response_outside_toon_mode(module_page):
    result = module_page.evaluate("""async profile => {
      const T = await import('three/webgpu');
      const M = await import('./js/mesh/material-profile.js');
      const renderer = new T.WebGPURenderer();
      await renderer.init();
      const scene = new T.Scene();
      const camera = new T.OrthographicCamera(-1,1,1,-1,0.1,10);
      camera.position.z = 2;
      const light = new T.DirectionalLight(0xffffff,1);
      light.position.set(0,0,2); scene.add(light);
      const material = M.createGameMaterial(profile,0x000000);
      M.setGameMaterialRimEnabled(material,false);
      const mesh = new T.Mesh(new T.PlaneGeometry(2,2),material); scene.add(mesh);
      const packed = new T.DataTexture(new Uint8Array([0,128,0,255]),1,1);
      packed.needsUpdate = true;
      const target = new T.RenderTarget(4,4,{depthBuffer:false});
      renderer.setRenderTarget(target);
      const sample = async (roughness,toon,map) => {
        material.roughness = roughness;
        M.setGameMaterialToonEnabled(material,toon);
        M.updateGameMaterialTextures(mesh,{light_map:map});
        renderer.render(scene,camera);
        const pixels = await renderer.readRenderTargetPixelsAsync(target,0,0,4,4);
        return pixels[0]+pixels[1]+pixels[2];
      };
      try {
        const matte = await sample(1,false,packed);
        const version = material.version;
        const glossy = await sample(0.2,false,packed);
        const masked = await sample(0.2,true,packed);
        const noMap = await sample(0.2,true,null);
        packed.image.data[2] = 153; packed.needsUpdate = true;
        const authored = await sample(0.2,true,packed);
        const restored = await sample(0.2,false,packed);
        return {matte,glossy,masked,noMap,authored,restored,
          stable:material.version===version,webgpu:renderer.backend.isWebGPUBackend===true};
      } finally {
        renderer.setRenderTarget(null); target.dispose(); packed.dispose();
        mesh.geometry.dispose(); M.disposeGameMaterial(material); material.dispose(); renderer.dispose();
      }
    }""", material_profile_for('genshin', 'gimi').to_metadata())
    assert result['webgpu'] and result['stable']
    assert result['glossy'] > result['matte'] + 100, result
    assert result['masked'] < result['glossy'] / 4, result
    assert result['noMap'] > result['masked'] + 100, result
    assert result['authored'] > result['masked'] + 100, result
    assert abs(result['restored'] - result['glossy']) <= 3, result


def test_shared_texture_uploads_preserve_role_specific_color_spaces(viewer):
    payload = textured_payload(2)
    payload['textures']['normal_map::texture-01.png'] = solid_texture((128, 128, 255))
    for mesh in payload['meshes'].values():
        mesh['normal_map_key'] = 'normal_map::texture-01.png'
    page = _open_fixture(viewer, payload)
    wait_texture(page)
    wait_texture(page, role='normal_map')
    result = page.evaluate("""async () => {
      const {getGameMaterialTexture} = await import('./js/mesh/material-profile.js');
      const [first, second] = window.modViewer.activeMeshes;
      const diffuse = getGameMaterialTexture(first.material, 'diffuse');
      const auxiliary = getGameMaterialTexture(first.material, 'normal_map');
      return {shared: diffuse === getGameMaterialTexture(second.material, 'diffuse'),
        separate: diffuse !== auxiliary, color: diffuse.colorSpace, data: auxiliary.colorSpace};
    }""")
    assert result == {'shared': True, 'separate': True, 'color': 'srgb', 'data': ''}


@pytest.mark.parametrize('asset_type', ['GIMI', 'ZZMI'])
def test_asset_packed_normal_shading_handles_empty_rg_and_retains_detail(viewer, tmp_path, asset_type):
    key = 'normal_map::texture-01.png'
    # Native asset axes differ; keep the triangle facing the preview camera.
    last_vertex = (0, 1, 0) if asset_type == 'GIMI' else (0, 0, 1)
    normal = (0, 0, 1) if asset_type == 'GIMI' else (0, -1, 0)
    part = AssetMeshPart(
        key='part-01', label='Component01', asset_type=asset_type,
        asset_path='Asset01', geometry_hash='12345678',
        component_name='Component01', classification='A',
        component_ordinal=0, first_index=0, index_count=3,
        positions=struct.pack('<9f', 0, 0, 0, 1, 0, 0, *last_vertex),
        indices=struct.pack('<3I', 0, 1, 2),
        normals=struct.pack('<9f', *(normal * 3)),
        uvs=struct.pack('<6f', 0, 0, 1, 0, 0, 1),
        textures={'normal_map': AssetTexture(
            'normal_map', 'texture-01.png', key, 'texture-01',
            uri=solid_texture((128, 128, 0)))})
    geometry = GeometryBlob()
    payload = AssetLoadResult.from_parts(
        asset_type, str(tmp_path), {'path': 'Asset01'}, [part],
        geometry=geometry).payload
    payload['_fixture_blob'] = bytes(geometry.data)
    page = _open_fixture(viewer, payload)
    wait_texture(page, role='normal_map')
    sample = [[0.25, 0.25, 0] if asset_type == 'GIMI' else [0.25, 0, 0.25]]
    packed = mesh_pixels(page, sample)[0]
    page.evaluate("""async () => {
      const {updateGameMaterialTextures} = await import('./js/mesh/material-profile.js');
      const mesh = window.modViewer.activeMeshes[0];
      updateGameMaterialTextures(mesh, {normal_map: null});
    }""")
    geometry_normal = mesh_pixels(page, sample)[0]
    assert max(abs(a-b) for a,b in zip(packed, geometry_normal)) < 10
    page.evaluate("""async () => {
      const {DataTexture, RGBAFormat, UnsignedByteType, NoColorSpace} = await import('three');
      const {updateGameMaterialTextures} = await import('./js/mesh/material-profile.js');
      window.__setNormalPixels = rgba => {
        const texture = new DataTexture(new Uint8Array(rgba), 1, 1, RGBAFormat, UnsignedByteType);
        texture.colorSpace = NoColorSpace;
        texture.needsUpdate = true;
        updateGameMaterialTextures(window.modViewer.activeMeshes[0], {normal_map: texture});
      };
      window.__setNormalPixels([128, 128, 255, 0]);
    }""")
    other_blue = mesh_pixels(page, sample)[0]
    assert max(abs(a-b) for a,b in zip(packed, other_blue)) < 5
    page.evaluate('window.__setNormalPixels([220, 128, 0, 255])')
    tilted = mesh_pixels(page, sample)[0]
    assert max(abs(a-b) for a,b in zip(packed, tilted)) > 10, (
        packed, geometry_normal, other_blue, tilted,
        page.evaluate('window.modViewer.activeMeshes[0].material.userData.gameMaterial.normalPacking'))
    for empty in ([0, 0, 0, 0], [0, 0, 255, 255]):
        page.evaluate('rgba => window.__setNormalPixels(rgba)', empty)
        empty_normal = mesh_pixels(page, sample)[0]
        assert max(abs(a-b) for a,b in zip(empty_normal, geometry_normal)) < 5
    page.evaluate('window.__setNormalPixels([220, 128, 0, 255])')
    restored = mesh_pixels(page, sample)[0]
    assert max(abs(a-b) for a,b in zip(restored, tilted)) < 5


def test_failed_texture_uses_renderable_fallback_without_retry_and_new_source_recovers(viewer):
    broken = textured_payload()
    broken['textures']['diffuse::texture-01.png'] = '/texture/fixture-missing.png'
    page = viewer({'fixture-01': broken, 'fixture-02': textured_payload()})
    requests = []
    page.route('**/texture/fixture-missing.png', lambda route: (requests.append(route.request.url), route.fulfill(status=404)))
    open_model(page, 'fixture-01')
    wait_loaded(page)
    fallback = mesh_pixel(page)
    assert max(fallback) - min(fallback) < 20
    page.evaluate("""async () => {
      const {refreshMeshTexture} = await import('./js/mesh/mesh-factory.js');
      refreshMeshTexture(window.modViewer.activeMeshes[0]);
    }""")
    mesh_pixel(page)
    assert len(requests) == 1
    open_model(page, 'fixture-02')
    wait_loaded(page)
    wait_texture(page)
    recovered = mesh_pixel(page)
    assert recovered[0] > recovered[2] + 25


def test_replaced_pending_texture_cannot_apply_stale_pixels(viewer):
    payload = textured_payload()
    payload['textures']['diffuse::texture-01.png'] = '/texture/fixture-delayed.png'
    page = viewer({'fixture-01': payload})
    delayed = []
    page.route('**/texture/fixture-delayed.png', lambda route: delayed.append(route))
    open_model(page, 'fixture-01')
    wait_loaded(page)
    assert len(delayed) == 1
    page.evaluate("""async () => {
      const {setManualTexOverride} = await import('./js/mesh/mesh-state.js');
      setManualTexOverride(window.modViewer.activeMeshes[0], 'diffuse::texture-02.png');
    }""")
    wait_texture(page)
    before = mesh_pixel(page)
    delayed[0].fulfill(status=200, content_type='image/png', body=base64.b64decode(solid_texture((240, 24, 24)).split(',')[1]))
    after = mesh_pixel(page)
    assert after[2] > after[0] + 25
    assert max(abs(a-b) for a,b in zip(before, after)) < 15
    assert page.evaluate('window.modViewer.activeMeshes[0].userData.texKey') == 'diffuse::texture-02.png'


def test_manual_texture_override_survives_controls_and_clear_restores_authored_binding(viewer):
    payload = textured_payload()
    payload['state']['defaults'] = {'input01': '0'}
    payload['meshes']['mesh-00']['texture_variants'] = [{
        'conditions': [[{'var': 'input01', 'value': '1', 'negate': False}]],
        'tex_key': 'diffuse::texture-02.png',
    }]
    page = _open_fixture(viewer, payload)
    result = page.evaluate("""async () => {
      const state = await import('./js/mesh/mesh-state.js');
      const controls = await import('./js/editing/control-state.js');
      const mesh = window.modViewer.activeMeshes[0];
      state.setManualTexOverride(mesh, 'diffuse::texture-01.png');
      controls.setControlValue('input01', '1'); state.applyTextureVariant(mesh);
      const sticky = mesh.userData.texKey;
      state.setManualTexOverride(mesh, null); const none = mesh.userData.texKey;
      state.setManualTexOverride(mesh, undefined); const automatic = mesh.userData.texKey;
      controls.setControlValue('input01', '0'); state.applyTextureVariant(mesh);
      return [sticky, none, automatic, mesh.userData.texKey];
    }""")
    assert result == ['diffuse::texture-01.png', None, 'diffuse::texture-02.png', 'diffuse::texture-01.png']


def test_color_preview_changes_pixels_and_reset_reuses_material_and_texture(viewer):
    page = _open_fixture(viewer, textured_payload())
    wait_texture(page)
    before = mesh_pixel(page)
    page.evaluate("""async () => {
      const {setMeshColorAdjustment} = await import('./js/mesh/mesh-color-session.js');
      const {getGameMaterialTexture} = await import('./js/mesh/material-profile.js');
      const mesh = window.modViewer.activeMeshes[0];
      window.__material = mesh.material;
      window.__texture = getGameMaterialTexture(mesh.material, 'diffuse');
      setMeshColorAdjustment(mesh, {brightness: 0.2});
    }""")
    changed = mesh_pixel(page)
    assert before[0] > changed[0] + 20
    result = page.evaluate("""async () => {
      const {resetMeshColorAdjustment} = await import('./js/mesh/mesh-color-session.js');
      const {getGameMaterialTexture} = await import('./js/mesh/material-profile.js');
      const mesh = window.modViewer.activeMeshes[0];
      resetMeshColorAdjustment(mesh);
      return mesh.material === window.__material && getGameMaterialTexture(mesh.material, 'diffuse') === window.__texture;
    }""")
    assert result
    restored = mesh_pixel(page)
    assert max(abs(a-b) for a,b in zip(before, restored)) < 15
    assert bridge_calls(page, 'textureSave') == []


def test_texture_save_requires_confirmation_flushes_metadata_and_blocks_duplicate_submit(viewer):
    page = _open_fixture(viewer, textured_payload(2, extension='dds'))
    page.evaluate("""async () => {
      const {setMeshColorAdjustment} = await import('./js/mesh/mesh-color-session.js');
      for (const mesh of window.modViewer.activeMeshes) setMeshColorAdjustment(mesh, {brightness: 0.6}, {persist: true});
      window.modViewer.activeMeshes[1].visible = false;
      window.__bridge.blocked.textureSave = true;
    }""")
    page.locator('#inspector-tab').click()
    page.locator('.draw-item').first.click()
    page.locator('.inspector-texture-bake').click()
    page.locator('#texture-bake-modal-backdrop.show').wait_for()
    assert bridge_calls(page, 'textureSave') == []
    page.locator('#texture-bake-close').click()
    assert bridge_calls(page, 'textureSave') == []
    page.locator('.inspector-texture-bake').click()
    page.locator('#texture-bake-confirm').click()
    page.wait_for_function('window.__bridge.calls.some(call => call.name === "textureSave")')
    page.locator('#texture-bake-close').click()
    assert page.locator('#texture-bake-modal-backdrop.show').is_visible()
    page.locator('#texture-bake-confirm').evaluate('button => button.click()')
    assert len(bridge_calls(page, 'textureSave')) == 1
    request = bridge_calls(page, 'textureSave')[0]
    assert request[:2] == ['fixture-01', 'diffuse::texture-01.dds']
    assert len(request[2]) == 2
    assert len(request[3]) == 2
    calls = page.evaluate('window.__bridge.calls.map(call => call.name)')
    assert calls.index('color') < calls.index('textureSave')
    page.evaluate('window.__bridge.release("textureSave")')
    page.locator('#texture-bake-error').wait_for(state='visible')
    assert page.evaluate('window.modViewer.activeMeshes[0].userData.colorAdjustment.brightness') == 0.6
    page.locator('#texture-bake-close').click()
    page.evaluate("""() => {
      const meshes = window.modViewer.activeMeshes;
      window.__bridge.results.textureSave = {
        status: 'ok', tex_key: 'diffuse::texture-01.dds', affected_tex_keys: ['diffuse::texture-01.dds'],
        saved_meshes: meshes.map(mesh => ({semantic_key: mesh.userData.semanticKey, metadata_key: mesh.userData.metadataKey})),
        metadata_reset: {cleared: meshes.map(mesh => mesh.userData.metadataKey), preserved: [], failed: []},
      };
      window.__materials = meshes.map(mesh => mesh.material);
    }""")
    page.locator('.inspector-texture-bake').click()
    page.locator('#texture-bake-confirm').click()
    page.wait_for_function('window.modViewer.activeMeshes.every(mesh => mesh.userData.colorAdjustment.brightness === 1)')
    page.locator('.texture-bake-summary').first.wait_for(state='visible')
    assert len(bridge_calls(page, 'textureSave')) == 2
    assert page.evaluate('window.modViewer.activeMeshes.every((mesh, i) => mesh.material === window.__materials[i])')


def test_shape_changes_update_stable_attributes_and_restore_authored_normals(viewer):
    payload = model_payload()
    mesh = payload['meshes']['mesh-00']
    mesh['normal'] = append_stream(payload, 'f', [0,0,1] * 3)
    mesh['shape_targets'] = [{'var': 'shape01', 'pos': append_stream(payload, 'f', [0,0,0, 2,0,0, 0,2,0])}]
    payload['state']['defaults'] = {'shape01': '0'}
    page = _open_fixture(viewer, payload)
    result = page.evaluate("""async () => {
      const {refreshMeshes} = await import('./js/mesh/mesh-state.js');
      const {setControlValue} = await import('./js/editing/control-state.js');
      const mesh = window.modViewer.activeMeshes[0];
      const position = mesh.geometry.attributes.position;
      const normal = mesh.geometry.attributes.normal;
      const baseline = [...mesh.userData.basePositions];
      setControlValue('shape01', '1'); refreshMeshes({force: {shapes: true}});
      const shaped = [...position.array];
      setControlValue('shape01', '0'); refreshMeshes({force: {shapes: true}});
      return {shaped, restored: [...position.array], baseline,
        stable: position === mesh.geometry.attributes.position && normal === mesh.geometry.attributes.normal,
        normals: [...normal.array]};
    }""")
    assert result['shaped'] == [0,0,0,2,0,0,0,2,0]
    assert result['restored'] == result['baseline'] == [0,0,0,1,0,0,0,1,0]
    assert result['normals'] == [0,0,1] * 3
    assert result['stable']


def test_animation_suspension_and_release_restore_canonical_geometry_then_resume(viewer, tmp_path):
    from tests.support.animations import compute_mod, load_mod

    ini = compute_mod(tmp_path / 'mod')
    parsed, built = load_mod(ini, ini.parent)
    compute = model_payload()
    compute['meshes'] = built.meshes
    compute['_fixture_blob'] = bytes(built.geometry.data)
    compute['state']['defaults'] = parsed.defaults
    compute['state']['defaults']['input02'] = '0'
    for entry in compute['meshes'].values():
        program = entry['animation_geometry']['program']
        program['external_variables'].append('input02')
        for command in program['commands']:
            command.setdefault('conditions', []).append({
                'kind': 'compare', 'op': '==', 'left': {'kind': 'variable', 'variable': 'input02'},
                'right': {'kind': 'literal', 'value': 1},
            })
    payload = model_payload(2)
    mesh = payload['meshes']['mesh-00']
    mesh['animation_id'] = 'clock-01'
    mesh['animation_geometry'] = {
        'frames': 2, 'frame_start': 0, 'position_frame_bytes': 36,
        'positions': append_stream(payload, 'f', [0,0,0,1,0,0,0,1,0, 0,0,0.5,1,0,0.5,0,1,0.5]),
    }
    payload['animations'] = {'clock-01': {'frame_start': 0, 'frame_end': 1, 'fps': 30}}
    mesh['conditions'] = [[{'var': 'input01', 'value': '1'}]]
    stationary = payload['meshes']['mesh-01']
    stationary['animation_id'] = 'track-static'
    stationary['animation_geometry'] = {
        **mesh['animation_geometry'], 'clock_ids': ['clock-01'], 'frames': 1,
        'positions': append_stream(payload, 'f', [0,0,0,1,0,0,0,1,0]),
    }
    payload['state']['defaults'] = {'input01': '0'}
    independent = model_payload(2)
    independent['animations'] = {}
    for index, entry in enumerate(independent['meshes'].values()):
        clock = f'clock-{index:02d}'
        entry['animation_id'] = clock
        entry['animation_geometry'] = {
            **mesh['animation_geometry'],
            'positions': append_stream(independent, 'f', [0,0,0,1,0,0,0,1,0, 0,0,.5,1,0,.5,0,1,.5]),
        }
        independent['animations'][clock] = {'frame_start': 0, 'frame_end': 1, 'fps': 30}
    page = _open_fixture(viewer, payload, {
        'fixture-02': model_payload(), 'fixture-03': compute, 'fixture-04': independent})
    assert page.locator('#animation-playback').is_hidden()
    assert page.locator('#animation-play-pause').is_hidden()
    page.evaluate('''async () => {
      const {setToggleValue, refreshAll} = await import('./js/mesh/visibility.js');
      window.__selectAnimation = value => { setToggleValue('input01', value); refreshAll(); };
      window.__selectAnimation('1');
    }''')
    page.wait_for_function('window.modViewer.activeMeshes[0].geometry.attributes.position.array[2] === 0.5')
    assert page.locator('#animation-playback').is_visible()
    assert page.locator('#animation-frame').is_visible()
    page.evaluate('window.__playbackButton = document.getElementById("animation-play-pause")')
    page.locator('.draw-item').nth(0).click()
    page.locator('#inspector-tab').click()
    page.evaluate('document.activeElement.blur()')
    page.keyboard.press('h')
    page.wait_for_function("document.getElementById('animation-play-pause').hidden")
    page.wait_for_function("import('./js/mesh/animation-runtime.js').then(m => !m.animationRuntimeSnapshot().rafActive)")
    page.locator('.inspector-visibility-btn').click()
    page.wait_for_function("!document.getElementById('animation-playback').hidden")
    version = page.evaluate('window.modViewer.activeMeshes[0].geometry.attributes.position.version')
    page.wait_for_function('version => window.modViewer.activeMeshes[0].geometry.attributes.position.version > version + 1', arg=version)
    page.locator('#animation-play-pause').click()
    assert page.locator('#animation-play-pause').get_attribute('aria-label') == 'Play animation'
    page.locator('#animation-frame').evaluate('''slider => {
      slider.value = '1'; slider.dispatchEvent(new Event('input',{bubbles:true}));
    }''')
    assert page.locator('#animation-frame-counter').text_content() == '1 / 1'
    page.locator('.inspector-visibility-btn').click()
    page.wait_for_function("document.getElementById('animation-play-pause').hidden")
    page.locator('.inspector-visibility-btn').click()
    page.wait_for_function("!document.getElementById('animation-playback').hidden")
    assert page.evaluate("import('./js/mesh/animation-runtime.js').then(m => !m.animationRuntimeSnapshot().rafActive && m.getAnimationPlaybackState().frame === 1)")
    assert page.evaluate('window.modViewer.activeMeshes[0].geometry.attributes.position.array[2]') == .5
    page.evaluate('const mesh = window.modViewer.activeMeshes[0]; mesh.userData.animationSuspended = true; mesh.geometry.attributes.position.array.fill(7)')
    page.locator('#animation-frame').evaluate('''slider => {
      slider.value = '0'; slider.dispatchEvent(new Event('input',{bubbles:true}));
    }''')
    page.evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
    assert page.evaluate('window.modViewer.activeMeshes[0].geometry.attributes.position.array.every(value => value === 7)')
    result = page.evaluate("""async () => {
      const {resumeAnimatedMesh} = await import('./js/mesh/animation-runtime.js');
      const mesh = window.modViewer.activeMeshes[0], position = mesh.geometry.attributes.position;
      mesh.userData.animationSuspended = false;
      const resumed = resumeAnimatedMesh(mesh);
      return {resumed, stable: mesh.geometry.attributes.position === position, canonical: [...position.array]};
    }""")
    assert result == {'resumed': True, 'stable': True, 'canonical': [0,0,0,1,0,0,0,1,0]}
    page.wait_for_function('window.modViewer.activeMeshes[0].geometry.attributes.position.array[2] === 0.5')
    assert page.evaluate("import('./js/mesh/animation-runtime.js').then(module => module.getAnimationPlaybackState().paused)")
    page.evaluate("window.__selectAnimation('0')")
    page.wait_for_function("document.getElementById('animation-playback').hidden")
    assert page.locator('#animation-play-pause').is_hidden()
    page.evaluate("window.__selectAnimation('1')")
    page.wait_for_function("!document.getElementById('animation-frame').hidden")
    assert page.locator('#animation-playback').is_visible()
    open_model(page, 'fixture-02')
    wait_loaded(page)
    assert page.locator('#animation-playback').is_hidden()
    assert page.locator('#animation-play-pause').is_hidden()
    assert page.evaluate("import('./js/mesh/animation-runtime.js').then(module => module.animationRuntimeSnapshot().clocks)") == 0
    open_model(page, 'fixture-01')
    wait_loaded(page, 2)
    assert page.locator('#animation-playback').is_hidden()
    page.evaluate("window.__selectAnimation('1')")
    page.wait_for_function("!document.getElementById('animation-playback').hidden")
    assert page.locator('#animation-play-pause').get_attribute('aria-label') == 'Pause animation'
    assert not page.evaluate("import('./js/mesh/animation-runtime.js').then(module => module.getAnimationPlaybackState().paused)")
    open_model(page, 'fixture-03')
    wait_loaded(page)
    assert page.locator('#animation-playback').is_hidden()
    assert page.locator('#animation-frame').is_hidden()
    assert page.locator('#animation-frame-counter').is_hidden()
    assert page.locator('#animation-play-pause').is_hidden()
    page.evaluate('''async () => {
      const {setToggleValue, refreshAll} = await import('./js/mesh/visibility.js');
      window.__selectCompute = value => { setToggleValue('input02', value); refreshAll(); };
      window.__selectCompute('1');
    }''')
    page.wait_for_function("!document.getElementById('animation-play-pause').hidden")
    assert page.locator('#tool-buttons > #animation-play-pause').is_visible()
    assert page.evaluate('window.__playbackButton === document.getElementById("animation-play-pause")')
    assert page.evaluate("import('./js/mesh/animation-runtime.js').then(module => module.animationRuntimeSnapshot().rafActive)")
    assert not page.evaluate("import('./js/mesh/animation-runtime.js').then(module => module.getAnimationPlaybackState().paused)")
    page.locator('#animation-play-pause').click()
    assert page.locator('#animation-play-pause').get_attribute('aria-label') == 'Play animation'
    assert page.evaluate("import('./js/mesh/animation-runtime.js').then(module => !module.animationRuntimeSnapshot().rafActive)")
    page.evaluate("window.__selectCompute('0')")
    page.wait_for_function("document.getElementById('animation-play-pause').hidden")
    page.evaluate("window.__selectCompute('1')")
    page.wait_for_function("!document.getElementById('animation-play-pause').hidden")
    assert page.locator('#animation-play-pause').get_attribute('aria-label') == 'Play animation'
    assert page.evaluate("import('./js/mesh/animation-runtime.js').then(module => !module.animationRuntimeSnapshot().rafActive)")
    open_model(page, 'fixture-04')
    wait_loaded(page, 2)
    page.wait_for_function("document.getElementById('animation-play-pause').parentElement.id === 'tool-buttons'")
    assert page.locator('#animation-playback').is_hidden()
    page.locator('#animation-play-pause').click()
    page.evaluate('''async () => {
      const {setManualMeshVisibility} = await import('./js/mesh/mesh-state.js');
      window.__setSecondVisible = visible => setManualMeshVisibility(window.modViewer.activeMeshes[1], visible);
      window.__setSecondVisible(false);
    }''')
    page.wait_for_function("!document.getElementById('animation-playback').hidden")
    assert page.locator('#animation-frame').is_visible()
    assert page.locator('#animation-play-pause').get_attribute('aria-label') == 'Play animation'
    page.evaluate('window.__setSecondVisible(true)')
    page.wait_for_function("document.getElementById('animation-play-pause').parentElement.id === 'tool-buttons'")
    assert page.locator('#animation-playback').is_hidden()
    assert page.evaluate('window.__playbackButton === document.getElementById("animation-play-pause")')
    assert bridge_calls(page, 'preferencesSave') == []
    assert bridge_calls(page, 'export') == []


def test_wireframe_suppresses_outlines_and_restores_preference_without_rebuilding(viewer):
    page = _open_fixture(viewer, model_payload())
    result = page.evaluate("""async () => {
      const modes = await import('./js/scene/render-modes.js');
      const outlines = await import('./js/scene/outline-renderer.js');
      const mesh = window.modViewer.activeMeshes[0], material = mesh.material, geometry = mesh.geometry;
      outlines.setOutlinesEnabled(true);
      const enabled = outlines.getOutlineState(mesh).visible;
      modes.toggleWireframeMode([mesh]);
      const suppressed = outlines.getOutlineState(mesh).visible;
      const retained = outlines.isOutlineEnabled();
      modes.toggleWireframeMode([mesh]);
      return {enabled, suppressed, retained, restored: outlines.getOutlineState(mesh).visible,
        stable: mesh.material === material && mesh.geometry === geometry};
    }""")
    assert result == {'enabled': True, 'suppressed': False, 'retained': True, 'restored': True, 'stable': True}


def test_thin_surfaces_keep_depth_order_through_distant_and_close_zoom(viewer):
    payload = textured_payload(2)
    keys = list(payload['textures'])
    payload['textures'][keys[0]] = solid_texture((24, 240, 24))
    payload['textures'][keys[1]] = solid_texture((240, 24, 24))
    payload['meshes']['mesh-01'].update(
        pos=append_stream(payload, 'f', [0, 0, -0.001,
                                       1, 0, -0.001,
                                       0, 1, -0.001]),
        tex_key=keys[1])
    page = _open_fixture(viewer, payload)
    wait_texture(page, 0)
    wait_texture(page, 1)
    page.evaluate("""async () => {
      const {camera, controls} = await import('./js/scene/scene.js');
      window.__zoomCamera = camera; window.__zoomControls = controls;
      controls.target.set(0.25, 0.25, 0);
      // Render the rear layer last to expose any equal-depth collisions.
      window.modViewer.activeMeshes.forEach((mesh, index) => mesh.renderOrder = index);
    }""")
    states = []
    for distance in (2, 12, 0.02, 2):
        page.evaluate("""distance => {
          const camera = window.__zoomCamera, controls = window.__zoomControls;
          camera.position.copy(controls.target); camera.position.z += distance;
          camera.lookAt(controls.target); camera.updateMatrixWorld();
        }""", distance)
        pixel = mesh_pixels(page, [[0.25, 0.25, 0]])[0]
        assert pixel[1] > pixel[0] + 50, (distance, pixel)
        states.append(page.evaluate('window.__zoomCamera.near'))
    assert states[1] > states[0]
    assert 0 < states[2] < 0.02
    assert states[3] == states[0]


def test_outline_keeps_shallow_backfaces_behind_the_visible_surface(viewer):
    payload = model_payload(2)
    payload['meshes']['mesh-01'].update(
        pos=append_stream(payload, 'f', [0.2, 0.2, -0.001,
                                       0.2, 0.4, -0.001,
                                       0.4, 0.2, -0.001]))
    for mesh in payload['meshes'].values():
        mesh['normal'] = append_stream(payload, 'f', [0, 0, 1] * 3)
    page = _open_fixture(viewer, payload)
    before = mesh_pixel(page)
    page.evaluate("""async () => {
      const {setOutlinesEnabled} = await import('./js/scene/outline-renderer.js');
      setOutlinesEnabled(true);
    }""")
    outlined = mesh_pixel(page)
    assert max(abs(a - b) for a, b in zip(before, outlined)) < 15


def test_outline_and_selection_preserve_coplanar_surface_with_visible_border(viewer):
    payload = model_payload()
    mesh = payload['meshes']['mesh-00']
    mesh['idx'] = append_stream(payload, 'I', [0, 2, 1])
    mesh['normal'] = append_stream(payload, 'f', [
        -0.707, 0, -0.707, 0.707, 0, -0.707, 0, 0.707, -0.707,
    ])
    page = _open_fixture(viewer, payload)
    points = [[x, y, 0] for x in (0.15, 0.3, 0.6)
              for y in (0.15, 0.3, 0.6) if x + y < 0.9]
    before = mesh_pixels(page, points)
    page.evaluate("""async () => {
      window.__outlines = await import('./js/scene/outline-renderer.js');
      window.__outlines.setOutlinesEnabled(true);
    }""")
    outlined = mesh_pixels(page, points)
    assert all(max(abs(a - b) for a, b in zip(old, new)) < 15
               for old, new in zip(before, outlined))
    page.evaluate("""async () => {
      const {selectMesh} = await import('./js/scene/selection.js');
      selectMesh(window.modViewer.activeMeshes[0]);
    }""")
    selected = mesh_pixels(page, points)
    assert all(max(abs(a - b) for a, b in zip(old, new)) < 15
               for old, new in zip(before, selected))
    corners = project_mesh_points(page, [[0, 0, 0], [1, 0, 0], [0, 1, 0]])
    with Image.open(io.BytesIO(page.screenshot())) as image:
        rgb = image.convert('RGB')
        yellow = sum(
            r > 180 and g > 120 and b < 80
            for x in range(min(p[0] for p in corners) - 3,
                           max(p[0] for p in corners) + 4)
            for y in range(min(p[1] for p in corners) - 3,
                           max(p[1] for p in corners) + 4)
            for r, g, b in [rgb.getpixel((x, y))]
        )
    assert yellow > 20, 'The selection silhouette must remain visible'
    page.evaluate("""async () => {
      window.__outlines.setOutlinesEnabled(false);
      const {selectMesh} = await import('./js/scene/selection.js');
      selectMesh(null);
    }""")
    restored = mesh_pixels(page, points)
    assert all(max(abs(a - b) for a, b in zip(old, new)) < 15
               for old, new in zip(before, restored))


def test_directional_shadows_keep_original_shape_with_a_low_angle_floor_fallback(viewer):
    payload = model_payload()
    mesh = payload['meshes']['mesh-00']
    mesh['pos'] = append_stream(payload, 'f', [
        -0.5, 0, -0.5, 0.5, 0, -0.5, 0.5, 2, -0.5, -0.5, 2, -0.5,
        -0.5, 0, 0.5, 0.5, 0, 0.5, 0.5, 2, 0.5, -0.5, 2, 0.5,
    ])
    mesh['idx'] = append_stream(payload, 'I', [
        0, 2, 1, 0, 3, 2, 4, 5, 6, 4, 6, 7,
        0, 1, 5, 0, 5, 4, 3, 7, 6, 3, 6, 2,
        0, 4, 7, 0, 7, 3, 1, 2, 6, 1, 6, 5,
    ])
    mesh['drawindexed'] = [36, 0, 0]
    mesh.pop('uv')
    page = _open_fixture(viewer, payload)
    midpoint = page.evaluate("""async () => {
      const {scene, camera, controls, setKeyLightIntensity} = await import('./js/scene/scene.js');
      const {requestRender} = await import('./js/scene/render-scheduler.js');
      scene.background.setHex(0x808080);
      camera.position.set(8, 7, 12);
      camera.lookAt(controls.target); camera.updateMatrixWorld();
      const light = scene.children.find(object => object.isDirectionalLight && object.castShadow);
      window.__shadowCameras = new Set();
      window.modViewer.activeMeshes[0].onBeforeShadow = (_renderer, _object, _camera, shadowCamera) =>
        window.__shadowCameras.add(shadowCamera.uuid);
      window.__lowerKeyLight = height => {
        window.__shadowCameras.clear(); light.position.set(-2, height, -2); requestRender();
      };
      setKeyLightIntensity(0);
      return controls.target.y;
    }""")
    points = [[1, -0.001, 1], [1.5, -0.001, -0.9], [3.5, -0.001, -2]]
    baseline = mesh_pixels(page, points)
    unshadowed = baseline[0]
    page.evaluate("""async () => {
      const {setKeyLightIntensity, invalidateCharacterShadowMap} = await import('./js/scene/scene.js');
      window.__shadowCameras.clear(); setKeyLightIntensity(1); invalidateCharacterShadowMap();
    }""")
    initial = mesh_pixels(page, points)
    assert page.evaluate('window.__shadowCameras.size') == 1
    assert sum(baseline[1]) - sum(initial[1]) > 30
    assert max(abs(a - b) for a, b in zip(baseline[2], initial[2])) < 5
    for height, maps in ((3, 1), (midpoint, 2), (-1, 1), (3, 1)):
        page.evaluate('height => window.__lowerKeyLight(height)', height)
        shadowed = mesh_pixels(page, [[1, -0.001, 1]])[0]
        assert page.evaluate('window.__shadowCameras.size') == maps
        if height > 0:
            assert sum(unshadowed) - sum(shadowed) > 30, height
        else:
            assert max(abs(a - b) for a, b in zip(unshadowed, shadowed)) < 5, height


def test_close_zoom_surface_shadow_stays_smooth_without_redrawing_the_map(viewer):
    payload = model_payload()
    mesh = payload['meshes']['mesh-00']
    mesh.update(pos=append_stream(payload, 'f', [-4,-4,0, 4,-4,0, 4,4,0, -4,4,0]),
                idx=append_stream(payload, 'I', [0,1,2, 0,2,3]), drawindexed=[6,0,0])
    mesh.pop('uv')
    page = _open_fixture(viewer, payload)
    page.evaluate("""async () => {
      const THREE = await import('three/webgpu');
      const {scene, camera, controls, adoptModelMeshes,
        invalidateCharacterShadowGeometry} = await import('./js/scene/scene.js');
      const caster = new THREE.Mesh(new THREE.PlaneGeometry(8, 8),
        new THREE.MeshStandardNodeMaterial({side: THREE.DoubleSide}));
      caster.position.set(-4.685, 0, 0.6);
      caster.castShadow = true;
      scene.add(caster); adoptModelMeshes([caster]); caster.rotation.z = 0.35;
      const light = scene.children.find(object => object.isDirectionalLight && object.castShadow);
      light.position.set(-5, 2, 6);
      controls.target.set(0, 0, 0);
      camera.position.set(0, 0, 1.2);
      camera.lookAt(controls.target); camera.updateMatrixWorld(); controls.update();
      invalidateCharacterShadowGeometry();
    }""")
    states = []
    for distance in (1.2, 0.9):
        page.evaluate("""async distance => {
          const {camera, controls} = await import('./js/scene/scene.js');
          camera.position.z = distance;
          camera.lookAt(controls.target); camera.updateMatrixWorld(); controls.update();
        }""", distance)
        mesh_pixels(page, [[0,0,0]])
        center = project_mesh_points(page, [[0,0,0]])[0]
        clip = {'x': center[0]-180, 'y': center[1]-180, 'width': 360, 'height': 360}
        with Image.open(io.BytesIO(page.screenshot(clip=clip))) as image:
            gray = image.convert('L')
            rows = [[gray.getpixel((x, y)) for x in range(360)] for y in range(60, 300)]
        # A flat receiver has one monotonic transition; per-pixel sample noise
        # introduces repeated backwards steps inside that shadow edge.
        assert all(row[-1] - row[0] > 30 for row in rows)
        backwards = sum(max(left-right, 0) for row in rows for left, right in zip(row, row[1:]))
        assert backwards < len(rows) / 2, backwards
        states.append(page.evaluate("""async () => {
          const {getCharacterShadowDebugState} = await import('./js/scene/scene.js');
          const {fitCount, shadowUpdateCount} = getCharacterShadowDebugState();
          return {fitCount, shadowUpdateCount};
        }"""))
    assert states[1] == states[0]


@pytest.mark.parametrize('mip_count', [2, 3, 4])
@pytest.mark.parametrize('format_id', [28, 29, 87, 88, 91, 93, 'rgba8', 'bgra8', 'rgbx8', 'bgrx8'])
def test_uncompressed_dds_mips_keep_orientation_across_roles_and_reload(
        module_page, format_id, mip_count):
    bgra = format_id in (87, 88, 91, 93, 'bgra8', 'bgrx8')
    alpha = format_id not in (88, 93, 'rgbx8', 'bgrx8')
    top, bottom = (192, 32, 16), (16, 48, 192)

    def fixture(reverse):
        pixels = bytearray()
        for level in range(mip_count):
            size = 8 >> level
            for y in range(size):
                rgb = top if (y < size / 2) != reverse else bottom
                pixels.extend(bytes((*(rgb[::-1] if bgra else rgb),
                                     255 if alpha else 0)) * size)
        return uncompressed_dds(pixels, width=8, height=8, mip_count=mip_count,
                                dxgi_format=format_id if isinstance(format_id, int) else None,
                                bgra=bgra, alpha=alpha)

    data = {"initial": fixture(False), "reloaded": fixture(True)}
    module_page.route('**/mips-*.dds', lambda route: route.fulfill(
        content_type='image/vnd-ms.dds',
        body=data[route.request.url.rsplit('mips-', 1)[1].split('.')[0]]))
    result = module_page.evaluate('''async mipCount => {
      const T = await import('three/webgpu');
      const {uniform} = await import('three/tsl');
      const {loadDDSTexture,reloadDDSTexture} = await import('./js/textures/dds-loader.js');
      const {createGameMaterial,updateGameMaterialTextures,disposeGameMaterial} =
        await import('./js/mesh/material-profile.js');
      const renderer = new T.WebGPURenderer();
      await renderer.init();
      if(!renderer.backend.isWebGPUBackend || renderer.backend.compatibilityMode) {
        throw Error('Native WebGPU required');
      }
      const errors = [];
      renderer.backend.device.addEventListener('uncapturederror', event => errors.push(event.error.message));
      const scene = new T.Scene();
      const camera = new T.OrthographicCamera(-1,1,1,-1,0,2);camera.position.z=1;
      const quad = new T.Mesh(new T.PlaneGeometry(2,2));scene.add(quad);
      const gameMaterial = createGameMaterial(null,0xffffff);
      const nodes = gameMaterial.userData.gameMaterial.bindings;
      const results = [];
      for(const role of ['diffuse','normal_map','normal_data','light_map','material_map','emission_map']) {
        const colorSpace = role === 'diffuse' ? T.SRGBColorSpace : T.NoColorSpace;
        const target = new T.RenderTarget(8,8,{depthBuffer:false,
          colorSpace:role === 'diffuse' ? T.SRGBColorSpace : T.LinearSRGBColorSpace});
        const mipLevel = uniform(0);
        const material = new T.MeshBasicNodeMaterial();
        material.colorNode = nodes[role].textureNode.level(mipLevel).setUpdateMatrix(true).rgb;
        material.toneMapped = false;quad.material=material;
        const sample = async () => {
          renderer.setRenderTarget(target);renderer.render(scene,camera);
          const pixels = await renderer.readRenderTargetPixelsAsync(target,0,0,8,8);
          return [1,6].map(y=>Array.from(pixels.slice(y*256+16,y*256+19)));
        };
        // Compile the actual stable binding before its lazy DDS arrives.
        await sample();
        const source = await new Promise((resolve,reject)=>loadDDSTexture('/mips-initial.dds',resolve,reject));
        source.colorSpace = colorSpace;
        const mesh = {material:gameMaterial};
        updateGameMaterialTextures(mesh,{[role]:source});
        const levels = [];
        for(let level=0;level<mipCount;level++) {mipLevel.value=level;levels.push(await sample());}
        mipLevel.value=0;source.needsUpdate=true;
        const uploadedAgain=await sample();
        const reloaded=await reloadDDSTexture(source,'/mips-reloaded.dds');
        const changedLevels = [];
        for(let level=0;level<mipCount;level++) {mipLevel.value=level;changedLevels.push(await sample());}
        results.push({role,levels,uploadedAgain,changedLevels,stable:reloaded===source && nodes[role].textureNode.value===source});
        updateGameMaterialTextures(mesh,{[role]:null});
        source.dispose();material.dispose();target.dispose();
      }
      disposeGameMaterial(gameMaterial);gameMaterial.dispose();
      quad.geometry.dispose();renderer.dispose();
      return {results,errors};
    }''', mip_count)
    assert result['errors'] == []
    for state in result['results']:
        assert state['stable']
        expected = [[top, bottom] if (8 >> level) > 1 else [top, top]
                    for level in range(mip_count)]
        reversed_expected = [[bottom, top] if (8 >> level) > 1 else [bottom, bottom]
                             for level in range(mip_count)]
        for actual_levels, expected_levels in ((state['levels'], expected),
                                                (state['changedLevels'], reversed_expected)):
            assert all(max(abs(a-b) for a,b in zip(pixel, color)) <= 2
                       for actual, colors in zip(actual_levels, expected_levels)
                       for pixel, color in zip(actual, colors)), state
        assert all(max(abs(a-b) for a,b in zip(pixel, color)) <= 2
                   for pixel, color in zip(state['uploadedAgain'], [top, bottom])), state


@pytest.mark.parametrize('format_id', [None, 28, 29, 87, 88, 91, 93, 'rgbx8', 'bgrx8'])
def test_dds_upload_matches_reference_colors_and_orientation(viewer, tmp_path, format_id):
    dds = tmp_path / 'texture-01.dds'
    top, bottom = (255, 0, 0), (0, 0, 255)
    if format_id is None:
        dds.write_bytes(split_color_dds())
    else:
        top, bottom = (192, 32, 16), (16, 48, 192)
        bgra = format_id in (87, 88, 91, 93, 'bgrx8')
        alpha = format_id not in (88, 93, 'rgbx8', 'bgrx8')
        pixels = b''.join(bytes((*(color[::-1] if bgra else color), 255 if alpha else 0)) * 8
                          for color in (top, bottom))
        dds.write_bytes(uncompressed_dds(
            pixels, dxgi_format=format_id if isinstance(format_id, int) else None,
            bgra=bgra, alpha=alpha))
    reference = tmp_path / 'texture-02.png'
    with Image.new('RGB', (4, 4), top) as image:
        image.paste(bottom, (0, 2, 4, 4))
        image.save(reference)
    publication = server.begin_texture_publication(str(tmp_path))
    urls = [publication.register(str(path)) for path in [dds, reference]]
    publication.commit()
    payload = model_payload()
    mesh = payload['meshes']['mesh-00']
    mesh.update(pos=append_stream(payload, 'f', [0,0,0, 1,0,0, 1,1,0, 0,1,0]),
                uv=append_stream(payload, 'f', [0,0, 1,0, 1,1, 0,1]),
                idx=append_stream(payload, 'I', [0,1,2, 0,2,3]), drawindexed=[6,0,0],
                tex_key='diffuse::texture-01.dds')
    payload['textures'] = dict(zip(['diffuse::texture-01.dds', 'diffuse::texture-02.png'], urls))
    page = _open_fixture(viewer, payload)
    wait_texture(page)
    assert page.evaluate("""async compressed => {
      const {getGameMaterialTexture} = await import('./js/mesh/material-profile.js');
      const {renderer, rendererReady} = await import('./js/scene/scene.js');
      await rendererReady;
      const texture = getGameMaterialTexture(window.modViewer.activeMeshes[0].material, 'diffuse');
      return renderer.backend.isWebGPUBackend && !renderer.backend.compatibilityMode
        && texture.isCompressedTexture === compressed && texture.colorSpace === 'srgb';
    }""", format_id is None)
    points = [[0.5, 0.2, 0], [0.5, 0.8, 0]]
    compressed = mesh_pixels(page, points)
    page.locator('#inspector-tab').click()
    page.locator('.draw-item').click()
    page.locator('.inspector-uv-section summary').click()
    page.wait_for_function("document.querySelector('.inspector-uv-texture').dataset.previewReady === 'true'")
    uv_pixels = '''() => {
      const canvas = document.querySelector('.inspector-uv-texture');
      return [0.75,0.25].map(v => [...canvas.getContext('2d')
        .getImageData(canvas.width/2,canvas.height*v,1,1).data]);
    }'''
    compressed_uv = page.evaluate(uv_pixels)
    assert all(max(abs(a-b) for a,b in zip(actual, expected)) <= 2
               for actual, expected in zip(compressed_uv, [(*bottom, 255), (*top, 255)]))
    page.evaluate("""async () => {
      const {setManualTexOverride} = await import('./js/mesh/mesh-state.js');
      setManualTexOverride(window.modViewer.activeMeshes[0], 'diffuse::texture-02.png');
    }""")
    page.wait_for_function("""() => {
      const texture = window.__getTexture(window.modViewer.activeMeshes[0].material, 'diffuse');
      return texture?.image && !texture.isCompressedTexture;
    }""")
    reference_pixels = mesh_pixels(page, points)
    page.wait_for_function("document.querySelector('.inspector-uv-texture').dataset.previewReady === 'true'")
    assert page.evaluate(uv_pixels) == compressed_uv
    assert compressed[0][2] > compressed[0][0] + 60
    assert compressed[1][0] > compressed[1][2] + 60
    assert all(max(abs(a-b) for a,b in zip(left, right)) < 40
               for left, right in zip(compressed, reference_pixels))


def test_model_dds_sampling_repeats_tiled_uvs_across_reload(viewer):
    payload = model_payload()
    payload['meshes']['mesh-00']['tex_key'] = 'diffuse::texture-01.dds'
    payload['textures'] = {'diffuse::texture-01.dds': '/texture-01.dds'}
    page = viewer({'fixture-01': payload})
    page.route('**/texture-01.dds*', lambda route: route.fulfill(
        content_type='image/vnd-ms.dds', body=split_color_dds()))
    open_model(page, 'fixture-01')
    wait_loaded(page)
    wait_texture(page)
    points = [[.1,.2,0], [.1,.7,0]]
    baseline = mesh_pixels(page, points)
    assert max(abs(a-b) for a,b in zip(*baseline)) > 60
    for shift in (8, -8):
        assert page.evaluate('''async shift => {
          const T = await import('three');
          const {getGameMaterialTexture} = await import('./js/mesh/material-profile.js');
          const mesh = window.modViewer.activeMeshes[0], uv = mesh.geometry.attributes.uv;
          uv.array.set([shift,shift,shift+1,shift,shift,shift+1]); uv.needsUpdate = true;
          const texture = getGameMaterialTexture(mesh.material,'diffuse');
          return texture.wrapS === T.RepeatWrapping && texture.wrapT === T.RepeatWrapping;
        }''', shift)
        tiled = mesh_pixels(page, points)
        assert all(max(abs(a-b) for a,b in zip(before, after)) <= 2
                   for before, after in zip(baseline, tiled))
    page.evaluate('''async () => {
      const {reloadTextures} = await import('./js/mesh/mesh-factory.js');
      await reloadTextures(['diffuse::texture-01.dds'],{force:true});
    }''')
    assert all(max(abs(a-b) for a,b in zip(before, after)) <= 2
               for before, after in zip(baseline, mesh_pixels(page, points)))
    assert page.evaluate('''() => [...window.modViewer.activeMeshes[0].geometry.attributes.uv.array]''') == [
        -8, -8, -7, -8, -8, -7]


def test_unaligned_dds_replacement_fails_locally_and_preserves_viewer(viewer, tmp_path):
    path = tmp_path / 'texture-01.dds'
    path.write_bytes(split_color_dds())
    publication = server.begin_texture_publication(str(tmp_path))
    url = publication.register(str(path))
    publication.commit()
    # A published source can change before its lazy browser request arrives.
    path.write_bytes(dx10_dds(mode6_block() * 2, width=7, height=4, dxgi_format=99))
    try:
        payload = textured_payload()
        key = 'diffuse::texture-01.dds'
        payload['textures'] = {key: url}
        payload['meshes']['mesh-00']['tex_key'] = key
        page = viewer({'fixture-01': payload, 'fixture-02': model_payload()})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.evaluate('''async () => {
          const {hasTexture} = await import('./js/mesh/mesh-factory.js');
          window.hasFixtureTexture = hasTexture;
          const {renderer} = await import('./js/scene/scene.js');
          window.gpuErrors = [];
          renderer.backend.device.addEventListener('uncapturederror',event=>window.gpuErrors.push(event.error.message));
        }''')
        open_model(page, 'fixture-01')
        wait_loaded(page)
        page.wait_for_function('key => !window.hasFixtureTexture(key)', arg=key)
        mesh_pixel(page)
        assert page.locator('#renderer-error').is_hidden()
        open_model(page, 'fixture-02')
        wait_loaded(page)
        mesh_pixel(page)
        assert page.evaluate('window.gpuErrors') == []
        assert page.locator('#renderer-error').is_hidden()
        assert errors == []
    finally:
        publication.discard()


def test_active_uv_refreshes_when_dds_is_replaced_at_the_same_url(viewer, tmp_path):
    path = tmp_path / 'texture-01.dds'
    original = split_color_dds()
    path.write_bytes(original)
    publication = server.begin_texture_publication(str(tmp_path))
    url = publication.register(str(path))
    publication.commit()
    try:
        payload = textured_payload()
        payload['textures']['diffuse::texture-01.dds'] = url
        payload['meshes']['mesh-00']['tex_key'] = 'diffuse::texture-01.dds'
        page = _open_fixture(viewer, payload)
        wait_texture(page)
        page.locator('#inspector-tab').click()
        page.locator('.draw-item').click()
        page.locator('.inspector-uv-section summary').click()
        page.wait_for_function("document.querySelector('.inspector-uv-texture').dataset.previewReady === 'true'")
        page.locator('.inspector-uv-expand').click()
        page.evaluate('''() => {
          window.uvCanvas = document.querySelector('.inspector-uv-outlines');
          window.uvWireframe = uvCanvas.toDataURL();
          window.uvPixels = () => {
            const canvas = document.querySelector('.inspector-uv-texture');
            return [0.25,0.75].map(y=>[...canvas.getContext('2d')
              .getImageData(canvas.width/2,canvas.height*y,1,1).data]);
          };
        }''')
        page.wait_for_function('uvPixels()[0][0] > 240 && uvPixels()[1][2] > 240')
        path.write_bytes(original[:-4] + struct.pack('<I', 0x00005555))
        assert page.evaluate('''async url => {
          const {addTexture,getTexturePreviewSource} = await import('./js/mesh/mesh-factory.js');
          const {setManualTexOverride} = await import('./js/mesh/mesh-state.js');
          const mesh = window.modViewer.activeMeshes[0];
          addTexture(mesh.userData.texKey,url);
          setManualTexOverride(mesh,mesh.userData.texKey);
          return getTexturePreviewSource(mesh.userData.texKey) === url;
        }''', url)
        page.wait_for_function('uvPixels()[0][2] > 240 && uvPixels()[1][0] > 240')
        assert page.evaluate("uvCanvas === document.querySelector('.inspector-uv-outlines') && uvCanvas.toDataURL() === uvWireframe")
        assert page.locator('.uv-viewport').is_visible()
    finally:
        publication.release()


@pytest.mark.parametrize('alpha', [0, 128])
def test_menu_dds_canvas_preserves_pixels_and_viewport(viewer, tmp_path, alpha):
    expected = [(96, 144, 192, alpha), (192, 96, 48, alpha)]
    transparent_expected = expected if alpha else [(0, 0, 0, 0)] * 2
    blocks = []
    for *rgb, alpha in expected:
        bits = int.from_bytes(mode6_block(tuple((value, value) for value in rgb)), 'little')
        for offset in (49, 56):
            bits = (bits & ~(127 << offset)) | ((alpha >> 1) << offset)
        blocks.append(bits.to_bytes(16, 'little'))
    path = tmp_path / 'menu.dds'
    path.write_bytes(dx10_dds(b''.join(blocks), dxgi_format=99, width=4, height=8))
    publication = server.begin_texture_publication(str(tmp_path))
    url = publication.register_menu_image(str(path))
    publication.commit()
    try:
        page = _open_fixture(viewer, textured_payload())
        wait_texture(page)
        before_pixels = mesh_pixel(page)
        before_state = page.evaluate("""async url => {
          const {renderer} = await import('./js/scene/scene.js');
          window.previewState = () => [renderer.getRenderTarget()?.uuid || null,
            renderer.getClearAlpha(), renderer.getScissorTest(), renderer.outputColorSpace,
            renderer.toneMapping, renderer.getPixelRatio(), renderer.autoClear];
          window.previewReads = 0;
          const read = renderer.readRenderTargetPixelsAsync.bind(renderer);
          renderer.readRenderTargetPixelsAsync = (...args) => {window.previewReads++;return read(...args);};
          const before = previewState();
          const {buildMenuPanel} = await import('./js/panels/menu-panel.js');
          buildMenuPanel({option01: {name: 'Option01', var: 'option01', values: ['0', '1'],
            default: '0', image: url}});
          return before;
        }""", url)
        page.wait_for_function("document.querySelector('#menu-list canvas')?.dataset.previewReady === 'true'")
        result = page.evaluate("""() => ({state: previewState(), pixels: [64, 192].map(y =>
          [...document.querySelector('#menu-list canvas').getContext('2d').getImageData(128, y, 1, 1).data])})""")
        assert result['state'] == before_state
        assert all(max(abs(left - right) for left, right in zip(actual, reference)) <= 4
                   for actual, reference in zip(result['pixels'], transparent_expected))
        assert max(abs(left - right) for left, right in zip(before_pixels, mesh_pixel(page))) < 5
        page.evaluate('''async url => {
          const {addTexture} = await import('./js/mesh/mesh-factory.js');
          const {setManualTexOverride} = await import('./js/mesh/mesh-state.js');
          addTexture('diffuse::texture-03.dds',url);
          setManualTexOverride(window.modViewer.activeMeshes[0],'diffuse::texture-03.dds');
        }''', url)
        wait_texture(page)
        dds_requests = []
        page.on('request', lambda request: dds_requests.append(request.url)
                if request.url.split('?')[0] == url else None)
        page.evaluate('''async () => {
          const {renderer} = await import('./js/scene/scene.js');
          const {getReadyTextureForPreview} = await import('./js/mesh/mesh-factory.js');
          const texture = getReadyTextureForPreview('diffuse::texture-03.dds');
          window.uvBorrowedTexture = texture;
          window.uvTextureVersion = texture.version;
          window.uvTextureDisposals = 0;
          texture.addEventListener('dispose',()=>uvTextureDisposals++);
          window.uvPreviewTargets = [];
          const render = renderer.render.bind(renderer);
          renderer.render = (scene,camera) => {
            if(scene.children.some(child=>child.material?.map===texture)) {
              const target = renderer.getRenderTarget();
              uvPreviewTargets.push([target.width,target.height]);
            }
            return render(scene,camera);
          };
        }''')
        page.locator('#inspector-tab').click()
        page.locator('.draw-item').click()
        state_before_uv = page.evaluate('previewState()')
        page.locator('.inspector-uv-section summary').click()
        page.wait_for_function("document.querySelector('.inspector-uv-texture').dataset.previewReady === 'true'")
        uv = page.locator('.inspector-uv-texture').evaluate('''canvas => ({
          aspect:canvas.width/canvas.height,
          pixels:[0.25,0.75].map(y => [...canvas.getContext('2d')
            .getImageData(canvas.width/2,canvas.height*y,1,1).data])})''')
        assert 0.45 < uv['aspect'] < 0.6
        assert all(max(abs(left-right) for left,right in zip(actual,reference)) <= 4
                   for actual,reference in zip(uv['pixels'], [(*rgba[:3], 255) for rgba in expected]))
        alpha_preview = page.evaluate('''async url => {
          const {getDDSPreview} = await import('./js/textures/dds-preview.js');
          const canvas = await getDDSPreview(url,()=>true,{size:32,preserveAspect:true});
          return [0.25,0.75].map(y => [...canvas.getContext('2d')
            .getImageData(canvas.width/2,canvas.height*y,1,1).data]);
        }''', url)
        assert all(max(abs(left-right) for left,right in zip(actual,reference)) <= 4
                   for actual,reference in zip(alpha_preview, transparent_expected))
        assert page.evaluate('previewState()') == state_before_uv
        inline_size = page.evaluate('Math.max(...uvPreviewTargets.at(-1))')
        assert inline_size <= 512
        reads_before_expansion = page.evaluate('previewReads')
        page.locator('.inspector-uv-expand').click()
        page.wait_for_function('Math.max(...uvPreviewTargets.at(-1)) > 512')
        page.wait_for_function("document.querySelector('.uv-viewport-status').hidden")
        assert page.evaluate('previewReads') == reads_before_expansion + 1
        assert page.evaluate('''() => {
          const canvas = document.querySelector('.inspector-uv-texture');
          return Math.max(...uvPreviewTargets.at(-1)) >= Math.max(canvas.width,canvas.height)-32;
        }''')
        expanded_pixels = page.locator('.inspector-uv-texture').evaluate('''canvas =>
          [0.25,0.75].map(y => [...canvas.getContext('2d')
            .getImageData(canvas.width/2,canvas.height*y,1,1).data])''')
        assert expanded_pixels == uv['pixels']
        page.keyboard.press('Escape')
        assert page.locator('.uv-viewport').is_visible()
        page.locator('.uv-viewport-close').click()
        page.locator('.inspector-uv-expand').click()
        assert page.evaluate('previewReads') == reads_before_expansion + 1
        size_before_resize = page.evaluate('Math.max(...uvPreviewTargets.at(-1))')
        page.set_viewport_size({'width': 1440, 'height': 1200})
        page.wait_for_function('size => Math.max(...uvPreviewTargets.at(-1)) > size', arg=size_before_resize)
        page.wait_for_function("document.querySelector('.uv-viewport-status').hidden")
        assert page.evaluate('previewReads') == reads_before_expansion + 2
        page.set_viewport_size({'width': 1280, 'height': 900})
        page.locator('.uv-viewport-close').click()
        assert page.evaluate('previewReads') == reads_before_expansion + 2
        assert page.evaluate('uvBorrowedTexture.version === uvTextureVersion && uvTextureDisposals === 0')
        assert dds_requests == []
        assert page.evaluate('previewState()') == state_before_uv
        cache = page.evaluate('''async url => {
          const {getDDSPreview,clearDDSPreviews} = await import('./js/textures/dds-preview.js');
          const {getReadyTextureForPreview} = await import('./js/mesh/mesh-factory.js');
          const texture = getReadyTextureForPreview('diffuse::texture-03.dds');
          let disposals = 0; texture.addEventListener('dispose',()=>disposals++);
          const options = {size:32,preserveAspect:true,preserveAlpha:false,texture};
          const before = previewReads;
          const a = await getDDSPreview(url,()=>true,options);
          const b = await getDDSPreview(url,()=>true,options);
          const reused = a === b && previewReads === before;
          clearDDSPreviews();
          const [c,d] = await Promise.all([getDDSPreview(url,()=>true,options),getDDSPreview(url,()=>true,options)]);
          const shared = c === d && previewReads === before+1;
          for(let i=0;i<9;i++) await getDDSPreview(`${url}?fixture=${i}`,()=>true,options);
          const evictedBefore = previewReads;
          await getDDSPreview(url,()=>true,options);
          const bounded = previewReads === evictedBefore+1;
          clearDDSPreviews(url);
          await getDDSPreview(url,()=>true,options);
          const invalidated = previewReads===evictedBefore+2;
          clearDDSPreviews();
          const large = {size:2048,texture};
          const largeBefore = previewReads;
          await getDDSPreview(url,()=>true,large);
          await getDDSPreview(`${url}?fixture=large`,()=>true,large);
          await getDDSPreview(`${url}?fixture=large`,()=>true,large);
          const largeReused = previewReads===largeBefore+2;
          await getDDSPreview(url,()=>true,large);
          return {reused,shared,bounded,invalidated,disposals,
            byteBounded:largeReused && previewReads===largeBefore+3};
        }''', url)
        assert cache == {'reused': True, 'shared': True, 'bounded': True, 'invalidated': True,
                         'disposals': 0, 'byteBounded': True}
    finally:
        publication.release()


def test_weight_rig_lazy_load_pose_deforms_vertices_and_ui_reset_restores_them(viewer):
    payload, weights = weighted_payload(include_ineligible=True)
    page = _open_fixture(viewer, payload, {'fixture-weights': weights})
    page.evaluate('window.modViewer.activeMeshes[1].visible = false')
    assert bridge_calls(page, 'weights') == []
    _activate_rig(page, humanoid=True)

    assert page.evaluate("""() => {
      const rig = window.__rigApi;
      rig.beginWeightModelPicking();
      rig.beginRigJointPicking({type: 'selected-joint'});
      const rigOwnsPicking = !rig.getModelWeightState().picking
        && rig.getModelRigState().jointPickIntent?.type === 'selected-joint';
      rig.beginWeightModelPicking();
      const weightOwnsPicking = rig.getModelWeightState().picking && !rig.getModelRigState().jointPickIntent;
      rig.cancelWeightModelPicking();
      return rigOwnsPicking && weightOwnsPicking;
    }""")
    page.evaluate("""() => {
      const rig = window.__rigApi, source = rig.getModelWeightState().sources[0];
      rig.setBoneSelected(source.key, source.availableBoneIds.at(-1), true);
    }""")
    page.wait_for_function('window.__rigApi.getModelPhysicsState().participantCount > 0')
    assert page.evaluate("""() => {
      const rig = window.__rigApi, frequency = rig.getModelPhysicsState().frequencyHz;
      rig.setPhysicsFrequency(frequency + 1.25);
      const changed = rig.getModelPhysicsState().frequencyHz === frequency + 1.25;
      rig.resetModelPhysics();
      return changed && rig.getModelPhysicsState().frequencyHz === frequency;
    }""")
    assert page.evaluate("""() => {
      const rig = window.__rigApi;
      rig.clearSelectedBones();
      const physics = rig.getModelPhysicsState();
      return rig.getModelWeightState().selectedBones.length === 0 && !physics.enabled && physics.participantCount === 0;
    }""")

    joint = page.evaluate("""() => {
      const model = window.__rigApi.getModelRigState().model;
      window.__rigJoint = model.components[0].nodeIds.find(id => id !== model.components[0].rootId);
      window.__rigPosition = window.modViewer.activeMeshes[0].geometry.attributes.position;
      window.__rigBaseline = [...window.__rigPosition.array];
      return window.__rigJoint;
    }""")
    page.locator('.rig-bone-select').select_option(str(joint))
    baseline_pixel = mesh_pixel(page)
    assert page.evaluate("""() => {
      const rig = window.__rigApi;
      rig.setRigJointRotation(window.__rigJoint, [0, 0, Math.SQRT1_2, Math.SQRT1_2], {dragging: true});
      rig.finishRigJointPose(window.__rigJoint);
      return [...window.__rigPosition.array].some((value, i) => Math.abs(value-window.__rigBaseline[i]) > 1e-4);
    }""")
    posed_pixel = mesh_pixel(page)
    assert sum(abs(a-b) for a,b in zip(baseline_pixel, posed_pixel)) > 30
    page.locator('.rig-reset-pose').click()
    assert page.evaluate("""() =>
      window.modViewer.activeMeshes[0].geometry.attributes.position === window.__rigPosition
      && window.__rigPosition.array.every((value, i) => Math.abs(value-window.__rigBaseline[i]) < 1e-5)
    """)
    restored_pixel = mesh_pixel(page)
    assert max(abs(a-b) for a,b in zip(baseline_pixel, restored_pixel)) < 15

    restored = page.evaluate("""async () => {
      const rig = window.__rigApi, api = window.pywebview.api;
      const component = rig.getModelRigState().model.components[0], originalRoot = component.rootId;
      const presetRoot = component.nodeIds.find(id => id !== originalRoot);
      rig.setRigJointRoot(presetRoot);
      rig.setRigJointRotation(originalRoot, [0, 0, Math.SQRT1_2, Math.SQRT1_2]);
      rig.finishRigJointPose(originalRoot);
      let captured;
      const previousSave = api.save_rig_pose_preset;
      api.save_rig_pose_preset = async (path, preset) => {
        captured = preset;
        return {saved: true, presets: [preset]};
      };
      await rig.saveRigPosePreset('Pose 01');
      api.save_rig_pose_preset = previousSave;
      rig.resetRigPose();
      const applied = rig.applyRigPosePresetById(captured.id), state = rig.getModelRigState();
      return {success: applied.success, root: state.model.components[0].rootId,
        posed: Object.hasOwn(state.model.poseRotationByJointId, String(originalRoot)), expectedRoot: presetRoot};
    }""")
    assert restored['success'] and restored['posed']
    assert restored['root'] == restored['expectedRoot']
    assert bridge_calls(page, 'weights') == [['fixture-01']]


def test_weight_rig_overlapping_unregister_rebuilds_using_final_membership(viewer):
    payload, weights = weighted_payload(include_third_member=True)
    page = _open_fixture(viewer, payload, {'fixture-weights': weights})
    page.evaluate('window.modViewer.activeMeshes.slice(1).forEach(mesh => { mesh.visible = false; })')
    _activate_rig(page, humanoid=True)
    initial_joints = page.evaluate('window.__rigApi.getModelRigState().model.joints.length')

    for index in (1, 2):
        assert page.evaluate("""async index => {
          const {unregisterWeightRigMesh} = await import('./js/weight-rig/weight-rig-core.js');
          unregisterWeightRigMesh(window.modViewer.activeMeshes[index]);
          const state = window.__rigApi.getModelRigState();
          return state.loading && !state.loaded && state.model === null;
        }""", index)
    page.wait_for_function('window.__rigApi.getModelRigState().loaded && !window.__rigApi.getModelRigState().loading')
    rebuilt_joints = page.evaluate('window.__rigApi.getModelRigState().model.joints.length')
    assert initial_joints > rebuilt_joints == 2


def test_weight_rig_shape_change_invalidates_and_rebuilds_preserving_root(viewer):
    payload, weights = weighted_payload()
    payload['meshes']['mesh-00']['shape_targets'] = [
        {'var': 'shape01', 'pos': append_stream(payload, 'f', [0, 0, 0, 2, 0, 0, 0, 2, 0])},
    ]
    payload['state']['defaults'] = {'shape01': '0'}
    page = _open_fixture(viewer, payload, {'fixture-weights': weights})
    _activate_rig(page, humanoid=True)
    joint = page.evaluate("""() => {
      const rig = window.__rigApi, component = rig.getModelRigState().model.components[0];
      const joint = component.nodeIds.find(id => id !== component.rootId);
      rig.setRigJointRoot(joint);
      return joint;
    }""")

    assert page.evaluate("""async () => {
      const {setControlValue} = await import('./js/editing/control-state.js');
      const {refreshMeshes} = await import('./js/mesh/mesh-state.js');
      setControlValue('shape01', '1'); refreshMeshes({force: {shapes: true}});
      const state = window.__rigApi.getModelRigState();
      return !state.loaded && !state.loading && state.model === null;
    }""")
    rebuilt = page.evaluate("""async () => {
      await window.__rigApi.activateWeightRig();
      const state = window.__rigApi.getModelRigState();
      return {root: state.model.components[0].rootId, selected: state.selectedJointId};
    }""")
    assert rebuilt == {'root': joint, 'selected': None}


def test_weight_rig_collapsed_source_keeps_weights_and_other_physics_then_recovers(viewer):
    payload, weights = weighted_payload(include_second_member=True)
    weights['meshes']['mesh-01']['source'] = {
        'key': 'stream-02.buf|offset=0', 'file': 'stream-02.buf', 'bone_id_offset': 0,
    }
    payload['meshes']['mesh-01']['shape_targets'] = [
        {'var': 'shape01', 'pos': append_stream(payload, 'f', [0] * 9)},
    ]
    payload['state']['defaults'] = {'shape01': '0'}
    page = _open_fixture(viewer, payload, {'fixture-weights': weights})
    _activate_rig(page)
    page.evaluate("""() => {
      const rig = window.__rigApi;
      rig.getModelWeightState().sources.forEach(source => rig.setBoneSelected(source.key, source.availableBoneIds.at(-1), true));
    }""")
    page.wait_for_function('window.__rigApi.getModelPhysicsState().participantCount === 2')

    for value, participants, errors in [('1', 1, 1), ('0', 2, 0)]:
        state = page.evaluate("""async value => {
          const {setControlValue} = await import('./js/editing/control-state.js');
          const {refreshMeshes} = await import('./js/mesh/mesh-state.js');
          setControlValue('shape01', value); refreshMeshes({force: {shapes: true}});
          await window.__rigApi.activateWeightRig();
          const weight = window.__rigApi.getModelWeightState(), rig = window.__rigApi.getModelRigState();
          return {selections: weight.selectedBones.length, errors: Object.keys(rig.sourceErrors).length};
        }""", value)
        assert state == {'selections': 2, 'errors': errors}
        page.wait_for_function(
            'expected => window.__rigApi.getModelPhysicsState().participantCount === expected',
            arg=participants,
        )
        if errors:
            assert 'Rig unavailable: no usable faces.' in page.locator('.weight-rig-status').inner_text()

"""Geometry-only fitting contracts using neutral generated surface fixtures."""

import pytest


def prepare_surface(page):
    page.evaluate("""async () => {
      const THREE = await import('three');
      const api = await import('./js/weight-rig/humanoid-geometry-fit.js');
      const meshes = [];
      const add = geometry => {
        const mesh = new THREE.Mesh(geometry);
        mesh.userData.humanoidRestPositions = new Float32Array(geometry.attributes.position.array);
        meshes.push(mesh); return mesh;
      };
      const tube = (a,b,radius) => {
        const first = new THREE.Vector3(...a), end = new THREE.Vector3(...b);
        const geometry = new THREE.CylinderGeometry(radius, radius, first.distanceTo(end), 16, 6);
        geometry.applyQuaternion(new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0,1,0),
          end.clone().sub(first).normalize()));
        geometry.translate(...first.add(end).multiplyScalar(0.5).toArray()); add(geometry);
      };
      tube([0,1.02,0],[0,1.6,0],0.19);
      tube([0,1.6,0],[0,1.73,0],0.065);
      const head = new THREE.SphereGeometry(0.17,24,16); head.translate(0,1.83,0); add(head);
      const reference = {};
      for (const [prefix,side] of [['left',-1],['right',1]]) {
        const shoulder=[side*0.19,1.6,0], elbow=[side*0.47,1.39,0], hand=[side*0.73,1.16,0];
        const hip=[side*0.105,1.06,0], knee=[side*0.11,0.6,0], foot=[side*0.11,0.06,0.01];
        tube(shoulder,elbow,0.055); tube(elbow,hand,0.04);
        tube(hip,knee,0.075); tube(knee,foot,0.055);
        const shoe=new THREE.BoxGeometry(0.13,0.12,0.22); shoe.translate(side*0.11,0.06,0.03); add(shoe);
        Object.assign(reference,{[prefix+'Shoulder']:shoulder,[prefix+'Elbow']:elbow,[prefix+'Hand']:hand,
          [prefix+'Hip']:hip,[prefix+'Knee']:knee,[prefix+'Foot']:foot});
      }
      window.fixture={THREE,api,meshes,reference,axes:{up:[0,1,0],right:[1,0,0],forward:[0,0,1]}};
    }""")


@pytest.mark.parametrize('z_up', [False, True])
def test_geometry_rig_orientation_accuracy_and_topology_without_weights(module_page, z_up):
    prepare_surface(module_page)
    result = module_page.evaluate("""async zUp => {
      const {THREE,api,meshes,reference,axes}=fixture;
      const first=await api.fitHumanoidGeometryRig({meshes,axes});
      const rotation=new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1,0,0),zUp?Math.PI/2:0);
      for (const mesh of meshes) {
        mesh.geometry.applyQuaternion(rotation);
        mesh.userData.humanoidRestPositions=new Float32Array(mesh.geometry.attributes.position.array);
        // The current mesh is deformed; the fitter must still consume rest data.
        mesh.geometry.attributes.position.array.fill(99);
      }
      const fit=await api.fitHumanoidGeometryRig({meshes,orientationState:{orientationInitialized:true,
        baseOrientation:rotation.clone().invert().toArray()}});
      const error=(rig,key)=>new THREE.Vector3(...rig.controls[key].position)
        .distanceTo(new THREE.Vector3(...reference[key]))/rig.frame.height;
      return {available:fit.available, keys:Object.keys(fit.controls), paths:fit.paths,
        maxOrientationError:Math.max(...Object.keys(first.controls).map(key=>
          new THREE.Vector3(...fit.controls[key].position).applyQuaternion(rotation.clone().invert())
            .distanceTo(new THREE.Vector3(...first.controls[key].position)))),
        armError:['leftShoulder','leftElbow','leftHand'].map(key=>[error(first.proportionalRig,key),error(first,key)]),
        joints:first.diagnostics.joints};
    }""", z_up)
    assert result['available']
    assert len(result['keys']) == 16
    assert sorted(map(len, result['paths'].values())) == [3, 3, 3, 3, 4]
    assert result['maxOrientationError'] < 0.025
    assert sum(row[1] for row in result['armError']) < sum(row[0] for row in result['armError'])
    assert all(item['support'] >= 0 and item['deviationHeight'] >= 0
               for item in result['joints'].values())


def test_geometry_rig_sampling_visibility_duplicates_transforms_and_determinism(module_page):
    prepare_surface(module_page)
    result = module_page.evaluate("""async () => {
      const {THREE,api,meshes,axes}=fixture;
      const fit=()=>api.fitHumanoidGeometryRig({meshes,axes});
      const before=await fit(), repeat=await fit();
      const rest=meshes.map(mesh=>[...mesh.userData.humanoidRestPositions]);
      const duplicate=new THREE.Mesh(meshes[0].geometry.clone());
      duplicate.userData.humanoidRestPositions=new Float32Array(rest[0]); meshes.push(duplicate);
      const hidden=new THREE.Mesh(new THREE.BoxGeometry(100,100,100)); hidden.visible=false;
      hidden.userData.humanoidRestPositions=hidden.geometry.attributes.position.array; meshes.push(hidden);
      const group=new THREE.Group(); group.visible=false;
      const child=hidden.clone(); child.visible=true; group.add(child); meshes.push(child);
      // Asset Fill is displayed geometry and must contribute to the surface.
      meshes[1].userData.assetFill=true;
      const moved=new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0,1,0),0.6);
      for (const mesh of meshes.filter(mesh=>mesh.parent!==group)) {
        mesh.quaternion.copy(moved); mesh.position.set(4,5,6); mesh.scale.setScalar(1.5);
      }
      const after=await fit();
      const capped=await api.sampleHumanoidRestSurface({meshes,options:{maxTriangles:200,maxSamples:300}});
      const noEvidence=await api.fitHumanoidGeometryRig({meshes:[hidden],axes});
      return {same:JSON.stringify(before.controls)===JSON.stringify(repeat.controls),
        duplicateDifference:Math.max(...Object.keys(before.controls).map(key=>
          new THREE.Vector3(...before.controls[key].position).distanceTo(new THREE.Vector3(...after.controls[key].position)))),
        uniqueBefore:before.diagnostics.uniqueTriangleCount,uniqueAfter:after.diagnostics.uniqueTriangleCount,
        sourceCount:after.diagnostics.sourceCount,capped:capped.diagnostics,noEvidence:noEvidence.available,
        maxAsymmetry:Math.max(...['Shoulder','Elbow','Hand','Hip','Knee','Foot'].map(suffix=>{
          const a=before.controls['left'+suffix].position,b=before.controls['right'+suffix].position;
          return Math.hypot(a[0]+b[0],a[1]-b[1],a[2]-b[2])/before.frame.height;})),
        unchanged:rest.every((value,i)=>JSON.stringify(value)===JSON.stringify([...meshes[i].userData.humanoidRestPositions]))};
    }""")
    assert result['same'] and result['unchanged']
    assert result['uniqueBefore'] == result['uniqueAfter']
    assert result['duplicateDifference'] < 0.005
    assert result['sourceCount'] >= 2
    assert result['capped']['visitedTriangleCount'] <= 200
    assert result['capped']['sampledPointCount'] <= 300
    assert result['capped']['scanCapped']
    assert not result['noEvidence']
    assert result['maxAsymmetry'] < 0.035


def test_geometry_preview_lifecycle_is_weight_free_and_preserves_active_state(viewer):
    from .payloads import model_payload
    from .support import bridge_calls, open_model, wait_loaded

    payload = model_payload()
    payload['meshes']['mesh-00']['skinning_available'] = True
    page = viewer({'fixture-01': payload, 'fixture-02': payload, 'fixture-weights': {'meshes': {}}})
    open_model(page, 'fixture-01')
    wait_loaded(page)
    prepare_surface(page)
    page.locator('#weight-rig-tab').click()
    page.locator('.rig-preview-geometry').evaluate("button => button.closest('details').open = true")
    page.evaluate("""async () => {
      const {weightRigApi,registerWeightRigMesh}=await import('./js/weight-rig/weight-rig-core.js');
      window.previewApi=weightRigApi;
      fixture.meshes.forEach(registerWeightRigMesh);
      window.hiddenPreviewGroup=new fixture.THREE.Group(); hiddenPreviewGroup.visible=false;
      const hiddenChild=new fixture.THREE.Mesh(fixture.meshes[0].geometry.clone());
      hiddenChild.userData.humanoidRestPositions=new Float32Array(hiddenChild.geometry.attributes.position.array);
      hiddenChild.geometry.attributes.position.array.fill(99);
      hiddenPreviewGroup.add(hiddenChild);registerWeightRigMesh(hiddenChild);
      window.beforePreview=JSON.stringify(weightRigApi.getModelRigState());
      window.beforeRest=fixture.meshes.map(mesh=>[...mesh.geometry.attributes.position.array]);
    }""")
    page.locator('.rig-preview-geometry').evaluate("button => button.click()")
    page.wait_for_function('previewApi.getModelRigState().geometryRigPreview?.rig?.available')
    assert bridge_calls(page, 'weights') == []
    assert page.locator('.rig-preview-comparison').is_enabled()
    summary = page.locator('.rig-preview-geometry').locator('..').inner_text()
    assert 'samples' in summary
    page.locator('.rig-preview-comparison').select_option('proportional')
    assert page.evaluate("previewApi.getModelRigState().geometryRigPreview.rig.mode") == 'proportional_template'
    page.locator('.rig-preview-comparison').select_option('geometry')
    page.locator('.rig-preview-comparison').select_option('current')
    assert page.locator('.weight-rig-advanced input[type="range"]').first.is_enabled()
    assert page.evaluate("JSON.stringify(previewApi.getModelRigState()) === beforePreview")
    assert page.evaluate("beforeRest.every((rest,i)=>JSON.stringify(rest)===JSON.stringify([...fixture.meshes[i].geometry.attributes.position.array]))")
    result = page.evaluate("""async () => {
      fixture.meshes[0].geometry.attributes.position.array[0]+=0.1;
      const rejected=await previewApi.previewGeometryRig();
      const reason=previewApi.getModelRigState().geometryRigPreview.errorKey;
      fixture.meshes[0].geometry.attributes.position.array[0]=beforeRest[0][0];
      previewApi.clearGeometryRigPreview();
      const pending=previewApi.previewGeometryRig();previewApi.clearGeometryRigPreview();await pending;
      const visibilityPending=previewApi.previewGeometryRig();hiddenPreviewGroup.visible=true;await visibilityPending;
      hiddenPreviewGroup.visible=false;
      return {rejected,reason,preview:previewApi.getModelRigState().geometryRigPreview};
    }""")
    assert result == {'rejected': False, 'reason': 'weightRig.geometryRequiresRest', 'preview': None}
    page.evaluate('previewApi.previewGeometryRig()')
    open_model(page, 'fixture-02')
    wait_loaded(page)
    assert page.evaluate('previewApi.getModelRigState().geometryRigPreview') is None
    assert bridge_calls(page, 'weights') == []
    page.locator('.rig-load-current').click()
    page.wait_for_function('previewApi.getModelWeightState().loaded')
    assert len(bridge_calls(page, 'weights')) == 1

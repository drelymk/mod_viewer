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
      const neck=first.controls.neck.position;
      const elbow=new THREE.Vector3(...first.controls.leftElbow.position);
      const extremity=new THREE.Vector3(...reference.leftHand);
      const wrist=new THREE.Vector3(...first.controls.leftHand.position);
      const forearm=extremity.clone().sub(elbow);
      const along=wrist.clone().sub(elbow).dot(forearm)/forearm.lengthSq();
      return {available:fit.available, keys:Object.keys(fit.controls), paths:fit.paths,
        maxOrientationError:Math.max(...Object.keys(first.controls).map(key=>
          new THREE.Vector3(...fit.controls[key].position).applyQuaternion(rotation.clone().invert())
            .distanceTo(new THREE.Vector3(...first.controls[key].position)))),
        armError:['leftShoulder','leftElbow','leftHand'].map(key=>[error(first.proportionalRig,key),error(first,key)]),
        joints:first.diagnostics.joints,neckError:Math.abs(neck[1]-1.665)/first.frame.height,
        neckBetween:neck[1]>first.controls.leftShoulder.position[1] && neck[1]<first.controls.head.position[1],
        wristAlong:along,wristAlignment:wrist.distanceTo(elbow.clone().addScaledVector(forearm,along))/first.frame.height};
    }""", z_up)
    assert result['available']
    assert len(result['keys']) == 16
    assert sorted(map(len, result['paths'].values())) == [3, 3, 3, 3, 4]
    assert result['maxOrientationError'] < 0.025
    assert sum(row[1] for row in result['armError']) < sum(row[0] for row in result['armError'])
    assert all(item['support'] >= 0 and item['deviationHeight'] >= 0
               for item in result['joints'].values())
    assert result['neckBetween'] and result['neckError'] < 0.025
    assert 0 < result['wristAlong'] < 1.05
    assert result['wristAlignment'] < 0.025


def test_geometry_rig_obscured_legs_preserve_anatomical_estimates(module_page):
    prepare_surface(module_page)
    result = module_page.evaluate("""async () => {
      const {THREE,api,meshes,axes}=fixture;
      const before=await api.fitHumanoidGeometryRig({meshes,axes});
      meshes[5].visible=false;meshes[10].visible=false;
      const geometry=new THREE.CylinderGeometry(0.14,0.5,0.56,32,8);
      geometry.translate(0,0.96,0);
      const skirt=new THREE.Mesh(geometry);
      skirt.userData.humanoidRestPositions=new Float32Array(geometry.attributes.position.array);
      const after=await api.fitHumanoidGeometryRig({meshes:[...meshes,skirt],axes});
      const hidden=['leftHip','rightHip','leftKnee','rightKnee'];
      return {available:after.available,
        hiddenDeviation:Math.max(...hidden.map(key=>new THREE.Vector3(...after.controls[key].position)
          .distanceTo(new THREE.Vector3(...after.proportionalRig.controls[key].position))/after.frame.height)),
        evidence:hidden.map(key=>after.diagnostics.joints[key].geometryDetected),
        footDisplacement:Math.max(...['leftFoot','rightFoot'].map(key=>new THREE.Vector3(...before.controls[key].position)
          .distanceTo(new THREE.Vector3(...after.controls[key].position))/after.frame.height))};
    }""")
    assert result['available']
    assert result['hiddenDeviation'] < 0.005
    assert not any(result['evidence'])
    assert result['footDisplacement'] < 0.025


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
    page.locator('.rig-preview-test-ik').click()
    page.wait_for_function('previewApi.getModelRigState().geometryRigPreview.pose?.enabled')
    page.evaluate("""async () => {
      window.previewScene=await import('./js/scene/scene.js');
      window.previewOverlay=previewScene.scene.getObjectByName('viewer-inferred-rig-overlay');
      window.previewMarkers=previewOverlay.getObjectByName('viewer-humanoid-control-rig-overlay')
        .children.map(child=>child.uuid);
      const rig=previewApi.getModelRigState().geometryRigPreview.rig;
      previewOverlay.updateMatrixWorld(true);
      const points=Object.values(rig.controls).map(control=>new fixture.THREE.Vector3(...control.position)
        .applyMatrix4(previewOverlay.matrixWorld));
      const center=new fixture.THREE.Box3().setFromPoints(points).getCenter(new fixture.THREE.Vector3());
      const forward=new fixture.THREE.Vector3(...rig.frame.forward).transformDirection(previewOverlay.matrixWorld);
      previewScene.camera.position.copy(center).addScaledVector(forward,rig.frame.height*3);
      previewScene.camera.up.set(...rig.frame.up).transformDirection(previewOverlay.matrixWorld);
      previewScene.camera.lookAt(center);previewScene.camera.updateMatrixWorld();
      previewScene.controls.target.copy(center);previewScene.controls.setCamera(previewScene.camera);
      await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
      window.previewEndpoint=async key=>{
        const {projectRigPointToClient}=await import('./js/scene/rig-overlay-controller.js');
        const state=previewApi.getModelRigState().geometryRigPreview;
        previewOverlay.updateMatrixWorld(true);
        return projectRigPointToClient({point:state.pose?.positions[key]||state.rig.controls[key].position,
          camera:previewScene.camera,canvas:previewScene.renderer.domElement,worldMatrix:previewOverlay.matrixWorld});
      };
    }""")
    for key in ('leftHand', 'rightHand', 'leftFoot', 'rightFoot'):
        point = page.evaluate('previewEndpoint', key)
        page.mouse.move(point['x'], point['y'])
        page.mouse.down()
        assert page.evaluate('previewScene.controls.enabled') is False, (key, point)
        page.mouse.move(point['x'] + 15, point['y'] - 30, steps=5)
        page.mouse.up()
        assert page.evaluate('previewScene.controls.enabled') is True
        assert page.evaluate('previewApi.getModelRigState().geometryRigPreview.pose.selectedControlKey') == key
        assert page.locator('.rig-preview-flip-bend').is_enabled()
    assert page.evaluate("JSON.stringify(previewMarkers)===JSON.stringify(previewOverlay.getObjectByName('viewer-humanoid-control-rig-overlay').children.map(child=>child.uuid))")
    page.locator('.rig-preview-comparison').select_option('proportional')
    for key in ('leftHand', 'rightHand', 'leftFoot', 'rightFoot'):
        point = page.evaluate('previewEndpoint', key)
        page.mouse.move(point['x'], point['y'])
        page.mouse.down()
        page.mouse.move(point['x'] - 10, point['y'] - 20, steps=4)
        page.mouse.up()
        assert page.evaluate('previewApi.getModelRigState().geometryRigPreview.pose.selectedControlKey') == key
        assert page.evaluate('previewScene.controls.enabled') is True
    page.locator('.rig-preview-comparison').select_option('geometry')
    point = page.evaluate('previewEndpoint', 'rightFoot')
    page.mouse.move(point['x'], point['y'])
    page.mouse.down()
    page.mouse.move(point['x'] + 10, point['y'] - 20)
    page.mouse.up()
    orbit_before = page.evaluate('previewScene.camera.quaternion.toArray()')
    viewport = page.locator('#canvas-container canvas').first.bounding_box()
    page.mouse.move(viewport['x'] + viewport['width'] / 2, viewport['y'] + 45)
    page.mouse.down()
    page.mouse.move(viewport['x'] + viewport['width'] / 2 + 60, viewport['y'] + 65, steps=5)
    page.mouse.up()
    assert page.evaluate('previewScene.camera.quaternion.toArray()') != orbit_before
    page.locator('.rig-preview-flip-bend').click()
    page.locator('.rig-preview-reset-pose').click()
    assert page.evaluate('Object.keys(previewApi.getModelRigState().geometryRigPreview.pose.positions).length') == 0
    for action in ('reset', 'mode', 'disable', 'current', 'regenerate', 'capture-loss', 'cancel',
                   'geometry', 'transform', 'panel-close'):
        page.evaluate("""async () => {
          if (!previewApi.getModelRigState().geometryRigPreview?.rig) await previewApi.previewGeometryRig();
          previewApi.setGeometryRigPreviewMode('geometry'); previewApi.setGeometryRigPreviewIkEnabled(true);
        }""")
        point = page.evaluate('previewEndpoint', 'leftHand')
        page.mouse.move(point['x'], point['y'])
        page.mouse.down()
        page.mouse.move(point['x'] + 10, point['y'] - 15)
        assert page.evaluate('previewScene.controls.enabled') is False
        page.evaluate("""async action => {
          const click=selector=>document.querySelector(selector).click();
          if (action==='reset') click('.rig-preview-reset-pose');
          if (action==='mode') {
            const select=document.querySelector('.rig-preview-comparison');select.value='proportional';
            select.dispatchEvent(new Event('change',{bubbles:true}));
          }
          if (action==='disable') click('.rig-preview-test-ik');
          if (action==='current') previewApi.setGeometryRigPreviewMode('current');
          if (action==='regenerate') await previewApi.previewGeometryRig();
          if (action==='geometry'||action==='transform') window.dispatchEvent(new CustomEvent(
            action==='geometry'?'mod-viewer-mesh-state-changed':'mod-viewer-model-transform-changed',
            {detail:{meshes:fixture.meshes}}));
          if (action==='panel-close') window.dispatchEvent(new CustomEvent('mod-viewer-right-dock-tab-changed',
            {detail:{tab:'weight-rig',open:false}}));
          if (action==='capture-loss'||action==='cancel') previewScene.renderer.domElement.dispatchEvent(
            new PointerEvent(action==='cancel'?'pointercancel':'lostpointercapture',{pointerId:1}));
        }""", action)
        assert page.evaluate('previewScene.controls.enabled') is True
        page.mouse.up()
    page.evaluate('previewApi.previewGeometryRig()')
    assert page.locator('.rig-preview-comparison').is_enabled()
    summary = page.locator('.rig-preview-geometry').locator('..').inner_text()
    assert 'samples' in summary
    posing = page.evaluate("""() => {
      const rest=previewApi.getModelRigState().geometryRigPreview.rig;
      const saved=JSON.stringify(rest), height=rest.frame.height;
      const distance=(a,b)=>Math.hypot(...a.map((v,i)=>v-b[i]));
      const roles={left_arm:['leftShoulder','leftElbow','leftHand'],
        right_arm:['rightShoulder','rightElbow','rightHand'],
        left_leg:['leftHip','leftKnee','leftFoot'],right_leg:['rightHip','rightKnee','rightFoot']};
      previewApi.setGeometryRigPreviewIkEnabled(true);
      const checks=[];
      for (const [role,keys] of Object.entries(roles)) {
        const before=structuredClone(previewApi.getModelRigState().geometryRigPreview.pose.positions);
        const points=keys.map(key=>rest.controls[key].position);
        const lengths=[distance(points[0],points[1]),distance(points[1],points[2])];
        const end=points[2], toward=role.startsWith('left')?1:-1;
        previewApi.solveGeometryRigPreviewIk(keys[2],end.map((v,i)=>v+height*(i===1?0.1:i===2?0.06*toward:0)));
        const preview=previewApi.getModelRigState().geometryRigPreview;
        const posed=keys.map(key=>preview.pose.positions[key]||rest.controls[key].position);
        checks.push({lengthError:Math.max(Math.abs(distance(posed[0],posed[1])-lengths[0]),
          Math.abs(distance(posed[1],posed[2])-lengths[1]))/height,
          reached:preview.pose.limbs[role].reached, anchor:distance(posed[0],points[0]),
          isolated:Object.keys(before).every(key=>keys.includes(key)||JSON.stringify(before[key])===JSON.stringify(preview.pose.positions[key]))});
        const direction=end.map((v,i)=>v-points[0][i]), length=Math.hypot(...direction);
        let previousBend=null;
        for (const fraction of [0.96,0.99,0.999,1,1.01,1.02]) {
          const target=points[0].map((v,i)=>v+direction[i]/length*(lengths[0]+lengths[1])*fraction);
          previewApi.solveGeometryRigPreviewIk(keys[2],target);
          const state=previewApi.getModelRigState().geometryRigPreview.pose;
          const bend=state.positions[keys[1]], a=points[0];
          const delta=bend.map((v,i)=>v-a[i]);
          const along=delta.reduce((sum,v,i)=>sum+v*direction[i]/length,0);
          const perpendicular=delta.map((v,i)=>v-direction[i]/length*along);
          const sign=perpendicular.reduce((sum,v,i)=>sum+v*state.limbs[role].bendDirection[i],0);
          checks.push({sign,finite:[...bend,...state.positions[keys[2]]].every(Number.isFinite),
            jump:previousBend?distance(bend,previousBend)/height:0});
          previousBend=bend;
        }
        const clamped=previewApi.getModelRigState().geometryRigPreview.pose.limbs[role];
        checks.push({clamped:!clamped.reached && clamped.residual>0});
      }
      previewApi.solveGeometryRigPreviewIk('rightFoot',rest.controls.rightFoot.position
        .map((v,i)=>v+height*(i===1?0.12:i===2?0.08:0)));
      const beforeFlip=structuredClone(previewApi.getModelRigState().geometryRigPreview.pose);
      previewApi.flipGeometryRigPreviewBend();
      const afterFlip=previewApi.getModelRigState().geometryRigPreview.pose;
      const flip=afterFlip.limbs.right_leg.bendSign===-beforeFlip.limbs.right_leg.bendSign
        && distance(afterFlip.positions.rightKnee,beforeFlip.positions.rightKnee)>height*0.05
        && distance(afterFlip.positions.rightFoot,beforeFlip.positions.rightFoot)<height*1e-9;
      const invalid=!previewApi.solveGeometryRigPreviewIk('head',[0,0,0])
        && !previewApi.solveGeometryRigPreviewIk('leftHand',[NaN,0,0]);
      previewApi.resetGeometryRigPreviewPose();
      const reset=Object.keys(previewApi.getModelRigState().geometryRigPreview.pose.positions).length===0;
      previewApi.solveGeometryRigPreviewIk('leftHand',rest.controls.leftElbow.position);
      previewApi.setGeometryRigPreviewMode('proportional');
      const modeReset=Object.keys(previewApi.getModelRigState().geometryRigPreview.pose.positions).length===0;
      previewApi.setGeometryRigPreviewMode('geometry');
      previewApi.setGeometryRigPreviewIkEnabled(false);
      return {checks,flip,invalid,reset,modeReset,disabled:!previewApi.getModelRigState().geometryRigPreview.pose,
        immutable:JSON.stringify(rest)===saved,loaded:previewApi.getModelRigState().loaded};
    }""")
    assert posing['flip'] and posing['invalid'] and posing['reset'] and posing['modeReset']
    assert posing['disabled'] and posing['immutable'] and not posing['loaded']
    for check in posing['checks']:
        if 'lengthError' in check:
            assert check['lengthError'] < 1e-9 and check['anchor'] == 0
            assert check['reached'] and check['isolated']
        elif 'sign' in check:
            assert check['sign'] >= -1e-9 and check['finite'] and check['jump'] < 0.08
        else:
            assert check['clamped']
    assert bridge_calls(page, 'weights') == []
    page.locator('.rig-preview-comparison').select_option('proportional')
    assert page.evaluate("previewApi.getModelRigState().geometryRigPreview.rig.mode") == 'proportional_template'
    page.locator('.rig-preview-comparison').select_option('geometry')
    page.locator('.rig-preview-comparison').select_option('current')
    assert page.locator('.weight-rig-advanced input[type="range"]').first.is_enabled()
    assert page.evaluate("JSON.stringify(previewApi.getModelRigState()) === beforePreview")
    assert page.evaluate("beforeRest.every((rest,i)=>JSON.stringify(rest)===JSON.stringify([...fixture.meshes[i].geometry.attributes.position.array]))")
    result = page.evaluate("""async () => {
      fixture.meshes[0].geometry.attributes.position.array[0]+=0.1;
      fixture.meshes[0].userData.animationState={lastFrame:1};
      const rejected=await previewApi.previewGeometryRig();
      const reason=previewApi.getModelRigState().geometryRigPreview.errorKey;
      fixture.meshes[0].geometry.attributes.position.array[0]=beforeRest[0][0];
      delete fixture.meshes[0].userData.animationState;
      previewApi.clearGeometryRigPreview();
      const stale=[];
      const pending=previewApi.previewGeometryRig();previewApi.clearGeometryRigPreview();stale.push(await pending);
      const visibilityPending=previewApi.previewGeometryRig();hiddenPreviewGroup.visible=true;stale.push(await visibilityPending);
      hiddenPreviewGroup.visible=false;
      const geometryPending=previewApi.previewGeometryRig();fixture.meshes[0].geometry.attributes.position.needsUpdate=true;
      stale.push(await geometryPending);
      const transformPending=previewApi.previewGeometryRig();fixture.meshes[0].position.x+=0.1;stale.push(await transformPending);
      fixture.meshes[0].position.x-=0.1;
      const rest=fixture.meshes[0].userData.humanoidRestPositions;
      const restPending=previewApi.previewGeometryRig();fixture.meshes[0].userData.humanoidRestPositions=new Float32Array(rest);
      stale.push(await restPending);fixture.meshes[0].userData.humanoidRestPositions=rest;
      const range=fixture.meshes[0].geometry.drawRange.count;
      const rangePending=previewApi.previewGeometryRig();fixture.meshes[0].geometry.drawRange.count=3;stale.push(await rangePending);
      fixture.meshes[0].geometry.drawRange.count=range;
      return {rejected,reason,staleRejected:stale.every(value=>value===false),preview:previewApi.getModelRigState().geometryRigPreview};
    }""")
    assert result == {'rejected': False, 'reason': 'weightRig.geometryRequiresRest', 'staleRejected': True, 'preview': None}
    page.evaluate('previewApi.previewGeometryRig()')
    page.evaluate("window.dispatchEvent(new CustomEvent('mod-viewer-model-transform-changed',{detail:{meshes:fixture.meshes}}))")
    assert page.evaluate('previewApi.getModelRigState().geometryRigPreview') is None
    page.evaluate('previewApi.previewGeometryRig()')
    page.evaluate("""async () => {
      const {refreshWeightRigAfterShapeChange}=await import('./js/weight-rig/weight-rig-core.js');
      refreshWeightRigAfterShapeChange(fixture.meshes[0]);
    }""")
    assert page.evaluate('previewApi.getModelRigState().geometryRigPreview') is None
    page.evaluate('previewApi.previewGeometryRig()')
    page.evaluate("window.dispatchEvent(new CustomEvent('mod-viewer-mesh-state-changed',{detail:{meshes:fixture.meshes}}))")
    assert page.evaluate('previewApi.getModelRigState().geometryRigPreview') is None
    page.evaluate('previewApi.previewGeometryRig()')
    page.evaluate('previewApi.setGeometryRigPreviewIkEnabled(true)')
    open_model(page, 'fixture-02')
    wait_loaded(page)
    assert page.evaluate('previewApi.getModelRigState().geometryRigPreview') is None
    assert bridge_calls(page, 'weights') == []
    page.locator('.rig-load-current').click()
    page.wait_for_function('previewApi.getModelWeightState().loaded')
    assert len(bridge_calls(page, 'weights')) == 1

def test_game_rig_source_identity_rest_pose_and_fallback_lifecycle(module_page):
    result = module_page.evaluate('''async () => {
      const T = await import('three');
      const {buildGameRigReconciliation} = await import('./js/weight-rig/game-rig.js');
      const {rebuildModelRestFrames} = await import('./js/weight-rig/weight-rig-frames.js');
      const {resolveHumanoidControlMappings,MODEL_RIG_BUILDER_VERSION} = await import('./js/weight-rig/humanoid-control-rig.js');
      const {buildJointSignatureIndex} = await import('./js/weight-rig/weight-rig-presets.js');
      const {buildForestTransformsFromLocalRotations,applyWeightedTransformDeformation} =
        await import('./js/weight-rig/weight-deformation.js');
      const q = new T.Quaternion().setFromAxisAngle(new T.Vector3(0,0,1),Math.PI/2).toArray();
      const bones = [
        {name:'Root',parent:-1,translation:[10,0,0],rotation:q},
        {name:'Joint01',parent:0,translation:[2,0,0],rotation:[0,0,0,1]},
        {name:'Joint02',parent:1,translation:[0,3,0],rotation:[0,0,0,1]},
      ];
      const sources = [
        {sourceKey:'First/Blend.buf|offset=0',boneIds:[2],gameRig:{id:'rig01',bones,boneMap:{2:2}},
          legacySourceKey:'Previous/Blend.buf|offset=5',legacyBoneIdOffset:5},
        {sourceKey:'Second/Blend.buf|offset=0',boneIds:[2],gameRig:{id:'rig01',bones,boneMap:{2:1}}},
      ];
      const rig = buildGameRigReconciliation(sources);
      const legacyJoint = buildJointSignatureIndex(rig).resolvedBySignature.get(JSON.stringify(['Previous/Blend.buf|offset=5#bone=7']));
      rebuildModelRestFrames(rig,{components:rig.components});
      const forest = {components:rig.components};
      const baseline = new Float32Array([7,2,0]);
      const pose = new Map();
      const deform = () => {
        const transforms = buildForestTransformsFromLocalRotations(forest,null,{
          quaternionByBoneId:pose,jointPivotByBoneId:rig.jointPivotByJointId});
        return [...applyWeightedTransformDeformation(baseline,new Uint32Array([2]),
          new Float32Array([1]),1,transforms)];
      };
      const rest = deform();
      pose.set(1,new T.Quaternion(...q));
      const bent = deform();
      pose.clear();
      const reset = deform();
      const aliases = [...rig.sourceBoneToModelJointMap.entries()];
      const originalPivot = [...rig.jointPivotByJointId.get(2)];
      rebuildModelRestFrames(rig,{components:rig.components});
      const unchanged = rig.jointPivotByJointId.get(2).every((v,i)=>v===originalPivot[i]);
      const missing = buildGameRigReconciliation([{...sources[0],gameRig:null}]);
      const incomplete = buildGameRigReconciliation([{...sources[0],boneIds:[99]}]);
      const mixed = buildGameRigReconciliation([sources[0],{...sources[1],gameRig:{...sources[1].gameRig,id:'other'}}]);
      const cyclic = buildGameRigReconciliation([{...sources[0],gameRig:{...sources[0].gameRig,bones:[{...bones[0],parent:0}]}}]);
      const saved={model_rig_builder_version:MODEL_RIG_BUILDER_VERSION,
        controls:{leftShoulder:{joint_id:1,semantic:{sideN:0,height01:0.5,depthN:0}}}};
      const originalModel={...rig,gameRigId:'a'.repeat(64)};
      const oldMapping=resolveHumanoidControlMappings({savedOverrides:saved,modelRig:originalModel});
      saved.model_rig_identity=originalModel.gameRigId;
      const matchingMapping=resolveHumanoidControlMappings({savedOverrides:saved,modelRig:originalModel});
      const previousAuthoredMapping=resolveHumanoidControlMappings({savedOverrides:{...saved,model_rig_builder_version:4},modelRig:originalModel});
      const fallbackMapping=resolveHumanoidControlMappings({savedOverrides:saved,modelRig:rig});
      return {names:rig.joints.map(j=>j.name),aliases,rest,bent,reset,unchanged,
        fallback:[missing,incomplete,mixed,cyclic].every(v=>v===null),
        root:rig.components[0].rootId,legacyJoint,frame:rig.restFrameByJointId.get(2).toArray(),
        savedMappingSizes:[oldMapping.size,matchingMapping.size,fallbackMapping.size],previousAuthoredMapping:previousAuthoredMapping.size};
    }''')
    import pytest
    assert result['names'] == ['Root', 'Joint01', 'Joint02']
    assert result['aliases'] == [
        ['First/Blend.buf|offset=0#bone=2', 2],
        ['Second/Blend.buf|offset=0#bone=2', 1],
    ]
    assert result['root'] == 0
    assert result['legacyJoint'] == 2
    assert result['rest'] == result['reset'] == [7, 2, 0]
    assert result['bent'] == pytest.approx([10, -1, 0], abs=1e-6)
    assert result['frame'] == pytest.approx([0, 0, 2 ** -.5, 2 ** -.5])
    assert result['unchanged'] and result['fallback']
    assert result['savedMappingSizes'] == [0, 1, 0]
    assert result['previousAuthoredMapping'] == 1


def test_authored_humanoid_ik_reach_extension_fold_and_edit_lifecycle(module_page):
    result = module_page.evaluate('''async () => {
      const T = await import('three');
      const {buildGameRigReconciliation} = await import('./js/weight-rig/game-rig.js');
      const {rebuildModelRestFrames} = await import('./js/weight-rig/weight-rig-frames.js');
      const {buildAuthoredHumanoidControlRig,mergeAuthoredHumanoidMappings} =
        await import('./js/weight-rig/game-rig-humanoid.js');
      const h = await import('./js/weight-rig/humanoid-control-rig.js');
      const b = await import('./js/weight-rig/humanoid-rig-binding.js');
      const ik = await import('./js/weight-rig/humanoid-rig-ik.js');
      const rows = [
        ['pelvis',-1,[0,5,0]],['chest',0,[0,2,0]],['neck',1,[0,2,0]],['head',2,[0,1,0]],
        ['leftShoulder',1,[-1,1,0]],['leftElbow',4,[-2,-1,-0.2]],['leftHand',5,[-2,-1,0.2]],
        ['rightShoulder',1,[1,1,0]],['rightElbow',7,[2,-1,-0.2]],['rightHand',8,[2,-1,0.2]],
        ['leftHip',0,[-0.5,0,0]],['leftKnee',10,[0,-2,0.2]],['leftFoot',11,[0,-2,-0.2]],
        ['rightHip',0,[0.5,0,0]],['rightKnee',13,[0,-2,0.2]],['rightFoot',14,[0,-2,-0.2]],
      ];
      const mapping = Object.fromEntries(rows.map(([key],i)=>[key,i]));
      const bones = rows.map(([,parent,translation],i)=>({name:`Joint${i}`,parent,translation,rotation:[0,0,0,1]}));
      bones.push({name:'Helper01',parent:5,translation:[0.1,0,0],rotation:[0,0,0,1]});
      const gameRig = {id:'a'.repeat(64),bones,humanoid_controls:mapping,
        boneMap:Object.fromEntries(bones.map((_,i)=>[i,i]))};
      const model=buildGameRigReconciliation([{sourceKey:'Skin.buf',boneIds:bones.map((_,i)=>i),gameRig}]);
      model.gameRigId=gameRig.id;
      rebuildModelRestFrames(model,{components:model.components});
      const fitted={version:1,accepted:true,frame:{height:10,lowHeight:0,sideCenter:0,depthCenter:0,
        up:[0,1,0],right:[1,0,0],forward:[0,0,1]},controls:{}};
      const original=JSON.stringify(fitted);
      const automatic=buildAuthoredHumanoidControlRig(fitted,model);
      const maps=mergeAuthoredHumanoidMappings(automatic,new Map(),null);
      const rig=h.applyHumanoidControlRigOverrides({automaticRig:automatic,modelRig:model,resolvedMappings:maps});
      const binding=b.buildHumanoidRigBinding({controlRig:rig,modelRig:model,controlMappings:maps});
      const v=p=>new T.Vector3(...p);
      const roles=['left_arm','right_arm','left_leg','right_leg'];
      const results=[];
      for(const role of roles){
        const keys=ik.HUMANOID_LIMB_CONTROLS[role];
        const anchor=v(rig.controls[keys[0]].position),end=v(rig.controls[keys[2]].position);
        const target=end.clone().lerp(anchor,0.25).add(new T.Vector3(0,0,0.4)).toArray();
        const first=ik.solveHumanoidControlIk({controlRig:rig,role,target});
        const far=anchor.clone().add(end.clone().sub(anchor).multiplyScalar(10)).toArray();
        const extended=ik.solveHumanoidControlIk({controlRig:rig,role,target:far,posedControls:first.positions});
        const returned=ik.solveHumanoidControlIk({controlRig:rig,role,target,posedControls:extended.positions});
        const folded=ik.solveHumanoidControlIk({controlRig:rig,role,target:anchor.toArray(),posedControls:returned.positions});
        const transformed=b.buildHumanoidDriverBaseTransforms({binding,controlRig:rig,modelRig:model,
          posedControls:returned.positions}).result;
        const lengths=s=>[v(s.anchor).distanceTo(v(s.bend)),v(s.bend).distanceTo(v(s.end))];
        const desired=[first.upperLength,first.lowerLength];
        const error=Math.max(...[first,extended,returned,folded].flatMap(s=>lengths(s).map((l,i)=>Math.abs(l-desired[i]))));
        const endpoint=v(model.jointPivotByJointId.get(mapping[keys[2]])).applyMatrix4(transformed.get(mapping[keys[2]]));
        results.push({reached:returned.reached,stable:v(first.bend).distanceTo(v(returned.bend))<1e-7,
          lengthError:error,endpointError:endpoint.distanceTo(v(returned.end)),unreachable:!extended.reached});
      }
      const rest=b.buildHumanoidDriverBaseTransforms({binding,controlRig:rig,modelRig:model}).result;
      const restError=Math.max(...[...rest].map(([id,m])=>v(model.jointPivotByJointId.get(id)).applyMatrix4(m)
        .distanceTo(v(model.jointPivotByJointId.get(id)))));
      const saved={controls:{leftElbow:{semantic:{sideN:-0.3,height01:0.7,depthN:0}}}};
      const editedMaps=mergeAuthoredHumanoidMappings(automatic,new Map(),saved);
      const edited=h.applyHumanoidControlRigOverrides({automaticRig:automatic,savedOverrides:saved,
        modelRig:model,resolvedMappings:editedMaps});
      const rejected=new Map();rejected.rejectedControlKeys=new Set(['leftHand']);
      const shoulder=v(rig.controls.leftShoulder.position);
      const rotations=[-1e-5,1e-5].map(x=>{
        const frames=b.buildHumanoidDriverFrames(rig,{leftElbow:shoulder.clone().add(new T.Vector3(x,0,2)).toArray()});
        return new T.Quaternion().setFromRotationMatrix(frames.get('left_upper_arm').matrix);
      });
      return {source:rig.source,results,restError,exactPivots:Object.entries(mapping).every(([key,id])=>
          v(rig.controls[key].position).distanceTo(v(model.jointPivotByJointId.get(id)))<1e-8),
        helperOwner:binding.jointBindings.get(16).controlKey,anchors:maps.size,
        edits:!editedMaps.has('leftElbow')&&edited.controls.leftElbow.source==='manual_override',
        rejected:!mergeAuthoredHumanoidMappings(automatic,rejected,null).has('leftHand'),
        fallback:buildAuthoredHumanoidControlRig(fitted,{...model,authoredHumanoidControls:null})===null,
        withoutFit:buildAuthoredHumanoidControlRig({...fitted,accepted:false,frame:{...fitted.frame,height:0}},model)?.accepted,
        rollStable:Math.abs(rotations[0].dot(rotations[1]))>0.99999,
        immutable:original===JSON.stringify(fitted)};
    }''')
    assert result['source'] == 'authored_skeleton'
    assert result['anchors'] == 16
    assert result['helperOwner'] == 'leftElbow'
    assert result['restError'] < 1e-7
    assert all(result[key] for key in ('exactPivots', 'edits', 'rejected', 'fallback', 'immutable', 'withoutFit', 'rollStable'))
    for limb in result['results']:
        assert limb['reached'] and limb['stable'] and limb['unreachable']
        assert limb['lengthError'] < 1e-6
        assert limb['endpointError'] < 1e-6


def test_uniform_reference_scale_pivots_manual_pose_and_animation_lifecycle(module_page):
    result = module_page.evaluate('''async () => {
      const T = await import('three');
      const {buildGameRigReconciliation} = await import('./js/weight-rig/game-rig.js');
      const {rebuildModelRestFrames} = await import('./js/weight-rig/weight-rig-frames.js');
      const {sampleAnimation} = await import('./js/weight-rig/rig-animation.js');
      const {buildForestTransformsFromLocalRotations,applyWeightedTransformDeformation} =
        await import('./js/weight-rig/weight-deformation.js');
      const q = new T.Quaternion().setFromAxisAngle(new T.Vector3(0,0,1),Math.PI/2).toArray();
      const bones = [
        {name:'Root',parent:-1,translation:[10,0,0],rotation:q,scale:[.75,.75,.75]},
        {name:'Joint01',parent:0,translation:[2,0,0],rotation:[0,0,0,1],scale:[2,2,2]},
        {name:'Joint02',parent:1,translation:[0,3,0],rotation:[0,0,0,1]},
      ];
      const source = {sourceKey:'Blend.buf',boneIds:[2],gameRig:{id:'scaled01',bones,boneMap:{2:2}}};
      const rig = buildGameRigReconciliation([source]);
      rebuildModelRestFrames(rig,{components:rig.components});
      const baseline = new Float32Array([5.5,1.5,0]);
      const deform = transforms => [...applyWeightedTransformDeformation(baseline,
        new Uint32Array([2]),new Float32Array([1]),1,transforms)];
      const manual = buildForestTransformsFromLocalRotations({components:rig.components},null,{
        quaternionByBoneId:new Map([[1,new T.Quaternion(...q)]]),jointPivotByBoneId:rig.jointPivotByJointId});
      const rows = bones.flatMap(bone=>[...bone.translation,...bone.rotation]);
      const next = [...rows]; next.splice(7,7,4,0,0,...q);
      const clip = {duration:1,frame_count:2}; const values = new Float32Array([...rows,...next]);
      const rest = sampleAnimation(rig.gameRig,clip,values,0);
      const animated = sampleAnimation(rig.gameRig,clip,values,1);
      const normal = new T.Vector3(1,0,0).transformDirection(animated.get(2)).toArray();
      const invalid = [[2,1,1],[0,0,0],[-1,1,1],[1e-7,1e-7,1e-7],[Infinity,Infinity,Infinity]]
        .every(scale=>buildGameRigReconciliation([{...source,gameRig:{...source.gameRig,
          bones:[{...bones[0],scale},...bones.slice(1)]}}])===null);
      const mirrored = buildGameRigReconciliation([{...source,gameRig:{...source.gameRig,
        bones:[{...bones[0],scale:[-1,-1,-1]},...bones.slice(1)]}}]);
      const mirroredFrame = null;
      // Exported game skeletons carry ~7e-4 float noise on otherwise uniform scales.
      const noisy = buildGameRigReconciliation([{...source,gameRig:{...source.gameRig,
        bones:[{...bones[0],scale:[1.00002,0.99999,1.00068]},...bones.slice(1)]}}])!==null;
      return {pivots:[...rig.jointPivotByJointId.values()],manual:deform(manual),mirrored:mirrored!==null,
        mirroredFrame:mirrored?[...mirrored.authoredRestFrames.get(0).toArray()]:null,
        rest:deform(rest),animated:deform(animated),reset:deform(sampleAnimation(rig.gameRig,clip,values,0)),
        normal,baseline:[...baseline],invalid,noisy,scale:rig.gameRig.bones[0].scale};
    }''')
    import pytest
    for actual, expected in zip(result['pivots'], ([10, 0, 0], [10, 1.5, 0], [5.5, 1.5, 0])):
        assert actual == pytest.approx(expected, abs=1e-6)
    assert result['manual'] == pytest.approx([10, -3, 0], abs=1e-6)
    assert result['animated'] == pytest.approx([10, -1.5, 0], abs=1e-6)
    assert result['normal'] == pytest.approx([0, 1, 0], abs=1e-6)
    assert result['rest'] == result['reset'] == result['baseline'] == [5.5, 1.5, 0]
    assert result['scale'] == [.75] * 3 and result['invalid'] and result['noisy'] and result['mirrored']
    half = 2 ** -.5
    assert result['mirroredFrame'] == pytest.approx([0, 0, half, half], abs=1e-6) or result['mirroredFrame'] == pytest.approx([0, 0, -half, -half], abs=1e-6)
